"""Prompt composition and the renderer revision that identifies it."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import shlex
import shutil
import sys
from pathlib import Path

from .common import LAUNCHER, MODE_QUICK, SKILL_DIR, WORKFLOW_FIVE_ROLE, die, stamp
from .frontmatter import is_empty, parse, sections, stated, unwrap_markdown
from .paths import _ordered, handoffs, latest, next_numbered, reports, root, scope_path
from .publication import publish_exclusive
from .policy import (
    authority_for, is_five_role, mode_of, need_run, require_preset_role, run_policy,
    submit_op_for, verifier_correction_allowed, workflow_of,
)
from .bundles import digest_of
from .state import (
    decide_as, derive_stage, group_numbered_items, latest_checkpoint, latest_verification,
    raw_checkbox_lines, ready_handoff, state_of,
)
from .profiles import (
    PROFILE_TOKEN_BUDGET, estimate_tokens, list_cards, match_model_profile,
    profile_revision, read_profile,
)
from .freeze import current_bundle
from .gate import task_intent_problems
from .playbooks import contract_path, load_contract, read_contract


PROMPT_STAGES = ("initial", "correction", "resume", "verification", "review")

# These entry points render prompt bytes. renderer_revision follows their calls
# into every package module, including selectors and shared readers, so a new helper
# cannot silently fall outside the revision. Data files have separate revisions.
RENDERER_SOURCE_FUNCTIONS = (
    "compose_prompt",
    "workflow_authority",
    "derive_stage",
    "rendering_problem",
    "applicable_verification_obligations",
    "select_guidance",
    "ranked_cards",
    "guidance_block",
    "read_profile",
    "list_cards",
    "profile_revision",
    "match_model_profile",
    "select_cards",
    "estimate_tokens",
    "raw_checkbox_lines",
    "ready_handoff",
    "latest_checkpoint",
    "resumable_checkpoint",
    "user_profiles_dir",
    "profile_path",
    "normalized_model",
    "model_aliases",
    "decision_required_lines",
    "contract_path",
    "load_contract",
    "read_contract",
    "unwrap_markdown",
    "prompt_steps",
    "stated",
)

RENDERER_SOURCE_CONSTANTS = (
    "VERIFICATION_OBLIGATION_TRIGGERS",
    "PROMPT_STAGES",
    "PROFILE_TOKEN_BUDGET",
)


def package_namespace() -> dict[str, object]:
    """The CLI's top-level names across every package module, as one namespace.

    Names are unique across the package, so a name means the same object in every
    module that binds it. A function another package defined is left out.
    """
    package = sys.modules[__package__]
    own = {module.__name__ for module in package.MODULES}
    namespace: dict[str, object] = {}
    for module in package.MODULES:
        for name, value in vars(module).items():
            if name.startswith("__") or (inspect.isfunction(value) and value.__module__ not in own):
                continue
            namespace.setdefault(name, value)
    return namespace


def renderer_revision() -> str:
    """Hash prompt entry points and every package helper they transitively call."""
    namespace = package_namespace()
    names = set(RENDERER_SOURCE_FUNCTIONS)
    pending = list(names)
    while pending:
        name = pending.pop()
        func = namespace.get(name)
        if not inspect.isfunction(func):
            die(f"missing renderer source: {name}")
        codes = [func.__code__]
        while codes:
            code = codes.pop()
            for called in code.co_names:
                helper = namespace.get(called)
                if inspect.isfunction(helper) and called not in names:
                    names.add(called)
                    pending.append(called)
            codes.extend(value for value in code.co_consts if isinstance(value, type(code)))
    parts: list[bytes] = []
    for name in sorted(names):
        func = namespace[name]
        try:
            parts.append(name.encode() + b"\0" + inspect.getsource(func).encode())
        except (OSError, TypeError) as exc:
            die(f"cannot derive renderer revision from {name} ({exc})")
    for name in RENDERER_SOURCE_CONSTANTS:
        if name not in namespace:
            die(f"missing renderer source: {name}")
        parts.append(repr(namespace[name]).encode())
    digest = hashlib.sha256(b"\n".join(parts)).hexdigest()[:12]
    return f"derived-{digest}"


def ranked_cards(task_text: str) -> list[str]:
    """Card names ordered by trigger relevance. No budget applied here."""
    haystack = (task_text or "").lower()
    scored = []
    for name in list_cards():
        meta, _ = read_profile(name)
        triggers = [t.strip().lower() for t in meta.get("triggers", "").split(",") if t.strip()]
        # A trigger matches at the start of a word, so a stem like `concurren`
        # still finds `concurrency` while `lock` no longer fires on `block`.
        hits = sum(1 for t in triggers
                   if t and re.search(r"(?<![a-z0-9])" + re.escape(t), haystack))
        if hits:
            scored.append((-hits, name))
    scored.sort()
    return [name for _, name in scored]


def guidance_block(name: str) -> str:
    """One optional guidance block exactly as carried in the prompt."""
    _, body = read_profile(name)
    return f"[{name}]\n{unwrap_markdown(body.strip())}" if body.strip() else ""


def select_guidance(profile_name: str, task_text: str,
                    budget_tokens: int) -> tuple[list[str], list[str], list[dict[str, str]], int]:
    """Select optional guidance under one budget. Never padded to fill it.

    The profile, every card, and their headers and separators all count.
    Zero budget selects nothing. An oversized first candidate is skipped so a
    later smaller one may still fit. Returns selected blocks, selected names,
    rejected records with reasons, and the reported guidance size.
    """
    candidates: list[str] = []
    if profile_name:
        _, pbody = read_profile(profile_name)
        if pbody.strip():
            candidates.append(profile_name)
    candidates.extend(ranked_cards(task_text))
    # Deduplicate while preserving order: a profile that is also a card appears once.
    seen: set[str] = set()
    ordered: list[str] = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    selected_blocks: list[str] = []
    selected_names: list[str] = []
    rejected: list[dict[str, str]] = []
    if budget_tokens <= 0:
        for name in ordered:
            rejected.append({"name": name, "reason": "zero budget selects no optional guidance"})
        return [], [], rejected, 0
    for name in ordered:
        block = guidance_block(name)
        if not block:
            rejected.append({"name": name, "reason": "empty guidance body"})
            continue
        tentative = "# Selected guidance\n\n" + "\n\n".join(selected_blocks + [block])
        cost = estimate_tokens(tentative)
        if cost <= budget_tokens:
            selected_blocks.append(block)
            selected_names.append(name)
        else:
            need = estimate_tokens("# Selected guidance\n\n" + block)
            rejected.append({"name": name,
                             "reason": f"over budget: needs {need}, budget {budget_tokens}"})
    final = "# Selected guidance\n\n" + "\n\n".join(selected_blocks) if selected_blocks else ""
    return selected_blocks, selected_names, rejected, (estimate_tokens(final) if final else 0)


def select_cards(task_text: str, budget_tokens: int) -> list[str]:
    """Relevance-selected cards within budget. Never padded to fill it."""
    _, names, _, _ = select_guidance("", task_text, budget_tokens)
    return names


VERIFICATION_OBLIGATION_TRIGGERS = {
    "offline-operation": (
        "offline", "without network", "network-disabled", "network access",
    ),
    "changed-oracle": (
        "oracle", "fixture", "expected value", "parity",
        "reference output",
    ),
    "event-aggregation": (
        "aggregate", "aggregation", "callback", "zero/one/many", "event boundary",
    ),
    "content-fingerprint": (
        "fingerprint", "digest", "content-address", "byte-sensitive", "hash over",
    ),
    "configuration": (
        "configuration", "deployment config", "deployment setting",
        "environment variable", "config file",
    ),
}


def verification_obligations_path() -> Path:
    return SKILL_DIR / "references" / \
        "verification-obligations.md"


def applicable_verification_obligations(claim_text: str) -> tuple[str, str]:
    """Render the universal duty plus claim-selected verifier obligations.

    The reference document owns each duty's wording. This selector only maps
    claim language to applicability, and its closing invitation deliberately
    leaves discovery open to risks no deterministic trigger predicted.
    Only claim sentences are matched: sentences discussing obligations,
    duties, selection or scope themselves are not claims about behavior.
    """
    path = verification_obligations_path()
    try:
        source = path.read_text()
    except OSError as exc:
        die(f"missing verification obligations: {path} ({exc})")
    duties = sections(source)
    universal = duties.get("honest-acceptance", "").strip()
    if not universal:
        die(f"missing verification obligation: honest-acceptance in {path}")
    markers = ("obligation", "duty", "duties", "discuss", "claim",
               "out of scope", "out-of-scope")
    kept: list[str] = []
    normalized = re.sub(r"\s+", " ", claim_text or "").strip()
    for sentence in re.split(r"(?<=[.!?;])\s+", normalized):
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered_sentence = sentence.lower()
        # Every marker but `claim` is a stem matched at the start of a word,
        # so inflections like "obligations" or "discussion" still read as
        # meta-discussion. Only `claim` matches the whole word, so a
        # behavioral sentence about "JWT claims" is kept.
        if (re.search(r"\bclaim\b", lowered_sentence)
                or any(re.search(r"(?<![a-z0-9])" + re.escape(marker),
                                 lowered_sentence)
                       for marker in markers if marker != "claim")):
            continue
        kept.append(sentence)
    lowered = "\n".join(kept).lower()
    selected: list[tuple[str, str, list[str]]] = [
        ("honest-acceptance", universal, []),
    ]
    for obligation, triggers in VERIFICATION_OBLIGATION_TRIGGERS.items():
        # A trigger matches at the start of a word, the same rule the
        # guidance-card selector uses, so a stem still finds its word
        # while an infix like parity in disparity no longer fires.
        hits = [trigger for trigger in triggers
                if trigger and re.search(r"(?<![a-z0-9])" + re.escape(trigger),
                                         lowered)]
        if not hits:
            continue
        body = duties.get(obligation, "").strip()
        if not body:
            die(f"missing verification obligation: {obligation} in {path}")
        selected.append((obligation, body, hits))
    lines = ["# Applicable verification obligations", ""]
    for obligation, body, hits in selected:
        lines.append(f"- obligation: {obligation}")
        if obligation == "honest-acceptance":
            lines.append("  why: applies to every submission")
        else:
            lines.append("  why: applies because the submitted contract or report claims "
                         + ", ".join(repr(hit) for hit in hits))
        lines.append("  duty: " + " ".join(
            line.strip() for line in unwrap_markdown(body).splitlines() if line.strip()))
    lines += ["", "Discovery may reveal another relevant obligation or risk; raise it "
              "even when no deterministic selector predicted it."]
    return "\n".join(lines), digest_of(path.read_bytes())


def workflow_authority(d: Path, workflow: str, role: str) -> str:
    """Workflow and mode authority, including quick's explicitly combined checker."""
    if mode_of(d) == MODE_QUICK:
        return ("Authority: quick - five-role-v1 gates remain active; the checker session "
                "performs verification and review as two recorded duties, never as an "
                "independent verifier opinion; the implementor never approves its output; "
                f"logical role {role} keeps its existing gate")
    if workflow == WORKFLOW_FIVE_ROLE:
        return ("Authority: five-role-v1 - only the reviewer approves or waives; "
                "submission is never terminal and becomes terminal only through a reviewer "
                f"decision; role {role} never assumes another role's authority")
    return ("Authority: legacy - legacy completion applies; an orchestrator-owned task "
            f"may complete on submit without a reviewer decision; role {role} acts "
            "under legacy semantics")


def decision_required_lines(d: Path, owner: str) -> tuple[Path | None, list[str]]:
    """Every numbered required change from the latest decision, verbatim.

    Multiline numbered items keep their indented continuation lines folded
    into the item they follow; a section with no numbered line still yields
    nothing, so a correction without actionable changes keeps refusing.
    """
    decs = _ordered(d.glob(f"{owner}-decision-*.mdx"))
    if not decs:
        return None, []
    try:
        _, body = parse(decs[-1].read_text())
    except (OSError, ValueError):
        return decs[-1], []
    section = sections(body).get("Required changes", "")
    lines = [line.rstrip() for line in section.splitlines() if line.strip()]
    return decs[-1], group_numbered_items(lines)


def rendering_problem(d: Path, run: str, owner: str, role: str, stage: str,
                      model: str = "", profile: str = "") -> str:
    """One concrete diagnostic when a required input is missing, else empty.

    Both prompt rendering and dispatch call this single helper so the rule
    lives in one place: a missing artifact refuses with its file named and
    never renders a placeholder.
    """
    _, contract_problem = load_contract(role)
    if contract_problem:
        return contract_problem
    if owner == "orch":
        plan = d / "plan.mdx"
        if not plan.is_file():
            return f"missing required artifact: plan.mdx for planner aggregate {run}/orch"
        try:
            _, plan_body = parse(plan.read_text())
        except (OSError, ValueError) as exc:
            return f"missing required artifact: plan.mdx is unreadable ({exc})"
        if not sections(plan_body).get("Objective", "").strip():
            return "missing required artifact: plan.mdx carries no Objective for the aggregate"
        return ""
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return f"missing required artifact: {owner}-task.mdx for role {role} stage {stage}"
    try:
        _, task_body = parse(task.read_text())
    except (OSError, ValueError) as exc:
        return f"missing required artifact: {owner}-task.mdx is unreadable ({exc})"
    intent_problems = task_intent_problems(d, owner)
    if intent_problems:
        return (f"missing required artifact: {owner}-task.mdx carries unresolved task intent "
                f"({'; '.join(intent_problems)})")
    if stage == "correction":
        dec_path, numbered = decision_required_lines(d, owner)
        if dec_path is None:
            return f"missing required artifact: {owner}-decision-*.mdx with numbered required changes for correction"
        if not numbered:
            return f"missing required artifact: {dec_path.name} carries no numbered required changes for correction"
    if stage in ("verification", "review"):
        try:
            rnd, status = state_of(d, owner)
        except (OSError, ValueError):
            return f"missing required artifact: {owner}-report-*.mdx for {stage}"
        if status not in ("submitted", "approved", "completed", "waived") \
                and not (status == "blocked" and stage == "review"):
            return f"missing required artifact: submitted {owner} report for {stage} (found {status})"
        if stage == "review" and status != "blocked":
            # A blocked submission skips verification by design - the reviewer
            # decides the waiver on the stated question, not on a test run -
            # so no verification artifact may be demanded for it here.
            paths = _ordered(d.glob(f"{owner}-verification-*.mdx"))
            if not paths:
                return f"missing required artifact: {owner}-verification-*.mdx for review"
    return ""


def compose_prompt(d: Path, run: str, owner: str, role: str, model: str = "",
                   profile: str = "", budget_tokens: int = PROFILE_TOKEN_BUDGET,
                   stage: str = "", mode: str = ""
                   ) -> tuple[str, dict[str, object]]:
    """Render one role prompt for its workflow, role, and stage.

    Same inputs always render byte-identical output. Model, provider, and
    effort are execution metadata, never persona filler. Mandatory material
    (contract, acceptance, hard constraints) is never truncated to fit a
    budget; only optional guidance lives under it. Mode is read from the run
    policy; an explicit inspection value must match rather than overriding it.
    """
    require_preset_role(d, role)
    selected_mode = mode_of(d)
    if mode and mode != selected_mode:
        die(f"mode {mode!r} does not match the run's recorded mode {selected_mode!r}")
    if stage and stage not in PROMPT_STAGES:
        die(f"unknown stage {stage!r}; use one of: {', '.join(PROMPT_STAGES)}")
    workflow = workflow_of(d)
    eff_stage = stage or derive_stage(d, owner)
    problem = rendering_problem(d, run, owner, role, eff_stage, model, profile)
    if problem:
        die(problem)
    contract = read_contract(role)
    contract_rev = ""
    try:
        contract_rev = digest_of(contract_path(role).read_bytes())
    except OSError:
        contract_rev = digest_of(contract.encode())
    decs = _ordered(d.glob(f"{owner}-decision-*.mdx"))
    # Task or aggregate contract. A missing required file already refused above,
    # so no placeholder is ever emitted.
    if owner == "orch":
        plan = d / "plan.mdx"
        plan_meta, plan_body = parse(plan.read_text())
        plan_secs = sections(plan_body)
        objective = plan_secs.get("Objective", "").strip()
        task_text = plan_body
        claim_text = objective
        contract_lines = [
            f"run: {run} aggregate: orch role: {role}",
            f"objective: {objective}",
            f"approach: {stated(plan_secs.get('Approach', '')) or 'none'}",
            f"risks: {stated(plan_secs.get('Risks', '')) or 'none'}",
            f"hard constraints: {plan_secs.get('Out of scope', '').strip() or 'none stated'}",
            f"plan revision: {digest_of(plan.read_bytes())}",
        ]
        source_task_rev = digest_of(plan.read_bytes())
    else:
        task = d / f"{owner}-task.mdx"
        task_meta, task_body = parse(task.read_text())
        task_secs = sections(task_body)
        criteria = raw_checkbox_lines(task_secs.get("Acceptance criteria", ""))
        goal_text = task_secs.get("Goal", "").strip()
        try:
            _, plan_body_for_task = parse((d / "plan.mdx").read_text())
            plan_secs_for_task = sections(plan_body_for_task)
            plan_constraints = plan_secs_for_task.get("Out of scope", "").strip() or "none stated"
            plan_risks = plan_secs_for_task.get("Risks", "").strip() or "none"
        except (OSError, ValueError):
            plan_constraints = "none stated"
            plan_risks = "none"
        # Every stated constraint is mandatory and carried whole; a field nobody
        # stated is left out rather than rendered as a line of "none".
        optional_fields = [
            ("existing decisions", task_secs.get("Existing decisions", "")),
            ("discovery constraints", task_secs.get("Discovery constraints", "")),
            ("plan hard constraints", "" if plan_constraints == "none stated" else plan_constraints),
            ("plan risks", "" if plan_risks == "none" else plan_risks),
            ("starting hints (advisory only)", task_secs.get("Starting hints", "")),
        ]
        contract_lines = [
            f"run: {run} task: {owner} role: {role}",
            f"goal: {goal_text or 'none'}",
            "acceptance:",
            *([f"- {item}" for item in criteria] or ["- none"]),
            f"hard constraints: {task_secs.get('Out of scope', '').strip() or 'none stated'}",
            *(f"{label}: {value.strip()}" for label, value in optional_fields
              if value.strip() and value.strip().lower() not in ("none", "none stated")),
        ]
        task_text = task_body + "\n" + str(task_meta.get("files", "")) + "\n" + str(task_meta.get("verify_hint", ""))
        claim_text = (goal_text + "\n" + "\n".join(criteria)).strip()
        source_task_rev = digest_of(task.read_bytes())
    decision_lines = []
    dec_rev = ""
    if decs:
        dmeta, dbody = parse(decs[-1].read_text())
        dec_rev = digest_of(decs[-1].read_bytes())
        dsecs = sections(dbody)
        decision_lines = [f"latest decision: {decs[-1].name} ({dmeta.get('verdict', '?')})"]
        reason = dsecs.get("Reason", "").strip()
        if reason and not is_empty(reason) and reason.lower() != "none":
            decision_lines.append(f"reason: {reason}")
        _, numbered = decision_required_lines(d, owner)
        for line in numbered:
            decision_lines.append(f"required change: {line.strip()}")
        # Immutable evidence pointers for the round being corrected or reviewed:
        # the decision round, not the draft round that has no bundle yet.
        try:
            dec_round = int(dmeta.get("round", "0") or 0)
        except (TypeError, ValueError):
            dec_round = 0
        try:
            rnd_now, _ = state_of(d, owner)
        except (OSError, ValueError):
            rnd_now = 0
        target_round = dec_round or rnd_now
        frozen, _ = current_bundle(d, owner, target_round) if target_round else (None, [])
        if frozen and isinstance(frozen, dict) and frozen.get("digest"):
            decision_lines.append(f"bundle_digest: {frozen.get('digest')}")
        else:
            try:
                rep_target = d / f"{owner}-report-{target_round:02d}.mdx"
                if rep_target.is_file():
                    rmeta, _ = parse(rep_target.read_text())
                    if rmeta.get("bundle_digest", ""):
                        decision_lines.append(f"bundle_digest: {rmeta.get('bundle_digest')}")
                else:
                    rep_now = latest(reports(d, owner))
                    if rep_now is not None:
                        rmeta, _ = parse(rep_now.read_text())
                        if rmeta.get("bundle_digest", ""):
                            decision_lines.append(f"bundle_digest: {rmeta.get('bundle_digest')}")
            except (OSError, ValueError):
                pass
        vpath, _, _ = latest_verification(d, owner, target_round) if target_round else (None, {}, "")
        if vpath is not None:
            decision_lines.append(f"verification: {vpath.name}")
        else:
            # A correction without a verifier artifact still names the frozen
            # bundle it corrects; the verification pointer is required only
            # when a verifier already recorded one.
            pass
        # Ensure a bundle pointer is always present when the decided round froze one.
        if not any(line.startswith("bundle_digest:") for line in decision_lines):
            try:
                rep_target = d / f"{owner}-report-{target_round:02d}.mdx"
                if rep_target.is_file():
                    rmeta, _ = parse(rep_target.read_text())
                    if rmeta.get("bundle_digest", ""):
                        decision_lines.append(f"bundle_digest: {rmeta.get('bundle_digest')}")
            except (OSError, ValueError):
                pass
    model_source = model
    if owner != "orch":
        try:
            tmeta, _ = parse((d / f"{owner}-task.mdx").read_text())
            if not model_source:
                model_source = str(tmeta.get("actual_model", "") or tmeta.get("requested_model", ""))
        except (OSError, ValueError):
            pass
    chosen_profile = profile or match_model_profile(model_source)
    guidance_blocks, chosen_names, rejected, guidance_tokens = select_guidance(
        chosen_profile, task_text, budget_tokens)
    profile_revisions = [profile_revision(name) for name in chosen_names]
    selected_guidance = [{"name": name, "revision": profile_revision(name)} for name in chosen_names]
    # Resume pointers use only a ready handoff. A draft is never named as ready.
    ready = ready_handoff(d, owner)
    checkpoint = latest_checkpoint(d, owner)
    if eff_stage == "resume":
        if ready is not None:
            handoff_line = f"ready handoff: {ready.name} (ready)"
        elif checkpoint is not None:
            handoff_line = (f"mechanical checkpoint: {checkpoint.name} (mechanical - "
                            "rediscover from the task, the accepted scope capsule, "
                            "and the task-local diff; no ready handoff)")
        else:
            handoff_line = ("ready handoff: (none ready - mechanical recovery: "
                            "rediscover from the task, the accepted scope capsule, "
                            "and the task-local diff; no draft checkpoint is ready)")
    else:
        if ready is not None:
            handoff_line = f"ready handoff: {ready.name} (ready)"
        elif latest(handoffs(d, owner)) is not None:
            handoff_line = ("ready handoff: (none ready - mechanical recovery: "
                            "rediscover from the task, the accepted scope capsule, "
                            "and the task-local diff; no draft checkpoint is ready)")
        else:
            # Outside resume, a run with no handoff at all has nothing to point at.
            handoff_line = ""
    try:
        run_rel = str(d.relative_to(root()))
    except ValueError:
        run_rel = str(d)
    pointers = [
        f"task: {run_rel}/{owner}-task.mdx; accepted scope: {run_rel}/{scope_path(d, owner).name}"
        if owner != "orch" else f"plan: {run_rel}/plan.mdx",
    ]
    if decs:
        pointers.append(f"latest decision: {run_rel}/{decs[-1].name}")
    if handoff_line and role in ("implementor", "orchestrator", "coordinator"):
        pointers.append(handoff_line)
    pointers += [
        f"task-local diff: docket diff {run} {owner}",
        f"frozen evidence: docket bundle {run} {owner}",
    ]
    if model:
        pointers.append(f"execution metadata (not identity): model={model}")
    header_lines = [
        f"# Role: {role}",
        "",
        contract,
        "",
        f"stage: {eff_stage} | workflow: {workflow} | mode: {selected_mode} | "
        f"review_policy: {run_policy(d)['review_policy']}",
        workflow_authority(d, workflow, role),
    ]
    parts = [
        "\n".join(header_lines),
        "# Task contract\n\n" + "\n".join(contract_lines),
    ]
    if decision_lines:
        parts.append("# Latest decision\n\n" + "\n".join(decision_lines))
    obligations_revision = ""
    report_revision = ""
    # The quick checker performs the verifier duty, so it carries the same
    # obligations while its round still awaits verification.
    if role == "verifier" or (role == "checker" and eff_stage == "verification"):
        latest_report = latest(reports(d, owner))
        if latest_report is not None:
            try:
                _, report_body = parse(latest_report.read_text())
                if owner == "orch":
                    claim_text += "\n" + report_body
                else:
                    report_secs = sections(report_body)
                    report_claims = "\n".join(
                        report_secs.get(name, "").strip()
                        for name in ("Summary", "Acceptance", "Verification")
                    ).strip()
                    claim_text += "\n" + (report_claims or report_body)
                report_revision = digest_of(latest_report.read_bytes())
            except (OSError, ValueError):
                pass
        obligation_text, obligations_revision = applicable_verification_obligations(
            claim_text)
        parts.append(obligation_text)
    guidance_section = ""
    if guidance_blocks:
        guidance_section = "# Selected guidance\n\n" + "\n\n".join(guidance_blocks)
        parts.append(guidance_section)
    steps = prompt_steps(d, run, owner, role, eff_stage)
    if steps:
        steps.append(
            f"Last, record what docket itself cost you this round, or `none`: `docket feedback "
            f"{run} --add --role {role} --task {owner} --round {state_of(d, owner)[0]} --category "
            "instruction|discovery|verification|recovery|dispatch|routing|review|delivery|other "
            "--body \"a missing or unclear instruction, a refusal you worked around, a command "
            "you had to look up, or waiting\"`.")
        parts.append("# Steps\n\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1)))
    parts.append("# Pointers\n\n" + "\n".join(f"- {item}" for item in pointers))
    revisions = [f"renderer: {renderer_revision()}"]
    if owner != "orch":
        revisions.append(f"task revision: {source_task_rev}")
    if dec_rev:
        revisions.append(f"decision revision: {dec_rev}")
    parts.append(" | ".join(revisions))
    if shutil.which("docket") is None:
        # Only command positions are rewritten; task prose and paths stay verbatim.
        cli = shlex.quote(str(LAUNCHER))
        command = r"(?<=`)docket(?= )|^([ \t]*)docket(?= )|(?<=: )docket(?= )"
        def absolute_command(value: str) -> str:
            return re.sub(command, lambda match: (match.group(1) or "") + cli,
                          value, flags=re.M)
        parts = [absolute_command(part) for part in parts]
        guidance_section = absolute_command(guidance_section)
    prompt = "\n\n".join(parts) + "\n"
    mandatory_text = "\n\n".join([p for p in parts if p != guidance_section]) + "\n"
    mandatory_tokens = estimate_tokens(mandatory_text)
    # The reported guidance size matches the bytes the prompt actually carries.
    if guidance_section:
        guidance_tokens = estimate_tokens(guidance_section)
    else:
        guidance_tokens = 0
    source_revisions: dict[str, str] = {
        "contract": f"{role}:{contract_rev}",
        "task": source_task_rev,
        "renderer": renderer_revision(),
        "workflow": workflow,
        "mode": selected_mode,
        "review_policy": str(run_policy(d)["review_policy"]),
        "stage": eff_stage,
    }
    if decs:
        source_revisions["decision"] = dec_rev
    if ready is not None:
        try:
            source_revisions["handoff"] = digest_of(ready.read_bytes())
        except OSError:
            source_revisions["handoff"] = ready.name
    if obligations_revision:
        source_revisions["verification-obligations"] = obligations_revision
    if report_revision:
        source_revisions["report"] = report_revision
    for name in chosen_names:
        source_revisions[f"guidance:{name}"] = profile_revision(name)
    info: dict[str, object] = {
        "digest": digest_of(prompt.encode()),
        "role": role,
        "workflow": workflow,
        "mode": selected_mode,
        "review_policy": str(run_policy(d)["review_policy"]),
        "stage": eff_stage,
        "renderer_revision": renderer_revision(),
        "source_revisions": source_revisions,
        "profile_revisions": profile_revisions,
        "model_profile": (profile_revision(chosen_profile)
                          if chosen_profile and chosen_profile in chosen_names else ""),
        "selected_guidance": selected_guidance,
        "rejected_guidance": rejected,
        "guidance_tokens": guidance_tokens,
        "mandatory_tokens": mandatory_tokens,
        "prompt_tokens": estimate_tokens(prompt),
        "budget_tokens": budget_tokens,
    }
    return prompt, info


def prompt_steps(d: Path, run: str, owner: str, role: str, stage: str) -> list[str]:
    """The exact commands and files one role needs for one stage, in order.

    A prompt that says what to do but not how sends every worker to the
    playbook first; these steps carry the run's real paths and the `--as`
    identity its preset requires, so a worker can act on the prompt alone.
    """
    try:
        rel = str(d.relative_to(root()))
    except ValueError:
        rel = str(d)
    try:
        rnd, status = state_of(d, owner)
    except (OSError, ValueError):
        rnd, status = 0, ""
    five = is_five_role(d)

    def as_role(op: str) -> str:
        acting = authority_for(d, op) if five else ()
        return f" --as {acting[0]}" if acting else ""

    report = f"{rel}/{owner}-report-{rnd:02d}.mdx"
    decide_as = as_role("decide:approve")
    checker = mode_of(d) == MODE_QUICK and role == "checker"
    if checker:
        decide_as += " --reviewer checker"
    decide = [
        f"Decide the round: `docket decide {run} {owner} --approve{decide_as} --reason "
        "\"why it holds\"`; or "
        f"`docket decide {run} {owner} --changes{decide_as}`, which opens "
        f"`{rel}/{owner}-decision-{rnd:02d}.mdx` for numbered Required changes, then run "
        "the same command again to apply it; or "
        f"`docket decide {run} {owner} --waive --reason TEXT{decide_as}` for a blocker you accept.",
    ]
    if role in ("verifier", "reviewer", "checker"):
        read = (f"Read the frozen round with `docket bundle {run} {owner}`: the patch, the "
                "report, and the verification as it actually ran.")
        if owner == "orch":
            read = (f"Read `docket review-packet {run} --role reviewer` and the aggregate with "
                    f"`docket bundle {run} orch`.")
        if role == "reviewer" or owner == "orch" or status == "blocked" \
                or (checker and stage == "review"):
            if status == "blocked":
                return [read, "The round is blocked: answer its Decisions needed question.",
                        *decide]
            return [read, "Judge correctness, design, and integration from the contract, "
                    "inspecting source wherever the evidence leaves doubt.", *decide]
        verify_as = as_role("verify-record") + (" --verifier checker" if checker else "")
        steps = [
            read,
            "Check every acceptance criterion against observable behavior, with expected "
            "values you derive independently; apply the obligations above.",
            f"Record one verdict: `docket verify {run} {owner} --result pass|fail|uncertain"
            f"{verify_as} --detail \"1. numbered finding with its evidence\"`"
            + (" (add `--open-correction` to a fail to open the correction round)"
               if verifier_correction_allowed(d) else "") + ".",
        ]
        if checker:
            steps.append("Then review it as the second duty. " + decide[0])
        return steps
    if owner == "orch":
        if role in ("orchestrator", "coordinator"):
            return [f"Fill every section of `{report}` from the decided task rounds.",
                    f"Submit with `docket submit {run} orch{as_role('submit:orchestrator')}`."]
        return []
    submit_op = submit_op_for(d, owner)
    submit_as = as_role(submit_op)
    finish = [
        f"Fill `{report}`: summary, files changed (every path `docket diff {run} {owner}` lists, "
        "earlier rounds included), the acceptance boxes (criteria exactly as "
        "the task states them, each checked only when it genuinely passes), the verify command "
        "with its real output, and decisions needed.",
        f"Submit with `docket submit {run} {owner}{submit_as}`. If you cannot finish honestly, "
        f"write the question under Decisions needed and run `docket submit {run} {owner} "
        f"--blocked{submit_as}`. Never edit the report after submitting.",
    ]
    if submit_op == "submit:orchestrator":
        if role not in ("orchestrator", "coordinator"):
            return []
        return ["Implement inside the task's `files:` and run its verify command.", *finish]
    if role != "implementor":
        return []
    handoff = (f"Only if you must stop before submitting (your context is running out), run "
               f"`docket handoff {run} {owner}`, fill the checkpoint, then "
               f"`docket handoff {run} {owner} --submit`; a submitted round needs no handoff.")
    build = (f"Run `docket preflight {run} {owner}`, implement only inside the accepted "
             "files, and run the verify command.")
    if stage == "correction":
        return [
            "Apply every required change listed above.",
            f"If a change needs a file outside the accepted scope, amend `{rel}/{owner}-scope.mdx` "
            f"and rerun `docket scope {run} {owner} --submit` first.",
            build, *finish, handoff,
        ]
    if stage == "resume":
        return [
            f"Read the task, the accepted `{rel}/{owner}-scope.mdx`, the handoff or checkpoint "
            f"named below, and `docket diff {run} {owner}`; rediscover only what they leave open.",
            build, *finish, handoff,
        ]
    return [
        f"Read `{rel}/{owner}-task.mdx`, then discover the code it touches.",
        f"Fill `{rel}/{owner}-scope.mdx` (proposed files, the exact verify command, relevant "
        f"symbols, reasoning) and run `docket scope {run} {owner} --submit` before editing; on a "
        "collision, stop and report it.",
        build, *finish, handoff,
    ]


def prompts_dir(d: Path) -> Path:
    return d / ".prompts"


def cmd_prompt(a: argparse.Namespace) -> None:
    """Render a bounded, deterministic role prompt and record its digest."""
    d = need_run(a.run)
    prompt, info = compose_prompt(d, a.run, a.owner, a.role, model=a.model,
                                  profile=a.profile, budget_tokens=a.max_tokens,
                                  stage=(a.stage or ""))
    record = {"run": a.run, "owner": a.owner, "model": a.model,
              "rendered_at": stamp(), "prompt": prompt, **info}
    prompts_dir(d).mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        existing = sorted(prompts_dir(d).glob(f"{a.owner}-{a.role}-*.json"))
        path = prompts_dir(d) / f"{a.owner}-{a.role}-{next_numbered(existing):02d}.json"
        try:
            publish_exclusive(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
        except FileExistsError:
            continue
        break
    else:
        die(f"could not record the prompt for {a.owner}; retry the command")
    print(prompt, end="")
    print(f"---\nprompt digest: {info['digest']} "
          f"({info['prompt_tokens']} estimated tokens, "
          f"guidance {info['guidance_tokens']}, mandatory {info['mandatory_tokens']})",
          file=sys.stderr)
    print(f"mode: {info['mode']} workflow: {info['workflow']} stage: {info['stage']} "
          f"renderer: {info['renderer_revision']}", file=sys.stderr)
    if info["profile_revisions"]:
        print("profiles: " + ", ".join(str(r) for r in info["profile_revisions"]),
              file=sys.stderr)
