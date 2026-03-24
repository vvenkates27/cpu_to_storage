#!/usr/bin/env bash
# run_debug_all.sh — run all debug variants (cpp, nixl POSIX libaio, nixl POSIX+uring) in sequence
#
# Tests (5 runs):
#   1.  cpp                      no O_DIRECT   threads 16
#   2.  nixl POSIX (libaio)      no O_DIRECT   threads 16
#   3.  nixl POSIX (libaio)      O_DIRECT      threads 16
#   4.  nixl POSIX + io_uring    no O_DIRECT   threads 16
#   5.  nixl POSIX + io_uring    O_DIRECT      threads 16
#
# Background (survives logout) — pick one:
#
#   nohup (simplest, no reattach):
#     nohup bash run_debug_all.sh > bench_debug_all.log 2>&1 &
#     echo $!                        # save the PID
#     tail -f bench_debug_all.log    # watch live output
#
#   screen (reattachable):
#     screen -S debug_all
#     bash run_debug_all.sh 2>&1 | tee bench_debug_all.log
#     Ctrl-A D  (detach)  /  screen -r debug_all  (reattach)
#
#   tmux (reattachable):
#     tmux new -s debug_all
#     bash run_debug_all.sh 2>&1 | tee bench_debug_all.log
#     Ctrl-B D  (detach)  /  tmux attach -t debug_all  (reattach)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Benchmark parameters ──────────────────────────────────────────────────────
export STORAGE_PATH="/lustre/vish_tests/"

MODE="data"
TOTAL_GB=100
BLOCK_SIZES="16"
ITERATIONS=3         # ← change this to run each test N times
BUFFER_SIZE=100
THREADS=16
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUMMARY_LOG="$LOG_DIR/debug_all_summary_${TIMESTAMP}.log"

TOTAL_TESTS=5
CURRENT_TEST=0

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$SUMMARY_LOG"
}

banner() {
    local title="$1"
    log ""
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "  TEST ${CURRENT_TEST}/${TOTAL_TESTS}: ${title}"
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

run_benchmark() {
    local test_name="$1"
    local title="$2"
    shift 2

    CURRENT_TEST=$((CURRENT_TEST + 1))
    banner "$title"
    local log_file="$LOG_DIR/${test_name}_${TIMESTAMP}.log"

    log "  test-name : $test_name"
    log "  log file  : $log_file"
    log "  command   : STORAGE_PATH=$STORAGE_PATH python3 compare_file_operations.py $*"
    log ""

    # shellcheck disable=SC2068
    if python3 -u compare_file_operations.py $@ 2>&1 | tee "$log_file"; then
        log "✓ PASSED  $test_name"
    else
        log "✗ FAILED  $test_name  (exit ${PIPESTATUS[0]}) — continuing..."
    fi
}

# ── Run start banner ──────────────────────────────────────────────────────────
log "======================================================================"
log "  DEBUG ALL BENCHMARK SUITE  —  started at $(date)"
log "======================================================================"
log "  Mode:          $MODE"
log "  Total GB:      $TOTAL_GB"
log "  Block sizes:   $BLOCK_SIZES MB"
log "  Iterations:    $ITERATIONS"
log "  Buffer size:   $BUFFER_SIZE GB"
log "  Threads:       $THREADS"
log "  Total tests:   $TOTAL_TESTS"
log "  Storage path:  $STORAGE_PATH"
log "  Results dir:   $SCRIPT_DIR/results/"
log "  Log dir:       $LOG_DIR/"
log "======================================================================"

COMMON="--mode $MODE --total-gb $TOTAL_GB --block-sizes $BLOCK_SIZES --iterations $ITERATIONS --buffer-size $BUFFER_SIZE --threads $THREADS"

# ── 1. cpp  —  no O_DIRECT ────────────────────────────────────────────────────
run_benchmark "debug_cpp" \
    "cpp | no O_DIRECT | threads: $THREADS" \
    $COMMON --backend cpp --no-o-direct \
    --test-name debug_cpp

# ── 2. nixl POSIX (libaio)  —  no O_DIRECT ───────────────────────────────────
run_benchmark "debug_nixl_posix_no_odirect" \
    "nixl POSIX (libaio) | no O_DIRECT | threads: $THREADS" \
    $COMMON --backend nixl --nixl-backend POSIX --no-o-direct \
    --test-name debug_nixl_posix_no_odirect

# ── 3. nixl POSIX (libaio)  —  O_DIRECT ──────────────────────────────────────
run_benchmark "debug_nixl_posix_odirect" \
    "nixl POSIX (libaio) | O_DIRECT | threads: $THREADS" \
    $COMMON --backend nixl --nixl-backend POSIX --o-direct \
    --test-name debug_nixl_posix_odirect

# ── 4. nixl POSIX + io_uring  —  no O_DIRECT ─────────────────────────────────
run_benchmark "debug_nixl_posix_uring_no_odirect" \
    "nixl POSIX+io_uring | no O_DIRECT | threads: $THREADS" \
    $COMMON --backend nixl --nixl-backend POSIX --nixl-use-uring --no-o-direct \
    --test-name debug_nixl_posix_uring_no_odirect

# ── 5. nixl POSIX + io_uring  —  O_DIRECT ────────────────────────────────────
run_benchmark "debug_nixl_posix_uring_odirect" \
    "nixl POSIX+io_uring | O_DIRECT | threads: $THREADS" \
    $COMMON --backend nixl --nixl-backend POSIX --nixl-use-uring --o-direct \
    --test-name debug_nixl_posix_uring_odirect

# ── Final summary ─────────────────────────────────────────────────────────────
log ""
log "======================================================================"
log "  ALL $TOTAL_TESTS TESTS COMPLETE  —  finished at $(date)"
log "======================================================================"
log "  Results : $SCRIPT_DIR/results/"
log "  Logs    : $LOG_DIR/"
log "  Summary : $SUMMARY_LOG"
log "======================================================================"
