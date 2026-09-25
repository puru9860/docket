"""Run the docket unittest suite in reusable worker processes and print one summary.

Every test already works in its own temporary directory, so tests are independent.
A few long-lived workers (`DOCKET_TEST_JOBS`, default half the CPUs) each start one
interpreter, import the suite once, and run test after test from a shared queue. A
worker restores the environment and working directory after every test, so one test's
leftovers never reach the next. `DOCKET_TEST_JOBS=1` is a single worker, so the serial
run gets the same deadline and the same output.

Each test is bounded by `DOCKET_TEST_TIMEOUT` seconds (default 300). A test that runs
past it is reported as an error, its worker's whole process group is killed, and a
fresh worker takes the rest of the queue. A worker that dies mid-test is reported the
same way. Tests get a `TMPDIR` the runner owns and removes, so a killed test leaves no
temporary files behind.

The output keeps unittest's shape: one progress line per test, failure details, and
exactly one terminal `Ran N tests` summary, which is what `docket suite --qualify`
parses. A test's own stdout goes to stdout and its own stderr to stderr.

The run executes against a snapshot of the repository taken when it starts, so an
edit made while the suite runs never mixes old and new code inside one test.
"""

from __future__ import annotations

import io
import json
import math
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SEPARATOR = "-" * 70
DEFAULT_TIMEOUT = 300.0


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


def timeout_seconds(env: dict[str, str]) -> float:
    """The per-test deadline from DOCKET_TEST_TIMEOUT, 300 seconds when unset."""
    raw = env.get("DOCKET_TEST_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not (value > 0 and math.isfinite(value)):
        raise SystemExit(f"DOCKET_TEST_TIMEOUT must be a positive number of seconds, got {raw!r}")
    return value


def snapshot(start: Path, into: Path) -> Path:
    """Copy the files the suite reads into a repository layout; return its test dir."""
    repo = start.parents[2]
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("skills", "docs", "tests"):
        if (repo / name).is_dir():
            shutil.copytree(repo / name, into / name, ignore=skip)
    for path in [*repo.glob("*.md"), repo / ".gitignore"]:
        if path.is_file():
            shutil.copy2(path, into / path.name)
    return into / start.relative_to(repo)


class Stream(io.StringIO):
    """The stream shape unittest's text result writes to."""

    def writeln(self, text: str = "") -> None:
        self.write(text + "\n")


class LoadFailure(unittest.TestCase):
    """Stands in for a test id the loader could not turn into a test."""

    def __init__(self, test_id: str) -> None:
        super().__init__()
        self.test_id = test_id

    def runTest(self) -> None:
        pass

    def __str__(self) -> str:
        return self.test_id

    def shortDescription(self) -> None:
        return None


def run_test(test_id: str) -> dict:
    """Run one test id in this process and restore what the test may have changed."""
    environ = dict(os.environ)
    cwd = os.getcwd()
    result = unittest.TextTestResult(Stream(), True, 2)
    try:
        unittest.defaultTestLoader.loadTestsFromName(test_id)(result)
    except Exception:
        failure = LoadFailure(test_id)
        result.startTest(failure)
        result.addError(failure, sys.exc_info())
        result.stopTest(failure)
    finally:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(environ)
    progress = result.stream.getvalue()
    result.stream = Stream()
    result.printErrors()
    counts = {"failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped),
              "expected failures": len(result.expectedFailures),
              "unexpected successes": len(result.unexpectedSuccesses)}
    return {"progress": progress, "details": result.stream.getvalue().lstrip("\n"),
            "counts": counts, "ok": result.wasSuccessful()}


def write_all(fd: int, data: bytes) -> None:
    while data:
        data = data[os.write(fd, data):]


def worker(start: Path, commands: int, results: int, capture: Path) -> None:
    """Run each test id read from `commands`; answer each with one JSON line."""
    os.set_inheritable(commands, False)
    os.set_inheritable(results, False)
    # What `python -m unittest` puts first, so test modules import from start.
    sys.path[0] = str(start)
    # O_APPEND lets a per-test truncate restart the capture at offset 0.
    for fd, name in ((1, "stdout"), (2, "stderr")):
        opened = os.open(capture / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(opened, fd)
        os.close(opened)
    with os.fdopen(commands, encoding="utf-8") as queue:
        for line in queue:
            sys.stdout.flush()
            sys.stderr.flush()
            os.ftruncate(1, 0)
            os.ftruncate(2, 0)
            record = run_test(line.strip())
            sys.stdout.flush()
            sys.stderr.flush()
            write_all(results, (json.dumps(record) + "\n").encode())


class Worker:
    """One long-lived test process in its own process group, and its pipes."""

    def __init__(self, runner: Path, start: Path, work: Path, env: dict[str, str]) -> None:
        commands, self.commands = os.pipe()
        self.results, results = os.pipe()
        self.capture = Path(tempfile.mkdtemp(prefix="io-", dir=work))
        self.proc = subprocess.Popen(
            [sys.executable, str(runner), "--worker", str(start), str(commands),
             str(results), str(self.capture)],
            cwd=str(start), env=env, stdin=subprocess.DEVNULL,
            pass_fds=(commands, results), start_new_session=True)
        os.close(commands)
        os.close(results)
        self.pending = b""
        self.reaping = threading.Lock()

    def run(self, test_id: str, timeout: float) -> tuple[dict | None, str]:
        """The test's result record, or None and why the worker gave none."""
        try:
            write_all(self.commands, (test_id + "\n").encode())
        except BrokenPipeError:
            return None, self.exited()
        deadline = time.monotonic() + timeout
        while b"\n" not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.results], [], [], left)[0]:
                return None, f"exceeded DOCKET_TEST_TIMEOUT={timeout:g}s"
            chunk = os.read(self.results, 65536)
            if not chunk:
                return None, self.exited()
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        return json.loads(line), ""

    def exited(self) -> str:
        try:
            code = self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return "worker closed its result pipe without exiting"
        return f"worker exited with code {code} before reporting"

    def output(self) -> tuple[str, str]:
        return tuple(  # type: ignore[return-value]
            (self.capture / name).read_bytes().decode("utf-8", "replace")
            if (self.capture / name).exists() else ""
            for name in ("stdout", "stderr"))

    def close(self) -> None:
        """End a healthy worker: it exits when its command pipe closes."""
        os.close(self.commands)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.kill()
        os.close(self.results)

    def kill(self) -> None:
        """Kill the worker's whole process group, then reap the worker."""
        with self.reaping:
            # Until the worker is reaped its pid, and so its group id, cannot be reused.
            if self.proc.returncode is None:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait()

    def retire(self) -> None:
        """Kill the worker and close its pipes; only the thread driving it calls this."""
        self.kill()
        os.close(self.commands)
        os.close(self.results)


def main() -> int:
    if sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5]))
        return 0
    with tempfile.TemporaryDirectory(prefix="docket-suite-") as frozen, \
            tempfile.TemporaryDirectory(prefix="docket-suite-work-") as work:
        start = snapshot(Path(sys.argv[1]).resolve(), Path(frozen))
        # Workers run this runner as it was at start, never the live file.
        runner = Path(shutil.copy2(__file__, Path(work) / "parallel.py"))
        return run(start, runner, Path(work))


def run(start: Path, runner: Path, work: Path) -> int:
    jobs = int(os.environ.get("DOCKET_TEST_JOBS", "0") or 0) or max(2, (os.cpu_count() or 2) // 2)
    timeout = timeout_seconds(os.environ)
    ids = test_ids(start)
    queue = iter(ids)
    began = time.monotonic()
    lock = threading.Lock()
    stopping = threading.Event()
    live: set[Worker] = set()
    totals: dict[str, int] = {}
    details: list[str] = []
    failed = False
    (work / "tmp").mkdir()
    env = {**os.environ, "TMPDIR": str(work / "tmp")}

    def report(test_id: str, record: dict | None, problem: str, output: tuple[str, str]) -> None:
        nonlocal failed
        if record is None:
            record = {"progress": f"{test_id} ... ERROR ({problem})\n",
                      "details": "\n".join(["=" * 70, f"ERROR: {test_id}", SEPARATOR,
                                            f"The test gave no result: {problem}.", ""]),
                      "counts": {"errors": 1}, "ok": False}
        with lock:
            if output[0]:
                sys.stdout.write(output[0])
                sys.stdout.flush()
            if output[1]:
                sys.stderr.write(output[1])
            sys.stderr.write(record["progress"])
            sys.stderr.flush()
            if record["details"]:
                details.append(record["details"])
            for key, value in record["counts"].items():
                totals[key] = totals.get(key, 0) + value
            if not record["ok"]:
                failed = True

    def drive() -> None:
        current: Worker | None = None
        while not stopping.is_set():
            with lock:
                test_id = next(queue, None)
            if test_id is None:
                break
            if current is None:
                current = Worker(runner, start, work, env)
                with lock:
                    live.add(current)
            record, problem = current.run(test_id, timeout)
            if record is None:
                current.kill()
            report(test_id, record, problem, current.output())
            if record is None:
                with lock:
                    live.discard(current)
                current.retire()
                current = None
        if current is not None:
            with lock:
                live.discard(current)
            current.close()

    threads = [threading.Thread(target=drive) for _ in range(max(1, min(jobs, len(ids))))]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        stopping.set()
        with lock:
            leftover = list(live)
        for each in leftover:
            each.kill()
        for thread in threads:
            if thread.is_alive():
                thread.join()
    elapsed = time.monotonic() - began
    if details:
        print("", file=sys.stderr)
        for block in details:
            sys.stderr.write(block)
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
