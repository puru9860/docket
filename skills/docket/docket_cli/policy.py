"""Run policy read from the plan: workflow, mode, roles, authority, and model limits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import (
    ACTING_ROLES, MODE_QUICK, MODE_STANDARD, QUICK_ROLES, RECORDED_MODES,
    REVIEW_COMBINED_CHECKER, REVIEW_INDEPENDENT, REVIEW_QUICK_MILESTONE,
    REVIEW_TIERED, TOPOLOGIES, WORKFLOWS,
    WORKFLOW_FIVE_ROLE, WORKFLOW_LEGACY_DECODE, die,
)
from .frontmatter import parse
from .paths import run_dir


MILESTONE_DEPENDENCIES = "approval-v1"


def need_run(run: str) -> Path:
    d = run_dir(run)
    if not d.is_dir():
        die(f"no such run: {run}  (try: docket init {run})")
    run_policy(d)
    return d


def topology(d: Path) -> str:
    return str(run_policy(d)["topology"])


def role_sessions_for(mode: str, workflow: str, selected_topology: str,
                      review_policy: str = "") -> str:
    """The physical sessions selected by a mode, kept separate from logical roles."""
    if mode == MODE_STANDARD:
        return "planner, orchestrator, implementor, verifier, reviewer"
    if mode == MODE_QUICK:
        return ("coordinator, implementor, checker"
                if review_policy == REVIEW_COMBINED_CHECKER
                else "planner, implementor, reviewer")
    if workflow == WORKFLOW_LEGACY_DECODE:
        return ("planner, orchestrator, implementor" if selected_topology == "split"
                else "coordinator, implementor")
    return ("planner, orchestrator, implementor, verifier, reviewer"
            if selected_topology == "split"
            else "coordinator, implementor, verifier, reviewer")


def run_policy(d: Path) -> dict[str, str]:
    """Decode and validate mode, workflow, topology, and physical role policy.

    Missing workflow is the historical legacy decode. It deliberately does not
    share the new-run default constant. Missing mode is also historical and is
    labelled without claiming that an old run was created from a new preset.
    Explicit empty or unknown values refuse rather than inheriting authority.
    """
    plan = d / "plan.mdx"
    if not plan.is_file():
        return {
            "mode": "legacy-unversioned", "workflow": WORKFLOW_LEGACY_DECODE,
            "topology": "split", "role_sessions": "planner, orchestrator, implementor",
            "review_policy": "legacy-completion", "model_policy": "unrecorded",
        }
    try:
        meta = parse(plan.read_text())[0]
    except (OSError, ValueError) as exc:
        die(f"malformed run policy in plan.mdx: {exc}")

    if "milestone_dependencies" in meta:
        value = meta["milestone_dependencies"].strip()
        if value != MILESTONE_DEPENDENCIES:
            die(f"unknown milestone_dependencies {value!r} in plan.mdx: use "
                f"{MILESTONE_DEPENDENCIES}")

    if "workflow" not in meta:
        workflow = WORKFLOW_LEGACY_DECODE
    else:
        workflow = meta.get("workflow", "").strip()
        if not workflow:
            die("malformed workflow in plan.mdx: an explicit workflow value cannot be empty")
        if workflow not in WORKFLOWS:
            die(f"unknown workflow {workflow!r} in plan.mdx: use "
                f"{WORKFLOW_LEGACY_DECODE} or {WORKFLOW_FIVE_ROLE}")

    if "mode" not in meta:
        selected_mode = ("legacy-unversioned" if workflow == WORKFLOW_LEGACY_DECODE
                         else "workflow-unversioned")
    else:
        selected_mode = meta.get("mode", "").strip()
        if not selected_mode:
            die("malformed mode in plan.mdx: an explicit mode value cannot be empty")
        if selected_mode not in RECORDED_MODES:
            die(f"unknown mode {selected_mode!r} in plan.mdx: use standard, quick, "
                "or a CLI-recorded custom policy")

    selected_topology = meta.get("topology", "split").strip()
    if selected_topology not in TOPOLOGIES:
        die(f"unknown topology {selected_topology!r} in plan.mdx: use split or combined")

    recorded_review = meta.get("review_policy", "").strip()
    expected_review = (recorded_review or REVIEW_QUICK_MILESTONE if selected_mode == MODE_QUICK
                       else ("legacy-completion" if workflow == WORKFLOW_LEGACY_DECODE
                             else REVIEW_TIERED if selected_mode == MODE_STANDARD
                             else REVIEW_INDEPENDENT))
    expected_sessions = role_sessions_for(selected_mode, workflow, selected_topology,
                                          expected_review)
    if selected_mode == MODE_STANDARD:
        if workflow != WORKFLOW_FIVE_ROLE or selected_topology != "split":
            die("malformed standard preset in plan.mdx: standard requires "
                "workflow five-role-v1 and topology split")
    if selected_mode == MODE_QUICK:
        old_quick = expected_review == REVIEW_COMBINED_CHECKER
        if expected_review not in (REVIEW_COMBINED_CHECKER, REVIEW_QUICK_MILESTONE):
            die("malformed quick preset in plan.mdx: unknown review_policy")
        expected_topology = "combined" if old_quick else "split"
        if workflow != WORKFLOW_FIVE_ROLE or selected_topology != expected_topology:
            die("malformed quick preset in plan.mdx: quick requires workflow "
                f"five-role-v1 and topology {expected_topology} for {expected_review}")
    recorded_sessions = meta.get("role_sessions", "").strip()
    if selected_mode in (MODE_STANDARD, MODE_QUICK) and recorded_sessions != expected_sessions:
        die(f"malformed {selected_mode} preset in plan.mdx: role_sessions must be "
            f"{expected_sessions!r}")
    if selected_mode == MODE_STANDARD and recorded_review not in ("", REVIEW_TIERED,
                                                                  REVIEW_INDEPENDENT):
        die(f"malformed standard preset in plan.mdx: review_policy must be "
            f"{REVIEW_TIERED!r} (recorded {REVIEW_INDEPENDENT!r} runs keep their semantics)")
    return {
        "mode": selected_mode,
        "workflow": workflow,
        "topology": selected_topology,
        "role_sessions": meta.get("role_sessions", "").strip() or expected_sessions,
        "review_policy": meta.get("review_policy", "").strip() or expected_review,
        "model_policy": meta.get("model_policy", "").strip() or "unrecorded",
    }


def workflow_of(d: Path) -> str:
    return str(run_policy(d)["workflow"])


def mode_of(d: Path) -> str:
    return str(run_policy(d)["mode"])


def require_preset_role(d: Path, role: str) -> None:
    """A role that does not apply to the active preset fails visibly, never silently.

    The union ACTING_ROLES is advertised everywhere so no command offers a role
    another refuses statically. Quick coordinator and checker apply only in the
    quick preset; elsewhere they refuse with a diagnostic naming the run's
    actual preset. Standard logical roles stay renderable everywhere to preserve
    legacy behavior.
    """
    if role not in ACTING_ROLES:
        die(f"unknown role {role!r}; use one of: {', '.join(sorted(ACTING_ROLES))}")
    if is_quick_milestone(d) and role not in ("planner", "implementor", "reviewer"):
        die(f"role {role!r} does not apply in quick/{REVIEW_QUICK_MILESTONE} "
            "(roles planner, implementor, reviewer)")
    if role in QUICK_ROLES and mode_of(d) != MODE_QUICK:
        policy = run_policy(d)
        die(f"role {role!r} does not apply in {policy['mode']}/{policy['workflow']} preset "
            f"(roles {policy['role_sessions']}); quick roles need a quick run")


def artifact_policy_fields(d: Path) -> dict[str, str]:
    policy = run_policy(d)
    return {"mode": str(policy["mode"]),
            "review_policy": str(policy["review_policy"])}


# FIVE_ROLES, QUICK_ROLES, and ACTING_ROLES are declared once near the top;
# every role list below reads that declaration.


def is_five_role(d: Path) -> bool:
    """Whether this run executes under the five-role-v1 workflow policy."""
    return workflow_of(d) == WORKFLOW_FIVE_ROLE


def review_policy_of(d: Path) -> str:
    """The recorded review policy, never reinterpreted for existing runs."""
    return str(run_policy(d)["review_policy"])


def is_quick_milestone(d: Path) -> bool:
    """New quick policy: implementor coordinates, reviewer settles milestones."""
    try:
        return review_policy_of(d) == REVIEW_QUICK_MILESTONE
    except SystemExit:
        return False


def is_tiered(d: Path) -> bool:
    """Whether this run settles tasks at verifier pass and decides at the milestone.

    New standard runs record tiered-verifier-reviewer. Existing standard runs that
    recorded independent-verifier-reviewer keep that policy with identical five-role
    authority; only the settlement rules below differ.
    """
    try:
        return review_policy_of(d) in (REVIEW_TIERED, REVIEW_QUICK_MILESTONE)
    except SystemExit:
        return False


def plan_flag(d: Path, key: str, default: str = "") -> str:
    plan = d / "plan.mdx"
    if not plan.is_file():
        return default
    try:
        return parse(plan.read_text())[0].get(key, default) or default
    except (OSError, ValueError):
        return default


def correction_limit_of(d: Path) -> int:
    try:
        return max(0, int(plan_flag(d, "correction_limit", "2")))
    except ValueError:
        return 2


# The harness tool that runs one task in a context of its own. Codex has none a
# role can rely on yet, so a Codex role keeps working in its session.
FRESH_CONTEXT_TOOLS = {"claude": "the Agent tool (general-purpose)",
                       "opencode": "the task tool (general agent)"}


# How the same harness continues a subagent it started, keeping that context.
FRESH_CONTEXT_RESUME = {"claude": "SendMessage to that agent's id",
                        "opencode": "the task tool given that subagent's task_id"}


def round_context_of(d: Path) -> str:
    """Scope a context to each task or milestone; absent means shared."""
    value = plan_flag(d, "round_context", "shared").strip()
    if value not in ("fresh", "shared"):
        die(f"unknown round_context {value!r} in plan.mdx: use fresh or shared")
    return value


def fresh_context_tool(d: Path, harness: str) -> str:
    """The subagent tool for a unit of work, or '' to keep its session."""
    return FRESH_CONTEXT_TOOLS.get(harness, "") if round_context_of(d) == "fresh" else ""


def fresh_context_resume(harness: str) -> str:
    """How a role on `harness` continues a subagent it started earlier."""
    return FRESH_CONTEXT_RESUME.get(harness, "")


def verifier_correction_allowed(d: Path) -> bool:
    return plan_flag(d, "verifier_correction", "forbidden").strip() == "allowed"


def require_five_role(a: argparse.Namespace, d: Path, op: str) -> None:
    """Role authority for five-role runs. Legacy runs are never reinterpreted."""
    if not is_five_role(d):
        return
    role = (a.as_role or "").strip()
    allowed = authority_for(d, op)
    if not allowed:
        die(f"{op} does not apply in {mode_of(d)}/{review_policy_of(d)}; "
            "this run has no verifier role")
    if role not in allowed:
        die(f"{op} in a five-role run requires --as { nice_roles(allowed)}; "
            f"got {role or 'no role'}. Verifiers never approve, orchestrators never "
            "waive or alter acceptance, and only reviewers approve.")


def authority_for(d: Path, op: str) -> tuple[str, ...]:
    """Acting sessions for one logical operation under the recorded mode policy."""
    allowed = FIVE_ROLE_AUTHORITY.get(op, ())
    if mode_of(d) != MODE_QUICK:
        return allowed
    if is_quick_milestone(d):
        physical = {"orchestrator": "implementor", "verifier": ""}
        return tuple(dict.fromkeys(physical.get(role, role) for role in allowed
                                   if physical.get(role, role)))
    physical = {"planner": "coordinator", "orchestrator": "coordinator",
                "verifier": "checker", "reviewer": "checker"}
    return tuple(dict.fromkeys(physical.get(role, role) for role in allowed))


def nice_roles(roles: tuple[str, ...]) -> str:
    if len(roles) == 1:
        return roles[0]
    return " or ".join(roles)


# op -> roles that may perform it in a five-role run.
FIVE_ROLE_AUTHORITY = {
    "submit:implementor": ("implementor",),
    "submit:orchestrator": ("orchestrator",),
    "verify-record": ("verifier",),
    "decide:approve": ("reviewer",),
    "decide:waive": ("reviewer",),
    "decide:changes": ("reviewer",),
    "verifier-correction": ("verifier",),
    "reopen": ("reviewer",),
}


def task_executor(d: Path, owner: str) -> str:
    """Who executes a task: `implementor` unless its task file says otherwise.

    A report with no task file keeps the historical reading as delegated work.
    """
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return "implementor"
    try:
        return parse(task.read_text())[0].get("executor", "implementor") or "implementor"
    except (OSError, ValueError):
        return "implementor"


def submit_op_for(d: Path, owner: str) -> str:
    task = d / f"{owner}-task.mdx"
    if owner == "orch":
        return "submit:orchestrator"
    if task.is_file():
        try:
            executor = parse(task.read_text())[0].get("executor", "implementor")
        except (OSError, ValueError):
            executor = "implementor"
        if executor == "orchestrator":
            return "submit:orchestrator"
    return "submit:implementor"


def plan_owner_role(d: Path) -> str:
    return ("coordinator" if mode_of(d) == MODE_QUICK and not is_quick_milestone(d)
            else "planner")


def verifier_role_of(d: Path) -> str:
    """The role that performs verification under the run's preset."""
    return ("" if is_quick_milestone(d) else
            "checker" if mode_of(d) == MODE_QUICK else "verifier")


def policy_models(d: Path) -> tuple[str, list[str]]:
    primary = plan_flag(d, "primary_model", "")
    fallbacks = [item.strip() for item in plan_flag(d, "fallback_models", "").split(",")
                 if item.strip()]
    return primary, fallbacks


def model_policy(d: Path) -> tuple[str, list[str], str, list[str] | None]:
    """Approved model policy with absent and empty kept as different states.

    Returns primary, fallbacks, state, and allowed. State is absent when no
    primary and no fallbacks are configured, single when a primary is
    configured with an empty fallback list, and list otherwise. Allowed is
    None for absent, meaning unrestricted, and the deduped approved list
    otherwise, so an empty fallback list means exactly one approved model
    rather than permission for anything.
    """
    primary, fallbacks = policy_models(d)
    if not primary and not fallbacks:
        return primary, fallbacks, "absent", None
    if primary and not fallbacks:
        return primary, fallbacks, "single", [primary]
    ordered = ([primary] if primary else []) + fallbacks
    seen: set[str] = set()
    deduped: list[str] = []
    for item in ordered:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return primary, fallbacks, "list", deduped


def role_fallbacks(d: Path, role: str) -> list[str]:
    """Per-role fallback models from flat `<role>_fallback_models`, in order."""
    if role not in ROLE_POLICY_ROLES:
        return []
    return [item.strip() for item in plan_flag(d, f"{role}_fallback_models", "").split(",")
            if item.strip()]


def role_model_chain(d: Path, role: str, owner: str = "") -> list[str]:
    """Effective ordered model chain for one role.

    The per-role model (or the run primary) first, then the role's own
    fallbacks, then any remaining run-wide fallbacks. Empty means the run has
    no model policy at all and any model dispatches.
    """
    primary, fallbacks = policy_models(d)
    head = role_model(d, role, owner) or primary
    ordered = ([head] if head else []) + role_fallbacks(d, role) + fallbacks
    seen: list[str] = []
    for item in ordered:
        if item and item not in seen:
            seen.append(item)
    return seen


def allowed_models_for_role(d: Path, role: str, owner: str = "") -> list[str] | None:
    """Run approvals plus models explicitly approved for this role."""
    _primary, _fallbacks, _state, run_allowed = model_policy(d)
    role_allowed = role_model_chain(d, role, owner)
    allowed = (None if run_allowed is None and not role_allowed else
               list(dict.fromkeys([*(run_allowed or []), *role_allowed])))
    assignment = recorded_assignment(d, role, owner)
    if assignment:
        restriction = assignment["allowed_models"]
        if restriction is not None:
            allowed = (list(restriction) if run_allowed is None else
                       [m for m in (allowed or []) if m in restriction])
        if assignment["pinned"] and assignment["model"]:
            pinned = str(assignment["model"])
            allowed = [pinned] if allowed is None or pinned in allowed else []
    return allowed


def next_role_fallback(d: Path, role: str, current: str, history: list[dict],
                       owner: str = "") -> str:
    """Next untried non-Codex model in the role's effective chain, or ''.

    Without per-role configuration the chain is the run-wide approved list,
    so this answers exactly as `next_approved_fallback` does.
    """
    chain = role_model_chain(d, role, owner)
    allowed = allowed_models_for_role(d, role, owner)
    if allowed is not None:
        chain = [model for model in chain if model in allowed]
    if not chain:
        return ""
    tried = {str(item.get("model", "")) for item in history if isinstance(item, dict)}
    start = chain.index(current) + 1 if current in chain else 0
    return next((model for model in chain[start:]
                 if model not in tried and not model.startswith(("gpt-", "codex/"))), "")


def next_approved_fallback(d: Path, current: str, history: list[dict]) -> str:
    """The next untried non-Codex model after a Codex account limit."""
    _primary, _fallbacks, _state, allowed = model_policy(d)
    if not allowed:
        return ""
    tried = {str(item.get("model", "")) for item in history if isinstance(item, dict)}
    start = allowed.index(current) + 1 if current in allowed else 0
    return next((model for model in allowed[start:]
                 if model not in tried and not model.startswith(("gpt-", "codex/"))), "")


def max_concurrency_of(d: Path) -> int:
    try:
        return max(0, int(plan_flag(d, "max_concurrency", "0")))
    except ValueError:
        return 0


ROLE_POLICY_ROLES = ("planner", "orchestrator", "coordinator", "implementor",
                     "verifier", "reviewer", "checker", "test_author")


def role_model(d: Path, role: str, owner: str = "") -> str:
    """Per-role model default from flat plan frontmatter `<role>_model`."""
    if role not in ROLE_POLICY_ROLES:
        return ""
    assignment = recorded_assignment(d, role, owner)
    if assignment:
        return str(assignment["model"])
    return plan_flag(d, f"{role}_model", "").strip()


ASSIGNMENT_ROLES = ("planner", "implementor", "reviewer", "test_author")


def assignment_snapshot(d: Path) -> dict:
    """Only recorded policy is consulted after init, never live config files."""
    plan = d / "plan.mdx"
    if not plan.exists():
        return {}
    try:
        meta = parse(plan.read_text())[0]
    except (OSError, ValueError) as exc:
        die(f"cannot read recorded assignment {plan}: {exc}")
    if "assignment_version" not in meta:
        return {}
    if meta["assignment_version"] != "1":
        die(f"unknown assignment_version in {d / 'plan.mdx'}")
    return meta


def assignment_batch(d: Path, owner: str) -> dict:
    if not owner or not assignment_snapshot(d):
        return {}
    found = []
    for path in sorted((d / ".batches").glob("*.json")):
        try:
            batch = json.loads(path.read_text())
            if not isinstance(batch, dict):
                raise ValueError("expected an object")
        except (OSError, ValueError) as exc:
            die(f"cannot read recorded milestone assignment {path}: {exc}")
        if batch.get("milestone") and owner in batch.get("members", []):
            found.append(batch)
    if len(found) > 1:
        die(f"{owner} belongs to multiple milestones; put each task in exactly one milestone")
    return found[0] if found else {}


def assignment_role(record: dict, role: str) -> dict:
    """Decode the flat plan or batch fields into one role's policy."""
    if role not in ASSIGNMENT_ROLES or not record.get("assignment_version"):
        return {}
    if str(record["assignment_version"]) != "1":
        die(f"unknown assignment_version {record['assignment_version']!r}")
    result = {key.removeprefix(role + "_"): value for key, value in record.items()
              if key.startswith(role + "_")}
    allowed = result.get("allowed_models")
    try:
        for key, value in result.items():
            if key.endswith("_source_file") and isinstance(value, str) and value.startswith('"'):
                result[key] = json.loads(value)
        if isinstance(allowed, str):
            allowed = json.loads(allowed)
        if allowed is not None and (not isinstance(allowed, list) or
                                   any(not isinstance(item, str) or not item for item in allowed)):
            raise ValueError("allowed_models must be a list or null")
        if result.get("harness") not in ("claude", "codex", "opencode") or "model" not in result:
            raise ValueError("missing model or invalid harness")
        if result.get("pinned") not in (True, False, "yes", "no"):
            raise ValueError("invalid pin")
    except (ValueError, TypeError) as exc:
        die(f"invalid recorded {role} assignment: {exc}")
    result["allowed_models"] = allowed
    result["pinned"] = result.get("pinned") in (True, "yes")
    return result


def conductor_managed(d: Path) -> bool:
    return (d / ".conductor" / "managed.json").is_file()


def recorded_assignment(d: Path, role: str, owner: str = "") -> dict:
    record = assignment_snapshot(d)
    if not record:
        return {}
    batch = assignment_batch(d, owner) if owner else {}
    return assignment_role(batch if batch.get("assignment_version") else record, role)


def role_effort(d: Path, role: str) -> str:
    """Per-role effort default from flat plan frontmatter `<role>_effort`."""
    if role not in ROLE_POLICY_ROLES:
        return ""
    return plan_flag(d, f"{role}_effort", "").strip()


def provider_of(model: str) -> str:
    """Provider prefix of a model id (`provider/model`, else `default`)."""
    text = (model or "").strip()
    if not text:
        return ""
    for sep in ("/", ":"):
        if sep in text:
            return text.split(sep, 1)[0].strip().lower() or "default"
    return "default"


def provider_concurrency_of(d: Path, provider: str) -> int:
    """Per-provider execution cap from `max_concurrency_<provider>`."""
    key = "".join(ch if ch.isalnum() else "_" for ch in provider.lower()).strip("_")
    if not key:
        return 0
    try:
        return max(0, int(plan_flag(d, f"max_concurrency_{key}", "0")))
    except ValueError:
        return 0


def task_risk(d: Path, owner: str) -> str:
    """Risk tier of a task: `high` only when its frontmatter says so, else `low`."""
    if owner == "orch":
        return "low"
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return "low"
    try:
        return "high" if parse(task.read_text())[0].get("risk", "low").strip() == "high" else "low"
    except (OSError, ValueError):
        return "low"


def high_risk_verifier_model(d: Path) -> str:
    """Stronger verifier model for high-risk tasks, or empty when unconfigured."""
    return plan_flag(d, "high_risk_verifier_model", "").strip()
