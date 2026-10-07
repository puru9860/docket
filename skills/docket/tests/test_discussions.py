"""Durable, attributed dialogue outside the coding lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DISCUSSION_BIN = Path(os.environ.get("DOCKET_BIN") or Path(__file__).parents[1] / "bin/docket")
DISCUSSION_HOOK = DISCUSSION_BIN.parent.parent / "hooks/wake.sh"


class Discussions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("DOCKET_", "CLAUDE_", "CODEX_", "OPENCODE"))}
        self.env["DOCKET_FEEDBACK_LOG"] = "off"

    def cli(self, *args, ok=True, env=None):
        result = subprocess.run([sys.executable, str(DISCUSSION_BIN), "discuss", *args],
                                cwd=self.root, env={**self.env, **(env or {})},
                                capture_output=True, text=True, timeout=15)
        if ok:
            self.assertEqual(0, result.returncode, result.stderr)
        return result

    def start(self, turns=12):
        self.cli("C01", "--start", "--topic", "Should this product work offline?",
                 "--message", "Our users often lose connectivity.",
                 "--harness", "claude", "--model", "peer-model", "--max-turns", str(turns))
        for who in ("invoker", "peer"):
            self.cli("C01", "--join", "--as", who, "--session", who)

    def send(self, who, reply, body="A reasoned response", action="--send"):
        return self.cli("C01", action, "--as", who, "--session", who,
                        "--reply-to", str(reply), "--message", body)

    def test_multi_turn_dialogue_and_agreed_conclusion(self):
        self.start()
        self.send("peer", 0, "Offline support adds synchronization complexity.")
        self.send("invoker", 1, "Could a read-only cache address that concern?")
        self.send("peer", 2, "Yes, and keep writes online.", "--propose")
        self.cli("C01", "--accept", "3", "--as", "invoker", "--session", "invoker")
        view = json.loads(self.cli("C01", "--read", "--json").stdout)
        self.assertEqual("concluded", view["status"])
        self.assertEqual(["peer", "invoker", "peer", "invoker"],
                         [m["sender"] for m in view["messages"]])
        self.assertEqual("proposal", view["messages"][2]["kind"])
        self.assertEqual("agent", view["messages"][0]["sender_kind"])
        self.assertFalse((self.root / ".docket/runs").exists())

    def test_markers_distinguish_agent_and_human_and_read_consumes_nothing(self):
        self.start()
        self.send("peer", 0, "Consider local caching.")
        self.cli("C01", "--send", "--as", "human", "--reply-to", "1",
                 "--message", "Budget is limited.")
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        output = self.cli("C01", "--read").stdout
        self.assertIn("[agent:peer]", output)
        self.assertIn("[human:human]", output)
        self.assertIn("Budget is limited.", output)
        self.cli("C01", "--read", "--json")
        after = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.cli("C01", "--pause", "--as", "invoker", "--session", "invoker",
                 "--message", "The user requested an interruption.")
        pickup = self.cli("C01", "--pickup", "--as", "invoker", "--session", "invoker")
        self.assertIn("Consider local caching.", pickup.stdout, "pausing does not consume an unread peer reply")
        self.assertIn("Budget is limited.", pickup.stdout)

    def test_stale_reply_and_false_consensus_are_refused(self):
        self.start()
        self.send("peer", 0)
        bad = self.cli("C01", "--send", "--as", "invoker", "--session", "invoker",
                       "--reply-to", "0", "--message", "Old context", ok=False)
        self.assertNotEqual(0, bad.returncode)
        self.assertIn("reply-to", bad.stderr)
        bad = self.cli("C01", "--accept", "1", "--as", "invoker",
                       "--session", "invoker", ok=False)
        self.assertNotEqual(0, bad.returncode)
        self.send("invoker", 1, "Proposal", "--propose")
        bad = self.cli("C01", "--accept", "2", "--as", "invoker",
                       "--session", "invoker", ok=False)
        self.assertNotEqual(0, bad.returncode)
        self.assertEqual("open", json.loads(self.cli("C01", "--read", "--json").stdout)["status"])

    def test_turn_limit_pauses_and_continue_preserves_history(self):
        self.start(turns=2)
        self.send("peer", 0)
        self.send("invoker", 1)
        self.assertEqual("paused", json.loads(self.cli("C01", "--read", "--json").stdout)["status"])
        refused = self.cli("C01", "--send", "--as", "peer", "--session", "peer",
                           "--reply-to", "2", "--message", "Keep going", ok=False)
        self.assertNotEqual(0, refused.returncode)
        first = self.root / ".docket/conversations/C01/messages/000001.mdx"
        frozen = first.read_bytes()
        self.send("invoker", 2, "Focus next on cost.", "--continue")
        self.send("peer", 3)
        self.assertEqual(frozen, first.read_bytes())

    def test_recipient_watch_pickup_and_generation_replacement(self):
        self.start()
        self.send("peer", 0)
        watch = self.cli("C01", "--watch", "--as", "invoker", "--session", "invoker",
                         "--timeout", "0", ok=False)
        self.assertEqual(2, watch.returncode, watch.stderr)
        self.assertIn("[agent:peer]", watch.stderr)
        self.cli("C01", "--pickup", "--as", "invoker", "--session", "invoker")
        watch = self.cli("C01", "--watch", "--as", "invoker", "--session", "invoker",
                         "--timeout", "0", ok=False)
        self.assertEqual(0, watch.returncode, watch.stderr)
        refused = self.cli("C01", "--join", "--as", "invoker", "--session", "replacement", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.cli("C01", "--join", "--as", "invoker", "--session", "replacement", "--replace")
        refused = self.cli("C01", "--pickup", "--as", "invoker", "--session", "invoker", ok=False)
        self.assertNotEqual(0, refused.returncode)
        pickup = self.cli("C01", "--pickup", "--as", "invoker", "--session", "replacement")
        self.assertIn("A reasoned response", pickup.stdout)

    def test_native_hook_wakes_only_for_selected_participant_without_coding_role(self):
        self.start()
        self.send("peer", 0)
        env = {**self.env, "DOCKET_DISCUSSION": "C01", "DOCKET_PARTICIPANT": "invoker",
               "DOCKET_DISCUSSION_SESSION": "invoker", "DOCKET_WATCH_TIMEOUT": "0"}
        hook = subprocess.run(["bash", str(DISCUSSION_HOOK)], cwd=self.root,
                              env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(2, hook.returncode, hook.stderr)
        self.assertIn("docket discuss C01 --pickup", hook.stderr)
        self.assertNotIn("A reasoned response", hook.stderr, "hook notices contain pointers, not peer prose")
        self.cli("C01", "--pickup", "--as", "invoker", "--session", "invoker")
        hook = subprocess.run(["bash", str(DISCUSSION_HOOK)], cwd=self.root,
                              env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, hook.returncode, hook.stderr)

    def test_concurrent_replies_leave_one_immutable_message(self):
        self.start()
        args = [sys.executable, str(DISCUSSION_BIN), "discuss", "C01", "--send",
                "--as", "peer", "--session", "peer", "--reply-to", "0", "--message"]
        procs = [subprocess.Popen([*args, body], cwd=self.root, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for body in ("View A", "View B")]
        for proc in procs:
            proc.communicate(timeout=15)
        self.assertEqual([0, 1], sorted(proc.returncode for proc in procs))
        messages = json.loads(self.cli("C01", "--read", "--json").stdout)["messages"]
        self.assertEqual(1, len(messages))
        self.assertIn(messages[0]["body"].strip(), ("View A", "View B"))

    def test_invalid_paths_and_missing_context_publish_nothing(self):
        bad = self.cli("../escape", "--start", "--topic", "Topic", "--harness", "claude", ok=False)
        self.assertNotEqual(0, bad.returncode)
        bad = self.cli("C01", "--start", "--topic", "Topic", "--harness", "claude",
                       "--message-file", "missing.txt", ok=False)
        self.assertNotEqual(0, bad.returncode)
        self.assertFalse((self.root / ".docket/conversations/C01/discussion.mdx").exists())

    def test_invalid_start_session_publishes_nothing_and_corrected_retry_succeeds(self):
        args = ("C01", "--start", "--topic", "Topic", "--message", "Context", "--harness", "claude")
        refused = self.cli(*args, "--session", "invalid/session", ok=False)
        self.assertNotEqual(0, refused.returncode)
        discussion = self.root / ".docket/conversations/C01"
        self.assertFalse(discussion.exists(), "validate the session before creating any discussion artifacts")
        self.cli(*args, "--session", "invoker")
        self.assertTrue((discussion / ".prompts/peer.txt").is_file())
        self.assertIn("session: invoker", (discussion / "participants/invoker.mdx").read_text())

    def test_prompt_invites_replies_and_resumable_visible_session(self):
        self.start()
        prompt = self.cli("C01", "--prompt", "--as", "peer").stdout
        self.assertIn("docket help discussion", prompt)
        self.assertIn("--pickup", prompt)
        self.assertIn("--reply-to", prompt)
        self.assertIn("--propose", prompt)
        self.assertIn("--accept", prompt)
        self.assertIn("Our users often lose connectivity.", prompt)

    def test_prompt_allows_acceptance_of_latest_peer_proposal_at_turn_limit(self):
        self.start(turns=1)
        self.send("peer", 0, "Final proposal", "--propose")
        self.assertEqual("paused", json.loads(self.cli("C01", "--read", "--json").stdout)["status"])
        prompt = self.cli("C01", "--prompt", "--as", "invoker").stdout
        self.assertIn("turn limit", prompt)
        self.assertIn("other agent's latest proposal", prompt)
        self.assertIn("may accept", prompt)
        self.assertIn("Do not use --send or --propose while paused", prompt)
        self.assertNotIn("If paused, concluded, or closed, end your turn.", prompt)
        self.cli("C01", "--accept", "1", "--as", "invoker", "--session", "invoker")
        self.assertEqual("concluded", json.loads(self.cli("C01", "--read", "--json").stdout)["status"])

    def test_user_interrupts_and_reopens_an_agreed_conclusion(self):
        self.start()
        self.send("peer", 0, "Initial recommendation", "--propose")
        self.cli("C01", "--accept", "1", "--as", "invoker", "--session", "invoker")
        agreed = (self.root / ".docket/conversations/C01/messages/000002.mdx").read_bytes()
        self.cli("C01", "--continue", "--as", "human", "--message", "Consider the cost.")
        self.send("peer", 3, "A cheaper option")
        self.cli("C01", "--pause", "--as", "human", "--message", "Stop for now.")
        refused = self.cli("C01", "--send", "--as", "invoker", "--session", "invoker",
                           "--reply-to", "5", "--message", "Continue anyway", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.cli("C01", "--continue", "--as", "human", "--message", "Focus on maintenance.")
        self.send("peer", 6, "Second recommendation", "--propose")
        self.cli("C01", "--accept", "7", "--as", "invoker", "--session", "invoker")
        view = json.loads(self.cli("C01", "--read", "--json").stdout)
        self.assertEqual(("concluded", 3), (view["status"], view["phase"]))
        self.assertEqual(agreed, (self.root / ".docket/conversations/C01/messages/000002.mdx").read_bytes())
        self.assertIn("Initial recommendation", view["messages"][1]["body"])
        self.assertIn("Second recommendation", view["messages"][-1]["body"])

    def test_accept_at_turn_cap_but_not_after_human_interruption(self):
        self.start(turns=1)
        self.send("peer", 0, "Final proposal", "--propose")
        self.cli("C01", "--accept", "1", "--as", "invoker", "--session", "invoker")
        self.cli("C01", "--continue", "--as", "human", "--message", "Revisit it.")
        self.send("peer", 3, "Another proposal", "--propose")
        # Human input invalidates the exact latest proposal rather than silently accepting it.
        refused = self.cli("C01", "--accept", "1", "--as", "invoker", "--session", "invoker", ok=False)
        self.assertNotEqual(0, refused.returncode)

    def test_user_pause_at_turn_limit_records_interruption_and_blocks_acceptance(self):
        self.start(turns=1)
        seq = 0
        for who in ("human", "invoker"):
            with self.subTest(sender=who):
                self.send("peer", seq, "Final proposal", "--propose")
                proposal = self.root / f".docket/conversations/C01/messages/{seq + 1:06d}.mdx"
                original = proposal.read_bytes()
                self.cli("C01", "--pause", "--as", who, "--session", who,
                         "--message", "The user asked to stop.")
                view = json.loads(self.cli("C01", "--read", "--json").stdout)
                self.assertEqual("paused", view["status"])
                interruption = view["messages"][-1]
                self.assertEqual((seq + 2, "pause", who, "both"),
                                 (interruption["sequence"], interruption["kind"],
                                  interruption["sender"], interruption["to"]))
                self.assertEqual("The user asked to stop.", interruption["body"].strip())
                self.assertEqual(original, proposal.read_bytes())
                for target in (seq + 1, seq + 2):
                    refused = self.cli("C01", "--accept", str(target), "--as", "invoker",
                                       "--session", "invoker", ok=False)
                    self.assertNotEqual(0, refused.returncode)
                for action in ("--send", "--propose"):
                    refused = self.cli("C01", action, "--as", "invoker", "--session", "invoker",
                                       "--reply-to", str(seq + 2), "--message", "Ignore pause", ok=False)
                    self.assertNotEqual(0, refused.returncode)
                unchanged = json.loads(self.cli("C01", "--read", "--json").stdout)
                self.assertEqual(view, unchanged, "refused replies must leave the interruption intact")
                self.cli("C01", "--continue", "--as", "human", "--message", "Discuss again.")
                seq += 3
        self.send("peer", seq, "Revised proposal", "--propose")
        self.cli("C01", "--accept", str(seq + 1), "--as", "invoker", "--session", "invoker")
        self.assertEqual("concluded", json.loads(self.cli("C01", "--read", "--json").stdout)["status"])

    def test_peer_cannot_control_discussion_or_impersonate_invoker_session(self):
        self.start()
        for action in ("--pause", "--continue", "--close"):
            result = self.cli("C01", action, "--as", "peer", "--session", "peer",
                              "--message", "Take control", ok=False)
            self.assertNotEqual(0, result.returncode)
        refused = self.cli("C01", "--join", "--as", "peer", "--session", "invoker", "--replace", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.send("peer", 0)
        refused = self.cli("C01", "--send", "--as", "peer", "--session", "peer",
                           "--reply-to", "1", "--message", "Monologue", ok=False)
        self.assertNotEqual(0, refused.returncode)

    def test_closed_discussion_and_missing_history_refuse_mutation(self):
        self.start()
        self.send("peer", 0)
        self.cli("C01", "--close", "--as", "human", "--message", "Finished.")
        refused = self.cli("C01", "--continue", "--as", "human", "--message", "Restart", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertEqual("closed", json.loads(self.cli("C01", "--read", "--json").stdout)["status"])
        first = self.root / ".docket/conversations/C01/messages/000001.mdx"
        first.unlink()
        refused = self.cli("C01", "--read", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("DAMAGED", refused.stderr)

    def test_message_file_preserves_code_indentation_and_private_permissions(self):
        self.start()
        body = "    indented code\n\n```python\nprint('hello')\n```\n"
        path = self.root / "reply.txt"
        path.write_text(body)
        self.cli("C01", "--send", "--as", "peer", "--session", "peer",
                 "--reply-to", "0", "--message-file", str(path))
        view = json.loads(self.cli("C01", "--read", "--json").stdout)
        self.assertEqual(body, view["messages"][0]["body"])
        self.assertEqual(0, (self.root / ".docket/conversations").stat().st_mode & 0o077)
        self.assertEqual("no", view["messages"][0]["harness_observed"])
        public = self.root / "conversations"
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        project = public / "project"
        (project / ".docket").mkdir(parents=True)
        done = subprocess.run([sys.executable, str(DISCUSSION_BIN), "discuss", "C02", "--start",
                               "--topic", "Topic", "--message", "Context", "--harness", "claude"],
                              cwd=project, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(0o755, public.stat().st_mode & 0o777,
                         "private transcript permissions must not change an unrelated ancestor")

    def test_native_notice_is_leased_retryable_and_invalid_hook_never_wakes(self):
        self.start()
        self.send("peer", 0)
        env = {**self.env, "DOCKET_DISCUSSION": "C01", "DOCKET_PARTICIPANT": "invoker",
               "DOCKET_DISCUSSION_SESSION": "invoker", "DOCKET_WATCH_TIMEOUT": "0"}
        def hook(extra=None):
            return subprocess.run(["bash", str(DISCUSSION_HOOK)], cwd=self.root,
                                  env={**env, **(extra or {})}, capture_output=True, text=True, timeout=15)
        self.assertEqual(2, hook().returncode)
        self.assertEqual(0, hook().returncode, "concurrent or repeated announcements wait for the lease")
        notice = self.root / ".docket/conversations/C01/.delivery/invoker-announce.json"
        record = json.loads(notice.read_text())
        record["lease_until"] = 0
        notice.write_text(json.dumps(record))
        self.assertEqual(2, hook().returncode, "a lost announcement can retry")
        failed = hook({"DOCKET_WATCH_TIMEOUT": "invalid"})
        self.assertEqual(1, failed.returncode)
        self.assertTrue((self.root / ".docket/hook-failure-discussion").exists())
        self.cli("C01", "--pickup", "--as", "invoker", "--session", "invoker")
        self.assertEqual(0, hook().returncode)
        self.assertFalse((self.root / ".docket/hook-failure-discussion").exists())

    @unittest.skipUnless(Path("/proc/self/stat").is_file(), "native start identity requires Linux procfs")
    def test_existing_native_session_auto_joins_and_wakes_without_role_or_env_selection(self):
        wrapper = self.root / "codex"
        wrapper.symlink_to(sys.executable)
        script = '''
import json, os, subprocess, sys
binary, hook = sys.argv[1:]
native = dict(os.environ)
clean = {k: v for k, v in native.items() if not k.startswith("CODEX_")}
def cli(args, env=native):
    p = subprocess.run([sys.executable, binary, 'discuss', 'C01', *args], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p
cli(['--start', '--topic', 'Topic', '--message', 'Context', '--harness', 'claude'])
cli(['--join', '--as', 'peer', '--session', 'peer'], clean)
cli(['--send', '--as', 'peer', '--session', 'peer', '--reply-to', '0', '--message', 'Peer reply'], clean)
wrong = subprocess.run(['bash', hook], env={**native, 'CODEX_THREAD_ID': 'different', 'DOCKET_WATCH_TIMEOUT': '0'}, capture_output=True, text=True)
assert wrong.returncode == 0, wrong.stderr
wake = subprocess.run(['bash', hook], env={**native, 'DOCKET_WATCH_TIMEOUT': '0'}, capture_output=True, text=True)
assert wake.returncode == 2, wake.stderr
assert '[agent:peer]' in wake.stderr
cli(['--pickup', '--as', 'invoker'])
done = subprocess.run(['bash', hook], env={**native, 'DOCKET_WATCH_TIMEOUT': '0'}, capture_output=True, text=True)
assert done.returncode == 0, done.stderr
short = subprocess.run([sys.executable, binary, 'discuss', 'C01', '--watch', '--as', 'invoker', '--timeout', '1'], env=native, capture_output=True, text=True)
assert short.returncode == 1 and '590' in short.stderr, short.stderr
print('native dialogue received')
'''
        result = subprocess.run([str(wrapper), "-c", script, str(DISCUSSION_BIN), str(DISCUSSION_HOOK)],
                                cwd=self.root, env={**self.env, "CODEX_THREAD_ID": "discussion-session"},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("native dialogue received", result.stdout)

    def test_coding_hook_receives_discussion_without_consuming_role_events(self):
        self.start()
        self.send("peer", 0)
        for args in (("init", "R01", "--mode", "standard", "--evidence-mode", "documents-only"),
                     ("arm", "R01", "--role", "planner")):
            result = subprocess.run([sys.executable, str(DISCUSSION_BIN), *args], cwd=self.root,
                                    env=self.env, capture_output=True, text=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr)
        run = self.root / ".docket/runs/R01"
        before = {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()}
        env = {**self.env, "DOCKET_ROLE": "planner", "DOCKET_DISCUSSION": "C01",
               "DOCKET_PARTICIPANT": "invoker", "DOCKET_DISCUSSION_SESSION": "invoker",
               "DOCKET_WATCH_TIMEOUT": "0"}
        result = subprocess.run(["bash", str(DISCUSSION_HOOK)], cwd=self.root, env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Docket peer conversation", result.stderr)
        after = {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    @unittest.skipUnless(Path("/proc/self/stat").is_file(), "native start identity requires Linux procfs")
    def test_opencode_binding_ignores_another_sessions_newer_running_command(self):
        script = '''
import json, os, pathlib, shlex, sqlite3, subprocess, sys, time
binary, scenario = sys.argv[1:]
home = pathlib.Path.cwd() / 'data'
database = home / 'opencode/opencode.db'
database.parent.mkdir(parents=True)
con = sqlite3.connect(database)
con.executescript('CREATE TABLE session (id TEXT, version TEXT, time_updated INTEGER); CREATE TABLE part (session_id TEXT, data TEXT, time_updated INTEGER);')
now = int(time.time() * 1000)
args = ['discuss', 'C01', '--start', '--topic', 'Topic with spaces', '--message', 'Context', '--harness', 'claude']
ours = shlex.join(['docket', *args])
foreign = ours if scenario == 'ambiguous' else 'docket status R99'
if scenario == 'prefix-variable':
    ours = ours.replace(' C01 ', ' "$CID" ', 1)
    foreign = shlex.join(['docket', *[arg if arg != 'C01' else 'C010' for arg in args]])
elif scenario == 'python':
    ours = shlex.join([sys.executable, binary, *args])
elif scenario == 'compound':
    ours += '; echo done'
elif scenario == 'malformed':
    ours += ' "'
for sid, command, timestamp in [('ours', ours, now), ('foreign', foreign, now + 1)]:
    con.execute('INSERT INTO session VALUES (?, ?, ?)', (sid, 'test', timestamp))
    part = {'type': 'tool', 'state': {'status': 'running', 'input': {'command': command}}}
    con.execute('INSERT INTO part VALUES (?, ?, ?)', (sid, json.dumps(part), timestamp))
con.commit()
env = dict(os.environ, OPENCODE_PID=str(os.getpid()), XDG_DATA_HOME=str(home))
p = subprocess.run([sys.executable, binary, *args], env=env, capture_output=True, text=True)
assert p.returncode == 0, p.stderr
path = pathlib.Path('.docket/conversations/C01/participants/invoker.mdx')
if scenario in ('ambiguous', 'prefix-variable', 'compound', 'malformed'):
    assert not path.exists(), 'Unproven sessions must not auto-join: ' + path.read_text()
    manual = subprocess.run([sys.executable, binary, 'discuss', 'C01', '--join', '--as', 'invoker', '--session', 'manual'], env=env, capture_output=True, text=True)
    assert manual.returncode == 0, manual.stderr
    record = path.read_text()
    assert 'session: manual' in record and 'native_session:' not in record, record
else:
    record = path.read_text()
    assert 'native_session: ours' in record, record
'''
        for scenario in ("distinct", "python", "ambiguous", "prefix-variable", "compound", "malformed"):
            with self.subTest(scenario=scenario):
                work = self.root / scenario
                work.mkdir()
                done = subprocess.run([sys.executable, "-c", script, str(DISCUSSION_BIN), scenario],
                                      cwd=work, env=self.env, capture_output=True, text=True, timeout=15)
                self.assertEqual(0, done.returncode, done.stderr)

    def test_shared_playbook_is_available(self):
        done = subprocess.run([sys.executable, str(DISCUSSION_BIN), "help", "discussion"], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("# Peer discussions", done.stdout)
        self.assertIn("--continue", done.stdout)
        skill = (DISCUSSION_BIN.parent.parent / "SKILL.md").read_text()
        self.assertIn("docket help discussion", skill)
        self.assertIn("docket discuss --list", skill)
