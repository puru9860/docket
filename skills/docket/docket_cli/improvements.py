"""The improvement backlog and run retrospectives."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import STATE_DIR, die, stamp
from .frontmatter import parse, render
from .paths import decisions, next_numbered, root
from .publication import publish, publish_exclusive
from .policy import need_run
from .state import list_incidents
from .feedback import feedback_dir
from .five_role import correction_attempts


IMPROVEMENT_STATUSES = ("observed", "proposed", "trial", "adopted", "rejected")
IMPROVEMENTS_DIR_NAME = "improvements"


def improvements_dir() -> Path:
    return root() / STATE_DIR / IMPROVEMENTS_DIR_NAME


def read_finding(path: Path) -> dict:
    try:
        meta, body = parse(path.read_text())
    except (OSError, ValueError):
        return {}
    if not meta.get("id"):
        return {}
    finding = dict(meta)
    finding["body"] = body
    return finding


def list_findings() -> list[tuple[Path, dict]]:
    out = []
    if improvements_dir().is_dir():
        for path in sorted(improvements_dir().glob("*.mdx")):
            finding = read_finding(path)
            if finding:
                out.append((path, finding))
    return out


def finding_incidents(finding: dict) -> tuple[int, int]:
    """Independent (incident, run) counts. Corroborating roles on one task
    incident count once, never as independent confirmations."""
    incidents = set()
    runs = set()
    for token in str(finding.get("evidence", "")).split(","):
        token = token.strip()
        if not token:
            continue
        head = token.split(":")[0]
        parts = head.split("/")
        if len(parts) >= 2:
            runs.add(parts[0])
            incidents.add("/".join(parts[:2]))
        else:
            incidents.add(head)
    return len(incidents), len(runs)


def cmd_improvements(a: argparse.Namespace) -> None:
    """List and advance the cross-run improvement backlog."""
    if a.add:
        title = (a.title or "").strip()
        if not title:
            die("--add requires --title TEXT")
        improvements_dir().mkdir(parents=True, exist_ok=True)
        # The number comes from file names, readable or not: an unparsable
        # finding still reserves its number, so a gap never reuses it.
        # This directory is shared across projects, so no run lock covers it;
        # exclusive creation plus retry is the no-overwrite guarantee.
        for _ in range(100):
            existing = sorted(improvements_dir().glob("I*.mdx")) \
                if improvements_dir().is_dir() else []
            num = next_numbered(existing)
            fid = f"I{num:02d}"
            meta = {"id": fid, "title": title, "category": a.category or "other",
                    "roles": a.role or "-", "models": a.model or "-",
                    "harnesses": "-", "severity": a.severity or "unknown",
                    "status": "observed", "impact": a.impact or "-",
                    "intervention": "-", "evidence": a.evidence or "-",
                    "expected": "-", "change": "-", "eval": "-",
                    "change_revision": "-", "trial_result": "-", "rationale": "-",
                    "scope": "project-local", "updated_at": stamp()}
            try:
                publish_exclusive(improvements_dir() / f"{fid}.mdx",
                        render(meta, (a.body or "Candidate improvement under triage.") + "\n"))
            except FileExistsError:
                continue
            break
        else:
            die("could not record a finding; retry the command")
        print(f"recorded finding {fid}: {title}")
        return
    if a.propose or a.trial or a.adopt or a.reject:
        advance_finding(a)
        return
    rows = []
    for path, finding in list_findings():
        if a.category and finding.get("category") != a.category:
            continue
        if a.status and finding.get("status") != a.status:
            continue
        if a.role and a.role not in str(finding.get("roles", "")).split(","):
            continue
        if a.model and a.model not in str(finding.get("models", "")):
            continue
        if a.run and a.run not in str(finding.get("evidence", "")):
            continue
        incidents, runs = finding_incidents(finding)
        rows.append((path.stem, finding, incidents, runs))
    if not rows:
        print("no findings match")
        return
    print(f"{len(rows)} finding(s), independent incident and run counts shown")
    for stem, finding, incidents, runs in rows:
        print(f"  {stem}  [{finding.get('status', '?')}] {finding.get('category', '?')}  "
              f"incidents={incidents} runs={runs}  {finding.get('title', '')}")


def advance_finding(a: argparse.Namespace) -> None:
    """Move one finding along observed -> proposed -> trial -> adopted."""
    fid = a.propose or a.trial or a.adopt or a.reject
    path = improvements_dir() / f"{fid}.mdx"
    if not path.is_file():
        die(f"no finding {fid!r}")
    finding = read_finding(path)
    if not finding:
        die(f"finding {fid!r} is unreadable")
    status = finding.get("status", "observed")
    if a.propose:
        if status != "observed":
            die(f"finding {fid} is {status}, not observed; only observed findings propose")
        for field, flag in (("expected", a.expected), ("change", a.change),
                            ("eval", a.eval)):
            if not (flag or "").strip():
                die(f"--propose requires --expected, --change, and --eval; missing {field}")
        finding.update({"status": "proposed", "expected": a.expected.strip(),
                        "change": a.change.strip(), "eval": a.eval.strip(),
                        "updated_at": stamp()})
    elif a.trial:
        if status != "proposed":
            die(f"finding {fid} is {status}, not proposed; only proposed findings trial")
        finding.update({"status": "trial", "trial_result": (a.note or "trial started").strip(),
                        "updated_at": stamp()})
    elif a.adopt:
        if status != "trial":
            die(f"finding {fid} is {status}, not trial; only trial findings adopt")
        if not (a.change_rev or "").strip() or not (a.trial_result or "").strip():
            die("--adopt requires --change-rev and --trial-result")
        finding.update({"status": "adopted", "change_revision": a.change_rev.strip(),
                        "trial_result": a.trial_result.strip(), "updated_at": stamp()})
    else:
        if status == "adopted":
            die(f"finding {fid} is already adopted; record a new finding instead")
        if not (a.rationale or "").strip():
            die("--reject requires --rationale TEXT")
        finding.update({"status": "rejected", "rationale": a.rationale.strip(),
                        "updated_at": stamp()})
    body = finding.pop("body", "")
    publish(path, render(finding, body))
    print(f"finding {fid} is now {finding['status']}")


def cmd_retrospective(a: argparse.Namespace) -> None:
    """Summarize a run mechanically: corrections, delivery, stalls, and use.

    No model is called. Premium-token use is reported unknown unless measured
    elsewhere; invocation counts appear only as an explicitly labelled proxy.
    Raw observations are never quoted into prompts here.
    """
    d = need_run(a.run)
    corrections = 0
    if (d / ".corrections").is_dir():
        for path in (d / ".corrections").glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            corrections += correction_attempts(record if isinstance(record, dict) else {})
    decisions = len(list(d.glob("*-decision-*.mdx")))
    verifications = len(list(d.glob("*-verification-*.mdx")))
    escalations = sorted(p.name for p in (d / ".escalations").glob("*.json")) \
        if (d / ".escalations").is_dir() else []
    incidents = list_incidents(d)
    kinds: dict[str, int] = {}
    for log in sorted((d / ".delivery").glob("*/log.jsonl")) if (d / ".delivery").is_dir() else []:
        try:
            lines = log.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                kinds[json.loads(line).get("kind", "?")] = \
                    kinds.get(json.loads(line).get("kind", "?"), 0) + 1
            except ValueError:
                continue
    feedback = len(list(feedback_dir(d).glob("*.mdx"))) if feedback_dir(d).is_dir() else 0
    print(f"retrospective for run {a.run} (mechanical summary, no model calls)")
    print(f"  correction attempts: {corrections}")
    print(f"  decisions recorded: {decisions}")
    print(f"  verifier findings: {verifications}")
    print(f"  open escalations: {', '.join(escalations) or 'none'}")
    print(f"  stall incidents: {len(incidents)} "
          f"({sum(1 for i in incidents if i.get('state') == 'open')} open)")
    print(f"  delivery transport records: "
          + (", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) or "none"))
    print(f"  feedback observations: {feedback}")
    print("  premium tokens: unknown (invocation counts above are a proxy, not spend)")
