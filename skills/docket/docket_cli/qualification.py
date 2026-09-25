"""Delivery qualification: probes, fixed notices, and qualification artifacts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .common import KNOWN_EVENT_ROLES, LAUNCHER, STATE_DIR, _has, die, stamp
from .paths import root
from .publication import fault, publish, publish_json
from .policy import need_run, require_preset_role
from .evidence import git_argv, git_env, state_prefix
from .bundles import check_frozen_digest, freeze_record, source_tree
from .liveness import read_registration
from .events import derive_events
from .delivery import (
    announce_active, announce_delivered, claim_event, delivery_dir, delivery_lock,
    delivery_log, input_active_flag, outbox_path, paused_flag, read_lease, sweep_role,
)
from .signalling import delivered_keys


def _git_bytes(*args: str) -> tuple[int, bytes]:
    """One read-only git command against the invocation root, raw bytes out."""
    try:
        result = subprocess.run(git_argv(*args), capture_output=True, env=git_env(),
                                timeout=30, cwd=str(root()))
    except (OSError, subprocess.TimeoutExpired):
        return 128, b""
    return result.returncode, result.stdout


def _porcelain_records(output: bytes) -> list[tuple[bytes, bytes, bytes | None]] | None:
    """NUL-delimited porcelain v1 parsed into (status, path, orig-or-None).

    Rename/copy entries carry their source path as a second NUL record. Paths
    stay raw bytes throughout, so quotes, escapes, spaces, and newlines
    survive intact. None on any malformed input: an unparseable listing is an
    unknown tree, never a guessed one.
    """
    chunks = output.split(b"\0")
    if chunks and chunks[-1] == b"":
        chunks.pop()
    records: list[tuple[bytes, bytes, bytes | None]] = []
    i = 0
    while i < len(chunks):
        head = chunks[i]
        i += 1
        if len(head) < 4 or head[2:3] != b" ":
            return None
        status, path = head[:2], head[3:]
        orig = None
        if status[:1] in (b"R", b"C"):
            if i >= len(chunks):
                return None
            orig = chunks[i]
            i += 1
        records.append((status, path, orig))
    return records


def _content_record(kind: bytes, path: bytes, mode: int, payload: bytes) -> bytes:
    """One unambiguous length-delimited content record.

    NUL separates the fixed fields while the payload carries an explicit
    8-byte length, so arbitrary file bytes (including NUL) cannot blur into
    neighboring fields or neighboring records.
    """
    return (kind + b"\0" + path + b"\0" + ("%o" % mode).encode() + b"\0"
            + len(payload).to_bytes(8, "big") + payload)


def worktree_identity(cwd: Path | None = None) -> str:
    """Content identity of the enclosing checkout, including uncommitted state.

    A Git HEAD alone is not a source identity in this repository: the work
    under review is usually uncommitted. The identity therefore hashes the
    HEAD, the staged entries, the unstaged binary diff, and every untracked
    path with its file identity: each relative path, file type, and mode plus
    a digest of the content (symlink targets recorded as symlinks, never
    followed), excluding only Docket's own state directory (protocol
    scratch, never reviewed source).
    All git calls are read-only, paths parse from NUL-delimited output, and
    only regular files are ever opened, so pipes and sockets cannot hang the
    probe. Unknown outside git; callers skip comparisons they cannot assess
    rather than treating unknown as matching. An optional cwd overrides the
    invocation root for tests.
    """
    base = Path(cwd) if cwd is not None else root()

    def git(*args: str) -> tuple[int, bytes]:
        try:
            result = subprocess.run(git_argv(*args), capture_output=True, env=git_env(),
                                    timeout=30, cwd=str(base))
        except (OSError, subprocess.TimeoutExpired):
            return 128, b""
        return result.returncode, result.stdout

    code, head = git("rev-parse", "HEAD")
    head_line = head.strip().split(b"\n")
    if code != 0 or not head_line or not head_line[0].strip():
        code, inside = git("rev-parse", "--is-inside-work-tree")
        if code == 0 and inside.strip() == b"true":
            return "unknown (no commits yet)"
        return "unknown (not a git checkout)"
    code, porcelain = git("status", "--porcelain=v1", "-z", "-uall")
    if code != 0:
        return "unknown (git status failed)"
    records = _porcelain_records(porcelain)
    if records is None:
        return "unknown (git status unparseable)"
    code, staged = git("ls-files", "-s", "-z")
    if code != 0:
        return "unknown (git index unreadable)"
    staged_entries = staged.split(b"\0")
    if staged_entries and staged_entries[-1] == b"":
        staged_entries.pop()
    for entry in staged_entries:
        if b"\t" not in entry:
            return "unknown (git index unparseable)"
    code, unstaged = git("diff", "--binary")
    if code not in (0, 1):
        return "unknown (git diff failed)"
    code, top = git("rev-parse", "--show-toplevel")
    top_dir = top.decode("utf-8", "surrogateescape").strip() if code == 0 else str(base)
    ignored = (state_prefix(top_dir) or STATE_DIR).encode("utf-8", "surrogateescape")
    state_h = hashlib.sha256()
    state_h.update(head_line[0].strip() + b"\0")
    untracked: list[bytes] = []
    for status, path, orig in records:
        if status[:1] == b"!":
            continue
        if path == ignored or path.startswith(ignored + b"/"):
            continue
        if status == b"??":
            untracked.append(path)
            continue
        state_h.update(status + b" " + path)
        if orig is not None:
            state_h.update(b"\0" + orig)
        state_h.update(b"\0")
    for entry in staged_entries:
        state_h.update(entry + b"\0")
    state_h.update(unstaged + b"\0")
    for rel in sorted(untracked):
        if rel == ignored or rel.startswith(ignored + b"/"):
            continue
        full = base / rel.decode("utf-8", "surrogateescape")
        try:
            info = os.lstat(full)
        except OSError:
            return f"unknown (unreadable worktree file {rel.decode('utf-8', 'replace')})"
        if stat.S_ISLNK(info.st_mode):
            try:
                target = os.readlink(full)
            except OSError:
                return f"unknown (unreadable worktree file {rel.decode('utf-8', 'replace')})"
            state_h.update(_content_record(b"symlink", rel, 0o120000,
                                           os.fsencode(target)))
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            try:
                with open(full, "rb") as handle:
                    while True:
                        chunk = handle.read(65536)
                        if not chunk:
                            break
                        digest.update(chunk)
            except OSError:
                return f"unknown (unreadable worktree file {rel.decode('utf-8', 'replace')})"
            state_h.update(_content_record(b"file", rel,
                                           stat.S_IMODE(info.st_mode),
                                           digest.digest()))
        else:
            state_h.update(_content_record(b"other", rel,
                                           stat.S_IMODE(info.st_mode), b""))
    return "sha256:" + state_h.hexdigest()


def bounded_probe(argv: list[str], deadline: float) -> tuple[int, str]:
    """Bound a probe and its children by the same deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv, 0)
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    return proc.returncode, output


def probe_boundary() -> tuple[str, str, str]:
    """Capability-test the delivery boundary. Never claims more than it proves.

    The installed `herdr agent prompt --help` establishes no turn tracking, so
    reading `idle` and then sending races user input. Unless a verified
    turn-boundary or queue mechanism is found, delivery stays queued for
    explicit inbox pickup or a native hook.

    Positive contract: help must positively advertise a flag-like capability
    (`--send-if-idle`, `--queue`, `--turn-boundary`, or a `capability:`
    line naming `queue`/`turn-boundary`), contain no negative statement about
    it, exit zero, and pass an executable probe of that exact flag. Ambiguous
    output, negative statements, unknown versions, timeouts, and nonzero exits
    all report manual mode. Only a positively verified queue or turn boundary
    reports unattended mode. The detail always records the probed command,
    version, and result so `doctor` can explain the decision.
    """
    command = "herdr agent prompt --help"
    version = "unknown"
    try:
        probe_timeout = float(os.environ.get("DOCKET_PROBE_TIMEOUT", "3"))
    except ValueError:
        probe_timeout = 3.0
    deadline = time.monotonic() + max(0.1, probe_timeout)
    try:
        vrc, version_output = bounded_probe(["herdr", "--version"], deadline)
        vtext = version_output.strip().splitlines()
        if vrc == 0 and vtext:
            version = vtext[0].strip()[:120] or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        version = "unknown"
    try:
        rc, help_text = bounded_probe(["herdr", "agent", "prompt", "--help"], deadline)
    except subprocess.TimeoutExpired:
        return ("none", "manual",
                f"probed {command} (version {version}, result timeout): "
                "capability probe timed out; delivery stays manual for explicit "
                "inbox pickup or a native hook")
    except OSError as exc:
        if _has("herdr"):
            return ("none", "manual",
                    f"probed {command} (version {version}, result OSError {exc}): "
                    "herdr is installed but did not answer the capability probe")
        return ("none", "manual",
                f"probed {command} (version {version}, result not-installed): "
                "no supported supervisor found; delivery is bounded/manual: "
                "events stay queued for explicit inbox pickup or a native hook")
    lowered = help_text.lower()
    prefix = f"probed {command} (version {version}, result rc={rc})"
    if rc != 0:
        return ("none", "manual",
                f"{prefix}: nonzero exit; delivery stays manual for explicit "
                "inbox pickup or a native hook")
    if not help_text.strip():
        return ("none", "manual",
                f"{prefix}: empty help output; delivery stays manual")
    advertised_flag = re.search(r"--(?:send-if-idle|turn-boundary|queue)\b", lowered)
    positive = bool(advertised_flag or re.search(
        r"capability\s*:\s*(queue|turn-boundary)|supports?\s+(queued|turn-boundary)(\s+delivery)?",
        lowered))
    negations = ("does not support", "doesn't support", "does not provide",
                 "do not support", "don't support", "no queue", "no support",
                 "not support", "without", "unable", "cannot", "can't",
                 "never", "not available", "unsupported", "no safe",
                 "does not track", "no turn")
    negative = any(item in lowered for item in negations)
    if not positive:
        return ("none", "manual",
                f"{prefix}: herdr advertises no safe idle queue or atomic "
                "send-if-idle; delivery stays manual for explicit inbox pickup "
                "or a native hook")
    if negative:
        return ("none", "manual",
                f"{prefix}: help contains a negative statement about the "
                "boundary; ambiguous output stays manual for explicit inbox "
                "pickup or a native hook")
    if advertised_flag is None:
        return ("none", "manual",
                f"{prefix}: queue capability names no executable flag; "
                "delivery stays manual")
    flag = advertised_flag.group(0)
    try:
        probe_rc, probe_output = bounded_probe(
            ["herdr", "agent", "prompt", flag, "--help"], deadline)
        probe_text = probe_output.lower()
    except subprocess.TimeoutExpired:
        return ("none", "manual",
                f"{prefix}: advertised queue capability but the executable "
                "probe timed out; delivery stays manual")
    except OSError as exc:
        return ("none", "manual",
                f"{prefix}: advertised queue capability but the executable "
                f"probe failed ({exc}); delivery stays manual")
    if probe_rc != 0 or flag not in probe_text:
        return ("none", "manual",
                f"{prefix}: advertised queue capability but the executable "
                f"probe failed (rc={probe_rc}); delivery stays manual for "
                "explicit inbox pickup or a native hook")
    if any(item in probe_text for item in negations):
        return ("none", "manual",
                f"{prefix}: executable probe contains a negative statement; "
                "delivery stays manual")
    return ("verified-queue", "unattended",
            f"{prefix}: herdr positively verified a queued turn boundary "
            f"(executable probe rc={probe_rc})")


def fixed_notice(run: str, role: str, key: str, session: str) -> str:
    """A fixed-format delivery notice. Never carries report prose."""
    return (f"run: {run}\nrole: {role}\nevent: {key}\nsession: {session}\n"
            f"inbox: docket inbox {run} --role {role} --claim --session {session}\n")


def docket_subprocess(*args: str, timeout: int = 60,
                      env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run this docket binary as a real subprocess. Qualification evidence."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        cwd=str(root()), text=True, capture_output=True,
        timeout=timeout, env=merged)


def delivery_snapshot(d: Path, role: str) -> dict[str, object]:
    """Ledger bytes plus the delivery file set, minus the paused flag.

    Pausing is expected to appear and disappear around a killed hook, so the
    flag itself is excluded; everything else must survive a SIGKILL exactly.
    """
    snap: dict[str, object] = {}
    led = d / f".woke-{role}"
    try:
        snap["ledger"] = led.read_bytes().decode() if led.is_file() else None
    except OSError:
        snap["ledger"] = "unreadable"
    files: dict[str, str] = {}
    base = delivery_dir(d, role)
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if rel == "paused":
                continue
            try:
                files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                files[rel] = "unreadable"
    snap["delivery"] = files
    return snap


def herdr_run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """One herdr invocation, captured for qualification evidence."""
    return subprocess.run(["herdr", *args], text=True, capture_output=True,
                          timeout=timeout)


@contextlib.contextmanager
def qualification_sandbox(d: Path, role: str):
    """A disposable copy of one run, entered as the working directory.

    Qualification runs real watchers, claims, and pauses. Against the live run it
    delivered the role's real pending event, so the real supervisor was never woken
    for it, and it lifted an operator's pause. The copy leaves out the object store,
    which delivery never reads, and starts unpaused, since a paused role cannot
    announce; the live pause is untouched.
    """
    home = Path(tempfile.mkdtemp(prefix="docket-qualify-"))
    previous = Path.cwd()

    def ignore(directory: str, names: list[str]) -> list[str]:
        if Path(directory).name == ".bundles":
            return [name for name in names if name in ("objects", "work")]
        return []

    try:
        target = home / STATE_DIR / "runs" / d.name
        shutil.copytree(d, target, symlinks=True, ignore=ignore)
        paused_flag(target, role).unlink(missing_ok=True)
        os.chdir(home)
        yield target
    finally:
        os.chdir(previous)
        shutil.rmtree(home, ignore_errors=True)


def cmd_delivery_qualify(d: Path, run: str, role: str, session: str,
                         artifact: Path | None = None, live_root: Path | None = None) -> None:
    """Qualify delivery against disposable real processes, or block honestly.

    Exercises a real hook announcement, a SIGKILLed hook restart, a real
    claim/send/ack/retry round-trip with session-generation reuse refusal, a
    simulated-active-input refusal, and a disposable herdr pane lifecycle.
    Nothing is ever injected into a live input surface: pane interaction is
    limited to split/run/read/close, never send-text. Findings land in
    `.delivery/qualification-<role>.json`; `blocked` names the unavailable
    capability, `failed` means something actually broke, and production stays
    manual until a positive behavior-level qualification succeeds.
    """
    artifact = artifact or delivery_dir(d, role).parent / f"qualification-{role}.json"
    record: dict[str, object] = {
        "run": run, "role": role, "started_at": stamp(), "checks": [],
        "live_state": "untouched: qualified against a disposable copy of the run",
    }
    failed: list[str] = []
    blocked: list[str] = []

    def with_history(rec: dict[str, object]) -> dict[str, object]:
        # The disposable copy is discarded, so its lease history travels in the record.
        log = delivery_dir(d, role) / "log.jsonl"
        try:
            lines = log.read_text().splitlines() if log.is_file() else []
        except OSError:
            lines = []
        history = []
        for line in lines:
            try:
                history.append(json.loads(line))
            except ValueError:
                continue
        return {**rec, "lease_history": history}

    def check(name: str, status: str, detail: str, **extra: object) -> None:
        record["checks"].append({"check": name, "status": status,
                                 "detail": detail, **extra})
        if status == "failed":
            failed.append(name)
        elif status == "blocked":
            blocked.append(name)

    boundary, mode, detail = probe_boundary()
    try:
        vres = subprocess.run(["herdr", "--version"], capture_output=True,
                              text=True, timeout=10)
        herdr_version = (((vres.stdout + vres.stderr).strip().splitlines()
                          or ["unknown"])[0][:120]
                         if vres.returncode == 0 else "unknown")
    except (OSError, subprocess.TimeoutExpired):
        herdr_version = "unknown" if _has("herdr") else "not-installed"
    record["probe"] = {"command": "herdr agent prompt --help",
                       "boundary": boundary, "mode": mode, "detail": detail,
                       "herdr_version": herdr_version}
    # The content tree and HEAD are the live checkout's, which is what a release binds;
    # only delivery state is exercised in the disposable copy.
    checkout = live_root or root()
    record["source_tree"] = worktree_identity(checkout)
    try:
        head_out = subprocess.run(git_argv("rev-parse", "HEAD"), capture_output=True,
                                  text=True, timeout=10, cwd=str(checkout), env=git_env())
        record["repo_head"] = (head_out.stdout.strip()[:64]
                               if head_out.returncode == 0 and head_out.stdout.strip()
                               else "unknown (not a git checkout)")
    except (OSError, subprocess.TimeoutExpired):
        record["repo_head"] = "unknown (git unavailable)"
    record["production_mode"] = "manual"
    record["supervised_delivery_service"] = (
        "not-implemented by design; foreground hooks plus explicit inbox pickup")
    record["safe_notification_injection"] = (
        "unavailable by design; notices are outbox files, never typed input")
    check("probe", "passed", f"boundary {boundary}, mode {mode}")
    with delivery_lock(d, role):
        derived = derive_events(d, role)
    if not derived:
        record["status"] = "blocked"
        record["blocked_capability"] = "no-actionable-event"
        record["finished_at"] = stamp()
        publish_json(artifact, freeze_record(with_history(record)))
        print(f"qualification {role}: blocked (no derived event to qualify against; "
              "submit work first)")
        print(f"artifact: {artifact}")
        return
    key = str(derived[0]["key"])
    record["event"] = {"key": key, "message": str(derived[0].get("message", ""))}
    sid = (session or "").strip() or f"dqd-{os.getpid()}-{role}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", sid):
        record["status"] = "failed"
        record["finished_at"] = stamp()
        publish_json(artifact, freeze_record(with_history(record)))
        die(f"invalid session id {sid!r} for qualification")
    session_argv = ["session", run, "--register", "--session", sid,
                    "--name", "delivery-qualification-disposable", "--role", role]
    reg_result = docket_subprocess(*session_argv)
    if reg_result.returncode != 0:
        check("session-register", "failed",
              f"exit {reg_result.returncode}: {reg_result.stderr.strip()[:300]}",
              argv=session_argv, exit=reg_result.returncode)
        record["status"] = "failed"
        record["finished_at"] = stamp()
        publish_json(artifact, freeze_record(with_history(record)))
        die(f"qualification {role}: disposable session registration failed")
    generation = read_registration(sid).get("generation", "?")
    record["session"] = {"id": sid, "generation": generation,
                         "argv": session_argv, "exit": 0}
    watch_argv = ["watch", run, "--role", role, "--timeout", "10"]
    announce = subprocess.Popen(
        [sys.executable, str(LAUNCHER), *watch_argv],
        cwd=str(root()), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _, announce_err = announce.communicate(timeout=20)
        announce_rc = announce.returncode
    except subprocess.TimeoutExpired:
        announce.kill()
        _, announce_err = announce.communicate()
        announce_rc = announce.returncode
    announced = key in delivered_keys(d, role)
    if announce_rc == 2 and announced:
        check("hook-announce", "passed",
              f"real watch pid {announce.pid} exited 2 with the event in the "
              "role ledger", pid=announce.pid, exit=announce_rc, argv=watch_argv)
    elif (announce_rc == 0 and announced
          and (announce_active(d, role, key) or announce_delivered(d, role, key))):
        check("hook-announce", "passed",
              "announcement already active under a live lease or already delivered; "
              "verified in the ledger and announce records instead of double-announcing",
              pid=announce.pid, exit=announce_rc, argv=watch_argv)
    else:
        check("hook-announce", "failed",
              f"real watch pid {announce.pid} exited {announce_rc} without "
              "the actionable event in the ledger", pid=announce.pid,
              exit=announce_rc, argv=watch_argv)
    docket_subprocess("delivery", run, "--role", role, "--pause")
    before = delivery_snapshot(d, role)
    doomed_argv = ["watch", run, "--role", role, "--timeout", "15"]
    doomed = subprocess.Popen(
        [sys.executable, str(LAUNCHER), *doomed_argv],
        cwd=str(root()), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    doomed.kill()
    try:
        _, _ = doomed.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    doomed_rc = doomed.returncode
    docket_subprocess("delivery", run, "--role", role, "--resume")
    after = delivery_snapshot(d, role)
    if doomed_rc == -9 and before == after:
        check("hook-kill-restart", "passed",
              f"SIGKILLed watch pid {doomed.pid} left ledger and delivery "
              "byte-for-byte unchanged", pid=doomed.pid, exit=doomed_rc,
              argv=doomed_argv)
    else:
        check("hook-kill-restart", "failed",
              f"watch pid {doomed.pid} exited {doomed_rc} or delivery state "
              "moved under SIGKILL", pid=doomed.pid, exit=doomed_rc,
              argv=doomed_argv)
    claim_argv = ["inbox", run, "--role", role, "--claim", "--session", sid]
    claim = docket_subprocess(*claim_argv)
    lease_key = ""
    if claim.returncode != 0:
        check("claim", "failed",
              f"exit {claim.returncode}: {claim.stderr.strip()[:300]}",
              argv=claim_argv, exit=claim.returncode)
    else:
        leases = sorted((delivery_dir(d, role) / "leases").glob("*.json"))
        try:
            lease_key = json.loads(leases[-1].read_text()).get("key", "") if leases else ""
        except (OSError, ValueError):
            lease_key = ""
        if lease_key == key:
            lease = read_lease(d, role, key)
            check("claim", "passed",
                  f"claimed {key} (attempt {lease.get('attempt', '?')}, "
                  f"lease until {lease.get('lease_until', '?')})",
                  argv=claim_argv, exit=claim.returncode)
        else:
            check("claim", "failed",
                  f"claim succeeded but no lease for {key} appeared",
                  argv=claim_argv, exit=claim.returncode)
    send_argv = ["delivery", run, "--role", role,
                   "--send", key, "--session", sid]
    sent = docket_subprocess(*send_argv)
    notice_path = outbox_path(d, role, key)
    if sent.returncode == 0 and notice_path.is_file():
        lines = notice_path.read_text(errors="replace").splitlines()
        if (lines[:1] == [f"run: {run}"]
                and any(line == f"role: {role}" for line in lines)
                and any(line == f"event: {key}" for line in lines)):
            check("send", "passed",
                  f"outbox notice for {key} carries only run/role/event/session/inbox",
                  argv=send_argv, exit=sent.returncode)
        else:
            check("send", "failed",
                  f"outbox notice for {key} is not fixed-format",
                  argv=send_argv, exit=sent.returncode)
    else:
        check("send", "failed",
              f"exit {sent.returncode}: {sent.stderr.strip()[:300]}",
              argv=send_argv, exit=sent.returncode)
    ack_argv = ["events", run, "--role", role,
                "--ack", key, "--session", sid]
    acked = docket_subprocess(*ack_argv)
    if acked.returncode == 0:
        check("ack", "passed", f"receipt recorded for {key}; receipt is not completion",
              argv=ack_argv, exit=acked.returncode)
    else:
        check("ack", "failed",
              f"exit {acked.returncode}: {acked.stderr.strip()[:300]}",
              argv=ack_argv, exit=acked.returncode)
    docket_subprocess("session", run, "--register", "--session", sid,
                      "--name", "delivery-qualification-reused", "--role", role)
    stale_argv = ["events", run, "--role", role,
                  "--ack", key, "--session", sid]
    stale_ack = docket_subprocess(*stale_argv)
    if stale_ack.returncode != 0 and "generation" in stale_ack.stderr:
        check("generation-reuse", "passed",
              "reused session name with a new generation cannot acknowledge "
              "the old generation's claim",
              argv=stale_argv, exit=stale_ack.returncode)
    else:
        check("generation-reuse", "failed",
              f"stale generation ack exited {stale_ack.returncode}; session "
              "generations must not inherit claims",
              argv=stale_argv, exit=stale_ack.returncode)
    retry_argv = ["events", run, "--role", role,
                  "--retry", key, "--reason",
                  "delivery-qualification complete; releasing disposable lease"]
    retry = docket_subprocess(*retry_argv)
    if retry.returncode == 0:
        check("retry-release", "passed", "disposable lease released with a reason",
              argv=retry_argv, exit=retry.returncode)
    else:
        check("retry-release", "failed",
              f"exit {retry.returncode}: {retry.stderr.strip()[:300]}",
              argv=retry_argv, exit=retry.returncode)
    notice_path.unlink(missing_ok=True)
    flag = input_active_flag(d, role)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("simulated typing: qualification never types into real input\n")
    refused_argv = ["delivery", run, "--role", role,
                    "--send", key, "--session", sid]
    refused = docket_subprocess(*refused_argv)
    leftovers = list((delivery_dir(d, role) / "outbox").glob("*.txt"))
    flag.unlink(missing_ok=True)
    if refused.returncode == 0 and "queued" in refused.stdout and not leftovers:
        check("active-input", "passed",
              "send while input is (simulated) active stays queued; the outbox "
              "gained no notice",
              argv=refused_argv, exit=refused.returncode)
    else:
        check("active-input", "failed",
              "a notice entered the outbox while input was active",
              argv=refused_argv, exit=refused.returncode)
    pane_record: dict[str, object] = {"commands": []}
    if _has("herdr") and os.environ.get("HERDR_ENV") == "1":
        pane_tmp = Path("/tmp") / f"docket-qualify-{os.getpid()}"
        pane_tmp.mkdir(parents=True, exist_ok=True)
        pane_id = ""
        try:
            split = herdr_run("pane", "split", "--current", "--direction", "down",
                              "--cwd", str(pane_tmp), "--no-focus")
            pane_record["commands"].append({"argv": "pane split", "exit": split.returncode,
                                            "output": (split.stdout + split.stderr)[:500]})
            try:
                pane_id = json.loads(split.stdout)["result"]["pane"]["pane_id"]
            except (ValueError, KeyError, TypeError):
                pane_id = ""
            if split.returncode == 0 and pane_id:
                marker = f"docket-qualify-{os.getpid()}-{role}-ok"
                run_out = herdr_run("pane", "run", pane_id, f"echo {marker}")
                pane_record["commands"].append({"argv": "pane run", "exit": run_out.returncode,
                                                "output": (run_out.stdout + run_out.stderr)[:300]})
                seen_marker = False
                read_out = ""
                for _ in range(2):
                    time.sleep(2)
                    read = herdr_run("pane", "read", pane_id, "--lines", "15")
                    read_out = read.stdout + read.stderr
                    if marker in read_out:
                        seen_marker = True
                        break
                pane_record["commands"].append({"argv": "pane read", "exit": 0,
                                                "output": read_out[:500]})
                close = herdr_run("pane", "close", pane_id)
                pane_record["commands"].append({"argv": "pane close", "exit": close.returncode,
                                                "output": (close.stdout + close.stderr)[:300]})
                gone = herdr_run("pane", "get", pane_id)
                disposed = "pane_not_found" in (gone.stdout + gone.stderr)
                pane_record["commands"].append({"argv": "pane get", "exit": gone.returncode,
                                                "output": (gone.stdout + gone.stderr)[:300]})
                pane_record["pane_id"] = pane_id
                if (run_out.returncode == 0 and close.returncode == 0 and disposed
                        and seen_marker):
                    check("disposable-pane", "passed",
                          f"herdr pane {pane_id} split, ran, read back, closed, "
                          "and is gone", pane_id=pane_id)
                elif run_out.returncode == 0 and close.returncode == 0 and disposed:
                    check("disposable-pane", "blocked",
                          f"herdr pane {pane_id} lifecycle verified but its output "
                          "was not observed; not counting unobserved output",
                          pane_id=pane_id)
                    record["blocked_capability"] = "herdr-pane-output"
                else:
                    check("disposable-pane", "failed",
                          f"herdr pane {pane_id or '?'} lifecycle misbehaved; "
                          "disposal must be exact", pane_id=pane_id)
            else:
                check("disposable-pane", "failed",
                      "herdr pane split did not yield a disposable pane id")
        finally:
            shutil.rmtree(pane_tmp, ignore_errors=True)
    else:
        check("disposable-pane", "blocked",
              "herdr CLI unavailable or outside a herdr pane; pane lifecycle "
              "not exercised")
        if "blocked_capability" not in record:
            record["blocked_capability"] = "herdr-disposable-pane"
    record["pane"] = pane_record
    if failed:
        record["status"] = "failed"
    elif blocked:
        record["status"] = "blocked"
        record.setdefault("blocked_capability", blocked[0])
    else:
        record["status"] = "passed"
    record["mode"] = mode
    record["finished_at"] = stamp()
    publish_json(artifact, freeze_record(with_history(record)))
    print(f"qualification {role}: {record['status']} (mode {mode}; event {key}; "
          f"session {sid}; {len(record['checks'])} checks)")
    print(f"artifact: {artifact}")
    if mode != "unattended":
        print("production stays manual: no verified turn boundary, so no "
              "unattended wake reliability is claimed")
    if failed:
        die(f"qualification {role} failed: " + "; ".join(failed))


QUALIFICATION_REQUIRED_CHECKS = ("probe", "hook-announce", "hook-kill-restart",
                                 "claim", "send", "ack", "generation-reuse",
                                 "retry-release", "active-input", "disposable-pane")


def qualification_artifact_path(d: Path, role: str) -> Path:
    return d / ".delivery" / f"qualification-{role}.json"


def qualification_problems(d: Path, run: str, role: str,
                             artifact: Path | None = None) -> list[str]:
    """Why a delivery qualification artifact cannot satisfy release.

    Only a complete `passed` qualification counts: the artifact must be
    frozen (its own digest recomputes), belong to this run and role, carry
    every required M6.4 check with a passing result plus captured commands,
    identities, and exits, and bind the probe, event, session, and
    adapter/harness version. `blocked`, `failed`, unreadable, malformed, and
    hand-written artifacts each name their exact reason. An explicit
    `artifact` path lets a frozen release revalidate its own copies instead
    of trusting mutable live files.
    """
    path = artifact if artifact is not None else qualification_artifact_path(d, role)
    if not path.is_file():
        return [f"no delivery qualification artifact for {role} "
                "(run delivery --qualify)"]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return [f"delivery qualification artifact {path.name} is unreadable "
                "or malformed"]
    if not isinstance(data, dict):
        return [f"delivery qualification artifact {path.name} is malformed; "
                "only frozen qualification records count"]
    digest_problems = check_frozen_digest(data)
    if digest_problems:
        return [f"delivery qualification artifact {path.name} is not frozen "
                "evidence (" + "; ".join(digest_problems) + ")"]
    problems: list[str] = []
    if str(data.get("run", "")) != run:
        problems.append(f"delivery qualification {path.name} belongs to run "
                        f"{data.get('run', '')!r}, not {run!r}")
    if str(data.get("role", "")) != role:
        problems.append(f"delivery qualification {path.name} belongs to role "
                        f"{data.get('role', '')!r}, not {role!r}")
    status = str(data.get("status", "") or "missing")
    if status != "passed":
        problems.append(f"delivery qualification {role} is {status}, not passed; "
                        "only a complete passed qualification satisfies release")
        return problems
    if not str(data.get("mode", "") or "").strip():
        problems.append(f"delivery qualification {role} records no delivery mode")
    started = str(data.get("started_at", ""))
    finished = str(data.get("finished_at", ""))
    if not started or not finished:
        problems.append(f"delivery qualification {role} records no bounded "
                        "timestamps")
    elif finished < started:
        problems.append(f"delivery qualification {role} timestamps run backwards")
    checks = data.get("checks", [])
    by_name = {c.get("check", ""): c for c in checks
               if isinstance(c, dict) and c.get("check")}
    for name in QUALIFICATION_REQUIRED_CHECKS:
        entry = by_name.get(name)
        if entry is None:
            problems.append(f"delivery qualification {role} is missing required "
                            f"check {name!r}")
            continue
        if str(entry.get("status", "")) != "passed":
            problems.append(f"delivery qualification {role} check {name!r} is "
                            f"{entry.get('status', '')!r}, not passed")
            continue
        if not str(entry.get("detail", "") or "").strip():
            problems.append(f"delivery qualification {role} check {name!r} "
                            "records no evidence detail")
        if name in ("hook-announce", "hook-kill-restart"):
            if not isinstance(entry.get("pid"), int):
                problems.append(f"delivery qualification {role} check {name!r} "
                                "records no process identity")
            if not isinstance(entry.get("exit"), int):
                problems.append(f"delivery qualification {role} check {name!r} "
                                "records no exit status")
        if name in ("claim", "send", "ack", "generation-reuse", "retry-release",
                    "active-input"):
            if not isinstance(entry.get("argv"), list) or not entry["argv"]:
                problems.append(f"delivery qualification {role} check {name!r} "
                                "records no captured command")
            if not isinstance(entry.get("exit"), int):
                problems.append(f"delivery qualification {role} check {name!r} "
                                "records no exit status")
        if name == "disposable-pane" and not str(entry.get("pane_id", "") or "").strip():
            problems.append(f"delivery qualification {role} check {name!r} "
                            "records no disposable pane identity")
    event = data.get("event", {})
    if not isinstance(event, dict) or not str(event.get("key", "") or "").strip():
        problems.append(f"delivery qualification {role} binds no event identity")
    session = data.get("session", {})
    if not isinstance(session, dict) or not str(session.get("id", "") or "").strip():
        problems.append(f"delivery qualification {role} binds no session identity")
    probe = data.get("probe", {})
    if not isinstance(probe, dict) or not str(probe.get("herdr_version", "") or "").strip():
        problems.append(f"delivery qualification {role} binds no adapter/harness version")
    if not str(data.get("repo_head", "") or "").strip():
        problems.append(f"delivery qualification {role} binds no source revision")
    source_tree = str(data.get("source_tree", "") or "")
    if not source_tree:
        problems.append(f"delivery qualification {role} binds no content tree; "
                        "re-qualify with a binary that records worktree identity")
    elif not source_tree.startswith("sha256:"):
        if source_tree.startswith("unknown "):
            problems.append(f"delivery qualification {role} binds unknown source tree "
                            f"{source_tree!r}; unknown trees are diagnostic only, never "
                            "release evidence - re-qualify against a git checkout with "
                            "a valid sha256 identity")
        else:
            problems.append(f"delivery qualification {role} carries a malformed "
                            "content tree binding")
    return problems


def cmd_delivery(a: argparse.Namespace) -> None:
    """Pause, probe, queue, and qualifiedly deliver role notifications."""
    d = need_run(a.run)
    role = a.role
    require_preset_role(d, role)
    if a.pause:
        publish(paused_flag(d, role), f"paused_at: {stamp()}\n")
        print(f"paused delivery for {a.run} {role}; implementation continues, events queue")
        return
    if a.resume:
        paused_flag(d, role).unlink(missing_ok=True)
        print(f"resumed delivery for {a.run} {role}")
        return
    if a.queued:
        derived = derive_events(d, role)
        state = "paused" if paused_flag(d, role).is_file() else "live"
        print(f"{a.run} {role}: delivery {state}, {len(derived)} queued event(s)")
        for ev in derived:
            print(f"  {ev['key']}")
            print(f"    {ev['message']}")
        return
    if a.probe:
        boundary, mode, detail = probe_boundary()
        print(f"boundary: {boundary}")
        print(f"mode: {mode}")
        print(f"detail: {detail}")
        if mode != "unattended":
            print("No unattended wake reliability is claimed; use explicit inbox pickup "
                  "or a native hook.")
        return
    if a.service_status:
        boundary, mode, _ = probe_boundary()
        print(f"supervised delivery service: none (never a model-launched background job)")
        print(f"mode: {mode} (boundary: {boundary})")
        for known in KNOWN_EVENT_ROLES:
            flag = "paused" if paused_flag(d, known).is_file() else "live"
            count = len(derive_events(d, known))
            print(f"  {known}: {flag}, {count} queued event(s)")
        return
    if a.qualify:
        artifact = delivery_dir(d, role).parent / f"qualification-{role}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        live_root = root()
        with qualification_sandbox(d, role) as sandbox:
            cmd_delivery_qualify(sandbox, a.run, role, a.session, artifact=artifact,
                                 live_root=live_root)
        return
    if a.send:
        if not a.session:
            die("--send requires --session SESSION of the registered recipient")
        with delivery_lock(d, role):
            sweep_role(d, role)
        if paused_flag(d, role).is_file():
            derived = derive_events(d, role)
            print(f"queued ({len(derived)} event(s)): delivery for {a.run} {role} is paused; "
                  "pausing consumes no events and pauses no implementation")
            return
        if input_active_flag(d, role).is_file():
            print(f"queued: recipient input is active for {a.run} {role}; no notice was "
                  "inserted into the uncertain input buffer. The event stays queued for "
                  "explicit inbox pickup.")
            return
        _boundary, mode, detail = probe_boundary()
        lease, pending, status = claim_event(d, a.run, role, a.session, key=a.send,
                                             lease_seconds=a.lease_secs)
        notice = fixed_notice(a.run, role, a.send, a.session)
        publish(outbox_path(d, role, a.send), notice)
        delivery_log(d, role, {"kind": "sent", "key": a.send, "session": a.session,
                               "mode": mode, "status": status})
        fault("delivery:send")
        if status == "already-held":
            print(f"already delivered to this session: {a.send}")
        else:
            print(f"delivered {a.send} to session {a.session} (attempt {lease.get('attempt', 1)})")
        if mode != "unattended":
            print(f"mode: {mode} - outbox notice for explicit inbox pickup or a native hook; "
                  "no unattended wake reliability claimed")
            print(f"probe: {detail}")
        print(notice, end="")
        return
    die("use --pause, --resume, --queued, --probe, --send EVENT, --service-status, "
        "or --qualify")
