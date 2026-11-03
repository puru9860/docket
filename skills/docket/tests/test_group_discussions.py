"""Opt-in group discussions: roster, floor, proposal answers, delivery, and recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DOCKET = Path(os.environ.get("DOCKET_BIN") or Path(__file__).parents[1] / "bin/docket")
HOOK = DOCKET.parent.parent / "hooks/wake.sh"
PEERS = ("--peer", "astra:codex:gpt-6-astra", "--peer", "opus:claude:claude-opus-5-5")


class GroupDiscussions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("DOCKET_", "CLAUDE_", "CODEX_", "OPENCODE"))}
        self.env["DOCKET_FEEDBACK_LOG"] = "off"

    def cli(self, *args, ok=True, env=None):
        result = subprocess.run([sys.executable, str(DOCKET), "discuss", *args], cwd=self.root,
                                env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=15)
        if ok:
            self.assertEqual(0, result.returncode, result.stderr)
        return result

    def refused(self, *args, env=None):
        result = self.cli(*args, ok=False, env=env)
        self.assertNotEqual(0, result.returncode, result.stdout)
        return result

    def start(self, cid="G01", turns=12):
        self.cli(cid, "--start", "--topic", "Adopt pstack ideas?", "--message", "Briefing for everyone.",
                 *PEERS, "--max-turns", str(turns))
        for who in ("invoker", "astra", "opus"):
            self.cli(cid, "--join", "--as", who, "--session", who)

    def say(self, who, reply, body="A reasoned view", action="--send", cid="G01", session=None):
        return self.cli(cid, action, "--as", who, "--session", session or who,
                        "--reply-to", str(reply), "--message", body)

    def answer(self, who, proposal, action="--accept", body=None, cid="G01", ok=True, session=None):
        args = [cid, action, str(proposal), "--as", who, "--session", session or who]
        if body is not None:
            args += ["--message", body]
        return self.cli(*args) if ok else self.refused(*args)

    def view(self, cid="G01"):
        return json.loads(self.cli(cid, "--read", "--json").stdout)

    def messages(self, cid="G01"):
        return sorted((self.root / f".docket/conversations/{cid}/messages").glob("*.mdx"))

    def files(self):
        return {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_three_party_convergence_needs_every_explicit_acceptance(self):
        self.start()
        self.say("astra", 0, "Recipes first.")
        self.refused("G01", "--send", "--as", "invoker", "--session", "invoker",
                     "--reply-to", "1", "--message", "Out of turn")
        self.say("opus", 1, "Agreed, with a changed-oracle duty.")
        self.say("invoker", 2, "Recipes with a changed-oracle duty.", "--propose")
        pending = self.view()["pending"]
        self.assertEqual((3, "invoker", ["astra", "opus"]),
                         (pending["sequence"], pending["proposer"], pending["missing"]))
        self.answer("astra", 3)
        view = self.view()
        self.assertEqual(("open", ["opus"]), (view["status"], view["pending"]["missing"]),
                         "one acceptance of two is not a conclusion")
        self.answer("opus", 3)
        view = self.view()
        last = view["messages"][-1]
        self.assertEqual("concluded", view["status"])
        self.assertEqual(("unanimous", "invoker,astra,opus", "none", "invoker"),
                         (last["outcome"], last["accepted_by"], last["dissenters"], last["to"]))
        self.assertEqual(["astra", "opus", "invoker", "astra", "opus"], [m["sender"] for m in view["messages"]])
        self.assertEqual(("gpt-6-astra", "no"),
                         (view["messages"][0]["model_requested"], view["messages"][0]["harness_observed"]),
                         "a requested model is recorded as a request, never as observed")
        self.assertEqual("unanimous", view["conclusion"]["outcome"])
        self.assertFalse((self.root / ".docket/runs").exists())
        self.assertIn("G01  concluded  Adopt pstack ideas?", self.cli("--list").stdout)

    def test_named_dissent_concludes_and_unanimous_dissent_declines(self):
        self.start()
        self.say("astra", 0, "First proposal", "--propose")
        self.answer("astra", 1, ok=False)
        self.refused("G01", "--dissent", "1", "--as", "opus", "--session", "opus")
        self.answer("opus", 1, "--dissent", "Too broad.")
        self.answer("invoker", 1, "--dissent", "Also too broad.")
        view = self.view()
        self.assertEqual(("open", None, "astra"), (view["status"], view["pending"], view["floor"]))
        self.assertEqual(("declined", "opus,invoker"),
                         (view["messages"][-1]["outcome"], view["messages"][-1]["dissenters"]))
        self.say("astra", 3, "Narrower proposal", "--propose")
        self.answer("opus", 4)
        self.answer("invoker", 4, "--dissent", "I still prefer a pilot.")
        view = self.view()
        self.assertEqual(("concluded", "dissent", "invoker", "astra,opus"),
                         (view["status"], view["messages"][-1]["outcome"],
                          view["messages"][-1]["dissenters"], view["messages"][-1]["accepted_by"]))
        self.assertIn("I still prefer a pilot.", self.cli("G01", "--read").stdout)

    def test_later_input_and_stale_replies_withdraw_or_refuse(self):
        self.start()
        self.say("astra", 0, "Proposal", "--propose")
        self.answer("opus", 1)
        self.cli("G01", "--send", "--as", "human", "--reply-to", "2", "--message", "New constraint: no new roles.")
        self.answer("invoker", 1, ok=False)
        self.assertIsNone(self.view()["pending"], "human input withdraws a pending proposal")
        self.refused("G01", "--send", "--as", "invoker", "--session", "invoker",
                     "--reply-to", "2", "--message", "Old context")
        self.say("invoker", 3, "Revised under the constraint")
        self.say("astra", 4, "Second proposal", "--propose")
        self.say("opus", 5, "An objection instead of an answer")
        self.answer("invoker", 5, ok=False)
        self.refused("G01", "--accept", "5", "--as", "invoker", "--session", "invoker", "--reply-to", "5")

    def test_turn_cap_answers_human_pause_and_user_continue(self):
        self.start(turns=2)
        self.say("astra", 0)
        self.say("opus", 1, "Final proposal", "--propose")
        view = self.view()
        self.assertEqual(("paused", ["invoker", "astra"]), (view["status"], view["pending"]["missing"]))
        self.refused("G01", "--send", "--as", "invoker", "--session", "invoker", "--reply-to", "2",
                     "--message", "More talk")
        self.answer("invoker", 2)
        self.assertEqual("paused", self.view()["status"], "the cap is not consent from the missing member")
        self.answer("astra", 2)
        self.assertEqual("concluded", self.view()["status"])

        self.start("G02", turns=1)
        self.say("astra", 0, "Proposal at the cap", "--propose", cid="G02")
        for who in ("astra", "opus"):
            for action in ("--pause", "--continue", "--close"):
                self.refused("G02", action, "--as", who, "--session", who, "--message", "Take control")
        self.cli("G02", "--pause", "--as", "human", "--message", "Stop everyone.")
        self.answer("opus", 1, cid="G02", ok=False)
        self.answer("opus", 1, "--dissent", "No", cid="G02", ok=False)
        view = self.view("G02")
        self.assertEqual(("paused", None, []), (view["status"], view["pending"], view["actionable"]),
                         "a user pause stops the whole group and wakes nobody")
        self.cli("G02", "--continue", "--as", "human", "--message", "Discuss cost.")
        view = self.view("G02")
        self.assertEqual(("open", 2, "astra"), (view["status"], view["phase"], view["floor"]))
        self.say("astra", 3, "Cost view", cid="G02")

    def test_only_the_floor_holder_is_woken_and_receipts_are_per_recipient(self):
        self.start()
        self.say("astra", 0, "Secret peer prose")
        hook_env = {"DOCKET_DISCUSSION": "G01", "DOCKET_WATCH_TIMEOUT": "0"}

        def hook(who):
            return subprocess.run(["bash", str(HOOK)], cwd=self.root, capture_output=True, text=True,
                                  timeout=15, env={**self.env, **hook_env, "DOCKET_PARTICIPANT": who,
                                                   "DOCKET_DISCUSSION_SESSION": who})
        self.assertEqual(0, hook("invoker").returncode, "observing another member's message is not a turn")
        wake = hook("opus")
        self.assertEqual(2, wake.returncode, wake.stderr)
        self.assertIn("floor for opus at message 1", wake.stderr)
        self.assertIn("docket discuss G01 --pickup --as opus --session opus", wake.stderr)
        self.assertNotIn("Secret peer prose", wake.stderr)
        self.assertEqual(0, hook("opus").returncode, "the announcement lease prevents a duplicate wake")
        watch = self.cli("G01", "--watch", "--as", "invoker", "--session", "invoker", "--timeout", "0")
        self.assertEqual("", watch.stderr)
        delivery = self.root / ".docket/conversations/G01/.delivery"
        before = self.files()
        self.cli("G01", "--read")
        self.cli("G01", "--read", "--json")
        self.assertEqual(before, self.files(), "inspection consumes nothing")
        picked = self.cli("G01", "--pickup", "--as", "invoker", "--session", "invoker").stdout
        self.assertIn("Secret peer prose", picked)
        self.assertFalse((delivery / "opus-receipt.json").exists(), "one member's pickup is not another's receipt")
        picked = self.cli("G01", "--pickup", "--as", "opus", "--session", "opus").stdout
        self.assertIn("Briefing for everyone.", picked)
        self.assertIn("floor opus", picked)
        self.assertEqual(0, hook("opus").returncode, "a received message is not announced again")
        self.say("opus", 1, "Opus proposal", "--propose")
        self.answer("invoker", 2)
        self.assertEqual(2, hook("astra").returncode)
        self.answer("astra", 2)
        self.assertEqual(0, hook("opus").returncode, "a conclusion does not wake every peer")
        report = hook("invoker")
        self.assertEqual(2, report.returncode, report.stderr)
        self.assertIn("concluded at message 4, for invoker to report", report.stderr)
        self.cli("G01", "--pickup", "--as", "invoker", "--session", "invoker")
        self.cli("G01", "--join", "--as", "astra", "--session", "astra-2", "--replace")
        self.assertEqual(0, hook("invoker").returncode, "a replacement event alone wakes nobody")

    def test_replacement_withdrawing_a_proposal_at_the_cap_wakes_only_the_invoker_once(self):
        self.start(turns=1)
        self.say("astra", 0, "Proposal at the cap", "--propose")
        self.cli("G01", "--pickup", "--as", "invoker", "--session", "invoker")
        self.cli("G01", "--pickup", "--as", "opus", "--session", "opus")
        hook_env = {"DOCKET_DISCUSSION": "G01", "DOCKET_WATCH_TIMEOUT": "0"}

        def hook(who, session=None):
            return subprocess.run(["bash", str(HOOK)], cwd=self.root, capture_output=True, text=True,
                                  timeout=15, env={**self.env, **hook_env, "DOCKET_PARTICIPANT": who,
                                                   "DOCKET_DISCUSSION_SESSION": session or who})
        self.assertEqual(0, hook("opus").returncode, "the floor holder already picked up the proposal")
        self.cli("G01", "--join", "--as", "astra", "--session", "astra-2", "--replace")
        view = self.view()
        self.assertEqual(("paused", None, ["invoker"]), (view["status"], view["pending"], view["actionable"]))
        self.answer("opus", 1, ok=False)
        self.assertEqual(0, hook("opus").returncode, "a withdrawn proposal leaves opus nothing to answer")
        wake = hook("invoker")
        self.assertEqual(2, wake.returncode, "the invoker must learn the cap proposal was withdrawn")
        self.assertIn("paused at message 2, for invoker to report", wake.stderr)
        self.assertNotIn("Proposal at the cap", wake.stderr)
        self.assertEqual(0, hook("invoker").returncode, "the announcement lease prevents a duplicate wake")
        picked = self.cli("G01", "--pickup", "--as", "invoker", "--session", "invoker").stdout
        self.assertIn("kind=generation", picked)
        notice = self.root / ".docket/conversations/G01/.delivery/invoker-announce.json"
        record = json.loads(notice.read_text())
        record["lease_until"] = 0
        notice.write_text(json.dumps(record))
        self.assertEqual(0, hook("invoker").returncode, "a received recovery notice is not repeated")
        self.assertEqual(0, hook("astra", "astra-2").returncode, "the replacement itself is not the invoker")
        self.assertEqual(1, hook("astra").returncode, "the replaced binding fails instead of waking")
        self.assertEqual("paused", self.view()["status"], "nothing concluded without the members' answers")

    def test_replacement_withdraws_old_acceptance_and_refuses_the_stale_writer(self):
        self.start()
        self.say("astra", 0, "Proposal", "--propose")
        self.answer("opus", 1)
        self.refused("G01", "--join", "--as", "opus", "--session", "opus-2")
        self.refused("G01", "--join", "--as", "opus", "--session", "astra", "--replace")
        self.cli("G01", "--join", "--as", "opus", "--session", "opus-2", "--replace")
        view = self.view()
        self.assertEqual(("generation", "opus", "2", "1"),
                         (view["messages"][-1]["kind"], view["messages"][-1]["sender"],
                          view["messages"][-1]["generation"], view["messages"][-1]["withdrew"]))
        hook_env = {**self.env, "DOCKET_DISCUSSION": "G01", "DOCKET_WATCH_TIMEOUT": "0"}
        for who, code in (("astra", 0), ("invoker", 2)):
            woke = subprocess.run(["bash", str(HOOK)], cwd=self.root, capture_output=True, text=True, timeout=15,
                                  env={**hook_env, "DOCKET_PARTICIPANT": who, "DOCKET_DISCUSSION_SESSION": who})
            self.assertEqual(code, woke.returncode, f"only the floor holder learns of the withdrawal: {who}")
        self.answer("invoker", 1, ok=False)
        self.refused("G01", "--pickup", "--as", "opus", "--session", "opus")
        picked = self.cli("G01", "--pickup", "--as", "opus", "--session", "opus-2").stdout
        self.assertIn("Briefing for everyone.", picked)
        self.assertIn("Proposal", picked)
        self.say("invoker", 3, "Restated proposal", "--propose")
        self.answer("astra", 4)
        self.answer("opus", 4, session="opus-2")
        self.assertEqual("unanimous", self.view()["messages"][-1]["outcome"])

    def test_interrupted_replacement_cannot_settle_old_acceptance_and_retry_recovers_once(self):
        self.start()
        self.say("astra", 0, "Proposal", "--propose")
        self.answer("opus", 1)
        fault = {"DOCKET_FAULT": "discuss:after-group-registration"}
        crashed = self.cli("G01", "--join", "--as", "opus", "--session", "opus-2", "--replace",
                           ok=False, env=fault)
        self.assertEqual(70, crashed.returncode, crashed.stderr)
        registration = self.root / ".docket/conversations/G01/participants/opus.mdx"
        self.assertIn("generation: 2", registration.read_text())
        self.assertEqual(2, len(self.messages()), "the interruption left no transcript event")
        # The old-generation acceptance must not count, with or without the event.
        self.answer("invoker", 1, ok=False)
        view = self.view()
        self.assertEqual((None, ["opus"]), (view["pending"], view["replaced"]))
        self.refused("G01", "--pickup", "--as", "opus", "--session", "opus")
        for _ in range(2):
            self.cli("G01", "--join", "--as", "opus", "--session", "opus-2")
        events = [m for m in self.view()["messages"] if m["kind"] == "generation"]
        self.assertEqual([("opus", "opus-2", "2", "1")],
                         [(m["sender"], m["session"], m["generation"], m.get("withdrew")) for m in events],
                         "retry records exactly one replacement and the proposal it withdrew")
        self.assertIn("generation: 2", registration.read_text())
        self.answer("invoker", 1, ok=False)
        self.say("invoker", 3, "Restated proposal", "--propose")
        self.answer("astra", 4)
        self.answer("opus", 4, session="opus-2")
        self.assertEqual("concluded", self.view()["status"])

        self.start("G02")
        self.say("astra", 0, cid="G02")
        self.cli("G02", "--join", "--as", "opus", "--session", "opus-2", "--replace", ok=False, env=fault)
        refused = self.refused("G02", "--send", "--as", "opus", "--session", "opus-2", "--reply-to", "1",
                               "--message", "Before recovery")
        self.assertIn("--join --as opus --session opus-2", refused.stderr)
        self.cli("G02", "--join", "--as", "opus", "--session", "opus-2")
        self.say("opus", 2, "After recovery", cid="G02", session="opus-2")

    def test_two_party_join_and_replace_ignore_the_group_seam(self):
        self.cli("C01", "--start", "--topic", "Topic", "--message", "Context", "--harness", "claude")
        for who in ("invoker", "peer"):
            self.cli("C01", "--join", "--as", who, "--session", who)
        self.cli("C01", "--join", "--as", "peer", "--session", "peer-2", "--replace",
                 env={"DOCKET_FAULT": "discuss:after-group-registration"})
        meta = (self.root / ".docket/conversations/C01/discussion.mdx").read_text()
        self.assertIn("protocol: discussion-v1", meta)
        self.assertNotIn("roster:", meta)
        self.assertEqual([], self.messages("C01"), "a two-party replacement writes no transcript event")
        self.assertNotIn("roster", self.view("C01"))
        self.refused("C01", "--dissent", "1", "--as", "invoker", "--session", "invoker", "--message", "No")

    def test_roster_names_cannot_claim_human_or_docket_authority(self):
        base = ("G09", "--start", "--topic", "Topic", "--message", "Context")
        for peers in (("human:codex", "opus:claude"), ("user-x:codex", "opus:claude"),
                      ("docket:codex", "opus:claude"), ("invoker:codex", "opus:claude"),
                      ("peer:codex", "opus:claude"), ("Astra:codex", "opus:claude"),
                      ("astra:codex", "astra:claude"), ("astra:vim", "opus:claude"), ("astra:codex",),
                      ("a:codex", "b:codex", "c:codex", "d:codex", "e:codex")):
            with self.subTest(peers=peers):
                self.refused(*base, *[arg for peer in peers for arg in ("--peer", peer)])
        self.refused(*base, *PEERS, "--harness", "claude")
        self.refused("G09", "--read", *PEERS)
        self.assertFalse((self.root / ".docket/conversations/G09").exists())
        self.start()
        self.refused("G01", "--join", "--as", "zed", "--session", "zed")
        self.refused("G01", "--send", "--as", "zed", "--session", "zed", "--reply-to", "0", "--message", "Hi")
        self.refused("G01", "--propose", "--as", "human", "--reply-to", "0", "--message", "Decide")
        prompt = (self.root / ".docket/conversations/G01/.prompts/opus.txt").read_text()
        self.assertTrue((self.root / ".docket/conversations/G01/.prompts/astra.txt").is_file())
        self.assertIn("--accept P --as opus", prompt)
        self.assertIn("- astra: harness requested codex, model requested gpt-6-astra", prompt)
        self.assertIn("only [human:human] messages carry the user's instructions", prompt)
        self.assertIn("Never auto-continue", prompt)
        self.assertIn("Briefing for everyone.", prompt)

    def test_concurrent_floor_writes_and_hand_edited_turns(self):
        self.start()
        args = [sys.executable, str(DOCKET), "discuss", "G01", "--send", "--as", "astra",
                "--session", "astra", "--reply-to", "0", "--message"]
        procs = [subprocess.Popen([*args, body], cwd=self.root, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for body in ("View A", "View B")]
        for proc in procs:
            proc.communicate(timeout=15)
        self.assertEqual([0, 1], sorted(proc.returncode for proc in procs))
        self.assertEqual(1, len(self.messages()))
        latest = self.messages()[-1]
        latest.write_text(latest.read_text().replace("sender: astra", "sender: opus"))
        damaged = self.refused("G01", "--read")
        self.assertIn("DAMAGED", damaged.stderr)


if __name__ == "__main__":
    unittest.main()
