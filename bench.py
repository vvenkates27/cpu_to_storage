#!/usr/bin/env python3
"""bench.py — add, remove, and run I/O benchmark tests.

Commands:
  list               List all registered tests
  add  <name> [args] Add or update a test
  rm   <name>        Remove a test
  show <name>        Show a test's full command
  run  [names...]    Run all tests, or named tests only

Examples:
  ./bench.py list
  ./bench.py add my_test --backend cpp --no-o-direct --threads 1 2 4 --block-sizes 2 4 8
  ./bench.py show my_test
  ./bench.py rm   my_test
  ./bench.py run
  ./bench.py run cpp_no_odirect nixl_posix_uring_odirect
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REGISTRY = Path(__file__).parent / "tests.json"
LOG_DIR  = Path(__file__).parent / "logs"
SCRIPT   = Path(__file__).parent / "compare_file_operations.py"


# ── Registry helpers ──────────────────────────────────────────────────────────

def load_registry() -> list:
    if not REGISTRY.exists():
        return []
    with open(REGISTRY) as f:
        return json.load(f).get("tests", [])


def save_registry(tests: list):
    with open(REGISTRY, "w") as f:
        json.dump({"tests": tests}, f, indent=2)


def find_test(tests: list, name: str) -> dict | None:
    return next((t for t in tests if t["name"] == name), None)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list(args):
    tests = load_registry()
    if not tests:
        print("No tests registered. Use './bench.py add <name> [args...]' to add one.")
        return
    width = max(len(t["name"]) for t in tests)
    print(f"\n{'#':<5}{'NAME':<{width + 3}}ARGS")
    print("─" * 100)
    for i, t in enumerate(tests, 1):
        print(f"{i:<5}{t['name']:<{width + 3}}{' '.join(t['args'])}")
    print(f"\n{len(tests)} test(s) registered.\n")


def cmd_show(args):
    tests = load_registry()
    t = find_test(tests, args.name)
    if not t:
        print(f"Error: test '{args.name}' not found.")
        sys.exit(1)
    full_cmd = ["python3", str(SCRIPT)] + t["args"] + ["--test-name", t["name"]]
    print(f"\nName : {t['name']}")
    print(f"Cmd  : {' '.join(full_cmd)}\n")


def cmd_add(args):
    if not args.test_args:
        print("Error: provide at least one argument for the test (e.g. --backend cpp).")
        sys.exit(1)
    tests = load_registry()
    existing = find_test(tests, args.name)
    entry = {"name": args.name, "args": args.test_args}
    if existing:
        existing["args"] = args.test_args
        print(f"Updated : {args.name}")
    else:
        tests.append(entry)
        print(f"Added   : {args.name}")
    save_registry(tests)


def cmd_remove(args):
    tests = load_registry()
    filtered = [t for t in tests if t["name"] != args.name]
    if len(filtered) == len(tests):
        print(f"Error: test '{args.name}' not found.")
        sys.exit(1)
    save_registry(filtered)
    print(f"Removed : {args.name}")


def _run_one(t: dict, idx: int, total: int, timestamp: str) -> bool:
    name = t["name"]
    cmd  = ["python3", str(SCRIPT)] + t["args"] + ["--test-name", name]

    bar    = "━" * 80
    header = (f"\n{bar}\n"
              f"  [{idx}/{total}] {name}\n"
              f"  {' '.join(cmd)}\n"
              f"{bar}")
    print(header, flush=True)

    log_file = LOG_DIR / f"{name}_{timestamp}.log"
    start    = time.perf_counter()

    try:
        with open(log_file, "w") as lf:
            lf.write(header + "\n\n")
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout:
                print(line, end="", flush=True)
                lf.write(line)
            proc.wait()

        elapsed = time.perf_counter() - start
        ok      = proc.returncode == 0
        mark    = "✓ PASSED" if ok else "✗ FAILED"
        print(f"\n{mark}  {name}  ({elapsed:.0f}s)  →  {log_file}", flush=True)
        return ok

    except Exception as e:
        print(f"\n✗ ERROR  {name}: {e}", flush=True)
        return False


def cmd_run(args):
    tests = load_registry()
    if not tests:
        print("No tests registered.")
        return

    if args.names:
        selected = []
        for name in args.names:
            t = find_test(tests, name)
            if t is None:
                print(f"Warning: '{name}' not found — skipping.")
            else:
                selected.append(t)
    else:
        selected = tests

    if not selected:
        print("No tests to run.")
        return

    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    total     = len(selected)

    print(f"\n{'=' * 80}")
    print(f"  BENCHMARK RUN — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Running {total} test(s)")
    print(f"  Logs → {LOG_DIR}/")
    print(f"{'=' * 80}")

    results = []
    for i, t in enumerate(selected, 1):
        ok = _run_one(t, i, total, timestamp)
        results.append((t["name"], ok))

    passed = sum(ok for _, ok in results)
    failed = total - passed

    summary_log = LOG_DIR / f"summary_{timestamp}.log"
    with open(summary_log, "w") as f:
        f.write(f"Run: {datetime.now()}\n\n")
        for name, ok in results:
            f.write(f"{'PASS' if ok else 'FAIL'}  {name}\n")

    print(f"\n{'=' * 80}")
    print(f"  {passed}/{total} passed   {'all ok' if not failed else f'{failed} failed'}")
    print(f"  Summary → {summary_log}")
    print(f"{'=' * 80}")
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'}  {name}")
    print()

    if failed:
        sys.exit(1)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all registered tests")

    p_show = sub.add_parser("show", help="Show a test's full command")
    p_show.add_argument("name")

    p_add = sub.add_parser("add", help="Add or update a test")
    p_add.add_argument("name", help="Unique test name (also used as --test-name)")
    p_add.add_argument(
        "test_args", nargs=argparse.REMAINDER,
        help="Arguments forwarded to compare_file_operations.py"
    )

    p_rm = sub.add_parser("rm", help="Remove a test")
    p_rm.add_argument("name")

    p_run = sub.add_parser("run", help="Run all tests, or specific tests by name")
    p_run.add_argument("names", nargs="*", help="Names to run (default: all)")

    args = parser.parse_args()
    {
        "list": cmd_list,
        "show": cmd_show,
        "add":  cmd_add,
        "rm":   cmd_remove,
        "run":  cmd_run,
    }[args.command](args)


if __name__ == "__main__":
    main()
