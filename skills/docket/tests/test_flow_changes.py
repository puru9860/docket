"""Milestone boundaries, correction evidence, and focused review duties."""
from __future__ import annotations

import json
import os
import unittest

import test_docket as fixtures


class FlowChanges(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.cli = self.f.cli
        self.previous_cwd = os.getcwd()
        os.chdir(self.f.root)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.f.tearDown()

    def milestone(self, name, members, edges=""):
        args = ["batch", "demo", "--create", name, "--members", members, "--milestone"]
        if edges:
            args += ["--depends-on", edges]
        self.cli(*args)
        self.cli("batch", "demo", "--close", name)

    def ready_pair(self, same=False, batch_edge=False, old=False):
        run = self.f.init_tiered()
        if old:
            plan = run / "plan.mdx"
            plan.write_text(plan.read_text().replace("milestone_dependencies: approval-v1\n", ""))
        self.f.assign_tiered(run, "demo", "T01")
        self.f.assign_tiered(run, "demo", "T02", depends="" if batch_edge else "T01")
        self.milestone("M1", "T01,T02" if same else "T01",
                       "T02:T01" if same and batch_edge else "")
        if not same:
            self.milestone("M2", "T02", "T02:T01" if batch_edge else "")
        self.f.submit_tiered(run, "demo", "T01")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        return run

    def test_new_runs_record_versioned_milestone_dependencies(self):
        for mode in ("standard", "quick"):
            self.cli("init", mode, "--mode", mode, "--evidence-mode", "documents-only")
            plan = self.f.root / ".docket/runs" / mode / "plan.mdx"
            self.assertIn("milestone_dependencies: approval-v1", plan.read_text())

    def test_cross_milestone_dispatch_consumption_and_events_wait_for_approval(self):
        for batch_edge in (False, True):
            with self.subTest(batch_edge=batch_edge):
                if batch_edge:
                    self.f.tearDown()
                    self.f.setUp()
                    os.chdir(self.f.root)
                run = self.ready_pair(batch_edge=batch_edge)
                refused = self.cli("dispatch", "demo", "T02", "--session", "worker",
                                   "--register", ok=False)
                self.assertNotEqual(0, refused.returncode)
                self.assertIn("milestone M1 approval", refused.stderr)
                self.assertFalse(list((run / ".dispatch").glob("T02*")))
                pin = self.cli("depend", "demo", "T02", "--on", "T01", ok=False)
                self.assertNotEqual(0, pin.returncode)
                self.assertIn("milestone M1 approval", pin.stderr)
                self.assertFalse((run / ".deps/T02.json").exists())
                events = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
                self.assertNotIn("T02:1:dispatch-ready", events)
                plan = run / "plan.mdx"
                plan.write_text(plan.read_text().replace("provisional_integration: forbidden",
                                                         "provisional_integration: allowed"))
                self.assertNotEqual(0, self.cli("depend", "demo", "T02", "--on", "T01",
                                              ok=False).returncode)
                self.cli("batch", "demo", "--verify", "M1", "--command", "true")
                self.cli("batch", "demo", "--approve", "M1", "--as", "reviewer")
                events = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
                self.assertIn("T02:1:dispatch-ready", events)
                self.cli("depend", "demo", "T02", "--on", "T01")
                self.cli("dispatch", "demo", "T02", "--session", "worker", "--register")

    def test_same_milestone_verified_dependency_can_dispatch_and_pin(self):
        run = self.ready_pair(same=True, batch_edge=True)
        self.cli("depend", "demo", "T02", "--on", "T01")
        self.assertIn('"kind": "settled"', (run / ".deps/T02.json").read_text())
        events = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertIn("T02:1:dispatch-ready", events)
        self.cli("dispatch", "demo", "T02", "--session", "worker", "--register")

    def test_old_run_keeps_verified_cross_milestone_dispatch_and_pin(self):
        run = self.ready_pair(old=True)
        self.cli("depend", "demo", "T02", "--on", "T01")
        self.assertIn('"kind": "settled"', (run / ".deps/T02.json").read_text())
        self.cli("dispatch", "demo", "T02", "--session", "worker", "--register")

    def test_unknown_or_empty_dependency_policy_refuses(self):
        run = self.f.init_tiered()
        plan = run / "plan.mdx"
        original = plan.read_text()
        for value in ("", "later-v2"):
            plan.write_text(original.replace("round_context: fresh",
                                              "round_context: fresh\nmilestone_dependencies: " + value))
            refused = self.cli("status", "demo", ok=False)
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("milestone_dependencies", refused.stderr)

    def test_damaged_approved_milestone_does_not_unlock_a_dependency(self):
        run = self.ready_pair()
        self.cli("batch", "demo", "--verify", "M1", "--command", "true")
        self.cli("batch", "demo", "--approve", "M1", "--as", "reviewer")
        module = fixtures.load_cli("docket_flow_barrier_tests")
        batch = module.read_batch(run, "M1")
        where = module.batch_verification_dir(run, "M1", batch["verification"]["digest"])
        (where / "stdout").write_text("tampered suite output")
        refused = self.cli("depend", "demo", "T02", "--on", "T01", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("milestone M1 approval", refused.stderr)
        events = self.cli("events", "demo", "--role", "orchestrator", "--peek").stdout
        self.assertNotIn("T02:1:dispatch-ready", events)

    def test_moved_approved_contract_closes_the_milestone_barrier(self):
        run = self.ready_pair()
        self.cli("batch", "demo", "--verify", "M1", "--command", "true")
        self.cli("batch", "demo", "--approve", "M1", "--as", "reviewer")
        task = run / "T01-task.mdx"
        task.write_text(task.read_text().replace("The feature works.", "The feature survives restart."))
        refused = self.cli("dispatch", "demo", "T02", "--session", "worker",
                           "--register", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("milestone M1 approval", refused.stderr)

    def test_changed_inputs_withdraw_the_milestone_dependency_approval(self):
        run = self.ready_pair()
        self.cli("batch", "demo", "--verify", "M1", "--command", "true")
        self.cli("batch", "demo", "--approve", "M1", "--as", "reviewer")
        inputs = run / ".deps/T01.json"
        inputs.parent.mkdir(exist_ok=True)
        inputs.write_text(json.dumps({"owner": "T01", "dependencies": [
            {"on": "T99", "kind": "final", "bundle": "sha256:" + "0" * 64}]}))
        refused = self.cli("depend", "demo", "T02", "--on", "T01", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("milestone M1 approval", refused.stderr)
        self.assertNotIn("T02:1:dispatch-ready", self.cli(
            "events", "demo", "--role", "orchestrator", "--peek").stdout)

    def test_approval_barrier_keeps_historical_pass_after_later_source_edits(self):
        self.f.repo()
        self.cli("init", "demo", "--mode", "standard", "--evidence-mode", "git")
        run = self.f.root / ".docket/runs/demo"
        self.f.assign_tiered(run, "demo", "T01", fname="src/a.py")
        self.f.assign_tiered(run, "demo", "T02", depends="T01")
        self.milestone("M1", "T01")
        self.milestone("M2", "T02")
        self.f.fill_task_report(run / "T01-report-01.mdx", files="- `src/a.py:1` - unchanged.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        self.cli("batch", "demo", "--verify", "M1", "--command", "true")
        self.cli("batch", "demo", "--approve", "M1", "--as", "reviewer")
        (self.f.root / "src/t02.py").write_text("later_milestone = 2\n")
        self.cli("depend", "demo", "T02", "--on", "T01")
        self.cli("dispatch", "demo", "T02", "--session", "worker", "--register")

    def correction(self, run, owner, number, change):
        self.cli("decide", "demo", owner, "--changes", "--as", "reviewer", "--change", change)
        report = run / f"{owner}-report-{number + 1:02d}.mdx"
        self.f.fill_task_report(report, files=f"- `src/{owner.lower()}.py:1` - corrected.")

    def test_correction_prompt_keeps_every_earlier_change_and_continuation(self):
        run = self.f.init_tiered()
        self.f.assign_tiered(run, "demo", "T01")
        self.f.submit_tiered(run, "demo", "T01")
        self.correction(run, "T01", 1, "1. Preserve FIRST-GUARD.\n   Keep FIRST-CONTINUATION.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.correction(run, "T01", 2, "1. Repair SECOND-GUARD.")
        prompt = self.cli("prompt", "demo", "T01", "--role", "implementor").stdout
        self.assertIn("# Running correction checklist", prompt)
        self.assertIn("FIRST-GUARD", prompt)
        self.assertIn("FIRST-CONTINUATION", prompt)
        self.assertIn("SECOND-GUARD", prompt)
        self.assertIn("T01-decision-01.mdx", prompt)
        self.assertIn("T01-decision-02.mdx", prompt)
        self.assertIn("preserve earlier fixes", prompt)

    def test_correction_packet_leads_with_frozen_delta_and_dependency_changes(self):
        self.f.repo()
        self.cli("init", "demo", "--mode", "standard", "--evidence-mode", "git")
        run = self.f.root / ".docket/runs/demo"
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("tiered-verifier-reviewer",
                                                 "independent-verifier-reviewer"))
        self.f.assign_tiered(run, "demo", "T01")
        changed = self.f.root / "src/t01.py"
        changed.write_text("ORIGINAL_REVIEW_ONLY = 1\n")
        self.f.submit_tiered(run, "demo", "T01")
        old = json.loads((run / ".bundles/T01/rounds.json").read_text())["entries"][-1]
        self.correction(run, "T01", 1, "1. A1: handle the permitted failed write.")
        self.cli("dispatch", "demo", "T01", "--session", "correction-worker", "--register")
        changed.write_text("ORIGINAL_REVIEW_ONLY = 1\nCORRECTION_DELTA = 2\n")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        current = json.loads((run / ".bundles/T01/rounds.json").read_text())["entries"][-1]
        packet = self.cli("review-packet", "demo", "--role", "reviewer",
                          "--correction", "T01").stdout
        self.assertIn("packet: correction", packet)
        self.assertLess(packet.index("## Correction review"), packet.index("## Acceptance coverage"))
        self.assertIn("Failure model", packet)
        self.assertIn("A1: handle the permitted failed write", packet)
        self.assertIn(old["digest"], packet)
        self.assertIn(current["digest"], packet)
        self.assertIn("CORRECTION_DELTA", packet)
        self.assertNotIn("+ORIGINAL_REVIEW_ONLY", packet)
        self.assertIn("Changed dependency identities", packet)
        self.assertIn("none (frozen input records match)", packet)
        self.assertIn("interfaces, dependencies, shared invariants, or unrelated code", packet)
        self.assertIn("Full historical patch", packet)
        # A correction packet also leads a normal milestone re-review.
        self.milestone("M1", "T01")
        batch = self.cli("review-packet", "demo", "--role", "reviewer", "--batch", "M1").stdout
        self.assertIn("## Correction review", batch)
        self.assertNotIn("+ORIGINAL_REVIEW_ONLY", batch)

    def test_correction_packet_refuses_damaged_prior_evidence(self):
        run = self.f.init_tiered()
        self.f.assign_tiered(run, "demo", "T01")
        self.f.submit_tiered(run, "demo", "T01")
        module = fixtures.load_cli("docket_flow_prior_tests")
        prior = module.latest_bundle(run, "T01")
        self.correction(run, "T01", 1, "1. A1: correct the evidence.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        (module.bundle_dir(run, "T01", prior) / "report.mdx").write_text("tampered prior report")
        packet = self.cli("review-packet", "demo", "--role", "reviewer",
                          "--correction", "T01", ok=False)
        self.assertNotEqual(0, packet.returncode)
        self.assertIn("reviewed prior bundle", packet.stderr)
        self.assertIn("damaged", packet.stderr)

    def test_correction_packet_reports_added_frozen_input_without_live_inference(self):
        run = self.f.init_tiered()
        for owner in ("T01", "T02"):
            self.f.assign_tiered(run, "demo", owner)
            self.f.submit_tiered(run, "demo", owner)
            self.cli("verify", "demo", owner, "--result", "pass", "--as", "verifier")
        self.correction(run, "T02", 1, "1. A1: consume T01's verified behavior.")
        self.cli("depend", "demo", "T02", "--on", "T01")
        self.cli("submit", "demo", "T02", "--as", "implementor")
        packet = self.cli("review-packet", "demo", "--role", "reviewer",
                          "--correction", "T02").stdout
        self.assertIn('T01: null -> {"bundle": "sha256:', packet)
        self.assertIn("unavailable:", packet)
        self.assertIn("no source revision", packet)

    def test_rereview_delta_cannot_hide_changes_before_its_frozen_start(self):
        run = self.f.init_tiered()
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("tiered-verifier-reviewer",
                                                 "independent-verifier-reviewer"))
        self.f.assign_tiered(run, "demo", "T01")
        self.f.submit_tiered(run, "demo", "T01")
        self.correction(run, "T01", 1, "1. A1: correct the observable behavior.")
        self.cli("submit", "demo", "T01", "--as", "implementor")
        self.cli("verify", "demo", "T01", "--result", "pass", "--as", "verifier")
        report = run / "T01-report-02.mdx"
        report.write_text(report.read_text() + "\nUpdated review explanation.\n")
        self.cli("decide", "demo", "T01", "--approve", "--as", "reviewer", "--re-review", ok=False)
        packet = self.cli("review-packet", "demo", "--role", "reviewer",
                          "--correction", "T01").stdout
        self.assertIn("the frozen delta starts after the reviewed decision", packet)
        self.assertIn("retrieve both full patches", packet)

    def test_focused_evidence_duties_reach_rendered_prompts(self):
        run = self.f.init_tiered()
        self.f.assign_tiered(run, "demo", "T01")
        impl = self.cli("prompt", "demo", "T01", "--role", "implementor").stdout
        self.assertIn("decisive negative control", impl)
        self.assertIn("failure was actually injected", impl)
        self.f.submit_tiered(run, "demo", "T01")
        verifier = self.cli("prompt", "demo", "T01", "--role", "verifier").stdout
        for phrase in ("criterion-to-observation", "false positives", "one or two",
                       "2-3 minutes", "uncertain", "identical captured suite"):
            self.assertIn(phrase, verifier)
        self.milestone("M1", "T01")
        reviewer = self.cli("prompt", "demo", "T01", "--role", "reviewer").stdout
        self.assertIn("consolidated numbered list per member", reviewer)
        self.assertIn("reproducer or causal argument", reviewer)

    def test_planner_guidance_defines_capability_and_context_sizing(self):
        guidance = self.cli("help", "planner").stdout
        for phrase in ("one demonstrable capability", "2-4 tasks", "one working context",
                       "shared discovery", "failure model", "excluded failure classes",
                       "different milestones"):
            self.assertIn(phrase, guidance.lower())
        verifier = self.cli("help", "verifier").stdout
        self.assertIn("2-3 minutes", verifier)
        self.assertIn("negative controls", verifier)
        self.assertIn("false positives", verifier)
        orchestrator = self.cli("help", "orchestrator").stdout
        self.assertIn("same closed milestone", orchestrator)
        self.assertIn("milestone_dependencies: approval-v1", orchestrator)


if __name__ == "__main__":
    unittest.main()
