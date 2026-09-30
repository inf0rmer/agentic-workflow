#!/usr/bin/env python3
"""Run the workspace suites (tests/test_workspace_*.py) with one process per test, in parallel.

Every test builds its own sandbox, so tests are independent; the suite is bound by
process start-up latency (thousands of git and team-guard calls), not CPU, and runs
several times faster in parallel.

  tests/run_workspace_tests.py [-j N] [pattern]     (default pattern: test_workspace_*.py)
"""

import argparse
import concurrent.futures
import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def test_ids(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from test_ids(t)
        else:
            yield t.id()


def run_one(tid):
    p = subprocess.run([sys.executable, "-m", "unittest", "-v", tid], cwd=HERE, capture_output=True, text=True)
    return tid, p.returncode, p.stderr + p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-j", type=int, default=min(16, (os.cpu_count() or 2) * 2))
    ap.add_argument("pattern", nargs="?", default="test_workspace_*.py")
    a = ap.parse_args()
    loader = unittest.TestLoader()
    suite = loader.discover(HERE, pattern=a.pattern, top_level_dir=HERE)
    if loader.errors:
        for e in loader.errors:
            print(e)
        return 1
    ids = sorted(test_ids(suite))
    start = time.time()
    failed, skipped = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.j) as pool:
        for tid, code, out in pool.map(run_one, ids):
            last = out.strip().splitlines()[-1] if out.strip() else ""
            if code:
                failed.append((tid, out))
                print("FAIL " + tid)
            elif "skipped" in last:
                skipped += 1
                print("skip " + tid + "  " + " ".join(l.split("... ", 1)[-1] for l in out.splitlines() if "... skipped" in l))
            else:
                print("ok   " + tid)
    for tid, out in failed:
        print("\n" + "=" * 70 + "\n" + tid + "\n" + out)
    print("\nran %d tests in %.1fs: %d failed, %d skipped" % (len(ids), time.time() - start, len(failed), skipped))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
