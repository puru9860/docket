"""Versioned scope amendments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import die, stamp
from .paths import next_numbered, owners
from .publication import fault, publish_exclusive, publish_json
from .policy import need_run
from .bundles import digest_of
from .locks import owner_lock
from .state import amendments_dir, list_amendments, list_batches, state_of, task_depends_on
from .dependencies import read_deps
from .freeze import current_bundle
from .aggregates import current_aggregate
from .events import derive_events
from .delivery import retire_event


def amendment_consumers(d: Path, owner: str) -> list[str]:
    """Tasks whose evidence depends on the amended task: batch edges plus pins."""
    affected = set()
    for batch in list_batches(d):
        for member, ds in ((batch.get("depends_on") or {}).items()):
            if owner in [str(x) for x in (ds or [])]:
                affected.add(str(member))
    for other in owners(d):
        if other in (owner, "orch"):
            continue
        for entry in read_deps(d, other):
            if str(entry.get("on", "")) == owner:
                affected.add(other)
    for other in owners(d):
        if other in (owner, "orch"):
            continue
        if owner in task_depends_on(d, other):
            affected.add(other)
    return sorted(affected)


def cmd_propose_amendment(a: argparse.Namespace) -> None:
    """Propose a compact contract amendment for planner decision."""
    d = need_run(a.run)
    for field in ("need", "conflicts", "alternative", "impact"):
        if not (getattr(a, field) or "").strip():
            die(f"--{field.replace('_', '-')} TEXT is required; an amendment request contains "
                "only the decision needed, conflicting constraints, evidence pointers, "
                "a recommended alternative, and impact on acceptance and dependencies")
    task = d / f"{a.owner}-task.mdx"
    if not task.is_file():
        die(f"no assignment {a.owner} in {a.run}")
    with owner_lock(d, a.owner):
        pending = [m for m in list_amendments(d, a.owner) if m.get("status") == "pending"]
        old_rev = digest_of(task.read_bytes())
        for _ in range(100):
            owned = [p for p in amendments_dir(d).glob(f"{a.owner}-*.json")
                     ] if amendments_dir(d).is_dir() else []
            num = next_numbered(owned)
            while (amendments_dir(d) / f"{a.owner}-{num:02d}.json").exists():
                num += 1
            record = {"id": f"{a.owner}-{num:02d}", "run": a.run, "owner": a.owner,
                      "status": "pending",
                      "need": a.need.strip(), "conflicts": a.conflicts.strip(),
                      "evidence": (a.evidence or "").strip() or "-",
                      "alternative": a.alternative.strip(), "impact": a.impact.strip(),
                      "old_contract_rev": old_rev,
                      "new_contract_rev": "", "decided_by": "", "decided_at": "",
                      "proposed_at": stamp()}
            amendments_dir(d).mkdir(parents=True, exist_ok=True)
            try:
                publish_exclusive(
                    amendments_dir(d) / f"{record['id']}.json",
                    json.dumps(record, indent=2, sort_keys=True) + "\n")
            except FileExistsError:
                continue
            break
        else:
            die(f"could not propose an amendment for {a.owner}; retry the command")
    if pending:
        print(f"note: {len(pending)} earlier amendment(s) still pending for {a.owner}")
    print(f"proposed amendment {record['id']}; the planner decides, unaffected tasks continue")


def cmd_amendment(a: argparse.Namespace) -> None:
    """Accept or reject a proposed amendment, invalidating only affected work."""
    d = need_run(a.run)
    if bool(a.accept) == bool(a.reject):
        die("use exactly one of --accept ID or --reject ID")
    iid = a.accept or a.reject
    path = amendments_dir(d) / f"{iid}.json"
    if not path.is_file():
        die(f"no amendment {iid!r} in {a.run}")
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        die(f"amendment {iid!r} is unreadable")
    if record.get("status") != "pending":
        print(f"amendment {iid} is already {record.get('status')}; nothing rewritten")
        return
    decider = (a.by or "").strip() or "planner"
    if a.reject:
        if not (a.reason or "").strip():
            die("--reject requires --reason TEXT")
        record.update({"status": "rejected", "decided_by": decider,
                       "decided_at": stamp(), "reason": a.reason.strip()})
        publish_json(path, record)
        print(f"rejected amendment {iid}; affected work continues untouched")
        return
    task = d / f"{record['owner']}-task.mdx"
    if not task.is_file():
        die(f"no assignment {record['owner']} in {a.run}")
    new_rev = digest_of(task.read_bytes())
    record.update({"status": "accepted", "decided_by": decider,
                   "decided_at": stamp(), "new_contract_rev": new_rev})
    publish_json(path, record)
    affected = [o for o in amendment_consumers(d, record["owner"])
                if o != record["owner"]]
    retired = []
    for other in affected:
        for role in ("orchestrator", "planner", "verifier", "reviewer"):
            try:
                derived = derive_events(d, role)
            except (OSError, ValueError):
                continue
            for ev in derived:
                if str(ev.get("owner", "")) == other:
                    retire_event(d, role, str(ev["key"]),
                                 f"amendment {iid} accepted for {record['owner']}")
                    retired.append(f"{role}:{ev['key']}")
    accepted_path = amendments_dir(d) / "accepted.json"
    accepted: list[dict[str, object]] = []
    if accepted_path.is_file():
        try:
            data = json.loads(accepted_path.read_text())
            accepted = data if isinstance(data, list) else []
        except (OSError, ValueError):
            accepted = []
    accepted.append({"amendment": iid, "task": record["owner"],
                     "old_contract_rev": record.get("old_contract_rev", ""),
                     "new_contract_rev": new_rev, "affected": affected,
                     "accepted_at": stamp()})
    publish_json(accepted_path, accepted)
    fault("amendment:accept")
    print(f"accepted amendment {iid} for {record['owner']}")
    if affected:
        print(f"invalidated {len(affected)} dependent(s): {', '.join(affected)}; "
              f"retired {len(retired)} stale event(s). Unaffected tasks continue.")
    else:
        print("no dependents consumed the old revision; nothing else invalidated")


def amendment_blocks(d: Path, owner: str) -> list[str]:
    """Accepted amendments that this owner has not yet reverified against."""
    path = amendments_dir(d) / "accepted.json"
    if not path.is_file():
        return []
    try:
        accepted = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(accepted, list):
        return []
    try:
        rnd, _ = state_of(d, owner)
    except (OSError, ValueError):
        return []
    if owner == "orch":
        frozen, _ = current_aggregate(d, rnd) if rnd else (None, [])
    else:
        frozen, _ = current_bundle(d, owner, rnd) if rnd else (None, [])
    frozen_at = str((frozen or {}).get("frozen_at", ""))
    problems = []
    for entry in accepted:
        if not isinstance(entry, dict):
            continue
        if owner != "orch" and owner != entry.get("task") \
                and owner not in (entry.get("affected") or []):
            continue
        if frozen_at and str(entry.get("accepted_at", "")) <= frozen_at:
            continue
        problems.append(
            f"amendment {entry.get('amendment')} was accepted after round {rnd} froze: "
            "reverify the new contract in a fresh round first")
    return problems
