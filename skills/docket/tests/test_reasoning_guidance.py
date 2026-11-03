"""M1 guidance in real rendered task/correction/review prompts, without new gates."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

import test_docket as fixtures


CRITERION = "A browser-added project remains available after restart."


class ReasoningGuidance(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.cli = self.f.cli
        self.skill = fixtures.DOCKET.resolve().parents[1]

    def start(self) -> Path:
        self.cli("init", "demo", "--evidence-mode", "documents-only")
        run = self.f.root / ".docket/runs/demo"
        self.cli("assign", "demo", "T01", "--as", "planner", "--title", "Preserve saved projects",
                 "--goal", "Preserve the approved project-list behavior.", "--criterion", CRITERION,
                 "--no-default-criteria", "--file", "src/a.py", "--verify", "true")
        self.cli("batch", "demo", "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner")
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        return run

    def ready(self, run: Path, round_number: int = 1, blocked: bool = False):
        if round_number == 1:
            self.f.fill_scope(run / "T01-scope.mdx")
            self.cli("scope", "demo", "T01", "--submit")
        self.f.fill_task_report(run / f"T01-report-{round_number:02d}.mdx", criterion=CRITERION,
                                blocked=blocked)

    def prompt(self, run: Path, role: str) -> tuple[str, dict]:
        text = self.cli("prompt", "demo", "T01", "--role", role, "--max-tokens", "0").stdout
        record = json.loads(sorted((run / ".prompts").glob(f"T01-{role}-*.json"))[-1].read_text())
        self.assertEqual(0, record["guidance_tokens"])
        self.assertEqual([], record["selected_guidance"])
        self.assertNotIn("# Selected guidance", text)
        self.assertGreater(record["mandatory_tokens"], 0)
        return text, record

    @staticmethod
    def state_bytes(run: Path) -> dict[str, bytes]:
        # Prompt records are allowed; public contracts, lifecycle and budgets are not.
        return {str(p.relative_to(run)): p.read_bytes() for p in run.rglob("*")
                if p.is_file() and ".prompts" not in p.relative_to(run).parts}

    def assert_runtime_guidance(self, text: str):
        for phrase in ("For consequential runtime claims", "supported with a source",
                       "unresolved", "contradictory", "proof type", "execution conditions",
                       "actual observed boundary", "static inspection", "controlled tests",
                       "real execution", "does not override user intent", "all interleavings safe"):
            self.assertIn(phrase, text)

    def test_initial_discovery_guidance_survives_zero_budget_and_precedes_scope(self):
        run = self.start()
        self.cli("dispatch", "demo", "T01", "--session", "worker", "--register")
        before = self.state_bytes(run)
        text, record = self.prompt(run, "implementor")
        self.assertEqual("initial", record["stage"])
        self.assertIn(CRITERION, text)
        self.assertIn("after dispatch and before editing", text)
        for phrase in ("consequential safety fact", "distinguishes competing explanations",
                       "discovery constraints", "safe private state", "explicit unrun state",
                       "existing scope reasoning", "do not invent a probe"):
            self.assertIn(phrase, text)
        self.assertLess(text.index("consequential safety fact"), text.index("docket scope demo T01 --submit"))
        self.assert_runtime_guidance(text)
        self.assertEqual(before, self.state_bytes(run), "rendering must not claim scope or mutate lifecycle")
        contract = (self.skill / "references/contracts/implementor.md").read_bytes()
        self.assertEqual("implementor:sha256:" + hashlib.sha256(contract).hexdigest(), record["source_revisions"]["contract"])

    def test_repeated_criteria_keep_distinct_findings_and_regressions_without_budget_changes(self):
        run = self.start()
        changes = [
            "1. Criterion A1: read the saved list after restarting the private companion.\n   Preserve unrelated preferences.",
            "1. Criterion A1: select the saved project and verify its own run, not the startup run.",
        ]
        for rnd, change in enumerate(changes, 1):
            self.ready(run, rnd)
            self.cli("submit", "demo", "T01", "--as", "implementor")
            self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer", "--change", change,
                     "--reason", "Probe unrun: restart capability unavailable; do not infer one shared cause.")
        before = self.state_bytes(run)
        text, record = self.prompt(run, "implementor")
        self.assertEqual("correction", record["stage"])
        self.assertIn("Repeated criterion numbers are a signal to inspect, not proof of a shared premise", text)
        for phrase in ("Use judgment", "when warranted", "existing correction report or escalation",
                       "explicit unrun state", "Preserve accepted criteria and correction budgets",
                       "only the recorded plan owner may grant more rounds", "Preserve earlier fixes"):
            self.assertIn(phrase, text)
        for change in changes:
            for line in change.splitlines():
                self.assertIn(line.strip(), text)
        self.assertIn("T01-decision-01.mdx", text)
        self.assertIn("T01-decision-02.mdx", text)
        self.assertIn("Probe unrun: restart capability unavailable", text)
        self.assertIn("bundle_digest: sha256:", text)
        self.assertEqual(before, self.state_bytes(run))
        self.assertIn(f"- [ ] {CRITERION}", (run / "T01-report-03.mdx").read_text())
        self.ready(run, 3)
        self.cli("submit", "demo", "T01", "--as", "implementor")
        exhausted = self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer",
                             "--change", "1. Criterion A1: inspect a remaining boundary.", ok=False)
        self.assertNotEqual(0, exhausted.returncode)
        self.assertIn("exceeded its local correction budget", exhausted.stderr)
        escalation = json.loads((run / ".escalations/T01-r01.json").read_text())
        self.assertEqual(2, escalation["limit"])
        self.assertEqual(3, escalation["attempts"]["reviewer_returns"])
        self.assertFalse((run / "T01-report-04.mdx").exists(), "guidance must not grant another round")

    def test_quick_review_keeps_semantic_classification_and_qualifies_a_runtime_claim(self):
        run = self.start()
        self.ready(run)
        report = run / "T01-report-01.mdx"
        # An overconfident claim is a representative review input, not proof that
        # persistence occurred. The renderer must retain independent review duties.
        report.write_text(report.read_text().replace("Implemented the feature and verified its behavior.",
                          "Static inspection found save(); therefore project persistence is safe."))
        self.cli("submit", "demo", "T01", "--as", "implementor")
        before = self.state_bytes(run)
        text, record = self.prompt(run, "reviewer")
        self.assertEqual("review", record["stage"])
        self.assert_runtime_guidance(text)
        self.assertNotIn("replace semantic classification", text)
        self.assertIn("criterion-to-observation map", text)
        self.assertIn("persistence read-back", text)
        self.assertIn("only the reviewer decides after a milestone full-suite pass", text)
        self.assertIn("obligation: honest-acceptance", text)
        obligations = text.split("# Applicable verification obligations", 1)[1].split("# Steps", 1)[0]
        self.assert_runtime_guidance(obligations)
        self.assertIn("docket review-packet demo --role reviewer --batch M1", text)
        self.assertEqual(before, self.state_bytes(run))

    def test_corrected_reviewer_prompt_inspects_a_premise_without_reopening_acceptance(self):
        run = self.start()
        self.ready(run)
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("decide", "demo", "T01", "--changes", "--as", "reviewer",
                 "--change", "1. Criterion A1: exercise actual read-back at restart.",
                 "--reason", "Probe result: a new process can lose the saved list; retain the original criterion.")
        self.ready(run, 2)
        self.cli("submit", "demo", "T01", "--as", "implementor")
        before = self.state_bytes(run)
        text, record = self.prompt(run, "reviewer")
        self.assertEqual("review", record["stage"])
        self.assertIn("For corrected members", text)
        self.assertIn("correction packet and delta", text)
        self.assertIn("Repeated criterion numbers are a signal to inspect, not proof of a shared premise", text)
        self.assertIn("explicit unrun state", text)
        self.assertIn("existing decision or escalation", text)
        self.assertIn("only the recorded plan owner may grant more rounds", text)
        self.assertIn("Criterion A1: exercise actual read-back at restart.", text)
        self.assertIn("Probe result: a new process can lose the saved list", text)
        self.assertIn(CRITERION, text)
        self.assertEqual(before, self.state_bytes(run))

    def test_helper_only_guidance_changes_move_the_renderer_revision(self):
        run = self.start()
        self.ready(run)
        self.cli("submit", "demo", "T01", "--as", "implementor")
        base, record = self.prompt(run, "reviewer")
        again, repeated = self.prompt(run, "reviewer")
        self.assertEqual(base, again)
        self.assertEqual(record["renderer_revision"], repeated["renderer_revision"])
        mod = fixtures.load_cli("reasoning_revision_tests")
        edits = (
            ("runtime_reasoning", '"For consequential runtime claims, retain the semantic classification supported "',
             '"For consequential runtime claims, retain the existing semantic classification supported "',
             "retain the existing semantic classification"),
            ("correction_reasoning", '"Repeated criterion numbers are a signal to inspect, not proof of a shared premise. "',
             '"Repeated criterion numbers only signal inspection; they do not prove a shared premise. "',
             "Repeated criterion numbers only signal inspection"),
        )
        for helper, old, new, rendered in edits:
            with self.subTest(helper=helper):
                self.assertIn(helper, mod.RENDERER_SOURCE_FUNCTIONS)
                # Each disposable build edits only this helper's wording. The
                # compose/checklist callers and canonical contracts stay fixed.
                binary = self.f._stage_patched_bin(f"changed-{helper}", old, new)
                output = self.f._prompt_with_bin(binary, "T01", "reviewer")
                self.assertIn(rendered, output)
                self.assertNotEqual(self.f._renderer_of(base), self.f._renderer_of(output))

    def test_old_report_without_new_fields_can_still_submit_and_be_approved(self):
        run = self.start()
        self.ready(run)
        report = run / "T01-report-01.mdx"
        for new_field in ("proof type", "safety fact", "probe result", "unrun state"):
            self.assertNotIn(new_field, report.read_text().lower())
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("batch", "demo", "--verify", "M1", "--as", "implementor")
        self.cli("batch", "demo", "--approve", "M1", "--as", "reviewer", "--reason", "Existing contract is met.")
        self.assertIn(f"- [x] {CRITERION}", report.read_text())
        self.assertIn("status: approved", report.read_text())

    def test_honest_block_needs_no_probe_or_runtime_proof_fields(self):
        run = self.start()
        self.ready(run, blocked=True)
        self.cli("submit", "demo", "T01", "--blocked", "--as", "implementor")
        before = self.state_bytes(run)
        text, record = self.prompt(run, "reviewer")
        self.assertEqual("review", record["stage"])
        self.assertIn("blocked work", text)
        self.assertIn("docket bundle demo T01", text)
        self.assertIn("--waive", text)
        self.assertIn("The reviewer must decide whether to waive the external failure.",
                      (run / "T01-report-01.mdx").read_text())
        self.assertEqual([], list(run.glob("T01-verification-*.mdx")))
        self.assertEqual(before, self.state_bytes(run))
        self.cli("decide", "demo", "T01", "--waive", "--as", "reviewer", "--reason", "Accept the stated external gap.")
        self.assertIn("status: waived", (run / "T01-report-01.mdx").read_text())


if __name__ == "__main__":
    unittest.main()
