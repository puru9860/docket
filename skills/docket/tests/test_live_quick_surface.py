"""Defaults and evidence duties for the live quick surface."""
from __future__ import annotations

import unittest

import test_docket as fixtures


class LiveQuickSurface(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.cli = self.f.cli

    def tearDown(self):
        self.f.tearDown()

    def prepare(self, name="quick", harness=None):
        self.cli("init", name, "--evidence-mode", "documents-only")
        args = ["assign", name, "T01", "--file", "src/t01.py", "--verify", "true",
                "--as", "planner"]
        if harness:
            args += ["--harness", harness]
        self.cli(*args)
        run = self.f.root / ".docket/runs" / name
        self.f.fill_task(run / "T01-task.mdx")
        self.cli("batch", name, "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner",
                 "--implementor-harness", harness or "claude",
                 "--assignment-reason", "Exercise the selected adapter.")
        self.cli("batch", name, "--close", "M1", "--as", "planner")
        return run

    def test_default_worker_is_claude_and_opencode_is_explicit(self):
        for name, harness in (("default", None), ("opt-in", "opencode"), ("cx", "codex")):
            run = self.prepare(name, harness)
            for path in (run / "T01-task.mdx", run / "T01-report-01.mdx"):
                self.assertEqual(harness or "claude", fixtures.parse_meta(path)["harness"])

    def test_new_quick_context_is_shared_and_dispatch_works_in_session(self):
        run = self.prepare()
        self.assertEqual("shared", fixtures.parse_meta(run / "plan.mdx")["round_context"])

    def test_default_quick_dispatch_and_correction_stay_in_session(self):
        run = self.prepare(harness="claude")
        out = self.cli("dispatch", "quick", "T01", "--session", "impl", "--register").stdout
        prompt = next((run / ".prompts").glob("T01-*.txt")).read_text()
        self.assertNotIn("subagent", out + prompt)
        self.assertIn("docket batch quick --verify M1 --as implementor", prompt)
        self.assertIn("docket watch quick --role implementor", prompt)
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "quick", "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx", files="- `src/t01.py:1` - done.")
        self.cli("submit", "quick", "T01", "--as", "implementor")
        self.cli("decide", "quick", "T01", "--changes", "--as", "reviewer",
                 "--change", "1. Preserve A1 while correcting the implementation.")
        corrected = self.cli("dispatch", "quick", "T01", "--session", "impl").stdout
        prompt = self.cli("prompt", "quick", "T01", "--role", "implementor").stdout
        self.assertNotIn("subagent", corrected + prompt)
        self.assertIn("Preserve A1", prompt)
        self.assertIn("docket batch quick --verify M1 --as implementor", prompt)

    def test_standard_warns_but_keeps_its_recorded_lifecycle(self):
        out = self.cli("init", "old-style", "--mode", "standard",
                       "--evidence-mode", "documents-only")
        self.assertIn("deprecated", out.stderr)
        self.assertIn("--mode quick", out.stderr)
        plan = self.f.root / ".docket/runs/old-style/plan.mdx"
        self.assertEqual("tiered-verifier-reviewer", fixtures.parse_meta(plan)["review_policy"])
        self.assertNotIn("deprecated", self.cli("status", "old-style").stderr)

    def test_quick_reviewer_carries_shared_obligations_and_audit(self):
        run = self.prepare()
        report = run / "T01-report-01.mdx"
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "quick", "T01", "--submit")
        self.f.fill_task_report(report, files="- `src/t01.py:1` - done.")
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Updated a fixture oracle and deployment configuration."))
        self.cli("submit", "quick", "T01", "--as", "implementor")
        self.cli("batch", "quick", "--verify", "M1", "--as", "implementor")
        prompt = self.cli("prompt", "quick", "T01", "--role", "reviewer").stdout
        for duty in ("honest-acceptance", "changed-oracle", "configuration"):
            self.assertIn("obligation: " + duty, prompt)
        for text in ("supported", "unresolved", "contradictory", "persistence read-back",
                     "original request", "identical captured suite"):
            self.assertIn(text, prompt)
        self.assertNotIn("docket verify quick T01", prompt)

    def test_active_playbooks_are_compact_and_route_detail(self):
        refs = fixtures.DOCKET.resolve().parents[1] / "references"
        for role in ("planner", "implementor", "reviewer"):
            text = (refs / (role + ".md")).read_text()
            self.assertGreaterEqual(len(text.splitlines()), 80)
            self.assertLessEqual(len(text.splitlines()), 140)
            self.assertIn("recovery.md", text)
            self.assertNotIn("standard preset", text)
        reviewer = (refs / "reviewer.md").read_text()
        self.assertIn("verification-obligations.md", reviewer)
        self.assertNotIn("Routine evidence checks belong to the verifier", reviewer)
        skill = (refs.parent / "SKILL.md").read_text()
        self.assertNotIn("--mode standard", skill)
        self.assertNotIn("coordinator (old quick)", skill)


if __name__ == "__main__":
    unittest.main()
