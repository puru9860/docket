"""Regression probes for the independent October workflow audit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import unittest

import test_docket as fixtures


class WorkflowAudit(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DocketCLI("runTest")
        self.fixture.setUp()
        self.root = self.fixture.root
        self.cli = self.fixture.cli
        self.cli_env = self.fixture.cli_env
        self.hook_path = Path(os.environ.get("DOCKET_AUDIT_HOOK", str(fixtures.HOOK)))
        self.env = {**os.environ, "DOCKET_ROLE": "reviewer", "DOCKET_WATCH_TIMEOUT": "0"}
        for name in ("DOCKET_RUN", "DOCKET_SESSION"):
            self.env.pop(name, None)

    def tearDown(self):
        self.fixture.tearDown()

    def prepare(self, run="a", session=""):
        self.cli("init", run, "--evidence-mode", "documents-only")
        args = ("--session", session) if session else ()
        self.cli("arm", run, "--role", "reviewer", *args)
        return self.root / ".docket" / "runs" / run

    def instruct(self, run="a", message="Audit wake"):
        self.cli("instruct", run, "--role", "reviewer", "--message", message)

    def hook(self, extra=None, cwd=None):
        return subprocess.run(["bash", str(self.hook_path)], cwd=cwd or self.root,
                              env={**self.env, **(extra or {})}, text=True,
                              capture_output=True, timeout=10)

    def test_hook_finds_nearest_ancestor_project(self):
        run = self.prepare()
        self.instruct()
        subdir = self.root / "src" / "nested"
        subdir.mkdir(parents=True)
        result = self.hook(cwd=subdir)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Audit wake", result.stderr)
        self.assertTrue(list((run / ".delivery/reviewer/announce").glob("*.json")))

    def test_hook_respects_run_selector(self):
        self.prepare("a")
        run_b = self.prepare("b")
        self.instruct("b")
        a = self.hook({"DOCKET_RUN": "a"})
        self.assertEqual((0, ""), (a.returncode, a.stderr))
        self.assertFalse((run_b / ".delivery").exists())
        b = self.hook({"DOCKET_RUN": "b"})
        self.assertEqual(2, b.returncode, b.stderr)
        self.assertIn("b: planner instruction", b.stderr)

    def test_unbound_hook_refuses_ambiguous_runs(self):
        self.prepare("a")
        run_b = self.prepare("b")
        self.instruct("b")
        result = self.hook()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertFalse((run_b / ".delivery").exists())
        self.assertIn("DOCKET_RUN", (self.root / ".docket/hook-failure-reviewer").read_text())

    def test_hook_crash_before_forwarding_retries_without_receipt(self):
        run = self.prepare()
        self.instruct()
        fake = self.root / "fake-bin"
        fake.mkdir()
        marker = self.root / "forward-started"
        cat = fake / "cat"
        cat.write_text('#!/bin/sh\n: > "$AUDIT_FORWARD_MARKER"\n'
                       '/bin/sleep 30\nexec /bin/cat "$@"\n')
        cat.chmod(0o755)
        proc = subprocess.Popen(["bash", str(self.hook_path)], cwd=self.root,
                                env={**self.env, "PATH": str(fake) + ":" + self.env["PATH"],
                                     "AUDIT_FORWARD_MARKER": str(marker)},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "did not reach forwarding boundary")
            announcement = json.loads(next((run / ".delivery/reviewer/announce").glob("*.json")).read_text())
            os.killpg(proc.pid, signal.SIGKILL)
            _, err = proc.communicate(timeout=5)
            self.assertEqual("", err)
            retry = self.hook({"DOCKET_NOW": str(announcement["lease_until"] + 1)})
            self.assertEqual(2, retry.returncode, retry.stderr)
            self.assertIn("Audit wake", retry.stderr)
            self.assertFalse(announcement.get("delivered_at"))
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()

    def test_doctor_uses_new_quick_roles(self):
        self.prepare()
        self.cli("arm", "a", "--role", "planner")
        missing = self.cli("doctor").stdout
        self.assertIn("a is missing role(s): implementor", missing)
        self.cli("arm", "a", "--role", "implementor")
        ready = self.cli("doctor").stdout
        self.assertNotIn("a is missing role(s)", ready)
        self.assertNotIn("checker", ready)
        self.assertNotIn("coordinator", ready)

    def test_hook_detection_rejects_async_unrelated_and_malformed_handlers(self):
        home = self.root / "codex-home"
        home.mkdir()
        for hook in (
            {"type": "command", "command": str(self.hook_path), "async": True},
            {"type": "command", "command": "/unrelated/project/wake.sh"},
            {"type": "command", "command": "echo docket/hooks/wake.sh"},
            {"type": "prompt", "command": str(self.hook_path)},
            {"type": "command", "command": str(self.hook_path), "async": "false"},
        ):
            with self.subTest(hook=hook):
                (home / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [hook]}]}}))
                result = self.cli_env({"CODEX_HOME": str(home)}, "doctor")
                self.assertIn("[ note ] Stop hook (Codex) absent", result.stdout)
        (home / "hooks.json").write_text('{"hooks": {"Stop": [{"hooks": "bad"}]}}')
        self.assertEqual(0, self.cli_env({"CODEX_HOME": str(home)}, "doctor").returncode)

    def test_hook_detection_reads_inline_toml_and_enabled_state(self):
        home = self.root / "codex-home"
        home.mkdir()
        config = ('[features]\nhooks = true\n[[hooks.Stop]]\n[[hooks.Stop.hooks]]\n'
                  f'type = "command"\ncommand = "{self.hook_path}"\n')
        (home / "config.toml").write_text(config)
        result = self.cli_env({"CODEX_HOME": str(home)}, "doctor")
        self.assertIn(f"Stop hook (Codex) in {home / 'config.toml'}", result.stdout)
        self.assertIn("trust", result.stdout)
        (home / "config.toml").write_text(config.replace("hooks = true", "hooks = false"))
        result = self.cli_env({"CODEX_HOME": str(home)}, "doctor")
        self.assertIn("disabled", result.stdout)

    def test_doctor_preserves_historical_quick_roles(self):
        self.cli("init", "old", "--mode", "quick", "--evidence-mode", "documents-only")
        self.fixture.old_quick("old")
        self.cli("arm", "old", "--role", "coordinator")
        self.assertIn("old is missing role(s): checker", self.cli("doctor").stdout)
        self.cli("arm", "old", "--role", "checker")
        self.assertNotIn("old is missing role(s)", self.cli("doctor").stdout)

    def test_forwarding_requires_pickup_and_received_events_stay_quiet(self):
        run = self.prepare()
        self.instruct()
        first = self.hook()
        self.assertEqual(2, first.returncode, first.stderr)
        self.assertIn("docket pickup a --role reviewer", first.stderr)
        path = next((run / ".delivery/reviewer/announce").glob("*.json"))
        notice = json.loads(path.read_text())
        self.assertFalse(notice.get("received_at"))
        self.assertIn("1 pending", self.cli("events", "a", "--role", "reviewer", "--peek").stdout)
        self.assertEqual(0, self.hook().returncode, "announcement lease avoids concurrent wakes")
        later = {"DOCKET_NOW": str(notice["lease_until"] + 1)}
        retry = self.hook(later)
        self.assertEqual(2, retry.returncode, retry.stderr)
        receipt = self.cli_env(later, "pickup", "a", "--role", "reviewer")
        self.assertIn("picked up 1 event", receipt.stdout)
        received = json.loads(path.read_text())
        self.assertTrue(received.get("received_at"))
        self.assertEqual(notice["session_generation"], received["session_generation"])
        self.assertEqual(0, self.hook({"DOCKET_NOW": str(received["lease_until"] + 4000)}).returncode)
        self.assertIn("0 pending", self.cli("events", "a", "--role", "reviewer", "--peek").stdout)
        self.assertIn("1 derived", self.cli("events", "a", "--role", "reviewer", "--peek").stdout)
        self.instruct(message="New revision")
        newer = self.hook()
        self.assertEqual(2, newer.returncode, newer.stderr)
        self.assertIn("New revision", newer.stderr)

    def test_native_recipient_generation_cannot_inherit_old_receipt_or_lease(self):
        run = self.prepare(session="reviewer-a")
        self.instruct()
        extra = {"DOCKET_SESSION": "reviewer-a"}
        self.assertEqual(2, self.hook(extra).returncode)
        path = next((run / ".delivery/reviewer/announce").glob("*.json"))
        initial = json.loads(path.read_text())
        self.cli("pickup", "a", "--role", "reviewer", "--session", "reviewer-a")
        self.cli("session", "a", "--register", "--session", "reviewer-a", "--role", "reviewer")
        stale = self.cli("pickup", "a", "--role", "reviewer", "--session", "reviewer-a", ok=False)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("previous session generation", stale.stderr)
        renewed = self.hook(extra)
        self.assertEqual(2, renewed.returncode, renewed.stderr)
        notice = json.loads(path.read_text())
        self.assertEqual(initial["session_generation"] + 1, notice["session_generation"])
        self.assertFalse(notice.get("received_at"))
        self.cli("pickup", "a", "--role", "reviewer", "--session", "reviewer-a")
        self.assertTrue(json.loads(path.read_text()).get("received_at"))

    def test_registered_recipient_only_receives_its_run(self):
        self.prepare("a", "reviewer-a")
        run_b = self.prepare("b", "reviewer-b")
        self.instruct("b")
        self.assertEqual(0, self.hook({"DOCKET_SESSION": "reviewer-a"}).returncode)
        conflict = self.hook({"DOCKET_SESSION": "reviewer-a", "DOCKET_RUN": "b"})
        self.assertEqual(1, conflict.returncode)
        self.assertFalse((run_b / ".delivery").exists())
        self.assertEqual(2, self.hook({"DOCKET_SESSION": "reviewer-b"}).returncode)
        wrong = self.cli("pickup", "b", "--role", "reviewer", "--session", "reviewer-a", ok=False)
        self.assertNotEqual(0, wrong.returncode)
        self.cli("session", "b", "--register", "--session", "other", "--role", "reviewer")
        other = self.cli("pickup", "b", "--role", "reviewer", "--session", "other")
        self.assertIn("picked up 0 event", other.stdout)
        notice = json.loads(next((run_b / ".delivery/reviewer/announce").glob("*.json")).read_text())
        self.assertFalse(notice.get("received_at"))

    def test_hook_allows_explicit_multi_run_supervision(self):
        self.prepare("a")
        self.prepare("b")
        self.instruct("a", "A wake")
        self.instruct("b", "B wake")
        result = self.hook({"DOCKET_RUN": "a,b"})
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("A wake", result.stderr)
        self.assertIn("B wake", result.stderr)
        self.assertIn("docket pickup a", result.stderr)
        self.assertIn("docket pickup b", result.stderr)

    def test_native_watch_leaves_actively_claimed_event_quiet(self):
        run = self.prepare(session="reviewer-a")
        self.instruct()
        self.cli("reconcile", "a", "--role", "reviewer")
        self.cli("inbox", "a", "--role", "reviewer", "--claim", "--session", "reviewer-a")
        result = self.hook({"DOCKET_SESSION": "reviewer-a"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(list((run / ".delivery/reviewer/announce").glob("*.json")))

    def test_manual_armed_watch_with_run_filters_other_runs(self):
        self.prepare("a")
        run_b = self.prepare("b")
        self.instruct("b")
        result = self.cli("watch", "a", "--armed", "--role", "reviewer", "--timeout", "0")
        self.assertEqual(0, result.returncode)
        self.assertFalse((run_b / ".delivery").exists())

    def test_dead_noted_supervisor_needs_a_replacement_launch(self):
        run = self.prepare()
        self.cli("arm", "a", "--role", "implementor")
        self.instruct()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            process = {"pid": proc.pid, "noted_at": "2020-01-01T00:00:00Z"}
            if Path("/proc").is_dir():
                process["start"] = (Path("/proc") / str(proc.pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]
            (run / ".harness-sessions.jsonl").write_text(json.dumps({
                "harness": "codex", "session_id": "reviewer-thread", "role": "reviewer",
                "owner": "-", "worker_process": process,
            }) + "\n")
            self.assertNotIn("launch reviewer", self.cli("events", "a", "--role", "implementor", "--peek").stdout)
            proc.terminate()
            proc.wait(timeout=5)
            self.assertIn("launch reviewer", self.cli("events", "a", "--role", "implementor", "--peek").stdout)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_detected_harness_binds_run_and_advances_after_restart(self):
        run = self.prepare("a")
        self.prepare("b")
        self.instruct("a", "A wake")
        self.instruct("b", "B wake")
        wrapper = self.root / "bin" / "codex"
        wrapper.parent.mkdir()
        wrapper.symlink_to(sys.executable)
        script = ("import json, os, subprocess, sys\n"
                  "def call(args):\n"
                  " r = subprocess.run(args, capture_output=True, text=True)\n"
                  " return {'code': r.returncode, 'out': r.stdout, 'err': r.stderr}\n"
                  f"cli = [sys.executable, {str(fixtures.DOCKET)!r}]\n"
                  f"hook = ['bash', {str(self.hook_path)!r}]\n"
                  "print(json.dumps([call(cli + ['arm', 'a', '--role', 'reviewer']),\n"
                  " call(hook), call(cli + ['pickup', 'a', '--role', 'reviewer'])]))\n")
        env = {**self.env, "CODEX_THREAD_ID": "reviewer-thread", "DOCKET_SESSION_CAPTURE": "on"}
        first = subprocess.run([str(wrapper), "-c", script], cwd=self.root, env=env,
                               text=True, capture_output=True, timeout=10)
        self.assertEqual(0, first.returncode, first.stderr)
        calls = json.loads(first.stdout)
        self.assertEqual([0, 2, 0], [c["code"] for c in calls], calls)
        self.assertIn("A wake", calls[1]["err"])
        self.assertNotIn("B wake", calls[1]["err"])
        path = next((run / ".delivery/reviewer/announce").glob("*.json"))
        generation = json.loads(path.read_text())["session_generation"]
        second = subprocess.run([str(wrapper), "-c", script], cwd=self.root, env=env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(0, second.returncode, second.stderr)
        calls = json.loads(second.stdout)
        self.assertEqual([0, 2, 0], [c["code"] for c in calls], calls)
        self.assertEqual(generation + 1, json.loads(path.read_text())["session_generation"])

    def test_doctor_reports_installed_revision_without_syncing(self):
        home = self.root / "home"
        installed = home / ".agents/skills/docket"
        source = fixtures.DOCKET.resolve().parents[1]
        installed.mkdir(parents=True)
        shutil.copy2(source / "SKILL.md", installed / "SKILL.md")
        for name in ("bin", "hooks", "docket_cli", "references"):
            shutil.copytree(source / name, installed / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        noise = installed / "docket_cli/__pycache__/ignored.pyc"
        noise.parent.mkdir()
        noise.write_bytes(b"cached")
        result = self.cli_env({"HOME": str(home)}, "doctor")
        self.assertIn("matches runtime files", result.stdout)
        target = installed / "hooks/wake.sh"
        target.write_text("#!/bin/sh\nexit 0\n")
        before = target.read_bytes()
        result = self.cli_env({"HOME": str(home)}, "doctor")
        self.assertIn("differs in 1 runtime file", result.stdout)
        self.assertIn("hooks/wake.sh", result.stdout)
        self.assertEqual(before, target.read_bytes())

    def test_duplicate_pickup_repairs_interrupted_native_receipt_write(self):
        run = self.prepare()
        self.instruct()
        self.assertEqual(2, self.hook().returncode)
        self.cli("pickup", "a", "--role", "reviewer")
        path = next((run / ".delivery/reviewer/announce").glob("*.json"))
        notice = json.loads(path.read_text())
        # Model the crash window: inbox acknowledgement persisted, native
        # receipt publication did not. Duplicate pickup must finish that write.
        notice.pop("received_at")
        path.write_text(json.dumps(notice))
        receipt = self.cli("pickup", "a", "--role", "reviewer")
        self.assertIn("already received", receipt.stdout)
        self.assertTrue(json.loads(path.read_text()).get("received_at"))
        self.assertEqual(0, self.hook({"DOCKET_NOW": str(notice["lease_until"] + 1000)}).returncode)

    def test_native_run_selector_accepts_dotted_ids(self):
        self.prepare("run.v2")
        self.instruct("run.v2")
        result = self.hook({"DOCKET_RUN": "run.v2"})
        self.assertEqual(2, result.returncode, result.stderr)

    def test_pickup_refuses_an_announcement_for_another_revision(self):
        run = self.prepare()
        self.instruct()
        self.assertEqual(2, self.hook().returncode)
        path = next((run / ".delivery/reviewer/announce").glob("*.json"))
        notice = json.loads(path.read_text())
        notice["revision"] = "sha256:" + "0" * 64
        path.write_text(json.dumps(notice))
        result = self.cli("pickup", "a", "--role", "reviewer", ok=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("announcement moved", result.stderr)
        self.assertFalse(json.loads(path.read_text()).get("received_at"))
        self.assertFalse(list((run / ".delivery/reviewer/leases").glob("*.json")))

    def test_native_inspection_does_not_acknowledge_or_mutate_delivery(self):
        run = self.prepare()
        self.instruct()
        self.assertEqual(2, self.hook().returncode)
        before = self.fixture.delivery_snapshot(run)
        self.cli("events", "a", "--role", "reviewer", "--peek")
        self.cli("status", "a")
        self.cli("doctor")
        self.assertEqual(before, self.fixture.delivery_snapshot(run))

    def test_failed_banner_forwarding_is_not_a_harness_wake(self):
        run = self.prepare()
        self.instruct()
        fake = self.root / "fake-bin"
        fake.mkdir()
        cat = fake / "cat"
        cat.write_text("#!/bin/sh\nexit 1\n")
        cat.chmod(0o755)
        result = self.hook({"PATH": str(fake) + ":" + self.env["PATH"]})
        self.assertEqual(1, result.returncode, result.stderr)
        failure = self.root / ".docket/hook-failure-reviewer"
        self.assertIn("forwarding failed", failure.read_text())
        path = next((run / ".delivery/reviewer/announce").glob("*.json"))
        notice = json.loads(path.read_text())
        self.assertFalse(notice.get("received_at"))
        self.assertEqual(2, self.hook({"DOCKET_NOW": str(notice["lease_until"] + 1)}).returncode)
        self.assertFalse(failure.exists())

    def test_pickup_does_not_reclaim_received_events_on_a_later_wake(self):
        run = self.prepare()
        self.instruct()
        self.assertEqual(2, self.hook().returncode)
        self.cli("pickup", "a", "--role", "reviewer")
        before = {path: path.read_bytes() for path in (run / ".delivery/reviewer/leases").glob("*.json")}
        self.instruct(message="Another instruction")
        self.assertEqual(2, self.hook().returncode)
        picked = self.cli("pickup", "a", "--role", "reviewer")
        self.assertIn("picked up 1 event", picked.stdout)
        self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))


if __name__ == "__main__":
    unittest.main()
