"""Run policy read from the plan: workflow, mode, roles, authority, and model limits."""

from __future__ import annotations

import argparse
from pathlib import Path

from .common import (
    ACTING_ROLES, MODE_QUICK, MODE_STANDARD, QUICK_ROLES, RECORDED_MODES,
    REVIEW_COMBINED_CHECKER, REVIEW_INDEPENDENT, TOPOLOGIES, WORKFLOWS, WORKFLOW_FIVE_ROLE,
    WORKFLOW_LEGACY_DECODE, die,
)
from .frontmatter import parse
from .paths import run_dir


def need_run(run: str) -> Path:
    d = run_dir(run)
    if not d.is_dir():
        die(f"no such run: {run}  (try: docket init {run})")
    run_policy(d)
    return d


def topology(d: Path) -> str:
    return str(run_policy(d)["topology"])


def role_sessions_for(mode: str, workflow: str, selected_topology: str) -> str:
    """The physical sessions selected by a mode, kept separate from logical roles."""
    if mode == MODE_STANDARD:
        return "planner, orchestrator, implementor, verifier, reviewer"
    if mode == MODE_QUICK:
        return "coordinator, implementor, checker"
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

    expected_sessions = role_sessions_for(selected_mode, workflow, selected_topology)
    expected_review = (REVIEW_COMBINED_CHECKER if selected_mode == MODE_QUICK
                       else ("legacy-completion" if workflow == WORKFLOW_LEGACY_DECODE
                             else REVIEW_INDEPENDENT))
    if selected_mode == MODE_STANDARD:
        if workflow != WORKFLOW_FIVE_ROLE or selected_topology != "split":
            die("malformed standard preset in plan.mdx: standard requires "
                "workflow five-role-v1 and topology split")
    if selected_mode == MODE_QUICK:
        if workflow != WORKFLOW_FIVE_ROLE or selected_topology != "combined":
            die("malformed quick preset in plan.mdx: quick requires workflow "
                "five-role-v1 and topology combined")
    for key, expected in (("role_sessions", expected_sessions),
                          ("review_policy", expected_review)):
        if selected_mode in (MODE_STANDARD, MODE_QUICK) and meta.get(key, "").strip() != expected:
            die(f"malformed {selected_mode} preset in plan.mdx: {key} must be {expected!r}")
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


def verifier_correction_allowed(d: Path) -> bool:
    return plan_flag(d, "verifier_correction", "forbidden").strip() == "allowed"


def require_five_role(a: argparse.Namespace, d: Path, op: str) -> None:
    """Role authority for five-role runs. Legacy runs are never reinterpreted."""
    if not is_five_role(d):
        return
    role = (a.as_role or "").strip()
    allowed = authority_for(d, op)
    if role not in allowed:
        die(f"{op} in a five-role run requires --as { nice_roles(allowed)}; "
            f"got {role or 'no role'}. Verifiers never approve, orchestrators never "
            "waive or alter acceptance, and only reviewers approve.")


def authority_for(d: Path, op: str) -> tuple[str, ...]:
    """Acting sessions for one logical operation under the recorded mode policy."""
    allowed = FIVE_ROLE_AUTHORITY.get(op, ())
    if mode_of(d) != MODE_QUICK:
        return allowed
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
    return "coordinator" if mode_of(d) == MODE_QUICK else "planner"


def verifier_role_of(d: Path) -> str:
    """The role that performs verification under the run's preset."""
    return "checker" if mode_of(d) == MODE_QUICK else "verifier"


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


def max_concurrency_of(d: Path) -> int:
    try:
        return max(0, int(plan_flag(d, "max_concurrency", "0")))
    except ValueError:
        return 0
