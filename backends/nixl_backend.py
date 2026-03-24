import torch
from nixl_cu13._api import nixl_agent, nixl_agent_config
import os
import time
import utils.config as config
from concurrent.futures import ThreadPoolExecutor

_O_DIRECT_FLAG = getattr(os, 'O_DIRECT', 0)


def _open_o_direct(path: str, flags: int, mode: int = 0o644) -> int:
    if config.USE_O_DIRECT and _O_DIRECT_FLAG:
        try:
            return os.open(path, flags | _O_DIRECT_FLAG, mode)
        except OSError:
            pass
    return os.open(path, flags, mode)

# Storage path configuration - can be overridden by STORAGE_PATH environment variable
STORAGE_PATH = os.environ.get('STORAGE_PATH', '/dev/shm')


# Per-thread agent pools: one agent per thread slot so threads never share an agent.
# NIXL agents are NOT thread-safe; sharing one across threads causes heap corruption.
# Pools grow (never shrink) as set_thread_count_nixl is called with increasing values,
# so calling with [1, 4, 16] will ultimately create 16 write + 16 read agents.
_write_agent_pool: list = []  # list of (agent_name, agent) tuples
_read_agent_pool: list  = []  # list of (agent_name, agent) tuples

# Number of threads to use for parallel block transfers
_nixl_num_threads = 1


_nixl_split_warned = False  # Print the experimental warning only once per process
_NIXL_IOS_PER_CHUNK = 16    # Contiguous IOs per chunk before round-robining across threads


def _distribute_blocks(blocks_indices, file_names, n):
    """Split blocks into contiguous chunks of _NIXL_IOS_PER_CHUNK, then
    assign chunks round-robin across n threads. Each thread receives a list
    of contiguous runs rather than individually scattered blocks."""
    thread_indices = [[] for _ in range(n)]
    thread_fnames  = [[] for _ in range(n)]
    chunk_id = 0
    for start in range(0, len(blocks_indices), _NIXL_IOS_PER_CHUNK):
        end = start + _NIXL_IOS_PER_CHUNK
        t = chunk_id % n
        thread_indices[t].extend(blocks_indices[start:end])
        thread_fnames[t].extend(file_names[start:end])
        chunk_id += 1
    return thread_indices, thread_fnames


def _grow_pool(pool: list, prefix: str, target: int):
    """Append new agents to *pool* until it has at least *target* entries."""
    while len(pool) < target:
        idx = len(pool)
        name = f"{prefix}_{idx}"
        conf = nixl_agent_config(enable_prog_thread=True, backends=[])
        agent = nixl_agent(agent_name=name, nixl_conf=conf, instantiate_all=False)
        backend_params = {}
        if config.NIXL_IO_BACKEND == "POSIX" and config.NIXL_USE_URING:
            backend_params["use_uring"] = "true"
        elif config.NIXL_IO_BACKEND == "GDS_MT" and config.NIXL_GDS_MT_THREADS is not None:
            backend_params["thread_count"] = str(config.NIXL_GDS_MT_THREADS)
        agent.create_backend(config.NIXL_IO_BACKEND, backend_params)
        pool.append((name, agent))


def set_nixl_io_backend(backend: str):
    """Switch the NIXL I/O backend (POSIX or GDS_MT).
    Clears the agent pools so new agents are created with the new backend.
    Must be called before any transfers start."""
    config.set_nixl_io_backend(backend)
    global _write_agent_pool, _read_agent_pool
    _write_agent_pool = []
    _read_agent_pool = []


def set_thread_count_nixl(n: int):
    global _nixl_num_threads
    _nixl_num_threads = n
    # Pre-create agents so threads are ready before the first transfer
    _grow_pool(_write_agent_pool, "NIXL_Writer", n)
    _grow_pool(_read_agent_pool,  "NIXL_Reader", n)


def _resolve_split_threads(n: int) -> int:
    """Warn once when NIXL agents are split across threads (experimental path)."""
    global _nixl_split_warned
    if not _nixl_split_warned:
        print(f"[WARNING] NIXL agent splitting is experimental (threads={n}).")
        _nixl_split_warned = True
    return n


def _register_buffer(agent: nixl_agent, buffer: torch.Tensor):
    if not buffer.is_contiguous():
        buffer = buffer.contiguous()
    return agent.register_memory(agent.get_reg_descs(buffer))


def nixl_register_write_buffer(buffer: torch.Tensor) -> list:
    """Register buffer with all write pool agents. Returns list of handles."""
    _grow_pool(_write_agent_pool, "NIXL_Writer", max(_nixl_num_threads, 1))
    return [_register_buffer(agent, buffer) for _, agent in _write_agent_pool]


def nixl_unregister_write_buffer(handles: list):
    """Unregister buffer from all write pool agents."""
    for (_, agent), handle in zip(_write_agent_pool, handles):
        agent.deregister_memory(handle)


def nixl_register_read_buffer(buffer: torch.Tensor) -> list:
    """Register buffer with all read pool agents. Returns list of handles."""
    _grow_pool(_read_agent_pool, "NIXL_Reader", max(_nixl_num_threads, 1))
    return [_register_buffer(agent, buffer) for _, agent in _read_agent_pool]


def nixl_unregister_read_buffer(handles: list):
    """Unregister buffer from all read pool agents."""
    for (_, agent), handle in zip(_read_agent_pool, handles):
        agent.deregister_memory(handle)

def _write_chunk(agent, agent_name, block_size, buffer_addr, chunk_indices, chunk_fnames):
    """Write a subset of blocks using the given agent (runs in a single thread)."""
    local_descs_data = []
    remote_descs_data = []
    open_fds = []
    temp_files = []

    try:
        for idx, fname in zip(chunk_indices, chunk_fnames):
            temp_fname = f"{STORAGE_PATH}/temp_block_{idx}.bin"
            temp_files.append((temp_fname, fname))
            fd = _open_o_direct(temp_fname, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            open_fds.append(fd)

            block_addr = buffer_addr + (idx * block_size)
            local_descs_data.append((block_addr, block_size, 0))
            remote_descs_data.append((0, block_size, fd, ""))

        local_xfer_dl = agent.get_xfer_descs(local_descs_data, mem_type="DRAM")
        file_handle = agent.register_memory(remote_descs_data, mem_type="FILE", backends=[config.NIXL_IO_BACKEND])
        file_desc = file_handle.trim()

        xfer_handle = agent.initialize_xfer(
            operation="WRITE",
            local_descs=local_xfer_dl,
            remote_descs=file_desc,
            remote_agent=agent_name,
            backends=[config.NIXL_IO_BACKEND]
        )
        agent.transfer(xfer_handle)

        while True:
            state = agent.check_xfer_state(xfer_handle)
            if state == "DONE":
                break
            elif state == "ERR":
                raise RuntimeError("NIXL Transfer Failed")

        for fd in open_fds:
            os.close(fd)
        open_fds = []

        for temp_fname, final_fname in temp_files:
            os.rename(temp_fname, final_fname)
    except:
        for fd in open_fds:
            os.close(fd)
    finally:
        if 'xfer_handle' in locals():
            agent.release_xfer_handle(xfer_handle)
        if 'file_handle' in locals():
            agent.deregister_memory(file_handle)


def nixl_write_blocks(block_size, buffer, blocks_indices, file_names):
    """
    Writes discrete blocks from a CPU buffer to files as fast as possible.
    Uses a persistent agent and splits work across _nixl_num_threads threads.
    """
    start = time.perf_counter()

    buffer_addr = buffer.data_ptr()
    n = _nixl_num_threads
    _grow_pool(_write_agent_pool, "NIXL_Writer", n)

    # Split into contiguous chunks of _NIXL_IOS_PER_CHUNK, round-robin across threads
    chunks, fname_chunks = _distribute_blocks(blocks_indices, file_names, n)

    if n == 1:
        pool_name, pool_agent = _write_agent_pool[0]
        _write_chunk(pool_agent, pool_name, block_size, buffer_addr, chunks[0], fname_chunks[0])
    else:
        n = _resolve_split_threads(n)
        with ThreadPoolExecutor(max_workers=n) as executor:
            futures = [
                executor.submit(_write_chunk, _write_agent_pool[i][1], _write_agent_pool[i][0],
                                block_size, buffer_addr, chunks[i], fname_chunks[i])
                for i in range(n) if chunks[i]
            ]
            for f in futures:
                f.result()

    end = time.perf_counter()
    return (end - start)


def _read_chunk(agent, agent_name, block_size, buffer_addr, chunk_indices, chunk_fnames):
    """Read a subset of blocks using the given agent (runs in a single thread)."""
    local_descs_data = []
    remote_descs_data = []
    open_fds = []

    try:
        for idx, fname in zip(chunk_indices, chunk_fnames):
            fd = _open_o_direct(fname, os.O_RDONLY)
            open_fds.append(fd)

            block_addr = buffer_addr + (idx * block_size)
            local_descs_data.append((block_addr, block_size, 0))
            remote_descs_data.append((0, block_size, fd, ""))

        local_xfer_dl = agent.get_xfer_descs(local_descs_data, mem_type="DRAM")
        file_handle = agent.register_memory(remote_descs_data, mem_type="FILE", backends=[config.NIXL_IO_BACKEND])
        file_desc = file_handle.trim()

        xfer_handle = agent.initialize_xfer(
            operation="READ",
            local_descs=local_xfer_dl,
            remote_descs=file_desc,
            remote_agent=agent_name,
            backends=[config.NIXL_IO_BACKEND]
        )
        agent.transfer(xfer_handle)

        while True:
            state = agent.check_xfer_state(xfer_handle)
            if state == "DONE":
                break
            elif state == "ERR":
                raise RuntimeError("NIXL Transfer Failed")

        for fd in open_fds:
            os.close(fd)
        open_fds = []
    except:
        for fd in open_fds:
            os.close(fd)
    finally:
        if 'xfer_handle' in locals():
            agent.release_xfer_handle(xfer_handle)
        if 'file_handle' in locals():
            agent.deregister_memory(file_handle)


def nixl_read_blocks(block_size, buffer, block_indices: list, file_names: list):
    """
    Reads discrete blocks from files into a CPU buffer as fast as possible.
    Uses a persistent agent and splits work across _nixl_num_threads threads.
    """
    start = time.perf_counter()

    buffer_addr = buffer.data_ptr()
    n = _nixl_num_threads
    _grow_pool(_read_agent_pool, "NIXL_Reader", n)

    # Split into contiguous chunks of _NIXL_IOS_PER_CHUNK, round-robin across threads
    chunks, fname_chunks = _distribute_blocks(block_indices, file_names, n)

    if n == 1:
        pool_name, pool_agent = _read_agent_pool[0]
        _read_chunk(pool_agent, pool_name, block_size, buffer_addr, chunks[0], fname_chunks[0])
    else:
        n = _resolve_split_threads(n)
        with ThreadPoolExecutor(max_workers=n) as executor:
            futures = [
                executor.submit(_read_chunk, _read_agent_pool[i][1], _read_agent_pool[i][0],
                                block_size, buffer_addr, chunks[i], fname_chunks[i])
                for i in range(n) if chunks[i]
            ]
            for f in futures:
                f.result()

    end = time.perf_counter()
    return (end - start)

