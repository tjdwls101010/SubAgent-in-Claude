#!/usr/bin/env python3
"""The suite split by test class and run several classes at once, for iterating while a change is made; the sequential `python3 -m unittest discover -s tests` stays the check before a merge.

    python3 tests/parallel.py                 # every class, as many at once as half the CPUs
    python3 tests/parallel.py -j 8 test_clean test_batch

Each class runs as its own `python3 -m unittest <module>.<Class>` from tests/, so classes share nothing but the machine. A class that fails while others run is run again alone: one that passes then is reported as failing under load (its timing assertions met the contention, not a fault), one that fails again is a failure. Exits 0 when nothing failed alone, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TESTS = Path(__file__).resolve().parent
RAN = re.compile(r"^Ran (\d+) tests?", re.M)


def classes(modules):
    """`module.Class` of every TestCase class with tests in `modules` (every test_*.py when empty)."""
    # Discovery puts tests/ on the import path itself, as `-s tests` does.
    loader = unittest.TestLoader()
    suites = [loader.discover(str(TESTS), pattern=f"{m.removesuffix('.py')}.py", top_level_dir=str(TESTS)) for m in modules] if modules else [loader.discover(str(TESTS), pattern="test_*.py", top_level_dir=str(TESTS))]
    found = []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                name = f"{type(t).__module__}.{type(t).__qualname__}"
                if name not in found:
                    found.append(name)

    for s in suites:
        walk(s)
    return found


def run(name):
    start = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "unittest", name], cwd=TESTS, capture_output=True, text=True)
    m = RAN.search(p.stderr)
    return {"name": name, "ok": p.returncode == 0, "tests": int(m.group(1)) if m else 0, "seconds": time.monotonic() - start, "output": p.stderr}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("modules", nargs="*", help="test modules to run (default: every test_*.py)")
    ap.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="classes run at once (default: half the CPUs)")
    args = ap.parse_args(argv)
    names = classes(args.modules)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, names))
    wall = time.monotonic() - start
    failed = [r for r in results if not r["ok"]]
    under_load, real = [], []
    for r in failed:
        again = run(r["name"])
        (under_load if again["ok"] else real).append(again if not again["ok"] else r)
    for r in real:
        print(f"=== FAILED {r['name']}\n{r['output']}", file=sys.stderr)
    total = sum(r["tests"] for r in results)
    print(f"Ran {total} tests in {len(names)} classes, {args.jobs} at once, in {wall:.0f} s; slowest: " + ", ".join(f"{r['name']} {r['seconds']:.0f} s" for r in sorted(results, key=lambda r: -r["seconds"])[:3]))
    if under_load:
        print("failed only under load, passed alone: " + ", ".join(r["name"] for r in under_load))
    print("FAILED: " + ", ".join(r["name"] for r in real) if real else "OK")
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())
