import aiofiles.os
from typing import Any
import time
import asyncio
import os
import utils.config as config

# Storage path configuration - can be overridden by STORAGE_PATH environment variable
STORAGE_PATH = os.environ.get('STORAGE_PATH', '/dev/shm')

_O_DIRECT_FLAG = getattr(os, 'O_DIRECT', 0)


def _open_o_direct(path: str, flags: int, mode: int = 0o644) -> int:
    if config.USE_O_DIRECT and _O_DIRECT_FLAG:
        try:
            return os.open(path, flags | _O_DIRECT_FLAG, mode)
        except OSError:
            pass
    return os.open(path, flags, mode)


def _write_sync(temp_name: str, final_name: str, data: memoryview) -> bool:
    fd = _open_o_direct(temp_name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        total = 0
        n = len(data)
        while total < n:
            total += os.write(fd, data[total:])
    finally:
        os.close(fd)
    os.replace(temp_name, final_name)
    return True


def _read_sync(file_name: str, buf: memoryview) -> bool:
    fd = _open_o_direct(file_name, os.O_RDONLY)
    try:
        total = 0
        n = len(buf)
        while total < n:
            chunk = os.read(fd, n - total)
            if not chunk:
                break
            buf[total:total + len(chunk)] = chunk
            total += len(chunk)
    finally:
        os.close(fd)
    return total == n


async def write_and_rename(block_size, block_inx, buffer_view, temp_name, final_name):
    """Encapsulates the logic for a single buffer operation."""
    try:
        loop = asyncio.get_running_loop()
        data = buffer_view[block_inx * block_size: (block_inx + 1) * block_size]
        await loop.run_in_executor(None, _write_sync, temp_name, final_name, data)
        return True
    except Exception as e:
        print(f"Error processing {final_name}: {e}")
        if await aiofiles.os.path.exists(temp_name):
            await aiofiles.os.unlink(temp_name)
        return False

async def read_block_from_file(block_size, block_inx, buffer_view, file_name):
    """Encapsulates the logic for reading a single block from file."""
    try:
        loop = asyncio.get_running_loop()
        buf = buffer_view[block_inx * block_size: (block_inx + 1) * block_size]
        ok = await loop.run_in_executor(None, _read_sync, file_name, buf)
        if not ok:
            print(f"read mismatch for {file_name}")
        return ok
    except Exception as e:
        print(f"Error reading {file_name}: {e}")
        return False

async def aiofiles_write_blocks(block_size, buffer_view, block_indices, dest_files):
    tasks: list[Any] = []

    start = time.perf_counter()
    for i,block_inx in enumerate(block_indices):
        tasks.append(write_and_rename(block_size, block_inx, buffer_view, f"{STORAGE_PATH}/temp_block_{block_inx}.bin", dest_files[i]))
    
    results = await asyncio.gather(*tasks)
    end: float = time.perf_counter()
    return (end -start)

async def aiofiles_read_blocks(block_size, buffer_view, block_indices, dest_files):
    tasks: list[Any] = []

    start = time.perf_counter()
    for i,block_inx in enumerate(block_indices):
        tasks.append(read_block_from_file(block_size, block_inx, buffer_view, dest_files[i]))
    
    results = await asyncio.gather(*tasks)
    end: float = time.perf_counter()
    for result in results:
        if not result:
            print(f"Reading blocks with aiofiles Failed")
            return
    return (end -start)