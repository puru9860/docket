"""Five-role correction budgets, escalations, blocked-report routing, and mode escalation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .common import MODE_QUICK, MODE_STANDARD, die, stamp
from .frontmatter import render
from .paths import _ordered, decisions, next_numbered, root
from .publication import publish_exclusive, publish_json
from .policy import (
    artifact_policy_fields, correction_limit_of, is_five_role, mode_of, need_run,
    plan_owner_role, task_executor, verifier_role_of,
)
from .bundles import digest_of
from .locks import owner_lock, run_lock
from .state import armed, corrections_path, latest_verification, read_corrections, state_of
from .feedback import machine_feedback
from .models import record_outcome


def correction_attempts(record: dict) -> int:
    """Returns of work to the implementor; gate refusals before handoff are not returns."""
    total = 0
    for key in ("verifier_returns", "reviewer_returns"):
        try:
            total += int(record.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def note_correction(d: Path, owner: str, kind: str, token: str = "") -> dict:
    """Count one local correction attempt. Only meaningful in five-role runs.

    A token names the artifact the attempt belongs to, so retrying an
    interrupted command that already counted it never charges the budget twice.
    """
    record = read_corrections(d, owner)
    if not record:
        try:
            rnd, _ = state_of(d, owner)
        except (OSError, ValueError):
            rnd = 0
        record = {"owner": owner, "chain_start_round": rnd, "gate_repairs": 0,
                  "verifier_returns": 0, "reviewer_returns": 0, "escalated": ""}
    counted = record.get("counted")
    counted = [str(item) for item in counted] if isinstance(counted, list) else []
    if token and token in counted:
        return record
    record[kind] = int(record.get(kind, 0) or 0) + 1
    if token:
        record["counted"] = counted + [token]
    publish_json(corrections_path(d, owner), record)
    return record


def correction_budget(d: Path, owner: str, record: dict | None = None) -> int:
    """The plan's correction limit plus any rounds the plan owner granted this chain."""
    record = read_corrections(d, owner) if record is None else record
    try:
        granted = int(record.get("granted", 0) or 0)
    except (TypeError, ValueError):
        granted = 0
    return correction_limit_of(d) + max(0, granted)


def clear_corrections(d: Path, owner: str) -> None:
    corrections_path(d, owner).unlink(missing_ok=True)
    # An accepting verdict settles the chain, so an escalation it left open
    # would otherwise wake the plan owner for work that is already accepted.
    directory = d / ".escalations"
    for path in sorted(directory.glob(f"{owner}-r*.json")) if directory.is_dir() else []:
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("state") in ("open", "granted"):
            entry["state"] = "closed"
            entry["closed_at"] = stamp()
            entry["closed_by"] = "accepting verdict"
            publish_json(path, entry)


def open_escalations(d: Path, states: tuple[str, ...] = ("open",)) -> list[dict]:
    """Escalation records in the given states, oldest first."""
    directory = d / ".escalations"
    found = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("state") in states:
            entry["_path"] = str(path)
            found.append(entry)
    return found


def escalation_events(d: Path) -> list[tuple[str, str, str, int, str]]:
    """(key, message, owner, round, revision) for chains only the plan owner can extend."""
    out = []
    for entry in open_escalations(d):
        owner = str(entry.get("owner", ""))
        iid = str(entry.get("id", owner))
        attempts = entry.get("attempts") if isinstance(entry.get("attempts"), dict) else {}
        used = sum(int(v or 0) for v in attempts.values())
        out.append((f"{owner}:escalated:{iid}",
                    f"{owner} exhausted its local correction budget ({used} attempts, limit "
                    f"{entry.get('limit', '?')}): grant more rounds with `docket escalation "
                    f"{d.name} {owner} --grant 1 --as {plan_owner_role(d)} --reason TEXT`, "
                    "re-plan the task, or leave it "
                    "for a waiver",
                    owner, state_of(d, owner)[0],
                    f"sha256:{hashlib.sha256(iid.encode()).hexdigest()}"))
    return out


def run_complete_event(d: Path) -> tuple[str, str, str, int, str] | None:
    """(key, message, owner, round, revision) once the aggregate is accepted.

    The plan owner is the session the user talks to; without this wake it keeps
    waiting after the reviewer settles the run and never reports the outcome.
    """
    rnd, st = state_of(d, "orch")
    if st not in ("approved", "waived", "completed"):
        return None
    dec = d / f"orch-decision-{rnd:02d}.mdx"
    revision = digest_of(dec.read_bytes()) if dec.is_file() else f"orch:{rnd}:{st}"
    return (f"orch:{rnd}:complete",
            f"run {d.name} is complete: the aggregate was {st} at round {rnd}. Tell the user the "
            f"outcome, record what docket cost you with `docket feedback {d.name} --add --role "
            f"{plan_owner_role(d)} --category <kind> --body TEXT`, then run `docket usage {d.name} "
            f"--archive` and `docket disarm {d.name}`",
            "orch", rnd, revision)


def cmd_escalation(a: argparse.Namespace) -> None:
    """Grant an exhausted correction chain more rounds; only the plan owner may."""
    d = need_run(a.run)
    owner_role = plan_owner_role(d)
    role = a.as_role
    if not role:
        die(f"escalation --grant requires --as {owner_role}: only the plan owner extends a "
            "correction budget, and the role must be stated, never assumed")
    if role != owner_role:
        die(f"only the {owner_role} extends a correction budget in {a.run}; the budget is "
            "plan policy, and the role that hit it cannot raise it")
    if a.grant < 1:
        die("--grant N needs N of at least 1")
    reason = (a.reason or "").strip()
    if not reason:
        die("--reason is required: a granted budget records why more rounds are worth it")
    with owner_lock(d, a.owner):
        record = read_corrections(d, a.owner)
        opened = [e for e in open_escalations(d) if e.get("owner") == a.owner]
        if not record or not opened:
            die(f"{a.owner} has no open escalation to grant")
        entry = opened[-1]
        path = Path(entry.pop("_path"))
        record["granted"] = int(record.get("granted", 0) or 0) + a.grant
        record["escalated"] = ""
        entry.update({"state": "granted", "granted": a.grant, "granted_by": role,
                      "reason": reason, "resolved_at": stamp()})
        publish_json(corrections_path(d, a.owner), record)
        publish_json(path, entry)
    grnd, gst = state_of(d, a.owner)
    if gst == "submitted" and refused_verifier_correction(d, a.owner, grnd) is not None:
        woken = f"the {verifier_role_of(d)} is woken to finish the correction it was refused"
    else:
        reviewer = "checker" if mode_of(d) == MODE_QUICK else "reviewer"
        woken = f"the {reviewer} is woken to apply the correction it was refused"
    print(f"granted {a.owner} {a.grant} more correction round(s): "
          f"{correction_attempts(record)} used of {correction_budget(d, a.owner, record)}; "
          + woken)


def escalation_path(d: Path, owner: str, start_round: int) -> Path:
    return d / ".escalations" / f"{owner}-r{start_round:02d}.json"


def open_escalation(d: Path, owner: str, record: dict, why: str) -> dict:
    """One durable escalation per exhausted chain. Never auto-anything."""
    try:
        start = int(record.get("chain_start_round", 0) or 0)
    except (TypeError, ValueError):
        start = 0
    path = escalation_path(d, owner, start)
    history: list[dict] = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
            if isinstance(existing, dict) and existing.get("state") == "open":
                return existing
            # A chain exhausted again after a grant keeps every earlier grant and
            # its reason; the new escalation never erases why rounds were given.
            if isinstance(existing, dict):
                history = [h for h in existing.get("history", []) if isinstance(h, dict)]
                history.append({k: v for k, v in existing.items() if k != "history"})
        except (OSError, ValueError):
            pass
    findings = [p.name for p in _ordered(d.glob(f"{owner}-verification-*.mdx"))]
    decisions = [p.name for p in _ordered(d.glob(f"{owner}-decision-*.mdx"))]
    entry = {
        "id": f"{owner}-r{start:02d}",
        "run": d.name,
        "owner": owner,
        "state": "open",
        "why": why,
        "attempts": {k: record.get(k, 0) for k in
                     ("gate_repairs", "verifier_returns", "reviewer_returns")},
        "limit": correction_budget(d, owner, record),
        "findings": findings,
        "decisions": decisions,
        "opened_at": stamp(),
    }
    if history:
        entry["history"] = history
    record["escalated"] = entry["id"]
    publish_json(corrections_path(d, owner), record)
    publish_json(path, entry)
    record_outcome(d, owner, state_of(d, owner)[0], "escalation", "opened", why, path.name)
    machine_feedback(d, "review", owner, f"correction budget exhausted: {why}")
    return entry


def guard_correction_budget(d: Path, owner: str, opening: str,
                            on_exhausted: object = None) -> None:
    """Refuse another correction round once the local budget is exceeded."""
    record = read_corrections(d, owner)
    if not record:
        return
    if correction_attempts(record) <= correction_budget(d, owner, record):
        return
    if callable(on_exhausted):
        on_exhausted()
    entry = open_escalation(d, owner, record,
                            f"local correction budget exceeded by {opening}")
    die(f"{owner} exceeded its local correction budget "
        f"({correction_attempts(record)} attempts, limit {correction_budget(d, owner, record)}): "
        f"escalation {entry['id']} is open. This never auto-approves, auto-waives, "
        "or opens another round; a reviewer must decide from the escalation packet.")


def refused_verifier_correction(d: Path, owner: str, rnd: int) -> Path | None:
    """The round's verification whose correction the budget refused, if that is its state."""
    vpath, vmeta, _ = latest_verification(d, owner, rnd)
    if vpath is not None and vmeta.get("result") == "fail" \
            and vmeta.get("opened_correction") == "escalated":
        return vpath
    return None


ROUTE_TABLE = {
    "scope-change": ("implementor",
                     "amend scope through the deterministic scope gate after a collision check"),
    "collision": ("orchestrator", "serialize or reassign the overlapping work"),
    "verification-defect": ("implementor",
                            "correct the exact diagnostics or numbered findings; the verifier rechecks"),
    "rate-limit-crash": ("orchestrator",
                         "resume from the checkpoint or fall back within approved policy"),
    "requirement-conflict": ("planner",
                             "request a compact amendment with evidence and a proposed alternative"),
    "dependency-invalidated": ("orchestrator",
                               "adjust the schedule within approved intent, or escalate to the planner"),
    "dependency-plan-change": ("planner",
                               "replan the invalidated branch; unaffected tasks continue"),
    "dispute": ("reviewer",
                "preserve both claims with their evidence; stop the argument loop"),
    "budget-exhausted": ("reviewer",
                         "emit one durable escalation; never auto-approve or auto-waive"),
    "review-defect": ("implementor",
                      "apply the numbered corrections, reverify, and return to the reviewer"),
    "plan-gap": ("planner", "explain the objective and contract gap in an amendment"),
    "waiver-request": ("reviewer", "decide the explicit waiver packet"),
    "blocked": ("reviewer",
                "decide the blocked round: request changes that carry the answer, or waive"),
    "spending-request": ("user", "the authorized budget owner decides outside policy spend"),
}


def routes_dir(d: Path) -> Path:
    return d / ".routes"


def blocked_route(d: Path, owner: str, rnd: int) -> dict[str, object] | None:
    """The record handing owner's blocked round rnd to the reviewer, if any."""
    path = routes_dir(d) / f"{owner}-{rnd:02d}-blocked.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def route_blocked(d: Path, run: str, owner: str, note: str) -> None:
    """Hand an implementor's blocked round to the reviewer with a durable wake.

    The orchestrator is woken for a block first, so it can answer what is its
    own to answer; only the reviewer can settle the round. The record, not a
    message typed into another session, is what queues the reviewer event.
    Delivery waits until the reviewer role is armed and its watcher can receive it.
    """
    if not owner or owner == "orch":
        die("--kind blocked needs --owner TASK; a blocked aggregate already wakes the reviewer")
    if not is_five_role(d):
        die(f"run {run} has no separate reviewer; decide the blocked round yourself")
    with owner_lock(d, owner):
        rnd, st = state_of(d, owner)
        if st != "blocked":
            die(f"{owner} round {rnd} is {st}, not blocked; nothing to route")
        if task_executor(d, owner) == "orchestrator":
            die(f"{owner} is executed by the orchestrator; its block already wakes the reviewer")
        path = routes_dir(d) / f"{owner}-{rnd:02d}-blocked.json"
        if not path.is_file():
            routes_dir(d).mkdir(parents=True, exist_ok=True)
            publish_json(path, {"owner": owner, "round": rnd, "kind": "blocked",
                                "note": note.strip(), "routed_at": stamp()})
    reviewer = "checker" if mode_of(d) == MODE_QUICK else "reviewer"
    print(f"routed {owner} round {rnd} to the {reviewer}; notification queued for its watcher")
    if (run, reviewer) not in armed():
        print(f"warning: {reviewer} is unarmed for {run}; arm it to receive the queued notification")


def cmd_route(a: argparse.Namespace) -> None:
    """Route one exception to its owning role from the deterministic table."""
    d = need_run(a.run)
    if a.kind not in ROUTE_TABLE:
        die(f"unknown kind {a.kind!r}; use one of: {', '.join(sorted(ROUTE_TABLE))}")
    if a.kind == "blocked":
        route_blocked(d, a.run, a.owner or "", a.note or "")
        return
    dest, action = ROUTE_TABLE[a.kind]
    if mode_of(d) == MODE_QUICK:
        dest = {"planner": "coordinator", "orchestrator": "coordinator",
                "reviewer": "checker"}.get(dest, dest)
    owner = a.owner or ""
    pointer = ""
    if owner:
        decs = _ordered(d.glob(f"{owner}-decision-*.mdx"))
        reps = _ordered(d.glob(f"{owner}-report-*.mdx"))
        vers = _ordered(d.glob(f"{owner}-verification-*.mdx"))
        pointer = str((decs or reps or vers or [Path(f"{owner}: no artifacts")])[-1].name)
    print(f"destination: {dest}")
    print(f"action: {action}")
    print("notification: no event queued; this route is advisory")
    if dest != "user" and (a.run, dest) not in armed():
        print(f"warning: {dest} is unarmed for {a.run}")
    if pointer:
        print(f"artifact: {pointer}")


def cmd_escalate_mode(a: argparse.Namespace) -> None:
    """Record that quick is no longer suitable without migrating any live state."""
    d = need_run(a.run)
    if mode_of(d) != MODE_QUICK:
        die(f"mode escalation is only defined for quick runs; {a.run} records "
            f"mode {mode_of(d)!r}")
    reason = (a.reason or "").strip()
    if not reason:
        die("--reason is required so the escalation request records why quick no longer fits")
    # Run-scoped numbering holds the run lock across read-number-write, so two
    # concurrent requests serialize instead of publishing the same request
    # number with the second silently overwriting the first's reason.
    with run_lock(d):
        directory = d / ".mode-escalations"
        policy = artifact_policy_fields(d)
        for _ in range(100):
            existing = sorted(directory.glob("request-*.mdx")) if directory.is_dir() else []
            number = next_numbered(existing)
            path = directory / f"request-{number:02d}.mdx"
            meta = {
                "run": a.run,
                "request": str(number),
                "status": "requested",
                "requested_mode": MODE_STANDARD,
                **policy,
            }
            body = (f"# Mode escalation request {number}\n\n"
                    f"## Reason\n\n{reason}\n\n"
                    "## Effect\n\nNo migration was performed. The run remains quick, every existing "
                    "artifact and baseline is preserved, and an authorized follow-up must decide "
                    "how to proceed.\n")
            try:
                publish_exclusive(path, render(meta, body))
            except FileExistsError:
                continue
            break
        else:
            die("could not record a mode escalation request; retry the command")
    print(f"recorded {path.relative_to(root())}; mode remains quick and no baseline was recaptured")
