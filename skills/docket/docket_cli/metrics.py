"""Run metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from .frontmatter import parse
from .paths import _ordered, latest, owners, root
from .policy import need_run
from .bundles import digest_of
from .state import state_of
from .five_role import correction_attempts
from .qualification import _content_record, qualification_problems
from .prompts import prompts_dir


def scan_content_tree(root: Path) -> tuple[list[dict[str, object]], list[str]]:
    """Every entry under one directory tree, or why the tree is unusable.

    Nothing is skipped. Dotfiles, hidden directories, and everything beneath
    them are listed exactly like any other entry, because a name starting with
    a dot is a naming convention, not a permission to stay out of an
    inventory. Symlinks are recorded by their target and never followed, so a
    link cannot smuggle content in from elsewhere or make the walk escape the
    root. Sockets, fifos, and devices are refused explicitly rather than
    quietly skipped: an entry nobody can hash is an entry nobody can pin.
    Returns (entries, problems) with entries sorted by relative path; each
    carries its kind, mode, and either a content digest or a link target.
    """
    if not root.is_dir() or root.is_symlink():
        return [], [f"{root.name!r} is missing, unreadable, or a symlink"]
    entries: list[dict[str, object]] = []
    problems: list[str] = []

    def walk(current: Path) -> None:
        try:
            children = sorted(os.scandir(current), key=lambda item: item.name)
        except OSError:
            problems.append(f"{current.relative_to(root).as_posix()!r} is unreadable")
            return
        for child in children:
            path = Path(child.path)
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                problems.append(f"{str(path)!r} escapes its root; refusing")
                continue
            try:
                info = child.stat(follow_symlinks=False)
            except OSError:
                problems.append(f"{rel!r} is unreadable")
                continue
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                try:
                    target = os.readlink(path)
                except OSError:
                    problems.append(f"{rel!r} is an unreadable symlink")
                    continue
                entries.append({"rel": rel, "kind": "symlink", "mode": mode,
                                "target": target})
            elif stat.S_ISDIR(info.st_mode):
                entries.append({"rel": rel, "kind": "dir", "mode": mode})
                walk(path)
            elif stat.S_ISREG(info.st_mode):
                try:
                    data = path.read_bytes()
                except OSError:
                    problems.append(f"{rel!r} is unreadable")
                    continue
                entries.append({"rel": rel, "kind": "file", "mode": mode,
                                "digest": digest_of(data)})
            else:
                problems.append(f"{rel!r} is an unsupported entry type (not a "
                                "regular file, directory, or symlink); refusing")

    walk(root)
    entries.sort(key=lambda item: str(item["rel"]))
    return entries, problems


def tree_revision(entries: list[dict[str, object]]) -> str:
    """Content identity over a scanned tree: names, types, modes, and bytes.

    A mode-only change, a retargeted symlink, and a byte edit all move this,
    so a revision cannot claim to pin a tree it only partly describes.
    """
    if not entries:
        return ""
    digest = hashlib.sha256()
    for entry in entries:
        kind = str(entry.get("kind", ""))
        if kind == "file":
            payload = str(entry.get("digest", "")).encode()
        elif kind == "symlink":
            payload = os.fsencode(str(entry.get("target", "")))
        else:
            payload = b""
        digest.update(_content_record(kind.encode(), str(entry["rel"]).encode(),
                                      int(entry.get("mode", 0) or 0), payload))
    return "sha256:" + digest.hexdigest()


def run_artifact_span(d: Path) -> tuple[float, float] | None:
    """Bounded wall-time proxy: first to last artifact write inside the run."""
    earliest: float | None = None
    latest: float | None = None
    for path in d.rglob("*"):
        try:
            if not path.is_file():
                continue
            mt = path.stat().st_mtime
        except OSError:
            continue
        if earliest is None or mt < earliest:
            earliest = mt
        if latest is None or mt > latest:
            latest = mt
    if earliest is None or latest is None:
        return None
    return (earliest, latest)


def cmd_metrics(a: argparse.Namespace) -> None:
    """Measure a run from artifacts: approval, effort, and delivery.

    Every label names its source. Approval counts reviewer decisions.
    Prompt renders are an invocation proxy, never proven model calls.
    Wall time is the first-to-last artifact span.
    """
    d = need_run(a.run)
    decided = approved = waived = rounds = 0
    first_round_approvals = 0
    for owner in owners(d):
        if owner == "orch":
            continue
        rnd, st = state_of(d, owner)
        rounds += max(rnd, 0)
        if st in ("approved", "completed", "waived"):
            decided += 1
        if st in ("approved", "completed"):
            approved += 1
        if st == "waived":
            waived += 1
        decs = _ordered(d.glob(f"{owner}-decision-*.mdx"))
        if len(decs) == 1:
            try:
                if parse(decs[0].read_text())[0].get("verdict") == "approved":
                    first_round_approvals += 1
            except (OSError, ValueError):
                pass
    corrections = 0
    if (d / ".corrections").is_dir():
        for path in (d / ".corrections").glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            corrections += correction_attempts(record if isinstance(record, dict) else {})
    prompt_chars = prompt_renders = 0
    prompt_by_role: dict[str, int] = {}
    if prompts_dir(d).is_dir():
        for path in prompts_dir(d).glob("*.json"):
            prompt_renders += 1
            try:
                data = json.loads(path.read_text())
                prompt_chars += len(str(data.get("prompt", "")))
                role = str(data.get("role", ""))
                if role:
                    prompt_by_role[role] = prompt_by_role.get(role, 0) + 1
            except (OSError, ValueError):
                continue
    delayed = duplicated = receipts = 0
    for log in sorted((d / ".delivery").glob("*/log.jsonl")) if (d / ".delivery").is_dir() else []:
        try:
            lines = log.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
                kind = entry.get("kind", "")
            except ValueError:
                continue
            if kind in ("expired", "retry"):
                delayed += 1
            if kind == "receipt":
                receipts += 1
            if kind == "duplicate-receipt":
                duplicated += 1
            if kind == "sent" and entry.get("status") == "already-held":
                duplicated += 1
    span = run_artifact_span(d)
    print(f"metrics for run {a.run} (from artifacts; labels name their sources)")
    print(f"  tasks decided: {decided} (approved/completed {approved}, waived {waived})")
    if decided:
        print(f"  approval rate: {approved}/{decided} approved "
              "(reviewer decisions; not observed correctness)")
    else:
        print("  approval rate: unknown (no decided tasks)")
    print(f"  first-round approvals: {first_round_approvals}")
    print(f"  report rounds frozen: {rounds}")
    print(f"  review rounds: {rounds} (one round per report submission)")
    print(f"  local correction attempts: {corrections}")
    print(f"  decisions recorded: {len(list(d.glob('*-decision-*.mdx')))}")
    print(f"  verifier findings: {len(list(d.glob('*-verification-*.mdx')))}")
    print(f"  prompt renders: {prompt_renders} ({prompt_chars} chars)")
    for role in sorted(prompt_by_role):
        print(f"    {role} prompt renders: {prompt_by_role[role]} "
              "(invocation proxy; not proven model calls)")
    print(f"  delayed or redelivered notifications: {delayed}")
    print(f"  normal receipts: {receipts}")
    print(f"  duplicate notifications: {duplicated} (actual duplicates only)")
    if span is not None:
        print(f"  wall time: {round(span[1] - span[0])}s "
              "(first to last run artifact; proxy, not measured execution)")
    else:
        print("  wall time: unknown (no run artifacts)")
    qual_paths = sorted((d / ".delivery").glob("qualification-*.json")) \
        if (d / ".delivery").is_dir() else []
    if qual_paths:
        for path in qual_paths:
            stem_role = path.name[len("qualification-"):-len(".json")]
            try:
                content = json.loads(path.read_text())
            except (OSError, ValueError):
                content = {}
            stored = content.get("status", "?") if isinstance(content, dict) else "?"
            mode = content.get("mode", "?") if isinstance(content, dict) else "?"
            validation = qualification_problems(d, a.run, stem_role)
            verdict = "validated" if not validation else "invalid: " + validation[0]
            print(f"  delivery qualification {stem_role}: "
                  f"{stored} (mode {mode}; {verdict})")
    else:
        print("  delivery qualification: none recorded (run delivery --qualify)")
