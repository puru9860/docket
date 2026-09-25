"""The verifier command and verifier-opened correction rounds."""

from __future__ import annotations

import argparse
from pathlib import Path

from .common import die
from .frontmatter import parse, render, sections
from .paths import latest, next_numbered, reports
from .publication import fault, publish, publish_exclusive
from .policy import (
    artifact_policy_fields, authority_for, is_five_role, need_run, require_five_role,
    verifier_correction_allowed, workflow_of,
)
from .baselines import evidence_digest
from .bundles import digest_of
from .locks import owner_lock
from .state import (
    DECIDE_FLAGS, decide_as, group_numbered_items, latest_verification, read_transition,
    verification_paths,
)
from .freeze import current_bundle
from .models import record_outcome
from .five_role import guard_correction_budget, note_correction
from .transitions import commit_transition, pending_payload, transition_id


def cmd_verify(a: argparse.Namespace) -> None:
    """Record a numbered verifier finding against one submitted round.

    A pass means ready for review, never approved: the report stays submitted
    and only a reviewer decision can settle it.
    """
    d = need_run(a.run)
    if not is_five_role(d):
        die(f"{a.run} is a {workflow_of(d)} run; verifier findings need "
            "`workflow: five-role-v1` (migrate explicitly with `docket migrate`)")
    require_five_role(a, d, "verify-record")
    if a.result not in ("pass", "fail", "uncertain"):
        die("--result must be pass, fail, or uncertain")
    if a.owner == "orch":
        die("the aggregate is reviewed directly by the reviewer; "
            "verify its constituent task rounds instead")
    detail = (a.detail or "").strip() or "none"
    with owner_lock(d, a.owner):
        rep = latest(reports(d, a.owner))
        if not rep:
            die(f"no report for {a.owner} in {a.run}")
        report_meta, _ = parse(rep.read_text())
        rnd = int(report_meta.get("round", "1"))
        status = report_meta.get("status")
        # An interrupted verifier correction is finished, never re-recorded: a
        # second verification and a second budget charge for one failure would
        # be the exact duplication the recorded transition exists to prevent.
        # Interrupted after the report step, the round already reads
        # changes-requested with no next round, and only this path finishes it.
        interrupted = None
        if status in ("submitted", "changes-requested"):
            interrupted = interrupted_verifier_correction(d, a.owner, rnd)
        if status != "submitted" and interrupted is None:
            die(f"{rep.name} is {status}, not submitted; "
                "verification records against the submitted round only")
        if interrupted is not None:
            finish_verifier_correction(a, d, rnd, interrupted)
            return

        task_file = d / f"{a.owner}-task.mdx"
        contract_rev = digest_of(task_file.read_bytes()) if task_file.is_file() else ""
        frozen, _ = current_bundle(d, a.owner, rnd)
        bundle_digest = str((frozen or {}).get("digest", ""))
        if a.open_correction and a.result != "fail":
            die("--open-correction applies only to --result fail. Nothing was recorded")
        # A correction the policy forbids still leaves the failure on record: the
        # finding is evidence, and the reviewer decides the required changes.
        refused_policy = a.open_correction and not verifier_correction_allowed(d)
        opening = a.result == "fail" and a.open_correction and not refused_policy
        for _ in range(100):
            prior_round = verification_paths(d, a.owner, rnd)
            prior_attempts = []
            for prior in prior_round:
                try:
                    prior_attempts.append(int(parse(prior.read_text())[0].get("attempt", "0") or 0))
                except (OSError, ValueError):
                    continue
            attempt = max(prior_attempts, default=0) + 1
            seq = next_numbered(verification_paths(d, a.owner))
            path = d / f"{a.owner}-verification-{seq:02d}.mdx"
            body = (f"# Verification: {a.owner} round {rnd} attempt {attempt}\n\n"
                    f"## Result\n\n{a.result}\n\n## Findings\n\n{detail}\n")
            if opening and not verifier_correction_changes(body, path.name):
                die(f"cannot open a verifier correction for {a.owner} round {rnd}: the findings "
                    "carry no numbered required change and no finding text to derive one from; "
                    "record specific findings with --detail TEXT so the opened round carries "
                    "numbered required changes. Nothing was recorded")
            # The intent to open a correction is recorded in the same write as the
            # finding, so an interruption anywhere after it is recognisable and
            # finishable rather than silently stranded.
            meta = {"run": a.run, "task": a.owner, "owner": a.owner, "round": str(rnd),
                    "attempt": str(attempt), "verifier": a.verifier or "verifier",
                    "result": a.result, "contract_revision": contract_rev,
                    "bundle_digest": bundle_digest,
                    "opened_correction": "requested" if opening else "no",
                    **artifact_policy_fields(d)}
            try:
                publish_exclusive(path, render(meta, body))
            except FileExistsError:
                continue
            break
        else:
            die(f"could not record a verification for {a.owner} round {rnd}; retry the command")
        record_outcome(d, a.owner, rnd, "verification", a.result,
                       sections(body).get("Findings", ""), path.name)
        if a.result == "fail":
            if refused_policy:
                die("verifier-triggered corrections need `verifier_correction: allowed` in "
                    f"plan.mdx; the failure is recorded in {path.name} and the round stays "
                    "submitted for the reviewer to decide")
            if opening:
                # A fail that leaves the round for the reviewer returns nothing to
                # the implementor yet; the reviewer's changes are the one return.
                note_correction(d, a.owner, "verifier_returns", token=path.name)
                guard_correction_budget(
                    d, a.owner, f"verification attempt {attempt}",
                    on_exhausted=lambda: mark_verification_correction(path, "escalated"))
                fault("verify:before-correction")
                open_verifier_correction(a, d, rnd, path)
                return
    print(f"recorded {path.name}: {a.result} (report stays submitted)")


def mark_verification_correction(vpath: Path, state: str) -> None:
    """Record how a verification's requested correction stands."""
    vmeta, vbody = parse(vpath.read_text())
    if vmeta.get("opened_correction") != state:
        vmeta["opened_correction"] = state
        publish(vpath, render(vmeta, vbody))


def interrupted_verifier_correction(d: Path, owner: str, rnd: int) -> Path | None:
    """The verification whose correction began but never opened the next round.

    Called only while the round is still submitted: a finished verifier
    correction leaves the report changes-requested. Evidence is read from the
    artifacts first and the journal second, exactly as `applied_steps` does,
    so a lost journal still leaves the transition finishable.
    """
    journal = read_transition(d, owner)
    if journal.get("state") == "in-progress" and journal.get("verdict") == "changes-requested" \
            and str(journal.get("round", "")) == str(rnd):
        jmeta = journal.get("decision_meta")
        if isinstance(jmeta, dict) and jmeta.get("triggered_by") and jmeta.get("verification"):
            vpath = d / str(jmeta["verification"])
            if vpath.is_file():
                return vpath
    dec = d / f"{owner}-decision-{rnd:02d}.mdx"
    if dec.is_file():
        try:
            dmeta, _ = parse(dec.read_text())
        except (OSError, ValueError):
            dmeta = {}
        if dmeta.get("applied") == "yes" and dmeta.get("verdict") == "changes-requested" \
                and dmeta.get("triggered_by") and dmeta.get("verification"):
            vpath = d / str(dmeta["verification"])
            if vpath.is_file():
                return vpath
    vpath, vmeta, _ = latest_verification(d, owner, rnd)
    # A correction the budget refused is still the one to finish once more rounds
    # are granted, so the retry reuses its findings rather than recording another.
    if vpath is not None and vmeta.get("result") == "fail" \
            and vmeta.get("opened_correction") in ("requested", "yes", "escalated"):
        return vpath
    return None


def finish_verifier_correction(a: argparse.Namespace, d: Path, rnd: int, vpath: Path) -> None:
    """Finish an interrupted verifier correction exactly as it was recorded."""
    cmd = (f"docket verify {a.run} {a.owner} --result fail --open-correction "
           f"--as {a.as_role or 'verifier'}")
    if a.result != "fail" or not a.open_correction:
        die(f"{a.owner} round {rnd} has an unfinished verifier-triggered correction opened "
            f"from {vpath.name}; finish it with `{cmd}` (the recorded findings are reused), "
            "or the reviewer finishes it with "
            f"`docket decide {a.run} {a.owner} --changes{decide_as(d)}`. Nothing was recorded")
    _, vbody = parse(vpath.read_text())
    recorded = sections(vbody).get("Findings", "").strip()
    supplied = (a.detail or "").strip()
    if supplied and supplied != recorded:
        die(f"{vpath.name} already recorded these findings:\n{recorded}\n"
            "A retry finishes that correction and never replaces it; repeat the command "
            "without --detail, or state anything new in the next round. Nothing was recorded")
    # Attempting it again makes it a return again, charged once under its own token.
    mark_verification_correction(vpath, "requested")
    note_correction(d, a.owner, "verifier_returns", token=vpath.name)
    guard_correction_budget(
        d, a.owner, f"verification {vpath.name}",
        on_exhausted=lambda: mark_verification_correction(vpath, "escalated"))
    print(f"resuming the interrupted verifier correction from {vpath.name}")
    open_verifier_correction(a, d, rnd, vpath)


def verifier_correction_changes(vbody: str, vname: str) -> list[str] | None:
    """Numbered required changes derived from a verifier's recorded findings.

    A correction that cannot be worked is not a correction: the implementor
    prompt refuses a decision whose Required changes section carries no
    numbered lines. Findings that already name numbered defects are carried
    verbatim; prose findings become one numbered item per line pointing at
    the verification artifact. Empty findings or a bare "none" cannot yield
    a workable change and return None so the caller refuses instead of
    writing an unrenderable decision.
    """
    findings = sections(vbody).get("Findings", "").strip()
    if not findings or findings.lower() == "none":
        return None
    lines = [line.rstrip() for line in findings.splitlines() if line.strip()]
    if not lines:
        return None
    numbered = group_numbered_items(lines)
    if numbered:
        return numbered
    return [f"{i}. Address verifier finding in {vname}: {line.strip()}"
            for i, line in enumerate(lines, 1)]


def open_verifier_correction(a: argparse.Namespace, d: Path, rnd: int,
                             vpath: Path) -> None:
    """Open a correction round from a verifier failure under configured policy.

    Retry-safe in every window: a journalled payload or an applied decision
    that names this verification is finished exactly as recorded, and only
    when neither exists is a decision built from the findings. Any other
    decision or unfinished transition for the round is refused, never
    overwritten.
    """
    rep = d / f"{a.owner}-report-{rnd:02d}.mdx"
    report_meta, report_body = parse(rep.read_text())
    dec = d / f"{a.owner}-decision-{rnd:02d}.mdx"
    journal = read_transition(d, a.owner)
    recorded = pending_payload(journal, rnd, "changes-requested")
    decision_meta: dict[str, str] | None = None
    decision_body = ""
    if recorded and recorded[0].get("verification") == vpath.name:
        decision_meta, decision_body = recorded
    elif journal.get("state") == "in-progress":
        die(f"{a.owner} has an unfinished {journal.get('verdict')} transition "
            f"{journal.get('transition')} for round {journal.get('round')}; the reviewer "
            f"finishes it by repeating that verdict with `docket decide {a.run} {a.owner} "
            f"{DECIDE_FLAGS.get(str(journal.get('verdict')), '')}{decide_as(d)}`")
    elif dec.is_file():
        dmeta, dbody = parse(dec.read_text())
        if dmeta.get("applied") == "yes" and dmeta.get("verdict") == "changes-requested" \
                and dmeta.get("verification") == vpath.name:
            decision_meta, decision_body = dmeta, dbody
        else:
            die(f"{dec.name} already exists and was not opened from {vpath.name}; finish "
                f"that decision with `docket decide {a.run} {a.owner} --changes{decide_as(d)}`")
    vmeta, vbody = parse(vpath.read_text())
    if decision_meta is None:
        changes = verifier_correction_changes(vbody, vpath.name)
        if not changes:
            die(f"cannot open a verifier correction from {vpath.name}: its Findings "
                f"section carries no numbered required change and no finding text to "
                f"derive one from; record specific findings with --detail TEXT so the "
                f"opened round carries numbered required changes")
        digest = evidence_digest(report_body)
        txn = transition_id(a.run, a.owner, rnd, "changes-requested", digest)
        acting = authority_for(d, "verifier-correction")
        acting_role = acting[0] if acting else "verifier"
        decision_meta = {"run": a.run, "task": a.owner, "owner": a.owner,
                         "round": str(rnd), "verdict": "changes-requested",
                         "reviewer": acting_role, "triggered_by": acting_role,
                         "verification": vpath.name, "evidence_digest": digest,
                         "applied": "yes", "transition": txn,
                         **artifact_policy_fields(d)}
        decision_body = (f"# Decision: {a.owner} round {rnd}\n\n## Verdict\n\n"
                         "changes-requested\n\n"
                         f"## Reason\n\nverifier finding in {vpath.name}\n\n"
                         "## Required changes\n\n" + "\n".join(changes) + "\n")
    if vmeta.get("opened_correction") != "yes":
        vmeta["opened_correction"] = "yes"
        publish(vpath, render(vmeta, vbody))
    commit_transition(a, d, rnd, "changes-requested", rep, report_meta, report_body,
                      dec, decision_meta, decision_body)
    record_outcome(d, a.owner, rnd, "decision", "changes-requested",
                   sections(decision_body).get("Required changes", ""), dec.name)
    print(f"{a.owner} round {rnd} needs changes (verifier-triggered) -> {dec.name}")
