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
