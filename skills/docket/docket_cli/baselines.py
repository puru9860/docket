"""Baselines captured before work, and the change evidence measured against them."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shlex
from pathlib import Path

from .common import (
    COVERAGE_AVAILABLE, COVERAGE_UNAVAILABLE, DOCUMENTS_ONLY, EMPTY_TREE_SHA1,
    EVIDENCE_MODES, ROOTS_DECLARED, UNTRACKED_CONTENT_LIMIT, die,
)
from .frontmatter import parse
from .paths import read_dispatch, reports
from .publication import publish_bytes, publish_json
from .evidence import (
    dirty_paths, empty_tree, file_fingerprint, git_raw, git_text, index_entries,
    is_state_path, path_state, porcelain_records, state_prefix,
)
from .roots import read_roots, resolve_scope, root_identity


def snapshot_path(d: Path, owner: str) -> Path:
    return d / ".snapshots" / f"{owner}.json"


def snapshot_dir(d: Path, owner: str) -> Path:
    return d / ".snapshots" / owner


def capture_untracked(repo: Path, relative: str) -> dict[str, object]:
    """Enough of one untracked entry to put it back: mode, symlink target, content.

    A baseline is captured in full or it is not captured at all, so an entry that
    cannot be stored whole is returned as an error rather than as truncated
    content. A truncated capture that still reported coverage as available was
    the thing that made a partial baseline indistinguishable from a real one.
    """
    path = repo / relative
    try:
        if path.is_symlink():
            return {"kind": "symlink", "target": os.readlink(path)}
        if not path.is_file():
            return {"kind": "absent"}
        stat_result = path.stat()
        size = stat_result.st_size
        kind = "executable" if stat_result.st_mode & 0o111 else "file"
        if size > UNTRACKED_CONTENT_LIMIT:
            return {
                "kind": kind,
                "size": size,
                "error": f"untracked file {relative} is {size} bytes, over the "
                         f"{UNTRACKED_CONTENT_LIMIT} byte baseline capture limit",
            }
        return {
            "kind": kind,
            "size": size,
            "content_base64": base64.b64encode(path.read_bytes()).decode(),
        }
    except OSError as exc:
        return {"kind": "unreadable", "error": f"untracked file {relative} cannot be read: {exc}"}


def discard_capture(d: Path, owner: str) -> None:
    """Delete a half-captured baseline's evidence. Nothing may point at a partial one."""
    into = snapshot_dir(d, owner)
    if not into.is_dir():
        return
    for entry in into.iterdir():
        with contextlib.suppress(OSError):
            entry.unlink()
    with contextlib.suppress(OSError):
        into.rmdir()


def capture_root_baseline(d: Path, owner: str, record: dict[str, str]) -> dict[str, object]:
    """Reconstructable initial state of one declared root, without touching user work.

    Nothing here commits, stashes, or writes into the checkout. Branch and HEAD are
    re-read at the instant of capture and the pinned base tree is that same HEAD, so
    a commit made between declaration and assignment cannot leave the baseline's
    metadata describing one state and its patches another. The capture either
    records everything it needs, or it records why it could not.
    """
    path = Path(record.get("path", ""))
    alias = record.get("alias", "")
    captured: dict[str, object] = {
        "alias": alias,
        "path": str(path),
        "git_dir": record.get("git_dir", ""),
        "common_dir": record.get("common_dir", ""),
        "declared_branch": record.get("branch", ""),
        "declared_head": record.get("head", ""),
        "branch": "",
        "head": "",
        "status": COVERAGE_UNAVAILABLE,
        "reason": "",
    }
    if not path.is_dir():
        captured["reason"] = f"the declared root {path} is missing"
        return captured
    identity = root_identity(path)
    if not identity:
        captured["reason"] = f"Git cannot identify the declared root {path}"
        return captured
    if any(identity[key] != record.get(key, "") for key in ("path", "git_dir", "common_dir")):
        captured["reason"] = (
            f"the declared root {alias} at {path} no longer names the checkout it was "
            "declared as"
        )
        return captured
    captured["branch"] = identity["branch"]
    captured["head"] = identity["head"]
    records = porcelain_records(path)
    if records is None:
        captured["reason"] = f"Git cannot report changes in {path}"
        return captured
    index = index_entries(path)
    if index is None:
        captured["reason"] = f"Git cannot read the index in {path}"
        return captured
    base_tree = identity["head"] or empty_tree(path)
    staged = git_raw(
        path, "diff", "--cached", "--binary", "--no-color", "--no-ext-diff", "--no-textconv",
        "--no-renames", base_tree,
    )
    unstaged = git_raw(
        path, "diff", "--binary", "--no-color", "--no-ext-diff", "--no-textconv",
        "--no-renames",
    )
    if staged is None or unstaged is None:
        captured["reason"] = f"Git cannot render the baseline difference in {path}"
        return captured
    untracked: dict[str, object] = {}
    embedded: list[str] = []
    observed: dict[str, str] = {}
    for status, relative in records:
        observed.setdefault(relative, status)
        if status.strip() != "??":
            continue
        if relative.endswith("/"):
            embedded.append(relative.rstrip("/"))
            continue
        untracked[relative] = capture_untracked(path, relative)
    unstorable = sorted(
        str(entry["error"]) for entry in untracked.values()
        if isinstance(entry, dict) and entry.get("error")
    )
    if unstorable:
        captured["reason"] = f"{unstorable[0]}; a baseline is captured in full or not at all"
        return captured
    try:
        states = {
            relative: path_state(path, relative, status, index)
            for relative, status in sorted(observed.items())
        }
    except OSError as exc:
        captured["reason"] = f"a path under {path} could not be read: {exc}"
        return captured
    into = snapshot_dir(d, owner)
    publish_bytes(into / f"{alias}.staged.patch", staged)
    publish_bytes(into / f"{alias}.worktree.patch", unstaged)
    publish_json(into / f"{alias}.untracked.json", {"entries": untracked, "embedded": embedded})
    captured.update({
        "status": "recorded",
        "unborn": not identity["head"],
        "base_tree": base_tree,
        "staged_patch": f"{alias}.staged.patch",
        "worktree_patch": f"{alias}.worktree.patch",
        "untracked_manifest": f"{alias}.untracked.json",
        "embedded_checkouts": embedded,
        "states": states,
        "reconstructable": "full",
    })
    return captured


def snapshot_assignment(
    d: Path, owner: str, scope: list[str], kind: str = "task"
) -> dict[str, object]:
    """Capture one immutable baseline, or return why it could not be captured.

    An existing baseline is never recaptured, and an incomplete one is never
    published: a snapshot that exists on disk is a complete baseline, so nothing
    downstream has to ask whether the evidence beneath it is whole.
    """
    target = snapshot_path(d, owner)
    if target.is_file():
        try:
            return json.loads(target.read_text())
        except (OSError, ValueError) as exc:
            return {"baseline": COVERAGE_UNAVAILABLE,
                    "reason": f"unreadable assignment snapshot {target.name}: {exc}"}
    mode = evidence_mode(d)
    data: dict[str, object] = {"kind": kind, "evidence_mode": mode, "scope": scope, "roots": []}
    if mode != "git":
        data["baseline"] = DOCUMENTS_ONLY
        publish_json(target, data)
        return data
    state, roots, reason = read_roots(d)
    if state != ROOTS_DECLARED:
        data["baseline"] = COVERAGE_UNAVAILABLE
        data["reason"] = reason
        return data
    captured = [capture_root_baseline(d, owner, record) for record in roots]
    data["roots"] = captured
    failed = [
        str(record.get("reason") or f"the baseline for {record.get('alias', '')} failed")
        for record in captured if record.get("status") != "recorded"
    ]
    if failed:
        data["baseline"] = COVERAGE_UNAVAILABLE
        data["reason"] = "; ".join(failed)
        discard_capture(d, owner)
        return data
    data["baseline"] = "recorded"
    publish_json(target, data)
    return data


def ensure_run_baseline(d: Path) -> dict[str, object]:
    """The run baseline is taken before any implementor edit, not at aggregate time."""
    return snapshot_assignment(d, "run", [], kind="run")


def require_baseline(d: Path, owner: str, scope: list[str], kind: str = "task") -> None:
    """Capture a baseline before dispatch, or refuse to dispatch at all.

    A `git` run that cannot record a complete baseline has no reviewable change
    surface, so the task and its report are never created in the first place.
    """
    data = snapshot_assignment(d, owner, scope, kind)
    if str(data.get("baseline", "")) in ("recorded", DOCUMENTS_ONLY):
        return
    die(
        f"cannot capture a complete {kind} baseline for {owner}: "
        f"{data.get('reason') or 'the declared checkout roots could not be read'}\n"
        "  Declare this run's checkouts with `docket roots <run> --declare alias=path`,\n"
        "  repair the workspace, or declare `evidence_mode: documents-only` in plan.mdx\n"
        "  for a run with no Git evidence. Nothing was dispatched."
    )


def task_scope_hint(d: Path, owner: str) -> list[str]:
    """The paths a task was assigned with: accepted files, else its file hints."""
    task = d / f"{owner}-task.mdx"
    try:
        meta = parse(task.read_text())[0]
    except (OSError, ValueError):
        return []
    try:
        return shlex.split(str(meta.get("files", "") or meta.get("file_hints", "") or ""))
    except ValueError:
        return []


def update_snapshot_scope(d: Path, owner: str, scope: list[str]) -> None:
    """Expand/change claimed paths without rebasing away implementation changes."""
    target = snapshot_path(d, owner)
    if not target.is_file():
        snapshot_assignment(d, owner, scope)
        return
    data = json.loads(target.read_text())
    data["scope"] = scope
    publish_json(target, data)


def evidence_mode(d: Path) -> str:
    """The run's declared evidence mode. Unrecognized values are returned verbatim."""
    plan = d / "plan.mdx"
    if not plan.is_file():
        return "git"
    return parse(plan.read_text())[0].get("evidence_mode", "").strip() or "git"


def baseline_states(record: dict[str, object]) -> dict[str, dict[str, str]]:
    """Per-path baseline identity, reading an older content-only snapshot as content."""
    states = record.get("states")
    if isinstance(states, dict):
        return {str(key): dict(value) for key, value in states.items() if isinstance(value, dict)}
    return {
        str(key): {"content": str(value or "")}
        for key, value in dict(record.get("dirty", {})).items()
    }


def root_changes(record: dict[str, object]) -> tuple[list[str], str]:
    """Paths that differ from one captured root baseline, or why coverage is unavailable.

    A path is compared on content, worktree mode, index entry, and porcelain state,
    so a mode-only or index-only change layered on a path that was already dirty at
    assignment cannot vanish behind an unchanged content hash. Edits committed
    during the task count too: current dirty status alone misses them, which used
    to present as an empty diff.
    """
    path = Path(str(record.get("path", "")))
    if record.get("status") != "recorded":
        return [], str(record.get("reason") or f"no baseline was captured for {path}")
    if str(record.get("reconstructable", "full")) != "full":
        return [], f"the baseline captured for {path} is incomplete"
    if not path.is_dir():
        return [], f"the declared root {path} is missing"
    records = porcelain_records(path)
    if records is None:
        return [], f"Git cannot report changes in {path}"
    index = index_entries(path)
    if index is None:
        return [], f"Git cannot read the index in {path}"
    observed: dict[str, str] = {}
    for status, relative in records:
        observed.setdefault(relative, status)
    baseline = baseline_states(record)
    changed: set[str] = set()
    try:
        for relative in set(baseline) | set(observed):
            before = baseline.get(relative)
            now = path_state(path, relative, observed.get(relative, ""), index)
            if not before or any(
                str(before.get(key) or "") != now[key] for key in now if key in before
            ):
                changed.add(relative)
    except OSError as exc:
        return [], f"a path under {path} could not be read: {exc}"
    pinned = str(record.get("base_tree") or EMPTY_TREE_SHA1)
    now_head = git_text(path, "rev-parse", "HEAD", timeout=10) or empty_tree(path)
    if now_head != pinned:
        committed = git_text(path, "diff", "--name-only", "--no-renames", "-z", pinned, now_head)
        if committed is None:
            return [], f"Git cannot compare the pinned baseline {pinned[:12]} in {path}"
        state = state_prefix(path)
        changed |= {
            relative for relative in committed.split("\0")
            if relative and not is_state_path(state, relative)
        }
    return sorted(changed), ""


def legacy_evidence(data: dict, snap: Path) -> tuple[str, str, list[str], list[str]]:
    """Coverage for a single-root snapshot written before roots were declared."""
    repo_text = str(data.get("git_root", "")).strip()
    if not repo_text:
        return COVERAGE_UNAVAILABLE, "no Git root was resolved when the task baseline was recorded", [], []
    if str(data.get("baseline", "recorded")) != "recorded":
        return COVERAGE_UNAVAILABLE, f"Git recorded no assignment baseline in {repo_text}", [], []
    repo = Path(repo_text)
    if not repo.is_dir():
        return COVERAGE_UNAVAILABLE, f"the recorded Git root {repo_text} is missing", [], []
    current = dirty_paths(repo)
    if current is None:
        return COVERAGE_UNAVAILABLE, f"Git cannot report changes in {repo_text}", [], []
    baseline = dict(data.get("dirty", {}))
    try:
        changed = sorted(
            path for path in set(baseline) | current
            if file_fingerprint(repo, path) != baseline.get(path)
        )
    except OSError as exc:
        return COVERAGE_UNAVAILABLE, f"a task-local path could not be read: {exc}", [], []
    scope = [str(item).strip("`/") for item in data.get("scope", []) if str(item).strip("`/")]

    def allowed(path: str) -> bool:
        return any(path == item or path.startswith(item + "/") for item in scope)

    return COVERAGE_AVAILABLE, f"Git root {repo_text}", changed, [p for p in changed if not allowed(p)]


def assignment_evidence(d: Path, owner: str) -> tuple[str, str, list[str], list[str]]:
    """Diff coverage state, why, task-local changed paths, and paths outside scope.

    Coverage is always explicit. Unavailable Git evidence is never returned as an
    empty change list, because a reviewer cannot tell that apart from clean work.
    Changed paths are root-qualified whenever a run declares more than one root.
    """
    mode = evidence_mode(d)
    if mode == DOCUMENTS_ONLY:
        return DOCUMENTS_ONLY, "the run declares evidence_mode: documents-only", [], []
    if mode not in EVIDENCE_MODES:
        return COVERAGE_UNAVAILABLE, f"plan.mdx declares an unknown evidence_mode: {mode}", [], []
    snap = snapshot_path(d, owner)
    if not snap.is_file():
        if owner != "orch" and not read_dispatch(d, owner):
            return (COVERAGE_UNAVAILABLE, f"{owner}'s baseline is captured when it is "
                    "dispatched, so work finished before then is its starting point", [], [])
        return COVERAGE_UNAVAILABLE, f"no assignment snapshot at {snap.name}", [], []
    try:
        data = json.loads(snap.read_text())
    except (OSError, ValueError) as exc:
        return COVERAGE_UNAVAILABLE, f"unreadable assignment snapshot {snap.name}: {exc}", [], []
    if "roots" not in data:
        return legacy_evidence(data, snap)
    if str(data.get("baseline", "recorded")) != "recorded":
        reason = str(data.get("reason") or f"no complete baseline was captured for {owner}")
        return COVERAGE_UNAVAILABLE, reason, [], []
    records = [record for record in data.get("roots", []) if isinstance(record, dict)]
    if not records:
        reason = str(data.get("reason") or "no checkout root was declared for this run")
        return COVERAGE_UNAVAILABLE, reason, [], []

    tokens = [str(item).strip("`") for item in data.get("scope", []) if str(item).strip("`")]
    scope, problems = resolve_scope(records, tokens)
    unrestricted = data.get("kind") == "run"
    if problems and not unrestricted:
        return COVERAGE_UNAVAILABLE, "; ".join(problems), [], []

    qualified: list[str] = []
    for record in records:
        paths, reason = root_changes(record)
        if reason:
            return COVERAGE_UNAVAILABLE, reason, [], []
        qualified.extend(f"{record.get('alias', '')}:{relative}" for relative in paths)
    qualified.sort()

    single = len(records) == 1

    def shown(value: str) -> str:
        return value.split(":", 1)[1] if single else value

    def allowed(value: str) -> bool:
        if unrestricted:
            return True
        # A whole-root claim is `alias:`, which admits that root's paths and no other.
        return any(
            value == item
            or (item.endswith(":") and value.startswith(item))
            or value.startswith(item + "/")
            for item in scope
        )

    detail = f"{len(records)} declared root(s): " + ", ".join(
        str(record.get("alias", "")) for record in records
    )
    return (
        COVERAGE_AVAILABLE,
        detail,
        [shown(value) for value in qualified],
        [shown(value) for value in qualified if not allowed(value)],
    )


def baseline_path(d: Path, owner: str) -> Path:
    return d / ".baselines" / f"{owner}.json"


def evidence_digest(report_body: str) -> str:
    """Identity of a submitted report body, recorded in the decision artifact."""
    return "sha256:" + hashlib.sha256(report_body.encode()).hexdigest()


def verify_output_bytes(verification: dict, stream: str) -> bytes:
    """A captured stream's exact bytes, or its text for a record from an older build."""
    raw = verification.get(f"{stream}_bytes")
    return raw if isinstance(raw, bytes) else str(verification.get(f"{stream}_text", "")).encode()


def output_fingerprint(stdout: str, stderr: str) -> str:
    return hashlib.sha256((stdout + "\n" + stderr).encode()).hexdigest()


def task_verify(d: Path, owner: str) -> tuple[str, int]:
    if owner == "orch":
        # The aggregate has no task file; its integration verification command
        # is declared in the report frontmatter at assignment.
        reps = reports(d, owner)
        if not reps:
            return "", 900
        try:
            meta, _ = parse(reps[-1].read_text())
        except (OSError, ValueError):
            return "", 900
        try:
            timeout = int(str(meta.get("verify_timeout", "900") or "900"))
        except ValueError:
            timeout = 900
        return (meta.get("verify", "") or "").strip(), timeout
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return "", 900
    meta, _ = parse(task.read_text())
    try:
        timeout = int(meta.get("verify_timeout", "900"))
    except ValueError:
        timeout = 900
    return meta.get("verify", "").strip(), timeout
