"""Embedded feedback records and the user-level feedback log."""

from __future__ import annotations

import fcntl
import json
import os
import re
from pathlib import Path

from .common import stamp
from .paths import root
from .state import state_of


FEEDBACK_CATEGORIES = ("instruction", "discovery", "verification", "recovery",
                       "dispatch", "routing", "review", "delivery", "other")


def feedback_dir(d: Path) -> Path:
    return d / "feedback"


def next_feedback_id(d: Path) -> str:
    nums: list[int] = []
    if feedback_dir(d).is_dir():
        for path in feedback_dir(d).glob("*.mdx"):
            match = re.fullmatch(r"F(\d+)", path.stem)
            if match:
                nums.append(int(match.group(1)))
    num = max(nums, default=0) + 1
    return f"F{num:02d}"


def feedback_log_path() -> Path | None:
    """The user-level feedback log shared by every project, or None when disabled.

    Feedback collected per run stays in that run; this append-only log is what
    accumulates across runs and projects so bottlenecks can be reviewed
    periodically with `docket feedback --digest`. `DOCKET_FEEDBACK_LOG` names
    another file, and `off` disables it.
    """
    raw = os.environ.get("DOCKET_FEEDBACK_LOG")
    if raw is not None:
        raw = raw.strip()
        if raw.lower() in ("", "off", "none", "0"):
            return None
        return Path(raw).expanduser()
    base = os.environ.get("XDG_STATE_HOME", "").strip() or str(Path.home() / ".local" / "state")
    return Path(base) / "docket" / "feedback.jsonl"


def ensure_private_dir(path: Path) -> None:
    """Create path and any missing parents with owner-only mode 0700."""
    missing: list[Path] = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError:
            continue
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def log_feedback(entry: dict[str, object]) -> None:
    """Append one record to the user-level log. Never raises: feedback never blocks."""
    path = feedback_log_path()
    if path is None:
        return
    try:
        project = str(root())
    except SystemExit:
        project = ""
    record = {"at": stamp(), "project": project, **entry}
    try:
        ensure_private_dir(path.parent)
        with path.open("a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        return


def machine_feedback(d: Path, category: str, owner: str, body: str,
                     role: str = "docket") -> None:
    """Record a bottleneck docket observed itself, at no model cost."""
    try:
        rnd = state_of(d, owner)[0] if owner else 0
    except (OSError, ValueError):
        rnd = 0
    log_feedback({"origin": "machine", "run": d.name, "role": role, "category": category,
                  "task": owner or "-", "round": str(rnd or "-"), "body": body})
