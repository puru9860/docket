"""Actionable events for every role, derived from run documents."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .common import KNOWN_EVENT_ROLES, MODE_QUICK
from .frontmatter import parse, sections
from .paths import _ordered, handoffs, latest, owners, scope_path
from .policy import (
    is_five_role, mode_of, task_executor, topology, verifier_role_of, workflow_of,
)
from .baselines import evidence_digest
from .bundles import digest_of
from .locks import owner_lock_held
from .state import (
    decide_as, derive_stage, handoff_state, list_amendments, list_batches, list_incidents,
    read_transition, reopen_epoch, state_of, task_depends_on, unfinished_decision,
    unfinished_decision_event,
)
from .freeze import current_bundle
from .aggregates import stale_aggregate
from .verification import (
    current_round_digest, current_task_revision, matching_verifications,
    round_failed_verification, verifier_exempt_set,
)
from .liveness import dispatch_dependencies_unmet, round_dispatched
from .five_role import (
    blocked_route, escalation_events, open_escalations, refused_verifier_correction,
    run_complete_event,
)
from .batches import batch_ready, frontier_ready, verification_fragment


def reviewer_verifier_events(d: Path, role: str) -> list[dict[str, object]]:
    """Events for the reviewer and verifier roles.

    Submissions route to verifiers promptly and individually, never batched:
    a verifier checks whether the evidence holds up. Capable-review readiness
    is batched at explicit integration milestones, and durable escalations
    route to the reviewer with their packet. The     planner receives only
    intent and constraint amendments (written by milestone 10); anything else
    addressed to the planner is a routing error, not an event.
    """
    if not is_five_role(d):
        return []
    workflow = workflow_of(d)
    out: list[dict[str, object]] = []

    def emit(key: str, message: str, owner: str = "", rnd: int = 0,
             revision: str = "") -> None:
        out.append({
            "key": key,
            "role": role,
            "workflow": workflow,
            "owner": owner,
            "round": rnd,
            "generation": reopen_epoch(d, owner, rnd) if owner else 1,
            "revision": revision,
            "message": message,
        })

    if role == "verifier":
        exempt = verifier_exempt_set(d)
        for o in owners(d):
            if o == "orch":
                continue
            if not (d / f"{o}-task.mdx").is_file():
                continue
            # Every submitted task round needs verifier scrutiny, including an
            # executor: orchestrator task: approval still requires a passing
            # verification for the exact frozen bundle, so skipping these
            # owners leaves them submitted with no route to review.
            rnd, st = state_of(d, o)
            if st != "submitted":
                continue
            if o in exempt:
                continue
            frozen, _ = current_bundle(d, o, rnd)
            rev = str((frozen or {}).get("digest", "")) or evidence_digest(
                (d / f"{o}-report-{rnd:02d}.mdx").read_text())
            if rev and matching_verifications(d, o, rnd, rev, current_task_revision(d, o)):
                continue
            emit(f"{o}:{rnd}:submitted",
                 f"{o} round {rnd} submitted; challenge whether the evidence proves acceptance",
                 o, rnd, rev)
        # A granted chain whose refused correction was the verifier's own is the
        # verifier's to finish: no draft decision exists for the reviewer to apply.
        for entry in open_escalations(d, ("granted",)):
            owner = str(entry.get("owner", ""))
            grnd, gst = state_of(d, owner)
            refused = refused_verifier_correction(d, owner, grnd) if gst == "submitted" else None
            if refused is None:
                continue
            iid = str(entry.get("id", owner))
            emit(f"{owner}:{grnd}:budget-granted:{iid}",
                 f"{owner} was granted {entry.get('granted', 1)} more correction round(s): finish "
                 f"the refused correction with `docket verify {d.name} {owner} --result fail "
                 f"--open-correction --as {verifier_role_of(d)}`",
                 owner, grnd, f"sha256:{hashlib.sha256((iid + refused.name).encode()).hexdigest()}")
        return out

    if role == "reviewer":
        rnd, st = state_of(d, "orch")
        if st in ("submitted", "blocked"):
            rep = d / f"orch-report-{rnd:02d}.mdx"
            rev = evidence_digest(rep.read_text()) if rep.is_file() else ""
            if st == "submitted":
                emit(f"orch:{rnd}:submitted", f"orchestrator submitted round {rnd} for review",
                     "orch", rnd, rev)
            else:
                emit(f"orch:{rnd}:blocked", f"orchestrator is BLOCKED at round {rnd} and needs a decision",
                     "orch", rnd, rev)
        exempt = verifier_exempt_set(d)
        for o in owners(d):
            if o == "orch":
                continue
            if not (d / f"{o}-task.mdx").is_file():
                continue
            ornd, ost = state_of(d, o)
            # The orchestrator executed this task itself, so it already knows
            # about the block; the decision it needs belongs to the reviewer,
            # exactly as for a blocked aggregate.
            if ost == "blocked" and task_executor(d, o) == "orchestrator":
                rep = d / f"{o}-report-{ornd:02d}.mdx"
                rev = evidence_digest(rep.read_text()) if rep.is_file() else ""
                emit(f"{o}:{ornd}:blocked",
                     f"{o} is BLOCKED at round {ornd} and needs a decision",
                     o, ornd, rev)
                continue
            # A delegated task's block reaches the reviewer once the
            # orchestrator has routed it, with whatever answer it gave.
            route = blocked_route(d, o, ornd) if ost == "blocked" else None
            if route is not None:
                rep = d / f"{o}-report-{ornd:02d}.mdx"
                rev = evidence_digest(rep.read_text()) if rep.is_file() else ""
                note = str(route.get("note", "") or "")
                emit(f"{o}:{ornd}:blocked-routed",
                     f"{o} round {ornd} is BLOCKED and routed to you: request changes that "
                     f"carry the answer, or waive" + (f"; orchestrator answer: {note}" if note
                                                       else ""),
                     o, ornd, rev)
                continue
            # As above, a failed verification on an executor: orchestrator
            # task still routes to the reviewer for required changes.
            if ost != "submitted" or o in exempt:
                continue
            if not round_failed_verification(d, o, ornd):
                continue
            digest = current_round_digest(d, o, ornd)
            contract_rev = current_task_revision(d, o)
            matched = matching_verifications(d, o, ornd, digest, contract_rev)
            if not matched:
                continue
            _, fvmeta, fvbody = matched[-1]
            findings = sections(fvbody).get("Findings", "").strip()
            revision = "sha256:" + hashlib.sha256(
                f"{digest}:{contract_rev}:{findings}".encode()).hexdigest()
            # A completed verifier correction leaves the report
            # changes-requested, so a round still submitted here whose failure
            # asked for a correction is an interrupted one. It must stay
            # visible: nothing else wakes for it. Once its decision transition
            # has begun, the unfinished-decision event below speaks for it,
            # and while the owner lock is held the correction is still running.
            if str(fvmeta.get("opened_correction", "")) in ("yes", "requested"):
                if owner_lock_held(d, o) or unfinished_decision(d, o)[1]:
                    continue
                emit(f"{o}:{ornd}:verification-failed",
                     f"{o} round {ornd} failed verification and its verifier-opened "
                     "correction was interrupted; repeat the verifier's --open-correction "
                     "command to finish it, or decide the required changes",
                     o, ornd, revision)
                continue
            emit(f"{o}:{ornd}:verification-failed",
                 f"{o} round {ornd} failed verification; decide the required changes",
                 o, ornd, revision)
        for batch in list_batches(d):
            if batch.get("state") != "closed" or not batch.get("milestone"):
                continue
            ready, revision = batch_ready(d, batch)
            if not ready:
                continue
            bid = str(batch.get("batch"))
            emit(f"batch:{bid}:ready:{batch.get('generation', 1)}",
                 f"milestone batch {bid} ready for capable review", "", 0, revision)
        if mode_of(d) == MODE_QUICK:
            closed = [b for b in list_batches(d) if b.get("state") == "closed"]
            derived = {str(e["key"]) for e in out}
            for key, message, revision in review_readiness_events(
                    d, review_scope_states(d), closed):
                if key not in derived:
                    emit(key, message, "", 0, revision)
        for batch in list_batches(d):
            if batch.get("state") != "closed":
                continue
            frontier, revision = frontier_ready(d, batch)
            if not frontier:
                continue
            bid = str(batch.get("batch"))
            emit(f"batch:{bid}:frontier:{batch.get('generation', 1)}",
                 f"review frontier {bid} ready with {','.join(frontier)}", "", 0, revision)
        # Every decision transition is the reviewer's to finish in five-role,
        # including a verifier-opened correction, and an interrupted one leaves
        # a state no other event describes.
        for o in owners(d):
            unfinished = unfinished_decision_event(d, d.name, o)
            if unfinished:
                key, message, urnd, revision = unfinished
                emit(key, message, o, urnd, revision)
        # A granted chain leaves the refused correction for the reviewer to
        # apply; its draft decision is still unapplied for the current round.
        for entry in open_escalations(d, ("granted",)):
            owner = str(entry.get("owner", ""))
            grnd, gst = state_of(d, owner)
            if gst != "submitted":
                continue
            draft = d / f"{owner}-decision-{grnd:02d}.mdx"
            try:
                dmeta, _ = parse(draft.read_text())
            except (OSError, ValueError):
                continue
            if str(dmeta.get("applied", "")) == "yes":
                continue
            iid = str(entry.get("id", owner))
            emit(f"{owner}:{grnd}:budget-granted:{iid}",
                 f"{owner} was granted {entry.get('granted', 1)} more correction round(s): apply "
                 f"the refused changes with `docket decide {d.name} {owner} --changes"
                 f"{decide_as(d)}`",
                 owner, grnd, f"sha256:{hashlib.sha256((iid + draft.name).encode()).hexdigest()}")
        return out

    return out


def derive_events(d: Path, role: str, _logical: bool = False) -> list[dict[str, object]]:
    """Actionable event records for a supervising role, derived from documents.

    Derivation is a pure projection of durable lifecycle documents: the same
    documents always yield the same event identities, so a missing derived
    record can be recreated by reconciliation and a delayed event is actionable
    only while its round, artifact revision, destination, and generation still
    match current durable state. Transport records (claims, receipts, retries)
    live outside lifecycle frontmatter, under `.delivery/`.
    """
    if not _logical and mode_of(d) == MODE_QUICK:
        logical_roles = {
            "coordinator": ("planner", "orchestrator"),
            "checker": ("verifier", "reviewer"),
        }.get(role)
        if logical_roles is not None:
            mapped: list[dict[str, object]] = []
            for logical_role in logical_roles:
                for event in derive_events(d, logical_role, _logical=True):
                    physical = dict(event)
                    physical["role"] = role
                    mapped.append(physical)
            return mapped
        if role in ("planner", "orchestrator", "verifier", "reviewer"):
            return []
    workflow = workflow_of(d)
    if role not in KNOWN_EVENT_ROLES:
        return []
    if role not in ("planner", "orchestrator", "verifier", "reviewer"):
        return []
    if role in ("verifier", "reviewer"):
        return reviewer_verifier_events(d, role)
    out: list[dict[str, object]] = []

    def emit(key: str, message: str, owner: str = "", rnd: int = 0,
             revision: str = "") -> None:
        out.append({
            "key": key,
            "role": role,
            "workflow": workflow,
            "owner": owner,
            "round": rnd,
            "generation": reopen_epoch(d, owner, rnd) if owner else 1,
            "revision": revision,
            "message": message,
        })

    if role == "planner":
        if topology(d) == "combined":
            return []
        if is_five_role(d):
            for key, message, owner, ernd, revision in escalation_events(d):
                emit(key, message, owner, ernd, revision)
            complete = run_complete_event(d)
            if complete:
                emit(*complete)
        if not is_five_role(d):
            rnd, st = state_of(d, "orch")
            if st == "submitted":
                rep = d / f"orch-report-{rnd:02d}.mdx"
                rev = evidence_digest(rep.read_text()) if rep.is_file() else ""
                emit(f"orch:{rnd}:submitted", f"orchestrator submitted round {rnd} for review",
                     "orch", rnd, rev)
            elif st == "blocked":
                rep = d / f"orch-report-{rnd:02d}.mdx"
                rev = evidence_digest(rep.read_text()) if rep.is_file() else ""
                emit(f"orch:{rnd}:blocked", f"orchestrator is BLOCKED at round {rnd} and needs a decision",
                     "orch", rnd, rev)
            # A legacy split planner decides the aggregate, so an interrupted
            # aggregate decision is its to finish.
            unfinished = unfinished_decision_event(d, d.name, "orch")
            if unfinished:
                key, message, urnd, revision = unfinished
                emit(key, message, "orch", urnd, revision)
        for amendment in list_amendments(d):
            if amendment.get("status") != "pending":
                continue
            owner = str(amendment.get("owner", ""))
            iid = str(amendment.get("id", ""))
            emit(f"{owner}:amendment:{iid}",
                 f"{owner} needs an intent decision: {amendment.get('need', '')}",
                 owner, state_of(d, owner)[0],
                 f"sha256:{hashlib.sha256(iid.encode()).hexdigest()}")
        return out

    # Combined topology puts the plan in the orchestrator's session, so the
    # plan owner's escalations are its to answer.
    if topology(d) == "combined" and is_five_role(d):
        for key, message, owner, ernd, revision in escalation_events(d):
            emit(key, message, owner, ernd, revision)
        complete = run_complete_event(d)
        if complete:
            emit(*complete)
    scope = []
    orchestrator_tasks = []
    for o in owners(d):
        if o == "orch":
            continue
        executor = task_executor(d, o)
        if executor == "implementor":
            scope.append(o)
        elif executor == "orchestrator":
            orchestrator_tasks.append(o)

    def emit_correction_ready(owner: str, message: str) -> None:
        """Wake the role that can dispatch the owner into its correction round.

        The wake lasts only until that round is dispatched: once a live
        dispatch record binds the correction round, the work is in a worker's
        hands and asking for re-dispatch again would only burn a supervisor
        turn.
        """
        rnd, st = state_of(d, owner)
        if st != "draft" or not rnd:
            return
        decs = _ordered(d.glob(f"{owner}-decision-*.mdx"))
        if not decs:
            return
        try:
            dmeta, _ = parse(decs[-1].read_text())
        except (OSError, ValueError):
            dmeta = {}
        try:
            dec_round = int(dmeta.get("round", 0) or 0)
        except (TypeError, ValueError):
            dec_round = 0
        if str(dmeta.get("verdict", "")) != "changes-requested" or rnd <= dec_round:
            return
        if round_dispatched(d, owner, rnd):
            return
        try:
            dec_rev = digest_of(decs[-1].read_bytes())
        except OSError:
            dec_rev = ""
        emit(f"{owner}:{rnd}:correction-ready", message, owner, rnd, dec_rev)

    for o in scope:
        rnd, st = state_of(d, o)
        capsule = scope_path(d, o)
        if capsule.is_file():
            sm, _ = parse(capsule.read_text())
            if sm.get("status") == "collision":
                detail = sm.get("collisions", "scope ownership overlaps another active task")
                digest = hashlib.sha256(detail.encode()).hexdigest()[:10]
                emit(f"{o}:scope:collision:{digest}",
                     f"{o} discovery scope collides with active work and needs sequencing",
                     o, rnd, f"sha256:{hashlib.sha256(detail.encode()).hexdigest()}")
        handoff_number, handoff_status = handoff_state(d, o)
        if handoff_status == "ready" and st == "draft":
            hp = latest(handoffs(d, o))
            rev = digest_of(hp.read_bytes()) if hp and hp.is_file() else ""
            emit(f"{o}:handoff:{handoff_number}:ready",
                 f"{o} handoff checkpoint {handoff_number} is ready; resume with a replacement implementor",
                 o, rnd, rev)
        if st == "blocked" and not (is_five_role(d) and blocked_route(d, o, rnd)):
            rep = d / f"{o}-report-{rnd:02d}.mdx"
            rev = evidence_digest(rep.read_text()) if rep.is_file() else ""
            handoff = (f"; answer what is yours, then `docket route {d.name} --kind blocked "
                       f"--owner {o}` queues a notification for the "
                       f"{'checker' if mode_of(d) == MODE_QUICK else 'reviewer'} to decide it"
                       if is_five_role(d) else "")
            emit(f"{o}:{rnd}:blocked",
                 f"{o} is BLOCKED at round {rnd} and needs a decision{handoff}", o, rnd, rev)
        emit_correction_ready(
            o, f"{o} round {rnd} needs re-dispatch to an implementor for the requested correction")

    # An executor: orchestrator task has no implementor scope to enter the
    # ordinary delegated-task projection above, and neither does the aggregate.
    # Each correction still needs an actionable wake for the orchestrator, which
    # owns that execution; nothing else re-enters it once the reviewer or the
    # planner has asked for changes.
    for o in orchestrator_tasks:
        rnd = state_of(d, o)[0]
        emit_correction_ready(
            o, f"{o} round {rnd} needs re-dispatch to the orchestrator for the requested "
            "correction")
    orch_rnd = state_of(d, "orch")[0]
    emit_correction_ready(
        "orch", f"aggregate report round {orch_rnd} needs the requested correction "
        "from the orchestrator")

    # A task gated on dependencies becomes dispatchable only when they are
    # approved, and nothing else re-enters the orchestrator at that moment: the
    # dependent is still a draft, so neither review readiness nor all-decided
    # forms. The wake lasts until the round is dispatched.
    for o in scope + orchestrator_tasks:
        rnd, st = state_of(d, o)
        if st != "draft" or not rnd:
            continue
        gates = set(task_depends_on(d, o))
        for batch in list_batches(d):
            if o in [str(m) for m in (batch.get("members") or [])]:
                gates.update(str(dep) for dep in ((batch.get("depends_on") or {}).get(o) or []))
        if not gates or dispatch_dependencies_unmet(d, o) or round_dispatched(d, o, rnd):
            continue
        if derive_stage(d, o) != "initial":
            # Corrections and handoffs already carry their own wakes.
            continue
        met = ", ".join(f"{dep} {state_of(d, dep)[1]}" for dep in sorted(gates))
        signature = ";".join(f"{dep}:{state_of(d, dep)[1]}" for dep in sorted(gates))
        emit(f"{o}:{rnd}:dispatch-ready",
             f"{o} is ready to dispatch: its dependencies are settled ({met})",
             o, rnd, f"sha256:{hashlib.sha256(signature.encode()).hexdigest()}")

    # A legacy orchestrator decides its delegated task rounds itself, so an
    # interrupted task decision is its to finish. Five-role routes these to the
    # reviewer, who holds decision authority there.
    if not is_five_role(d):
        for o in scope + orchestrator_tasks:
            unfinished = unfinished_decision_event(d, d.name, o)
            if unfinished:
                key, message, urnd, revision = unfinished
                emit(key, message, o, urnd, revision)

    for incident in list_incidents(d):
        if incident.get("state") != "open":
            continue
        owner = str(incident.get("owner", ""))
        try:
            epoch = reopen_epoch(d, owner, state_of(d, owner)[0])
        except (OSError, ValueError):
            epoch = 1
        if str(incident.get("epoch", 1)) != str(epoch):
            continue
        cause = str(incident.get("cause", "stalled"))
        iid = str(incident.get("id", owner))
        emit(f"{owner}:stalled:{iid.rsplit('-', 1)[-1] if '-' in iid else iid}",
             f"{owner} stalled ({cause}); one incident yields one recovery event",
             owner, state_of(d, owner)[0],
             f"sha256:{hashlib.sha256(cause.encode()).hexdigest()}")

    closed_batches = [b for b in list_batches(d) if b.get("state") == "closed"]
    terminal = {"approved", "waived", "completed"}
    # The same tracked set gates review readiness and the all-decided wake.
    states = review_scope_states(d)
    if states and not any(st == "blocked" for _, _, st in states):
        # In quick the checker holds the review duty itself, so verified work
        # wakes the checker directly; routing it through the coordinator would
        # spend a coordinator turn, and usually a checker prompt, on news the
        # checker acts on anyway.
        if mode_of(d) != MODE_QUICK:
            for key, message, revision in review_readiness_events(d, states, closed_batches):
                emit(key, message, "", 0, revision)
        orch_status = state_of(d, "orch")[1]
        if all(st in terminal for _, _, st in states) and orch_status not in {
            "submitted", "completed", "approved", "waived",
        }:
            signature = ";".join(f"{o}:{rnd}:{st}" for o, rnd, st in states)
            sig12 = hashlib.sha256(signature.encode()).hexdigest()[:12]
            revision = f"sha256:{hashlib.sha256(signature.encode()).hexdigest()}"
            agg_stale = stale_aggregate(d)
            if agg_stale:
                emit(f"all:decided:stale-aggregate:{sig12}",
                     f"all {len(states)} delegated tasks decided, but the aggregate bundle is stale; "
                     f"resubmit the orchestrator report to freeze a new one",
                     "", 0, revision)
            else:
                emit(f"all:decided:{sig12}",
                     f"all {len(states)} delegated tasks decided - complete the aggregate report",
                     "", 0, revision)

    # Notify of reopened tasks that invalidate existing aggregate evidence.
    for o in scope:
        journal = read_transition(d, o)
        if journal.get("verdict") == "reopen-waived" and journal.get("state") == "complete":
            rnd = journal.get("round", "")
            emit(f"{o}:reopened:{rnd}",
                 f"{o} round {rnd} was reopened from a waiver; any aggregate bundle that pinned "
                 f"it is stale and must be resubmitted",
                 o, int(rnd or 0), str(journal.get("evidence_digest", "")))
    return out


def review_scope_states(d: Path) -> list[tuple[str, int, str]]:
    """(owner, round, status) for every task whose review readiness is tracked.

    Delegated tasks always count. Under five-role-v1 an orchestrator-owned task
    is verified and decided like delegated work, so it counts too; legacy runs
    complete those tasks on submission and keep their original projection.
    """
    five = is_five_role(d)
    out: list[tuple[str, int, str]] = []
    for o in owners(d):
        if o == "orch":
            continue
        executor = task_executor(d, o)
        if executor == "implementor" or (five and executor == "orchestrator"):
            out.append((o, *state_of(d, o)))
    return out


def review_readiness_events(d: Path, states: list[tuple[str, int, str]],
                            closed_batches: list[dict]) -> list[tuple[str, str, str]]:
    """(key, message, revision) for work that is ready for a review decision.

    Without closed batches, one whole-run batch forms once every tracked task
    is submitted or terminal. Under five-role-v1 readiness means verified:
    waking a supervisor at submission leaves it nothing to route and nothing
    to wait on, so it would poll until the verifier finished. The batch waits
    for every submitted member to hold a resolved verification (pass,
    uncertain, or verifier_exempt); a failure routes to the reviewer
    separately and keeps the batch unready. Verdicts and findings are part of
    the identity, so a new verification after a failure yields a new wake.
    """
    terminal = {"approved", "waived", "completed"}
    five = is_five_role(d)
    found: list[tuple[str, str, str]] = []
    if not states or any(st == "blocked" for _, _, st in states):
        return found
    if not closed_batches and any(st == "submitted" for _, _, st in states) and all(
        st == "submitted" or st in terminal for _, _, st in states
    ):
        signature = ";".join(f"{o}:{rnd}:{st}" for o, rnd, st in states)
        resolved = True
        if five:
            fragments = []
            for o, rnd, st in states:
                if st != "submitted":
                    continue
                fragment = verification_fragment(d, o, rnd)
                if not fragment:
                    resolved = False
                    break
                fragments.append(fragment)
            signature = ";".join([signature, *fragments])
        if resolved:
            digest = hashlib.sha256(signature.encode()).hexdigest()[:12]
            submitted = [f"{o} round {rnd}" for o, rnd, st in states if st == "submitted"]
            kind = "verified" if five else "submitted"
            found.append((f"review-batch:{digest}",
                          f"review batch ready with {len(submitted)} {kind} task report(s): "
                          + ", ".join(submitted),
                          f"sha256:{hashlib.sha256(signature.encode()).hexdigest()}"))
    for batch in closed_batches:
        ready, revision = batch_ready(d, batch)
        if not ready:
            continue
        bid = str(batch.get("batch"))
        gen = batch.get("generation", 1)
        members = ",".join(str(m) for m in (batch.get("members") or []))
        found.append((f"batch:{bid}:ready:{gen}",
                      f"review batch {bid} ready with {members}"
                      + (" (milestone)" if batch.get("milestone") else ""),
                      revision))
    return found


def events(d: Path, role: str) -> list[tuple[str, str]]:
    """Actionable (key, message) pairs for a supervising role."""
    return [(str(e["key"]), str(e["message"])) for e in derive_events(d, role)]
