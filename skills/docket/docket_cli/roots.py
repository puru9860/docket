"""Declared checkout roots and root-qualified scope paths."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import ROOTS_ABSENT, ROOTS_DECLARED, ROOTS_INVALID, ROOT_ALIAS, ROOT_KEYS, die
from .paths import root
from .publication import publish_json
from .evidence import git_text, porcelain_records


def root_identity(where: Path) -> dict[str, str] | None:
    """One checkout root: canonical worktree, Git directory identity, branch, HEAD.

    Two worktrees of one repository share `common_dir` and differ in `path` and
    `git_dir`, which is exactly why identity is not the common directory alone.
    """
    top = git_text(where, "rev-parse", "--show-toplevel", timeout=10)
    if not top:
        return None
    worktree = Path(top).resolve()
    git_dir = git_text(worktree, "rev-parse", "--absolute-git-dir", timeout=10) or ""
    common = git_text(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=10)
    if common is None:
        relative = git_text(worktree, "rev-parse", "--git-common-dir", timeout=10) or ""
        common = str((worktree / relative).resolve()) if relative else git_dir
    branch = git_text(worktree, "rev-parse", "--abbrev-ref", "HEAD", timeout=10) or ""
    head = git_text(worktree, "rev-parse", "HEAD", timeout=10) or ""
    return {
        "path": str(worktree),
        "git_dir": str(Path(git_dir).resolve()) if git_dir else "",
        "common_dir": str(Path(common).resolve()) if common else "",
        "branch": branch,
        "head": head,
    }


def linked_worktrees(worktree: Path) -> list[Path]:
    """Every worktree sharing this repository's Git storage, including this one.

    Advisory only. A linked worktree is a change surface of its own and is only
    ever captured when it is declared by name.
    """
    listing = git_text(worktree, "worktree", "list", "--porcelain", timeout=30)
    if listing is None:
        return []
    return [
        Path(line[len("worktree ") :])
        for line in listing.splitlines()
        if line.startswith("worktree ")
    ]


def nested_checkouts(worktree: Path) -> list[Path]:
    """Checkouts embedded in one worktree: submodule gitlinks and embedded repositories.

    Advisory only, for the same reason `linked_worktrees` is.
    """
    out: list[Path] = []
    staged = git_text(worktree, "ls-files", "--stage", "-z", timeout=60)
    if staged:
        for entry in staged.split("\0"):
            if entry.startswith("160000 ") and "\t" in entry:
                out.append(worktree / entry.split("\t", 1)[1])
    records = porcelain_records(worktree)
    for status, path in records or []:
        # Git never descends into an embedded repository; it reports the directory itself.
        if status.strip() == "??" and path.endswith("/") and (worktree / path / ".git").exists():
            out.append(worktree / path.rstrip("/"))
    return out


def candidate_roots(base: Path) -> list[Path]:
    """Checkouts somebody might want to declare. Advisory: nothing here is captured."""
    identity = root_identity(base)
    if not identity:
        # No enclosing checkout, so `.docket` sits in a plain parent. The sibling
        # repositories of that layout are one level down; look only there.
        try:
            return sorted(
                child for child in base.iterdir()
                if child.is_dir() and (child / ".git").exists()
            )
        except OSError:
            return []
    here = Path(identity["path"])
    found = {here}
    for candidate in [*linked_worktrees(here), *nested_checkouts(here)]:
        resolved = candidate.resolve()
        if resolved.is_dir():
            found.add(resolved)
    return sorted(found)


def roots_path(d: Path) -> Path:
    return d / ".snapshots" / "roots.json"


def valid_root_record(record: object) -> dict[str, str] | None:
    """One declaration entry, or None when a field is missing or malformed."""
    if not isinstance(record, dict):
        return None
    out: dict[str, str] = {}
    for key in ROOT_KEYS:
        value = record.get(key)
        if not isinstance(value, str):
            return None
        out[key] = value
    if not ROOT_ALIAS.fullmatch(out["alias"]) or not out["path"].startswith("/"):
        return None
    return out


def read_roots(d: Path) -> tuple[str, list[dict[str, str]], str]:
    """This run's declaration as `(state, roots, reason)`.

    Absent and invalid are deliberately different states. A declaration that
    cannot be read is never reported as "nothing declared yet", because that is
    exactly what would let a later assignment rediscover aliases that existing
    baselines already point at.
    """
    path = roots_path(d)
    if not path.is_file():
        return ROOTS_ABSENT, [], "no checkout root is declared for this run"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return ROOTS_INVALID, [], f"the root declaration in {path.name} is unreadable: {exc}"
    entries = data.get("roots") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return ROOTS_INVALID, [], f"the root declaration in {path.name} names no checkout root"
    records: list[dict[str, str]] = []
    for entry in entries:
        record = valid_root_record(entry)
        if record is None:
            return ROOTS_INVALID, [], f"the root declaration in {path.name} has a malformed entry"
        records.append(record)
    aliases = [record["alias"] for record in records]
    paths = [record["path"] for record in records]
    if len(set(aliases)) != len(aliases) or len(set(paths)) != len(paths):
        return ROOTS_INVALID, [], f"the root declaration in {path.name} repeats an alias or a path"
    return ROOTS_DECLARED, records, ""


def declared_roots(d: Path) -> list[dict[str, str]]:
    """The declared roots, or none. Callers needing the reason use `read_roots`."""
    return read_roots(d)[1]


def baselines_exist(d: Path) -> bool:
    """Whether any baseline already resolves aliases through the declaration."""
    return any(path.name != "roots.json" for path in (d / ".snapshots").glob("*.json"))


def build_root_records(specs: list[str]) -> list[dict[str, str]]:
    """Validate `alias=path` declarations into canonical checkout identities."""
    base = root()
    records: list[dict[str, str]] = []
    aliases: set[str] = set()
    paths: set[str] = set()
    for spec in specs:
        alias, sep, where = spec.partition("=")
        if not sep or not ROOT_ALIAS.fullmatch(alias) or not where.strip():
            die(f"root declaration {spec!r} must be alias=path, alias in [A-Za-z0-9._-]")
        target = Path(where.strip())
        target = target if target.is_absolute() else base / target
        if not target.is_dir():
            die(f"root {alias}: {target} is not a directory")
        identity = root_identity(target)
        if not identity:
            die(f"root {alias}: {target} is not inside a Git checkout")
        if identity["path"] != str(target.resolve()):
            die(
                f"root {alias}: {target} is inside the checkout at {identity['path']}; "
                "declare that worktree, or the nested checkout you actually meant"
            )
        if alias in aliases:
            die(f"root alias {alias} is declared twice")
        if identity["path"] in paths:
            die(f"{identity['path']} is declared twice")
        aliases.add(alias)
        paths.add(identity["path"])
        identity["alias"] = alias
        records.append({key: identity[key] for key in ROOT_KEYS})
    return records


def declare_roots(d: Path, specs: list[str], *, replace: bool = False) -> list[dict[str, str]]:
    """Declare this run's checkout roots once, explicitly, and never silently again.

    Only a declared root is ever captured. A linked worktree or a nested
    repository is never added implicitly: it can carry unrelated dirt into
    secret-bearing local evidence, and `.docket` may well sit in a non-Git parent
    holding several checkouts, where there is no single enclosing repository to
    discover from at all.
    """
    state, existing, reason = read_roots(d)
    if state == ROOTS_DECLARED and not replace:
        return existing
    if state == ROOTS_INVALID and not replace:
        die(f"{reason}; repair or remove it deliberately - it is never rediscovered")
    if baselines_exist(d):
        if replace:
            die("baselines already reference the declared roots; redeclaring would leave "
                "them pointing at aliases that no longer mean the same checkout")
        die("baselines were captured against a root declaration that is now missing; "
            "restore .snapshots/roots.json instead of declaring a new one")
    if not specs:
        identity = root_identity(root())
        if not identity:
            die(
                f"{root()} is not inside a Git checkout, so there is no current root to "
                "default to; name each checkout explicitly as alias=path"
            )
        specs = [f"root={identity['path']}"]
    records = build_root_records(specs)
    publish_json(roots_path(d), {"version": 1, "roots": records})
    return records


def within(target: Path, container: Path) -> bool:
    return target == container or container in target.parents


def split_qualified(token: str) -> tuple[str, str]:
    """An `alias:path` scope token split into its parts; a bare token has no alias."""
    alias, sep, rest = token.partition(":")
    if sep and alias and re.fullmatch(r"[A-Za-z0-9._-]+", alias):
        return alias, rest.strip("/")
    return "", token.strip("/")


def resolve_scope(
    roots: list[dict[str, str]], tokens: list[str]
) -> tuple[list[str], list[str]]:
    """Root-qualified scope paths, plus one problem per token that cannot resolve.

    A bare path resolves to the deepest declared root containing it, so a nested
    repository owns its own paths instead of being absorbed by its parent. Two
    roots at the same depth are ambiguous, and ambiguity is reported, not guessed.
    """
    if not roots:
        return list(tokens), []
    aliases = {record.get("alias", "") for record in roots}
    base = root()
    qualified: list[str] = []
    problems: list[str] = []
    for token in tokens:
        alias, relative = split_qualified(token)
        if alias:
            if alias not in aliases:
                problems.append(f"scope path {token} names an undeclared root alias {alias!r}")
                continue
            qualified.append(f"{alias}:{relative}")
            continue
        candidate = Path(relative)
        target = candidate.resolve() if candidate.is_absolute() else (base / relative).resolve()
        matches = [record for record in roots if within(target, Path(record["path"]))]
        if not matches:
            problems.append(f"scope path {token} resolves to no declared checkout root")
            continue
        deepest = max(len(Path(record["path"]).parts) for record in matches)
        best = [record for record in matches if len(Path(record["path"]).parts) == deepest]
        if len(best) > 1:
            names = ", ".join(sorted(record.get("alias", "") for record in best))
            problems.append(f"scope path {token} is ambiguous across declared roots: {names}")
            continue
        inside = os.path.relpath(target, Path(best[0]["path"])).replace(os.sep, "/")
        qualified.append(f"{best[0]['alias']}:{'' if inside == '.' else inside}")
    return qualified, problems
