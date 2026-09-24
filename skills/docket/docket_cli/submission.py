"""Submitting a round under the owner lock."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import COVERAGE_AVAILABLE, die
from .frontmatter import parse, render, sections
from .paths import latest, reports, scope_path
from .publication import perturb, publish
from .policy import is_five_role, need_run, require_five_role, submit_op_for, topology
from .baselines import baseline_path, evidence_mode, task_verify
from .bundles import digest_of, source_identity
from .locks import owner_lock
from .dependencies import dependency_problems, deps_path, deps_record_bytes
from .freeze import freeze_task_bundle
from .aggregates import freeze_aggregate_bundle
from .verification import (
    run_verification, task_env, verify_slot_busy, verify_slot_path, verify_slot_refusal,
)
from .feedback import machine_feedback
from .models import record_outcome
from .gate import gate_problems


def submission_identity(d: Path, owner: str, rep: Path) -> dict[str, str]:
    """Revision of every document a submission's verification is taken to describe."""
    def revision(path: Path) -> str:
        return digest_of(path.read_bytes()) if path.is_file() else ""

    return {
        "report": revision(rep),
        "task contract": revision(d / f"{owner}-task.mdx"),
        "accepted scope": revision(scope_path(d, owner)),
        "record of consumed inputs": revision(deps_path(d, owner)),
    }


def drifted_inputs(d: Path, owner: str, rep: Path, verified: dict[str, str]) -> list[str]:
    """Whatever moved between the verification run and the freeze it would describe.

    A captured green result is evidence about one exact contract, report, scope, and
    set of consumed inputs. If any of them changed while the command ran, freezing the
    round would attach that result to documents it never saw. The submission is refused
    instead, and the concurrent edit is left exactly as its writer left it.
    """
    problems = [
        f"the {label} changed while the verification ran, so the captured result does not "
        "describe what would be frozen. Nothing was frozen and the concurrent edit was left "
        "in place: re-read the current documents and run `docket submit` again"
        for label, digest in submission_identity(d, owner, rep).items()
        if digest != verified.get(label, "")
    ]
    if owner != "orch":
        problems.extend(
            f"a consumed input moved while the verification ran: {item}"
            for item in dependency_problems(d, owner)
        )
    return problems


def cmd_submit(a: argparse.Namespace) -> None:
    """The gate. A report becomes visible to the reviewer only if it passes here."""
    d = need_run(a.run)
    slot = verify_slot_path(d, a.owner)
    if verify_slot_busy(slot):
        verify, _ = task_verify(d, a.owner)
        die(
            f"{verify_slot_refusal(slot, verify or 'the registered verify command')}\n"
            "    reports stay draft until submit; wait for the running verification"
        )
    # One submission per owner at a time. Verification, the freeze, the ledger append,
    # and the report write are one transition: two submitters interleaving them could
    # leave a published report pointing at a bundle the ledger no longer retains.
    with owner_lock(d, a.owner):
        submit_locked(a, d)


def submit_locked(a: argparse.Namespace, d: Path) -> None:
    """The submission itself, always under the owner lock."""
    require_five_role(a, d, submit_op_for(d, a.owner))
    rep = latest(reports(d, a.owner))
    if not rep:
        die(f"no report for {a.owner} in {a.run}")
    meta, body = parse(rep.read_text())

    status = meta.get("status", "")
    if status not in ("draft", "blocked"):
        die(
            f"{rep.name} has status: {status} - reports stay draft until submit; "
            "restore status: draft and run `docket submit`"
        )
    blocked = a.blocked or status == "blocked"
    task = d / f"{a.owner}-task.mdx"
    verified = submission_identity(d, a.owner, rep)
    # The exact bytes the verification is about to describe. The freeze is given these
    # and never re-reads: between the drift recheck and the freeze there is still a
    # window, and a contract or a consumed-input record that slipped through it would
    # be frozen as though the captured result had described it.
    contract_bytes = task.read_bytes() if task.is_file() else None
    dependency_bytes = deps_record_bytes(d, a.owner)
    verify, task_timeout = task_verify(d, a.owner)
    problems = gate_problems(d, a.run, a.owner, rep, meta, body, blocked,
                             verify_skipped=bool(verify and a.skip_verify and not blocked))

    # Run the task's declared verify command, but only once the report itself holds up -
    # no point spending a test run on a report that is already incomplete.
    verify_timeout = a.verify_timeout or task_timeout
    verification: dict[str, object] = {"status": "none", "command": verify}
    verified_source: list[dict[str, str]] | None = None
    if verify and a.skip_verify and not a.skip_verify_reason.strip():
        problems.append("--skip-verify requires --skip-verify-reason TEXT")
    if blocked:
        verification["status"] = "blocked"
    elif verify and a.skip_verify:
        verification["status"] = "skipped"
        verification["skip_reason"] = a.skip_verify_reason.strip()
    if verify and not blocked and not a.skip_verify and not problems:
        # The resulting source revision is pinned on both sides of the run, so a green
        # result can never be frozen against a checkout it did not describe. The
        # aggregate is measured against the run baseline, not a snapshot of its own.
        pinned, unpinned = source_identity(d, "run" if a.owner == "orch" else a.owner, "verify")
        verified_source = pinned or None
        if unpinned and evidence_mode(d) == "git":
            # Without a pre-verify pin the drift check has nothing to compare, so a
            # result would be frozen against a source nobody tied it to.
            problems.append(f"the source could not be pinned before verification: {unpinned}")
    if verify and not blocked and not a.skip_verify and not problems:
        print(f"running verify: {verify}")
        task_requested = parse(task.read_text())[0].get("requested_model", "") if task.is_file() else ""
        verification = run_verification(verify, verify_timeout,
                                        requested_model=task_requested,
                                        observed_model=meta.get("actual_model", ""),
                                        declared_env=task_env(d, a.owner),
                                        slot=verify_slot_path(d, a.owner))
        if verification["status"] == "timeout":
            problems.append(f"verify command timed out after {verify_timeout}s")
        elif verification["status"] == "failed":
            tail = list(verification["output_tail"]) or ["(no output)"]
            baseline_note = ""
            base = baseline_path(d, a.owner)
            if base.is_file():
                recorded = json.loads(base.read_text())
                if (
                    recorded.get("returncode") == verification["returncode"]
                    and recorded.get("fingerprint") == verification["output_fingerprint"]
                ):
                    baseline_note = " (matches the recorded failing baseline; submit --blocked for waiver review)"
            problems.append(
                f"verify command failed (exit {verification['returncode']}){baseline_note}:\n      "
                + "\n      ".join(tail)
            )
        else:
            print("verify passed")

    # Recheck the documents the verification was taken to describe. Freezing binds a
    # captured result to a contract, a report, a scope, and a set of consumed inputs;
    # anything that moved in between makes that binding a claim nobody observed.
    if not problems:
        problems.extend(drifted_inputs(d, a.owner, rep, verified))

    if problems:
        # A refused submit is the implementor's own check before handoff:
        # nothing was returned to it, so it is recorded, never charged.
        record_outcome(d, a.owner, meta.get("round", "?"), "gate", "refused",
                       "\n".join(problems), rep.name)
        machine_feedback(d, "verification", a.owner,
                         "submit refused by the gate: " + problems[0].splitlines()[0],
                         role=a.as_role or "implementor")
        print(f"\nREJECTED: {rep.name} is not ready to submit\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\nFix these and run submit again. Nothing was handed off.", file=sys.stderr)
        raise SystemExit(1)

    # Freeze the round before anything becomes visible to a reviewer. A task-round
    # bundle is what a verdict later binds to, so a round that cannot be frozen is
    # not handed off at all. The aggregate round freezes its own bundle with
    # constituent pins and an aggregate patch from the run baseline.
    frozen: dict[str, object] | None = None
    if a.owner == "orch":
        perturb("submit:before-freeze")
        frozen, problem = freeze_aggregate_bundle(
            d, a.run, rep, body, verification,
            "blocked-submission" if blocked else "submission",
            verified_source=verified_source,
            require_patch=not blocked and evidence_mode(d) == "git",
        )
        if problem:
            print(f"\nREJECTED: {rep.name} could not be frozen as aggregate evidence\n", file=sys.stderr)
            print(f"  - {problem}", file=sys.stderr)
            print("\nNothing was handed off.", file=sys.stderr)
            raise SystemExit(1)
        meta["bundle_digest"] = str(frozen["digest"])
    else:
        perturb("submit:before-freeze")
        frozen, problem = freeze_task_bundle(
            d, a.run, a.owner, rep, body, verification,
            "blocked-submission" if blocked else "submission",
            verified_source=verified_source,
            require_patch=not blocked and evidence_mode(d) == "git",
            contract_bytes=contract_bytes, dependency_bytes=dependency_bytes,
        )
        if problem:
            print(f"\nREJECTED: {rep.name} could not be frozen as review evidence\n", file=sys.stderr)
            print(f"  - {problem}", file=sys.stderr)
            print("\nNothing was handed off.", file=sys.stderr)
            raise SystemExit(1)
        meta["bundle_digest"] = str(frozen["digest"])

    if blocked:
        meta["status"] = "blocked"
    elif is_five_role(d):
        # Five-role-v1 never completes on submission. Submission is evidence
        # for reviewer-owned approval, never a terminal state.
        meta["status"] = "submitted"
    elif a.owner == "orch" and topology(d) == "combined":
        meta["status"] = "completed"
    elif a.owner != "orch" and task.is_file() and parse(task.read_text())[0].get("executor") == "orchestrator":
        meta["status"] = "completed"
    else:
        meta["status"] = "submitted"
    publish(rep, render(meta, body))
    if frozen:
        patch = frozen["patch"]
        kind = frozen.get("kind", "task-round")
        baseline_label = "the run baseline" if kind == "aggregate" else "the task baseline"
        print(f"\nfrozen {kind} bundle {frozen['digest']} (round {frozen['round']})")
        if patch["coverage"] == COVERAGE_AVAILABLE:
            print(f"  patch: {len(frozen['changed_paths'])} path(s) against {baseline_label}")
        else:
            print(f"  patch coverage unavailable - {patch['reason']}")
            print("  This is not an empty patch.")
        if kind == "task-round" and frozen.get("delta", {}).get("coverage") == COVERAGE_AVAILABLE:
            print(f"  correction delta from {frozen['delta']['from']}")
        if kind == "aggregate":
            pinned = frozen.get("constituents")
            pinned = pinned if isinstance(pinned, list) else []
            print(f"  pins {len(pinned)} constituent task-round bundle(s)")
    verdict = {
        "blocked": "blocked, needs a decision",
        "completed": "completed; recorded without a redundant handoff",
        "submitted": "submitted for review",
    }[meta["status"]]
    if meta["status"] == "blocked":
        record_outcome(d, a.owner, meta.get("round", "?"), "submit", "blocked",
                       sections(parse(rep.read_text())[1]).get("Decisions needed", ""), rep.name)
    print(f"\n{rep.name}: {verdict}")
