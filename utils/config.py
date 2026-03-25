"""Configuration constants and environment variables for benchmarking."""

import os

# Storage path configuration - can be overridden by STORAGE_PATH environment variable
STORAGE_PATH = os.environ.get('STORAGE_PATH', '/dev/shm')

# Cluster name for tracking benchmark results
CLUSTER = os.environ.get('CLUSTER_NAME', 'unknown')

# List of Python-based backends (vs C++ backends)
PYTHON_BACKENDS = ["python_aiofiles", "python_self_imp", "nixl"]

# Whether to use O_DIRECT for I/O (bypasses OS page cache).
# Set to False for tmpfs paths like /dev/shm, which don't support O_DIRECT.
# Controlled at runtime via set_o_direct() called from the CLI.
USE_O_DIRECT: bool = True


def set_o_direct(enabled: bool) -> None:
    global USE_O_DIRECT
    USE_O_DIRECT = enabled


def get_o_direct() -> bool:
    return USE_O_DIRECT


# NIXL I/O backend selection. Valid values: "POSIX", "GDS_MT".
# Controlled at runtime via set_nixl_io_backend() called from the CLI.
NIXL_IO_BACKEND: str = "POSIX"

# Enable io_uring within the POSIX backend (passed as backend param use_uring=true).
# Only applicable when NIXL_IO_BACKEND == "POSIX".
NIXL_USE_URING: bool = False


def set_nixl_io_backend(backend: str) -> None:
    global NIXL_IO_BACKEND
    NIXL_IO_BACKEND = backend


def get_nixl_io_backend() -> str:
    return NIXL_IO_BACKEND


def set_nixl_use_uring(enabled: bool) -> None:
    global NIXL_USE_URING
    NIXL_USE_URING = enabled


def get_nixl_use_uring() -> bool:
    return NIXL_USE_URING


# GDS_MT internal thread count. None means use the GDS_MT default
# (hardware_concurrency / 2). Passed as backend param thread_count=N
# to a single NIXL agent; no agent splitting is used.
NIXL_GDS_MT_THREADS: int = None


def set_nixl_gds_mt_threads(n: int) -> None:
    global NIXL_GDS_MT_THREADS
    NIXL_GDS_MT_THREADS = n


def get_nixl_gds_mt_threads() -> int:
    return NIXL_GDS_MT_THREADS


# Whether to skip atomic publish (temp+rename or O_TMPFILE+linkat) and write
# directly to the final filename. Useful for measuring rename overhead.
NO_RENAME: bool = False

# Whether to pre-open all temp FDs sequentially in the main thread before
# dispatching NIXL worker threads (python_impl approach). When True, all
# O_CREAT opens are serialized in the caller, avoiding per-thread directory-inode
# contention. Set to False to revert to the legacy behavior where each worker
# thread opens its own FDs.
NIXL_PREOPEN_FDS: bool = True


def set_no_rename(enabled: bool) -> None:
    global NO_RENAME
    NO_RENAME = enabled


def get_no_rename() -> bool:
    return NO_RENAME


def set_nixl_preopen_fds(enabled: bool) -> None:
    global NIXL_PREOPEN_FDS
    NIXL_PREOPEN_FDS = enabled


def get_nixl_preopen_fds() -> bool:
    return NIXL_PREOPEN_FDS
