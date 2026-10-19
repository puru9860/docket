"""One context per task or milestone, reused across corrections."""
from __future__ import annotations

import os
import unittest

import test_docket as fixtures


class RoundContext(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.cli = self.f.cli
        self.previous_cwd = os.getcwd()
        os.chdir(self.f.root)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.f.tearDown()

    def quick(self, name, harness="claude", context=""):
        self.cli("init", name, "--mode", "quick", "--evidence-mode", "documents-only")
        run = self.f.root / ".docket/runs" / name
        if context:
            plan = run / "plan.mdx"
            plan.write_text(plan.read_text().replace("round_context: fresh",
                                                     f"round_context: {context}"))
        self.cli("assign", name, "T01", "--harness", harness, "--file", "src/t01.py",
                 "--verify", "true", "--as", "planner")
        self.f.fill_task(run / "T01-task.mdx")
        self.cli("batch", name, "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner")
        self.cli("batch", name, "--close", "M1", "--as", "planner")
        return run

    def dispatch(self, name):
        out = self.cli("dispatch", name, "T01", "--session", f"{name}-impl",
                       "--agent", f"{name}-impl", "--register").stdout
        [prompt] = (self.f.root / ".docket/runs" / name / ".prompts").glob("T01-*.txt")
        return out, prompt.read_text()

    def test_new_runs_record_a_fresh_round_context(self):
        run = self.quick("recorded")
        self.assertIn("round_context: fresh", (run / "plan.mdx").read_text())

    def test_fresh_claude_round_runs_in_a_subagent_and_the_session_keeps_the_loop(self):
        self.quick("fresh")
        out, prompt = self.dispatch("fresh")
        self.assertIn("NEXT, required: start one subagent now with the Agent tool", out)
        self.assertIn("no model override", out)
        self.assertIn("Do not read that file or work on the task in this session", out)
        # The delegation is the last thing a coordinator reads.
        self.assertTrue(out.rstrip().splitlines()[-2].startswith("NEXT, required:"))
        self.assertIn('"Read .docket/runs/fresh/.prompts/T01-implementor-r01-', out)
        # The milestone loop is the coordinator's, after the subagent replies.
        self.assertIn("`docket batch fresh --verify M1 --as implementor`", out)
        self.assertNotIn("send these exact bytes", out)
        # The worker stops at submission and never runs the suite or waits.
        self.assertNotIn("docket batch fresh --verify", prompt)
        self.assertNotIn("docket watch", prompt)
        self.assertIn("docket submit fresh T01 --as implementor", prompt)

    def test_opencode_uses_its_task_tool(self):
        self.quick("oc", harness="opencode")
        out, _ = self.dispatch("oc")
        self.assertIn("start one subagent now with the task tool (general agent)", out)

    def test_correction_handoff_continues_the_task_subagent(self):
        for harness, method in (("claude", "SendMessage to that agent's id"),
                                ("opencode", "task tool given that subagent's task_id")):
            name = "correction-" + harness
            run = self.quick(name, harness=harness)
            self.dispatch(name)
            self.f.fill_scope(run / "T01-scope.mdx")
            self.cli("scope", name, "T01", "--submit")
            self.f.fill_task_report(run / "T01-report-01.mdx")
            self.cli("submit", name, "T01", "--as", "implementor")
            self.cli("decide", name, "T01", "--changes", "--as", "reviewer",
                     "--change", "1. Correct A1 and preserve earlier behavior.")
            out = self.cli("dispatch", name, "T01", "--session", f"{name}-impl").stdout
            self.assertIn("continue the subagent that ran T01's earlier round", out)
            self.assertIn(method, out)
            self.assertIn("only if that subagent is gone", out)
            self.assertNotIn("start one subagent now", out)

    def test_review_notes_reuse_per_milestone_and_verification_per_task(self):
        for harness, method in (("claude", "SendMessage to that agent's id"),
                                ("opencode", "task tool given that subagent's task_id")):
            name = "note-" + harness
            run = self.quick(name)
            plan = run / "plan.mdx"
            plan.write_text(plan.read_text().replace(
                "round_context: fresh", "round_context: fresh\n"
                f"reviewer_harness: {harness}\nverifier_harness: {harness}"))
            review = self.module_note("reviewer", name)
            self.assertIn("one subagent per milestone", review)
            self.assertIn("a correction or re-review", review)
            self.assertIn(method, review)
            verify = self.module_note("verifier", name)
            self.assertIn("one subagent per task", verify)
            self.assertIn(method, verify)

    def test_codex_and_shared_runs_keep_the_round_in_the_session(self):
        for name, harness, context in (("cx", "codex", ""), ("shared", "claude", "shared")):
            self.quick(name, harness=harness, context=context)
            out, prompt = self.dispatch(name)
            self.assertNotIn("NEXT, required", out)
            self.assertIn("send these exact bytes to the worker", out)
            self.assertIn(f"`docket batch {name} --verify M1 --as implementor`", prompt)

    def test_a_run_without_the_field_keeps_its_shared_session(self):
        run = self.quick("old")
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("round_context: fresh\n", ""))
        out, prompt = self.dispatch("old")
        self.assertNotIn("NEXT, required", out)
        self.assertIn("docket watch old --role implementor", prompt)

    def test_an_unknown_round_context_refuses(self):
        self.quick("bad", context="sometimes")
        refused = self.cli("dispatch", "bad", "T01", "--session", "impl-1", "--register",
                           ok=False)
        self.assertIn("unknown round_context 'sometimes'", refused.stderr)

    def test_a_fresh_reviewer_wake_says_to_decide_in_a_subagent(self):
        run = self.quick("review")
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("round_context: fresh",
                                                 "round_context: fresh\nreviewer_harness: claude"))
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "review", "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "review", "T01", "--as", "implementor")
        self.cli("batch", "review", "--verify", "M1", "--as", "implementor")
        wake = self.cli("watch", "review", "--role", "reviewer", "--timeout", "0", ok=False)
        self.assertEqual(2, wake.returncode, wake.stderr)
        self.assertIn("Round context is fresh: after pickup, do not review here.", wake.stderr)
        self.assertIn("You are the reviewer for docket run review.", wake.stderr)
        # The implementor's own wakes lead to a dispatch, which carries its instruction.
        self.assertEqual("", self.module_note("implementor", "review"))

    def test_a_coordinator_that_stops_after_a_submission_is_woken_for_the_next_task(self):
        """Live run L5: Haiku stopped after T01 to ask; nothing pending would ever wake it."""
        name = "loop"
        self.cli("init", name, "--mode", "quick", "--evidence-mode", "documents-only")
        run = self.f.root / ".docket/runs" / name
        for owner in ("T01", "T02", "T03"):
            self.cli("assign", name, owner, "--harness", "codex", "--file", f"src/{owner}.py",
                     "--verify", "true", "--as", "planner", "--title", owner, "--goal", "Works.",
                     "--criterion", "The feature works.", "--no-default-criteria")
        for bid, members in (("M1", "T01,T02"), ("M2", "T03")):
            self.cli("batch", name, "--create", bid, "--members", members, "--milestone",
                     "--command", "true", "--as", "planner")
            self.cli("batch", name, "--close", bid, "--as", "planner")
        module = fixtures.load_cli("docket_round_loop_tests")

        def next_tasks():
            return {k: e["message"] for k, e in
                    ((str(e["key"]), e) for e in module.derive_events(run, "implementor"))
                    if k.endswith(":next-task")}
        self.cli("dispatch", name, "T01", "--session", "coord", "--register")
        self.assertEqual({}, next_tasks())
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", name, "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx", files="- `src/T01.py:1` - done.")
        self.cli("submit", name, "T01", "--as", "implementor")
        waiting = next_tasks()
        self.assertEqual(["T02:1:next-task"], list(waiting))
        self.assertIn(f"`docket dispatch {name} T02 --session coord`", waiting["T02:1:next-task"])
        self.cli("dispatch", name, "T02", "--session", "coord")
        self.assertEqual({}, next_tasks())
        self.f.fill_scope(run / "T02-scope.mdx")
        self.cli("scope", name, "T02", "--submit")
        self.f.fill_task_report(run / "T02-report-01.mdx", files="- `src/T02.py:1` - done.")
        self.cli("submit", name, "T02", "--as", "implementor")
        # The next milestone waits for its predecessor's approval, so nothing is offered.
        self.assertEqual({}, next_tasks())

    def module_note(self, role, run):
        module = fixtures.load_cli("docket_round_context_tests")
        return module.fresh_review_note(role, [(run, "item")])


if __name__ == "__main__":
    unittest.main()
