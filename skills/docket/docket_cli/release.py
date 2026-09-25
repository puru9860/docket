"""Release freezing and the evidence checks the final packet shares."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import COVERAGE_AVAILABLE, LAUNCHER, SHA_DIGEST_RE, die, stamp
from .frontmatter import parse
from .paths import _ordered, owners, root
from .publication import fault, publish_bytes, publish_json
from .policy import need_run
from .evidence import git_argv, git_env
from .baselines import evidence_digest
from .bundles import (
    BUNDLE_VERSION, baseline_roots, bundle_dir, bundle_objects, bundle_problems,
    bundles_for, check_frozen_digest, digest_of, freeze_record, latest_bundle, load_bundle,
    owner_bundles, source_tree,
)
from .state import list_amendments, list_incidents, state_of
from .aggregates import aggregate_bundle_problems
from .five_role import open_escalations
from .qualification import qualification_problems, worktree_identity
from .suite import parse_suite_streams, resolve_suite_artifact, suite_problems
from .metrics import scan_content_tree, tree_revision


def packet_staleness(d: Path, owner: str, rnd: int,
                       manifest: dict[str, object] | None) -> list[str]:
    """Why a frozen bundle no longer binds the live report, contract, or inputs."""
    problems: list[str] = []
    if manifest is None:
        return ["bundle manifest is missing or unreadable"]
    rep = d / f"{owner}-report-{rnd:02d}.mdx"
    if rep.is_file():
        try:
            live_body = parse(rep.read_text())[1]
            live_digest = evidence_digest(live_body)
        except (OSError, ValueError):
            live_digest = ""
        frozen_digest = str((manifest.get("report") or {}).get("body_digest", "")
                            if isinstance(manifest.get("report"), dict) else "")
        if live_digest and frozen_digest and live_digest != frozen_digest:
            problems.append(
                f"{owner} round {rnd} report body changed since the bundle "
                f"{manifest.get('digest')} was frozen; re-verify before review")
    task = d / f"{owner}-task.mdx"
    if task.is_file():
        try:
            live_contract = digest_of(task.read_bytes())
        except OSError:
            live_contract = ""
        frozen_contract = str((manifest.get("contract") or {}).get("revision", "")
                              if isinstance(manifest.get("contract"), dict) else "")
        if live_contract and frozen_contract and live_contract != frozen_contract:
            problems.append(
                f"{owner} contract changed since bundle {manifest.get('digest')} "
                "was frozen; the captured verification no longer binds it")
    if owner == "orch":
        plan = d / "plan.mdx"
        if plan.is_file():
            try:
                live_plan = digest_of(plan.read_bytes())
            except OSError:
                live_plan = ""
            frozen_plan = str((manifest.get("contract") or {}).get("revision", "")
                              if isinstance(manifest.get("contract"), dict) else "")
            if live_plan and frozen_plan and live_plan != frozen_plan:
                problems.append(
                    f"orch plan changed since bundle {manifest.get('digest')} "
                    "was frozen; the captured integration verification no longer "
                    "binds it")
    return problems


def aggregate_source_moved(d: Path, manifest: dict[str, object]) -> list[str]:
    """Live source trees that no longer match the aggregate's frozen ones.

    The freeze refuses a checkout that moves under verification; the final
    packet refuses a live tree that moved after the freeze. Unavailable
    coverage cannot be assessed and is skipped, never treated as matching.
    """
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("coverage") != COVERAGE_AVAILABLE:
        return []
    frozen = {str(r.get("alias", "")): str(r.get("tree", ""))
              for r in (source.get("roots", []) or []) if isinstance(r, dict)}
    if not frozen:
        return []
    records, reason = baseline_roots(d, "run")
    if reason:
        return []
    moved: list[str] = []
    for record in records:
        alias = str(record.get("alias", ""))
        if alias not in frozen:
            continue
        identity, problem = source_tree(d, "run", record, "packet-check")
        if problem:
            return []
        if identity.get("tree") != frozen[alias]:
            moved.append(alias)
    return moved


def integration_verification_problems(d: Path, entry: dict, manifest: dict | None) -> list[str]:
    """Why an aggregate bundle carries no reviewable integration verification.

    A final packet binds an approval to integrated behavior, not to task
    verdicts alone, so the aggregate's own captured verification must exist,
    have passed, keep its outputs, and still describe the frozen source.
    """
    digest = str(entry.get("digest", "") or "?")
    if manifest is None:
        return [f"aggregate bundle {digest} manifest is missing or unreadable"]
    ver = manifest.get("verification")
    if not isinstance(ver, dict):
        return [f"aggregate bundle {digest} carries no integration verification; "
                "submit the aggregate with its verify command"]
    status = str(ver.get("status", "") or "missing")
    if status != "passed":
        return [f"integration verification is {status}, not passed "
                f"(command {str(ver.get('command', ''))[:80] or 'none'}); a final "
                "packet needs integrated behavior demonstrated, not skipped, "
                "blocked, failed, or timed-out evidence"]
    if not str(ver.get("command", "") or "").strip():
        return [f"aggregate bundle {digest} integration verification records "
                "no command"]
    where = bundle_dir(d, "orch", entry)
    for key in ("stdout", "stderr"):
        ref = ver.get(key, {})
        if not isinstance(ref, dict) or not ref.get("file"):
            return [f"aggregate bundle {digest} integration verification is "
                    f"missing its {key} artifact"]
        target = where / str(ref["file"])
        if not target.is_file():
            return [f"integration verification artifact {ref['file']} is missing "
                    f"from aggregate bundle {digest}"]
        try:
            actual = digest_of(target.read_bytes())
        except OSError:
            return [f"integration verification artifact {ref['file']} in aggregate "
                    f"bundle {digest} is unreadable"]
        if actual != str(ref.get("sha256", "")):
            return [f"integration verification artifact {ref['file']} in aggregate "
                    f"bundle {digest} is damaged (digest mismatch)"]
    for alias in aggregate_source_moved(d, manifest):
        return [f"aggregate source {alias} moved since the integration "
                "verification ran; re-verify the integrated tree"]
    return []


RELEASE_MILESTONES = ("M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11")


MILESTONE_RE = re.compile(r"^M(4|5|6|7|8|9|10|11)$")

UNAVAILABLE_MILESTONE = "unavailable (historical evidence predates milestone bundles; not reconstructed)"


def find_bundle_by_digest(d: Path, digest: str) -> tuple[str | None, dict | None]:
    """The owner and ledger entry frozen under one bundle digest, if real."""
    for owner in owners(d):
        for entry in bundles_for(d, owner):
            if isinstance(entry, dict) and str(entry.get("digest", "")) == digest:
                return owner, entry
    return None, None


def current_repo_head() -> str:
    """Enclosing checkout HEAD at this instant, or an explicit unknown."""
    try:
        out = subprocess.run(git_argv("rev-parse", "HEAD"), capture_output=True,
                             text=True, timeout=10, cwd=str(root()), env=git_env())
    except (OSError, subprocess.TimeoutExpired):
        return "unknown (git unavailable)"
    head = out.stdout.strip().splitlines()
    if out.returncode == 0 and head and head[0].strip():
        return head[0].strip()[:64]
    return "unknown (not a git checkout)"


def installation_state() -> str:
    """Where, if anywhere, this skill is installed. Read-only checks."""
    cands = [Path.home() / ".agents" / "skills" / "docket",
             Path.home() / ".claude" / "skills" / "docket"]
    found = [str(p) for p in cands if (p / "SKILL.md").is_file()]
    if found:
        return "installed at " + ", ".join(found)
    return f"not installed (using repo CLI at {LAUNCHER.parent})"


def release_dir(d: Path) -> Path:
    return d / ".release"


def resolve_release(d: Path, digest: str) -> tuple[Path | None, dict | None, list[str]]:
    """One frozen release by exact digest or unique prefix, or why not."""
    base = release_dir(d)
    found: list[tuple[Path, dict | None]] = []
    if base.is_dir():
        for path in sorted(base.glob("*/manifest.json")):
            if path.parent.name.startswith(".staging-"):
                continue
            try:
                manifest = json.loads(path.read_text())
            except (OSError, ValueError):
                manifest = None
            found.append((path.parent, manifest if isinstance(manifest, dict) else None))
    if digest:
        matched = [(p, m) for p, m in found
                   if p.name == digest or (m or {}).get("digest", "") == digest
                   or str((m or {}).get("digest", "")).startswith(digest)]
        if not matched:
            return None, None, [f"no frozen release {digest!r} in {d.name}; "
                                "run docket release RUN freeze"]
        if len(matched) > 1:
            return None, None, [f"release prefix {digest!r} is ambiguous; "
                                "pass a full digest"]
        return matched[0][0], matched[0][1], []
    if not found:
        return None, None, ["no frozen release; run docket release RUN freeze"]
    if len(found) > 1:
        return None, None, ["multiple frozen releases; pass --release DIGEST"]
    return found[0][0], found[0][1], []


def validate_frozen_release(d: Path, run: str, rel_dir: Path,
                            manifest: dict | None,
                            staged: bool = False) -> list[str]:
    """Why a frozen release cannot support a final packet.

    Recomputes every digest over the release's own inventory,
    revalidates frozen qualification
    copies, re-checks pinned bundles and integration outputs, and refuses
    recorded source revisions that moved since freezing. Frozen pins are the
    reviewed revisions; anything that moved needs a re-freeze, never a silent
    substitution. `staged` skips the directory-name address check for a
    staging directory that has not been renamed into place yet.
    """
    problems: list[str] = []
    if manifest is None:
        return ["release manifest is missing or unreadable"]
    problems.extend(f"release manifest: {item}" for item in check_frozen_digest(manifest))
    if problems:
        return problems
    digest = str(manifest.get("digest", ""))
    if not staged and rel_dir.name != digest.split(":")[-1][:12]:
        problems.append(f"frozen release directory {rel_dir.name!r} does not address "
                        f"the manifest digest {digest}; the address and its bytes "
                        "disagree (DAMAGED)")
        return problems
    if str(manifest.get("run", "")) != run:
        problems.append(f"frozen release is for run {manifest.get('run', '')!r}, "
                        f"not {run!r}")
        return problems
    # The complete inventory binds every name, entry kind, mode, symlink
    # target, and regular-file byte under the release. An undeclared file, a
    # removed entry, a changed mode, or a retargeted link is DAMAGED.
    declared = manifest.get("inventory", None)
    if not isinstance(declared, list) or not all(isinstance(item, dict)
                                                 for item in declared):
        return problems + ["release manifest carries no complete inventory; a "
                           "release that cannot be rebuilt is DAMAGED"]
    scanned, scan_problems = scan_content_tree(rel_dir)
    if scan_problems:
        return problems + [f"frozen release tree {item} (DAMAGED)"
                           for item in scan_problems]
    scanned = [item for item in scanned if str(item.get("rel", "")) != "manifest.json"]
    if scanned != declared:
        return problems + ["frozen release tree no longer matches the manifest "
                           "inventory it pins (DAMAGED): an entry is missing, added, "
                           "altered, retargeted, or mode-changed"]
    if str(manifest.get("inventory_revision", "")) != tree_revision(declared):
        return problems + ["frozen release inventory does not address the manifest "
                           "revision (DAMAGED)"]
    files = manifest.get("files", {})
    blobs: dict[str, bytes] = {}
    if not isinstance(files, dict) or not files:
        return problems + ["release manifest names no frozen files"]
    for rel, ref in files.items():
        target = rel_dir / str(rel)
        try:
            data = target.read_bytes()
        except OSError:
            problems.append(f"frozen release file {rel} is missing or unreadable")
            continue
        blobs[str(rel)] = data
        want = ref.get("sha256", "") if isinstance(ref, dict) else ""
        if digest_of(data) != want:
            problems.append(f"frozen release file {rel} is damaged (digest mismatch)")
    if problems:
        return problems
    for rel in sorted(name for name in blobs if name.startswith("qualifications/")):
        role = Path(rel).name[len("qualification-"):-len(".json")]
        problems.extend(f"frozen qualification {rel}: {item}" for item in
                        qualification_problems(d, run, role, artifact=rel_dir / rel))
    suite = manifest.get("suite", {})
    if not isinstance(suite, dict):
        problems.append("frozen release records no suite result")
    else:
        if suite.get("exit", 1) != 0 or suite.get("failures", 1) != 0 \
                or suite.get("ok", False) is not True:
            problems.append(f"frozen suite result is red (exit {suite.get('exit')}, "
                            f"failures {suite.get('failures')}); release needs a green suite")
        if not isinstance(suite.get("tests", 0), int) or suite.get("tests", 0) <= 0:
            problems.append("frozen suite result records no passing tests")
        try:
            frozen_stdout = blobs["suite/stdout.txt"]
        except KeyError:
            frozen_stdout = None
        try:
            frozen_stderr = blobs["suite/stderr.txt"]
        except KeyError:
            frozen_stderr = None
        if frozen_stdout is None:
            problems.append("frozen suite stdout is missing")
        elif frozen_stderr is None:
            problems.append("frozen suite stderr is missing")
        else:
            frozen_summary, frozen_stream_problem = parse_suite_streams(
                frozen_stdout.decode(errors="replace"),
                frozen_stderr.decode(errors="replace"))
            if frozen_summary is None:
                problems.append("frozen suite output holds no qualifiable summary: "
                                f"{frozen_stream_problem}")
            else:
                frozen_tests, frozen_failures, frozen_ok = frozen_summary
                if (frozen_tests != suite.get("tests")
                        or frozen_failures != suite.get("failures") or not frozen_ok):
                    problems.append(
                        f"frozen suite output reports {frozen_tests} tests, "
                        f"{frozen_failures} failures "
                        f"({'OK' if frozen_ok else 'FAILED'}) but the release records "
                        f"{suite.get('tests')} tests, {suite.get('failures')} failures; "
                        "refusing")
    agg_digest = str((manifest.get("aggregate", {}) or {}).get("digest", ""))
    owner, entry = find_bundle_by_digest(d, agg_digest) if agg_digest else (None, None)
    if entry is None or owner != "orch":
        problems.append(f"frozen aggregate {agg_digest or 'none'} resolves to no "
                        "frozen aggregate bundle in this run; never invent one")
    else:
        manifest_agg = load_bundle(d, "orch", entry)
        problems.extend(f"frozen aggregate: {item}" for item in
                        bundle_problems(d, "orch", entry))
        problems.extend(f"frozen aggregate: {item}" for item in
                        aggregate_bundle_problems(d, entry))
        problems.extend(f"frozen aggregate: {item}" for item in
                        integration_verification_problems(d, entry, manifest_agg))
    milestones = manifest.get("milestones", {})
    if not isinstance(milestones, dict):
        problems.append("frozen release records no milestone inventory")
    else:
        for milestone in RELEASE_MILESTONES:
            pinned = milestones.get(milestone, "")
            if not pinned or pinned == UNAVAILABLE_MILESTONE:
                continue
            resolver, _ = find_bundle_by_digest(d, str(pinned))
            if resolver is None:
                problems.append(f"frozen milestone {milestone} digest {str(pinned)[:19]}... "
                                "resolves to no frozen bundle in this run; never invent one")
    live_tree = worktree_identity()
    suite_source = str((manifest.get("suite", {}) or {}).get("source", ""))
    if not suite_source.startswith("sha256:"):
        problems.append(f"frozen suite source {suite_source!r} is unknown or malformed; "
                        "unknown trees are diagnostic only, never release evidence")
    qual_sources: dict[str, str] = {}
    for rel in sorted(name for name in blobs if name.startswith("qualifications/")):
        try:
            frozen_qual = json.loads(blobs[rel].decode())
        except ValueError:
            problems.append(f"frozen qualification {rel} is missing or unreadable")
            continue
        if not isinstance(frozen_qual, dict):
            problems.append(f"frozen qualification {rel} is malformed")
            continue
        qual_source = str(frozen_qual.get("source_tree", "") or "")
        qual_sources[rel] = qual_source
        if not qual_source.startswith("sha256:"):
            problems.append(f"frozen qualification {Path(rel).name} source "
                            f"{qual_source!r} is unknown or malformed; unknown trees "
                            "are diagnostic only, never release evidence")
    if not live_tree.startswith("sha256:"):
        problems.append(f"live source tree {live_tree!r} is unknown or malformed; "
                        "a final packet needs one exact sha256 content tree for the "
                        "live tree, the suite, and every qualification")
    else:
        stale_inputs = []
        if suite_source.startswith("sha256:") and suite_source != live_tree:
            stale_inputs.append(f"suite (frozen {suite_source[:19]}...)")
        for rel, qual_source in sorted(qual_sources.items()):
            if qual_source.startswith("sha256:") and qual_source != live_tree:
                stale_inputs.append(f"qualification {Path(rel).name} "
                                    f"(frozen {qual_source[:19]}...)")
        if stale_inputs:
            problems.append("release inputs are stale for the current source tree; "
                            "re-freeze: " + "; ".join(stale_inputs))
        pinned_sources = (([suite_source] if suite_source.startswith("sha256:") else [])
                          + [s for s in qual_sources.values() if s.startswith("sha256:")])
        if len(set(pinned_sources)) > 1:
            problems.append("frozen release pins different source trees for its suite "
                            "and qualifications; re-freeze against one exact tree")
    return problems


def copy_release_file(src: Path, dest: Path) -> None:
    """Copy one file into the release, preserving its mode."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def copy_release_tree(src: Path, dest: Path) -> None:
    """Copy one dependency tree whole, preserving modes and symlink targets.

    Names, entry kinds, modes, symlink targets, and bytes travel together, so
    a frozen fixture or workspace snapshot has exactly the tree semantics it
    had when it was validated.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, symlinks=True, copy_function=shutil.copy2)


def pinned_bundle_entries(d: Path, digests: list[str]) -> list[tuple[str, dict]]:
    """Every distinct pinned bundle as (owner, ledger entry), or why not.

    The release must carry each bundle the final packet or its validation
    resolves: the aggregate, every constituent, and every milestone digest.
    A pinned digest that resolves to nothing refuses the freeze instead of
    being dropped.
    """
    pinned: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for digest in digests:
        digest = str(digest or "")
        if not digest or digest in seen:
            continue
        owner, entry = find_bundle_by_digest(d, digest)
        if owner is None or entry is None:
            die(f"pinned bundle {digest[:19]}... resolves to no frozen bundle in "
                "this run; never invent or drop milestone evidence")
        seen.add(digest)
        pinned.append((owner, entry))
    return pinned


def stage_bundle_dependencies(staging: Path, d: Path,
                              pinned: list[tuple[str, dict]]) -> None:
    """Copy every pinned bundle and the object store it reconstructs from.

    The release carries each pinned round directory, a ledger filtered to
    exactly the pinned entries, and the run-private Git object store, so
    bundle and integration validation never reads mutable `.bundles` after
    freeze. Names, modes, symlinks, and bytes travel together.
    """
    owners_seen: dict[str, list[dict]] = {}
    for owner, entry in pinned:
        source = bundle_dir(d, owner, entry)
        if not source.is_dir() or source.is_symlink():
            die(f"pinned bundle {str(entry.get('digest', ''))[:19]}... for {owner} "
                "is missing; refusing to freeze evidence nobody can rebuild")
        relative = source.relative_to(owner_bundles(d, owner))
        copy_release_tree(source, staging / "bundles" / owner / str(relative))
        owners_seen.setdefault(owner, []).append(entry)
    for owner, entries in sorted(owners_seen.items()):
        publish_json(staging / "bundles" / owner / "rounds.json",
                     {"version": BUNDLE_VERSION, "entries": entries})
    objects = bundle_objects(d)
    if objects.is_dir():
        copy_release_tree(objects, staging / "bundles" / "objects")
    # The private reconstruction environment creates these directories on
    # demand; staging them here keeps the frozen release byte-stable when it
    # is validated again.
    (staging / "bundles" / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (staging / "bundles" / "objects" / "pack").mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def frozen_bundle_view(rel_dir: Path, manifest: dict):
    """Point bundle resolution at one release's own carried bundle store.

    Validation and packet rendering read only the release's ledger, round
    directories, and object store. Writable Git scratch is redirected outside
    the release, so validating frozen evidence never mutates it, and a missing
    store refuses instead of silently resolving live `.bundles`.
    """
    bundle_root = rel_dir / "bundles"
    bundles_meta = manifest.get("bundles", {})
    owner_list = [str(item) for item in
                  (bundles_meta.get("owners", []) if isinstance(bundles_meta, dict)
                   else [])]
    work = Path(tempfile.mkdtemp(prefix=".bundle-work-", dir=str(rel_dir.parent)))
    names = ("DOCKET_BUNDLES_ROOT", "DOCKET_BUNDLE_OWNERS", "DOCKET_BUNDLE_WORK")
    previous = {name: os.environ.get(name) for name in names}
    os.environ["DOCKET_BUNDLES_ROOT"] = str(bundle_root)
    os.environ["DOCKET_BUNDLE_OWNERS"] = ",".join(owner_list)
    os.environ["DOCKET_BUNDLE_WORK"] = str(work)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(work, ignore_errors=True)


def cmd_release(a: argparse.Namespace) -> None:
    """Freeze the exact inputs a final packet may present. One command, one digest."""
    d = need_run(a.run)
    if not a.freeze:
        die("only --freeze is supported: `docket release RUN --freeze ...`")
    qual_paths = sorted((d / ".delivery").glob("qualification-*.json")) \
        if (d / ".delivery").is_dir() else []
    if not qual_paths:
        die("cannot freeze a release with no delivery qualification artifact "
            "(run delivery --qualify)")
    for path in qual_paths:
        stem_role = path.name[len("qualification-"):-len(".json")]
        qual_problems = qualification_problems(d, a.run, stem_role)
        if qual_problems:
            die("cannot freeze a release on unqualified delivery: "
                + "; ".join(qual_problems))
    tree = worktree_identity()
    if not tree.startswith("sha256:"):
        die(f"cannot freeze a release from unknown live source tree {tree!r}; "
            "re-run against a git checkout with a valid sha256 identity so the "
            "frozen evidence binds to one exact content tree")
    for path in qual_paths:
        stem_role = path.name[len("qualification-"):-len(".json")]
        try:
            qual_content = json.loads(path.read_bytes())
        except (OSError, ValueError):
            die(f"delivery qualification artifact {path.name} changed underfoot; retry")
        qual_source = qual_content.get("source_tree", "") if isinstance(qual_content, dict) else ""
        if not (isinstance(qual_source, str) and qual_source.startswith("sha256:")):
            die(f"delivery qualification {stem_role} binds unknown or malformed source "
                f"{qual_source!r}; unknown trees are diagnostic only, never release "
                "evidence - re-qualify against the reviewed tree")
        if qual_source != tree:
            die(f"delivery qualification {stem_role} exercised source "
                f"{qual_source[:19]}... but the current source is {tree[:19]}...; "
                "source is stale, re-qualify against the reviewed tree")
    suite_path, suite_resolve_problems = resolve_suite_artifact(
        d, (a.suite_artifact or "").strip())
    if suite_path is None:
        die("; ".join(suite_resolve_problems))
    suite_validation = suite_problems(suite_path, a.run)
    if suite_validation:
        die("cannot freeze a release on an unqualified suite run: "
            + "; ".join(suite_validation))
    try:
        suite_record = json.loads(suite_path.read_bytes())
    except (OSError, ValueError):
        die(f"suite artifact {suite_path.name} changed underfoot; retry")
    if not isinstance(suite_record, dict):
        die(f"suite artifact {suite_path.name} changed underfoot; retry")
    if suite_record.get("exit") != 0 or suite_record.get("failures") != 0 \
            or suite_record.get("ok") is not True:
        die(f"cannot freeze a red suite as release evidence (exit "
            f"{suite_record.get('exit')}, failures {suite_record.get('failures')})")
    try:
        suite_stdout = (suite_path.parent / str(suite_record.get("stdout_file", ""))).read_bytes()
        suite_stderr = (suite_path.parent / str(suite_record.get("stderr_file", ""))).read_bytes()
    except OSError:
        die(f"suite artifact {suite_path.name} outputs changed underfoot; retry")
    suite_source = suite_record.get("source_after", "")
    suite_before = suite_record.get("source_before", "")
    if not (isinstance(suite_source, str) and suite_source.startswith("sha256:")
            and isinstance(suite_before, str) and suite_before.startswith("sha256:")):
        die(f"suite artifact {suite_path.name} binds unknown or malformed source "
            f"({suite_before!r} -> {suite_source!r}); unknown trees are diagnostic "
            "only, never release evidence - re-run against a git checkout with "
            "a valid sha256 identity")
    if suite_source != tree or suite_before != tree:
        die(f"suite artifact {suite_path.name} exercised source "
            f"{str(suite_source)[:19]}... but the current source is {tree[:19]}...; "
            "the suite result is stale for the reviewed tree, rerun it")
    try:
        orch_rnd, _ = state_of(d, "orch")
    except (OSError, ValueError):
        orch_rnd = 0
    orch_entry = latest_bundle(d, "orch", orch_rnd) if orch_rnd else None
    if orch_entry is None:
        die("cannot freeze a release with no aggregate bundle; submit the "
            "aggregate with its integration verification first")
    orch_manifest = load_bundle(d, "orch", orch_entry)
    for problems in (bundle_problems(d, "orch", orch_entry),
                     aggregate_bundle_problems(d, orch_entry),
                     packet_staleness(d, "orch", orch_rnd, orch_manifest),
                     integration_verification_problems(d, orch_entry, orch_manifest)):
        if problems:
            die("cannot freeze a release on evidence the packet would refuse: "
                + "; ".join(problems))
    milestones: dict[str, str] = {}
    for spec in a.milestone or []:
        name, sep, digest = str(spec).partition("=")
        if not sep or not MILESTONE_RE.match(name.strip()) or not SHA_DIGEST_RE.match(digest.strip()):
            die(f"invalid --milestone {spec!r}: use M=DIGEST with M in M4..M11 and "
                "a sha256 bundle digest")
        name, digest = name.strip(), digest.strip()
        resolver, _ = find_bundle_by_digest(d, digest)
        if resolver is None:
            die(f"milestone {name} digest {digest[:19]}... resolves to no frozen "
                "bundle in this run; never invent milestone evidence")
        milestones[name] = digest
    for milestone in RELEASE_MILESTONES:
        milestones.setdefault(milestone, UNAVAILABLE_MILESTONE)
    constituents = []
    for item in (orch_manifest.get("constituents", []) or []):
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner", ""))
        try:
            rnd = int(str(item.get("round", "0")))
        except ValueError:
            rnd = 0
        verifications = []
        for path in _ordered(d.glob(f"{owner}-verification-*.mdx")):
            try:
                if parse(path.read_text())[0].get("round", "") == str(rnd):
                    verifications.append(path.name)
            except (OSError, ValueError):
                continue
        verification = verifications[-1] if verifications else ""
        decision = f"{owner}-decision-{rnd:02d}.mdx" if (d / f"{owner}-decision-{rnd:02d}.mdx").is_file() else ""
        constituents.append({"owner": owner, "round": rnd,
                             "bundle": str(item.get("bundle", "")),
                             "verification": verification,
                             "verifications": verifications,
                             "decision": decision})
    constituent_digests = [str(item.get("bundle", "")) for item in constituents
                           if item.get("bundle")]
    milestone_digests = [str(digest) for digest in milestones.values()
                         if digest and digest != UNAVAILABLE_MILESTONE]
    pinned_bundles = pinned_bundle_entries(
        d, [str(orch_entry.get("digest", "")), *constituent_digests,
            *milestone_digests])
    staged_files: dict[str, bytes] = {
        "suite/artifact.json": suite_path.read_bytes(),
        "suite/stdout.txt": suite_stdout,
        "suite/stderr.txt": suite_stderr,
    }
    for path in qual_paths:
        staged_files[f"qualifications/{path.name}"] = path.read_bytes()
    try:
        staged_files["documents/plan.mdx"] = (d / "plan.mdx").read_bytes()
    except OSError:
        die("plan.mdx is missing or unreadable; cannot freeze release documents")
    for item in constituents:
        owner = str(item.get("owner", ""))
        if owner and owner != "orch":
            task_path = d / f"{owner}-task.mdx"
            if task_path.is_file():
                try:
                    staged_files[f"documents/tasks/{owner}-task.mdx"] = task_path.read_bytes()
                except OSError:
                    die(f"task contract {task_path.name} is unreadable; "
                        "cannot freeze release documents")
        verification_names = item.get("verifications", [])
        if not isinstance(verification_names, list):
            verification_names = []
        if not verification_names and item.get("verification"):
            verification_names = [item.get("verification")]
        document_names = [(str(name), "verifications") for name in verification_names]
        if item.get("decision"):
            document_names.append((str(item.get("decision")), "decisions"))
        for name, subdir in document_names:
            if not name:
                continue
            try:
                staged_files[f"documents/{subdir}/{name}"] = (d / name).read_bytes()
            except OSError:
                die(f"release document {name} is missing or unreadable; cannot "
                    "freeze what review would read live")
    open_incidents = [i for i in list_incidents(d) if i.get("state") == "open"]
    open_escalations = []
    if (d / ".escalations").is_dir():
        for path in sorted((d / ".escalations").glob("*.json")):
            try:
                entry = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(entry, dict) and entry.get("state") == "open":
                open_escalations.append(entry)
    pending_amendments = [entry for entry in list_amendments(d)
                          if entry.get("status") == "pending"]
    try:
        staged_files["snapshot/incidents.json"] = json.dumps(
            open_incidents, indent=2, sort_keys=True).encode() + b"\n"
        staged_files["snapshot/escalations.json"] = json.dumps(
            open_escalations, indent=2, sort_keys=True).encode() + b"\n"
        staged_files["snapshot/amendments.json"] = json.dumps(
            pending_amendments, indent=2, sort_keys=True).encode() + b"\n"
    except (TypeError, ValueError):
        die("run state snapshot is not serializable; cannot freeze release documents")
    head = current_repo_head()
    # The release is addressed by its content, so the recorded freeze time is
    # the suite's own bounded end time rather than the wall clock: identical
    # inputs re-freeze at one address and converge, while any content change
    # moves the address.
    frozen_at = str(suite_record.get("ended_at", "") or "") or stamp()
    manifest: dict[str, object] = {
        "run": a.run, "frozen_at": frozen_at,
        "aggregate": {"owner": "orch", "round": orch_rnd,
                      "digest": str(orch_entry.get("digest", ""))},
        "constituents": constituents,
        "qualifications": {path.name[len("qualification-"):-len(".json")]:
                           {"digest": digest_of(path.read_bytes())}
                           for path in qual_paths},
        "suite": {"artifact": suite_path.name,
                  "command": suite_record.get("command", ""),
                  "exit": suite_record.get("exit", 1),
                  "tests": suite_record.get("tests", 0),
                  "failures": suite_record.get("failures", 1),
                  "ok": suite_record.get("ok", False),
                  "duration_s": suite_record.get("duration_s", 0),
                  "started_at": suite_record.get("started_at", ""),
                  "ended_at": suite_record.get("ended_at", ""),
                  "cwd": suite_record.get("cwd", ""),
                  "source": suite_record.get("source_after", ""),
                  "stdout_digest": suite_record.get("stdout_digest", ""),
                  "stderr_digest": suite_record.get("stderr_digest", ""),
                  "artifact_digest": suite_record.get("digest", "")},
        "milestones": milestones,
        "repo_head": head,
        "bundles": {"owners": sorted({owner for owner, _ in pinned_bundles}),
                    "digests": [str(entry.get("digest", ""))
                                for _, entry in pinned_bundles]},
        "files": {},
    }
    release_dir(d).mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=str(release_dir(d))))
    try:
        fault("release:stage")
        for rel, data in staged_files.items():
            fault(f"release:write:{rel}")
            publish_bytes(staging / rel, data)
        stage_bundle_dependencies(staging, d, pinned_bundles)
        inventory, inventory_problems = scan_content_tree(staging)
        if inventory_problems:
            die("staged release tree is unusable: " + "; ".join(inventory_problems))
        files = {str(entry["rel"]): {
                     "sha256": str(entry.get("digest", "")),
                     "bytes": (staging / str(entry["rel"])).stat().st_size}
                 for entry in inventory if str(entry.get("kind", "")) == "file"}
        manifest["files"] = files
        manifest["inventory"] = inventory
        manifest["inventory_revision"] = tree_revision(inventory)
        manifest = freeze_record(manifest)
        short = str(manifest["digest"]).split(":")[1][:12]
        fault("release:write:manifest.json")
        publish_json(staging / "manifest.json", manifest)
        with frozen_bundle_view(staging, manifest):
            staged_problems = validate_frozen_release(d, a.run, staging, manifest,
                                                      staged=True)
        if staged_problems:
            die("cannot freeze a release whose staged bytes do not validate: "
                + "; ".join(staged_problems))
        final = release_dir(d) / short
        if final.is_dir():
            try:
                existing = json.loads((final / "manifest.json").read_text())
            except (OSError, ValueError):
                existing = None
            if not isinstance(existing, dict) or \
                    str(existing.get("digest", "")) != str(manifest["digest"]):
                die(f"release address {short} already holds different content; "
                    "immutable evidence is never overwritten")
            with frozen_bundle_view(final, existing):
                published_problems = validate_frozen_release(d, a.run, final, existing)
            if published_problems:
                die(f"published release {existing.get('digest', '')} is unusable "
                    f"({published_problems[0]}); immutable evidence is never "
                    "overwritten")
            published_inventory, published_scan_problems = scan_content_tree(final)
            published_inventory = [item for item in published_inventory
                                   if str(item.get("rel", "")) != "manifest.json"]
            if published_scan_problems or published_inventory != inventory:
                die(f"published release {short} does not match the staged bytes; "
                    "immutable evidence is never overwritten")
            shutil.rmtree(staging, ignore_errors=True)
            print(f"release {manifest['digest']} already frozen")
            return
        fault("release:rename")
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    fault("release:renamed")
    print(f"froze release {manifest['digest']} "
          f"(aggregate {manifest['aggregate']['digest'][:19]}...)")
    print(f"evidence: {final}")
