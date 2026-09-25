"""Retry-safe decision transitions and reopens."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from .common import DEFAULT_REVIEWER, MODE_QUICK, PLACEHOLDER, die, stamp
from .frontmatter import is_empty, parse, render, sections, set_section
from .paths import decisions, latest, reports
from .publication import fault, publish, publish_json
from .policy import (
    artifact_policy_fields, is_five_role, mode_of, need_run, plan_flag, require_five_role,
    task_executor,
)
from .baselines import evidence_digest, evidence_mode, task_verify
from .bundles import digest_of
from .locks import owner_lock
from .state import (
    applied_decision, batch_path, group_numbered_items, latest_verification, list_batches,
    normalized_scope, read_batch, read_transition, record_reopen, scope_collisions,
    seed_report_acceptance, transition_path,
)
from .dependencies import dependency_problems, provisional_dependencies, refreshed_inputs
from .freeze import current_bundle, freeze_task_bundle
from .aggregates import (
    current_aggregate, freeze_aggregate_bundle, require_aggregate, stale_aggregate,
)
from .verification import (
    frozen_verification_status, run_verification, skipped_approval_problem, task_env,
    verify_slot_busy, verify_slot_path, verify_slot_refusal,
)
from .templates import template
from .sessions import collect_run_usage
from .models import record_outcome
from .five_role import clear_corrections, guard_correction_budget, note_correction
from .gate import gate_problems
from .amendments import amendment_blocks


REOPEN_STEPS = ("next-round",)


def reopen_collision_problems(d: Path, owner: str) -> list[str]:
    """Collisions that block reclaiming the accepted scope on reopen."""
    task = d / f"{owner}-task.mdx"
    scope: list[str] = []
    if task.is_file():
        scope = normalized_scope(parse(task.read_text())[0])
    if not scope:
        return []
    return scope_collisions(d, owner, scope)


def reopen_finish(d: Path, a: argparse.Namespace, journal: dict, reason: str) -> Path:
    """Finish an in-progress reopen journal, idempotently.

    Uses only the journal's recorded round, report, decision, and evidence
    digest - never the latest report, which may already be the next-round
    draft created before the crash. The next-round file is reused when it
    already exists, so a retry never opens a second round.
    """
    rnd = int(journal.get("round", 0))
    rep_name = str(journal.get("report", f"{a.owner}-report-{rnd:02d}.mdx"))
    dec_name = str(journal.get("decision", f"{a.owner}-decision-{rnd:02d}.mdx"))
    rep = d / rep_name
    dec = d / dec_name
    if not rep.is_file():
        die(f"{rep_name} is missing, so the interrupted reopen cannot be finished")
    report_meta, _ = parse(rep.read_text())
    if report_meta.get("status") != "waived":
        die(
            f"{rep.name} is {report_meta.get('status')}, not waived; the interrupted "
            "reopen was recorded against a waived round that has since moved"
        )
    if not dec.is_file():
        die(f"{dec_name} is missing, so there is no waiver to reopen")
    dec_meta, _ = parse(dec.read_text())
    if dec_meta.get("verdict") != "waived":
        die(f"{dec.name} recorded {dec_meta.get('verdict')}, not waived; nothing to reopen")
    recorded_digest = str(journal.get("evidence_digest", ""))
    if recorded_digest and dec_meta.get("evidence_digest", "") != recorded_digest:
        die(
            f"{dec.name} no longer matches the evidence the interrupted reopen "
            "was recorded against; resolve the change before reopening"
        )
    collisions = reopen_collision_problems(d, a.owner)
    if collisions:
        scope = normalized_scope(parse((d / f"{a.owner}-task.mdx").read_text())[0]) \
            if (d / f"{a.owner}-task.mdx").is_file() else []
        die(
            f"{a.owner} cannot be reopened: the accepted scope {', '.join(scope)} now overlaps "
            f"active work:\n      " + "\n      ".join(f"- {item}" for item in sorted(collisions))
            + "\n    Resolve the collision before reopening."
        )
    nxt_name = str(journal.get("next_report", f"{a.owner}-report-{rnd + 1:02d}.mdx"))
    nxt = d / nxt_name
    if not nxt.is_file():
        nxt = open_next_round(d, a, rnd, report_meta)
    # Move dependent batches into a new generation. Targets are journalled
    # before any batch file is touched, so a retry writes the same numbers
    # instead of incrementing twice.
    recorded_targets = journal.get("batch_generations")
    targets = dict(recorded_targets) if isinstance(recorded_targets, dict) else {}
    for batch in list_batches(d):
        bid = str(batch.get("batch", ""))
        members = [str(m) for m in (batch.get("members") or [])]
        if bid and a.owner in members and bid not in targets:
            try:
                current = int(batch.get("generation", 1) or 1)
            except (TypeError, ValueError):
                current = 1
            targets[bid] = current + 1
    if targets and targets != (recorded_targets or {}):
        journal["batch_generations"] = targets
        publish_json(transition_path(d, a.owner), journal)
    for bid, generation in targets.items():
        live = read_batch(d, bid)
        if live and live.get("generation", 1) != generation:
            live["generation"] = generation
            live["regenerated_at"] = stamp()
            live["regenerated_by"] = f"reopen:{a.owner}"
            publish_json(batch_path(d, bid), live)
    journal["completed"] = ["next-round"]
    publish_json(transition_path(d, a.owner), journal)
    fault("transition:next-round")
    record_reopen(d, a.owner, journal)
    journal["state"] = "complete"
    fault("transition:complete")
    publish_json(transition_path(d, a.owner), journal)
    return nxt


def reopen_waived(
    d: Path, a: argparse.Namespace, rnd: int, reason: str,
) -> None:
    """Audited reopen of a waived task: preserve prior evidence, open the next round.

    The prior round, its report body, and its recorded reason are left unchanged.
    One next round is opened as an explicit extension of the monotonic-round
    invariant. Accepted scope is reclaimed subject to collision rules, and the
    reopen is refused when a collision blocks reclamation.
    """
    rep = d / f"{a.owner}-report-{rnd:02d}.mdx"
    if not rep.is_file():
        die(f"no report for {a.owner} round {rnd}")
    report_meta, report_body = parse(rep.read_text())
    if report_meta.get("status") != "waived":
        die(f"{rep.name} is {report_meta.get('status')}, not waived; only a waived task can be reopened")
    _ = report_body
    dec = d / f"{a.owner}-decision-{rnd:02d}.mdx"
    if not dec.is_file():
        dec = latest(decisions(d, a.owner))
    if not dec:
        die(f"{a.owner} has no decision for round {rnd}, so there is no waiver to reopen")
    dec_meta, dec_body = parse(dec.read_text())
    _ = dec_body
    if dec_meta.get("verdict") != "waived":
        die(f"{dec.name} recorded {dec_meta.get('verdict')}, not waived; nothing to reopen")

    txn = transition_id(a.run, a.owner, rnd, "reopen-waived", dec_meta.get("evidence_digest", ""))

    existing = read_transition(d, a.owner)
    if existing.get("state") == "in-progress":
        if existing.get("verdict") != "reopen-waived":
            die(
                f"{a.owner} has an unfinished {existing.get('verdict')} transition "
                f"{existing.get('transition')} for round {existing.get('round')}; repeat that verdict "
                "to finish it before reopening"
            )
        if str(existing.get("round", "")) == str(rnd) and existing.get("transition") == txn:
            recorded = str(existing.get("reopen_reason", ""))
            if reason and recorded and reason != recorded:
                die(
                    f"unfinished transition {txn} already recorded the reason {recorded!r}; retrying with "
                    "different text would rewrite the decision it is meant to finish. Repeat --reopen "
                    "without --reason, or restate the recorded reason exactly"
                )
            nxt = reopen_finish(d, a, existing, recorded or reason)
            print(f"{a.owner} round {rnd} waiver reopened -> {nxt.name}")
            print(f"Prior round {rnd}, its report, and its waiver reason are preserved.")
            print(f"Any aggregate bundle that pinned {a.owner} round {rnd} is now stale.")
            return

    collisions = reopen_collision_problems(d, a.owner)
    if collisions:
        scope: list[str] = []
        task = d / f"{a.owner}-task.mdx"
        if task.is_file():
            scope = normalized_scope(parse(task.read_text())[0])
        die(
            f"{a.owner} cannot be reopened: the accepted scope {', '.join(scope)} now overlaps "
            f"active work:\n      " + "\n      ".join(f"- {item}" for item in sorted(collisions))
            + "\n    Resolve the collision before reopening."
        )

    journal: dict[str, object] = {
        "transition": txn,
        "run": a.run,
        "owner": a.owner,
        "round": rnd,
        "verdict": "reopen-waived",
        "evidence_digest": dec_meta.get("evidence_digest", ""),
        "report": rep.name,
        "decision": dec.name,
        "next_report": f"{a.owner}-report-{rnd + 1:02d}.mdx",
        "steps": list(REOPEN_STEPS),
        "completed": [],
        "state": "in-progress",
        "reopen_reason": reason,
        "reopen_at": stamp(),
    }

    publish_json(transition_path(d, a.owner), journal)
    fault("transition:begin")

    nxt = open_next_round(d, a, rnd, report_meta)
    publish_json(transition_path(d, a.owner), journal)

    nxt = reopen_finish(d, a, journal, reason)

    print(f"{a.owner} round {rnd} waiver reopened -> {nxt.name}")
    print(f"Prior round {rnd}, its report, and its waiver reason are preserved.")
    print(f"Any aggregate bundle that pinned {a.owner} round {rnd} is now stale.")


def require_bundle(
    d: Path, owner: str, rnd: int, rep: Path, digest: str
) -> tuple[dict[str, object], str]:
    """The frozen bundle this verdict must bind to, and whether it is stale for the body.

    Missing or damaged bundle evidence stops a decision outright. A verdict that bound
    to nothing, or to bytes that no longer exist, would be an approval of whatever the
    workspace happens to hold when someone next looks.
    """
    frozen, integrity = current_bundle(d, owner, rnd)
    if integrity:
        die(
            f"{owner} round {rnd} cannot be decided against damaged frozen evidence:\n      "
            + "\n      ".join(f"- {item}" for item in integrity)
            + "\n    Restore the bundle, or return the report to status: draft and resubmit it."
        )
    if frozen is None:
        die(
            f"{owner} round {rnd} has no frozen evidence bundle, so a verdict would bind to "
            f"nothing. Restore {rep.name} to status: draft and run `docket submit {d.name} "
            f"{owner}` so the contract, baseline, patch, source revision, report body, and "
            "verification freeze together"
        )
    recorded = str(frozen.get("report", {}).get("body_digest", ""))
    return frozen, "" if recorded == digest else str(frozen.get("digest", ""))


TRANSITION_STEPS = {
    "approved": ("decision", "report"),
    "waived": ("decision", "report"),
    "changes-requested": ("decision", "report", "next-round"),
}


def transition_id(run: str, owner: str, rnd: int, verdict: str, digest: str) -> str:
    """Durable identity of one decision transition, stable across restarts."""
    seed = "|".join([run, owner, str(rnd), verdict, digest])
    return "txn:" + hashlib.sha256(seed.encode()).hexdigest()[:16]


def applied_steps(d: Path, owner: str, rnd: int, verdict: str, dec: Path) -> set[str]:
    """Transition steps the artifacts themselves already show as applied.

    The journal records intent and progress; the artifacts are the truth. Deriving
    the completed set from them is what lets a restart recover a transition whose
    journal was never written, including one an older docket left behind.
    """
    done: set[str] = set()
    if dec.is_file():
        meta, _ = parse(dec.read_text())
        if meta.get("applied") == "yes" and meta.get("verdict") == verdict:
            done.add("decision")
    rep = d / f"{owner}-report-{rnd:02d}.mdx"
    if rep.is_file():
        meta, _ = parse(rep.read_text())
        if meta.get("status") == verdict and meta.get("decision") == dec.name:
            done.add("report")
    if verdict == "changes-requested" and (d / f"{owner}-report-{rnd + 1:02d}.mdx").exists():
        done.add("next-round")
    return done


def open_next_round(d: Path, a: argparse.Namespace, rnd: int, report_meta: dict[str, str]) -> Path:
    """Open exactly one next round. An existing round is never replaced."""
    nxt = d / f"{a.owner}-report-{rnd + 1:02d}.mdx"
    if nxt.exists():
        return nxt
    report_template = "orchestrator_report" if a.owner == "orch" else "report"
    fields = {
        "run": a.run, "owner": a.owner, "round": rnd + 1,
        "harness": report_meta.get("harness", ""),
        "model": report_meta.get("requested_model", report_meta.get("model", "")),
        "effort": report_meta.get("requested_effort", ""),
        **artifact_policy_fields(d),
    }
    if a.owner == "orch":
        # A correction round re-verifies the same integration command.
        fields["verify"] = report_meta.get("verify", "")
        fields["verify_timeout"] = report_meta.get("verify_timeout", "900")
    text = template(report_template).format(**fields)
    if report_meta.get("actual_model"):
        nm, nb = parse(text)
        nm["actual_model"] = report_meta["actual_model"]
        nm["model_history"] = report_meta.get("model_history", report_meta["actual_model"])
        nm["actual_effort"] = report_meta.get("actual_effort", "")
        nm["effort_history"] = report_meta.get(
            "effort_history", report_meta.get("actual_effort", "")
        )
        text = render(nm, nb)
    publish(nxt, text)
    seed_report_acceptance(d, a.owner, nxt)
    return nxt


def commit_transition(
    a: argparse.Namespace, d: Path, rnd: int, verdict: str,
    rep: Path, report_meta: dict[str, str], report_body: str,
    dec: Path, decision_meta: dict[str, str], decision_body: str,
) -> list[str]:
    """Apply one decision transition step by step, and return the steps applied.

    Every step is idempotent and is re-derived from the artifacts before it runs,
    so an interrupted approve, waive, or changes finishes on retry instead of
    overwriting review evidence or opening a second round.
    """
    txn = decision_meta.get("transition") or transition_id(
        a.run, a.owner, rnd, verdict, decision_meta.get("evidence_digest", "")
    )
    decision_meta["transition"] = txn
    steps = list(TRANSITION_STEPS[verdict])
    done = applied_steps(d, a.owner, rnd, verdict, dec)
    remaining = [step for step in steps if step not in done]
    journal = read_transition(d, a.owner)
    if not remaining and journal.get("state") != "in-progress":
        return []

    journal = {
        "transition": txn,
        "run": a.run,
        "owner": a.owner,
        "round": rnd,
        "verdict": verdict,
        "evidence_digest": decision_meta.get("evidence_digest", ""),
        "report": rep.name,
        "decision": dec.name,
        "next_report": f"{a.owner}-report-{rnd + 1:02d}.mdx" if "next-round" in steps else "",
        "steps": steps,
        "completed": sorted(done),
        "state": "in-progress",
        # The reviewer-authored payload, recorded before the first artifact write so a
        # retry finishes this decision instead of reconstructing a different one.
        "decision_meta": dict(decision_meta),
        "decision_body": decision_body,
    }
    publish_json(transition_path(d, a.owner), journal)
    fault("transition:begin")

    applied: list[str] = []
    for step in remaining:
        if step == "decision":
            publish(dec, render(decision_meta, decision_body))
        elif step == "report":
            # A re-review freezes a replacement bundle for the same round. The decided
            # report must name that replacement, matching the decision. On a resumed
            # transition the report was re-parsed from the superseded file while the
            # decision already carries the replacement, so copy it every time.
            decided_digest = (decision_meta.get("bundle_digest") or "").strip()
            if decided_digest:
                report_meta["bundle_digest"] = decided_digest
            report_meta["status"] = verdict
            report_meta["decision"] = dec.name
            publish(rep, render(report_meta, report_body))
        else:
            open_next_round(d, a, rnd, report_meta)
        applied.append(step)
        fault(f"transition:{step}")
        journal["completed"] = sorted(set(journal["completed"]) | {step})
        publish_json(transition_path(d, a.owner), journal)

    journal["state"] = "complete"
    publish_json(transition_path(d, a.owner), journal)
    return applied


def pending_payload(pending: dict, rnd: int, verdict: str) -> tuple[dict[str, str], str] | None:
    """The complete decision payload an unfinished transition already committed to.

    Verdict, reviewer, reason, and the reviewer-authored decision body are journalled
    before the first artifact write, so finishing an interrupted transition never
    depends on the retry repeating free text the reviewer may no longer have. A journal
    from an older docket carries no payload; that falls back to the argument path.
    """
    if not pending or pending.get("state") != "in-progress":
        return None
    if str(pending.get("round", "")) != str(rnd) or pending.get("verdict") != verdict:
        return None
    meta, body = pending.get("decision_meta"), pending.get("decision_body")
    if not isinstance(meta, dict) or not isinstance(body, str):
        return None
    return {str(k): str(v) for k, v in meta.items()}, body


def adopt_pending_payload(
    a: argparse.Namespace, pending_meta: dict[str, str], pending_body: str
) -> tuple[dict[str, str], str]:
    """Finish the decision an interrupted transition recorded, never a different one.

    The journalled payload is immutable in every field. A retry may repeat what was
    recorded, or supply nothing at all, but it may never state something else - and
    an absent reason is something the transition recorded, not a gap left open. A
    retry that filled one in would be publishing a decision nobody made under the
    identity of the transition it claims to be finishing.
    """
    txn = pending_meta.get("transition", "?")
    recorded_reviewer = pending_meta.get("reviewer", "")
    if a.reviewer is not None and a.reviewer != recorded_reviewer:
        die(
            f"unfinished transition {txn} recorded reviewer {recorded_reviewer!r}; retrying as "
            f"{a.reviewer!r} would change the decision that was already begun. Repeat the verdict "
            "without --reviewer to finish it"
        )
    recorded = sections(pending_body).get("Reason", "").strip()
    supplied = a.reason.strip()
    if supplied and supplied != recorded:
        die(
            f"unfinished transition {txn} already recorded the reason {recorded!r}; retrying with "
            "different text would rewrite the decision it is meant to finish. Repeat the verdict "
            "without --reason, or restate the recorded reason exactly. State anything new in the "
            "next round instead"
        )
    supplied_changes = list(getattr(a, "change", None) or [])
    if supplied_changes:
        recorded_changes = sections(pending_body).get("Required changes", "").strip()
        if format_supplied_changes(supplied_changes).strip() != recorded_changes:
            die(
                f"unfinished transition {txn} already recorded its required changes; retrying with "
                "different --change text would rewrite the decision it is meant to finish. Repeat "
                "the verdict without --change to finish it exactly as recorded. State anything new "
                "in the next round instead"
            )
    return pending_meta, pending_body


def format_supplied_changes(items: list[str]) -> str:
    """Number one-call `--change` items as the decision's Required changes.

    Each repeated `--change TEXT` becomes one numbered item in order, so the
    reviewer records every required correction in a single invocation instead
    of opening a draft first. An item that already carries a leading number is
    renumbered, so the recorded list stays sequential.
    """
    numbered: list[str] = []
    for index, raw in enumerate(items, 1):
        text = (raw or "").strip()
        if not text:
            die("--change needs non-empty TEXT; repeat --change once per required change")
        text = re.sub(r"^\d+[.)]\s+", "", text).strip()
        if not text:
            die("--change needs non-empty TEXT; repeat --change once per required change")
        numbered.append(f"{index}. {text}")
    return "\n".join(numbered)


def correction_handoff_line(d: Path, owner: str, dec: Path, nxt: Path) -> str:
    """How the applied correction reaches its worker under the active preset.

    A changes request never assigns work directly: the decision opens the next
    round and a `correction-ready` wake tells the role that dispatches it. That
    role is the orchestrator in standard and the coordinator in quick, and a
    round the orchestrator executes itself is taken up rather than dispatched.
    """
    try:
        _, body = parse(dec.read_text())
        raw_lines = [line.rstrip() for line in
                     sections(body).get("Required changes", "").splitlines() if line.strip()]
        count = len(group_numbered_items(raw_lines)) or len(raw_lines)
    except (OSError, ValueError):
        count = 0
    dispatcher = "coordinator" if mode_of(d) == MODE_QUICK else "orchestrator"
    noun = "change" if count == 1 else "changes"
    if owner == "orch" or task_executor(d, owner) == "orchestrator":
        return (f"recorded {count} required {noun} in {dec.name}; the {dispatcher} "
                f"takes up the correction in {nxt.name} itself.")
    return (f"recorded {count} required {noun} in {dec.name}; the {dispatcher} "
            f"receives a `correction-ready` wake and re-dispatches the implementor "
            f"into {nxt.name}.")


def announce_transition(a: argparse.Namespace, d: Path, rnd: int, verdict: str, dec: Path) -> None:
    try:
        dsecs = sections(parse(dec.read_text())[1])
    except (OSError, ValueError):
        dsecs = {}
    record_outcome(d, a.owner, rnd, "decision", verdict,
                   dsecs.get("Required changes", "") if verdict == "changes-requested"
                   else dsecs.get("Reason", ""), dec.name)
    if verdict in ("approved", "waived"):
        print(f"{a.owner} round {rnd} {verdict} -> {dec.name}")
        if a.owner == "orch":
            # The run is settled: keep every role's conversation and token usage
            # before harness retention can delete them. Never blocks the verdict.
            try:
                kept = [r for r in collect_run_usage(d, archive=True) if r["archive"]]
            except (OSError, ValueError, subprocess.SubprocessError):
                kept = []
            if kept:
                print(f"archived {len(kept)} harness session(s) with token usage; "
                      f"`docket usage {d.name}` shows them")
    else:
        nxt = d / f"{a.owner}-report-{rnd + 1:02d}.mdx"
        print(f"{a.owner} round {rnd} needs changes -> {dec.name}")
        print(f"opened next round -> {nxt.name}")
        print(correction_handoff_line(d, a.owner, dec, nxt))


def reverify_changed_evidence(a: argparse.Namespace, d: Path, rep: Path) -> dict[str, object]:
    """Re-run the whole report gate against a body that changed after review began.

    Changed evidence is not silently reusable. Re-running only the registered command
    would let a report edited into a structurally invalid, unaccepted, out-of-scope, or
    unreadable state through, because a passing test says nothing about the report. So
    the structural, acceptance, scope, and diff checks of `docket submit` run first, in
    the same order, and only a body that clears all of them is verified again.

    A blocked report clears the blocked list and stops there, exactly as submission
    does. Blocking is the honest way to stop when verification cannot pass, so
    demanding a passing command before its waiver may be reviewed would make an
    edited blocker undecidable and blocking harder to use than submitting.
    """
    meta, body = parse(rep.read_text())
    blocked = meta.get("status", "") == "blocked"
    verify, task_timeout = task_verify(d, a.owner)
    problems = gate_problems(d, a.run, a.owner, rep, meta, body, blocked)
    if problems:
        detail = "\n      ".join(f"- {problem}" for problem in problems)
        die(
            f"{rep.name} changed after review began and no longer passes the report gate, so "
            f"the decision cannot apply:\n      {detail}\n"
            "    Have the implementor correct the report and resubmit it."
        )
    print("re-review passed the report gate")
    if blocked:
        print(
            f"{rep.name} is blocked, so re-review skips completion verification, exactly as "
            "submission does; decide the waiver on the changed body"
        )
        return {"status": "blocked", "command": verify, "reverified": "blocked"}

    if not verify:
        print(f"{a.owner} has no registered verify command; recording the re-review without one")
        return {"status": "none", "command": "", "reverified": "unavailable"}
    timeout = a.verify_timeout or task_timeout
    print(f"re-verifying changed evidence: {verify}")
    task_requested = ""
    task_file = d / f"{a.owner}-task.mdx"
    if task_file.is_file():
        task_requested = parse(task_file.read_text())[0].get("requested_model", "")
    observed = parse(rep.read_text())[0].get("actual_model", "")
    record = run_verification(verify, timeout, requested_model=task_requested,
                              observed_model=observed,
                              declared_env=task_env(d, a.owner),
                              slot=verify_slot_path(d, a.owner))
    if record["status"] == "timeout":
        die(f"re-verification timed out after {timeout}s; {rep.name} is not reviewable as it stands")
    if record["status"] == "failed":
        tail = list(record["output_tail"]) or ["(no output)"]
        die(
            f"re-verification failed (exit {record['returncode']}); the changed report cannot be "
            "decided:\n      " + "\n      ".join(tail)
        )
    print("re-verification passed")
    record["reverified"] = "yes"
    return record


def cmd_decide(a: argparse.Namespace) -> None:
    """Record a verdict. The submitted report body is evidence and is never rewritten."""
    d = need_run(a.run)
    slot = verify_slot_path(d, a.owner)
    if a.re_review and verify_slot_busy(slot):
        verify, _ = task_verify(d, a.owner)
        die(
            f"{verify_slot_refusal(slot, verify or 'the registered verify command')}\n"
            "    no decision was applied; wait for the running re-verification"
        )
    if a.reopen:
        with owner_lock(d, a.owner):
            reopen_decide(a, d)
        return
    with owner_lock(d, a.owner):
        decide_locked(a, d)


def reopen_decide(a: argparse.Namespace, d: Path) -> None:
    """Reopen a waived task.

    An in-progress reopen journal is detected before selecting the latest
    report, so a retry after the next-round draft exists finishes the same
    transition instead of refusing that the new draft is not waived.
    """
    if a.owner == "orch":
        die("the orchestrator report cannot be reopened; use --changes instead")
    require_five_role(a, d, "reopen")
    pending = read_transition(d, a.owner)
    if pending.get("state") == "in-progress" and pending.get("verdict") == "reopen-waived":
        recorded = str(pending.get("reopen_reason", ""))
        supplied = a.reason.strip()
        if supplied and recorded and supplied != recorded:
            die(
                f"unfinished transition {pending.get('transition')} already recorded the reason "
                f"{recorded!r}; retrying with different text would rewrite the decision it is meant "
                "to finish. Repeat --reopen without --reason, or restate the recorded reason exactly"
            )
        if not recorded:
            if not supplied:
                die("--reopen requires --reason TEXT")
            recorded = supplied
        reopen_waived(d, a, int(pending.get("round", 0)), recorded)
        return
    if pending.get("state") == "in-progress":
        die(
            f"{a.owner} has an unfinished {pending.get('verdict')} transition "
            f"{pending.get('transition')} for round {pending.get('round')}; repeat that verdict "
            "to finish it before reopening"
        )
    if pending.get("state") == "complete" and pending.get("verdict") == "reopen-waived":
        nxt_name = str(pending.get("next_report", ""))
        rnd = int(pending.get("round", 0) or 0)
        recorded = str(pending.get("reopen_reason", ""))
        supplied = a.reason.strip()
        if supplied and recorded and supplied != recorded:
            die(
                f"completed transition {pending.get('transition')} already recorded the reason "
                f"{recorded!r}; retrying with different text would rewrite it. Repeat --reopen "
                "without --reason, or restate the recorded reason exactly"
            )
        if nxt_name and (d / nxt_name).is_file():
            latest_paths = reports(d, a.owner)
            if latest_paths and latest(latest_paths).name == nxt_name:
                print(f"{a.owner} round {rnd} waiver reopened -> {nxt_name}")
                print("Nothing was rewritten: the recorded reopen, the prior report body, and the")
                print("round numbering are unchanged.")
                return
            if latest_paths:
                latest_rnd = int(parse(latest(latest_paths).read_text())[0].get("round", "0") or 0)
                if latest_rnd == rnd + 1:
                    print(f"{a.owner} round {rnd} waiver reopened -> {nxt_name}")
                    print("Nothing was rewritten: the recorded reopen, the prior report body, and the")
                    print("round numbering are unchanged.")
                    return
    rep = latest(reports(d, a.owner))
    if not rep:
        die(f"no report for {a.owner} in {a.run}")
    report_meta, _ = parse(rep.read_text())
    rnd = int(report_meta.get("round", "1"))
    status = report_meta.get("status", "")
    if status != "waived":
        die(f"{rep.name} is {status}, not waived; only a waived task can be reopened via --reopen")
    reason = a.reason.strip()
    if not reason:
        die("--reopen requires --reason TEXT")
    reopen_waived(d, a, rnd, reason)


def abandon_unpublished_transition(
    a: argparse.Namespace, d: Path, journal: dict, verdict: str, reasons: list[str]
) -> None:
    """Retire an interrupted transition that published nothing and can no longer finish.

    Such a transition exists only in its journal. When the evidence it bound has moved,
    finishing it would record a verdict on evidence nobody reviewed, and refusing it
    forever would strand the round, since a different verdict is refused while it is
    unfinished. It is abandoned instead: the journal keeps the reviewer's text with the
    reason, a copy survives the next transition, and the reviewer decides again.
    """
    txn = str(journal.get("transition", "?"))
    journal = {**journal, "state": "abandoned", "abandoned_at": stamp(),
               "abandoned_because": list(reasons)}
    publish_json(transition_path(d, a.owner), journal)
    keep = d / ".transitions" / "abandoned" / f"{a.owner}-{txn.split(':')[-1]}.json"
    keep.parent.mkdir(parents=True, exist_ok=True)
    publish_json(keep, journal)
    print(
        f"docket: the interrupted {verdict} transition {txn} was abandoned: it published "
        "nothing, and the evidence it bound no longer supports it:\n      "
        + "\n      ".join(f"- {item}" for item in reasons)
        + f"\n    Its text is kept in {keep.relative_to(d)}. Decide {a.owner} again against the "
        "current evidence.",
        file=sys.stderr,
    )


def guard_accepting_verdict(
    a: argparse.Namespace, d: Path, rnd: int, verdict: str, frozen: dict[str, object] | None
) -> None:
    """Refuse an approval or waiver whose frozen evidence no longer supports it.

    An accepting verdict settles the work. For a task owner, a consumed input that
    moved after the consumer submitted takes its review readiness with it. For the
    orchestrator, pinned constituent evidence that moved does the same.
    """
    if a.owner == "orch":
        if verdict == "approved" and frozen_verification_status(frozen) == "skipped":
            die(skipped_approval_problem(a.owner, rnd, a.run))
        stale_constituents = stale_aggregate(d)
        stale_constituents.extend(amendment_blocks(d, "orch"))
        if stale_constituents:
            die(
                f"orch cannot be {verdict} against aggregate evidence that moved:\n      "
                + "\n      ".join(f"- {item}" for item in stale_constituents)
                + f"\n    Open a fresh round with `docket decide {a.run} orch --changes` and "
                "resubmit so the aggregate is verified against current constituent evidence."
            )
    else:
        if verdict == "approved" and frozen_verification_status(frozen) == "skipped":
            die(skipped_approval_problem(a.owner, rnd, a.run))
        stale_inputs = [
            *dependency_problems(d, a.owner),
            *refreshed_inputs(d, a.owner, frozen or {}),
            *amendment_blocks(d, a.owner),
        ]
        live_contract = ""
        task_file = d / f"{a.owner}-task.mdx"
        if task_file.is_file():
            live_contract = digest_of(task_file.read_bytes())
        frozen_contract = str(((frozen or {}).get("contract", {}) or {}).get("revision", ""))
        if live_contract and frozen_contract and live_contract != frozen_contract:
            stale_inputs.append(
                f"the task contract changed after round {rnd} was frozen: "
                "the captured verification described different acceptance"
            )
        if is_five_role(d) and verdict == "approved":
            exempt = [item.strip() for item in
                      plan_flag(d, "verifier_exempt", "").split(",") if item.strip()]
            if a.owner not in exempt:
                vpath, vmeta, _ = latest_verification(d, a.owner, rnd)
                frozen_digest = str((frozen or {}).get("digest", ""))
                if vpath is None or vmeta.get("result") != "pass":
                    die(f"{a.owner} round {rnd} has no passing verification: a verifier pass "
                        "means ready for review, never approved. Record one with "
                        f"`docket verify {a.run} {a.owner} --result pass --as verifier` first")
                if str(vmeta.get("bundle_digest", "")) != frozen_digest:
                    die(f"{vpath.name} binds bundle {vmeta.get('bundle_digest')} but round {rnd} "
                        f"froze {frozen_digest}: re-verify the current evidence")
                if str(vmeta.get("contract_revision", "")) != live_contract:
                    die(f"{vpath.name} verified contract {vmeta.get('contract_revision')} but the "
                        "task now reads differently: re-verify after the amendment")
        if stale_inputs:
            die(
                f"{a.owner} cannot be {verdict} against consumed evidence that moved:\n      "
                + "\n      ".join(f"- {item}" for item in stale_inputs)
                + f"\n    Open a fresh round with `docket decide {a.run} {a.owner} --changes`, "
                f"re-record the input with `docket depend {a.run} {a.owner} --on <task>`, and "
                f"resubmit so {a.owner} is verified against the evidence it now consumes."
            )
        for entry in provisional_dependencies(d, a.owner):
            print(
                f"note: {a.owner} consumed {entry.get('on')} provisionally at "
                f"{entry.get('bundle')}; that is readiness, not an approval of {entry.get('on')}"
            )


def decide_locked(a: argparse.Namespace, d: Path) -> None:
    """The decision transition itself, always under the owner lock."""
    verdict = "waived" if a.waive else ("approved" if a.approve else "changes-requested")
    require_five_role(a, d, f"decide:{'waive' if a.waive else ('approve' if a.approve else 'changes')}")
    supplied_changes = list(getattr(a, "change", None) or [])
    if supplied_changes and verdict != "changes-requested":
        die("--change needs --changes; only a changes request records required changes")
    pending = read_transition(d, a.owner)
    if pending.get("state") != "in-progress":
        pending = {}
    if pending and pending.get("verdict") != verdict:
        if pending.get("verdict") == "reopen-waived":
            die(
                f"{a.owner} has an unfinished reopen-waived transition "
                f"{pending.get('transition')} for round {pending.get('round')}; repeat "
                f"`docket decide {a.run} {a.owner} --reopen` to finish it before recording a different verdict"
            )
        die(
            f"{a.owner} has an unfinished {pending.get('verdict')} transition "
            f"{pending.get('transition')} for round {pending.get('round')}; repeat that verdict "
            "to finish it before recording a different one"
        )

    rep = latest(reports(d, a.owner))
    if not rep:
        die(f"no report for {a.owner} in {a.run}")
    report_meta, report_body = parse(rep.read_text())
    status = report_meta.get("status", "")

    # The report has already moved: either the transition finished, or it was
    # interrupted after the report write and the remaining steps are still owed.
    if status not in ("submitted", "blocked"):
        settled = applied_decision(d, a.owner)
        if not settled:
            die(f"{rep.name} has status: {status} - only submitted or blocked reports are reviewable")
        dec, decision_meta = settled
        recorded = decision_meta.get("verdict", "?")
        settled_round = int(decision_meta.get("round", "0") or 0)
        if recorded != verdict:
            die(
                f"{a.owner} round {settled_round} is already {recorded} in {dec.name}; "
                f"{rep.name} is {status} and not reviewable"
            )
        rep = d / f"{a.owner}-report-{settled_round:02d}.mdx"
        report_meta, report_body = parse(rep.read_text())
        decision_body = parse(dec.read_text())[1]
        finished = commit_transition(
            a, d, settled_round, verdict, rep, report_meta, report_body,
            dec, decision_meta, decision_body,
        )
        if verdict in ("approved", "waived") and is_five_role(d):
            clear_corrections(d, a.owner)
        if not finished:
            print(f"{a.owner} round {settled_round} is already {recorded} -> {dec.name}")
            print("Nothing was rewritten: the recorded decision, the report body, and the")
            print("round numbering are unchanged.")
            return
        print(
            f"resumed interrupted {verdict} transition {decision_meta['transition']} for "
            f"{a.owner} round {settled_round}: applied {', '.join(finished)}"
        )
        announce_transition(a, d, settled_round, verdict, dec)
        return

    blocked = status == "blocked"
    if blocked and a.approve:
        die("a blocked report cannot become approved; use --changes or --waive --reason TEXT")
    rnd = int(report_meta.get("round", "1"))
    if not blocked and a.waive:
        # A skipped verification may still be recorded but never supports approval.
        # Waiving accepts without claiming a pass, so it stays available for that
        # round. Peek without dying; damaged or missing evidence is handled at binding.
        skipped_round = False
        try:
            if a.owner == "orch":
                peeked, _ = current_aggregate(d, rnd)
            else:
                peeked, _ = current_bundle(d, a.owner, rnd)
            skipped_round = frozen_verification_status(peeked) == "skipped"
        except (OSError, ValueError):
            skipped_round = False
        if not skipped_round:
            die("only a blocked report can be waived")
    dec = d / f"{a.owner}-decision-{rnd:02d}.mdx"
    fresh = not dec.is_file()
    if fresh:
        decision_meta, decision_body = parse(template("decision").format(
            run=a.run, owner=a.owner, round=rnd, verdict=verdict,
            reviewer=a.reviewer or DEFAULT_REVIEWER,
            **artifact_policy_fields(d),
        ))
    else:
        decision_meta, decision_body = parse(dec.read_text())

    # A decision already applied against this round means an interrupted transition:
    # finish it from the recorded artifact instead of writing a second verdict.
    resuming = decision_meta.get("applied") == "yes"
    if resuming and decision_meta.get("verdict") != verdict:
        die(
            f"{dec.name} already recorded a {decision_meta.get('verdict')} decision for round "
            f"{rnd}; nothing was rewritten"
        )

    # An interrupted transition journalled its complete decision payload before writing
    # any artifact. Finish that decision rather than rebuilding one from arguments the
    # retry cannot be expected to repeat.
    recovered = None if resuming else pending_payload(pending, rnd, verdict)
    if recovered:
        decision_meta, decision_body = adopt_pending_payload(a, *recovered)
    elif not resuming and verdict == "waived" and not a.reason.strip():
        die("--waive requires --reason TEXT")

    digest = evidence_digest(report_body)
    frozen: dict[str, object] | None = None
    stale_bundle = ""
    binding = not resuming and not recovered
    # A recovered transition has published nothing, so the evidence it bound is
    # checked exactly as a fresh verdict's is; only its payload is not rebuilt.
    if not resuming:
        if a.owner == "orch":
            frozen, stale_bundle = require_aggregate(d, rnd, rep, digest)
        else:
            frozen, stale_bundle = require_bundle(d, a.owner, rnd, rep, digest)
    if binding and frozen is not None:
        decision_meta["bundle_digest"] = str(frozen.get("digest", ""))
        decision_meta["bundle_round"] = str(frozen.get("round", rnd))
        if a.owner == "orch":
            decision_meta["bundle_kind"] = "aggregate"
    if recovered:
        moved = []
        journalled = decision_meta.get("evidence_digest", "").strip()
        if stale_bundle or (journalled and journalled != digest):
            moved.append(f"{rep.name} changed after the transition was journalled: it reviewed "
                         f"{journalled or 'the frozen body'} and the body is now {digest}")
        bound = decision_meta.get("bundle_digest", "").strip()
        if frozen is not None and bound and bound != str(frozen.get("digest", "")):
            moved.append(f"round {rnd} now freezes {frozen.get('digest')}, not the bundle "
                         f"{bound} the transition bound")
        if moved:
            abandon_unpublished_transition(a, d, pending, verdict, moved)
            raise SystemExit(1)
    if resuming:
        recorded_body = decision_meta.get("evidence_digest", "").strip()
        if recorded_body and recorded_body != digest:
            die(
                f"{rep.name} changed after {dec.name} recorded the {verdict} verdict: it reviewed "
                f"{recorded_body} and the body is now {digest}. A recorded verdict cannot be "
                "applied to evidence it did not review, so restore the reviewed body (its frozen "
                f"copy is listed by `docket bundle {a.run} {a.owner}`) and repeat the verdict to "
                "finish it. Anything new belongs in the next round"
            )

    if a.changes and fresh and not supplied_changes and not recovered:
        decision_meta["applied"] = "no"
        decision_meta["evidence_digest"] = digest
        publish(dec, render(decision_meta, decision_body))
        print(f"opened decision draft -> {dec.name}")
        print("Fill its required changes, then run the same command again to apply the transition.")
        return

    # A verdict may only apply to the evidence it was formed against. A report body
    # that changed since the decision recorded its identity is new evidence, and
    # new evidence needs fresh verification and a fresh review, not a reused verdict.
    reviewed = decision_meta.get("evidence_digest", "").strip()
    frozen_body = str((frozen or {}).get("report", {}).get("body_digest", ""))
    changed_evidence = bool(stale_bundle) or bool(reviewed and reviewed != digest and not resuming)
    if changed_evidence and not resuming:
        if not a.re_review:
            flag = "--changes" if a.changes else ("--waive" if a.waive else "--approve")
            source = f"{dec.name} recorded evidence {reviewed}" if reviewed else (
                f"the frozen bundle {stale_bundle} holds body {frozen_body}"
            )
            die(
                f"{rep.name} changed after review began: {source}, and the report body is now "
                f"{digest}. A decision cannot apply to evidence it did not review. Re-verify and "
                f"re-review the current report with "
                f"`docket decide {a.run} {a.owner} {flag} --re-review`, or restore the reviewed body"
            )
        record = reverify_changed_evidence(a, d, rep)
        decision_meta["reverified"] = str(record.pop("reverified", ""))
        if binding:
            # The re-reviewed body is new evidence, so it is frozen as its own bundle.
            # The superseded one keeps its own address and its own bytes.
            if a.owner == "orch":
                refrozen, problem = freeze_aggregate_bundle(
                    d, a.run, rep, report_body, record, "re-review",
                    require_patch=report_meta.get("status") != "blocked" and evidence_mode(d) == "git",
                )
            else:
                refrozen, problem = freeze_task_bundle(
                    d, a.run, a.owner, rep, report_body, record, "re-review",
                    require_patch=report_meta.get("status") != "blocked" and evidence_mode(d) == "git",
                )
            if problem:
                die(f"the re-reviewed evidence could not be frozen, so it cannot be decided: {problem}")
            decision_meta["superseded_bundle"] = stale_bundle or str(frozen.get("digest", ""))
            decision_meta["bundle_digest"] = str(refrozen["digest"])
            report_meta["bundle_digest"] = str(refrozen["digest"])
            frozen = refrozen
            print(f"froze the re-reviewed evidence as {refrozen['digest']}")
        decision_meta["superseded_evidence"] = reviewed or frozen_body
        decision_meta["evidence_digest"] = digest
    elif a.re_review:
        print(f"{rep.name} still matches the reviewed evidence; nothing needed re-review")

    if not resuming and verdict in ("approved", "waived"):
        try:
            guard_accepting_verdict(a, d, rnd, verdict, frozen)
        except SystemExit:
            if recovered:
                abandon_unpublished_transition(a, d, pending, verdict, [
                    "the accepting-verdict check above refused it against the current evidence"])
            raise

    if not resuming and not recovered:
        if a.changes:
            if supplied_changes:
                decision_body = set_section(
                    decision_body, "Required changes",
                    format_supplied_changes(supplied_changes))
            required = sections(decision_body).get("Required changes", "")
            if is_empty(required) or PLACEHOLDER.search(required):
                die(f"{dec.name} needs specific required changes before the transition can be applied")
            if is_five_role(d) and a.owner != "orch":
                note_correction(d, a.owner, "reviewer_returns", token=dec.name)
                guard_correction_budget(d, a.owner, f"reviewer changes on round {rnd}")
        else:
            for name in ("Required changes", "Answers to decisions needed"):
                if is_empty(sections(decision_body).get(name, "")):
                    decision_body = set_section(decision_body, name, "none")
        reason = a.reason.strip()
        if not reason:
            existing = sections(decision_body).get("Reason", "")
            reason = "" if is_empty(existing) else existing.strip()
        decision_body = set_section(decision_body, "Verdict", verdict)
        decision_body = set_section(decision_body, "Reason", reason or "none")
        decision_meta.update({
            "run": a.run, "task": a.owner, "owner": a.owner, "round": str(rnd),
            "verdict": verdict,
            "reviewer": a.reviewer or decision_meta.get("reviewer") or DEFAULT_REVIEWER,
            "evidence_digest": digest,
            "applied": "yes",
        })
        if is_five_role(d) and a.owner != "orch" and verdict == "approved":
            vpath, _, _ = latest_verification(d, a.owner, rnd)
            task_file = d / f"{a.owner}-task.mdx"
            decision_meta["verification"] = vpath.name if vpath else ""
            decision_meta["task_revision"] = digest_of(task_file.read_bytes()) \
                if task_file.is_file() else ""

    commit_transition(
        a, d, rnd, verdict, rep, report_meta, report_body, dec, decision_meta, decision_body,
    )
    if verdict in ("approved", "waived") and is_five_role(d):
        clear_corrections(d, a.owner)
    if resuming or pending:
        print(
            f"resumed interrupted {verdict} transition {decision_meta['transition']} for "
            f"{a.owner} round {rnd}"
        )
    announce_transition(a, d, rnd, verdict, dec)
