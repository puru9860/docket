"""Per-model outcome records, scorecards, and reviewed model profiles."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
from pathlib import Path

from .common import STATE_DIR, die, stamp
from .frontmatter import parse, render, sections
from .paths import _ordered, latest, owners, read_dispatch, root
from .publication import publish, publish_exclusive
from .policy import need_run, task_executor
from .locks import run_lock
from .state import read_transition
from .profiles import (
    PROFILE_TOKEN_BUDGET, estimate_tokens, match_model_profile, model_aliases_path,
    normalized_model, profile_candidates, profile_revision, profile_version, read_profile,
    user_profiles_dir,
)
from .feedback import feedback_dir, feedback_log_path, log_feedback, next_feedback_id
from .sessions import session_transcript_path, tokens_h


#
# Every rejection of an executor's work is a case about the model that did it:
# a refused submit, a failed verification, requested changes, a block, an
# escalation. Verdicts are recorded too, so rates have a denominator. Cases
# accumulate in the user-level log across projects; a periodic review turns the
# recurring ones into a short per-model profile that every later prompt for
# that model carries. Raw cases never enter prompts, only reviewed rules.

REJECT_CASES = (("gate", "refused"), ("verification", "fail"),
                ("decision", "changes-requested"), ("submit", "blocked"),
                ("escalation", "opened"))


def task_model(d: Path, owner: str) -> tuple[str, str, str]:
    """(model, harness, model profile revision) that executed owner's current round."""
    dispatch = read_dispatch(d, owner) or {}
    observed = str(dispatch.get("model_observed", "") or "")
    model = observed if observed not in ("", "unobserved", "unknown") else ""
    # The dispatch binds the current round, so the model it requests outranks the
    # task's older fields; a switch or a resume moved it, and the task never did.
    model = model or str(dispatch.get("model_requested", "") or "")
    harness = ""
    try:
        tmeta, _ = parse((d / f"{owner}-task.mdx").read_text())
        model = model or str(tmeta.get("actual_model", "") or tmeta.get("requested_model", ""))
        harness = str(tmeta.get("harness", "") or "")
    except (OSError, ValueError):
        pass
    return (normalized_model(model) or "unknown", harness or "-",
            str(dispatch.get("model_profile", "") or ""))


def record_outcome(d: Path, owner: str, rnd: object, kind: str, result: str,
                   text: str = "", pointer: str = "") -> None:
    """Append one outcome case about the model that executed owner. Never raises."""
    if owner == "orch":
        return
    try:
        model, harness, profile = task_model(d, owner)
        log_feedback({"origin": "outcome", "run": d.name, "task": owner, "round": str(rnd),
                      "kind": kind, "result": result, "model": model, "harness": harness,
                      "model_profile": profile or "-", "executor": task_executor(d, owner),
                      "text": re.sub(r"<!--.*?-->", "", text or "", flags=re.S).strip()[:4000],
                      "pointer": pointer})
    except (OSError, ValueError, SystemExit):
        return


def import_run_outcomes(d: Path) -> int:
    """Backfill outcome cases from a run's verification and decision artifacts.

    Runs from before outcomes were recorded still hold the verdicts and
    findings; importing them gives the first review real history. A case
    already in the log is never recorded twice.
    """
    log = feedback_log_path()
    try:
        project = str(root())
    except SystemExit:
        project = ""
    known = {(r.get("project"), r.get("run"), r.get("task"), r.get("pointer"))
             for r in outcome_records(log) if r.get("origin") == "outcome"}
    added = 0
    for owner in (o for o in owners(d) if o != "orch"):
        try:
            tmeta, _ = parse((d / f"{owner}-task.mdx").read_text())
        except (OSError, ValueError):
            tmeta = {}
        artifacts = [("verification", p) for p in _ordered(d.glob(f"{owner}-verification-*.mdx"))]
        artifacts += [("decision", p) for p in _ordered(d.glob(f"{owner}-decision-*.mdx"))]
        for kind, path in artifacts:
            if (project, d.name, owner, path.name) in known:
                continue
            try:
                meta, body = parse(path.read_text())
            except (OSError, ValueError):
                continue
            result = str(meta.get("result" if kind == "verification" else "verdict", ""))
            if kind == "decision" and meta.get("applied") != "yes":
                continue
            if not result:
                continue
            rnd = str(meta.get("round", "?"))
            try:
                rmeta, _ = parse((d / f"{owner}-report-{int(rnd):02d}.mdx").read_text())
            except (OSError, ValueError):
                rmeta = {}
            model = normalized_model(str(rmeta.get("actual_model", "") or tmeta.get("actual_model", "")
                                         or rmeta.get("requested_model", "")
                                         or tmeta.get("requested_model", "")))
            secs = sections(body)
            text = (secs.get("Findings", "") if kind == "verification" else
                    secs.get("Required changes", "") if result == "changes-requested"
                    else secs.get("Reason", ""))
            log_feedback({"origin": "outcome", "run": d.name, "task": owner, "round": rnd,
                          "kind": kind, "result": result, "model": model or "unknown",
                          "harness": str(tmeta.get("harness", "") or "-"),
                          "model_profile": "-", "executor": task_executor(d, owner),
                          "text": re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()[:4000],
                          "pointer": path.name, "imported": True,
                          "at_source": stamp(path.stat().st_mtime)})
            added += 1
    return added


def outcome_records(path: Path | None, since: str = "") -> list[dict]:
    if path is None or not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("origin") in ("outcome", "usage", "profile") \
                and (not since or str(record.get("at", "")) >= since):
            if record.get("origin") == "outcome":
                record["model"] = normalized_model(str(record.get("model", ""))) or "unknown"
            elif record.get("origin") == "usage":
                record["models"] = [normalized_model(str(m)) for m in record.get("models") or []]
            out.append(record)
    return out


def is_reject(record: dict) -> bool:
    return (record.get("kind"), record.get("result")) in REJECT_CASES


def model_scorecard(records: list[dict], model: str) -> dict[str, object]:
    """Tasks, first-pass rate, rounds to accept, reject counts, and tokens for one model."""
    tasks: dict[tuple, dict] = {}
    rejects: dict[str, int] = {}
    for r in records:
        if r.get("origin") != "outcome" or r.get("model") != model:
            continue
        key = (r.get("project"), r.get("run"), r.get("task"))
        task = tasks.setdefault(key, {"changes": 0, "accepted": 0, "profile": ""})
        if is_reject(r):
            rejects[str(r.get("kind"))] = rejects.get(str(r.get("kind")), 0) + 1
        if r.get("kind") == "decision":
            if r.get("result") == "changes-requested":
                task["changes"] += 1
            elif r.get("result") in ("approved", "waived"):
                task["accepted"] = int(str(r.get("round", "0")) or 0) if \
                    str(r.get("round", "")).isdigit() else 1
                task["profile"] = str(r.get("model_profile", "-"))
    decided = [t for t in tasks.values() if t["accepted"]]
    tokens = sum(int(r.get("total") or 0) for r in records
                 if r.get("origin") == "usage" and model in (r.get("models") or []))
    by_profile: dict[str, list[dict]] = {}
    for t in decided:
        by_profile.setdefault(t["profile"] or "-", []).append(t)
    return {
        "tasks": len(tasks), "decided": len(decided),
        "first_pass": sum(1 for t in decided if not t["changes"]),
        "rounds": (sum(t["accepted"] for t in decided) / len(decided)) if decided else 0.0,
        "rejects": rejects, "tokens": tokens,
        "by_profile": {name: (len(items), sum(1 for t in items if not t["changes"]))
                       for name, items in by_profile.items()},
    }


def adopted_profile(model: str) -> tuple[str, dict[str, str]]:
    """The user's adopted profile name and frontmatter for a model, if any."""
    name = match_model_profile(model)
    if not name or not (user_profiles_dir() / f"{name}.md").is_file():
        return "", {}
    return name, read_profile(name)[0]


def model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", normalized_model(model).lower()).strip("-") or "model"


def pending_reviews(records: list[dict]) -> list[tuple[str, int, str]]:
    """(model, new reject cases, reviewed_through) for models with cases since review."""
    out = []
    for model in sorted({str(r.get("model")) for r in records
                         if r.get("origin") == "outcome" and r.get("model") not in ("", "unknown")}):
        _, meta = adopted_profile(model)
        through = str(meta.get("reviewed_through", "") or "")
        fresh = [r for r in records if r.get("origin") == "outcome" and r.get("model") == model
                 and is_reject(r) and str(r.get("at", "")) > through]
        if fresh:
            out.append((model, len(fresh), through))
    return out


def cmd_models(a: argparse.Namespace) -> None:
    """Per-model scorecards, a review packet of new reject cases, or adopting a profile."""
    if a.adopt:
        adopt_model_profile(Path(a.adopt).expanduser())
        return
    if a.alias:
        name, sep, target = a.alias.partition("=")
        if not sep or not name.strip() or not target.strip():
            die("--alias takes NAME=ID, for example 'DeepSeek V4.1 Flash=deepseek/deepseek-flash'")
        path = model_aliases_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        kept = [line for line in (path.read_text().splitlines() if path.is_file() else [])
                if line.partition("=")[0].strip().lower() != name.strip().lower()]
        publish(path, "\n".join([*kept, f"{name.strip()} = {target.strip()}"]) + "\n")
        print(f"{name.strip()} is now counted as {target.strip()}, in past cases too")
        return
    if a.import_runs is not None:
        runs_root = root() / STATE_DIR / "runs"
        names = a.import_runs
        if not names and runs_root.is_dir():
            names = sorted(path.name for path in runs_root.iterdir() if path.is_dir())
        total = 0
        for name in names:
            added = import_run_outcomes(need_run(name))
            total += added
            print(f"imported {added} outcome case(s) from {name}")
        print(f"imported {total} case(s) in all; `docket models` shows the scorecards")
        return
    log = Path(a.log).expanduser() if a.log else feedback_log_path()
    records = outcome_records(log, a.since)
    if a.review:
        print(model_review_packet(records, normalized_model(a.review), a.since))
        return
    models = sorted({str(r.get("model")) for r in records
                     if r.get("origin") == "outcome" and r.get("model")})
    if not models:
        print("no outcome cases recorded yet: they accumulate as tasks are verified and decided")
        return
    print(f"per-model outcomes from {log}" + (f" since {a.since}" if a.since else ""))
    for model in models:
        card = model_scorecard(records, model)
        name, meta = adopted_profile(model)
        rejects = ", ".join(f"{k} {v}" for k, v in sorted(card["rejects"].items())) or "none"
        rate = (f"{card['first_pass']}/{card['decided']} first-pass"
                if card["decided"] else "no decided task yet")
        print(f"  {model}: {card['tasks']} task(s), {rate}, "
              f"{card['rounds']:.1f} rounds to accept, rejects: {rejects}, "
              f"{tokens_h(card['tokens'])} tokens; profile: "
              + (f"{name} v{meta.get('version', '?')}" if name else "none"))
    due = [item for item in pending_reviews(records) if item[1] >= 3]
    for model, count, _ in due:
        print(f"  review due: {model} has {count} new reject case(s); "
              f"run `docket models --review {shlex.quote(model)}`")


def model_review_packet(records: list[dict], model: str, since: str) -> str:
    """Everything a reviewing session needs to update one model's profile."""
    name, meta = adopted_profile(model)
    through = since or str(meta.get("reviewed_through", "") or "")
    card = model_scorecard(records, model)
    cases = [r for r in records if r.get("origin") == "outcome" and r.get("model") == model
             and is_reject(r) and str(r.get("at", "")) > through]
    lines = [f"# Model review: {model}", ""]
    if name:
        lines.append(f"Current profile: {user_profiles_dir() / (name + '.md')} (version "
                     f"{meta.get('version', '?')}, reviewed through {through or 'never'}):")
        lines += ["", read_profile(name)[1].strip(), ""]
    else:
        lines += ["No profile yet for this model.", ""]
    lines.append(f"Scorecard: {card['tasks']} task(s), {card['first_pass']}/{card['decided']} "
                 f"accepted without a correction, {card['rounds']:.1f} rounds to accept, "
                 f"{tokens_h(card['tokens'])} tokens.")
    for profile, (n, first) in sorted(card["by_profile"].items()):
        lines.append(f"- with profile {profile}: {first}/{n} first-pass")
    lines += ["", f"## {len(cases)} reject case(s) since {through or 'the start'}", ""]
    labels = {"decision": "Changes requested", "verification": "Verification failed",
              "gate": "Submit refused by the gate", "submit": "Blocked",
              "escalation": "Correction budget escalated"}
    tasks_seen = set()
    for kind in ("decision", "verification", "gate", "submit", "escalation"):
        group = [r for r in cases if r.get("kind") == kind]
        if not group:
            continue
        lines += [f"### {labels[kind]} ({len(group)})", ""]
        for r in group:
            tasks_seen.add((r.get("project"), r.get("run"), r.get("task")))
            where = f"{Path(str(r.get('project', ''))).name}/{r.get('run')}/{r.get('task')}"
            text = re.sub(r"\s+", " ", str(r.get("text", ""))).strip()
            lines.append(f"- {where} round {r.get('round')} ({str(r.get('at', ''))[:10]}): "
                         f"{text[:600] or '(no text recorded)'}")
            lines.append(f"  evidence: {r.get('project')}/.docket/runs/{r.get('run')}/"
                         f"{r.get('pointer')}")
        lines.append("")
    newest = max((str(r.get("at", "")) for r in cases), default=through)
    version = (profile_version(meta) or 0) + 1 if name else 1
    lines += [
        "## How to update the profile",
        "",
        f"These cases come from {len(tasks_seen)} task(s). Distill what recurs into at most "
        "eight short imperative rules for this model, each about a behavior it should change.",
        "Keep a rule only when cases from at least two different tasks support it; a single "
        "run is not a pattern. Keep existing rules the scorecard shows helping, drop the ones "
        "that did not, and leave out project names, paths, and case details.",
        "Write the profile to a file with this frontmatter and a bulleted body, then adopt it:",
        "",
        "```",
        "---",
        f"profile: {name or 'model-' + model_slug(model)}",
        f"models: {model}",
        f"version: {version}",
        "card: false",
        f"reviewed_through: {newest or stamp()}",
        f"evidence_cases: {len(cases)}",
        f"evidence_tasks: {len(tasks_seen)}",
        "---",
        "",
        f"# {model}",
        "",
        "- One behavior rule per line.",
        "```",
        "",
        "`docket models --adopt FILE` validates it and installs it for every later prompt "
        "to this model.",
    ]
    return "\n".join(lines)


def adopt_model_profile(path: Path) -> None:
    """Validate a reviewed profile and install it in the user's profile tree."""
    try:
        meta, body = parse(path.read_text())
    except (OSError, ValueError) as exc:
        die(f"cannot read {path}: {exc}")
    name = str(meta.get("profile", "") or "")
    models = [m.strip() for m in str(meta.get("models", "")).split(",") if m.strip()]
    problems = []
    if not re.fullmatch(r"model-[a-z0-9._-]+", name):
        problems.append("profile must be model-<slug>, so it never shadows a task card")
    if not models:
        problems.append("models must name the exact model id(s) it applies to")
    if meta.get("card", "") == "true":
        problems.append("card must be false: a model profile applies by model, not by topic")
    if not str(meta.get("reviewed_through", "") or "").strip():
        problems.append("reviewed_through must name the newest case the review covered")
    rules = [line for line in body.splitlines() if line.strip().startswith("- ")]
    if not rules:
        problems.append("the body needs at least one '- ' rule")
    if estimate_tokens(body) > PROFILE_TOKEN_BUDGET // 2:
        problems.append(f"the body is about {estimate_tokens(body)} tokens; keep it under "
                        f"{PROFILE_TOKEN_BUDGET // 2} so task cards still fit the guidance budget")
    version = profile_version(meta)
    if version is None:
        problems.append(f"version must be a whole number, got {meta.get('version')!r}")
    target = user_profiles_dir() / f"{name}.md"
    if target.is_file() and version is not None:
        old, _ = parse(target.read_text())
        if version <= (profile_version(old) or 0):
            problems.append(f"version must be above the adopted version {old.get('version')}")
    # Matching takes the first profile naming a model, so a second one would install
    # and never apply; a model's rules evolve as versions of its one profile.
    wanted = {normalized_model(m).lower() for m in models}
    for other in profile_candidates():
        if other.stem == name:
            continue
        try:
            other_meta, _ = parse(other.read_text())
        except (OSError, ValueError):
            continue
        if other_meta.get("card", "") == "true":
            continue
        shared = sorted(wanted & {normalized_model(m.strip()).lower()
                                  for m in str(other_meta.get("models", "")).split(",")
                                  if m.strip()})
        if shared:
            problems.append(f"{', '.join(shared)} already has the profile {other.stem}; adopt a "
                            f"new version of {other.stem} instead of a second profile")
    if problems:
        die(f"cannot adopt {path.name}:\n  - " + "\n  - ".join(problems))
    history = user_profiles_dir() / "history"
    history.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        old, _ = parse(target.read_text())
        shutil.copy2(target, history / f"{name}-v{old.get('version', '0')}.md")
    publish(target, path.read_text())
    revision = profile_revision(name)
    log_feedback({"origin": "profile", "profile": name, "models": models,
                  "revision": revision, "reviewed_through": meta.get("reviewed_through", ""),
                  "evidence_cases": meta.get("evidence_cases", ""),
                  "evidence_tasks": meta.get("evidence_tasks", "")})
    print(f"adopted {name} version {meta.get('version')} ({revision}) for "
          f"{', '.join(models)}; every prompt dispatched to it from now on carries it")


def cmd_feedback_digest(a: argparse.Namespace) -> None:
    """Summarize the user-level feedback log: where docket costs roles effort."""
    path = Path(a.log).expanduser() if a.log else feedback_log_path()
    if path is None or not path.is_file():
        print("no feedback recorded yet" + (f" at {path}" if path else " (the log is off)"))
        return
    records = []
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and (not a.since or str(record.get("at", "")) >= a.since):
            records.append(record)
    if not records:
        print(f"no feedback in {path}" + (f" since {a.since}" if a.since else ""))
        return

    def quiet(record: dict) -> bool:
        return str(record.get("body", "")).strip().lower().rstrip(".") in ("none", "")

    usage = [r for r in records if r.get("origin") == "usage"]
    # The same normalized records `docket models` reads, so an aliased spelling counts
    # toward its model rather than splitting it under the review threshold.
    due = [item for item in pending_reviews(outcome_records(path, a.since)) if item[1] >= 3]
    records = [r for r in records if r.get("origin") not in ("usage", "outcome", "profile")]
    agent = [r for r in records if r.get("origin") != "machine"]
    machine = [r for r in records if r.get("origin") == "machine"]
    projects = {str(r.get("project", "")) for r in records}
    runs = {(str(r.get("project", "")), str(r.get("run", ""))) for r in records}
    print(f"feedback digest: {len(records)} record(s) from {len(runs)} run(s) in "
          f"{len(projects)} project(s) - {path}")
    print(f"  agent reports: {len(agent)} ({sum(1 for r in agent if quiet(r))} said none); "
          f"machine observations: {len(machine)}")
    if due:
        print("\nmodel profiles due for review")
        for model, count, through in due:
            print(f"  {model}: {count} new reject case(s) since "
                  f"{through or 'no review yet'}; run `docket models --review {shlex.quote(model)}`")
    latest: dict[tuple[str, str, str, str], dict] = {}
    for record in usage:
        key = tuple(str(record.get(k, "")) for k in ("project", "run", "harness", "session_id"))
        latest[key] = record
    if latest:
        print("\ntoken usage by role (latest archive of each harness session)")
        by_role: dict[str, list[dict]] = {}
        for record in latest.values():
            by_role.setdefault(str(record.get("role", "?")), []).append(record)
        for role, items in sorted(by_role.items()):
            total = sum(int(r.get("total") or 0) for r in items)
            runs_seen = {(r.get("project"), r.get("run")) for r in items}
            models = sorted({m for r in items for m in (r.get("models") or [])})
            print(f"  {role:<14} {len(items)} session(s) in {len(runs_seen)} run(s): "
                  f"{tokens_h(total)} total, {tokens_h(total // len(items))} per session, "
                  f"{sum(int(r.get('calls') or 0) for r in items)} calls, "
                  f"{tokens_h(sum(int(r.get('output') or 0) for r in items))} output"
                  + (f" ({', '.join(models)})" if models else ""))
    by_role: dict[str, list[dict]] = {}
    for record in agent:
        by_role.setdefault(str(record.get("role", "?")), []).append(record)
    if by_role:
        print("\nfriction by role (reports naming a cost / all reports)")
        for role, items in sorted(by_role.items()):
            costly = [r for r in items if not quiet(r)]
            print(f"  {role:<12} {len(costly)}/{len(items)}")
    categories: dict[tuple[str, str], int] = {}
    for record in records:
        if record.get("origin") != "machine" and quiet(record):
            continue
        key = (str(record.get("origin", "agent")), str(record.get("category", "other")))
        categories[key] = categories.get(key, 0) + 1
    if categories:
        print("\nwhere the cost lands (origin/category: count)")
        for (origin, category), count in sorted(categories.items(), key=lambda kv: -kv[1]):
            print(f"  {origin}/{category}: {count}")
    recurring: dict[str, list[dict]] = {}
    for record in records:
        if record.get("origin") != "machine" and quiet(record):
            continue
        text = re.sub(r"\s+", " ", str(record.get("body", ""))).strip()
        pattern = re.sub(r"\b(?:T\d+|R\d+|round \d+|\d+)\b", "#", text.lower())[:160]
        recurring.setdefault(pattern, []).append(record)
    ranked = sorted(recurring.values(), key=lambda items: (-len(items), str(items[-1].get("at"))))
    if ranked:
        print(f"\nrecurring items (top {min(a.top, len(ranked))})")
        for items in ranked[:a.top]:
            last = items[-1]
            roles = ",".join(sorted({str(r.get("role", "?")) for r in items}))
            body = re.sub(r"\s+", " ", str(last.get("body", ""))).strip()
            print(f"  {len(items)}x [{last.get('category', 'other')}] ({roles}) "
                  f"{body[:220]}")
            print(f"      latest: {last.get('project', '')} {last.get('run', '')} "
                  f"{last.get('task', '-')} round {last.get('round', '-')} at {last.get('at', '')}")


def cmd_feedback(a: argparse.Namespace) -> None:
    """Record or list short, evidence-linked workflow observations.

    Feedback is always optional: recording nothing is valid, and a failure to
    record feedback never rolls back a lifecycle operation.
    """
    if a.digest:
        cmd_feedback_digest(a)
        return
    if not a.run:
        die("give a run id, or --digest to summarize feedback across runs and projects")
    d = need_run(a.run)
    if a.list:
        found = []
        if feedback_dir(d).is_dir():
            for path in sorted(feedback_dir(d).glob("*.mdx")):
                try:
                    meta, _ = parse(path.read_text())
                except (OSError, ValueError):
                    continue
                if a.category and meta.get("category", "") != a.category:
                    continue
                if a.role and meta.get("author_role", "") != a.role:
                    continue
                found.append((path.stem, meta))
        if not found:
            print(f"run {a.run}: no recorded observations")
            return
        print(f"run {a.run}: {len(found)} observation(s)")
        for stem, meta in found:
            print(f"  {stem}  {meta.get('author_role', '?'):<12} "
                  f"{meta.get('category', '?'):<12} "
                  f"{meta.get('task', '-')}:{meta.get('round', '-')}  "
                  f"confidence={meta.get('confidence', '?')}")
        return
    if a.import_ops:
        imported = import_operational_feedback(d, a.run)
        print(f"run {a.run}: reconciled {imported} operational observation(s) "
              "from durable transport records")
        return
    if not a.add:
        die("use --add to record an observation, --list to show them, or --import-ops")
    body = (a.body or "").strip()
    if not body:
        die("--add requires --body TEXT")
    fid = next_feedback_id(d)
    # The dispatch record already knows which prompt and model a task round was
    # given, so an agent's report is tied to them without restating either.
    dispatch = read_dispatch(d, a.task) if a.task and re.fullmatch(r"T\d+|orch", a.task) else {}
    if dispatch and not a.prompt_digest and str(dispatch.get("prompt_role", "")) == (a.role or ""):
        a.prompt_digest = str(dispatch.get("prompt_digest", ""))
    if dispatch and not a.model:
        observed = str(dispatch.get("model_observed", "") or "")
        a.model = observed if observed not in ("", "unobserved") else str(
            dispatch.get("model_requested", "") or "")
    # The calling harness session points at the full conversation behind the
    # report, so the report can be checked against what actually happened.
    ref = getattr(a, "harness_session_ref", None) or {}
    if ref and not a.harness:
        a.harness = " ".join(v for v in (ref["harness"], ref.get("version", "")) if v)
    transcript = ""
    if ref:
        transcript = session_transcript_path(ref)
    meta = {"id": fid, "author_role": a.role or "unknown",
            "category": a.category or "other",
            "run": a.run, "task": a.task or "-", "round": a.round or "-",
            "model": a.model or "-", "provider": a.provider or "-",
            "harness": a.harness or "-", "origin": a.origin,
            "prompt_digest": a.prompt_digest or "-",
            "profile_revisions": a.profile_revisions or "-",
            "evidence_paths": a.evidence or "-",
            "confidence": a.confidence or "low",
            "observed_at": stamp()}
    if ref:
        meta.update({"session": f"{ref['harness']}:{ref['session_id']}",
                     "session_cwd": ref.get("cwd", "") or "-",
                     "transcript": transcript or "-"})
    text = render(meta, body + "\n")
    with run_lock(d):
        for _ in range(100):
            fid = next_feedback_id(d)
            meta["id"] = fid
            text = render(meta, body + "\n")
            try:
                feedback_dir(d).mkdir(parents=True, exist_ok=True)
                publish_exclusive(feedback_dir(d) / f"{fid}.mdx", text)
            except FileExistsError:
                continue
            except OSError as exc:
                die(f"could not record feedback ({exc}); the lifecycle operation this "
                    "accompanied is unaffected - feedback never blocks it")
            break
        else:
            die("could not record feedback (no free observation id); the lifecycle operation "
                "this accompanied is unaffected - feedback never blocks it")
    log_feedback({"origin": a.origin, "run": a.run, "id": fid, "role": meta["author_role"],
                  "category": meta["category"], "task": meta["task"], "round": meta["round"],
                  "model": meta["model"], "harness": meta["harness"],
                  "prompt_digest": meta["prompt_digest"], "body": body,
                  **({"session_id": ref["session_id"], "cwd": ref.get("cwd", ""),
                      "transcript": transcript} if ref else {})})
    print(f"recorded observation {fid} ({meta['category']})")


def import_operational_feedback(d: Path, run: str) -> int:
    """Reconcile durable transport records into machine observations.

    Existing operational records (expired leases, retries, escalations,
    unfinished transitions) become concise machine observations without any
    model call. Re-running never duplicates an incident already imported.
    """
    existing_refs = set()
    if feedback_dir(d).is_dir():
        for path in feedback_dir(d).glob("*.mdx"):
            try:
                meta, _ = parse(path.read_text())
            except (OSError, ValueError):
                continue
            if meta.get("incident_ref"):
                existing_refs.add(meta["incident_ref"])
    candidates: list[tuple[str, str, str]] = []
    delivery = d / ".delivery"
    if delivery.is_dir():
        for log in sorted(delivery.glob("*/log.jsonl")):
            try:
                lines = log.read_text().splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("kind") in ("expired", "retry", "retired"):
                    ref = f"{log.parent.name}:{entry.get('key')}:{entry.get('kind')}"
                    candidates.append((ref, "delivery",
                                       f"{entry.get('kind')} on {entry.get('key')} "
                                       f"(session {entry.get('session', '-')})"))
    for path in sorted((d / ".escalations").glob("*.json")) if (d / ".escalations").is_dir() else []:
        candidates.append((f"escalation:{path.stem}", "review",
                           f"escalation {path.stem} opened"))
    for owner in owners(d):
        journal = read_transition(d, owner)
        if journal.get("state") == "in-progress":
            candidates.append((f"transition:{owner}:{journal.get('transition')}",
                               "recovery",
                               f"unfinished {journal.get('verdict')} transition for {owner}"))
    imported = 0
    for ref, category, detail in candidates:
        if ref in existing_refs:
            continue
        recorded = False
        with run_lock(d):
            for _ in range(100):
                fid = next_feedback_id(d)
                meta = {"id": fid, "author_role": "docket", "category": category,
                        "run": run, "task": "-", "round": "-",
                        "model": "-", "provider": "-", "harness": "-",
                        "origin": "machine", "prompt_digest": "-", "profile_revisions": "-",
                        "evidence_paths": "-", "confidence": "high",
                        "incident_ref": ref, "observed_at": stamp()}
                body = (f"Observed behavior: {detail}.\n\nImpact: delivery or recovery needed "
                        "attention.\n\nIntervention: none automated.\n\nOutcome: recorded for "
                        "improvement triage.\n\nCandidate improvement: none proposed.\n")
                try:
                    feedback_dir(d).mkdir(parents=True, exist_ok=True)
                    publish_exclusive(feedback_dir(d) / f"{fid}.mdx", render(meta, body))
                except FileExistsError:
                    continue
                except OSError:
                    break
                recorded = True
                break
        if not recorded:
            continue
        existing_refs.add(ref)
        imported += 1
    return imported
