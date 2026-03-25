#!/usr/bin/env bash
# run_odirect_blocksize_sweep.sh — sweep block sizes 2–64 MB with O_DIRECT,
# comparing cpp, nixl POSIX, and python_self_imp backends.
#
# Block sizes tested: 2 4 8 16 32 64 MB
# All three backends run with O_DIRECT and no-rename.
#
# Background (survives logout) — pick one:
#
#   nohup (simplest, no reattach):
#     nohup bash run_odirect_blocksize_sweep.sh > sweep_odirect.log 2>&1 &
#     echo $!
#     tail -f sweep_odirect.log
#
#   tmux (reattachable):
#     tmux new -s odirect_sweep
#     bash run_odirect_blocksize_sweep.sh 2>&1 | tee sweep_odirect.log
#     Ctrl-B D  (detach)  /  tmux attach -t odirect_sweep  (reattach)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Benchmark parameters ───────────────────────────────────────────────────────
export STORAGE_PATH="/lustre/vish_tests/"

MODE="data"
TOTAL_GB=100
BLOCK_SIZES="2 4 8 16 32 64"   # MB — passed as a list to the benchmark tool
ITERATIONS=1
BUFFER_SIZE=100
THREADS=16
# ──────────────────────────────────────────────────────────────────────────────

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUMMARY_LOG="$LOG_DIR/odirect_blocksize_sweep_summary_${TIMESTAMP}.log"

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

# ── Run start banner ───────────────────────────────────────────────────────────
log "======================================================================"
log "  O_DIRECT BLOCK SIZE SWEEP  —  started at $(date)"
log "======================================================================"
log "  Mode:          $MODE"
log "  Total GB:      $TOTAL_GB"
log "  Block sizes:   $BLOCK_SIZES MB"
log "  Iterations:    $ITERATIONS"
log "  Buffer size:   $BUFFER_SIZE GB"
log "  Threads:       $THREADS"
log "  O_DIRECT:      yes (all backends)"
log "  No Rename:     yes (all backends)"
log "  Storage path:  $STORAGE_PATH"
log "  Results dir:   $SCRIPT_DIR/results/"
log "  Log dir:       $LOG_DIR/"
log "======================================================================"

COMMON="--mode $MODE --total-gb $TOTAL_GB --block-sizes $BLOCK_SIZES --iterations $ITERATIONS --buffer-size $BUFFER_SIZE --threads $THREADS --no-rename --o-direct"

# ── cpp: O_DIRECT, all block sizes in one run ──────────────────────────────────
run_benchmark "sweep_cpp_odirect" \
    "cpp | O_DIRECT | no-rename | threads: $THREADS | block-sizes: $BLOCK_SIZES MB" \
    $COMMON --backend cpp \
    --test-name sweep_cpp_odirect

# ── nixl POSIX: O_DIRECT, all block sizes in one run ──────────────────────────
run_benchmark "sweep_nixl_posix_odirect" \
    "nixl POSIX | O_DIRECT | no-rename | threads: $THREADS | block-sizes: $BLOCK_SIZES MB" \
    $COMMON --backend nixl --nixl-backend POSIX \
    --test-name sweep_nixl_posix_odirect

# ── python_self_imp: O_DIRECT, all block sizes in one run ─────────────────────
run_benchmark "sweep_python_odirect" \
    "python_self_imp | O_DIRECT | no-rename | threads: $THREADS | block-sizes: $BLOCK_SIZES MB" \
    $COMMON --backend python_self_imp \
    --test-name sweep_python_odirect

# ── Final summary ──────────────────────────────────────────────────────────────
log ""
log "======================================================================"
log "  DONE  —  finished at $(date)"
log "======================================================================"
log "  Results : $SCRIPT_DIR/results/"
log "  Logs    : $LOG_DIR/"
log "  Summary : $SUMMARY_LOG"
log "======================================================================"
