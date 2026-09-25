"""Constants and the few helpers every other module shares."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path


# This package sits in the skill directory; `bin/docket` is the launcher that runs it.
SKILL_DIR = Path(__file__).resolve().parents[1]
LAUNCHER = SKILL_DIR / "bin" / "docket"

STATE_DIR = ".docket"
SUPPORTED_HARNESSES = ("claude", "codex", "opencode")
DEFAULT_REVIEWER = "orch"
TOPOLOGIES = ("split", "combined")
WORKFLOW_LEGACY_DECODE = "legacy"
WORKFLOW_NEW_RUN_DEFAULT = "five-role-v1"
WORKFLOW_FIVE_ROLE = "five-role-v1"
WORKFLOWS = (WORKFLOW_LEGACY_DECODE, WORKFLOW_FIVE_ROLE)
MODE_STANDARD = "standard"
MODE_QUICK = "quick"
MODE_CUSTOM = "custom"
MODES = (MODE_STANDARD, MODE_QUICK)
# A bare `docket init` selects this preset. Quick is the default because most runs
# have one clear outcome, and three sessions start and wait far more cheaply than
# five; `--mode standard` keeps an independent verifier behind every decision.
MODE_NEW_RUN_DEFAULT = MODE_QUICK
RECORDED_MODES = (MODE_STANDARD, MODE_QUICK, MODE_CUSTOM)
REVIEW_INDEPENDENT = "independent-verifier-reviewer"
REVIEW_COMBINED_CHECKER = "combined-checker"
# One role declaration every caller reads. Logical five, quick two, and the
# union all session, event, prompt, and help choices advertise. Per-run preset
# checks then accept exactly the roles the active preset declares.
FIVE_ROLES = ("planner", "orchestrator", "implementor", "verifier", "reviewer")
QUICK_ROLES = ("coordinator", "checker")
ACTING_ROLES = (*FIVE_ROLES, *QUICK_ROLES)
REQUIRED_TASK_REPORT_SECTIONS = [
    "Summary",
    "Files changed",
    "Acceptance",
    "Verification",
    "Decisions needed",
]
REQUIRED_ORCH_REPORT_SECTIONS = [
    "Executive summary",
    "Task outcomes",
    "Changes delivered",
    "Integrated verification",
    "Exceptions and waivers",
    "Decisions needed",
]
REQUIRED_TASK_SECTIONS = [
    "Goal",
    "Acceptance criteria",
]
REQUIRED_SCOPE_SECTIONS = [
    "Discovery summary",
    "Change surface",
    "Relevant symbols",
    "Verification plan",
    "Risks and assumptions",
]
REQUIRED_HANDOFF_SECTIONS = [
    "Resume summary",
    "Completed",
    "Remaining",
    "Files and symbols",
    "Verification state",
    "Decisions and risks",
    "Exact next action",
]
PLACEHOLDER = re.compile(r"<!--\s*TODO|\bTODO:|\bFIXME\b|\bXXX\b")
# The template's own unfilled markers. Supervisor-written tasks are checked for these
# alone, so a criterion that names FIXME or XXX is a criterion, not a placeholder.
TEMPLATE_PLACEHOLDER = re.compile(r"<!--\s*TODO")
DOCUMENTS_ONLY = "documents-only"
EVIDENCE_MODES = ("git", DOCUMENTS_ONLY)
COVERAGE_AVAILABLE = "available"
COVERAGE_UNAVAILABLE = "unavailable"
# Git's well-known SHA-1 empty tree, used only when a repository cannot name its own.
EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# Bound on baseline capture. A baseline is captured in full or it fails; a partial
# capture is never published and never described as available coverage.
UNTRACKED_CONTENT_LIMIT = 8 * 1024 * 1024
ROOT_ALIAS = re.compile(r"[A-Za-z0-9._-]+")
ROOT_KEYS = ("alias", "path", "git_dir", "common_dir", "branch", "head")
ROOTS_ABSENT = "absent"
ROOTS_INVALID = "invalid"
ROOTS_DECLARED = "declared"


def die(msg: str) -> None:
    print(f"docket: {msg}", file=sys.stderr)
    raise SystemExit(1)


def stamp(when: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))


DELIVERY_LEASE_SECONDS = 300


SESSION_ROLES = ACTING_ROLES
KNOWN_EVENT_ROLES = ACTING_ROLES


def now_s() -> float:
    """Current time for lease arithmetic. `DOCKET_NOW` pins it for tests."""
    raw = os.environ.get("DOCKET_NOW", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return time.time()


def _has(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


SHA_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
