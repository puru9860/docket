"""Execution health and incidents, kept separate from report state."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .common import die, now_s, stamp
from .frontmatter import parse
from .paths import next_numbered, owners, planned_tasks, scope_path
from .publication import publish_exclusive, publish_json
from .policy import max_concurrency_of, need_run
from .bundles import bundle_dir, latest_bundle, load_bundle
from .locks import owner_lock
from .state import handoff_state, incidents_dir, list_incidents, reopen_epoch, state_of
from .liveness import live_dispatches


HEALTH_UNKNOWN = "unknown"
RATE_LIMIT_HINT = re.compile(r"rate.?limit|429|quota|overloaded|capacity|try again later",
                             re.IGNORECASE)


def latest_verify_text(d: Path, owner: str) -> tuple[str, str]:
    """The newest frozen verification output for an owner, or empty strings."""
    entry = latest_bundle(d, owner)
    if entry is None:
        return "", ""
    manifest = load_bundle(d, owner, entry)
    if not manifest:
        return "", ""
    where = bundle_dir(d, owner, entry)
    verification = manifest.get("verification", {})
    if not isinstance(verification, dict):
        return "", ""
    out = []
    for key in ("stdout", "stderr"):
        ref = verification.get(key, {})
        name = ref.get("file", "") if isinstance(ref, dict) else ""
        if not name:
            out.append("")
            continue
        try:
            out.append((where / name).read_text(errors="replace"))
        except OSError:
            out.append("")
    return out[0], out[1]


def execution_health(d: Path, owner: str) -> tuple[str, str]:
    """Execution state, derived separately from report lifecycle state.

    Agent `done` is never task approval: `report=` is the lifecycle truth and
    `execution=` is what the checkout appears to be doing. Quiet work is
    healthy; only an explicit stall report opens an incident.
    """
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return HEALTH_UNKNOWN, "no assignment"
    rnd, st = state_of(d, owner)
    if rnd == 0:
        return HEALTH_UNKNOWN, "no report opened"
    if st in ("approved", "completed", "waived"):
        return "done", f"round {rnd} {st}"
    if st in ("submitted", "blocked"):
        return "awaiting-review", f"round {rnd} {st}"
    hn, hs = handoff_state(d, owner)
    if hs == "ready":
        return "stopped-recoverable", f"handoff {hn} ready for a replacement"
    capsule = scope_path(d, owner)
    if capsule.is_file():
        try:
            status = parse(capsule.read_text())[0].get("status", "")
        except (OSError, ValueError):
            status = ""
        if status == "ready":
            return "running", f"round {rnd} draft with accepted scope"
        if status == "collision":
            return "idle-unsubmitted", "scope collision needs sequencing"
    return "idle-unsubmitted", f"round {rnd} draft with no accepted scope"


def cmd_health(a: argparse.Namespace) -> None:
    """Show execution health separately from report state, and manage stalls."""
    d = need_run(a.run)
    selected_owner = a.flag_stall or a.owner
    if selected_owner and not (d / f"{selected_owner}-task.mdx").is_file():
        die(f"no task {selected_owner!r} in {a.run}")
    if a.flag_stall:
        cause = (a.cause or "").strip()
        if not cause:
            die("--flag-stall requires --cause TEXT")
        # The duplicate check and the numbered write hold the owner lock, so
        # two concurrent flags with one cause still yield one incident and two
        # with different causes never publish the same file. The number comes
        # from file names, readable or not, and the create is exclusive.
        with owner_lock(d, a.flag_stall):
            rnd, _ = state_of(d, a.flag_stall)
            epoch = reopen_epoch(d, a.flag_stall, rnd)
            for incident in list_incidents(d, a.flag_stall):
                if (incident.get("state") == "open" and incident.get("cause") == cause
                        and incident.get("epoch") == epoch):
                    print(f"already flagged: {incident['id']} ({cause}); one stall yields one incident")
                    return
            pattern = re.compile(rf"{re.escape(a.flag_stall)}-\d+")
            for _ in range(100):
                owned = [p for p in incidents_dir(d).glob("*.json")
                         if pattern.fullmatch(p.stem)] if incidents_dir(d).is_dir() else []
                num = next_numbered(owned)
                iid = f"{a.flag_stall}-{num}"
                try:
                    publish_exclusive(incidents_dir(d) / f"{iid}.json", json.dumps({
                        "id": iid,
                        "run": a.run,
                        "owner": a.flag_stall,
                        "cause": cause,
                        "epoch": epoch,
                        "detected_at": stamp(),
                        "detected_at_epoch": now_s(),
                        "state": "open",
                    }, indent=2, sort_keys=True) + "\n")
                except FileExistsError:
                    continue
                break
            else:
                die(f"could not flag a stall for {a.flag_stall}; retry the command")
            print(f"flagged stall {iid}: {cause}")
            print("One incident yields one recovery event until it is resolved or its generation changes.")
        return
    if a.resolve_stall:
        path = incidents_dir(d) / f"{a.resolve_stall}.json"
        if not path.is_file():
            die(f"no stall incident {a.resolve_stall!r} in {a.run}")
        try:
            incident = json.loads(path.read_text())
        except (OSError, ValueError):
            die(f"stall incident {a.resolve_stall!r} is unreadable")
        if incident.get("state") == "resolved":
            print(f"stall {a.resolve_stall} is already resolved")
            return
        incident["state"] = "resolved"
        incident["resolved_at"] = stamp()
        publish_json(path, incident)
        print(f"resolved stall {a.resolve_stall}")
        return
    targets = [a.owner] if a.owner else [o for o in owners(d) if o != "orch"] or planned_tasks(d)
    for owner in targets:
        rnd, st = state_of(d, owner)
        exec_state, detail = execution_health(d, owner)
        stdout_text, stderr_text = latest_verify_text(d, owner)
        hint = ""
        if RATE_LIMIT_HINT.search(stdout_text + "\n" + stderr_text):
            hint = "  [hint] provider rate-limit text seen in verification output (not state)"
        print(f"  {owner:<8} report={st:<18} execution={exec_state:<20} {detail}{hint}")
    try:
        live = live_dispatches(d)
    except (OSError, ValueError):
        live = []
    try:
        cap = max_concurrency_of(d)
    except ValueError:
        cap = 0
    if cap:
        print(f"live execution capacity: {len(live)} live dispatch record(s) (cap {cap})")
    else:
        print(f"live execution capacity: {len(live)} live dispatch record(s)")
    open_incidents = [i for i in list_incidents(d) for _ in [0]
                      if i.get("state") == "open" and (not a.owner or i.get("owner") == a.owner)]
    if open_incidents:
        print(f"\nopen stall incidents ({len(open_incidents)}):")
        for incident in open_incidents:
            print(f"  {incident.get('id')}: {incident.get('cause')}")
