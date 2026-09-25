"""Atomic publication of documents and JSON, and the fault and perturb test seams."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .paths import root


def fault(point: str) -> None:
    """Abort exactly at a named write boundary. Inert unless `DOCKET_FAULT` names it.

    This is a test seam, not a feature. The regression suite has to interrupt a
    real transition rather than a simulated one, because the whole point of the
    recovery path is what it finds on disk after an interruption.
    """
    wanted = [item.strip() for item in os.environ.get("DOCKET_FAULT", "").split(",")]
    if point in wanted:
        sys.stdout.flush()
        sys.stderr.write(f"docket: injected fault at {point}\n")
        sys.stderr.flush()
        os._exit(70)


def perturb(point: str) -> None:
    """Run a shell command exactly at a named boundary. Inert unless `DOCKET_PERTURB` names it.

    The companion of `fault`, for the races an abort cannot show. A window between two
    adjacent reads is real but unobservable from outside the process, so the suite needs
    a way to land a concurrent writer inside it. `DOCKET_PERTURB` holds one
    `point=command` pair per line. This is a test seam, not a feature.
    """
    for item in os.environ.get("DOCKET_PERTURB", "").splitlines():
        name, _, command = item.partition("=")
        if name.strip() == point and command.strip():
            subprocess.run(command, shell=True, cwd=root())


# Directories that hold captured workspace content: baseline patches, untracked file
# bodies, frozen bundles, and the private object store. They can contain secrets, so
# they are readable by their owner alone, which covers everything beneath them.
PRIVATE_STATE_DIRS = (".snapshots", ".bundles", ".baselines")


def keep_private(path: Path) -> None:
    """Make every private state directory above `path` owner-only."""
    for parent in (path, *path.parents):
        if parent.name in PRIVATE_STATE_DIRS and parent.is_dir():
            try:
                if parent.stat().st_mode & 0o077:
                    os.chmod(parent, 0o700)
            except OSError:
                pass


def publish_bytes(path: Path, data: bytes) -> None:
    """Publish raw bytes through a temporary file and one atomic replace.

    Baseline patches carry binary hunks and paths that need not be valid UTF-8,
    so evidence is written as bytes while documents go through `publish`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    keep_private(path.parent)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if path.is_file():
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        fault(f"publish:{path.name}")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # not every filesystem allows a directory fsync
    finally:
        os.close(fd)


def publish(path: Path, text: str) -> None:
    """Publish a document through a temporary file and one atomic replace.

    A reader never sees a half-written artifact: the temporary file is fully
    written and flushed first, so an interrupted publication leaves the previous
    content untouched rather than a truncated document.
    """
    publish_bytes(path, text.encode())


def publish_json(path: Path, data: object) -> None:
    publish(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def publish_bytes_exclusive(path: Path, data: bytes) -> None:
    """Publish raw bytes only when the path does not exist yet.

    Numbered evidence must never overwrite a surviving artifact after a gap:
    the allocator chooses highest-plus-one, and this exclusive create turns
    any residual race into a retryable FileExistsError instead of a silent
    overwrite. A partial write on failure is removed, never left behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    keep_private(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError:
        raise
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        fault(f"publish:{path.name}")
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # not every filesystem allows a directory fsync
    finally:
        os.close(fd)


def publish_exclusive(path: Path, text: str) -> None:
    """Publish a document only when the path does not exist yet.

    Like `publish`, but the create is exclusive: an existing path raises
    FileExistsError and the surviving bytes are left untouched.
    """
    publish_bytes_exclusive(path, text.encode())
