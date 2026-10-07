"""Dispatch liveness, session registrations, and dispatch readiness."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .common import STATE_DIR, die, now_s, stamp
from .paths import read_dispatch, root
from .publication import publish_json
from .locks import owner_lock
from .policy import is_tiered, is_quick_milestone
from .state import list_batches, quick_submitted_pass, state_of, task_depends_on
from .verification import verifier_exempt_set as _verifier_exempt_set
from .verification import current_round_digest as _current_round_digest
from .verification import current_task_revision as _current_task_revision
from .verification import matching_verifications as _matching_verifications


def sessions_dir() -> Path:
    return root() / STATE_DIR / "sessions"


def check_session_id(sid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", sid or ""):
        die(f"invalid session id {sid!r}: use letters, digits, '.', '_' or '-'")
    return sid


def session_path(sid: str) -> Path:
    return sessions_dir() / f"{check_session_id(sid)}.json"


def read_registration(sid: str) -> dict:
    """The session registration, or an empty dict when there is none."""
    path = session_path(sid)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


TERMINAL_REPORT_STATES = {"approved", "waived", "completed"}
WORKER_EXIT_GRACE_SECONDS = 60


def worker_process_state(record: dict) -> str:
    """Observed harness process health; unknown when no worker process was proven."""
    process = record.get("worker_process") if isinstance(record, dict) else None
    if not isinstance(process, dict):
        return "unknown"
    try:
        pid = int(process.get("pid", 0) or 0)
        noted = datetime.fromisoformat(str(process.get("noted_at", "")).replace(
            "Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return "unknown"
    if pid <= 0:
        return "unknown"
    if now_s() - noted < WORKER_EXIT_GRACE_SECONDS:
        return "grace"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "exited"
    except PermissionError:
        return "alive"
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z" or (process.get("start") and fields[19] != process["start"]):
            return "exited"
    except (OSError, ValueError, IndexError):
        pass
    return "alive"


def _tiered_settled_live(d: Path, member: str) -> bool:
    """Whether a tiered run treats this member as settled, using allowed imports only.

    Liveness sits after verification in MODULES, so it reads resolved verification
    directly instead of importing the later batches module.
    """
    try:
        if not is_tiered(d):
            return False
    except SystemExit:
        return False
    try:
        rnd, st = state_of(d, member)
    except (OSError, ValueError):
        return False
    if st != "submitted" or not rnd:
        return False
    if is_quick_milestone(d):
        return quick_submitted_pass(d, member, rnd)
    try:
        if member in _verifier_exempt_set(d):
            return True
        digest = _current_round_digest(d, member, rnd)
        contract_rev = _current_task_revision(d, member)
        matched = _matching_verifications(d, member, rnd, digest, contract_rev)
    except (OSError, ValueError):
        return False
    if not matched:
        return False
    _, vmeta, _ = matched[-1]
    result = str(vmeta.get("result", ""))
    if str(vmeta.get("opened_correction", "")) in ("yes", "requested"):
        return False
    return result == "pass"


def dispatch_liveness(d: Path, record: dict) -> tuple[bool, str]:
    """Whether one dispatch record still describes live work.

    The answer is derived from the lifecycle documents, never from the record
    itself apart from its owner, round, and session identifiers. A record is
    live only when its state is dispatched, its owner still has an assignment,
    its round is the current report round, that round is not terminal, and its
    session registration still names this run, role, and workspace. A newer
    registration generation for the same session name stays live here: that is
    a different writer to be refused at dispatch time with a resume pointer,
    not a slot to be quietly reconciled away. Model fields are never read here.
    """
    if not isinstance(record, dict) or record.get("state") != "dispatched":
        return False, "not-dispatched"
    owner = str(record.get("owner", ""))
    if not owner:
        return False, "missing owner"
    if owner != "orch" and not (d / f"{owner}-task.mdx").is_file():
        return False, "no assignment"
    try:
        rnd, st = state_of(d, owner)
    except (OSError, ValueError):
        return False, "unreadable report"
    if not rnd:
        return False, "no report"
    try:
        rec_round = int(record.get("round", 0) or 0)
    except (TypeError, ValueError):
        return False, "unreadable round"
    if rec_round != rnd:
        return False, f"superseded: record round {rec_round} != current round {rnd}"
    if st in TERMINAL_REPORT_STATES:
        return False, f"round {rnd} {st}"
    if st == "submitted":
        try:
            if _tiered_settled_live(d, owner):
                return False, f"round {rnd} verification-settled for milestone review"
        except (OSError, ValueError):
            pass
    sess = str(record.get("session", ""))
    if sess:
        reg = read_registration(sess)
        if not reg:
            return False, f"session {sess} unknown"
        if str(reg.get("run", "")) != d.name:
            return False, f"session {sess} registered for run {reg.get('run')!r}"
        rec_role = str(record.get("role", ""))
        if rec_role and str(reg.get("role", "")) != rec_role:
            return False, f"session {sess} role mismatch"
        try:
            workspace = str(root().resolve())
        except OSError:
            workspace = ""
        if workspace and str(reg.get("workspace", "")) != workspace:
            return False, f"session {sess} workspace moved"
    return True, "live"


def round_dispatched(d: Path, owner: str, rnd: int) -> bool:
    """Whether a live dispatch record binds exactly this owner round."""
    record = read_dispatch(d, owner)
    if not record:
        return False
    try:
        if int(record.get("round", 0) or 0) != rnd:
            return False
    except (TypeError, ValueError):
        return False
    live, _ = dispatch_liveness(d, record)
    return live


def live_dispatches(d: Path) -> list[dict]:
    """Execution capacity as live work, derived from lifecycle documents.

    This is the single rule for what counts as a held execution slot. Counting
    and reconciliation both call it, so the two can never disagree about what
    is live. Held scope is a different quantity owned by scope_collisions and
    is never touched here.
    """
    out = []
    if (d / ".dispatch").is_dir():
        for path in sorted((d / ".dispatch").glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            live, _ = dispatch_liveness(d, data)
            if live:
                out.append(data)
    return out


def active_dispatches(d: Path) -> list[dict]:
    return live_dispatches(d)


def reconcile_dispatches(d: Path) -> list[str]:
    """Reconcile stale dispatch records against lifecycle documents.

    For each record that still reads dispatched but no longer describes live
    work, persist a release that preserves every model field and names the
    document-derived reason. Each owner update runs under that owner's lock
    alone, never holding the run-wide capacity lock at the same time, so a
    concurrent dispatch holding owner plus capacity cannot deadlock against a
    reconcile holding capacity plus owner. Returns the reconciled owner names.
    """
    names: list[str] = []
    if not (d / ".dispatch").is_dir():
        return names
    for path in sorted((d / ".dispatch").glob("*.json")):
        try:
            owner = path.stem
        except ValueError:
            continue
        with owner_lock(d, owner):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("state") != "dispatched":
                continue
            live, reason = dispatch_liveness(d, data)
            if live:
                continue
            data["state"] = "released"
            data["released_reason"] = reason
            data["released_at"] = stamp()
            publish_json(path, data)
            names.append(f"{owner}: {reason}")
    return sorted(set(names))


# A waived dependency is settled: the reviewer accepted it as it stands. A dependent
# gated only on approval could never dispatch and nothing would wake anyone about it,
# so a waiver releases it, and dispatch says the work beneath it was never passed.
DEPENDENCY_MET_STATES = {"approved", "completed", "waived"}


def dispatch_dependencies_unmet(d: Path, owner: str) -> list[str]:
    """Task and batch dependencies that are not settled yet.

    Approved, completed, or waived work always satisfies a dependency. Under the
    tiered policy a submitted predecessor with resolved verification also
    satisfies it for dispatch: the verifier settled the round for sequencing,
    while the milestone verdict still decides approval. Readiness is never
    approval, and a settled input that later moves still withdraws readiness.
    """
    terminal = DEPENDENCY_MET_STATES

    def settled(dep: str) -> bool:
        if state_of(d, dep)[1] in terminal:
            return True
        try:
            return bool(_tiered_settled_live(d, dep))
        except (OSError, ValueError):
            return False

    unmet = []
    for dep in task_depends_on(d, owner):
        if not settled(dep):
            unmet.append(f"{dep} ({state_of(d, dep)[1]})")
    for batch in list_batches(d):
        if owner in [str(m) for m in (batch.get("members") or [])]:
            for dep in ((batch.get("depends_on") or {}).get(owner) or []):
                if not settled(str(dep)):
                    unmet.append(f"{dep} ({state_of(d, str(dep))[1]}) via batch {batch.get('batch')}")
    return sorted(set(unmet))
