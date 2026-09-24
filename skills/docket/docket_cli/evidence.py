"""Git plumbing and the dirty-path records that change evidence is measured from."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from .common import EMPTY_TREE_SHA1, STATE_DIR
from .paths import root


# Evidence is read in Git's own formats, never the user's presentation settings: a
# `diff.noprefix` patch applied one directory too shallow and turned a baseline's dirt
# into the implementor's rename. `--no-ext-diff` and `--no-textconv` cover drivers.
GIT_FIXED_CONFIG = (
    "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
    "-c", "diff.submodule=short", "-c", "core.quotePath=false", "-c", "color.ui=false",
)
# Repository selection and config a caller's environment can carry, as inside a git
# hook. Evidence always names its own repository, index, and object store instead.
GIT_CALLER_ENV = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS",
)


def git_env() -> dict[str, str]:
    """The environment every Git command runs in: the caller's, minus its Git state.

    `GIT_OPTIONAL_LOCKS=0` keeps `git status` from refreshing the user's index, which
    would otherwise be a write into the checkout being measured.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in GIT_CALLER_ENV and not re.fullmatch(r"GIT_CONFIG_(KEY|VALUE)_\d+", k)}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def git_argv(*args: str) -> list[str]:
    return ["git", *GIT_FIXED_CONFIG, *args]


def git_text(cwd: Path | str, *args: str, timeout: int = 30) -> str | None:
    """Trimmed stdout of a Git command, or None when Git cannot answer at all.

    Paths that are not UTF-8 survive as surrogate escapes, so they round-trip to the
    filesystem instead of crashing the command that met them.
    """
    try:
        result = subprocess.run(
            git_argv(*args), cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="surrogateescape", timeout=timeout, env=git_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def git_raw(cwd: Path | str, *args: str, timeout: int = 300) -> bytes | None:
    """Raw stdout of a Git command. Patches are captured as bytes so binary survives."""
    try:
        result = subprocess.run(git_argv(*args), cwd=str(cwd), capture_output=True,
                                timeout=timeout, env=git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def git_root() -> Path | None:
    top = git_text(root(), "rev-parse", "--show-toplevel", timeout=10)
    return Path(top).resolve() if top else None


def empty_tree(repo: Path) -> str:
    """This repository's empty-tree id, so an unborn branch still has a baseline."""
    return git_text(repo, "hash-object", "-t", "tree", os.devnull) or EMPTY_TREE_SHA1


def state_prefix(repo: Path | str) -> str:
    """Docket's own state directory relative to one checkout, or '' when it is outside it.

    `.docket` may sit below a checkout's top, in a package directory, so a filter for
    `.docket/` at the top alone measured Docket's own files as a task's changes.
    """
    try:
        relative = os.path.relpath((root() / STATE_DIR).resolve(), Path(repo).resolve())
    except ValueError:
        return ""
    if relative == os.curdir or relative.startswith(os.pardir):
        return ""
    return relative.replace(os.sep, "/")


def is_state_path(prefix: str, relative: str) -> bool:
    """Whether a checkout-relative path is Docket's own state under that prefix."""
    return bool(prefix) and (relative == prefix or relative.startswith(prefix + "/"))


def porcelain_records(repo: Path) -> list[tuple[str, str]] | None:
    """(status, path) pairs from porcelain v1, or None when Git cannot answer.

    A rename or copy source is returned as its own record so callers see both
    sides of the move. Docket's own state directory is never reported.
    """
    text = git_text(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=120,
    )
    if text is None:
        return None
    records = text.split("\0")
    out: list[tuple[str, str]] = []
    state = state_prefix(repo)
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        status, path = record[:2], record[3:]
        # An untracked `.docket` is reported as one directory entry with a slash.
        if path and not is_state_path(state, path.rstrip("/")):
            out.append((status, path))
        if "R" in status or "C" in status:
            if i < len(records) and records[i]:
                old = records[i]
                i += 1
                if not is_state_path(state, old):
                    out.append((status, old))
    return out


def dirty_paths(repo: Path) -> set[str] | None:
    """Paths Git reports as changed, or None when Git cannot answer at all.

    An empty set means a clean worktree. None means there is no diff evidence,
    which callers must report as unavailable rather than as an unchanged tree.
    """
    records = porcelain_records(repo)
    if records is None:
        return None
    return {path for _, path in records}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(repo: Path, relative: str) -> str | None:
    path = repo / relative
    if path.is_symlink():
        return "link:" + os.readlink(path)
    if not path.is_file():
        return None
    return file_digest(path)


def worktree_mode(path: Path) -> str:
    """The mode Git would record, or `absent`. An executable bit is part of identity."""
    if path.is_symlink():
        return "120000"
    if path.is_dir():
        return "040000"
    if not path.is_file():
        return "absent"
    return "100755" if path.stat().st_mode & 0o111 else "100644"


def index_entries(repo: Path) -> dict[str, str] | None:
    """`<mode> <blob>` per indexed path, or None when Git cannot answer.

    The index is a change surface of its own. Without it, staging or unstaging a
    path that was already dirty at assignment leaves content and mode unchanged
    and the change disappears.
    """
    text = git_text(repo, "ls-files", "--stage", "-z", timeout=120)
    if text is None:
        return None
    out: dict[str, str] = {}
    state = state_prefix(repo)
    for entry in text.split("\0"):
        if not entry or "\t" not in entry:
            continue
        meta, name = entry.split("\t", 1)
        fields = meta.split()
        if len(fields) >= 2 and not is_state_path(state, name):
            out[name] = f"{fields[0]} {fields[1]}"
    return out


def path_state(repo: Path, relative: str, status: str, index: dict[str, str]) -> dict[str, str]:
    """One path's full identity: porcelain state, worktree mode, content, index entry.

    A content hash alone cannot see a mode-only or index-only change layered on a
    path that was already dirty when the baseline was taken.
    """
    target = repo / relative
    mode = worktree_mode(target)
    if mode == "120000":
        content = "link:" + os.readlink(target)
    elif mode in ("100644", "100755"):
        content = file_digest(target)
    else:
        content = ""
    return {
        "status": status,
        "mode": mode,
        "content": content,
        "index": index.get(relative, ""),
    }
