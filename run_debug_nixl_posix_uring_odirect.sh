#!/usr/bin/env bash
# run_debug_nixl_posix_uring_odirect.sh — quick single-run debug test for nixl POSIX+io_uring backend, O_DIRECT
#
# Equivalent to:
#   STORAGE_PATH=/lustre/vish_tests/ python3 compare_file_operations.py \
#     --mode data --total-gb 100 --block-sizes 16 --iterations 1 \
#     --buffer-size 100 --backend nixl --nixl-backend POSIX --nixl-use-uring --o-direct \
#     --threads 16 --test-name debug_nixl_posix_uring_odirect
#
# Background (survives logout) — pick one:
#
#   nohup (simplest, no reattach):
#     nohup bash run_debug_nixl_posix_uring_odirect.sh > bench_debug_nixl_posix_uring_odirect.log 2>&1 &
#     echo $!                            # save the PID
#     tail -f bench_debug_nixl_posix_uring_odirect.log
#
#   screen (reattachable):
#     screen -S debug_nixl_posix_uring_odirect
#     bash run_debug_nixl_posix_uring_odirect.sh 2>&1 | tee bench_debug_nixl_posix_uring_odirect.log
#     Ctrl-A D  (detach)  /  screen -r debug_nixl_posix_uring_odirect  (reattach)
#
#   tmux (reattachable):
#     tmux new -s debug_nixl_posix_uring_odirect
#     bash run_debug_nixl_posix_uring_odirect.sh 2>&1 | tee bench_debug_nixl_posix_uring_odirect.log
#     Ctrl-B D  (detach)  /  tmux attach -t debug_nixl_posix_uring_odirect  (reattach)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Benchmark parameters ──────────────────────────────────────────────────────
export STORAGE_PATH="/lustre/vish_tests/"

MODE="data"
TOTAL_GB=100
BLOCK_SIZES="16"
ITERATIONS=1
BUFFER_SIZE=100
BACKEND="nixl"
NIXL_BACKEND="POSIX"
THREADS=16
TEST_NAME="debug_nixl_posix_uring_odirect"
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUMMARY_LOG="$LOG_DIR/${TEST_NAME}_summary_${TIMESTAMP}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$SUMMARY_LOG"
}

banner() {
    local title="$1"
    log ""
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "  TEST: ${title}"
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

run_benchmark() {
    local test_name="$1"
    local title="$2"
    shift 2

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
log "  DEBUG nixl POSIX+io_uring O_DIRECT  —  started at $(date)"
log "======================================================================"
log "  Mode:          $MODE"
log "  Total GB:      $TOTAL_GB"
log "  Block sizes:   $BLOCK_SIZES MB"
log "  Iterations:    $ITERATIONS"
log "  Buffer size:   $BUFFER_SIZE GB"
log "  Backend:       $BACKEND / $NIXL_BACKEND + io_uring"
log "  O_DIRECT:      yes"
log "  Threads:       $THREADS"
log "  Test name:     $TEST_NAME"
log "  Storage path:  $STORAGE_PATH"
log "  Results dir:   $SCRIPT_DIR/results/"
log "  Log dir:       $LOG_DIR/"
log "======================================================================"

COMMON="--mode $MODE --total-gb $TOTAL_GB --block-sizes $BLOCK_SIZES --iterations $ITERATIONS --buffer-size $BUFFER_SIZE"

# ── debug_nixl_posix_uring_odirect ────────────────────────────────────────────
run_benchmark "$TEST_NAME" \
    "nixl POSIX+io_uring | O_DIRECT | threads: $THREADS | block-sizes: $BLOCK_SIZES MB" \
    $COMMON --backend $BACKEND --nixl-backend $NIXL_BACKEND --nixl-use-uring --o-direct \
    --threads $THREADS \
    --test-name $TEST_NAME

# ── Final summary ─────────────────────────────────────────────────────────────
log ""
log "======================================================================"
log "  DONE  —  finished at $(date)"
log "======================================================================"
log "  Results : $SCRIPT_DIR/results/"
log "  Logs    : $LOG_DIR/"
log "  Summary : $SUMMARY_LOG"
log "======================================================================"
