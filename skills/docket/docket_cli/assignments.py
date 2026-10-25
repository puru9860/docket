"""Config ingestion and recorded role assignments; this module never launches sessions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .common import SUPPORTED_HARNESSES, die
from .frontmatter import parse, render
from .paths import dispatch_path, latest, reports, root, scope_path
from .publication import publish
from .policy import (
    ASSIGNMENT_ROLES, assignment_role, assignment_snapshot,
    is_quick_milestone, need_run, recorded_assignment,
)
from .locks import owner_lock
from .state import list_batches


CONFIG_VERSION = 1
DEFAULT_HARNESSES = {"planner": "claude", "implementor": "claude",
                     "reviewer": "codex", "test_author": "codex"}


def config_paths() -> tuple[Path, Path]:
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    home = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    if not home.is_absolute():
        die("XDG_CONFIG_HOME must be an absolute directory")
    return home / "docket/config.json", root() / ".docket/config.json"


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def read_config(path: Path, *, user: bool) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError:
        # A dangling symlink is invalid, not an absent configuration.
        if not path.is_symlink():
            return {}
        die(f"invalid config {path}: dangling symlink")
    except (OSError, UnicodeError) as exc:
        die(f"cannot read config {path}: {exc}")
    try:
        data = json.loads(text, object_pairs_hook=unique_object)
        if not isinstance(data, dict):
            raise ValueError("expected one flat JSON object")
        if type(data.get("version", 1)) is not int or data.get("version", 1) != CONFIG_VERSION:
            raise ValueError("version must be 1")
        allowed = {f"{role}_{field}" for role in ASSIGNMENT_ROLES
                   for field in (("harness", "model", "allowed_models", "pinned") if user
                                 else ("harness", "model"))} | {"version"}
        if user:
            allowed |= {f"{h}_launch_args" for h in SUPPORTED_HARNESSES}
        if set(data) - allowed:
            raise ValueError("unknown or user-only keys: " + ", ".join(sorted(set(data) - allowed)))
        for key, value in data.items():
            if key == "version":
                continue
            if key.endswith("_launch_args"):
                if (not isinstance(value, list) or
                        any(not valid_text(arg) for arg in value)):
                    raise ValueError(f"{key} must be a list of nonempty argument strings")
            elif key.endswith("_pinned"):
                if type(value) is not bool:
                    raise ValueError(f"{key} must be a boolean")
            elif key.endswith("_allowed_models"):
                if not isinstance(value, list) or any(not valid_assignment_model(m) for m in value):
                    raise ValueError(f"{key} must be a list of nonempty model names")
            elif not valid_text(value):
                raise ValueError(f"{key} must be a nonempty string without line breaks")
            elif key.endswith("_harness") and value not in SUPPORTED_HARNESSES:
                raise ValueError(f"{key} must be one of {', '.join(SUPPORTED_HARNESSES)}")
            elif key.endswith("_model") and not valid_assignment_model(value):
                raise ValueError(f"{key} cannot contain frontmatter comment syntax or line separators")
    except (ValueError, TypeError, UnicodeError) as exc:
        die(f"invalid config {path}: {exc}")
    return data


def valid_text(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and len(value.splitlines()) == 1 and not any(ord(c) < 32 for c in value))


def valid_assignment_model(value: object) -> bool:
    return valid_text(value) and parse(render({"model": str(value)}, ""))[0].get("model") == value


def validate_role(role: str, assignment: dict, source: str) -> None:
    allowed = assignment.get("allowed_models")
    model = str(assignment.get("model", ""))
    if model and allowed is not None and model not in allowed:
        die(f"{role} model {model!r} from {source} is outside the allowed models {allowed} "
            f"recorded by {assignment.get('allowed_models_source_file')}")


def pin_refusal(role: str, assignment: dict) -> None:
    die(f"{role} is pinned to {assignment['harness']}/{assignment['model'] or '(session default)'} "
        f"by {assignment['pin_source_file']}; planner overrides are refused. "
        "Ask the user to change that pin for a new run; existing runs retain it")


def resolve_config(planner_harness: str = "claude") -> dict:
    user_path, project_path = config_paths()
    user = read_config(user_path, user=True)
    project = read_config(project_path, user=False)
    record: dict = {"assignment_version": 1}
    for role in ASSIGNMENT_ROLES:
        assignment = {
            "harness": planner_harness if role == "planner" else DEFAULT_HARNESSES[role],
            "model": "", "harness_source": "default", "model_source": "default",
            "harness_source_file": "", "model_source_file": "",
            "allowed_models": user.get(f"{role}_allowed_models"),
            "allowed_models_source_file": str(user_path) if f"{role}_allowed_models" in user else "",
            "pinned": user.get(f"{role}_pinned", False),
            "pin_source_file": str(user_path) if user.get(f"{role}_pinned") else "",
            "assignment_reason": "",
        }
        for source, path, values in (("user", user_path, user), ("project", project_path, project)):
            for field in ("harness", "model"):
                key = f"{role}_{field}"
                if key not in values:
                    continue
                if source == "project" and assignment["pinned"]:
                    pin_refusal(role, assignment)
                assignment[field] = values[key]
                assignment[field + "_source"] = source
                assignment[field + "_source_file"] = str(path)
            validate_role(role, assignment, str(path))
        record.update({f"{role}_{key}": value for key, value in assignment.items()})
    return record


def frontmatter_assignments(record: dict) -> dict[str, str]:
    # Quote provenance paths and escape # so flat frontmatter cannot truncate them.
    return {key: (json.dumps(value).replace("#", "\\u0023") if key.endswith("_source_file") else
                  "yes" if value is True else "no" if value is False else
                  json.dumps(value).replace("#", "\\u0023")
                  if isinstance(value, list) or value is None else str(value))
            for key, value in record.items()}


def milestone_assignments(a: argparse.Namespace, d: Path) -> dict:
    snapshot = assignment_snapshot(d)
    if not snapshot:
        if any(getattr(a, f"{role}_{field}", "") for role in ASSIGNMENT_ROLES
               for field in ("harness", "model")) or getattr(a, "reviewer_context", None):
            die("this run has no recorded assignment config; create a new run to use milestone assignments")
        return {}
    overrides = [(role, field, getattr(a, f"{role}_{field}", ""))
                 for role in ASSIGNMENT_ROLES for field in ("harness", "model")
                 if getattr(a, f"{role}_{field}", "")]
    reason = str(getattr(a, "assignment_reason", "") or "").strip()
    if overrides and not reason:
        die("milestone assignment overrides require --assignment-reason TEXT")
    if (overrides or getattr(a, "reviewer_context", None)) and a.as_role != "planner":
        die("milestone assignment and continuity overrides require --as planner")
    if reason and not valid_text(reason):
        die("--assignment-reason must fit one nonempty line")
    record: dict = {"assignment_version": 1}
    for role in ASSIGNMENT_ROLES:
        assignment = assignment_role(snapshot, role)
        changes = [(field, value) for selected, field, value in overrides if selected == role]
        if changes and assignment["pinned"]:
            pin_refusal(role, assignment)
        for field, value in changes:
            if not valid_text(value) or (field == "model" and not valid_assignment_model(value)):
                die(f"{role} {field} must fit one nonempty line")
            assignment[field] = value
            assignment[field + "_source"] = "milestone"
            assignment[field + "_source_file"] = str(d / ".batches" / f"{a.create}.json")
        if changes:
            assignment["assignment_reason"] = reason
        validate_role(role, assignment, f"milestone {a.create}")
        record.update({f"{role}_{key}": value for key, value in assignment.items()})
    continuity = getattr(a, "reviewer_context", None)
    continuity_reason = str(getattr(a, "continuity_reason", "") or "").strip()
    if continuity_reason and not continuity:
        die("--continuity-reason requires an explicit --reviewer-context continue|fresh")
    if continuity and not valid_text(continuity_reason):
        die("an explicit --reviewer-context needs --continuity-reason TEXT on one line")
    record["reviewer_context"] = continuity or "continue"
    record["continuity_reason"] = continuity_reason or "Default continuity: continue the reviewer context."
    return record


def task_assignment_conflicts(d: Path, owner: str, assignment: dict,
                              harness: str = "", model: str = "") -> None:
    if not assignment or not is_quick_milestone(d):
        return
    for field, value in (("harness", harness), ("model", model)):
        if value and value != assignment[field]:
            die(f"{owner} task {field} {value!r} conflicts with its milestone implementor "
                f"{field} {assignment[field]!r}. Before dispatch, run `docket assign "
                f"{d.name} {owner} --clear-assignment --as planner` to inherit the milestone. "
                "If batch creation was refused, retry it after clearing the task choices")


def check_batch_tasks(d: Path, members: list[str], record: dict) -> None:
    if not is_quick_milestone(d) or not record:
        return
    assignment = assignment_role(record, "implementor")
    for owner in members:
        meta = parse((d / f"{owner}-task.mdx").read_text())[0]
        task_assignment_conflicts(
            d, owner, assignment,
            meta.get("harness", "") if meta.get("assignment_harness_explicit") == "yes" else "",
            meta.get("requested_model", "") if meta.get("assignment_model_explicit") == "yes" else "")


def sync_batch_tasks(d: Path, batch: dict) -> None:
    """Finish draft inheritance before publishing closed membership, never after dispatch."""
    if not is_quick_milestone(d) or not batch.get("assignment_version"):
        return
    assignment = assignment_role(batch, "implementor")
    check_batch_tasks(d, batch["members"], batch)
    for owner in sorted(batch["members"]):
        with owner_lock(d, owner):
            task = d / f"{owner}-task.mdx"
            report = latest(reports(d, owner))
            if not report:
                die(f"{owner} has no draft report for milestone assignment")
            tm, tb = parse(task.read_text())
            rm, rb = parse(report.read_text())
            if all(meta.get("harness") == assignment["harness"] and
                   meta.get("requested_model", "") == assignment["model"] for meta in (tm, rm)):
                continue
            if (dispatch_path(d, owner).exists() or rm.get("status") != "draft"
                    or tm.get("actual_model") or rm.get("actual_model")):
                die(f"{owner} already started; milestone assignments cannot rewrite its evidence")
            for path, meta, body in ((task, tm, tb), (report, rm, rb)):
                meta["harness"] = str(assignment["harness"])
                meta["requested_model"] = str(assignment["model"])
                publish(path, render(meta, body))


def clear_task_assignment(d: Path, owner: str) -> None:
    if not assignment_snapshot(d) or not is_quick_milestone(d):
        die("--clear-assignment applies only to new quick runs with recorded assignments")
    with owner_lock(d, owner):
        task = d / f"{owner}-task.mdx"
        report = latest(reports(d, owner))
        if not task.is_file() or not report:
            die(f"no assigned task {owner}")
        tm, tb = parse(task.read_text())
        rm, rb = parse(report.read_text())
        capsule = scope_path(d, owner)
        scope = parse(capsule.read_text())[0] if capsule.is_file() else {}
        if (dispatch_path(d, owner).exists() or rm.get("status") != "draft"
                or tm.get("actual_model") or rm.get("actual_model") or scope.get("status") == "ready"):
            die(f"{owner} already started; --clear-assignment cannot rewrite its evidence")
        assignment = recorded_assignment(d, "implementor", owner)
        tm["assignment_harness_explicit"] = "no"
        tm["assignment_model_explicit"] = "no"
        for path, meta, body in ((task, tm, tb), (report, rm, rb)):
            meta["harness"] = str(assignment["harness"])
            meta["requested_model"] = str(assignment["model"])
            publish(path, render(meta, body))
    print(f"{owner} now inherits its milestone assignment; task intent and scope are unchanged")


def config_view(record: dict) -> dict:
    return {role: assignment_role(record, role) for role in ASSIGNMENT_ROLES}


def cmd_config(a: argparse.Namespace) -> None:
    if a.run:
        d = need_run(a.run)
        record = assignment_snapshot(d)
        if not record:
            die(f"run {a.run} predates recorded assignment config; its existing model policy remains in plan.mdx")
        milestones = {str(batch["batch"]): {
            "roles": config_view(batch), "reviewer_context": batch.get("reviewer_context", "continue"),
            "continuity_reason": batch.get("continuity_reason", "")}
            for batch in list_batches(d) if batch.get("milestone") and batch.get("assignment_version")}
    else:
        record, milestones = resolve_config(), {}
    payload = {"run": a.run or "", "roles": config_view(record), "milestones": milestones}
    if a.json:
        print(json.dumps(payload, sort_keys=True))
        return
    for title, roles in [("run " + a.run if a.run else "effective defaults", payload["roles"]),
                         *(("milestone " + bid, item["roles"]) for bid, item in milestones.items())]:
        print(title + ":")
        for role, value in roles.items():
            sources = "; ".join(f"{field}: {value[field + '_source']} "
                                f"{value[field + '_source_file']}" for field in ("harness", "model"))
            model = value["model"] or ("session model" if role == "planner" else "harness default")
            print(f"  {role}: {value['harness']}/{model} ({sources}); "
                  f"allowed(user)={value['allowed_models'] if value['allowed_models'] is not None else 'no user restriction'} "
                  f"{value['allowed_models_source_file']}; pin={value['pinned']} {value['pin_source_file']}")
    for bid, item in milestones.items():
        print(f"  {bid} reviewer_context={item['reviewer_context']}: {item['continuity_reason']}")
