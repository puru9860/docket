import importlib.util
import json
import tempfile
import unittest
import contextlib
import io
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("inspector", Path(__file__).parents[1] / "inspection.py")
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.run = self.workspace / ".docket/runs/R01"
        self.run.mkdir(parents=True)
        self.plan()

    def plan(self, extra=""):
        (self.run / "plan.mdx").write_text(
            "---\nworkflow: five-role-v1\nmode: quick\ntopology: split\n"
            "role_sessions: planner, implementor, reviewer\n"
            "review_policy: quick-milestone-reviewer\nstatus: draft\n" + extra +
            "---\n\n# Plan: A browser spike\n")

    def test_inspection_uses_actual_quick_roles_and_does_not_write(self):
        before = {p.relative_to(self.workspace): p.read_bytes()
                  for p in self.workspace.rglob("*") if p.is_file()}
        result = inspector.snapshot(self.workspace)
        self.assertEqual([s["role"] for s in result["runs"][0]["slots"]],
                         ["planner", "implementor", "reviewer"])
        inspector.document(self.workspace, "runs/R01/plan.mdx")
        after = {p.relative_to(self.workspace): p.read_bytes()
                 for p in self.workspace.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_numeric_latest_report_is_100_not_99(self):
        (self.run / "T01-task.mdx").write_text("---\ntitle: Task\n---\n")
        for number, status in ((99, "approved"), (100, "blocked")):
            (self.run / f"T01-report-{number}.mdx").write_text(f"---\nstatus: {status}\n---\n")
        task = inspector.snapshot(self.workspace)["runs"][0]["tasks"][0]
        self.assertEqual(task["status"], "blocked")
        self.assertTrue(task["document"].endswith("100.mdx"))

    def test_runs_are_ordered_by_latest_document_activity(self):
        older = self.workspace / ".docket/runs/R00"
        older.mkdir()
        (older / "plan.mdx").write_text((self.run / "plan.mdx").read_text())
        os.utime(older / "plan.mdx", (1000, 1000))
        os.utime(self.run / "plan.mdx", (2000, 2000))
        self.assertEqual([r["id"] for r in inspector.snapshot(self.workspace)["runs"]], ["R01", "R00"])
        (older / "T01-task.mdx").write_text("---\ntitle: Task\n---\n")
        os.utime(older / "T01-task.mdx", (3000, 3000))
        runs = inspector.snapshot(self.workspace)["runs"]
        self.assertEqual([r["id"] for r in runs], ["R00", "R01"])
        self.assertEqual(runs[0]["updated_at"], 3000)

    def test_worker_sessions_expand_and_generation_changes(self):
        sessions = self.workspace / ".docket/sessions"
        sessions.mkdir()
        for sid in ("impl-a", "impl-b"):
            (sessions / f"{sid}.json").write_text(json.dumps({"session": sid, "role": "implementor",
                "run": "R01", "workspace": str(self.workspace), "generation": 1}))
        slots = inspector.snapshot(self.workspace)["runs"][0]["slots"]
        self.assertEqual(sum(s["role"] == "implementor" for s in slots), 2)
        old = next(s for s in slots if s.get("session") == "impl-a")["generation"]
        path = sessions / "impl-a.json"
        record = json.loads(path.read_text()); record["generation"] = 2
        path.write_text(json.dumps(record))
        slots = inspector.snapshot(self.workspace)["runs"][0]["slots"]
        self.assertNotEqual(old, next(s for s in slots if s.get("session") == "impl-a")["generation"])

    def test_private_evidence_traversal_and_symlinks_are_not_documents(self):
        private = self.run / ".bundles"
        private.mkdir(); (private / "secret.mdx").write_text("secret")
        outside = self.workspace / "secret.mdx"; outside.write_text("outside")
        (self.run / "escape.mdx").symlink_to(outside)
        docs = self.run / "docs"; docs.mkdir()
        (docs / "nested").symlink_to(self.workspace, target_is_directory=True)
        for key in ("runs/R01/.bundles/secret.mdx", "runs/R01/escape.mdx",
                    "runs/R01/docs/nested/secret.mdx", "../../secret.mdx"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspector.document(self.workspace, key)

    def test_discussions_are_raw_attributed_documents_and_receipts_untouched(self):
        messages = self.workspace / ".docket/conversations/C01/messages"
        messages.mkdir(parents=True)
        text = "---\nsender: human\nsequence: 1\n---\n\nHello <script>world</script>\n"
        (messages / "000001.mdx").write_text(text)
        self.assertEqual(inspector.document(self.workspace, "conversations/C01/messages/000001.mdx")["text"], text)
        self.assertEqual(len(inspector.snapshot(self.workspace)["conversations"]), 1)
        self.assertFalse((messages.parent / ".delivery").exists())

    def test_combined_legacy_policy_and_invalid_policy_remain_explicit(self):
        (self.run / "plan.mdx").write_text("---\ntopology: combined\n---\n# Legacy\n")
        result = inspector.snapshot(self.workspace)["runs"][0]
        self.assertEqual([s["role"] for s in result["slots"]], ["coordinator", "implementor"])
        self.plan("mode: made-up\n")
        with contextlib.redirect_stderr(io.StringIO()):
            result = inspector.snapshot(self.workspace)["runs"][0]
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["slots"], [])

    def registration(self, sid, role, native=None, process=None, name=None, run="R01"):
        sessions = self.workspace / ".docket/sessions"
        sessions.mkdir(exist_ok=True)
        (sessions / f"{sid}.json").write_text(json.dumps({
            "session": sid, "role": role, "run": run, "workspace": str(self.workspace),
            "generation": 1, "native_harness": native or {},
            "native_process": process or {}, **({"name": name} if name else {})}))

    def test_shared_identity_and_capabilities_distinguish_transport(self):
        # T02 review note: send/interrupt potential is True here because T02 implements
        # the exact-agent transport; T01 left them False with no transport. The values
        # below derive from that specified contract, not from observed output.
        self.registration("planner-a", "planner",
                          native={"harness": "opencode", "session_id": "abc"},
                          process={"harness": "opencode", "pid": 123, "start": "42"})
        self.registration("reviewer-b", "reviewer",
                          process={"harness": "opencode", "pid": 456, "start": "7"})
        slots = inspector.snapshot(self.workspace)["runs"][0]["slots"]
        native = next(s for s in slots if s.get("session") == "planner-a")
        process = next(s for s in slots if s.get("session") == "reviewer-b")
        self.assertEqual(native["identity"], {"kind": "native", "harness": "opencode", "session_id": "abc"})
        self.assertTrue(native["capabilities"]["history"])
        self.assertTrue(native["capabilities"]["send"])
        self.assertTrue(native["capabilities"]["interrupt"])
        self.assertFalse(native["capabilities"]["questions"])
        self.assertTrue(native["capabilities"]["terminal"])
        self.assertEqual(process["identity"], {"kind": "process", "pid": 456, "start": "7"})
        self.assertFalse(process["capabilities"]["history"])
        self.assertTrue(process["capabilities"]["send"])
        self.assertTrue(process["capabilities"]["interrupt"])
        self.assertTrue(process["capabilities"]["terminal"])

    def test_conflicted_and_historical_slots_advertise_no_send_or_interrupt(self):
        self.registration("planner-a", "planner",
                          native={"harness": "opencode", "session_id": "abc"},
                          process={"harness": "opencode", "pid": 123, "start": "42"})
        self.ledger = self.run / ".harness-sessions.jsonl"
        self.ledger.write_text(json.dumps({"role": "reviewer", "harness": "opencode",
            "session_id": "abc", "cwd": str(self.workspace), "owner": "-",
            "worker_process": {"harness": "opencode", "pid": 123, "start": "42"}}) + "\n")
        slots = inspector.snapshot(self.workspace)["runs"][0]["slots"]
        conflicted = next(s for s in slots if s.get("session") == "planner-a")
        self.assertIn("ambiguous", conflicted.get("mapping_issue", ""))
        self.assertFalse(conflicted["capabilities"]["send"])
        self.assertFalse(conflicted["capabilities"]["interrupt"])
        self.assertFalse(conflicted["capabilities"]["history"])

    def test_missing_planner_has_no_proven_identity_but_explicit_recovery_is_possible(self):
        self.registration("implementor", "implementor",
                          native={"harness": "opencode", "session_id": "abc"})
        slots = inspector.snapshot(self.workspace)["runs"][0]["slots"]
        planner = next(s for s in slots if s["role"] == "planner")
        self.assertEqual(planner["identity"], {"kind": "none"})
        self.assertFalse(planner["capabilities"]["history"])
        self.assertTrue(planner["capabilities"]["terminal"])

    def test_historical_slot_capabilities_advertise_no_transport(self):
        slot = {"native": {"harness": "opencode", "session_id": "abc"}, "historical": True}
        self.assertEqual(inspector.physical_identity(slot)["kind"], "native")
        caps = inspector.slot_capabilities(slot)
        self.assertFalse(caps["send"])
        self.assertFalse(caps["interrupt"])
        self.assertFalse(caps["terminal"])

    def test_stale_process_start_invalidates_identity_and_generation(self):
        self.registration("planner-a", "planner",
                          process={"harness": "opencode", "pid": 123, "start": "42"})
        before = next(s for s in inspector.snapshot(self.workspace)["runs"][0]["slots"]
                      if s.get("session") == "planner-a")
        self.registration("planner-a", "planner",
                          process={"harness": "opencode", "pid": 123, "start": "99"})
        after = next(s for s in inspector.snapshot(self.workspace)["runs"][0]["slots"]
                     if s.get("session") == "planner-a")
        self.assertNotEqual(before["generation"], after["generation"])
        self.assertNotEqual(before["identity"], after["identity"])


    # Real shapes from the run that motivated the role-first refinement.
    def dispatch(self, owner, session, state="released", at="2026-10-08T10:00:00Z", process=None, agent=None, run=None):
        run = run or self.run
        directory = run / ".dispatch"
        directory.mkdir(exist_ok=True)
        record = {"session": session, "session_generation": 1, "run": run.name, "role": "implementor",
                  "owner": owner, "state": state, "dispatched_at": at}
        if process:
            record["worker_process"] = process
        if agent:
            record["agent"] = agent
        (directory / f"{owner}.json").write_text(json.dumps(record))

    def note(self, role, sid, owner="-", harness="claude", process=None, at="2026-10-08T10:01:00Z"):
        row = {"at": at, "role": role, "owner": owner, "harness": harness, "session_id": sid,
               "cwd": str(self.workspace)}
        if process:
            row["worker_process"] = process
        with (self.run / ".harness-sessions.jsonl").open("a") as handle:
            handle.write(json.dumps(row) + "\n")

    def slots(self):
        return next(r for r in inspector.snapshot(self.workspace)["runs"] if r["id"] == "R01")["slots"]

    def test_planner_without_identity_stays_current_and_its_misrecorded_session_is_provenance(self):
        process = {"harness": "codex", "pid": 11, "start": "5"}
        self.registration("planner", "planner")
        self.note("implementor", "codex-thread", harness="codex", process=process)
        planner = next(s for s in self.slots() if s["role"] == "planner")
        self.assertEqual((planner["group"], planner["historical"], planner["identity"]["kind"]),
                         ("current", False, "none"))
        recorded = next(s for s in self.slots() if s.get("native", {}).get("session_id") == "codex-thread")
        self.assertEqual((recorded["role"], recorded["historical"], recorded["group"]),
                         ("implementor", True, "unattributed"))
        self.assertEqual(recorded["process"], process)

    def test_dispatch_process_proof_folds_the_native_registration_into_one_implementor(self):
        proof = {"harness": "claude", "pid": 77, "start": "9"}
        self.registration("impl", "implementor")
        self.registration("native-x", "implementor", native={"harness": "claude", "session_id": "sid-x"}, process=proof)
        self.dispatch("T01", "impl", state="dispatched", process=proof)
        workers = [s for s in self.slots() if s["role"] == "implementor"]
        self.assertEqual([(s["session"], s["group"]) for s in workers], [("impl", "current")])
        self.assertEqual((workers[0]["native"]["session_id"], workers[0]["process"], workers[0]["history_attribution"]),
                         ("sid-x", proof, "dispatch-process"))
        self.assertEqual(len(workers[0]["registrations"]), 2)

    def test_dispatch_process_that_differs_never_folds_and_leaves_the_registration_unattributed(self):
        self.registration("impl", "implementor")
        self.registration("native-x", "implementor", native={"harness": "claude", "session_id": "sid-x"},
                          process={"harness": "claude", "pid": 77, "start": "9"})
        self.dispatch("T01", "impl", state="dispatched", process={"harness": "claude", "pid": 77, "start": "10"})
        workers = {s["session"]: s for s in self.slots() if s["role"] == "implementor"}
        self.assertEqual(workers["impl"]["identity"], {"kind": "none"})
        self.assertEqual((workers["impl"]["group"], workers["native-x"]["group"]), ("current", "unattributed"))

    def test_current_is_last_dispatch_and_earlier_and_unattributed_collapse_but_concurrent_workers_stay(self):
        for owner, sid, at in (("T01", "w1", "2026-10-08T10:00:00Z"), ("T02", "w2", "2026-10-08T11:00:00Z"),
                               ("T03", "w3", "2026-10-08T12:00:00Z")):
            self.registration(sid, "implementor")
            self.dispatch(owner, sid, at=at)
        self.registration("native-stray", "implementor", native={"harness": "claude", "session_id": "stray"})
        (self.run / "T03-task.mdx").write_text("---\ntitle: Three\n---\n")
        (self.run / "T03-report-01.mdx").write_text("---\nstatus: approved\n---\n")
        groups = {s["session"]: (s["group"], s["historical"]) for s in self.slots() if s["role"] == "implementor"}
        self.assertEqual(groups, {"w3": ("current", False), "w2": ("earlier", True), "w1": ("earlier", True),
                                  "native-stray": ("unattributed", True)})
        self.assertEqual(next(s for s in self.slots() if s.get("session") == "w3")["task_note"], "last task T03")
        # A second dispatch that is still running makes both workers current, never a guess between them.
        self.dispatch("T04", "w1", state="dispatched", at="2026-10-08T13:00:00Z")
        self.dispatch("T05", "w2", state="dispatched", at="2026-10-08T13:01:00Z")
        groups = {s["session"]: s["group"] for s in self.slots() if s["role"] == "implementor"}
        self.assertEqual((groups["w1"], groups["w2"], groups["w3"]), ("current", "current", "earlier"))
        self.assertEqual({s["session"]: s["task_note"] for s in self.slots() if s.get("session") in ("w1", "w2")},
                         {"w1": "task T04", "w2": "task T05"})

    def test_duplicate_native_registrations_merge_only_on_agreeing_identity(self):
        native = {"harness": "claude", "session_id": "same"}
        self.registration("rev-a", "reviewer", native=native, process={"harness": "claude", "pid": 5, "start": "1"})
        self.registration("rev-b", "reviewer", native=native, process={"harness": "claude", "pid": 5, "start": "1"})
        self.assertEqual(len([s for s in self.slots() if s["role"] == "reviewer"]), 1)
        self.registration("rev-b", "reviewer", native=native, process={"harness": "claude", "pid": 6, "start": "1"})
        reviewers = [s for s in self.slots() if s["role"] == "reviewer"]
        self.assertEqual(len(reviewers), 2)
        self.assertTrue(all("different processes" in s["mapping_issue"] for s in reviewers))

    def test_choices_are_run_scoped_role_first_and_prove_a_process_only_when_records_agree(self):
        proof = {"harness": "claude", "pid": 8, "start": "3"}
        self.registration("planner", "planner")
        self.note("implementor", "tid-1", process=proof)
        other = self.workspace / ".docket/runs/R02"
        other.mkdir()
        (other / "plan.mdx").write_text((self.run / "plan.mdx").read_text())
        (other / ".harness-sessions.jsonl").write_text(json.dumps({
            "role": "planner", "owner": "-", "harness": "claude", "session_id": "elsewhere", "cwd": str(self.workspace)}) + "\n")
        planner = next(s for s in self.slots() if s["role"] == "planner")
        choices = inspector.history_choices(self.workspace, "R01", planner["id"], planner["generation"])["choices"]
        self.assertEqual([(c["id"], c["label"], c["process"]) for c in choices],
                         [("claude:tid-1", "Implementor · claude · unattributed", proof)])
        self.assertNotIn("tid-1", choices[0]["label"])
        wide = inspector.history_choices(self.workspace, "R01", planner["id"], planner["generation"], "project")["choices"]
        self.assertEqual(sorted(c["id"] for c in wide), ["claude:elsewhere", "claude:tid-1"])
        self.assertNotIn("process", next(c for c in wide if c["id"] == "claude:elsewhere"))
        self.note("implementor", "tid-1", process={"harness": "claude", "pid": 9, "start": "3"}, at="2026-10-08T10:02:00Z")
        again = inspector.history_choices(self.workspace, "R01", planner["id"], planner["generation"])["choices"]
        self.assertNotIn("process", again[0])

    def test_identically_labelled_choices_are_told_apart_by_a_short_tail_only(self):
        self.registration("planner", "planner")
        self.note("implementor", "aaaa-111111")
        self.note("implementor", "bbbb-222222")
        planner = next(s for s in self.slots() if s["role"] == "planner")
        labels = [c["label"] for c in inspector.history_choices(self.workspace, "R01", planner["id"], planner["generation"])["choices"]]
        self.assertEqual(labels, ["Implementor · claude · unattributed · …111111", "Implementor · claude · unattributed · …222222"])

    def test_a_current_worker_carries_the_herdr_name_docket_launched_it_under(self):
        self.registration("impl-s1", "implementor", name="impl-s1")
        self.dispatch("T01", "impl-s1", state="released", at="2026-10-08T09:00:00Z", agent="old-name")
        self.dispatch("T02", "impl-s1", state="dispatched", at="2026-10-08T10:00:00Z", agent="r01-impl")
        self.registration("rev-s1", "reviewer", name="r01-reviewer")
        self.registration("plan-s1", "planner", name="codex:01a1153c-7a58")
        slots = {s["role"]: s for s in self.slots() if not s.get("historical")}
        self.assertEqual(slots["implementor"]["herdr_name"], "r01-impl", "the latest dispatch names it")
        self.assertTrue(slots["implementor"]["capabilities"]["send"])
        self.assertFalse(slots["implementor"]["capabilities"]["history"], "a name proves no saved conversation")
        self.assertEqual(slots["reviewer"]["herdr_name"], "r01-reviewer")
        self.assertNotIn("herdr_name", slots["planner"], "a native registration name is not a Herdr name")

    def test_a_name_two_current_roles_claim_identifies_neither(self):
        other = self.workspace / ".docket/runs/R02"
        other.mkdir()
        (other / "plan.mdx").write_text((self.run / "plan.mdx").read_text())
        self.registration("impl-a", "implementor")
        self.dispatch("T01", "impl-a", state="dispatched", agent="shared-impl")
        self.registration("impl-b", "implementor", run="R02")
        self.dispatch("T01", "impl-b", state="dispatched", agent="shared-impl", run=other)
        for run in inspector.snapshot(self.workspace)["runs"]:
            worker = next(s for s in run["slots"] if s["role"] == "implementor" and not s.get("historical"))
            self.assertNotIn("herdr_name", worker, run["id"])
            self.assertIn("more than one current role", worker["name_note"])
            self.assertFalse(worker["capabilities"]["send"], run["id"])

    def test_a_dispatch_registration_merged_into_its_native_twin_keeps_its_dispatch_and_name(self):
        self.registration("r01-impl", "implementor", name="r01-impl")
        self.dispatch("T01", "r01-impl", state="dispatched", at="2026-10-08T10:00:00Z", agent="r01-impl")
        self.note("implementor", "sid-1", owner="T01", at="2026-10-08T10:01:00Z")
        self.registration("native-1", "implementor", name="claude:sid-1",
                          native={"harness": "claude", "session_id": "sid-1", "cwd": str(self.workspace)})
        workers = [s for s in self.slots() if s["role"] == "implementor" and not s.get("historical")]
        self.assertEqual(len(workers), 1)
        self.assertEqual([d["owner"] for d in workers[0]["dispatches"]], ["T01"])
        self.assertEqual(workers[0]["herdr_name"], "r01-impl")

    def test_an_earlier_worker_carries_no_name(self):
        self.registration("impl-a", "implementor")
        self.dispatch("T01", "impl-a", state="released", at="2026-10-08T09:00:00Z", agent="first-impl")
        self.registration("impl-b", "implementor")
        self.dispatch("T02", "impl-b", state="dispatched", at="2026-10-08T10:00:00Z", agent="second-impl")
        earlier = next(s for s in self.slots() if s.get("session") == "impl-a")
        self.assertTrue(earlier["historical"])
        self.assertNotIn("herdr_name", earlier)


if __name__ == "__main__":
    unittest.main()
