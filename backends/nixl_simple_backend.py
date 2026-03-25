"""
Single-agent NIXL backend. All IOs are submitted to NIXL in one batch and
NIXL/GDS_MT handles parallelism internally via its own thread pool.

Contrast with nixl_backend.py which splits work across multiple Python threads
each owning a separate NIXL agent. Use this backend when you want NIXL to own
all concurrency (e.g. GDS_MT with thread_count=N).
"""
import torch
try:
    from nixl._api import nixl_agent, nixl_agent_config
except ModuleNotFoundError:
    from nixl_cu13._api import nixl_agent, nixl_agent_config
import os
import time
import utils.config as config

try:
    import cpp_ext
except ImportError:
    cpp_ext = None

_O_DIRECT_FLAG = getattr(os, 'O_DIRECT', 0)
_O_TMPFILE = getattr(os, 'O_TMPFILE', None)

STORAGE_PATH = os.environ.get('STORAGE_PATH', '/dev/shm')

_write_agent: tuple = None   # (agent_name, agent)
_read_agent: tuple  = None   # (agent_name, agent)


def _open_o_direct(path: str, flags: int, mode: int = 0o644) -> int:
    if config.USE_O_DIRECT and _O_DIRECT_FLAG:
        try:
            return os.open(path, flags | _O_DIRECT_FLAG, mode)
        except OSError:
            pass
    return os.open(path, flags, mode)


def _open_tmpfile(dir_path: str) -> int:
    """Open an unnamed inode via O_TMPFILE in dir_path (Linux ≥ 3.11).
    The file is invisible until linked to a final name with os.link().
    Falls back to None if O_TMPFILE is unavailable."""
    if _O_TMPFILE is None:
        return None
    flags = _O_TMPFILE | os.O_WRONLY
    if config.USE_O_DIRECT and _O_DIRECT_FLAG:
        try:
            return os.open(dir_path, flags | _O_DIRECT_FLAG)
        except OSError:
            pass
    return os.open(dir_path, flags)


def _make_agent(name: str) -> tuple:
    conf = nixl_agent_config(enable_prog_thread=True, backends=[])
    agent = nixl_agent(agent_name=name, nixl_conf=conf, instantiate_all=False)
    backend_params = {}
    if config.NIXL_IO_BACKEND == "POSIX" and config.NIXL_USE_URING:
        backend_params["use_uring"] = "true"
    elif config.NIXL_IO_BACKEND == "GDS_MT" and config.NIXL_GDS_MT_THREADS is not None:
        backend_params["thread_count"] = str(config.NIXL_GDS_MT_THREADS)
    agent.create_backend(config.NIXL_IO_BACKEND, backend_params)
    return (name, agent)


def _get_write_agent() -> tuple:
    global _write_agent
    if _write_agent is None:
        _write_agent = _make_agent("NIXL_Simple_Writer")
    return _write_agent


def _get_read_agent() -> tuple:
    global _read_agent
    if _read_agent is None:
        _read_agent = _make_agent("NIXL_Simple_Reader")
    return _read_agent


def reset_agents():
    """Drop cached agents (e.g. after switching backends via set_nixl_io_backend)."""
    global _write_agent, _read_agent
    _write_agent = None
    _read_agent = None


# ---------------------------------------------------------------------------
# Buffer registration (DRAM side) — called once per benchmark iteration
# ---------------------------------------------------------------------------

def nixl_simple_register_write_buffer(buffer: torch.Tensor):
    """Register the CPU buffer with the write agent. Returns a single handle."""
    _, agent = _get_write_agent()
    if not buffer.is_contiguous():
        buffer = buffer.contiguous()
    return agent.register_memory(agent.get_reg_descs(buffer))


def nixl_simple_unregister_write_buffer(handle):
    _, agent = _get_write_agent()
    agent.deregister_memory(handle)


def nixl_simple_register_read_buffer(buffer: torch.Tensor):
    """Register the CPU buffer with the read agent. Returns a single handle."""
    _, agent = _get_read_agent()
    if not buffer.is_contiguous():
        buffer = buffer.contiguous()
    return agent.register_memory(agent.get_reg_descs(buffer))


def nixl_simple_unregister_read_buffer(handle):
    _, agent = _get_read_agent()
    agent.deregister_memory(handle)


# ---------------------------------------------------------------------------
# Transfer functions — one register_memory + one transfer for all blocks
# ---------------------------------------------------------------------------

def nixl_simple_write_blocks(block_size, buffer, blocks_indices, file_names):
    """
    Write all blocks in a single NIXL transfer.
    One register_memory call covers all FDs; NIXL/GDS_MT parallelises internally.
    """
    t0 = time.perf_counter()

    agent_name, agent = _get_write_agent()
    buffer_addr = buffer.data_ptr()

    local_descs_data = []
    remote_descs_data = []
    open_fds = []
    fds = []
    open_paths = []
    is_tmpfile = False

    try:
        # Step 1: open files via C++ (GIL released, sequential opens for O_CREAT)
        t_open_start = time.perf_counter()
        fds, open_paths, is_tmpfile = cpp_ext.open_files_write(
            file_names, config.NO_RENAME, config.USE_O_DIRECT, STORAGE_PATH
        )
        open_fds = fds
        t_open = time.perf_counter() - t_open_start

        for idx, fd in zip(blocks_indices, fds):
            block_addr = buffer_addr + (idx * block_size)
            local_descs_data.append((block_addr, block_size, 0))
            remote_descs_data.append((0, block_size, fd, ""))

        # Step 2: get local DRAM xfer descriptors
        t_xfer_descs_start = time.perf_counter()
        local_xfer_dl = agent.get_xfer_descs(local_descs_data, mem_type="DRAM")
        t_xfer_descs = time.perf_counter() - t_xfer_descs_start

        # Step 3: register file-side memory
        t_reg_start = time.perf_counter()
        file_handle = agent.register_memory(remote_descs_data, mem_type="FILE", backends=[config.NIXL_IO_BACKEND])
        t_reg = time.perf_counter() - t_reg_start

        # Step 4: trim file descriptor list
        t_trim_start = time.perf_counter()
        file_desc = file_handle.trim()
        t_trim = time.perf_counter() - t_trim_start

        # Step 5: initialize transfer
        t_init_start = time.perf_counter()
        xfer_handle = agent.initialize_xfer(
            operation="WRITE",
            local_descs=local_xfer_dl,
            remote_descs=file_desc,
            remote_agent=agent_name,
            backends=[config.NIXL_IO_BACKEND],
        )
        t_init = time.perf_counter() - t_init_start

        # Step 6: submit + poll until done
        t_xfer_start = time.perf_counter()
        agent.transfer(xfer_handle)
        while True:
            state = agent.check_xfer_state(xfer_handle)
            if state == "DONE":
                break
            elif state == "ERR":
                raise RuntimeError("NIXL Transfer Failed")
        t_xfer = time.perf_counter() - t_xfer_start

        # Step 7+8: publish (link/rename) + close FDs via C++ thread pool (GIL released)
        t_link_start = time.perf_counter()
        cpp_ext.publish_and_close(fds, open_paths, file_names, is_tmpfile, config.NO_RENAME)
        open_fds = []
        t_link = time.perf_counter() - t_link_start
        t_close = 0.0

    except:
        if open_fds:
            cpp_ext.close_fds(open_fds)
        t_open = t_xfer_descs = t_reg = t_trim = t_init = t_xfer = t_link = t_close = float('nan')
    finally:
        # Step 9: cleanup handles
        t_cleanup_start = time.perf_counter()
        if 'xfer_handle' in locals():
            agent.release_xfer_handle(xfer_handle)
        if 'file_handle' in locals():
            agent.deregister_memory(file_handle)
        t_cleanup = time.perf_counter() - t_cleanup_start

    total = time.perf_counter() - t0
    print(
        f"[nixl_simple WRITE profile] "
        f"open_fds={t_open*1e3:.2f}ms  "
        f"get_xfer_descs={t_xfer_descs*1e3:.2f}ms  "
        f"register_file={t_reg*1e3:.2f}ms  "
        f"trim={t_trim*1e3:.2f}ms  "
        f"initialize_xfer={t_init*1e3:.2f}ms  "
        f"transfer+poll={t_xfer*1e3:.2f}ms  "
        f"publish_close={t_link*1e3:.2f}ms  "
        f"cleanup={t_cleanup*1e3:.2f}ms  "
        f"total={total*1e3:.2f}ms"
    )
    return total


def nixl_simple_read_blocks(block_size, buffer, block_indices, file_names):
    """
    Read all blocks in a single NIXL transfer.
    One register_memory call covers all FDs; NIXL/GDS_MT parallelises internally.
    """
    t0 = time.perf_counter()

    agent_name, agent = _get_read_agent()
    buffer_addr = buffer.data_ptr()

    local_descs_data = []
    remote_descs_data = []
    open_fds = []

    fds = []

    try:
        # Step 1: open files via C++ thread pool (GIL released, parallel O_RDONLY)
        t_open_start = time.perf_counter()
        fds = cpp_ext.open_files_read(file_names, config.USE_O_DIRECT)
        open_fds = fds
        t_open = time.perf_counter() - t_open_start

        for idx, fd in zip(block_indices, fds):
            block_addr = buffer_addr + (idx * block_size)
            local_descs_data.append((block_addr, block_size, 0))
            remote_descs_data.append((0, block_size, fd, ""))

        # Step 2: get local DRAM xfer descriptors
        t_xfer_descs_start = time.perf_counter()
        local_xfer_dl = agent.get_xfer_descs(local_descs_data, mem_type="DRAM")
        t_xfer_descs = time.perf_counter() - t_xfer_descs_start

        # Step 3: register file-side memory
        t_reg_start = time.perf_counter()
        file_handle = agent.register_memory(remote_descs_data, mem_type="FILE", backends=[config.NIXL_IO_BACKEND])
        t_reg = time.perf_counter() - t_reg_start

        # Step 4: trim file descriptor list
        t_trim_start = time.perf_counter()
        file_desc = file_handle.trim()
        t_trim = time.perf_counter() - t_trim_start

        # Step 5: initialize transfer
        t_init_start = time.perf_counter()
        xfer_handle = agent.initialize_xfer(
            operation="READ",
            local_descs=local_xfer_dl,
            remote_descs=file_desc,
            remote_agent=agent_name,
            backends=[config.NIXL_IO_BACKEND],
        )
        t_init = time.perf_counter() - t_init_start

        # Step 6: submit + poll until done
        t_xfer_start = time.perf_counter()
        agent.transfer(xfer_handle)
        while True:
            state = agent.check_xfer_state(xfer_handle)
            if state == "DONE":
                break
            elif state == "ERR":
                raise RuntimeError("NIXL Transfer Failed")
        t_xfer = time.perf_counter() - t_xfer_start

        # Step 7: close file descriptors via C++ (GIL released)
        t_close_start = time.perf_counter()
        cpp_ext.close_fds(fds)
        open_fds = []
        t_close = time.perf_counter() - t_close_start

    except:
        if open_fds:
            cpp_ext.close_fds(open_fds)
        t_open = t_xfer_descs = t_reg = t_trim = t_init = t_xfer = t_close = float('nan')
    finally:
        # Step 8: cleanup handles
        t_cleanup_start = time.perf_counter()
        if 'xfer_handle' in locals():
            agent.release_xfer_handle(xfer_handle)
        if 'file_handle' in locals():
            agent.deregister_memory(file_handle)
        t_cleanup = time.perf_counter() - t_cleanup_start

    total = time.perf_counter() - t0
    print(
        f"[nixl_simple READ  profile] "
        f"open_fds={t_open*1e3:.2f}ms  "
        f"get_xfer_descs={t_xfer_descs*1e3:.2f}ms  "
        f"register_file={t_reg*1e3:.2f}ms  "
        f"trim={t_trim*1e3:.2f}ms  "
        f"initialize_xfer={t_init*1e3:.2f}ms  "
        f"transfer+poll={t_xfer*1e3:.2f}ms  "
        f"close_fds={t_close*1e3:.2f}ms  "
        f"cleanup={t_cleanup*1e3:.2f}ms  "
        f"total={total*1e3:.2f}ms"
    )
    return total
