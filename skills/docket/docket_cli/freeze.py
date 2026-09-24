"""Freezing a task round into a content-addressed bundle."""

from __future__ import annotations

import re
from pathlib import Path

from .common import COVERAGE_AVAILABLE, COVERAGE_UNAVAILABLE, DOCUMENTS_ONLY, stamp
from .policy import artifact_policy_fields
from .baselines import (
    evidence_digest, evidence_mode, snapshot_dir, snapshot_path, verify_output_bytes,
)
from .bundles import (
    BUNDLE_VERSION, DEPS_DIR, VERIFY_OUTPUT_KEYS, baseline_roots, baseline_tree,
    bundle_problems, digest_of, latest_bundle, load_bundle, moved_paths, publish_bundle,
    retain_tree_objects, source_tree, tree_patch,
)
from .dependencies import deps_record_bytes, parse_deps


def frozen_baseline_root(
    d: Path, owner: str, record: dict[str, object], store: object
) -> tuple[dict[str, object], str]:
    """One baseline root, with everything a reconstruction needs copied into the bundle.

    The mutable `.snapshots` tree is where a baseline is captured, not where it is
    kept for review. Copying the staged patch, the worktree patch, and the untracked
    manifest into the bundle means a later deletion or rewrite of the capture cannot
    reach evidence that was already frozen, and the copies are covered by the
    bundle's own digest.

    A capture that disappears between reconstructing the trees and copying it is a
    reported problem, never a placeholder: a bundle that records an artifact as
    unavailable and still reads as intact is exactly the false assurance the freeze
    exists to prevent.
    """
    row: dict[str, object] = {
        "alias": record.get("alias", ""),
        "path": record.get("path", ""),
        "git_dir": record.get("git_dir", ""),
        "common_dir": record.get("common_dir", ""),
        "base_tree": record.get("base_tree", ""),
        "branch": record.get("branch", ""),
        "head": record.get("head", ""),
        "embedded_checkouts": list(record.get("embedded_checkouts") or []),
    }
    snap = snapshot_dir(d, owner)
    alias = str(record.get("alias", ""))
    for key in ("staged_patch", "worktree_patch", "untracked_manifest"):
        name = str(record.get(key, ""))
        if not name:
            continue
        try:
            data = (snap / name).read_bytes()
        except OSError as exc:
            return row, (
                f"the baseline reconstruction artifact {name} for {alias} could not be read "
                f"while the round was being frozen: {exc.strerror or exc}"
            )
        row[key] = store(f"baseline/{name}", data)
    return row, ""


def freeze_task_bundle(
    d: Path, run: str, owner: str, rep: Path, report_body: str,
    verification: dict[str, object], trigger: str,
    verified_source: list[dict[str, str]] | None = None, require_patch: bool = False,
    contract_bytes: bytes | None = None, dependency_bytes: bytes | None = None,
) -> tuple[dict[str, object] | None, str]:
    """Freeze one task round into an immutable, content-addressed evidence bundle.

    Everything a reviewer or a verifier may later bind to is inside: the contract
    revision under review, the immutable task baseline it was dispatched over, the
    root-qualified full patch from that baseline, the resulting source revision, the
    record of the inputs it consumed, the report body as submitted, and the
    verification as captured. Nothing that happens afterwards can reach back into it,
    because the address is the content.
    """
    match = re.search(r"-report-(\d+)\.mdx$", rep.name)
    rnd = int(match.group(1)) if match else 1
    artifacts: dict[str, bytes] = {}

    def store(name: str, data: bytes) -> dict[str, object]:
        artifacts[name] = data
        return {"file": name, "sha256": digest_of(data), "bytes": len(data)}

    task = d / f"{owner}-task.mdx"
    contract: dict[str, object] = {"source": task.name, "revision": ""}
    # The contract is frozen from the bytes the verification described, not from
    # whatever the file holds by the time the freeze runs.
    text = contract_bytes if contract_bytes is not None else (
        task.read_bytes() if task.is_file() else None
    )
    if text is not None:
        contract["revision"] = digest_of(text)
        contract.update(store("contract.mdx", text))

    snap = snapshot_path(d, owner)
    baseline: dict[str, object] = {"snapshot": snap.name, "revision": "", "roots": []}
    if snap.is_file():
        captured = snap.read_bytes()
        baseline["revision"] = digest_of(captured)
        baseline.update(store("baseline/snapshot.json", captured))

    body = report_body.encode()
    report: dict[str, object] = {"source": rep.name, "body_digest": evidence_digest(report_body)}
    report.update(store("report.mdx", body))

    # The inputs the captured verification was taken against, frozen as bytes. Without
    # this the manifest says nothing about what the round consumed, and re-recording a
    # pin afterwards would leave an accepting verdict checking the replacement instead
    # of the dependencies the result described.
    consumed = dependency_bytes if dependency_bytes is not None else deps_record_bytes(d, owner)
    dependencies: dict[str, object] = {
        "source": f"{DEPS_DIR}/{owner}.json",
        "revision": digest_of(consumed),
        "consumed": [
            {
                "on": entry.get("on", ""), "kind": entry.get("kind", ""),
                "round": entry.get("round", ""), "bundle": entry.get("bundle", ""),
            }
            for entry in parse_deps(consumed)
        ],
    }
    dependencies.update(store("dependencies.json", consumed))

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
        records, reason = baseline_roots(d, owner)
        if reason:
            patch["reason"] = source["reason"] = reason
        else:
            problem = ""
            added: list[str] = []
            for record in records:
                alias = str(record.get("alias", ""))
                left, problem = baseline_tree(d, owner, record)
                if problem:
                    break
                identity, problem = source_tree(d, owner, record, "frozen")
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
                # One root that cannot be rendered makes the whole patch partial, and a
                # partial patch is never published beside a manifest that omits it.
                patch["reason"] = source["reason"] = problem
                patch["roots"], source["roots"], changed = [], [], []
                for name in added:
                    artifacts.pop(name, None)
            else:
                patch["coverage"] = source["coverage"] = COVERAGE_AVAILABLE
        frozen_roots: list[dict[str, object]] = []
        for record in records:
            row, missing = frozen_baseline_root(d, owner, record, store)
            if missing:
                return None, (
                    f"{missing}. Nothing was frozen and nothing was handed off: a bundle that "
                    "cannot carry the baseline it was measured against is not review evidence"
                )
            frozen_roots.append(row)
        baseline["roots"] = frozen_roots

    if require_patch and patch["coverage"] != COVERAGE_AVAILABLE:
        return None, (
            "a root-qualified patch against the task baseline could not be frozen: "
            f"{patch['reason']}. This is not an empty patch, and nothing was handed off"
        )

    if verified_source is not None and source["coverage"] == COVERAGE_AVAILABLE:
        before = {item["alias"]: item["tree"] for item in verified_source}
        after = {item["alias"]: item["tree"] for item in source["roots"]}
        if before != after:
            return None, (
                "the checkout changed while the verification ran, so the captured result does "
                f"not describe the source being frozen: {moved_paths(d, owner, before, after)}. "
                "Nothing was frozen "
                "and nothing was handed off: stop whatever writes those paths during the verify "
                "command, then re-run `docket submit` on a settled workspace"
            )

    delta: dict[str, object] = {"coverage": COVERAGE_UNAVAILABLE, "reason": "no earlier bundle", "roots": []}
    previous = latest_bundle(d, owner)
    if previous:
        earlier = load_bundle(d, owner, previous)
        delta["from"] = previous.get("digest", "")
        delta["from_round"] = previous.get("round", "")
        if earlier is None:
            delta["reason"] = f"the earlier bundle {previous.get('digest')} is unreadable"
        elif str(earlier.get("source", {}).get("coverage", "")) != COVERAGE_AVAILABLE:
            delta["reason"] = f"the earlier bundle {previous.get('digest')} pinned no source revision"
        elif patch["coverage"] != COVERAGE_AVAILABLE:
            delta["reason"] = str(patch["reason"])
        else:
            before = {str(item.get("alias", "")): str(item.get("tree", ""))
                      for item in earlier.get("source", {}).get("roots", [])}
            problem = ""
            rows: list[dict[str, object]] = []
            for record in records:
                alias = str(record.get("alias", ""))
                if alias not in before:
                    problem = f"the earlier bundle pinned no source revision for {alias}"
                    break
                right = next(item["tree"] for item in source["roots"] if item["alias"] == alias)
                data, paths, problem = tree_patch(d, record, before[alias], right)
                if problem:
                    break
                row: dict[str, object] = {
                    "alias": alias, "from_tree": before[alias], "to_tree": right,
                }
                row.update(store(f"delta/{alias}.patch", data))
                row["paths"] = paths
                rows.append(row)
            if problem:
                delta["reason"] = problem
                for row in rows:
                    artifacts.pop(str(row.get("file", "")), None)
            else:
                delta["coverage"] = COVERAGE_AVAILABLE
                delta["reason"] = ""
                delta["roots"] = rows

    manifest: dict[str, object] = {
        "version": BUNDLE_VERSION,
        "kind": "task-round",
        "run": run,
        "owner": owner,
        "round": rnd,
        "trigger": trigger,
        "frozen_at": stamp(),
        "evidence_mode": mode,
        "contract": contract,
        "baseline": baseline,
        "report": report,
        "dependencies": dependencies,
        "verification": captured,
        "patch": patch,
        "source": source,
        "delta": delta,
        "changed_paths": sorted(changed),
        "digest": "",
        **artifact_policy_fields(d),
    }
    entry, where, problems = publish_bundle(
        d, owner, manifest, artifacts, validate=lambda e: bundle_problems(d, owner, e))
    if problems:
        return None, "; ".join(problems)
    return manifest, ""


def current_bundle(d: Path, owner: str, rnd: int) -> tuple[dict[str, object] | None, list[str]]:
    """The bundle a verdict on this round must bind to, plus anything wrong with it."""
    entry = latest_bundle(d, owner, rnd)
    if not entry:
        return None, []
    problems = bundle_problems(d, owner, entry)
    if problems:
        return None, problems
    return load_bundle(d, owner, entry), []
