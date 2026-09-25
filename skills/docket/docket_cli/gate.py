"""The submission gate: document structure, placeholders, and acceptance evidence."""

from __future__ import annotations

import re
from pathlib import Path

from .common import (
    COVERAGE_AVAILABLE, COVERAGE_UNAVAILABLE, PLACEHOLDER, REQUIRED_ORCH_REPORT_SECTIONS,
    REQUIRED_TASK_REPORT_SECTIONS, REQUIRED_TASK_SECTIONS, SUPPORTED_HARNESSES,
    TEMPLATE_PLACEHOLDER,
)
from .frontmatter import (
    CODE_FENCE, checkbox_items, duplicate_sections, is_empty, parse, sections, stated,
)
from .paths import planned_tasks, root
from .roots import declared_roots
from .baselines import assignment_evidence
from .state import generated_path_hint, names_path, state_of
from .dependencies import dependency_problems


def body_line_offset(text: str, body: str) -> int:
    """Lines before the body in its file, so a diagnostic names the line an editor shows."""
    text = text.removeprefix("\ufeff")
    return text[: len(text) - len(body)].count("\n") if body and text.endswith(body) else 0


def placeholder_problems(body: str, pattern: re.Pattern = PLACEHOLDER, offset: int = 0,
                         quoted_sections: tuple[str, ...] = ()) -> list[str]:
    """One problem per line still holding a placeholder, at its line in the file.

    Code fences and inline code are quoted material, such as real tool output, and
    are never policed for words. A section in `quoted_sections` copies supervisor
    text verbatim, so only template markers count there.
    """
    problems: list[str] = []
    fence = ""
    current = ""
    for n, line in enumerate(body.splitlines(), 1):
        marker = CODE_FENCE.match(line)
        if marker:
            fence = "" if fence and marker.group(1) == fence else (fence or marker.group(1))
            continue
        if fence:
            continue
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
        prose = re.sub(r"`[^`\n]*`", "", line)
        check = TEMPLATE_PLACEHOLDER if current in quoted_sections else pattern
        if check.search(prose):
            problems.append(f"unresolved placeholder at line {n + offset}: {line.strip()[:60]}"
                            " (quote literal text in backticks)")
    return problems


def required_section_problems(body: str, required: list[str],
                              pattern: re.Pattern = PLACEHOLDER, offset: int = 0) -> list[str]:
    secs = sections(body)
    problems: list[str] = []
    for name in required:
        if name not in secs:
            problems.append(f"missing section '## {name}'")
        elif is_empty(secs[name]):
            problems.append(f"section '## {name}' is empty")
    for name in duplicate_sections(body, required):
        problems.append(f"duplicate section '## {name}': keep one")
    problems.extend(placeholder_problems(body, pattern, offset))
    return problems


def task_intent_problems(d: Path, owner: str) -> list[str]:
    """Shared task-intent gate for validate-task, dispatch, and prompt rendering.

    One shared check so the three callers cannot drift: an untouched generated
    task whose goal and acceptance are still the template's unresolved comments
    is refused everywhere work would start from it.
    """
    task = d / f"{owner}-task.mdx"
    try:
        text = task.read_text()
        _, body = parse(text)
    except (OSError, ValueError) as exc:
        return [f"{owner}-task.mdx is unreadable ({exc})"]
    problems = required_section_problems(body, REQUIRED_TASK_SECTIONS, TEMPLATE_PLACEHOLDER,
                                         body_line_offset(text, body))
    secs = sections(body)
    criteria = checkbox_items(secs.get("Acceptance criteria", ""))
    if not criteria:
        problems.append("task needs at least one acceptance criterion")
    return problems


EVIDENCE_STATES = ("met", "partial", "not-met", "not-verified")
EVIDENCE_FROZEN_ARTIFACTS = ("verify.stdout", "verify.stderr", "bundle", "report",
                             "contract", "baseline", "dependencies", "delta")


def task_criterion_ids(d: Path, owner: str, text: str | None = None) -> list[str]:
    """Stable acceptance IDs for a task, in task order: A1, A2, ...

    Reads the live task file unless explicit document text is given, so frozen
    release rendering can use frozen bytes instead of mutable live documents.
    """
    try:
        body = text if text is not None else parse((d / f"{owner}-task.mdx").read_text())[1]
        items = checkbox_items(sections(body).get("Acceptance criteria", ""))
    except (OSError, ValueError):
        return []
    return [f"A{n}" for n, _ in enumerate(items, 1)]


def parse_evidence_table(body: str) -> tuple[list[dict[str, str]] | None, list[str]]:
    """An evidence table's rows, or None when the report uses the legacy path.

    Returns (None, []) when no `## Evidence` section is present: the checkbox
    contract keeps applying. Free-text contradiction detection is deliberately
    absent; only the table's structure is mechanical.
    """
    text = sections(body).get("Evidence", "")
    if is_empty(text):
        return None, []
    lines = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return [], ["'## Evidence' must hold a table with one row per acceptance criterion"]
    rows: list[dict[str, str]] = []
    problems: list[str] = []
    for line in lines[2:]:
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            problems.append(f"malformed evidence row (need id, state, artifacts, gaps): {line.strip()[:80]}")
            continue
        aid, state, artifacts, gaps = cells[0].upper(), cells[1].lower(), cells[2], cells[3]
        rows.append({"id": aid, "state": state, "artifacts": artifacts, "gaps": gaps})
    return rows, problems


def evidence_artifact_problems(d: Path, artifacts: str) -> list[str]:
    """Every named artifact must be frozen evidence or an existing path."""
    tokens = [token.strip().strip("`") for token in artifacts.split(",") if token.strip().strip("`")]
    if not tokens or all(token.lower() in ("none", "n/a", "-") for token in tokens):
        return ["criterion is met but names no artifacts"]
    problems = []
    for token in tokens:
        low = token.lower()
        if low in EVIDENCE_FROZEN_ARTIFACTS:
            continue
        path_token = re.sub(r":\d+$", "", token)
        resolved = None
        if ":" in path_token and not path_token.startswith(":"):
            alias, _, rel = path_token.partition(":")
            for record in declared_roots(d):
                if record.get("alias") == alias:
                    resolved = Path(str(record.get("path", ""))) / rel
        else:
            resolved = root() / path_token
        if resolved is None or not resolved.exists():
            problems.append(f"unknown or missing evidence artifact: {token}")
    return problems


def evidence_problems(d: Path, owner: str, body: str, blocked: bool,
                      verify_skipped: bool = False) -> list[str]:
    """Mechanical evidence-table checks. Blocked work stays lightweight.

    A blocked submission never has to satisfy completion evidence: missing
    dependencies, broken verification, and provider loss stay reportable with
    the legacy sections alone.
    """
    if owner == "orch" or blocked:
        return []
    rows, problems = parse_evidence_table(body)
    if rows is None:
        return []
    problems = list(problems)
    aids = task_criterion_ids(d, owner)
    seen: dict[str, int] = {}
    for row in rows:
        seen[row["id"]] = seen.get(row["id"], 0) + 1
    for aid, count in sorted(seen.items()):
        if count > 1:
            problems.append(f"duplicate evidence id {aid}")
        if aid not in aids:
            problems.append(f"evidence id {aid} is not a task acceptance criterion")
    for aid in aids:
        if aid not in seen:
            problems.append(f"missing evidence for required criterion {aid}")
    for row in rows:
        if row["state"] not in EVIDENCE_STATES:
            problems.append(f"evidence {row['id']} has unknown state {row['state']!r}; "
                            f"use one of {', '.join(EVIDENCE_STATES)}")
            continue
        gaps = row["gaps"].strip()
        gapless = not gaps or gaps.lower() in ("none", "n/a", "-")
        if row["state"] == "met":
            if not gapless:
                problems.append(f"explicit gap attached to met criterion {row['id']}: "
                                "a met criterion must have no gaps")
            for problem in evidence_artifact_problems(d, row["artifacts"]):
                problems.append(f"evidence {row['id']}: {problem}")
            if verify_skipped and "verify.stdout" in row["artifacts"]:
                problems.append(f"evidence {row['id']}: names verify.stdout but the "
                                "verification was skipped")
        else:
            if gapless:
                problems.append(f"evidence {row['id']} is {row['state']} but names no gap")
            # Its box is checked, since a normal submission checks every box; the row
            # says otherwise, and the report cannot claim both.
            problems.append(f"evidence {row['id']} is {row['state']}, but a normal submission "
                            "needs every criterion met: finish it, or submit with --blocked")
    return problems


def gate_problems(
    d: Path, run: str, owner: str, rep: Path, meta: dict[str, str], body: str, blocked: bool,
    verify_skipped: bool = False,
) -> list[str]:
    """Every structural, acceptance, scope, and diff check the gate applies to a report.

    `cmd_submit` runs these before spending a verification run. A changed-evidence
    re-review runs exactly the same list, because a report edited after review is new
    evidence, and new evidence has to clear the gate the original body cleared - not
    only the registered verify command.
    """
    problems: list[str] = []
    secs = sections(body)

    expected_round = int(re.search(r"-report-(\d+)\.mdx$", rep.name).group(1))
    expected_meta = {
        "run": run,
        "owner": owner,
        "round": str(expected_round),
    }
    if owner != "orch":
        expected_meta["task"] = owner
    for key, expected in expected_meta.items():
        if meta.get(key) != expected:
            problems.append(f"frontmatter {key!r} must be {expected!r}, got {meta.get(key)!r}")
    if meta.get("harness") not in SUPPORTED_HARNESSES:
        problems.append(f"frontmatter 'harness' must be one of: {', '.join(SUPPORTED_HARNESSES)}")
    # A block is the honest stop, so it never waits on model bookkeeping; an unrecorded
    # model reads as the requested one in the outcome log.
    if not blocked and meta.get("requested_model") and not meta.get("actual_model"):
        problems.append(
            "record actual_model before submission (it may equal requested_model): "
            f"`docket set-model {run} {owner} --actual MODEL`"
        )
    if not blocked and meta.get("requested_effort") and not meta.get("actual_effort"):
        problems.append(
            "record actual_effort before submission (it may equal requested_effort): "
            f"`docket set-model {run} {owner} --actual MODEL --effort EFFORT`"
        )

    required = REQUIRED_ORCH_REPORT_SECTIONS if owner == "orch" else REQUIRED_TASK_REPORT_SECTIONS
    for name in required:
        if name not in secs:
            problems.append(f"missing section '## {name}'")
        elif is_empty(secs[name]):
            problems.append(f"section '## {name}' is empty")
    for name in duplicate_sections(body, required):
        problems.append(f"duplicate section '## {name}': keep one")

    try:
        offset = body_line_offset(rep.read_text(), body)
    except OSError:
        offset = 0
    problems.extend(placeholder_problems(body, PLACEHOLDER, offset, quoted_sections=("Acceptance",)))

    acc = secs.get("Acceptance", "")
    unchecked = [l.strip() for l in acc.splitlines() if re.match(r"^\s*-\s*\[\s*\]", l)]
    if owner != "orch":
        task = d / f"{owner}-task.mdx"
        if not task.is_file():
            problems.append(f"missing assignment {task.name}")
        else:
            task_meta, task_body = parse(task.read_text())
            task_acceptance = checkbox_items(sections(task_body).get("Acceptance criteria", ""))
            report_acceptance = checkbox_items(acc)
            if task_acceptance != report_acceptance:
                problems.append("report acceptance criteria do not match the task acceptance criteria")
            if (
                not blocked
                and task_meta.get("executor", "implementor") == "implementor"
                and task_meta.get("scope_status") != "ready"
            ):
                problems.append("implementor discovery scope is not ready; submit it with `docket scope`")
        coverage, coverage_detail, changed, outside = assignment_evidence(d, owner)
        print(f"diff coverage: {coverage} - {coverage_detail}")
        if coverage == COVERAGE_AVAILABLE:
            if outside:
                problems.append(
                    "paths changed since the task baseline outside the task scope: "
                    + ", ".join(outside) + generated_path_hint(outside)
                )
            reported_files = secs.get("Files changed", "")
            omitted = [path for path in changed if not names_path(reported_files, path)]
            if omitted:
                problems.append("task-local changes omitted from 'Files changed': " + ", ".join(omitted))
        elif coverage == COVERAGE_UNAVAILABLE and not blocked:
            problems.append(
                f"diff coverage is unavailable, so task-local changes cannot be verified: "
                f"{coverage_detail}. This is not an empty diff. Restore the Git evidence, or "
                "declare `evidence_mode: documents-only` in plan.mdx for a run without it"
            )
        problems.extend(dependency_problems(d, owner))
    else:
        planned = planned_tasks(d)
        terminal = {"approved", "waived", "completed"}
        incomplete = [o for o in planned if state_of(d, o)[1] not in terminal]
        if incomplete:
            problems.append("orchestrator report cannot submit before tasks are decided: " + ", ".join(incomplete))
        reported = set(re.findall(r"\bT\d+\b", secs.get("Task outcomes", "")))
        missing = [o for o in planned if o not in reported]
        if missing:
            problems.append("orchestrator task outcomes omit planned tasks: " + ", ".join(missing))
        waived = [o for o in planned if state_of(d, o)[1] == "waived"]
        exceptions = stated(secs.get("Exceptions and waivers", "")).lower()
        if waived and exceptions in ("", "none", "none."):
            problems.append("orchestrator report must explain every waived task")
    if blocked:
        needed = secs.get("Decisions needed", "")
        # The template's own comment sits above whatever the worker wrote, so compare
        # the authored text alone: "none" under that comment is still none.
        if stated(needed).lower() in ("", "none", "none."):
            problems.append(
                "status is blocked but 'Decisions needed' says none - state what the reviewer must decide"
            )
    elif owner != "orch":
        if unchecked:
            problems.append(
                f"{len(unchecked)} unchecked acceptance criteria - finish them, or submit with --blocked"
            )
        if not re.search(r"^\s*-\s*\[[xX]\]", acc, re.M):
            problems.append("no acceptance criteria are checked off")
    problems.extend(evidence_problems(d, owner, body, blocked, verify_skipped))
    return problems
