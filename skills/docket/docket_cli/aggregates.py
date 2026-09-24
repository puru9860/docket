"""Aggregate bundles over a run's task bundles."""

from __future__ import annotations

import re
from pathlib import Path

from .common import COVERAGE_AVAILABLE, COVERAGE_UNAVAILABLE, DOCUMENTS_ONLY, die, stamp
from .paths import owners, reports
from .policy import artifact_policy_fields
from .baselines import evidence_digest, evidence_mode, snapshot_path, verify_output_bytes
from .bundles import (
    BUNDLE_VERSION, VERIFY_OUTPUT_KEYS, baseline_roots, baseline_tree, bundle_problems,
    digest_of, latest_bundle, load_bundle, moved_paths, publish_bundle, retain_tree_objects,
    source_tree, tree_patch,
)
from .state import read_transition, state_of
from .freeze import frozen_baseline_root


def aggregate_constituents(d: Path) -> list[dict[str, str]]:
    """Every decided task's latest bundle, for pinning into an aggregate bundle."""
    out: list[dict[str, str]] = []
    terminal = {"approved", "waived", "completed"}
    for owner in owners(d):
        if owner == "orch":
            continue
        rnd, st = state_of(d, owner)
        if st not in terminal:
            continue
        entry = latest_bundle(d, owner, rnd)
        if entry and not bundle_problems(d, owner, entry):
            out.append({
                "owner": owner,
                "round": str(rnd),
                "bundle": str(entry.get("digest", "")),
                "state": st,
            })
    return out


def aggregate_bundle_problems(d: Path, entry: dict[str, object]) -> list[str]:
    """Everything that stops a frozen aggregate bundle from being usable evidence.

    Beyond the standard artifact integrity and pinned-tree checks, an aggregate
    bundle also verifies that each constituent task-round bundle it pins is still
    intact and has not moved since the aggregate was frozen.
    """
    problems = bundle_problems(d, "orch", entry)
    if problems:
        return problems
    manifest = load_bundle(d, "orch", entry) or {}
    pinned = manifest.get("constituents")
    if not isinstance(pinned, list):
        return [f"aggregate bundle {entry.get('digest')} pins no constituent list"]
    for item in pinned:
        if not isinstance(item, dict):
            continue
        other = str(item.get("owner", ""))
        recorded = str(item.get("bundle", ""))
        try:
            pinned_round = int(str(item.get("round", "0")))
        except ValueError:
            pinned_round = 0
        current = latest_bundle(d, other)
        if not current:
            return [
                f"aggregate bundle {entry.get('digest')} pins {other} at {recorded}, "
                f"but {other} has no frozen evidence"
            ]
        if str(current.get("digest", "")) != recorded:
            return [
                f"aggregate bundle {entry.get('digest')} pins {other} at {recorded}, "
                f"but its current bundle is {current.get('digest')}"
            ]
        if bundle_problems(d, other, current):
            return [
                f"aggregate bundle {entry.get('digest')} pins {other} at {recorded}, "
                f"and that bundle is damaged"
            ]
        # A waived constituent that was reopened after the aggregate froze no
        # longer means what the pin claimed, even though the old bundle bytes
        # are still intact. The reopen journal and the next-round draft report
        # both mark the pin as superseded.
        journal = read_transition(d, other)
        if journal.get("verdict") == "reopen-waived" and journal.get("state") == "complete":
            try:
                reopened_round = int(str(journal.get("round", "0")))
            except ValueError:
                reopened_round = 0
            if reopened_round == pinned_round:
                return [
                    f"aggregate bundle {entry.get('digest')} pins {other} at {recorded}, "
                    f"but {other} round {pinned_round} was reopened from waiver"
                ]
        for rep in reports(d, other):
            match = re.search(r"-report-(\d+)\.mdx$", rep.name)
            if match and int(match.group(1)) > pinned_round:
                return [
                    f"aggregate bundle {entry.get('digest')} pins {other} at {recorded}, "
                    f"but {other} has a later round {match.group(1)} after reopen"
                ]
    return []


def current_aggregate(d: Path, rnd: int) -> tuple[dict[str, object] | None, list[str]]:
    """The aggregate bundle a verdict on the orch report must bind to."""
    entry = latest_bundle(d, "orch", rnd)
    if not entry:
        return None, []
    problems = aggregate_bundle_problems(d, entry)
    if problems:
        return None, problems
    return load_bundle(d, "orch", entry), []


def require_aggregate(d: Path, rnd: int, rep: Path, digest: str) -> tuple[dict[str, object], str]:
    """The aggregate bundle this verdict must bind to, and whether it is stale."""
    frozen, integrity = current_aggregate(d, rnd)
    if integrity:
        die(
            f"orch round {rnd} cannot be decided against damaged aggregate evidence:\n      "
            + "\n      ".join(f"- {item}" for item in integrity)
            + "\n    Restore the bundle, or return the report to status: draft and resubmit it."
        )
    if frozen is None:
        die(
            f"orch round {rnd} has no frozen aggregate evidence bundle, so a verdict would bind "
            f"to nothing. Restore {rep.name} to status: draft and run `docket submit "
            f"{d.name} orch` so the aggregate patch, constituent pins, source revision, "
            "report body, and verification freeze together"
        )
    recorded = str(frozen.get("report", {}).get("body_digest", ""))
    return frozen, "" if recorded == digest else str(frozen.get("digest", ""))


def freeze_aggregate_bundle(
    d: Path, run: str, rep: Path, report_body: str,
    verification: dict[str, object], trigger: str,
    verified_source: list[dict[str, str]] | None = None, require_patch: bool = False,
) -> tuple[dict[str, object] | None, str]:
    """Freeze one aggregate round into an immutable, content-addressed evidence bundle.

    An aggregate bundle pins the exact constituent task-round bundle digests, the
    run baseline it was measured from (not any one task baseline), the aggregate
    patch from that run baseline to the integrated source revision, the aggregate
    report body as submitted, and the captured verification. It carries no task
    contract because the orch has none; the plan stands in for the contract.
    """
    owner = "orch"
    match = re.search(r"-report-(\d+)\.mdx$", rep.name)
    rnd = int(match.group(1)) if match else 1
    artifacts: dict[str, bytes] = {}

    def store(name: str, data: bytes) -> dict[str, object]:
        artifacts[name] = data
        return {"file": name, "sha256": digest_of(data), "bytes": len(data)}

    plan = d / "plan.mdx"
    contract: dict[str, object] = {"source": plan.name, "revision": ""}
    if plan.is_file():
        data = plan.read_bytes()
        contract["revision"] = digest_of(data)
        contract.update(store("plan.mdx", data))

    # The run baseline, not any task baseline. The aggregate patch is from here.
    run_snap = snapshot_path(d, "run")
    baseline: dict[str, object] = {"snapshot": run_snap.name, "revision": "", "roots": []}
    if run_snap.is_file():
        captured = run_snap.read_bytes()
        baseline["revision"] = digest_of(captured)
        baseline.update(store("baseline/snapshot.json", captured))

    body = report_body.encode()
    report: dict[str, object] = {"source": rep.name, "body_digest": evidence_digest(report_body)}
    report.update(store("report.mdx", body))

    constituents: list[dict[str, str]] = aggregate_constituents(d)
    pinned: dict[str, object] = {
        "count": len(constituents),
        "entries": [
            {key: value for key, value in entry.items() if key != "state"}
            for entry in constituents
        ],
    }

    captured = {key: value for key, value in verification.items()
                if key not in VERIFY_OUTPUT_KEYS}
    if "stdout_text" in verification:
        captured["stdout"] = store("verify.stdout", verify_output_bytes(verification, "stdout"))
        captured["stderr"] = store("verify.stderr", verify_output_bytes(verification, "stderr"))

    mode = evidence_mode(d)
    records: list[dict[str, object]] = []
    patch: dict[str, object] = {"coverage": COVERAGE_UNAVAILABLE, "reason": "", "roots": []}
    source: dict[str, object] = {"coverage": COVERAGE_UNAVAILABLE, "reason": "", "roots": []}
    changed: list[str] = []
    if mode == DOCUMENTS_ONLY:
        patch["reason"] = source["reason"] = "the run declares evidence_mode: documents-only"
    elif mode != "git":
        patch["reason"] = source["reason"] = f"plan.mdx declares an unknown evidence_mode: {mode}"
    else:
        records, reason = baseline_roots(d, "run")
        if reason:
            patch["reason"] = source["reason"] = reason
        else:
            problem = ""
            added: list[str] = []
            for record in records:
                alias = str(record.get("alias", ""))
                left, problem = baseline_tree(d, "run", record)
                if problem:
                    break
                identity, problem = source_tree(d, "run", record, "aggregate")
                if problem:
                    break
                data, paths, problem = tree_patch(d, record, left, identity["tree"])
                if problem:
                    break
                problem = retain_tree_objects(d, record, (left, identity["tree"]))
                if problem:
                    break
                entry: dict[str, object] = {
                    "alias": alias, "baseline_tree": left, "source_tree": identity["tree"],
                }
                entry.update(store(f"{alias}.patch", data))
                entry["paths"] = paths
                added.append(f"{alias}.patch")
                patch["roots"].append(entry)
                source["roots"].append(identity)
                changed.extend(f"{alias}:{item}" for item in paths)
            if problem:
                patch["reason"] = source["reason"] = problem
                patch["roots"], source["roots"], changed = [], [], []
                for name in added:
                    artifacts.pop(name, None)
            else:
                patch["coverage"] = source["coverage"] = COVERAGE_AVAILABLE
        frozen_roots: list[dict[str, object]] = []
        for record in records:
            row, missing = frozen_baseline_root(d, "run", record, store)
            if missing:
                return None, (
                    f"{missing}. Nothing was frozen and nothing was handed off: an aggregate "
                    "bundle that cannot carry the run baseline it was measured against is not "
                    "review evidence"
                )
            frozen_roots.append(row)
        baseline["roots"] = frozen_roots

    if require_patch and patch["coverage"] != COVERAGE_AVAILABLE:
        return None, (
            "an aggregate patch against the run baseline could not be frozen: "
            f"{patch['reason']}. This is not an empty patch, and nothing was handed off"
        )

    if verified_source is not None and source["coverage"] == COVERAGE_AVAILABLE:
        before = {item["alias"]: item["tree"] for item in verified_source}
        after = {item["alias"]: item["tree"] for item in source["roots"]}
        if before != after:
            return None, (
                "the checkout changed while the verification ran, so the captured result does "
                f"not describe the source being frozen: {moved_paths(d, 'run', before, after)}. "
                "Nothing was frozen "
                "and nothing was handed off: stop whatever writes those paths during the verify "
                "command, then re-run `docket submit` on a settled workspace"
            )

    manifest: dict[str, object] = {
        "version": BUNDLE_VERSION,
        "kind": "aggregate",
        "run": run,
        "owner": "orch",
        "round": rnd,
        "trigger": trigger,
        "frozen_at": stamp(),
        "evidence_mode": mode,
        "contract": contract,
        "baseline": baseline,
        "report": report,
        "constituents": pinned["entries"],
        "verification": captured,
        "patch": patch,
        "source": source,
        "changed_paths": sorted(changed),
        "digest": "",
        **artifact_policy_fields(d),
    }
    entry, where, problems = publish_bundle(
        d, "orch", manifest, artifacts, validate=lambda e: aggregate_bundle_problems(d, e))
    if problems:
        return None, "; ".join(problems)
    return manifest, ""


def stale_aggregate(d: Path) -> list[str]:
    """Everything that makes any aggregate bundle no longer current evidence."""
    problems: list[str] = []
    orch_rnd, orch_st = state_of(d, "orch")
    if orch_rnd == 0 or orch_st in ("draft", "unassigned"):
        return []
    entry = latest_bundle(d, "orch", orch_rnd)
    if not entry:
        return []
    agg_problems = aggregate_bundle_problems(d, entry)
    if agg_problems:
        return [f"aggregate round {orch_rnd} at {entry.get('digest')}: {item}"
                for item in agg_problems]
    return problems
