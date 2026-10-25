"""Regressions for the eval and first live conductor run."""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_docket as fixtures


class Step4(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.cli = self.f.cli
        self.module = fixtures.load_cli("docket_step4_tests")
        self.cli("init", "quick", "--evidence-mode", "documents-only")
        self.run = self.f.root / ".docket/runs/quick"

    def prepare(self):
        self.cli("assign", "quick", "T01", "--file", "src/t01.py", "--verify", "true",
                 "--as", "planner")
        self.f.fill_task(self.run / "T01-task.mdx")
        self.cli("batch", "quick", "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner", "--reviewer-model", "requested-only",
                 "--assignment-reason", "Distinguish requested and observed execution.")
        self.cli("batch", "quick", "--close", "M1", "--as", "planner")

    def test_quick_status_omits_inert_plan_state_before_and_after_completion(self):
        self.prepare()
        for role in ([], ["--role", "planner"]):
            self.assertNotIn("plan: draft", self.cli("status", "quick", *role).stdout)
        self.f.fill_scope(self.run / "T01-scope.mdx")
        self.cli("scope", "quick", "T01", "--submit")
        self.f.fill_task_report(self.run / "T01-report-01.mdx", files="- `src/t01.py:1` - done.")
        self.cli("submit", "quick", "T01", "--as", "implementor")
        self.cli("batch", "quick", "--verify", "M1", "--as", "implementor")
        self.cli("batch", "quick", "--approve", "M1", "--as", "reviewer")
        status = self.cli("status", "quick").stdout
        self.assertIn("1/1 decided", status)
        self.assertNotIn("plan: draft", status)
        planner = self.cli("help", "planner").stdout
        self.assertIn("no plan-status transition", planner)

    def test_evidence_refusal_names_exact_path_and_comma_syntax(self):
        self.prepare()
        self.f.fill_scope(self.run / "T01-scope.mdx")
        self.cli("scope", "quick", "T01", "--submit")
        report = self.run / "T01-report-01.mdx"
        self.f.fill_task_report(report, files="- `src/t01.py:1` - done.")
        original = report.read_text()
        table = "| ID | State | Artifacts | Gaps |\n| --- | --- | --- | --- |\n| A1 | met | TOKEN | none |\n"
        for token in ("evidence/x.out", "evidence/x.out; verify.stdout", "evidence/x.out passed"):
            report.write_text(original.replace("## Evidence", "## Evidence\n\n" + table.replace("TOKEN", token), 1))
            refused = self.cli("submit", "quick", "T01", "--check", ok=False)
            self.assertNotEqual(0, refused.returncode)
            self.assertIn(".docket/runs/quick/evidence/x.out", refused.stdout + refused.stderr)
            self.assertIn("comma-separated", refused.stdout + refused.stderr)
        artifact = self.run / "evidence/x.out"
        artifact.parent.mkdir()
        artifact.write_text("observed output")
        for suffix in ("; verify.stdout", " passed"):
            report.write_text(original.replace("## Evidence", "## Evidence\n\n" + table.replace(
                "TOKEN", ".docket/runs/quick/evidence/x.out" + suffix), 1))
            self.assertNotEqual(0, self.cli("submit", "quick", "T01", "--check", ok=False).returncode)
        report.write_text(original.replace("## Evidence", "## Evidence\n\n" + table.replace(
            "TOKEN", ".docket/runs/quick/evidence/x.out, verify.stdout"), 1))
        self.cli("submit", "quick", "T01", "--check")

    def test_implementor_prompt_has_correct_evidence_row(self):
        self.prepare()
        prompt = self.cli("prompt", "quick", "T01", "--role", "implementor").stdout
        self.assertIn("| A1 | met | `.docket/runs/quick/evidence/x.out` | none |", prompt)
        self.assertIn("comma-separated", prompt)

    def test_manual_wait_guidance_preserves_claude_background_task_and_bans_detachment(self):
        claude = self.module.short_watch_refusal("claude", "planner", 30)
        self.assertIn("run_in_background", claude)
        self.assertIn("without a short --timeout", claude)
        for harness in ("claude", "codex", "opencode"):
            refusal = self.module.short_watch_refusal(harness, "planner", 30)
            self.assertIn("nohup", refusal)
            self.assertIn("Stop hook", refusal)
        for role in ("signalling", "launch"):
            text = (self.cli("help", role).stdout if role == "signalling" else
                    (fixtures.DOCKET.resolve().parents[1] / "references/launch.md").read_text())
            self.assertIn("run_in_background", text)
            self.assertIn("Codex background terminals", text)
            self.assertIn("nohup", text)
            self.assertNotIn("Never background `docket watch` in any harness", text)
        self.prepare()
        prompt = self.cli("prompt", "quick", "T01", "--role", "implementor").stdout
        self.assertNotIn("Never background `docket watch`;", prompt)
        self.assertIn("run_in_background", prompt)

    def test_help_command_names_match_their_own_help_without_side_effects(self):
        before = {str(p): p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        for name in ("decide", "feedback", "submit", "help"):
            with self.subTest(name=name):
                self.assertEqual(self.cli(name, "--help").stdout, self.cli("help", name).stdout)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.run.rglob("*") if p.is_file()})
        self.assertIn("Reviewer", self.cli("help", "reviewer").stdout)
        invalid = self.cli("help", "not-a-command", ok=False)
        self.assertNotEqual(0, invalid.returncode)
        active = self.cli("help", "decide", "--run", "quick", "--owner", "T01", ok=False)
        self.assertNotEqual(0, active.returncode)

    def test_conductor_worker_prompts_end_turn_for_both_roles_and_harnesses(self):
        self.prepare()
        (self.run / ".conductor").mkdir()
        (self.run / ".conductor/managed.json").write_text("{}")
        before = Path.cwd()
        os.chdir(self.f.root)
        self.addCleanup(os.chdir, before)
        for role in ("implementor", "reviewer"):
            if role == "reviewer":
                self.f.fill_scope(self.run / "T01-scope.mdx")
                self.cli("scope", "quick", "T01", "--submit")
                self.f.fill_task_report(self.run / "T01-report-01.mdx", files="- `src/t01.py:1` - done.")
                self.cli("submit", "quick", "T01", "--as", "implementor")
                self.cli("batch", "quick", "--verify", "M1", "--as", "implementor")
            for harness in ("claude", "codex", "opencode"):
                with self.subTest(harness=harness, role=role):
                    task = self.run / "T01-task.mdx"
                    meta, body = self.module.parse(task.read_text())
                    meta["harness"] = harness
                    task.write_text(self.module.render(meta, body))
                    record = dict(role=role, session="worker", agent_name="worker", requested_harness=harness)
                    event = dict(key="work:T01", owner="T01", message="Review or implement this task.")
                    text = self.module.worker_prompt(self.run, {"batch": "M1", "members": ["T01"]}, record, event)
                    self.assertIn("end your turn", text)
                    self.assertNotIn("`docket watch", text)
                    self.assertNotIn("Wait with", text)

    def test_reviewer_model_is_observed_from_own_window_and_persisted(self):
        self.prepare()
        before = Path.cwd()
        os.chdir(self.f.root)
        self.addCleanup(os.chdir, before)
        env, transcript = self.f.fake_claude_session()
        session = dict(harness="claude", session_id=env["CLAUDE_CODE_SESSION_ID"])
        sessions = sys.modules["docket_step4_tests.sessions"]
        with patch.dict(os.environ, env), patch.object(sessions, "harness_session", return_value=session), \
                patch.object(sessions, "stamp", return_value="2026-09-23T01:00:07Z"):
            text = transcript.read_text()
            transcript.write_text("\n".join(line for line in text.splitlines()
                                             if "01:00:10" not in line) + "\n")
            ref = self.module.note_harness_session(self.run, "reviewer", "T01", [])
            self.assertNotIn("models_observed", json.loads(
                (self.run / ".harness-sessions.jsonl").read_text().splitlines()[-1]))
            transcript.write_text(text)
            self.module.note_harness_session(self.run, "reviewer", "T01", [])
            ledger = [json.loads(line) for line in (self.run / ".harness-sessions.jsonl").read_text().splitlines()]
            self.assertIn("claude-opus-5-5", ledger[-1].get("models_observed", []))
            status = self.f.cli_env(env, "status", "quick").stdout
            self.assertIn("reviewer=claude-opus-5-5", status)
            self.assertNotIn("reviewer=claude-haiku", status)
        self.assertIsNotNone(ref)

    def test_codex_reviewer_status_uses_telemetry_without_requested_model_fallback(self):
        self.prepare()
        sid = "review-thread"
        started = self.module.stamp(time.time() - 30)
        turn_at = self.module.stamp(time.time() - 20)
        counted_at = self.module.stamp(time.time() - 10)
        home = Path(self.f._feedback_tmp.name) / "codex-home"
        path = home / "sessions/2026/10/26/rollout-2026-10-26T00-00-00-review-thread.jsonl"
        path.parent.mkdir(parents=True)
        with (self.run / ".harness-sessions.jsonl").open("w") as ledger:
            ledger.write(json.dumps(dict(at=started, role="reviewer", owner="-",
                                         harness="codex", session_id=sid)) + "\n")
        env = {"CODEX_HOME": str(home)}
        self.f.fill_scope(self.run / "T01-scope.mdx")
        self.cli("scope", "quick", "T01", "--submit")
        self.f.fill_task_report(self.run / "T01-report-01.mdx", files="- `src/t01.py:1` - done.")
        self.cli("submit", "quick", "T01", "--as", "implementor")
        self.cli("batch", "quick", "--verify", "M1", "--as", "implementor")
        self.cli("batch", "quick", "--approve", "M1", "--as", "reviewer")
        self.assertIn("reviewer=unobserved", self.f.cli_env(env, "status", "quick").stdout)
        path.write_text("\n".join(json.dumps(r) for r in [
            dict(type="session_meta", timestamp=started, payload=dict(id=sid)),
            dict(type="turn_context", timestamp=turn_at, payload=dict(model="gpt-6.1-sol")),
            dict(type="event_msg", timestamp=counted_at, payload=dict(
                type="token_count", info=dict(total_token_usage=dict(total_tokens=20, input_tokens=10, output_tokens=10))))
        ]) + "\n")
        status = self.f.cli_env(env, "status", "quick").stdout
        self.assertIn("reviewer=gpt-6.1-sol", status)
        self.assertIn("run session telemetry", status)
        self.assertNotIn("actual reviewer=requested-only", status)
        before = Path.cwd()
        os.chdir(self.f.root)
        self.addCleanup(os.chdir, before)
        sessions = sys.modules["docket_step4_tests.sessions"]
        ref = dict(harness="codex", session_id=sid)
        with patch.dict(os.environ, env), patch.object(sessions, "harness_session", return_value=ref):
            self.module.note_harness_session(self.run, "reviewer", "T01", [])
        entries = [json.loads(line) for line in (self.run / ".harness-sessions.jsonl").read_text().splitlines()]
        self.assertEqual(["gpt-6.1-sol"], entries[-1]["models_observed"])
        path.write_text(path.read_text().replace('"total_tokens": 20', '"total_tokens": "bad"'))
        self.assertIn("reviewer=gpt-6.1-sol", self.f.cli_env(env, "status", "quick").stdout)
        path.unlink()
        self.assertIn("reviewer=gpt-6.1-sol", self.f.cli_env(env, "status", "quick").stdout)

    def test_explicit_discussion_join_warns_without_native_binding_on_retry_too(self):
        self.cli("discuss", "C04", "--start", "--topic", "Wake test", "--message", "Discuss transport.",
                 "--harness", "codex")
        for _ in range(2):
            joined = self.cli("discuss", "C04", "--join", "--as", "peer", "--session", "C04-peer")
            self.assertIn("No native wake binding", joined.stdout)
            self.assertIn("docket discuss C04 --watch --as peer --session C04-peer", joined.stdout)
            self.assertIn("foreground", joined.stdout)
        peer = self.run.parents[1] / "conversations/C04/participants/peer.mdx"
        self.assertEqual("no", fixtures.parse_meta(peer)["harness_observed"])


if __name__ == "__main__":
    unittest.main()
