"""Leased event delivery: pending records, claims, announcements, and the inbox."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import re
from pathlib import Path

from .common import (
    DELIVERY_LEASE_SECONDS, KNOWN_EVENT_ROLES, WORKFLOW_LEGACY_DECODE, die, now_s, stamp,
)
from .paths import root
from .publication import fault, publish, publish_json
from .policy import max_concurrency_of, need_run, require_preset_role
from .liveness import (
    check_session_id, live_dispatches, read_registration, reconcile_dispatches,
    session_path, sessions_dir,
)
from .events import derive_events


def delivery_dir(d: Path, role: str) -> Path:
    return d / ".delivery" / role


def delivery_slug(key: str) -> str:
    """A stable, filename-safe identity for one event key."""
    short = re.sub(r"[^A-Za-z0-9]+", "-", key)[:40].strip("-") or "event"
    return f"{short}-{hashlib.sha1(key.encode()).hexdigest()[:12]}"


def pending_path(d: Path, role: str, key: str) -> Path:
    return delivery_dir(d, role) / "pending" / f"{delivery_slug(key)}.json"


def lease_path(d: Path, role: str, key: str) -> Path:
    return delivery_dir(d, role) / "leases" / f"{delivery_slug(key)}.json"


def announce_dir(d: Path, role: str) -> Path:
    return delivery_dir(d, role) / "announce"


def announce_path(d: Path, role: str, key: str) -> Path:
    return announce_dir(d, role) / f"{delivery_slug(key)}.json"


def read_announce(d: Path, role: str, key: str) -> dict:
    path = announce_path(d, role, key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def announce_active(d: Path, role: str, key: str) -> bool:
    """Whether a native-hook announcement lease still covers this key.

    The ledger alone is not delivery truth: a watcher that crashes after
    writing the ledger but before the harness accepts the wake must not
    strand the event. An announcement lease bounds the suppression, so a
    restart re-announces the same actionable event once the lease expires
    while concurrent watchers still converge on one wake.
    """
    record = read_announce(d, role, key)
    if not record:
        return False
    try:
        until = float(record.get("lease_until", 0) or 0)
    except (TypeError, ValueError):
        return False
    return until > now_s()


def announce_delivered(d: Path, role: str, key: str) -> bool:
    """Whether the watcher handed this announcement to the harness.

    The watcher marks an announcement delivered only after its banner is
    written and immediately before it exits 2, so a crash anywhere before that
    leaves the mark absent and the lease-bounded re-announcement still applies.
    """
    return bool(read_announce(d, role, key).get("delivered_at"))


def mark_announce_delivered(d: Path, role: str, key: str) -> None:
    record = read_announce(d, role, key)
    if not record:
        return
    record["delivered_at"] = now_s()
    publish_json(announce_path(d, role, key), record)


@contextlib.contextmanager
def delivery_lock(d: Path, role: str):
    """Serialize one role's delivery mutations. Derivation stays lock-free."""
    path = delivery_dir(d, role) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def delivery_log(d: Path, role: str, entry: dict[str, object]) -> None:
    """Append one transport record. The log is append-only; nothing rewrites it."""
    log = delivery_dir(d, role) / "log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": stamp(), **entry}, sort_keys=True) + "\n"
    with log.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def check_registration(d: Path, sid: str, run: str, role: str) -> dict:
    """Validate a session registration before it may claim or acknowledge work.

    The registration is an accidental-misrouting guard, not a security
    boundary against another process with the same filesystem permissions.
    """
    reg = read_registration(sid)
    if not reg:
        die(f"unknown session {sid!r}; register it with `docket session {run} --register "
            f"--session {sid} --name NAME --role {role}`")
    if str(reg.get("run", "")) != run:
        die(f"session {sid!r} is registered for run {reg.get('run')!r}, not {run!r}")
    if str(reg.get("role", "")) != role:
        die(f"session {sid!r} is registered for role {reg.get('role')!r}, not {role!r}")
    if str(reg.get("workspace", "")) != str(root().resolve()):
        die(f"session {sid!r} is registered for workspace {reg.get('workspace')!r}; "
            "re-register it here before claiming work")
    return reg


def event_actionable(d: Path, ev: dict) -> tuple[bool, str]:
    """Whether a derived event still describes current durable state.

    A delayed event is actionable only when its key is still derived and its
    round, artifact revision, destination role, workflow, and generation still
    match. Anything else is stale and must be retired, never acted on.
    """
    role = str(ev.get("role", ""))
    key = str(ev.get("key", ""))
    current = {str(e["key"]): e for e in derive_events(d, role)}
    now = current.get(key)
    if now is None:
        return False, f"{key} is no longer derived from current lifecycle state"
    for field in ("round", "revision", "role", "workflow", "generation"):
        if str(now.get(field, "")) != str(ev.get(field, "")):
            return False, (f"{key} is stale for {field}: derived {now.get(field)!r}, "
                           f"event holds {ev.get(field)!r}")
    return True, ""


def retire_event(d: Path, role: str, key: str, why: str) -> None:
    """Drop a stale pending event and its lease, and record the retirement."""
    pending_path(d, role, key).unlink(missing_ok=True)
    lease_path(d, role, key).unlink(missing_ok=True)
    announce_path(d, role, key).unlink(missing_ok=True)
    delivery_log(d, role, {"kind": "retired", "key": key, "reason": why})


def ensure_pending(d: Path, role: str, ev: dict[str, object]) -> tuple[Path, bool]:
    """Make sure a derived event has a durable pending record. Idempotent."""
    path = pending_path(d, role, str(ev["key"]))
    if path.is_file():
        return path, False
    record = {k: ev.get(k, "") for k in ("key", "role", "workflow", "owner", "round",
                                         "generation", "revision", "message")}
    record["derived_at"] = stamp()
    record["attempts"] = 0
    publish_json(path, record)
    delivery_log(d, role, {"kind": "derived", "key": str(ev["key"])})
    return path, True


def sweep_role(d: Path, role: str) -> tuple[list[str], list[str]]:
    """Reconcile durable pending records with current derivation.

    Missing records for still-actionable events are recreated; records that
    are no longer derived, or whose identity no longer matches, are retired.
    Repeated and concurrent sweeps converge on one record per logical event.
    """
    derived = derive_events(d, role)
    by_key = {str(e["key"]): e for e in derived}
    for ev in derived:
        ensure_pending(d, role, ev)
    retired: list[str] = []
    pending_dir = delivery_dir(d, role) / "pending"
    if pending_dir.is_dir():
        for path in sorted(pending_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            key = str(record.get("key", ""))
            now = by_key.get(key)
            if now is None:
                retire_event(d, role, key, "no longer derived from lifecycle state")
                retired.append(key)
            elif any(str(now.get(f, "")) != str(record.get(f, "")) for f in
                     ("round", "revision", "role", "workflow", "generation")):
                retire_event(d, role, key, "superseded by a newer derivation")
                retired.append(key)
                ensure_pending(d, role, now)
    return sorted(by_key), sorted(retired)


def read_pending(d: Path, role: str, key: str) -> dict:
    path = pending_path(d, role, key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_lease(d: Path, role: str, key: str) -> dict:
    path = lease_path(d, role, key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_reconcile(a: argparse.Namespace) -> None:
    """Rebuild derived pending events from documents and retire stale ones.

    Idempotent: repeated and concurrent runs converge on one record per
    logical event. Read-only inspection commands never do this as a side
    effect; reconciliation is always explicit. Dispatch records are reconciled
    the same way: a record whose round, owner, or session no longer describes
    live work is released against the lifecycle documents and named.
    """
    d = need_run(a.run)
    if a.role:
        require_preset_role(d, a.role)
    roles = [a.role] if a.role else list(KNOWN_EVENT_ROLES)
    total_derived: list[str] = []
    total_retired: list[str] = []
    for role in roles:
        with delivery_lock(d, role):
            derived, retired = sweep_role(d, role)
        total_derived.extend(f"{role}:{key}" for key in derived)
        total_retired.extend(f"{role}:{key}" for key in retired)
    reconciled = reconcile_dispatches(d)
    live = live_dispatches(d)
    cap = max_concurrency_of(d)
    fault("delivery:reconcile")
    print(f"run {a.run}: {len(total_derived)} derived event(s), {len(total_retired)} retired")
    for item in sorted(total_retired):
        print(f"  retired {item}")
    for item in sorted(total_derived):
        print(f"  derived {item}")
    for item in reconciled:
        print(f"  reconciled dispatch {item}")
    if cap:
        print(f"  live execution capacity: {len(live)} live dispatch record(s) (cap {cap})")
    else:
        print(f"  live execution capacity: {len(live)} live dispatch record(s)")


def cmd_inbox(a: argparse.Namespace) -> None:
    """Claim one pending event under a bounded lease bound to a registration."""
    d = need_run(a.run)
    require_preset_role(d, a.role)
    if not a.claim:
        die("only --claim is supported: `docket inbox RUN --role ROLE --claim --session SESSION`")
    lease, pending, status = claim_event(d, a.run, a.role, a.session,
                                         lease_seconds=a.lease_secs)
    fault("delivery:before-send")
    key = str(lease["key"])
    if status == "already-held":
        print(f"already held by this session: {key}")
        print(f"  {pending.get('message', '')}")
        print(f"  lease until {lease.get('lease_until')} (attempt {lease.get('attempt', 0)})")
        return
    print(f"claimed {key} for session {a.session} (attempt {lease.get('attempt', 1)})")
    print(f"  {pending.get('message', '')}")
    print(f"  lease until {lease.get('lease_until')}; ack with "
          f"`docket events {a.run} --ack {key} --session {a.session}`")


def cmd_session(a: argparse.Namespace) -> None:
    """Register a delivery session, or list a run's registrations.

    A registration binds workspace, run, role, session ID, and session
    generation. Re-registration advances the generation without rewriting
    prior transport history, so a restarted session reusing an ID can never
    inherit the old generation's leases.
    """
    d = need_run(a.run)
    if a.list:
        found = []
        if sessions_dir().is_dir():
            for path in sorted(sessions_dir().glob("*.json")):
                reg = read_registration(path.stem)
                if reg.get("run") == a.run:
                    found.append(reg)
        if not found:
            print(f"run {a.run}: no registered sessions")
            return
        print(f"run {a.run}: {len(found)} registered session(s)")
        for reg in found:
            print(f"  {reg.get('session')}  role={reg.get('role')}  "
                  f"generation={reg.get('generation')}  name={reg.get('name')}")
        return
    if not a.register:
        die("use --register to create a registration or --list to show them")
    require_preset_role(d, a.role)
    sid = check_session_id(a.session)
    workspace = str(root().resolve())
    previous = read_registration(sid)
    if previous and str(previous.get("run", "")) != a.run:
        die(f"session {sid!r} is registered for run {previous.get('run')!r}; use a new session id")
    try:
        generation = int(previous.get("generation", 0) or 0) + 1
    except (TypeError, ValueError):
        generation = 1
    record: dict[str, object] = {
        "session": sid,
        "run": a.run,
        "role": a.role,
        "workspace": workspace,
        "generation": generation,
        "name": a.name or sid,
        "registered_at": stamp(),
    }
    if previous:
        record["previous_name"] = previous.get("name", "")
    publish(sessions_dir() / f"{sid}.json",
            json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"registered session {sid} for {a.run} {a.role} (generation {generation})")


def ensure_session_registration(run: str, sid: str, role: str, name: str) -> dict:
    """Register a worker session for dispatch unless it is already registered here.

    Unlike `docket session --register`, an existing matching registration is
    reused rather than advanced, so a retried dispatch stays the same writer.
    """
    check_session_id(sid)
    reg = read_registration(sid)
    workspace = str(root().resolve())
    if reg:
        if str(reg.get("run", "")) != run:
            die(f"session {sid!r} is registered for run {reg.get('run')!r}; use a new session id")
        if str(reg.get("role", "")) != role:
            die(f"session {sid!r} is registered for role {reg.get('role')!r}, not {role!r}")
        if str(reg.get("workspace", "")) != workspace:
            die(f"session {sid!r} is registered for workspace {reg.get('workspace')!r}; "
                f"re-register it here with `docket session {run} --register --session {sid} "
                f"--name NAME --role {role}`")
        return reg
    record: dict[str, object] = {
        "session": sid, "run": run, "role": role, "workspace": workspace,
        "generation": 1, "name": name or sid, "registered_at": stamp(),
    }
    publish(session_path(sid), json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"registered session {sid} for {run} {role} (generation 1)")
    return record


def inbox_ack(a: argparse.Namespace, d: Path) -> None:
    """Record receipt of a claimed event. Receipt is not completion."""
    with delivery_lock(d, a.role):
        pending = read_pending(d, a.role, a.ack)
        if not pending:
            print(f"{a.ack}: unknown or already retired; nothing to acknowledge")
            return
        lease = read_lease(d, a.role, a.ack)
        if not lease:
            die(f"{a.ack} is not claimed; claim it with "
                f"`docket inbox {a.run} --role {a.role} --claim --session {a.session}` first")
        reg = check_registration(d, a.session, a.run, a.role)
        if str(lease.get("session", "")) != a.session or str(
                lease.get("session_generation", "")) != str(reg.get("generation", 1)):
            die(f"{a.ack} is held by session {lease.get('session')!r} "
                f"(generation {lease.get('session_generation')!r}); stale registrations cannot "
                "acknowledge another session's claim")
        try:
            until = float(lease.get("lease_until", 0) or 0)
        except (TypeError, ValueError):
            until = 0
        if until <= now_s() and not lease.get("acked"):
            die(f"{a.ack}: the handling lease expired; re-claim it with "
                f"`docket inbox {a.run} --role {a.role} --claim --session {a.session}`")
        ok, why = event_actionable(d, pending)
        if not ok:
            retire_event(d, a.role, a.ack, f"acknowledged after becoming stale: {why}")
            die(f"{a.ack} is stale and was retired: {why}")
        if lease.get("acked") and str(lease.get("session", "")) == a.session:
            delivery_log(d, a.role, {"kind": "duplicate-receipt", "key": a.ack,
                                     "session": a.session})
            print(f"{a.ack}: already received; duplicate acknowledgement is harmless")
            return
        try:
            lease_seconds = float(lease.get("lease_seconds", 0) or DELIVERY_LEASE_SECONDS)
        except (TypeError, ValueError):
            lease_seconds = DELIVERY_LEASE_SECONDS
        lease["acked"] = True
        lease["lease_until"] = now_s() + lease_seconds
        publish_json(lease_path(d, a.role, a.ack), lease)
        delivery_log(d, a.role, {"kind": "receipt", "key": a.ack, "session": a.session})
        fault("delivery:receipt")
        print(f"received {a.ack} (handling lease until {lease['lease_until']})")
        print("Receipt is not completion: the durable verification finding, decision, or")
        print("resolution retires the event automatically.")


def inbox_retry(a: argparse.Namespace, d: Path) -> None:
    """Record why another delivery attempt is due and release the lease."""
    reason = (a.reason or "").strip()
    if not reason:
        die("--retry requires --reason TEXT")
    with delivery_lock(d, a.role):
        pending = read_pending(d, a.role, a.retry)
        if not pending:
            die(f"{a.retry}: unknown or already retired; nothing to retry")
        ok, why = event_actionable(d, pending)
        if not ok:
            retire_event(d, a.role, a.retry, f"retried after becoming stale: {why}")
            die(f"{a.retry} is stale and was retired: {why}")
        lease_path(d, a.role, a.retry).unlink(missing_ok=True)
        delivery_log(d, a.role, {"kind": "retry", "key": a.retry, "reason": reason})
        fault("delivery:retry")
        print(f"{a.retry} will be attempted again: {reason}")


def paused_flag(d: Path, role: str) -> Path:
    return delivery_dir(d, role) / "paused"


def input_active_flag(d: Path, role: str) -> Path:
    return delivery_dir(d, role) / "input-active"


def outbox_path(d: Path, role: str, key: str) -> Path:
    return delivery_dir(d, role) / "outbox" / f"{delivery_slug(key)}.txt"


def claim_event(d: Path, run: str, role: str, session: str, key: str = "",
                lease_seconds: int = 0) -> tuple[dict, dict, str]:
    """Claim one pending event (or one specific key) for a registered session.

    Shared by inbox pickup and qualified delivery so both paths enforce the
    same registration, lease, and actionability rules. Returns the lease, the
    pending record, and whether the lease was newly `claimed` or `already-held`.
    """
    reg = check_registration(d, session, run, role)
    generation = reg.get("generation", 1)
    seconds = lease_seconds if lease_seconds and lease_seconds > 0 else DELIVERY_LEASE_SECONDS
    with delivery_lock(d, role):
        sweep_role(d, role)
        if key:
            keys = [key] if read_pending(d, role, key) else []
            if not keys:
                die(f"{key}: unknown or already retired for role {role}")
        else:
            pending_dir = delivery_dir(d, role) / "pending"
            keys = []
            if pending_dir.is_dir():
                for path in sorted(pending_dir.glob("*.json")):
                    try:
                        record = json.loads(path.read_text())
                    except (OSError, ValueError):
                        continue
                    if record.get("key"):
                        keys.append(str(record["key"]))
            keys.sort()
        for candidate in keys:
            pending = read_pending(d, role, candidate)
            if not pending:
                continue
            lease = read_lease(d, role, candidate)
            if lease:
                try:
                    until = float(lease.get("lease_until", 0) or 0)
                except (TypeError, ValueError):
                    until = 0
                if until > now_s():
                    if (str(lease.get("session", "")) == session
                            and str(lease.get("session_generation", "")) == str(generation)):
                        return lease, pending, "already-held"
                    continue
                delivery_log(d, role, {"kind": "expired", "key": candidate,
                                       "session": str(lease.get("session", ""))})
            ok, why = event_actionable(d, pending)
            if not ok:
                retire_event(d, role, candidate, f"claimed after becoming stale: {why}")
                if key:
                    die(f"{key} is stale and was retired: {why}")
                continue
            try:
                attempt = int(pending.get("attempts", 0) or 0) + 1
            except (TypeError, ValueError):
                attempt = 1
            claimed_at = now_s()
            record = {
                "key": candidate,
                "role": role,
                "owner": pending.get("owner", ""),
                "round": pending.get("round", 0),
                "generation": pending.get("generation", 1),
                "revision": pending.get("revision", ""),
                "workflow": pending.get("workflow", WORKFLOW_LEGACY_DECODE),
                "session": session,
                "session_generation": generation,
                "session_name": reg.get("name", ""),
                "claimed_at": claimed_at,
                "lease_until": claimed_at + seconds,
                "lease_seconds": seconds,
                "attempt": attempt,
                "acked": False,
            }
            publish_json(lease_path(d, role, candidate), record)
            pending["attempts"] = attempt
            publish_json(pending_path(d, role, candidate), pending)
            delivery_log(d, role, {"kind": "claimed", "key": candidate, "session": session,
                                   "attempt": attempt, "lease_until": record["lease_until"]})
            return record, pending, "claimed"
        if key:
            die(f"{key} is not claimable for role {role}")
        held = []
        leases_dir = delivery_dir(d, role) / "leases"
        if leases_dir.is_dir():
            for path in leases_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if data.get("key"):
                    held.append(f"{data['key']} (session {data.get('session')})")
        if held:
            die(f"no claimable events for role {role}: all pending events are leased:\n      "
                + "\n      ".join(f"- {item}" for item in sorted(held)))
        die(f"no pending events for role {role}")
