"""Run metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from .frontmatter import parse
from .paths import _ordered, latest, owners, read_dispatch, root
from .policy import need_run, run_policy
from .bundles import digest_of
from .state import state_of, verification_paths
from .five_role import correction_attempts
from .qualification import _content_record, qualification_problems
from .prompts import prompts_dir
from .feedback import feedback_dir
from .sessions import collect_run_usage, tokens_h


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
    # Applied decisions and verification findings survive approval, while the
    # active .corrections budget record is deliberately cleared. Count each
    # charged return from those durable artifacts and use the active record
    # only when a return has been charged before its decision was published.
    corrections_by_owner: dict[str, int] = {}
    verifier_decisions: set[str] = set()
    for decision in d.glob("*-decision-*.mdx"):
        try:
            meta = parse(decision.read_text())[0]
        except (OSError, ValueError):
            continue
        if meta.get("verdict") != "changes-requested" or meta.get("applied") != "yes" \
                or meta.get("requirement_amendment"):
            continue
        owner = str(meta.get("owner", ""))
        if owner and owner != "orch":
            corrections_by_owner[owner] = corrections_by_owner.get(owner, 0) + 1
        if meta.get("verification"):
            verifier_decisions.add(str(meta["verification"]))
    for verification in d.glob("*-verification-*.mdx"):
        try:
            meta = parse(verification.read_text())[0]
        except (OSError, ValueError):
            continue
        owner = str(meta.get("owner", ""))
        if owner and verification.name not in verifier_decisions \
                and meta.get("opened_correction") in ("requested", "yes", "escalated"):
            corrections_by_owner[owner] = corrections_by_owner.get(owner, 0) + 1
    if (d / ".corrections").is_dir():
        for path in (d / ".corrections").glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                owner = str(record.get("owner", path.stem))
                corrections_by_owner[owner] = max(corrections_by_owner.get(owner, 0),
                                                  correction_attempts(record))
    corrections = sum(corrections_by_owner.values())
    escaped = []
    for path in sorted(feedback_dir(d).glob("*.mdx")):
        try:
            meta, _ = parse(path.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("category") == "escaped-defect":
            escaped.append((str(meta.get("task", "-")), str(meta.get("round", "-"))))
    recoveries = 0
    for owner in owners(d):
        dispatch = read_dispatch(d, owner)
        history = dispatch.get("model_history", []) if isinstance(dispatch, dict) else []
        if isinstance(history, list):
            recoveries += sum(1 for entry in history
                              if isinstance(entry, dict) and entry.get("kind") == "resume")
    usage = collect_run_usage(d, archive=False)
    observed = [row for row in usage if row.get("found")]
    expected_roles = set(run_policy(d)["role_sessions"].replace(" ", "").split(","))
    observed_roles = {role for row in observed for role in row.get("roles", [])}
    usage_complete = (bool(usage) and len(observed) == len(usage)
                      and expected_roles <= observed_roles)
    token_fields = ("input", "cache_read", "cache_write", "output", "total")
    tokens = {field: sum(int(row.get(field) or 0) for row in observed)
              for field in token_fields}
    reviewer_returns_after_checker_pass = 0
    for decision in d.glob("*-decision-*.mdx"):
        try:
            meta = parse(decision.read_text())[0]
            if meta.get("verdict") != "changes-requested" or meta.get("applied") != "yes":
                continue
            owner = str(meta.get("owner", ""))
            rnd = int(meta.get("round", 0) or 0)
            digest = str(meta.get("bundle_digest", ""))
            if not owner or not rnd or not digest:
                continue
            findings = [parse(path.read_text())[0]
                        for path in verification_paths(d, owner, rnd)]
            matching = [finding for finding in findings
                        if finding.get("bundle_digest") == digest]
            if matching and matching[-1].get("result") == "pass":
                reviewer_returns_after_checker_pass += 1
        except (OSError, ValueError):
            continue
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
    print(f"  reviewer returns after checker pass: {reviewer_returns_after_checker_pass} "
          "(applied changes decisions with a linked passing verification)")
    print(f"  report rounds frozen: {rounds}")
    print(f"  review rounds: {rounds} (one round per report submission)")
    print(f"  local correction attempts: {corrections}")
    print(f"  recovery resumes recorded: {recoveries} (current dispatch histories)")
    print(f"  escaped defects reported after acceptance: {len(escaped)} "
          "(feedback records; unreported defects unknown)")
    if escaped:
        print("    affected accepted rounds: "
              + ", ".join(f"{owner}:{rnd}" for owner, rnd in escaped))
    if observed:
        print(f"  measured tokens: {tokens_h(tokens['total'])} total "
              f"({tokens_h(tokens['input'])} other input, "
              f"{tokens_h(tokens['cache_read'])} cached input, "
              f"{tokens_h(tokens['cache_write'])} cache write, "
              f"{tokens_h(tokens['output'])} output)")
        if usage_complete and decided:
            print(f"  measured tokens per accepted outcome: "
                  f"{tokens['total'] // decided} (run sessions / {decided} accepted tasks)")
        by_role: dict[str, int] = {}
        for row in observed:
            role = "+".join(row.get("roles") or []) or "unknown"
            by_role[role] = by_role.get(role, 0) + int(row.get("total") or 0)
        print("  measured tokens by role: "
              + "; ".join(f"{role} {tokens_h(count)}"
                          for role, count in sorted(by_role.items())))
    if not usage_complete or not decided:
        print("  measured tokens per accepted outcome: unknown "
              "(missing role telemetry or no accepted tasks)")
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
