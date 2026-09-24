from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


# `DOCKET_BIN` points the suite at another build of the CLI. It exists so a change
# can be demonstrated failing against the previous implementation before it ships.
DOCKET = Path(os.environ.get("DOCKET_BIN") or Path(__file__).parents[1] / "bin" / "docket")
# `bin/docket` only launches the CLI; its code lives in the `docket_cli` package beside
# `bin/`. An earlier build keeps it in one `docket_cli.py`, and a build before that has
# no module and is its own source. `CLI_FILES` is the whole CLI source in each layout.
CLI_PACKAGE = DOCKET.resolve().parents[1] / "docket_cli"
_CLI_MODULE = DOCKET.resolve().parents[1] / "docket_cli.py"
CLI_FILES = (sorted(CLI_PACKAGE.glob("*.py")) if CLI_PACKAGE.is_dir()
             else [_CLI_MODULE] if _CLI_MODULE.is_file() else [DOCKET])
HOOK = Path(__file__).parents[1] / "hooks" / "wake.sh"
# Variables a harness gives the commands it runs. A suite run inside a harness
# strips them so docket never notes that harness's own session as a test role.
HARNESS_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
               "OPENCODE_PID", "OPENCODE")


def cli_source_text() -> str:
    """The CLI's whole source as one text, however the build lays it out."""
    return "".join(path.read_text() for path in CLI_FILES)


def cli_function_source(name: str) -> str:
    """One top-level function's source, up to the next top-level def in its file."""
    for path in CLI_FILES:
        text = path.read_text()
        start = text.find(f"\ndef {name}(")
        if start != -1:
            end = text.find("\ndef ", start + 1)
            return text[start + 1:end if end != -1 else len(text)]
    raise AssertionError(f"{name} not found in {CLI_FILES}")


class _PackageView:
    """A package build read as the one namespace the single-module CLI was.

    A name is read from the module that holds it. Assigning one rebinds it in every
    module that imported it, which is what patching the single module's global did.
    """

    def __init__(self, modules: tuple) -> None:  # type: ignore[type-arg]
        object.__setattr__(self, "_modules", modules)

    def __getattr__(self, name: str) -> object:
        for module in self._modules:
            if name in vars(module):
                return vars(module)[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: object) -> None:
        holders = [module for module in self._modules if name in vars(module)]
        if not holders:
            raise AttributeError(name)
        for module in holders:
            setattr(module, name, value)


def load_cli(name: str):  # type: ignore[no-untyped-def]
    """The CLI under test as an importable namespace, without running main or caching it."""
    # A bytecode cache would land beside the source, inside the checkout under test.
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        if CLI_PACKAGE.is_dir():
            for loaded in [key for key in sys.modules if key == name or key.startswith(name + ".")]:
                del sys.modules[loaded]
            spec = importlib.util.spec_from_file_location(
                name, CLI_PACKAGE / "__init__.py", submodule_search_locations=[str(CLI_PACKAGE)])
            assert spec is not None and spec.loader is not None
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            spec.loader.exec_module(package)
            return _PackageView(package.MODULES)
        loader = importlib.machinery.SourceFileLoader(name, str(CLI_FILES[0]))
        spec = importlib.util.spec_from_loader(name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = saved


def _docket_constant(name: str) -> str:
    """Reference a constant from the CLI binary itself, not a duplicated literal."""
    text = cli_source_text()
    match = re.search(rf"^{name}\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    assert match is not None, f"{name} not found in {CLI_FILES}"
    return match.group(1)


COVERAGE_UNAVAILABLE = _docket_constant("COVERAGE_UNAVAILABLE")


def sha(data: bytes) -> str:
    """The digest form Docket records for frozen evidence."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def parse_meta(path: Path) -> dict[str, str]:
    """Flat frontmatter of a document, read the way the CLI reads it."""
    text = path.read_text()
    head = text[3:text.find("\n---", 3)]
    return {
        line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
        for line in head.splitlines() if ":" in line
    }


class DocketCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # The user-level feedback log must never collect test runs.
        # It lives outside the test checkout, which must see no stray file.
        self._feedback_log = os.environ.get("DOCKET_FEEDBACK_LOG")
        self._feedback_tmp = tempfile.TemporaryDirectory()
        os.environ["DOCKET_FEEDBACK_LOG"] = str(Path(self._feedback_tmp.name) / "feedback.jsonl")
        self._harness_env = {k: os.environ.pop(k) for k in HARNESS_ENV if k in os.environ}
        # A suite run inside a docket role session must never inherit that
        # session's role or wake configuration. The wake hook reads
        # DOCKET_ROLE to decide whether to wake, and DOCKET_WATCH_TIMEOUT for
        # its default timeout, while docket watch reads DOCKET_WATCH_HOOK to
        # choose its wake exit code. Tests that need a role set it explicitly
        # in their subprocess env instead.
        self._docket_role_env = {k: os.environ.pop(k) for k in (
            "DOCKET_ROLE", "DOCKET_WATCH_HOOK", "DOCKET_WATCH_TIMEOUT") if k in os.environ}
        # A suite run inside a herdr pane must never split, drive, or close the user's
        # real panes; a test that needs panes installs `fake_herdr_panes` instead.
        self._herdr_env = {k: os.environ.pop(k) for k in list(os.environ)
                           if k.startswith("HERDR_")}
        self._fake_herdr_path: str | None = None
        # Profiles the user adopted must never shape a test's prompts.
        self._profiles = os.environ.get("DOCKET_MODEL_PROFILES")
        os.environ["DOCKET_MODEL_PROFILES"] = str(Path(self._feedback_tmp.name) / "profiles")

    def tearDown(self) -> None:
        if self._fake_herdr_path is not None:
            os.environ["PATH"] = self._fake_herdr_path
            os.environ.pop("HERDR_ENV", None)
        os.environ.update(self._herdr_env)
        os.environ.update(self._harness_env)
        for _key in ("DOCKET_ROLE", "DOCKET_WATCH_HOOK", "DOCKET_WATCH_TIMEOUT"):
            os.environ.pop(_key, None)
        os.environ.update(self._docket_role_env)
        if self._profiles is None:
            os.environ.pop("DOCKET_MODEL_PROFILES", None)
        else:
            os.environ["DOCKET_MODEL_PROFILES"] = self._profiles
        if self._feedback_log is None:
            os.environ.pop("DOCKET_FEEDBACK_LOG", None)
        else:
            os.environ["DOCKET_FEEDBACK_LOG"] = self._feedback_log
        self._feedback_tmp.cleanup()
        self.tmp.cleanup()

    def fake_herdr_panes(self) -> None:
        """Put a herdr on PATH whose one disposable pane runs, echoes, and closes.

        Delivery qualification splits a real pane, runs a marker command in it, and
        reads it back. Against the user's terminal that is a side effect, and under
        load the read-back raced the pane and blocked the qualification.
        """
        fake = Path(self._feedback_tmp.name) / "herdr-bin"
        fake.mkdir(exist_ok=True)
        (fake / "herdr").write_text(
            "#!/bin/sh\n"
            "pane=\"$(dirname \"$0\")/pane\"\n"
            "case \"$1 $2\" in\n"
            "  'pane split') : >\"$pane\"; "
            "echo '{\"result\": {\"pane\": {\"pane_id\": \"fake-1\"}}}'; exit 0 ;;\n"
            "  'pane run') shift 3; sh -c \"$*\" >\"$pane\" 2>&1; exit 0 ;;\n"
            "  'pane read') cat \"$pane\"; exit 0 ;;\n"
            "  'pane close') rm -f \"$pane\"; exit 0 ;;\n"
            "  'pane get') [ -e \"$pane\" ] && exit 0; echo 'error: pane_not_found'; exit 1 ;;\n"
            "esac\n"
            "[ \"$1\" = --version ] && echo 'herdr 9.9.9-fake' && exit 0\n"
            "exit 1\n")
        (fake / "herdr").chmod(0o755)
        if self._fake_herdr_path is None:
            self._fake_herdr_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{fake}{os.pathsep}{self._fake_herdr_path}"
        os.environ["HERDR_ENV"] = "1"

    def assert_disposable(self, target: Path) -> Path:
        """Mechanically refuse to destroy anything outside this test's temp root.

        Every destructive test operation resolves its target first and proves
        the result is inside the disposable root this test created and outside
        the repository and the installed skill. Ancestry is checked on resolved
        paths, never as a string prefix.
        """
        resolved = Path(target).resolve()
        root = self.root.resolve()
        self.assertTrue(resolved == root or root in resolved.parents,
                        f"{resolved} is outside the disposable test root {root}")
        repo = Path(__file__).resolve().parents[3]
        installed = (Path.home() / ".agents" / "skills" / "docket").resolve()
        for forbidden in (repo, installed):
            self.assertFalse(resolved == forbidden or forbidden in resolved.parents,
                             f"{resolved} is inside protected {forbidden}")
        return resolved

    def cli(
        self, *args: str, ok: bool = True, fault: str = "", perturb: str = "",
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("DOCKET_FAULT", None)
        env.pop("DOCKET_PERTURB", None)
        if fault:
            env["DOCKET_FAULT"] = fault
        if perturb:
            env["DOCKET_PERTURB"] = perturb
        result = subprocess.run(
            [sys.executable, str(DOCKET), *args], cwd=self.root, text=True, capture_output=True,
            env=env,
        )
        if ok and result.returncode:
            self.fail(f"command failed: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def cli_env(
        self, extra: dict[str, str], *args: str, ok: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run the CLI with a scoped dependency-root seam injected."""
        env = dict(os.environ)
        env.pop("DOCKET_FAULT", None)
        env.pop("DOCKET_PERTURB", None)
        env.update(extra)
        result = subprocess.run(
            [sys.executable, str(DOCKET), *args], cwd=self.root, text=True, capture_output=True,
            env=env,
        )
        if ok and result.returncode:
            self.fail(f"command failed: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def init(
        self, topology: str = "split", *, evidence_mode: str = "",
        roots: tuple[str, ...] = (),
    ) -> Path:
        """Scaffold the run. Checkout roots are declared explicitly or not at all."""
        extra = ["--evidence-mode", evidence_mode] if evidence_mode else []
        for spec in roots:
            extra += ["--root", spec]
        self.cli("init", "demo", "--harness", "claude", "--topology", topology,
                 "--workflow", "five-role-v1", *extra)
        run = self.root / ".docket" / "runs" / "demo"
        plan = run / "plan.mdx"
        text = plan.read_text().replace("workflow: five-role-v1", "workflow: legacy")
        sessions = ("planner, orchestrator, implementor"
                    if topology == "split" else "coordinator, implementor")
        text = text.replace("independent-verifier-reviewer", "legacy-completion")
        for line in text.splitlines():
            if line.startswith("role_sessions:"):
                text = text.replace(line, f"role_sessions: {sessions}")
                break
        plan.write_text(text)
        return run

    def init_legacy_as(self, run_id: str, topology: str = "split",
                       evidence_mode: str = "") -> Path:
        """Simulate a historical legacy run without using the closed init door."""
        extra = ["--evidence-mode", evidence_mode] if evidence_mode else []
        self.cli("init", run_id, "--harness", "claude", "--topology", topology,
                 "--workflow", "five-role-v1", *extra)
        run = self.root / ".docket" / "runs" / run_id
        plan = run / "plan.mdx"
        text = plan.read_text().replace("workflow: five-role-v1", "workflow: legacy")
        sessions = ("planner, orchestrator, implementor"
                    if topology == "split" else "coordinator, implementor")
        text = text.replace("independent-verifier-reviewer", "legacy-completion")
        for line in text.splitlines():
            if line.startswith("role_sessions:"):
                text = text.replace(line, f"role_sessions: {sessions}")
                break
        plan.write_text(text)
        return run

    def git(self, *args: str, cwd: Path | None = None) -> str:
        """Run a real Git command. Baseline behavior cannot be proved against a fake."""
        result = subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), text=True, capture_output=True,
        )
        if result.returncode:
            self.fail(f"git {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout

    def repo(self, where: Path | None = None) -> Path:
        """A checkout with one commit, so a baseline has a real commit to pin."""
        where = where or self.root
        where.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main", ".", cwd=where)
        self.git("config", "user.email", "docket@example.test", cwd=where)
        self.git("config", "user.name", "Docket Test", cwd=where)
        (where / "src").mkdir(exist_ok=True)
        (where / "src" / "a.py").write_text("allowed = 1\n")
        self.git("add", "src/a.py", cwd=where)
        self.git("commit", "-qm", "baseline", cwd=where)
        return where

    @staticmethod
    def body_of(path: Path) -> str:
        """The document body exactly as the CLI frontmatter parser yields it."""
        text = path.read_text()
        return text[text.find("\n---", 3) + 4:].lstrip("\n")

    def assign(
        self, owner: str = "T01", *, executor: str = "implementor", claim_scope: bool = True,
        file: str = "src/a.py",
    ) -> Path:
        # The aggregate declares an integration verify command only when a test
        # passes one explicitly; the default orch assignment carries none, so a
        # bare --skip-verify keeps working exactly as before.
        verify_args: list[str] = [] if owner == "orch" else ["--verify", f'printf "{owner} ok\\n"']
        self.cli(
            "assign", "demo", owner, "--complexity", "high", "--executor", executor,
            "--harness", "opencode", "--file", file, *verify_args,
        )
        run = self.root / ".docket" / "runs" / "demo"
        if executor == "implementor" and claim_scope:
            self.fill_scope(run / f"{owner}-scope.mdx")
            self.cli("scope", "demo", owner, "--submit")
        return run

    @staticmethod
    def fill_task(path: Path, criterion: str = "The feature works.") -> None:
        text = path.read_text()
        text = text.replace("# Task T01: <title>", "# Task T01: Feature")
        text = text.replace(
            "<!-- TODO: one paragraph. What must be true when this is done. -->", "Implement the feature."
        )
        text = text.replace("- [ ] <!-- TODO -->", f"- [ ] {criterion}")
        path.write_text(text)

    @staticmethod
    def fill_scope(path: Path) -> None:
        replacements = {
            "<!-- TODO: what you inspected and why this is enough context to implement. -->": "Followed the feature entry point into its tests and direct dependencies.",
            "<!-- TODO: explain every proposed path in frontmatter `files:`. -->": "`src/a.py` contains the implementation change.",
            "<!-- TODO: useful paths and symbols for implementation and later recovery. -->": "- `src/a.py:feature` - implementation entry point.",
            "<!-- TODO: explain the exact frontmatter `verify:` command and what it proves. -->": "The command exercises the task acceptance path.",
            '<!-- TODO: unresolved discovery uncertainty, or "none". -->': "none",
        }
        text = path.read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text)

    @staticmethod
    def fill_task_report(
        path: Path, criterion: str = "The feature works.", *,
        blocked: bool = False, files: str = "- `src/a.py:1` - implemented the feature.",
    ) -> None:
        text = path.read_text()
        text = text.replace(
            "<!-- TODO: what you actually did, 2-4 sentences. No plans, only past tense. -->",
            "Implemented the feature and verified its behavior.",
        )
        text = text.replace(
            '<!-- TODO: one bullet per change as `path/to/file.py:120` plus a short note. Write "none" if nothing changed. -->',
            files,
        )
        text = text.replace("- [ ] <!-- TODO -->", f"- [{' ' if blocked else 'x'}] {criterion}")
        if not blocked:
            # Dispatch and assign seed the task's criteria as unchecked boxes.
            text = text.replace(f"- [ ] {criterion}", f"- [x] {criterion}")
        text = text.replace(
            "<!-- TODO: the exact command you ran and its real output. Never claim a result you did not see. -->",
            'Command: `printf "T01 ok\\n"`\n\nOutput: `T01 ok`',
        )
        if blocked:
            text = text.replace(
                "none\n\n## Notes", "The reviewer must decide whether to waive the external failure.\n\n## Notes"
            )
        path.write_text(text)

    @staticmethod
    def fill_orch_report(path: Path) -> None:
        replacements = {
            "<!-- TODO: summarize the delivered outcome for the planner. -->": "Delivered every planned task.",
            "<!-- TODO: one row per planned task, including owner, terminal state, and verification. -->": "| task | outcome | verification |\n| --- | --- | --- |\n| T01 | approved | passed |\n| T02 | approved | passed |",
            "<!-- TODO: summarize the task-local changes the planner should review. -->": "Updated `src/a.py`.",
            "<!-- TODO: exact integrated command and real output. -->": "Command: `printf ok`\n\nOutput: `ok`",
        }
        text = path.read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text)

    @staticmethod
    def fill_handoff(path: Path) -> None:
        replacements = {
            "<!-- TODO: current implementation state in 2-4 sentences. -->": "The main path is implemented; one edge case remains.",
            "<!-- TODO: finished behavior and decisions; cite `path:symbol` where useful. -->": "- `src/a.py:feature` handles the main case.",
            "<!-- TODO: concrete unfinished items, in dependency order. -->": "1. Add the empty-input guard.\n2. Run the registered verification.",
            "<!-- TODO: every touched or relevant `path:symbol`, plus what changed or remains. -->": "- `src/a.py:feature` - main behavior changed; guard remains.",
            "<!-- TODO: exact commands already run, real results, and tests not yet run. -->": "Not yet run; execute the task's registered command after the guard.",
            '<!-- TODO: unresolved issues and fragile assumptions, or "none". -->': "none",
            "<!-- TODO: one precise first action for the replacement implementor. -->": "Add the empty-input guard in `src/a.py:feature`.",
        }
        text = path.read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text)

    def test_supported_harnesses_and_topology_are_recorded(self) -> None:
        run = self.init("combined")
        plan = (run / "plan.mdx").read_text()
        self.assertIn("evidence_mode:", plan)
        self.assertIn("topology: combined", plan)
        self.assertIn("planner_harness: claude", plan)
        self.assertIn("progress_updates: quiet", plan)
        self.assertNotEqual(0, self.cli("init", "bad", "--harness", "gemini", ok=False).returncode)

    def test_complexity_is_independent_from_executor(self) -> None:
        self.init()
        task = (self.assign() / "T01-task.mdx").read_text()
        self.assertIn("complexity: high", task)
        self.assertIn("executor: implementor", task)
        self.assertIn("harness: opencode", task)
        self.assertIn("files: src/a.py", task)

    def test_submit_rejects_acceptance_criteria_that_do_not_match_task(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx", "An easier invented criterion.")
        result = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("do not match the task", result.stderr)

    def test_blocked_report_can_only_end_as_waived_not_approved(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True)
        self.cli("submit", "demo", "T01", "--blocked")
        approval = self.cli("decide", "demo", "T01", "--approve", ok=False)
        self.assertIn("--waive", approval.stderr)
        self.cli("decide", "demo", "T01", "--waive", "--reason", "unchanged baseline failure")
        status = self.cli("status", "demo").stdout
        self.assertIn("waived", status)
        self.assertIn("1/1 decided", status)
        self.assertNotIn("1/1 approved", status)

    def test_planner_status_and_events_hide_implementor_tasks(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        planner = self.cli("status", "demo", "--role", "planner").stdout
        self.assertNotIn("T01", planner)
        self.assertIn("orchestrator report: not opened", planner)
        planner_watch = self.cli("watch", "demo", "--role", "planner", "--timeout", "0", ok=False)
        self.assertEqual(0, planner_watch.returncode)
        orchestrator_watch = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(2, orchestrator_watch.returncode)
        self.assertIn("review batch ready", orchestrator_watch.stderr)
        self.assertIn("T01 round 1", orchestrator_watch.stderr)
        self.assertIn("Process normal review batches silently", orchestrator_watch.stderr)

    def test_submitted_reports_wake_once_only_when_the_batch_is_ready(self) -> None:
        self.init("combined")
        run = self.assign("T01", file="src/a.py")
        self.assign("T02", file="src/b.py")
        for owner in ("T01", "T02"):
            self.fill_task(run / f"{owner}-task.mdx")
            self.fill_task_report(run / f"{owner}-report-01.mdx")

        self.cli("submit", "demo", "T01")
        early = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(0, early.returncode)

        self.cli("submit", "demo", "T02")
        ready = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(2, ready.returncode)
        self.assertIn("review batch ready with 2 submitted task report(s)", ready.stderr)
        self.assertIn("T01 round 1", ready.stderr)
        self.assertIn("T02 round 1", ready.stderr)

        repeated = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(0, repeated.returncode)

        self.cli("decide", "demo", "T01", "--approve")
        self.cli("decide", "demo", "T02", "--changes")
        decision = run / "T02-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Correct `src/b.py:1` and rerun verification.",
        ))
        self.cli("decide", "demo", "T02", "--changes")
        self.fill_task_report(run / "T02-report-02.mdx")
        self.cli("submit", "demo", "T02")
        revision = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(2, revision.returncode)
        self.assertIn("review batch ready with 1 submitted task report(s)", revision.stderr)
        self.assertIn("T02 round 2", revision.stderr)

        self.cli("decide", "demo", "T02", "--approve")
        self.assign("orch", executor="orchestrator")
        aggregate = run / "orch-report-01.mdx"
        self.fill_orch_report(aggregate)
        self.cli("submit", "demo", "orch", "--skip-verify")
        stale = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(0, stale.returncode)

    def test_blocker_wakes_immediately_without_waiting_for_batch(self) -> None:
        self.init("combined")
        run = self.assign("T01", file="src/a.py")
        self.assign("T02", file="src/b.py")
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True)
        self.cli("submit", "demo", "T01", "--blocked")

        wake = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(2, wake.returncode)
        self.assertIn("T01 is BLOCKED", wake.stderr)
        self.assertNotIn("review batch ready", wake.stderr)

    def test_split_orchestrator_report_is_standardized_and_planner_reviewed(self) -> None:
        self.init("split")
        run = self.assign("T01", executor="orchestrator")
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        run = self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.assertIn("## Task outcomes", report.read_text())
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")
        self.assertIn("status: submitted", report.read_text())
        planner_watch = self.cli("watch", "demo", "--role", "planner", "--timeout", "0", ok=False)
        self.assertEqual(2, planner_watch.returncode)
        self.assertIn("orchestrator submitted", planner_watch.stderr)

    def test_combined_orchestrator_report_completes_without_redundant_handoff(self) -> None:
        self.init("combined")
        run = self.assign("T01", executor="orchestrator")
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        run = self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")
        self.assertIn("status: completed", report.read_text())
        planner_watch = self.cli("watch", "demo", "--role", "planner", "--timeout", "0", ok=False)
        self.assertEqual(0, planner_watch.returncode)

    def test_status_includes_planned_but_unassigned_tasks(self) -> None:
        run = self.init()
        plan = run / "plan.mdx"
        plan.write_text(
            plan.read_text().replace("| T01 |       | low", "| T01 | First | low").replace(
                "## Risks", "| T02 | Second | high | implementor | `src/b.py` | `true` |\n\n## Risks"
            )
        )
        status = self.cli("status", "demo").stdout
        self.assertIn("T01", status)
        self.assertIn("T02", status)
        self.assertIn("unassigned", status)
        self.assertIn("0/2 decided", status)

    def test_status_shows_full_verify_command_beyond_twenty_characters(self) -> None:
        self.init()
        prefix = "echo 123456789012345"
        commands = {"T01": prefix + "6789", "T02": prefix + "678A"}
        self.assertEqual(20, len(prefix))
        for owner, command in commands.items():
            self.assertEqual(prefix, command[:20])
            self.cli(
                "assign", "demo", owner, "--complexity", "high", "--executor", "implementor",
                "--harness", "opencode", "--file", f"src/{owner.lower()}.py", "--verify", command,
            )
            self.fill_scope(self.root / ".docket" / "runs" / "demo" / f"{owner}-scope.mdx")
            self.cli("scope", "demo", owner, "--submit")
        self.assertNotEqual(commands["T01"], commands["T02"])
        status = self.cli("status", "demo").stdout
        for owner, command in commands.items():
            self.assertIn(command, status, f"{owner} verify command was truncated in status")

    def test_assignment_snapshot_rejects_new_out_of_scope_changes(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "docket@example.test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Docket Test"], cwd=self.root, check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("allowed = 1\n")
        (self.root / "outside.py").write_text("outside = 1\n")
        subprocess.run(["git", "add", "src/a.py", "outside.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

        run = self.init()
        self.assertIn("evidence_mode: git", (run / "plan.mdx").read_text())
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        (self.root / "outside.py").write_text("outside = 2\n")

        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] src/a.py", diff)
        self.assertIn("[OUTSIDE SCOPE] outside.py", diff)
        rejected = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("outside the task scope: outside.py", rejected.stderr)

    def test_t07_generated_outside_scope_paths_get_cleanup_hint(self) -> None:
        self.repo()
        self.cli("init", "generated", "--mode", "standard", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "generated"
        self.assign_simple_in(run, "generated", "T01")
        generated = self.root / "src" / "__pycache__" / "sample.pyc"
        generated.parent.mkdir()
        generated.write_bytes(b"sample bytecode")
        loose_pyc = self.root / "build" / "loose.pyc"
        loose_pyc.parent.mkdir()
        loose_pyc.write_bytes(b"loose bytecode")
        module = self.root / "node_modules" / "package" / "index.js"
        module.parent.mkdir(parents=True)
        module.write_text("export default 1;\n")
        ordinary = self.root / "notes.txt"
        ordinary.write_text("retain this note\n")
        diff = self.cli("diff", "generated", "T01").stdout
        self.assertIn("[OUTSIDE SCOPE] src/__pycache__/sample.pyc", diff)
        self.assertIn("[OUTSIDE SCOPE] build/loose.pyc", diff)
        self.assertIn("[OUTSIDE SCOPE] node_modules/package/index.js", diff)
        self.assertIn("[OUTSIDE SCOPE] notes.txt", diff)
        self.assertIn("Ignore them if disposable, or delete only files you created", diff)
        self.assertIn("src/__pycache__/sample.pyc", diff)
        self.assertNotIn("notes.txt", diff.split("Generated-looking path(s):", 1)[1])
        self.fill_task_report(run / "T01-report-01.mdx")
        refused = self.cli("submit", "generated", "T01", "--as", "implementor", ok=False)
        self.assertIn("outside the task scope", refused.stderr)
        self.assertIn("Ignore them if disposable, or delete only files you created", refused.stderr)
        self.assertEqual(b"sample bytecode", generated.read_bytes())
        self.assertEqual(b"loose bytecode", loose_pyc.read_bytes())
        self.assertEqual("export default 1;\n", module.read_text())
        self.assertEqual("retain this note\n", ordinary.read_text())

    def test_t07_submit_hint_for_generated_outside_scope_path(self) -> None:
        self.repo()
        self.cli("init", "generatedgate", "--mode", "standard", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "generatedgate"
        self.assign_simple_in(run, "generatedgate", "T01")
        generated = self.root / "node_modules" / "package" / "index.js"
        generated.parent.mkdir(parents=True)
        generated.write_text("export default 2;\n")
        self.fill_task_report(run / "T01-report-01.mdx")
        refused = self.cli("submit", "generatedgate", "T01", "--as", "implementor",
                           ok=False)
        self.assertIn("outside the task scope: node_modules/package/index.js", refused.stderr)
        self.assertIn("Ignore them if disposable, or delete only files you created", refused.stderr)
        self.assertEqual("export default 2;\n", generated.read_text())
        self.assertIn("status: draft", (run / "T01-report-01.mdx").read_text())

    def test_preflight_records_a_failing_baseline_without_failing_the_command(self) -> None:
        self.init()
        self.cli(
            "assign", "demo", "T01", "--harness", "opencode", "--file", "src/a.py",
            "--verify", "printf baseline-failure; exit 7", "--verify-timeout", "12",
        )
        run = self.root / ".docket" / "runs" / "demo"
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        result = self.cli("preflight", "demo", "T01")
        self.assertIn("failing (exit 7)", result.stdout)
        baseline = self.root / ".docket" / "runs" / "demo" / ".baselines" / "T01.json"
        self.assertIn('"returncode": 7', baseline.read_text())

    def test_t13_verify_slot_refuses_a_second_preflight_and_releases(self) -> None:
        """A live preflight owns the slot until its gated command and result finish."""
        run = self.init(evidence_mode="documents-only")
        gate = Path(self._feedback_tmp.name) / "t13-slot-gate"
        shell_pid = Path(self._feedback_tmp.name) / "t13-slot-shell.pid"
        gate.unlink(missing_ok=True)
        shell_pid.unlink(missing_ok=True)
        command = self._gated_command(gate, shell_pid)
        self.cli(
            "assign", "demo", "T01", "--harness", "opencode", "--file", "src/a.py",
            "--verify", command, "--verify-timeout", "120",
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        metadata = run / ".locks" / "T01.verify.json"
        metadata.unlink(missing_ok=True)
        first = subprocess.Popen(
            [sys.executable, str(DOCKET), "preflight", "demo", "T01"],
            cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and (not metadata.is_file() or not shell_pid.is_file()):
                if first.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(metadata.is_file())
            self.assertTrue(shell_pid.is_file())
            self.assertTrue(self._pid_alive(int(shell_pid.read_text())))
            info = json.loads(metadata.read_text())
            second = self.cli("preflight", "demo", "T01", ok=False)
            self.assertIn(f"pid {info['pid']}", second.stderr)
            self.assertIn(command, second.stderr)
            self.assertNotIn("running baseline verify", second.stdout)
            gate.touch()
            first_out, first_err = first.communicate(timeout=30)
        finally:
            gate.touch()
            if first.poll() is None:
                first.terminate()
                first.communicate(timeout=10)
            if shell_pid.is_file() and self._pid_alive(int(shell_pid.read_text())):
                try:
                    os.kill(int(shell_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.assertEqual(0, first.returncode, first_err)
        self.assertIn("baseline recorded: passing", first_out)
        self.assertEqual(0, self.cli("preflight", "demo", "T01").returncode)

    def test_t13_signals_end_the_whole_verify_process_group(self) -> None:
        """TERM, HUP, and INT stop the command tree before docket leaves."""
        run = self.init(evidence_mode="documents-only")
        gate = Path(self._feedback_tmp.name) / "t13-signal-gate"
        shell_pid = Path(self._feedback_tmp.name) / "t13-signal-shell.pid"
        child_pid = Path(self._feedback_tmp.name) / "t13-signal-child.pid"
        command = self._gated_command(gate, shell_pid, child_pid)
        self.cli(
            "assign", "demo", "T01", "--harness", "opencode", "--file", "src/a.py",
            "--verify", command, "--verify-timeout", "120",
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")

        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=sig.name):
                gate.unlink(missing_ok=True)
                shell_pid.unlink(missing_ok=True)
                child_pid.unlink(missing_ok=True)
                metadata = run / ".locks" / "T01.verify.json"
                metadata.unlink(missing_ok=True)
                proc = subprocess.Popen(
                    [sys.executable, str(DOCKET), "preflight", "demo", "T01"],
                    cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                shell = 0
                child = 0
                try:
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline and (
                        not metadata.is_file() or not shell_pid.is_file() or not child_pid.is_file()
                    ):
                        if proc.poll() is not None:
                            break
                        time.sleep(0.05)
                    self.assertTrue(metadata.is_file())
                    self.assertTrue(shell_pid.is_file())
                    self.assertTrue(child_pid.is_file())
                    shell = int(shell_pid.read_text())
                    child = int(child_pid.read_text())
                    self.assertTrue(self._pid_alive(child))
                    proc.send_signal(sig)
                    proc.communicate(timeout=10)
                    deadline = time.monotonic() + 30
                    while self._pid_alive(child) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertFalse(self._pid_alive(child), "the verify process group outlived docket")
                finally:
                    gate.touch()
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate(timeout=5)
                    for pid in (child, shell):
                        if pid and self._pid_alive(pid):
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    self._kill_pid_file(shell_pid)
                    self._kill_pid_file(child_pid)

    def test_t13_sigkill_keeps_the_slot_until_the_command_ends(self) -> None:
        """A killed docket cannot release a slot its gated verify child still owns."""
        run = self.init(evidence_mode="documents-only")
        gate = Path(self._feedback_tmp.name) / "t13-sigkill-gate"
        shell_pid = Path(self._feedback_tmp.name) / "t13-sigkill-shell.pid"
        child_pid = Path(self._feedback_tmp.name) / "t13-sigkill-child.pid"
        gate.unlink(missing_ok=True)
        shell_pid.unlink(missing_ok=True)
        child_pid.unlink(missing_ok=True)
        command = self._gated_command(gate, shell_pid, child_pid)
        self.cli(
            "assign", "demo", "T01", "--harness", "opencode", "--file", "src/a.py",
            "--verify", command, "--verify-timeout", "120",
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        metadata = run / ".locks" / "T01.verify.json"
        metadata.unlink(missing_ok=True)
        first = subprocess.Popen(
            [sys.executable, str(DOCKET), "preflight", "demo", "T01"],
            cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        shell = 0
        child = 0
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and (
                not metadata.is_file() or not shell_pid.is_file() or not child_pid.is_file()
            ):
                if first.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(metadata.is_file())
            self.assertTrue(shell_pid.is_file())
            self.assertTrue(child_pid.is_file())
            shell = int(shell_pid.read_text())
            child = int(child_pid.read_text())
            self.assertTrue(self._pid_alive(shell))
            self.assertTrue(self._pid_alive(child))
            first.kill()
            first.communicate(timeout=5)
            self.assertTrue(self._pid_alive(shell))
            self.assertTrue(self._pid_alive(child))
            second = self.cli("preflight", "demo", "T01", ok=False)
            self.assertIn(f"pid {first.pid}", second.stderr)
            self.assertIn(command, second.stderr)
            self.assertTrue(self._pid_alive(child))
            gate.touch()
            deadline = time.monotonic() + 30
            while (self._pid_alive(shell) or self._pid_alive(child)) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(self._pid_alive(shell))
            self.assertFalse(self._pid_alive(child))
            self.assertEqual(0, self.cli("preflight", "demo", "T01").returncode)
        finally:
            gate.touch()
            if first.poll() is None:
                first.kill()
                first.communicate(timeout=5)
            for pid in (child, shell):
                if pid and self._pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self._kill_pid_file(shell_pid)
            self._kill_pid_file(child_pid)

    def test_t13_submit_refuses_a_running_preflight_slot(self) -> None:
        """Submit cannot start a second verify while preflight owns the slot."""
        run = self.init(evidence_mode="documents-only")
        gate = Path(self._feedback_tmp.name) / "t13-submit-preflight-gate"
        shell_pid = Path(self._feedback_tmp.name) / "t13-submit-preflight-shell.pid"
        gate.unlink(missing_ok=True)
        shell_pid.unlink(missing_ok=True)
        command = self._gated_command(gate, shell_pid)
        self.cli(
            "assign", "demo", "T01", "--harness", "opencode", "--file", "src/a.py",
            "--verify", command, "--verify-timeout", "120",
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        metadata = run / ".locks" / "T01.verify.json"
        metadata.unlink(missing_ok=True)
        first = subprocess.Popen(
            [sys.executable, str(DOCKET), "preflight", "demo", "T01"],
            cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and (not metadata.is_file() or not shell_pid.is_file()):
                if first.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(metadata.is_file())
            self.assertTrue(shell_pid.is_file())
            self.assertTrue(self._pid_alive(int(shell_pid.read_text())))
            info = json.loads(metadata.read_text())
            refused = self.cli("submit", "demo", "T01", "--as", "implementor", ok=False)
            self.assertIn(f"pid {info['pid']}", refused.stderr)
            self.assertIn(command, refused.stderr)
            gate.touch()
            first_out, first_err = first.communicate(timeout=30)
        finally:
            gate.touch()
            if first.poll() is None:
                first.terminate()
                first.communicate(timeout=10)
            if shell_pid.is_file() and self._pid_alive(int(shell_pid.read_text())):
                try:
                    os.kill(int(shell_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.assertEqual(0, first.returncode, first_err)
        self.assertIn("baseline recorded: passing", first_out)
        self.assertEqual("draft", parse_meta(run / "T01-report-01.mdx")["status"])
        self.assertEqual([], self.ledger())
        self.assertEqual(0, self.cli("preflight", "demo", "T01").returncode)

    def test_t13_second_submit_refuses_a_live_verify_slot(self) -> None:
        """A second submit is refused while the first submit's verify runs."""
        run = self.init(evidence_mode="documents-only")
        gate = Path(self._feedback_tmp.name) / "t13-submit-gate"
        shell_pid = Path(self._feedback_tmp.name) / "t13-submit-shell.pid"
        gate.unlink(missing_ok=True)
        shell_pid.unlink(missing_ok=True)
        command = self._gated_command(gate, shell_pid)
        self.cli(
            "assign", "demo", "T01", "--harness", "opencode", "--file", "src/a.py",
            "--verify", command, "--verify-timeout", "120",
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        metadata = run / ".locks" / "T01.verify.json"
        metadata.unlink(missing_ok=True)
        first = subprocess.Popen(
            [sys.executable, str(DOCKET), "submit", "demo", "T01", "--as", "implementor"],
            cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and (not metadata.is_file() or not shell_pid.is_file()):
                if first.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(metadata.is_file())
            self.assertTrue(shell_pid.is_file())
            self.assertTrue(self._pid_alive(int(shell_pid.read_text())))
            info = json.loads(metadata.read_text())
            second = self.cli("submit", "demo", "T01", "--as", "implementor", ok=False)
            self.assertIn(f"pid {info['pid']}", second.stderr)
            self.assertIn(command, second.stderr)
            self.assertIn("reports stay draft until submit", second.stderr)
            gate.touch()
            first_out, first_err = first.communicate(timeout=30)
        finally:
            gate.touch()
            if first.poll() is None:
                first.terminate()
                first.communicate(timeout=10)
            if shell_pid.is_file() and self._pid_alive(int(shell_pid.read_text())):
                try:
                    os.kill(int(shell_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.assertEqual(0, first.returncode, first_err)
        self.assertIn("frozen task-round bundle", first_out)
        self.assertEqual(0, self.cli("preflight", "demo", "T01").returncode)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            with open(f"/proc/{pid}/stat") as fh:
                return fh.read().split()[2] != "Z"
        except (ProcessLookupError, FileNotFoundError):
            return False

    @staticmethod
    def _kill_pid_file(path: Path) -> None:
        try:
            pid = int(path.read_text().strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            return
        if DocketCLI._pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _gated_command(gate: Path, shell_pid: Path, child_pid: Path | None = None) -> str:
        gate_text = shlex.quote(str(gate))
        shell_text = shlex.quote(str(shell_pid))
        prefix = f"printf '%s\\n' \"$$\" > {shell_text}; "
        loop = f"while [ ! -f {gate_text} ]; do sleep 0.05; done"
        if child_pid is None:
            return prefix + loop + "; printf ok"
        return prefix + f"(while [ ! -f {gate_text} ]; do sleep 0.05; done) & " \
            f"echo $! > {shlex.quote(str(child_pid))}; wait"

    def test_t13_re_review_refuses_a_running_preflight_slot(self) -> None:
        """Changed-evidence re-review cannot bypass a live verify slot."""
        run = self.init(evidence_mode="documents-only")
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        self.cli("submit", "demo", "T01")
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Reimplemented the feature and verified its behavior.",
        ))
        gate = Path(self._feedback_tmp.name) / "t13-rereview-gate"
        shell_pid = Path(self._feedback_tmp.name) / "t13-rereview-shell.pid"
        gate.unlink(missing_ok=True)
        shell_pid.unlink(missing_ok=True)
        command = self._gated_command(gate, shell_pid)
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace("verify: printf \"T01 ok\\n\"", f"verify: {command}"))
        metadata = run / ".locks" / "T01.verify.json"
        metadata.unlink(missing_ok=True)
        first = subprocess.Popen(
            [sys.executable, str(DOCKET), "preflight", "demo", "T01"],
            cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and (not metadata.is_file() or not shell_pid.is_file()):
                if first.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(metadata.is_file())
            self.assertTrue(shell_pid.is_file())
            self.assertTrue(self._pid_alive(int(shell_pid.read_text())))
            info = json.loads(metadata.read_text())
            refused = self.cli("decide", "demo", "T01", "--approve", "--re-review", ok=False)
            self.assertIn(f"pid {info['pid']}", refused.stdout + refused.stderr)
            self.assertIn(command, refused.stdout + refused.stderr)
            gate.touch()
            first_out, first_err = first.communicate(timeout=30)
        finally:
            gate.touch()
            if first.poll() is None:
                first.terminate()
                first.communicate(timeout=10)
            if shell_pid.is_file() and self._pid_alive(int(shell_pid.read_text())):
                try:
                    os.kill(int(shell_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.assertEqual(0, first.returncode, first_err)
        self.assertIn("running baseline verify:", first_out)
        self.assertEqual("submitted", parse_meta(report)["status"])
        self.assertEqual(0, self.cli("preflight", "demo", "T01").returncode)

    def test_t13_preflight_reuses_only_matching_frozen_evidence(self) -> None:
        """Each mismatch changes one identity dimension from a reusable baseline."""
        self.repo()
        source = self.root / "src" / "b.py"
        source_original = "second = 1\n"
        source.write_text(source_original)
        self.git("add", "src/b.py")
        self.git("commit", "-qm", "second file")
        counter = Path(self._feedback_tmp.name) / "preflight-counter"
        command = f"printf x >> {shlex.quote(str(counter))}; printf ok"
        run = self.init(evidence_mode="git")
        self.assign_with_verify("T01", command, file="src/a.py", executor="implementor")
        t01 = run / "T01-task.mdx"
        t01.write_text(t01.read_text().replace("env: \n", "env: T13_INPUT=one\n"))
        self.fill_task(t01)
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        digest = self.frozen("T01")[0]["digest"]
        self.assign_with_verify("T02", command, file="src/b.py", executor="implementor")
        task = run / "T02-task.mdx"
        task.write_text(task.read_text().replace("env: \n", "env: T13_INPUT=one\n"))
        self.fill_task(task)

        reused = self.cli("preflight", "demo", "T02")
        baseline = json.loads((run / ".baselines" / "T02.json").read_text())
        self.assertEqual("x", counter.read_text())
        self.assertIn(f"reused bundle {digest}", reused.stdout)
        self.assertEqual(digest, baseline["reused_bundle"])
        self.assertEqual("T01", baseline["reused_owner"])
        self.assertNotIn("running baseline verify", reused.stdout)

        source.write_text("second = 2\n")
        changed_source = self.cli("preflight", "demo", "T02")
        self.assertEqual("xx", counter.read_text())
        self.assertIn("running baseline verify", changed_source.stdout)

        source.write_text(source_original)
        restored = self.cli("preflight", "demo", "T02")
        self.assertEqual("xx", counter.read_text())
        self.assertIn(f"reused bundle {digest}", restored.stdout)

        changed_command = f"printf y >> {shlex.quote(str(counter))}; printf changed"
        task.write_text(task.read_text().replace(f"verify: {command}", f"verify: {changed_command}"))
        changed = self.cli("preflight", "demo", "T02")
        self.assertEqual("xxy", counter.read_text())
        self.assertIn("running baseline verify", changed.stdout)

        task.write_text(task.read_text().replace(f"verify: {changed_command}", f"verify: {command}"))
        restored_command = self.cli("preflight", "demo", "T02")
        self.assertEqual("xxy", counter.read_text())
        self.assertIn(f"reused bundle {digest}", restored_command.stdout)

        task.write_text(task.read_text().replace("env: T13_INPUT=one\n", "env: T13_INPUT=two\n"))
        changed_env = self.cli("preflight", "demo", "T02")
        self.assertEqual("xxyx", counter.read_text())
        self.assertIn("running baseline verify", changed_env.stdout)

    def test_concurrent_watchers_atomically_claim_one_role_event(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        command = [
            sys.executable, str(DOCKET), "watch", "demo", "--role", "orchestrator",
            "--interval", "1", "--timeout", "2",
        ]
        first = subprocess.Popen(command, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        first.communicate(timeout=5)
        second.communicate(timeout=5)
        self.assertEqual([0, 2], sorted([first.returncode, second.returncode]))

    def test_supervisors_arming_at_once_all_stay_armed(self) -> None:
        """Regression: arm read and rewrote watch.conf unlocked, so a racing arm lost a role.

        A second arm starts inside the first one's read-modify-write window and has a
        second to finish there; unlocked, the first then republished the list without it.
        """
        self.init5()
        rival = (f"env -u DOCKET_PERTURB {shlex.quote(sys.executable)} {shlex.quote(str(DOCKET))} "
                 "arm demo --role reviewer >/dev/null 2>&1 & sleep 1")
        self.cli("arm", "demo", "--role", "verifier", perturb=f"arm:before-publish={rival}")
        conf = self.root / ".docket" / "watch.conf"
        deadline = time.monotonic() + 20
        while "reviewer" not in conf.read_text() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(["demo reviewer", "demo verifier"],
                         sorted(conf.read_text().splitlines()))

    def test_wake_hook_requires_and_respects_session_role(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        self.cli("arm", "demo", "--role", "orchestrator")

        base_env = os.environ | {
            "CLAUDE_PROJECT_DIR": str(self.root),
            "DOCKET_WATCH_TIMEOUT": "0",
        }
        unscoped = subprocess.run([str(HOOK)], cwd=self.root, env=base_env)
        self.assertEqual(0, unscoped.returncode)
        planner = subprocess.run([str(HOOK)], cwd=self.root, env=base_env | {"DOCKET_ROLE": "planner"})
        self.assertEqual(0, planner.returncode)
        orchestrator = subprocess.run(
            [str(HOOK)], cwd=self.root, env=base_env | {"DOCKET_ROLE": "orchestrator"},
            capture_output=True, text=True,
        )
        self.assertEqual(2, orchestrator.returncode)
        self.assertIn("review batch ready", orchestrator.stderr)
        self.assertIn("Process normal review batches silently", orchestrator.stderr)

    def test_test_environment_strips_role_and_wake_config(self) -> None:
        """Regression: no test inherits the caller's role or wake configuration.

        setUp strips DOCKET_ROLE, DOCKET_WATCH_HOOK, and DOCKET_WATCH_TIMEOUT
        from the test process, so the suite passes from inside any docket role
        session. The child below replays the wake-hook test with all three set,
        the way an orchestrator session exports them; without the strip the
        unscoped hook run wakes and the child fails.
        """
        for key in ("DOCKET_ROLE", "DOCKET_WATCH_HOOK", "DOCKET_WATCH_TIMEOUT"):
            self.assertNotIn(key, os.environ)
        env = dict(os.environ, DOCKET_ROLE="orchestrator", DOCKET_WATCH_HOOK="1",
                   DOCKET_WATCH_TIMEOUT="8h", DOCKET_BIN=str(DOCKET))
        env["PYTHONPYCACHEPREFIX"] = str(Path(self._feedback_tmp.name) / "child-pycache")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest",
             "test_docket.DocketCLI.test_wake_hook_requires_and_respects_session_role"],
            cwd=str(Path(__file__).parent), text=True, capture_output=True,
            env=env, timeout=120,
        )
        self.assertEqual(0, proc.returncode, proc.stderr[-2000:])
        self.assertIn("OK", proc.stderr)

    def test_a_failing_hook_launcher_never_reads_as_a_wake(self) -> None:
        """Regression: uv and argparse exit 2 on their own errors, and 2 means wake.

        A broken hook then re-entered the session on every Stop with the error as
        its prompt, a loop that burns a full-context turn each time.
        """
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        self.cli("arm", "demo", "--role", "orchestrator")
        env = os.environ | {"CLAUDE_PROJECT_DIR": str(self.root), "DOCKET_ROLE": "orchestrator"}
        failure = self.root / ".docket" / "hook-failure-orchestrator"

        bad_arg = subprocess.run([str(HOOK)], cwd=self.root, capture_output=True, text=True,
                                 env=env | {"DOCKET_WATCH_TIMEOUT": "8h"})
        self.assertEqual(1, bad_arg.returncode)
        self.assertIn("docket wake hook failed (exit 2)", bad_arg.stderr)
        self.assertIn("exit: 2", failure.read_text())

        fake = Path(self._feedback_tmp.name) / "fake-uv"
        fake.mkdir()
        (fake / "uv").write_text("#!/bin/sh\necho 'error: no interpreter found' >&2\nexit 2\n")
        (fake / "uv").chmod(0o755)
        broken_uv = subprocess.run(
            [str(HOOK)], cwd=self.root, capture_output=True, text=True,
            env=env | {"DOCKET_WATCH_TIMEOUT": "0", "PATH": f"{fake}:{os.environ['PATH']}"},
        )
        self.assertEqual(1, broken_uv.returncode)
        self.assertIn("error: no interpreter found", failure.read_text())
        self.assertIn("wake hook for orchestrator last failed",
                      self.cli("doctor").stdout)

        woken = subprocess.run([str(HOOK)], cwd=self.root, capture_output=True, text=True,
                               env=env | {"DOCKET_WATCH_TIMEOUT": "0"})
        self.assertEqual(2, woken.returncode)
        self.assertIn("review batch ready", woken.stderr)
        self.assertFalse(failure.exists(), "a working hook clears the recorded failure")

    def test_changes_decision_is_filled_before_state_transition(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        opened = self.cli("decide", "demo", "T01", "--changes")
        self.assertIn("opened decision draft", opened.stdout)
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())
        self.assertFalse((run / "T01-report-02.mdx").exists())

        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Correct `src/a.py:1` and add a regression assertion.",
        ))
        self.cli("decide", "demo", "T01", "--changes")
        self.assertIn("status: changes-requested", (run / "T01-report-01.mdx").read_text())
        self.assertTrue((run / "T01-report-02.mdx").exists())

    def test_verified_model_overrides_requested_fallback_and_preserves_history(self) -> None:
        self.init()
        self.cli(
            "assign", "demo", "T01", "--harness", "opencode", "--model", "opencode/hy3-free",
            "--effort", "high", "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"',
        )
        first = self.cli(
            "set-model", "demo", "T01", "--actual", "minimax-m3", "--effort", "high"
        )
        self.assertIn("WARNING", first.stdout)
        switched = self.cli(
            "set-model", "demo", "T01", "--actual", "qwen3.8-27b", "--effort", "xhigh"
        )
        self.assertIn("model switched in existing session", switched.stdout)
        self.assertIn("effort switched in existing session", switched.stdout)

        run = self.root / ".docket" / "runs" / "demo"
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        for path in (run / "T01-task.mdx", run / "T01-report-01.mdx"):
            text = path.read_text()
            self.assertIn("actual_model: qwen3.8-27b", text)
            self.assertIn("model_history: opencode/hy3-free -> minimax-m3 -> qwen3.8-27b", text)
            self.assertIn("actual_effort: xhigh", text)
            self.assertIn("effort_history: high -> xhigh", text)

        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")

    def test_task_intent_does_not_require_supervisor_authored_code_map(self) -> None:
        self.init()
        self.cli(
            "assign", "demo", "T01", "--complexity", "high",
            "--executor", "implementor", "--harness", "opencode",
        )
        run = self.root / ".docket" / "runs" / "demo"
        invalid = self.cli("validate-task", "demo", "T01", ok=False)
        self.assertIn("INVALID TASK", invalid.stderr)
        self.fill_task(run / "T01-task.mdx")
        ready = self.cli("validate-task", "demo", "T01")
        self.assertIn("ready for implementor-owned discovery", ready.stdout)
        task = (run / "T01-task.mdx").read_text()
        self.assertIn("scope_status: discovery", task)
        self.assertIn("file_hints: \n", task)
        self.assertIn("verify_hint: \n", task)

    def test_discovery_capsule_claims_scope_and_reports_real_collision(self) -> None:
        self.init()
        run = self.assign("T01")
        self.assign("T02", claim_scope=False)
        capsule = run / "T02-scope.mdx"
        self.fill_scope(capsule)
        collision = self.cli("scope", "demo", "T02", "--submit", ok=False)
        self.assertIn("SCOPE COLLISION", collision.stderr)
        self.assertIn("src/a.py overlaps T01 at src/a.py", collision.stderr)
        self.assertIn("status: collision", capsule.read_text())

        wake = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(2, wake.returncode)
        self.assertIn("discovery scope collides", wake.stderr)

    def test_implementor_can_expand_scope_without_rebasing_away_changes(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "docket@example.test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Docket Test"], cwd=self.root, check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("a = 1\n")
        (self.root / "outside.py").write_text("outside = 1\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

        self.init()
        run = self.assign()
        (self.root / "src" / "a.py").write_text("a = 2\n")
        (self.root / "outside.py").write_text("outside = 2\n")
        capsule = run / "T01-scope.mdx"
        capsule.write_text(capsule.read_text().replace("files: src/a.py", "files: src/a.py outside.py"))
        amended = self.cli("scope", "demo", "T01", "--submit")
        self.assertIn("scope accepted", amended.stdout)

        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] src/a.py", diff)
        self.assertIn("[in scope] outside.py", diff)

    def test_partial_handoff_is_gated_and_wakes_orchestrator(self) -> None:
        self.init()
        run = self.assign()
        opened = self.cli("handoff", "demo", "T01")
        self.assertIn("opened handoff draft", opened.stdout)
        rejected = self.cli("handoff", "demo", "T01", "--submit", ok=False)
        self.assertIn("INVALID HANDOFF", rejected.stderr)

        checkpoint = run / "T01-handoff-01.mdx"
        self.fill_handoff(checkpoint)
        submitted = self.cli("handoff", "demo", "T01", "--submit")
        self.assertIn("ready for a replacement implementor", submitted.stdout)
        self.assertIn("status: ready", checkpoint.read_text())

        status = self.cli("status", "demo").stdout
        self.assertIn("1:ready", status)
        wake = self.cli(
            "watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False
        )
        self.assertEqual(2, wake.returncode)
        self.assertIn("handoff checkpoint 1 is ready", wake.stderr)

    def test_approval_preserves_the_report_body_and_records_review_evidence(self) -> None:
        """Regression: approval used to overwrite the report body with decision text."""
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        self.cli("submit", "demo", "T01")
        submitted_body = self.body_of(report)

        self.cli("decide", "demo", "T01", "--approve", "--reason", "Diff matches the criterion.")

        self.assertEqual(submitted_body, self.body_of(report))
        self.assertIn("Implemented the feature and verified its behavior.", submitted_body)
        self.assertIn("- [x] The feature works.", submitted_body)
        self.assertNotIn("# Decision:", submitted_body)
        self.assertIn("status: approved", report.read_text())
        self.assertIn("decision: T01-decision-01.mdx", report.read_text())

        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("verdict: approved", decision)
        self.assertIn("reviewer: orch", decision)
        self.assertIn("applied: yes", decision)
        self.assertIn("Diff matches the criterion.", decision)
        digest = "sha256:" + hashlib.sha256(submitted_body.encode()).hexdigest()
        self.assertIn(f"evidence_digest: {digest}", decision)

    def test_waiver_preserves_the_report_body_and_its_reason(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, blocked=True)
        self.cli("submit", "demo", "T01", "--blocked")
        submitted_body = self.body_of(report)

        self.cli("decide", "demo", "T01", "--waive", "--reason", "Upstream API is down.")

        self.assertEqual(submitted_body, self.body_of(report))
        self.assertIn("The reviewer must decide whether to waive", submitted_body)
        self.assertNotIn("## Waiver reason", self.body_of(report))
        self.assertIn("status: waived", report.read_text())

        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("verdict: waived", decision)
        self.assertIn("applied: yes", decision)
        self.assertIn("Upstream API is down.", decision)
        digest = "sha256:" + hashlib.sha256(submitted_body.encode()).hexdigest()
        self.assertIn(f"evidence_digest: {digest}", decision)

    def test_changes_transition_preserves_report_body_and_reviewer_text(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        self.cli("submit", "demo", "T01")
        submitted_body = self.body_of(report)

        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        self.assertIn("applied: no", decision.read_text())
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Correct `src/a.py:1` and add a regression assertion.",
        ))
        self.cli("decide", "demo", "T01", "--changes", "--reason", "The guard is untested.")

        self.assertEqual(submitted_body, self.body_of(report))
        self.assertIn("status: changes-requested", report.read_text())
        settled = decision.read_text()
        self.assertIn("1. Correct `src/a.py:1` and add a regression assertion.", settled)
        self.assertIn("The guard is untested.", settled)
        self.assertIn("verdict: changes-requested", settled)
        self.assertIn("applied: yes", settled)
        self.assertTrue((run / "T01-report-02.mdx").exists())

    def test_repeated_decision_neither_rewrites_evidence_nor_opens_an_extra_round(self) -> None:
        self.init()
        run = self.assign("T01", file="src/a.py")
        self.assign("T02", file="src/b.py")
        for owner in ("T01", "T02"):
            self.fill_task(run / f"{owner}-task.mdx")
            self.fill_task_report(run / f"{owner}-report-01.mdx")
            self.cli("submit", "demo", owner)

        self.cli("decide", "demo", "T01", "--approve", "--reason", "Correct as submitted.")
        approved_report = (run / "T01-report-01.mdx").read_bytes()
        approved_decision = (run / "T01-decision-01.mdx").read_bytes()
        repeated = self.cli("decide", "demo", "T01", "--approve")
        self.assertIn("already approved", repeated.stdout)
        self.assertIn("Nothing was rewritten", repeated.stdout)
        self.assertEqual(approved_report, (run / "T01-report-01.mdx").read_bytes())
        self.assertEqual(approved_decision, (run / "T01-decision-01.mdx").read_bytes())
        conflicting = self.cli(
            "decide", "demo", "T01", "--waive", "--reason", "second thoughts", ok=False
        )
        self.assertEqual(1, conflicting.returncode)
        self.assertIn("already approved", conflicting.stderr)
        self.assertEqual(approved_decision, (run / "T01-decision-01.mdx").read_bytes())

        self.cli("decide", "demo", "T02", "--changes")
        changes = run / "T02-decision-01.mdx"
        changes.write_text(changes.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Handle the empty input in `src/b.py:1`.",
        ))
        self.cli("decide", "demo", "T02", "--changes")
        round_two = (run / "T02-report-02.mdx").read_bytes()
        requested_decision = changes.read_bytes()
        again = self.cli("decide", "demo", "T02", "--changes")
        self.assertIn("already changes-requested", again.stdout)
        self.assertFalse((run / "T02-report-03.mdx").exists())
        self.assertEqual(round_two, (run / "T02-report-02.mdx").read_bytes())
        self.assertEqual(requested_decision, changes.read_bytes())

    def test_unavailable_git_evidence_is_never_reported_as_an_empty_diff(self) -> None:
        self.repo()
        run = self.init(evidence_mode="git")
        self.assertIn("evidence_mode: git", (run / "plan.mdx").read_text())
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")

        # The baseline was complete at dispatch. The evidence under it went away.
        shutil.rmtree(self.root / ".git")

        diff = self.cli("diff", "demo", "T01", ok=False)
        self.assertEqual(1, diff.returncode)
        self.assertIn("diff coverage unavailable", diff.stderr)
        self.assertIn("Git cannot report changes in", diff.stderr)
        self.assertIn("not an empty diff", diff.stderr)
        self.assertNotIn("no worktree changes", diff.stdout)

        rejected = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("diff coverage is unavailable", rejected.stderr)
        self.assertIn("evidence_mode: documents-only", rejected.stderr)
        self.assertIn("status: draft", (run / "T01-report-01.mdx").read_text())

    def test_documents_only_mode_stays_usable_and_labels_coverage_unavailable(self) -> None:
        run = self.init(evidence_mode="documents-only")
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")

        diff = self.cli("diff", "demo", "T01")
        self.assertIn("diff coverage unavailable", diff.stdout)
        self.assertIn("evidence_mode: documents-only", diff.stdout)
        self.assertNotIn("no worktree changes", diff.stdout)
        self.assertIn("evidence: documents-only", self.cli("status", "demo").stdout)

        submitted = self.cli("submit", "demo", "T01")
        self.assertIn("diff coverage: documents-only", submitted.stdout)
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())

    def test_peek_inspects_pending_events_without_consuming_them(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        ledger = run / ".woke-orchestrator"

        for _ in range(2):
            peek = self.cli("events", "demo", "--role", "orchestrator", "--peek")
            self.assertEqual(0, peek.returncode)
            self.assertIn("1 pending of 1 derived event(s)", peek.stdout)
            self.assertIn("[pending  ]", peek.stdout)
            self.assertIn("review batch ready", peek.stdout)
            self.assertIn("Nothing was claimed", peek.stdout)
            self.assertFalse(ledger.exists())

        empty = self.cli("events", "demo", "--role", "planner", "--peek")
        self.assertIn("no derived events for this role", empty.stdout)
        self.assertFalse((run / ".woke-planner").exists())

        wake = self.cli("watch", "demo", "--role", "orchestrator", "--timeout", "0", ok=False)
        self.assertEqual(2, wake.returncode)
        delivered = ledger.read_bytes()

        after = self.cli("events", "demo", "--role", "orchestrator", "--peek")
        self.assertIn("0 pending of 1 derived event(s)", after.stdout)
        self.assertIn("[delivered]", after.stdout)
        self.assertEqual(delivered, ledger.read_bytes())

    # ------------------------------- atomic publication and retry-safe transitions

    REQUIREMENT = "1. Correct `src/a.py:1` and add a regression assertion."
    REASON = "The guard is untested."

    def changes_draft(self) -> tuple[Path, Path, Path, str]:
        """A submitted report plus a filled, not yet applied changes decision draft."""
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        self.cli("submit", "demo", "T01")
        submitted_body = self.body_of(report)
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        return run, report, decision, submitted_body

    @staticmethod
    def rounds(run: Path, owner: str = "T01") -> list[str]:
        return sorted(path.name for path in run.glob(f"{owner}-report-*.mdx"))

    def test_an_interrupted_publication_never_exposes_a_partial_document(self) -> None:
        run, report, decision, submitted_body = self.changes_draft()
        drafted = decision.read_bytes()

        crash = self.cli(
            "decide", "demo", "T01", "--changes", ok=False, fault="publish:T01-decision-01.mdx"
        )
        self.assertEqual(70, crash.returncode)
        self.assertEqual(drafted, decision.read_bytes())
        self.assertIn("applied: no", decision.read_text())
        self.assertIn("status: submitted", report.read_text())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

        # The crash journalled a decision with no reason, and the journalled payload is
        # immutable, so the retry finishes it with the bare verdict.
        self.cli("decide", "demo", "T01", "--changes")
        self.assertIn("applied: yes", decision.read_text())
        self.assertEqual(submitted_body, self.body_of(report))
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))

    def test_an_interrupted_next_round_write_leaves_no_partial_round(self) -> None:
        run, report, _, _ = self.changes_draft()
        crash = self.cli(
            "decide", "demo", "T01", "--changes", ok=False, fault="publish:T01-report-02.mdx"
        )
        self.assertEqual(70, crash.returncode)
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))
        self.assertTrue(
            list(run.glob(".T01-report-02.mdx.*.tmp")),
            "an interrupted publication leaves its temporary file, never the artifact",
        )

        self.cli("decide", "demo", "T01", "--changes")
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))
        self.assertIn("# Report: T01 round 2", (run / "T01-report-02.mdx").read_text())

    def test_every_interrupted_changes_boundary_finishes_on_retry(self) -> None:
        """Interrupt each material write boundary; a retry must complete that transition."""
        boundaries = (
            "transition:begin", "transition:decision", "transition:report", "transition:next-round",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                self.tearDown()
                self.setUp()
                run, report, decision, submitted_body = self.changes_draft()
                journal = run / ".transitions" / "T01.json"

                crash = self.cli(
                    "decide", "demo", "T01", "--changes", "--reason", self.REASON,
                    ok=False, fault=boundary,
                )
                self.assertEqual(70, crash.returncode)
                self.assertIn(f"injected fault at {boundary}", crash.stderr)
                self.assertEqual("in-progress", json.loads(journal.read_text())["state"])

                self.cli("decide", "demo", "T01", "--changes", "--reason", self.REASON)

                self.assertEqual(submitted_body, self.body_of(report))
                settled = decision.read_text()
                self.assertIn("applied: yes", settled)
                self.assertIn("verdict: changes-requested", settled)
                self.assertIn("transition: txn:", settled)
                self.assertIn(self.REQUIREMENT, settled)
                self.assertIn(self.REASON, settled)
                self.assertIn("status: changes-requested", report.read_text())
                self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))
                self.assertEqual("complete", json.loads(journal.read_text())["state"])

                again = self.cli("decide", "demo", "T01", "--changes")
                self.assertIn("already changes-requested", again.stdout)
                self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))

    def test_an_interrupted_approval_finishes_without_touching_review_evidence(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        self.cli("submit", "demo", "T01")
        submitted_body = self.body_of(report)

        crash = self.cli(
            "decide", "demo", "T01", "--approve", "--reason", "Diff matches the criterion.",
            ok=False, fault="transition:decision",
        )
        self.assertEqual(70, crash.returncode)
        decision = run / "T01-decision-01.mdx"
        self.assertIn("applied: yes", decision.read_text())
        self.assertIn("status: submitted", report.read_text())
        stranded = decision.read_bytes()

        resumed = self.cli("decide", "demo", "T01", "--approve")
        self.assertIn("resumed interrupted approved transition txn:", resumed.stdout)
        self.assertEqual(stranded, decision.read_bytes())
        self.assertEqual(submitted_body, self.body_of(report))
        self.assertIn("status: approved", report.read_text())
        self.assertIn("decision: T01-decision-01.mdx", report.read_text())
        self.assertEqual(1, len(list(run.glob("T01-decision-*.mdx"))))
        digest = "sha256:" + hashlib.sha256(submitted_body.encode()).hexdigest()
        self.assertIn(f"evidence_digest: {digest}", decision.read_text())

    def verified5(self) -> Path:
        """A five-role round submitted and passed by its verifier, ready to approve."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "1. ok")
        return run

    def test_an_interrupted_approval_rechecks_the_evidence_it_bound(self) -> None:
        """Regression: a recovered approval skipped every accepting-verdict guard.

        An approval interrupted before its first artifact, then a contract edit, then
        a bare retry used to approve against acceptance nobody verified. Nothing was
        published, so the transition is abandoned and the reviewer decides again.
        """
        run = self.verified5()
        report = run / "T01-report-01.mdx"
        journal = run / ".transitions" / "T01.json"
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer",
                 "--reason", "Diff matches.", ok=False, fault="transition:begin")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text() + "\nThe feature must also be fast.\n")

        retry = self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer", ok=False)
        self.assertEqual(1, retry.returncode)
        self.assertIn("but the task now reads differently", retry.stderr)
        self.assertIn("was abandoned", retry.stderr)
        self.assertEqual("abandoned", json.loads(journal.read_text())["state"])
        self.assertIn("Diff matches.", journal.read_text(), "the reviewer's text is kept")
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        self.assertIn("status: submitted", report.read_text())

        opened = self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
        self.assertIn("opened decision draft", opened.stdout)

    def test_an_interrupted_decision_never_binds_a_body_edited_after_it(self) -> None:
        """Regression: retries bound or approved report text nobody reviewed.

        Before the decision artifact, `--re-review` recorded the new body without
        freezing it. After it, a bare retry published `approved` over the new body.
        """
        edit = ("Implemented the feature and verified its behavior.", "EDITED AFTER THE VERDICT.")
        with self.subTest(boundary="transition:begin"):
            run = self.verified5()
            report = run / "T01-report-01.mdx"
            ledger = run / ".bundles" / "T01" / "rounds.json"
            self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer",
                     "--reason", "Diff matches.", ok=False, fault="transition:begin")
            frozen = ledger.read_bytes()
            report.write_text(report.read_text().replace(*edit))

            retry = self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer",
                             "--re-review", ok=False)
            self.assertEqual(1, retry.returncode)
            self.assertIn("was abandoned", retry.stderr)
            self.assertEqual(frozen, ledger.read_bytes(), "nothing unfrozen was bound")
            self.assertFalse((run / "T01-decision-01.mdx").exists())
            self.assertIn("status: submitted", report.read_text())

        self.tearDown()
        self.setUp()
        with self.subTest(boundary="transition:decision"):
            run = self.verified5()
            report = run / "T01-report-01.mdx"
            reviewed = report.read_text()
            self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer",
                     "--reason", "Diff matches.", ok=False, fault="transition:decision")
            report.write_text(reviewed.replace(*edit))

            retry = self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer", ok=False)
            self.assertEqual(1, retry.returncode)
            self.assertIn("restore the reviewed body", retry.stderr)
            self.assertIn("status: submitted", report.read_text())

            report.write_text(reviewed)
            self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
            self.assertIn("status: approved", report.read_text())

    def test_an_approval_finished_on_retry_closes_its_escalation(self) -> None:
        """Regression: only the uninterrupted path settled an open escalation.

        An approval interrupted after its report step finished on retry through the
        already-moved branch, which left the escalation open and the plan owner woken
        for work that was already accepted.
        """
        run = self.init5_in("eg", mode="quick")
        self.assign_simple_in(run, "eg", "T01")
        self.cli("dispatch", "eg", "T01", "--session", "w1", "--register")

        def submit(rnd: int) -> None:
            self.fill_task_report(run / f"T01-report-{rnd:02d}.mdx",
                                  files="- `src/t01.py:1` - implemented T01.")
            self.cli("submit", "eg", "T01", "--as", "implementor")

        def changes(rnd: int, ok: bool = True) -> subprocess.CompletedProcess[str]:
            self.cli("decide", "eg", "T01", "--changes", "--as", "checker")
            dec = run / f"T01-decision-{rnd:02d}.mdx"
            dec.write_text(dec.read_text().replace(self.DECISION_PLACEHOLDER, self.REQUIREMENT))
            return self.cli("decide", "eg", "T01", "--changes", "--as", "checker", ok=ok)

        submit(1)
        changes(1)
        submit(2)
        changes(2)
        submit(3)
        changes(3, ok=False)
        escalation = run / ".escalations" / "T01-r01.json"
        self.assertEqual("open", json.loads(escalation.read_text())["state"])
        self.cli("verify", "eg", "T01", "--result", "pass", "--as", "checker", "--detail", "1. ok")
        self.cli("decide", "eg", "T01", "--approve", "--as", "checker", "--reason", "holds",
                 ok=False, fault="transition:report")

        self.cli("decide", "eg", "T01", "--approve", "--as", "checker")
        self.assertEqual("closed", json.loads(escalation.read_text())["state"])
        self.assertNotIn("T01:escalated:T01-r01", self.derived_keys("eg", "coordinator"))

    def test_a_transition_interrupted_without_a_journal_still_opens_one_next_round(self) -> None:
        """Regression: this exact state used to be terminal.

        Before retry-safe transitions, an interruption between the report status
        write and the next round left `status: changes-requested` with no round 2,
        and every retry answered "already changes-requested" and returned, so the
        round was never opened. The state is reconstructed by hand here, without
        fault injection, so it is exactly what an older docket would leave behind.
        """
        run, report, decision, submitted_body = self.changes_draft()
        decision.write_text(decision.read_text().replace("applied: no", "applied: yes"))
        report.write_text(report.read_text().replace(
            "status: submitted", "status: changes-requested\ndecision: T01-decision-01.mdx"
        ))
        self.assertFalse((run / ".transitions").exists())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

        recovered = self.cli("decide", "demo", "T01", "--changes")
        self.assertIn("resumed interrupted changes-requested transition txn:", recovered.stdout)
        self.assertIn("applied next-round", recovered.stdout)
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))
        self.assertEqual(submitted_body, self.body_of(report))
        self.assertIn(self.REQUIREMENT, decision.read_text())

        again = self.cli("decide", "demo", "T01", "--changes")
        self.assertIn("already changes-requested", again.stdout)
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))

    def test_an_unfinished_transition_blocks_a_different_verdict_and_is_visible(self) -> None:
        run, report, decision, _ = self.changes_draft()
        self.cli("decide", "demo", "T01", "--changes", ok=False, fault="transition:begin")

        status = self.cli("status", "demo").stdout
        self.assertIn("unfinished decision transitions", status)
        self.assertIn("T01 round 1 changes-requested txn:", status)

        hijack = self.cli("decide", "demo", "T01", "--approve", ok=False)
        self.assertEqual(1, hijack.returncode)
        self.assertIn("unfinished changes-requested transition", hijack.stderr)
        self.assertIn("applied: no", decision.read_text())
        self.assertIn("status: submitted", report.read_text())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

        self.cli("decide", "demo", "T01", "--changes")
        self.assertNotIn("unfinished decision transitions", self.cli("status", "demo").stdout)

    def test_concurrent_deciders_are_serialized_into_one_next_round(self) -> None:
        run, report, decision, submitted_body = self.changes_draft()
        command = [
            sys.executable, str(DOCKET), "decide", "demo", "T01", "--changes",
            "--reason", self.REASON,
        ]
        first = subprocess.Popen(
            command, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        second = subprocess.Popen(
            command, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        first.communicate(timeout=30)
        second.communicate(timeout=30)

        self.assertEqual([0, 0], [first.returncode, second.returncode])
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))
        self.assertEqual(1, len(list(run.glob("T01-decision-*.mdx"))))
        self.assertIn("applied: yes", decision.read_text())
        self.assertEqual(submitted_body, self.body_of(report))

    def test_a_decision_cannot_apply_to_report_evidence_that_changed_after_review(self) -> None:
        run, report, decision, _ = self.changes_draft()
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Implemented the feature, verified its behavior, and rewrote the guard.",
        ))

        stale = self.cli("decide", "demo", "T01", "--changes", ok=False)
        self.assertEqual(1, stale.returncode)
        self.assertIn("changed after review began", stale.stderr)
        self.assertIn("--re-review", stale.stderr)
        self.assertIn("applied: no", decision.read_text())
        self.assertIn("status: submitted", report.read_text())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

        changed_body = self.body_of(report)
        applied = self.cli(
            "decide", "demo", "T01", "--changes", "--re-review", "--reason", self.REASON
        )
        self.assertIn("re-verifying changed evidence", applied.stdout)
        self.assertIn("re-verification passed", applied.stdout)
        settled = decision.read_text()
        digest = "sha256:" + hashlib.sha256(changed_body.encode()).hexdigest()
        self.assertIn(f"evidence_digest: {digest}", settled)
        self.assertIn("reverified: yes", settled)
        self.assertIn("superseded_evidence: sha256:", settled)
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))
        self.assertEqual(changed_body, self.body_of(report))

    def submitted_report(self, *, blocked: bool = False) -> tuple[Path, Path, str]:
        """A submitted report, blocked or not, with nothing decided yet."""
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, blocked=blocked)
        self.cli("submit", "demo", "T01", *(["--blocked"] if blocked else []))
        return run, report, self.body_of(report)

    def begun_transition(self, verdict: str) -> tuple[Path, Path]:
        """A transition interrupted before it published any artifact.

        `transition:begin` fires after the journal and before the first document, so
        the reviewer's text exists only where the recovery path can find it.
        """
        if verdict == "--changes":
            run, report, decision, _ = self.changes_draft()
        else:
            run, report, _ = self.submitted_report(blocked=verdict == "--waive")
            decision = run / "T01-decision-01.mdx"
        crash = self.cli(
            "decide", "demo", "T01", verdict, "--reason", self.REASON, "--reviewer", "planner",
            ok=False, fault="transition:begin",
        )
        self.assertEqual(70, crash.returncode)
        self.assertEqual("in-progress", json.loads((run / ".transitions" / "T01.json").read_text())["state"])
        return run, decision

    def test_an_interrupted_transition_keeps_reviewer_text_a_retry_does_not_repeat(self) -> None:
        """Regression: the retry used to rebuild the decision from the arguments.

        An approve or waive interrupted before its decision artifact existed left the
        reviewer's reason nowhere on disk, so a retry that supplied only the verdict
        recorded `none` instead - and a waiver retry could not even run, because the
        reason was mandatory. The payload is now journalled before the first write.
        """
        for verdict, recorded in (
            ("--approve", "approved"), ("--waive", "waived"), ("--changes", "changes-requested"),
        ):
            with self.subTest(verdict=verdict):
                self.tearDown()
                self.setUp()
                run, decision = self.begun_transition(verdict)

                resumed = self.cli("decide", "demo", "T01", verdict)

                self.assertIn(f"resumed interrupted {recorded} transition txn:", resumed.stdout)
                settled = decision.read_text()
                self.assertIn(f"verdict: {recorded}", settled)
                self.assertIn("reviewer: planner", settled)
                self.assertIn("applied: yes", settled)
                self.assertIn(self.REASON, settled)
                self.assertNotIn("## Reason\n\nnone", settled)
                if verdict == "--changes":
                    self.assertIn(self.REQUIREMENT, settled)
                    self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))
                self.assertEqual("complete", json.loads((run / ".transitions" / "T01.json").read_text())["state"])

    def test_a_fresh_waiver_still_requires_a_stated_reason(self) -> None:
        """Only a retry may omit it, because only a retry has one already recorded."""
        run, report, _ = self.submitted_report(blocked=True)

        refused = self.cli("decide", "demo", "T01", "--waive", ok=False)

        self.assertEqual(1, refused.returncode)
        self.assertIn("--waive requires --reason TEXT", refused.stderr)
        self.assertIn("status: blocked", report.read_text())
        self.assertFalse((run / "T01-decision-01.mdx").exists())

    def test_a_retry_cannot_rewrite_the_reason_a_transition_already_recorded(self) -> None:
        run, decision = self.begun_transition("--approve")

        conflict = self.cli(
            "decide", "demo", "T01", "--approve", "--reason", "Different rationale.", ok=False
        )
        self.assertEqual(1, conflict.returncode)
        self.assertIn("already recorded the reason", conflict.stderr)
        self.assertFalse(decision.exists())
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())

        hijack = self.cli("decide", "demo", "T01", "--approve", "--reviewer", "orch", ok=False)
        self.assertEqual(1, hijack.returncode)
        self.assertIn("recorded reviewer 'planner'", hijack.stderr)

        self.cli("decide", "demo", "T01", "--approve")
        settled = decision.read_text()
        self.assertIn(self.REASON, settled)
        self.assertNotIn("Different rationale.", settled)
        self.assertIn("reviewer: planner", settled)

    def test_a_retry_cannot_fill_in_a_reason_the_transition_recorded_as_absent(self) -> None:
        """The journalled payload is immutable in every field, empty ones included.

        A retry used to be allowed to fill in a reason the interrupted attempt had
        recorded as `none`, on the argument that nothing was published under it. But
        the journal is written before the first artifact precisely so that it, and not
        the invocation, is the decision: adding a reason there publishes a decision
        nobody made under an identity somebody else began.
        """
        run, report, decision, _ = self.changes_draft()
        crash = self.cli("decide", "demo", "T01", "--changes", ok=False, fault="transition:begin")
        self.assertEqual(70, crash.returncode)
        journal = json.loads((run / ".transitions" / "T01.json").read_text())
        self.assertIn("## Reason\n\nnone", journal["decision_body"])

        conflict = self.cli(
            "decide", "demo", "T01", "--changes", "--reason", self.REASON, ok=False
        )

        self.assertEqual(1, conflict.returncode)
        self.assertIn("already recorded the reason 'none'", conflict.stderr)
        self.assertIn("applied: no", decision.read_text())
        self.assertIn("status: submitted", report.read_text())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

        self.cli("decide", "demo", "T01", "--changes")
        settled = decision.read_text()
        self.assertIn("## Reason\n\nnone", settled)
        self.assertNotIn(self.REASON, settled)
        self.assertIn("applied: yes", settled)
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run))

    def test_re_review_of_a_changed_blocked_report_skips_completion_verification(self) -> None:
        """A blocked report is waived, not verified, and re-review must keep it that way.

        Re-review used to run the registered command for every changed body. A report
        blocked *because* that command fails could then never be re-reviewed after an
        edit, so the lightweight waiver path the gate promises was unreachable exactly
        where it matters. Submission skips completion verification for a blocked
        report; re-review now does the same.
        """
        run, report, _ = self.submitted_report(blocked=True)
        task = run / "T01-task.mdx"
        text = task.read_text()
        registered = next(line for line in text.splitlines() if line.startswith("verify:"))
        task.write_text(text.replace(registered, "verify: false"))
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        report.write_text(report.read_text().replace(
            "The reviewer must decide whether to waive the external failure.",
            "The upstream dependency is still broken; the reviewer must decide on a waiver.",
        ))
        changed_body = self.body_of(report)

        stale = self.cli("decide", "demo", "T01", "--waive", "--reason", self.REASON, ok=False)
        self.assertEqual(1, stale.returncode)
        self.assertIn("changed after review began", stale.stderr)

        waived = self.cli(
            "decide", "demo", "T01", "--waive", "--re-review", "--reason", self.REASON
        )

        self.assertIn("re-review passed the report gate", waived.stdout)
        self.assertIn("skips completion verification", waived.stdout)
        self.assertNotIn("re-verifying changed evidence", waived.stdout)
        settled = decision.read_text()
        self.assertIn("verdict: waived", settled)
        self.assertIn("reverified: blocked", settled)
        self.assertIn("superseded_evidence: sha256:", settled)
        self.assertIn(self.REASON, settled)
        self.assertIn("status: waived", report.read_text())
        self.assertEqual(changed_body, self.body_of(report))

    def test_re_review_refuses_changed_evidence_that_no_longer_passes_the_gate(self) -> None:
        """A passing verify command is not a passing report.

        Re-verification used to run only the registered command, so a report edited
        into a state `docket submit` would have rejected could still be decided.
        """
        edits = {
            "unchecked acceptance": ("- [x] The feature works.", "- [ ] The feature works."),
            "emptied summary": (
                "Implemented the feature and verified its behavior.",
                "<!-- TODO: what you actually did. -->",
            ),
            "rewritten acceptance criterion": (
                "- [x] The feature works.", "- [x] The feature mostly works.",
            ),
        }
        for label, (old, new) in edits.items():
            with self.subTest(edit=label):
                self.tearDown()
                self.setUp()
                run, report, decision, _ = self.changes_draft()
                report.write_text(report.read_text().replace(old, new))

                refused = self.cli(
                    "decide", "demo", "T01", "--changes", "--re-review", "--reason", self.REASON,
                    ok=False,
                )

                self.assertEqual(1, refused.returncode)
                self.assertIn("no longer passes the report gate", refused.stderr)
                self.assertNotIn("re-verification passed", refused.stdout)
                self.assertIn("applied: no", decision.read_text())
                self.assertIn("status: submitted", report.read_text())
                self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

    def test_re_review_re_runs_the_scope_and_diff_checks_under_git_evidence(self) -> None:
        """Structural checks are not the only ones a changed body has to clear again."""
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "docket@example.test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Docket Test"], cwd=self.root, check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("allowed = 1\n")
        subprocess.run(["git", "add", "src/a.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

        run = self.init()
        self.assertIn("evidence_mode: git", (run / "plan.mdx").read_text())
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        report.write_text(report.read_text().replace(
            "- `src/a.py:1` - implemented the feature.", "- Nothing worth listing.",
        ))

        refused = self.cli(
            "decide", "demo", "T01", "--changes", "--re-review", "--reason", self.REASON, ok=False
        )

        self.assertEqual(1, refused.returncode)
        self.assertIn("no longer passes the report gate", refused.stderr)
        self.assertIn("task-local changes omitted from 'Files changed': src/a.py", refused.stderr)
        self.assertIn("applied: no", decision.read_text())
        self.assertIn("status: submitted", report.read_text())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

    def test_re_review_refuses_a_changed_report_that_fails_verification(self) -> None:
        run, report, decision, _ = self.changes_draft()
        task = run / "T01-task.mdx"
        text = task.read_text()
        registered = next(line for line in text.splitlines() if line.startswith("verify:"))
        task.write_text(text.replace(registered, "verify: false"))
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.", "Rewrote the feature entirely.",
        ))

        refused = self.cli("decide", "demo", "T01", "--changes", "--re-review", ok=False)
        self.assertEqual(1, refused.returncode)
        self.assertIn("re-verification failed", refused.stderr)
        self.assertIn("applied: no", decision.read_text())
        self.assertIn("status: submitted", report.read_text())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run))

    # ------------------------------------------------------- multi-root baselines

    def test_a_git_run_declares_every_checkout_root_before_dispatch(self) -> None:
        self.repo()
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        self.repo(self.root / "vendor" / "lib")

        run = self.init(evidence_mode="git", roots=("root=.", "wt=wt", "lib=vendor/lib"))
        listing = self.cli("roots", "demo").stdout
        self.assertIn("3 declared checkout root(s)", listing)

        declared = json.loads((run / ".snapshots" / "roots.json").read_text())["roots"]
        by_alias = {record["alias"]: record for record in declared}
        self.assertEqual({"root", "wt", "lib"}, set(by_alias))
        self.assertEqual(str(self.root.resolve()), by_alias["root"]["path"])
        self.assertEqual("main", by_alias["root"]["branch"])
        self.assertEqual("feature", by_alias["wt"]["branch"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), by_alias["root"]["head"])
        # Two worktrees of one repository share Git storage and are still distinct
        # change surfaces, so identity is the worktree, never the common directory.
        self.assertEqual(by_alias["root"]["common_dir"], by_alias["wt"]["common_dir"])
        self.assertNotEqual(by_alias["root"]["git_dir"], by_alias["wt"]["git_dir"])
        self.assertNotEqual(by_alias["root"]["common_dir"], by_alias["lib"]["common_dir"])
        self.assertIn("Some roots share Git storage", listing)

    def test_worktrees_sharing_git_storage_do_not_collide_on_equal_paths(self) -> None:
        self.repo()
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        run = self.init(evidence_mode="git", roots=("root=.", "wt=wt"))

        for owner, claimed in (("T01", "src/a.py"), ("T02", "wt:src/a.py")):
            self.cli("assign", "demo", owner, "--file", claimed, "--verify", "true")
            self.fill_scope(run / f"{owner}-scope.mdx")
            accepted = self.cli("scope", "demo", owner, "--submit")
            self.assertIn("scope accepted", accepted.stdout)

        self.cli("assign", "demo", "T03", "--file", "src/a.py", "--verify", "true")
        self.fill_scope(run / "T03-scope.mdx")
        clash = self.cli("scope", "demo", "T03", "--submit", ok=False)
        self.assertIn("SCOPE COLLISION", clash.stderr)
        self.assertIn("root:src/a.py overlaps T01 at root:src/a.py", clash.stderr)
        self.assertNotIn("T02", clash.stderr)

    def test_a_nested_repository_resolves_to_its_own_declared_root(self) -> None:
        self.repo()
        nested = self.repo(self.root / "vendor" / "lib")
        run = self.init(evidence_mode="git", roots=("root=.", "lib=vendor/lib"))
        self.cli("assign", "demo", "T01", "--file", "vendor/lib/src/a.py", "--verify", "true")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")

        (nested / "src" / "a.py").write_text("allowed = 2\n")
        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("changes since its baseline", diff)
        self.assertIn("[in scope] lib:src/a.py", diff)
        # The outer checkout never owns a nested repository's paths.
        self.assertNotIn("root:vendor", diff)
        self.assertNotIn("no worktree changes", diff)

    def test_a_task_baseline_reconstructs_the_dirt_it_was_assigned_over(self) -> None:
        self.repo()
        tracked = self.root / "src" / "a.py"
        tracked.write_text("allowed = 2\n")
        self.git("add", "src/a.py")
        tracked.write_text("allowed = 3\n")

        run = self.init(evidence_mode="git")
        self.assign()
        record = json.loads((run / ".snapshots" / "T01.json").read_text())["roots"][0]
        self.assertEqual("recorded", record["status"])
        # A fingerprint of the dirty file is gone the moment the implementor edits it.
        tracked.write_text("allowed = 4\n")

        with tempfile.TemporaryDirectory() as scratch:
            clone = Path(scratch) / "clone"
            self.git("clone", "-q", str(self.root), str(clone))
            self.git("checkout", "-q", record["head"], cwd=clone)
            captured = run / ".snapshots" / "T01"
            for name, expected in (
                (record["staged_patch"], "allowed = 2\n"),
                (record["worktree_patch"], "allowed = 3\n"),
            ):
                patch = Path(scratch) / name
                patch.write_bytes((captured / name).read_bytes())
                self.git("apply", "--binary", str(patch), cwd=clone)
                self.assertEqual(expected, (clone / "src" / "a.py").read_text())

    def test_a_baseline_captures_untracked_content_modes_symlinks_and_binaries(self) -> None:
        self.repo()
        (self.root / "notes.txt").write_text("dirty note\n")
        script = self.root / "tool.sh"
        script.write_text("#!/bin/sh\necho hi\n")
        script.chmod(0o755)
        payload = bytes(range(256)) * 8
        (self.root / "asset.bin").write_bytes(payload)
        (self.root / "link.txt").symlink_to("notes.txt")

        run = self.init(evidence_mode="git")
        self.assign()
        record = json.loads((run / ".snapshots" / "T01.json").read_text())["roots"][0]
        manifest = run / ".snapshots" / "T01" / record["untracked_manifest"]
        entries = json.loads(manifest.read_text())["entries"]

        self.assertEqual(b"dirty note\n", base64.b64decode(entries["notes.txt"]["content_base64"]))
        self.assertEqual(payload, base64.b64decode(entries["asset.bin"]["content_base64"]))
        self.assertEqual("executable", entries["tool.sh"]["kind"])
        self.assertEqual({"kind": "symlink", "target": "notes.txt"}, entries["link.txt"])
        self.assertEqual("full", record["reconstructable"])

    def test_a_baseline_records_initial_deletions_and_mode_changes(self) -> None:
        self.repo()
        keep = self.root / "src" / "keep.sh"
        keep.write_text("#!/bin/sh\necho keep\n")
        gone = self.root / "src" / "gone.py"
        gone.write_text("gone = 1\n")
        self.git("add", "src/keep.sh", "src/gone.py")
        self.git("commit", "-qm", "more")
        gone.unlink()
        keep.chmod(0o755)

        run = self.init(evidence_mode="git")
        self.assign()
        record = json.loads((run / ".snapshots" / "T01.json").read_text())["roots"][0]
        patch = (run / ".snapshots" / "T01" / record["worktree_patch"]).read_text()
        self.assertIn("src/gone.py", patch)
        self.assertIn("deleted file mode 100644", patch)
        self.assertIn("old mode 100644", patch)
        self.assertIn("new mode 100755", patch)

    def test_the_run_baseline_is_taken_before_the_first_implementor_edit(self) -> None:
        self.repo()
        run = self.init(evidence_mode="git")
        self.assertFalse((run / ".snapshots" / "run.json").is_file())

        self.assign()
        captured = json.loads((run / ".snapshots" / "run.json").read_text())
        self.assertEqual("run", captured["kind"])
        self.assertEqual("recorded", captured["baseline"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), captured["roots"][0]["head"])

        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        (self.root / "src" / "b.py").write_text("b = 1\n")
        diff = self.cli("diff", "demo", "run").stdout
        self.assertIn("src/a.py", diff)
        self.assertIn("src/b.py", diff)
        self.assertNotIn("OUTSIDE SCOPE", diff)

    def test_edits_committed_during_a_task_are_not_reported_as_an_empty_diff(self) -> None:
        self.repo()
        run = self.init(evidence_mode="git")
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")

        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.git("add", "src/a.py")
        self.git("commit", "-qm", "implementor commit")

        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] src/a.py", diff)
        self.assertNotIn("no worktree changes", diff)

        (self.root / "outside.py").write_text("outside = 1\n")
        self.git("add", "outside.py")
        self.git("commit", "-qm", "committed outside the accepted scope")
        rejected = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("outside the task scope: outside.py", rejected.stderr)

    def test_unresolved_and_missing_roots_block_a_claim_of_diff_coverage(self) -> None:
        self.repo()
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        run = self.init(evidence_mode="git", roots=("root=.", "wt=wt"))

        self.cli("assign", "demo", "T01", "--file", "ghost:src/a.py", "--verify", "true")
        self.fill_scope(run / "T01-scope.mdx")
        noted = self.cli("scope", "demo", "T01", "--submit")
        self.assertIn("undeclared root alias", noted.stderr)
        unresolved = self.cli("diff", "demo", "T01", ok=False)
        self.assertIn("undeclared root alias 'ghost'", unresolved.stderr)
        self.assertIn("not an empty diff", unresolved.stderr)

        self.cli("assign", "demo", "T02", "--file", "wt:src/a.py", "--verify", "true")
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        self.git("worktree", "remove", "--force", "wt")
        missing = self.cli("diff", "demo", "T02", ok=False)
        self.assertIn("is missing", missing.stderr)
        self.assertIn("not an empty diff", missing.stderr)
        self.assertNotIn("no worktree changes", missing.stdout)

    def test_documents_only_runs_declare_no_roots_and_admit_it(self) -> None:
        run = self.init(evidence_mode="documents-only")
        listing = self.cli("roots", "demo").stdout
        self.assertIn("Git baseline coverage is unavailable", listing)
        self.assertIn("not claiming an unchanged tree", listing)
        self.assertFalse((run / ".snapshots" / "roots.json").is_file())

        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        submitted = self.cli("submit", "demo", "T01")
        self.assertIn("diff coverage: documents-only", submitted.stdout)

    def test_a_declared_root_set_is_not_rediscovered_once_a_baseline_exists(self) -> None:
        self.repo()
        run = self.init(evidence_mode="git")
        self.assign()
        self.git("worktree", "add", "-q", "-b", "late", "late")

        unchanged = self.cli("roots", "demo").stdout
        self.assertIn("1 declared checkout root(s)", unchanged)
        refused = self.cli("roots", "demo", "--redeclare", "root=.", "late=late", ok=False)
        self.assertIn("baselines already reference the declared roots", refused.stderr)
        declared = json.loads((run / ".snapshots" / "roots.json").read_text())["roots"]
        self.assertEqual(["root"], [record["alias"] for record in declared])

        # A declaration that goes missing under a baseline is never rediscovered. A
        # delegated task captures its baseline at dispatch, so that is what refuses.
        (run / ".snapshots" / "roots.json").unlink()
        self.cli("assign", "demo", "T02", "--file", "src/b.py", "--verify", "true")
        self.fill_task(run / "T02-task.mdx")
        blocked = self.cli("dispatch", "demo", "T02", "--session", "s2", "--register", ok=False)
        self.assertIn("cannot capture a complete task baseline", blocked.stderr)
        self.assertFalse((run / ".snapshots" / "T02.json").exists())
        self.assertFalse((run / ".dispatch" / "T02.json").exists())
        again = self.cli("roots", "demo", "--declare", "root=.", ok=False)
        self.assertIn("baselines were captured against a root declaration", again.stderr)

    def test_multi_root_change_lists_are_root_qualified_through_the_gate(self) -> None:
        self.repo()
        linked = self.root / "wt"
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        run = self.init(evidence_mode="git", roots=("root=.", "wt=wt"))
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--harness", "opencode",
            "--file", "src/a.py", "--file", "wt:src/a.py", "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")

        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        (linked / "src" / "a.py").write_text("allowed = 3\n")
        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] root:src/a.py", diff)
        self.assertIn("[in scope] wt:src/a.py", diff)

        rejected = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("root:src/a.py", rejected.stderr)
        self.assertIn("wt:src/a.py", rejected.stderr)

        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text().replace(
            "- `src/a.py:1` - implemented the feature.",
            "- `root:src/a.py:1` - implemented the feature.\n"
            "- `wt:src/a.py:1` - mirrored it in the linked worktree.",
        ))
        submitted = self.cli("submit", "demo", "T01")
        self.assertIn("diff coverage: available", submitted.stdout)
        self.assertIn("status: submitted", report.read_text())

    def test_the_assigned_verify_command_is_a_floor_the_capsule_cannot_lower(self) -> None:
        """Regression: a capsule `verify: true` replaced the supervisor's oracle outright."""
        self.init()
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/a.py", "--verify", "false")
        self.fill_task(run / "T01-task.mdx")
        capsule = run / "T01-scope.mdx"
        self.fill_scope(capsule)
        capsule.write_text(capsule.read_text().replace("verify: false", "verify: true"))
        accepted = self.cli("scope", "demo", "T01", "--submit")
        self.assertIn("runs the assigned verify command as well", accepted.stdout)
        self.assertEqual("(false) && (true)", parse_meta(run / "T01-task.mdx")["verify"])

        self.fill_task_report(run / "T01-report-01.mdx")
        refused = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("verify command failed", refused.stderr)

    def test_a_block_that_says_none_under_the_template_comment_is_refused(self) -> None:
        """Regression: the template comment above "none" hid it from the check."""
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        self.assertIn("Do not edit status. -->\n\nnone", report.read_text(),
                      "the template itself leaves none under its comment")
        refused = self.cli("submit", "demo", "T01", "--blocked", ok=False)
        self.assertIn("'Decisions needed' says none", refused.stderr)

    def test_an_unrecorded_model_never_makes_blocking_harder_than_submitting(self) -> None:
        """Regression: `--blocked` was refused until actual_model was recorded."""
        self.init()
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--model", "vendor/small", "--file", "src/a.py",
                 "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        refused = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("docket set-model demo T01 --actual MODEL", refused.stderr)

        report.write_text(report.read_text().replace(
            "Do not edit status. -->\n\nnone", "Do not edit status. -->\n\nWhich schema wins?"))
        blocked = self.cli("submit", "demo", "T01", "--blocked")
        self.assertIn("blocked", blocked.stdout)

    def test_files_changed_needs_the_exact_path_not_a_lookalike(self) -> None:
        """Regression: `docs/src/a.py.orig` satisfied a change to `src/a.py`."""
        self.repo()
        run = self.init(evidence_mode="git")
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        fresh = report.read_text()
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        for lookalike in ("docs/src/a.py.orig", "src/a.pyi", "tests/src/a.py"):
            with self.subTest(lookalike=lookalike):
                report.write_text(fresh)
                self.fill_task_report(report, files=f"- `{lookalike}` - unrelated note.")
                refused = self.cli("submit", "demo", "T01", ok=False)
                self.assertIn("task-local changes omitted from 'Files changed': src/a.py",
                              refused.stderr)
        report.write_text(fresh)
        self.fill_task_report(report, files="- `src/a.py:1` - changed the value.")
        self.cli("submit", "demo", "T01")

    def test_a_capsule_that_keeps_the_assigned_verify_runs_it_once(self) -> None:
        self.init()
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.assertEqual('printf "T01 ok\\n"', parse_meta(run / "T01-task.mdx")["verify"])

    def test_a_whole_root_scope_admits_only_that_root(self) -> None:
        """Regression: claiming `wt:` admitted a change in every other declared root."""
        self.repo()
        linked = self.root / "wt"
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        run = self.init(evidence_mode="git", roots=("root=.", "wt=wt"))
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--harness", "opencode",
            "--file", "wt:", "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")

        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        (linked / "src" / "a.py").write_text("allowed = 3\n")
        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] wt:src/a.py", diff)
        self.assertIn("[OUTSIDE SCOPE] root:src/a.py", diff)

    def test_only_explicitly_declared_checkout_roots_are_captured(self) -> None:
        """`.docket` in a plain parent holding several checkouts is a real layout."""
        app = self.repo(self.root / "app")
        lib = self.repo(self.root / "lib")
        self.git("worktree", "add", "-q", "-b", "feature", str(self.root / "app-wt"), cwd=app)
        (self.root / "app-wt" / "secret.txt").write_text("unrelated worktree dirt\n")

        run = self.init(evidence_mode="git", roots=("app=app", "lib=lib"))
        declared = json.loads((run / ".snapshots" / "roots.json").read_text())["roots"]
        self.assertEqual(["app", "lib"], [record["alias"] for record in declared])

        self.cli("assign", "demo", "T01", "--file", "app:src/a.py", "--verify", "true")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        captured = run / ".snapshots" / "T01"
        self.assertEqual(
            ["app.staged.patch", "app.untracked.json", "app.worktree.patch",
             "lib.staged.patch", "lib.untracked.json", "lib.worktree.patch"],
            sorted(path.name for path in captured.iterdir()),
        )
        # An undeclared linked worktree is never copied into secret-bearing evidence.
        for path in captured.iterdir():
            self.assertNotIn("unrelated worktree dirt", path.read_text(errors="replace"))
        (app / "src" / "a.py").write_text("allowed = 2\n")
        (lib / "src" / "a.py").write_text("allowed = 3\n")
        (self.root / "app-wt" / "src" / "a.py").write_text("allowed = 4\n")

        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] app:src/a.py", diff)
        self.assertIn("[OUTSIDE SCOPE] lib:src/a.py", diff)
        self.assertNotIn("app-wt", diff)

    def test_a_git_run_refuses_dispatch_without_a_declared_root(self) -> None:
        self.repo(self.root / "app")
        run = self.init(evidence_mode="git")
        self.assertFalse((run / ".snapshots" / "roots.json").is_file())

        refused = self.cli(
            "assign", "demo", "T01", "--file", "app:src/a.py", "--verify", "true", ok=False
        )
        self.assertIn("cannot capture a complete run baseline", refused.stderr)
        self.assertIn("Nothing was dispatched", refused.stderr)
        self.assertFalse((run / "T01-task.mdx").exists())
        self.assertFalse((run / "T01-report-01.mdx").exists())
        self.assertFalse((run / ".snapshots" / "run.json").is_file())

        listing = self.cli("roots", "demo", ok=False)
        self.assertIn("no checkout root is declared", listing.stderr)
        self.assertIn("Only a declared root is captured", listing.stderr)
        # A hint, not a declaration: the checkout is named but nothing is captured.
        self.assertIn(str(self.root / "app"), listing.stderr)

        self.cli("roots", "demo", "--declare", "app=app")
        self.cli("assign", "demo", "T01", "--file", "app:src/a.py", "--verify", "true")
        self.assertTrue((run / "T01-task.mdx").exists())
        self.assertEqual(
            "recorded", json.loads((run / ".snapshots" / "run.json").read_text())["baseline"]
        )

    def test_dispatch_fails_when_a_declared_root_cannot_be_captured(self) -> None:
        self.repo()
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        run = self.init(evidence_mode="git", roots=("root=.", "wt=wt"))
        self.git("worktree", "remove", "--force", "wt")

        refused = self.cli(
            "assign", "demo", "T01", "--file", "src/a.py", "--verify", "true", ok=False
        )
        self.assertIn("cannot capture a complete run baseline", refused.stderr)
        self.assertIn("is missing", refused.stderr)
        self.assertFalse((run / "T01-task.mdx").exists())
        self.assertFalse((run / ".snapshots" / "run.json").is_file())
        # A half-captured baseline is discarded, never left for a later read to trust.
        self.assertFalse((run / ".snapshots" / "run").exists())

    def test_an_oversize_untracked_file_fails_the_baseline_instead_of_truncating(self) -> None:
        self.repo()
        (self.root / "huge.bin").write_bytes(b"\0" * (8 * 1024 * 1024 + 1))
        run = self.init(evidence_mode="git")

        refused = self.cli(
            "assign", "demo", "T01", "--file", "src/a.py", "--verify", "true", ok=False
        )
        self.assertIn("huge.bin", refused.stderr)
        self.assertIn("baseline capture limit", refused.stderr)
        self.assertIn("captured in full or not at all", refused.stderr)
        self.assertFalse((run / "T01-task.mdx").exists())
        self.assertFalse((run / ".snapshots" / "run.json").is_file())

        (self.root / ".gitignore").write_text("huge.bin\n")
        self.cli("assign", "demo", "T01", "--file", "src/a.py", "--verify", "true")
        record = json.loads((run / ".snapshots" / "run.json").read_text())["roots"][0]
        self.assertEqual("full", record["reconstructable"])

    def test_an_unreadable_untracked_file_fails_the_baseline(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can read a mode 000 file, so there is nothing to fail on")
        self.repo()
        secret = self.root / "unreadable.txt"
        secret.write_text("cannot be captured\n")
        secret.chmod(0o000)
        run = self.init(evidence_mode="git")

        refused = self.cli(
            "assign", "demo", "T01", "--file", "src/a.py", "--verify", "true", ok=False
        )
        self.assertIn("unreadable.txt cannot be read", refused.stderr)
        self.assertIn("Nothing was dispatched", refused.stderr)
        self.assertFalse((run / "T01-task.mdx").exists())
        self.assertFalse((run / ".snapshots" / "run.json").is_file())

    def test_a_corrupt_root_declaration_is_refused_rather_than_rediscovered(self) -> None:
        self.repo()
        run = self.init(evidence_mode="git")
        declaration = run / ".snapshots" / "roots.json"
        declaration.write_text("{ not json")

        listing = self.cli("roots", "demo", ok=False)
        self.assertIn("is unreadable", listing.stderr)
        self.assertIn("never rediscovered", listing.stderr)

        refused = self.cli(
            "assign", "demo", "T01", "--file", "src/a.py", "--verify", "true", ok=False
        )
        self.assertIn("is unreadable", refused.stderr)
        self.assertFalse((run / "T01-task.mdx").exists())

        blocked = self.cli("roots", "demo", "--declare", "root=.", ok=False)
        self.assertIn("never rediscovered", blocked.stderr)

        # An entry missing a field is invalid too, not "nothing declared yet".
        declaration.write_text(json.dumps({"roots": [{"alias": "root", "path": "/tmp"}]}))
        incomplete = self.cli("roots", "demo", ok=False)
        self.assertIn("malformed entry", incomplete.stderr)

    def test_a_commit_between_declaration_and_assignment_is_captured_coherently(self) -> None:
        self.repo()
        run = self.init(evidence_mode="git")
        declared_head = self.git("rev-parse", "HEAD").strip()

        (self.root / "src" / "b.py").write_text("b = 1\n")
        self.git("add", "src/b.py")
        self.git("commit", "-qm", "committed after the roots were declared")
        head_now = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(declared_head, head_now)

        self.assign()
        record = json.loads((run / ".snapshots" / "T01.json").read_text())["roots"][0]
        # Branch, HEAD, and the pinned base tree all describe the instant of capture.
        self.assertEqual(head_now, record["head"])
        self.assertEqual(head_now, record["base_tree"])
        self.assertEqual("main", record["branch"])
        self.assertEqual(declared_head, record["declared_head"])
        # The commit predates the task, so it is not attributed to it.
        self.assertIn("no worktree changes", self.cli("diff", "demo", "T01").stdout)

    def test_a_mode_change_on_initially_dirty_content_is_detected(self) -> None:
        self.repo()
        tracked = self.root / "src" / "a.py"
        tracked.write_text("allowed = 2\n")
        untracked = self.root / "notes.sh"
        untracked.write_text("#!/bin/sh\necho hi\n")

        run = self.init(evidence_mode="git")
        self.cli(
            "assign", "demo", "T01", "--file", "src/a.py", "--file", "notes.sh",
            "--verify", "true",
        )
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.assertIn("no worktree changes", self.cli("diff", "demo", "T01").stdout)

        # Only the executable bit moves. Both files are byte-for-byte what they were.
        tracked.chmod(0o755)
        untracked.chmod(0o755)
        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] src/a.py", diff)
        self.assertIn("[in scope] notes.sh", diff)

    def test_staging_work_that_was_already_dirty_is_detected_as_a_change(self) -> None:
        self.repo()
        tracked = self.root / "src" / "a.py"
        tracked.write_text("allowed = 2\n")

        self.init(evidence_mode="git")
        self.assign()
        self.assertIn("no worktree changes", self.cli("diff", "demo", "T01").stdout)

        # The index moves; the worktree content does not.
        self.git("add", "src/a.py")
        diff = self.cli("diff", "demo", "T01").stdout
        self.assertEqual("allowed = 2\n", tracked.read_text())
        self.assertIn("[in scope] src/a.py", diff)

    # ------------------------------------------------- frozen evidence bundles

    ALL_SHAPES = (
        "- `src/a.py` `src/blob.bin` `src/gone.py` `src/link` `src/mode.py` `src/new.bin` "
        "`src/note.txt` `src/renamed.py` `src/staged.py` `src/tool.sh` `src/unstaged.py` - "
        "reshaped the module."
    )

    def ledger(self, owner: str = "T01") -> list[dict]:
        path = self.root / ".docket" / "runs" / "demo" / ".bundles" / owner / "rounds.json"
        return json.loads(path.read_text())["entries"] if path.is_file() else []

    def frozen(self, owner: str = "T01", index: int = -1) -> tuple[dict, Path]:
        """One frozen bundle manifest and the directory it was published into."""
        entry = self.ledger(owner)[index]
        where = self.root / ".docket" / "runs" / "demo" / ".bundles" / owner / entry["dir"]
        return json.loads((where / "bundle.json").read_text()), where

    def frozen_patch(self, owner: str = "T01", index: int = -1) -> str:
        manifest, where = self.frozen(owner, index)
        record = manifest["patch"]["roots"][0]
        return (where / record["file"]).read_text(errors="replace")

    def dirty_task_over_every_shape(self) -> Path:
        """A task dispatched over real dirt, then every change shape a patch must carry.

        The dirt is deliberately not the implementor's work: it is staged, unstaged,
        untracked, symlinked, and mode-changed content that already existed when the
        task was assigned, so a patch taken against the last commit rather than
        against the baseline shows up immediately.
        """
        self.repo()
        (self.root / "src" / "blob.bin").write_bytes(bytes(range(256)))
        for name in ("gone.py", "mode.py", "unstaged.py"):
            (self.root / "src" / name).write_text(f"{name.split('.')[0]} = 1\n")
        self.git("add", "src")
        self.git("commit", "-qm", "more tracked content")

        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.git("add", "src/a.py")
        (self.root / "src" / "a.py").write_text("allowed = 3\n")
        (self.root / "src" / "note.txt").write_text("dirty note\n")
        (self.root / "src" / "link").symlink_to("a.py")
        (self.root / "src" / "blob.bin").chmod(0o755)

        run = self.init(evidence_mode="git")
        self.assign(file="src")
        self.fill_task(run / "T01-task.mdx")

        self.git("mv", "src/a.py", "src/renamed.py")
        (self.root / "src" / "renamed.py").write_text("allowed = 3\nguard = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "committed during the task")
        (self.root / "src" / "gone.py").unlink()
        (self.root / "src" / "note.txt").unlink()
        (self.root / "src" / "new.bin").write_bytes(bytes(range(256)) * 4)
        tool = self.root / "src" / "tool.sh"
        tool.write_text("#!/bin/sh\necho hi\n")
        tool.chmod(0o755)
        (self.root / "src" / "mode.py").chmod(0o755)
        (self.root / "src" / "staged.py").write_text("staged = 1\n")
        self.git("add", "src/staged.py")
        (self.root / "src" / "unstaged.py").write_text("unstaged = 2\n")
        (self.root / "src" / "link").unlink()
        (self.root / "src" / "link").symlink_to("renamed.py")
        return run

    def test_a_submission_freezes_the_whole_task_round_as_one_bundle(self) -> None:
        """A verdict has to bind to something, so submission freezes what it binds to."""
        run = self.dirty_task_over_every_shape()
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, files=self.ALL_SHAPES)
        body = self.body_of(report)

        submitted = self.cli("submit", "demo", "T01")

        self.assertIn("frozen task-round bundle sha256:", submitted.stdout)
        manifest, where = self.frozen()
        self.assertEqual("task-round", manifest["kind"])
        self.assertEqual(1, manifest["round"])
        self.assertEqual(manifest["digest"], parse_meta(report)["bundle_digest"])
        # The contract revision under review, frozen with its own bytes.
        task_bytes = (run / "T01-task.mdx").read_bytes()
        self.assertEqual("T01-task.mdx", manifest["contract"]["source"])
        self.assertEqual(sha(task_bytes), manifest["contract"]["revision"])
        self.assertEqual(task_bytes, (where / manifest["contract"]["file"]).read_bytes())
        # The immutable task baseline it was dispatched over.
        snapshot = (run / ".snapshots" / "T01.json").read_bytes()
        self.assertEqual("T01.json", manifest["baseline"]["snapshot"])
        self.assertEqual(sha(snapshot), manifest["baseline"]["revision"])
        # The report body as submitted.
        self.assertEqual(sha(body.encode()), manifest["report"]["body_digest"])
        self.assertEqual(body, (where / manifest["report"]["file"]).read_text())
        # The verification as captured, not as claimed.
        verification = manifest["verification"]
        self.assertEqual("passed", verification["status"])
        self.assertEqual(0, verification["returncode"])
        self.assertEqual('printf "T01 ok\\n"', verification["command"])
        self.assertEqual("T01 ok\n", (where / verification["stdout"]["file"]).read_text())
        # The resulting source revision, pinned per declared root.
        source = manifest["source"]["roots"][0]
        self.assertEqual("root", source["alias"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), source["head"])
        self.assertEqual(40, len(source["tree"]))
        # And a root-qualified change list, never a bare one.
        self.assertIn("root:src/renamed.py", manifest["changed_paths"])

    def test_a_frozen_patch_carries_every_change_shape_against_the_task_baseline(self) -> None:
        """The patch is baseline-relative, so the dirt the task inherited is not in it.

        A patch taken against HEAD instead would show `allowed = 1` being replaced,
        which is somebody else's uncommitted work, and would lose the untracked file
        the task deleted entirely.
        """
        run = self.dirty_task_over_every_shape()
        self.fill_task_report(run / "T01-report-01.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")

        patch = self.frozen_patch()
        self.assertNotIn("allowed = 1", patch)
        self.assertIn("rename from src/a.py", patch)
        self.assertIn("rename to src/renamed.py", patch)
        self.assertIn("+guard = 1", patch)
        self.assertIn("+unstaged = 2", patch)
        self.assertIn("+staged = 1", patch)
        self.assertIn("deleted file mode 100644", patch)
        self.assertIn("src/gone.py", patch)
        self.assertIn("src/note.txt", patch)
        self.assertIn("GIT binary patch", patch)
        self.assertIn("new file mode 100755", patch)
        self.assertIn("old mode 100644", patch)
        self.assertIn("new mode 100755", patch)
        self.assertIn("120000", patch)
        self.assertIn("+renamed.py", patch)

        # The whole patch is reconstructable: applying it to the rebuilt baseline
        # yields exactly the source revision the bundle pinned.
        manifest, _ = self.frozen()
        record = manifest["patch"]["roots"][0]
        objects = run / ".bundles" / "objects"
        env = dict(os.environ)
        env.update({
            "GIT_DIR": str(self.root / ".git"),
            "GIT_OBJECT_DIRECTORY": str(objects),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.root / ".git" / "objects"),
        })
        listed = subprocess.run(
            ["git", "ls-tree", "-r", record["source_tree"]],
            cwd=self.root, env=env, text=True, capture_output=True,
        )
        self.assertEqual(0, listed.returncode, listed.stderr)
        self.assertIn("src/renamed.py", listed.stdout)
        self.assertIn("120000", listed.stdout)
        self.assertNotIn("src/note.txt", listed.stdout)

    def test_a_project_that_gitignores_docket_still_freezes_its_rounds(self) -> None:
        """Regression: excluding an already-ignored path made `git add` refuse outright.

        Ignoring `.docket` is the normal thing for a project to do, and Docket's own
        repository does it. Building the source tree with an exclude pathspec naming an
        ignored path fails the whole invocation, which surfaced as unavailable patch
        coverage on exactly the projects most likely to be using Docket.
        """
        self.repo()
        (self.root / ".gitignore").write_text(".docket/\n__pycache__/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore docket state")
        run = self.init(evidence_mode="git")
        self.assign(file="src/a.py")
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")

        submitted = self.cli("submit", "demo", "T01")

        self.assertIn("frozen task-round bundle sha256:", submitted.stdout)
        self.assertNotIn("patch coverage unavailable", submitted.stdout)
        manifest, _ = self.frozen()
        self.assertEqual("available", manifest["patch"]["coverage"])
        self.assertEqual(["root:src/a.py"], manifest["changed_paths"])
        self.assertIn("+allowed = 2", self.frozen_patch())
        # The run's own state never becomes part of the reviewed change surface.
        self.assertNotIn(".docket", self.frozen_patch())

    def test_a_correction_round_keeps_the_full_patch_and_a_reproducible_delta(self) -> None:
        """Round two answers both questions: what changed overall, and what changed since."""
        run = self.dirty_task_over_every_shape()
        self.fill_task_report(run / "T01-report-01.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")
        first, _ = self.frozen()
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        self.cli("decide", "demo", "T01", "--changes", "--reason", self.REASON)

        (self.root / "src" / "renamed.py").write_text("allowed = 3\nguard = 2\n")
        self.fill_task_report(run / "T01-report-02.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")

        second, where = self.frozen()
        self.assertEqual(2, second["round"])
        # The complete task-baseline-relative patch is still there.
        full = self.frozen_patch()
        self.assertIn("+guard = 2", full)
        self.assertIn("GIT binary patch", full)
        self.assertIn("src/note.txt", full)
        # And so is a delta that reproduces only the correction.
        self.assertEqual(first["digest"], second["delta"]["from"])
        self.assertEqual(1, second["delta"]["from_round"])
        record = second["delta"]["roots"][0]
        delta = (where / record["file"]).read_text()
        self.assertIn("-guard = 1", delta)
        self.assertIn("+guard = 2", delta)
        self.assertNotIn("GIT binary patch", delta)
        self.assertNotIn("src/note.txt", delta)
        # The delta is reproducible from the two pinned trees, not only from the file.
        self.assertEqual(first["source"]["roots"][0]["tree"], record["from_tree"])
        self.assertEqual(second["source"]["roots"][0]["tree"], record["to_tree"])

    def test_later_edits_retries_and_rounds_never_rewrite_an_earlier_bundle(self) -> None:
        """Regression: review evidence used to be whatever the workspace held later."""
        run = self.dirty_task_over_every_shape()
        self.fill_task_report(run / "T01-report-01.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")
        first, where = self.frozen()
        preserved = {path.name: path.read_bytes() for path in where.iterdir() if path.is_file()}

        # A later workspace edit, a later report edit, a retry, and a whole later round.
        (self.root / "src" / "renamed.py").write_text("allowed = 3\nrewritten = 1\n")
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        self.cli("decide", "demo", "T01", "--changes", "--reason", self.REASON)
        self.cli("decide", "demo", "T01", "--changes")
        self.fill_task_report(run / "T01-report-02.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")

        self.assertEqual(
            preserved, {path.name: path.read_bytes() for path in where.iterdir() if path.is_file()}
        )
        again, _ = self.frozen("T01", index=0)
        self.assertEqual(first, again)
        self.assertEqual([1, 2], [entry["round"] for entry in self.ledger()])
        self.assertNotEqual(self.ledger()[0]["digest"], self.ledger()[1]["digest"])
        listing = self.cli("bundle", "demo", "T01", "--list").stdout
        self.assertEqual(2, listing.count("intact"))

    def test_a_verdict_refuses_a_bundle_that_is_stale_for_the_report_body(self) -> None:
        """Regression: a first review used to bind to nothing, so an edit rode along.

        Nothing had recorded an evidence digest yet when the first verdict was formed,
        so a body edited between submission and approval inherited the approval of a
        body nobody read.
        """
        run = self.dirty_task_over_every_shape()
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")
        frozen_digest = self.frozen()[0]["digest"]

        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Implemented the feature, verified it, and quietly widened the change.",
        ))
        stale = self.cli("decide", "demo", "T01", "--approve", ok=False)

        self.assertEqual(1, stale.returncode)
        self.assertIn("changed after review began", stale.stderr)
        self.assertIn(frozen_digest, stale.stderr)
        self.assertIn("--re-review", stale.stderr)
        self.assertIn("status: submitted", report.read_text())
        self.assertFalse((run / "T01-decision-01.mdx").exists())

        applied = self.cli("decide", "demo", "T01", "--approve", "--re-review")

        self.assertIn("froze the re-reviewed evidence as sha256:", applied.stdout)
        settled = (run / "T01-decision-01.mdx").read_text()
        rebound = self.frozen()[0]["digest"]
        self.assertNotEqual(frozen_digest, rebound)
        self.assertIn(f"bundle_digest: {rebound}", settled)
        self.assertIn(f"superseded_bundle: {frozen_digest}", settled)
        self.assertEqual("re-review", self.ledger()[-1]["trigger"])

    def test_a_verdict_refuses_missing_or_damaged_bundle_evidence(self) -> None:
        """Stale or absent evidence blocks approval; it never approves by default."""
        run = self.dirty_task_over_every_shape()
        self.fill_task_report(run / "T01-report-01.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")
        _, where = self.frozen()

        patch = where / "root.patch"
        kept = patch.read_bytes()
        patch.write_bytes(b"not the patch that was reviewed\n")
        damaged = self.cli("decide", "demo", "T01", "--approve", ok=False)
        self.assertEqual(1, damaged.returncode)
        self.assertIn("damaged frozen evidence", damaged.stderr)
        self.assertIn("no longer matches its recorded artifact root.patch", damaged.stderr)
        patch.write_bytes(kept)

        (where / "bundle.json").unlink()
        missing = self.cli("decide", "demo", "T01", "--approve", ok=False)
        self.assertEqual(1, missing.returncode)
        self.assertIn("is missing or unreadable", missing.stderr)
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())

    def test_an_interrupted_freeze_publishes_no_partial_bundle(self) -> None:
        """A bundle directory arrives whole or not at all, and hands nothing off."""
        run = self.dirty_task_over_every_shape()
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, files=self.ALL_SHAPES)

        crash = self.cli("submit", "demo", "T01", ok=False, fault="bundle:publish")

        self.assertEqual(70, crash.returncode)
        self.assertIn("status: draft", report.read_text())
        self.assertEqual([], self.ledger())
        self.assertFalse(any((run / ".bundles" / "T01").glob("*/*/bundle.json")))
        absent = self.cli("bundle", "demo", "T01", ok=False)
        self.assertEqual(1, absent.returncode)
        self.assertIn("no frozen evidence bundle", absent.stderr)

        self.cli("submit", "demo", "T01")

        self.assertEqual(1, len(self.ledger()))
        self.assertIn("intact", self.cli("bundle", "demo", "T01", "--list").stdout)
        self.assertIn("status: submitted", report.read_text())

    def test_a_green_result_is_never_frozen_against_a_checkout_it_did_not_describe(self) -> None:
        """Regression: a verify run that edits the tree used to inherit its own result."""
        self.repo()
        run = self.init(evidence_mode="git")
        self.cli(
            "assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
            "--file", "src", "--verify", "printf 'allowed = 9\\n' > src/a.py",
        )
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        (self.root / "src" / "a.py").write_text("allowed = 2\n")

        rejected = self.cli("submit", "demo", "T01", ok=False)

        self.assertEqual(1, rejected.returncode)
        self.assertIn("could not be frozen as review evidence", rejected.stderr)
        self.assertIn("changed while the verification ran", rejected.stderr)
        self.assertIn("being frozen: src/a.py.", rejected.stderr)
        self.assertIn("status: draft", report.read_text())
        self.assertEqual([], self.ledger())

    def test_an_aggregate_is_never_frozen_against_a_checkout_its_verify_moved(self) -> None:
        """Regression: the aggregate pinned its source against a baseline that never exists.

        Submit read `.snapshots/orch.json` for the pre-verify source, found nothing, and
        skipped the comparison, so an integration verify that edited the tree froze green.
        """
        self.repo()
        run = self.init(evidence_mode="git")
        self.cli(
            "assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
            "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.cli("submit", "demo", "T01")
        self.cli(
            "assign", "demo", "orch", "--complexity", "high", "--executor", "orchestrator",
            "--harness", "claude", "--file", "src/orch.py",
            "--verify", "printf 'allowed = 9\\n' > src/a.py",
        )
        orch = run / "orch-report-01.mdx"
        self.fill_orch_report(orch)

        rejected = self.cli("submit", "demo", "orch", ok=False)

        self.assertEqual(1, rejected.returncode)
        self.assertIn("changed while the verification ran", rejected.stderr)
        self.assertIn("being frozen: src/a.py.", rejected.stderr)
        self.assertIn("status: draft", orch.read_text())
        self.assertFalse((run / ".bundles" / "orch" / "rounds.json").exists())

    def test_a_task_baseline_is_taken_at_dispatch_so_earlier_work_is_its_start(self) -> None:
        """Regression: a baseline taken at assign counted an earlier task's work as its own.

        The playbook assigns a whole batch before starting any implementor, so T02's
        assign-time baseline predated T01. Once T01 was approved, its uncommitted
        `src/a.py` refused T02's submit as T02's own out-of-scope change.
        """
        self.repo()
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "demo"
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace(
            "correction_limit: 2\n", "correction_limit: 2\nmax_concurrency: 1\n", 1))
        for owner, path in (("T01", "src/a.py"), ("T02", "src/b.py")):
            self.cli("assign", "demo", owner, "--complexity", "high", "--executor", "implementor",
                     "--harness", "opencode", "--file", path, "--verify", f'printf "{owner} ok\\n"')
            self.fill_task(run / f"{owner}-task.mdx")
        self.assertFalse((run / ".snapshots" / "T02.json").exists())

        self.cli("dispatch", "demo", "T01", "--session", "w1", "--register")
        full = self.cli("dispatch", "demo", "T02", "--session", "w2", "--register", ok=False)
        self.assertIn("concurrency 1/1 exhausted", full.stderr)
        self.assertFalse((run / ".snapshots" / "T02.json").exists(),
                         "a refused dispatch must not fix a baseline under unfinished work")

        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "checker",
                 "--detail", "1. ok")
        self.cli("decide", "demo", "T01", "--approve", "--as", "checker")

        self.cli("dispatch", "demo", "T02", "--session", "w2", "--register")
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        (self.root / "src" / "b.py").write_text("value = 2\n")
        self.fill_task_report(run / "T02-report-01.mdx", files="- `src/b.py:1` - implemented T02.")
        submitted = self.cli("submit", "demo", "T02", "--as", "implementor")
        self.assertIn("patch: 1 path(s) against the task baseline", submitted.stdout)
        self.assertIn("[in scope] src/b.py", self.cli("diff", "demo", "T02").stdout)
        self.assertNotIn("src/a.py", self.cli("diff", "demo", "T02").stdout)

    def dirty_orchestrator_task(self, *config: tuple[str, str]) -> Path:
        """A git run whose T01 baseline sits over staged and unstaged dirt elsewhere."""
        self.repo()
        for key, value in config:
            self.git("config", key, value)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "c.txt").write_text("one\n")
        self.git("add", "docs/c.txt")
        self.git("commit", "-qm", "docs")
        (self.root / "src" / "b.py").write_text("staged = 1\n")
        self.git("add", "src/b.py")
        (self.root / "docs" / "c.txt").write_text("two\n")
        run = self.init(evidence_mode="git")
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        return run

    def test_user_diff_settings_never_change_the_evidence(self) -> None:
        """Regression: `diff.noprefix` made baseline patches apply one directory too shallow."""
        run = self.dirty_orchestrator_task(("diff.noprefix", "true"),
                                           ("diff.mnemonicPrefix", "true"))
        submitted = self.cli("submit", "demo", "T01")
        self.assertIn("patch: 1 path(s) against the task baseline", submitted.stdout)
        entry = json.loads((run / ".bundles" / "T01" / "rounds.json").read_text())["entries"][-1]
        manifest = json.loads((run / ".bundles" / "T01" / entry["dir"] / "bundle.json").read_text())
        self.assertEqual(["root:src/a.py"], manifest["changed_paths"])

    def test_hidden_local_edits_are_never_frozen_as_the_implementors_work(self) -> None:
        """Regression: skip-worktree and assume-unchanged edits entered the frozen patch.

        Baseline capture reads Git's view, which hides those paths, while the source
        tree was rebuilt from HEAD without the flags, so a local secret was frozen.
        """
        for flag in ("--skip-worktree", "--assume-unchanged"):
            with self.subTest(flag=flag):
                self.tearDown()
                self.setUp()
                self.repo()
                (self.root / "src" / "cfg.py").write_text("token = 'placeholder'\n")
                self.git("add", "src/cfg.py")
                self.git("commit", "-qm", "config")
                self.git("update-index", flag, "src/cfg.py")
                (self.root / "src" / "cfg.py").write_text("token = 'LOCAL_SECRET'\n")
                run = self.init(evidence_mode="git")
                self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness",
                         "claude", "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
                self.fill_task(run / "T01-task.mdx")
                self.fill_task_report(run / "T01-report-01.mdx")
                (self.root / "src" / "a.py").write_text("allowed = 2\n")
                self.cli("submit", "demo", "T01")
                bundles = run / ".bundles" / "T01"
                entry = json.loads((bundles / "rounds.json").read_text())["entries"][-1]
                manifest = json.loads((bundles / entry["dir"] / "bundle.json").read_text())
                self.assertEqual(["root:src/a.py"], manifest["changed_paths"])
                for path in (bundles / entry["dir"]).rglob("*.patch"):
                    self.assertNotIn(b"LOCAL_SECRET", path.read_bytes())

    def test_git_state_inherited_from_the_caller_never_redirects_evidence(self) -> None:
        """Regression: a leaked GIT_INDEX_FILE, as inside a git hook, read the wrong index."""
        run = self.dirty_orchestrator_task()
        clean = self.cli("diff", "demo", "T01").stdout
        leaked = self.cli_env({"GIT_INDEX_FILE": str(self.root / "elsewhere.index")},
                              "diff", "demo", "T01").stdout
        self.assertEqual(clean, leaked)
        self.assertIn("[in scope] src/a.py", leaked)

    def test_a_filename_that_is_not_utf8_never_crashes_evidence(self) -> None:
        """Regression: an undecodable untracked name raised UnicodeDecodeError in assign."""
        self.repo()
        Path(os.fsdecode(bytes(self.root) + b"/src/caf\xe9.txt")).write_text("x\n")
        run = self.init(evidence_mode="git")
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.assertEqual("recorded",
                         json.loads((run / ".snapshots" / "T01.json").read_text())["baseline"])
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        diff = self.cli("diff", "demo", "T01")
        self.assertIn("[in scope] src/a.py", diff.stdout)

    def submit_with_verify(self, verify: str, timeout: int = 900) -> tuple[Path, subprocess.CompletedProcess[str]]:
        """Submit an orchestrator-executed task whose gate runs `verify`."""
        self.init()
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", verify, "--verify-timeout", str(timeout))
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        return run, self.cli("submit", "demo", "T01", ok=False)

    def frozen_verification(self, run: Path) -> tuple[dict, Path]:
        bundles = run / ".bundles" / "T01"
        entry = json.loads((bundles / "rounds.json").read_text())["entries"][-1]
        manifest = json.loads((bundles / entry["dir"] / "bundle.json").read_text())
        return manifest["verification"], bundles / entry["dir"]

    def test_verify_output_that_is_not_utf8_is_frozen_byte_for_byte(self) -> None:
        """Regression: an undecodable byte in verify output crashed submit with a traceback."""
        run, submitted = self.submit_with_verify("printf 'ok \\377\\n'")
        self.assertEqual(0, submitted.returncode, submitted.stderr)
        _, bundle = self.frozen_verification(run)
        self.assertEqual(b"ok \xff\n", (bundle / "verify.stdout").read_bytes())

    def test_a_verify_timeout_ends_the_whole_command_not_just_its_shell(self) -> None:
        """Regression: a timeout killed /bin/sh and left its test processes running."""
        pid_file = Path(self._feedback_tmp.name) / "child.pid"
        _, submitted = self.submit_with_verify(f"sleep 37 & echo $! > {pid_file}; wait", timeout=1)
        self.assertIn("timed out after 1s", submitted.stderr)
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        alive = True
        while alive and time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
                with open(f"/proc/{pid}/stat") as fh:
                    alive = fh.read().split()[2] != "Z"
            except (ProcessLookupError, FileNotFoundError):
                alive = False
            if alive:
                time.sleep(0.1)
        self.assertFalse(alive, "the verify command's child outlived its timeout")

    def test_a_background_helper_never_turns_a_pass_into_a_timeout(self) -> None:
        """Regression: a helper holding the output pipe made a passing run wait out its timeout."""
        run, submitted = self.submit_with_verify("sleep 30 & echo ready", timeout=10)
        self.assertEqual(0, submitted.returncode, submitted.stderr)
        verification, _ = self.frozen_verification(run)
        self.assertEqual("passed", verification["status"])
        self.assertLess(verification["duration_seconds"], 5)

    def test_pytest_counts_are_read_in_any_order(self) -> None:
        """Regression: `3 failed, 12 passed` was recorded as zero failures."""
        run, submitted = self.submit_with_verify(
            "printf '== 3 failed, 12 passed, 1 skipped, 2 errors in 0.52s ==\\n'; true")
        self.assertEqual(0, submitted.returncode, submitted.stderr)
        verification, _ = self.frozen_verification(run)
        self.assertEqual({"parser": "pytest", "passed": 12, "failed": 3, "skipped": 1,
                          "errors": 2}, verification["framework"])

    def test_a_hash_inside_a_word_is_part_of_the_verify_command(self) -> None:
        """Regression: any `#` started a comment, so `echo a#b` ran as `echo a`."""
        run, submitted = self.submit_with_verify("echo a#b; echo kept # a shell comment")
        self.assertEqual(0, submitted.returncode, submitted.stderr)
        _, bundle = self.frozen_verification(run)
        self.assertEqual(b"a#b\nkept\n", (bundle / "verify.stdout").read_bytes())

    def test_a_frontmatter_value_can_never_add_a_key(self) -> None:
        """Regression: a newline in `--verify` wrote a second frontmatter line."""
        self.init()
        refused = self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness",
                           "claude", "--file", "src/a.py", "--verify", "true\nstatus: approved",
                           ok=False)
        self.assertIn("cannot contain a line break", refused.stderr)
        self.assertFalse((self.root / ".docket" / "runs" / "demo" / "T01-task.mdx").exists())

    def test_a_byte_order_mark_never_hides_a_report_status(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        report.write_text("﻿" + report.read_text())
        self.cli("submit", "demo", "T01")
        self.assertIn("status: submitted", report.read_text())

    def test_a_heading_inside_a_code_fence_is_not_a_section(self) -> None:
        """Regression: pasted output with `## Acceptance` in a fence replaced the real one."""
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        report.write_text(report.read_text().replace(
            "## Decisions needed",
            "Pasted output:\n\n```\n## Acceptance\n- [ ] fake\n```\n\n## Decisions needed"))
        self.cli("submit", "demo", "T01")

    def test_a_duplicated_report_section_is_refused(self) -> None:
        """Regression: the gate read the last copy of a heading while seeding edited the first."""
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        report.write_text(report.read_text() + "\n## Acceptance\n\n- [x] Something else.\n")
        refused = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("duplicate section '## Acceptance'", refused.stderr)

    def test_placeholder_words_in_criteria_and_quoted_output_are_not_placeholders(self) -> None:
        """Regression: a criterion naming FIXME made its task undispatchable."""
        self.init()
        run = self.root / ".docket" / "runs" / "demo"
        criterion = "No FIXME markers remain in src/."
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"',
                 "--goal", "Clean up markers.", "--criterion", criterion)
        self.assertIn("task intent is ready", self.cli("validate-task", "demo", "T01").stdout)
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, criterion=criterion)
        text = report.read_text()
        report.write_text(text.replace("## Decisions needed",
                                       "```\nlint: TODO: rename XXX\n```\n\n## Decisions needed"))
        self.cli("submit", "demo", "T01")

    def test_an_authored_placeholder_is_still_refused_at_its_file_line(self) -> None:
        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.", "TODO: finish this."))
        refused = self.cli("submit", "demo", "T01", ok=False)
        line = next(n for n, text in enumerate(report.read_text().splitlines(), 1)
                    if "TODO: finish" in text)
        self.assertIn(f"unresolved placeholder at line {line}: TODO: finish this.", refused.stderr)

    def test_comma_separated_scope_paths_claim_the_paths_not_the_commas(self) -> None:
        """Regression: `files: a.py, b.py` claimed `a.py,`."""
        self.init()
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        capsule = run / "T01-scope.mdx"
        self.fill_scope(capsule)
        capsule.write_text(capsule.read_text().replace("files: src/a.py",
                                                       "files: src/a.py, tests/test_a.py"))
        self.cli("scope", "demo", "T01", "--submit")
        self.assertEqual("src/a.py tests/test_a.py", parse_meta(run / "T01-task.mdx")["files"])

    def test_a_docket_in_a_package_directory_never_measures_its_own_state(self) -> None:
        """Regression: `.docket` below the checkout top listed its own files as out of scope."""
        self.repo()
        package = self.root / "pkg"
        (package / "src").mkdir(parents=True)
        (package / "src" / "a.py").write_text("allowed = 1\n")
        self.git("add", "pkg")
        self.git("commit", "-qm", "package")

        def docket(*args: str) -> subprocess.CompletedProcess[str]:
            result = subprocess.run([sys.executable, str(DOCKET), *args], cwd=package, text=True,
                                    capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            return result

        docket("init", "r", "--evidence-mode", "git")
        docket("assign", "r", "T01", "--executor", "orchestrator", "--harness", "claude",
               "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        (package / "src" / "a.py").write_text("allowed = 2\n")
        diff = docket("diff", "r", "T01").stdout
        self.assertIn("[in scope] pkg/src/a.py", diff)
        self.assertNotIn(".docket", diff)

    def test_init_with_declared_roots_in_a_plain_parent_uses_git_evidence(self) -> None:
        """Regression: `--root` in a non-Git parent silently fell back to documents-only."""
        self.repo(self.root / "api")
        self.repo(self.root / "web")
        created = self.cli("init", "demo", "--root", "api=./api", "--root", "web=./web")
        self.assertIn("evidence_mode: git", created.stdout)
        self.assertIn("declared 2 checkout root(s)", created.stdout)

    def test_a_frozen_bundle_survives_the_user_pruning_their_repository(self) -> None:
        """Regression: a staged blob lived only in the user's store, and `git gc` damaged the bundle.

        Git does not copy an object that already exists through alternates, so staged
        content frozen into a bundle was pinned but never kept by the private store.
        """
        self.repo()
        run = self.init(evidence_mode="git")
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 'staged and frozen'\n")
        self.git("add", "src/a.py")
        self.cli("submit", "demo", "T01")
        (self.root / "src" / "a.py").write_text("allowed = 'restaged later'\n")
        self.git("add", "src/a.py")
        self.git("-c", "gc.reflogExpire=now", "-c", "gc.reflogExpireUnreachable=now",
                 "gc", "-q", "--prune=now")
        checked = self.cli("bundle", "demo", "T01", "--list")
        self.assertIn("intact", checked.stdout)
        self.assertNotIn("DAMAGED", checked.stdout)

    def test_a_bundle_whose_objects_cannot_be_checked_is_never_intact(self) -> None:
        """Regression: a failed object check produced no output and read as nothing missing."""
        self.repo()
        run = self.init(evidence_mode="git")
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.cli("submit", "demo", "T01")
        shim = Path(self._feedback_tmp.name) / "shim"
        shim.mkdir()
        real = shutil.which("git")
        (shim / "git").write_text(
            "#!/bin/sh\n"
            'case " $* " in *" --batch-check "*) exit 128 ;; esac\n'
            f'exec {shlex.quote(str(real))} "$@"\n')
        (shim / "git").chmod(0o755)
        shimmed = {"PATH": f"{shim}:{os.environ['PATH']}"}
        listed = self.cli_env(shimmed, "bundle", "demo", "T01", "--list", ok=False)
        self.assertIn("DAMAGED", listed.stdout + listed.stderr)
        detail = self.cli_env(shimmed, "bundle", "demo", "T01", ok=False)
        self.assertIn("could not check", detail.stdout + detail.stderr)

    def test_a_freeze_that_fails_its_own_check_leaves_no_ledger_entry(self) -> None:
        """Regression: the ledger named a bundle before the bundle was validated."""
        self.repo()
        run = self.init(evidence_mode="git")
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        shim = Path(self._feedback_tmp.name) / "shim"
        shim.mkdir()
        (shim / "git").write_text(
            "#!/bin/sh\n"
            'case " $* " in *" ls-tree "*) exit 128 ;; esac\n'
            f'exec {shlex.quote(str(shutil.which("git")))} "$@"\n')
        (shim / "git").chmod(0o755)
        refused = self.cli_env({"PATH": f"{shim}:{os.environ['PATH']}"},
                               "submit", "demo", "T01", ok=False)
        self.assertNotEqual(0, refused.returncode)
        ledger = run / ".bundles" / "T01" / "rounds.json"
        entries = json.loads(ledger.read_text())["entries"] if ledger.is_file() else []
        self.assertEqual([], entries)
        self.assertIn("status: draft", (run / "T01-report-01.mdx").read_text())

    def test_captured_workspace_content_is_readable_by_its_owner_alone(self) -> None:
        """Regression: baseline patches and bundles, which can hold secrets, were 0775/0664."""
        self.repo()
        (self.root / "notes.txt").write_text("API_KEY=secret\n")
        run = self.init(evidence_mode="git")
        self.cli("assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
                 "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.cli("submit", "demo", "T01")
        for name in (".snapshots", ".bundles"):
            mode = (run / name).stat().st_mode & 0o777
            self.assertEqual(0o700, mode, f"{name} is {oct(mode)}")

    def test_a_python_verify_command_writes_no_bytecode_into_the_checkout(self) -> None:
        """Importing the code under test must not move the checkout the result describes."""
        self.repo()
        run = self.init(evidence_mode="git")
        self.cli(
            "assign", "demo", "T01", "--executor", "orchestrator", "--harness", "claude",
            "--file", "src",
            "--verify", "python3 -c 'import sys; sys.path.insert(0, \"src\"); import a'",
        )
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        ambient = os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        try:
            self.cli("submit", "demo", "T01")
        finally:
            if ambient is not None:
                os.environ["PYTHONDONTWRITEBYTECODE"] = ambient
        self.assertFalse((self.root / "src" / "__pycache__").exists())
        self.assertIn("status: completed", report.read_text())

    def test_documents_only_bundles_record_unavailable_patch_coverage(self) -> None:
        """A run without Git evidence still freezes a round, and still says so plainly."""
        run = self.init(evidence_mode="documents-only")
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")

        submitted = self.cli("submit", "demo", "T01")

        self.assertIn("patch coverage unavailable", submitted.stdout)
        self.assertIn("This is not an empty patch.", submitted.stdout)
        manifest, where = self.frozen()
        self.assertEqual("documents-only", manifest["evidence_mode"])
        self.assertEqual("unavailable", manifest["patch"]["coverage"])
        self.assertEqual("unavailable", manifest["source"]["coverage"])
        self.assertIn("documents-only", manifest["patch"]["reason"])
        self.assertEqual([], manifest["changed_paths"])
        self.assertEqual([], list(where.glob("*.patch")))
        shown = self.cli("bundle", "demo", "T01").stdout
        self.assertIn("patch coverage unavailable", shown)
        self.assertIn("This is not an empty patch.", shown)
        # The round is still decidable, because documents-only runs stay usable.
        self.cli("decide", "demo", "T01", "--approve")
        self.assertIn("status: approved", (run / "T01-report-01.mdx").read_text())

    def test_scope_stays_owned_through_verification_and_until_the_review_boundary(self) -> None:
        """Accepted scope is not released when the work is handed to the reviewer."""
        self.repo()
        run = self.init(evidence_mode="git")
        self.assign("T01", file="src/a.py")
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        self.assertEqual(1, len(self.ledger()))

        self.cli("assign", "demo", "T02", "--file", "src/a.py", "--verify", "true")
        self.fill_scope(run / "T02-scope.mdx")
        held = self.cli("scope", "demo", "T02", "--submit", ok=False)
        self.assertEqual(1, held.returncode)
        self.assertIn("overlaps T01", held.stderr)

        self.cli("decide", "demo", "T01", "--approve")
        released = self.cli("scope", "demo", "T02", "--submit")
        self.assertIn("scope accepted", released.stdout)

    def test_provisional_integration_needs_an_explicit_run_policy(self) -> None:
        """Consuming unapproved work is a declared run choice, never a default."""
        run = self.init("combined", evidence_mode="documents-only")
        self.assign("T01", file="src/a.py")
        self.assign("T02", file="src/b.py")
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")

        refused = self.cli("depend", "demo", "T02", "--on", "T01", ok=False)
        self.assertEqual(1, refused.returncode)
        self.assertIn("provisional_integration: forbidden", refused.stderr)
        self.assertFalse((run / ".deps" / "T02.json").exists())

        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace(
            "provisional_integration: forbidden", "provisional_integration: allowed"
        ))
        allowed = self.cli("depend", "demo", "T02", "--on", "T01")
        self.assertIn("readiness, never approval", allowed.stdout)
        recorded = json.loads((run / ".deps" / "T02.json").read_text())["dependencies"][0]
        self.assertEqual("provisional", recorded["kind"])
        self.assertEqual("submitted", recorded["state"])
        self.assertEqual(self.frozen("T01")[0]["digest"], recorded["bundle"])

        # Readiness is not approval: T01 stays submitted while T02 is approved.
        self.fill_task(run / "T02-task.mdx")
        self.fill_task_report(run / "T02-report-01.mdx")
        self.cli("submit", "demo", "T02")
        approved = self.cli("decide", "demo", "T02", "--approve")
        self.assertIn("readiness, not an approval of T01", approved.stdout)
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())

    def test_a_consumed_input_that_moves_invalidates_the_work_that_consumed_it(self) -> None:
        """Provisional work is reverified when its input changes, not silently kept."""
        run = self.init("combined", evidence_mode="documents-only")
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace(
            "provisional_integration: forbidden", "provisional_integration: allowed"
        ))
        self.assign("T01", file="src/a.py")
        self.assign("T02", file="src/b.py")
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        self.cli("submit", "demo", "T01")
        self.cli("depend", "demo", "T02", "--on", "T01")
        consumed = self.frozen("T01")[0]["digest"]

        # T01 freezes different evidence under the same round.
        report.write_text(report.read_text().replace("status: submitted", "status: draft").replace(
            "Implemented the feature and verified its behavior.", "Reimplemented it differently.",
        ))
        self.cli("submit", "demo", "T01")
        self.assertNotEqual(consumed, self.frozen("T01")[0]["digest"])

        self.fill_task(run / "T02-task.mdx")
        self.fill_task_report(run / "T02-report-01.mdx")
        blocked = self.cli("submit", "demo", "T02", ok=False)
        self.assertEqual(1, blocked.returncode)
        self.assertIn("dependency T01 changed since T02 consumed it", blocked.stderr)
        self.assertIn("status: draft", (run / "T02-report-01.mdx").read_text())
        self.assertIn("stale: dependency T01 changed", self.cli("status", "demo").stdout)

        self.cli("depend", "demo", "T02", "--on", "T01")
        self.cli("submit", "demo", "T02")
        self.assertIn("status: submitted", (run / "T02-report-01.mdx").read_text())

    def allow_provisional(self, run: Path) -> None:
        """Turn on the run policy that permits consuming verified-but-unapproved work."""
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace(
            "provisional_integration: forbidden", "provisional_integration: allowed"
        ))

    def assign_with_verify(
        self, owner: str, verify: str, file: str = "src/a.py",
        executor: str = "orchestrator",
    ) -> None:
        """A task whose verification command is the thing under test."""
        self.cli(
            "assign", "demo", owner, "--complexity", "high", "--executor", executor,
            "--harness", "claude", "--file", file, "--verify", verify,
        )
        if executor == "implementor":
            run = self.root / ".docket" / "runs" / "demo"
            self.fill_scope(run / f"{owner}-scope.mdx")
            self.cli("scope", "demo", owner, "--submit")

    def resolves(self, owner: str = "T01") -> None:
        """The report's recorded bundle digest names exactly one intact ledger entry."""
        run = self.root / ".docket" / "runs" / "demo"
        recorded = parse_meta(run / f"{owner}-report-01.mdx")["bundle_digest"]
        matching = [entry for entry in self.ledger(owner) if entry["digest"] == recorded]
        self.assertEqual(1, len(matching), f"{recorded} does not resolve to one ledger entry")
        manifest = json.loads(
            (run / ".bundles" / owner / matching[0]["dir"] / "bundle.json").read_text()
        )
        self.assertEqual(recorded, manifest["digest"])

    def test_a_frozen_bundle_keeps_the_baseline_evidence_it_depends_on(self) -> None:
        """Regression: a bundle used to point at `.snapshots`, which anything may rewrite.

        The manifest recorded the snapshot's name and digest but stored neither the
        record nor the staged, worktree and untracked artifacts a reconstruction needs.
        Deleting or rewriting the capture afterwards left `docket bundle` still calling
        the round intact while nothing could rebuild the baseline it was measured from.
        """
        run = self.dirty_task_over_every_shape()
        self.fill_task_report(run / "T01-report-01.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")

        manifest, where = self.frozen()
        snapshots = run / ".snapshots"
        # The baseline record and every reconstruction artifact are inside the bundle.
        self.assertEqual(
            (snapshots / "T01.json").read_bytes(), (where / manifest["baseline"]["file"]).read_bytes()
        )
        record = manifest["baseline"]["roots"][0]
        for key in ("staged_patch", "worktree_patch", "untracked_manifest"):
            frozen_copy = where / record[key]["file"]
            self.assertEqual(
                (snapshots / "T01" / Path(record[key]["file"]).name).read_bytes(),
                frozen_copy.read_bytes(),
            )
            self.assertEqual(sha(frozen_copy.read_bytes()), record[key]["sha256"])

        # Deleting the mutable capture cannot reach evidence that was already frozen.
        shutil.rmtree(snapshots / "T01")
        (snapshots / "T01.json").write_text('{"baseline": "recorded", "roots": []}\n')

        self.assertIn("intact", self.cli("bundle", "demo", "T01", "--list").stdout)
        self.assertEqual(manifest, self.frozen()[0])
        self.cli("decide", "demo", "T01", "--approve")
        self.assertIn("status: approved", (run / "T01-report-01.mdx").read_text())

    def test_a_verdict_refuses_a_bundle_whose_pinned_git_objects_are_gone(self) -> None:
        """Regression: tree identities were never checked, only artifact digests.

        A bundle states its patch, delta and source against trees in the run-private
        object store. Losing that store leaves the manifest hashing perfectly to itself
        and the round unreconstructable, which used to read as intact evidence.
        """
        run = self.dirty_task_over_every_shape()
        self.fill_task_report(run / "T01-report-01.mdx", files=self.ALL_SHAPES)
        self.cli("submit", "demo", "T01")
        self.assertIn("intact", self.cli("bundle", "demo", "T01", "--list").stdout)

        shutil.rmtree(run / ".bundles" / "objects")

        damaged = self.cli("bundle", "demo", "T01", ok=False)
        self.assertEqual(1, damaged.returncode)
        self.assertIn("DAMAGED", damaged.stderr)
        self.assertIn("Git can no longer read", damaged.stderr)
        self.assertIn("DAMAGED", self.cli("bundle", "demo", "T01", "--list").stdout)

        refused = self.cli("decide", "demo", "T01", "--approve", ok=False)
        self.assertEqual(1, refused.returncode)
        self.assertIn("damaged frozen evidence", refused.stderr)
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())

    def test_concurrent_and_interrupted_submissions_publish_one_coherent_round(self) -> None:
        """Regression: two submitters could race verification, the ledger, and the report.

        Nothing serialized a submission, so both writers passed the gate, both froze a
        round, and the last read-modify-write of `rounds.json` discarded the other
        entry - leaving a published report pointing at a bundle the ledger no longer
        retained.
        """
        self.repo()
        run = self.init(evidence_mode="git")
        self.assign_with_verify("T01", 'sleep 2; printf "T01 ok\\n"', executor="implementor")
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        (self.root / "src" / "a.py").write_text("allowed = 2\n")

        racing = [
            subprocess.Popen(
                [sys.executable, str(DOCKET), "submit", "demo", "T01"],
                cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        finished = [(process, *process.communicate()) for process in racing]

        won = [item for item in finished if item[0].returncode == 0]
        lost = [item for item in finished if item[0].returncode != 0]
        self.assertEqual(1, len(won), f"both submitters succeeded: {finished}")
        self.assertIn("reports stay draft until submit", lost[0][2])
        self.assertEqual(1, len(self.ledger()))
        self.assertEqual(1, self.cli("bundle", "demo", "T01", "--list").stdout.count("intact"))
        self.resolves()

        # An interruption between the freeze and the ledger append leaves neither a
        # visible submission nor an entry, and the retry still resolves exactly.
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        self.cli("decide", "demo", "T01", "--changes", "--reason", self.REASON)
        (self.root / "src" / "a.py").write_text("allowed = 3\n")
        second = run / "T01-report-02.mdx"
        self.fill_task_report(second)

        crash = self.cli("submit", "demo", "T01", ok=False, fault="publish:rounds.json")

        self.assertEqual(70, crash.returncode)
        self.assertIn("status: draft", second.read_text())
        self.assertEqual([1], [entry["round"] for entry in self.ledger()])

        self.cli("submit", "demo", "T01")

        self.assertEqual([1, 2], [entry["round"] for entry in self.ledger()])
        recorded = parse_meta(second)["bundle_digest"]
        self.assertEqual(self.ledger()[-1]["digest"], recorded)
        self.assertEqual(2, self.cli("bundle", "demo", "T01", "--list").stdout.count("intact"))

    def test_a_task_contract_edited_during_verification_is_never_frozen(self) -> None:
        """Regression: the freeze re-read the task after the verification had finished.

        The gate checked one contract, the command ran against it, and whatever the
        file held afterwards was frozen as the revision under review.
        """
        self.repo()
        run = self.init(evidence_mode="git")
        self.assign_with_verify(
            "T01", "printf 'Amended mid-run.\\n' >> .docket/runs/demo/T01-task.mdx"
        )
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        (self.root / "src" / "a.py").write_text("allowed = 2\n")

        rejected = self.cli("submit", "demo", "T01", ok=False)

        self.assertEqual(1, rejected.returncode)
        self.assertIn("the task contract changed while the verification ran", rejected.stderr)
        self.assertIn("status: draft", report.read_text())
        self.assertEqual([], self.ledger())
        # The concurrent edit is left exactly as its writer left it.
        self.assertIn("Amended mid-run.", (run / "T01-task.mdx").read_text())

    def test_a_report_edited_during_verification_is_never_overwritten(self) -> None:
        """Regression: the report was republished from the body read before the run."""
        self.repo()
        run = self.init(evidence_mode="git")
        self.assign_with_verify(
            "T01",
            "printf 'Added by a concurrent writer.\\n' >> .docket/runs/demo/T01-report-01.mdx",
        )
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report)
        (self.root / "src" / "a.py").write_text("allowed = 2\n")

        rejected = self.cli("submit", "demo", "T01", ok=False)

        self.assertEqual(1, rejected.returncode)
        self.assertIn("the report changed while the verification ran", rejected.stderr)
        self.assertIn("the concurrent edit was left in place", rejected.stderr)
        self.assertIn("Added by a concurrent writer.", report.read_text())
        self.assertIn("status: draft", report.read_text())
        self.assertEqual([], self.ledger())

    def test_a_dependency_that_moves_during_verification_stops_the_submission(self) -> None:
        """Regression: consumed inputs were checked before the run and never again."""
        run = self.init("combined", evidence_mode="documents-only")
        self.allow_provisional(run)
        self.assign("T01", file="src/a.py")
        self.assign_with_verify(
            "T02", f"{sys.executable} {DOCKET} submit demo T01", file="src/b.py"
        )
        self.fill_task(run / "T01-task.mdx")
        first = run / "T01-report-01.mdx"
        self.fill_task_report(first)
        self.cli("submit", "demo", "T01")
        self.cli("depend", "demo", "T02", "--on", "T01")
        consumed = self.frozen("T01")[0]["digest"]

        # T02's verification is what moves T01: it resubmits a differently worded round.
        first.write_text(first.read_text().replace("status: submitted", "status: draft").replace(
            "Implemented the feature and verified its behavior.", "Reimplemented it differently.",
        ))
        self.fill_task(run / "T02-task.mdx")
        self.fill_task_report(run / "T02-report-01.mdx")

        rejected = self.cli("submit", "demo", "T02", ok=False)

        self.assertEqual(1, rejected.returncode)
        self.assertIn("a consumed input moved while the verification ran", rejected.stderr)
        self.assertIn("dependency T01 changed since T02 consumed it", rejected.stderr)
        self.assertNotEqual(consumed, self.frozen("T01")[0]["digest"])
        self.assertIn("status: draft", (run / "T02-report-01.mdx").read_text())
        self.assertEqual([], self.ledger("T02"))

    def test_provisional_integration_refuses_evidence_without_a_passed_verification(self) -> None:
        """Regression: any frozen bundle was consumable once the policy said allowed.

        The policy permits consuming verified-but-unapproved work. Blocked and skipped
        rounds carry no usable verification result at all.
        """
        run = self.init("combined", evidence_mode="documents-only")
        self.allow_provisional(run)
        for owner, file in (("T01", "src/a.py"), ("T02", "src/b.py"), ("T03", "src/c.py")):
            self.assign(owner, file=file)
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True)
        self.cli("submit", "demo", "T01", "--blocked")

        blocked = self.cli("depend", "demo", "T03", "--on", "T01", ok=False)
        self.assertEqual(1, blocked.returncode)
        self.assertIn("submitted blocked, so no verification was run", blocked.stderr)
        self.assertIn("not usable work", blocked.stderr)
        self.assertFalse((run / ".deps" / "T03.json").exists())

        self.fill_task(run / "T02-task.mdx")
        self.fill_task_report(run / "T02-report-01.mdx")
        self.cli("submit", "demo", "T02", "--skip-verify", "--skip-verify-reason", "provider outage")

        skipped = self.cli("depend", "demo", "T03", "--on", "T02", ok=False)
        self.assertEqual(1, skipped.returncode)
        self.assertIn("its frozen verification was skipped", skipped.stderr)
        self.assertFalse((run / ".deps" / "T03.json").exists())

    def test_provisional_integration_refuses_stale_and_patchless_evidence(self) -> None:
        """A consumed bundle must still describe its task, and must carry its patch."""
        self.repo()
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        run = self.init("combined", evidence_mode="git", roots=("root=.", "wt=wt"))
        self.allow_provisional(run)
        self.assign("T01", file="src/a.py")
        self.assign("T02", file="src/b.py")
        self.assign("T03", file="src/c.py")
        self.fill_task(run / "T01-task.mdx")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, files="- `root:src/a.py:1` - implemented the feature.")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.cli("submit", "demo", "T01")

        # The report moved on after the freeze, so the bundle no longer describes T01.
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.", "Quietly widened the change.",
        ))
        stale = self.cli("depend", "demo", "T03", "--on", "T01", ok=False)
        self.assertEqual(1, stale.returncode)
        self.assertIn("edited its report after freezing", stale.stderr)
        self.assertIn("stale evidence", stale.stderr)

        # A blocked round whose declared root has gone froze no patch at all.
        self.fill_task(run / "T02-task.mdx")
        self.fill_task_report(run / "T02-report-01.mdx", blocked=True)
        self.git("worktree", "remove", "--force", "wt")
        self.cli("submit", "demo", "T02", "--blocked")
        self.assertEqual("unavailable", self.frozen("T02")[0]["patch"]["coverage"])

        patchless = self.cli("depend", "demo", "T03", "--on", "T02", ok=False)
        self.assertEqual(1, patchless.returncode)
        self.assertIn("froze no root-qualified patch", patchless.stderr)
        self.assertIn("This is not an empty patch", patchless.stderr)
        self.assertFalse((run / ".deps" / "T03.json").exists())

    def test_a_moved_input_withdraws_review_readiness_until_it_is_reverified(self) -> None:
        """Regression: only approval was blocked, so a waiver settled moved evidence.

        Dependency readiness is never final approval, and it does not survive the input
        moving: an accepting verdict needs fresh verification and a fresh review.
        """
        run = self.init("combined", evidence_mode="documents-only")
        self.allow_provisional(run)
        for owner, file in (("T01", "src/a.py"), ("T02", "src/b.py"), ("T03", "src/c.py")):
            self.assign(owner, file=file)
        self.fill_task(run / "T01-task.mdx")
        first = run / "T01-report-01.mdx"
        self.fill_task_report(first)
        self.cli("submit", "demo", "T01")
        for owner in ("T02", "T03"):
            self.cli("depend", "demo", owner, "--on", "T01")
            self.fill_task(run / f"{owner}-task.mdx")
        self.fill_task_report(run / "T02-report-01.mdx", blocked=True)
        self.cli("submit", "demo", "T02", "--blocked")
        self.fill_task_report(run / "T03-report-01.mdx")
        self.cli("submit", "demo", "T03")

        # T01 freezes different evidence after both consumers submitted.
        first.write_text(first.read_text().replace("status: submitted", "status: draft").replace(
            "Implemented the feature and verified its behavior.", "Reimplemented it differently.",
        ))
        self.cli("submit", "demo", "T01")

        approval = self.cli("decide", "demo", "T03", "--approve", ok=False)
        self.assertEqual(1, approval.returncode)
        self.assertIn("T03 cannot be approved against consumed evidence that moved", approval.stderr)
        waiver = self.cli("decide", "demo", "T02", "--waive", "--reason", "external", ok=False)
        self.assertEqual(1, waiver.returncode)
        self.assertIn("T02 cannot be waived against consumed evidence that moved", waiver.stderr)
        for owner in ("T02", "T03"):
            self.assertFalse((run / f"{owner}-decision-01.mdx").exists())
        status = self.cli("status", "demo").stdout
        self.assertIn("review readiness withdrawn for T02, T03", status)

        # Readiness comes back through a fresh round, never through re-recording the pin
        # under the submitted one: that would swap the inputs without reverifying anything.
        refused = self.cli("depend", "demo", "T03", "--on", "T01", ok=False)
        self.assertEqual(1, refused.returncode)
        self.assertIn("T03 is submitted", refused.stderr)
        self.assertIn("docket decide demo T03 --changes", refused.stderr)

    def reseal(self, owner: str, mutate: object, index: int = -1) -> dict:
        """Rewrite a frozen manifest and re-address it the way the CLI does.

        A forger who edits a bundle also fixes up its address and its ledger entry, so
        the self-address check alone proves nothing about the fields inside. This is how
        the suite reaches the checks that come after it.
        """
        run = self.root / ".docket" / "runs" / "demo"
        entry = self.ledger(owner)[index]
        where = run / ".bundles" / owner / entry["dir"]
        manifest = json.loads((where / "bundle.json").read_text())
        mutate(manifest)
        payload = {key: value for key, value in manifest.items() if key != "digest"}
        manifest["digest"] = sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        (where / "bundle.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        ledger_path = run / ".bundles" / owner / "rounds.json"
        ledger = json.loads(ledger_path.read_text())
        ledger["entries"][index]["digest"] = manifest["digest"]
        ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        return manifest

    def consuming_pair(self) -> Path:
        """T01 submitted and frozen, T02 recording it as a provisional input."""
        run = self.init("combined", evidence_mode="documents-only")
        self.allow_provisional(run)
        self.assign("T01", file="src/a.py")
        self.assign("T02", file="src/b.py")
        for owner in ("T01", "T02"):
            self.fill_task(run / f"{owner}-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        self.cli("depend", "demo", "T02", "--on", "T01")
        return run

    def test_a_bundle_freezes_the_consumed_input_record_it_was_verified_against(self) -> None:
        """Regression: the manifest said nothing about the inputs the round consumed.

        `submission_identity` checked `.deps/<owner>.json` around the verification, but
        the freeze kept no copy, so nothing downstream could tell which inputs a captured
        result had actually described.
        """
        run = self.consuming_pair()
        self.fill_task_report(run / "T02-report-01.mdx")
        self.cli("submit", "demo", "T02")

        live = (run / ".deps" / "T02.json").read_bytes()
        manifest, where = self.frozen("T02")
        block = manifest["dependencies"]
        self.assertEqual(".deps/T02.json", block["source"])
        self.assertEqual(sha(live), block["revision"])
        self.assertEqual(live, (where / block["file"]).read_bytes())
        self.assertEqual(
            [{"on": "T01", "kind": "provisional", "round": "1",
              "bundle": self.frozen("T01")[0]["digest"]}],
            block["consumed"],
        )

        # A task that consumes nothing freezes the explicit empty set, not an absent
        # artifact that cannot be told apart from one that was lost.
        empty, where = self.frozen("T01")
        self.assertEqual([], empty["dependencies"]["consumed"])
        self.assertEqual(
            {"owner": "T01", "dependencies": []},
            json.loads((where / empty["dependencies"]["file"]).read_text()),
        )
        self.assertEqual(
            sha((where / empty["dependencies"]["file"]).read_bytes()),
            empty["dependencies"]["revision"],
        )

        # The record is checked like any other part of the evidence, past the address.
        self.reseal("T02", lambda m: m.pop("dependencies"))
        damaged = self.cli("bundle", "demo", "T02", ok=False)
        self.assertEqual(1, damaged.returncode)
        self.assertIn("froze no consumed-input record", damaged.stderr)
        self.assertIn("DAMAGED", self.cli("bundle", "demo", "T02", "--list").stdout)

    def test_a_refreshed_pin_cannot_approve_a_round_verified_against_the_old_input(self) -> None:
        """Regression: re-recording a moved input silenced the staleness and approved it.

        `dependency_problems` compared the live record against the input's newest bundle,
        so `docket depend` alone restored review readiness with no second verification.
        The verdict now binds to the record frozen into the round, and the refresh itself
        is refused while the consumer is submitted.
        """
        run = self.init("combined", evidence_mode="documents-only")
        self.allow_provisional(run)
        counter = self.root / "verify.log"
        self.assign("T01", file="src/a.py")
        self.assign_with_verify(
            "T03", f'printf x >> "{counter}"', file="src/c.py", executor="implementor",
        )
        for owner in ("T01", "T03"):
            self.fill_task(run / f"{owner}-task.mdx")
        first = run / "T01-report-01.mdx"
        self.fill_task_report(first)
        self.cli("submit", "demo", "T01")
        self.cli("depend", "demo", "T03", "--on", "T01")
        consumed = self.frozen("T01")[0]["digest"]
        self.fill_task_report(run / "T03-report-01.mdx", files="- `src/c.py:1` - built on T01.")
        self.cli("submit", "demo", "T03")
        self.assertEqual("x", counter.read_text())
        pinned = (run / ".deps" / "T03.json").read_bytes()
        stale_bundle = self.ledger("T03")[0]

        # T01 freezes different evidence after T03 was verified against the old bundle.
        first.write_text(first.read_text().replace("status: submitted", "status: draft").replace(
            "Implemented the feature and verified its behavior.", "Reimplemented it differently.",
        ))
        self.cli("submit", "demo", "T01")
        moved = self.frozen("T01")[0]["digest"]
        self.assertNotEqual(consumed, moved)

        # Layer one: the refresh is refused outright, and nothing on disk moves.
        refused = self.cli("depend", "demo", "T03", "--on", "T01", ok=False)
        self.assertEqual(1, refused.returncode)
        self.assertIn("T03 is submitted", refused.stderr)
        self.assertIn(f"Round 1 stays frozen at {stale_bundle['digest']}", refused.stderr)
        self.assertEqual(pinned, (run / ".deps" / "T03.json").read_bytes())
        self.assertEqual([stale_bundle], self.ledger("T03"))
        self.assertIn("intact", self.cli("bundle", "demo", "T03", "--list").stdout)

        # Dropping the record entirely is the same kind of move, and stays visible even
        # though there is no live consumption left to list.
        record = run / ".deps" / "T03.json"
        record.write_text(json.dumps({"owner": "T03", "dependencies": []}, indent=2) + "\n")
        dropped = self.cli("decide", "demo", "T03", "--approve", ok=False)
        self.assertEqual(1, dropped.returncode)
        self.assertIn(f"T03 no longer records consuming T01 at {consumed}", dropped.stderr)
        self.assertIn("review readiness withdrawn for T03", self.cli("status", "demo").stdout)

        # Layer two: the record moves anyway, and the verdict still refuses it because
        # the round was frozen against the pin it no longer holds.
        (run / ".deps" / "T03.json").write_text(
            json.dumps({"owner": "T03", "dependencies": [{
                "on": "T01", "kind": "provisional", "state": "submitted",
                "round": "1", "bundle": moved, "recorded_at": "2026-01-01T00:00:00Z",
            }]}, indent=2, sort_keys=True) + "\n"
        )
        approval = self.cli("decide", "demo", "T03", "--approve", ok=False)
        self.assertEqual(1, approval.returncode)
        self.assertIn("T03 cannot be approved against consumed evidence that moved", approval.stderr)
        self.assertIn("re-recording a pin is not a reverification", approval.stderr)
        self.assertFalse((run / "T03-decision-01.mdx").exists())
        self.assertIn("review readiness withdrawn for T03", self.cli("status", "demo").stdout)
        self.assertEqual("x", counter.read_text())

        # Recovery: a changes-requested round, then the refresh, then a real reverification.
        self.cli("decide", "demo", "T03", "--changes")
        decision = run / "T03-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Reverify `src/c.py:1` against the new T01 evidence.",
        ))
        self.cli("decide", "demo", "T03", "--changes", "--reason", "The input moved.")
        self.cli("depend", "demo", "T03", "--on", "T01")
        self.fill_task_report(run / "T03-report-02.mdx", files="- `src/c.py:1` - rebuilt on T01.")
        self.cli("submit", "demo", "T03")
        self.assertEqual("xx", counter.read_text(), "the correction round reran the verification")
        self.assertEqual(
            sha((run / ".deps" / "T03.json").read_bytes()),
            self.frozen("T03")[0]["dependencies"]["revision"],
        )
        self.cli("decide", "demo", "T03", "--approve")
        self.assertIn("status: approved", (run / "T03-report-02.mdx").read_text())
        # The superseded round keeps its own address and its own bytes.
        self.assertEqual(stale_bundle, self.ledger("T03")[0])

    def test_a_decided_consumer_stays_visibly_stale_rather_than_quietly_refreshed(self) -> None:
        """An approved round is not reopened by re-recording its input from the side."""
        run = self.consuming_pair()
        self.fill_task_report(run / "T02-report-01.mdx")
        self.cli("submit", "demo", "T02")
        self.cli("decide", "demo", "T02", "--approve")
        approved = self.ledger("T02")
        pinned = (run / ".deps" / "T02.json").read_bytes()

        first = run / "T01-report-01.mdx"
        first.write_text(first.read_text().replace("status: submitted", "status: draft").replace(
            "Implemented the feature and verified its behavior.", "Reimplemented it differently.",
        ))
        self.cli("submit", "demo", "T01")

        refused = self.cli("depend", "demo", "T02", "--on", "T01", ok=False)
        self.assertEqual(1, refused.returncode)
        self.assertIn("T02 is approved", refused.stderr)
        self.assertIn("audited work this build does not do", refused.stderr)
        self.assertEqual(pinned, (run / ".deps" / "T02.json").read_bytes())
        self.assertEqual(approved, self.ledger("T02"))
        self.assertIn("status: approved", (run / "T02-report-01.mdx").read_text())

        status = self.cli("status", "demo").stdout
        self.assertIn("stale: dependency T01 changed since T02 consumed it", status)
        self.assertIn("review readiness withdrawn for T02", status)

    def test_a_baseline_artifact_that_disappears_fails_the_freeze(self) -> None:
        """Regression: a lost reconstruction artifact was recorded as `unavailable`.

        A blocked round freezes without requiring a patch, so a capture deleted between
        reconstructing the trees and copying them left a placeholder inside a bundle that
        `docket bundle` still called intact.
        """
        run = self.dirty_task_over_every_shape()
        self.fill_task_report(run / "T01-report-01.mdx", files=self.ALL_SHAPES, blocked=True)
        lost = run / ".snapshots" / "T01" / "root.worktree.patch"
        self.assertTrue(lost.is_file())
        lost.unlink()

        refused = self.cli("submit", "demo", "T01", "--blocked", ok=False)
        self.assertEqual(1, refused.returncode)
        self.assertIn("root.worktree.patch", refused.stderr)
        self.assertIn("could not be read while the round was being frozen", refused.stderr)
        self.assertIn("Nothing was handed off", refused.stderr)
        self.assertEqual([], self.ledger("T01"))
        self.assertIn("status: draft", (run / "T01-report-01.mdx").read_text())

    def test_the_contract_and_inputs_frozen_are_the_ones_the_verification_described(self) -> None:
        """Regression: the freeze re-read the task after the drift recheck had passed.

        Between the recheck and the freeze a writer could still land, and the bundle then
        carried a contract and a consumed-input record the captured result never saw. Both
        are captured before the verification runs and handed to the freeze.
        """
        run = self.consuming_pair()
        task = run / "T02-task.mdx"
        self.fill_task_report(run / "T02-report-01.mdx")
        verified_contract = task.read_bytes()
        verified_record = (run / ".deps" / "T02.json").read_bytes()

        # A writer lands in the window between the drift recheck and the freeze.
        deps = run / ".deps" / "T02.json"
        self.cli("submit", "demo", "T02", perturb="\n".join([
            'submit:before-freeze=printf "late contract edit\\n" >> "%s"' % task,
            'submit:before-freeze=printf \'{"owner": "T02", "dependencies": []}\' > "%s"' % deps,
        ]))

        manifest, where = self.frozen("T02")
        self.assertEqual(verified_contract, (where / manifest["contract"]["file"]).read_bytes())
        self.assertEqual(sha(verified_contract), manifest["contract"]["revision"])
        self.assertEqual(verified_record, (where / manifest["dependencies"]["file"]).read_bytes())
        self.assertEqual(sha(verified_record), manifest["dependencies"]["revision"])
        self.assertEqual("T01", manifest["dependencies"]["consumed"][0]["on"])
        # The perturbation really landed; it just never reached the frozen evidence.
        self.assertIn("late contract edit", task.read_text())
        self.assertEqual([], json.loads(deps.read_text())["dependencies"])

    def test_a_multi_root_task_bundle_freezes_every_root_it_measured(self) -> None:
        """Two checkouts are two change surfaces, and both belong in one frozen round."""
        self.repo()
        linked = self.root / "wt"
        self.git("worktree", "add", "-q", "-b", "feature", "wt")
        run = self.init(evidence_mode="git", roots=("root=.", "wt=wt"))
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--executor", "orchestrator",
            "--harness", "claude", "--file", "src/a.py", "--file", "wt:src/a.py",
            "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_task(run / "T01-task.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        (linked / "src" / "a.py").write_text("allowed = 3\n")
        self.fill_task_report(
            run / "T01-report-01.mdx",
            files="- `root:src/a.py:1` - outer checkout.\n- `wt:src/a.py:1` - linked worktree.",
        )

        self.cli("submit", "demo", "T01")

        manifest, where = self.frozen()
        self.assertEqual(["root", "wt"], sorted(r["alias"] for r in manifest["patch"]["roots"]))
        self.assertEqual(["root", "wt"], sorted(r["alias"] for r in manifest["baseline"]["roots"]))
        self.assertEqual(["root", "wt"], sorted(r["alias"] for r in manifest["source"]["roots"]))
        self.assertEqual(
            ["root:src/a.py", "wt:src/a.py"], sorted(manifest["changed_paths"])
        )
        wanted = {"root": "+allowed = 2", "wt": "+allowed = 3"}
        for record in manifest["patch"]["roots"]:
            self.assertIn(wanted[record["alias"]], (where / record["file"]).read_text())
        # Every root's baseline evidence is frozen, and every pinned tree still resolves.
        for record in manifest["baseline"]["roots"]:
            for key in ("staged_patch", "worktree_patch", "untracked_manifest"):
                self.assertTrue((where / record[key]["file"]).is_file())
        self.assertIn("intact", self.cli("bundle", "demo", "T01", "--list").stdout)

    # ------------------------------------------------------ aggregate bundles

    def setup_aggregate_run(self, evidence_mode: str = "git") -> Path:
        """A run with two decided tasks, ready for aggregate submission."""
        if evidence_mode == "git":
            self.repo()
            (self.root / "src" / "b.py").write_text("b = 1\n")
            self.git("add", "src/b.py")
            self.git("commit", "-qm", "add b")
        self.init("split", evidence_mode=evidence_mode)
        run = self.root / ".docket" / "runs" / "demo"
        # Serialize through one checkout: assign, edit, and submit T01 before
        # assigning T02, so T02's baseline already contains T01's worktree
        # change. Assigning both upfront would leave T01's edit outside T02's
        # scope and the scope gate would correctly reject T02's submission.
        for owner, fname in (("T01", "a.py"), ("T02", "b.py")):
            if evidence_mode == "git":
                self.assign(owner, file=f"src/{fname}")
            else:
                self.cli(
                    "assign", "demo", owner, "--complexity", "high", "--executor", "implementor",
                    "--harness", "opencode", "--file", f"src/{fname}",
                    "--verify", f'printf "{owner} ok\\n"',
                )
                self.fill_scope(run / f"{owner}-scope.mdx")
                self.cli("scope", "demo", owner, "--submit")
            task = run / f"{owner}-task.mdx"
            if task.is_file():
                self.fill_task(task)
            self.fill_task_report(run / f"{owner}-report-01.mdx",
                                  files=f"- `src/{fname}:1` - implemented {owner}.")
            if evidence_mode == "git":
                (self.root / "src" / fname).write_text(f"changed for {owner}\n")
            self.cli("submit", "demo", owner)
            self.cli("decide", "demo", owner, "--approve")
        return run

    def aggregate(self, index: int = -1) -> tuple[dict, Path]:
        return self.frozen("orch", index)

    def setup_overlapping_aggregate_run(self) -> Path:
        """Two decided tasks sharing one path, over pre-baseline dirt, with every patch shape.

        The run baseline is captured over pre-existing user dirt that no task
        touches, so the aggregate must not attribute it to the run. T01 and T02
        then change `src/shared.py` in sequence: T02 is assigned only after T01
        is approved releases the scope, per the plan's explicit serialization
        for writers sharing one checkout. The aggregate patch must therefore
        carry the composed run-baseline-to-final change for that path, not two
        stacked task hunks. The remaining edits mirror the T42 shape catalogue
        in `dirty_task_over_every_shape` (committed, staged, unstaged,
        untracked, deleted, renamed, executable-mode, symlink, binary) because
        the aggregate freeze renders its patch with the same
        `baseline_tree`/`source_tree`/`tree_patch` routine the task-round
        freeze uses, measured from the run baseline instead of a task baseline.
        """
        self.repo()
        (self.root / "src" / "shared.py").write_text("shared = 1\n")
        (self.root / "src" / "blob.bin").write_bytes(bytes(range(256)))
        for name in ("gone.py", "mode.py"):
            (self.root / "src" / name).write_text(f"{name.split('.')[0]} = 1\n")
        # Enough shared lines that rename detection still pairs old.py with
        # new.py after the one-line edit below (git needs 50 percent similarity).
        (self.root / "src" / "old.py").write_text("old = 1\nguard = 0\nkeep = 1\n")
        (self.root / "src" / "pre.py").write_text("pre = 1\n")
        (self.root / "src" / "stage.py").write_text("stage = 1\n")
        self.git("add", "src")
        self.git("commit", "-qm", "more tracked content")

        # Initial dirt: somebody else's uncommitted work, dirty before the run
        # baseline is taken. A tracked worktree edit, a staged edit, and an
        # untracked file, none of which any task will touch.
        (self.root / "src" / "pre.py").write_text("pre = 999\n")
        (self.root / "src" / "stage.py").write_text("stage = 999\n")
        self.git("add", "src/stage.py")
        (self.root / "src" / "predirt.txt").write_text("pre-existing untracked\n")

        run = self.init("split", evidence_mode="git")

        # T01 takes the first pass over the shared path, plus a staged rename,
        # a deletion, a mode change, a new executable, and a new symlink.
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src", "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.fill_task(run / "T01-task.mdx")
        (self.root / "src" / "shared.py").write_text("shared = 2\n")
        self.git("mv", "src/old.py", "src/new.py")
        (self.root / "src" / "new.py").write_text("old = 1\nguard = 1\nkeep = 1\n")
        (self.root / "src" / "gone.py").unlink()
        (self.root / "src" / "mode.py").chmod(0o755)
        tool = self.root / "src" / "tool.sh"
        tool.write_text("#!/bin/sh\necho hi\n")
        tool.chmod(0o755)
        (self.root / "src" / "link").symlink_to("new.py")
        self.fill_task_report(
            run / "T01-report-01.mdx",
            files="- `src/shared.py` `src/old.py` `src/new.py` `src/gone.py` "
            "`src/mode.py` `src/tool.sh` `src/link` - first pass over the shared path.",
        )
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--approve")

        # T02 is assigned after the approval released the scope, changes the
        # shared path again, and adds binary, staged, and unstaged shapes,
        # committing midway so committed-during-the-task work is included too.
        self.cli(
            "assign", "demo", "T02", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src", "--verify", 'printf "T02 ok\\n"',
        )
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        self.fill_task(run / "T02-task.mdx")
        (self.root / "src" / "shared.py").write_text("shared = 3\n")
        self.git("add", "src/shared.py")
        # Commit only this task's own path: the staged rename and staged dirt
        # inherited at assignment stay staged, so the gate does not attribute
        # committing somebody else's staged state to this task.
        self.git("commit", "-qm", "committed during T02", "--", "src/shared.py")
        (self.root / "src" / "blob.bin").write_bytes(bytes(range(256)) * 4)
        (self.root / "src" / "new.bin").write_bytes(bytes(range(256)) * 2)
        (self.root / "src" / "staged.py").write_text("staged = 1\n")
        self.git("add", "src/staged.py")
        (self.root / "src" / "unstaged.py").write_text("unstaged = 2\n")
        self.fill_task_report(
            run / "T02-report-01.mdx",
            files="- `src/shared.py` `src/blob.bin` `src/new.bin` `src/staged.py` "
            "`src/unstaged.py` - second pass over the shared path.",
        )
        self.cli("submit", "demo", "T02")
        self.cli("decide", "demo", "T02", "--approve")
        return run

    def test_an_aggregate_submission_freezes_an_aggregate_bundle(self) -> None:
        """The orch submission freezes its own immutable bundle with constituent pins."""
        run = self.setup_aggregate_run()
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")

        manifest, where = self.aggregate()
        self.assertEqual("aggregate", manifest["kind"])
        self.assertEqual("orch", manifest["owner"])
        self.assertEqual(1, manifest["round"])
        self.assertIn("constituents", manifest)
        self.assertEqual(2, len(manifest["constituents"]))
        for item in manifest["constituents"]:
            self.assertIn(item["owner"], ("T01", "T02"))
            self.assertTrue(item["bundle"].startswith("sha256:"))
        self.assertEqual(
            "orch-report-01.mdx", manifest["report"]["source"]
        )
        self.assertIn("intact", self.cli("bundle", "demo", "orch", "--list").stdout)
        self.assertEqual("plan.mdx", manifest["contract"]["source"])
        self.assertEqual(
            (run / "plan.mdx").read_bytes(),
            (where / manifest["contract"]["file"]).read_bytes(),
        )
        # The aggregate pins the run baseline, provably not any task baseline:
        # the frozen revision is the digest of run.json, and no task snapshot
        # digests to it. The reconstruction artifacts travel with the bundle.
        self.assertEqual("run.json", manifest["baseline"]["snapshot"])
        run_snapshot = (run / ".snapshots" / "run.json").read_bytes()
        self.assertEqual(sha(run_snapshot), manifest["baseline"]["revision"])
        self.assertEqual(
            run_snapshot, (where / manifest["baseline"]["file"]).read_bytes()
        )
        for owner in ("T01", "T02"):
            task_snapshot = (run / ".snapshots" / f"{owner}.json").read_bytes()
            self.assertNotEqual(sha(task_snapshot), manifest["baseline"]["revision"])
        for record in manifest["baseline"]["roots"]:
            for key in ("staged_patch", "worktree_patch", "untracked_manifest"):
                self.assertTrue((where / record[key]["file"]).is_file())
        # The integrated source revision is recorded.
        self.assertEqual("available", manifest["source"]["coverage"])
        source = manifest["source"]["roots"][0]
        self.assertEqual("root", source["alias"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), source["head"])
        self.assertEqual(40, len(source["tree"]))
        # The captured verification. An orch report structurally has no verify
        # command (there is no orch-task.mdx, so task_verify returns ""), hence
        # the recorded status is none with an empty command and --skip-verify
        # is vacuous rather than a skipped run of a real command.
        self.assertEqual("none", manifest["verification"]["status"])
        self.assertEqual("", manifest["verification"]["command"])

    def test_aggregate_patch_is_from_the_run_baseline(self) -> None:
        """The aggregate patch is composed from the run baseline, not concatenated tasks."""
        run = self.setup_overlapping_aggregate_run()
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        result = self.cli("submit", "demo", "orch", "--skip-verify")

        manifest, where = self.aggregate()
        self.assertEqual("available", manifest["patch"]["coverage"])
        # Overlapping scopes compose into one net change per path. The
        # intermediate value is real task evidence: T01's own frozen patch
        # carries `shared = 2`. A concatenation of the two task patches would
        # repeat that intermediate value as both an addition and a removal.
        t01_patch = self.frozen_patch("T01")
        self.assertIn("+shared = 2", t01_patch)
        patch = "\n".join(
            (where / record["file"]).read_text(errors="replace")
            for record in manifest["patch"]["roots"]
        )
        self.assertIn("-shared = 1", patch)
        self.assertIn("+shared = 3", patch)
        self.assertNotIn("shared = 2", patch)
        section = [part for part in patch.split("diff --git") if "src/shared.py" in part]
        self.assertEqual(1, len(section))
        self.assertEqual(
            1, sum(1 for line in section[0].splitlines() if line.startswith("@@"))
        )
        # Initial dirt is nobody's work: it predates the run baseline, so none
        # of its paths or values may appear in the aggregate change surface.
        for dirt in ("root:src/pre.py", "root:src/stage.py", "root:src/predirt.txt"):
            self.assertNotIn(dirt, manifest["changed_paths"])
        self.assertNotIn("pre = 999", patch)
        self.assertNotIn("stage = 999", patch)
        self.assertNotIn("pre-existing untracked", patch)
        # The remaining criterion shapes, asserted once at aggregate level. The
        # same routine renders task and aggregate patches, so one pass suffices.
        for path in (
            "root:src/shared.py", "root:src/new.py", "root:src/gone.py",
            "root:src/mode.py", "root:src/tool.sh", "root:src/link",
            "root:src/blob.bin", "root:src/new.bin",
            "root:src/staged.py", "root:src/unstaged.py",
        ):
            self.assertIn(path, manifest["changed_paths"])
        self.assertIn("rename from src/old.py", patch)
        self.assertIn("rename to src/new.py", patch)
        self.assertIn("+guard = 1", patch)
        self.assertIn("deleted file mode 100644", patch)
        self.assertIn("src/gone.py", patch)
        self.assertIn("new file mode 100755", patch)
        self.assertIn("old mode 100644", patch)
        self.assertIn("new mode 100755", patch)
        self.assertIn("120000", patch)
        self.assertIn("+new.py", patch)
        self.assertIn("GIT binary patch", patch)
        self.assertIn("+staged = 1", patch)
        self.assertIn("+unstaged = 2", patch)
        # Work committed during T02 is inside the aggregate patch, not lost
        # with the commit: the mid-task commit exists and its change is carried.
        self.assertIn("committed during T02", self.git("log", "--oneline"))
        self.assertIn("frozen aggregate bundle", result.stdout)

    def test_constituent_staleness_blocks_aggregate_decision(self) -> None:
        """A damaged pinned constituent makes the aggregate stale and blocks verdicts."""
        run = self.setup_aggregate_run()
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")

        # Damage a pinned constituent: an approved task cannot open another
        # round, so staleness through a moved pin is exercised through the
        # reopen path elsewhere. Here the constituent bundle itself becomes
        # unreconstructable, which must also invalidate the aggregate.
        _, where_t01 = self.frozen("T01")
        manifest_t01, _ = self.frozen("T01")
        damaged_target = where_t01 / str(manifest_t01["report"]["file"])
        damaged_target.unlink()

        status = self.cli("status", "demo").stdout
        self.assertIn("aggregate bundle staleness", status)
        self.assertIn("T01", status)

        # The aggregate stale info appears in the bundle listing.
        bundle_list = self.cli("bundle", "demo", "orch", "--list")
        self.assertIn("DAMAGED", bundle_list.stdout)

        # Decision on the stale aggregate is blocked.
        result = self.cli("decide", "demo", "orch", "--approve", ok=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("damaged aggregate evidence", result.stderr)

    def test_aggregate_decision_binds_to_bundle_digest(self) -> None:
        """Approval of the aggregate records the bundle digest in the decision."""
        run = self.setup_aggregate_run()
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")

        manifest, _ = self.aggregate()
        self.cli("decide", "demo", "orch", "--approve")
        dec = run / "orch-decision-01.mdx"
        self.assertTrue(dec.is_file())
        meta = parse_meta(dec)
        self.assertEqual("approved", meta["verdict"])
        self.assertEqual("yes", meta["applied"])
        self.assertEqual(manifest["digest"], meta["bundle_digest"])
        self.assertEqual("aggregate", meta["bundle_kind"])

    def test_aggregate_bundle_immutability_across_rounds(self) -> None:
        """Later rounds produce new aggregate addresses, never rewrite old ones."""
        run = self.setup_aggregate_run()
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")

        first, _ = self.aggregate()
        first_digest = first["digest"]

        # Open round 2 and submit again.
        self.cli("decide", "demo", "orch", "--changes", ok=False)
        decision = run / "orch-decision-01.mdx"
        if decision.is_file():
            decision.write_text(decision.read_text().replace(
                "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
                "1. Update the summary.",
            ))
        self.cli("decide", "demo", "orch", "--changes")
        r2 = run / "orch-report-02.mdx"
        self.fill_orch_report(r2)
        self.cli("submit", "demo", "orch", "--skip-verify")

        second, _ = self.aggregate()
        self.assertNotEqual(first_digest, second["digest"])
        # First bundle still intact.
        self.assertIn("intact", self.cli("bundle", "demo", "orch", "--list").stdout)

    def test_documents_only_still_freezes_aggregate(self) -> None:
        """Documents-only runs freeze an aggregate and label coverage explicitly."""
        run = self.setup_aggregate_run(evidence_mode="documents-only")
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")

        manifest, _ = self.aggregate()
        self.assertEqual("aggregate", manifest["kind"])
        self.assertEqual(COVERAGE_UNAVAILABLE, manifest["patch"]["coverage"])
        self.assertIn("documents-only", manifest["patch"]["reason"])
        self.assertIn("documents-only", manifest["source"]["reason"])
        self.assertEqual(2, len(manifest["constituents"]))

    def test_reopen_preserves_prior_round_and_invalidates_aggregate(self) -> None:
        """Reopening a waived task preserves its round, opens one next round, and breaks the aggregate."""
        run = self.setup_aggregate_run()
        # Approved T01/T02 cannot be waived, so use a fresh T03 for the waiver.
        self.assign("T03", file="src/c.py")
        self.fill_task(run / "T03-task.mdx")
        self.fill_task_report(run / "T03-report-01.mdx", blocked=True,
                              files="- `src/c.py:1` - blocked change.")
        self.cli("submit", "demo", "T03", "--blocked")
        self.cli("decide", "demo", "T03", "--waive", "--reason", "external outage")

        # Create aggregate with T01/T02 approved and T03 waived.
        self.assign("orch", executor="orchestrator")
        agg_report = run / "orch-report-01.mdx"
        agg_report.write_text(
            agg_report.read_text()
            .replace(
                "<!-- TODO: summarize the delivered outcome for the planner. -->",
                "Delivered every planned task."
            )
            .replace(
                "<!-- TODO: one row per planned task, including owner, terminal state, and verification. -->",
                "| task | outcome | verification |\n| --- | --- | --- |\n| T01 | approved | passed |\n| T02 | approved | passed |\n| T03 | waived | blocked (external outage) |"
            )
            .replace(
                "<!-- TODO: summarize the task-local changes the planner should review. -->",
                "Updated `src/a.py`, `src/b.py`; T03 is waived."
            )
            .replace(
                "<!-- TODO: exact integrated command and real output. -->",
                "Command: `printf ok`\n\nOutput: `ok`"
            )
        )
        self.cli("submit", "demo", "orch", "--skip-verify")
        first_agg, _ = self.aggregate()
        first_digest = first_agg["digest"]

        # Now reopen T03 via --reopen.
        reopen_result = self.cli(
            "decide", "demo", "T03", "--reopen",
            "--reason", "external service is back up",
            ok=True,
        )
        self.assertIn("waiver reopened", reopen_result.stdout)

        # Check T03 has a round 2 report, and the prior round is preserved.
        self.assertTrue((run / "T03-report-02.mdx").is_file())
        self.assertIn("waived", (run / "T03-report-01.mdx").read_text())
        self.assertIn("external outage", (run / "T03-decision-01.mdx").read_text())

        # The aggregate is now stale.
        stale_status = self.cli("status", "demo").stdout
        self.assertIn("aggregate bundle staleness", stale_status)
        self.assertIn("T03", stale_status)
        self.assertIn("reopened from waiver", stale_status)

        # Aggregate list shows DAMAGED.
        agg_list = self.cli("bundle", "demo", "orch", "--list")
        self.assertIn("DAMAGED", agg_list.stdout)

        # Decision on the stale aggregate is blocked.
        dec_result = self.cli("decide", "demo", "orch", "--approve", ok=False)
        self.assertNotEqual(0, dec_result.returncode)
        self.assertIn("damaged aggregate", dec_result.stderr)

        # Reopening the orch itself is refused.
        reopen_denied = self.cli("decide", "demo", "orch", "--reopen", ok=False)
        self.assertNotEqual(0, reopen_denied.returncode)

    def test_a_reopen_generation_outlives_the_next_decision(self) -> None:
        """Regression: the generation was read from the journal the next decision overwrote."""
        run = self.init()
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True)
        self.cli("submit", "demo", "T01", "--blocked")
        self.cli("decide", "demo", "T01", "--waive", "--reason", "external outage")
        self.cli("decide", "demo", "T01", "--reopen", "--reason", "the outage is over")

        def flagged_epoch(cause: str) -> int:
            self.cli("health", "demo", "--flag-stall", "T01", "--cause", cause)
            incidents = [json.loads(path.read_text())
                         for path in (run / ".incidents").glob("T01-*.json")]
            return next(item["epoch"] for item in incidents if item["cause"] == cause)

        self.assertEqual(2, flagged_epoch("idle after the reopen"))
        self.fill_task_report(run / "T01-report-02.mdx")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-02.mdx"
        decision.write_text(decision.read_text().replace(self.DECISION_PLACEHOLDER,
                                                         self.REQUIREMENT))
        self.cli("decide", "demo", "T01", "--changes")
        self.assertEqual(2, flagged_epoch("idle in the correction round"))

    def test_reopen_requires_waived_status(self) -> None:
        """Only a waived task can be reopened with --reopen."""
        self.setup_aggregate_run()
        result = self.cli("decide", "demo", "T01", "--reopen", "--reason", "test", ok=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not waived", result.stderr)

    def test_reopen_requires_reason(self) -> None:
        """Reopen requires a stated reason, just like a new waiver."""
        run = self.setup_aggregate_run()
        # A waived task needs a fresh owner: approved T01/T02 cannot be waived.
        self.assign("T03", file="src/c.py")
        self.fill_task(run / "T03-task.mdx")
        self.fill_task_report(run / "T03-report-01.mdx", blocked=True,
                              files="- `src/c.py:1` - blocked change.")
        self.cli("submit", "demo", "T03", "--blocked")
        self.cli("decide", "demo", "T03", "--waive", "--reason", "external outage")
        result = self.cli("decide", "demo", "T03", "--reopen", ok=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("requires --reason", result.stderr)

    def test_reopen_collision_refused(self) -> None:
        """Reopen with scope collision is refused rather than partially applied."""
        run = self.setup_aggregate_run()
        # Waive T03 on a disjoint scope; approved T01/T02 cannot be waived.
        self.assign("T03", file="src/c.py")
        self.fill_task(run / "T03-task.mdx")
        self.fill_task_report(run / "T03-report-01.mdx", blocked=True,
                              files="- `src/c.py:1` - blocked change.")
        self.cli("submit", "demo", "T03", "--blocked")
        self.cli("decide", "demo", "T03", "--waive", "--reason", "external outage")
        # Create active work on the same scope. Waived T03 is terminal so the
        # new assignment itself does not collide.
        self.cli(
            "assign", "demo", "T04", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/c.py",
            "--verify", 'printf "T04 ok\\n"',
        )
        self.fill_task(run / "T04-task.mdx")
        self.fill_scope(run / "T04-scope.mdx")
        self.cli("scope", "demo", "T04", "--submit")

        # Reopening T03 must now collide with active T04.
        result = self.cli(
            "decide", "demo", "T03", "--reopen",
            "--reason", "service is back up",
            ok=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("now overlaps active work", result.stderr)

    def test_reopen_interrupted_at_every_boundary_recovers(self) -> None:
        """Interrupted reopen recovery: every material-write boundary is finishable.

        A waived task can begin `--reopen`, create its next-round draft, and
        crash at the `transition:next-round` boundary. The old code then
        selected the new draft as the latest report and refused because it is
        not waived, leaving the reopen journal permanently in progress. The
        retry must instead finish the journalled transition without opening
        another round, without rewriting prior evidence, and without requiring
        the reason to be restated.
        """
        self.init("split", evidence_mode="documents-only")
        run = self.root / ".docket" / "runs" / "demo"
        faults = [
            "transition:begin",
            "publish:T0-REPORT",
            "transition:next-round",
            "transition:complete",
        ]
        for idx, fault_template in enumerate(faults):
            owner = f"T0{idx + 1}"
            fname = f"src/{owner.lower()}.py"
            with self.subTest(fault=fault_template, owner=owner):
                self.cli(
                    "assign", "demo", owner, "--complexity", "high", "--executor", "implementor",
                    "--harness", "opencode", "--file", fname,
                    "--verify", f'printf "{owner} ok\\n"',
                )
                self.fill_task(run / f"{owner}-task.mdx")
                self.fill_scope(run / f"{owner}-scope.mdx")
                self.cli("scope", "demo", owner, "--submit")
                self.fill_task_report(
                    run / f"{owner}-report-01.mdx", blocked=True,
                    files=f"- `{fname}:1` - blocked change.",
                )
                self.cli("submit", "demo", owner, "--blocked")
                self.cli("decide", "demo", owner, "--waive", "--reason", "external outage")
                rep1_before = (run / f"{owner}-report-01.mdx").read_bytes()
                dec1_before = (run / f"{owner}-decision-01.mdx").read_bytes()
                fault = fault_template.replace(
                    "T0-REPORT", f"{owner}-report-02.mdx",
                )
                crash = self.cli(
                    "decide", "demo", owner, "--reopen",
                    "--reason", "service is back up",
                    ok=False, fault=fault,
                )
                self.assertEqual(70, crash.returncode)
                journal_path = run / ".transitions" / f"{owner}.json"
                self.assertTrue(journal_path.is_file())
                # Restart in a new process and repeat without restating the reason.
                recovered = self.cli("decide", "demo", owner, "--reopen")
                self.assertIn("waiver reopened", recovered.stdout)
                self.assertEqual(
                    [f"{owner}-report-01.mdx", f"{owner}-report-02.mdx"],
                    sorted(p.name for p in run.glob(f"{owner}-report-*.mdx")),
                )
                journal = json.loads(journal_path.read_text())
                self.assertEqual("complete", journal["state"])
                self.assertEqual("reopen-waived", journal["verdict"])
                self.assertEqual(1, journal["round"])
                self.assertEqual("service is back up", journal["reopen_reason"])
                self.assertTrue(str(journal["transition"]).startswith("txn:"))
                self.assertEqual(rep1_before, (run / f"{owner}-report-01.mdx").read_bytes())
                self.assertEqual(dec1_before, (run / f"{owner}-decision-01.mdx").read_bytes())
                self.assertIn("waived", (run / f"{owner}-report-01.mdx").read_text())
                # Repeating the completed reopen never opens a third round.
                again = self.cli("decide", "demo", owner, "--reopen")
                self.assertIn("waiver reopened", again.stdout)
                self.assertEqual(
                    2, len(list(run.glob(f"{owner}-report-*.mdx"))),
                )
                self.assertEqual(journal["transition"], json.loads(journal_path.read_text())["transition"])
                # The recorded reason is immutable.
                wrong = self.cli(
                    "decide", "demo", owner, "--reopen",
                    "--reason", "a different reason",
                    ok=False,
                )
                self.assertNotEqual(0, wrong.returncode)
                self.assertIn("already recorded the reason", wrong.stderr)

    def test_reopen_recovery_preserves_evidence_reclaims_scope_and_breaks_aggregate(self) -> None:
        """Crash recovery keeps prior evidence, reclaims scope, and stales the aggregate."""
        run = self.setup_aggregate_run()
        self.assign("T03", file="src/c.py")
        self.fill_task(run / "T03-task.mdx")
        self.fill_task_report(run / "T03-report-01.mdx", blocked=True,
                              files="- `src/c.py:1` - blocked change.")
        self.cli("submit", "demo", "T03", "--blocked")
        self.cli("decide", "demo", "T03", "--waive", "--reason", "external outage")
        self.assign("orch", executor="orchestrator")
        agg_report = run / "orch-report-01.mdx"
        self.fill_orch_report(agg_report)
        self.cli("submit", "demo", "orch", "--skip-verify")
        first_agg, _ = self.aggregate()
        first_digest = first_agg["digest"]
        rep1_before = (run / "T03-report-01.mdx").read_bytes()
        dec1_before = (run / "T03-decision-01.mdx").read_bytes()

        prior_bin = os.environ.get("DOCKET_BIN_PRIOR")
        if prior_bin:
            prior_crash = subprocess.run(
                [sys.executable, prior_bin, "decide", "demo", "T03", "--reopen",
                 "--reason", "service is back up"],
                cwd=str(self.root), text=True, capture_output=True,
                env={**dict(os.environ), "DOCKET_FAULT": "transition:next-round"},
            )
            self.assertEqual(70, prior_crash.returncode)
            prior_retry = subprocess.run(
                [sys.executable, prior_bin, "decide", "demo", "T03", "--reopen"],
                cwd=str(self.root), text=True, capture_output=True,
            )
            self.assertNotEqual(0, prior_retry.returncode)
            self.assertIn("not waived", prior_retry.stderr)
            # Remove the prior binary's draft and journal so the fixed binary
            # proves recovery from the same boundary on the same run.
            (run / "T03-report-02.mdx").unlink(missing_ok=True)
            (run / ".transitions" / "T03.json").unlink(missing_ok=True)

        crash = self.cli(
            "decide", "demo", "T03", "--reopen",
            "--reason", "service is back up",
            ok=False, fault="transition:next-round",
        )
        self.assertEqual(70, crash.returncode)
        self.assertTrue((run / "T03-report-02.mdx").is_file())
        self.assertIn("unfinished decision transitions", self.cli("status", "demo").stdout)
        # New process, no restated reason.
        recovered = self.cli("decide", "demo", "T03", "--reopen")
        self.assertIn("waiver reopened", recovered.stdout)
        self.assertEqual(rep1_before, (run / "T03-report-01.mdx").read_bytes())
        self.assertEqual(dec1_before, (run / "T03-decision-01.mdx").read_bytes())
        journal = json.loads((run / ".transitions" / "T03.json").read_text())
        self.assertEqual("complete", journal["state"])
        self.assertEqual("service is back up", journal["reopen_reason"])
        # Scope is reclaimed: overlapping active work now collides.
        self.cli(
            "assign", "demo", "T04", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/c.py",
            "--verify", 'printf "T04 ok\\n"',
        )
        self.fill_task(run / "T04-task.mdx")
        self.fill_scope(run / "T04-scope.mdx")
        collision = self.cli("scope", "demo", "T04", "--submit", ok=False)
        self.assertNotEqual(0, collision.returncode)
        self.assertIn("COLLISION", (collision.stdout + collision.stderr).upper())
        # The aggregate that pinned the waiver is stale and names both sides.
        status = self.cli("status", "demo").stdout
        self.assertIn("aggregate bundle staleness", status)
        self.assertIn("reopened from waiver", status)
        self.assertIn("T03", status)
        events = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertIn("T03:reopened", events)
        agg_list = self.cli("bundle", "demo", "orch", "--list").stdout
        self.assertIn("DAMAGED", agg_list)
        self.assertIn(first_digest, agg_list)
        # Two concurrent retries settle as one reopen, not a third round.
        import concurrent.futures as futures

        def retry() -> subprocess.CompletedProcess[str]:
            env = dict(os.environ)
            env.pop("DOCKET_FAULT", None)
            env.pop("DOCKET_PERTURB", None)
            return subprocess.run(
                [sys.executable, str(DOCKET), "decide", "demo", "T03", "--reopen"],
                cwd=str(self.root), text=True, capture_output=True, env=env,
            )

        with futures.ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: retry(), range(2)))
        self.assertEqual(0, first.returncode)
        self.assertEqual(0, second.returncode)
        self.assertEqual(
            ["T03-report-01.mdx", "T03-report-02.mdx"],
            sorted(p.name for p in run.glob("T03-report-*.mdx")),
        )
        self.assertEqual(
            journal["transition"],
            json.loads((run / ".transitions" / "T03.json").read_text())["transition"],
        )

    # --------------------------------------- leased delivery and reconciliation

    def submit_simple(self, run: Path, owner: str) -> None:
        """Assign, scope, and submit one task in a documents-only run."""
        self.assign_simple(run, owner)
        self.fill_task_report(run / f"{owner}-report-01.mdx",
                              files=f"- `src/{owner.lower()}.py:1` - implemented {owner}.")
        self.cli("submit", "demo", owner)

    def assign_simple(self, run: Path, owner: str) -> None:
        """Assign and scope one task without submitting it."""
        fname = f"src/{owner.lower()}.py"
        self.cli(
            "assign", "demo", owner, "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", fname,
            "--verify", f'printf "{owner} ok\\n"',
        )
        self.fill_task(run / f"{owner}-task.mdx")
        self.fill_scope(run / f"{owner}-scope.mdx")
        self.cli("scope", "demo", owner, "--submit")

    @staticmethod
    def delivery_snapshot(run: Path) -> dict[str, bytes]:
        """Every delivery file's bytes, to prove read-only commands change nothing."""
        out: dict[str, bytes] = {}
        for path in sorted((run / ".delivery").rglob("*")):
            if path.is_file():
                out[str(path.relative_to(run))] = path.read_bytes()
        return out

    def test_m5_derivation_and_reconcile_recovery(self) -> None:
        """Deterministic events: reconcile restores a deleted record exactly once."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        reconciled = self.cli("reconcile", "demo")
        self.assertIn("1 derived event(s), 0 retired", reconciled.stdout)
        pending = list((run / ".delivery" / "orchestrator" / "pending").glob("*.json"))
        self.assertEqual(1, len(pending))
        record = json.loads(pending[0].read_text())
        self.assertEqual("orchestrator", record["role"])
        self.assertEqual("legacy", record["workflow"])
        self.assertEqual(1, record["generation"])
        self.assertTrue(str(record["revision"]).startswith("sha256:"))
        self.assertTrue(str(record["key"]).startswith("review-batch:"))
        # Delete only the derived record; the submitted report survives.
        pending[0].unlink()
        self.assertIn("submitted", (run / "T01-report-01.mdx").read_text())
        restored = self.cli("reconcile", "demo")
        self.assertIn("1 derived event(s), 0 retired", restored.stdout)
        again = list((run / ".delivery" / "orchestrator" / "pending").glob("*.json"))
        self.assertEqual(1, len(again))
        self.assertEqual(record["key"], json.loads(again[0].read_text())["key"])
        # Repeated and concurrent reconciliation never duplicates the identity.
        self.cli("reconcile", "demo")
        import concurrent.futures as futures
        with futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.cli("reconcile", "demo"), range(4)))
        self.assertEqual(
            1, len(list((run / ".delivery" / "orchestrator" / "pending").glob("*.json"))))
        # Peek, status, and doctor leave event and lease files byte-for-byte.
        before = self.delivery_snapshot(run)
        self.assertTrue(before)
        self.cli("events", "demo", "--role", "orchestrator", "--peek")
        self.cli("status", "demo")
        self.cli("doctor")
        self.assertEqual(before, self.delivery_snapshot(run))

    def test_m5_claim_lease_receipt_retry_and_completion(self) -> None:
        """One lease per event; ack is receipt; resolution retires; duplicates harmless."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("reconcile", "demo")
        key = json.loads(next(
            (run / ".delivery" / "orchestrator" / "pending").glob("*.json")).read_text())["key"]
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch-main", "--role", "orchestrator")
        first = self.cli("inbox", "demo", "--role", "orchestrator",
                         "--claim", "--session", "s1")
        self.assertIn(f"claimed {key}", first.stdout)
        self.assertIn("attempt 1", first.stdout)
        # The same session re-claiming is idempotent; a competitor is refused.
        self.assertIn("already held by this session",
                      self.cli("inbox", "demo", "--role", "orchestrator",
                               "--claim", "--session", "s1").stdout)
        self.cli("session", "demo", "--register", "--session", "s2",
                 "--name", "orch-other", "--role", "orchestrator")
        rival = self.cli("inbox", "demo", "--role", "orchestrator",
                         "--claim", "--session", "s2", ok=False)
        self.assertNotEqual(0, rival.returncode)
        self.assertEqual(
            1, len(list((run / ".delivery" / "orchestrator" / "leases").glob("*.json"))))
        # Acknowledgement is receipt, not completion; duplicates are harmless.
        self.assertIn("received",
                      self.cli("events", "demo", "--role", "orchestrator",
                               "--ack", key, "--session", "s1").stdout)
        self.assertIn("already received",
                      self.cli("events", "demo", "--role", "orchestrator",
                               "--ack", key, "--session", "s1").stdout)
        self.assertTrue((run / ".delivery" / "orchestrator" / "pending" /
                         next((run / ".delivery" / "orchestrator" / "pending").glob("*.json")).name).is_file())
        # A retry with a reason releases the lease; the next claim continues it.
        self.assertIn("attempted again",
                      self.cli("events", "demo", "--role", "orchestrator",
                               "--retry", key, "--reason", "lost wake").stdout)
        self.assertIn("attempt 2", self.cli(
            "inbox", "demo", "--role", "orchestrator",
            "--claim", "--session", "s1").stdout)
        # Lease expiry makes the event claimable again for another session.
        lease_file = next((run / ".delivery" / "orchestrator" / "leases").glob("*.json"))
        until = json.loads(lease_file.read_text())["lease_until"]
        os.environ["DOCKET_NOW"] = str(float(until) + 1)
        self.addCleanup(os.environ.pop, "DOCKET_NOW", None)
        redelivered = self.cli("inbox", "demo", "--role", "orchestrator",
                               "--claim", "--session", "s2")
        self.assertIn(f"claimed {key}", redelivered.stdout)
        os.environ.pop("DOCKET_NOW", None)
        # A durable resolution retires the claimed event; the next actionable
        # step (completing the aggregate) is derived in its place.
        self.cli("decide", "demo", "T01", "--approve")
        swept = self.cli("reconcile", "demo")
        self.assertIn("1 retired", swept.stdout)
        remaining = [json.loads(p.read_text())["key"] for p in
                     (run / ".delivery" / "orchestrator" / "pending").glob("*.json")]
        self.assertNotIn(key, remaining)
        self.assertTrue(any(k.startswith("all:decided:") for k in remaining))
        # Duplicate handling after completion is harmless, not a second decision.
        self.assertIn("already retired", self.cli(
            "events", "demo", "--role", "orchestrator",
            "--ack", key, "--session", "s2").stdout)
        self.assertEqual(1, len(list(run.glob("T01-decision-*.mdx"))))

    def test_m5_session_registration_identity(self) -> None:
        """Stale generations and wrong roles, runs, or workspaces cannot inherit work."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("reconcile", "demo")
        key = json.loads(next(
            (run / ".delivery" / "orchestrator" / "pending").glob("*.json")).read_text())["key"]
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch-main", "--role", "orchestrator")
        self.cli("inbox", "demo", "--role", "orchestrator", "--claim", "--session", "s1")
        # Wrong role: a planner session cannot claim the orchestrator inbox.
        self.cli("session", "demo", "--register", "--session", "sp",
                 "--name", "planner-main", "--role", "planner")
        wrong_role = self.cli("inbox", "demo", "--role", "orchestrator",
                              "--claim", "--session", "sp", ok=False)
        self.assertNotEqual(0, wrong_role.returncode)
        self.assertIn("role", wrong_role.stderr)
        # Restart reuses the id with a new generation; the old lease must fail.
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch-restarted", "--role", "orchestrator")
        reg = json.loads((self.root / ".docket" / "sessions" / "s1.json").read_text())
        self.assertEqual(2, reg["generation"])
        self.assertEqual("orch-main", reg["previous_name"])
        stale_ack = self.cli("events", "demo", "--role", "orchestrator",
                             "--ack", key, "--session", "s1", ok=False)
        self.assertNotEqual(0, stale_ack.returncode)
        self.assertIn("generation", stale_ack.stderr)
        # A restarted session cannot steal the active lease before expiry.
        steal = self.cli("inbox", "demo", "--role", "orchestrator",
                         "--claim", "--session", "s1", ok=False)
        self.assertNotEqual(0, steal.returncode)
        # Wrong workspace registration cannot claim here.
        reg_path = self.root / ".docket" / "sessions" / "s1.json"
        tampered = json.loads(reg_path.read_text())
        tampered["workspace"] = "/elsewhere"
        reg_path.write_text(json.dumps(tampered))
        wrong_ws = self.cli("inbox", "demo", "--role", "orchestrator",
                            "--claim", "--session", "s1", ok=False)
        self.assertNotEqual(0, wrong_ws.returncode)
        self.assertIn("workspace", wrong_ws.stderr)
        # Racing consumers still yield exactly one lease.
        tampered["workspace"] = str(self.root.resolve())
        reg_path.write_text(json.dumps(tampered))
        (run / ".delivery" / "orchestrator" / "leases" /
         next((run / ".delivery" / "orchestrator" / "leases").glob("*.json")).name).unlink()
        self.cli("session", "demo", "--register", "--session", "s3",
                 "--name", "third", "--role", "orchestrator")
        import concurrent.futures as futures
        with futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda sid: self.cli("inbox", "demo", "--role", "orchestrator",
                                     "--claim", "--session", sid, ok=False),
                ["s1", "s3"]))
        self.assertEqual(1, sum(1 for r in results if r.returncode == 0))
        self.assertEqual(
            1, len(list((run / ".delivery" / "orchestrator" / "leases").glob("*.json"))))

    def test_m5_crash_restart_matrix(self) -> None:
        """Crash before send, after receipt, and before resolution all recover."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("reconcile", "demo")
        key = json.loads(next(
            (run / ".delivery" / "orchestrator" / "pending").glob("*.json")).read_text())["key"]
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch-main", "--role", "orchestrator")
        # Crash after the lease write but before the claim response.
        crash = self.cli("inbox", "demo", "--role", "orchestrator",
                         "--claim", "--session", "s1",
                         ok=False, fault="delivery:before-send")
        self.assertEqual(70, crash.returncode)
        held = self.cli("inbox", "demo", "--role", "orchestrator",
                        "--claim", "--session", "s1")
        self.assertIn("already held by this session", held.stdout)
        # Crash after the receipt write but before its response.
        crash_ack = self.cli("events", "demo", "--role", "orchestrator",
                             "--ack", key, "--session", "s1",
                             ok=False, fault="delivery:receipt")
        self.assertEqual(70, crash_ack.returncode)
        self.assertIn("already received", self.cli(
            "events", "demo", "--role", "orchestrator",
            "--ack", key, "--session", "s1").stdout)
        # The event is still pending (receipt is not completion) until resolved.
        self.assertEqual(
            1, len(list((run / ".delivery" / "orchestrator" / "pending").glob("*.json"))))
        self.cli("decide", "demo", "T01", "--approve")
        self.assertIn("1 retired", self.cli("reconcile", "demo").stdout)
        remaining = [json.loads(p.read_text())["key"] for p in
                     (run / ".delivery" / "orchestrator" / "pending").glob("*.json")]
        self.assertNotIn(key, remaining)
        self.assertTrue(any(k.startswith("all:decided:") for k in remaining))

    # -------------------------------- explicit batches and health and delivery

    def test_m6_explicit_batches_and_generations(self) -> None:
        """Closed membership is stable; reopen moves exactly one new generation."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("batch", "demo", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.cli("batch", "demo", "--close", "B1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01")
        # The batch is not ready while a member is still a draft, and the
        # legacy review-batch no longer fires once closed batches exist.
        self.assertEqual([], [e for e in self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout.splitlines()
            if "batch:B1" in e or "review-batch:" in e])
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli("submit", "demo", "T02")
        ready = [e.strip() for e in self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout.splitlines()
            if "batch:B1:ready:" in e]
        self.assertEqual(1, len(ready))
        key = ready[0].split()[-1]
        # A task added after closure cannot join or change the emitted event,
        # and a dependency chain outside the closed batch does not deadlock it.
        self.submit_simple(run, "T03")
        self.assertIn("B1  closed", self.cli("batch", "demo", "--list").stdout)
        self.assertEqual(key, [e.strip().split()[-1] for e in self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout.splitlines()
            if "batch:B1:ready:" in e][0])
        # Reopen of a constituent invalidates the old generation exactly once.
        fname = "src/t04.py"
        self.cli("assign", "demo", "T04", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", fname,
                 "--verify", 'printf "T04 ok\\n"')
        self.fill_task(run / "T04-task.mdx")
        self.fill_scope(run / "T04-scope.mdx")
        self.cli("scope", "demo", "T04", "--submit")
        self.fill_task_report(run / "T04-report-01.mdx", blocked=True,
                              files="- `src/t04.py:1` - blocked change.")
        self.cli("submit", "demo", "T04", "--blocked")
        self.cli("decide", "demo", "T04", "--waive", "--reason", "external outage")
        self.cli("batch", "demo", "--create", "B2", "--members", "T03,T04")
        self.cli("batch", "demo", "--close", "B2")
        old_ready = [e.strip().split()[-1] for e in self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout.splitlines()
            if "batch:B2:ready:" in e]
        self.assertEqual(1, len(old_ready))
        self.assertIn(":ready:1", old_ready[0])
        self.cli("reconcile", "demo")
        self.cli("decide", "demo", "T04", "--reopen", "--reason", "service is back")
        listed = self.cli("batch", "demo", "--list").stdout
        self.assertIn("B2", listed)
        self.assertIn("generation=2", listed)
        swept = self.cli("reconcile", "demo")
        self.assertIn("1 retired", swept.stdout)
        now = [e.strip().split()[-1] for e in self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout.splitlines()
            if "batch:B2:ready:" in e]
        self.assertEqual([], now)
        self.assertNotIn("batch:B2:ready:3", swept.stdout + self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout)

    def test_m6_execution_health_separate_from_report(self) -> None:
        """Quiet work is healthy; rate limits are hints; one stall is one incident."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        healthy = self.cli("health", "demo").stdout
        self.assertIn("report=submitted", healthy)
        self.assertIn("execution=awaiting-review", healthy)
        self.assertNotIn("open stall incidents", healthy)
        # Long verification and unchanged worktrees are not stalls by themselves.
        self.submit_simple(run, "T02")
        quiet = self.cli("health", "demo", "--owner", "T02").stdout
        self.assertIn("execution=awaiting-review", quiet)
        self.assertEqual([], list((run / ".incidents").glob("*.json"))
                         if (run / ".incidents").is_dir() else [])
        # Rate-limit text is a best-effort hint, never authoritative state.
        fname = "src/t03.py"
        self.cli("assign", "demo", "T03", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", fname,
                 "--verify", 'printf "Rate limit 429 retrying\\n"; printf "T03 ok\\n"')
        self.fill_task(run / "T03-task.mdx")
        self.fill_scope(run / "T03-scope.mdx")
        self.cli("scope", "demo", "T03", "--submit")
        self.fill_task_report(run / "T03-report-01.mdx",
                              files="- `src/t03.py:1` - implemented T03.")
        self.cli("submit", "demo", "T03")
        hinted = self.cli("health", "demo", "--owner", "T03").stdout
        self.assertIn("[hint]", hinted)
        self.assertIn("execution=awaiting-review", hinted)
        # One stall yields one incident and one recovery event, deduplicated.
        self.cli("health", "demo", "--flag-stall", "T01", "--cause", "provider hung")
        self.cli("health", "demo", "--flag-stall", "T01", "--cause", "provider hung")
        incidents = list((run / ".incidents").glob("*.json"))
        self.assertEqual(1, len(incidents))
        stalled = [e.strip().split()[-1] for e in self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout.splitlines()
            if ":stalled:" in e]
        self.assertEqual(1, len(stalled))
        self.assertIn("T01:stalled:1", stalled[0])
        status_out = self.cli("status", "demo").stdout
        self.assertIn("exec", status_out)
        self.assertIn("awaiting-review", status_out)
        self.cli("health", "demo", "--resolve-stall", "T01-1")
        self.cli("reconcile", "demo")
        self.assertEqual([], [e for e in self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout.splitlines()
            if ":stalled:" in e])

    def test_m6_pause_resume_probe_and_queued(self) -> None:
        """Paused delivery queues visibly without consuming events or work."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("delivery", "demo", "--role", "orchestrator", "--pause")
        watched = subprocess.run(
            [sys.executable, str(DOCKET), "watch", "demo",
             "--role", "orchestrator", "--timeout", "2"],
            cwd=str(self.root), text=True, capture_output=True)
        self.assertEqual(0, watched.returncode)
        peek = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertIn("1 pending", peek)
        queued = self.cli("delivery", "demo", "--role", "orchestrator", "--queued").stdout
        self.assertIn("paused", queued)
        self.assertIn("1 queued", queued)
        # Pausing delivery neither consumes events nor pauses implementation.
        self.assertEqual([], list((run / ".delivery" / "orchestrator" / "leases").glob("*.json"))
                         if (run / ".delivery" / "orchestrator" / "leases").is_dir() else [])
        self.submit_simple(run, "T02")
        probe = self.cli("delivery", "demo", "--role", "orchestrator", "--probe").stdout
        self.assertIn("boundary:", probe)
        self.assertIn("mode:", probe)
        self.assertIn("doctor", self.cli("doctor").stdout.lower())
        self.assertIn("Delivery", self.cli("doctor").stdout)
        self.cli("delivery", "demo", "--role", "orchestrator", "--resume")
        fired = subprocess.run(
            [sys.executable, str(DOCKET), "watch", "demo",
             "--role", "orchestrator", "--timeout", "5"],
            cwd=str(self.root), text=True, capture_output=True)
        self.assertEqual(2, fired.returncode)

    def test_m6_safe_boundary_delivery_matrix(self) -> None:
        """Unsafe delivery stays queued; qualified delivery is fixed-format."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        peek = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        key = [l.strip().split()[-1] for l in peek.splitlines() if "review-batch:" in l][0]
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch-main", "--role", "orchestrator")
        body_before = self.body_of(run / "T01-report-01.mdx")
        # The user typing while delivery is eligible: nothing enters the buffer.
        (run / ".delivery" / "orchestrator" / "input-active").parent.mkdir(
            parents=True, exist_ok=True)
        (run / ".delivery" / "orchestrator" / "input-active").write_text("typing\n")
        held_back = self.cli("delivery", "demo", "--role", "orchestrator",
                             "--send", key, "--session", "s1").stdout
        self.assertIn("queued", held_back)
        self.assertEqual(
            [], list((run / ".delivery" / "orchestrator" / "outbox").glob("*.txt")))
        # Explicit inbox pickup still succeeds through the qualified boundary.
        self.assertIn(f"claimed {key}", self.cli(
            "inbox", "demo", "--role", "orchestrator",
            "--claim", "--session", "s1").stdout)
        (run / ".delivery" / "orchestrator" / "input-active").unlink()
        (run / ".delivery" / "orchestrator" / "leases" /
         next((run / ".delivery" / "orchestrator" / "leases").glob("*.json")).name).unlink()
        sent = self.cli("delivery", "demo", "--role", "orchestrator",
                        "--send", key, "--session", "s1")
        self.assertEqual(0, sent.returncode)
        outbox = list((run / ".delivery" / "orchestrator" / "outbox").glob("*.txt"))
        self.assertEqual(1, len(outbox))
        notice = outbox[0].read_text()
        self.assertEqual(
            ["run: demo", "role: orchestrator", f"event: {key}", "session: s1",
             "inbox: docket inbox demo --role orchestrator --claim --session s1"],
            notice.strip().splitlines())
        self.assertNotIn(body_before.strip().splitlines()[0], notice)
        # A restarted session reusing the pane cannot inherit the delivery.
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch-reused-pane", "--role", "orchestrator")
        stale_send = self.cli("delivery", "demo", "--role", "orchestrator",
                              "--send", key, "--session", "s1", ok=False)
        self.assertNotEqual(0, stale_send.returncode)
        # A transport failure never rolls back the valid report submission.
        leases = list((run / ".delivery" / "orchestrator" / "leases").glob("*.json"))
        self.assertEqual(1, len(leases))
        import json as _json
        until = _json.loads(leases[0].read_text())["lease_until"]
        os.environ["DOCKET_NOW"] = str(float(until) + 1)
        self.addCleanup(os.environ.pop, "DOCKET_NOW", None)
        crashed = self.cli("delivery", "demo", "--role", "orchestrator",
                           "--send", key, "--session", "s1",
                           ok=False, fault="delivery:send")
        self.assertEqual(70, crashed.returncode)
        os.environ.pop("DOCKET_NOW", None)
        self.assertIn("submitted", (run / "T01-report-01.mdx").read_text())
        retried = self.cli("delivery", "demo", "--role", "orchestrator",
                           "--send", key, "--session", "s1")
        self.assertEqual(0, retried.returncode)
        self.assertEqual(1, len(list((run / ".delivery" / "orchestrator" / "outbox").glob("*.txt"))))
        # Killing the hook leaves the durable event recoverable for the next
        # hook or reconciliation pass: the pending record survives and stays
        # claimable, even though the announcement ledger already holds the key.
        proc = subprocess.Popen(
            [sys.executable, str(DOCKET), "watch", "demo",
             "--role", "orchestrator", "--timeout", "30"],
            cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        import time as _time
        _time.sleep(1)
        proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()
        self.assertEqual(
            1, len(list((run / ".delivery" / "orchestrator" / "pending").glob("*.json"))))
        self.assertIn("1 derived event(s)", self.cli("reconcile", "demo").stdout)
        held = self.cli("inbox", "demo", "--role", "orchestrator",
                        "--claim", "--session", "s1")
        self.assertTrue("already held" in held.stdout or f"claimed {key}" in held.stdout)

    # -------------------------------- captured verification and acceptance

    def test_m7_verification_artifact_is_complete(self) -> None:
        """Frozen verification captures command, timing, outputs, env, and model."""
        run = self.init("split", evidence_mode="documents-only")
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/a.py",
                 "--verify", 'printf "2 passed, 1 skipped in 0.1s\\n"',
                 "--model", "req-m", "--env", "FOO=1", "--env", "BAR=2")
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.cli("set-model", "demo", "T01", "--actual", "obs-m")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        verifying = self.cli("bundle", "demo", "T01").stdout
        self.assertIn("passed", verifying)
        manifest, where = self.frozen("T01")
        verification = manifest["verification"]
        self.assertTrue(verification["command"].startswith("printf"))
        self.assertEqual(str(self.root.resolve()), verification["cwd"])
        self.assertEqual(900, verification["timeout_seconds"])
        self.assertEqual(0, verification["returncode"])
        self.assertEqual("passed", verification["status"])
        self.assertIn("started_at", verification)
        self.assertIn("ended_at", verification)
        self.assertIn("duration_seconds", verification)
        self.assertEqual(["BAR=2", "FOO=1"], verification["declared_env"])
        self.assertEqual("req-m", verification["model_requested"])
        self.assertEqual("obs-m", verification["model_observed"])
        self.assertEqual("observed", verification["model_source"])
        self.assertEqual("pytest", verification["framework"]["parser"])
        self.assertEqual(2, verification["framework"]["passed"])
        self.assertEqual(1, verification["framework"]["skipped"])
        self.assertIn("2 passed", (where / "verify.stdout").read_text())
        # Unknown models and unparsable output stay unknown, never guessed.
        self.cli("assign", "demo", "T02", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/b.py",
                 "--verify", 'printf hello')
        self.fill_task(run / "T02-task.mdx")
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        self.fill_task_report(run / "T02-report-01.mdx", files="- `src/b.py:1` - change.")
        self.cli("submit", "demo", "T02")
        manifest, _ = self.frozen("T02")
        self.assertEqual("unknown", manifest["verification"]["model_observed"])
        self.assertEqual("unknown", manifest["verification"]["model_source"])
        self.assertEqual("unknown", manifest["verification"]["framework"]["parser"])
        # A timeout is captured as a timeout, not a pass or a failure.
        self.cli("assign", "demo", "T03", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/c.py",
                 "--verify", "sleep 5; printf ok", "--verify-timeout", "1")
        self.fill_task(run / "T03-task.mdx")
        self.fill_scope(run / "T03-scope.mdx")
        self.cli("scope", "demo", "T03", "--submit")
        self.fill_task_report(run / "T03-report-01.mdx", files="- `src/c.py:1` - change.")
        refused = self.cli("submit", "demo", "T03", ok=False)
        self.assertIn("timed out", refused.stderr)

    def test_m7_verification_reruns_on_every_identity_change(self) -> None:
        """No frozen result is ever reused across a changed command, env, or source."""
        run = self.init("split", evidence_mode="documents-only")
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/a.py",
                 "--verify", "cat counter.txt 2>/dev/null; printf x >> counter.txt; printf ok",
                 "--env", "FOO=1")
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        first, _ = self.frozen("T01")
        self.assertEqual(["FOO=1"], first["verification"]["declared_env"])
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        self.cli("decide", "demo", "T01", "--changes")
        # Change the command and the declared env between rounds.
        task_text = (run / "T01-task.mdx").read_text()
        task_text = task_text.replace(
            "cat counter.txt 2>/dev/null; printf x >> counter.txt; printf ok",
            "cat counter.txt 2>/dev/null; printf y >> counter.txt; printf ok2")
        task_text = task_text.replace("env: FOO=1", "env: FOO=2")
        (run / "T01-task.mdx").write_text(task_text)
        self.fill_task_report(run / "T01-report-02.mdx")
        self.cli("submit", "demo", "T01")
        second, _ = self.frozen("T01", index=1)
        self.assertNotEqual(first["verification"]["command"],
                            second["verification"]["command"])
        self.assertIn("ok2", second["verification"]["command"])
        self.assertEqual(["FOO=2"], second["verification"]["declared_env"])
        self.assertNotEqual(first["report"]["body_digest"], second["report"]["body_digest"])
        # The counter proves both rounds really executed instead of reusing.
        self.assertEqual("xy", (self.root / "counter.txt").read_text())

    def test_m7_source_modifying_verify_is_refused(self) -> None:
        """A green result that no longer describes the frozen source is refused."""
        self.repo()
        self.init("split", evidence_mode="git")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/a.py",
                 "--verify", 'printf "\\ndirty\\n" >> src/a.py; printf ok')
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.fill_task_report(run / "T01-report-01.mdx")
        refused = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("changed while the verification ran", refused.stderr)

    def test_m7_evidence_table_states_and_prose(self) -> None:
        """Stable IDs map to evidence; prose is never keyword-policed."""
        run = self.init("split", evidence_mode="documents-only")
        table = ("| id | state | artifacts | gaps |\n"
                 "| --- | --- | --- | --- |\n"
                 "| A1 | met | verify.stdout | none |\n"
                 "| A2 | met | verify.stdout | none |\n")

        def two_criteria(owner: str, table_text: str = "", blocked: bool = False,
                         extra: str = "") -> "subprocess.CompletedProcess[str]":
            fname = f"src/{owner.lower()}.py"
            self.cli("assign", "demo", owner, "--complexity", "high",
                     "--executor", "implementor", "--harness", "opencode",
                     "--file", fname, "--verify", 'printf ok')
            task_text = (run / f"{owner}-task.mdx").read_text()
            task_text = task_text.replace("# Task T01: <title>", f"# Task {owner}: Feature")
            task_text = task_text.replace(
                "<!-- TODO: one paragraph. What must be true when this is done. -->",
                "Implement the feature.")
            task_text = task_text.replace(
                "- [ ] <!-- TODO -->", "- [ ] First works.\n- [ ] Second works.")
            (run / f"{owner}-task.mdx").write_text(task_text)
            self.fill_scope(run / f"{owner}-scope.mdx")
            self.cli("scope", "demo", owner, "--submit")
            report = run / f"{owner}-report-01.mdx"
            text = report.read_text()
            text = text.replace(
                "<!-- TODO: what you actually did, 2-4 sentences. No plans, only past tense. -->",
                "Implemented the feature and verified its behavior.")
            text = text.replace(
                '<!-- TODO: one bullet per change as `path/to/file.py:120` plus a short note. Write "none" if nothing changed. -->',
                f"- `{fname}:1` - implemented the feature.")
            text = text.replace("- [ ] <!-- TODO -->",
                                f"- [{' ' if blocked else 'x'}] First works.\n"
                                f"- [{' ' if blocked else 'x'}] Second works.")
            text = text.replace(
                "<!-- TODO: the exact command you ran and its real output. Never claim a result you did not see. -->",
                "Command: `printf ok`\n\nOutput: `ok`")
            if blocked:
                text = text.replace("none\n\n## Notes",
                                    "The reviewer must decide whether to waive.\n\n## Notes")
            if table_text:
                text = text.rstrip("\n") + "\n\n## Evidence\n\n" + table_text
            if extra:
                text = text.rstrip("\n") + "\n\n" + extra
            report.write_text(text)
            return self.cli("submit", "demo", owner, *(["--blocked"] if blocked else []),
                            ok=False)

        # Every required ID met with artifacts and no gaps submits.
        self.assertEqual(0, two_criteria("T01", table).returncode)
        # Duplicate, absent, unknown IDs, unknown states, gaps on met,
        # missing artifacts, and stale links are hard failures.
        cases = {
            "T02": table.replace("| A2 | met", "| A1 | met"),
            "T03": "| id | state | artifacts | gaps |\n| --- | --- | --- | --- |\n"
                   "| A1 | met | verify.stdout | none |\n",
            "T04": table.replace("| A2 | met", "| A9 | met"),
            "T05": table.replace("| A2 | met | verify.stdout | none |",
                                 "| A2 | done | verify.stdout | none |"),
            "T06": table.replace("| A2 | met | verify.stdout | none |",
                                 "| A2 | met | verify.stdout | slow path untested |"),
            "T07": table.replace("| A2 | met | verify.stdout | none |",
                                 "| A2 | met | none | none |"),
            "T08": table.replace("| A2 | met | verify.stdout | none |",
                                 "| A2 | met | src/nope-missing.py | none |"),
        }
        for owner, broken in cases.items():
            with self.subTest(owner=owner):
                result = two_criteria(owner, broken)
                self.assertNotEqual(0, result.returncode, owner)
        self.assertIn("duplicate evidence id A1", two_criteria("T09", cases["T02"]).stderr)
        self.assertIn("missing evidence for required criterion A2",
                      two_criteria("T10", cases["T03"]).stderr)
        self.assertIn("not a task acceptance criterion", two_criteria("T11", cases["T04"]).stderr)
        self.assertIn("unknown state", two_criteria("T12", cases["T05"]).stderr)
        self.assertIn("explicit gap attached to met criterion A2",
                      two_criteria("T13", cases["T06"]).stderr)
        self.assertIn("names no artifacts", two_criteria("T14", cases["T07"]).stderr)
        self.assertIn("missing evidence artifact", two_criteria("T15", cases["T08"]).stderr)
        # Prose counterexamples are not keyword-policed.
        prose = ("## Notes\n\nNo limitations.\n\nThe edge is not covered by this unit "
                 "test but covered by integration run nightly-42.\n")
        self.assertEqual(0, two_criteria("T16", table, extra=prose).returncode)
        # Blocked work stays lightweight: partial evidence never blocks a block.
        partial = table.replace("| A2 | met | verify.stdout | none |",
                                "| A2 | partial | verify.stdout | waiting on data |")
        self.assertEqual(0, two_criteria("T17", partial, blocked=True).returncode)
        # Regression: a checked box beside a not-met row contradicted itself and passed.
        for state in ("partial", "not-met", "not-verified"):
            with self.subTest(state=state):
                owner = {"partial": "T18", "not-met": "T19", "not-verified": "T20"}[state]
                short = table.replace("| A2 | met | verify.stdout | none |",
                                      f"| A2 | {state} | verify.stdout | slow path untested |")
                refused = two_criteria(owner, short)
                self.assertNotEqual(0, refused.returncode)
                self.assertIn(f"evidence A2 is {state}, but a normal submission needs every "
                              "criterion met", refused.stderr)

    def test_m7_adversarial_diff_and_oracle(self) -> None:
        """Byte changes count, and a changed oracle cannot inherit verification."""
        # A byte change with an unchanged status shape is still a change.
        self.repo()
        self.init("split", evidence_mode="git")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/a.py", "--verify", 'printf ok')
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        diff = self.cli("diff", "demo", "T01")
        self.assertEqual(0, diff.returncode)
        self.assertIn("src/a.py", diff.stdout)
        self.assertNotIn("no worktree changes", diff.stdout)

    def test_m7_changed_oracle_needs_reverification(self) -> None:
        """A changed oracle (edited task) cannot inherit the frozen verification."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(
            task_text.replace("- [ ] The feature works.", "- [ ] The feature works quickly."))
        stale = self.cli("decide", "demo", "T01", "--approve", ok=False)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("task contract changed", stale.stderr)
        # A fresh changes round against the new oracle decides cleanly.
        self.cli("decide", "demo", "T01", "--changes")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT,
        ))
        self.cli("decide", "demo", "T01", "--changes")
        report = run / "T01-report-02.mdx"
        self.fill_task_report(report, criterion="The feature works quickly.")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--approve")


    def init5(self) -> Path:
        """A five-role-v1 run; legacy behavior is never the default here."""
        self.cli("init", "demo", "--harness", "claude", "--topology", "split",
                 "--evidence-mode", "documents-only", "--workflow", "five-role-v1")
        return self.root / ".docket" / "runs" / "demo"

    def submit5(self, run: Path, owner: str) -> None:
        """Assign, scope, and submit one task as its implementor."""
        self.assign_simple(run, owner)
        self.fill_task_report(run / f"{owner}-report-01.mdx",
                              files=f"- `src/{owner.lower()}.py:1` - implemented {owner}.")
        self.cli("submit", "demo", owner, "--as", "implementor")

    def test_m8_authority_enforcement(self) -> None:
        """Every forbidden cross-role operation is refused before mutation."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx")
        refused = self.cli("submit", "demo", "T01", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("requires --as implementor", refused.stderr)
        self.assertFalse((run / ".bundles" / "T01" / "rounds.json").exists())
        self.assertIn("draft", (run / "T01-report-01.mdx").read_text())
        wrong = self.cli("submit", "demo", "T01", "--as", "orchestrator", ok=False)
        self.assertNotEqual(0, wrong.returncode)
        self.cli("submit", "demo", "T01", "--as", "implementor")
        bad_verify = self.cli("verify", "demo", "T01", "--result", "pass",
                              "--as", "implementor", ok=False)
        self.assertNotEqual(0, bad_verify.returncode)
        self.assertIn("requires --as verifier", bad_verify.stderr)
        bad_approve = self.cli("decide", "demo", "T01", "--approve",
                               "--as", "verifier", ok=False)
        self.assertNotEqual(0, bad_approve.returncode)
        self.assertIn("requires --as reviewer", bad_approve.stderr)
        bad_waive = self.cli("decide", "demo", "T01", "--waive", "--reason", "x",
                             "--as", "orchestrator", ok=False)
        self.assertNotEqual(0, bad_waive.returncode)
        self.assertIn("requires --as reviewer", bad_waive.stderr)
        no_proof = self.cli("decide", "demo", "T01", "--approve",
                            "--as", "reviewer", ok=False)
        self.assertNotEqual(0, no_proof.returncode)
        self.assertIn("no passing verification", no_proof.stderr)
        self.assertEqual([], list(run.glob("T01-decision-*.mdx")))
        self.assertIn("submitted", (run / "T01-report-01.mdx").read_text())
        # Reopen is a reviewer transition too. Waive T02 first as reviewer.
        self.assign_simple(run, "T02")
        self.fill_task_report(run / "T02-report-01.mdx", blocked=True,
                              files="- `src/t02.py:1` - blocked change.")
        self.cli("submit", "demo", "T02", "--blocked", "--as", "implementor")
        self.cli("decide", "demo", "T02", "--waive", "--reason", "outage",
                 "--as", "reviewer")
        bad_reopen = self.cli("decide", "demo", "T02", "--reopen",
                              "--reason", "back", "--as", "implementor", ok=False)
        self.assertNotEqual(0, bad_reopen.returncode)
        self.assertIn("requires --as reviewer", bad_reopen.stderr)
        self.assertFalse((run / "T02-report-02.mdx").exists())

    def test_m8_verifier_pass_cannot_complete(self) -> None:
        """A verifier pass leaves the task submitted; approval binds revisions."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "outputs match the oracle")
        self.assertIn("submitted", (run / "T01-report-01.mdx").read_text())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run, "T01"))
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        attempts = sorted(p.name for p in run.glob("T01-verification-*.mdx"))
        self.assertEqual(["T01-verification-01.mdx", "T01-verification-02.mdx"], attempts)
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("verdict: approved", decision)
        self.assertIn("verification: T01-verification-02.mdx", decision)
        self.assertIn("task_revision: sha256:", decision)
        self.assertIn("bundle_digest: sha256:", decision)

    def test_m8_routing_corrections_and_escalation(self) -> None:
        """Every routing row resolves; exhausted corrections escalate once."""
        run = self.init5()
        expected = {
            "scope-change": "implementor", "collision": "orchestrator",
            "verification-defect": "implementor", "rate-limit-crash": "orchestrator",
            "requirement-conflict": "planner", "dependency-invalidated": "orchestrator",
            "dependency-plan-change": "planner", "dispute": "reviewer",
            "budget-exhausted": "reviewer", "review-defect": "implementor",
            "plan-gap": "planner", "waiver-request": "reviewer",
            "spending-request": "user",
        }
        for kind, dest in expected.items():
            with self.subTest(kind=kind):
                out = self.cli("route", "demo", "--kind", kind).stdout
                self.assertIn(f"destination: {dest}", out)
        out = self.cli("route", "demo", "--kind", "dispute", "--owner", "T01").stdout
        self.assertIn("destination: reviewer", out)
        # Correction rounds and the durable escalation on a five-role run.
        # A failed gate submission counts as a repair attempt without stranding
        # the draft: the correction still submits afterwards.
        self.assign_simple(run, "T09")
        (run / "T09-report-01.mdx").write_text(
            (run / "T09-report-01.mdx").read_text() + "\nTODO: finish\n")
        gated_submit = self.cli("submit", "demo", "T09", "--as", "implementor",
                                ok=False)
        self.assertNotEqual(0, gated_submit.returncode)
        # A refusal before handoff returns nothing to the implementor: never charged.
        self.assertFalse((run / ".corrections" / "T09.json").exists())
        (run / "T09-report-01.mdx").write_text(
            (run / "T09-report-01.mdx").read_text().replace("\nTODO: finish\n", "\n"))
        self.fill_task_report(run / "T09-report-01.mdx",
                              files="- `src/t09.py:1` - implemented T09.")
        self.cli("submit", "demo", "T09", "--as", "implementor")
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "oracle mismatch")
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run, "T01"))
        gated = self.cli("verify", "demo", "T01", "--result", "fail",
                         "--open-correction", "--as", "verifier", ok=False)
        self.assertNotEqual(0, gated.returncode)
        self.assertIn("verifier_correction: allowed", gated.stderr)
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("verifier_correction: forbidden",
                                                 "verifier_correction: allowed"))
        self.cli("verify", "demo", "T01", "--result", "fail", "--open-correction",
                 "--detail", "oracle mismatch at src/t01.py:1",
                 "--as", "verifier")
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run, "T01"))
        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("triggered_by: verifier", decision)
        self.assertIn("verification: T01-verification-03.mdx", decision)
        vmeta = parse_meta(run / "T01-verification-03.mdx")
        self.assertEqual("yes", vmeta["opened_correction"])
        # A separate chain: two reviewer corrections open; the third escalates.
        self.submit5(run, "T02")
        for rnd in (1, 2):
            self.cli("decide", "demo", "T02", "--changes", "--as", "reviewer")
            dec = run / f"T02-decision-{rnd:02d}.mdx"
            dec.write_text(dec.read_text().replace(
                "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
                self.REQUIREMENT))
            self.cli("decide", "demo", "T02", "--changes", "--as", "reviewer")
            self.fill_task_report(run / f"T02-report-{rnd + 1:02d}.mdx",
                                  files="- `src/t02.py:1` - implemented T02.")
            self.cli("submit", "demo", "T02", "--as", "implementor")
        self.cli("decide", "demo", "T02", "--changes", "--as", "reviewer")
        dec = run / "T02-decision-03.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT))
        exhausted = self.cli("decide", "demo", "T02", "--changes", "--as", "reviewer",
                             ok=False)
        self.assertNotEqual(0, exhausted.returncode)
        self.assertIn("escalation T02-r01 is open", exhausted.stderr)
        self.assertTrue((run / ".escalations" / "T02-r01.json").is_file())
        self.assertEqual(["T02-report-01.mdx", "T02-report-02.mdx", "T02-report-03.mdx"],
                         self.rounds(run, "T02"))
        again = self.cli("decide", "demo", "T02", "--changes", "--as", "reviewer",
                         ok=False)
        self.assertNotEqual(0, again.returncode)
        self.assertEqual(1, len(list((run / ".escalations").glob("*.json"))))
        self.assertIn("submitted", (run / "T02-report-03.mdx").read_text())

    def test_quick_budget_counts_one_return_and_the_coordinator_can_extend_it(self) -> None:
        """A checker's fail plus changes is one return; an exhausted chain wakes the coordinator."""
        run = self.init5_in("eg", mode="quick")
        self.assign_simple_in(run, "eg", "T01")
        self.cli("dispatch", "eg", "T01", "--session", "w1", "--register")
        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text() + "\nTODO: finish\n")
        self.cli("submit", "eg", "T01", "--as", "implementor", ok=False)
        report.write_text(report.read_text().replace("\nTODO: finish\n", "\n"))

        def submit(rnd: int) -> None:
            self.fill_task_report(run / f"T01-report-{rnd:02d}.mdx",
                                  files="- `src/t01.py:1` - implemented T01.")
            self.cli("submit", "eg", "T01", "--as", "implementor")

        def request_changes(rnd: int, ok: bool = True) -> subprocess.CompletedProcess[str]:
            self.cli("decide", "eg", "T01", "--changes", "--as", "checker")
            dec = run / f"T01-decision-{rnd:02d}.mdx"
            dec.write_text(dec.read_text().replace(
                "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
                self.REQUIREMENT))
            return self.cli("decide", "eg", "T01", "--changes", "--as", "checker", ok=ok)

        def corrections() -> dict:
            return json.loads((run / ".corrections" / "T01.json").read_text())

        submit(1)
        self.cli("verify", "eg", "T01", "--result", "fail", "--as", "checker",
                 "--detail", "1. oracle mismatch")
        self.assertFalse((run / ".corrections" / "T01.json").exists())
        # An older build charged the gate refusal and every fail; neither counts now.
        (run / ".corrections").mkdir(exist_ok=True)
        (run / ".corrections" / "T01.json").write_text(json.dumps({
            "owner": "T01", "chain_start_round": 1, "gate_repairs": 1, "verifier_returns": 1,
            "reviewer_returns": 0, "escalated": "", "counted": ["T01-verification-01.mdx"]}))
        request_changes(1)
        self.assertEqual((1, 0, 1), tuple(corrections()[k] for k in
                                          ("gate_repairs", "verifier_returns", "reviewer_returns")))
        submit(2)
        request_changes(2)
        submit(3)
        self.cli("verify", "eg", "T01", "--result", "fail", "--as", "checker",
                 "--detail", "1. still mismatched")
        refused = request_changes(3, ok=False)
        self.assertIn("escalation T01-r01 is open", refused.stderr)
        self.assertEqual(["T01:escalated:T01-r01"], self.derived_keys("eg", "coordinator"))
        self.assertIn("docket escalation eg T01 --grant 1 --as coordinator --reason TEXT",
                      self.cli("events", "eg", "--role", "coordinator", "--peek").stdout)
        self.assertNotIn("T01:3:budget-granted:T01-r01", self.derived_keys("eg", "checker"))
        denied = self.cli("escalation", "eg", "T01", "--grant", "1", "--reason", "one more",
                          "--as", "checker", ok=False)
        self.assertIn("only the coordinator extends a correction budget", denied.stderr)
        unstated = self.cli("escalation", "eg", "T01", "--grant", "1", "--reason", "one more",
                            ok=False)
        self.assertIn("requires --as coordinator", unstated.stderr)
        granted = self.cli("escalation", "eg", "T01", "--grant", "1", "--as", "coordinator",
                           "--reason", "the UTF-8 fix is worth one more round")
        self.assertIn("granted T01 1 more correction round(s): 3 used of 3", granted.stdout)
        self.assertEqual([], self.derived_keys("eg", "coordinator"))
        self.assertIn("T01:3:budget-granted:T01-r01", self.derived_keys("eg", "checker"))
        self.cli("decide", "eg", "T01", "--changes", "--as", "checker")
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx", "T01-report-03.mdx",
                          "T01-report-04.mdx"], self.rounds(run, "T01"))
        self.assertEqual(3, corrections()["reviewer_returns"])
        self.assertNotIn("T01:3:budget-granted:T01-r01", self.derived_keys("eg", "checker"))
        submit(4)
        self.cli("verify", "eg", "T01", "--result", "pass", "--as", "checker",
                 "--detail", "1. fixed")
        self.cli("decide", "eg", "T01", "--approve", "--as", "checker",
                 "--reason", "the correction holds")
        entry = json.loads((run / ".escalations" / "T01-r01.json").read_text())
        self.assertEqual("closed", entry["state"])
        self.assertNotIn("T01:escalated:T01-r01", self.derived_keys("eg", "coordinator"))

    def test_an_accepted_aggregate_wakes_the_coordinator_to_close_the_run(self) -> None:
        """The session the user talks to learns the run is complete, and runs the aggregate."""
        run = self.init5_in("fin", mode="quick")
        self.assign_simple_in(run, "fin", "T01")
        self.cli("dispatch", "fin", "T01", "--session", "w1", "--register")
        self.fill_task_report(run / "T01-report-01.mdx", files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "fin", "T01", "--as", "implementor")
        self.cli("verify", "fin", "T01", "--result", "pass", "--as", "checker", "--detail", "1. ok")
        self.cli("decide", "fin", "T01", "--approve", "--as", "checker", "--reason", "holds")
        env = {"CLAUDE_CODE_SESSION_ID": "0c1d2e3f-aaaa-bbbb-cccc-000000000009",
               "CLAUDE_PID": str(os.getpid())}
        self.cli_env(env, "assign", "fin", "orch", "--executor", "orchestrator",
                     "--verify", "printf ok")
        self.assertIn("harness: claude", (run / "orch-report-01.mdx").read_text())
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        report.write_text(report.read_text().replace("| T02 | approved | passed |\n", ""))
        self.cli("submit", "fin", "orch", "--as", "coordinator")
        self.assertNotIn("orch:1:complete", self.derived_keys("fin", "coordinator"))
        self.cli("decide", "fin", "orch", "--approve", "--as", "checker", "--reason", "delivered")
        self.assertIn("orch:1:complete", self.derived_keys("fin", "coordinator"))
        peek = self.cli("events", "fin", "--role", "coordinator", "--peek").stdout
        self.assertIn("run fin is complete: the aggregate was approved at round 1", peek)
        self.assertIn("`docket usage fin --archive` and `docket disarm fin`", peek)
        self.assertEqual([], self.derived_keys("fin", "checker"))

    def test_m8_stale_revision_blocks_verdict(self) -> None:
        """A verdict never falls back to whatever the workspace holds now."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(
            task_text.replace("- [ ] The feature works.", "- [ ] The feature works fast."))
        stale = self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer",
                         ok=False)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("re-verify after the amendment", stale.stderr)
        self.assertIn("submitted", (run / "T01-report-01.mdx").read_text())

    def test_m8_legacy_preserved_and_migrated(self) -> None:
        """Legacy runs keep their semantics until an explicit migration."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("decide", "demo", "T01", "--approve")
        self.assertIn("approved", (run / "T01-report-01.mdx").read_text())
        legacy_verify = self.cli("verify", "demo", "T01", "--result", "pass",
                                 "--as", "verifier", ok=False)
        self.assertNotEqual(0, legacy_verify.returncode)
        self.assertIn("migrate explicitly", legacy_verify.stderr)
        self.assertIn("no derived events",
                      self.cli("events", "demo", "--role", "verifier", "--peek").stdout)
        rep_before = (run / "T01-report-01.mdx").read_bytes()
        dec_before = (run / "T01-decision-01.mdx").read_bytes()
        # An interrupted migration resumes without rewriting legacy evidence.
        # The plan write is atomic, so the crash lands after it: the rerun
        # finishes the pending sweep instead of starting over.
        crash = self.cli("migrate", "demo", "--to", "five-role-v1",
                         ok=False, fault="migrate:plan")
        self.assertEqual(70, crash.returncode)
        self.assertIn("workflow: five-role-v1", (run / "plan.mdx").read_text())
        resumed = self.cli("migrate", "demo", "--to", "five-role-v1")
        self.assertIn("already five-role-v1", resumed.stdout)
        plan = (run / "plan.mdx").read_text()
        self.assertIn("workflow: five-role-v1", plan)
        self.assertIn("migrated_from: legacy", plan)
        self.assertEqual(rep_before, (run / "T01-report-01.mdx").read_bytes())
        self.assertEqual(dec_before, (run / "T01-decision-01.mdx").read_bytes())
        # The new workflow is enforced from here on.
        self.assign_simple(run, "T02")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        bare = self.cli("submit", "demo", "T02", ok=False)
        self.assertNotEqual(0, bare.returncode)
        self.assertIn("requires --as implementor", bare.stderr)
        self.cli("submit", "demo", "T02", "--as", "implementor")
        self.assertIn("already five-role-v1",
                      self.cli("migrate", "demo", "--to", "five-role-v1").stdout)

    def test_m8_verifier_reviewer_routing(self) -> None:
        """Submissions reach verifiers promptly; milestones reach the reviewer."""
        run = self.init5()
        self.submit5(run, "T01")
        self.submit5(run, "T02")
        verifier_keys = [l.strip().split()[-1] for l in self.cli(
            "events", "demo", "--role", "verifier", "--peek").stdout.splitlines()
            if ":submitted" in l]
        self.assertEqual({"T01:1:submitted", "T02:1:submitted"}, set(verifier_keys))
        self.assertNotIn("review-batch",
                         self.cli("events", "demo", "--role", "verifier", "--peek").stdout)
        self.assertIn("no derived events",
                      self.cli("events", "demo", "--role", "reviewer", "--peek").stdout)
        self.cli("batch", "demo", "--create", "M1", "--members", "T01,T02",
                 "--milestone")
        self.cli("batch", "demo", "--close", "M1")
        self.assertNotIn("batch:M1:ready:",
                         self.cli("events", "demo", "--role", "reviewer", "--peek").stdout)
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("verify", "demo", "T02", "--result", "pass", "--as", "verifier")
        reviewer_keys = [l.strip().split()[-1] for l in self.cli(
            "events", "demo", "--role", "reviewer", "--peek").stdout.splitlines()
            if "batch:M1:ready:" in l]
        self.assertEqual(1, len(reviewer_keys))
        # Exhausting corrections opens a durable escalation for the planner,
        # who owns the budget. A verifier failure left for the reviewer is not
        # itself a return, so two reviewer corrections open and the third
        # escalates.
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier")
        for rnd in (1, 2, 3):
            self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
            dec = run / f"T01-decision-{rnd:02d}.mdx"
            dec.write_text(dec.read_text().replace(
                "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
                self.REQUIREMENT))
            applied = self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer",
                               ok=rnd < 3)
            if rnd < 3:
                self.fill_task_report(run / f"T01-report-{rnd + 1:02d}.mdx")
                self.cli("submit", "demo", "T01", "--as", "implementor")
        self.assertNotEqual(0, applied.returncode)
        def escalated(role: str) -> list[str]:
            return [l.strip().split()[-1] for l in self.cli(
                "events", "demo", "--role", role, "--peek").stdout.splitlines()
                if ":escalated:" in l]
        self.assertEqual(["T01:escalated:T01-r01"], escalated("planner"))
        self.assertEqual([], escalated("reviewer"))

    # --------------------------------- prompts, feedback, improvements

    def prompt_text(self, run: Path, owner: str, role: str, **kwargs: str) -> str:
        result = self.cli("prompt", "demo", owner, "--role", role,
                          *[item for pair in kwargs.items() for item in
                            (f"--{pair[0].replace('_', '-')}", pair[1])])
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def test_m9_prompt_is_deterministic_bounded_and_role_correct(self) -> None:
        """Same inputs render byte-identical prompts with digests and no filler."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        first = self.prompt_text(run, "T01", "implementor", model="unknown-model-9")
        second = self.prompt_text(run, "T01", "implementor", model="unknown-model-9")
        self.assertEqual(first, second)
        self.assertIn("Implement the assigned task", first)
        self.assertIn("task revision: sha256:", first)
        self.assertNotIn("you are", first.lower())
        self.assertNotIn("unknown-model-9 implementor", first)
        # Unknown models get no model profile, only task-relevant cards (none trigger here).
        self.assertNotIn("[muse]", first)
        self.assertNotIn("[fixture-changes]", first)
        # Relevant task text selects task cards in budget; one-run model defaults are gone.
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(task_text.replace(
            "Implement the feature.", "Fix the flaky fixture oracle under concurrency."))
        guided = self.prompt_text(run, "T01", "implementor", model="claude-opus-9")
        self.assertNotIn("[muse]", guided)
        self.assertIn("[fixture-changes]", guided)
        self.assertIn("[concurrency]", guided)
        self.assertNotIn("[deployment-config]", guided)
        self.assertNotEqual(first, guided)
        digest_line = self.cli("prompt", "demo", "T01", "--role", "implementor",
                               "--model", "claude-opus-9").stderr
        self.assertIn("prompt digest: sha256:", digest_line)
        records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
        self.assertTrue(records)
        record = json.loads(records[-1].read_text())
        self.assertTrue(record["digest"].startswith("sha256:"))
        self.assertTrue(any(r.startswith("fixture-changes:") for r in record["profile_revisions"]))
        self.assertLessEqual(record["guidance_tokens"], 600)
        # Orchestrator prompts carry routing rules and prohibitions.
        orch = self.prompt_text(run, "T01", "orchestrator")
        self.assertIn("never invent technical fixes", orch.lower())
        self.assertIn("dispute", orch.lower())

    def test_m9_feedback_is_optional_and_isolated(self) -> None:
        """Absent or failed feedback never blocks the primary workflow."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("decide", "demo", "T01", "--approve")
        listed = self.cli("feedback", "demo", "--list").stdout
        self.assertIn("no recorded observations", listed)
        self.cli("feedback", "demo", "--add", "--role", "verifier",
                 "--category", "verification", "--body", "oracle derived twice",
                 "--task", "T01", "--round", "1", "--confidence", "high")
        self.assertIn("F01", self.cli("feedback", "demo", "--list").stdout)
        # Break the feedback sink: lifecycle work still succeeds.
        import shutil
        shutil.rmtree(run / "feedback")
        (run / "feedback").write_text("not a directory")
        broken = self.cli("feedback", "demo", "--add", "--role", "implementor",
                          "--category", "other", "--body", "lost", ok=False)
        self.assertNotEqual(0, broken.returncode)
        self.submit_simple(run, "T02")
        self.cli("decide", "demo", "T02", "--approve")
        self.assertIn("approved", (run / "T02-report-01.mdx").read_text())

    def test_m9_backlog_dedups_incidents_and_filters(self) -> None:
        """Corroborating roles count once; backlog filters slice the backlog."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("feedback", "demo", "--add", "--role", "implementor",
                 "--category", "verification", "--body", "oracle unclear",
                 "--task", "T01", "--round", "1")
        self.cli("feedback", "demo", "--add", "--role", "verifier",
                 "--category", "verification", "--body", "oracle unclear too",
                 "--task", "T01", "--round", "1")
        self.cli("improvements", "--add", "--title", "unclear oracle",
                 "--category", "verification", "--evidence", "demo/T01, demo/T01")
        out = self.cli("improvements").stdout
        self.assertIn("incidents=1 runs=1", out)
        filtered = self.cli("improvements", "--category", "verification").stdout
        self.assertIn("unclear oracle", filtered)
        self.assertIn("no findings match",
                      self.cli("improvements", "--category", "delivery").stdout)
        self.assertIn("no findings match",
                      self.cli("improvements", "--status", "adopted").stdout)
        # Operational records reconcile into machine observations without dups.
        self.cli("reconcile", "demo")
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "o", "--role", "orchestrator")
        self.cli("inbox", "demo", "--role", "orchestrator", "--claim", "--session", "s1")
        self.cli("events", "demo", "--role", "orchestrator", "--retry",
                 [json.loads(p.read_text())["key"] for p in
                  (run / ".delivery" / "orchestrator" / "pending").glob("*.json")][0],
                 "--reason", "lost wake")
        self.assertIn("1 operational", self.cli("feedback", "demo", "--import-ops").stdout)
        self.assertIn("0 operational", self.cli("feedback", "demo", "--import-ops").stdout)

    def test_m9_retrospective_needs_no_model(self) -> None:
        """Retrospective output is mechanical and premium use stays unknown."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("decide", "demo", "T01", "--approve")
        out = self.cli("retrospective", "demo").stdout
        self.assertIn("mechanical summary, no model calls", out)
        self.assertIn("decisions recorded: 1", out)
        self.assertIn("premium tokens: unknown", out)
        self.assertIn("proxy", out)

    def test_m9_promotion_requires_evidence_and_never_autoedits(self) -> None:
        """Adoption links authorization, revision, and trial; nothing self-edits."""
        import hashlib
        refs = Path(__file__).parents[1] / "references"
        before = {str(p.relative_to(refs)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(refs.rglob("*")) if p.is_file()}
        self.cli("improvements", "--add", "--title", "slow gate",
                 "--category", "verification")
        refused = self.cli("improvements", "--propose", "I01", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.cli("improvements", "--propose", "I01", "--expected", "faster review",
                 "--change", "cache the gate", "--eval", "pilot task 3")
        early = self.cli("improvements", "--adopt", "I01", ok=False)
        self.assertNotEqual(0, early.returncode)
        self.cli("improvements", "--trial", "I01", "--note", "trying on one run")
        no_rev = self.cli("improvements", "--adopt", "I01", "--trial-result", "helped",
                          ok=False)
        self.assertNotEqual(0, no_rev.returncode)
        self.cli("improvements", "--adopt", "I01", "--change-rev", "abc123",
                 "--trial-result", "helped")
        self.assertIn("[adopted]", self.cli("improvements").stdout)
        self.cli("improvements", "--add", "--title", "dead end", "--category", "other")
        self.cli("improvements", "--reject", "I02", "--rationale", "no supporting incident")
        self.assertIn("[rejected]", self.cli("improvements", "--status", "rejected").stdout)
        after = {str(p.relative_to(refs)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(refs.rglob("*")) if p.is_file()}
        self.assertEqual(before, after)

    # ----------------------------------- dispatch, budgets, amendments

    def init_policy(self, **fields: str) -> Path:
        """A documents-only run with flat budget and policy fields on its plan."""
        run = self.init("split", evidence_mode="documents-only")
        plan = run / "plan.mdx"
        lines = plan.read_text().splitlines()
        for i, line in enumerate(lines):
            key = line.split(":", 1)[0].strip()
            if key in fields:
                lines[i] = f"{key}: {fields.pop(key)}"
        if fields:
            insert_at = next(i for i, line in enumerate(lines) if line.startswith("run:"))
            for key, value in fields.items():
                insert_at += 1
                lines.insert(insert_at, f"{key}: {value}")
        plan.write_text("\n".join(lines) + "\n")
        return run

    def test_m10_dispatch_is_idempotent_and_guarded(self) -> None:
        """One writer per round and session; refusals name the exact condition."""
        run = self.init_policy(primary_model="m1", fallback_models="m2, m3",
                               max_concurrency="1")
        self.assign_simple(run, "T01")
        self.cli("assign", "demo", "T02", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/t02.py",
                 "--verify", 'printf "T02 ok\\n"', "--depends-on", "T01")
        self.fill_task(run / "T02-task.mdx")
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        unmet = self.cli("dispatch", "demo", "T02", "--session", "w1", ok=False)
        self.assertNotEqual(0, unmet.returncode)
        self.assertIn("T01", unmet.stderr)
        # A crash after the record write still adopts the same single writer.
        crash = self.cli("dispatch", "demo", "T01", "--session", "w1",
                         ok=False, fault="dispatch:launch")
        self.assertEqual(70, crash.returncode)
        first = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertIn("already dispatched: T01 round 1 -> session w1 (dsp:", first.stdout)
        txn = re.search(r"dsp:[0-9a-f]+", first.stdout).group(0)
        again = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertIn("already dispatched", again.stdout)
        self.assertIn(txn, again.stdout)
        rival = self.cli("dispatch", "demo", "T01", "--session", "w2", ok=False)
        self.assertNotEqual(0, rival.returncode)
        self.assertIn("already dispatched to session 'w1'", rival.stderr)
        self.assertEqual(1, len(list((run / ".dispatch").glob("*.json"))))
        # Provider concurrency is enforced with its exact utilization.
        self.assign_simple(run, "T03")
        capped = self.cli("dispatch", "demo", "T03", "--session", "w2", ok=False)
        self.assertNotEqual(0, capped.returncode)
        self.assertIn("concurrency 1/1", capped.stderr)

    def test_t31_approved_releases_capacity(self) -> None:
        """An approved round stops counting as live execution capacity."""
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        blocked = self.cli("dispatch", "demo", "T02", "--session", "w2", ok=False)
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn("concurrency 1/1", blocked.stderr)
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--approve", "--reason", "Correct.")
        freed = self.cli("dispatch", "demo", "T02", "--session", "w2")
        self.assertIn("dispatched T02 round 1", freed.stdout)
        self.cli("reconcile", "demo")
        t01 = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertNotEqual("dispatched", t01.get("state"))
        t02 = json.loads((run / ".dispatch" / "T02.json").read_text())
        self.assertEqual("dispatched", t02.get("state"))

    def test_t31_superseded_round_releases_capacity(self) -> None:
        """A round superseded by a later round no longer holds a slot."""
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--changes")
        dec = run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Correct `src/t01.py:1` and add a regression.",
        ))
        self.cli("decide", "demo", "T01", "--changes", "--reason", "Needs a guard.")
        self.assertTrue((run / "T01-report-02.mdx").is_file())
        second = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertIn("dispatched T01 round 2", second.stdout)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(2, int(record.get("round", 0)))
        self.assertEqual("dispatched", record.get("state"))

    def test_t31_handoff_releases_capacity_and_preserves_scope(self) -> None:
        """A ready handoff frees execution capacity but keeps scope owned."""
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.cli("handoff", "demo", "T01")
        self.fill_handoff(run / "T01-handoff-01.mdx")
        ready = self.cli("handoff", "demo", "T01", "--submit")
        self.assertIn("ready for a replacement", ready.stdout)
        freed = self.cli("dispatch", "demo", "T02", "--session", "w2")
        self.assertIn("dispatched T02 round 1", freed.stdout)
        self.cli("assign", "demo", "T03", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/t01.py",
                 "--verify", 'printf "T03 ok\\n"')
        self.fill_task(run / "T03-task.mdx")
        self.fill_scope(run / "T03-scope.mdx")
        collision = self.cli("scope", "demo", "T03", "--submit", ok=False)
        self.assertNotEqual(0, collision.returncode)
        self.assertIn("T01", collision.stderr)

    def test_t31_reconcile_reconciles_stale_dispatch(self) -> None:
        """Reconcile derives liveness from documents and names stale records."""
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--approve", "--reason", "Correct.")
        before = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("dispatched", before.get("state"))
        out = self.cli("reconcile", "demo").stdout
        self.assertIn("T01", out)
        self.assertIn("dispatch", out.lower())
        after = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertNotEqual("dispatched", after.get("state"))
        self.assertEqual(before.get("model_requested"), after.get("model_requested"))

    def test_t31_serialized_cap_claim_across_owners(self) -> None:
        """Two concurrent dispatches under a cap of one cannot both succeed."""
        import concurrent.futures as futures
        import threading
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.assign_simple(run, "T03")
        for sid in ("w1", "w2", "w3"):
            self.cli("session", "demo", "--register", "--session", sid,
                     "--name", sid, "--role", "implementor")
        barrier = threading.Barrier(3)
        root = self.root

        def one(owner: str, session: str) -> subprocess.CompletedProcess[str]:
            barrier.wait(timeout=10)
            return subprocess.run(
                [sys.executable, str(DOCKET), "dispatch", "demo", owner,
                 "--session", session],
                cwd=str(root), text=True, capture_output=True,
            )

        with futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(one, ("T01", "T02", "T03"), ("w1", "w2", "w3")))
        ok = [r for r in results if r.returncode == 0]
        refused = [r for r in results if r.returncode != 0]
        self.assertEqual(1, len(ok))
        self.assertEqual(2, len(refused))
        for r in refused:
            self.assertIn("concurrency", r.stderr)
            self.assertIn("1/1", r.stderr)
        text = cli_source_text()
        self.assertIn("capacity_lock", text)
        self.assertIn("read-check-write", text)
        self.assertIn("run-wide capacity", text)

    def test_t31_retry_generation_binding(self) -> None:
        """Same session plus newer generation is a different writer needing resume."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        same = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertIn("already dispatched", same.stdout)
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1-restarted", "--role", "implementor")
        rival = self.cli("dispatch", "demo", "T01", "--session", "w1", ok=False)
        self.assertNotEqual(0, rival.returncode)
        self.assertIn("docket resume", rival.stderr)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("w1", record.get("session"))
        self.assertEqual(1, int(record.get("session_generation", 1)))

    def test_t31_resume_holds_single_live_record(self) -> None:
        """Handoff frees the slot and resume re-acquires exactly one live record."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.cli("set-model", "demo", "T01", "--actual", "live-m")
        self.cli("handoff", "demo", "T01")
        self.fill_handoff(run / "T01-handoff-01.mdx")
        self.cli("handoff", "demo", "T01", "--submit")
        handed = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertNotEqual("dispatched", handed.get("state"))
        resumed = self.cli("resume", "demo", "T01", "--session", "w2",
                           "--reason", "process killed")
        self.assertIn("resumed T01 round 1 -> session w2", resumed.stdout)
        files = list((run / ".dispatch").glob("*.json"))
        self.assertEqual(1, len(files))
        record = json.loads(files[0].read_text())
        self.assertEqual("dispatched", record.get("state"))
        self.assertEqual("w2", record.get("session"))
        self.assertIn("live-m", [h.get("model") for h in record.get("model_history", [])])

    def test_t31_status_and_health_show_live_capacity(self) -> None:
        """Status and health report live execution capacity matching live records."""
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        status_one = self.cli("status", "demo").stdout
        self.assertIn("1 live", status_one)
        health_one = self.cli("health", "demo").stdout
        self.assertIn("1 live", health_one)
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--approve", "--reason", "Correct.")
        self.cli("reconcile", "demo")
        status_zero = self.cli("status", "demo").stdout
        self.assertIn("0 live", status_zero)
        health_zero = self.cli("health", "demo").stdout
        self.assertIn("0 live", health_zero)
        self.cli("dispatch", "demo", "T02", "--session", "w2")
        status_back = self.cli("status", "demo").stdout
        self.assertIn("1 live", status_back)

    def test_m10_resume_recovers_without_handoff(self) -> None:
        """Abrupt loss without a semantic handoff is recoverable and honest."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.cli("set-model", "demo", "T01", "--actual", "live-m")
        resumed = self.cli("resume", "demo", "T01", "--session", "w2",
                           "--reason", "process killed")
        self.assertIn("resumed T01 round 1 -> session w2", resumed.stdout)
        checkpoint = json.loads((run / ".checkpoints" / "T01-01.json").read_text())
        self.assertEqual("no", checkpoint["semantic_handoff"])
        self.assertEqual("T01-task.mdx", checkpoint["task_pointer"])
        self.assertTrue(checkpoint["task_revision"].startswith("sha256:"))
        dispatch = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("w2", dispatch["session"])
        self.assertIn("observed", [h.get("kind") for h in dispatch["model_history"]])
        self.assertIn("live-m", [h.get("model") for h in dispatch["model_history"]])
        self.assertEqual("live-m", dispatch["model_observed"])
        # A crash during the rebind still settles on one writer on retry.
        crash = self.cli("resume", "demo", "T01", "--session", "w2", ok=False,
                         fault="resume:rebind")
        self.assertEqual(70, crash.returncode)
        retried = self.cli("resume", "demo", "T01", "--session", "w2")
        self.assertEqual(0, retried.returncode)
        self.assertEqual("w2", json.loads(
            (run / ".dispatch" / "T01.json").read_text())["session"])

    def test_a_model_switch_attributes_later_outcomes_to_the_new_model(self) -> None:
        """Regression: after a switch the old observed model kept every later reject case."""
        run = self.init_policy(primary_model="m1", fallback_models="m2, m3")
        self.assign_simple(run, "T01")
        self.cli("dispatch", "demo", "T01", "--session", "w1", "--register")
        self.cli("set-model", "demo", "T01", "--actual", "m1")
        switched = self.cli("switch-model", "demo", "T01", "--model", "m2")
        dispatch = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(("m2", "unobserved"),
                         (dispatch["model_requested"], dispatch["model_observed"]))
        self.assertIn("prompt:", switched.stdout)
        refused = self.cli("submit", "demo", "T01", "--as", "implementor", ok=False)
        self.assertNotEqual(0, refused.returncode)
        cases = [json.loads(line) for line in
                 Path(os.environ["DOCKET_FEEDBACK_LOG"]).read_text().splitlines()]
        gate = [case for case in cases if case.get("kind") == "gate"]
        self.assertEqual("m2", gate[-1]["model"])

    def test_a_decided_round_cannot_be_resumed(self) -> None:
        """Regression: a stale dispatch for an approved round resumed and logged a recovery."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.cli("dispatch", "demo", "T01", "--session", "w1", "--register")
        self.fill_task_report(run / "T01-report-01.mdx", files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        self.cli("session", "demo", "--register", "--session", "w2", "--name", "w2",
                 "--role", "implementor")
        refused = self.cli("resume", "demo", "T01", "--session", "w2", ok=False)
        self.assertIn("is approved: there is no work left to resume", refused.stderr)

    def test_a_refused_dispatch_registers_no_session(self) -> None:
        """Regression: `--register` wrote a registration before the dispatch was refused."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.cli("assign", "demo", "T02", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/t02.py", "--verify", "true",
                 "--depends-on", "T01")
        self.fill_task(run / "T02-task.mdx")
        refused = self.cli("dispatch", "demo", "T02", "--session", "w2", "--register", ok=False)
        self.assertIn("unmet dependencies", refused.stderr)
        self.assertNotIn("registered session", refused.stdout)
        self.assertFalse((self.root / ".docket" / "sessions" / "w2.json").exists())

    def test_m10_budgets_fallbacks_sizing_and_unknown_telemetry(self) -> None:
        """Flat policy bounds spend; breadth warns; missing telemetry is unknown."""
        run = self.init_policy(primary_model="m1", fallback_models="m2, m3")
        plan = (run / "plan.mdx").read_text()
        self.assertIn("correction_limit: 2", plan)
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        dispatched = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertNotIn("unknown", dispatched.stdout)
        dispatch = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("m1", dispatch["model_requested"])
        self.assertEqual("unobserved", dispatch["model_observed"])
        self.cli("switch-model", "demo", "T01", "--model", "m2")
        repeat = self.cli("switch-model", "demo", "T01", "--model", "m2", ok=False)
        self.assertNotEqual(0, repeat.returncode)
        self.assertIn("already tried", repeat.stderr)
        outside = self.cli("switch-model", "demo", "T01", "--model", "mx", ok=False)
        self.assertNotEqual(0, outside.returncode)
        self.assertIn("higher spending tier", outside.stderr)
        entry = json.loads((run / ".exceptions" / "T01.json").read_text())
        self.assertEqual("open", entry["state"])
        self.assertIn("higher spending tier", entry["detail"])
        again = self.cli("switch-model", "demo", "T01", "--model", "mx", ok=False)
        self.assertNotEqual(0, again.returncode)
        self.assertEqual(entry, json.loads((run / ".exceptions" / "T01.json").read_text()))
        retrospective = self.cli("retrospective", "demo").stdout
        self.assertIn("premium tokens: unknown", retrospective)
        # Task sizing warns on guessed breadth and never rejects on file count.
        many = self.cli("assign", "demo", "T09", "--complexity", "high", "--executor",
                        "implementor", "--harness", "opencode",
                        *sum((["--file", f"src/p{i}.py"] for i in range(10)), []),
                        "--verify", 'printf ok')
        self.assertEqual(0, many.returncode)
        self.assertIn("guessed breadth", many.stdout)

    def test_t32_initial_dispatch_outside_policy_refused(self) -> None:
        """An initial model outside the approved policy needs the exception."""
        run = self.init_policy(primary_model="m1", fallback_models="m2, m3")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        refused = self.cli("dispatch", "demo", "T01", "--session", "w1",
                           "--model", "mx", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("exception", refused.stderr)
        self.assertIn("is open", refused.stderr)
        self.assertIn("higher spending tier", refused.stderr)
        entry = json.loads((run / ".exceptions" / "T01.json").read_text())
        self.assertEqual("T01", entry.get("owner"))
        self.assertEqual("open", entry.get("state"))
        self.assertIn("mx", entry.get("detail", ""))
        self.assertFalse((run / ".dispatch" / "T01.json").is_file())

    def test_t32_empty_fallback_is_single_model(self) -> None:
        """Primary with empty fallbacks allows exactly one model."""
        run = self.init_policy(primary_model="m1", fallback_models="")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        ok_dispatch = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertIn("dispatched T01 round 1", ok_dispatch.stdout)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("m1", record.get("model_requested"))
        refused = self.cli("switch-model", "demo", "T01", "--model", "mx", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("exception", refused.stderr)
        self.assertIn("is open", refused.stderr)
        entry = json.loads((run / ".exceptions" / "T01.json").read_text())
        self.assertEqual("open", entry.get("state"))
        self.assertIn("[m1]", entry.get("detail", ""))

    def test_t32_absent_policy_usable_with_plain_message(self) -> None:
        """No policy at all stays usable and says so plainly once."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        out = self.cli("dispatch", "demo", "T01", "--session", "w1",
                       "--model", "any-m")
        self.assertIn("dispatched T01 round 1", out.stdout)
        combined = out.stdout + out.stderr
        self.assertIn("no approved model policy", combined)
        self.assertIn("nothing is being enforced", combined)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("any-m", record.get("model_requested"))

    def test_t32_absent_and_empty_distinguishable(self) -> None:
        """Absent and single-model runs produce different messages."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        absent_out = self.cli("dispatch", "demo", "T01", "--session", "w1",
                              "--model", "any-m")
        self.assertIn("no approved model policy", absent_out.stdout + absent_out.stderr)
        plan = run / "plan.mdx"
        text = plan.read_text().replace("run: demo", "run: demo\nprimary_model: m1")
        plan.write_text(text)
        self.assign_simple(run, "T02")
        single_out = self.cli("dispatch", "demo", "T02", "--session", "w1")
        self.assertNotIn("no approved model policy", single_out.stdout + single_out.stderr)
        refused = self.cli("switch-model", "demo", "T02", "--model", "mx", ok=False)
        self.assertIn("[m1]", refused.stderr)

    def test_t32_model_history_and_open_exception(self) -> None:
        """Approved and exceptional transitions keep history with an open owner."""
        run = self.init_policy(primary_model="m1", fallback_models="")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        refused = self.cli("switch-model", "demo", "T01", "--model", "mx", ok=False)
        self.assertNotEqual(0, refused.returncode)
        entry = json.loads((run / ".exceptions" / "T01.json").read_text())
        self.assertEqual("T01", entry.get("owner"))
        self.assertEqual("open", entry.get("state"))
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        tried = [h.get("model", "") for h in record.get("model_history", [])]
        self.assertIn("m1", tried)
        self.assertNotIn("mx", tried)
        again = self.cli("switch-model", "demo", "T01", "--model", "mx", ok=False)
        self.assertNotEqual(0, again.returncode)
        entry2 = json.loads((run / ".exceptions" / "T01.json").read_text())
        self.assertEqual(entry, entry2)
        self.assertEqual("open", entry2.get("state"))

    def test_t32_dispatch_binds_prompt_digest(self) -> None:
        """The dispatch record carries a digest recomputable from the prompt."""
        run = self.init_policy(primary_model="m1", fallback_models="m2, m3")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        digest = record.get("prompt_digest", "")
        self.assertTrue(str(digest).startswith("sha256:"))
        rendered = self.cli("prompt", "demo", "T01", "--role", "implementor",
                            "--model", "m1")
        recomputed = sha(rendered.stdout.encode())
        self.assertEqual(digest, recomputed)

    def test_t32_binding_not_delivery(self) -> None:
        """Dispatch records a binding with unobserved fields, not a delivery."""
        run = self.init_policy(primary_model="m1", fallback_models="m2, m3")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        out = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertIn("recorded binding", out.stdout)
        self.assertIn("prompt digest", out.stdout)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("unobserved", record.get("model_observed"))

    def test_t32_premium_budget_removed(self) -> None:
        """No product document still names the unimplemented knob."""
        repo = Path(DOCKET).resolve().parents[3]
        for relative in ("ARCHITECTURE.md", "docs/docket-current-system.mdx",
                         "docs/docket-architecture.html"):
            text = (repo / relative).read_text()
            self.assertNotIn("premium_budget", text)

    def test_m10_amendment_selectively_invalidates(self) -> None:
        """An amendment pauses only affected work; delayed old events cannot act."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.cli("assign", "demo", "T02", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/t02.py",
                 "--verify", 'printf "T02 ok\\n"', "--depends-on", "T01")
        self.fill_task(run / "T02-task.mdx")
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli("submit", "demo", "T02")
        self.submit_simple(run, "T03")
        thin = self.cli("propose-amendment", "demo", "T01", "--need", "decide X",
                        ok=False)
        self.assertNotEqual(0, thin.returncode)
        self.assertIn("is required", thin.stderr)
        self.cli("propose-amendment", "demo", "T01", "--need", "decide the cache scope",
                 "--conflicts", "task says memory, repo uses disk",
                 "--evidence", "src/cache.py:40",
                 "--alternative", "allow either backend",
                 "--impact", "T01 acceptance and T02 schedule")
        planner = self.cli("events", "demo", "--role", "planner", "--peek").stdout
        self.assertIn("T01:amendment:T01-01", planner)
        self.cli("reconcile", "demo")
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch", "--role", "orchestrator")
        self.cli("health", "demo", "--flag-stall", "T02", "--cause", "waiting")
        self.cli("reconcile", "demo")
        old_keys = [json.loads(p.read_text())["key"] for p in
                    (run / ".delivery" / "orchestrator" / "pending").glob("*.json")
                    if json.loads(p.read_text()).get("owner") == "T02"]
        self.assertEqual(1, len(old_keys))
        # The planner edits the contract, then accepts: only T02 invalidates.
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(task_text.replace(
            "Implement the feature.", "Implement the feature with either backend."))
        accepted = self.cli("amendment", "demo", "--accept", "T01-01", "--by", "planner")
        self.assertIn("invalidated 1 dependent(s): T02", accepted.stdout)
        self.cli("health", "demo", "--resolve-stall", "T02-1")
        swept = self.cli("reconcile", "demo").stdout
        self.assertIn("retired", swept)
        blocked_t02 = self.cli("decide", "demo", "T02", "--approve", ok=False)
        self.assertNotEqual(0, blocked_t02.returncode)
        self.assertIn("amendment T01-01", blocked_t02.stderr)
        self.cli("decide", "demo", "T03", "--approve")
        self.assertIn("approved", (run / "T03-report-01.mdx").read_text())
        # A delayed event for the old revision cannot act after retirement.
        self.assertIn("already retired", self.cli(
            "events", "demo", "--role", "orchestrator",
            "--ack", old_keys[0], "--session", "s1").stdout)
        # The affected task reverifies in a fresh round and decides cleanly.
        self.cli("decide", "demo", "T02", "--changes")
        dec = run / "T02-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT))
        self.cli("decide", "demo", "T02", "--changes")
        self.fill_task_report(run / "T02-report-02.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli("submit", "demo", "T02")
        self.cli("decide", "demo", "T02", "--approve")
        self.assertIn("approved", (run / "T02-report-02.mdx").read_text())

    # --------------------------------- packets and qualification

    @staticmethod
    def report_body_digest(report: Path) -> str:
        """The evidence digest form, computed the way the CLI computes it."""
        text = report.read_text()
        body = text[text.find("\n---", 3) + 4:].lstrip("\n")
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()

    @staticmethod
    def packet_tokens(packet: str) -> int:
        return max(1, len(packet) // 4)

    def test_m11_review_packet_pinned_and_bounded(self) -> None:
        """Packets bind exact revisions, bound tokens, and preserve risks."""
        run = self.init5()
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace(
            "## Risks", "## Risks\n\nCache stampede on cold start."))
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        self.assign_simple(run, "T02")
        self.fill_task_report(run / "T02-report-01.mdx", blocked=True,
                              files="- `src/t02.py:1` - blocked change.")
        self.cli("submit", "demo", "T02", "--blocked", "--as", "implementor")
        self.cli("decide", "demo", "T02", "--waive", "--reason", "disk is full",
                 "--as", "reviewer")
        packet = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        manifest, _ = self.frozen("T01")
        self.assertIn(manifest["digest"], packet)
        self.assertIn("T01-verification-01.mdx: pass", packet)
        self.assertIn("disk is full", packet)
        self.assertIn("Cache stampede", packet)
        tokens = int(re.search(r"packet: (\d+) estimated tokens",
                               self.cli("review-packet", "demo",
                                        "--role", "reviewer").stderr).group(1))
        self.assertLessEqual(tokens, 2000)
        # A correction packet carries findings, the delta, and fresh evidence.
        self.submit5(run, "T03")
        self.cli("verify", "demo", "T03", "--result", "fail", "--as", "verifier",
                 "--detail", "F1: oracle mismatch")
        self.cli("decide", "demo", "T03", "--changes", "--as", "reviewer")
        dec = run / "T03-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT))
        self.cli("decide", "demo", "T03", "--changes", "--as", "reviewer")
        self.fill_task_report(run / "T03-report-02.mdx",
                              files="- `src/t03.py:1` - implemented T03.")
        self.cli("submit", "demo", "T03", "--as", "implementor")
        self.cli("verify", "demo", "T03", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T03", "--approve", "--as", "reviewer")
        correction = self.cli("review-packet", "demo", "--role", "reviewer",
                              "--correction", "T03").stdout
        self.assertIn("T03-verification-01.mdx: fail", correction)
        self.assertIn("T03-verification-02.mdx: pass", correction)
        self.assertNotIn("- T01 round", correction)
        self.assertIn("T03-decision-01.mdx", correction)

    def docket_mod(self):  # type: ignore[no-untyped-def]
        """The CLI binary as an importable module, cached per test."""
        if getattr(self, "_docket_mod", None) is None:
            self._docket_mod = load_cli("docket_test_mod")
        return self._docket_mod

    def test_aggregate_retry_safety(self) -> None:
        """Interrupted aggregate freeze and decision transitions are retry-safe."""
        run = self.setup_aggregate_run()
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)

        self.cli(
            "submit", "demo", "orch", "--skip-verify",
            fault="bundle:publish",
            ok=False,
        )
        # The freeze was interrupted; re-submitting should succeed.
        result = self.cli("submit", "demo", "orch", "--skip-verify")
        self.assertIn("frozen aggregate bundle", result.stdout)
        self.assertIn("intact", self.cli("bundle", "demo", "orch", "--list").stdout)

        # Interrupt a decision transition.
        self.cli(
            "decide", "demo", "orch", "--approve",
            fault="transition:report",
            ok=False,
        )
        # Retry finishes it without a second round: either it resumes the
        # journalled transition or it observes the already-applied verdict.
        result = self.cli("decide", "demo", "orch", "--approve", ok=True)
        self.assertTrue(
            "resumed interrupted" in result.stdout or "already approved" in result.stdout,
            f"unexpected retry output: {result.stdout}",
        )
        self.assertIn("approved", (run / "orch-decision-01.mdx").read_text())
        self.assertEqual(1, len(list(run.glob("orch-decision-*.mdx"))))

    def test_aggregate_invalidation_on_constituent_reopen(self) -> None:
        """Reopening a constituent visibly invalidates dependent aggregate readiness."""
        run = self.setup_aggregate_run()

        # Waive a fresh T03, then build the aggregate pinning it.
        self.assign("T03", file="src/c.py")
        self.fill_task(run / "T03-task.mdx")
        self.fill_task_report(run / "T03-report-01.mdx", blocked=True,
                              files="- `src/c.py:1` - blocked change.")
        self.cli("submit", "demo", "T03", "--blocked")
        self.cli("decide", "demo", "T03", "--waive", "--reason", "external outage")

        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)
        self.cli("submit", "demo", "orch", "--skip-verify")

        # Reopen T03.
        self.cli(
            "decide", "demo", "T03", "--reopen",
            "--reason", "service is back up",
            ok=True,
        )

        # Status names the invalidated aggregate and the reopened task.
        status = self.cli("status", "demo").stdout
        self.assertIn("aggregate bundle staleness", status)
        self.assertIn("reopened from waiver", status)
        self.assertIn("T03", status)

        # Events now include the aggregate-stale and reopened notifications.
        events = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertIn("T03:reopened", events)
        self.assertIn("any aggregate bundle that pinned it is stale", events)

    # ------------------------------------------------------- before/after evidence

    def test_aggregate_submission_previously_froze_nothing(self) -> None:
        """Before aggregate support, orch submissions had no bundle at all."""
        # This test always verifies the current behavior: orch submit freezes
        # an aggregate bundle. When DOCKET_BIN_PRIOR points at a prior build,
        # it first demonstrates the regression (prior froze nothing) on the
        # same run, then shows the fix. It never skips, so it is real evidence.
        run = self.setup_aggregate_run()
        self.assign("orch", executor="orchestrator")
        report = run / "orch-report-01.mdx"
        self.fill_orch_report(report)

        prior_bin = os.environ.get("DOCKET_BIN_PRIOR")
        if prior_bin:
            prior_result = subprocess.run(
                [sys.executable, prior_bin, "submit", "demo", "orch", "--skip-verify"],
                cwd=str(self.root), text=True, capture_output=True,
            )
            self.assertEqual(0, prior_result.returncode)
            # Before: no bundle digest in the report frontmatter.
            self.assertNotIn("bundle_digest:", report.read_text())
            # Reset to draft so the current binary can submit the same round.
            text = report.read_text()
            for old_status in ("status: submitted", "status: completed", "status: blocked"):
                text = text.replace(old_status, "status: draft")
            report.write_text(text)
        # With the fix, submitting now freezes the aggregate.
        self.cli("submit", "demo", "orch", "--skip-verify")
        self.assertIn("bundle_digest:", report.read_text())
        manifest, _ = self.aggregate()
        self.assertEqual("aggregate", manifest["kind"])

    # ---------------- blocking findings from the independent review

    def test_watch_announce_lease_recovers_after_crash(self) -> None:
        """A crash after ledger write but before harness accept re-announces after expiry."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        # Crash exactly at the announcement boundary: ledger and durable
        # announce state are written, but the notice never reaches the harness.
        crashed = subprocess.run(
            [sys.executable, str(DOCKET), "watch", "demo",
             "--role", "orchestrator", "--timeout", "2"],
            cwd=str(self.root), text=True, capture_output=True,
            env=dict(os.environ, DOCKET_FAULT="watch:announce"))
        self.assertEqual(70, crashed.returncode)
        self.assertIn("watch:announce", crashed.stderr)
        led = run / ".woke-orchestrator"
        self.assertTrue(led.is_file())
        pending = list((run / ".delivery" / "orchestrator" / "pending").glob("*.json"))
        self.assertEqual(1, len(pending))
        key = json.loads(pending[0].read_text())["key"]
        announce = list((run / ".delivery" / "orchestrator" / "announce").glob("*.json"))
        self.assertEqual(1, len(announce))
        self.assertFalse(json.loads(announce[0].read_text()).get("delivered_at"))
        # Immediate restart sees the active announcement lease and stays quiet:
        # concurrent hooks converge on one wake and create no lifecycle change.
        rounds_before = self.rounds(run, "T01")
        quiet = subprocess.run(
            [sys.executable, str(DOCKET), "watch", "demo",
             "--role", "orchestrator", "--timeout", "1"],
            cwd=str(self.root), text=True, capture_output=True)
        self.assertEqual(0, quiet.returncode)
        self.assertEqual(rounds_before, self.rounds(run, "T01"))
        # After lease expiry the same actionable event is announced again, and
        # stays claimable throughout via explicit inbox pickup.
        until = float(json.loads(announce[0].read_text()).get("lease_until", 0) or 0)
        env = dict(os.environ, DOCKET_NOW=str(until + 1))
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "orch-main", "--role", "orchestrator")
        claimable = subprocess.run(
            [sys.executable, str(DOCKET), "inbox", "demo", "--role", "orchestrator",
             "--claim", "--session", "s1"],
            cwd=str(self.root), text=True, capture_output=True, env=env)
        self.assertEqual(0, claimable.returncode)
        self.assertTrue("already held" in claimable.stdout or f"claimed {key}" in claimable.stdout)
        # Release the inbox lease so the watch re-announcement is observable.
        for path in (run / ".delivery" / "orchestrator" / "leases").glob("*.json"):
            path.unlink()
        revived = subprocess.run(
            [sys.executable, str(DOCKET), "watch", "demo",
             "--role", "orchestrator", "--timeout", "2"],
            cwd=str(self.root), text=True, capture_output=True, env=env)
        self.assertEqual(2, revived.returncode)
        self.assertIn("review batch ready", revived.stderr)
        # That announcement reached the harness, so it stays delivered: the
        # event persists while the review is pending, and waking the
        # supervisor again after every lease would be polling by another name.
        record = json.loads(announce[0].read_text())
        self.assertTrue(record.get("delivered_at"))
        later = subprocess.run(
            [sys.executable, str(DOCKET), "watch", "demo",
             "--role", "orchestrator", "--timeout", "1"],
            cwd=str(self.root), text=True, capture_output=True,
            env=dict(os.environ, DOCKET_NOW=str(float(record["lease_until"]) + 1)))
        self.assertEqual(0, later.returncode, later.stderr)
        self.assertEqual("", later.stderr)

    def test_hook_routes_verifier_and_reviewer_isolated(self) -> None:
        """Every five-role session receives only its own events through the hook."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("arm", "demo", "--role", "verifier")
        self.cli("arm", "demo", "--role", "reviewer")
        self.cli("arm", "demo", "--role", "orchestrator")
        base = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root),
                    DOCKET_WATCH_TIMEOUT="0")
        verifier = subprocess.run([str(HOOK)], cwd=str(self.root),
                                  env=dict(base, DOCKET_ROLE="verifier"),
                                  capture_output=True, text=True)
        self.assertEqual(2, verifier.returncode)
        self.assertIn("T01 round 1 submitted", verifier.stderr)
        reviewer_idle = subprocess.run([str(HOOK)], cwd=str(self.root),
                                       env=dict(base, DOCKET_ROLE="reviewer"),
                                       capture_output=True, text=True)
        self.assertEqual(0, reviewer_idle.returncode)
        # Wrong-role isolation: a verifier submission cannot act as reviewer.
        peek_verifier = self.cli("events", "demo", "--role", "verifier", "--peek").stdout
        peek_reviewer = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertIn("T01:1:submitted", peek_verifier)
        self.assertNotIn("T01:1:submitted", peek_reviewer)
        # A real verifier submission followed by milestone readiness wakes reviewer.
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.submit5(run, "T02")
        self.cli("verify", "demo", "T02", "--result", "pass", "--as", "verifier")
        self.cli("batch", "demo", "--create", "M1", "--members", "T01,T02", "--milestone")
        self.cli("batch", "demo", "--close", "M1")
        reviewer = subprocess.run([str(HOOK)], cwd=str(self.root),
                                  env=dict(base, DOCKET_ROLE="reviewer"),
                                  capture_output=True, text=True)
        self.assertEqual(2, reviewer.returncode)
        self.assertIn("milestone batch M1 ready", reviewer.stderr)
        reviewer_peek = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertIn("batch:M1:ready:1", reviewer_peek)
        # The hook script itself routes all four roles.
        hook_text = HOOK.read_text()
        for role in ("planner", "orchestrator", "verifier", "reviewer"):
            self.assertIn(role, hook_text)

    def test_probe_rejects_negative_and_requires_positive(self) -> None:
        """Only a positively verified queue reports unattended; everything else stays manual."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")

        def fake_herdr(help_body: str, probe_rc: int = 0, help_rc: int = 0) -> Path:
            d = Path(tempfile.mkdtemp())
            script = d / "herdr"
            script.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then echo \"herdr 9.9.9-fake\"; exit 0; fi\n"
                f"if [ \"$*\" = \"agent prompt --send-if-idle --help\" ]; then "
                f"echo \"fake probe rc={probe_rc}\"; "
                f"{'echo --send-if-idle; exit 0' if probe_rc == 0 else 'exit 1'}; fi\n"
                f"if [ \"$*\" = \"agent prompt --help\" ]; then cat <<'EOF'\n{help_body}\nEOF\n"
                f"exit {help_rc}; fi\n"
                "exit 1\n")
            script.chmod(0o755)
            return d

        def probe_with(fake: Path) -> subprocess.CompletedProcess[str]:
            env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")
            return subprocess.run(
                [sys.executable, str(DOCKET), "delivery", "demo",
                 "--role", "orchestrator", "--probe"],
                cwd=str(self.root), text=True, capture_output=True, env=env)

        positive_help = "Usage: herdr agent prompt\nOptions:\n  --send-if-idle\n  --queue\n  capability: queue\n"
        fake = fake_herdr(positive_help, probe_rc=0)
        good = probe_with(fake)
        self.assertIn("boundary: verified-queue", good.stdout)
        self.assertIn("mode: unattended", good.stdout)
        self.assertIn("probed herdr agent prompt --help", good.stdout)
        self.assertIn("version", good.stdout)
        shutil.rmtree(fake, ignore_errors=True)

        negative_help = "does not support queue or atomic send-if-idle turn-boundary"
        fake = fake_herdr(negative_help, probe_rc=0)
        bad = probe_with(fake)
        self.assertIn("mode: manual", bad.stdout)
        self.assertNotIn("mode: unattended", bad.stdout)
        shutil.rmtree(fake, ignore_errors=True)

        unknown_help = "Submit a prompt to an agent\nUsage: herdr agent prompt <TARGET>"
        fake = fake_herdr(unknown_help, probe_rc=0)
        unknown = probe_with(fake)
        self.assertIn("mode: manual", unknown.stdout)
        shutil.rmtree(fake, ignore_errors=True)

        fake = fake_herdr(positive_help, probe_rc=0, help_rc=3)
        nonzero = probe_with(fake)
        self.assertIn("mode: manual", nonzero.stdout)
        shutil.rmtree(fake, ignore_errors=True)

        fake = fake_herdr(positive_help, probe_rc=1)
        advertised = probe_with(fake)
        self.assertIn("mode: manual", advertised.stdout)
        self.assertIn("executable", advertised.stdout)
        shutil.rmtree(fake, ignore_errors=True)

        # No herdr at all stays manual without claiming unattended reliability.
        empty = Path(tempfile.mkdtemp())
        env = dict(os.environ, PATH=str(empty))
        missing = subprocess.run(
            [sys.executable, str(DOCKET), "delivery", "demo",
             "--role", "orchestrator", "--probe"],
            cwd=str(self.root), text=True, capture_output=True, env=env)
        self.assertIn("mode: manual", missing.stdout)
        shutil.rmtree(empty, ignore_errors=True)

        # A hanging help command times out into manual mode.
        slow = Path(tempfile.mkdtemp())
        (slow / "herdr").write_text("#!/bin/sh\nsleep 5\necho slow\n")
        (slow / "herdr").chmod(0o755)
        env = dict(os.environ, PATH=f"{slow}{os.pathsep}{os.environ.get('PATH', '')}",
                   DOCKET_PROBE_TIMEOUT="1")
        timed_out = subprocess.run(
            [sys.executable, str(DOCKET), "delivery", "demo",
             "--role", "orchestrator", "--probe"],
            cwd=str(self.root), text=True, capture_output=True, env=env)
        self.assertIn("mode: manual", timed_out.stdout)
        self.assertIn("timeout", timed_out.stdout.lower())
        shutil.rmtree(slow, ignore_errors=True)

    def test_real_harness_qualification_or_honest_limitation(self) -> None:
        """Disposable real-process qualification with captured evidence, staying manual."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        result = self.cli("delivery", "demo", "--role", "orchestrator", "--qualify")
        self.assertEqual(0, result.returncode)
        artifact = run / ".delivery" / "qualification-orchestrator.json"
        self.assertTrue(artifact.is_file())
        record = json.loads(artifact.read_text())
        self.assertIn(record["status"], ("passed", "blocked"))
        # The installed herdr tracks no turn boundary, so manual is honest.
        self.assertEqual("manual", record["mode"])
        self.assertEqual("manual", record["production_mode"])
        self.assertNotIn("unattended", json.dumps(record["probe"]))
        checks = {c["check"]: c for c in record["checks"]}
        for name in ("hook-announce", "hook-kill-restart", "claim", "send", "ack",
                     "generation-reuse", "retry-release", "active-input"):
            self.assertIn(name, checks, f"missing qualification check {name}")
            self.assertEqual("passed", checks[name]["status"],
                             f"check {name}: {checks[name]['detail']}")
        self.assertIn("pid", checks["hook-announce"])
        self.assertIn("exit", checks["hook-kill-restart"])
        self.assertIn("key", record["event"])
        # Lease history survives in the append-only log; the disposable outbox
        # notice was cleaned up; the submission was never rolled back.
        # It ran against a disposable copy, so the history is in the record, not the live log.
        log_text = json.dumps(record["lease_history"])
        for kind in ("claimed", "receipt", "retry"):
            self.assertIn(kind, log_text)
        self.assertFalse((run / ".delivery" / "orchestrator" / "log.jsonl").exists())
        self.assertEqual([], list((run / ".delivery" / "orchestrator" / "outbox").glob("*.txt")))
        self.assertIn("submitted", (run / "T01-report-01.mdx").read_text())
        # Honest limitation: no unattended reliability is claimed here.
        self.assertIn("manual", result.stdout)
        doctor = self.cli("doctor").stdout
        self.assertIn("mode: manual", doctor)

    def test_qualify_blocks_without_actionable_events(self) -> None:
        """Qualification with nothing to deliver blocks instead of inventing success."""
        run = self.init("split", evidence_mode="documents-only")
        result = self.cli("delivery", "demo", "--role", "orchestrator", "--qualify")
        self.assertEqual(0, result.returncode)
        record = json.loads((run / ".delivery" / "qualification-orchestrator.json").read_text())
        self.assertEqual("blocked", record["status"])
        self.assertEqual("no-actionable-event", record["blocked_capability"])

    def test_a_test_never_reaches_the_users_herdr(self) -> None:
        """Regression: release tests split and drove panes in the terminal running the suite.

        Under load the real pane's read-back raced its output and blocked the delivery
        qualification the release tests need, so they failed only on a busy machine.
        """
        self.assertEqual([], sorted(k for k in os.environ if k.startswith("HERDR_")))
        self.fake_herdr_panes()
        self.assertEqual("1", os.environ["HERDR_ENV"])
        herdr = shutil.which("herdr")
        self.assertIsNotNone(herdr)
        self.assertTrue(str(herdr).startswith(self._feedback_tmp.name), herdr)

    def test_qualify_blocks_when_pane_lifecycle_unavailable(self) -> None:
        """Without herdr panes, qualification names the unavailable capability."""
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        empty = Path(tempfile.mkdtemp())
        env = dict(os.environ, PATH=str(empty))
        for var in ("HERDR_ENV",):
            env.pop(var, None)
        result = subprocess.run(
            [sys.executable, str(DOCKET), "delivery", "demo",
             "--role", "orchestrator", "--qualify"],
            cwd=str(self.root), text=True, capture_output=True, env=env)
        shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(0, result.returncode)
        record = json.loads((run / ".delivery" / "qualification-orchestrator.json").read_text())
        self.assertEqual("blocked", record["status"])
        self.assertEqual("herdr-disposable-pane", record["blocked_capability"])
        checks = {c["check"]: c for c in record["checks"]}
        self.assertEqual("blocked", checks["disposable-pane"]["status"])
        self.assertEqual("passed", checks["hook-announce"]["status"])

    def test_qualification_never_consumes_a_live_wake_or_changes_a_pause(self) -> None:
        """Regression: qualifying a role delivered its real pending event and resumed its delivery."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("delivery", "demo", "--role", "verifier", "--pause")
        empty = Path(tempfile.mkdtemp())
        env = dict(os.environ, PATH=str(empty))
        env.pop("HERDR_ENV", None)
        result = subprocess.run(
            [sys.executable, str(DOCKET), "delivery", "demo", "--role", "verifier", "--qualify"],
            cwd=str(self.root), text=True, capture_output=True, env=env)
        shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(0, result.returncode, result.stderr)
        record = json.loads((run / ".delivery" / "qualification-verifier.json").read_text())
        self.assertEqual("passed", {c["check"]: c for c in record["checks"]}["hook-announce"]["status"])
        self.assertFalse((run / ".woke-verifier").exists(), "the live ledger was written")
        self.assertTrue((run / ".delivery" / "verifier" / "paused").is_file(),
                        "the operator's pause was lifted")
        peek = self.cli("events", "demo", "--role", "verifier", "--peek").stdout
        self.assertIn("[pending", peek)
        self.assertNotIn("[delivered", peek)

    def test_worktree_identity_preserves_paths_and_types(self) -> None:
        """Untracked renames, modes, symlinks, and odd names all move the identity."""
        mod = load_cli("docket_mod3")
        repo = self.repo(self.root / "idrepo")
        ident = lambda: mod.worktree_identity(cwd=repo)
        base = ident()
        self.assertTrue(base.startswith("sha256:"))
        (repo / "alpha.txt").write_text("same bytes\n")
        added = ident()
        self.assertNotEqual(base, added)
        (repo / "alpha.txt").rename(repo / "beta.txt")
        self.assertNotEqual(added, ident(),
                            "renaming without touching bytes must move the identity")
        (repo / "gamma.txt").write_text("same bytes\n")
        two_paths = ident()
        (repo / "beta.txt").write_text("other bytes\n")
        (repo / "gamma.txt").write_text("same bytes\n")
        swapped = ident()
        self.assertNotEqual(two_paths, swapped,
                            "swapped contents must move the identity")
        os.chmod(repo / "gamma.txt", 0o755)
        self.assertNotEqual(swapped, ident(),
                            "an executable bit must move the identity")
        os.chmod(repo / "gamma.txt", 0o644)
        (repo / "link.py").symlink_to("beta.txt")
        linked = ident()
        (repo / "link.py").unlink()
        (repo / "link.py").write_text("beta.txt\n")
        self.assertNotEqual(linked, ident(),
                            "a symlink must differ from a file holding its target")
        (repo / "link.py").unlink()
        (repo / "link.py").symlink_to("gamma.txt")
        self.assertNotEqual(linked, ident(), "retargeting must move the identity")
        (repo / "link.py").unlink()
        settled = ident()
        for name in ("with space.txt", "ünïcodé.txt", 'quote"q.txt', "new\nline.txt"):
            (repo / name).write_text("odd\n")
            self.assertNotEqual(settled, ident(), f"odd name {name!r} must move the identity")
            odd = ident()
            self.assertTrue(odd.startswith("sha256:"), f"odd name {name!r} must hash")
            (repo / name).unlink()
        self.assertEqual(settled, ident(), "removing odd names must restore the identity")
        (repo / "src" / "a.py").write_text("allowed = 2\n")
        self.git("add", "src/a.py", cwd=repo)
        self.assertNotEqual(settled, ident(), "a staged change must move the identity")
        self.git("commit", "-qm", "second", cwd=repo)
        committed = ident()
        (repo / "src" / "a.py").write_text("allowed = 3\n")
        self.assertNotEqual(committed, ident(), "an unstaged change must move the identity")
        if os.geteuid() != 0:
            secret = repo / "secret.txt"
            secret.write_text("hidden\n")
            os.chmod(secret, 0)
            try:
                self.assertTrue(ident().startswith("unknown"))
            finally:
                os.chmod(secret, 0o644)
        else:
            self.skipTest("unreadable-file case needs a non-root user")

    def test_review_packet_validates_and_preserves(self) -> None:
        """Packets refuse damaged/stale evidence, keep full risks, and pin aggregates."""
        run = self.init5()
        marker = "UNIQUE-RISK-MARKER-987654321-after-char-300-" + "x" * 400
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("## Risks", f"## Risks\n\n{marker}"))
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        packet = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        self.assertIn(marker, packet)
        manifest, _ = self.frozen("T01")
        self.assertIn(manifest["digest"], packet)
        self.assertIn("A1", packet)
        self.assertLessEqual(self.packet_tokens(packet), 2000)
        # Damaged artifact blocks the packet with a specific error.
        where = run / ".bundles" / "T01" / manifest["digest"].split(":")[1][:12]
        # Find the actual bundle dir via ledger instead of guessing the prefix.
        entries = json.loads((run / ".bundles" / "T01" / "rounds.json").read_text())["entries"]
        bdir = run / ".bundles" / "T01" / entries[-1]["dir"]
        artifact = bdir / "report.mdx"
        saved = artifact.read_bytes()
        artifact.write_bytes(saved + b"tamper")
        damaged = self.cli("review-packet", "demo", "--role", "reviewer", ok=False)
        self.assertNotEqual(0, damaged.returncode)
        self.assertIn("damaged", damaged.stderr)
        artifact.write_bytes(saved)
        # Stale bundle (report edited after freeze) blocks with a stale error.
        rep = run / "T01-report-01.mdx"
        rep.write_text(rep.read_text() + "\nStale edit.\n")
        stale = self.cli("review-packet", "demo", "--role", "reviewer", ok=False)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("stale", stale.stderr.lower())
        rep.write_text(rep.read_text().replace("\nStale edit.\n", ""))
        # Aggregate digest and root-qualified patch appear once orch submits.
        self.submit5(run, "T02")
        self.cli("verify", "demo", "T02", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T02", "--approve", "--as", "reviewer")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_text = orch_report.read_text()
        orch_text = orch_text.replace("| T01 | approved | passed |",
                                      "| T01 | approved | passed |\n| T02 | approved | passed |")
        orch_report.write_text(orch_text)
        self.cli("submit", "demo", "orch", "--skip-verify", "--skip-verify-reason",
                 "covered by task evidence", "--as", "orchestrator")
        full = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        orch_entries = json.loads((run / ".bundles" / "orch" / "rounds.json").read_text())["entries"]
        self.assertIn(orch_entries[-1]["digest"], full)
        for entry in entries:
            self.assertIn(entry["digest"], full)

    def submit_orch_verified(self, run: Path, *, verify: str = 'printf "orch ok\\n"',
                             blocked: bool = False, skip_verify: bool = False) -> None:
        """Approve T01/T02, then submit the aggregate with real verification."""
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        self.submit5(run, "T02")
        self.cli("verify", "demo", "T02", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T02", "--approve", "--as", "reviewer")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", verify)
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_text = orch_report.read_text()
        orch_text = orch_text.replace("| T01 | approved | passed |",
                                      "| T01 | approved | passed |\n| T02 | approved | passed |")
        if blocked:
            orch_text = orch_text.replace("## Decisions needed\n\nnone",
                                          "## Decisions needed\n\nWaive the slow integration job?")
        orch_report.write_text(orch_text)
        args = ["submit", "demo", "orch", "--as", "orchestrator"]
        if blocked:
            args.append("--blocked")
        if skip_verify:
            args += ["--skip-verify", "--skip-verify-reason", "none needed"]
        self.cli(*args)

    def qualify_suite(self, run: Path, passed: int = 164, failed: int = 0,
                      exit_code: int = 0) -> Path:
        """Capture a trivial suite execution; returns the artifact path.

        The command only needs to print a unittest summary; the machinery
        under test is the capture, binding, and validation, not the suite.
        """
        total = passed + failed
        body = f"Ran {total} tests in 1s\\n\\n"
        body += "OK\\n" if failed == 0 else f"FAILED (failures={failed})\\n"
        command = f"printf '{body}'"
        if exit_code != 0:
            command += f"; exit {exit_code}"
        result = self.cli("suite", "demo", "--qualify", "--command", command)
        self.assertEqual(0, result.returncode)
        arts = sorted((run / ".suite").glob("qual-*.json"))
        self.assertTrue(arts)
        return arts[-1]

    def freeze_release(self, run: Path, agg_digest: str = "", **over: object) -> object:
        """Freeze a release against the run's single suite artifact."""
        args: list[str] = ["release", "demo", "--freeze"]
        if "suite_artifact" in over:
            args += ["--suite-artifact", str(over["suite_artifact"])]
        for spec in over.get("milestones", [f"M11={agg_digest}"] if agg_digest else []):
            args += ["--milestone", spec]
        return self.cli(*args, ok=bool(over.get("ok", True)))

    def release_setup(self, run: Path) -> None:
        """Pane qualification and a green suite artifact."""
        self.fake_herdr_panes()
        qualified = self.cli("delivery", "demo", "--role", "orchestrator", "--qualify")
        self.assertEqual(0, qualified.returncode)
        self.qualify_suite(run)

    def release_ready_run(self) -> tuple[Path, str]:
        """A five-role run fully set up for one release freeze.

        Returns (run, aggregate digest). Delivery is qualified, the suite is green,
        and the aggregate bundle is verified, but no release is frozen yet.
        """
        self.repo()
        run = self.init5()
        self.submit5(run, "T01")
        self.submit5(run, "T02")
        for owner in ("T01", "T02"):
            self.cli("verify", "demo", owner, "--result", "pass", "--as", "verifier")
        # Delivery is qualified against a real orchestrator event: under
        # five-role the review batch exists once verification resolves.
        self.release_setup(run)
        for owner in ("T01", "T02"):
            self.cli("decide", "demo", owner, "--approve", "--as", "reviewer")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_text = orch_report.read_text().replace(
            "| T01 | approved | passed |",
            "| T01 | approved | passed |\n| T02 | approved | passed |")
        orch_report.write_text(orch_text)
        self.cli("submit", "demo", "orch", "--as", "orchestrator")
        orch_entries = json.loads(
            (run / ".bundles" / "orch" / "rounds.json").read_text())["entries"]
        return run, str(orch_entries[-1]["digest"])

    def build_frozen_release(self) -> tuple[Path, Path, str]:
        """A ready run plus one frozen release; returns (run, rel_dir, digest)."""
        run, agg = self.release_ready_run()
        frozen = self.freeze_release(run, agg)
        rel_digest = frozen.stdout.split("froze release ")[1].split()[0]
        rel_dir = run / ".release" / rel_digest.split(":")[-1][:12]
        self.assertTrue(rel_dir.is_dir())
        return run, rel_dir, rel_digest

    def test_final_packet_binds_integration_verification(self) -> None:
        """A standard packet names the aggregate verification, command, and outputs."""
        run = self.init5()
        self.submit_orch_verified(run)
        packet = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        self.assertIn("packet: standard", packet)
        self.assertIn("## Integration verification", packet)
        orch_entries = json.loads((run / ".bundles" / "orch" / "rounds.json").read_text())["entries"]
        self.assertIn(orch_entries[-1]["digest"], packet)
        self.assertIn("verification: passed", packet)
        self.assertIn('printf "orch ok', packet)
        self.assertIn("verify.stdout", packet)
        # A correction packet is never final qualification.
        conflict = self.cli("review-packet", "demo", "--role", "reviewer",
                            "--correction", "T01", "--final", ok=False)
        self.assertNotEqual(0, conflict.returncode)

    def test_final_packet_needs_frozen_release(self) -> None:
        """Task and aggregate evidence alone cannot pass as final qualification."""
        run = self.init5()
        self.submit_orch_verified(run)
        missing = self.cli("review-packet", "demo", "--role", "reviewer",
                           "--final", ok=False)
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("no frozen release", missing.stderr)

    def test_freeze_refuses_skipped_or_blocked_integration(self) -> None:
        """Skipped or blocked integration evidence cannot anchor a release."""
        self.repo()
        run = self.init5()
        self.submit5(run, "T01")
        self.submit5(run, "T02")
        for owner in ("T01", "T02"):
            self.cli("verify", "demo", owner, "--result", "pass", "--as", "verifier")
        # Delivery is qualified against a real orchestrator event: under
        # five-role the review batch exists once verification resolves.
        self.release_setup(run)
        for owner in ("T01", "T02"):
            self.cli("decide", "demo", owner, "--approve", "--as", "reviewer")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_text = orch_report.read_text().replace("| T01 | approved | passed |",
                                                    "| T01 | approved | passed |\n| T02 | approved | passed |")
        orch_report.write_text(orch_text)
        self.cli("submit", "demo", "orch", "--skip-verify", "--skip-verify-reason",
                 "covered by task evidence", "--as", "orchestrator")
        skipped = self.freeze_release(run, ok=False)
        self.assertNotEqual(0, skipped.returncode)
        self.assertIn("not passed", skipped.stderr)
        self.assertIn("skipped", skipped.stderr)

    def test_freeze_refuses_blocked_integration(self) -> None:
        """Blocked integration evidence cannot anchor a release."""
        self.repo()
        run = self.init5()
        self.submit5(run, "T01")
        self.submit5(run, "T02")
        for owner in ("T01", "T02"):
            self.cli("verify", "demo", owner, "--result", "pass", "--as", "verifier")
        # Delivery is qualified against a real orchestrator event: under
        # five-role the review batch exists once verification resolves.
        self.release_setup(run)
        for owner in ("T01", "T02"):
            self.cli("decide", "demo", owner, "--approve", "--as", "reviewer")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_text = orch_report.read_text().replace("| T01 | approved | passed |",
                                                    "| T01 | approved | passed |\n| T02 | approved | passed |")
        orch_text = orch_text.replace("## Decisions needed\n\nnone",
                                      "## Decisions needed\n\nWaive the slow integration job?")
        orch_report.write_text(orch_text)
        self.cli("submit", "demo", "orch", "--blocked", "--as", "orchestrator")
        blocked = self.freeze_release(run, ok=False)
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn("not passed", blocked.stderr)
        self.assertIn("blocked", blocked.stderr)

    def test_suite_qualify_captures_green(self) -> None:
        """A green run freezes command bytes, times, outputs, and one source tree."""
        run = self.init("split", evidence_mode="documents-only")
        artifact = self.qualify_suite(run)
        record = json.loads(artifact.read_text())
        self.assertTrue(record["digest"].startswith("sha256:"))
        self.assertEqual("demo", record["run"])
        self.assertIn("printf", record["command"])
        self.assertTrue(record["cwd"])
        self.assertLessEqual(record["started_at"], record["ended_at"])
        self.assertEqual(0, record["exit"])
        self.assertEqual(164, record["tests"])
        self.assertEqual(0, record["failures"])
        self.assertTrue(record["ok"])
        self.assertEqual(record["source_before"], record["source_after"])
        out = run / ".suite" / record["stdout_file"]
        self.assertEqual(sha(out.read_bytes()), record["stdout_digest"])
        self.assertIn("Ran 164 tests", out.read_text())

    def test_suite_qualify_refuses_drift_and_unmeasurable(self) -> None:
        """Drift during the run and summary-less output freeze no artifact."""
        self.repo()
        run = self.init("split", evidence_mode="documents-only")
        dude = self.cli("suite", "demo", "--qualify", "--command", "echo hello", ok=False)
        self.assertNotEqual(0, dude.returncode)
        self.assertIn("no supported summary", dude.stderr)
        drifted = self.cli("suite", "demo", "--qualify", "--command",
                           "printf 'Ran 1 tests in 1s\\n\\nOK\\n' >> drift.txt "
                           "&& printf 'Ran 1 tests in 1s\\n\\nOK\\n'", ok=False)
        self.assertNotEqual(0, drifted.returncode)
        self.assertIn("drifted", drifted.stderr)
        self.assertEqual([], list((run / ".suite").glob("qual-*.json")))

    def test_suite_qualify_records_red(self) -> None:
        """A failing run still freezes an honest artifact for release to refuse."""
        run = self.init("split", evidence_mode="documents-only")
        artifact = self.qualify_suite(run, passed=1, failed=2, exit_code=1)
        record = json.loads(artifact.read_text())
        self.assertEqual(1, record["exit"])
        self.assertEqual(3, record["tests"])
        self.assertEqual(2, record["failures"])
        self.assertFalse(record["ok"])

    def test_suite_problems_rejects_tampering(self) -> None:
        """Edited commands, times, trees, and outputs fail validation with reasons."""
        mod = load_cli("docket_suite_mod")
        run = self.init("split", evidence_mode="documents-only")
        artifact = self.qualify_suite(run)
        base = json.loads(artifact.read_text())

        def check(mutated: dict, needle: str) -> None:
            payload = {k: v for k, v in mutated.items() if k != "digest"}
            mutated["digest"] = "sha256:" + hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            path = artifact.parent / "case.json"
            path.write_text(json.dumps(mutated, indent=2, sort_keys=True))
            try:
                problems = mod.suite_problems(path, "demo")
            finally:
                path.unlink(missing_ok=True)
            self.assertTrue(problems, f"expected a problem containing {needle!r}")
            self.assertTrue(any(needle in problem for problem in problems),
                            f"{needle!r} not in {problems}")

        red = dict(base, exit=1)
        check(red, "contradicts")
        backwards = dict(base, started_at="2026-09-08T00:02:00Z",
                             ended_at="2026-09-08T00:01:00Z")
        check(backwards, "backwards")
        moved = dict(base, source_before="sha256:" + "0" * 64)
        check(moved, "drifted")
        no_cwd = dict(base, cwd="")
        check(no_cwd, "working directory")
        foreign = mod.suite_problems(artifact, "other")
        self.assertTrue(any("belongs to run" in problem for problem in foreign))
        out_path = artifact.parent / base["stdout_file"]
        saved = out_path.read_bytes()
        out_path.write_bytes(saved + b"tamper")
        try:
            damaged = mod.suite_problems(artifact, "demo")
        finally:
            out_path.write_bytes(saved)
        self.assertTrue(any("damaged" in problem for problem in damaged))

    def test_suite_qualify_captures_stderr_and_refuses_ambiguous_streams(self) -> None:
        """A unittest summary on stderr qualifies; both-stream summaries refuse."""
        run = self.init("split", evidence_mode="documents-only")
        # Summary on stderr only (the real unittest location) qualifies.
        err_only = self.cli("suite", "demo", "--qualify", "--command",
                            "printf 'Ran 1 tests in 1s\\n\\nOK\\n' >&2")
        self.assertEqual(0, err_only.returncode)
        arts = sorted((run / ".suite").glob("qual-*.json"))
        self.assertTrue(arts)
        record = json.loads(arts[-1].read_text())
        self.assertEqual(1, record["tests"])
        self.assertEqual(0, record["failures"])
        self.assertTrue(record["ok"])
        # A real unittest runner writes its summary to stderr.
        real = self.cli(
            "suite", "demo", "--qualify", "--command",
            "python3 -c \"import unittest; "
            "exec('class T(unittest.TestCase):\\n def test_x(self): assert True\\n'); "
            "unittest.main(argv=['x', '-v'])\"")
        self.assertEqual(0, real.returncode)
        # Summaries in both streams are ambiguous and refuse.
        both = self.cli("suite", "demo", "--qualify", "--command",
                        "printf 'Ran 1 tests in 1s\\n\\nOK\\n'; "
                        "printf 'Ran 1 tests in 1s\\n\\nOK\\n' >&2", ok=False)
        self.assertNotEqual(0, both.returncode)
        self.assertIn("ambiguous", both.stderr)
        # Contradictory summaries refuse as well.
        contra = self.cli("suite", "demo", "--qualify", "--command",
                          "printf 'Ran 2 tests in 1s\\n\\nOK\\n'; "
                          "printf 'Ran 1 tests in 1s\\n\\nOK\\n' >&2", ok=False)
        self.assertNotEqual(0, contra.returncode)
        self.assertIn("contradictory", contra.stderr)
        # Tampered stderr is damaged, not silently trusted.
        mod = load_cli("docket_suite_stderr_mod")
        err_path = arts[-1].parent / record["stderr_file"]
        saved = err_path.read_bytes()
        err_path.write_bytes(saved + b"tamper")
        try:
            damaged = mod.suite_problems(arts[-1], "demo")
        finally:
            err_path.write_bytes(saved)
        self.assertTrue(any("damaged" in problem for problem in damaged))

    def test_suite_parser_requires_complete_terminal_verdict(self) -> None:
        """Bare counts, trailing noise, and doubled summaries never qualify."""
        run = self.init("split", evidence_mode="documents-only")
        before = sorted((run / ".suite").glob("qual-*.json"))
        bare = self.cli("suite", "demo", "--qualify", "--command",
                        "printf 'Ran 1 test in 0.001s\\n'", ok=False)
        self.assertNotEqual(0, bare.returncode)
        self.assertIn("without a terminal verdict", bare.stderr)
        noisy = self.cli("suite", "demo", "--qualify", "--command",
                         "printf 'Ran 1 tests in 1s\\n\\nOK\\nEXTRA NOISE\\n'", ok=False)
        self.assertNotEqual(0, noisy.returncode)
        self.assertIn("after the terminal unittest status", noisy.stderr)
        doubled = self.cli("suite", "demo", "--qualify", "--command",
                           "printf 'Ran 1 tests in 1s\\n\\nOK\\nRan 1 tests in 1s\\n\\nOK\\n'",
                           ok=False)
        self.assertNotEqual(0, doubled.returncode)
        self.assertIn("terminal unittest status", doubled.stderr)
        self.assertEqual(before, sorted((run / ".suite").glob("qual-*.json")))
        self.assertNotIn("qualified suite", bare.stdout + noisy.stdout + doubled.stdout)
        # A real unittest discovery run qualifies from its stderr summary.
        tiny = self.root / "tiny-suite"
        (tiny / "test_tiny.py").parent.mkdir(parents=True, exist_ok=True)
        (tiny / "test_tiny.py").write_text(
            "import unittest\n"
            "class Tiny(unittest.TestCase):\n"
            "    def test_holds(self):\n"
            "        self.assertEqual(1 + 1, 2)\n")
        real = self.cli("suite", "demo", "--qualify", "--command",
                        f"python3 -m unittest discover -s {tiny} -v")
        self.assertEqual(0, real.returncode)
        self.assertIn("qualified suite", real.stdout)
        arts = sorted((run / ".suite").glob("qual-*.json"))
        record = json.loads(arts[-1].read_text())
        self.assertEqual(1, record["tests"])
        self.assertEqual(0, record["failures"])
        self.assertTrue(record["ok"])
        stderr_text = (arts[-1].parent / record["stderr_file"]).read_text()
        self.assertIn("Ran 1 test", stderr_text)
        self.assertIn("OK", stderr_text.split("Ran 1 test")[-1])
        # One real test from this repository's own suite qualifies as well.
        # (A full tests/test.sh run nests the whole suite including this very
        # test, so the regression scopes the real runner to one test instead.)
        repo = Path(DOCKET).parents[3]
        single = self.cli(
            "suite", "demo", "--qualify", "--command",
            "PYTHONPATH=" + str(repo) + " python3 -m unittest "
            "skills.docket.tests.test_docket.DocketCLI."
            "test_supported_harnesses_and_topology_are_recorded -v")
        self.assertEqual(0, single.returncode)
        self.assertIn("qualified suite", single.stdout)

    def test_suite_parser_refuses_summary_tokens_in_either_stream(self) -> None:
        """Summary-shaped output beside a valid summary refuses the capture.

        A green stream cannot excuse a count or verdict in the other stream
        that forms no summary of its own: the two streams then disagree about
        what the run did, and choosing the greener one is guessing.
        """
        run = self.init("split", evidence_mode="documents-only")
        before = sorted((run / ".suite").glob("qual-*.json"))
        valid_err = "printf 'Ran 1 test in 0.001s\\n\\nOK\\n' >&2"
        cases = {
            "FAILED (failures=99)": "contradictory verdict",
            "Ran 999 tests": "bare count",
            "OK": "stray terminal status",
            "FAILED": "bare failing verdict",
        }
        for stray, label in cases.items():
            with self.subTest(stray=label):
                refused = self.cli(
                    "suite", "demo", "--qualify", "--command",
                    f"printf '{stray}\\n'; {valid_err}", ok=False)
                self.assertNotEqual(0, refused.returncode)
                self.assertIn("summary material that forms no complete terminal "
                              "summary", refused.stderr)
                self.assertNotIn("qualified suite", refused.stdout)
        # Ordinary progress output beside the valid summary still qualifies.
        allowed = self.cli("suite", "demo", "--qualify", "--command",
                           "printf 'building index\\nrunning checks\\n'; " + valid_err)
        self.assertEqual(0, allowed.returncode)
        self.assertIn("qualified suite", allowed.stdout)
        # Nothing was frozen for any refused capture.
        self.assertEqual(len(before) + 1,
                         len(sorted((run / ".suite").glob("qual-*.json"))))

    def test_suite_green_needs_the_verdict_and_the_exit_status(self) -> None:
        """A complete FAILED summary never turns green because a wrapper exits 0."""
        run = self.init("split", evidence_mode="documents-only")
        mod = self.docket_mod()
        # A failing verdict whose counted failures are zero still is not green.
        zero_count = self.cli(
            "suite", "demo", "--qualify", "--command",
            "printf 'Ran 3 tests in 1s\\n\\nFAILED (unexpected successes=1)\\n' >&2; "
            "exit 0")
        self.assertEqual(0, zero_count.returncode)
        artifact = sorted((run / ".suite").glob("qual-*.json"))[-1]
        record = json.loads(artifact.read_text())
        self.assertEqual(0, record["exit"])
        self.assertEqual(0, record["failures"])
        self.assertFalse(record["ok"])
        self.assertFalse([item for item in mod.suite_problems(artifact, "demo")
                          if "ok flag" in item or "refusing" in item])
        # A counted failure with a zero exit is honest evidence and still red.
        counted = self.cli(
            "suite", "demo", "--qualify", "--command",
            "printf 'Ran 3 tests in 1s\\n\\nFAILED (failures=2)\\n' >&2; exit 0")
        self.assertEqual(0, counted.returncode)
        artifact2 = sorted((run / ".suite").glob("qual-*.json"))[-1]
        record2 = json.loads(artifact2.read_text())
        self.assertEqual(2, record2["failures"])
        self.assertFalse(record2["ok"])
        self.assertFalse([item for item in mod.suite_problems(artifact2, "demo")
                          if "ok flag" in item or "refusing" in item])
        # A hand-flipped ok flag contradicts the frozen output and refuses.
        forged = dict(record2, ok=True)
        payload = {k: v for k, v in forged.items() if k != "digest"}
        forged["digest"] = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        path = artifact2.parent / "qual-forged.json"
        path.write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n")
        self.assertTrue([item for item in mod.suite_problems(path, "demo")
                         if "ok flag" in item or "refusing" in item])

    def test_unknown_source_identities_are_unqualified_and_block_release(self) -> None:
        """Equal-unknown and SHA-versus-unknown sources never support release."""
        mod = load_cli("docket_unknown_mod")
        # Suite artifacts frozen outside git bind equal unknown trees and are
        # honest diagnostics, but they are unqualified release evidence.
        run = self.init("split", evidence_mode="documents-only")
        artifact = self.qualify_suite(run)
        base = json.loads(artifact.read_text())
        self.assertTrue(str(base.get("source_before", "")).startswith("unknown "))
        self.assertEqual(base["source_before"], base["source_after"])
        equal_unknown = mod.suite_problems(artifact, "demo")
        self.assertTrue(any("unknown" in problem for problem in equal_unknown),
                        f"equal-unknown suite must be unqualified, got {equal_unknown}")
        # A SHA-versus-unknown mismatch is drift, never a match.
        mutated = dict(base, source_after="sha256:" + "0" * 64)
        payload = {k: v for k, v in mutated.items() if k != "digest"}
        mutated["digest"] = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        path = artifact.parent / "unknown-case.json"
        path.write_text(json.dumps(mutated, indent=2, sort_keys=True))
        try:
            mixed = mod.suite_problems(path, "demo")
        finally:
            path.unlink(missing_ok=True)
        self.assertTrue(mixed)
        # Delivery qualification with an unknown tree is unqualified as well.
        qual = {"run": "demo", "role": "orchestrator", "status": "passed",
                "mode": "manual", "started_at": "2026-09-08T00:00:00Z",
                "finished_at": "2026-09-08T00:00:01Z",
                "checks": [{"check": name, "status": "passed",
                            "detail": "test detail", "pid": 1, "exit": 0,
                            "argv": ["test"], "pane_id": "pane-1"}
                           for name in ("hook-announce", "hook-kill-restart",
                                        "claim", "send", "ack", "generation-reuse",
                                        "retry-release", "active-input",
                                        "disposable-pane")],
                "event": {"key": "k", "message": "m"},
                "session": {"id": "s", "generation": "1"},
                "probe": {"herdr_version": "test"},
                "repo_head": "abc", "source_tree": "unknown (not a git checkout)"}
        frozen_qual = mod.freeze_record(dict(qual))
        qual_path = run / ".delivery" / "qualification-orchestrator.json"
        qual_path.parent.mkdir(parents=True, exist_ok=True)
        qual_path.write_text(json.dumps(frozen_qual, indent=2, sort_keys=True))
        try:
            qual_problems = mod.qualification_problems(run, "demo", "orchestrator")
        finally:
            qual_path.unlink(missing_ok=True)
        self.assertTrue(any("unknown" in problem for problem in qual_problems),
                        f"unknown delivery source must be unqualified, got {qual_problems}")
        # Frozen-release validation reports unknown sources even when live is
        # also unknown (the equal-unknown bypass is closed).
        self.assertTrue(str(mod.worktree_identity()).startswith("unknown ")
                        or str(mod.worktree_identity()).startswith("sha256:"))
        fake_manifest = {"run": "demo", "digest": "sha256:" + "0" * 64,
                         "suite": {"source": "unknown (not a git checkout)"}}
        fake_dir = run / ".release" / "unknown-probe"
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "manifest.json").write_text(json.dumps(
            mod.freeze_record(dict(fake_manifest)), indent=2, sort_keys=True))
        try:
            frozen_problems = mod.validate_frozen_release(
                run, "demo", fake_dir,
                json.loads((fake_dir / "manifest.json").read_text()))
        finally:
            import shutil as _shutil
            _shutil.rmtree(fake_dir, ignore_errors=True)
        self.assertTrue(frozen_problems)

    def test_resolve_suite_artifact_missing_and_multiple(self) -> None:
        """Resolution names absence and ambiguity instead of guessing."""
        mod = load_cli("docket_suite_mod2")
        run = self.init("split", evidence_mode="documents-only")
        missing, problems = mod.resolve_suite_artifact(run, "")
        self.assertIsNone(missing)
        self.assertTrue(any("no suite qualification artifact" in problem for problem in problems))
        artifact = self.qualify_suite(run)
        (run / ".suite" / "qual-copy.json").write_bytes(artifact.read_bytes())
        try:
            dup, problems = mod.resolve_suite_artifact(run, "")
            self.assertIsNone(dup)
            self.assertTrue(any("multiple" in problem for problem in problems))
        finally:
            (run / ".suite" / "qual-copy.json").unlink(missing_ok=True)

    def test_integration_validator_refuses_failing_status(self) -> None:
        """A failing status can never be frozen, so the validator is probed directly."""
        run = self.init5()
        self.submit_orch_verified(run)
        orch_entries = json.loads((run / ".bundles" / "orch" / "rounds.json").read_text())["entries"]
        bdir = run / ".bundles" / "orch" / orch_entries[-1]["dir"]
        mod = load_cli("docket_mod")
        manifest = json.loads((bdir / "bundle.json").read_text())
        manifest["verification"] = dict(manifest["verification"], status="failed")
        failing = mod.integration_verification_problems(run, orch_entries[-1], manifest)
        self.assertTrue(failing)
        self.assertIn("failed", failing[0])

    def test_release_freeze_and_final_packet_full(self) -> None:
        """Freeze refuses red inputs, then a pinned packet renders every handoff field."""
        self.repo()
        run = self.init5()
        self.submit5(run, "T01")
        self.submit5(run, "T02")
        for owner in ("T01", "T02"):
            self.cli("verify", "demo", owner, "--result", "pass", "--as", "verifier")
        # Delivery is qualified against a real orchestrator event: under
        # five-role the review batch exists once verification resolves.
        self.release_setup(run)
        for owner in ("T01", "T02"):
            self.cli("decide", "demo", owner, "--approve", "--as", "reviewer")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_text = orch_report.read_text().replace("| T01 | approved | passed |",
                                                    "| T01 | approved | passed |\n| T02 | approved | passed |")
        orch_report.write_text(orch_text)
        self.cli("submit", "demo", "orch", "--as", "orchestrator")
        orch_entries = json.loads((run / ".bundles" / "orch" / "rounds.json").read_text())["entries"]
        agg_digest = orch_entries[-1]["digest"]
        shutil.rmtree(run / ".suite")
        self.qualify_suite(run, passed=1, failed=2, exit_code=1)
        red = self.freeze_release(run, agg_digest, ok=False)
        self.assertNotEqual(0, red.returncode)
        self.assertIn("red suite", red.stderr)
        shutil.rmtree(run / ".suite")
        self.qualify_suite(run)
        bogus = self.freeze_release(run, "sha256:" + "f" * 64, ok=False)
        self.assertNotEqual(0, bogus.returncode)
        self.assertIn("never invent", bogus.stderr)
        frozen = self.freeze_release(run, agg_digest)
        self.assertIn("froze release sha256:", frozen.stdout)
        rel_digest = frozen.stdout.split("froze release ")[1].split()[0]
        packet = self.cli("review-packet", "demo", "--role", "reviewer",
                          "--final").stdout
        self.assertIn("Final review packet", packet)
        self.assertIn("packet: final", packet)
        self.assertIn(f"release: {rel_digest}", packet)
        self.assertIn("## Milestone inventory (M4-M11)", packet)
        self.assertIn(f"- M11: bundle {agg_digest}", packet)
        self.assertIn("- M4: unavailable (historical", packet)
        self.assertIn("## Full-suite result (frozen)", packet)
        self.assertIn("printf 'Ran 164 tests", packet)
        self.assertIn("164", packet)
        self.assertIn("log digest:", packet)
        self.assertIn("## Real-adapter result (frozen qualifications)", packet)
        self.assertIn("orchestrator", packet)
        self.assertIn("## Installation state (live check)", packet)
        self.assertIn(agg_digest, packet)
        self.assertIn("verification: passed", packet)
        # A damaged frozen file refuses the packet it would have supported.
        rel_dirs = [p for p in (run / ".release").glob("*/manifest.json")]
        self.assertEqual(1, len(rel_dirs))
        frozen_stdout = rel_dirs[0].parent / "suite" / "stdout.txt"
        saved = frozen_stdout.read_bytes()
        frozen_stdout.write_bytes(saved + b"tamper")
        try:
            damaged = self.cli("review-packet", "demo", "--role", "reviewer",
                               "--final", ok=False)
            self.assertNotEqual(0, damaged.returncode)
            self.assertIn("DAMAGED", damaged.stderr)
        finally:
            frozen_stdout.write_bytes(saved)
        # A red suite smuggled past freezing with a recomputed digest still refuses.
        manifest_path = rel_dirs[0].parent / "manifest.json"
        release_parent = manifest_path.parent
        saved_manifest = manifest_path.read_bytes()
        manifest = json.loads(saved_manifest.decode())
        manifest["suite"] = dict(manifest["suite"], exit=1, failures=2)
        payload = {k: v for k, v in manifest.items() if k != "digest"}
        manifest["digest"] = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        # Rename the directory to the recomputed address so only the suite
        # content can be what refuses it, never the address binding.
        smuggled_dir = release_parent.parent / manifest["digest"].split(":")[-1][:12]
        os.rename(release_parent, smuggled_dir)
        try:
            smuggled = self.cli("review-packet", "demo", "--role", "reviewer",
                                "--final", ok=False)
            self.assertNotEqual(0, smuggled.returncode)
            self.assertIn("red", smuggled.stderr)
        finally:
            os.rename(smuggled_dir, release_parent)
            manifest_path.write_bytes(saved_manifest)
        # A commit after freezing makes the recorded source revisions stale.
        self.git("add", "-A", cwd=self.root)
        self.git("commit", "-qm", "post-freeze edit", cwd=self.root)
        stale = self.cli("review-packet", "demo", "--role", "reviewer",
                         "--final", ok=False)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("stale", stale.stderr)

    def test_evaluation_subsystem_is_retired(self) -> None:
        """The pilot, fixtures, dashboard, and pilot plans are gone, not stubbed."""
        repo_eval = Path(__file__).parents[1] / "eval"
        self.assertFalse(repo_eval.exists(), f"{repo_eval} still exists")
        for rel in ("tests/test_evaluation_dashboard.py",):
            self.assertFalse((Path(__file__).parents[1] / rel).exists())
        root = Path(__file__).parents[3]
        for rel in ("docs/evaluation-dashboard.md",
                    "improvements/evaluation_dashboard.py",
                    "improvements/model-combination-evaluation-plan.md",
                    "improvements/model-role-combination-pilot-plan.md"):
            self.assertFalse((root / rel).exists(), f"{rel} still exists")
        self.assertFalse((root / "improvements" / "review-artifacts").exists())
        top = self.cli("--help").stdout
        self.assertNotIn("pilot", top)
        refused = self.cli("pilot", "demo", "--list-fixtures", ok=False)
        self.assertNotEqual(0, refused.returncode)
        metrics_help = self.cli("metrics", "--help").stdout
        self.assertNotIn("--release", metrics_help)
        self.assertNotIn("pilot", metrics_help.lower())

    def test_metrics_reports_operational_usage_without_evaluation(self) -> None:
        """Metrics keeps ordinary accounting and never reads evaluation artifacts."""
        run = self.init5()
        self.submit5(run, "T01")
        out = self.cli("metrics", "demo").stdout
        self.assertIn("tasks decided:", out)
        self.assertIn("prompt renders:", out)
        self.assertIn("wall time:", out)
        self.assertIn("delivery qualification:", out)
        lowered = out.lower()
        self.assertNotIn("pilot", lowered)
        self.assertNotIn("adjudication", lowered)
        self.assertNotIn("comparison", lowered)
        self.assertNotIn("seeded", lowered)

    def test_final_packet_ignores_live_doc_edits(self) -> None:
        """Later live edits never silently change a packet under the old digest."""
        self.repo()
        run = self.init5()
        self.submit5(run, "T01")
        self.submit5(run, "T02")
        for owner in ("T01", "T02"):
            self.cli("verify", "demo", owner, "--result", "pass", "--as", "verifier")
        # Delivery is qualified against a real orchestrator event: under
        # five-role the review batch exists once verification resolves.
        self.release_setup(run)
        for owner in ("T01", "T02"):
            self.cli("decide", "demo", owner, "--approve", "--as", "reviewer")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_text = orch_report.read_text().replace("| T01 | approved | passed |",
                                                    "| T01 | approved | passed |\n| T02 | approved | passed |")
        orch_report.write_text(orch_text)
        self.cli("submit", "demo", "orch", "--as", "orchestrator")
        orch_entries = json.loads((run / ".bundles" / "orch" / "rounds.json").read_text())["entries"]
        self.freeze_release(run, orch_entries[-1]["digest"])
        before = self.cli("review-packet", "demo", "--role", "reviewer", "--final").stdout
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("## Risks", "## Risks\n\nAFTER-FREEZE-RISK"))
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace("The feature works.", "The feature works well."))
        finding = run / "T01-verification-01.mdx"
        finding.write_text(finding.read_text() + "\nAfter-freeze annotation.\n")
        after = self.cli("review-packet", "demo", "--role", "reviewer", "--final").stdout
        self.assertEqual(before, after)
        self.assertNotIn("AFTER-FREEZE-RISK", after)

    def test_release_source_drift_freeze_and_packet(self) -> None:
        """A moved source tree refuses freezing and, later, the packet itself."""
        self.repo()
        self.init("split", evidence_mode="git")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/a.py",
                 "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        self.fake_herdr_panes()
        self.cli("delivery", "demo", "--role", "orchestrator", "--qualify")
        self.qualify_suite(run)
        self.cli("decide", "demo", "T01", "--approve")
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        self.cli("submit", "demo", "orch")
        (self.root / "src" / "a.py").write_text("allowed = 999\n")
        drifted = self.freeze_release(run, ok=False)
        self.assertNotEqual(0, drifted.returncode)
        self.assertIn("re-qualify", drifted.stderr)
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        orch_entries = json.loads((run / ".bundles" / "orch" / "rounds.json").read_text())["entries"]
        self.freeze_release(run, orch_entries[-1]["digest"])
        good = self.cli("review-packet", "demo", "--role", "reviewer", "--final")
        self.assertEqual(0, good.returncode)
        (self.root / "src" / "a.py").write_text("allowed = 999\n")
        moved = self.cli("review-packet", "demo", "--role", "reviewer",
                         "--final", ok=False)
        self.assertNotEqual(0, moved.returncode)
        self.assertIn("moved", moved.stderr)
        self.assertIn("suite (frozen", moved.stderr)

    def test_freeze_refuses_stale_suite_source(self) -> None:
        """A suite artifact newer than the reviewed tree cannot anchor a release."""
        self.repo()
        run = self.init("split", evidence_mode="documents-only")
        self.submit_simple(run, "T01")
        self.fake_herdr_panes()
        self.cli("delivery", "demo", "--role", "orchestrator", "--qualify")
        self.qualify_suite(run)
        (self.root / "src" / "a.py").write_text("allowed = 999\n")
        newer = self.qualify_suite(run)
        (self.root / "src" / "a.py").write_text("allowed = 1\n")
        stale = self.freeze_release(run, suite_artifact=str(newer), ok=False)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("stale", stale.stderr)
        self.assertIn("rerun it", stale.stderr)

    def test_final_packet_refuses_fabricated_release(self) -> None:
        """A hand-written release manifest never passes as pinned evidence."""
        run = self.init5()
        self.submit_orch_verified(run)
        rel = run / ".release" / "deadbeefcafe"
        rel.mkdir(parents=True)
        manifest: dict[str, object] = {
            "run": "demo", "frozen_at": "2026-09-08T00:00:00Z",
            "aggregate": {"owner": "orch", "round": 1,
                          "digest": "sha256:" + "f" * 64},
            "constituents": [], "qualifications": {}, "suite": {},
            "milestones": {}, "repo_head": "unknown", "files": {},
        }
        payload = {k: v for k, v in manifest.items() if k != "digest"}
        manifest["digest"] = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        (rel / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        fabricated = self.cli("review-packet", "demo", "--role", "reviewer",
                              "--final", "--release", "deadbeefcafe", ok=False)
        self.assertNotEqual(0, fabricated.returncode)

    def test_frozen_release_damage_refuses_each_dependency_class(self) -> None:
        """Every frozen dependency class is bound; damaging one refuses DAMAGED.

        One representative of each class the release carries - suite,
        qualifications, documents, and every frozen bundle class (manifest, report, patch,
        baseline, verification output, ledger, and object store) - is altered
        in place and the final packet must refuse.
        """
        run, rel_dir, _ = self.build_frozen_release()
        bundles = rel_dir / "bundles"
        aggregate_dir = sorted((bundles / "orch").glob("*/*/bundle.json"))[0].parent
        constituent_dirs = [p.parent for p in
                            sorted(bundles.glob("*/*/*/bundle.json"))
                            if p.parent != aggregate_dir]
        constituent = constituent_dirs[0]
        cases: list[tuple[str, Path]] = [
            ("suite artifact", rel_dir / "suite" / "artifact.json"),
            ("suite stdout", rel_dir / "suite" / "stdout.txt"),
            ("qualification", sorted((rel_dir / "qualifications").glob("*.json"))[0]),
            ("release plan", rel_dir / "documents" / "plan.mdx"),
            ("aggregate bundle manifest", aggregate_dir / "bundle.json"),
            ("constituent report", constituent / "report.mdx"),
            ("bundle baseline",
             next(p for p in sorted(constituent.rglob("snapshot.json"))
                  if "baseline" in p.parts)),
            ("integration verification output",
             next(p for p in sorted(aggregate_dir.glob("verify.*")))),
            ("bundle ledger", bundles / "orch" / "rounds.json"),
        ]
        object_files = [p for p in sorted((bundles / "objects").rglob("*"))
                        if p.is_file() and p.parent.name not in ("info", "pack")]
        if object_files:
            cases.append(("bundle object store", object_files[0]))
        patch_files = sorted(constituent.glob("*.patch"))
        if patch_files:
            cases.append(("constituent patch", patch_files[0]))
        for label, path in cases:
            with self.subTest(dependency=label):
                self.assert_disposable(path)
                saved = path.read_bytes()
                path.write_bytes(saved + b"tamper")
                try:
                    refused = self.cli("review-packet", "demo", "--role", "reviewer",
                                       "--final", ok=False)
                finally:
                    path.write_bytes(saved)
                self.assertNotEqual(0, refused.returncode)
                self.assertIn("DAMAGED", refused.stderr)
        # The untouched release still renders after every probe is restored.
        good = self.cli("review-packet", "demo", "--role", "reviewer", "--final")
        self.assertEqual(0, good.returncode)

    def test_frozen_release_inventory_refuses_added_removed_and_mode(self) -> None:
        """The release inventory binds additions, removals, and modes.

        An undeclared frozen file, a removed manifest entry, and a changed mode
        each move the complete inventory and refuse
        with DAMAGED rather than being silently accepted.
        Retargeted-link inventory stays covered by baseline symlink capture;
        the frozen release tree holds only regular files after pilot retirement.
        """
        run, rel_dir, _ = self.build_frozen_release()
        # An undeclared file inside the frozen release.
        undeclared = rel_dir / "undeclared.txt"
        undeclared.write_text("not in any manifest\n")
        try:
            added = self.cli("review-packet", "demo", "--role", "reviewer",
                             "--final", ok=False)
            self.assertNotEqual(0, added.returncode)
            self.assertIn("DAMAGED", added.stderr)
        finally:
            undeclared.unlink()
        # A removed manifest entry: delete a frozen file the manifest lists.
        card = rel_dir / "suite" / "artifact.json"
        self.assert_disposable(card)
        saved_card = card.read_bytes()
        card.unlink()
        try:
            removed = self.cli("review-packet", "demo", "--role", "reviewer",
                               "--final", ok=False)
            self.assertNotEqual(0, removed.returncode)
            self.assertIn("DAMAGED", removed.stderr)
        finally:
            card.write_bytes(saved_card)
        # A mode-only change on a frozen tree file.
        task_file = rel_dir / "suite" / "stdout.txt"
        self.assert_disposable(task_file)
        saved_mode = stat.S_IMODE(task_file.stat().st_mode)
        os.chmod(task_file, saved_mode ^ 0o100)
        try:
            changed = self.cli("review-packet", "demo", "--role", "reviewer",
                               "--final", ok=False)
            self.assertNotEqual(0, changed.returncode)
            self.assertIn("DAMAGED", changed.stderr)
        finally:
            os.chmod(task_file, saved_mode)
        good = self.cli("review-packet", "demo", "--role", "reviewer", "--final")
        self.assertEqual(0, good.returncode)

    def test_frozen_release_atomic_faults_and_retries(self) -> None:
        """One rename commits a complete release; retries converge or refuse.

        Faults before the rename leave no final address at all; a fault after
        it leaves a complete unit. Re-freezing identical content converges on
        the existing address only after full validation, and conflicting
        content at that address refuses.
        """
        run, agg = self.release_ready_run()
        release_base = run / ".release"

        def finals() -> list[Path]:
            if not release_base.is_dir():
                return []
            return [p for p in release_base.glob("*/manifest.json")
                    if not p.parent.name.startswith(".")]

        for point in ("release:stage", "release:write:suite/artifact.json",
                      "release:write:manifest.json", "release:rename"):
            with self.subTest(fault=point):
                interrupted = self.cli("release", "demo", "--freeze",
                                       "--milestone", f"M11={agg}",
                                       fault=point, ok=False)
                self.assertNotEqual(0, interrupted.returncode)
                self.assertIn("injected fault", interrupted.stderr)
                self.assertEqual([], finals(),
                                 f"a fault at {point} left a final release")
        frozen = self.freeze_release(run, agg)
        rel_digest = frozen.stdout.split("froze release ")[1].split()[0]
        rel_dir = release_base / rel_digest.split(":")[-1][:12]
        self.assertTrue((rel_dir / "manifest.json").is_file())
        # An identical retry converges on the same address after validation.
        again = self.freeze_release(run, agg)
        self.assertIn("already frozen", again.stdout)
        self.assertIn(rel_digest, again.stdout)
        self.assertEqual(1, len(finals()))
        # Conflicting content at the address refuses rather than overwriting.
        manifest_path = rel_dir / "manifest.json"
        saved_manifest = manifest_path.read_bytes()
        conflicting = json.loads(saved_manifest.decode())
        conflicting["repo_head"] = "unknown (conflicting rewrite)"
        payload = {k: v for k, v in conflicting.items() if k != "digest"}
        conflicting["digest"] = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifest_path.write_text(json.dumps(conflicting, indent=2, sort_keys=True))
        try:
            conflict = self.freeze_release(run, agg, ok=False)
            self.assertNotEqual(0, conflict.returncode)
            self.assertIn("different content", conflict.stderr)
        finally:
            manifest_path.write_bytes(saved_manifest)
        # A damaged published unit refuses a retry instead of being trusted.
        card = rel_dir / "suite" / "artifact.json"
        saved_card = card.read_bytes()
        card.write_bytes(saved_card + b"tamper")
        try:
            damaged = self.freeze_release(run, agg, ok=False)
            self.assertNotEqual(0, damaged.returncode)
            self.assertIn("unusable", damaged.stderr)
        finally:
            card.write_bytes(saved_card)
        # A fault after the rename leaves the complete unit, not a partial one.
        self.assert_disposable(rel_dir)
        shutil.rmtree(rel_dir)
        interrupted = self.cli("release", "demo", "--freeze",
                               "--milestone", f"M11={agg}",
                               fault="release:renamed", ok=False)
        self.assertNotEqual(0, interrupted.returncode)
        self.assertIn("injected fault", interrupted.stderr)
        self.assertTrue(rel_dir.is_dir())
        manifest = json.loads((rel_dir / "manifest.json").read_text())
        self.assertEqual(rel_digest, manifest["digest"])
        self.assertEqual(1, len(finals()))

    def test_installation_state_reports_not_installed(self) -> None:
        """The installation check names absence instead of assuming setup."""
        mod = load_cli("docket_mod2")
        saved_home = os.environ.get("HOME", "")
        os.environ["HOME"] = str(self.root / "nohome")
        try:
            self.assertIn("not installed", mod.installation_state())
        finally:
            os.environ["HOME"] = saved_home

    def write_qualification(self, rundir: Path, role: str, **over: object) -> Path:
        """Hand-craft a fully valid qualification artifact, then freeze it."""
        import hashlib as _hashlib
        checks = []
        for name in ("probe", "hook-announce", "hook-kill-restart", "claim", "send",
                     "ack", "generation-reuse", "retry-release", "active-input",
                     "disposable-pane"):
            entry: dict[str, object] = {"check": name, "status": "passed",
                                        "detail": f"{name} evidence"}
            if name in ("hook-announce", "hook-kill-restart"):
                entry.update({"pid": 4242, "exit": 2 if name == "hook-announce" else -9,
                              "argv": ["watch", "demo", "--role", role]})
            elif name == "disposable-pane":
                entry.update({"pane_id": "w9:p1",
                              "argv": ["pane", "split"]})
            else:
                entry.update({"argv": ["docket", name], "exit": 0})
            checks.append(entry)
        record: dict[str, object] = {
            "run": "demo", "role": role, "status": "passed", "mode": "manual",
            "started_at": "2026-09-08T00:00:00Z",
            "finished_at": "2026-09-08T00:01:00Z", "checks": checks,
            "source_tree": "sha256:" + "0" * 64,
            "probe": {"command": "herdr agent prompt --help", "boundary": "none",
                      "mode": "manual", "detail": "manual", "herdr_version": "9.9.9"},
            "production_mode": "manual",
            "event": {"key": "review-batch:abc", "message": "ready"},
            "session": {"id": "dqd-1", "generation": 1},
            "repo_head": "unknown (not a git checkout)",
        }
        record.update(over)
        payload = {k: v for k, v in record.items() if k != "digest"}
        record["digest"] = "sha256:" + _hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path = rundir / ".delivery" / f"qualification-{role}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
        return path

    def init5_topology(self, topology: str, run_id: str = "demo") -> Path:
        """A five-role-v1 run with an explicit topology for submission tests."""
        self.cli("init", run_id, "--harness", "claude", "--topology", topology,
                 "--evidence-mode", "documents-only", "--workflow", "five-role-v1")
        return self.root / ".docket" / "runs" / run_id

    def assign_orchestrator_task(self, run: Path, owner: str, run_id: str = "demo") -> None:
        """Assign an executor orchestrator task and fill its contract."""
        fname = f"src/{owner.lower()}.py"
        self.cli(
            "assign", run_id, owner, "--complexity", "high", "--executor", "orchestrator",
            "--harness", "opencode", "--file", fname,
            "--verify", f'printf "{owner} ok\\n"',
        )
        self.fill_task(run / f"{owner}-task.mdx")

    def submit_orchestrator_task(self, run: Path, owner: str, run_id: str = "demo"):
        """Fill and submit an executor orchestrator task as orchestrator."""
        self.fill_task_report(run / f"{owner}-report-01.mdx",
                              files=f"- `src/{owner.lower()}.py:1` - implemented {owner}.")
        return self.cli("submit", run_id, owner, "--as", "orchestrator")

    def submit_aggregate(self, run: Path, run_id: str = "demo"):
        """Assign and submit the aggregate report as orchestrator."""
        self.cli(
            "assign", run_id, "orch", "--complexity", "high", "--executor",
            "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
            "--verify", 'printf "orch ok\\n"',
        )
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        return self.cli("submit", run_id, "orch", "--as", "orchestrator")

    def submit5_in(self, run: Path, run_id: str, owner: str) -> None:
        """Assign, scope, and submit one task as implementor in the named run."""
        fname = f"src/{owner.lower()}.py"
        self.cli(
            "assign", run_id, owner, "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", fname,
            "--verify", f'printf "{owner} ok\\n"',
        )
        self.fill_task(run / f"{owner}-task.mdx")
        self.fill_scope(run / f"{owner}-scope.mdx")
        self.cli("scope", run_id, owner, "--submit")
        self.fill_task_report(run / f"{owner}-report-01.mdx",
                              files=f"- `src/{owner.lower()}.py:1` - implemented {owner}.")
        self.cli("submit", run_id, owner, "--as", "implementor")

    def assign_simple_in(self, run: Path, run_id: str, owner: str) -> None:
        """Assign and scope one task without submitting it, in the named run."""
        fname = f"src/{owner.lower()}.py"
        self.cli(
            "assign", run_id, owner, "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", fname,
            "--verify", f'printf "{owner} ok\\n"',
        )
        self.fill_task(run / f"{owner}-task.mdx")
        self.fill_scope(run / f"{owner}-scope.mdx")
        self.cli("scope", run_id, owner, "--submit")

    def test_p1_five_role_submission_never_completes(self) -> None:
        """Five-role submission records submitted; legacy still records completed."""
        run = self.init5()
        self.assign_orchestrator_task(run, "T01", "demo")
        out = self.submit_orchestrator_task(run, "T01", "demo")
        self.assertIn("submitted for review", out.stdout)
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())
        self.assertNotIn("status: completed", (run / "T01-report-01.mdx").read_text())
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        self.assertIn("status: approved", (run / "T01-report-01.mdx").read_text())

        combined = self.init5_topology("combined", "demo2")
        self.submit5_in(combined, "demo2", "T01")
        self.cli("verify", "demo2", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo2", "T01", "--approve", "--as", "reviewer")
        self.submit5_in(combined, "demo2", "T02")
        self.cli("verify", "demo2", "T02", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo2", "T02", "--approve", "--as", "reviewer")
        agg_out = self.submit_aggregate(combined, "demo2")
        self.assertIn("submitted for review", agg_out.stdout)
        self.assertIn("status: submitted", (combined / "orch-report-01.mdx").read_text())
        self.assertNotIn("status: completed", (combined / "orch-report-01.mdx").read_text())
        self.cli("decide", "demo2", "orch", "--approve", "--as", "reviewer")
        self.assertIn("status: approved", (combined / "orch-report-01.mdx").read_text())

        legacy = self.init_legacy_as("demo3", "combined", evidence_mode="documents-only")
        self.cli(
            "assign", "demo3", "T01", "--complexity", "high", "--executor", "orchestrator",
            "--harness", "opencode", "--file", "src/t01.py",
            "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_task(legacy / "T01-task.mdx")
        self.fill_task_report(legacy / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        legacy_out = self.cli("submit", "demo3", "T01")
        self.assertIn("completed; recorded without a redundant handoff", legacy_out.stdout)
        self.assertIn("status: completed", (legacy / "T01-report-01.mdx").read_text())
        self.cli("assign", "demo3", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py")
        self.fill_orch_report(legacy / "orch-report-01.mdx")
        legacy_agg = self.cli("submit", "demo3", "orch", "--skip-verify")
        self.assertIn("completed; recorded without a redundant handoff", legacy_agg.stdout)
        self.assertIn("status: completed", (legacy / "orch-report-01.mdx").read_text())
        self.assertTrue((legacy / ".bundles" / "orch" / "rounds.json").is_file())

        no_key = self.init_legacy_as("demo4", "split", evidence_mode="documents-only")
        plan = no_key / "plan.mdx"
        text = plan.read_text()
        text = "\n".join(line for line in text.splitlines() if not line.startswith("workflow:"))
        plan.write_text(text + "\n")
        self.cli(
            "assign", "demo4", "T09", "--complexity", "high", "--executor", "orchestrator",
            "--harness", "opencode", "--file", "src/t09.py",
            "--verify", 'printf "T09 ok\\n"',
        )
        self.fill_task(no_key / "T09-task.mdx")
        self.fill_task_report(no_key / "T09-report-01.mdx",
                              files="- `src/t09.py:1` - implemented T09.")
        no_key_out = self.cli("submit", "demo4", "T09")
        self.assertIn("completed; recorded without a redundant handoff", no_key_out.stdout)
        self.assertIn("status: completed", (no_key / "T09-report-01.mdx").read_text())

    def test_p1_five_role_aggregate_wakes_reviewer_not_planner(self) -> None:
        """Five-role aggregate routes to reviewer; legacy aggregate stays with planner."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        self.submit_aggregate(run, "demo")
        reviewer = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        planner = self.cli("events", "demo", "--role", "planner", "--peek").stdout
        self.assertIn("orch:1:submitted", reviewer)
        self.assertNotIn("orch:1:submitted", planner)
        self.cli("propose-amendment", "demo", "T01", "--need", "decide the cache scope",
                 "--conflicts", "task says memory, repo uses disk",
                 "--evidence", "src/cache.py:40",
                 "--alternative", "allow either backend",
                 "--impact", "T01 acceptance")
        planner_after = self.cli("events", "demo", "--role", "planner", "--peek").stdout
        self.assertIn("amendment", planner_after)
        self.assertNotIn("orch:1:submitted", planner_after)

        blocked_run = self.init5_topology("split", "demo2")
        self.submit5_in(blocked_run, "demo2", "T01")
        self.cli("verify", "demo2", "T01", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo2", "T01", "--approve", "--as", "reviewer")
        self.cli(
            "assign", "demo2", "orch", "--complexity", "high", "--executor",
            "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
        )
        orch_report = blocked_run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        blocked_text = orch_report.read_text().replace(
            "## Decisions needed\n\nnone",
            "## Decisions needed\n\nWaive the slow integration job?")
        orch_report.write_text(blocked_text)
        self.cli("submit", "demo2", "orch", "--blocked", "--as", "orchestrator")
        blocked_reviewer = self.cli("events", "demo2", "--role", "reviewer", "--peek").stdout
        blocked_planner = self.cli("events", "demo2", "--role", "planner", "--peek").stdout
        self.assertIn("orch:1:blocked", blocked_reviewer)
        self.assertNotIn("orch:1:blocked", blocked_planner)

        legacy = self.init_legacy_as("demo3", "split", evidence_mode="documents-only")
        self.cli(
            "assign", "demo3", "T01", "--complexity", "high", "--executor", "orchestrator",
            "--harness", "opencode", "--file", "src/t01.py",
            "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_task(legacy / "T01-task.mdx")
        self.fill_task_report(legacy / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo3", "T01")
        self.cli("assign", "demo3", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py")
        self.fill_orch_report(legacy / "orch-report-01.mdx")
        self.cli("submit", "demo3", "orch", "--skip-verify")
        legacy_planner = self.cli("events", "demo3", "--role", "planner", "--peek").stdout
        legacy_reviewer = self.cli("events", "demo3", "--role", "reviewer", "--peek").stdout
        self.assertIn("orch:1:submitted", legacy_planner)
        self.assertIn("orchestrator submitted round 1 for review", legacy_planner)
        self.assertIn("no derived events", legacy_reviewer)

    def test_p1_verifier_resolves_event_and_batch_needs_verification(self) -> None:
        """A recorded verdict resolves its verifier event; milestone review needs pass."""
        run = self.init5()
        self.submit5(run, "T01")
        before = self.cli("events", "demo", "--role", "verifier", "--peek").stdout
        self.assertIn("T01:1:submitted", before)
        self.assign_simple(run, "T02")
        self.cli("batch", "demo", "--create", "M1", "--members", "T01,T02", "--milestone")
        self.cli("batch", "demo", "--close", "M1")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli("submit", "demo", "T02", "--as", "implementor")
        early_reviewer = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertNotIn("batch:M1:ready:", early_reviewer)
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        after = self.cli("events", "demo", "--role", "verifier", "--peek").stdout
        self.assertNotIn("T01:1:submitted", after)
        self.assertIn("T02:1:submitted", after)
        self.cli("verify", "demo", "T02", "--result", "uncertain", "--as", "verifier",
                 "--detail", "oracle partly offline")
        ready = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertIn("batch:M1:ready:1", ready)
        first_key = [line.strip().split()[-1] for line in ready.splitlines()
                     if "batch:M1:ready:" in line][0]
        self.cli("reconcile", "demo", "--role", "reviewer")
        pending = list((run / ".delivery" / "reviewer" / "pending").glob("*.json"))
        first_pending = [json.loads(p.read_text()) for p in pending
                         if json.loads(p.read_text()).get("key") == first_key][0]
        first_revision = first_pending.get("revision", "")
        self.assertTrue(str(first_revision).startswith("sha256:"))
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "changed finding with new evidence pointer")
        changed = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        changed_keys = [line.strip().split()[-1] for line in changed.splitlines()
                        if "batch:M1:ready:" in line]
        self.assertEqual(1, len(changed_keys))
        self.assertEqual(first_key, changed_keys[0])
        self.cli("reconcile", "demo", "--role", "reviewer")
        pending_after = list((run / ".delivery" / "reviewer" / "pending").glob("*.json"))
        changed_pending = [json.loads(p.read_text()) for p in pending_after
                           if json.loads(p.read_text()).get("key") == first_key][0]
        self.assertNotEqual(first_revision, changed_pending.get("revision", ""))
        same = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertEqual(changed, same)
        self.cli("reconcile", "demo", "--role", "reviewer")
        pending_same = list((run / ".delivery" / "reviewer" / "pending").glob("*.json"))
        same_pending = [json.loads(p.read_text()) for p in pending_same
                        if json.loads(p.read_text()).get("key") == first_key][0]
        self.assertEqual(changed_pending.get("revision", ""), same_pending.get("revision", ""))

        fail_run = self.init5_topology("split", "demo2")
        self.submit5_in(fail_run, "demo2", "T01")
        self.submit5_in(fail_run, "demo2", "T02")
        self.cli("batch", "demo2", "--create", "M1", "--members", "T01,T02", "--milestone")
        self.cli("batch", "demo2", "--close", "M1")
        self.cli("verify", "demo2", "T01", "--result", "pass", "--as", "verifier")
        self.cli("verify", "demo2", "T02", "--result", "fail", "--as", "verifier",
                 "--detail", "oracle mismatch")
        fail_reviewer = self.cli("events", "demo2", "--role", "reviewer", "--peek").stdout
        self.assertNotIn("batch:M1:ready:", fail_reviewer)
        fail_verifier = self.cli("events", "demo2", "--role", "verifier", "--peek").stdout
        self.assertNotIn("T01:1:submitted", fail_verifier)
        self.assertNotIn("T02:1:submitted", fail_verifier)

        new_round_run = self.init5_topology("split", "demo3")
        self.submit5_in(new_round_run, "demo3", "T01")
        self.cli("verify", "demo3", "T01", "--result", "pass", "--as", "verifier")
        self.assertNotIn("T01:1:submitted",
                         self.cli("events", "demo3", "--role", "verifier", "--peek").stdout)
        self.cli("decide", "demo3", "T01", "--changes", "--as", "reviewer")
        dec = new_round_run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Fix `src/t01.py:1` - handle the empty input.\n"))
        self.cli("decide", "demo3", "T01", "--changes", "--as", "reviewer")
        self.fill_task_report(new_round_run / "T01-report-02.mdx",
                              files="- `src/t01.py:2` - fixed T01.")
        self.cli("submit", "demo3", "T01", "--as", "implementor")
        second = self.cli("events", "demo3", "--role", "verifier", "--peek").stdout
        self.assertIn("T01:2:submitted", second)

        exempt_run = self.init5_topology("split", "demo4")
        plan = exempt_run / "plan.mdx"
        plan.write_text(plan.read_text().replace("verifier_correction: forbidden",
                                                 "verifier_correction: forbidden\nverifier_exempt: T02"))
        self.submit5_in(exempt_run, "demo4", "T01")
        self.submit5_in(exempt_run, "demo4", "T02")
        self.cli("batch", "demo4", "--create", "M1", "--members", "T01,T02", "--milestone")
        self.cli("batch", "demo4", "--close", "M1")
        self.cli("verify", "demo4", "T01", "--result", "pass", "--as", "verifier")
        exempt_ready = self.cli("events", "demo4", "--role", "reviewer", "--peek").stdout
        self.assertIn("batch:M1:ready:1", exempt_ready)

    def test_p2_frontier_wakes_reviewer_for_stalled_dependency(self) -> None:
        """A closed batch with a verified submission blocking a dependent derives one frontier."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("batch", "demo", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.cli("batch", "demo", "--close", "B1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        reviewer = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        frontier_keys = [line.strip().split()[-1] for line in reviewer.splitlines()
                         if "batch:B1:frontier:" in line]
        self.assertEqual(1, len(frontier_keys))
        self.assertIn("batch:B1:frontier:1", frontier_keys[0])
        self.assertIn("T01", reviewer)
        # T01 -> T02 completes through derived events alone.
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "w1", "--role", "implementor")
        blocked_dispatch = self.cli("dispatch", "demo", "T02", "--session", "s1",
                                    ok=False)
        self.assertNotEqual(0, blocked_dispatch.returncode)
        self.assertIn("T01", blocked_dispatch.stderr)
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        self.assertIn("approved", (run / "T01-report-01.mdx").read_text())
        self.assertIn("draft", (run / "T02-report-01.mdx").read_text())
        dispatched = self.cli("dispatch", "demo", "T02", "--session", "s1")
        self.assertEqual(0, dispatched.returncode)
        self.assertIn("dispatched T02", dispatched.stdout)
        # A decision binds to its own bundle; approving T01 never approves T02.
        self.assertNotIn("approved", (run / "T02-report-01.mdx").read_text())
        # A frontier is a wake, never a terminal state of its own.
        self.assertNotIn("status: frontier", (run / "T01-report-01.mdx").read_text())
        self.assertNotIn("status: frontier", (run / "T02-report-01.mdx").read_text())

        # A member is in a frontier only with resolved verification and a stalled dependent.
        fail_run = self.init5_topology("split", "demo2")
        self.assign_simple_in(fail_run, "demo2", "T01")
        self.assign_simple_in(fail_run, "demo2", "T02")
        self.cli("batch", "demo2", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.cli("batch", "demo2", "--close", "B1")
        self.fill_task_report(fail_run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo2", "T01", "--as", "implementor")
        self.cli("verify", "demo2", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "oracle mismatch")
        no_frontier = self.cli("events", "demo2", "--role", "reviewer", "--peek").stdout
        self.assertNotIn("frontier", no_frontier)
        # Unverified submissions never join a frontier either.
        unverified_run = self.init5_topology("split", "demo3")
        self.assign_simple_in(unverified_run, "demo3", "T01")
        self.assign_simple_in(unverified_run, "demo3", "T02")
        self.cli("batch", "demo3", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.cli("batch", "demo3", "--close", "B1")
        self.fill_task_report(unverified_run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo3", "T01", "--as", "implementor")
        pending = self.cli("events", "demo3", "--role", "reviewer", "--peek").stdout
        self.assertNotIn("frontier", pending)

        # A milestone batch that is already ready does not wake twice for one decision.
        milestone_run = self.init5_topology("split", "demo4")
        self.submit5_in(milestone_run, "demo4", "T01")
        self.submit5_in(milestone_run, "demo4", "T02")
        self.cli("batch", "demo4", "--create", "M1", "--members", "T01,T02",
                 "--milestone")
        self.cli("batch", "demo4", "--close", "M1")
        self.cli("verify", "demo4", "T01", "--result", "pass", "--as", "verifier")
        self.cli("verify", "demo4", "T02", "--result", "pass", "--as", "verifier")
        both = self.cli("events", "demo4", "--role", "reviewer", "--peek").stdout
        self.assertIn("batch:M1:ready:1", both)
        self.assertNotIn("frontier", both)

        # Identity is stable while nothing changes and moves when evidence moves.
        stable_run = self.init5_topology("split", "demo5")
        self.assign_simple_in(stable_run, "demo5", "T01")
        self.assign_simple_in(stable_run, "demo5", "T02")
        self.cli("batch", "demo5", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.cli("batch", "demo5", "--close", "B1")
        self.fill_task_report(stable_run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo5", "T01", "--as", "implementor")
        self.cli("verify", "demo5", "T01", "--result", "pass", "--as", "verifier")
        first = self.cli("events", "demo5", "--role", "reviewer", "--peek").stdout
        first_keys = [line.strip().split()[-1] for line in first.splitlines()
                      if "batch:B1:frontier:" in line]
        self.assertEqual(1, len(first_keys))
        self.cli("reconcile", "demo5", "--role", "reviewer")
        pending_paths = list((stable_run / ".delivery" / "reviewer" / "pending").glob("*.json"))
        first_pending = [json.loads(p.read_text()) for p in pending_paths
                         if json.loads(p.read_text()).get("key") == first_keys[0]][0]
        first_revision = first_pending.get("revision", "")
        self.assertTrue(str(first_revision).startswith("sha256:"))
        same = self.cli("events", "demo5", "--role", "reviewer", "--peek").stdout
        self.assertEqual(first, same)
        self.cli("reconcile", "demo5", "--role", "reviewer")
        same_paths = list((stable_run / ".delivery" / "reviewer" / "pending").glob("*.json"))
        same_pending = [json.loads(p.read_text()) for p in same_paths
                        if json.loads(p.read_text()).get("key") == first_keys[0]][0]
        self.assertEqual(first_revision, same_pending.get("revision", ""))
        self.cli("verify", "demo5", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "changed finding with new evidence pointer")
        changed = self.cli("events", "demo5", "--role", "reviewer", "--peek").stdout
        changed_keys = [line.strip().split()[-1] for line in changed.splitlines()
                        if "batch:B1:frontier:" in line]
        self.assertEqual(1, len(changed_keys))
        self.assertEqual(first_keys[0], changed_keys[0])
        self.cli("reconcile", "demo5", "--role", "reviewer")
        changed_paths = list((stable_run / ".delivery" / "reviewer" / "pending").glob("*.json"))
        changed_pending = [json.loads(p.read_text()) for p in changed_paths
                           if json.loads(p.read_text()).get("key") == first_keys[0]][0]
        self.assertNotEqual(first_revision, changed_pending.get("revision", ""))

        # Legacy runs derive no frontier event.
        legacy = self.init5_topology("split", "demo6")
        # Convert demo6 to legacy by removing the workflow key.
        plan6 = legacy / "plan.mdx"
        plan6.write_text("\n".join(
            line for line in plan6.read_text().splitlines()
            if not line.startswith("workflow:")) + "\n")
        self.assign_simple_in(legacy, "demo6", "T01")
        self.assign_simple_in(legacy, "demo6", "T02")
        self.cli("batch", "demo6", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.cli("batch", "demo6", "--close", "B1")
        self.fill_task_report(legacy / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo6", "T01")
        legacy_reviewer = self.cli("events", "demo6", "--role", "reviewer", "--peek").stdout
        self.assertNotIn("frontier", legacy_reviewer)

    def test_p2_packet_scoped_to_reviewable_members(self) -> None:
        """Unscoped packets skip never-submitted tasks; batch scope selects exactly its batch."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("batch", "demo", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.cli("batch", "demo", "--close", "B1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        packet = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        self.assertIn("T01 round 1", packet)
        self.assertNotIn("T02 round", packet)
        scoped = self.cli("review-packet", "demo", "--role", "reviewer",
                          "--batch", "B1").stdout
        self.assertIn("T01 round 1", scoped)
        self.assertNotIn("T02 round", scoped)
        # A second batch with no reviewable work stays outside a scoped packet.
        self.assign_simple(run, "T03")
        self.cli("batch", "demo", "--create", "B2", "--members", "T03")
        self.cli("batch", "demo", "--close", "B2")
        rescoped = self.cli("review-packet", "demo", "--role", "reviewer",
                            "--batch", "B1").stdout
        self.assertIn("T01 round 1", rescoped)
        self.assertNotIn("T03 round", rescoped)
        other = self.cli("review-packet", "demo", "--role", "reviewer",
                         "--batch", "B2", ok=False)
        self.assertNotEqual(0, other.returncode)
        # The evidence gate is not weakened inside the selected scope.
        rep = run / "T01-report-01.mdx"
        rep.write_text(rep.read_text() + "\nStale edit.\n")
        stale = self.cli("review-packet", "demo", "--role", "reviewer", ok=False)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("stale", stale.stderr.lower())
        rep.write_text(rep.read_text().replace("\nStale edit.\n", ""))
        entries = json.loads((run / ".bundles" / "T01" / "rounds.json").read_text())["entries"]
        bdir = run / ".bundles" / "T01" / entries[-1]["dir"]
        artifact = bdir / "report.mdx"
        saved = artifact.read_bytes()
        artifact.write_bytes(saved + b"tamper")
        damaged = self.cli("review-packet", "demo", "--role", "reviewer", ok=False)
        self.assertNotEqual(0, damaged.returncode)
        self.assertIn("damaged", damaged.stderr)
        artifact.write_bytes(saved)
        # Correction and final selection keep their current behavior exactly.
        self.submit5(run, "T04")
        self.cli("verify", "demo", "T04", "--result", "pass", "--as", "verifier")
        self.cli("decide", "demo", "T04", "--approve", "--as", "reviewer")
        correction = self.cli("review-packet", "demo", "--role", "reviewer",
                              "--correction", "T04").stdout
        self.assertIn("T04 round", correction)
        self.assertNotIn("T01 round", correction)

    def test_p2_milestone_and_frontier_agree_on_verification(self) -> None:
        """One unresolved or failed member is excluded from both readiness paths."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.assign_simple(run, "T03")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli("submit", "demo", "T02", "--as", "implementor")
        self.cli("batch", "demo", "--create", "M1", "--members", "T01,T02",
                 "--milestone")
        self.cli("batch", "demo", "--close", "M1")
        self.cli("batch", "demo", "--create", "B1", "--members", "T01,T03",
                 "--depends-on", "T03:T01")
        self.cli("batch", "demo", "--close", "B1")
        self.cli("verify", "demo", "T02", "--result", "pass", "--as", "verifier")
        unresolved = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertNotIn("batch:M1:ready:", unresolved)
        self.assertNotIn("batch:B1:frontier:", unresolved)
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "oracle mismatch")
        failed = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertNotIn("batch:M1:ready:", failed)
        self.assertNotIn("batch:B1:frontier:", failed)

    # ---------------- T41: one canonical contract per role, docs that agree

    @staticmethod
    def _skill_root() -> Path:
        return Path(DOCKET).parents[1]

    @staticmethod
    def _ref_text(name: str) -> str:
        return (Path(DOCKET).parents[1] / "references" / name).read_text()

    def test_t41_canonical_contracts_single_source_help_prompt_agree(self) -> None:
        """Help and prompt both read the one contract file, so they cannot drift."""
        contracts = self._skill_root() / "references" / "contracts"
        for role in ("planner", "orchestrator", "implementor", "verifier", "reviewer"):
            self.assertTrue((contracts / f"{role}.md").is_file(),
                            f"missing canonical contract: contracts/{role}.md")
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        marker = "T41-CANONICAL-MARKER-7f3a"
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp)
            for role in ("planner", "orchestrator", "implementor", "verifier", "reviewer"):
                (overlay / f"{role}.md").write_text(
                    (contracts / f"{role}.md").read_text().rstrip() + f"\n\n{marker}\n")
            help_out = self.cli_env({"DOCKET_CONTRACTS_ROOT": str(overlay)},
                                    "help", "implementor")
            self.assertIn(marker, help_out.stdout)
            prompt_out = self.cli_env({"DOCKET_CONTRACTS_ROOT": str(overlay)},
                                      "prompt", "demo", "T01", "--role", "implementor")
            self.assertEqual(0, prompt_out.returncode, prompt_out.stderr)
            self.assertIn(marker, prompt_out.stdout)

    def test_t41_prompt_compact_excludes_playbook_prose(self) -> None:
        """The prompt carries the compact contract, never the playbook detail."""
        contracts = self._skill_root() / "references" / "contracts"
        maxima = {"implementor": 544, "orchestrator": 612, "verifier": 349,
                  "reviewer": 359, "planner": 302}
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        for role, limit in maxima.items():
            body = (contracts / f"{role}.md").read_text().strip()
            self.assertTrue(body, f"empty contract {role}")
            self.assertLessEqual(len(body), limit,
                                 f"{role} contract exceeds its historic compact size")
            out = self.prompt_text(run, "T01", role)
            self.assertIn(body.split()[0], out)
            self.assertIn(body.strip().splitlines()[0][:40], out)
        impl = self.prompt_text(run, "T01", "implementor")
        self.assertNotIn("return the report to", impl)
        orch = self.prompt_text(run, "T01", "orchestrator")
        self.assertNotIn("repeating the bare verdict", orch)
        self.assertNotIn("return the report to", orch)

    def test_t41_missing_contract_is_a_concrete_diagnostic(self) -> None:
        """A missing contract names its file; it never renders a placeholder prompt."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        with tempfile.TemporaryDirectory() as tmp:
            help_refused = self.cli_env({"DOCKET_CONTRACTS_ROOT": tmp},
                                        "help", "implementor", ok=False)
            self.assertNotEqual(0, help_refused.returncode)
            self.assertIn("implementor.md", help_refused.stderr)
            self.assertIn("contract", help_refused.stderr.lower())
            self.assertNotIn("Implement the assigned task", help_refused.stdout)
            prompt_refused = self.cli_env({"DOCKET_CONTRACTS_ROOT": tmp},
                                          "prompt", "demo", "T01",
                                          "--role", "implementor", ok=False)
            self.assertNotEqual(0, prompt_refused.returncode)
            self.assertIn("implementor.md", prompt_refused.stderr)
            self.assertIn("contract", prompt_refused.stderr.lower())
            self.assertNotIn("# Role: implementor", prompt_refused.stdout)

    def test_t41_dispatch_records_unavailable_binding_when_contract_missing(self) -> None:
        """A missing document must not make dispatch impossible."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "impl-one", "--role", "implementor")
        with tempfile.TemporaryDirectory() as tmp:
            dispatched = self.cli_env({"DOCKET_CONTRACTS_ROOT": tmp},
                                      "dispatch", "demo", "T01", "--session", "w1")
            self.assertEqual(0, dispatched.returncode, dispatched.stderr)
            self.assertIn("recorded binding", dispatched.stdout)
            self.assertIn("unavailable", dispatched.stdout.lower())
            record = json.loads((run / ".dispatch" / "T01.json").read_text())
            self.assertTrue(str(record.get("prompt_digest", "")).startswith("unavailable"),
                            f"prompt_digest is not unavailable: {record.get('prompt_digest')}")
            self.assertIn("implementor.md", str(record.get("prompt_digest", "")))

    def test_t41_planner_title_and_role_authority(self) -> None:
        """Planner is not titled reviewer; only the reviewer approves, waives, reopens."""
        planner = self._ref_text("planner.md")
        first = next(line for line in planner.splitlines() if line.strip())
        self.assertEqual("# Planner", first)
        self.assertNotIn("# Planner / reviewer", planner)
        orch = self._ref_text("orchestrator.md")
        self.assertNotIn("Approve, waive a genuine external blocker", orch)
        for name in ("planner.md", "orchestrator.md", "implementor.md",
                     "verifier.md", "signalling.md"):
            text = self._ref_text(name)
            for line in text.splitlines():
                stripped = line.strip()
                if "docket decide" not in stripped:
                    continue
                if any(flag in stripped for flag in ("--approve", "--waive",
                                                     "--changes", "--reopen")):
                    self.assertIn("--as reviewer", stripped,
                                  f"{name} directs a decision without reviewer authority: {stripped}")
        reviewer = self._ref_text("reviewer.md")
        self.assertIn("--approve --as reviewer", reviewer)
        self.assertIn("--waive", reviewer)

    def test_t41_no_playbook_hand_edits_lifecycle_status(self) -> None:
        """No playbook tells anyone to edit lifecycle status by hand."""
        for name in ("planner.md", "orchestrator.md", "implementor.md",
                     "verifier.md", "reviewer.md", "signalling.md",
                     "verification-obligations.md"):
            text = self._ref_text(name)
            self.assertNotIn("return the report to", text, f"{name} suggests a hand-edit")
            self.assertNotIn("return the report to `status: draft`", text)
        orch = self._ref_text("orchestrator.md")
        self.assertNotIn("or return the report to", orch)

    def test_t41_submission_examples_carry_as_and_commands_are_valid(self) -> None:
        """Implementor examples carry five-role --as; every fenced docket command is real."""
        impl = self._ref_text("implementor.md")
        self.assertIn("docket submit <run> <owner> --as implementor", impl)
        top = self.cli("--help")
        self.assertEqual(0, top.returncode)
        brace = re.search(r"\{([\w\-,]+)\}", top.stdout)
        self.assertIsNotNone(brace, "no subcommand list in docket --help")
        known_cmds = set(brace.group(1).split(","))
        self.assertIn("dispatch", known_cmds)
        flag_cache: dict[str, str] = {}
        repo_refs = self._skill_root() / "references"
        for path in sorted(repo_refs.glob("*.md")):
            text = path.read_text()
            for block in re.findall(r"```bash(.*?)```", text, re.S):
                for raw in block.splitlines():
                    line = raw.strip()
                    if line.startswith("#") or not line.startswith("docket "):
                        continue
                    line = line.split("#", 1)[0].strip()
                    tokens = line.split()
                    self.assertGreater(len(tokens), 1, f"{path.name}: {raw}")
                    cmd = tokens[1]
                    self.assertIn(cmd, known_cmds, f"{path.name} names no such command: {raw}")
                    if cmd not in flag_cache:
                        help_out = self.cli(cmd, "--help")
                        self.assertEqual(0, help_out.returncode, f"docket {cmd} --help")
                        flag_cache[cmd] = help_out.stdout
                    flags = [t for t in tokens[2:] if t.startswith("--")]
                    for flag in flags:
                        name = flag.split("=")[0]
                        self.assertIn(name, flag_cache[cmd],
                                      f"{path.name} names no such flag for {cmd}: {raw}")
            for match in re.findall(r"`(references/[^\s`]+\.md)`", text):
                self.assertTrue((self._skill_root() / match).is_file(),
                                f"{path.name} names no such path: {match}")

    def test_t41_readme_describes_five_roles_and_at_least_once(self) -> None:
        """User-facing promises match the code: five roles, at-least-once delivery."""
        repo = Path(DOCKET).parents[3]
        readme = (repo / "README.md").read_text()
        self.assertNotIn("Three logical roles", readme)
        self.assertIn("Five logical roles", readme)
        self.assertIn("at-least-once", readme)
        self.assertNotIn("exactly once", readme.lower())
        arch = (repo / "ARCHITECTURE.md").read_text()
        self.assertIn("at-least-once", arch)

    def test_t09_architecture_records_the_shipped_evidence_rules(self) -> None:
        """The maintainer guide names the evidence boundaries the CLI implements."""
        repo = Path(__file__).resolve().parents[3]
        arch = (repo / "ARCHITECTURE.md").read_text()
        for phrase in (
            "verify floor", "GIT_FIXED_CONFIG", "GIT_OPTIONAL_LOCKS",
            "retain_tree_objects", "qualification_sandbox", ".reopens/<owner>.json",
            "PRIVATE_STATE_DIRS", "only `met`", "arm:before-publish",
            "dispatch:before-claim", "submit:before-freeze",
        ):
            self.assertIn(phrase, arch, phrase)

    def test_t09_agent_and_guides_reject_obsolete_instructions(self) -> None:
        """Stale watcher, budget, and replacement instructions stay removed."""
        repo = Path(__file__).resolve().parents[3]
        arch = (repo / "ARCHITECTURE.md").read_text()
        current = (repo / "docs" / "docket-current-system.mdx").read_text()
        html = (repo / "docs" / "docket-architecture.html").read_text()
        for path, content in (("ARCHITECTURE.md", arch),
                              ("docs/docket-current-system.mdx", current),
                              ("docs/docket-architecture.html", html)):
            self.assertNotIn("round cap is advice", content, path)
            self.assertNotIn("3-round cap is playbook advice", content, path)
            self.assertNotIn("watch still announces directly from derivation", content, path)
            self.assertNotIn("direct watcher announcement does not claim an inbox lease",
                             content, path)
        skill_agents = (repo / "skills" / "docket" / "AGENTS.md").read_text()
        self.assertNotIn("A split planner receives only the aggregate", skill_agents)
        self.assertNotIn("Before\nreplacing an implementor, require a ready", skill_agents)
        self.assertNotIn("No <code>verify:</code> sanity check at scope submit", html)

    def test_t09_sync_recipe_excludes_bytecode(self) -> None:
        """The repository's installed-copy recipe cannot copy generated bytecode."""
        repo = Path(__file__).resolve().parents[3]
        agents = (repo / "AGENTS.md").read_text()
        self.assertIn("rsync -a", agents)
        self.assertIn("--exclude='__pycache__/'", agents)
        self.assertIn("--exclude='*.pyc'", agents)
        self.assertNotIn("cp -r ~/Documents/Projects/docket/skills/docket/.", agents)

    def test_the_docs_arm_every_role_the_default_preset_waits_on(self) -> None:
        """Regression: no doc told anyone to arm the quick checker, so it was never woken."""
        repo = Path(DOCKET).parents[3]
        signalling = (self._skill_root() / "references" / "signalling.md").read_text()
        readme = (repo / "README.md").read_text()
        for text in (signalling, readme):
            for role in ("coordinator", "checker"):
                self.assertIn(f"--role {role}", text)
        self.assertIn("docket arm R01 --role checker", readme)
        self.assertIn("docket arm <run> --role checker", signalling)
        self.assertNotIn("Combined topology uses only the\norchestrator role", signalling)
        skill = (self._skill_root() / "SKILL.md").read_text()
        self.assertIn("Arm your wake once with `docket arm R01 --role <role>`", skill)

    def test_t41_skill_is_a_router_not_a_duplicate_protocol(self) -> None:
        """SKILL.md routes to help and references instead of restating the protocol."""
        skill = (self._skill_root() / "SKILL.md").read_text()
        self.assertIn("docket help", skill)
        self.assertIn("references/", skill)
        self.assertLess(len(skill.splitlines()), 136,
                        f"SKILL.md is still a protocol duplicate at {len(skill.splitlines())} lines")
        self.assertNotIn("docket dispatch <run> T03 --session", skill)
        self.assertNotIn("One owner submits at a time", skill)
        for needle in ("evidence_mode: git", "provisional_integration: allowed",
                       "content-addressed", "at-least-once"):
            found = needle in skill
            in_refs = any(needle in p.read_text()
                          for p in (self._skill_root() / "references").glob("*.md"))
            self.assertTrue(found or in_refs, f"{needle} lost from the documentation set")

    def test_t41_changed_documents_carry_no_em_dashes(self) -> None:
        """Every changed document avoids em dashes."""
        repo = Path(DOCKET).parents[3]
        candidates = [repo / "README.md", repo / "ARCHITECTURE.md",
                      self._skill_root() / "SKILL.md"]
        candidates += sorted((self._skill_root() / "references").glob("*.md"))
        candidates += sorted((self._skill_root() / "references" / "contracts").glob("*.md")) \
            if (self._skill_root() / "references" / "contracts").is_dir() else []
        for path in candidates:
            if path.is_file():
                self.assertNotIn("\u2014", path.read_text(), f"em dash in {path}")

    # ------------------------------- T42: stage, workflow, budget, model guidance

    def test_t42_stage_derived_and_inspectable(self) -> None:
        """Derived stage labels initial work; an explicit flag inspects other stages."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        initial = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: initial", initial)
        self.assertIn("workflow: legacy", initial)
        # Resume needs no extra input and renders mechanical recovery when none is ready.
        resume = self.prompt_text(run, "T01", "implementor", stage="resume")
        self.assertIn("stage: resume", resume)
        self.assertNotIn("stage: initial", resume)
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        verify_stage = self.prompt_text(run, "T01", "implementor", stage="verification")
        self.assertIn("stage: verification", verify_stage)
        self.cli("decide", "demo", "T01", "--changes")
        dec = run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Fix `src/t01.py:1` for the stage probe.",
        ))
        self.cli("decide", "demo", "T01", "--changes")
        correction = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: correction", correction)
        inspected = self.prompt_text(run, "T01", "implementor", stage="correction")
        self.assertIn("stage: correction", inspected)
        # Review is distinguishable where verification exists: five-role with a pass.
        five = self.init5_topology("split", "demo2")
        self.assign_simple_in(five, "demo2", "T01")
        self.fill_task_report(five / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo2", "T01", "--as", "implementor")
        self.cli("verify", "demo2", "T01", "--result", "pass", "--as", "verifier")
        review = self.cli("prompt", "demo2", "T01", "--role", "implementor",
                          "--stage", "review").stdout
        self.assertIn("stage: review", review)

    def test_t42_workflow_selects_authority(self) -> None:
        """Five-role and legacy prompts differ where authority differs."""
        legacy = self.init("split", evidence_mode="documents-only")
        self.assign_simple(legacy, "T01")
        legacy_prompt = self.prompt_text(legacy, "T01", "implementor")
        five = self.init5_topology("split", "demo2")
        self.assign_simple_in(five, "demo2", "T01")
        five_prompt = self.cli("prompt", "demo2", "T01", "--role", "implementor").stdout
        self.assertNotEqual(legacy_prompt, five_prompt)
        # The regression names the difference it asserts.
        self.assertIn("only the reviewer approves", five_prompt)
        self.assertIn("legacy completion", legacy_prompt)

    def test_t42_planner_aggregate_has_plan_objective(self) -> None:
        """A planner prompt about the aggregate carries the plan, never task placeholders."""
        run = self.init("split", evidence_mode="documents-only")
        marker = "PLAN-OBJECTIVE-T42-9f2c"
        plan_text = (run / "plan.mdx").read_text()
        (run / "plan.mdx").write_text(plan_text.replace(
            "<!-- TODO: what \"done\" means for this whole run, in 2-4 sentences -->",
            marker))
        self.assign_simple(run, "T01")
        out = self.prompt_text(run, "orch", "planner")
        self.assertIn(marker, out)
        self.assertNotIn("see task file", out)
        self.assertNotIn("(see task file)", out)

    def test_t42_missing_task_is_diagnostic(self) -> None:
        """A missing required task file fails naming the artifact, never a placeholder."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        refused = self.cli("prompt", "demo", "T99", "--role", "implementor", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("T99-task.mdx", refused.stderr)
        self.assertNotIn("see task file", refused.stdout)
        self.assertNotIn("(see task file)", refused.stdout)

    def test_t42_dispatch_records_unavailable_on_correction_refusal(self) -> None:
        """Dispatch still binds when a correction cannot render; the digest is unavailable."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        opened = self.cli("decide", "demo", "T01", "--changes")
        self.assertIn("opened decision draft", opened.stdout)
        dec = run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Fix `src/t01.py:1` and add a regression.\n2. Re-run the verify command.",
        ))
        self.cli("decide", "demo", "T01", "--changes")
        # Empty the required changes so correction rendering must refuse.
        broken = dec.read_text().replace(
            "1. Fix `src/t01.py:1` and add a regression.\n2. Re-run the verify command.",
            "<!-- TODO: required changes removed for the refusal probe -->")
        dec.write_text(broken)
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "impl-one", "--role", "implementor")
        dispatched = self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.assertEqual(0, dispatched.returncode, dispatched.stderr)
        self.assertIn("recorded binding", dispatched.stdout)
        self.assertIn("unavailable", dispatched.stdout.lower())
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertTrue(str(record.get("prompt_digest", "")).startswith("unavailable"))

    def test_t42_correction_carries_required_changes(self) -> None:
        """A correction prompt carries every numbered change plus frozen evidence pointers."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        opened = self.cli("decide", "demo", "T01", "--changes")
        self.assertIn("opened decision draft", opened.stdout)
        dec = run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Guard `src/t01.py:1` against empty input.\n"
            "2. Add a regression asserting the guard fails without it.\n"
            "3. Re-run `printf \"T01 ok\\n\"` and quote its output.",
        ))
        self.cli("decide", "demo", "T01", "--changes")
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: correction", out)
        self.assertIn("1. Guard `src/t01.py:1` against empty input.", out)
        self.assertIn("2. Add a regression asserting the guard fails without it.", out)
        self.assertIn("3. Re-run `printf \"T01 ok\\n\"` and quote its output.", out)
        self.assertIn("bundle_digest: sha256:", out)
        self.assertIn("T01-decision-01.mdx", out)

    def test_t42_resume_selects_ready_only(self) -> None:
        """Resume names the latest ready handoff; with none ready it says mechanical recovery."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("handoff", "demo", "T01")
        self.fill_handoff(run / "T01-handoff-01.mdx")
        self.cli("handoff", "demo", "T01", "--submit")
        self.cli("handoff", "demo", "T01")
        self.fill_handoff(run / "T01-handoff-02.mdx")
        # 02 is still a draft; the resume prompt must name 01 as ready, never 02.
        out = self.prompt_text(run, "T01", "implementor", stage="resume")
        self.assertIn("T01-handoff-01.mdx", out)
        self.assertIn("ready", out.lower())
        ready_line = next(line for line in out.splitlines() if "ready handoff" in line.lower())
        self.assertIn("T01-handoff-01.mdx", ready_line)
        self.assertNotIn("T01-handoff-02.mdx", ready_line)
        # With only a draft and no ready handoff, recovery is mechanical rediscovery.
        # Use a fresh owner in the same run with only a draft handoff.
        self.assign_simple(run, "T02")
        self.cli("handoff", "demo", "T02")
        self.fill_handoff(run / "T02-handoff-01.mdx")
        lone = self.prompt_text(run, "T02", "implementor", stage="resume")
        self.assertNotIn("T02-handoff-01.mdx (ready", lone)
        self.assertIn("mechanical", lone.lower())
        self.assertIn("task", lone.lower())
        self.assertIn("scope", lone.lower())
        self.assertIn("docket diff", lone)

    def test_t42_budget_covers_profile_and_formatting(self) -> None:
        """Reported guidance size matches the prompt bytes and never exceeds the budget."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(task_text.replace(
            "Implement the feature.", "Fix the flaky fixture oracle under concurrency."))
        out = self.prompt_text(run, "T01", "implementor", model="unknown-model-9")
        records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
        self.assertTrue(records)
        record = json.loads(records[-1].read_text())
        guidance_tokens = int(record["guidance_tokens"])
        self.assertLessEqual(guidance_tokens, 600)
        # The reported size matches what the prompt actually carries.
        if "# Selected guidance" in out:
            section = out.split("# Selected guidance", 1)[1]
            # Guidance section ends at the next top-level heading.
            cut = section.find("\n\n# ")
            guidance_body = ("# Selected guidance" + (section[:cut] if cut != -1 else section))
        else:
            guidance_body = ""
        actual = max(1, len(guidance_body) // 4) if guidance_body else 0
        self.assertEqual(guidance_tokens, actual)

    def test_t42_zero_budget_yields_no_guidance(self) -> None:
        """Zero budget means no optional guidance at all, including no model profile."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(task_text.replace(
            "Implement the feature.", "Fix the flaky fixture oracle under concurrency."))
        out = self.prompt_text(run, "T01", "implementor", model="claude-opus-9",
                               max_tokens="0")
        self.assertNotIn("[concurrency]", out)
        self.assertNotIn("[fixture-changes]", out)
        self.assertNotIn("[muse]", out)
        self.assertNotIn("# Selected guidance", out)
        records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
        record = json.loads(records[-1].read_text())
        self.assertEqual(0, int(record["guidance_tokens"]))

    def test_t42_oversized_first_card_skipped(self) -> None:
        """A first card larger than the whole budget is skipped, never padded."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp)
            (overlay / "huge.md").write_text(
                "---\nprofile: huge\nversion: 1\ncard: true\ntriggers: feature\n---\n\n"
                + ("Huge guidance line.\n" * 800))
            task_text = (run / "T01-task.mdx").read_text()
            (run / "T01-task.mdx").write_text(task_text.replace(
                "Implement the feature.", "Implement the feature."))
            out = self.cli_env({"DOCKET_PROFILES_ROOT": str(overlay)},
                               "prompt", "demo", "T01", "--role", "implementor",
                               "--max-tokens", "50").stdout
            self.assertNotIn("[huge]", out)
            records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
            record = json.loads(records[-1].read_text())
            self.assertLessEqual(int(record["guidance_tokens"]), 50)

    def test_t42_guidance_selection_recorded(self) -> None:
        """Selected and rejected guidance is recorded with reasons in the digest record."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.prompt_text(run, "T01", "implementor")
        records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
        self.assertTrue(records)
        record = json.loads(records[-1].read_text())
        self.assertIn("selected_guidance", record)
        self.assertIn("rejected_guidance", record)
        for entry in record["rejected_guidance"]:
            self.assertIn("reason", entry)

    def test_t42_mandatory_never_truncated(self) -> None:
        """Mandatory contract and acceptance survive any budget and are sized separately."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        out = self.prompt_text(run, "T01", "implementor", max_tokens="1")
        self.assertIn("Implement the assigned task", out)
        self.assertIn("The feature works.", out)
        self.assertIn("hard constraints:", out)
        records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
        record = json.loads(records[-1].read_text())
        self.assertIn("mandatory_tokens", record)
        self.assertGreater(int(record["mandatory_tokens"]), 0)
        self.assertIn("guidance_tokens", record)

    def test_t42_model_matching_is_exact(self) -> None:
        """A model name that merely contains another name never selects that profile."""
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp)
            (overlay / "familymodel.md").write_text(
                "---\nprofile: familymodel\nversion: 1\nmodels: claude\n---\n\nFamily guidance.\n")
            (overlay / "musemodel.md").write_text(
                "---\nprofile: musemodel\nversion: 1\nmodels: muse\n---\n\nMuse guidance.\n")
            run = self.init("split", evidence_mode="documents-only")
            self.assign_simple(run, "T01")
            claude_test = self.cli_env(
                {"DOCKET_PROFILES_ROOT": str(overlay)},
                "prompt", "demo", "T01", "--role", "implementor",
                "--model", "claude-test").stdout
            self.assertNotIn("[familymodel]", claude_test)
            exact = self.cli_env(
                {"DOCKET_PROFILES_ROOT": str(overlay)},
                "prompt", "demo", "T01", "--role", "implementor",
                "--model", "claude").stdout
            self.assertIn("[familymodel]", exact)
            spark = self.cli_env(
                {"DOCKET_PROFILES_ROOT": str(overlay)},
                "prompt", "demo", "T01", "--role", "implementor",
                "--model", "muse-spark-1.3").stdout
            self.assertNotIn("[musemodel]", spark)

    def test_t42_discarded_evaluation_removed(self) -> None:
        """One-run model defaults are gone; task cards still trigger on task text."""
        profiles = self._skill_root() / "references" / "model-profiles"
        self.assertFalse((profiles / "muse.md").is_file(),
                         "discarded one-run muse profile still present")
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(task_text.replace(
            "Implement the feature.", "Fix the flaky fixture oracle under concurrency."))
        out = self.prompt_text(run, "T01", "implementor", model="claude-opus-9")
        self.assertNotIn("[muse]", out)
        self.assertNotIn("one-run", out.lower())
        self.assertIn("[fixture-changes]", out)
        self.assertIn("[concurrency]", out)

    def test_t42_unknown_model_gets_task_cards(self) -> None:
        """An unknown model still receives task-relevant cards under the stated policy."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(task_text.replace(
            "Implement the feature.", "Fix the concurrent deployment configuration."))
        out = self.prompt_text(run, "T01", "implementor", model="unknown-model-9")
        self.assertIn("[concurrency]", out)
        self.assertIn("[deployment-config]", out)
        repo = Path(DOCKET).parents[3]
        arch = (repo / "ARCHITECTURE.md").read_text()
        self.assertNotIn("unknown models get the common contract", arch)

    def test_t42_digest_record_has_workflow_stage_renderer_sources(self) -> None:
        """The digest record binds workflow, stage, renderer, and every source revision."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.prompt_text(run, "T01", "implementor")
        records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
        record = json.loads(records[-1].read_text())
        self.assertEqual("legacy", record["workflow"])
        self.assertEqual("initial", record["stage"])
        self.assertTrue(str(record["renderer_revision"]).strip())
        revisions = record["source_revisions"]
        self.assertIn("sha256:", str(revisions.get("contract", "")))
        self.assertIn("sha256:", str(revisions.get("task", "")))

    def test_t42_literals_survive(self) -> None:
        """Literal commands, fences, and tables round-trip byte-identical."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        command = "docket diff demo T01  --root app=./app"
        task_path = run / "T01-task.mdx"
        text = task_path.read_text()
        text = text.replace("The feature works.",
                            f"The feature works. Run `{command}` exactly.")
        task_path.write_text(text)
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn(f"`{command}`", out)
        fence = "```bash\ndocket diff demo T01\n```"
        task_text = (run / "T01-task.mdx").read_text()
        (run / "T01-task.mdx").write_text(task_text.replace(
            "Implement the feature.",
            f"Implement the feature.\n\n{fence}\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"))
        out2 = self.prompt_text(run, "T01", "implementor")
        self.assertIn(fence, out2)
        self.assertIn("| a | b |", out2)

    # ------------------------- T43: complete packets and applicable obligations

    def test_t43_standard_packet_preserves_findings_waiver_and_questions(self) -> None:
        """Every selected owner's mandatory review material survives whole and in order."""
        run = self.init5()
        self.assign_simple(run, "T01")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, files="- `src/t01.py:1` - implemented T01.")
        question = "Should the reviewer accept the documented clock-skew tradeoff for T01?"
        report.write_text(re.sub(
            r"## Decisions needed\n\n.*?\n\n## Notes",
            f"## Decisions needed\n\n{question}\n\n## Notes",
            report.read_text(), flags=re.S))
        self.cli("submit", "demo", "T01", "--as", "implementor")
        first = "F1: the independent oracle disagrees at the empty-input boundary."
        late = "F2-LATE: the final retry loses the byte-level provenance marker."
        self.cli("verify", "demo", "T01", "--result", "uncertain", "--as", "verifier",
                 "--detail", first)
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", late)

        self.assign_simple(run, "T02")
        blocked = run / "T02-report-01.mdx"
        self.fill_task_report(blocked, blocked=True,
                              files="- `src/t02.py:1` - recorded the blocked attempt.")
        blocked_question = "May T02 waive the unavailable external clock service?"
        blocked.write_text(re.sub(
            r"## Decisions needed\n\n.*?\n\n## Notes",
            f"## Decisions needed\n\n{blocked_question}\n\n## Notes",
            blocked.read_text(), flags=re.S))
        self.cli("submit", "demo", "T02", "--blocked", "--as", "implementor")
        waiver = ("The external clock service is unavailable; accept the local proof while "
                  + "retaining the documented limitation " + "x" * 180
                  + " QUALIFICATION-END: production still needs an online probe.")
        self.cli("decide", "demo", "T02", "--waive", "--reason", waiver,
                 "--as", "reviewer")

        packet = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        self.assertIn(first, packet)
        self.assertIn(late, packet, "a late verifier finding must not disappear")
        self.assertIn(waiver, packet)
        self.assertIn("QUALIFICATION-END", packet)
        self.assertIn(question, packet)
        self.assertIn(blocked_question, packet)
        self.assertNotIn("Approve the exact revisions above, request numbered corrections", packet)

    def test_t43_correction_packet_preserves_every_numbered_change_in_full(self) -> None:
        """Correction packets carry every outstanding item, including continuations and tails."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "F1: empty input is not guarded")
        self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
        decision = run / "T01-decision-01.mdx"
        long_tail = "FIRST-ITEM-TAIL-MUST-SURVIVE"
        required = (
            "1. Guard `src/t01.py:1` against empty input and preserve the independent "
            + "oracle derivation " + "x" * 190 + f" {long_tail}.\n"
            "   Keep this continuation because it qualifies the first required change.\n"
            "2. Add a regression that fails when the guard is removed.\n"
            "3. Re-run the registered verification and record the independently derived result."
        )
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            required))
        self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
        self.fill_task_report(run / "T01-report-02.mdx",
                              files="- `src/t01.py:1` - corrected T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")

        packet = self.cli("review-packet", "demo", "--role", "reviewer",
                          "--correction", "T01").stdout
        self.assertIn(long_tail, packet)
        self.assertIn("Keep this continuation", packet)
        self.assertIn("2. Add a regression", packet)
        self.assertIn("3. Re-run the registered verification", packet)

    def test_t43_packet_shrinks_routine_diff_before_mandatory_findings(self) -> None:
        """A large routine patch is excerpted while mandatory findings remain complete."""
        self.repo()
        self.cli("init", "demo", "--harness", "claude", "--topology", "split",
                 "--workflow", "five-role-v1", "--evidence-mode", "git",
                 "--root", f"root={self.root}")
        run = self.root / ".docket" / "runs" / "demo"
        self.assign_simple(run, "T01")
        changed = self.root / "src" / "t01.py"
        changed.write_text("\n".join(f"routine_line_{n} = {n}" for n in range(2400)) + "\n")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, files="- `src/t01.py:1` - generated routine data.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        finding = "MANDATORY-FINDING-TAIL: retain the byte-sensitive mismatch in full."
        self.cli("verify", "demo", "T01", "--result", "uncertain", "--as", "verifier",
                 "--detail", finding)
        packet = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        self.assertIn(finding, packet)
        self.assertLessEqual(self.packet_tokens(packet), 2000)
        self.assertNotIn("routine_line_2399", packet)

    def test_t43_mandatory_overage_is_explicit_with_required_reading_manifest(self) -> None:
        """Mandatory-only overage is stated and names every artifact to open."""
        run = self.init5()
        self.assign_simple(run, "T01")
        report = run / "T01-report-01.mdx"
        self.fill_task_report(report, files="- `src/t01.py:1` - implemented T01.")
        report.write_text(re.sub(
            r"## Decisions needed\n\n.*?\n\n## Notes",
            "## Decisions needed\n\nOVERAGE-QUESTION: decide the retained proof boundary.\n\n## Notes",
            report.read_text(), flags=re.S))
        self.cli("submit", "demo", "T01", "--as", "implementor")
        finding = "OVERAGE-FINDING-BEGIN " + ("mandatory-evidence " * 700) + "OVERAGE-FINDING-END"
        self.cli("verify", "demo", "T01", "--result", "uncertain", "--as", "verifier",
                 "--detail", finding)
        packet = self.cli("review-packet", "demo", "--role", "reviewer").stdout
        self.assertGreater(self.packet_tokens(packet), 2000)
        self.assertIn("OVERAGE-FINDING-BEGIN", packet)
        self.assertIn("OVERAGE-FINDING-END", packet)
        self.assertIn("Mandatory material exceeds the packet target", packet)
        self.assertIn("## Required-reading manifest", packet)
        self.assertIn("T01-verification-01.mdx", packet)
        self.assertIn("T01-report-01.mdx", packet)

    def test_t43_final_packet_uses_complete_frozen_findings_waiver_and_questions(self) -> None:
        """The frozen-release packet uses the same complete mandatory renderer."""
        self.repo()
        run = self.init5()
        self.assign_simple(run, "T01")
        t01_report = run / "T01-report-01.mdx"
        self.fill_task_report(t01_report, files="- `src/t01.py:1` - implemented T01.")
        t01_question = "FINAL-T01-QUESTION: accept the frozen retry boundary?"
        t01_report.write_text(re.sub(
            r"## Decisions needed\n\n.*?\n\n## Notes",
            f"## Decisions needed\n\n{t01_question}\n\n## Notes",
            t01_report.read_text(), flags=re.S))
        self.cli("submit", "demo", "T01", "--as", "implementor")
        early = "FINAL-EARLY-FINDING: preserve the first frozen finding body."
        late = "FINAL-LATE-FINDING: preserve the finding recorded last."
        self.cli("verify", "demo", "T01", "--result", "uncertain", "--as", "verifier",
                 "--detail", early)
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", late)
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")

        self.assign_simple(run, "T02")
        t02_report = run / "T02-report-01.mdx"
        self.fill_task_report(t02_report, blocked=True,
                              files="- `src/t02.py:1` - recorded the blocked attempt.")
        t02_question = "FINAL-T02-QUESTION: waive the external adapter outage?"
        t02_report.write_text(re.sub(
            r"## Decisions needed\n\n.*?\n\n## Notes",
            f"## Decisions needed\n\n{t02_question}\n\n## Notes",
            t02_report.read_text(), flags=re.S))
        self.cli("submit", "demo", "T02", "--blocked", "--as", "implementor")
        waiver = ("Freeze the local evidence while the external adapter is unavailable "
                  + "w" * 190
                  + " FINAL-WAIVER-QUALIFICATION: rerun the live adapter before release.")
        self.cli("decide", "demo", "T02", "--waive", "--reason", waiver,
                 "--as", "reviewer")

        self.release_setup(run)
        self.cli("assign", "demo", "orch", "--complexity", "high", "--executor",
                 "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
                 "--verify", 'printf "orch ok\\n"')
        orch_report = run / "orch-report-01.mdx"
        self.fill_orch_report(orch_report)
        orch_report.write_text(orch_report.read_text().replace(
            "| T02 | approved | passed |", "| T02 | waived | blocked |"))
        self.cli("submit", "demo", "orch", "--as", "orchestrator")
        aggregate = json.loads(
            (run / ".bundles" / "orch" / "rounds.json").read_text())["entries"][-1]["digest"]
        frozen = self.freeze_release(run, str(aggregate))
        release_digest = frozen.stdout.split("froze release ")[1].split()[0]
        packet = self.cli("review-packet", "demo", "--role", "reviewer", "--final",
                          "--release", release_digest).stdout
        self.assertIn(early, packet)
        self.assertIn(late, packet)
        self.assertIn(waiver, packet)
        self.assertIn("FINAL-WAIVER-QUALIFICATION", packet)
        self.assertIn(t01_question, packet)
        self.assertIn(t02_question, packet)

    def test_t43_verifier_obligations_are_selected_by_submission_claims(self) -> None:
        """Universal proof always applies; claim-specific duties appear only when relevant."""
        run = self.init5()
        self.submit5(run, "T01")
        unrelated = self.prompt_text(run, "T01", "verifier")
        self.assertIn("obligation: honest-acceptance", unrelated)
        self.assertNotIn("obligation: offline-operation", unrelated)
        self.assertNotIn("obligation: changed-oracle", unrelated)
        self.assertNotIn("obligation: event-aggregation", unrelated)
        self.assertNotIn("obligation: content-fingerprint", unrelated)
        self.assertNotIn("obligation: configuration", unrelated)
        self.assertIn("Discovery may reveal another relevant obligation or risk", unrelated)

        self.assign_simple(run, "T02")
        task = run / "T02-task.mdx"
        claims = ("Prove offline operation without network access, update a fixture oracle, "
                  "aggregate zero/one/many callback events, validate a byte-sensitive "
                  "fingerprint, and change deployment configuration.")
        task.write_text(task.read_text().replace("Implement the feature.", claims))
        report = run / "T02-report-01.mdx"
        self.fill_task_report(report, files="- `src/t02.py:1` - implemented every claim.")
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.", claims))
        self.cli("submit", "demo", "T02", "--as", "implementor")
        selected = self.prompt_text(run, "T02", "verifier")
        for obligation in ("offline-operation", "changed-oracle", "event-aggregation",
                           "content-fingerprint", "configuration"):
            self.assertIn(f"obligation: {obligation}", selected)
        self.assertIn("applies because the submitted contract or report claims", selected)
        self.assertIn("Discovery may reveal another relevant obligation or risk", selected)

    def test_t43_required_documents_describe_complete_packets_without_em_dashes(self) -> None:
        """The required architecture and playbooks document completeness and selection."""
        repo = Path(DOCKET).parents[3]
        expected = {
            repo / "ARCHITECTURE.md": ("required-reading manifest", "applicability"),
            self._skill_root() / "references" / "reviewer.md":
                ("required-reading manifest", "complete waiver"),
            self._skill_root() / "references" / "verifier.md":
                ("applicable verification obligations", "another relevant obligation"),
            self._skill_root() / "references" / "verification-obligations.md":
                ("honest-acceptance", "offline-operation"),
        }
        for path, needles in expected.items():
            text = path.read_text()
            self.assertNotIn("\u2014", text, f"em dash in {path}")
            for needle in needles:
                self.assertIn(needle, text, f"{path} does not document {needle}")

    # ------------------------------- T51: standard and quick presets

    def test_t51_new_runs_default_to_quick_and_modes_are_explicit(self) -> None:
        """Bare init is quick; explicit presets record every independent dimension."""
        self.cli("init", "bare", "--evidence-mode", "documents-only")
        bare = self.root / ".docket" / "runs" / "bare"
        bare_meta = parse_meta(bare / "plan.mdx")
        self.assertEqual("quick", bare_meta["mode"])
        self.assertEqual("five-role-v1", bare_meta["workflow"])
        self.assertEqual("combined", bare_meta["topology"])
        self.assertEqual("coordinator, implementor, checker", bare_meta["role_sessions"])
        self.assertEqual("combined-checker", bare_meta["review_policy"])
        self.assertIn("mode: quick", self.cli("status", "bare").stdout)

        self.cli("init", "standard", "--mode", "standard",
                 "--evidence-mode", "documents-only")
        standard_meta = parse_meta(self.root / ".docket" / "runs" / "standard" / "plan.mdx")
        self.assertEqual("standard", standard_meta["mode"])
        self.assertEqual("split", standard_meta["topology"])
        self.assertEqual(
            "planner, orchestrator, implementor, verifier, reviewer",
            standard_meta["role_sessions"],
        )
        self.assertEqual("independent-verifier-reviewer", standard_meta["review_policy"])
        # Asking standard for a combined coordinator points at quick instead of
        # leaving the caller to guess, and creates nothing.
        conflict = self.cli("init", "conflict", "--mode", "standard", "--topology", "combined",
                            ok=False)
        self.assertIn("use --mode quick", conflict.stderr)
        self.assertFalse((self.root / ".docket" / "runs" / "conflict").exists())

        self.cli("init", "quick", "--mode", "quick",
                 "--evidence-mode", "documents-only")
        quick_meta = parse_meta(
            self.root / ".docket" / "runs" / "quick" / "plan.mdx")
        self.assertEqual("quick", quick_meta["mode"])
        self.assertEqual("five-role-v1", quick_meta["workflow"])
        self.assertEqual("combined", quick_meta["topology"])
        self.assertEqual("coordinator, implementor, checker", quick_meta["role_sessions"])
        self.assertEqual("combined-checker", quick_meta["review_policy"])

        deferred = self.cli("init", "two", "--mode", "quick", "--agents", "2",
                            "--evidence-mode", "documents-only", ok=False)
        self.assertIn("two-role quick variant is deferred", deferred.stderr)
        self.assertFalse((self.root / ".docket" / "runs" / "two").exists())

        unknown_mode = self.cli("init", "bad-mode", "--mode", "instant", ok=False)
        self.assertIn("invalid choice", unknown_mode.stderr)
        self.assertFalse((self.root / ".docket" / "runs" / "bad-mode").exists())
        unknown_workflow = self.cli(
            "init", "bad-workflow", "--workflow", "mystery", ok=False)
        self.assertIn("invalid choice", unknown_workflow.stderr)
        self.assertFalse((self.root / ".docket" / "runs" / "bad-workflow").exists())

    def test_t51_missing_and_explicit_legacy_keep_completion_events_and_authority(self) -> None:
        """Old missing-key and explicit legacy plans retain their original semantics."""
        for run_id, missing_key in (("missing", True), ("explicit", False)):
            with self.subTest(run=run_id):
                self.init_legacy_as(run_id, "split", evidence_mode="documents-only")
                run = self.root / ".docket" / "runs" / run_id
                if missing_key:
                    plan = run / "plan.mdx"
                    old_keys = ("workflow:", "mode:", "role_sessions:",
                                "review_policy:", "model_policy:")
                    plan.write_text("\n".join(
                        line for line in plan.read_text().splitlines()
                        if not line.startswith(old_keys)
                    ) + "\n")
                self.cli(
                    "assign", run_id, "T01", "--complexity", "high", "--executor",
                    "orchestrator", "--harness", "opencode", "--file", "src/t01.py",
                    "--verify", 'printf "T01 ok\\n"',
                )
                self.fill_task(run / "T01-task.mdx")
                self.fill_task_report(run / "T01-report-01.mdx",
                                      files="- `src/t01.py:1` - implemented T01.")
                completed = self.cli("submit", run_id, "T01")
                self.assertIn("completed; recorded without a redundant handoff",
                              completed.stdout)

                self.cli("assign", run_id, "orch", "--complexity", "high",
                         "--executor", "orchestrator", "--harness", "opencode",
                         "--file", "src/orch.py")
                self.fill_orch_report(run / "orch-report-01.mdx")
                self.cli("submit", run_id, "orch")
                planner = self.cli("events", run_id, "--role", "planner", "--peek").stdout
                reviewer = self.cli("events", run_id, "--role", "reviewer", "--peek").stdout
                self.assertIn("orch:1:submitted", planner)
                self.assertIn("no derived events", reviewer)

    def test_t51_malformed_recorded_policy_refuses_instead_of_falling_back(self) -> None:
        """An explicit malformed mode or workflow never inherits legacy authority."""
        self.cli("init", "bad-workflow", "--mode", "standard",
                 "--evidence-mode", "documents-only")
        workflow_plan = self.root / ".docket" / "runs" / "bad-workflow" / "plan.mdx"
        workflow_plan.write_text(workflow_plan.read_text().replace(
            "workflow: five-role-v1", "workflow: mystery"))
        workflow = self.cli("status", "bad-workflow", ok=False)
        self.assertIn("unknown workflow 'mystery'", workflow.stderr)

        self.cli("init", "bad-mode", "--mode", "standard",
                 "--evidence-mode", "documents-only")
        mode_plan = self.root / ".docket" / "runs" / "bad-mode" / "plan.mdx"
        mode_plan.write_text(mode_plan.read_text().replace("mode: standard", "mode:"))
        mode = self.cli("status", "bad-mode", ok=False)
        self.assertIn("malformed mode", mode.stderr)

    def test_t51_quick_checker_runs_both_gates_and_binds_the_exact_bundle(self) -> None:
        """Quick combines duties explicitly while retaining verification and verdict binding."""
        self.repo()
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--executor",
            "implementor", "--harness", "opencode", "--file", "src/a.py",
            "--verify", 'printf "T01 ok\\n"',
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.fill_task_report(run / "T01-report-01.mdx")

        submitted = self.cli("submit", "demo", "T01", "--as", "implementor")
        self.assertIn("submitted for review", submitted.stdout)
        manifest, _ = self.frozen()
        self.assertEqual("passed", manifest["verification"]["status"])
        self.assertEqual("quick", manifest["mode"])
        self.assertEqual("combined-checker", manifest["review_policy"])
        self.assertNotIn("verifier_exempt", (run / "plan.mdx").read_text())
        self.assertIn("T01:1:submitted",
                      self.cli("events", "demo", "--role", "checker", "--peek").stdout)

        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "checker",
                 "--verifier", "checker")
        self.assertEqual("combined-checker", parse_meta(
            run / "T01-verification-01.mdx")["review_policy"])
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        self_approval = self.cli("decide", "demo", "T01", "--approve",
                                 "--as", "implementor", ok=False)
        self.assertIn("requires --as checker", self_approval.stderr)
        self.cli("decide", "demo", "T01", "--approve", "--as", "checker",
                 "--reviewer", "checker")
        decision = parse_meta(run / "T01-decision-01.mdx")
        self.assertEqual("combined-checker", decision["review_policy"])
        self.assertEqual(manifest["digest"], decision["bundle_digest"])
        self.assertIn("status: approved", (run / "T01-report-01.mdx").read_text())
        for name in ("plan.mdx", "T01-task.mdx", "T01-scope.mdx",
                     "T01-report-01.mdx", "T01-verification-01.mdx",
                     "T01-decision-01.mdx"):
            self.assertIn("review_policy: combined-checker", (run / name).read_text(), name)

    def test_t51_quick_rejects_source_drift_and_records_escalation_without_migration(self) -> None:
        """Quick keeps source binding, and escalation preserves mode and baseline bytes."""
        self.repo()
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--executor",
            "orchestrator", "--harness", "opencode", "--file", "src/a.py",
            "--verify", "printf 'allowed = 9\\n' > src/a.py",
        )
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        rejected = self.cli("submit", "demo", "T01", "--as", "coordinator", ok=False)
        self.assertIn("changed while the verification ran", rejected.stderr)
        self.assertIn("status: draft", (run / "T01-report-01.mdx").read_text())

        before = {
            path.relative_to(run).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((run / ".snapshots").rglob("*")) if path.is_file()
        }
        escalation = self.cli(
            "escalate-mode", "demo", "--reason",
            "Discovery found a concurrency protocol change requiring standard separation.",
        )
        self.assertIn("mode remains quick", escalation.stdout)
        self.assertEqual("quick", parse_meta(run / "plan.mdx")["mode"])
        after = {
            path.relative_to(run).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((run / ".snapshots").rglob("*")) if path.is_file()
        }
        self.assertEqual(before, after)
        requests = sorted((run / ".mode-escalations").glob("*.mdx"))
        self.assertEqual(1, len(requests))
        request = requests[0].read_text()
        self.assertIn("review_policy: combined-checker", request)
        self.assertIn("concurrency protocol change", request)

    def test_t51_required_documents_describe_presets_without_em_dashes(self) -> None:
        """The required architecture and playbooks document standard and quick."""
        repo = Path(DOCKET).parents[3]
        expected = {
            repo / "ARCHITECTURE.md": ("standard preset", "combined-checker"),
            repo / "README.md": ("standard preset", "combined-checker"),
            self._skill_root() / "SKILL.md": ("standard preset", "quick"),
            self._skill_root() / "references" / "planner.md":
                ("standard preset", "coordinator"),
            self._skill_root() / "references" / "orchestrator.md":
                ("standard preset", "coordinator"),
            self._skill_root() / "references" / "implementor.md":
                ("standard preset", "checker"),
            self._skill_root() / "references" / "verifier.md":
                ("combined-checker", "independent verifier"),
            self._skill_root() / "references" / "reviewer.md":
                ("combined-checker", "independent verifier"),
        }
        for path, needles in expected.items():
            text = path.read_text()
            self.assertNotIn("\u2014", text, f"em dash in {path}")
            for needle in needles:
                self.assertIn(needle, text, f"{path} does not document {needle}")

    def test_t12_standard_preset_documents_and_renders_two_window_layout(self) -> None:
        """Standard windows share exact herdr steps in docs and help; prompts carry none; quick is still."""
        split_reviewer = ('herdr pane split --current --direction right --ratio 0.5 '
                          '--cwd "$PWD" --env DOCKET_ROLE=reviewer --no-focus   # JSON: result.pane.pane_id')
        start_reviewer = 'herdr agent start reviewer --kind opencode --pane <pane_id> -- --auto'
        new_tab = 'herdr tab create --cwd "$PWD" --no-focus'
        split_impl = ('herdr pane split --current --direction right --ratio 0.5 '
                      '--cwd "$PWD" --no-focus   # JSON: result.pane.pane_id')
        split_verifier = ('herdr pane split --current --direction down --ratio 0.5 '
                          '--cwd "$PWD" --env DOCKET_ROLE=verifier --no-focus   # JSON: result.pane.pane_id')
        start_verifier = 'herdr agent start verifier --kind opencode --pane <pane_id> -- --auto'
        layout = (split_reviewer, start_reviewer, new_tab,
                  split_impl, split_verifier, start_verifier)
        skill = self._skill_root() / "SKILL.md"
        planner_ref = self._skill_root() / "references" / "planner.md"
        orch_ref = self._skill_root() / "references" / "orchestrator.md"
        for path in (skill, planner_ref, orch_ref):
            text = path.read_text()
            for command in layout:
                self.assertIn(command, text, f"{path} misses {command[:48]}")
            self.assertNotIn("\u2014", text, f"em dash in {path}")
        self.assertIn("1:1 vertical split", planner_ref.read_text())
        self.assertIn("1:1 vertical split", skill.read_text())
        self.assertIn("down in half", orch_ref.read_text())
        self.assertIn("Only the planner starts the reviewer", planner_ref.read_text())
        self.assertIn("Only the orchestrator starts the verifier", orch_ref.read_text())
        # Help renders the same bytes: fences survive unwrapping.
        orch_help = self.cli("help", "orchestrator").stdout
        planner_help = self.cli("help", "planner").stdout
        for command in layout:
            self.assertIn(command, orch_help, f"help orchestrator misses {command[:48]}")
            self.assertIn(command, planner_help, f"help planner misses {command[:48]}")
        # The generic quick dispatch snippet is untouched.
        self.assertIn('herdr pane split --current --direction right --cwd "$PWD" --no-focus',
                      skill.read_text())
        self.cli("init", "t12std", "--mode", "standard",
                 "--evidence-mode", "documents-only")
        std = self.root / ".docket" / "runs" / "t12std"
        self.assign_simple_in(std, "t12std", "T01")
        for role in ("implementor", "orchestrator", "planner"):
            prompt = self.prompt_text_in("t12std", "T01", role)
            for command in layout:
                self.assertNotIn(command, prompt, f"{role} task prompt carries layout")
        aggregate = self.prompt_text_in("t12std", "orch", "orchestrator")
        for command in layout:
            self.assertNotIn(command, aggregate)
        # Worked-before: task and aggregate prompts still carry their own steps.
        impl_prompt = self.prompt_text_in("t12std", "T01", "implementor")
        self.assertIn("docket scope t12std T01 --submit", impl_prompt)
        self.assertIn("docket preflight t12std T01", impl_prompt)
        self.assertIn("Fill every section", aggregate)
        # Correction, resume, and review stages carry no layout either.
        self.fill_task_report(std / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "t12std", "T01", "--as", "implementor")
        self.request_changes(std, "t12std", "T01")
        for role in ("implementor", "orchestrator"):
            correction = self.prompt_text_in("t12std", "T01", role)
            for command in layout:
                self.assertNotIn(command, correction, f"{role} correction carries layout")
        resumed = self.cli("prompt", "t12std", "T01", "--role", "implementor",
                           "--stage", "resume")
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        for command in layout:
            self.assertNotIn(command, resumed.stdout)
        self.assign_simple_in(std, "t12std", "T02")
        self.fill_task_report(std / "T02-report-01.mdx", blocked=True,
                              files="- `src/t02.py:1` - blocked change.")
        self.cli("submit", "t12std", "T02", "--blocked", "--as", "implementor")
        for role in ("implementor", "orchestrator", "planner"):
            review = self.prompt_text_in("t12std", "T02", role)
            for command in layout:
                self.assertNotIn(command, review, f"{role} review carries layout")
        submitted = self.prompt_text_in("t12std", "T02", "verifier")
        self.assertIn("The round is blocked", submitted)
        self.cli("init", "t12quick", "--mode", "quick",
                 "--evidence-mode", "documents-only")
        quick = self.root / ".docket" / "runs" / "t12quick"
        self.assign_simple_in(quick, "t12quick", "T01")
        coord_prompt = self.prompt_text_in("t12quick", "T01", "coordinator")
        checker_prompt = self.prompt_text_in("t12quick", "T01", "checker")
        for prompt in (coord_prompt, checker_prompt):
            for command in layout:
                self.assertNotIn(command, prompt)
        self.assertIn("Combine planning and orchestration", coord_prompt)
        coordinator_ref = self._skill_root() / "references" / "coordinator.md"
        for command in layout:
            self.assertNotIn(command, coordinator_ref.read_text())

    def test_t61_quick_roles_register_and_prompt_per_preset(self) -> None:
        """Quick coordinator and checker register and prompt in quick, refuse elsewhere."""
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "documents-only")
        run = self.root / ".docket" / "runs" / "demo"
        self.assign_simple(run, "T01")
        coord = self.cli("session", "demo", "--register", "--session", "c1",
                         "--name", "coord", "--role", "coordinator")
        self.assertIn("c1", coord.stdout)
        check = self.cli("session", "demo", "--register", "--session", "k1",
                         "--name", "check", "--role", "checker")
        self.assertIn("k1", check.stdout)
        coord_prompt = self.prompt_text(run, "T01", "coordinator")
        self.assertIn("planning", coord_prompt.lower())
        self.assertIn("orchestration", coord_prompt.lower())
        checker_prompt = self.prompt_text(run, "T01", "checker")
        self.assertIn("verification", checker_prompt.lower())
        self.assertIn("review", checker_prompt.lower())
        self.assertIn("combined-checker", checker_prompt)
        prompt_help = self.cli("prompt", "--help").stdout
        self.assertIn("coordinator", prompt_help)
        self.assertIn("checker", prompt_help)
        session_help = self.cli("session", "--help").stdout
        self.assertIn("coordinator", session_help)
        self.assertIn("checker", session_help)
        watch_help = self.cli("watch", "--help").stdout
        self.assertIn("coordinator", watch_help)
        self.assertIn("checker", watch_help)
        self.assertIn("implementor", watch_help)
        legacy_run = self.init_legacy_as("legacy", "split", evidence_mode="documents-only")
        self.cli("assign", "legacy", "T01", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/t01.py",
                 "--verify", 'printf "T01 ok\\n"')
        self.fill_task(legacy_run / "T01-task.mdx")
        self.fill_scope(legacy_run / "T01-scope.mdx")
        self.cli("scope", "legacy", "T01", "--submit")
        refused_session = self.cli("session", "legacy", "--register", "--session", "bad1",
                                   "--name", "bad", "--role", "coordinator", ok=False)
        self.assertNotEqual(0, refused_session.returncode)
        self.assertIn("legacy", refused_session.stderr.lower())
        refused_prompt = self.cli("prompt", "legacy", "T01", "--role", "coordinator", ok=False)
        self.assertNotEqual(0, refused_prompt.returncode)
        self.assertIn("legacy", refused_prompt.stderr.lower())
        self.cli("init", "standard", "--mode", "standard", "--evidence-mode", "documents-only")
        std_refused = self.cli("session", "standard", "--register", "--session", "bad2",
                               "--name", "bad", "--role", "checker", ok=False)
        self.assertNotEqual(0, std_refused.returncode)
        self.assertIn("standard", std_refused.stderr.lower())

    def test_t61_wake_hook_delivers_to_quick_roles(self) -> None:
        """The wake hook wakes coordinator and checker by executing it."""
        self.repo()
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/a.py",
                 "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("arm", "demo", "--role", "checker")
        self.cli("arm", "demo", "--role", "coordinator")
        base_env = dict(os.environ) | {
            "CLAUDE_PROJECT_DIR": str(self.root),
            "DOCKET_WATCH_TIMEOUT": "0",
        }
        checker = subprocess.run([str(HOOK)], cwd=self.root, capture_output=True, text=True,
                                 env=base_env | {"DOCKET_ROLE": "checker"})
        self.assertEqual(2, checker.returncode)
        self.assertIn("T01", checker.stderr)
        # Verified work wakes the checker again for its review duty; the
        # coordinator is left alone until there is something for it to do.
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "checker")
        reviewing = subprocess.run([str(HOOK)], cwd=self.root, capture_output=True, text=True,
                                   env=base_env | {"DOCKET_ROLE": "checker"})
        self.assertEqual(2, reviewing.returncode)
        self.assertIn("review batch ready with 1 verified", reviewing.stderr)
        idle = subprocess.run([str(HOOK)], cwd=self.root, capture_output=True, text=True,
                              env=base_env | {"DOCKET_ROLE": "coordinator"})
        self.assertEqual(0, idle.returncode, idle.stderr)
        self.cli("decide", "demo", "T01", "--approve", "--as", "checker")
        coordinator = subprocess.run([str(HOOK)], cwd=self.root, capture_output=True, text=True,
                                     env=base_env | {"DOCKET_ROLE": "coordinator"})
        self.assertEqual(2, coordinator.returncode)
        self.assertIn("delegated tasks decided", coordinator.stderr)

    def test_t61_quick_end_to_end_via_session_surface(self) -> None:
        """A quick round runs through register, dispatch, submit, verify and decide."""
        self.repo()
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/a.py",
                 "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.cli("session", "demo", "--register", "--session", "c1",
                 "--name", "coord", "--role", "coordinator")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "k1",
                 "--name", "check", "--role", "checker")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.fill_task_report(run / "T01-report-01.mdx")
        submitted = self.cli("submit", "demo", "T01", "--as", "implementor")
        self.assertIn("submitted for review", submitted.stdout)
        manifest, _ = self.frozen()
        self.assertEqual("quick", manifest["mode"])
        self.assertEqual("combined-checker", manifest["review_policy"])
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "checker",
                 "--verifier", "checker")
        self.assertEqual("combined-checker", parse_meta(
            run / "T01-verification-01.mdx")["review_policy"])
        self.cli("decide", "demo", "T01", "--approve", "--as", "checker",
                 "--reviewer", "checker")
        decision = parse_meta(run / "T01-decision-01.mdx")
        self.assertEqual("combined-checker", decision["review_policy"])
        self.assertEqual(manifest["digest"], decision["bundle_digest"])
        for name in ("plan.mdx", "T01-task.mdx", "T01-scope.mdx",
                     "T01-report-01.mdx", "T01-verification-01.mdx",
                     "T01-decision-01.mdx"):
            self.assertIn("review_policy: combined-checker", (run / name).read_text(), name)

    def test_t61_prompt_carries_mandatory_contract_sections(self) -> None:
        """Existing decisions, discovery constraints and plan text reach the prompt as mandatory."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        existing_marker = "T61-EXISTING-DECISION-9f2c"
        discovery_marker = "T61-DISCOVERY-CONSTRAINT-7a1b"
        scope_marker = "T61-OUT-OF-SCOPE-3e8d"
        plan_marker = "T61-PLAN-CONSTRAINT-5c4a"
        task_path = run / "T01-task.mdx"
        task_text = task_path.read_text()
        task_text = task_text.replace("## Out of scope\n", f"## Out of scope\n\n{scope_marker}\n")
        task_text = task_text.replace("## Existing decisions\n\nnone",
                                      f"## Existing decisions\n\n{existing_marker}\n")
        task_text = task_text.replace("## Discovery constraints\n\nnone",
                                      f"## Discovery constraints\n\n{discovery_marker}\n")
        task_path.write_text(task_text)
        plan_path = run / "plan.mdx"
        plan_text = plan_path.read_text()
        plan_text = plan_text.replace("## Out of scope", f"## Out of scope\n\n{plan_marker}\n")
        plan_path.write_text(plan_text)
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn(existing_marker, out)
        self.assertIn(discovery_marker, out)
        self.assertIn(scope_marker, out)
        self.assertIn(plan_marker, out)
        self.assertNotIn("return the report to", out)
        self.assertNotIn("repeating the bare verdict", out)
        zero = self.prompt_text(run, "T01", "implementor", max_tokens="0")
        self.assertIn(existing_marker, zero)
        self.assertIn(discovery_marker, zero)
        self.assertIn(plan_marker, zero)
        self.assertNotIn("# Selected guidance", zero)
        records = sorted((run / ".prompts").glob("T01-implementor-*.json"))
        record = json.loads(records[-1].read_text())
        self.assertEqual(0, int(record["guidance_tokens"]))
        self.assertGreater(int(record["mandatory_tokens"]), 0)

    def test_t61_mechanical_checkpoint_resumes_as_resume(self) -> None:
        """A resume with a mechanical checkpoint and no ready handoff renders as resume."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.cli("set-model", "demo", "T01", "--actual", "live-m")
        resumed = self.cli("resume", "demo", "T01", "--session", "w2",
                           "--reason", "process killed")
        self.assertIn("mechanical", resumed.stdout.lower())
        checkpoint = sorted((run / ".checkpoints").glob("T01-*.json"))[-1].name
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: resume", out)
        self.assertNotIn("stage: initial", out)
        self.assertIn(checkpoint, out)
        self.assertIn("T01-task.mdx", out)
        self.assertIn("T01-scope.mdx", out)
        self.assertIn("docket diff demo T01", out)

    def test_resume_of_an_untouched_round_starts_it_fresh(self) -> None:
        """A replacement for a worker that changed nothing gets the initial prompt."""
        self.repo()
        self.cli("init", "demo", "--harness", "claude", "--topology", "split",
                 "--workflow", "five-role-v1", "--evidence-mode", "git",
                 "--root", f"root={self.root}")
        run = self.root / ".docket" / "runs" / "demo"
        self.assign_simple(run, "T01")
        for sid in ("w1", "w2", "w3"):
            self.cli("session", "demo", "--register", "--session", sid,
                     "--name", sid, "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        resumed = self.cli("resume", "demo", "T01", "--session", "w2",
                           "--reason", "never started")
        self.assertIn("measured no work", resumed.stdout)
        first = json.loads((run / ".checkpoints" / "T01-01.json").read_text())
        self.assertEqual("none", first["work"])
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: initial", out)
        self.assertNotIn("mechanical checkpoint", out)
        (self.root / "src" / "t01.py").write_text("started = True\n")
        resumed = self.cli("resume", "demo", "T01", "--session", "w3",
                           "--reason", "killed mid-work")
        self.assertIn("is mechanical", resumed.stdout)
        second = json.loads((run / ".checkpoints" / "T01-02.json").read_text())
        self.assertEqual("changed", second["work"])
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: resume", out)
        self.assertIn("T01-02.json", out)

    def test_t61_required_documents_describe_quick_roles_and_mandatory_contract(self) -> None:
        """Architecture and playbooks name quick roles and mandatory sections without em dashes."""
        repo = Path(DOCKET).parents[3]
        skill = self._skill_root()
        expected = {
            repo / "ARCHITECTURE.md": ("coordinator", "checker", "Existing decisions",
                                       "Discovery constraints", "mandatory"),
            skill / "references" / "coordinator.md": ("coordinator", "quick"),
            skill / "references" / "checker.md": ("checker", "combined-checker"),
            skill / "references" / "contracts" / "coordinator.md": ("planning", "orchestration"),
            skill / "references" / "contracts" / "checker.md": ("verification", "combined-checker"),
        }
        for path, needles in expected.items():
            self.assertTrue(path.is_file(), f"missing {path}")
            text = path.read_text()
            self.assertNotIn("\u2014", text, f"em dash in {path}")
            for needle in needles:
                self.assertIn(needle, text, f"{path} does not document {needle}")

    def test_t62_failure_routes_to_reviewer_and_batch_not_ready(self) -> None:
        """A fail verdict wakes the reviewer, never claims batch readiness, and resolves."""
        run = self.init5()
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("correction_limit: 2", "correction_limit: 5"))
        self.submit5(run, "T01")
        # Review readiness waits for verification under five-role: before the
        # verifier records anything the orchestrator has nothing to route.
        before_orch = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertIn("no derived events", before_orch)
        self.assertIn("T01:1:submitted",
                      self.cli("events", "demo", "--role", "verifier", "--peek").stdout)
        self.assertIn("no derived events",
                      self.cli("events", "demo", "--role", "reviewer", "--peek").stdout)
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "oracle mismatch at src/t01.py:1")
        self.assertNotIn("T01:1:submitted",
                         self.cli("events", "demo", "--role", "verifier", "--peek").stdout)
        after_reviewer = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertIn("T01", after_reviewer)
        self.assertIn("fail", after_reviewer.lower())
        after_orch = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertNotIn("review batch ready", after_orch)
        self.cli("reconcile", "demo")
        pending = [json.loads(p.read_text()) for p in
                   (run / ".delivery" / "reviewer" / "pending").glob("*.json")]
        fail_pending = [r for r in pending if "verification-failed" in str(r.get("key", ""))]
        self.assertEqual(1, len(fail_pending))
        first_rev = str(fail_pending[0].get("revision", ""))
        self.cli("reconcile", "demo")
        again = [json.loads(p.read_text()) for p in
                 (run / ".delivery" / "reviewer" / "pending").glob("*.json")]
        self.assertEqual(1, len([r for r in again
                                 if "verification-failed" in str(r.get("key", ""))]))
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "second finding at src/t01.py:2")
        self.cli("reconcile", "demo")
        moved = [json.loads(p.read_text()) for p in
                 (run / ".delivery" / "reviewer" / "pending").glob("*.json")]
        fail_moved = [r for r in moved if "verification-failed" in str(r.get("key", ""))]
        self.assertEqual(1, len(fail_moved))
        self.assertNotEqual(first_rev, str(fail_moved[0].get("revision", "")))
        self.cli("batch", "demo", "--create", "M1", "--members", "T01", "--milestone")
        self.cli("batch", "demo", "--close", "M1")
        self.assertNotIn("batch:M1:ready:",
                         self.cli("events", "demo", "--role", "reviewer", "--peek").stdout)
        refused = self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer", ok=False)
        self.assertNotEqual(0, refused.returncode)
        fail_key = next(line.strip().split()[-1] for line in after_reviewer.splitlines()
                        if "T01" in line and ("fail" in line.lower() or "verification" in line.lower()))
        opened = self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
        self.assertIn("opened decision draft", opened.stdout)
        dec = run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            self.REQUIREMENT))
        self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
        resolved = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertNotIn(fail_key, resolved)

    def test_t62_verifier_correction_carries_numbered_changes_and_refuses_empty(self) -> None:
        """A verifier-opened correction renders numbered changes; empty findings refuse."""
        run = self.init5()
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("verifier_correction: forbidden",
                                                 "verifier_correction: allowed"))
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "oracle mismatch at src/t01.py:1", "--open-correction")
        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("1.", decision)
        self.assertIn("oracle mismatch", decision)
        self.assertNotIn("See the linked verification findings", decision)
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: correction", out)
        self.assertIn("oracle mismatch", out)
        orch_peek = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertIn("T01", orch_peek)
        self.assertIn("correction", orch_peek.lower())
        self.assign_simple(run, "T02")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli("submit", "demo", "T02", "--as", "implementor")
        refused = self.cli("verify", "demo", "T02", "--result", "fail", "--as", "verifier",
                           "--detail", "none", "--open-correction", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("numbered", refused.stderr.lower())
        self.assertFalse((run / "T02-decision-01.mdx").is_file())
        self.assertEqual(["T02-report-01.mdx"], self.rounds(run, "T02"))

    def test_t62_resume_claims_capacity_like_dispatch(self) -> None:
        """Resume passes the cap like dispatch; takeover succeeds, over-cap refuses."""
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        for sid in ("w1", "w2", "w3"):
            self.cli("session", "demo", "--register", "--session", sid,
                     "--name", sid, "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        takeover = self.cli("resume", "demo", "T01", "--session", "w2",
                            "--reason", "worker restarted")
        self.assertIn("resumed T01", takeover.stdout)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual("dispatched", record.get("state"))
        self.assertEqual("w2", record.get("session"))
        self.cli("handoff", "demo", "T01")
        self.fill_handoff(run / "T01-handoff-01.mdx")
        self.cli("handoff", "demo", "T01", "--submit")
        self.cli("dispatch", "demo", "T02", "--session", "w3")
        over = self.cli("resume", "demo", "T01", "--session", "w1", ok=False)
        self.assertNotEqual(0, over.returncode)
        self.assertIn("concurrency", over.stderr.lower())
        self.assertIn("1/1", over.stderr)
        live = [p for p in (run / ".dispatch").glob("*.json")
                if json.loads(p.read_text()).get("state") == "dispatched"]
        self.assertEqual(1, len(live))
        self.assertEqual("T02", json.loads(live[0].read_text()).get("owner"))

    def test_t62_suite_qualifies_colorized_output(self) -> None:
        """A green suite with FORCE_COLOR set qualifies; frozen output validates too."""
        self.repo()
        run = self.init("split", evidence_mode="documents-only")
        tiny = self.root / "tiny-suite"
        (tiny / "test_tiny.py").parent.mkdir(parents=True, exist_ok=True)
        (tiny / "test_tiny.py").write_text(
            "import unittest\n"
            "class Tiny(unittest.TestCase):\n"
            "    def test_holds(self):\n"
            "        self.assertEqual(1 + 1, 2)\n")
        before = sorted((run / ".suite").glob("qual-*.json")) if (run / ".suite").is_dir() else []
        qualified = self.cli_env({"FORCE_COLOR": "1"}, "suite", "demo", "--qualify",
                                 "--command", f"python3 -m unittest discover -s {tiny} -v")
        self.assertEqual(0, qualified.returncode, qualified.stderr)
        self.assertIn("qualified suite", qualified.stdout)
        arts = sorted((run / ".suite").glob("qual-*.json"))
        self.assertEqual(len(before) + 1, len(arts))
        record = json.loads(arts[-1].read_text())
        self.assertEqual(1, record["tests"])
        self.assertEqual(0, record["failures"])
        self.assertTrue(record["ok"])
        mod = self.docket_mod()
        self.assertEqual([], mod.suite_problems(arts[-1], "demo"))

    def test_t62_required_documents_describe_failure_capacity_and_color(self) -> None:
        """Architecture and playbooks describe failure routing, corrections, capacity, and color."""
        repo = Path(DOCKET).parents[3]
        skill = self._skill_root()
        expected = {
            repo / "ARCHITECTURE.md": ("verification-failed", "numbered required changes",
                                       "capacity", "ANSI"),
            skill / "references" / "signalling.md": ("verification-failed", "correction"),
            skill / "references" / "verifier.md": ("numbered",),
            skill / "references" / "orchestrator.md": ("capacity", "correction"),
            skill / "references" / "reviewer.md": ("verification-failed", "fail"),
            skill / "references" / "implementor.md": ("numbered", "correction"),
        }
        for path, needles in expected.items():
            self.assertTrue(path.is_file(), f"missing {path}")
            text = path.read_text()
            self.assertNotIn("\u2014", text, f"em dash in {path}")
            for needle in needles:
                self.assertIn(needle, text, f"{path} does not document {needle}")

    # ------------------------------- T71: legacy door and honesty follow-ups

    def test_t71_legacy_init_refuses_and_decode_and_migrate_survive(self) -> None:
        """New legacy runs refuse naming standard and quick; old decodes and migrate keep working."""
        refused = self.cli("init", "legacy-new", "--workflow", "legacy",
                           "--evidence-mode", "documents-only", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("standard", refused.stderr.lower())
        self.assertIn("quick", refused.stderr.lower())
        self.assertFalse((self.root / ".docket" / "runs" / "legacy-new").exists())
        run = self.init5()
        plan = run / "plan.mdx"
        old_keys = ("workflow:", "mode:", "role_sessions:",
                    "review_policy:", "model_policy:")
        plan.write_text("\n".join(
            line for line in plan.read_text().splitlines()
            if not line.startswith(old_keys)
        ) + "\n")
        missing_status = self.cli("status", "demo").stdout
        self.assertIn("legacy-unversioned", missing_status)
        self.cli("init", "explicit", "--harness", "claude", "--topology", "split",
                 "--evidence-mode", "documents-only", "--workflow", "five-role-v1")
        explicit_run = self.root / ".docket" / "runs" / "explicit"
        explicit_plan = explicit_run / "plan.mdx"
        explicit_plan.write_text(explicit_plan.read_text().replace(
            "workflow: five-role-v1", "workflow: legacy"))
        explicit_plan.write_text("\n".join(
            line for line in explicit_plan.read_text().splitlines()
            if not line.startswith(("mode:", "role_sessions:",
                                    "review_policy:", "model_policy:"))
        ) + "\n")
        explicit_status = self.cli("status", "explicit").stdout
        self.assertIn("legacy-unversioned", explicit_status)
        migrated = self.cli("migrate", "explicit", "--to", "five-role-v1")
        self.assertIn("migrated", migrated.stdout.lower())
        self.assertIn("workflow: five-role-v1",
                      (explicit_run / "plan.mdx").read_text())

    def _renderer_of(self, text: str) -> str:
        for line in text.splitlines():
            if line.strip().startswith("renderer:"):
                return line.strip().split(":", 1)[1].strip()
        self.fail("no renderer line in prompt")
        return ""

    def _uncached_entry(self) -> Path:
        """A script that runs the CLI under test without the launcher or any bytecode."""
        if not CLI_PACKAGE.is_dir():
            return CLI_FILES[0]
        entry = self.root / "uncached-docket"
        entry.write_text(
            "import sys\n"
            "sys.dont_write_bytecode = True\n"
            f"sys.path[0] = {str(CLI_PACKAGE.parent)!r}\n"
            "from docket_cli import main\n"
            "main()\n")
        return entry

    def _prompt_with_bin(self, binary: Path, owner: str, role: str) -> str:
        result = subprocess.run(
            [sys.executable, str(binary), "prompt", "demo", owner,
             "--role", role],
            cwd=str(self.root), text=True, capture_output=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def _stage_patched_bin(self, name: str, old: str, new: str) -> Path:
        """A copy of the CLI build with `old` replaced by `new` in the one file holding it."""
        skill = self.root / name
        (skill / "bin").mkdir(parents=True)
        binary = skill / "bin" / "docket"
        holders = [path for path in CLI_FILES if old in path.read_text()]
        self.assertEqual(1, len(holders), f"{old!r} must sit in exactly one CLI file")
        if CLI_FILES == [DOCKET]:
            binary.write_text(DOCKET.read_text().replace(old, new, 1))
        else:
            # The launcher beside the copied source runs it.
            if CLI_PACKAGE.is_dir():
                shutil.copytree(CLI_PACKAGE, skill / CLI_PACKAGE.name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                target = skill / CLI_PACKAGE.name / holders[0].name
            else:
                target = skill / holders[0].name
            target.write_text(holders[0].read_text().replace(old, new, 1))
            shutil.copy2(DOCKET, binary)
        binary.chmod(0o755)
        os.symlink(self._skill_root() / "references", skill / "references",
                   target_is_directory=True)
        return binary

    def test_t71_renderer_derived_stable_moves_and_no_churn(self) -> None:
        """Renderer revision is stable, moves on renderer edits, and ignores unrelated edits."""
        run = self.init5()
        self.assign_simple(run, "T01")
        first = self.prompt_text(run, "T01", "implementor")
        second = self.prompt_text(run, "T01", "implementor")
        rev1 = self._renderer_of(first)
        rev2 = self._renderer_of(second)
        self.assertTrue(rev1.strip())
        self.assertEqual(rev1, rev2)
        self.assertNotEqual("t43-p4-v1", rev1)
        docket_text = cli_source_text()
        self.assertIn('    """Render one role prompt', docket_text)
        renderer_bin = self._stage_patched_bin(
            "patched-renderer",
            '    """Render one role prompt',
            '    # T71 renderer probe - composition tracking\n    """Render one role prompt')
        patched_text = self._prompt_with_bin(renderer_bin, "T01", "implementor")
        patched_rev = self._renderer_of(patched_text)
        self.assertNotEqual(rev1, patched_rev)
        if '    """A checkout with one commit' in docket_text:
            unrelated = ('    """A checkout with one commit',
                         '    # T71 unrelated probe - must not churn renderer\n'
                         '    """A checkout with one commit')
        else:
            unrelated = ('def capture_root_baseline',
                         '# T71 unrelated probe - must not churn renderer\ndef capture_root_baseline')
        self.assertIn(unrelated[0], docket_text)
        unrelated_bin = self._stage_patched_bin("patched-unrelated", *unrelated)
        unrelated_out = self._prompt_with_bin(unrelated_bin, "T01", "implementor")
        unrelated_rev = self._renderer_of(unrelated_out)
        self.assertEqual(rev1, unrelated_rev)

    def test_renderer_revision_tracks_profile_candidate_helper(self) -> None:
        """Changing a helper that selects prompt guidance changes the renderer revision."""
        run = self.init5()
        self.assign_simple(run, "T01")
        overlay = self.root / "profiles"
        overlay.mkdir()
        (overlay / "probe.md").write_text(
            "---\nprofile: probe\nversion: 1\nmodels: vendor/probe\n---\n\n"
            "Distinct profile guidance.\n")
        previous = os.environ.get("DOCKET_PROFILES_ROOT")
        os.environ["DOCKET_PROFILES_ROOT"] = str(overlay)
        try:
            args = ("prompt", "demo", "T01", "--role", "implementor",
                    "--model", "vendor/probe")
            base = self.cli(*args).stdout
            self.assertIn("Distinct profile guidance.", base)
            self.assertEqual(self._renderer_of(base),
                             self._renderer_of(self.cli(*args).stdout))
            source = cli_source_text()
            target = 'def profile_candidates() -> list[Path]:\n'
            self.assertIn(target, source)
            patched = self._stage_patched_bin("patched-profile-candidates",
                                              target, target + '    return []\n')
            result = subprocess.run([sys.executable, str(patched), *args], cwd=self.root,
                                    text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertNotIn("Distinct profile guidance.", result.stdout)
            self.assertNotEqual(self._renderer_of(base), self._renderer_of(result.stdout))
        finally:
            if previous is None:
                os.environ.pop("DOCKET_PROFILES_ROOT", None)
            else:
                os.environ["DOCKET_PROFILES_ROOT"] = previous

    def test_prompts_are_byte_identical_through_the_launcher(self) -> None:
        """The launcher, the module run as a script, and a prior build render the same bytes.

        Renderer revisions hash function source and prompt digests hash the prompt, so
        moving the CLI into a module must not change a single byte of either.
        `DOCKET_BIN_PRIOR` names a pre-move single-file build to compare against.
        """
        run = self.init5()
        self.assign_simple(run, "T01")
        builds = [DOCKET, self._uncached_entry()]
        if os.environ.get("DOCKET_BIN_PRIOR"):
            builds.append(Path(os.environ["DOCKET_BIN_PRIOR"]))
        for role in ("implementor", "verifier", "reviewer", "orchestrator"):
            with self.subTest(role=role):
                prompts = [self._prompt_with_bin(build, "T01", role) for build in builds]
                self.assertTrue(self._renderer_of(prompts[0]).startswith("derived-"))
                for build, prompt in zip(builds[1:], prompts[1:]):
                    self.assertEqual(prompts[0], prompt, f"{build} renders differently")
                self.assertEqual(sha(prompts[0].encode()), sha(prompts[-1].encode()))

    def test_t71_prompt_states_identity_exactly_once(self) -> None:
        """Workflow, stage and renderer each render exactly once and stay present."""
        run = self.init5()
        self.assign_simple(run, "T01")
        out = self.prompt_text(run, "T01", "implementor")
        def count(key: str) -> int:
            # Each identity key is stated once, wherever the compact metadata
            # line places it.
            return sum(line.count(key) for line in out.splitlines())
        self.assertEqual(1, count("workflow:"))
        self.assertEqual(1, count("stage:"))
        self.assertEqual(1, count("renderer:"))
        self.assertIn("workflow:", out)
        self.assertIn("stage:", out)
        self.assertIn("renderer:", out)

    def test_t71_obligations_select_claims_not_out_of_scope(self) -> None:
        """Same word offline in Out of scope does not select, in Goal it does."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace(
            "## Out of scope",
            "## Out of scope\n\noffline operation is out of scope and will not be implemented.\n"))
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        discussed = self.prompt_text(run, "T01", "verifier")
        self.assertIn("obligation: honest-acceptance", discussed)
        self.assertNotIn("obligation: offline-operation", discussed)
        self.assertIn("Discovery may reveal another relevant obligation or risk", discussed)
        self.assign_simple(run, "T02")
        genuine_task = run / "T02-task.mdx"
        genuine_task.write_text(genuine_task.read_text().replace(
            "Implement the feature.",
            "Prove offline operation without network access."))
        genuine_report = run / "T02-report-01.mdx"
        self.fill_task_report(genuine_report,
                              files="- `src/t02.py:1` - implemented offline.")
        genuine_report.write_text(genuine_report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Prove offline operation without network access."))
        self.cli("submit", "demo", "T02", "--as", "implementor")
        selected = self.prompt_text(run, "T02", "verifier")
        self.assertIn("obligation: honest-acceptance", selected)
        self.assertIn("obligation: offline-operation", selected)
        self.assertIn("applies because the submitted contract or report claims", selected)
        self.assertIn("Discovery may reveal another relevant obligation or risk", selected)

    def test_t71_obligations_ignore_obligation_discussion(self) -> None:
        """Same word offline in obligation discussion does not select, genuine does."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace(
            "Implement the feature.",
            "This task discusses verification obligations and duties about offline operation."))
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        discussed = self.prompt_text(run, "T01", "verifier")
        self.assertIn("obligation: honest-acceptance", discussed)
        self.assertNotIn("obligation: offline-operation", discussed)
        self.assertIn("Discovery may reveal another relevant obligation or risk", discussed)
        self.assign_simple(run, "T02")
        genuine_task = run / "T02-task.mdx"
        genuine_task.write_text(genuine_task.read_text().replace(
            "Implement the feature.",
            "Prove offline operation without network access."))
        genuine_report = run / "T02-report-01.mdx"
        self.fill_task_report(genuine_report,
                              files="- `src/t02.py:1` - implemented offline.")
        genuine_report.write_text(genuine_report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Prove offline operation without network access."))
        self.cli("submit", "demo", "T02", "--as", "implementor")
        selected = self.prompt_text(run, "T02", "verifier")
        self.assertIn("obligation: offline-operation", selected)

    def test_t02_obligation_trigger_at_word_start_is_selected(self) -> None:
        """A stem trigger at the start of a word selects, matching card behavior."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace(
            "Implement the feature.",
            "The service aggregates JWT claims across regions."))
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        selected = self.prompt_text(run, "T01", "verifier")
        self.assertIn("obligation: honest-acceptance", selected)
        self.assertIn("obligation: event-aggregation", selected)
        self.assertIn("Discovery may reveal another relevant obligation or risk", selected)

    def test_t02_disparity_selects_no_obligation(self) -> None:
        """An infix like parity inside disparity matches no obligation trigger."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace(
            "Implement the feature.",
            "The disparity between replicas exceeds the tolerance budget."))
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        selected = self.prompt_text(run, "T01", "verifier")
        self.assertIn("obligation: honest-acceptance", selected)
        self.assertNotIn("obligation: offline-operation", selected)
        self.assertNotIn("obligation: changed-oracle", selected)
        self.assertNotIn("obligation: event-aggregation", selected)
        self.assertNotIn("obligation: content-fingerprint", selected)
        self.assertNotIn("obligation: configuration", selected)
        self.assertIn("Discovery may reveal another relevant obligation or risk", selected)

    def test_t02_jwt_claims_sentence_is_kept(self) -> None:
        """A behavioral sentence about JWT claims survives the claim filter."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace(
            "Implement the feature.",
            "The service preserves JWT claims across token refresh without network access."))
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        selected = self.prompt_text(run, "T01", "verifier")
        self.assertIn("obligation: honest-acceptance", selected)
        self.assertIn("obligation: offline-operation", selected)
        self.assertIn("Discovery may reveal another relevant obligation or risk", selected)

    def test_t02_inflected_markers_still_drop_meta_sentences(self) -> None:
        """Meta sentences using inflected markers stay dropped, not behavior claims."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace(
            "Implement the feature.",
            "Offline obligations are handled by another task. "
            "Discussion of offline behavior belongs to T09. "
            "The fixture discusses offline mode only as background."))
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        selected = self.prompt_text(run, "T01", "verifier")
        self.assertIn("obligation: honest-acceptance", selected)
        self.assertNotIn("obligation: offline-operation", selected)
        self.assertNotIn("obligation: changed-oracle", selected)
        self.assertNotIn("obligation: event-aggregation", selected)
        self.assertNotIn("obligation: content-fingerprint", selected)
        self.assertNotIn("obligation: configuration", selected)
        self.assertIn("Discovery may reveal another relevant obligation or risk", selected)

    @staticmethod
    def _t03_fn_source(name: str) -> str:
        """The CLI binary's own source for one function, for lock-holding checks."""
        return cli_function_source(name)

    def test_t03_edits_hold_owner_or_run_lock(self) -> None:
        """depend, set-model, and escalate-mode serialize their read-modify-write."""
        self.assertIn("owner_lock", self._t03_fn_source("cmd_depend"))
        self.assertIn("owner_lock", self._t03_fn_source("cmd_set_model"))
        self.assertIn("run_lock", self._t03_fn_source("cmd_escalate_mode"))
        run = self.init5()
        self.assign_simple(run, "T01")
        recorded = self.cli("set-model", "demo", "T01", "--actual", "live-m")
        self.assertIn("recorded in T01-report-01.mdx", recorded.stdout)
        self.assertIn("actual_model: live-m", (run / "T01-report-01.mdx").read_text())

    def test_t03_concurrent_depends_keep_every_pin(self) -> None:
        """A depend record waits for the owner lock; concurrent records serialize."""
        import fcntl
        run = self.init5()
        self.allow_provisional(run)
        self.submit5(run, "T01")
        self.assign_simple(run, "T04")
        lock_path = run / ".locks" / "T04.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(DOCKET), "depend", "demo", "T04", "--on", "T01"],
                    cwd=str(self.root), text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=dict(os.environ))
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    blocked = True
                else:
                    blocked = False
                self.assertTrue(blocked, "depend recorded while the owner lock was held")
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(0, proc.returncode, f"depend failed after release: {err}")
        self.assertIn("T04 consumes T01", out)
        recorded = [entry["on"] for entry in
                    json.loads((run / ".deps" / "T04.json").read_text())["dependencies"]]
        self.assertEqual(["T01"], recorded)

    def test_t03_verification_gap_chooses_above_highest(self) -> None:
        """A missing lower verification number never causes an overwrite."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "first pass")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "second pass")
        second = run / "T01-verification-02.mdx"
        second_bytes = second.read_bytes()
        (run / "T01-verification-01.mdx").unlink()
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "third pass")
        self.assertTrue((run / "T01-verification-03.mdx").is_file())
        self.assertEqual(second_bytes, second.read_bytes())

    def test_t03_mode_escalation_gap_chooses_above_highest(self) -> None:
        """A missing lower escalation request never causes an overwrite."""
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "documents-only")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("escalate-mode", "demo", "--reason", "first reason")
        self.cli("escalate-mode", "demo", "--reason", "second reason")
        second = run / ".mode-escalations" / "request-02.mdx"
        second_bytes = second.read_bytes()
        (run / ".mode-escalations" / "request-01.mdx").unlink()
        self.cli("escalate-mode", "demo", "--reason", "third reason")
        self.assertTrue((run / ".mode-escalations" / "request-03.mdx").is_file())
        self.assertEqual(second_bytes, second.read_bytes())

    def test_t03_handoff_gap_cannot_overwrite(self) -> None:
        """A missing lower handoff never causes the next open to overwrite."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.cli("handoff", "demo", "T01")
        self.fill_handoff(run / "T01-handoff-01.mdx")
        self.cli("handoff", "demo", "T01", "--submit")
        self.cli("handoff", "demo", "T01")
        self.fill_handoff(run / "T01-handoff-02.mdx")
        self.cli("handoff", "demo", "T01", "--submit")
        second_bytes = (run / "T01-handoff-02.mdx").read_bytes()
        (run / "T01-handoff-01.mdx").unlink()
        self.cli("handoff", "demo", "T01")
        self.assertTrue((run / "T01-handoff-03.mdx").is_file())
        self.assertEqual(second_bytes, (run / "T01-handoff-02.mdx").read_bytes())

    def test_t03_feedback_gap_uses_highest_plus_one(self) -> None:
        """Feedback ids advance past the highest surviving id after a gap."""
        run = self.init5()
        for body in ("first", "second"):
            self.cli("feedback", "demo", "--add", "--role", "implementor",
                     "--category", "other", "--body", body)
        second = run / "feedback" / "F02.mdx"
        second_bytes = second.read_bytes()
        (run / "feedback" / "F01.mdx").unlink()
        self.cli("feedback", "demo", "--add", "--role", "implementor",
                 "--category", "other", "--body", "third")
        self.assertTrue((run / "feedback" / "F03.mdx").is_file())
        self.assertEqual(second_bytes, second.read_bytes())

    def test_t03_prompt_gap_cannot_overwrite(self) -> None:
        """A missing lower prompt record never causes the next render to overwrite."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.cli("prompt", "demo", "T01", "--role", "implementor")
        self.cli("prompt", "demo", "T01", "--role", "implementor")
        second = run / ".prompts" / "T01-implementor-02.json"
        second_bytes = second.read_bytes()
        (run / ".prompts" / "T01-implementor-01.json").unlink()
        self.cli("prompt", "demo", "T01", "--role", "implementor")
        self.assertTrue((run / ".prompts" / "T01-implementor-03.json").is_file())
        self.assertEqual(second_bytes, second.read_bytes())

    def test_t03_checkpoint_gap_cannot_overwrite(self) -> None:
        """A missing lower checkpoint never causes the next resume to overwrite."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign_simple(run, "T01")
        for session in ("w1", "w2", "w3", "w4"):
            self.cli("session", "demo", "--register", "--session", session,
                     "--name", f"worker-{session}", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.cli("resume", "demo", "T01", "--session", "w2", "--reason", "first move")
        self.cli("resume", "demo", "T01", "--session", "w3", "--reason", "second move")
        second = run / ".checkpoints" / "T01-02.json"
        second_bytes = second.read_bytes()
        (run / ".checkpoints" / "T01-01.json").unlink()
        self.cli("resume", "demo", "T01", "--session", "w4", "--reason", "third move")
        self.assertTrue((run / ".checkpoints" / "T01-03.json").is_file())
        self.assertEqual(second_bytes, second.read_bytes())

    def test_t03_improvement_gap_uses_highest_plus_one(self) -> None:
        """An improvements gap never causes the next add to reuse a number."""
        self.cli("improvements", "--add", "--title", "first finding")
        self.cli("improvements", "--add", "--title", "second finding")
        directory = self.assert_disposable(self.root / ".docket" / "improvements")
        second = directory / "I02.mdx"
        second_bytes = second.read_bytes()
        (directory / "I01.mdx").unlink()
        self.cli("improvements", "--add", "--title", "third finding")
        self.assertTrue((directory / "I03.mdx").is_file())
        self.assertEqual(second_bytes, second.read_bytes())

    def test_t03_stall_gap_uses_filenames(self) -> None:
        """An unreadable stall incident still reserves its number."""
        run = self.init5()
        self.assign_simple(run, "T01")
        incidents = run / ".incidents"
        incidents.mkdir(parents=True, exist_ok=True)
        (incidents / "T01-2.json").write_text("corrupted{")
        second_bytes = (incidents / "T01-2.json").read_bytes()
        flagged = self.cli("health", "demo", "--flag-stall", "T01", "--cause", "provider hung")
        self.assertIn("flagged stall T01-3", flagged.stdout)
        self.assertTrue((incidents / "T01-3.json").is_file())
        self.assertEqual(second_bytes, (incidents / "T01-2.json").read_bytes())
        record = json.loads((incidents / "T01-3.json").read_text())
        self.assertEqual("provider hung", record["cause"])

    def test_t81_skipped_verification_cannot_support_approval(self) -> None:
        """An approving verdict over skipped verification is refused; waiving still works."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        submitted = self.cli("submit", "demo", "T01", "--as", "implementor",
                             "--skip-verify", "--skip-verify-reason", "broken command")
        self.assertIn("submitted for review", submitted.stdout)
        manifest, _ = self.frozen("T01")
        self.assertEqual("skipped", manifest["verification"]["status"])
        self.assertEqual("broken command", manifest["verification"]["skip_reason"])
        bundle_out = self.cli("bundle", "demo", "T01").stdout
        self.assertIn("skipped", bundle_out)
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        refused = self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer",
                           ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("skipped", refused.stderr)
        self.assertIn("--waive", refused.stderr)
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())
        self.cli("decide", "demo", "T01", "--waive", "--reason",
                 "broken command needs waiver", "--as", "reviewer")
        self.assertIn("status: waived", (run / "T01-report-01.mdx").read_text())

    def test_t81_quick_skipped_verification_refuses_approval(self) -> None:
        """The audit reproduction: quick init with verify false, skip, checker pass, no approval."""
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "documents-only")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/a.py",
                 "--verify", "false")
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.fill_task_report(run / "T01-report-01.mdx")
        submitted = self.cli("submit", "demo", "T01", "--as", "implementor",
                             "--skip-verify", "--skip-verify-reason", "broken command")
        self.assertIn("submitted for review", submitted.stdout)
        shown = self.cli("bundle", "demo", "T01").stdout
        self.assertIn("skipped", shown)
        self.assertIn("false", shown)
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "checker",
                 "--verifier", "checker")
        refused = self.cli("decide", "demo", "T01", "--approve", "--as", "checker",
                           "--reviewer", "checker", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("skipped", refused.stderr)
        self.assertIn("--waive", refused.stderr)
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        self.assertIn("status: submitted", (run / "T01-report-01.mdx").read_text())

    def test_t81_declared_env_overrides_ambient_and_records_effective(self) -> None:
        """Declared inputs control the run; only declared keys are captured as effective."""
        run = self.init5()
        self.cli("assign", "demo", "T01", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/t01.py",
                 "--verify", 'test "$FOO" = "actual"', "--env", "FOO=declared")
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        refused = self.cli_env({"FOO": "actual"}, "submit", "demo", "T01",
                               "--as", "implementor", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("verify command failed", refused.stderr)
        self.cli("assign", "demo", "T02", "--complexity", "high", "--executor",
                 "implementor", "--harness", "opencode", "--file", "src/t02.py",
                 "--verify", 'test "$FOO" = "declared"', "--env", "FOO=declared")
        self.fill_task(run / "T02-task.mdx")
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli_env({"FOO": "actual", "UNRELATED": "zzz"}, "submit", "demo", "T02",
                     "--as", "implementor")
        manifest, _ = self.frozen("T02")
        self.assertEqual("passed", manifest["verification"]["status"])
        self.assertEqual(["FOO=declared"], manifest["verification"]["declared_env"])
        self.assertEqual(["FOO=declared"], manifest["verification"]["effective_env"])
        self.assertNotIn("UNRELATED", json.dumps(manifest["verification"]))
        self.assertNotIn("zzz", json.dumps(manifest["verification"]))

    def test_t81_rereview_report_names_replacement_bundle(self) -> None:
        """After a re-review the decided report names the replacement digest, not the superseded one."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        first, _ = self.frozen("T01")
        first_digest = first["digest"]
        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Implemented the feature, verified its behavior, and rewrote the guard.",
        ))
        applied = self.cli("decide", "demo", "T01", "--approve", "--re-review")
        self.assertIn("froze the re-reviewed evidence as sha256:", applied.stdout)
        second, _ = self.frozen("T01")
        second_digest = second["digest"]
        self.assertNotEqual(first_digest, second_digest)
        decision_text = (run / "T01-decision-01.mdx").read_text()
        self.assertIn(f"bundle_digest: {second_digest}", decision_text)
        self.assertIn(f"superseded_bundle: {first_digest}", decision_text)
        report_meta = parse_meta(report)
        decision_meta = parse_meta(run / "T01-decision-01.mdx")
        self.assertEqual(second_digest, report_meta["bundle_digest"])
        self.assertEqual(second_digest, decision_meta["bundle_digest"])

    def test_t81_interrupted_rereview_resumes_without_second_round(self) -> None:
        """An interrupted re-review repeated finishes without a second round or stale pointer."""
        run = self.init("split", evidence_mode="documents-only")
        self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "demo", "T01")
        first_digest = self.frozen("T01")[0]["digest"]
        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Implemented the feature, verified its behavior, and rewrote the guard.",
        ))
        crash = self.cli("decide", "demo", "T01", "--approve", "--re-review",
                         ok=False, fault="transition:decision")
        self.assertEqual(70, crash.returncode)
        resumed = self.cli("decide", "demo", "T01", "--approve")
        self.assertIn("resumed interrupted approved transition txn:", resumed.stdout)
        rounds = sorted(p.name for p in run.glob("T01-report-*.mdx"))
        self.assertEqual(["T01-report-01.mdx"], rounds)
        second_digest = self.frozen("T01")[0]["digest"]
        self.assertNotEqual(first_digest, second_digest)
        report_meta = parse_meta(report)
        decision_meta = parse_meta(run / "T01-decision-01.mdx")
        self.assertEqual(second_digest, report_meta["bundle_digest"])
        self.assertEqual(second_digest, decision_meta["bundle_digest"])
        self.assertIn("status: approved", report.read_text())

    def test_t82_ordering_past_ninety_nine_resolves_to_failure(self) -> None:
        """Past 99 attempts the newest verification counts: pass at 99 then fail at 100 is a failure."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        for _ in range(98):
            self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.assertTrue((run / "T01-verification-99.mdx").is_file())
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "regression at attempt 100")
        self.assertTrue((run / "T01-verification-100.mdx").is_file())
        rendered = self.cli("bundle", "demo", "T01").stdout
        self.assertIn("T01", rendered)
        refused = self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer",
                           ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("no passing verification", refused.stderr)
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        peek = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertIn("T01:1:verification-failed", peek)

    def test_t82_dependency_cycle_refused_at_ingress(self) -> None:
        """A cycle is refused at ingress on both surfaces; diamonds, chains, and re-declares still work."""
        run = self.init5()
        refused_self = self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t01.py",
            "--verify", 'printf "T01 ok\\n"', "--depends-on", "T01", ok=False)
        self.assertNotEqual(0, refused_self.returncode)
        self.assertIn("cycle", refused_self.stderr.lower())
        self.assertIn("T01", refused_self.stderr)
        self.assertFalse((run / "T01-task.mdx").exists())
        self.assertFalse((run / "T01-report-01.mdx").exists())
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t01.py",
            "--verify", 'printf "T01 ok\\n"')
        self.fill_task(run / "T01-task.mdx")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.cli(
            "assign", "demo", "T02", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t02.py",
            "--verify", 'printf "T02 ok\\n"')
        self.fill_task(run / "T02-task.mdx")
        self.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", "demo", "T02", "--submit")
        refused_batch = self.cli(
            "batch", "demo", "--create", "B1", "--members", "T01,T02",
            "--depends-on", "T01:T02", "--depends-on", "T02:T01", ok=False)
        self.assertNotEqual(0, refused_batch.returncode)
        self.assertIn("cycle", refused_batch.stderr.lower())
        self.assertFalse((run / ".batches" / "B1.json").exists())
        self.cli("batch", "demo", "--create", "B1", "--members", "T01,T02",
                 "--depends-on", "T02:T01")
        self.assertTrue((run / ".batches" / "B1.json").is_file())
        self.cli(
            "assign", "demo", "T10", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t10.py",
            "--verify", 'printf "T10 ok\\n"')
        self.fill_task(run / "T10-task.mdx")
        self.fill_scope(run / "T10-scope.mdx")
        self.cli("scope", "demo", "T10", "--submit")
        self.cli(
            "assign", "demo", "T11", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t11.py",
            "--verify", 'printf "T11 ok\\n"', "--depends-on", "T10")
        self.fill_task(run / "T11-task.mdx")
        self.fill_scope(run / "T11-scope.mdx")
        self.cli("scope", "demo", "T11", "--submit")
        self.cli(
            "assign", "demo", "T12", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t12.py",
            "--verify", 'printf "T12 ok\\n"', "--depends-on", "T10")
        self.fill_task(run / "T12-task.mdx")
        self.fill_scope(run / "T12-scope.mdx")
        self.cli("scope", "demo", "T12", "--submit")
        self.cli(
            "assign", "demo", "T13", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t13.py",
            "--verify", 'printf "T13 ok\\n"',
            "--depends-on", "T11", "--depends-on", "T12")
        self.fill_task(run / "T13-task.mdx")
        self.fill_scope(run / "T13-scope.mdx")
        self.cli("scope", "demo", "T13", "--submit")
        status = self.cli("status", "demo").stdout
        self.assertIn("T13", status)
        self.cli("batch", "demo", "--create", "B2", "--members", "T11,T12",
                 "--depends-on", "T11:T10", "--depends-on", "T11:T10")
        self.assertTrue((run / ".batches" / "B2.json").is_file())

    def test_t91_verifier_correction_records_acting_role(self) -> None:
        """A verifier correction records checker in quick and verifier in standard."""
        # Standard: verifier acts and the decision names verifier.
        self.cli("init", "demo", "--mode", "standard", "--evidence-mode",
                 "documents-only", "--harness", "claude")
        run = self.root / ".docket" / "runs" / "demo"
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("verifier_correction: forbidden",
                                                 "verifier_correction: allowed"))
        self.assign_simple(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "1. oracle mismatch at src/t01.py:1",
                 "--open-correction")
        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("reviewer: verifier", decision)
        self.assertIn("triggered_by: verifier", decision)
        # The recorded verifier is the role that acted: checker is refused here.
        refused_checker = self.cli("verify", "demo", "T01", "--result", "pass",
                                   "--as", "checker", ok=False)
        self.assertNotEqual(0, refused_checker.returncode)
        # Quick: checker acts and the decision names checker.
        self.cli("init", "demoq", "--mode", "quick", "--evidence-mode",
                 "documents-only", "--harness", "claude")
        qrun = self.root / ".docket" / "runs" / "demoq"
        qplan = qrun / "plan.mdx"
        qplan.write_text(qplan.read_text().replace("verifier_correction: forbidden",
                                                   "verifier_correction: allowed"))
        self.assign_simple_in(qrun, "demoq", "T01")
        self.fill_task_report(qrun / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demoq", "T01", "--as", "implementor")
        # The quick run refuses the standard name for this operation.
        refused_verifier = self.cli("verify", "demoq", "T01", "--result", "fail",
                                    "--as", "verifier", "--detail", "1. x",
                                    ok=False)
        self.assertNotEqual(0, refused_verifier.returncode)
        self.assertIn("checker", refused_verifier.stderr)
        self.cli("verify", "demoq", "T01", "--result", "fail", "--as", "checker",
                 "--verifier", "checker",
                 "--detail", "1. oracle mismatch at src/t01.py:1",
                 "--open-correction")
        qdecision = (qrun / "T01-decision-01.mdx").read_text()
        self.assertIn("reviewer: checker", qdecision)
        self.assertIn("triggered_by: checker", qdecision)
        self.assertNotIn("reviewer: verifier", qdecision)
        # The verification artifact keeps its acting identity as today.
        qvmeta = parse_meta(qrun / "T01-verification-01.mdx")
        self.assertEqual("checker", qvmeta["verifier"])
        # The recorded checker is accepted where verifier was refused:
        # opening the correction with checker succeeded above, and a second
        # verifier attempt still refuses.
        still_refused = self.cli("verify", "demoq", "T01", "--result", "fail",
                                 "--as", "verifier", "--detail", "1. y",
                                 ok=False)
        self.assertNotEqual(0, still_refused.returncode)

    def test_t91_batch_refuses_non_member_source(self) -> None:
        """A batch edge from a non-member is refused fail-closed; member edges work."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        # Audit reproduction: source T99 is not a member.
        refused = self.cli("batch", "demo", "--create", "Bbad",
                           "--members", "T01,T02",
                           "--depends-on", "T99:T01", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("T99", refused.stderr)
        self.assertIn("source", refused.stderr.lower())
        self.assertFalse((run / ".batches" / "Bbad.json").exists())
        # A legitimate edge whose source is a member in the same command works.
        created = self.cli("batch", "demo", "--create", "B1",
                           "--members", "T01,T02",
                           "--depends-on", "T02:T01")
        self.assertIn("created batch B1", created.stdout)
        self.assertTrue((run / ".batches" / "B1.json").is_file())
        batch = json.loads((run / ".batches" / "B1.json").read_text())
        self.assertEqual(["T01"], batch["depends_on"]["T02"])
        self.cli("batch", "demo", "--close", "B1")
        # The real member edge still blocks the dependent dispatch as today.
        self.cli("session", "demo", "--register", "--session", "s1",
                 "--name", "w1", "--role", "implementor")
        blocked = self.cli("dispatch", "demo", "T02", "--session", "s1",
                           ok=False)
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn("T01", blocked.stderr)
        # A batch written before the rule stays readable; its stray edge is inert.
        legacy = {
            "batch": "Blegacy",
            "run": "demo",
            "members": ["T01", "T02"],
            "depends_on": {"T99": ["T01"]},
            "milestone": False,
            "state": "open",
            "generation": 1,
            "created_at": "2026-01-01T00:00:00Z",
        }
        (run / ".batches" / "Blegacy.json").write_text(json.dumps(legacy))
        listed = self.cli("batch", "demo", "--list").stdout
        self.assertIn("Blegacy", listed)
        self.assertIn("B1", listed)

    def test_t82_dispatch_refuses_unresolved_task_intent(self) -> None:
        """The same untouched task the validator rejects is a task dispatch refuses, with no binding."""
        run = self.init5()
        self.cli(
            "assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
            "--harness", "opencode", "--file", "src/t01.py",
            "--verify", 'printf "T01 ok\\n"')
        task_path = run / "T01-task.mdx"
        self.assertIn("<!-- TODO", task_path.read_text())
        invalid = self.cli("validate-task", "demo", "T01", ok=False)
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("INVALID TASK", invalid.stderr)
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "impl-one", "--role", "implementor")
        refused = self.cli("dispatch", "demo", "T01", "--session", "w1", ok=False)
        self.assertNotEqual(0, refused.returncode)
        for problem in ("unresolved placeholder", "acceptance criterion", "task needs"):
            if problem in invalid.stderr:
                self.assertIn(problem, refused.stderr)
                break
        else:
            self.assertIn(invalid.stderr.strip().splitlines()[-1][:20], refused.stderr)
        self.assertFalse((run / ".dispatch" / "T01.json").exists())
        prompt_refused = self.cli("prompt", "demo", "T01", "--role", "implementor",
                                  ok=False)
        self.assertNotEqual(0, prompt_refused.returncode)
        self.assertIn("missing required artifact", prompt_refused.stderr)
        self.assertIn("T01-task.mdx", prompt_refused.stderr)
        self.assertNotIn("<!-- TODO", prompt_refused.stdout)

    def test_p1_orchestrator_task_in_closed_milestone_reaches_verifier_and_reviewer(self) -> None:
        """An executor:orchestrator submission gets a verifier event and milestone review."""
        run = self.init5()
        self.assign_orchestrator_task(run, "T01")
        self.submit_orchestrator_task(run, "T01")
        orphan = run / "T99-report-01.mdx"
        orphan.write_text((run / "T01-report-01.mdx").read_text())
        self.cli("batch", "demo", "--create", "M1", "--members", "T01",
                 "--milestone")
        self.cli("batch", "demo", "--close", "M1")
        verifier = self.cli("events", "demo", "--role", "verifier", "--peek").stdout
        self.assertIn("T01:1:submitted", verifier)
        self.assertNotIn("T99:1:submitted", verifier)
        refused = self.cli("decide", "demo", "T01", "--approve",
                           "--as", "reviewer", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("no passing verification", refused.stderr)
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "orchestrator evidence needs a stronger assertion")
        failed = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertIn("T01:1:verification-failed", failed)
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier",
                 "--detail", "orchestrator evidence holds")
        reviewer = self.cli("events", "demo", "--role", "reviewer", "--peek").stdout
        self.assertIn("batch:M1:ready:", reviewer)
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer")
        self.assertIn("status: approved", (run / "T01-report-01.mdx").read_text())

    def test_p2_orchestrator_correction_wakes_orchestrator(self) -> None:
        """A reviewer correction for orchestrator-owned work wakes its supervisor."""
        run = self.init5()
        self.assign_orchestrator_task(run, "T01")
        self.submit_orchestrator_task(run, "T01")
        self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
        decision = run / "T01-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Correct `src/t01.py:1` and rerun the registered verification.",
        ))
        self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")

        orchestrator = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        correction_events = [
            line for line in orchestrator.splitlines() if "T01:2:correction-ready" in line
        ]
        self.assertEqual(1, len(correction_events))
        self.assertIn("re-dispatch", orchestrator)
        planner = self.cli("events", "demo", "--role", "planner", "--peek").stdout
        self.assertNotIn("T01:2:correction-ready", planner)

    def test_p2_quick_dispatch_orch_binds_coordinator_session(self) -> None:
        """In quick mode dispatch demo orch accepts a registered coordinator session."""
        self.cli("init", "demo", "--mode", "quick", "--evidence-mode", "documents-only")
        run = self.root / ".docket" / "runs" / "demo"
        self.cli(
            "assign", "demo", "orch", "--complexity", "high", "--executor",
            "orchestrator", "--harness", "opencode", "--file", "src/orch.py",
            "--verify", 'printf "orch ok\\n"',
        )
        self.cli("session", "demo", "--register", "--session", "c1",
                 "--name", "coord", "--role", "coordinator")
        dispatched = self.cli("dispatch", "demo", "orch", "--session", "c1")
        self.assertIn("dispatched orch round 1", dispatched.stdout)
        record = json.loads((run / ".dispatch" / "orch.json").read_text())
        self.assertEqual("coordinator", record.get("role"))
        self.assertEqual("c1", record.get("session"))

    def test_p2_resume_after_correction_binds_current_round_and_holds_capacity(self) -> None:
        """Resume of a correction round records the new round so the cap still counts it."""
        run = self.init_policy(max_concurrency="1")
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("session", "demo", "--register", "--session", "w2",
                 "--name", "worker-2", "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01")
        self.cli("decide", "demo", "T01", "--changes")
        dec = run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Correct `src/t01.py:1` and add a regression.",
        ))
        self.cli("decide", "demo", "T01", "--changes", "--reason", "Needs a guard.")
        self.assertTrue((run / "T01-report-02.mdx").is_file())
        resumed = self.cli("resume", "demo", "T01", "--session", "w1",
                           "--reason", "restart after correction")
        self.assertIn("resumed T01 round 2", resumed.stdout)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(2, int(record.get("round", 0)))
        prompt = self.cli("prompt", "demo", "T01", "--role", "implementor").stdout
        self.assertEqual(sha(prompt.encode()), record.get("prompt_digest"))
        capped = self.cli("dispatch", "demo", "T02", "--session", "w2", ok=False)
        self.assertNotEqual(0, capped.returncode)
        self.assertIn("concurrency 1/1", capped.stderr)

    def test_p2_cross_round_resume_refuses_unfinished_dependency(self) -> None:
        """A resume cannot bind a new round before its dependency is terminal."""
        run = self.init_policy()
        self.assign_simple(run, "T01")
        self.assign_simple(run, "T02")
        self.cli("session", "demo", "--register", "--session", "w1",
                 "--name", "worker-1", "--role", "implementor")
        self.cli("dispatch", "demo", "T02", "--session", "w1")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/t02.py:1` - implemented T02.")
        self.cli("submit", "demo", "T02")

        # The dependency is introduced after round 1 was dispatched. The
        # correction round must still obey it before a resume can rebind.
        task = run / "T02-task.mdx"
        task.write_text(task.read_text().replace("depends_on: ", "depends_on: T01"))
        self.cli("decide", "demo", "T02", "--changes")
        decision = run / "T02-decision-01.mdx"
        decision.write_text(decision.read_text().replace(
            "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->",
            "1. Correct `src/t02.py:1` and rerun the registered verification.",
        ))
        self.cli("decide", "demo", "T02", "--changes")

        refused = self.cli("resume", "demo", "T02", "--session", "w1", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("T02 cannot resume with unmet dependencies", refused.stderr)
        self.assertIn("T01 (draft)", refused.stderr)
        record = json.loads((run / ".dispatch" / "T02.json").read_text())
        self.assertEqual(1, int(record.get("round", 0)))
        self.assertIn("status: draft", (run / "T02-report-02.mdx").read_text())

    def test_p2_numbered_verifier_findings_keep_indented_continuations(self) -> None:
        """Indented continuation lines survive into the correction decision and prompt."""
        run = self.init5()
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("verifier_correction: forbidden",
                                                 "verifier_correction: allowed"))
        self.submit5(run, "T01")
        detail = ("1. Guard `src/t01.py:1` against empty input\n"
                  "   must use constant-time compare in `src/t01.py:42`\n"
                  "2. Add a regression test for the empty case")
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", detail, "--open-correction")
        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("1. Guard", decision)
        self.assertIn("constant-time compare", decision)
        self.assertIn("2. Add a regression test", decision)
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn("stage: correction", out)
        self.assertIn("constant-time compare", out)

    def test_p2_first_round_blocked_renders_reviewer_prompt_without_decision(self) -> None:
        """A round-1 blocked report derives a review stage the reviewer can render."""
        run = self.init5()
        self.assign_simple(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True,
                              files="- `src/t01.py:1` - blocked change.")
        self.cli("submit", "demo", "T01", "--blocked", "--as", "implementor")
        out = self.prompt_text(run, "T01", "reviewer")
        self.assertIn("stage: review", out)
        self.assertNotIn("stage: correction", out)
        wrong_stage = self.cli("prompt", "demo", "T01", "--role", "verifier",
                               "--stage", "verification", ok=False)
        self.assertIn("found blocked", wrong_stage.stderr)
        self.cli("decide", "demo", "T01", "--waive", "--reason", "external outage",
                 "--as", "reviewer")
        self.assertIn("status: waived", (run / "T01-report-01.mdx").read_text())

    # ---------------- wake coverage: every open state wakes exactly someone

    DECISION_PLACEHOLDER = (
        "<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->")

    def init5_in(self, run_id: str, mode: str = "") -> Path:
        """A documents-only five-role run under its own id, standard unless quick."""
        if mode == "quick":
            self.cli("init", run_id, "--mode", "quick", "--evidence-mode", "documents-only")
        else:
            self.cli("init", run_id, "--harness", "claude", "--topology", "split",
                     "--evidence-mode", "documents-only", "--workflow", "five-role-v1")
        return self.root / ".docket" / "runs" / run_id

    def request_changes(self, run: Path, run_id: str, owner: str, reviewer: str = "reviewer",
                        rnd: int = 1) -> None:
        """Open, fill, and apply a reviewer changes decision for one round."""
        self.cli("decide", run_id, owner, "--changes", "--as", reviewer)
        dec = run / f"{owner}-decision-{rnd:02d}.mdx"
        dec.write_text(dec.read_text().replace(self.DECISION_PLACEHOLDER, self.REQUIREMENT))
        self.cli("decide", run_id, owner, "--changes", "--as", reviewer)

    def test_t04_one_call_changes_records_numbered_changes(self) -> None:
        """Repeatable --change numbers every item and applies in one invocation."""
        run = self.init5_in("t04a")
        self.submit5_in(run, "t04a", "T01")
        applied = self.cli(
            "decide", "t04a", "T01", "--changes", "--as", "reviewer",
            "--change", "Guard `src/t01.py:1` against empty input.",
            "--change", "Add a regression test for the guard.")
        self.assertIn("needs changes -> T01-decision-01.mdx", applied.stdout)
        self.assertIn("opened next round -> T01-report-02.mdx", applied.stdout)
        self.assertNotIn("opened decision draft", applied.stdout)
        body = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("1. Guard `src/t01.py:1` against empty input.", body)
        self.assertIn("2. Add a regression test for the guard.", body)
        self.assertIn("status: changes-requested", (run / "T01-report-01.mdx").read_text())
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run, "T01"))

    def test_t04_applied_changes_message_names_preset_handoff(self) -> None:
        """The applied message states the preset handoff, never a write-again prompt."""
        run = self.init5_in("t04b")
        self.submit5_in(run, "t04b", "T01")
        opened = self.cli("decide", "t04b", "T01", "--changes", "--as", "reviewer")
        self.assertIn("opened decision draft", opened.stdout)
        dec = run / "T01-decision-01.mdx"
        dec.write_text(dec.read_text().replace(self.DECISION_PLACEHOLDER, self.REQUIREMENT))
        applied = self.cli("decide", "t04b", "T01", "--changes", "--as", "reviewer")
        self.assertIn(
            "recorded 1 required change in T01-decision-01.mdx; the orchestrator receives "
            "a `correction-ready` wake and re-dispatches the implementor into "
            "T01-report-02.mdx.", applied.stdout)
        self.assertNotIn("Write the required changes", applied.stdout)

    def test_t04_one_call_changes_names_coordinator_in_quick(self) -> None:
        """In quick runs the one-call handoff names the coordinator, not the orchestrator."""
        run = self.init5_in("t04c", mode="quick")
        self.submit5_in(run, "t04c", "T01")
        applied = self.cli(
            "decide", "t04c", "T01", "--changes", "--as", "checker",
            "--reviewer", "checker",
            "--change", "Tighten `src/t01.py:1` per the oracle.")
        self.assertIn(
            "recorded 1 required change in T01-decision-01.mdx; the coordinator receives "
            "a `correction-ready` wake and re-dispatches the implementor into "
            "T01-report-02.mdx.", applied.stdout)
        self.assertNotIn("Write the required changes", applied.stdout)
        self.assertIn("1. Tighten `src/t01.py:1` per the oracle.",
                      (run / "T01-decision-01.mdx").read_text())

    def test_t04_change_without_changes_is_refused(self) -> None:
        """--change records required changes, so it needs --changes."""
        run = self.init5_in("t04d")
        self.submit5_in(run, "t04d", "T01")
        refused = self.cli("decide", "t04d", "T01", "--approve", "--as", "reviewer",
                           "--change", "Stray.", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("--change needs --changes", refused.stderr)
        self.assertFalse((run / "T01-decision-01.mdx").exists())

    def test_t04_interrupted_one_call_changes_recovers_exactly(self) -> None:
        """An interrupted one-call correction finishes as journalled, never rewritten."""
        run = self.init5_in("t04e")
        self.submit5_in(run, "t04e", "T01")
        crashed = self.cli(
            "decide", "t04e", "T01", "--changes", "--as", "reviewer",
            "--change", "A.", "--change", "B.", ok=False, fault="transition:begin")
        self.assertEqual(70, crashed.returncode)
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        refused = self.cli(
            "decide", "t04e", "T01", "--changes", "--as", "reviewer",
            "--change", "C.", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("already recorded its required changes", refused.stderr)
        self.assertFalse((run / "T01-decision-01.mdx").exists())
        self.assertEqual(["T01-report-01.mdx"], self.rounds(run, "T01"))
        finished = self.cli("decide", "t04e", "T01", "--changes", "--as", "reviewer")
        self.assertIn("needs changes -> T01-decision-01.mdx", finished.stdout)
        decision = (run / "T01-decision-01.mdx").read_text()
        self.assertIn("applied: yes", decision)
        self.assertIn("1. A.", decision)
        self.assertIn("2. B.", decision)
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run, "T01"))
        # Repeating the same items after a later crash still finishes with one new round.
        self.fill_task_report(run / "T01-report-02.mdx",
                              files="- `src/t01.py:2` - applied the correction.")
        self.cli("submit", "t04e", "T01", "--as", "implementor")
        crashed2 = self.cli(
            "decide", "t04e", "T01", "--changes", "--as", "reviewer",
            "--change", "A2.", "--change", "B2.", ok=False, fault="transition:decision")
        self.assertEqual(70, crashed2.returncode)
        repeated = self.cli(
            "decide", "t04e", "T01", "--changes", "--as", "reviewer",
            "--change", "A2.", "--change", "B2.")
        self.assertIn("needs changes -> T01-decision-02.mdx", repeated.stdout)
        decision2 = (run / "T01-decision-02.mdx").read_text()
        self.assertIn("applied: yes", decision2)
        self.assertIn("1. A2.", decision2)
        self.assertIn("2. B2.", decision2)
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx", "T01-report-03.mdx"],
                         self.rounds(run, "T01"))

    def derived_keys(self, run_id: str, role: str) -> list[str]:
        """Event keys derived for one role, read through non-consuming inspection."""
        out = self.cli("events", run_id, "--role", role, "--peek").stdout
        return re.findall(r"^\s*\[(?:pending|delivered)\s*\]\s+(\S+)", out, re.MULTILINE)

    def woken(self, run_id: str, roles: tuple[str, ...]) -> dict[str, list[str]]:
        return {role: keys for role in roles if (keys := self.derived_keys(run_id, role))}

    def watch_once(self, run_id: str, role: str, now: float | None = None,
                   ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("DOCKET_FAULT", None)
        if now is not None:
            env["DOCKET_NOW"] = str(now)
        return subprocess.run(
            [sys.executable, str(DOCKET), "watch", run_id, "--role", role, "--timeout", "1"],
            cwd=str(self.root), text=True, capture_output=True, env=env)

    def lease_expiry(self, run: Path, role: str) -> float:
        records = (run / ".delivery" / role / "announce").glob("*.json")
        return max(float(json.loads(p.read_text())["lease_until"]) for p in records)

    def test_interrupted_verifier_correction_finishes_on_retry_without_recharging(self) -> None:
        """A retry finishes an interrupted verifier correction; nothing strands or double-counts."""
        detail = "1. Guard `src/t01.py:1` against empty input"
        command = ("--result", "fail", "--as", "verifier", "--detail", detail,
                   "--open-correction")
        for index, fault in enumerate(("verify:before-correction", "transition:begin",
                                       "transition:decision", "transition:report")):
            rid = f"vc{index}"
            run = self.init5_in(rid)
            plan = run / "plan.mdx"
            plan.write_text(plan.read_text().replace("verifier_correction: forbidden",
                                                     "verifier_correction: allowed"))
            self.submit5_in(run, rid, "T01")
            crashed = self.cli("verify", rid, "T01", *command, ok=False, fault=fault)
            self.assertEqual(70, crashed.returncode, f"{fault}: {crashed.stderr}")
            self.assertEqual(["T01-report-01.mdx"], self.rounds(run, "T01"), fault)
            # The interrupted state is never silent: the reviewer is told how
            # to finish it, and nobody is told the work is reviewable.
            reviewer = self.cli("events", rid, "--role", "reviewer", "--peek").stdout
            expected = ("T01:1:verification-failed" if fault == "verify:before-correction"
                        else "T01:1:unfinished-changes-requested")
            self.assertEqual([expected], self.derived_keys(rid, "reviewer"), fault)
            self.assertIn("interrupted", reviewer, fault)
            self.assertEqual([], self.derived_keys(rid, "orchestrator"), fault)
            # A different verdict, or different findings, cannot slip past it.
            passed = self.cli("verify", rid, "T01", "--result", "pass", "--as", "verifier",
                              ok=False)
            self.assertIn("unfinished verifier-triggered correction", passed.stderr, fault)
            restated = self.cli("verify", rid, "T01", "--result", "fail", "--as", "verifier",
                                "--detail", "1. something else", "--open-correction", ok=False)
            self.assertIn("already recorded these findings", restated.stderr, fault)
            retried = self.cli("verify", rid, "T01", *command)
            self.assertIn("resuming the interrupted verifier correction", retried.stdout, fault)
            self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"],
                             self.rounds(run, "T01"), fault)
            self.assertEqual(["T01-verification-01.mdx"],
                             sorted(p.name for p in run.glob("T01-verification-*.mdx")), fault)
            corrections = json.loads((run / ".corrections" / "T01.json").read_text())
            self.assertEqual(1, corrections["verifier_returns"], fault)
            decision = (run / "T01-decision-01.mdx").read_text()
            self.assertIn("verification: T01-verification-01.mdx", decision, fault)
            self.assertIn("Guard `src/t01.py:1`", decision, fault)
            self.assertEqual(["T01:2:correction-ready"], self.derived_keys(rid, "orchestrator"),
                             fault)
            self.assertEqual([], self.derived_keys(rid, "reviewer"), fault)
            again = self.cli("verify", rid, "T01", *command, ok=False)
            self.assertIn("not submitted", again.stderr, fault)

    def test_interrupted_verifier_correction_can_be_finished_by_the_reviewer(self) -> None:
        """The reviewer woken for an interrupted correction finishes the recorded decision."""
        run = self.init5()
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("verifier_correction: forbidden",
                                                 "verifier_correction: allowed"))
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "1. Guard input", "--open-correction", ok=False,
                 fault="transition:decision")
        self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer")
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run, "T01"))
        decision = parse_meta(run / "T01-decision-01.mdx")
        self.assertEqual("verifier", decision["triggered_by"])
        corrections = json.loads((run / ".corrections" / "T01.json").read_text())
        self.assertEqual(1, corrections["verifier_returns"])
        self.assertEqual(0, corrections["reviewer_returns"])

    def test_orchestrator_owned_blocked_task_wakes_the_reviewer(self) -> None:
        """A blocked executor:orchestrator round needs a reviewer decision and gets a wake."""
        run = self.init5()
        self.assign_orchestrator_task(run, "T01")
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True,
                              files="- `src/t01.py:1` - blocked change.")
        self.cli("submit", "demo", "T01", "--blocked", "--as", "orchestrator")
        self.assertEqual({"reviewer": ["T01:1:blocked"]},
                         self.woken("demo", ("planner", "orchestrator", "verifier", "reviewer")))
        self.cli("decide", "demo", "T01", "--waive", "--reason", "external outage",
                 "--as", "reviewer")
        self.assertEqual([], self.derived_keys("demo", "reviewer"))

    def test_aggregate_correction_wakes_the_orchestrator(self) -> None:
        """Changes on the aggregate re-enter the orchestrator, with or without delegated tasks."""
        for rid, delegated in (("only-orch", False), ("mixed", True)):
            run = self.init5_in(rid)
            if delegated:
                self.submit5_in(run, rid, "T01")
            else:
                self.assign_orchestrator_task(run, "T01", rid)
                self.submit_orchestrator_task(run, "T01", rid)
            self.cli("verify", rid, "T01", "--result", "pass", "--as", "verifier")
            self.cli("decide", rid, "T01", "--approve", "--as", "reviewer")
            self.assertTrue(any(key.startswith("all:decided:")
                                for key in self.derived_keys(rid, "orchestrator")), rid)
            self.submit_aggregate(run, rid)
            self.assertEqual(["orch:1:submitted"], self.derived_keys(rid, "reviewer"), rid)
            self.request_changes(run, rid, "orch")
            self.assertIn("orch:2:correction-ready", self.derived_keys(rid, "orchestrator"), rid)
            self.assertEqual([], self.derived_keys(rid, "reviewer"), rid)

    def test_correction_wake_retires_once_the_round_is_dispatched(self) -> None:
        """Dispatching the correction round ends its wake; no lease expiry revives it."""
        run = self.init5()
        self.assign_simple(run, "T01")
        for sid in ("w1", "w2"):
            self.cli("session", "demo", "--register", "--session", sid,
                     "--name", sid, "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.request_changes(run, "demo", "T01")
        self.assertEqual(["T01:2:correction-ready"], self.derived_keys("demo", "orchestrator"))
        woke = self.watch_once("demo", "orchestrator")
        self.assertEqual(2, woke.returncode)
        self.assertIn("needs re-dispatch", woke.stderr)
        until = self.lease_expiry(run, "orchestrator")
        self.cli("dispatch", "demo", "T01", "--session", "w2")
        self.assertEqual([], self.derived_keys("demo", "orchestrator"))
        quiet = self.watch_once("demo", "orchestrator", now=until + 1)
        self.assertEqual(0, quiet.returncode, quiet.stderr)
        self.assertEqual("", quiet.stderr)

    def test_a_delivered_wake_is_not_reannounced_while_its_identity_holds(self) -> None:
        """A handled event that persists never re-wakes; a changed one wakes once."""
        run = self.init5()
        self.submit5(run, "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        first = self.watch_once("demo", "orchestrator")
        self.assertEqual(2, first.returncode)
        self.assertIn("review batch ready with 1 verified", first.stderr)
        # The reviewer is still deciding, so the batch stays derived. That is
        # not news: well past the lease the supervisor is left alone.
        until = self.lease_expiry(run, "orchestrator")
        for later in (until + 1, until + 3600):
            quiet = self.watch_once("demo", "orchestrator", now=later)
            self.assertEqual(0, quiet.returncode, quiet.stderr)
        # New evidence is a new identity and wakes exactly once.
        self.submit5(run, "T02")
        self.cli("verify", "demo", "T02", "--result", "pass", "--as", "verifier")
        second = self.watch_once("demo", "orchestrator", now=until + 7200)
        self.assertEqual(2, second.returncode)
        self.assertIn("T02 round 1", second.stderr)
        self.assertEqual(0, self.watch_once("demo", "orchestrator",
                                            now=until + 99999).returncode)

    def test_resume_is_held_to_the_current_model_policy(self) -> None:
        """Resume cannot carry a model the policy no longer approves; --model picks one."""
        run = self.init_policy(primary_model="m1")
        self.assign_simple(run, "T01")
        for sid in ("w1", "w2"):
            self.cli("session", "demo", "--register", "--session", sid,
                     "--name", sid, "--role", "implementor")
        self.cli("dispatch", "demo", "T01", "--session", "w1")
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "demo", "T01")
        self.request_changes(run, "demo", "T01", reviewer="reviewer")
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("primary_model: m1", "primary_model: m2"))
        refused_dispatch = self.cli("dispatch", "demo", "T01", "--session", "w2",
                                    "--model", "m1", ok=False)
        self.assertIn("outside the approved", refused_dispatch.stderr)
        refused = self.cli("resume", "demo", "T01", "--session", "w1", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("cannot resume on model 'm1'", refused.stderr)
        self.assertIn("--model m2", refused.stderr)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(1, int(record["round"]))
        self.assertEqual("m1", record["model_requested"])
        outside = self.cli("resume", "demo", "T01", "--session", "w1", "--model", "m3",
                           ok=False)
        self.assertIn("outside the approved", outside.stderr)
        resumed = self.cli("resume", "demo", "T01", "--session", "w1", "--model", "m2")
        self.assertIn("resumed T01 round 2", resumed.stdout)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(2, int(record["round"]))
        self.assertEqual("m2", record["model_requested"])
        self.assertEqual("unobserved", record["model_observed"])
        self.assertEqual(["requested", "resume"],
                         [entry["kind"] for entry in record["model_history"]])
        prompt = self.cli("prompt", "demo", "T01", "--role", "implementor",
                          "--model", "m2").stdout
        self.assertEqual(sha(prompt.encode()), record["prompt_digest"])

    def test_five_role_review_readiness_waits_for_verification(self) -> None:
        """The orchestrator is woken to route verified work, not merely submitted work."""
        run = self.init5()
        self.submit5(run, "T01")
        self.assertEqual([], self.derived_keys("demo", "orchestrator"))
        self.assertEqual(["T01:1:submitted"], self.derived_keys("demo", "verifier"))
        self.cli("verify", "demo", "T01", "--result", "fail", "--as", "verifier",
                 "--detail", "1. oracle mismatch")
        self.assertEqual([], self.derived_keys("demo", "orchestrator"))
        self.cli("verify", "demo", "T01", "--result", "uncertain", "--as", "verifier",
                 "--detail", "timing could not be measured offline")
        after_uncertain = self.derived_keys("demo", "orchestrator")
        self.assertEqual(1, len(after_uncertain))
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        after_pass = self.derived_keys("demo", "orchestrator")
        self.assertEqual(1, len(after_pass))
        self.assertNotEqual(after_uncertain, after_pass)
        # A closed non-milestone batch follows the same rule.
        batched = self.init5_in("batched")
        self.submit5_in(batched, "batched", "T01")
        self.cli("batch", "batched", "--create", "B1", "--members", "T01")
        self.cli("batch", "batched", "--close", "B1")
        self.assertEqual([], self.derived_keys("batched", "orchestrator"))
        self.cli("verify", "batched", "T01", "--result", "pass", "--as", "verifier")
        self.assertEqual(["batch:B1:ready:1"], self.derived_keys("batched", "orchestrator"))

    def test_redispatch_records_a_renamed_agent(self) -> None:
        """Re-dispatching the same session under a new agent name updates the binding."""
        run = self.init5_in("ra", mode="quick")
        self.assign_simple_in(run, "ra", "T01")
        self.cli("dispatch", "ra", "T01", "--session", "w1", "--agent", "impl-1", "--register")
        again = self.cli("dispatch", "ra", "T01", "--session", "w1", "--agent", "r01-impl-1",
                         "--register")
        self.assertIn("agent: r01-impl-1", again.stdout)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(("w1", "r01-impl-1"), (record["session"], record["agent"]))

    def test_a_routed_block_wakes_the_checker_to_decide_it(self) -> None:
        """A block wakes the coordinator; routing it hands the verdict to the checker durably."""
        run = self.init5_in("rb", mode="quick")
        self.assign_simple_in(run, "rb", "T01")
        self.cli("dispatch", "rb", "T01", "--session", "w1", "--register")
        early = self.cli("route", "rb", "--kind", "blocked", "--owner", "T01", ok=False)
        self.assertIn("round 1 is draft, not blocked", early.stderr)
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True)
        self.cli("submit", "rb", "T01", "--blocked", "--as", "implementor")
        self.assertEqual(["T01:1:blocked"], self.derived_keys("rb", "coordinator"))
        with self.subTest("coordinator route instruction"):
            self.assertIn(
                "`docket route rb --kind blocked --owner T01` queues a notification for the checker",
                self.cli("events", "rb", "--role", "coordinator", "--peek").stdout)
        self.assertEqual([], self.derived_keys("rb", "checker"))
        routed = self.cli("route", "rb", "--kind", "blocked", "--owner", "T01",
                          "--note", "keep the default port")
        self.assertIn("routed T01 round 1 to the checker", routed.stdout)
        self.assertIn("notification queued", routed.stdout)
        self.assertIn("checker is unarmed for rb", routed.stdout)
        self.assertFalse((self.root / ".docket" / "watch.conf").exists())
        route_record = run / ".routes" / "T01-01-blocked.json"
        first_record = route_record.read_bytes()
        repeated = self.cli("route", "rb", "--kind", "blocked", "--owner", "T01")
        self.assertIn("notification queued", repeated.stdout)
        self.assertEqual(first_record, route_record.read_bytes())
        self.assertEqual([], self.derived_keys("rb", "coordinator"))
        self.assertEqual(["T01:1:blocked-routed"], self.derived_keys("rb", "checker"))
        self.assertIn("orchestrator answer: keep the default port",
                      self.cli("events", "rb", "--role", "checker", "--peek").stdout)
        self.cli("decide", "rb", "T01", "--waive", "--reason", "accepted as documented",
                 "--as", "checker")
        self.assertEqual([], self.derived_keys("rb", "checker"))
        self.assertTrue(self.derived_keys("rb", "coordinator")[0].startswith("all:decided:"))

    def test_t07_route_names_preset_role_and_armed_state(self) -> None:
        quick = self.init5_in("routequick", mode="quick")
        for kind, role in (("plan-gap", "coordinator"), ("collision", "coordinator"),
                           ("dispute", "checker")):
            with self.subTest(kind=kind):
                advisory = self.cli("route", "routequick", "--kind", kind)
                self.assertIn(f"destination: {role}", advisory.stdout)
                self.assertIn("action:", advisory.stdout)
                self.assertIn("no event queued; this route is advisory", advisory.stdout)
                self.assertIn(f"{role} is unarmed for routequick", advisory.stdout)
        self.assertFalse((self.root / ".docket" / "watch.conf").exists())
        self.cli("arm", "routequick", "--role", "coordinator")
        armed = self.cli("route", "routequick", "--kind", "plan-gap")
        self.assertNotIn("unarmed", armed.stdout)
        standard = self.init5_in("routestd")
        named = self.cli("route", "routestd", "--kind", "plan-gap")
        self.assertIn("destination: planner", named.stdout)
        self.assertIn("planner is unarmed for routestd", named.stdout)

    def test_t07_block_route_playbooks_describe_the_queue(self) -> None:
        references = DOCKET.resolve().parents[1] / "references"
        for role in ("coordinator", "orchestrator"):
            with self.subTest(role=role):
                text = (references / f"{role}.md").read_text()
                self.assertIn("queues a durable notification" if role == "orchestrator"
                              else "queues a notification", text)
                self.assertIn("Arm the" if role == "orchestrator" else "must be armed", text)

    def test_t07_prompt_uses_absolute_cli_without_path_entry(self) -> None:
        run = self.init5_in("nopath")
        self.assign_simple_in(run, "nopath", "T01")
        normal = self.prompt_text_in("nopath", "T01", "implementor")
        self.assertIn("`docket scope nopath T01 --submit`", normal)
        absent = self.cli_env({"PATH": ""}, "prompt", "nopath", "T01",
                              "--role", "implementor").stdout
        launcher = str(DOCKET.resolve())
        self.assertIn(f"`{launcher} scope nopath T01 --submit`", absent)
        self.assertIn(f"task-local diff: {launcher} diff nopath T01", absent)
        self.assertNotIn("`docket scope nopath", absent)
        self.assertIn("goal: Implement the feature.", absent)

    def test_a_grant_after_a_verifier_exhaustion_opens_exactly_that_correction(self) -> None:
        """Regression: a refused verifier correction was still charged, so a grant freed nothing.

        The escalated verification counted as a return, the retry wrote a second one
        charged again, and no event told the verifier that a grant had landed.
        """
        run = self.init5()
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text()
                        .replace("verifier_correction: forbidden", "verifier_correction: allowed")
                        .replace("correction_limit: 2", "correction_limit: 0"))
        self.submit5(run, "T01")
        exhausted = self.cli("verify", "demo", "T01", "--result", "fail", "--open-correction",
                             "--detail", "1. Fix the oracle at src/t01.py:1", "--as", "verifier",
                             ok=False)
        self.assertIn("exceeded its local correction budget", exhausted.stderr)
        granted = self.cli("escalation", "demo", "T01", "--grant", "1", "--as", "planner",
                           "--reason", "worth one round")
        self.assertIn("0 used of 1", granted.stdout)
        self.assertIn("T01:1:budget-granted:T01-r01", self.derived_keys("demo", "verifier"))

        self.cli("verify", "demo", "T01", "--result", "fail", "--open-correction",
                 "--as", "verifier")
        self.assertEqual(["T01-report-01.mdx", "T01-report-02.mdx"], self.rounds(run, "T01"))
        self.assertEqual(1, len(list(run.glob("T01-verification-*.mdx"))),
                         "the retry finishes the recorded correction instead of a second one")
        self.assertNotIn("T01:1:budget-granted:T01-r01", self.derived_keys("demo", "verifier"))

        # Exhausting the granted chain again keeps the first grant's audit record.
        self.fill_task_report(run / "T01-report-02.mdx", files="- `src/t01.py:1` - fixed T01.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "fail", "--open-correction",
                 "--detail", "1. Still wrong at src/t01.py:1", "--as", "verifier", ok=False)
        escalation = json.loads((run / ".escalations" / "T01-r01.json").read_text())
        self.assertEqual("open", escalation["state"])
        self.assertEqual(["worth one round"], [h.get("reason") for h in escalation["history"]])

    def test_a_waived_dependency_releases_its_dependent_and_says_so(self) -> None:
        """Regression: a waived dependency left its dependent undispatchable and unwoken."""
        run = self.init5_in("wd", mode="quick")
        self.assign_simple_in(run, "wd", "T01")
        self.cli("assign", "wd", "T02", "--complexity", "high", "--executor", "implementor",
                 "--harness", "opencode", "--file", "src/t02.py", "--verify", 'printf "T02 ok\\n"',
                 "--depends-on", "T01")
        self.fill_task(run / "T02-task.mdx")
        self.cli("dispatch", "wd", "T01", "--session", "w1", "--register")
        self.fill_task_report(run / "T01-report-01.mdx", blocked=True)
        self.cli("submit", "wd", "T01", "--blocked", "--as", "implementor")
        self.cli("route", "wd", "--kind", "blocked", "--owner", "T01", "--note", "skip it")
        self.cli("decide", "wd", "T01", "--waive", "--reason", "not needed now", "--as", "checker")

        self.assertIn("T02:1:dispatch-ready", self.derived_keys("wd", "coordinator"))
        peek = self.cli("events", "wd", "--role", "coordinator", "--peek").stdout
        self.assertIn("T01 waived", peek)
        dispatched = self.cli("dispatch", "wd", "T02", "--session", "w2", "--register")
        self.assertIn("T01 was waived, not approved", dispatched.stdout)
        self.assertNotIn("T02:1:dispatch-ready", self.derived_keys("wd", "coordinator"))

    def test_every_open_lifecycle_state_wakes_a_supervisor(self) -> None:
        """No owner and state combination is left with every supervising role asleep."""
        standard = ("planner", "orchestrator", "verifier", "reviewer")
        quick = ("coordinator", "checker")
        cases: list[tuple[str, str, str]] = []

        def expect(rid: str, mode: str, role: str) -> None:
            cases.append((rid, mode, role))

        def fresh(rid: str, mode: str, orchestrator_task: bool) -> Path:
            run = self.init5_in(rid, mode)
            if orchestrator_task:
                self.assign_orchestrator_task(run, "T01", rid)
                self.submit_orchestrator_task_as(run, rid, "T01", mode)
            else:
                self.submit5_in(run, rid, "T01")
            return run

        for mode in ("standard", "quick"):
            verifier = "checker" if mode == "quick" else "verifier"
            reviewer = "checker" if mode == "quick" else "reviewer"
            orchestrator = "coordinator" if mode == "quick" else "orchestrator"
            for kind in ("impl", "orch"):
                owned = kind == "orch"
                prefix = f"{mode[0]}{kind}"
                fresh(f"{prefix}-submitted", mode, owned)
                expect(f"{prefix}-submitted", mode, verifier)
                run = fresh(f"{prefix}-passed", mode, owned)
                self.cli("verify", f"{prefix}-passed", "T01", "--result", "pass",
                         "--as", verifier)
                # Standard routes verified work through the orchestrator; quick
                # wakes the checker, who holds the review duty itself.
                expect(f"{prefix}-passed", mode, reviewer if mode == "quick" else orchestrator)
                run = fresh(f"{prefix}-failed", mode, owned)
                self.cli("verify", f"{prefix}-failed", "T01", "--result", "fail",
                         "--as", verifier, "--detail", "1. oracle mismatch")
                expect(f"{prefix}-failed", mode, reviewer)
                run = fresh(f"{prefix}-changes", mode, owned)
                self.request_changes(run, f"{prefix}-changes", "T01", reviewer=reviewer)
                expect(f"{prefix}-changes", mode, orchestrator)
                run = fresh(f"{prefix}-approved", mode, owned)
                self.cli("verify", f"{prefix}-approved", "T01", "--result", "pass",
                         "--as", verifier)
                self.cli("decide", f"{prefix}-approved", "T01", "--approve", "--as", reviewer)
                expect(f"{prefix}-approved", mode, orchestrator)
                blocked_id = f"{prefix}-blocked"
                run = self.init5_in(blocked_id, mode)
                if owned:
                    self.assign_orchestrator_task(run, "T01", blocked_id)
                else:
                    self.assign_simple_in(run, blocked_id, "T01")
                self.fill_task_report(run / "T01-report-01.mdx", blocked=True,
                                      files="- `src/t01.py:1` - blocked change.")
                self.cli("submit", blocked_id, "T01", "--blocked", "--as",
                         orchestrator if owned else "implementor")
                expect(blocked_id, mode, reviewer if owned else orchestrator)

        for rid, mode, role in cases:
            roles = quick if mode == "quick" else standard
            woken = self.woken(rid, roles)
            self.assertIn(role, woken, f"{rid}: expected {role} to be woken, got {woken}")

    def submit_orchestrator_task_as(self, run: Path, run_id: str, owner: str,
                                    mode: str) -> None:
        """Submit an executor:orchestrator task as the run's orchestrating session."""
        self.fill_task_report(run / f"{owner}-report-01.mdx",
                              files=f"- `src/{owner.lower()}.py:1` - implemented {owner}.")
        role = "coordinator" if mode == "quick" else "orchestrator"
        self.cli("submit", run_id, owner, "--as", role)

    def test_an_interrupted_reviewer_decision_wakes_whoever_finishes_it(self) -> None:
        """Every interruption window of a decision wakes the role that repeats it."""
        for index, fault in enumerate(("transition:begin", "transition:decision",
                                       "transition:report")):
            rid = f"rd{index}"
            run = self.init5_in(rid)
            self.submit5_in(run, rid, "T01")
            self.cli("verify", rid, "T01", "--result", "pass", "--as", "verifier")
            self.cli("decide", rid, "T01", "--changes", "--as", "reviewer")
            dec = run / "T01-decision-01.mdx"
            dec.write_text(dec.read_text().replace(self.DECISION_PLACEHOLDER, self.REQUIREMENT))
            crashed = self.cli("decide", rid, "T01", "--changes", "--as", "reviewer",
                               ok=False, fault=fault)
            self.assertEqual(70, crashed.returncode, fault)
            self.assertEqual(["T01:1:unfinished-changes-requested"],
                             self.derived_keys(rid, "reviewer"), fault)
            self.assertEqual([], self.derived_keys(rid, "verifier"), fault)
            peek = self.cli("events", rid, "--role", "reviewer", "--peek").stdout
            self.assertIn(f"docket decide {rid} T01 --changes --as reviewer", peek, fault)
            self.cli("decide", rid, "T01", "--changes", "--as", "reviewer")
            self.assertEqual({"orchestrator": ["T01:2:correction-ready"]},
                             self.woken(rid, ("planner", "orchestrator", "verifier",
                                              "reviewer")), fault)
        # A legacy orchestrator decides its own task rounds and is woken instead.
        legacy = self.init_legacy_as("legacy", evidence_mode="documents-only")
        self.assign_simple_in(legacy, "legacy", "T01")
        self.fill_task_report(legacy / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "legacy", "T01")
        self.cli("decide", "legacy", "T01", "--approve", ok=False, fault="transition:decision")
        self.assertIn("T01:1:unfinished-approved", self.derived_keys("legacy", "orchestrator"))
        self.cli("decide", "legacy", "T01", "--approve")
        self.assertNotIn("T01:1:unfinished-approved",
                         self.derived_keys("legacy", "orchestrator"))

    def test_aggregate_prompt_leaves_out_plan_placeholders(self) -> None:
        """An unfilled plan section reads as none in the aggregate prompt, not as a comment."""
        self.cli("init", "bare", "--evidence-mode", "documents-only", "--objective", "Ship it.")
        run = self.root / ".docket" / "runs" / "bare"
        self.assertIn("<!-- TODO: the shape of the solution", (run / "plan.mdx").read_text())
        self.cli("assign", "bare", "orch", "--executor", "orchestrator",
                 "--verify", 'printf "ok\\n"')
        prompt = self.cli("prompt", "bare", "orch", "--role", "coordinator").stdout
        self.assertIn("approach: none", prompt)
        self.assertNotIn("TODO", prompt)

    def test_a_run_starts_in_three_commands(self) -> None:
        """init, assign with intent, and dispatch --register reach a bound, sendable prompt."""
        init = self.cli("init", "fast", "--evidence-mode", "documents-only",
                        "--title", "Guard input", "--objective", "Empty input is rejected.",
                        "--approach", "One guard at the entry point.")
        self.assertIn("mode: quick", init.stdout)
        run = self.root / ".docket" / "runs" / "fast"
        plan = (run / "plan.mdx").read_text()
        self.assertIn("# Plan: Guard input", plan)
        self.assertIn("Empty input is rejected.", plan)
        self.assertIn("## Approach\n\nOne guard at the entry point.", plan)
        self.assertEqual([], [line for line in plan.splitlines() if "<!-- TODO" in line])
        assigned = self.cli("assign", "fast", "T01", "--harness", "opencode",
                            "--file", "src/a.py", "--verify", 'printf "T01 ok\\n"',
                            "--title", "Empty guard", "--goal", "Empty input raises.",
                            "--criterion", "guard('') raises ValueError",
                            "--criterion", "callers are unchanged")
        self.assertIn("task intent is ready to dispatch", assigned.stdout)
        task = (run / "T01-task.mdx").read_text()
        self.assertIn("# Task T01: Empty guard", task)
        self.assertIn("- [ ] guard('') raises ValueError", task)
        self.assertIn("- [ ] callers are unchanged", task)
        self.assertEqual([], [line for line in task.splitlines() if "<!-- TODO" in line])
        self.cli("validate-task", "fast", "T01")
        dispatched = self.cli("dispatch", "fast", "T01", "--session", "impl-1",
                              "--agent", "impl-1", "--register")
        self.assertIn("registered session impl-1 for fast implementor (generation 1)",
                      dispatched.stdout)
        match = re.search(r"^prompt: (\S+) ", dispatched.stdout, re.MULTILINE)
        self.assertIsNotNone(match, dispatched.stdout)
        prompt_file = self.root / match.group(1)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(sha(prompt_file.read_bytes()), record["prompt_digest"])
        rendered = self.cli("prompt", "fast", "T01", "--role", "implementor").stdout
        self.assertEqual(rendered.encode(), prompt_file.read_bytes())
        # A retried dispatch reuses the registration instead of advancing it,
        # so it adopts its own record rather than looking like a new writer.
        again = self.cli("dispatch", "fast", "T01", "--session", "impl-1",
                         "--agent", "impl-1", "--register")
        self.assertIn("already dispatched", again.stdout)
        self.assertNotIn("registered session", again.stdout)
        registration = json.loads(
            (self.root / ".docket" / "sessions" / "impl-1.json").read_text())
        self.assertEqual(1, registration["generation"])
        # An incomplete intent still refuses, and registers nothing first.
        self.cli("assign", "fast", "T02", "--harness", "opencode", "--file", "src/b.py",
                 "--goal", "Only a goal, no criteria.")
        refused = self.cli("dispatch", "fast", "T02", "--session", "impl-2", "--register",
                           ok=False)
        self.assertIn("T02 cannot dispatch with unresolved task intent", refused.stderr)
        self.assertFalse((self.root / ".docket" / "sessions" / "impl-2.json").exists())
        orch = self.cli("assign", "fast", "orch", "--goal", "not a task", ok=False)
        self.assertIn("plan Objective", orch.stderr)

    # ---------------- prompts a worker can act on without the playbook

    def test_prompts_carry_the_exact_steps_for_their_role_and_stage(self) -> None:
        """Each rendered prompt names the run's real files and commands with the right --as."""
        run = self.init5_in("steps", mode="quick")
        self.assign_simple_in(run, "steps", "T01")
        initial = self.prompt_text_in("steps", "T01", "implementor")
        self.assertIn("# Steps", initial)
        self.assertIn("`.docket/runs/steps/T01-scope.mdx`", initial)
        self.assertIn("`docket scope steps T01 --submit`", initial)
        self.assertIn("`docket submit steps T01 --as implementor`", initial)
        self.assertIn("`docket submit steps T01 --blocked --as implementor`", initial)
        self.assertIn("`.docket/runs/steps/T01-report-01.mdx`", initial)
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "steps", "T01", "--as", "implementor")
        checking = self.prompt_text_in("steps", "T01", "checker")
        self.assertIn("stage: verification", checking)
        self.assertIn("# Applicable verification obligations", checking)
        self.assertIn("--as checker --verifier checker", checking)
        self.assertIn("`docket decide steps T01 --approve --as checker --reviewer checker "
                      "--reason", checking)
        self.request_changes(run, "steps", "T01", reviewer="checker")
        correcting = self.prompt_text_in("steps", "T01", "implementor")
        self.assertIn("stage: correction", correcting)
        self.assertIn("Apply every required change listed above.", correcting)
        self.assertIn("`.docket/runs/steps/T01-report-02.mdx`", correcting)
        self.assertIn("files changed (every path `docket diff steps T01` lists, earlier rounds "
                      "included)", correcting)
        self.assertIn("Only if you must stop before submitting", correcting)
        # Standard names its own roles, and legacy needs no --as at all.
        standard = self.init5_in("std")
        self.submit5_in(standard, "std", "T01")
        verifying = self.prompt_text_in("std", "T01", "verifier")
        self.assertIn("`docket verify std T01 --result pass|fail|uncertain --as verifier",
                      verifying)
        self.assertNotIn("docket decide", verifying)
        self.cli("verify", "std", "T01", "--result", "pass", "--as", "verifier")
        reviewing = self.prompt_text_in("std", "T01", "reviewer")
        self.assertIn("`docket decide std T01 --approve --as reviewer --reason", reviewing)
        legacy = self.init_legacy_as("old", evidence_mode="documents-only")
        self.assign_simple_in(legacy, "old", "T01")
        old = self.prompt_text_in("old", "T01", "implementor")
        self.assertIn("`docket submit old T01`", old)
        self.assertNotIn("--as", old)

    def prompt_text_in(self, run_id: str, owner: str, role: str) -> str:
        return self.cli("prompt", run_id, owner, "--role", role).stdout

    def test_prompts_are_unwrapped_and_carry_no_empty_fields(self) -> None:
        """Wrapped reference prose becomes one line per item; unstated fields are left out."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace(
            "Implement the feature.", "Fix the herdr pane wake hook for the terminal."))
        out = self.prompt_text(run, "T01", "implementor")
        self.assertIn("[harness-plumbing]", out)
        self.assertIn("- Confirm the live model label instead of trusting launch flags; record "
                      "what was observed, requested, or is unknown.", out)
        for empty in ("existing decisions: none", "discovery constraints: none",
                      "plan risks: none", "starting hints (advisory only): none",
                      "latest decision: (none yet)", "reason: none"):
            self.assertNotIn(empty, out)
        self.assertIn("hard constraints: none stated", out)
        # Help output is unwrapped the same way, with fences left alone.
        help_text = self.cli("help", "implementor").stdout
        self.assertIn("Read the complete `Txx-task.mdx`. Own repository discovery: begin with "
                      "project guidance/manifests", help_text)
        self.assertIn("```bash\ndocket handoff <run> <owner>\n", help_text)

    def test_guidance_triggers_match_whole_words_not_fragments(self) -> None:
        """Common words and fragments no longer pull in unrelated guidance cards."""
        run = self.init5()
        self.assign_simple(run, "T01")
        task = run / "T01-task.mdx"
        base = task.read_text()
        task.write_text(base.replace(
            "Implement the feature.",
            "Strip whitespace from the input and trace blocked users in a session."))
        out = self.prompt_text(run, "T01", "implementor")
        self.assertNotIn("[harness-plumbing]", out)
        self.assertNotIn("[concurrency]", out)
        task.write_text(base.replace("Implement the feature.",
                                     "Fix the concurrent lock handling."))
        self.assertIn("[concurrency]", self.prompt_text(run, "T01", "implementor"))

    def test_reports_start_with_the_task_criteria_to_check(self) -> None:
        """Every draft round is seeded with the task criteria; nothing written is overwritten."""
        run = self.init5_in("seed", mode="quick")
        self.cli("assign", "seed", "T01", "--harness", "opencode", "--file", "src/t01.py",
                 "--verify", 'printf "T01 ok\\n"', "--goal", "Works.",
                 "--criterion", "first criterion", "--criterion", "second criterion")
        report = (run / "T01-report-01.mdx").read_text()
        self.assertIn("- [ ] first criterion\n- [ ] second criterion", report)
        self.assertNotIn("- [ ] <!-- TODO -->", report)
        # A task filled in after assignment is seeded when it is dispatched.
        self.assign_simple_in(run, "seed", "T02")
        self.assertIn("- [ ] <!-- TODO -->", (run / "T02-report-01.mdx").read_text())
        self.cli("dispatch", "seed", "T02", "--session", "w2", "--register")
        self.assertIn("- [ ] The feature works.", (run / "T02-report-01.mdx").read_text())
        # A worker's own acceptance text is never replaced.
        self.assign_simple_in(run, "seed", "T03")
        mine = run / "T03-report-01.mdx"
        mine.write_text(mine.read_text().replace("- [ ] <!-- TODO -->", "- [x] my own words"))
        self.cli("dispatch", "seed", "T03", "--session", "w3", "--register")
        self.assertIn("- [x] my own words", mine.read_text())
        self.assertNotIn("- [ ] The feature works.", mine.read_text())
        # A correction round starts seeded too.
        report_path = run / "T01-report-01.mdx"
        text = report_path.read_text()
        for item in ("first criterion", "second criterion"):
            text = text.replace(f"- [ ] {item}", f"- [x] {item}")
        report_path.write_text(text)
        self.fill_task_report(report_path, files="- `src/t01.py:1` - implemented T01.")
        self.cli("scope", "seed", "T01", "--submit", ok=False)
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "seed", "T01", "--submit")
        self.cli("submit", "seed", "T01", "--as", "implementor")
        self.request_changes(run, "seed", "T01", reviewer="checker")
        self.assertIn("- [ ] first criterion\n- [ ] second criterion",
                      (run / "T01-report-02.mdx").read_text())

    def test_a_dependent_task_takes_its_baseline_at_dispatch(self) -> None:
        """A dependency's approved work is the dependent's starting point, not its diff."""
        self.repo()
        self.cli("init", "dep", "--evidence-mode", "git")
        run = self.root / ".docket" / "runs" / "dep"
        (self.root / "src" / "b.py").write_text("value = 1\n")
        self.git("add", "src/b.py")
        self.git("commit", "-qm", "b")
        self.cli("assign", "dep", "T01", "--harness", "opencode", "--file", "src/a.py",
                 "--verify", 'printf "T01 ok\\n"', "--goal", "Change a.",
                 "--criterion", "The feature works.")
        gated = self.cli("assign", "dep", "T02", "--harness", "opencode", "--file", "src/b.py",
                         "--verify", 'printf "T02 ok\\n"', "--depends-on", "T01",
                         "--goal", "Change b on top of a.", "--criterion", "The feature works.")
        self.assertIn("baseline is captured when it is dispatched", gated.stdout)
        self.assertFalse((run / ".snapshots" / "T02.json").exists())
        early = self.cli("dispatch", "dep", "T02", "--session", "w2", "--register", ok=False)
        self.assertIn("unmet dependencies", early.stderr)
        self.assertFalse((run / ".snapshots" / "T02.json").exists())
        # T01 is implemented and approved; its change stays uncommitted.
        self.cli("dispatch", "dep", "T01", "--session", "w1", "--register")
        self.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "dep", "T01", "--submit")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "dep", "T01", "--as", "implementor")
        self.cli("verify", "dep", "T01", "--result", "pass", "--as", "checker",
                 "--verifier", "checker")
        self.cli("decide", "dep", "T01", "--approve", "--as", "checker")
        dispatched = self.cli("dispatch", "dep", "T02", "--session", "w2", "--register")
        self.assertIn("captured T02's baseline at dispatch", dispatched.stdout)
        self.assertTrue((run / ".snapshots" / "T02.json").is_file())
        # T02 is built on T01's approved round, and its evidence says which one.
        self.assertIn("T02 consumes T01's approved bundle", dispatched.stdout)
        inputs = self.cli("depend", "dep", "T02").stdout
        self.assertIn("T01  final  approved", inputs)
        scope = run / "T02-scope.mdx"
        self.fill_scope(scope)
        scope.write_text(scope.read_text().replace("`src/a.py` contains", "`src/b.py` contains"))
        self.cli("scope", "dep", "T02", "--submit")
        (self.root / "src" / "b.py").write_text("value = 2\n")
        self.fill_task_report(run / "T02-report-01.mdx",
                              files="- `src/b.py:1` - built on T01.")
        diff = self.cli("diff", "dep", "T02").stdout
        self.assertIn("src/b.py", diff)
        self.assertNotIn("src/a.py", diff)
        self.cli("submit", "dep", "T02", "--as", "implementor")
        bundle = self.cli("bundle", "dep", "T02").stdout
        self.assertIn("1 path(s)", bundle)

    def test_a_dependent_task_wakes_its_supervisor_once_dispatchable(self) -> None:
        """Approving a dependency wakes the coordinator to dispatch its dependent, once."""
        run = self.init5_in("dw", mode="quick")
        self.assign_simple_in(run, "dw", "T01")
        self.cli("assign", "dw", "T02", "--harness", "opencode", "--file", "src/t02.py",
                 "--verify", 'printf "T02 ok\\n"', "--depends-on", "T01",
                 "--goal", "Builds on T01.", "--criterion", "works")
        self.cli("assign", "dw", "T03", "--harness", "opencode", "--file", "src/t03.py",
                 "--verify", 'printf "T03 ok\\n"', "--goal", "Independent.",
                 "--criterion", "works")
        self.cli("dispatch", "dw", "T01", "--session", "w1", "--register")
        self.assertFalse(any("dispatch-ready" in key
                             for key in self.derived_keys("dw", "coordinator")))
        self.fill_task_report(run / "T01-report-01.mdx",
                              files="- `src/t01.py:1` - implemented T01.")
        self.cli("submit", "dw", "T01", "--as", "implementor")
        self.cli("verify", "dw", "T01", "--result", "pass", "--as", "checker",
                 "--verifier", "checker")
        self.cli("decide", "dw", "T01", "--approve", "--as", "checker")
        ready = self.derived_keys("dw", "coordinator")
        self.assertIn("T02:1:dispatch-ready", ready)
        self.assertFalse(any(key.startswith("T03:") for key in ready))
        self.cli("dispatch", "dw", "T02", "--session", "w2", "--register")
        self.assertNotIn("T02:1:dispatch-ready", self.derived_keys("dw", "coordinator"))

    def test_redispatch_rebinds_a_prompt_the_task_changed_under(self) -> None:
        """The same writer re-dispatching after a task edit gets the new prompt bound."""
        run = self.init5_in("rb", mode="quick")
        self.cli("assign", "rb", "T01", "--harness", "opencode", "--file", "src/t01.py",
                 "--verify", 'printf "T01 ok\\n"', "--goal", "Guard input.",
                 "--criterion", "works", "--out-of-scope", "Do not touch word_count.",
                 "--decision", "Tokenize with str.split().")
        task = (run / "T01-task.mdx").read_text()
        self.assertIn("## Out of scope\n\n- Do not touch word_count.", task)
        self.assertIn("## Existing decisions\n\n- Tokenize with str.split().", task)
        first = self.cli("dispatch", "rb", "T01", "--session", "w1", "--register").stdout
        old_digest = json.loads((run / ".dispatch" / "T01.json").read_text())["prompt_digest"]
        same = self.cli("dispatch", "rb", "T01", "--session", "w1", "--register").stdout
        self.assertIn("already dispatched", same)
        self.assertNotIn("rebound", same)
        (run / "T01-task.mdx").write_text(task.replace(
            "- Tokenize with str.split().", "- Tokenize with str.split() only."))
        again = self.cli("dispatch", "rb", "T01", "--session", "w1", "--register").stdout
        self.assertIn("rebound T01 round 1", again)
        record = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertNotEqual(old_digest, record["prompt_digest"])
        self.assertEqual(old_digest, record["prompt_history"][-1]["digest"])
        prompt_file = self.root / re.search(r"^prompt: (\S+) ", again, re.MULTILINE).group(1)
        self.assertEqual(sha(prompt_file.read_bytes()), record["prompt_digest"])
        self.assertIn("str.split() only", prompt_file.read_text())
        self.assertFalse((run / ".checkpoints").exists())
        self.assertIn("prompt:", first)


    # ---------------- feedback embedded in every run

    def fake_claude_session(self) -> tuple[dict[str, str], Path]:
        """A Claude Code config dir holding one transcript and one subagent transcript."""
        sid = "0c1d2e3f-aaaa-bbbb-cccc-000000000001"
        home = Path(self._feedback_tmp.name) / "claude-home"
        transcript = home / "projects" / "-work-project" / f"{sid}.jsonl"
        subagent = transcript.parent / sid / "subagents" / "agent-1.jsonl"
        subagent.parent.mkdir(parents=True)

        def turn(mid: str, model: str, counts: tuple[int, int, int, int], at: str) -> str:
            usage = dict(zip(("input_tokens", "cache_creation_input_tokens",
                              "cache_read_input_tokens", "output_tokens"), counts))
            return json.dumps({"type": "assistant", "timestamp": at, "cwd": "/work/project",
                               "message": {"id": mid, "model": model, "usage": usage}})
        transcript.write_text("\n".join([
            json.dumps({"type": "user", "timestamp": "2026-09-23T01:00:00Z",
                        "cwd": "/work/project", "message": {"role": "user", "content": "go"}}),
            turn("m1", "claude-opus-5-5", (10, 100, 1000, 5), "2026-09-23T01:00:05Z"),
            # One API message spans several transcript lines; it is counted once.
            turn("m1", "claude-opus-5-5", (10, 100, 1000, 5), "2026-09-23T01:00:06Z"),
            turn("m2", "claude-opus-5-5", (20, 0, 1100, 7), "2026-09-23T01:00:10Z"),
            turn("m3", "<synthetic>", (0, 0, 0, 0), "2026-09-23T01:00:11Z"),
        ]) + "\n")
        subagent.write_text(turn("s1", "claude-haiku-4-5", (1, 0, 50, 2),
                                 "2026-09-23T01:00:08Z") + "\n")
        env = {"CLAUDE_CODE_SESSION_ID": sid, "CLAUDE_PID": str(os.getpid()),
               "CLAUDE_CONFIG_DIR": str(home),
               "CLAUDE_CODE_EXECPATH": "/opt/claude/versions/9.9.9"}
        return env, transcript

    def test_role_sessions_are_noted_with_usage_and_archived_transcripts(self) -> None:
        """A role's harness session is noted once; usage and archives come from its transcript."""
        run = self.init5()
        env, transcript = self.fake_claude_session()
        sid = env["CLAUDE_CODE_SESSION_ID"]
        self.cli_env(env, "arm", "demo", "--role", "orchestrator")
        self.cli_env(env, "arm", "demo", "--role", "orchestrator")
        self.cli_env(env, "feedback", "demo", "--add", "--role", "orchestrator",
                     "--category", "waiting", "--body", "none")
        ledger = [json.loads(line) for line in
                  (run / ".harness-sessions.jsonl").read_text().splitlines()]
        self.assertEqual([("orchestrator", "-", "claude", sid, "9.9.9")],
                         [(e["role"], e["owner"], e["harness"], e["session_id"], e["version"])
                          for e in ledger])
        observation = next(run.rglob("F01.mdx")).read_text()
        self.assertIn(f"session: claude:{sid}", observation)
        self.assertIn("harness: claude 9.9.9", observation)
        self.assertIn(f"transcript: {transcript}", observation)
        self.assertIn(f"session_cwd: {self.root}", observation)

        [row] = json.loads(self.cli_env(env, "usage", "demo", "--json").stdout)
        self.assertEqual((3, 31, 100, 2150, 14, 2295),
                         tuple(row[k] for k in ("calls", "input", "cache_write", "cache_read",
                                                "output", "total")))
        self.assertEqual(["claude-opus-5-5", "claude-haiku-4-5"], row["models"])
        self.assertEqual("/work/project", row["harness_cwd"])
        self.assertEqual("", row["archive"])

        shown = self.cli_env(env, "usage", "demo", "--archive").stdout
        self.assertIn("orchestrator", shown)
        self.assertIn("2.3k", shown)
        self.assertIn("archived 1 session(s)", shown)
        archive = Path(self._feedback_tmp.name) / "sessions"
        [kept] = list(archive.rglob("transcript.jsonl.gz"))
        self.assertEqual(transcript.read_bytes(), gzip.decompress(kept.read_bytes()))
        self.assertEqual(1, len(list(archive.rglob("agent-1.jsonl.gz"))))
        self.assertEqual(2295, json.loads((kept.parent / "usage.json").read_text())["total"])
        self.assertEqual(0o700, stat.S_IMODE(kept.parent.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(kept.stat().st_mode))
        log = Path(os.environ["DOCKET_FEEDBACK_LOG"]).read_text().splitlines()
        usage = [r for r in map(json.loads, log) if r.get("origin") == "usage"]
        self.assertEqual([("orchestrator", sid, 2295)],
                         [(r["role"], r["session_id"], r["total"]) for r in usage])
        digest = self.cli("feedback", "--digest").stdout
        self.assertIn("token usage by role", digest)
        self.assertIn("orchestrator   1 session(s) in 1 run(s): 2.3k total", digest)
        self.assertIn("agent reports: 1 (1 said none)", digest)

    def test_inherited_harness_variables_note_no_session(self) -> None:
        """Variables inherited from a harness that is not an ancestor are ignored."""
        run = self.init5()
        ledger = run / ".harness-sessions.jsonl"
        for env in ({"CLAUDE_CODE_SESSION_ID": "stale-session", "CLAUDE_PID": "4194000"},
                    {"OPENCODE_PID": "4194001"},
                    {"CLAUDE_CODE_SESSION_ID": "live", "CLAUDE_PID": str(os.getpid()),
                     "DOCKET_SESSION_CAPTURE": "off"}):
            self.cli_env(env, "arm", "demo", "--role", "orchestrator")
            self.assertFalse(ledger.exists(), env)
        self.assertIn("no harness session noted yet", self.cli("usage", "demo").stdout)

    def test_codex_session_is_noted_through_its_process(self) -> None:
        """A Codex thread is noted from its variable, proven by a codex ancestor process."""
        run = self.init5()
        tid = "01a0cc39-0000-7471-b60c-92c1c2dd9522"
        home = Path(self._feedback_tmp.name) / "codex-home"
        rollout = (home / "sessions" / "2026" / "09" / "23"
                   / f"rollout-2026-09-23T08-49-49-{tid}.jsonl")
        rollout.parent.mkdir(parents=True)

        def count(total: tuple[int, int, int, int, int]) -> dict:
            keys = ("input_tokens", "cached_input_tokens", "output_tokens",
                    "reasoning_output_tokens", "total_tokens")
            return {"type": "event_msg", "timestamp": "2026-09-23T03:00:05Z",
                    "payload": {"type": "token_count",
                                "info": {"total_token_usage": dict(zip(keys, total))}}}
        rollout.write_text("\n".join(json.dumps(r) for r in [
            {"type": "session_meta", "timestamp": "2026-09-23T03:00:00Z",
             "payload": {"id": tid, "cwd": "/work/project"}},
            {"type": "turn_context", "payload": {"model": "gpt-x"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": None}},
            count((1000, 800, 50, 10, 1050)),
            count((3000, 2500, 120, 30, 3120)),
        ]) + "\n")
        wrapper = Path(self._feedback_tmp.name) / "bin" / "codex"
        wrapper.parent.mkdir()
        wrapper.symlink_to(sys.executable)
        env = {**os.environ, "CODEX_THREAD_ID": tid, "CODEX_HOME": str(home),
               "CODEX_VERSION": "0.1.0"}
        launch = ("import subprocess, sys; sys.exit(subprocess.run([sys.executable, "
                  f"{str(DOCKET)!r}, 'arm', 'demo', '--role', 'orchestrator']).returncode)")
        subprocess.run([str(wrapper), "-c", launch], cwd=self.root, env=env, check=True,
                       capture_output=True, text=True)
        [entry] = [json.loads(line) for line in
                   (run / ".harness-sessions.jsonl").read_text().splitlines()]
        self.assertEqual(("codex", tid, "0.1.0"),
                         (entry["harness"], entry["session_id"], entry["version"]))
        [row] = json.loads(self.cli_env({"CODEX_HOME": str(home)}, "usage", "demo",
                                        "--json").stdout)
        self.assertEqual((2, 500, 2500, 120, 30, 3120, ["gpt-x"], "/work/project"),
                         tuple(row[k] for k in ("calls", "input", "cache_read", "output",
                                                "reasoning", "total", "models",
                                                "harness_cwd")))
        self.assertEqual(str(rollout), row["transcript"])

    def test_codex_watch_says_how_long_one_poll_may_be(self) -> None:
        """Under Codex the watcher names the one-hour poll; hooks and other harnesses get nothing."""
        self.init5()
        self.cli("arm", "demo", "--role", "orchestrator")
        wrapper = Path(self._feedback_tmp.name) / "bin" / "codex"
        wrapper.parent.mkdir()
        wrapper.symlink_to(sys.executable)
        launch = ("import subprocess, sys; sys.exit(subprocess.run([sys.executable, "
                  f"{str(DOCKET)!r}, 'watch', 'demo', '--role', 'orchestrator', "
                  "'--timeout', '1']).returncode)")

        home = Path(self._feedback_tmp.name) / "codex-home"
        home.mkdir()

        def watched(extra: dict[str, str]) -> str:
            env = {**os.environ, "CODEX_THREAD_ID": "01a0cc39-0000-7471-b60c-92c1c2dd9522",
                   "CODEX_HOME": str(home), **extra}
            return subprocess.run([str(wrapper), "-c", launch], cwd=self.root, env=env,
                                  capture_output=True, text=True).stderr
        default = watched({})
        self.assertIn("caps a poll at 300000 ms, so pass yield_time_ms: 300000", default)
        self.assertIn("background_terminal_max_timeout = 3600000", default)
        (home / "config.toml").write_text('model = "x"\nbackground_terminal_max_timeout = 3600000\n')
        raised = watched({})
        self.assertIn("allows one-hour polls", raised)
        self.assertIn("pass yield_time_ms: 3600000", raised)
        self.assertNotIn("yield_time_ms", watched({"DOCKET_WATCH_HOOK": "1"}))
        plain = self.cli("watch", "demo", "--role", "orchestrator", "--timeout", "1", ok=False)
        self.assertNotIn("yield_time_ms", plain.stderr)

    def test_opencode_session_is_the_one_running_this_command(self) -> None:
        """OpenCode names no session, so docket finds the one whose shell call it is."""
        run = self.init5()
        data = Path(self._feedback_tmp.name) / "xdg-data"
        db = data / "opencode" / "opencode.db"
        db.parent.mkdir(parents=True)
        now = int(time.time() * 1000)
        con = sqlite3.connect(db)
        con.executescript(
            "create table session (id text primary key, parent_id text, directory text, "
            "version text, time_updated integer);"
            "create table message (id text primary key, session_id text, "
            "time_created integer, data text);"
            "create table part (id text primary key, message_id text, session_id text, "
            "time_updated integer, data text);")

        def running(command: str) -> str:
            return json.dumps({"type": "tool", "tool": "bash",
                               "state": {"status": "running", "input": {"command": command}}})
        con.executemany("insert into session values (?, ?, ?, ?, ?)", [
            ("ses_other", None, "/elsewhere", "1.0.0", now),
            ("ses_mine", None, "/work/project", "1.18.32", now),
            ("ses_child", "ses_mine", "/work/project", "1.18.32", now)])
        con.executemany("insert into message values (?, ?, ?, ?)", [
            ("msg_u", "ses_mine", now - 3, json.dumps({"role": "user", "time": {"created": now}})),
            ("msg_a", "ses_mine", now - 2, json.dumps({
                "role": "assistant", "providerID": "deepseek", "modelID": "flash",
                "cost": 0.01, "time": {"created": now},
                "tokens": {"total": 1300, "input": 100, "output": 50, "reasoning": 150,
                           "cache": {"read": 1000, "write": 0}}})),
            ("msg_c", "ses_child", now - 1, json.dumps({
                "role": "assistant", "providerID": "deepseek", "modelID": "flash",
                "tokens": {"input": 10, "output": 5, "reasoning": 0,
                           "cache": {"read": 100, "write": 0}}}))])
        con.executemany("insert into part values (?, ?, ?, ?, ?)", [
            ("prt_o", "msg_o", "ses_other", now,
             running("docket feedback elsewhere --add --role implementor")),
            ("prt_m", "msg_a", "ses_mine", now,
             running(f"{DOCKET} feedback demo --add --role implementor --task T01"))])
        con.commit()
        con.close()
        fake = Path(self._feedback_tmp.name) / "bin" / "opencode"
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\n[ \"$1\" = export ] || exit 1\n"
                        "printf '{\"info\": {\"id\": \"%s\"}, \"messages\": []}\\n' \"$2\"\n")
        fake.chmod(0o755)
        env = {"OPENCODE_PID": str(os.getpid()), "XDG_DATA_HOME": str(data),
               "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"}
        self.cli_env(env, "feedback", "demo", "--add", "--role", "implementor", "--task", "T01",
                     "--category", "other", "--body", "none")
        [entry] = [json.loads(line) for line in
                   (run / ".harness-sessions.jsonl").read_text().splitlines()]
        self.assertEqual(("implementor", "T01", "opencode", "ses_mine", "1.18.32"),
                         (entry["role"], entry["owner"], entry["harness"], entry["session_id"],
                          entry["version"]))
        [row] = json.loads(self.cli_env(env, "usage", "demo", "--archive", "--json").stdout)
        self.assertEqual((2, 110, 55, 150, 1100, 1415, ["deepseek/flash"], "/work/project"),
                         tuple(row[k] for k in ("calls", "input", "output", "reasoning",
                                                "cache_read", "total", "models",
                                                "harness_cwd")))
        self.assertAlmostEqual(0.01, row["cost"])
        kept = Path(row["archive"])
        self.assertEqual({"info": {"id": "ses_mine"}, "messages": []},
                         json.loads(gzip.decompress((kept / "opencode-export.json.gz")
                                                    .read_bytes())))
        self.assertTrue((kept / "subagents" / "ses_child.json.gz").is_file())
        # Without a working export the database rows are kept instead.
        fake.write_text("#!/bin/sh\nexit 1\n")
        [row] = json.loads(self.cli_env(env, "usage", "demo", "--archive", "--json").stdout)
        rows = json.loads(gzip.decompress(
            (Path(row["archive"]) / "opencode-session.json.gz").read_bytes()))
        self.assertEqual(["ses_mine", "ses_child"], [s["id"] for s in rows["sessions"]])

    def test_opencode_running_session_reads_recent_sessions_through_part_index(self) -> None:
        """The newest active session wins, and SQLite looks up its parts by session."""
        db = self.root / "opencode.db"
        now = int(time.time() * 1000)
        con = sqlite3.connect(db)
        con.executescript(
            "create table session (id text primary key, version text, time_updated integer);"
            "create index session_recent_idx on session(time_updated desc);"
            "create table part (id text primary key, session_id text, "
            "time_updated integer, data text);"
            "create index part_session_idx on part(session_id);")
        con.executemany("insert into session values (?, ?, ?)", [
            ("ses_old", "1.0", now - 1000),
            ("ses_new", "2.0", now),
        ])
        command = json.dumps({"type": "tool", "state": {"status": "running",
                              "input": {"command": "docket submit demo T08"}}})
        con.executemany("insert into part values (?, ?, ?, ?)", [
            ("prt_old", "ses_old", now, command),
            ("prt_new", "ses_new", now - 500, command),
        ])
        con.executemany("insert into part values (?, ?, ?, ?)",
                        [(f"noise_{i}", "ses_old", now, "{}") for i in range(500)])
        con.commit()
        con.close()
        module = load_cli("docket_session_probe")
        module.opencode_db = lambda: db
        statements: list[str] = []
        original_connect = sqlite3.connect

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        sqlite3.connect = traced_connect
        try:
            found = module.opencode_running_session(["submit", "T08"])
            self.assertEqual(("", ""), module.opencode_running_session(["absent"]))
        finally:
            sqlite3.connect = original_connect
        query = next(statement for statement in statements
                     if "part" in statement and "session_id" in statement)
        con = sqlite3.connect(db)
        plan = [row[3] for row in con.execute("explain query plan " + query)]
        con.close()
        indexed = any("USING INDEX part_session_idx" in step for step in plan)
        scanned = any("SCAN p" in step or "SCAN part" in step for step in plan)
        self.assertEqual((("ses_new", "2.0"), True, False),
                         (found, indexed, scanned), plan)

    def test_final_aggregate_verdict_archives_every_noted_session(self) -> None:
        """Settling the run keeps each noted session's transcript before retention drops it."""
        run = self.setup_aggregate_run()
        env, _transcript = self.fake_claude_session()
        self.cli_env(env, "arm", "demo", "--role", "orchestrator")
        self.assign("orch", executor="orchestrator")
        self.fill_orch_report(run / "orch-report-01.mdx")
        self.cli("submit", "demo", "orch", "--skip-verify")
        decided = self.cli_env({"CLAUDE_CONFIG_DIR": env["CLAUDE_CONFIG_DIR"]},
                               "decide", "demo", "orch", "--approve")
        self.assertIn("archived 1 harness session(s)", decided.stdout)
        archive = Path(self._feedback_tmp.name) / "sessions"
        self.assertEqual(1, len(list(archive.rglob("transcript.jsonl.gz"))))

    def test_reject_cases_become_a_reviewed_profile_for_that_model(self) -> None:
        """Cases accumulate per model; a reviewed profile then rides every later prompt."""
        run = self.init5_in("ml", mode="quick")

        def through_a_correction(owner: str, finding: str, gate: bool = False) -> None:
            self.assign_simple_in(run, "ml", owner)
            self.cli("dispatch", "ml", owner, "--session", f"w-{owner}", "--register")
            self.cli("set-model", "ml", owner, "--actual", "acme/coder-1 (Acme Coder)")
            report = run / f"{owner}-report-01.mdx"
            if gate:
                report.write_text(report.read_text() + "\nTODO: finish\n")
                self.cli("submit", "ml", owner, "--as", "implementor", ok=False)
                report.write_text(report.read_text().replace("\nTODO: finish\n", "\n"))
            for rnd in (1, 2):
                self.fill_task_report(run / f"{owner}-report-{rnd:02d}.mdx",
                                      files=f"- `src/{owner.lower()}.py:1` - implemented.")
                self.cli("submit", "ml", owner, "--as", "implementor")
                if rnd == 1:
                    self.cli("verify", "ml", owner, "--result", "fail", "--as", "checker",
                             "--detail", finding)
                    self.request_changes(run, "ml", owner, reviewer="checker")
            self.cli("verify", "ml", owner, "--result", "pass", "--as", "checker",
                     "--detail", "1. fixed")
            self.cli("decide", "ml", owner, "--approve", "--as", "checker", "--reason", "holds")

        through_a_correction("T01", "1. empty input raises instead of returning 0", gate=True)
        through_a_correction("T02", "1. empty input crashes the parser")
        scorecard = self.cli("models").stdout
        self.assertIn("acme/coder-1: 2 task(s), 0/2 first-pass, 2.0 rounds to accept, "
                      "rejects: decision 2, gate 1, verification 2", scorecard)
        self.assertIn("review due: acme/coder-1 has 5 new reject case(s)", scorecard)
        self.assertIn("run `docket models --review acme/coder-1`",
                      self.cli("feedback", "--digest").stdout)
        packet = self.cli("models", "--review", "acme/coder-1").stdout
        self.assertIn("## 5 reject case(s) since the start", packet)
        self.assertIn("empty input crashes the parser", packet)
        self.assertIn("### Submit refused by the gate (1)", packet)
        self.assertIn("profile: model-acme-coder-1", packet)
        self.assertIn("evidence_tasks: 2", packet)
        through = re.search(r"^reviewed_through: (\S+)$", packet, re.M).group(1)

        draft = Path(self._feedback_tmp.name) / "draft.md"
        draft.write_text("---\nprofile: acme\nmodels: acme/coder-1\nversion: 1\ncard: false\n"
                         f"reviewed_through: {through}\n---\n\n- Handle empty input first.\n")
        refused = self.cli("models", "--adopt", str(draft), ok=False)
        self.assertIn("profile must be model-<slug>", refused.stderr)
        draft.write_text(draft.read_text().replace("profile: acme", "profile: model-acme-coder-1"))
        adopted = self.cli("models", "--adopt", str(draft))
        self.assertIn("adopted model-acme-coder-1 version 1", adopted.stdout)
        self.assertTrue((Path(os.environ["DOCKET_MODEL_PROFILES"]) / "model-acme-coder-1.md")
                        .is_file())

        self.assign_simple_in(run, "ml", "T03")
        carried = self.cli("prompt", "ml", "T03", "--role", "implementor",
                           "--model", "acme/coder-1").stdout
        self.assertIn("[model-acme-coder-1]", carried)
        self.assertIn("Handle empty input first.", carried)
        other = self.cli("prompt", "ml", "T03", "--role", "implementor",
                         "--model", "acme/coder-2").stdout
        self.assertNotIn("Handle empty input first.", other)
        self.assertIn("## 0 reject case(s) since " + through,
                      self.cli("models", "--review", "acme/coder-1").stdout)
        self.assertIn("profile: model-acme-coder-1 v1", self.cli("models").stdout)
        log = [json.loads(line) for line in
               Path(os.environ["DOCKET_FEEDBACK_LOG"]).read_text().splitlines()]
        self.assertEqual(["model-acme-coder-1"],
                         [r["profile"] for r in log if r.get("origin") == "profile"])

    def test_runs_from_before_outcomes_can_be_imported_once(self) -> None:
        """Existing verdicts and findings backfill the log; a second import adds nothing."""
        run = self.init5_in("old", mode="quick")
        self.assign_simple_in(run, "old", "T01")
        self.cli("dispatch", "old", "T01", "--session", "w1", "--register")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace("requested_model: ", "requested_model: acme/old-1", 1))
        self.fill_task_report(run / "T01-report-01.mdx", files="- `src/t01.py:1` - done.")
        self.cli("submit", "old", "T01", "--as", "implementor")
        self.cli("verify", "old", "T01", "--result", "fail", "--as", "checker",
                 "--detail", "1. off by one")
        self.request_changes(run, "old", "T01", reviewer="checker")
        Path(os.environ["DOCKET_FEEDBACK_LOG"]).unlink()
        first = self.cli("models", "--import").stdout
        self.assertIn("imported 2 outcome case(s) from old", first)
        self.assertIn("imported 0 outcome case(s) from old", self.cli("models", "--import", "old").stdout)
        self.assertIn("acme/old-1: 1 task(s), no decided task yet, 0.0 rounds to accept, "
                      "rejects: decision 1, verification 1", self.cli("models").stdout)
        self.assertIn("off by one", self.cli("models", "--review", "acme/old-1").stdout)
        # A display label recorded elsewhere is the same model once aliased, in past cases too.
        log = Path(os.environ["DOCKET_FEEDBACK_LOG"])
        record = json.loads(log.read_text().splitlines()[0])
        log.write_text(log.read_text() + json.dumps({**record, "model": "Acme Old One",
                                                     "pointer": "elsewhere"}) + "\n")
        self.assertIn("Acme Old One: 1 task(s)", self.cli("models").stdout)
        aliased = self.cli("models", "--alias", "Acme Old One=acme/old-1")
        self.assertIn("counted as acme/old-1", aliased.stdout)
        board = self.cli("models").stdout
        self.assertNotIn("Acme Old One", board)
        self.assertIn("rejects: decision 1, verification 2", board)

    def test_a_model_keeps_one_profile_and_a_whole_number_version(self) -> None:
        """Regressions: a second profile for a model installed and never applied; `3.0` crashed."""
        draft = Path(self._feedback_tmp.name) / "draft.md"

        def profile(name: str, version: str) -> str:
            return (f"---\nprofile: {name}\nmodels: vendor/flash\nversion: {version}\n"
                    "card: false\nreviewed_through: 2026-09-01T00:00:00Z\n---\n\n- Rule.\n")

        draft.write_text(profile("model-flash", "3.0"))
        bad = self.cli("models", "--adopt", str(draft), ok=False)
        self.assertIn("version must be a whole number, got '3.0'", bad.stderr)
        self.assertNotIn("Traceback", bad.stderr)
        draft.write_text(profile("model-flash", "1"))
        self.cli("models", "--adopt", str(draft))
        draft.write_text(profile("model-aaa", "1"))
        shadow = self.cli("models", "--adopt", str(draft), ok=False)
        self.assertIn("vendor/flash already has the profile model-flash", shadow.stderr)
        draft.write_text(profile("model-flash", "2"))
        self.cli("models", "--adopt", str(draft))

    def test_the_feedback_digest_counts_an_aliased_model_as_one(self) -> None:
        """Regression: the digest read raw names, so two spellings each stayed under review."""
        log = Path(os.environ["DOCKET_FEEDBACK_LOG"])
        case = {"origin": "outcome", "kind": "verification", "result": "fail", "run": "r",
                "project": "p", "owner": "T01", "round": "1"}
        lines = [json.dumps({**case, "model": model, "at": f"2026-09-2{n}T00:00:00Z",
                             "pointer": f"v{n}"})
                 for n, model in enumerate(["Flash Display", "Flash Display",
                                            "vendor/flash", "vendor/flash"], 1)]
        log.write_text("\n".join(lines) + "\n")
        self.cli("models", "--alias", "Flash Display=vendor/flash")
        self.assertIn("review due: vendor/flash has 4 new reject case(s)", self.cli("models").stdout)
        digest = self.cli("feedback", "--digest").stdout
        self.assertIn("vendor/flash: 4 new reject case(s)", digest)
        self.assertNotIn("Flash Display", digest)

    def test_every_role_prompt_asks_for_feedback_and_it_accumulates(self) -> None:
        """Roles record what docket cost them; records reach the user-level log and digest."""
        log = Path(os.environ["DOCKET_FEEDBACK_LOG"])
        run = self.init5_in("fb", mode="quick")
        self.assign_simple_in(run, "fb", "T01")
        self.cli("dispatch", "fb", "T01", "--session", "w1", "--register")
        prompt = self.prompt_text_in("fb", "T01", "implementor")
        self.assertIn("`docket feedback fb --add --role implementor --task T01 --round 1", prompt)
        self.cli("feedback", "fb", "--add", "--role", "implementor", "--task", "T01",
                 "--round", "1", "--category", "instruction",
                 "--body", "had to look up the scope capsule format")
        self.cli("feedback", "fb", "--add", "--role", "checker", "--task", "T01",
                 "--round", "1", "--category", "review", "--body", "none")
        records = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(["implementor", "checker"], [r["role"] for r in records])
        dispatch = json.loads((run / ".dispatch" / "T01.json").read_text())
        self.assertEqual(dispatch["prompt_digest"], records[0]["prompt_digest"])
        self.assertEqual(str(self.root), records[0]["project"])
        # A gate refusal is observed mechanically, with no agent involved.
        refused = self.cli("submit", "fb", "T01", "--as", "implementor", ok=False)
        self.assertNotEqual(0, refused.returncode)
        machine = [json.loads(line) for line in log.read_text().splitlines()
                   if json.loads(line).get("origin") == "machine"]
        self.assertEqual("verification", machine[-1]["category"])
        self.assertIn("submit refused by the gate", machine[-1]["body"])
        digest = self.cli("feedback", "--digest").stdout
        self.assertIn("agent reports: 2 (1 said none)", digest)
        self.assertIn("implementor  1/1", digest)
        self.assertIn("checker      0/1", digest)
        self.assertIn("had to look up the scope capsule format", digest)
        # Off means off, and feedback never blocks the work it describes.
        off = self.cli_env({"DOCKET_FEEDBACK_LOG": "off"}, "feedback", "fb", "--add",
                           "--role", "implementor", "--body", "not logged")
        self.assertIn("recorded observation", off.stdout)
        self.assertNotIn("not logged", log.read_text())

    def test_first_auto_archive_creates_missing_parents_with_0700(self) -> None:
        """The first archive on a fresh machine creates its state chain at 0700."""
        run = self.init5()
        env, _transcript = self.fake_claude_session()
        self.cli_env(env, "arm", "demo", "--role", "orchestrator")
        fresh_log = Path(self._feedback_tmp.name) / "fresh" / "nested" / "feedback.jsonl"
        self.assertFalse(fresh_log.parent.exists())
        out = self.cli_env({**env, "DOCKET_FEEDBACK_LOG": str(fresh_log)},
                           "usage", "demo", "--archive")
        self.assertIn("archived 1 session(s)", out.stdout)
        base = fresh_log.parent / "sessions"
        self.assertTrue(base.is_dir())
        kept = list(base.rglob("transcript.jsonl.gz"))
        self.assertEqual(1, len(kept))
        target = kept[0].parent
        chain = [fresh_log.parent.parent, fresh_log.parent, base]
        probe = target
        while True:
            chain.append(probe)
            if probe == base:
                break
            probe = probe.parent
        for directory in chain:
            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode), str(directory))

    def test_log_feedback_flushes_before_releasing_lock(self) -> None:
        """log_feedback flushes its writes before releasing the lock."""
        src = cli_function_source("log_feedback")
        self.assertIn("fh.flush()", src)
        self.assertIn("os.fsync", src)
        self.assertLess(src.find("fh.flush()"), src.find("LOCK_UN"))
        self.assertLess(src.find("os.fsync"), src.find("LOCK_UN"))
        run = self.init5()
        self.cli("feedback", "demo", "--add", "--role", "implementor",
                 "--category", "other", "--body", "flush check log")
        self.assertIn("flush check log", Path(os.environ["DOCKET_FEEDBACK_LOG"]).read_text())

    def test_note_harness_session_flushes_before_releasing_lock(self) -> None:
        """note_harness_session flushes its writes before releasing the lock."""
        src = cli_function_source("note_harness_session")
        self.assertIn("fh.flush()", src)
        self.assertIn("os.fsync", src)
        self.assertLess(src.find("fh.flush()"), src.find("LOCK_UN"))
        self.assertLess(src.find("os.fsync"), src.find("LOCK_UN"))
        run = self.init5()
        env, _transcript = self.fake_claude_session()
        self.cli_env(env, "arm", "demo", "--role", "orchestrator")
        ledger = (run / ".harness-sessions.jsonl").read_text()
        self.assertIn(env["CLAUDE_CODE_SESSION_ID"], ledger)

    def test_feedback_add_locates_transcript_without_parsing_transcript(self) -> None:
        """feedback --add finds the transcript path without parsing the whole transcript."""
        src = cli_function_source("cmd_feedback")
        self.assertIn("session_transcript_path", src)
        self.assertNotIn("session_usage", src)
        run = self.init5()
        env, transcript = self.fake_claude_session()
        self.cli_env(env, "arm", "demo", "--role", "orchestrator")
        self.cli_env(env, "feedback", "demo", "--add", "--role", "orchestrator",
                     "--category", "other", "--body", "transcript lookup check")
        observation = next(run.rglob("F01.mdx")).read_text()
        self.assertIn(f"transcript: {transcript}", observation)

    def test_feedback_log_leaves_existing_parent_mode_alone(self) -> None:
        """An existing feedback log parent keeps its mode; only created dirs get 0700."""
        run = self.init5()
        existing = Path(self._feedback_tmp.name) / "existing-parent"
        existing.mkdir()
        os.chmod(existing, 0o755)
        log = existing / "feedback.jsonl"
        out = self.cli_env({"DOCKET_FEEDBACK_LOG": str(log)}, "feedback", "demo",
                           "--add", "--role", "implementor",
                           "--category", "other", "--body", "existing parent kept")
        self.assertIn("recorded observation", out.stdout)
        self.assertEqual(0o755, stat.S_IMODE(existing.stat().st_mode))
        self.assertIn("existing parent kept", log.read_text())


    def test_t05_health_rejects_unassigned_owners_without_creating_state(self) -> None:
        run = self.init("split", evidence_mode="documents-only")
        before = sorted(str(p.relative_to(run)) for p in run.rglob("*"))
        for args in (("--owner", "T99"),
                     ("--flag-stall", "T99", "--cause", "hung")):
            result = self.cli("health", "demo", *args, ok=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("no task 'T99'", result.stderr)
        self.assertEqual(before, sorted(str(p.relative_to(run)) for p in run.rglob("*")))
        self.cli("assign", "demo", "T01", "--goal", "one", "--criterion", "done",
                 "--file", "one.txt", "--verify", "true")
        self.assertIn("T01", self.cli("health", "demo", "--owner", "T01").stdout)
        self.assertIn("flagged stall T01-1", self.cli(
            "health", "demo", "--flag-stall", "T01", "--cause", "hung").stdout)

    def test_t05_stall_identity_is_open_cause_and_reopen_generation(self) -> None:
        run = self.init("split", evidence_mode="documents-only")
        self.cli("assign", "demo", "T01", "--goal", "one", "--criterion", "done",
                 "--file", "one.txt", "--verify", "true")
        self.cli("health", "demo", "--flag-stall", "T01", "--cause", "hung")
        first = run / ".incidents" / "T01-1.json"
        record = json.loads(first.read_text())
        record["detected_at_epoch"] = 1
        first.write_text(json.dumps(record))
        repeated = self.cli("health", "demo", "--flag-stall", "T01", "--cause", "hung")
        self.assertIn("already flagged: T01-1", repeated.stdout)
        self.assertEqual(1, len(list((run / ".incidents").glob("*.json"))))
        distinct = self.cli("health", "demo", "--flag-stall", "T01", "--cause", "quota")
        self.assertIn("flagged stall T01-2", distinct.stdout)
        reopens = run / ".reopens"
        reopens.mkdir()
        (reopens / "T01.json").write_text(json.dumps({"owner": "T01", "reopens": [
            {"transition": "test-reopen", "round": 0}]}))
        new_epoch = self.cli("health", "demo", "--flag-stall", "T01", "--cause", "hung")
        self.assertIn("flagged stall T01-3", new_epoch.stdout)
        self.assertEqual(2, json.loads((run / ".incidents" / "T01-3.json").read_text())["epoch"])

    def test_t05_doctor_names_each_wake_settings_location(self) -> None:
        home = self.root / "home"
        home.mkdir()
        project = self.root / ".claude" / "settings.json"
        local = self.root / ".claude" / "settings.local.json"
        user = home / ".claude" / "settings.json"
        codex = home / ".codex" / "hooks.json"
        claude_locations = ((project, ".claude/settings.json"),
                            (local, ".claude/settings.local.json"),
                            (user, "~/.claude/settings.json"))

        def configure(path: Path, *, claude: bool) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            hook = {"type": "command", "command": "docket/hooks/wake.sh"}
            if claude:
                hook["asyncRewake"] = True
            path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [hook]}]}}))

        for path, label in claude_locations:
            for other, _ in claude_locations:
                other.unlink(missing_ok=True)
            configure(path, claude=True)
            result = self.cli_env({"HOME": str(home)}, "doctor")
            lines = [line for line in result.stdout.splitlines()
                     if "Stop hook (Claude)" in line]
            self.assertEqual([f"  [  ok  ] Stop hook (Claude) in {label}"], lines)

        for path, _ in claude_locations:
            path.unlink(missing_ok=True)
        configure(codex, claude=False)
        result = self.cli_env({"HOME": str(home)}, "doctor")
        self.assertIn("[  ok  ] Stop hook (Codex) in ~/.codex/hooks.json", result.stdout)
        lines = [line for line in result.stdout.splitlines()
                 if "Stop hook (Claude)" in line]
        self.assertEqual(1, len(lines))
        self.assertIn("[ note ]", lines[0])
        for _, label in claude_locations:
            self.assertIn(label, lines[0])
        self.assertIn("docket help signalling", lines[0])

        project.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "other.sh", "asyncRewake": True}]}]}}))
        result = self.cli_env({"HOME": str(home)}, "doctor")
        self.assertIn("[ note ] Stop hook (Claude) absent in", result.stdout)
        configure(user, claude=True)
        result = self.cli_env({"HOME": str(home)}, "doctor")
        self.assertIn("[  ok  ] Stop hook (Claude) in ~/.claude/settings.json", result.stdout)
        self.assertFalse(any("[ note ]" in line for line in result.stdout.splitlines()
                             if "Stop hook (Claude)" in line))

        configure(project, claude=True)
        configure(local, claude=True)
        result = self.cli_env({"HOME": str(home)}, "doctor")
        lines = [line for line in result.stdout.splitlines()
                 if "Stop hook (Claude)" in line]
        self.assertEqual(1, len(lines))
        for _, label in claude_locations:
            self.assertIn(label, lines[0])
        self.assertIn("[  ok  ]", lines[0])

    def test_t05_doctor_probe_bounds_an_unresponsive_process_tree(self) -> None:
        fake = self.root / "bin"
        fake.mkdir()
        herdr = fake / "herdr"
        herdr.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(20)\n")
        herdr.chmod(0o755)
        env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, str(DOCKET), "doctor"],
                                    cwd=self.root, env=env, text=True,
                                    capture_output=True, timeout=6)
        except subprocess.TimeoutExpired:
            self.fail("doctor did not finish within six seconds")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLess(time.monotonic() - started, 6)
        self.assertIn("mode: manual", result.stdout)

    def test_t05_suite_counts_only_literal_failures_and_errors(self) -> None:
        run = self.init("split", evidence_mode="documents-only")
        result = self.cli("suite", "demo", "--qualify", "--command",
                          "printf 'Ran 5 tests in 0.1s\\n\\nFAILED (expected failures=2, failures=1, errors=1)\\n' >&2; exit 1")
        self.assertIn("2 failures", result.stdout)
        artifact = sorted((run / ".suite").glob("qual-*.json"))[-1]
        self.assertEqual(2, json.loads(artifact.read_text())["failures"])
        passed = self.cli("suite", "demo", "--qualify", "--command",
                          "printf 'Ran 2 tests in 0.1s\\n\\nOK (expected failures=2)\\n' >&2")
        self.assertIn("0 failures", passed.stdout)

    def test_t05_probe_executes_the_advertised_flag(self) -> None:
        self.init("split", evidence_mode="documents-only")
        fake = self.root / "bin"
        fake.mkdir()
        herdr = fake / "herdr"
        herdr.write_text("#!/bin/sh\n"
                         "if [ \"$1\" = --version ]; then echo 'herdr test'; exit 0; fi\n"
                         "if [ \"$*\" = 'agent prompt --help' ]; then echo '--queue'; exit 0; fi\n"
                         "if [ \"$*\" = 'agent prompt --queue --help' ]; then echo '--queue'; exit 0; fi\n"
                         "exit 2\n")
        herdr.chmod(0o755)
        env = {"PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}"}
        queued = self.cli_env(env, "delivery", "demo", "--role", "orchestrator",
                              "--probe")
        self.assertIn("boundary: verified-queue", queued.stdout)
        herdr.write_text(herdr.read_text().replace("--queue", "--send-if-idle"))
        idle = self.cli_env(env, "delivery", "demo", "--role", "orchestrator",
                            "--probe")
        self.assertIn("boundary: verified-queue", idle.stdout)


    def test_t06_status_lists_artifact_and_journal_recovery_with_commands(self) -> None:
        run = self.init(evidence_mode="documents-only")
        self.assertNotIn("unfinished decision transitions", self.cli("status", "demo").stdout)
        (run / "T01-report-01.mdx").write_text(
            "---\nrun: demo\nowner: T01\nround: 1\nstatus: changes-requested\n---\n\nreport\n"
        )
        one = self.cli("status", "demo").stdout
        self.assertIn("T01 round 1 changes-requested", one)
        self.assertIn("docket decide demo T01 --changes", one)
        for owner, status in (("T01", "changes-requested"), ("T02", "submitted"),
                              ("T03", "submitted"), ("T04", "submitted"),
                              ("T05", "waived"), ("orch", "submitted")):
            (run / f"{owner}-report-01.mdx").write_text(
                f"---\nrun: demo\nowner: {owner}\nround: 1\nstatus: {status}\n---\n\nreport\n"
            )
        (run / "orch-decision-01.mdx").write_text(
            "---\napplied: yes\nverdict: approved\ntransition: txn:orch\n---\n"
        )
        transitions = run / ".transitions"
        transitions.mkdir()
        for owner, verdict in (("T02", "approved"), ("T03", "approved"),
                               ("T04", "waived"), ("T05", "reopen-waived")):
            (transitions / f"{owner}.json").write_text(json.dumps({
                "state": "in-progress", "round": 1, "verdict": verdict,
                "transition": f"txn:{owner}", "completed": [],
            }))

        # The old artifact path has no journal, while the journal path was already
        # shown. Both must carry a usable finishing command.
        shown = self.cli("status", "demo").stdout
        self.assertIn("T01 round 1 changes-requested", shown)
        self.assertIn("docket decide demo T01 --changes", shown)
        self.assertIn("T02 round 1 approved", shown)
        self.assertIn("docket decide demo T02 --approve", shown)
        self.assertIn("T03 round 1 approved", shown)
        self.assertIn("docket decide demo T04 --waive", shown)
        self.assertIn("docket decide demo T05 --reopen", shown)
        self.assertIn("docket decide demo orch --approve", shown)

        # A live owner lock means the transition is still being written. Inspection
        # must leave the lock and its journal alone and must not offer a retry.
        import fcntl
        lock_path = run / ".locks" / "T03.lock"
        lock_path.parent.mkdir()
        with lock_path.open("a+") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            try:
                locked = self.cli("status", "demo").stdout
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        self.assertIn("T01 round 1 changes-requested", locked)
        self.assertIn("T02 round 1 approved", locked)
        self.assertNotIn("T03 round 1 approved", locked)
        self.assertIn("T03 round 1 approved", self.cli("status", "demo").stdout)

    def test_t06_redeclare_preserves_old_bytes_on_invalid_spec(self) -> None:
        self.repo()
        run = self.init(evidence_mode="git")
        declaration = run / ".snapshots" / "roots.json"
        old = declaration.read_bytes()
        refused = self.cli("roots", "demo", "--redeclare", "new=.",
                           "missing=does-not-exist", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertEqual(old, declaration.read_bytes())
        self.assertIn("root", self.cli("roots", "demo").stdout)
        self.assertFalse(list(declaration.parent.glob(".roots.json.*.tmp")))

        # A valid replacement was already supported and remains supported.
        self.cli("roots", "demo", "--redeclare", "new=.")
        self.assertEqual(["new"], [item["alias"] for item in
                         json.loads(declaration.read_text())["roots"]])

    def test_t06_argparse_rejects_invalid_owners_and_dependency_ids(self) -> None:
        run = self.init(evidence_mode="documents-only")
        required = {
            "set-model": ("--actual", "model"), "verify": ("--result", "pass"),
            "prompt": ("--role", "implementor"), "escalation": ("--grant", "1"),
            "dispatch": ("--session", "worker"), "resume": ("--session", "worker"),
            "switch-model": ("--model", "model"), "decide": ("--approve",),
        }
        for command in ("assign", "validate-task", "scope", "handoff", "set-model",
                        "submit", "verify", "prompt", "escalation", "dispatch",
                        "resume", "switch-model", "propose-amendment", "decide",
                        "diff", "bundle", "depend", "preflight"):
            with self.subTest(command=command):
                result = self.cli(command, "demo", "T01/../bad", *required.get(command, ()),
                                  ok=False)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("argument target" if command == "diff" else "argument owner",
                              result.stderr)
        for command, args in (("route", ("--kind", "blocked", "--owner", "bad")),
                              ("health", ("--owner", "bad")),
                              ("health", ("--flag-stall", "bad"))):
            with self.subTest(option=args):
                result = self.cli(command, "demo", *args, ok=False)
                self.assertEqual(2, result.returncode, result.stderr)
        for arg in ("T01,bad", "T01,orch", "T01,", "bad"):
            with self.subTest(depends_on=arg):
                result = self.cli("assign", "demo", "T02", "--depends-on", arg,
                                  ok=False)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("--depends-on", result.stderr)
        for arg in ("bad:T01", "T02:bad", "T02:orch"):
            with self.subTest(batch_depends_on=arg):
                result = self.cli("batch", "demo", "--depends-on", arg, ok=False)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("--depends-on", result.stderr)
        for command, args in (("depend", ("demo", "T01", "--on", "bad")),
                              ("batch", ("demo", "--members", "T01,bad")),
                              ("review-packet", ("demo", "--correction", "bad")),
                              ("feedback", ("demo", "--task", "bad"))):
            result = self.cli(command, *args, ok=False)
            self.assertEqual(2, result.returncode, result.stderr)
        self.assertFalse((run / "T02-task.mdx").exists())

        # Valid task IDs and a comma separated dependency worked before this fix.
        self.cli("assign", "demo", "T01", "--file", "src/a.py", "--verify", "true")
        self.cli("assign", "demo", "T03", "--file", "src/c.py", "--verify", "true")
        self.cli("assign", "demo", "T02", "--depends-on", "T01,T03", "--file", "src/b.py",
                 "--verify", "true")
        self.assertEqual("T01,T03", parse_meta(run / "T02-task.mdx")["depends_on"])
        self.assertEqual(0, self.cli("diff", "demo", "orch").returncode)
        self.assertIn("diff coverage unavailable", self.cli("diff", "demo", "run").stdout)


class CLILauncher(unittest.TestCase):
    """`bin/docket` launches the CLI module, from anywhere, without recompiling it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.skill = DOCKET.resolve().parents[1]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def env(self, **extra: str) -> dict[str, str]:
        """The caller's environment with no bytecode settings of its own."""
        env = {k: v for k, v in os.environ.items() if k not in (
            "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE", "XDG_CACHE_HOME",
            "DOCKET_CONTRACTS_ROOT", "DOCKET_PROFILES_ROOT", *HARNESS_ENV)}
        env["XDG_CACHE_HOME"] = str(self.root / "cache")
        env.update(extra)
        return env

    def copy_skill(self, into: Path) -> Path:
        """A copy of the skill under test, without caches or tests; returns its launcher."""
        shutil.copytree(self.skill, into,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
        return into / "bin" / "docket"

    def run_cli(self, launcher: Path, *args: str, flags: tuple[str, ...] = (),
                cwd: Path | None = None, env: dict[str, str] | None = None
                ) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, *flags, str(launcher), *args],
                              cwd=str(cwd or self.root), env=env or self.env(),
                              capture_output=True, text=True, timeout=120)

    def module(self):  # type: ignore[no-untyped-def]
        return load_cli("docket_launcher_mod")

    def test_bin_docket_is_a_short_launcher_over_the_module(self) -> None:
        """The executable imports the `docket_cli` package and calls its main; the CLI lives there."""
        launcher = DOCKET.read_text()
        self.assertLess(len(launcher.splitlines()), 80)
        self.assertIn('"docket_cli"', launcher)
        self.assertIn("module.main()", launcher)
        self.assertTrue(launcher.startswith("#!/usr/bin/env -S uv run --script\n"))
        self.assertEqual(self.skill / "docket_cli", CLI_PACKAGE)
        self.assertFalse((self.skill / "docket_cli.py").exists(), "the single module is gone")
        self.assertIn("\ndef main() -> None:\n", (CLI_PACKAGE / "cli.py").read_text())

    def test_the_package_modules_import_in_one_order_from_the_stdlib_only(self) -> None:
        """`MODULES` names every module, and each imports only earlier ones and the stdlib.

        Every name is defined in one module, so the package is still the one namespace
        the CLI was, and the launcher imports all of it.
        """
        import ast
        self.assertTrue(CLI_PACKAGE.is_dir(), f"no CLI package at {CLI_PACKAGE}")
        init = ast.parse((CLI_PACKAGE / "__init__.py").read_text())
        listed = [node.value for node in init.body if isinstance(node, ast.Assign)
                  and [getattr(target, "id", "") for target in node.targets] == ["MODULES"]]
        self.assertEqual(1, len(listed), "__init__.py declares MODULES once")
        order = [element.id for element in listed[0].elts]
        self.assertEqual(sorted(path.stem for path in CLI_PACKAGE.glob("*.py")
                                if path.name != "__init__.py"), sorted(order))
        self.assertEqual("cli", order[-1], "main sits above every module it dispatches to")
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        defined: dict[str, str] = {}
        for index, name in enumerate(order):
            tree = ast.parse((CLI_PACKAGE / f"{name}.py").read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    self.assertIn(node.module, order[:index], f"{name} imports {node.module}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertIn(str(node.module).split(".")[0], allowed, f"{name}: {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed, f"{name}: {alias.name}")
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = [leaf.id for target in targets for leaf in ast.walk(target)
                             if isinstance(leaf, ast.Name)]
                else:
                    continue
                for defined_name in names:
                    self.assertNotIn(defined_name, defined,
                                     f"{defined_name} is defined in {defined.get(defined_name)} and {name}")
                    defined[defined_name] = name
        out = self.run_cli(DOCKET, "--help", flags=("-X", "importtime"))
        self.assertEqual(0, out.returncode, out.stderr)
        imported = re.findall(r"\|\s+docket_cli\.(\w+)$", out.stderr, re.MULTILINE)
        self.assertEqual(sorted(order), sorted(imported), "the launcher imports every module")

    def test_every_self_located_path_resolves_as_before(self) -> None:
        """References, contracts, profiles, obligations, and re-invocation stay put."""
        import types
        saved = {k: os.environ.pop(k) for k in ("DOCKET_CONTRACTS_ROOT", "DOCKET_PROFILES_ROOT")
                 if k in os.environ}
        self.addCleanup(os.environ.update, saved)
        mod = self.module()
        references = self.skill / "references"
        self.assertEqual(references / "contracts", mod.contracts_dir())
        self.assertEqual(references / "model-profiles", mod.profiles_dir())
        self.assertEqual(references / "verification-obligations.md",
                         mod.verification_obligations_path())
        home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.root)
        try:
            self.assertEqual(f"not installed (using repo CLI at {DOCKET.resolve().parent})",
                             mod.installation_state())
        finally:
            if home is None:
                os.environ.pop("HOME")
            else:
                os.environ["HOME"] = home
        calls: list[list[str]] = []
        mod.subprocess = types.SimpleNamespace(
            run=lambda argv, **_: calls.append(argv))
        mod.root = lambda: self.root
        mod.docket_subprocess("--help")
        self.assertEqual([[sys.executable, str(DOCKET.resolve()), "--help"]], calls)
        playbook = self.run_cli(DOCKET, "help", "implementor")
        self.assertEqual(0, playbook.returncode, playbook.stderr)
        self.assertIn((references / "implementor.md").read_text().splitlines()[0],
                      playbook.stdout)

    def test_the_cli_runs_from_any_directory_and_through_a_symlink(self) -> None:
        """A relative or symlinked launcher resolves the same skill from any cwd."""
        direct = self.run_cli(DOCKET, "help", "implementor")
        self.assertEqual(0, direct.returncode, direct.stderr)
        link = self.root / "path" / "docket"
        link.parent.mkdir()
        link.symlink_to(DOCKET.resolve())
        for cwd in (Path("/"), self.root, self.skill / "references"):
            with self.subTest(cwd=str(cwd)):
                linked = self.run_cli(link, "help", "implementor", cwd=cwd)
                self.assertEqual(0, linked.returncode, linked.stderr)
                self.assertEqual(direct.stdout, linked.stdout)
        relative = subprocess.run(
            [sys.executable, os.path.relpath(DOCKET.resolve(), self.skill.parent), "--help"],
            cwd=str(self.skill.parent), env=self.env(), capture_output=True, text=True,
            timeout=120)
        self.assertEqual(0, relative.returncode, relative.stderr)
        self.assertIn("usage: docket", relative.stdout)

    def test_the_installed_layout_reads_its_own_files(self) -> None:
        """A copy at ~/.agents/skills/docket, reached by the README symlink, reads itself."""
        home = self.root / "home"
        launcher = self.copy_skill(home / ".agents" / "skills" / "docket")
        playbook = launcher.parents[1] / "references" / "implementor.md"
        playbook.write_text(playbook.read_text() + "\nInstalled-copy marker line.\n")
        link = home / ".local" / "bin" / "docket"
        link.parent.mkdir(parents=True)
        link.symlink_to(launcher)
        out = self.run_cli(link, "help", "implementor", env=self.env(HOME=str(home)))
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("Installed-copy marker line.", out.stdout)
        self.assertNotIn("Installed-copy marker line.",
                         self.run_cli(DOCKET, "help", "implementor").stdout)

    def test_t07_prompt_quotes_spaced_launcher_without_path_entry(self) -> None:
        launcher = self.copy_skill(self.root / "skill copy" / "docket")
        created = self.run_cli(launcher, "init", "spaced", "--mode", "standard",
                               "--evidence-mode", "documents-only")
        self.assertEqual(0, created.returncode, created.stderr)
        assigned = self.run_cli(
            launcher, "assign", "spaced", "T01", "--harness", "codex",
            "--file", "src/a.py", "--verify", "true", "--title", "Copy path",
            "--goal", "Use the copied CLI", "--criterion", "The CLI runs")
        self.assertEqual(0, assigned.returncode, assigned.stderr)
        prompt = self.run_cli(launcher, "prompt", "spaced", "T01", "--role",
                              "implementor", env=self.env(PATH=""))
        self.assertEqual(0, prompt.returncode, prompt.stderr)
        expected_launcher = str(launcher.resolve())
        quoted_launcher = shlex.quote(expected_launcher)
        self.assertNotEqual(expected_launcher, quoted_launcher)
        self.assertIn(f"`{quoted_launcher} scope spaced T01 --submit`", prompt.stdout)
        command = re.search(r"`([^`]* scope spaced T01 --submit)`", prompt.stdout)
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual([expected_launcher, "scope", "spaced", "T01", "--submit"],
                         shlex.split(command.group(1)))

    def test_the_uv_shebang_runs_the_launcher(self) -> None:
        """Executing `bin/docket` directly goes through its `uv run --script` shebang."""
        if shutil.which("uv") is None:
            self.skipTest("uv is not installed")
        out = subprocess.run([str(DOCKET.resolve()), "help", "implementor"], cwd=str(self.root),
                             env=self.env(), capture_output=True, text=True, timeout=300)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(self.run_cli(DOCKET, "help", "implementor").stdout, out.stdout)

    def test_a_repeated_call_compiles_nothing_and_caches_outside_the_skill(self) -> None:
        """The second call loads hash-checked bytecode from the user cache, not the checkout."""
        launcher = self.copy_skill(self.root / "tree" / "skills" / "docket")
        package = launcher.parents[1] / "docket_cli"
        cache = self.root / "cache" / "docket" / "pycache"
        pycs = [cache / str(package.resolve()).lstrip("/") /
                f"{source.stem}.{sys.implementation.cache_tag}.pyc"
                for source in sorted(package.glob("*.py"))]
        self.assertIn("__init__", [pyc.name.split(".")[0] for pyc in pycs])
        first = self.run_cli(launcher, "--help")
        self.assertEqual(0, first.returncode, first.stderr)
        for pyc in pycs:
            self.assertTrue(pyc.is_file(), f"no bytecode at {pyc}")
            self.assertEqual(0b11, int.from_bytes(pyc.read_bytes()[4:8], "little"),
                             "bytecode must be checked against the source hash")
        again = self.run_cli(launcher, "--help", flags=("-v",))
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertEqual(first.stdout, again.stdout)
        for pyc in pycs:
            self.assertIn(f"code object from {str(pyc)!r}", again.stderr)
        self.assertNotIn("docket_cli", "\n".join(
            line for line in again.stderr.splitlines() if line.startswith("# wrote")))
        self.assertEqual([], sorted(str(p) for p in (self.root / "tree").rglob("__pycache__")))
        prefix = self.root / "prefix"
        chosen = self.run_cli(launcher, "--help", env=self.env(PYTHONPYCACHEPREFIX=str(prefix)))
        self.assertEqual(0, chosen.returncode, chosen.stderr)
        for pyc in pycs:
            self.assertTrue((prefix / pyc.relative_to(cache)).is_file())

    def test_an_edit_in_the_same_second_never_runs_stale_bytecode(self) -> None:
        """Same size, same mtime, new text: the hash check still sees the edit."""
        launcher = self.copy_skill(self.root / "tree" / "skills" / "docket")
        module = launcher.parents[1] / "docket_cli" / "cli.py"
        self.assertIn("run the suite once", module.read_text())
        self.assertIn("run the suite once", self.run_cli(launcher, "--help").stdout)
        stat_before = module.stat()
        text = module.read_text()
        module.write_text(text.replace("run the suite once", "run the suite ONCE", 1))
        os.utime(module, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
        self.assertEqual(stat_before.st_size, module.stat().st_size)
        edited = self.run_cli(launcher, "--help")
        self.assertEqual(0, edited.returncode, edited.stderr)
        self.assertIn("run the suite ONCE", edited.stdout)

    def test_pycache_stays_ignored_in_this_repository(self) -> None:
        """The repository's .gitignore keeps `__pycache__/` out of version control."""
        ignore = DOCKET.resolve().parents[3] / ".gitignore"
        self.assertIn("__pycache__/", ignore.read_text().splitlines())
