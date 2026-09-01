from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DOCKET = Path(__file__).parents[1] / "bin" / "docket"
HOOK = Path(__file__).parents[1] / "hooks" / "wake.sh"


class DocketCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(DOCKET), *args], cwd=self.root, text=True, capture_output=True
        )
        if ok and result.returncode:
            self.fail(f"command failed: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def init(self, topology: str = "split") -> Path:
        self.cli("init", "demo", "--harness", "claude", "--topology", topology)
        return self.root / ".docket" / "runs" / "demo"

    def assign(
        self, owner: str = "T01", *, executor: str = "implementor", claim_scope: bool = True
    ) -> Path:
        self.cli(
            "assign", "demo", owner, "--complexity", "high", "--executor", executor,
            "--harness", "opencode", "--file", "src/a.py", "--verify", f'printf "{owner} ok\\n"',
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
    def fill_task_report(path: Path, criterion: str = "The feature works.", *, blocked: bool = False) -> None:
        text = path.read_text()
        text = text.replace(
            "<!-- TODO: what you actually did, 2-4 sentences. No plans, only past tense. -->",
            "Implemented the feature and verified its behavior.",
        )
        text = text.replace(
            '<!-- TODO: one bullet per change as `path/to/file.py:120` plus a short note. Write "none" if nothing changed. -->',
            "- `src/a.py:1` - implemented the feature.",
        )
        text = text.replace("- [ ] <!-- TODO -->", f"- [{' ' if blocked else 'x'}] {criterion}")
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
            "<!-- TODO: one row per planned task, including owner, terminal state, and verification. -->": "| task | outcome | verification |\n| --- | --- | --- |\n| T01 | approved | passed |",
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
        self.assertIn("T01 submitted", orchestrator_watch.stderr)

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

    def test_assignment_snapshot_rejects_new_out_of_scope_changes(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "docket@example.test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Docket Test"], cwd=self.root, check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("allowed = 1\n")
        (self.root / "outside.py").write_text("outside = 1\n")
        subprocess.run(["git", "add", "src/a.py", "outside.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

        self.init()
        run = self.assign()
        self.fill_task(run / "T01-task.mdx")
        self.fill_task_report(run / "T01-report-01.mdx")
        (self.root / "src" / "a.py").write_text("allowed = 2\n")
        (self.root / "outside.py").write_text("outside = 2\n")

        diff = self.cli("diff", "demo", "T01").stdout
        self.assertIn("[in scope] src/a.py", diff)
        self.assertIn("[OUTSIDE SCOPE] outside.py", diff)
        rejected = self.cli("submit", "demo", "T01", ok=False)
        self.assertIn("outside the task scope: outside.py", rejected.stderr)

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
        self.assertIn("T01 submitted", orchestrator.stderr)

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
        self.assertIn("src/a.py overlaps T01:src/a.py", collision.stderr)
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


if __name__ == "__main__":
    unittest.main()
