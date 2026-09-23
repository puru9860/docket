"""Run the docket unittest suite across processes and print one unittest summary.

Every test already works in its own temporary directory, so tests are independent
and each runs in its own `python -m unittest` process from a shared work queue.
The output keeps unittest's shape: one progress line per test, failure details, and
exactly one terminal `Ran N tests` summary, which is what `docket suite --qualify`
parses. `DOCKET_TEST_JOBS` sets the worker count; 1 runs the plain serial suite.

The run executes against a snapshot of the repository taken when it starts, so an
edit made while the suite runs never mixes old and new code inside one test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SEPARATOR = "-" * 70
SUMMARY = re.compile(r"^Ran (\d+) tests? in [\d.]+s$")
VERDICT = re.compile(r"^(OK|FAILED)(?: \((.*)\))?$")


def test_ids(start: Path) -> list[str]:
    """Every test id the default loader discovers under start."""
    found: list[str] = []

    def walk(suite: unittest.TestSuite) -> None:
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                walk(item)
            else:
                found.append(item.id())

    walk(unittest.defaultTestLoader.discover(str(start)))
    return found


def split_output(text: str) -> tuple[list[str], list[str], dict[str, int], bool]:
    """Progress lines, failure detail lines, counts, and whether the run was OK."""
    lines = text.splitlines()
    verdict_at = next((i for i in range(len(lines) - 1, -1, -1)
                       if VERDICT.match(lines[i].strip())), None)
    summary_at = next((i for i in range(len(lines) - 1, -1, -1)
                       if SUMMARY.match(lines[i].strip())), None)
    if verdict_at is None or summary_at is None or summary_at > verdict_at:
        return [], lines, {"errors": 1}, False
    body = lines[:summary_at]
    while body and body[-1].strip() in ("", SEPARATOR):
        body.pop()
    detail_at = next((i for i, line in enumerate(body) if line.startswith("=" * 70)), None)
    progress = body if detail_at is None else body[:detail_at]
    details = [] if detail_at is None else body[detail_at:]
    match = VERDICT.match(lines[verdict_at].strip())
    counts: dict[str, int] = {}
    for part in (match.group(2) or "").split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            counts[key.strip()] = int(value)
    return [line for line in progress if line.strip()], details, counts, match.group(1) == "OK"


def snapshot(start: Path, into: Path) -> Path:
    """Copy the files the suite reads into a repository layout; return its test dir."""
    repo = start.parents[2]
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("skills", "docs"):
        if (repo / name).is_dir():
            shutil.copytree(repo / name, into / name, ignore=skip)
    for path in repo.glob("*.md"):
        shutil.copy2(path, into / path.name)
    return into / start.relative_to(repo)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="docket-suite-") as frozen:
        return run(snapshot(Path(sys.argv[1]).resolve(), Path(frozen)))


def run(start: Path) -> int:
    jobs = int(os.environ.get("DOCKET_TEST_JOBS", "0") or 0) or max(2, (os.cpu_count() or 2) // 2)
    if jobs <= 1:
        return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(start),
                               "-v"]).returncode
    ids = test_ids(start)
    began = time.monotonic()
    lock = threading.Lock()
    totals: dict[str, int] = {}
    details: list[str] = []
    failed = False

    def run_one(test_id: str) -> None:
        nonlocal failed
        proc = subprocess.run([sys.executable, "-m", "unittest", "-v", test_id],
                              cwd=str(start), capture_output=True, text=True)
        progress, detail, counts, ok = split_output(proc.stderr)
        with lock:
            if proc.stdout:
                sys.stdout.write(proc.stdout)
                sys.stdout.flush()
            if not progress:
                progress = [f"{test_id} ... ERROR (no unittest summary, exit {proc.returncode})"]
                detail = ["=" * 70, f"ERROR: {test_id}", SEPARATOR,
                          *proc.stderr.splitlines()]
            for line in progress:
                print(line, file=sys.stderr)
            details.extend(detail)
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value
            if not ok or proc.returncode != 0:
                failed = True
            sys.stderr.flush()

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(run_one, ids))
    elapsed = time.monotonic() - began
    if details:
        print("", file=sys.stderr)
        for line in details:
            print(line, file=sys.stderr)
    print(f"\n{SEPARATOR}\nRan {len(ids)} tests in {elapsed:.3f}s\n", file=sys.stderr)
    order = ("failures", "errors", "skipped", "expected failures", "unexpected successes")
    extras = ", ".join(f"{key}={totals[key]}" for key in order if totals.get(key))
    if failed:
        print(f"FAILED ({extras})" if extras else "FAILED (errors=1)", file=sys.stderr)
        return 1
    print(f"OK ({extras})" if extras else "OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
