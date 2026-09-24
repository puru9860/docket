"""Run directories, numbered documents, and the task owners a run holds."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import STATE_DIR
from .frontmatter import parse, sections


def root() -> Path:
    here = Path.cwd().resolve()
    for d in [here, *here.parents]:
        if (d / STATE_DIR).is_dir():
            return d
    return here


def run_dir(run: str) -> Path:
    return root() / STATE_DIR / "runs" / run


def _seq_key(p: Path) -> tuple[tuple[int, ...], str]:
    """Numeric ordering for sequence-numbered artifacts.

    Artifact names are zero padded to two digits, so text ordering puts
    attempt 100 before attempt 99 and taking the last element of a sorted
    glob returns the older record. Every newest-record selection orders by
    the numbers in the name, not by text, so the newest record is always
    the newest. One shared place every caller uses.
    """
    return (tuple(int(n) for n in re.findall(r"\d+", p.name)), p.name)


def _ordered(paths) -> list[Path]:
    """Sequence-numbered paths oldest first, by number rather than by text."""
    return sorted(paths, key=_seq_key)


def _trailing_number(p: Path) -> int:
    """The last number in an artifact name, or 0 when it carries none.

    Numbered artifacts of one series share every number but the last
    (`T03-verification-02.mdx`, `request-02.mdx`, `T03-02.json`, `F02.mdx`),
    so the last number is the series counter in every case.
    """
    nums = re.findall(r"\d+", p.name)
    return int(nums[-1]) if nums else 0


def next_numbered(paths) -> int:
    """One above the highest existing number, or 1 when nothing exists.

    Counting artifacts (`len(...) + 1`) reuses a number after a gap and the
    next publish silently overwrites the survivor. The maximum plus one
    never points at a surviving artifact, even when a lower number is absent.
    """
    return max([_trailing_number(p) for p in paths], default=0) + 1


def reports(d: Path, owner: str) -> list[Path]:
    return _ordered(d.glob(f"{owner}-report-*.mdx"))


def decisions(d: Path, owner: str) -> list[Path]:
    return _ordered(d.glob(f"{owner}-decision-*.mdx"))


def handoffs(d: Path, owner: str) -> list[Path]:
    return _ordered(d.glob(f"{owner}-handoff-*.mdx"))


def scope_path(d: Path, owner: str) -> Path:
    return d / f"{owner}-scope.mdx"


def owners(d: Path) -> list[str]:
    """Every owner that has at least one report, task owners first.

    A frozen release carries an explicit owner list; the override keeps bundle
    resolution from rediscovering owners by scanning mutable live documents.
    """
    override = os.environ.get("DOCKET_BUNDLE_OWNERS", "").strip()
    if override:
        return [item for item in override.split(",") if item]
    found = {p.name.split("-report-")[0] for p in d.glob("*-report-*.mdx")}
    tasks = sorted(o for o in found if o != "orch")
    return tasks + (["orch"] if "orch" in found else [])


def latest(paths: list[Path]) -> Path | None:
    return max(paths, key=_seq_key) if paths else None


def planned_tasks(d: Path) -> list[str]:
    """Task ids from the plan table, preserving plan order."""
    plan = d / "plan.mdx"
    if not plan.is_file():
        return []
    _, body = parse(plan.read_text())
    task_section = sections(body).get("Tasks", "")
    out: list[str] = []
    for line in task_section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if cells and re.fullmatch(r"T\d+", cells[0]) and cells[0] not in out:
            out.append(cells[0])
    return out


def dispatch_path(d: Path, owner: str) -> Path:
    return d / ".dispatch" / f"{owner}.json"


def read_dispatch(d: Path, owner: str) -> dict:
    path = dispatch_path(d, owner)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
