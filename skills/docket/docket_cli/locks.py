"""Owner and run locks that serialize each read-modify-write of a task or run."""

from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path


@contextlib.contextmanager
def owner_lock(d: Path, owner: str):
    """Serialize one owner's multi-file lifecycle transition.

    Two reviewers deciding the same owner at once could otherwise interleave the
    decision, report, and next-round writes and produce two rounds for one verdict.
    """
    path = d / ".locks" / f"{owner}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def owner_lock_held(d: Path, owner: str) -> bool:
    """Whether a live process holds the owner's transition lock right now.

    Read-only: a missing lock file means nobody holds it, and it is never
    created here, so inspection stays free of writes.
    """
    path = d / ".locks" / f"{owner}.lock"
    if not path.is_file():
        return False
    try:
        with path.open("r") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        return False
    return False


@contextlib.contextmanager
def run_lock(d: Path):
    """Serialize one run's run-scoped read-modify-write.

    The owner lock is per-owner, so two run-scoped writers (mode-escalation
    requests, run-level observations) could otherwise read the same highest
    number and publish the same path, with the second silently overwriting
    the first. Run-scoped allocation holds this lock across its
    read-number-write instead.
    """
    path = d / ".locks" / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
