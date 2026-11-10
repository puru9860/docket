"""Antigravity CLI (`agy`) as a Docket harness."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import test_docket as fixtures

AGY_HOOK = Path(__file__).parents[1] / "hooks" / "agy-wake.sh"
CONVERSATION = "9064b38d-a99a-41be-855f-adadf7545698"


class Antigravity(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.root = self.f.root
        self.tmp = Path(self.f._feedback_tmp.name)

    def tearDown(self):
        self.f.tearDown()

    def agy(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        """Run docket as a child of a process named agy, the way its run_command does."""
        wrapper = self.tmp / "bin" / "agy"
        if not wrapper.exists():
            wrapper.parent.mkdir(exist_ok=True)
            wrapper.symlink_to(sys.executable)
        launch = ("import subprocess, sys; sys.exit(subprocess.run([sys.executable, "
                  f"{str(fixtures.DOCKET)!r}, *{list(args)!r}]).returncode)")
        return subprocess.run([str(wrapper), "-c", launch], cwd=self.root, text=True,
                              capture_output=True, env={**os.environ, **(env or {})})

    def agy_env(self) -> dict[str, str]:
        return {"ANTIGRAVITY_CONVERSATION_ID": CONVERSATION, "ANTIGRAVITY_LS_VERSION": "cli-1.5.3",
                "ANTIGRAVITY_APP_DATA_DIR": str(self.tmp / "agy-data")}

    def write_transcript(self, records: list[dict]) -> Path:
        path = (self.tmp / "agy-data" / "brain" / CONVERSATION / ".system_generated" / "logs"
                / "transcript_full.jsonl")
        path.parent.mkdir(parents=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        return path

    def test_session_is_noted_through_its_process_and_usage_is_measured(self):
        """An agy conversation is noted from its variable, proven by an agy ancestor process."""
        run = self.f.init5()
        armed = self.agy("arm", "demo", "--role", "orchestrator", env=self.agy_env())
        self.assertEqual(0, armed.returncode, armed.stderr)
        [entry] = [json.loads(line) for line in
                   (run / ".harness-sessions.jsonl").read_text().splitlines()]
        self.assertEqual(("agy", CONVERSATION, "cli-1.5.3"),
                         (entry["harness"], entry["session_id"], entry["version"]))

        transcript = self.write_transcript([
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
             "created_at": "2026-11-10T03:38:49Z", "content": "go"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
             "created_at": "2026-11-10T03:38:50Z", "input_tokens": 5704,
             "cache_read_tokens": 5192, "output_tokens": 5},
            {"step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE",
             "created_at": "2026-11-10T03:39:10Z", "input_tokens": 6000,
             "cache_read_tokens": 5700, "output_tokens": 40, "thinking_tokens": 12},
        ])
        entry["at"] = "2026-11-10T03:38:00Z"
        (run / ".harness-sessions.jsonl").write_text(json.dumps(entry) + "\n")
        [row] = json.loads(self.f.cli_env(self.agy_env(), "usage", "demo", "--json").stdout)
        # input_tokens already include the cache read, so only the remainder is fresh input.
        self.assertEqual((2, 812, 10892, 45, 12, 11761, [], str(transcript), "measured"),
                         tuple(row[k] for k in ("calls", "input", "cache_read", "output",
                                                "reasoning", "total", "models", "transcript",
                                                "telemetry")))

    def test_inherited_conversation_variable_without_agy_ancestor_notes_nothing(self):
        run = self.f.init5()
        self.f.cli_env(self.agy_env(), "arm", "demo", "--role", "orchestrator")
        self.assertFalse((run / ".harness-sessions.jsonl").exists())

    def test_an_unknown_ledger_harness_is_missing_telemetry_not_a_crash(self):
        run = self.f.init5()
        (run / ".harness-sessions.jsonl").write_text(json.dumps({
            "at": "2026-11-10T03:38:00Z", "harness": "someday", "session_id": "s1",
            "role": "orchestrator", "owner": "-"}) + "\n")
        [row] = json.loads(self.f.cli("usage", "demo", "--json").stdout)
        self.assertEqual(("someday", "missing"), (row["harness"], row["telemetry"]))

    def test_nearest_harness_wins_when_agy_runs_inside_another_harness(self):
        """agy started from a Claude Code shell inherits Claude's variables; agy is the caller."""
        run = self.f.init5()
        env = {**self.agy_env(), "CLAUDE_CODE_SESSION_ID": "outer-claude", "CLAUDE_PID": "1"}
        self.assertEqual(0, self.agy("arm", "demo", "--role", "orchestrator", env=env).returncode)
        [entry] = [json.loads(line) for line in
                   (run / ".harness-sessions.jsonl").read_text().splitlines()]
        self.assertEqual(("agy", CONVERSATION), (entry["harness"], entry["session_id"]))

    def test_short_watch_inside_agy_is_refused_toward_the_stop_hook(self):
        self.f.init5()
        self.f.cli("arm", "demo", "--role", "orchestrator")
        refused = self.agy("watch", "demo", "--role", "orchestrator", "--timeout", "120",
                           env=self.agy_env())
        self.assertEqual(1, refused.returncode, refused.stderr)
        self.assertIn("--timeout 120 inside agy is a polling loop", refused.stderr)
        self.assertIn("hooks/agy-wake.sh", refused.stderr)

    def hook(self, payload: object, cwd: Path, extra: dict | None = None) -> subprocess.CompletedProcess:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        env = {**os.environ, "DOCKET_ROLE": "orchestrator", "DOCKET_WATCH_TIMEOUT": "0", **(extra or {})}
        return subprocess.run(["bash", str(AGY_HOOK)], cwd=cwd, input=text, env=env,
                              capture_output=True, text=True, timeout=30)

    def test_stop_hook_turns_a_wake_into_a_continue_decision(self):
        """The adapter finds the project from the payload, not from the hooks.json folder."""
        self.f.init()
        run = self.f.assign()
        self.f.fill_task(run / "T01-task.mdx")
        self.f.fill_task_report(run / "T01-report-01.mdx")
        self.f.cli("submit", "demo", "T01")
        self.f.cli("arm", "demo", "--role", "orchestrator")
        config = self.tmp / "gemini-config"
        config.mkdir()
        payload = {"conversationId": CONVERSATION, "workspacePaths": [str(self.root)],
                   "terminationReason": "NO_TOOL_CALL"}

        # An inherited CLAUDE_PROJECT_DIR names another project and must not win.
        woken = self.hook(payload, config, {"CLAUDE_PROJECT_DIR": str(config)})
        self.assertEqual(0, woken.returncode, woken.stderr)
        decision = json.loads(woken.stdout)
        self.assertEqual("continue", decision["decision"])
        self.assertIn("review batch ready", decision["reason"])

        quiet = self.hook(payload, config)
        self.assertEqual((0, {}), (quiet.returncode, json.loads(quiet.stdout)))

    def test_stop_hook_without_a_usable_workspace_stays_quiet(self):
        config = self.tmp / "gemini-config"
        config.mkdir()
        for payload in ("not json", {"workspacePaths": []}, {"workspacePaths": [str(self.tmp / "gone")]},
                        {"workspacePaths": "x"}):
            with self.subTest(payload=payload):
                result = self.hook(payload, config)
                self.assertEqual((0, {}), (result.returncode, json.loads(result.stdout)))

    def test_stop_hook_failure_never_reads_as_a_wake(self):
        """wake.sh's own failure exit stays a quiet stop, with its reason on stderr."""
        self.f.init5()
        self.f.cli("arm", "demo", "--role", "orchestrator")
        result = self.hook({"workspacePaths": [str(self.root)]}, self.root,
                           {"DOCKET_WATCH_TIMEOUT": "8h"})
        self.assertEqual((0, {}), (result.returncode, json.loads(result.stdout)))
        self.assertIn("docket wake hook failed (exit 2)", result.stderr)

    def test_doctor_recognizes_only_an_enabled_adapter_stop_hook(self):
        home = self.tmp / "home"
        config = home / ".gemini" / "config"
        config.mkdir(parents=True)
        adapter = {"type": "command", "command": f'"{AGY_HOOK}"', "timeout": 28800}
        env = {"HOME": str(home), "CODEX_HOME": str(home / ".codex")}
        for spec, wired in (
            ({"docket-wake": {"Stop": [adapter]}}, True),
            ({"docket-wake": {"enabled": False, "Stop": [adapter]}}, False),
            ({"docket-wake": {"Stop": [{**adapter, "command": str(fixtures.HOOK)}]}}, False),
            ({"docket-wake": {"Stop": [{**adapter, "type": "prompt"}]}}, False),
            ({"docket-wake": {"PreToolUse": [adapter]}}, False),
            ({"docket-wake": {"Stop": "bad"}}, False),
        ):
            with self.subTest(spec=spec):
                (config / "hooks.json").write_text(json.dumps(spec))
                result = self.f.cli_env(env, "doctor")
                self.assertEqual(wired, "Stop hook (Antigravity) in ~/.gemini/config/hooks.json"
                                 in result.stdout, result.stdout)
        (config / "hooks.json").unlink()
        project = self.root / ".agents"
        project.mkdir()
        (project / "hooks.json").write_text(json.dumps({"docket-wake": {"Stop": [adapter]}}))
        self.assertIn(f"Stop hook (Antigravity) in {project / 'hooks.json'}",
                      self.f.cli_env(env, "doctor").stdout)

    def test_hook_payload_names_an_antigravity_session(self):
        cli = fixtures.load_cli("docket_agy_payload_test")
        read, write = os.pipe()
        os.write(write, json.dumps({
            "conversationId": CONVERSATION,
            "transcriptPath": f"/home/u/.gemini/antigravity-cli/brain/{CONVERSATION}/x.jsonl"}).encode())
        os.close(write)
        saved = sys.stdin
        try:
            sys.stdin = os.fdopen(read)
            self.assertEqual({"session_id": CONVERSATION, "harness": "agy"}, cli.hook_input_ref())
        finally:
            sys.stdin.close()
            sys.stdin = saved

    def test_conductor_resumes_an_agy_worker_by_its_conversation(self):
        cli = fixtures.load_cli("docket_agy_resume_test")
        started = []
        cli.herdr = lambda *args: started.append(args) or {}
        cli.launch_args = lambda harness, model: ["--model", model]
        cli.publish_json = lambda path, record: None
        cli.fault = lambda name: None
        cli.start_worker(self.root, {"milestone": "M1", "role": "implementor",
                                     "requested_harness": "agy", "requested_model": "gemini-4-argon-high",
                                     "resume_native_id": CONVERSATION, "agent_name": "docket-x-impl",
                                     "pane_id": "w1:p1"})
        self.assertEqual(("agent", "start", "docket-x-impl", "--kind", "agy", "--pane", "w1:p1", "--",
                          "--model", "gemini-4-argon-high", "--conversation", CONVERSATION),
                         started[0])

    def test_agy_is_a_recordable_harness(self):
        self.f.init()
        run = self.root / ".docket" / "runs" / "demo"
        self.f.cli("assign", "demo", "T01", "--complexity", "high", "--executor", "implementor",
                   "--harness", "agy", "--file", "src/a.py", "--verify", "true")
        self.assertIn("harness: agy", (run / "T01-task.mdx").read_text())


if __name__ == "__main__":
    unittest.main()
