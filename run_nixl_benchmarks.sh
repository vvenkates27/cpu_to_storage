#!/usr/bin/env bash
# run_nixl_benchmarks.sh — comprehensive I/O benchmark across cpp, python_self_imp, and NIXL backends
#
# Test inventory (18 runs):
#   1.  nixl POSIX          no O_DIRECT   agent-split threads 1 2 4 8
#   2.  nixl POSIX          O_DIRECT      agent-split threads 1 2 4 8
#   3.  nixl POSIX+uring    no O_DIRECT   agent-split threads 1 2 4 8
#   4.  nixl POSIX+uring    O_DIRECT      agent-split threads 1 2 4 8
#   5.  nixl GDS_MT         no O_DIRECT   1 agent, gds_threads 8 16 32 64 128
#   6.  nixl GDS_MT         O_DIRECT      1 agent, gds_threads 8 16 32 64 128
#   7.  cpp                 no O_DIRECT   threads 1 2 4 8 16 32
#   8.  cpp                 O_DIRECT      threads 1 2 4 8 16 32
#   9.  python_self_imp     no O_DIRECT   threads 1 2 4 8 16 32
#  10.  python_self_imp     O_DIRECT      threads 1 2 4 8 16 32
#
# Usage:
#   bash run_nixl_benchmarks.sh
#
# Background (survives logout) — pick one:
#
#   nohup (simplest, no reattach):
#     nohup bash run_nixl_benchmarks.sh > bench_full.log 2>&1 &
#     echo $!                            # save the PID
#     tail -f bench_full.log             # watch live output
#
#   screen (reattachable):
#     screen -S bench
#     bash run_nixl_benchmarks.sh 2>&1 | tee bench_full.log
#     Ctrl-A D  (detach)  /  screen -r bench  (reattach)
#
#   tmux (reattachable):
#     tmux new -s bench
#     bash run_nixl_benchmarks.sh 2>&1 | tee bench_full.log
#     Ctrl-B D  (detach)  /  tmux attach -t bench  (reattach)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Benchmark parameters ──────────────────────────────────────────────────────
MODE="data"
TOTAL_GB=100
BLOCK_SIZES="2 4 8 16 32 64"
ITERATIONS=5
BUFFER_SIZE=100

THREADS_STANDARD="1 2 4 8 16 32"   # cpp / python_self_imp
THREADS_NIXL_SPLIT="1 2 4 8"       # NIXL agent-split (experimental)
GDS_MT_THREADS="8 16 32 64 128"    # GDS_MT internal thread counts (1 NIXL agent each)
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUMMARY_LOG="$LOG_DIR/nixl_benchmark_summary_${TIMESTAMP}.log"

TOTAL_TESTS=18
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
    # remaining args are passed directly to compare_file_operations.py

    CURRENT_TEST=$((CURRENT_TEST + 1))
    banner "$title"
    local log_file="$LOG_DIR/${test_name}_${TIMESTAMP}.log"

    log "  test-name : $test_name"
    log "  log file  : $log_file"
    log "  command   : python3 compare_file_operations.py $*"
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
log "  NIXL BENCHMARK SUITE  —  started at $(date)"
log "======================================================================"
log "  Mode:          $MODE"
log "  Total GB:      $TOTAL_GB"
log "  Block sizes:   $BLOCK_SIZES MB"
log "  Iterations:    $ITERATIONS"
log "  Buffer size:   $BUFFER_SIZE GB"
log "  Total tests:   $TOTAL_TESTS"
log "  Results dir:   $SCRIPT_DIR/results/"
log "  Log dir:       $LOG_DIR/"
log "======================================================================"

COMMON="--mode $MODE --total-gb $TOTAL_GB --block-sizes $BLOCK_SIZES --iterations $ITERATIONS --buffer-size $BUFFER_SIZE"

# ── 1. NIXL POSIX  —  no O_DIRECT  —  agent split ────────────────────────────
run_benchmark "nixl_posix_no_odirect" \
    "nixl POSIX | no O_DIRECT | agent-split threads: $THREADS_NIXL_SPLIT" \
    $COMMON --backend nixl --nixl-backend POSIX --no-o-direct \
    --threads $THREADS_NIXL_SPLIT \
    --test-name nixl_posix_no_odirect

# ── 2. NIXL POSIX  —  O_DIRECT  —  agent split ───────────────────────────────
run_benchmark "nixl_posix_odirect" \
    "nixl POSIX | O_DIRECT | agent-split threads: $THREADS_NIXL_SPLIT" \
    $COMMON --backend nixl --nixl-backend POSIX --o-direct \
    --threads $THREADS_NIXL_SPLIT \
    --test-name nixl_posix_odirect

# ── 3. NIXL POSIX + io_uring  —  no O_DIRECT  —  agent split ─────────────────
run_benchmark "nixl_posix_uring_no_odirect" \
    "nixl POSIX+io_uring | no O_DIRECT | agent-split threads: $THREADS_NIXL_SPLIT" \
    $COMMON --backend nixl --nixl-backend POSIX --nixl-use-uring --no-o-direct \
    --threads $THREADS_NIXL_SPLIT \
    --test-name nixl_posix_uring_no_odirect

# ── 4. NIXL POSIX + io_uring  —  O_DIRECT  —  agent split ───────────────────
run_benchmark "nixl_posix_uring_odirect" \
    "nixl POSIX+io_uring | O_DIRECT | agent-split threads: $THREADS_NIXL_SPLIT" \
    $COMMON --backend nixl --nixl-backend POSIX --nixl-use-uring --o-direct \
    --threads $THREADS_NIXL_SPLIT \
    --test-name nixl_posix_uring_odirect

# ── 5 & 6. NIXL GDS_MT  —  1 NIXL agent  —  varying internal thread counts ───
for GDS_T in $GDS_MT_THREADS; do

    # 5. no O_DIRECT
    run_benchmark "nixl_gds_mt_t${GDS_T}_no_odirect" \
        "nixl GDS_MT | no O_DIRECT | 1 agent | gds_threads=${GDS_T}" \
        $COMMON --backend nixl --nixl-backend GDS_MT --nixl-gds-threads "$GDS_T" \
        --no-o-direct --threads 1 \
        --test-name "nixl_gds_mt_t${GDS_T}_no_odirect"

    # 6. O_DIRECT
    run_benchmark "nixl_gds_mt_t${GDS_T}_odirect" \
        "nixl GDS_MT | O_DIRECT | 1 agent | gds_threads=${GDS_T}" \
        $COMMON --backend nixl --nixl-backend GDS_MT --nixl-gds-threads "$GDS_T" \
        --o-direct --threads 1 \
        --test-name "nixl_gds_mt_t${GDS_T}_odirect"

done

# ── 7. cpp  —  no O_DIRECT ────────────────────────────────────────────────────
run_benchmark "cpp_no_odirect" \
    "cpp | no O_DIRECT | threads: $THREADS_STANDARD" \
    $COMMON --backend cpp --no-o-direct --threads $THREADS_STANDARD \
    --test-name cpp_no_odirect

# ── 8. cpp  —  O_DIRECT ───────────────────────────────────────────────────────
run_benchmark "cpp_odirect" \
    "cpp | O_DIRECT | threads: $THREADS_STANDARD" \
    $COMMON --backend cpp --o-direct --threads $THREADS_STANDARD \
    --test-name cpp_odirect

# ── 9. python_self_imp  —  no O_DIRECT ───────────────────────────────────────
run_benchmark "python_no_odirect" \
    "python_self_imp | no O_DIRECT | threads: $THREADS_STANDARD" \
    $COMMON --backend python_self_imp --no-o-direct --threads $THREADS_STANDARD \
    --test-name python_no_odirect

# ── 10. python_self_imp  —  O_DIRECT ──────────────────────────────────────────
run_benchmark "python_odirect" \
    "python_self_imp | O_DIRECT | threads: $THREADS_STANDARD" \
    $COMMON --backend python_self_imp --o-direct --threads $THREADS_STANDARD \
    --test-name python_odirect

# ── Final summary ─────────────────────────────────────────────────────────────
log ""
log "======================================================================"
log "  ALL $TOTAL_TESTS TESTS COMPLETE  —  finished at $(date)"
log "======================================================================"
log "  Results : $SCRIPT_DIR/results/"
log "  Logs    : $LOG_DIR/"
log "  Summary : $SUMMARY_LOG"
log "======================================================================"
