"""Explicit review batches and their readiness."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from .common import die, stamp
from .frontmatter import sections
from .paths import owners, planned_tasks
from .publication import publish_json
from .policy import is_five_role, need_run
from .state import (
    _cycle_for_new_edges, batch_path, list_batches, read_batch, state_of, task_depends_on,
)
from .verification import (
    current_round_digest, current_task_revision, matching_verifications,
    verifier_exempt_set,
)


def verification_fragment(d: Path, member: str, rnd: int) -> str:
    """Resolved-verification signature fragment for one batch member.

    Shared by milestone readiness and review frontiers so both callers agree
    about what counts as resolved: verifier_exempt coverage, the bundle
    digest and contract revision lookup, matching_verifications, the fail
    and opened_correction rejection, the pass-or-uncertain test, the
    findings hash, and the member:result:findings:digest fragment. Returns
    the fragment, or an empty string when verification is unresolved.
    """
    if member in verifier_exempt_set(d):
        return f"{member}:exempt"
    if not rnd:
        return ""
    digest = current_round_digest(d, member, rnd)
    contract_rev = current_task_revision(d, member)
    matched = matching_verifications(d, member, rnd, digest, contract_rev)
    if not matched:
        return ""
    _, vmeta, vbody = matched[-1]
    result = str(vmeta.get("result", ""))
    if result == "fail" or str(vmeta.get("opened_correction", "")) == "yes":
        return ""
    if result not in ("pass", "uncertain"):
        return ""
    findings = sections(vbody).get("Findings", "").strip()
    findings_hash = hashlib.sha256(findings.encode()).hexdigest()
    return f"{member}:{result}:{findings_hash}:{digest}"


def batch_ready(d: Path, batch: dict) -> tuple[bool, str]:
    """Whether a closed batch's explicit membership is ready for review.

    Readiness uses only closed membership and explicit dependencies: tasks
    outside the batch - including future tasks waiting on batch members -
    never block it. A member is ready when submitted or terminal and every
    task it explicitly depends on is terminal. Under five-role-v1 a closed
    batch, milestone or not, is additionally ready only when every submitted
    member has a resolved verification for its current round and evidence,
    where resolved means a recorded pass or uncertain verdict, or
    verifier_exempt coverage. A fail verdict or a correction keeps it unready.
    Readiness before verification would wake a supervisor with nothing to
    route and no later event to wait on.
    """
    terminal = {"approved", "waived", "completed"}
    members = [str(m) for m in (batch.get("members") or [])]
    deps = batch.get("depends_on") or {}
    rounds = {m: state_of(d, m)[0] for m in members}
    states = {m: state_of(d, m)[1] for m in members}
    if not members or any(st == "blocked" for st in states.values()):
        return False, ""
    if not any(st == "submitted" for st in states.values()):
        return False, ""
    if any(st not in terminal and st != "submitted" for st in states.values()):
        return False, ""
    dep_states = {}
    for member in members:
        for dep in (deps.get(member) or []):
            dep_states[str(dep)] = state_of(d, str(dep))[1]
    if any(st not in terminal and st != "submitted" for st in dep_states.values()):
        return False, ""
    base_sig = ";".join([f"batch:{batch.get('batch')}:gen{batch.get('generation', 1)}"]
                   + [f"{m}:{states[m]}" for m in sorted(states)]
                   + [f"{m}->{dep}:{dep_states[dep]}" for m in sorted(deps)
                      for dep in sorted(deps[m] or []) if dep in dep_states])
    if not is_five_role(d):
        return True, f"sha256:{hashlib.sha256(base_sig.encode()).hexdigest()}"
    ver_parts: list[str] = []
    for member in sorted(members):
        if states[member] != "submitted":
            continue
        fragment = verification_fragment(d, member, rounds[member])
        if not fragment:
            return False, ""
        ver_parts.append(fragment)
    sig = base_sig + ";" + ";".join(ver_parts) if ver_parts else base_sig
    return True, f"sha256:{hashlib.sha256(sig.encode()).hexdigest()}"


def frontier_ready(d: Path, batch: dict) -> tuple[list[str], str]:
    """Review frontier for one closed batch: verified work blocking a stalled member.

    A frontier wakes the reviewer once for exactly those submitted members
    whose approval unlocks the next wave. A member joins only when it is
    submitted with resolved verification for its current round and evidence
    (a recorded pass or uncertain verdict bound to its bundle digest and
    contract revision, or verifier_exempt coverage) and at least one member
    of the same batch that cannot dispatch yet explicitly depends on it
    through task frontmatter or the batch edge. The revision reuses the
    phase 1 verification shape so a changed verdict, finding, round, or
    re-review moves it while unchanged state stays stable. A milestone batch
    whose readiness is already derived for the same evidence yields no
    frontier, so one decision never wakes twice. Legacy runs yield none.
    """
    if not is_five_role(d):
        return [], ""
    if batch.get("state") != "closed":
        return [], ""
    members = [str(m) for m in (batch.get("members") or [])]
    if not members:
        return [], ""
    rounds = {m: state_of(d, m)[0] for m in members}
    states = {m: state_of(d, m)[1] for m in members}
    terminal = {"approved", "waived", "completed"}
    resolved: dict[str, str] = {}
    for member in members:
        if states.get(member) != "submitted":
            continue
        fragment = verification_fragment(d, member, rounds.get(member, 0))
        if not fragment:
            continue
        resolved[member] = fragment
    if not resolved:
        return [], ""
    batch_deps = batch.get("depends_on") or {}
    if not isinstance(batch_deps, dict):
        batch_deps = {}
    frontier: list[str] = []
    for member in sorted(resolved):
        for other in members:
            if other == member:
                continue
            st = states.get(other, "")
            if st in terminal or st == "submitted" or st == "blocked":
                continue
            if st == "unassigned":
                continue
            task_deps = task_depends_on(d, other)
            raw = batch_deps.get(other) or []
            try:
                b_deps = [str(x) for x in (raw or [])]
            except (TypeError, ValueError):
                b_deps = []
            if member not in task_deps and member not in b_deps:
                continue
            frontier.append(member)
            break
    if not frontier:
        return [], ""
    if batch.get("milestone"):
        ready, _ = batch_ready(d, batch)
        if ready:
            return [], ""
    bid = str(batch.get("batch"))
    gen = batch.get("generation", 1)
    base_sig = ";".join([f"batch:{bid}:gen{gen}:frontier"]
                        + [f"{m}:{states.get(m, '')}" for m in sorted(frontier)])
    ver_parts = [resolved[m] for m in sorted(frontier)]
    sig = base_sig + ";" + ";".join(ver_parts)
    return sorted(frontier), f"sha256:{hashlib.sha256(sig.encode()).hexdigest()}"


def cmd_batch(a: argparse.Namespace) -> None:
    """Create, close, and list explicit review batches with closed membership."""
    d = need_run(a.run)
    if a.list:
        found = list_batches(d)
        if not found:
            print(f"run {a.run}: no review batches")
            return
        print(f"run {a.run}: {len(found)} review batch(es)")
        for batch in found:
            print(f"  {batch.get('batch')}  {batch.get('state')}  "
                  f"generation={batch.get('generation', 1)}  "
                  f"milestone={batch.get('milestone', False)}  "
                  f"members={','.join(str(m) for m in (batch.get('members') or []))}")
        return
    if a.close:
        batch = read_batch(d, a.close)
        if not batch:
            die(f"no batch {a.close!r} in {a.run}")
        if batch.get("state") == "closed":
            print(f"batch {a.close} is already closed "
                  f"(generation {batch.get('generation', 1)}); membership unchanged")
            return
        batch["state"] = "closed"
        batch["closed_at"] = stamp()
        publish_json(batch_path(d, a.close), batch)
        print(f"closed batch {a.close} with members "
              f"{','.join(str(m) for m in (batch.get('members') or []))}; "
              "later tasks cannot join it")
        return
    if not a.create:
        die("use --create ID, --close ID, or --list")
    bid = a.create
    if read_batch(d, bid):
        die(f"batch {bid!r} already exists in {a.run}")
    members = sorted({m.strip() for m in (a.members or "").split(",") if m.strip()})
    if not members:
        die("--create requires --members T01,T02")
    for member in members:
        if not (d / f"{member}-task.mdx").is_file():
            die(f"batch member {member!r} is not assigned in {a.run}; "
                "create every assignment before closing the batch")
    depends: dict[str, list[str]] = {}
    for spec in (a.depends_on or []):
        member, _, dep = spec.partition(":")
        if not member or not dep:
            die(f"invalid --depends-on {spec!r}: use MEMBER:DEP such as T02:T01")
        depends.setdefault(member.strip(), []).append(dep.strip())
    member_set = set(members)
    for source in depends:
        if source not in member_set:
            die(f"dependency source {source!r} is not a member of batch {bid!r} "
                f"in {a.run}; nothing published")
    known = set(planned_tasks(d)) | set(owners(d))
    for member, ds in depends.items():
        for dep in ds:
            if dep not in known and not (d / f"{dep}-task.mdx").is_file():
                die(f"dependency target {dep!r} is neither planned nor assigned in {a.run}")
    if depends:
        normalized = {m: sorted(set(ds)) for m, ds in depends.items()}
        cycle = _cycle_for_new_edges(d, normalized)
        if cycle is not None:
            die(f"refusing dependency cycle: {' -> '.join(cycle)}; nothing published")
    record: dict[str, object] = {
        "batch": bid,
        "run": a.run,
        "members": members,
        "depends_on": {k: sorted(set(v)) for k, v in depends.items()},
        "milestone": bool(a.milestone),
        "state": "open",
        "generation": 1,
        "created_at": stamp(),
    }
    publish_json(batch_path(d, bid), record)
    print(f"created batch {bid} with members {','.join(members)}"
          + (" (milestone)" if a.milestone else ""))
