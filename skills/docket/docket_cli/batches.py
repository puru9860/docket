"""Explicit review batches and their readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .common import die, stamp
from .frontmatter import sections
from .paths import owners, planned_tasks
from .publication import publish_bytes, publish_json
from .policy import is_five_role, is_quick_milestone, is_tiered, need_run
from .locks import owner_lock
from .state import (
    _cycle_for_new_edges, batch_path, list_batches, quick_submitted_pass, read_batch,
    state_of, task_depends_on,
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
    if is_quick_milestone(d):
        digest = current_round_digest(d, member, rnd)
        return f"{member}:pass:{digest}" if digest and quick_submitted_pass(d, member, rnd) else ""
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


def tiered_settled(d: Path, member: str) -> bool:
    """Whether a tiered run treats this member as settled for dispatch and scope.

    Settled means submitted with resolved verification for its current round and
    evidence (a recorded pass or uncertain verdict bound to its bundle digest and
    contract revision, or verifier_exempt coverage). A fail verdict or an opened
    correction keeps it unsettled. Non-tiered runs never settle this way; only the
    reviewer settles them. A settled round is ready for milestone review, never
    reviewer-approved: approval still needs a reviewer verdict.
    """
    if not is_tiered(d):
        return False
    try:
        rnd, st = state_of(d, member)
    except (OSError, ValueError):
        return False
    if st != "submitted" or not rnd:
        return False
    fragment = verification_fragment(d, member, rnd)
    return fragment == f"{member}:exempt" or fragment.startswith(f"{member}:pass:")


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
        # Terminal quick members with a waiver still need a milestone verdict
        # for their current bundles. An earlier verdict cannot cover a member
        # reopened and waived again; an unchanged verdict must not re-wake review.
        decision = batch.get("milestone_decision") or {}
        decided = (isinstance(decision, dict) and decision.get("verdict") == "approved"
                   and decision.get("member_bundles") == current_member_bundles(d, batch)
                   and decision.get("batch_verification") ==
                   (batch.get("verification") or {}).get("digest"))
        if (not is_quick_milestone(d) or not all(st in terminal for st in states.values())
                or not any(st == "waived" for st in states.values())
                or decided):
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


def current_member_bundles(d: Path, batch: dict) -> dict[str, str]:
    """Member to frozen bundle digest for the batch's current rounds."""
    from .bundles import latest_bundle
    out: dict[str, str] = {}
    for member in [str(m) for m in (batch.get("members") or [])]:
        latest = latest_bundle(d, member)
        if latest:
            out[member] = str(latest.get("digest", ""))
        else:
            out[member] = ""
    return out


def current_batch_source(d: Path) -> tuple[list[dict[str, str]], str]:
    """Source identity the batch verification binds, or why it is unavailable."""
    from .bundles import source_identity
    from .baselines import evidence_mode
    if evidence_mode(d) != "git":
        return [], "documents-only"
    try:
        return source_identity(d, "run", "batch-verify")
    except (OSError, ValueError) as exc:
        return [], str(exc)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def batch_verification_dir(d: Path, bid: str, digest: str) -> Path:
    """A content-addressed, immutable full-suite result under the private store."""
    return d / ".bundles" / "batches" / bid / digest.removeprefix("sha256:")


def freeze_batch_verification(d: Path, bid: str, verification: dict[str, object],
                              stdout: bytes, stderr: bytes) -> dict[str, object]:
    """Publish all verification bytes in one rename and return its digest pointer."""
    record = dict(verification)
    artifacts = {"stdout": hashlib.sha256(stdout).hexdigest(),
                 "stderr": hashlib.sha256(stderr).hexdigest()}
    content = {"version": 1, "record": record, "artifacts": artifacts}
    digest = "sha256:" + hashlib.sha256(_canonical(content)).hexdigest()
    manifest = {**content, "digest": digest}
    target = batch_verification_dir(d, bid, digest)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        stage = Path(tempfile.mkdtemp(prefix=".freeze-", dir=parent))
        try:
            publish_bytes(stage / "stdout", stdout)
            publish_bytes(stage / "stderr", stderr)
            publish_json(stage / "manifest.json", manifest)
            if not target.exists():
                os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {**record, "digest": digest}


def batch_verification_intact(d: Path, batch: dict) -> bool:
    verification = batch.get("verification")
    if not isinstance(verification, dict):
        return False
    digest = verification.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return False
    where = batch_verification_dir(d, str(batch.get("batch", "")), digest)
    try:
        manifest = json.loads((where / "manifest.json").read_text())
        content = {key: value for key, value in manifest.items() if key != "digest"}
        if manifest.get("digest") != digest or hashlib.sha256(_canonical(content)).hexdigest() != digest[7:]:
            return False
        if manifest.get("record") != {key: value for key, value in verification.items()
                                      if key != "digest"}:
            return False
        for name in ("stdout", "stderr"):
            if hashlib.sha256((where / name).read_bytes()).hexdigest() != manifest["artifacts"][name]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def batch_verification_matches(d: Path, batch: dict, *, historical: bool = False) -> bool:
    """Whether intact output describes the members and the required source.

    A completed milestone may be audited after later work changes the checkout.
    Reusing a pass or making a new decision must also match the live source.
    """
    verification = batch.get("verification")
    if not isinstance(verification, dict) or not batch_verification_intact(d, batch):
        return False
    if not verification.get("command"):
        return False
    frozen_members = verification.get("member_bundles")
    if not isinstance(frozen_members, dict):
        return False
    if dict(frozen_members) != current_member_bundles(d, batch):
        return False
    frozen_source = verification.get("source")
    if is_quick_milestone(d) or historical:
        # Preserve the frozen tree for audit after later milestones edit the checkout.
        from .bundles import baseline_roots, tree_problems
        from .baselines import evidence_mode
        if not isinstance(frozen_source, dict):
            return False
        frozen_roots = frozen_source.get("roots", [])
        if evidence_mode(d) == "documents-only":
            return frozen_source.get("coverage") == "documents-only" and frozen_roots == []
        records, problem = baseline_roots(d, "run")
        if problem or not isinstance(frozen_roots, list) or len(frozen_roots) != len(records):
            return False
        by_alias = {str(record.get("alias", "")): record for record in records}
        for source in frozen_roots:
            if not isinstance(source, dict):
                return False
            alias = str(source.get("alias", ""))
            record = by_alias.get(alias)
            tree = str(source.get("tree", ""))
            if not record or not tree or source.get("path") != record.get("path"):
                return False
            if tree_problems(d, record, [tree]):
                return False
        if historical:
            return True
        current_source, problem = current_batch_source(d)
        return not problem and frozen_roots == current_source
    current_source, problem = current_batch_source(d)
    if problem and problem != "documents-only":
        return False
    if isinstance(frozen_source, dict):
        frozen_roots = frozen_source.get("roots", [])
        current_roots = current_source
        if frozen_roots != current_roots:
            # Documents-only runs freeze an empty source with a reason; equality
            # there means still documents-only, which stays valid.
            if not (not frozen_roots and not current_roots):
                return False
    elif frozen_source != current_source:
        return False
    return True


def batch_verification_valid(d: Path, batch: dict) -> bool:
    """A passing full-suite result bound to the current member and source bytes."""
    verification = batch.get("verification")
    return (isinstance(verification, dict)
            and str(verification.get("status", "")) == "passed"
            and batch_verification_matches(d, batch))


def batch_verification_historical_valid(d: Path, batch: dict) -> bool:
    """An intact pass for current member bundles, even after later source edits."""
    verification = batch.get("verification")
    return (isinstance(verification, dict)
            and str(verification.get("status", "")) == "passed"
            and batch_verification_matches(d, batch, historical=True))


def batch_verify_failed(d: Path, batch: dict) -> bool:
    """Whether the batch froze a failed full-suite result for current evidence."""
    verification = batch.get("verification")
    if not isinstance(verification, dict):
        return False
    if str(verification.get("status", "")) not in ("failed", "timeout"):
        return False
    return batch_verification_matches(d, batch)


def cmd_batch(a: argparse.Namespace) -> None:
    """Create, close, and verify explicit review batches with closed membership.

    Milestone approval lives in transitions.cmd_batch_decide (reached through the
    batch entry point in commands), because approval runs the standard decision
    transition which is defined after this module.
    """
    d = need_run(a.run)
    if is_quick_milestone(d) and not a.list:
        required = "implementor" if str(getattr(a, "verify", "") or "").strip() else "planner"
        if a.as_role != required:
            die(f"quick milestone {'verification' if required == 'implementor' else 'planning'} "
                f"requires --as {required}")
    if str(getattr(a, "verify", "") or "").strip():
        return cmd_batch_verify(a, d)
    if str(getattr(a, "approve", "") or "").strip():
        die("batch approval is handled by the milestone decision entry point; "
            "use `docket batch RUN --approve BID --as reviewer`")
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
    if is_quick_milestone(d) and not a.milestone:
        die("new quick runs require --milestone for every review batch")
    verify_command = str(getattr(a, "command", "") or "").strip()
    if is_quick_milestone(d) and not verify_command:
        die("new quick milestones require a planner-declared full-suite --command CMD "
            "at --create")
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
    if is_quick_milestone(d):
        record["verify_command"] = verify_command
    publish_json(batch_path(d, bid), record)
    print(f"created batch {bid} with members {','.join(members)}"
          + (" (milestone)" if a.milestone else ""))


def quick_milestone_plan_problems(d: Path, owner: str) -> list[str]:
    """All quick tasks and closed milestone membership precede implementation."""
    if not is_quick_milestone(d) or owner == "orch":
        return []
    planned = set(planned_tasks(d))
    assigned = {task for task in planned if (d / f"{task}-task.mdx").is_file()}
    milestones = [batch for batch in list_batches(d)
                  if batch.get("milestone") and batch.get("state") == "closed"]
    membership: dict[str, int] = {}
    for batch in milestones:
        for member in batch.get("members", []):
            key = str(member)
            membership[key] = membership.get(key, 0) + 1
    covered = set(membership)
    problems = []
    if not planned:
        problems.append("the plan names no tasks")
    if planned - assigned:
        problems.append("assign every planned task first: " + ", ".join(sorted(planned - assigned)))
    if planned - covered:
        problems.append("put every planned task in a closed milestone first: "
                        + ", ".join(sorted(planned - covered)))
    if owner not in covered:
        problems.append(f"{owner} is not in a closed milestone")
    repeated = sorted(task for task in planned if membership.get(task, 0) > 1)
    if repeated:
        problems.append("put each task in exactly one milestone: " + ", ".join(repeated))
    return problems


def quick_milestone_implementation_problem(d: Path, owner: str) -> str:
    """Finish each milestone's review before implementing the next one."""
    if not is_quick_milestone(d) or owner == "orch":
        return ""
    batches = sorted(
        (batch for batch in list_batches(d)
         if batch.get("milestone") and batch.get("state") == "closed"),
        key=lambda batch: tuple((1, int(part)) if part.isdigit() else (0, part.lower())
                                for part in re.split(r"(\d+)", str(batch.get("batch", "")))),
    )
    target = next((str(batch.get("batch")) for batch in batches
                   if owner in [str(m) for m in batch.get("members", [])]), "")
    if not target:
        return ""
    for batch in batches:
        settled_members = all(state_of(d, str(member))[1] in
                              ("submitted", "approved", "waived", "completed")
                              for member in batch.get("members", []))
        verification = batch.get("verification") or {}
        decision = batch.get("milestone_decision") or {}
        decided = (isinstance(verification, dict) and isinstance(decision, dict)
                   and decision.get("verdict") == "approved"
                   and decision.get("batch_verification") == verification.get("digest")
                   and decision.get("member_bundles") == current_member_bundles(d, batch))
        current = str(batch.get("batch"))
        if current == target:
            return ""
        if not (settled_members and batch_verification_historical_valid(d, batch) and decided):
            return (f"milestone {current} is not complete; finish its full-suite "
                    f"verification and reviewer approval before dispatching milestone {target}")
    return ""


def cmd_batch_verify(a: argparse.Namespace, d: Path) -> None:
    bid = str(getattr(a, "verify", "") or getattr(a, "verify_batch", "") or "").strip()
    if not bid:
        die("batch --verify needs a batch id: `docket batch RUN --verify BID --command CMD`")
    batch_path(d, bid)  # validate the id before using it in a private directory name
    with owner_lock(d, f"batch-{bid}"):
        _cmd_batch_verify_locked(a, d, bid)


def _cmd_batch_verify_locked(a: argparse.Namespace, d: Path, bid: str) -> None:
    """Run the declared full-suite command once when the batch is ready and freeze it.

    The frozen record binds command, output, source identity, and member-bundle
    digests. A changed member bundle, a moved checkout, or a different command
    invalidates the pass rather than reusing it. A failure routes to the
    orchestrator, never to a member's reviewer queue.
    """
    batch = read_batch(d, bid)
    if not batch:
        die(f"no batch {bid!r} in {d.name}")
    if batch.get("state") != "closed":
        die(f"batch {bid} is {batch.get('state')}, not closed; close it before verifying")
    supplied_command = str(getattr(a, "command", "") or "").strip()
    planned_command = str(batch.get("verify_command", "") or "").strip()
    if is_quick_milestone(d) and planned_command:
        if supplied_command and supplied_command != planned_command:
            die(f"batch {bid} must use planner-declared full-suite command "
                f"{planned_command!r}; --command cannot replace it")
        command = planned_command
    else:
        command = supplied_command
    if not command:
        die(f"batch {bid} needs its declared full-suite command: "
            f"`docket batch {d.name} --verify {bid} --command CMD`")
    try:
        timeout = int(getattr(a, "timeout", 0) or 900)
    except (TypeError, ValueError):
        timeout = 900
    ready, _ = batch_ready(d, batch)
    if not ready:
        die(f"batch {bid} is not ready for review: every submitted member needs resolved "
            "verification (pass, uncertain, or verifier_exempt); a fail or a correction keeps "
            "it unready")
    existing = batch.get("verification")
    if isinstance(existing, dict) and str(existing.get("command", "")) == command \
            and batch_verification_valid(d, batch):
        print(f"batch {bid} already verified {existing.get('frozen_at')}: "
              f"{command} ({existing.get('status')}); not rerun")
        return
    from .bundles import source_identity as _source_identity
    from .baselines import evidence_mode as _evidence_mode
    from .verification import run_verification as _run_verification
    if _evidence_mode(d) == "git":
        before, before_problem = _source_identity(d, "run", "batch-verify")
        if before_problem:
            die(f"batch {bid} cannot freeze source identity: {before_problem}")
    else:
        before = []
    member_bundles = current_member_bundles(d, batch)
    if any(not digest for digest in member_bundles.values()):
        missing = sorted(m for m, digest in member_bundles.items() if not digest)
        die(f"batch {bid} cannot verify: {', '.join(missing)} froze no bundle yet")
    record = _run_verification(command, timeout)
    if current_member_bundles(d, batch) != member_bundles or not batch_ready(d, batch)[0]:
        die(f"batch {bid}: a member bundle or verifier finding moved while the full-suite "
            "command ran; nothing was frozen")
    if _evidence_mode(d) == "git":
        after, after_problem = _source_identity(d, "run", "batch-verify")
        if after_problem:
            die(f"batch {bid} cannot freeze source identity: {after_problem}")
        if after != before:
            die(f"batch {bid}: the checkout moved while the full-suite command ran, so the "
                "captured result does not describe the frozen source; nothing was frozen")
        if is_quick_milestone(d):
            from .bundles import baseline_roots, retain_tree_objects
            roots, roots_problem = baseline_roots(d, "run")
            if roots_problem:
                die(f"batch {bid} cannot retain its verified source: {roots_problem}")
            by_alias = {str(root.get("alias", "")): root for root in roots}
            for source in after:
                root = by_alias.get(str(source.get("alias", "")))
                if root is None:
                    die(f"batch {bid} cannot retain its verified source: root declaration moved")
                retention_problem = retain_tree_objects(d, root, (str(source.get("tree", "")),))
                if retention_problem:
                    die(f"batch {bid} cannot retain its verified source: {retention_problem}")
        source_block: dict[str, object] = {"roots": after, "coverage": "available"}
    else:
        source_block = {"roots": [], "coverage": "documents-only",
                        "reason": "the run declares evidence_mode: documents-only"}
    verification: dict[str, object] = {
        "command": command,
        "status": str(record.get("status", "")),
        "returncode": record.get("returncode"),
        "output_fingerprint": record.get("output_fingerprint", ""),
        "output_tail": list(record.get("output_tail", []) or []),
        "source": source_block,
        "member_bundles": dict(member_bundles),
        "timeout_seconds": timeout,
        "frozen_at": stamp(),
    }
    batch["verification"] = freeze_batch_verification(
        d, bid, verification, bytes(record.get("stdout_bytes", b"")),
        bytes(record.get("stderr_bytes", b"")),
    )
    publish_json(batch_path(d, bid), batch)
    if str(record.get("status", "")) == "passed":
        print(f"froze batch {bid} verification: {command} passed; bound to "
              f"{len(member_bundles)} member bundle(s) and current source")
    else:
        print(f"froze batch {bid} verification: {command} {record.get('status')}; "
              "routed to the orchestrator for recovery, not to member review")
