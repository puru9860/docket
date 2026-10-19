"""Token accounting and handoff-stall regressions from the October UI-run audit."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

import test_docket as fixtures


class UsageAccounting(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.cli = self.f.cli
        self.name = "docket_usage_accounting_tests"
        self.module = fixtures.load_cli(self.name)
        self.previous_cwd = os.getcwd()
        os.chdir(self.f.root)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.f.tearDown()

    def submodule(self, name):
        return sys.modules[f"{self.name}.{name}"]

    def quick_run(self, name):
        self.cli("init", name, "--mode", "quick", "--evidence-mode", "documents-only")
        run = self.f.root / ".docket/runs" / name
        self.cli("assign", name, "T01", "--harness", "opencode", "--file", "src/t01.py",
                 "--verify", "true", "--as", "planner")
        self.f.fill_task(run / "T01-task.mdx")
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", name, "T01", "--submit")
        self.cli("batch", name, "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner")
        self.cli("batch", name, "--close", "M1", "--as", "planner")
        return run

    def note(self, run, sid, at, role="reviewer", harness="claude"):
        with (run / ".harness-sessions.jsonl").open("a") as fh:
            fh.write(json.dumps({"at": at, "harness": harness, "session_id": sid,
                                 "role": role, "owner": "-", "cwd": str(self.f.root)}) + "\n")

    def rollout(self, path, thread, readings, model="gpt-x"):
        keys = ("input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "total_tokens")
        lines = [json.dumps({"type": "session_meta", "timestamp": readings[0][0],
                             "payload": {"id": thread, "cwd": "/work/project"}}),
                 json.dumps({"type": "turn_context", "timestamp": readings[0][0],
                             "payload": {"model": model}})]
        lines += [json.dumps({"type": "event_msg", "timestamp": at, "payload": {
            "type": "token_count", "info": {"total_token_usage": dict(zip(keys, counts))}}})
            for at, counts in readings]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")

    def test_codex_resumed_shard_continues_the_thread(self):
        """A resumed Codex thread's shard counts; a stranger's shard never does."""
        home = Path(self.f._feedback_tmp.name) / "codex-home"
        tid = "01a1153c-7a58-7470-96f9-d74f52e3d6aa"
        day1 = home / "sessions/2026/10/07"
        day2 = home / "sessions/2026/10/08"
        self.rollout(day1 / f"rollout-2026-10-07T13-05-47-{tid}.jsonl", tid,
                     [("2026-10-07T13:06:00Z", (100, 80, 10, 2, 110))])
        # The resumed shard's cumulative counter carries on from the original's.
        self.rollout(day2 / f"rollout-2026-10-08T22-59-25-{tid}_01a11c82-54bd.jsonl", tid,
                     [("2026-10-08T17:18:00Z", (300, 250, 30, 5, 330)),
                      ("2026-10-08T17:18:00Z", (300, 250, 30, 5, 330))], model="gpt-y")
        self.rollout(day2 / f"rollout-2026-10-08T23-00-00-{tid}_ffff.jsonl", "someone-else",
                     [("2026-10-08T18:00:00Z", (9000, 0, 0, 0, 9000))])
        os.environ["CODEX_HOME"] = str(home)
        try:
            shards = self.module.codex_thread_shards(tid)
            usage = self.module.codex_session_usage(shards)
        finally:
            os.environ.pop("CODEX_HOME", None)
        self.assertEqual(2, len(shards))
        # A repeated reading advances nothing and is not another call.
        self.assertEqual((2, 330, 50, 250, 30), tuple(usage[k] for k in (
            "calls", "total", "input", "cache_read", "output")))
        self.assertEqual({"gpt-x": 110, "gpt-y": 220},
                         {m: c["total"] for m, c in usage["by_model"].items()})

    def test_usage_window_ends_when_the_session_joins_a_later_run(self):
        """A session reused by a later run stops counting for the earlier one."""
        env, _ = self.f.fake_claude_session()
        self.f.init5()
        first = self.f.root / ".docket/runs/demo"
        self.cli("init", "later", "--mode", "quick", "--evidence-mode", "documents-only")
        later = self.f.root / ".docket/runs/later"
        sid = env["CLAUDE_CODE_SESSION_ID"]
        self.note(first, sid, "2026-09-23T01:00:00Z")
        self.note(later, sid, "2026-09-23T01:00:09Z")
        rows = {row["session_id"]: row for row in json.loads(self.f.cli_env(
            {"CLAUDE_CONFIG_DIR": env["CLAUDE_CONFIG_DIR"]}, "usage", "demo", "--json").stdout)}
        row = rows[sid]
        # m1 at :05 and the subagent's s1 at :08 count; m2 at :10 belongs to the later run.
        self.assertEqual("2026-09-23T01:00:09Z", row["until"])
        self.assertEqual((2, 1168), (row["calls"], row["total"]))
        self.assertEqual({"claude-opus-5-5": 1115, "claude-haiku-4-5": 53},
                         {m: c["total"] for m, c in row["by_model"].items()})

    def test_finished_run_stops_counting_after_its_wrap_up(self):
        sessions = self.submodule("sessions")
        run = self.quick_run("closed")
        self.assertIsNone(sessions.run_completed_at(run))
        original = sessions.run_completed_at
        sessions.run_completed_at = lambda _d: 1_000_000.0
        try:
            until = sessions.session_until(run, {"harness": "claude", "session_id": "s1",
                                                 "first_seen": "1970-01-01T00:00:00Z"})
        finally:
            sessions.run_completed_at = original
        self.assertEqual(sessions.stamp(1_000_000.0 + sessions.RUN_WRAP_UP_SECONDS), until)

    def test_a_transcript_with_no_call_in_the_window_is_a_gap_not_zero(self):
        env, _ = self.f.fake_claude_session()
        run = self.f.init5()
        self.note(run, env["CLAUDE_CODE_SESSION_ID"], "2026-09-24T00:00:00Z")
        extra = {"CLAUDE_CONFIG_DIR": env["CLAUDE_CONFIG_DIR"]}
        [row] = json.loads(self.f.cli_env(extra, "usage", "demo", "--json").stdout)
        self.assertEqual((True, "empty", 0), (row["found"], row["telemetry"], row["total"]))
        self.assertIn("run tokens: unknown (reviewer empty)",
                      self.f.cli_env(extra, "retrospective", "demo").stdout)
        self.assertIn("transcript holds no call inside the run window",
                      self.f.cli_env(extra, "metrics", "demo").stdout)

    def test_scorecard_splits_a_mixed_session_by_call(self):
        models = self.submodule("models")
        record = {"origin": "usage", "total": 30, "models": ["a/one", "b/two"],
                  "by_model": {"a/one": {"total": 10}, "b/two": {"total": 20}}}
        self.assertEqual((10, 20), (models.usage_tokens_for_model(record, "a/one"),
                                    models.usage_tokens_for_model(record, "b/two")))
        # Without a split, a mixed session cannot be divided after the fact.
        legacy = {"origin": "usage", "total": 30, "models": ["a/one", "b/two"]}
        self.assertEqual(0, models.usage_tokens_for_model(legacy, "a/one"))
        self.assertEqual(30, models.usage_tokens_for_model(
            {"origin": "usage", "total": 30, "models": ["a/one"]}, "a/one"))

    def test_opencode_subagent_command_is_credited_to_its_root_session(self):
        import sqlite3
        sessions = self.submodule("sessions")
        con = sqlite3.connect(":memory:")
        con.execute("create table session (id text primary key, parent_id text)")
        con.executemany("insert into session values (?, ?)",
                        [("root", None), ("child", "root"), ("grandchild", "child")])
        self.assertEqual("root", sessions._opencode_root(con, "grandchild"))
        self.assertEqual("root", sessions._opencode_root(con, "root"))

    def test_metrics_count_prompts_bound_by_dispatch(self):
        run = self.quick_run("prompts")
        prompts = run / ".prompts"
        prompts.mkdir()
        (prompts / "T01-implementor-r01-0123456789ab.txt").write_text("x" * 40)
        (prompts / "T01-implementor-r02-ba9876543210.txt").write_text("y" * 2)
        out = self.cli("metrics", "prompts").stdout
        self.assertIn("prompt renders: 2 (42 chars)", out)
        self.assertIn("implementor prompt renders: 2", out)

    def log(self, run, role, entries):
        path = run / ".delivery" / role / "log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def test_missed_handoffs_ignore_concurrent_watchers(self):
        run = self.quick_run("wakes")
        self.log(run, "implementor", [
            # Two watchers announce together: not a miss.
            {"at": "2026-10-08T09:57:26Z", "key": "a", "kind": "announced"},
            {"at": "2026-10-08T09:57:28Z", "key": "a", "kind": "announced"},
            {"at": "2026-10-08T09:57:31Z", "key": "a", "kind": "receipt"},
            # Re-announced after the lease lapsed, then taken: a miss.
            {"at": "2026-10-08T12:56:33Z", "key": "b", "kind": "announced"},
            {"at": "2026-10-08T14:50:56Z", "key": "b", "kind": "announced"},
            {"at": "2026-10-08T15:20:15Z", "key": "b", "kind": "claimed"},
        ])
        [missed] = self.module.missed_wakes(run)
        self.assertEqual(("b", 1, False, "2026-10-08T15:20:15Z"),
                         (missed["key"], missed["repeats"], missed["open"], missed["taken"]))
        retrospective = self.cli("retrospective", "wakes").stdout
        self.assertIn("missed handoffs: 1", retrospective)
        self.assertIn("implementor b: first announced 2026-10-08T12:56:33Z, taken "
                      "2026-10-08T15:20:15Z", retrospective)

    def test_a_handoff_resolved_without_receipt_reads_the_same_everywhere(self):
        run = self.quick_run("resolved")
        self.log(run, "checker", [
            {"at": "2026-10-09T20:08:26Z", "key": "orch:1:submitted", "kind": "announced"},
            {"at": "2026-10-09T20:13:30Z", "key": "orch:1:submitted", "kind": "announced"},
            {"at": "2026-10-09T20:14:24Z", "key": "orch:1:submitted", "kind": "retired"},
        ])
        outcome = "resolved 2026-10-09T20:14:24Z without a receipt"
        self.assertIn(outcome, self.cli("metrics", "resolved").stdout)
        self.assertIn(outcome, self.cli("retrospective", "resolved").stdout)

    def test_an_untaken_wake_wakes_the_planner_once_and_shows_in_status(self):
        run = self.quick_run("stalled")
        entries = [{"at": "2026-10-08T14:50:00Z", "key": "batch:M1:verify-ready:1",
                    "kind": "announced"},
                   {"at": "2026-10-08T14:55:05Z", "key": "batch:M1:verify-ready:1",
                    "kind": "announced"}]
        self.log(run, "implementor", entries)
        planner = {str(e["key"]) for e in self.module.derive_events(run, "planner")}
        # One lapsed lease can be a busy session finishing its turn.
        self.assertNotIn("handoff-stalled:implementor:batch:M1:verify-ready:1", planner)
        entries.append({"at": "2026-10-08T15:00:10Z", "key": "batch:M1:verify-ready:1",
                        "kind": "announced"})
        self.log(run, "implementor", entries)
        planner = {str(e["key"]): e for e in self.module.derive_events(run, "planner")}
        stalled = planner["handoff-stalled:implementor:batch:M1:verify-ready:1"]
        self.assertIn("docket pickup stalled --role implementor", stalled["message"])
        self.assertIn("wake not taken: implementor has not taken 'batch:M1:verify-ready:1'",
                      self.cli("status", "stalled").stdout)
        entries.append({"at": "2026-10-08T15:01:00Z", "key": "batch:M1:verify-ready:1",
                        "kind": "receipt"})
        self.log(run, "implementor", entries)
        self.assertNotIn("handoff-stalled:implementor:batch:M1:verify-ready:1",
                         {str(e["key"]) for e in self.module.derive_events(run, "planner")})

    def test_a_run_planned_only_from_the_command_line_completes_and_wakes_its_planner(self):
        """`assign` lists each task in the plan, so completion sees every milestone member."""
        name = "cli-only"
        self.cli("init", name, "--mode", "quick", "--evidence-mode", "documents-only",
                 "--objective", "Two small tasks.", "--approach", "One milestone.")
        run = self.f.root / ".docket/runs" / name
        for owner in ("T01", "T02"):
            self.cli("assign", name, owner, "--harness", "opencode", "--file",
                     f"src/{owner.lower()}.py", "--verify", "true", "--as", "planner",
                     "--title", f"Task {owner}", "--goal", "Works.", "--criterion",
                     "The feature works.", "--no-default-criteria")
        self.assertEqual(["T01", "T02"], self.module.planned_tasks(run))
        self.cli("batch", name, "--create", "M1", "--members", "T01,T02", "--milestone",
                 "--command", "true", "--as", "planner")
        self.cli("batch", name, "--close", "M1", "--as", "planner")
        for owner in ("T01", "T02"):
            self.f.fill_scope(run / f"{owner}-scope.mdx")
            self.cli("scope", name, owner, "--submit")
            self.f.fill_task_report(run / f"{owner}-report-01.mdx",
                                    files=f"- `src/{owner.lower()}.py:1` - implemented it.")
            self.cli("submit", name, owner, "--as", "implementor")
        self.cli("batch", name, "--verify", "M1", "--as", "implementor")
        self.cli("batch", name, "--approve", "M1", "--as", "reviewer", "--reason", "Both hold.")
        planner = [str(e["key"]) for e in self.module.derive_events(run, "planner")]
        self.assertTrue(any(key.startswith("milestones:complete:") for key in planner), planner)
        # The coordinator is told the run is over rather than left to look for more work.
        approved = {str(e["key"]): str(e["message"])
                    for e in self.module.derive_events(run, "implementor")}["batch:M1:approved"]
        self.assertIn("it was the last planned milestone", approved)

    def test_a_flapping_event_does_not_wake_its_recipient_again(self):
        """Live run L4: tests run during review flipped readiness, and each flap re-woke the reviewer."""
        run = self.quick_run("flap")
        self.f.fill_task_report(run / "T01-report-01.mdx")
        self.cli("submit", "flap", "T01", "--as", "implementor")
        self.cli("batch", "flap", "--verify", "M1", "--as", "implementor")
        first = self.cli("watch", "flap", "--role", "reviewer", "--timeout", "0", ok=False)
        self.assertEqual(2, first.returncode, first.stderr)
        key = "batch:M1:ready:1"
        delivery = self.submodule("delivery")
        # A flap: the readiness stops deriving for a moment and its records are retired.
        delivery.retire_event(run, "reviewer", key, "no longer derived from lifecycle state")
        quiet = self.cli("watch", "flap", "--role", "reviewer", "--timeout", "0", ok=False)
        self.assertEqual(0, quiet.returncode, quiet.stderr)
        # A changed identity is new work and still wakes.
        received = delivery.received_path(run, "reviewer", key)
        record = json.loads(received.read_text())
        received.write_text(json.dumps({**record, "revision": "sha256:older"}))
        delivery.retire_event(run, "reviewer", key, "no longer derived from lifecycle state")
        again = self.cli("watch", "flap", "--role", "reviewer", "--timeout", "0", ok=False)
        self.assertEqual(2, again.returncode, again.stderr)

    def test_arm_refuses_a_session_started_for_another_run(self):
        self.quick_run("current")
        refused = self.f.cli_env({"DOCKET_RUN": "previous"}, "arm", "current", "--role",
                                 "reviewer", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("DOCKET_RUN=previous", refused.stderr)
        self.assertIn("/clear keeps the old process environment", refused.stderr)
        self.assertNotIn("current reviewer", self.cli("watch", "--armed", "--role", "reviewer",
                                                      "--timeout", "0").stdout)
        # A supervisor arming another role speaks for that role's own session.
        self.f.cli_env({"DOCKET_RUN": "previous", "DOCKET_ROLE": "planner"}, "arm",
                       "current", "--role", "reviewer")
        self.f.cli_env({"DOCKET_RUN": "current"}, "arm", "current", "--role", "implementor")


if __name__ == "__main__":
    unittest.main()
