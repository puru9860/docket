"""Regression tests for `tests/parallel.py`, the runner behind `tests/test.sh`."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_file_location, spec_from_loader
from pathlib import Path

# `DOCKET_BIN` points the suite at another build of the CLI. The runner under test is
# the one in that build's checkout, so a pre-task copy of the tree tests its own runner.
DOCKET = Path(os.environ.get("DOCKET_BIN") or Path(__file__).parents[1] / "bin" / "docket")
RUNNER = DOCKET.resolve().parents[3] / "tests" / "parallel.py"
# `bin/docket` only launches the CLI; its code is the `docket_cli` package beside `bin/`.
# An earlier build keeps it in one `docket_cli.py`, and one before that is its own source.
CLI_PACKAGE = DOCKET.resolve().parents[1] / "docket_cli"
_CLI_MODULE = DOCKET.resolve().parents[1] / "docket_cli.py"
CLI_SOURCE = _CLI_MODULE if _CLI_MODULE.is_file() else DOCKET
SUMMARY = re.compile(r"^Ran \d+ tests?\b")


def load(path: Path, name: str):  # type: ignore[no-untyped-def]
    """A script as an importable module, without running its entry point or caching it."""
    loader = SourceFileLoader(name, str(path))
    spec = spec_from_loader(name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    # A bytecode cache would land beside the script, inside the checkout under test.
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


def suite_parser():  # type: ignore[no-untyped-def]
    """The CLI's own `parse_suite_streams`, the parser `docket suite --qualify` reads with."""
    if not CLI_PACKAGE.is_dir():
        return load(CLI_SOURCE, "docket_parallel_oracle").parse_suite_streams
    name = "docket_parallel_oracle"
    for loaded in [key for key in sys.modules if key == name or key.startswith(name + ".")]:
        del sys.modules[loaded]
    spec = spec_from_file_location(name, CLI_PACKAGE / "__init__.py",
                                   submodule_search_locations=[str(CLI_PACKAGE)])
    assert spec is not None and spec.loader is not None
    package = module_from_spec(spec)
    sys.modules[name] = package
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(package)
    finally:
        sys.dont_write_bytecode = saved
    return next(vars(module)["parse_suite_streams"] for module in package.MODULES
                if "parse_suite_streams" in vars(module))


def gone(pid: int) -> bool:
    """Whether a process has exited; a zombie awaiting its reaper counts as gone."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state == "Z"
    except FileNotFoundError:
        return True
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


class SuiteRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tests = root / "repo" / "skills" / "docket" / "tests"
        self.tests.mkdir(parents=True)
        self.marks = root / "marks"
        self.marks.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fixture(self, body: str, name: str = "test_fixture.py") -> None:
        header = "import os, subprocess, sys, tempfile, time, unittest\n" \
                 "from pathlib import Path\nMARKS = Path(os.environ['FIXTURE_MARKS'])\n"
        (self.tests / name).write_text(header + textwrap.dedent(body))

    def run_suite(self, jobs: int, timeout: str = "", limit: float = 60
                  ) -> subprocess.CompletedProcess[str]:
        """Run the runner under test on the fixture suite, killed if it hangs."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DOCKET_TEST_JOBS", "DOCKET_TEST_TIMEOUT")}
        env.update(DOCKET_TEST_JOBS=str(jobs), FIXTURE_MARKS=str(self.marks))
        if timeout:
            env["DOCKET_TEST_TIMEOUT"] = timeout
        proc = subprocess.Popen([sys.executable, str(RUNNER), str(self.tests)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=env, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            self.fail(f"runner still running after {limit:g}s:\n{err[-2000:]}")
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def assert_one_summary(self, proc: subprocess.CompletedProcess[str], tests: int,
                           verdict: str, failures: int = 0) -> None:
        """Exactly one summary, on stderr, ending the output, and read by the qualifier."""
        self.assertEqual([], [line for line in proc.stdout.splitlines()
                              if SUMMARY.match(line.strip())], proc.stdout)
        counts = [line for line in proc.stderr.splitlines() if SUMMARY.match(line.strip())]
        self.assertEqual(1, len(counts), proc.stderr)
        self.assertRegex(counts[0], rf"^Ran {tests} tests? in [\d.]+s$")
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        self.assertEqual(verdict, lines[-1], proc.stderr)
        parsed, problem = suite_parser()(proc.stdout, proc.stderr)
        self.assertIsNone(problem, proc.stderr)
        self.assertEqual((tests, failures, verdict.startswith("OK")), parsed)

    def progress(self, proc: subprocess.CompletedProcess[str], name: str) -> str:
        """The one progress line naming a test, from before the failure details."""
        head = proc.stderr.split("=" * 70, 1)[0]
        lines = [line for line in head.splitlines() if name in line and " ... " in line]
        self.assertEqual(1, len(lines), proc.stderr)
        return lines[0]

    def test_a_worker_runs_many_tests(self) -> None:
        """Eight tests on two workers run in at most two processes."""
        self.fixture("class Many(unittest.TestCase):\n" + "".join(
            f"    def test_{n}(self):\n"
            f"        (MARKS / '{n}').write_text(str(os.getpid()))\n" for n in range(8)))
        proc = self.run_suite(jobs=2)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assert_one_summary(proc, 8, "OK")
        pids = {(self.marks / str(n)).read_text() for n in range(8)}
        self.assertLessEqual(len(pids), 2, pids)

    def test_one_summary_the_qualifier_reads(self) -> None:
        """Mixed outcomes aggregate into one unittest summary, serial or parallel."""
        self.fixture("""
            class Mixed(unittest.TestCase):
                def test_error(self):
                    raise RuntimeError("fixture error")
                def test_fail(self):
                    self.fail("fixture failure")
                def test_pass(self):
                    print("fixture-stdout")
                    print("fixture-stderr", file=sys.stderr)
                @unittest.skip("fixture skip")
                def test_skip(self):
                    pass
                @unittest.expectedFailure
                def test_xfail(self):
                    self.fail("expected")
            """)
        for jobs in (1, 3):
            with self.subTest(jobs=jobs):
                proc = self.run_suite(jobs=jobs)
                self.assertEqual(1, proc.returncode, proc.stderr)
                self.assert_one_summary(
                    proc, 5, "FAILED (failures=1, errors=1, skipped=1, expected failures=1)",
                    failures=2)
                self.assertIn("fixture-stdout", proc.stdout)
                self.assertIn("fixture-stderr", proc.stderr)
                self.assertIn("RuntimeError: fixture error", proc.stderr)
                self.assertIn("AssertionError: fixture failure", proc.stderr)

    def test_a_green_suite_reads_ok(self) -> None:
        """A passing suite ends with OK and its skip count, serial or parallel."""
        self.fixture("""
            class Green(unittest.TestCase):
                def test_one(self):
                    pass
                def test_two(self):
                    pass
                @unittest.skip("fixture skip")
                def test_three(self):
                    pass
            """)
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                proc = self.run_suite(jobs=jobs)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assert_one_summary(proc, 3, "OK (skipped=1)")
                self.assertTrue(self.progress(proc, "test_one").endswith("... ok"))

    def test_a_hung_test_is_an_error_and_the_rest_still_run(self) -> None:
        """A test past DOCKET_TEST_TIMEOUT errors, loses its processes, and leaves no files."""
        self.fixture("""
            class Hang(unittest.TestCase):
                def test_after(self):
                    pass
                def test_before(self):
                    pass
                def test_hang(self):
                    child = subprocess.Popen(["sleep", "300"])
                    (MARKS / "child").write_text(str(child.pid))
                    (MARKS / "tmp").write_text(tempfile.mkdtemp())
                    time.sleep(300)
            """)
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                began = time.monotonic()
                proc = self.run_suite(jobs=jobs, timeout="2", limit=20)
                self.assertLess(time.monotonic() - began, 20)
                self.assertEqual(1, proc.returncode, proc.stderr)
                self.assert_one_summary(proc, 3, "FAILED (errors=1)", failures=1)
                self.assertIn("ERROR (exceeded DOCKET_TEST_TIMEOUT=2s)",
                              self.progress(proc, "test_hang"))
                self.assertTrue(self.progress(proc, "test_after").endswith("... ok"))
                self.assertTrue(self.progress(proc, "test_before").endswith("... ok"))
                child = int((self.marks / "child").read_text())
                deadline = time.monotonic() + 5
                while not gone(child) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(gone(child), f"hung test's child {child} survived")
                self.assertFalse(Path((self.marks / "tmp").read_text()).exists())

    def test_a_worker_that_dies_is_an_error_and_the_rest_still_run(self) -> None:
        """A test that kills its process errors; later tests still run and pass."""
        self.fixture("""
            class Dies(unittest.TestCase):
                def test_a_dies(self):
                    os._exit(3)
                def test_b_later(self):
                    pass
                def test_c_later(self):
                    pass
            """)
        for jobs in (2, 1):
            with self.subTest(jobs=jobs):
                proc = self.run_suite(jobs=jobs)
                self.assertEqual(1, proc.returncode, proc.stderr)
                self.assert_one_summary(proc, 3, "FAILED (errors=1)", failures=1)
                self.assertIn("ERROR", self.progress(proc, "test_a_dies"))
                self.assertTrue(self.progress(proc, "test_b_later").endswith("... ok"))
                self.assertTrue(self.progress(proc, "test_c_later").endswith("... ok"))

    def test_a_test_never_sees_an_earlier_tests_environment(self) -> None:
        """Environment and working directory changes end with the test that made them."""
        self.fixture("""
            START = os.getcwd()
            class Leak(unittest.TestCase):
                def test_a_leaks(self):
                    os.environ["FIXTURE_LEAK"] = "1"
                    os.environ.pop("FIXTURE_MARKS")
                    os.chdir(tempfile.gettempdir())
                def test_b_is_clean(self):
                    # Compare single values: a failure must never print the environment.
                    self.assertIsNone(os.environ.get("FIXTURE_LEAK"))
                    self.assertIsNotNone(os.environ.get("FIXTURE_MARKS"))
                    self.assertEqual(START, os.getcwd())
            """)
        for jobs in (2, 1):
            with self.subTest(jobs=jobs):
                proc = self.run_suite(jobs=jobs)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assert_one_summary(proc, 2, "OK")

    def test_the_default_deadline_is_300_seconds(self) -> None:
        """DOCKET_TEST_TIMEOUT defaults to 300 seconds and rejects a non-positive value."""
        timeout_seconds = load(RUNNER, "docket_parallel_runner").timeout_seconds
        self.assertEqual(300, timeout_seconds({}))
        self.assertEqual(300, timeout_seconds({"DOCKET_TEST_TIMEOUT": " "}))
        self.assertEqual(2.5, timeout_seconds({"DOCKET_TEST_TIMEOUT": "2.5"}))
        for bad in ("0", "-1", "soon", "nan"):
            with self.subTest(value=bad), self.assertRaises(SystemExit):
                timeout_seconds({"DOCKET_TEST_TIMEOUT": bad})

    def test_the_snapshot_holds_the_tree_the_suite_reads(self) -> None:
        """The snapshot copies skills, docs, tests, .gitignore, and top-level Markdown."""
        repo = self.tests.parents[2]
        (self.tests / "test_x.py").write_text("")
        (self.tests / "__pycache__").mkdir()
        (self.tests / "__pycache__" / "test_x.pyc").write_text("")
        (repo / "docs").mkdir()
        (repo / "docs" / "guide.mdx").write_text("guide")
        (repo / "README.md").write_text("readme")
        (repo / ".gitignore").write_text("__pycache__/\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "parallel.py").write_text("runner")
        (repo / "scratch.txt").write_text("not read by the suite")
        snapshot = load(RUNNER, "docket_parallel_runner").snapshot
        with tempfile.TemporaryDirectory() as into:
            start = snapshot(self.tests, Path(into))
            self.assertEqual(Path(into) / "skills" / "docket" / "tests", start)
            self.assertTrue((start / "test_x.py").exists())
            self.assertFalse((start / "__pycache__").exists())
            self.assertEqual("guide", (Path(into) / "docs" / "guide.mdx").read_text())
            self.assertEqual("readme", (Path(into) / "README.md").read_text())
            self.assertEqual("__pycache__/\n", (Path(into) / ".gitignore").read_text())
            self.assertFalse((Path(into) / "scratch.txt").exists())
            # A snapshot run's own tests find its runner beside its CLI.
            self.assertEqual("runner", (Path(into) / "tests" / "parallel.py").read_text())


if __name__ == "__main__":
    unittest.main()
