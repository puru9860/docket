"""Consumed-input records and the provisional dependency policy."""

from __future__ import annotations

import json
from pathlib import Path

from .common import COVERAGE_AVAILABLE, stamp
from .frontmatter import parse
from .paths import latest, reports
from .publication import publish_json
from .baselines import evidence_digest
from .bundles import (
    DEPS_DIR, FINAL_STATES, PROVISIONAL_FORBIDDEN, bundle_problems, digest_of,
    latest_bundle, load_bundle,
)
from .state import state_of, task_depends_on


def pin_final_dependencies(d: Path, owner: str) -> list[tuple[str, str]]:
    """Record each approved `depends_on` task as a final consumed input.

    The dependent is built on that approved work, so its frozen rounds should
    say which bundle it was: a dependency that later freezes different evidence
    then withdraws the dependent's review readiness instead of going unnoticed.
    Only a draft round records inputs, and an existing pin is never rewritten.
    """
    if owner == "orch" or state_of(d, owner)[1] not in ("unassigned", "draft"):
        return []
    entries = read_deps(d, owner)
    recorded = {str(entry.get("on", "")) for entry in entries}
    pinned: list[tuple[str, str]] = []
    for dep in task_depends_on(d, owner):
        if dep in recorded or state_of(d, dep)[1] not in FINAL_STATES:
            continue
        latest = latest_bundle(d, dep)
        if not latest or consumable_problems(d, dep, latest, True):
            continue
        entries.append({
            "on": dep, "kind": "final", "state": state_of(d, dep)[1],
            "round": str(latest.get("round", "")), "bundle": str(latest.get("digest", "")),
            "recorded_at": stamp(),
        })
        pinned.append((dep, str(latest.get("digest", ""))))
    if pinned:
        write_deps(d, owner, sorted(entries, key=lambda item: item.get("on", "")))
    return pinned


def provisional_policy(d: Path) -> str:
    """Whether this run allows consuming verified-but-unapproved work. Default: no."""
    plan = d / "plan.mdx"
    if not plan.is_file():
        return PROVISIONAL_FORBIDDEN
    value = parse(plan.read_text())[0].get("provisional_integration", "").strip()
    return value or PROVISIONAL_FORBIDDEN


def deps_path(d: Path, owner: str) -> Path:
    return d / DEPS_DIR / f"{owner}.json"


def parse_deps(data: bytes) -> list[dict[str, str]]:
    """The consumed-input entries a record holds, from its bytes rather than its path.

    A frozen bundle keeps the record as bytes, so the same reader has to work on a
    live file and on a copy that no longer has one.
    """
    try:
        payload = json.loads(data.decode())
    except (UnicodeDecodeError, ValueError):
        return []
    entries = payload.get("dependencies") if isinstance(payload, dict) else None
    return [
        {str(k): str(v) for k, v in entry.items()}
        for entry in entries or [] if isinstance(entry, dict)
    ]


def deps_record_bytes(d: Path, owner: str) -> bytes:
    """The consumed-input record exactly as a submission freezes it.

    A task that consumes nothing still has a record: the explicit empty set. Freezing
    it means "this round was verified against no inputs" is a stated claim rather than
    an absent artifact indistinguishable from one that was lost.
    """
    path = deps_path(d, owner)
    if path.is_file():
        try:
            return path.read_bytes()
        except OSError:
            pass
    return (json.dumps({"owner": owner, "dependencies": []}, indent=2, sort_keys=True) + "\n").encode()


def read_deps(d: Path, owner: str) -> list[dict[str, str]]:
    return parse_deps(deps_record_bytes(d, owner))


def write_deps(d: Path, owner: str, entries: list[dict[str, str]]) -> None:
    publish_json(deps_path(d, owner), {"owner": owner, "dependencies": entries})


def dependency_problems(d: Path, owner: str) -> list[str]:
    """Consumed inputs that moved since they were consumed, and must be reverified."""
    problems: list[str] = []
    for entry in read_deps(d, owner):
        other = entry.get("on", "")
        pinned = entry.get("bundle", "")
        latest = latest_bundle(d, other)
        if not latest:
            problems.append(
                f"dependency {other} no longer publishes the frozen bundle {pinned} that "
                f"{owner} consumed"
            )
            continue
        if str(latest.get("digest", "")) != pinned:
            problems.append(
                f"dependency {other} changed since {owner} consumed it (pinned {pinned}, now "
                f"{latest.get('digest')}); reverify against the new evidence and re-record it "
                f"with `docket depend {d.name} {owner} --on {other}`"
            )
    return problems


def provisional_dependencies(d: Path, owner: str) -> list[dict[str, str]]:
    return [entry for entry in read_deps(d, owner) if entry.get("kind") == "provisional"]


def refreshed_inputs(d: Path, owner: str, frozen: dict[str, object]) -> list[str]:
    """Whether the live consumed-input record still is the one the round froze.

    `dependency_problems` compares the live record against each input's newest bundle,
    so re-recording a pin makes it go quiet without anything having been reverified.
    The frozen record is what the captured verification actually described, so an
    accepting verdict binds to that instead: a record that moved at all since the freeze
    means the round has to be verified again, not decided over the gap.
    """
    block = frozen.get("dependencies")
    if not isinstance(block, dict) or not str(block.get("revision", "")):
        return []
    live = deps_record_bytes(d, owner)
    if digest_of(live) == str(block.get("revision", "")):
        return []
    rows = block.get("consumed")
    before = {
        str(row.get("on", "")): str(row.get("bundle", ""))
        for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)
    }
    after = {str(entry.get("on", "")): str(entry.get("bundle", "")) for entry in parse_deps(live)}
    problems: list[str] = []
    for other in sorted(set(before) | set(after)):
        if before.get(other) == after.get(other):
            continue
        if other not in before:
            problems.append(
                f"{owner} now records consuming {other} at {after[other]}, which the verification "
                f"frozen for this round never saw"
            )
        elif other not in after:
            problems.append(
                f"{owner} no longer records consuming {other} at {before[other]}, which the "
                f"verification frozen for this round was built on"
            )
        else:
            problems.append(
                f"{owner} re-recorded {other} at {after[other]} after freezing a round verified "
                f"against {before[other]}; re-recording a pin is not a reverification"
            )
    return problems or [
        f"the consumed-input record of {owner} changed after this round was frozen"
    ]


def withdrawn_readiness(d: Path, owner: str) -> list[str]:
    """Everything that stops this owner's frozen round from being accepted as it stands."""
    problems = dependency_problems(d, owner)
    rnd = state_of(d, owner)[0]
    entry = latest_bundle(d, owner, rnd) if rnd else None
    frozen = load_bundle(d, owner, entry) if entry else None
    if frozen:
        problems.extend(refreshed_inputs(d, owner, frozen))
    return problems


VERIFICATION_REFUSALS = {
    "failed": "its frozen verification failed",
    "timeout": "its frozen verification timed out",
    "skipped": "its frozen verification was skipped",
    "blocked": "it was submitted blocked, so no verification was run",
    "none": "it registered no verification command, so the round proves nothing",
}


def consumable_problems(
    d: Path, other: str, entry: dict[str, object], final: bool
) -> list[str]:
    """Why another task's frozen bundle cannot be consumed as an input, if it cannot.

    Provisional integration is permission to consume verified work before a reviewer
    has approved it. It is not permission to consume blocked, failed, skipped, stale,
    damaged, or patchless evidence: none of those carry a usable verification result,
    so building on them would inherit a claim nobody ever made.
    """
    damaged = bundle_problems(d, other, entry)
    if damaged:
        return damaged
    manifest = load_bundle(d, other, entry) or {}
    rnd = int(entry.get("round", 0) or 0)
    rep = latest(reports(d, other))
    if rep is None:
        return [f"{other} has no report, so its frozen evidence describes nothing"]
    meta, body = parse(rep.read_text())
    problems: list[str] = []
    if int(meta.get("round", "0") or 0) != rnd:
        problems.append(
            f"{other} has moved on to round {meta.get('round')}, so its round {rnd} bundle "
            f"{entry.get('digest')} is stale evidence"
        )
    elif evidence_digest(body) != str(manifest.get("report", {}).get("body_digest", "")):
        problems.append(
            f"{other} edited its report after freezing {entry.get('digest')}, so that bundle is "
            "stale evidence"
        )
    if meta.get("status") == "blocked":
        problems.append(f"{other} is blocked, so its round {rnd} evidence is not usable work")
    status = str(manifest.get("verification", {}).get("status", ""))
    if not final and status != "passed":
        problems.append(
            f"{other} round {rnd} cannot be consumed provisionally: "
            + VERIFICATION_REFUSALS.get(status, f"its frozen verification is {status or 'unknown'}")
        )
    if str(manifest.get("evidence_mode", "")) == "git":
        for key, label in (("patch", "root-qualified patch"), ("source", "source revision")):
            block = manifest.get(key)
            block = block if isinstance(block, dict) else {}
            if str(block.get("coverage", "")) != COVERAGE_AVAILABLE:
                problems.append(
                    f"{other} round {rnd} froze no {label}: {block.get('reason')}. This is not "
                    "an empty patch"
                )
    return problems
