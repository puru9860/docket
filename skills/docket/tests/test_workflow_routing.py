"""Progress regressions for the October workflow audit's event-routing gaps."""
from __future__ import annotations

import json
import os
import shlex
import unittest

import test_docket as fixtures


class WorkflowRouting(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.cli = self.f.cli
        self.module = fixtures.load_cli("docket_routing_tests")
        self.previous_cwd = os.getcwd()
        os.chdir(self.f.root)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.f.tearDown()

    def events(self, run, role):
        return {str(e["key"]): e for e in self.module.derive_events(run, role)}

    def prepare(self, name, mode="standard", dependent=False, blocked=False):
        self.cli("init", name, "--mode", mode, "--evidence-mode", "documents-only")
        run = self.f.root / ".docket/runs" / name
        for owner in (("T01", "T02") if dependent else ("T01",)):
            args = ["assign", name, owner, "--harness", "opencode", "--file",
                    f"src/{owner.lower()}.py", "--verify", "true"]
            if mode == "quick":
                args += ["--as", "planner"]
            if owner == "T02":
                args += ["--depends-on", "T01"]
            self.cli(*args)
            self.f.fill_task(run / f"{owner}-task.mdx")
            self.f.fill_scope(run / f"{owner}-scope.mdx")
            self.cli("scope", name, owner, "--submit")
        self.cli("batch", name, "--create", "M1", "--members",
                 "T01,T02" if dependent else "T01", "--milestone", "--command", "true",
                 "--as", "planner")
        self.cli("batch", name, "--close", "M1", "--as", "planner")
        self.submit(run, mode=mode, blocked=blocked)
        return run

    def submit(self, run, rnd=1, mode="standard", blocked=False):
        self.f.fill_task_report(run / f"T01-report-{rnd:02d}.mdx", blocked=blocked)
        self.cli("submit", run.name, "T01", "--as", "implementor",
                 *(("--blocked",) if blocked else ()))
        if mode == "standard" and not blocked:
            self.cli("verify", run.name, "T01", "--result", "pass", "--as", "verifier")

    def suite(self, run, command="true", mode="standard", ok=True):
        return self.cli("batch", run.name, "--verify", "M1", "--command", command,
                        "--as", "implementor" if mode == "quick" else "orchestrator", ok=ok)

    def consume_review(self, run):
        wake = self.cli("watch", run.name, "--role", "reviewer", "--timeout", "0", ok=False)
        self.assertEqual(2, wake.returncode, wake.stderr)

    def test_standard_suite_starts_from_its_event_and_approval_starts_aggregate(self):
        run = self.prepare("standard-progress")
        events = self.events(run, "orchestrator")
        self.assertIn("batch:M1:verify-ready:1", events)
        self.assertFalse(self.events(run, "reviewer"))
        self.assertIn("docket batch standard-progress --verify M1", events[
            "batch:M1:verify-ready:1"]["message"])
        command = events["batch:M1:verify-ready:1"]["message"].split("`")[1]
        # Standard keeps the suite command with the runner; CMD is its explicit
        # instruction to supply that command at verification time.
        self.cli(*(arg if arg != "CMD" else "true" for arg in shlex.split(command)[1:]))
        self.assertIn("batch:M1:ready:1", self.events(run, "reviewer"))
        self.assertFalse(self.events(run, "orchestrator"))
        self.cli("batch", run.name, "--approve", "M1", "--as", "reviewer")
        self.assertTrue(any(k.startswith("all:decided:") for k in self.events(run, "orchestrator")))

    def test_standard_passing_suite_wakes_only_reviewer(self):
        run = self.prepare("single-review-owner")
        self.suite(run)
        self.assertIn("batch:M1:ready:1", self.events(run, "reviewer"))
        self.assertNotIn("batch:M1:ready:1", self.events(run, "orchestrator"))

    def test_quick_suite_and_completion_follow_their_events(self):
        run = self.prepare("quick-progress", mode="quick")
        events = self.events(run, "implementor")
        self.assertIn("batch:M1:verify-ready:1", events)
        self.assertFalse(self.events(run, "reviewer"))
        command = events["batch:M1:verify-ready:1"]["message"].split("`")[1]
        self.cli(*shlex.split(command)[1:])
        self.assertIn("batch:M1:ready:1", self.events(run, "reviewer"))
        self.assertNotIn("batch:M1:verify-ready:1", self.events(run, "implementor"))
        self.cli("batch", run.name, "--approve", "M1", "--as", "reviewer")
        self.assertIn("batch:M1:approved", self.events(run, "implementor"))
        self.assertTrue(any(k.startswith("milestones:complete:") for k in self.events(run, "planner")))

    def test_uncertainty_wakes_reviewer_without_releasing_dependent(self):
        run = self.prepare("uncertain", dependent=True)
        detail = "Cannot independently establish expected boundary behavior."
        self.cli("verify", run.name, "T01", "--result", "uncertain", "--detail", detail,
                 "--as", "verifier")
        events = self.events(run, "reviewer")
        self.assertIn("T01:1:verification-uncertain", events)
        event = events["T01:1:verification-uncertain"]
        self.assertIn(detail, event["message"])
        digest = self.module.current_round_digest(run, "T01", 1)
        self.assertIn(digest, event["message"])
        self.assertIn("T01-verification-02.mdx", event["message"])
        dispatch = self.cli("dispatch", run.name, "T02", "--session", "worker2",
                            "--register", ok=False)
        self.assertNotEqual(0, dispatch.returncode)
        self.assertNotIn("T02:1:dispatch-ready", self.events(run, "orchestrator"))
        self.cli("verify", run.name, "T01", "--result", "pass", "--as", "verifier")
        self.assertNotIn("T01:1:verification-uncertain", self.events(run, "reviewer"))
        self.assertIn("T02:1:dispatch-ready", self.events(run, "orchestrator"))

    def test_uncertainty_exception_can_open_and_finish_a_correction(self):
        run = self.prepare("uncertain-correction", dependent=True)
        self.cli("verify", run.name, "T01", "--result", "uncertain", "--detail",
                 "Expected result lacks an independent oracle.", "--as", "verifier")
        self.assertIn("T01:1:verification-uncertain", self.events(run, "reviewer"))
        self.consume_review(run)
        self.cli("decide", run.name, "T01", "--changes", "--change",
                 "Add an independently derived expected value.", "--as", "reviewer")
        self.assertNotIn("T01:1:verification-uncertain", self.events(run, "reviewer"))
        self.assertIn("T01:2:correction-ready", self.events(run, "orchestrator"))
        self.assertNotIn("T02:1:dispatch-ready", self.events(run, "orchestrator"))
        self.submit(run, rnd=2)
        self.assertIn("T02:1:dispatch-ready", self.events(run, "orchestrator"))

    def test_quick_interrupted_changes_wake_reviewer_after_consumed_readiness(self):
        run = self.prepare("interrupted", mode="quick")
        self.suite(run, mode="quick")
        self.consume_review(run)
        crash = self.cli("decide", run.name, "T01", "--changes", "--change",
                         "Fix the missing boundary test.", "--as", "reviewer",
                         fault="transition:report", ok=False)
        self.assertEqual(70, crash.returncode, crash.stderr)
        self.assertFalse((run / "T01-report-02.mdx").exists())
        self.assertTrue(any("unfinished-changes-requested" in k
                            for k in self.events(run, "reviewer")))
        self.consume_review(run)
        self.cli("decide", run.name, "T01", "--changes", "--as", "reviewer")
        self.assertTrue((run / "T01-report-02.mdx").exists())
        self.assertFalse(self.events(run, "reviewer"))
        self.assertIn("T01:2:correction-ready", self.events(run, "implementor"))

    def test_quick_budget_grant_has_new_reviewer_event(self):
        run = self.prepare("budget", mode="quick")
        for rnd in (1, 2, 3):
            if rnd > 1:
                self.submit(run, rnd, mode="quick")
            self.suite(run, mode="quick")
            self.consume_review(run)
            correction = self.cli("decide", run.name, "T01", "--changes", "--change",
                                  "Fix boundary behavior.", "--as", "reviewer", ok=False)
            self.assertEqual(1 if rnd == 3 else 0, correction.returncode, correction.stderr)
        self.assertTrue(any("escalated" in k for k in self.events(run, "planner")))
        self.cli("escalation", run.name, "T01", "--grant", "1", "--as", "planner",
                 "--reason", "One focused fix remains.")
        self.assertTrue(any("budget-granted" in k for k in self.events(run, "reviewer")))
        refused = run / "T01-decision-03.mdx"
        self.assertIn("Fix boundary behavior.", refused.read_text())
        self.assertEqual("no", fixtures.parse_meta(refused)["applied"])
        self.consume_review(run)
        self.cli("decide", run.name, "T01", "--changes", "--as", "reviewer")
        self.assertTrue((run / "T01-report-04.mdx").exists())
        self.assertFalse(any("budget-granted" in k for k in self.events(run, "reviewer")))

    def test_quick_reviewer_launch_has_one_owner_and_one_claim(self):
        run = self.prepare("launch", mode="quick")
        self.suite(run, mode="quick")
        for role in ("planner", "implementor"):
            self.cli("arm", run.name, "--role", role)
        self.assertNotIn("launch:reviewer", self.events(run, "planner"))
        self.assertIn("launch:reviewer", self.events(run, "implementor"))
        for session in ("first", "second"):
            self.cli("session", run.name, "--register", "--session", session,
                     "--role", "implementor")
        self.cli("reconcile", run.name, "--role", "implementor")
        first, _, result = self.module.claim_event(
            run, run.name, "implementor", "first", key="launch:reviewer")
        self.assertEqual("claimed", result)
        retry, _, result = self.module.claim_event(
            run, run.name, "implementor", "first", key="launch:reviewer")
        self.assertEqual("already-held", result)
        self.assertEqual(first, retry)
        with self.assertRaises(SystemExit):
            self.module.claim_event(run, run.name, "implementor", "second", key="launch:reviewer")

    def check_blocked_budget_grant(self, mode):
        run = self.prepare(f"blocked-budget-{mode}", mode=mode, blocked=True)
        for rnd in (1, 2, 3):
            if rnd > 1:
                self.submit(run, rnd=rnd, mode=mode, blocked=True)
            self.cli("route", run.name, "--kind", "blocked", "--owner", "T01")
            self.consume_review(run)
            correction = self.cli("decide", run.name, "T01", "--changes", "--change",
                                  "Resolve the blocked boundary condition.", "--as", "reviewer",
                                  ok=False)
            self.assertEqual(1 if rnd == 3 else 0, correction.returncode, correction.stderr)
        report = run / "T01-report-03.mdx"
        self.assertEqual("blocked", fixtures.parse_meta(report)["status"])
        before_report = report.read_bytes()
        bundle_dir = run / ".bundles/T01/03"
        before_bundle = {p.relative_to(bundle_dir): p.read_bytes()
                         for p in bundle_dir.rglob("*") if p.is_file()}
        self.assertTrue(before_bundle)
        self.assertTrue(any("escalated" in k for k in self.events(run, "planner")))
        self.cli("escalation", run.name, "T01", "--grant", "1", "--as", "planner",
                 "--reason", "Resolve the outstanding blocker.")
        key = "T01:3:budget-granted:T01-r01"
        events = self.events(run, "reviewer")
        self.assertIn(key, events)
        self.assertFalse(any("budget-granted" in k for k in self.events(run, "verifier")))
        self.assertEqual(before_report, report.read_bytes())
        self.assertEqual(before_bundle, {p.relative_to(bundle_dir): p.read_bytes()
                                        for p in bundle_dir.rglob("*") if p.is_file()})
        self.assertIn("Resolve the blocked boundary condition.",
                      (run / "T01-decision-03.mdx").read_text())
        wake = self.cli("watch", run.name, "--role", "reviewer", "--timeout", "0", ok=False)
        self.assertEqual(2, wake.returncode, wake.stderr)
        self.assertIn("was granted", wake.stderr)
        command = events[key]["message"].split("`")[1]
        self.cli(*shlex.split(command)[1:])
        self.assertEqual("changes-requested", fixtures.parse_meta(report)["status"])
        self.assertEqual("draft", fixtures.parse_meta(run / "T01-report-04.mdx")["status"])
        self.assertNotIn(key, self.events(run, "reviewer"))
        runner = "implementor" if mode == "quick" else "orchestrator"
        self.assertIn("T01:4:correction-ready", self.events(run, runner))
        self.cli(*shlex.split(command)[1:])
        self.assertFalse((run / "T01-report-05.mdx").exists())

    def test_quick_blocked_budget_grant_wakes_reviewer(self):
        self.check_blocked_budget_grant("quick")

    def test_standard_blocked_budget_grant_wakes_reviewer(self):
        self.check_blocked_budget_grant("standard")

    def test_suite_ready_retires_while_running_and_failure_routes_only_recovery(self):
        run = self.prepare("suite-states")
        self.assertIn("batch:M1:verify-ready:1", self.events(run, "orchestrator"))
        first_revision = self.events(run, "orchestrator")["batch:M1:verify-ready:1"]["revision"]
        self.assertEqual(2, self.cli("watch", run.name, "--role", "orchestrator",
                                     "--timeout", "0", ok=False).returncode)
        with self.module.owner_lock(run, "batch-M1"):
            self.assertFalse(self.events(run, "orchestrator"))
        self.suite(run, command="false", ok=False)
        self.assertEqual({"batch:M1:verify-failed"}, set(self.events(run, "orchestrator")))
        self.assertFalse(self.events(run, "reviewer"))
        self.suite(run)
        self.assertFalse(self.events(run, "orchestrator"))
        self.assertIn("batch:M1:ready:1", self.events(run, "reviewer"))
        batch = run / ".batches/M1.json"
        record = json.loads(batch.read_text())
        digest = record["verification"]["digest"].removeprefix("sha256:")
        (run / ".bundles/batches/M1" / digest / "stdout").write_text("tampered")
        self.assertIn("batch:M1:verify-ready:1", self.events(run, "orchestrator"))
        self.assertNotEqual(first_revision, self.events(run, "orchestrator")[
            "batch:M1:verify-ready:1"]["revision"])
        self.assertEqual(2, self.cli("watch", run.name, "--role", "orchestrator",
                                     "--timeout", "0", ok=False).returncode)
        self.assertFalse(self.events(run, "reviewer"))


if __name__ == "__main__":
    unittest.main()
