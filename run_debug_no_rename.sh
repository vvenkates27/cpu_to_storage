#!/usr/bin/env bash
# run_debug_no_rename.sh — quick single-run debug test comparing cpp and nixl
# backends with --no-rename (write directly to destination, skip temp+rename /
# O_TMPFILE+linkat).
#
# Equivalent to:
#   STORAGE_PATH=/lustre/vish_tests/ python3 compare_file_operations.py \
#     --mode data --total-gb 100 --block-sizes 16 --iterations 1 \
#     --buffer-size 100 --backend cpp --threads 16 --no-rename \
#     --test-name debug_cpp_no_rename
#
#   STORAGE_PATH=/lustre/vish_tests/ python3 compare_file_operations.py \
#     --mode data --total-gb 100 --block-sizes 16 --iterations 1 \
#     --buffer-size 100 --backend nixl --nixl-backend POSIX --no-o-direct \
#     --threads 16 --no-rename --test-name debug_nixl_posix_no_rename
#
# Background (survives logout) — pick one:
#
#   nohup (simplest, no reattach):
#     nohup bash run_debug_no_rename.sh > bench_debug_no_rename.log 2>&1 &
#     echo $!                            # save the PID
#     tail -f bench_debug_no_rename.log
#
#   screen (reattachable):
#     screen -S debug_no_rename
#     bash run_debug_no_rename.sh 2>&1 | tee bench_debug_no_rename.log
#     Ctrl-A D  (detach)  /  screen -r debug_no_rename  (reattach)
#
#   tmux (reattachable):
#     tmux new -s debug_no_rename
#     bash run_debug_no_rename.sh 2>&1 | tee bench_debug_no_rename.log
#     Ctrl-B D  (detach)  /  tmux attach -t debug_no_rename  (reattach)

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
THREADS=16
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUMMARY_LOG="$LOG_DIR/debug_no_rename_summary_${TIMESTAMP}.log"

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
    # remaining args are passed directly to compare_file_operations.py

    banner "$title"
    local log_file="$LOG_DIR/${test_name}_${TIMESTAMP}.log"

    log "  test-name : $test_name"
    log "  log file  : $log_file"
    log "  command   : STORAGE_PATH=$STORAGE_PATH python3 compare_file_operations.py $*"
    log ""

    # -u = unbuffered: streams output line-by-line instead of batching at the end
    # shellcheck disable=SC2068
    if python3 -u compare_file_operations.py $@ 2>&1 | tee "$log_file"; then
        log "✓ PASSED  $test_name"
    else
        log "✗ FAILED  $test_name  (exit ${PIPESTATUS[0]}) — continuing..."
    fi
}

# ── Run start banner ──────────────────────────────────────────────────────────
log "======================================================================"
log "  DEBUG NO-RENAME BENCHMARK  —  started at $(date)"
log "======================================================================"
log "  Mode:          $MODE"
log "  Total GB:      $TOTAL_GB"
log "  Block sizes:   $BLOCK_SIZES MB"
log "  Iterations:    $ITERATIONS"
log "  Buffer size:   $BUFFER_SIZE GB"
log "  Threads:       $THREADS"
log "  No Rename:     yes (both backends)"
log "  Storage path:  $STORAGE_PATH"
log "  Results dir:   $SCRIPT_DIR/results/"
log "  Log dir:       $LOG_DIR/"
log "======================================================================"

COMMON="--mode $MODE --total-gb $TOTAL_GB --block-sizes $BLOCK_SIZES --iterations $ITERATIONS --buffer-size $BUFFER_SIZE --threads $THREADS --no-rename"

# ── cpp no-rename ─────────────────────────────────────────────────────────────
run_benchmark "debug_cpp_no_rename" \
    "cpp | no-rename | threads: $THREADS | block-sizes: $BLOCK_SIZES MB" \
    $COMMON --backend cpp \
    --test-name debug_cpp_no_rename

# ── nixl POSIX no-rename, no O_DIRECT ─────────────────────────────────────────
run_benchmark "debug_nixl_posix_no_rename_no_odirect" \
    "nixl POSIX | no-rename | no O_DIRECT | threads: $THREADS | block-sizes: $BLOCK_SIZES MB" \
    $COMMON --backend nixl --nixl-backend POSIX --no-o-direct \
    --test-name debug_nixl_posix_no_rename_no_odirect

# ── nixl POSIX no-rename, O_DIRECT ────────────────────────────────────────────
run_benchmark "debug_nixl_posix_no_rename_odirect" \
    "nixl POSIX | no-rename | O_DIRECT | threads: $THREADS | block-sizes: $BLOCK_SIZES MB" \
    $COMMON --backend nixl --nixl-backend POSIX --o-direct \
    --test-name debug_nixl_posix_no_rename_odirect

# ── Final summary ─────────────────────────────────────────────────────────────
log ""
log "======================================================================"
log "  DONE  —  finished at $(date)"
log "======================================================================"
log "  Results : $SCRIPT_DIR/results/"
log "  Logs    : $LOG_DIR/"
log "  Summary : $SUMMARY_LOG"
log "======================================================================"
