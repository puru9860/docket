"""Explicit task-method decisions and shared help, without inference or new gates."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import unittest
from pathlib import Path

import test_docket as fixtures


CRITERION = "Approved behavior is preserved."


def selection(method: str) -> str:
    return (f"Work type: {method}. Follow `docket help task-methods` ({method}) within this contract; "
            "it does not change acceptance or authorize behavior changes.")


class TaskMethods(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.cli = self.f.cli
        self.skill = fixtures.DOCKET.resolve().parents[1]
        self.mod = fixtures.load_cli("task_methods_tests")

    def assign(self, decisions: tuple[str, ...] = ()) -> Path:
        self.cli("init", "demo", "--evidence-mode", "documents-only")
        self.cli("assign", "demo", "T01", "--as", "planner", "--title", "Preserve behavior",
                 "--goal", "Keep the approved behavior.", "--criterion", CRITERION,
                 "--no-default-criteria", "--file", "src/a.py", "--verify", "true",
                 *(arg for text in decisions for arg in ("--decision", text)))
        return self.f.root / ".docket/runs/demo"

    def submit(self, run: Path, close: bool = True):
        if close:
            self.cli("batch", "demo", "--create", "M1", "--members", "T01", "--milestone", "--command", "true", "--as", "planner")
            self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "demo", "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx", criterion=CRITERION)
        self.cli("submit", "demo", "T01", "--as", "implementor")

    def path_with_docket(self) -> str:
        directory = self.f.root / "on-path"
        directory.mkdir()
        (directory / "docket").symlink_to(self.mod.LAUNCHER)
        return str(directory) + os.pathsep + os.environ.get("PATH", "")

    @staticmethod
    def state_bytes(run: Path) -> dict[str, bytes]:
        return {str(p.relative_to(run)): p.read_bytes() for p in run.rglob("*")
                if p.is_file() and ".prompts" not in p.relative_to(run).parts}

    def prompts(self, run: Path, close: bool = True, env: dict[str, str] | None = None) -> dict[str, str]:
        result = {}
        for role in ("implementor", "reviewer"):
            if role == "reviewer":
                self.submit(run, close)
            result[role] = self.f.cli_env(env or {}, "prompt", "demo", "T01", "--role", role, "--max-tokens", "0").stdout
            record = json.loads(sorted((run / ".prompts").glob(f"T01-{role}-*.json"))[-1].read_text())
            self.assertEqual(0, record["guidance_tokens"])
            self.assertEqual([], record["selected_guidance"])
            self.assertNotIn("# Selected guidance", result[role])
        return result

    def test_explicit_assign_decision_reaches_both_roles_verbatim_at_zero_optional_budget(self):
        decisions = tuple(selection(name) for name in ("refactor-equivalence", "performance"))
        run = self.assign(decisions)
        for role, text in self.prompts(run, env={"PATH": self.path_with_docket()}).items():
            with self.subTest(role=role):
                self.assertIn("existing decisions:", text)
                for decision in decisions:
                    self.assertIn(decision, text)
                self.assertIn(CRITERION, text)
                self.assertNotIn("# Focused task methods", text, "full methods are read on demand")

    def test_json_plan_decision_remains_mandatory_without_a_new_field(self):
        decision = selection("performance")
        plan = {
            "run": {"title": "Preserve behavior", "objective": CRITERION, "evidence_mode": "documents-only"},
            "tasks": [{"owner": "T01", "title": "Feature", "goal": CRITERION, "criterion": [CRITERION],
                       "no_default_criteria": True, "file": ["src/a.py"], "verify": "true", "decision": [decision]}],
            "milestones": [{"id": "M1", "members": ["T01"], "command": "true"}],
        }
        file = self.f.root / "plan.json"
        file.write_text(json.dumps(plan))
        self.cli("plan", "apply", "demo", "--file", str(file))
        run = self.f.root / ".docket/runs/demo"
        for text in self.prompts(run, close=False, env={"PATH": self.path_with_docket()}).values():
            self.assertIn(decision, text)
        meta, _ = self.mod.parse((run / "T01-task.mdx").read_text())
        self.assertNotIn("work_type", meta)
        self.assertNotIn("method", meta)

    def test_unknown_method_and_ordinary_free_text_stay_unrestricted_decisions(self):
        decisions = ("Work type: future-method. This is ordinary free text, with no automatic meaning.",
                     'Preserve # literal "quoted" intent; consult the planner for ambiguity.')
        run = self.assign(decisions)
        for text in self.prompts(run).values():
            for decision in decisions:
                self.assertIn(decision, text)
        self.cli("batch", "demo", "--verify", "M1", "--as", "implementor")
        self.cli("batch", "demo", "--approve", "M1", "--as", "reviewer", "--reason", "The existing criterion holds.")
        self.assertIn("status: approved", (run / "T01-report-01.mdx").read_text())

    def test_shared_help_prints_the_reference_without_reading_a_role_contract(self):
        empty = self.f.root / "empty-contracts"
        empty.mkdir()
        output = self.f.cli_env({"DOCKET_CONTRACTS_ROOT": str(empty)}, "help", "task-methods").stdout
        reference = self.skill / "references/task-methods.md"
        self.assertEqual(self.mod.unwrap_markdown(reference.read_text().strip()) + "\n", output)
        for phrase in ("## refactor-equivalence", "## performance", "documents-only",
                       "baseline", "patch", "inconclusive", "ballpark", "Overlapping ranges",
                       "Implementor", "Reviewer"):
            self.assertIn(phrase, output)
        self.assertNotIn("# Role:", output)

    def test_shared_help_refuses_active_role_options_before_looking_up_a_run(self):
        for topic in ("task-methods", "discussion", "signalling"):
            with self.subTest(topic=topic):
                result = self.cli("help", topic, "--run", "missing-run", "--owner", "T01", ok=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(f"{topic} help is shared across presets", result.stderr)
                self.assertNotIn("missing role contract", result.stderr)
                self.assertFalse((self.f.root / ".docket").exists())
        for args in (("--run", "missing-run"), ("--owner", "T01")):
            result = self.cli("help", "task-methods", *args, ok=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("active guidance needs both", result.stderr)

    def test_ordinary_and_active_role_help_keep_their_behavior(self):
        run = self.assign()
        ordinary = self.cli("help", "implementor").stdout
        self.assertIn("# Role: implementor", ordinary)
        self.assertIn("Claim scope before editing", ordinary)
        prompt = self.cli("prompt", "demo", "T01", "--role", "implementor").stdout
        records = sorted((run / ".prompts").glob("*.json"))
        active = self.cli("help", "implementor", "--run", "demo", "--owner", "T01").stdout
        self.assertEqual(prompt, active)
        self.assertEqual(records, sorted((run / ".prompts").glob("*.json")), "help renders without publishing a prompt record")

    def test_unselected_tasks_receive_no_method_at_zero_budget_and_rendering_preserves_state(self):
        run = self.assign()
        for role in ("implementor", "reviewer"):
            if role == "reviewer":
                self.submit(run)
            before_state = self.state_bytes(run)
            args = ("prompt", "demo", "T01", "--role", role, "--max-tokens", "0")
            text = self.cli(*args).stdout
            for marker in ("work type:", "task-methods", "refactor-equivalence", "performance", "focused task methods",
                           "characterization", "before structural edits", "state the metric, units", "interleave repeated baseline/patch"):
                self.assertNotIn(marker, text.lower(), role)
            record = json.loads(sorted((run / ".prompts").glob(f"T01-{role}-*.json"))[-1].read_text())
            self.assertEqual(0, record["guidance_tokens"])
            self.assertEqual([], record["selected_guidance"])
            self.assertNotIn("# Selected guidance", text)
            self.assertIn(CRITERION, text)
            self.assertEqual(before_state, self.state_bytes(run))

    def test_pointed_help_uses_the_source_launcher_when_docket_is_absent_from_path(self):
        decision = selection("refactor-equivalence")
        run = self.assign((decision,))
        empty = self.f.root / "empty-path"
        empty.mkdir()
        self.assertIsNone(shutil.which("docket", path=str(empty)))
        pointed = f"`{shlex.quote(str(self.mod.LAUNCHER))} help task-methods`"
        for role in ("implementor", "reviewer"):
            with self.subTest(role=role):
                if role == "reviewer":
                    self.submit(run)
                before = self.state_bytes(run)
                text = self.f.cli_env({"PATH": str(empty)}, "prompt", "demo", "T01",
                                      "--role", role, "--max-tokens", "0").stdout
                self.assertIn(pointed, text)
                self.assertIn(decision.replace("`docket help task-methods`", pointed), text)
                self.assertNotIn("`docket help task-methods`", text)
                self.assertIn(decision, (run / "T01-task.mdx").read_text())
                self.assertEqual(before, self.state_bytes(run))
        ledger = json.loads((run / ".bundles/T01/rounds.json").read_text())
        bundle = run / ".bundles/T01" / ledger["entries"][-1]["dir"]
        manifest = json.loads((bundle / "bundle.json").read_text())
        frozen = (bundle / manifest["contract"]["file"]).read_text()
        self.assertIn(decision, frozen)
        self.assertNotIn(str(self.mod.LAUNCHER), frozen)


if __name__ == "__main__":
    unittest.main()
