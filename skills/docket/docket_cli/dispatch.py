"""Dispatch, capacity, resume, model switching, and exceptions."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .common import COVERAGE_AVAILABLE, COVERAGE_UNAVAILABLE, MODE_QUICK, die, stamp
from .frontmatter import parse, render
from .paths import _ordered, dispatch_path, next_numbered, read_dispatch, root
from .publication import fault, perturb, publish, publish_bytes, publish_exclusive, publish_json
from .policy import (is_quick_milestone, max_concurrency_of, mode_of, model_policy, need_run,
                      next_role_fallback, allowed_models_for_role, plan_flag, provider_concurrency_of,
                      provider_of, role_effort, role_model)
from .baselines import assignment_evidence, require_baseline, snapshot_path, task_scope_hint
from .bundles import digest_of
from .locks import owner_lock
from .state import (
    derive_stage, normalized_scope, resumable_checkpoint, scope_collisions,
    seed_report_acceptance, state_of, task_depends_on,
)
from .dependencies import pin_final_dependencies
from .freeze import current_bundle
from .feedback import machine_feedback
from .liveness import TERMINAL_REPORT_STATES, dispatch_dependencies_unmet, live_dispatches
from .delivery import check_registration, ensure_session_registration
from .gate import task_intent_problems
from .prompts import compose_prompt, prompts_dir, rendering_problem
from .sessions import codex_limit_exhausted, codex_rate_limits, collect_run_usage
from .verification import write_checkpoint
from .batches import quick_milestone_implementation_problem, quick_milestone_plan_problems


@contextlib.contextmanager
def capacity_lock(d: Path):
    """Serialize run-wide capacity claims across owners.

    The owner lock is per-owner, so two different owners can otherwise both
    read an empty cap and both write. This lock holds the run-wide capacity
    lock only for the read-check-write of the cap claim inside cmd_dispatch
    and cmd_resume:
    re-reading live capacity, refusing when the cap is full, and publishing
    the new record. It is never held across dependency checks, scope checks,
    registration checks, or the whole dispatch, so an ordinary dispatch with
    no contender completes promptly and unrelated work is not serialized
    behind one slow dispatch.
    """
    path = d / ".locks" / "dispatch.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def dispatch_ownership_problems(d: Path, owner: str) -> list[str]:
    """The exclusive change surface must be free before starting work."""
    if owner == "orch":
        # The aggregate has no task file and claims no exclusive change
        # surface of its own; constituent tasks hold their scope through
        # the scope gate, so there is nothing here to collide.
        return []
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return [f"no assignment {task.name}"]
    try:
        meta = parse(task.read_text())[0]
    except (OSError, ValueError):
        return [f"{task.name} is unreadable"]
    if owner != "orch" and meta.get("executor", "implementor") == "orchestrator":
        return []
    proposed = normalized_scope(meta)
    if not proposed:
        hints = [h.strip("`/") for h in str(meta.get("file_hints", "")).split() if h.strip("`/")]
        proposed = sorted(set(hints))
    if not proposed:
        return []
    return sorted(set(scope_collisions(d, owner, proposed)))


def session_ownership_problem(d: Path, session: str, owner: str) -> str:
    """A live worker session can hold at most one task in this run."""
    for active in live_dispatches(d):
        if str(active.get("session", "")) == session and str(active.get("owner", "")) != owner:
            return (f"session {session} already holds live {active.get('owner')} round "
                    f"{active.get('round')}; use a different session or wait for it to settle")
    return ""


def guard_provider_concurrency(d: Path, owner: str, model: str,
                               active: list[dict]) -> None:
    """Refuse a dispatch that would exceed its model provider's own cap."""
    provider = provider_of(model)
    if not provider:
        return
    cap = provider_concurrency_of(d, provider)
    if not cap:
        return
    holding = [r for r in active
               if provider_of(str(r.get("model_requested", "") or "")) == provider]
    if len(holding) >= cap:
        die(f"{owner} cannot dispatch: provider {provider} concurrency "
            f"{len(holding)}/{cap} exhausted; wait for a {provider} task to settle")


def guard_review_reserve(d: Path, model: str) -> None:
    """Keep configured run tokens available for final review before premium launches."""
    premium = {item.strip() for item in plan_flag(d, "premium_models", "").split(",")
               if item.strip()}
    if model not in premium:
        return
    raw_budget = plan_flag(d, "token_budget", "").strip()
    if not raw_budget:
        return
    raw_reserve = plan_flag(d, "review_reserve", "0").strip()
    try:
        budget, reserve = int(raw_budget), int(raw_reserve)
    except ValueError:
        die("token_budget and review_reserve must be whole token counts")
    if budget <= 0 or reserve < 0 or reserve >= budget:
        die("token_budget must be positive and review_reserve must be smaller than it")
    observations = collect_run_usage(d, False)
    if any(not row.get("found") for row in observations):
        die("premium token budget cannot be enforced: a noted harness session's "
            "usage is unavailable; recover its transcript or choose a non-premium model")
    used = sum(int(row.get("total", 0) or 0) for row in observations)
    if used >= budget - reserve:
        die(f"premium model {model} cannot launch: {used} of {budget} run tokens used, "
            f"leaving the {reserve}-token review reserve. Choose an approved "
            "non-premium fallback or have the planner amend the flat budget policy")


def cmd_dispatch(a: argparse.Namespace) -> None:
    """Dispatch one task round to one session with one writer.

    A retry with the same session adopts the same record; a different session
    while one holds the round is refused. Refusals name the exact unmet
    dependency, ownership, registration, policy, or concurrency condition.
    """
    d = need_run(a.run)
    if a.owner == "orch":
        role = "orchestrator"
    else:
        task = d / f"{a.owner}-task.mdx"
        if not task.is_file():
            die(f"no assignment {a.owner} in {a.run}")
        role = "orchestrator" if parse(task.read_text())[0].get(
            "executor", "implementor") == "orchestrator" else "implementor"
    if is_quick_milestone(d) and role == "orchestrator":
        role = "implementor"
    elif mode_of(d) == MODE_QUICK and role == "orchestrator":
        # Quick merges planning and orchestration into the coordinator
        # session, the same mapping authority_for applies to submit and
        # verify duties, so the dispatch binding names the physical role.
        role = "coordinator"
    try:
        rnd, _ = state_of(d, a.owner)
    except (OSError, ValueError):
        rnd = 0
    if not rnd:
        die(f"no report for {a.owner} in {a.run}")
    if not a.session:
        die("dispatch requires --session SESSION of a registered worker")
    planning = quick_milestone_plan_problems(d, a.owner)
    if planning:
        die(f"{a.owner} cannot dispatch before the planner defines every task and "
            "closed milestone:\n      " + "\n      ".join(f"- {item}" for item in planning))
    if a.owner != "orch":
        intent_problems = task_intent_problems(d, a.owner)
        if intent_problems:
            print(f"\nINVALID TASK: {a.owner}-task.mdx\n", file=sys.stderr)
            for problem in intent_problems:
                print(f"  - {problem}", file=sys.stderr)
            die(f"{a.owner} cannot dispatch with unresolved task intent; "
                f"run `docket validate-task {a.run} {a.owner}` for the full gate")
    # A dispatch that will be refused writes nothing, a registration included. The
    # checks below repeat under the lock and stay the authority; this pass only
    # keeps a refused first dispatch from leaving a session registered behind it.
    prior = read_dispatch(d, a.owner) or {}
    adopting = (prior.get("state") == "dispatched" and int(prior.get("round", 0) or 0) == rnd
                and str(prior.get("session", "")) == a.session)
    session_problem = session_ownership_problem(d, a.session, a.owner)
    if session_problem:
        die(f"{a.owner} cannot dispatch: {session_problem}")
    if not adopting:
        milestone_problem = quick_milestone_implementation_problem(d, a.owner)
        if milestone_problem:
            die(f"{a.owner} cannot dispatch: {milestone_problem}")
        early = dispatch_dependencies_unmet(d, a.owner)
        if early:
            die(f"{a.owner} cannot dispatch with unmet dependencies:\n      "
                + "\n      ".join(f"- {item}" for item in early))
        early = dispatch_ownership_problems(d, a.owner)
        if early:
            die(f"{a.owner} cannot dispatch: the change surface is not exclusively free:\n      "
                + "\n      ".join(f"- {item}" for item in early))
        cap = max_concurrency_of(d)
        active = [r for r in live_dispatches(d)
                  if not (r.get("owner") == a.owner and int(r.get("round", 0) or 0) == rnd)]
        if cap and len(active) >= cap:
            die(f"{a.owner} cannot dispatch: provider concurrency {len(active)}/{cap} "
                "exhausted; wait for a dispatched task to finish")
        primary, _fallbacks, _policy_state, _run_allowed = model_policy(d)
        allowed = allowed_models_for_role(d, role)
        configured = role_model(d, role)
        if configured and allowed is not None and configured not in allowed:
            die(f"{a.owner} cannot dispatch: per-role model {configured!r} for {role} "
                f"is outside the approved run policy {allowed}; amend the plan first")
        candidate = a.model or configured or primary
        if allowed is not None and candidate not in allowed:
            refuse_outside_policy(d, a.owner, candidate, allowed)
        guard_provider_concurrency(d, a.owner, candidate, active)
        guard_review_reserve(d, candidate)
    if a.register:
        ensure_session_registration(a.run, a.session, role, a.agent or a.session)
    reg = check_registration(d, a.session, a.run, role)
    with owner_lock(d, a.owner):
        existing = read_dispatch(d, a.owner)
        if existing and existing.get("state") == "dispatched" \
                and int(existing.get("round", 0) or 0) == rnd \
                and str(existing.get("session", "")) == a.session:
            try:
                old_gen = int(existing.get("session_generation", 1) or 1)
            except (TypeError, ValueError):
                old_gen = 1
            try:
                new_gen = int(reg.get("generation", 1) or 1)
            except (TypeError, ValueError):
                new_gen = 1
            if old_gen == new_gen:
                print(f"already dispatched: {a.owner} round {rnd} -> session {a.session} "
                      f"({existing.get('dispatch')})")
                if a.agent and a.agent != str(existing.get("agent", "") or ""):
                    # The terminal agent was renamed (a harness name clash, a
                    # relaunch); the binding keeps pointing at who does the work.
                    existing["agent"] = a.agent
                    publish_json(dispatch_path(d, a.owner), existing)
                    print(f"agent: {a.agent}")
                existing_digest = str(existing.get("prompt_digest", "") or "")
                # The same writer re-dispatching after the task or plan changed gets
                # the prompt re-rendered and rebound, so the record never keeps
                # describing bytes nobody should send. Nothing else about the
                # binding moves: same session, same round, same model.
                model = str(existing.get("model_requested", "") or "")
                seed_report_acceptance(d, a.owner)
                fresh_digest, prompt, prompt_file = rebind_dispatch_prompt(
                    d, a.run, a.owner, role, rnd, existing, model)
                if prompt_file is not None:
                    publish_json(dispatch_path(d, a.owner), existing)
                    machine_feedback(d, "dispatch", a.owner,
                                     "prompt rebound: the task or plan changed after dispatch")
                    print(f"rebound {a.owner} round {rnd}: its prompt changed since it was "
                          f"bound (was {existing_digest}, now {fresh_digest})")
                    print(f"prompt: {prompt_file.relative_to(root())} - send these exact "
                          "bytes to the worker; the earlier prompt no longer describes the task")
                    return
                if existing_digest:
                    print(f"recorded binding {a.owner} round {rnd} "
                          f"(prompt digest {existing_digest}); no model launched, "
                          "no prompt delivered, nothing observed")
                if prompt and fresh_digest:
                    prompt_file = write_prompt_file(d, a.owner, role, rnd, prompt, fresh_digest)
                    print(f"prompt: {prompt_file.relative_to(root())}")
                return
            die(f"{a.owner} round {rnd} was dispatched to session {a.session!r} "
                f"(generation {old_gen}), but session {a.session!r} is now generation "
                f"{new_gen}; this is a different writer after re-registration. Only the "
                f"same generation may adopt; move the round with "
                f"`docket resume {a.run} {a.owner} --session NEW`")
        if existing and existing.get("state") == "dispatched" \
                and int(existing.get("round", 0) or 0) == rnd:
            die(f"{a.owner} round {rnd} is already dispatched to session "
                f"{existing.get('session')!r} ({existing.get('agent', '?')}); retry with the "
                "same --session to adopt it after a crash, or resume it elsewhere with "
                f"`docket resume {a.run} {a.owner} --session NEW`")
        unmet = dispatch_dependencies_unmet(d, a.owner)
        if unmet:
            die(f"{a.owner} cannot dispatch with unmet dependencies:\n      "
                + "\n      ".join(f"- {item}" for item in unmet))
        ownership = dispatch_ownership_problems(d, a.owner)
        if ownership:
            die(f"{a.owner} cannot dispatch: the change surface is not exclusively free:\n      "
                + "\n      ".join(f"- {item}" for item in ownership))
        for dep, digest in pin_final_dependencies(d, a.owner):
            print(f"{a.owner} consumes {dep}'s approved bundle {digest} as a final input")
        for dep in sorted(set(task_depends_on(d, a.owner))):
            if state_of(d, dep)[1] == "waived":
                print(f"note: {dep} was waived, not approved: {a.owner} builds on work accepted "
                      "without a passing verification")
        with capacity_lock(d):
            # Holds the run-wide capacity lock only for the read-check-write
            # below: re-read live capacity, refuse a full cap, and publish the
            # new record. Dependency, scope, and registration checks stay
            # outside, so unrelated work is never serialized behind a dispatch.
            perturb("dispatch:before-claim")
            milestone_problem = quick_milestone_implementation_problem(d, a.owner)
            if milestone_problem:
                die(f"{a.owner} cannot dispatch: {milestone_problem}")
            session_problem = session_ownership_problem(d, a.session, a.owner)
            if session_problem:
                die(f"{a.owner} cannot dispatch: {session_problem}")
            cap = max_concurrency_of(d)
            active = [r for r in live_dispatches(d)
                      if not (r.get("owner") == a.owner and int(r.get("round", 0) or 0) == rnd)]
            if cap and len(active) >= cap:
                die(f"{a.owner} cannot dispatch: provider concurrency {len(active)}/{cap} "
                    "exhausted; wait for a dispatched task to finish")
            primary, fallbacks, policy_state, _run_allowed = model_policy(d)
            allowed = allowed_models_for_role(d, role)
            configured = role_model(d, role)
            if configured and allowed is not None and configured not in allowed:
                die(f"{a.owner} cannot dispatch: per-role model {configured!r} for {role} "
                    f"is outside the approved run policy {allowed}; amend the plan first")
            model = a.model or configured or primary
            if allowed is not None and model not in allowed:
                refuse_outside_policy(d, a.owner, model, allowed)
            guard_provider_concurrency(d, a.owner, model, active)
            guard_review_reserve(d, model)
            if a.owner != "orch" and not snapshot_path(d, a.owner).is_file():
                # A task takes its baseline at its first dispatch, after every
                # refusal. A tiered correction has its own versioned baseline;
                # the previous round's complete capture is never replaced.
                # An incomplete capture refuses the dispatch and writes no record.
                require_baseline(d, a.owner, task_scope_hint(d, a.owner))
                if rnd > 1 and snapshot_path(d, a.owner).stem != a.owner:
                    print(f"captured {a.owner}'s fresh pre-correction baseline for round "
                          f"{rnd}; prior baselines and bundles stay intact")
                else:
                    print(f"captured {a.owner}'s baseline at dispatch")
            seed_report_acceptance(d, a.owner)
            prompt_file = None
            try:
                prompt, prompt_info = compose_prompt(d, a.run, a.owner, role, model=model)
            except SystemExit:
                diag = rendering_problem(d, a.run, a.owner, role,
                                         derive_stage(d, a.owner), model)
                if not diag:
                    raise
                prompt_digest = f"unavailable: {diag}"
                prompt_info = {"digest": prompt_digest}
            else:
                prompt_digest = str(prompt_info.get("digest", ""))
                prompt_file = write_prompt_file(d, a.owner, role, rnd, prompt, prompt_digest)
            txn = "dsp:" + hashlib.sha256(
                "|".join([a.run, a.owner, str(rnd), a.session]).encode()).hexdigest()[:16]
            record = {
                "dispatch": txn,
                "run": a.run,
                "owner": a.owner,
                "round": rnd,
                "role": role,
                "session": a.session,
                "session_generation": reg.get("generation", 1),
                "agent": a.agent or a.session,
                "model_requested": model,
                "model_fallbacks": fallbacks,
                "model_observed": "unobserved",
                "model_history": [{"at": stamp(), "kind": "requested", "model": model or "unknown"}],
                "prompt_digest": prompt_digest,
                "model_profile": str(prompt_info.get("model_profile", "") or ""),
                "prompt_role": role,
                "state": "dispatched",
                "dispatched_at": stamp(),
            }
            (d / ".dispatch").mkdir(parents=True, exist_ok=True)
            publish_json(dispatch_path(d, a.owner), record)
            fault("dispatch:launch")
            print(f"dispatched {a.owner} round {rnd} -> session {a.session} ({txn})")
            print(f"recorded binding {a.owner} round {rnd} for model {model or 'unobserved'} "
                  f"(prompt digest {prompt_digest}); no model launched, no prompt delivered, "
                  "nothing observed")
            if prompt_file is not None:
                print(f"prompt: {prompt_file.relative_to(root())} - send these exact bytes to "
                      "the worker")
            if policy_state == "absent":
                print(f"run {a.run} has no approved model policy so nothing is being enforced")
            if not model:
                print("model telemetry unavailable: model is unknown until `docket set-model` "
                      "verifies the live harness")


def write_prompt_file(d: Path, owner: str, role: str, rnd: int, prompt: str,
                      digest: str) -> Path:
    """Keep the exact prompt bytes a dispatch bound, named by round and digest.

    The file lets a supervisor hand the worker the bound prompt without a second
    render, and its bytes hash to the recorded prompt digest.
    """
    short = digest.split(":", 1)[-1][:12]
    path = prompts_dir(d) / f"{owner}-{role}-r{rnd:02d}-{short}.txt"
    if not path.is_file():
        prompts_dir(d).mkdir(parents=True, exist_ok=True)
        publish_bytes(path, prompt.encode())
    return path


def cmd_resume(a: argparse.Namespace) -> None:
    """Resume a dispatched task under a new session after checkpointing."""
    d = need_run(a.run)
    with owner_lock(d, a.owner):
        dispatch = read_dispatch(d, a.owner)
        if not dispatch or dispatch.get("state") not in ("dispatched", "released-handoff"):
            die(f"no active dispatch for {a.owner} in {a.run}; dispatch it first")
        if not a.session:
            die("resume requires --session SESSION of a registered worker")
        try:
            rnd, round_state = state_of(d, a.owner)
        except (OSError, ValueError):
            rnd, round_state = 0, ""
        if not rnd:
            die(f"no report for {a.owner} in {a.run}; dispatch it first")
        if round_state in TERMINAL_REPORT_STATES:
            die(f"{a.owner} round {rnd} is {round_state}: there is no work left to resume, and "
                "a recovery recorded for it would describe something that never happened")
        try:
            bound_round = int(dispatch.get("round", 0) or 0)
        except (TypeError, ValueError):
            bound_round = 0
        if bound_round != rnd:
            # Rebinding a superseded dispatch to a correction round is a new
            # dispatch claim. It must not skip the current round's admission
            # checks merely because the predecessor already held a slot.
            if a.owner != "orch":
                milestone_problem = quick_milestone_implementation_problem(d, a.owner)
                if milestone_problem:
                    die(f"{a.owner} cannot resume: {milestone_problem}")
                intent_problems = task_intent_problems(d, a.owner)
                if intent_problems:
                    print(f"\nINVALID TASK: {a.owner}-task.mdx\n", file=sys.stderr)
                    for problem in intent_problems:
                        print(f"  - {problem}", file=sys.stderr)
                    die(f"{a.owner} cannot resume with unresolved task intent; "
                        f"run `docket validate-task {a.run} {a.owner}` for the full gate")
            unmet = dispatch_dependencies_unmet(d, a.owner)
            if unmet:
                die(f"{a.owner} cannot resume with unmet dependencies:\n      "
                    + "\n      ".join(f"- {item}" for item in unmet))
            ownership = dispatch_ownership_problems(d, a.owner)
            if ownership:
                die(f"{a.owner} cannot resume: the change surface is not exclusively free:\n      "
                    + "\n      ".join(f"- {item}" for item in ownership))
        # A resume launches a new session, so it is held to the model policy in
        # force now, exactly like a dispatch. Carrying the predecessor's model
        # forward unchecked would let a policy change be bypassed by resuming
        # instead of dispatching. --model names an approved replacement.
        _primary, _fallbacks, _state, _run_allowed = model_policy(d)
        role = str(dispatch.get("role", "implementor") or "implementor")
        allowed = allowed_models_for_role(d, role)
        previous_model = str(dispatch.get("model_requested", "") or "")
        if a.on_limit:
            if round_state != "draft":
                die(f"{a.owner} round {rnd} is {round_state}; --on-limit requires a draft round")
            task_file = d / f"{a.owner}-task.mdx"
            try:
                task_meta, task_body = parse(task_file.read_text())
            except (OSError, ValueError):
                die(f"{a.owner} needs a readable task contract for usage-limit recovery")
            if task_meta.get("harness") != "codex":
                die(f"{a.owner} is not running in the Codex harness; --on-limit does not apply")
            if not codex_limit_exhausted(codex_rate_limits()):
                die("Codex has no current exhausted usage window; --on-limit would be a "
                    "fallback without its recorded trigger")
            if a.model:
                die("--on-limit selects the next approved model; omit --model")
            if a.harness not in ("opencode", "claude"):
                die("--on-limit needs --harness opencode or --harness claude for the replacement")
            role = str(dispatch.get("role", "implementor") or "implementor")
            model = next_role_fallback(d, role, previous_model,
                                       dispatch.get("model_history", []))
            if not model:
                entry = emit_exception(d, a.owner, "model-fallback-exhausted",
                                       "Codex usage limit is exhausted and no untried "
                                       "approved fallback remains")
                die(f"{a.owner}: {entry['detail']} (exception {entry['id']} is open)")
            if model.startswith("opencode/") and a.harness != "opencode":
                die(f"fallback model {model} requires the opencode harness")
            if model.startswith("claude-") and a.harness != "claude":
                die(f"fallback model {model} requires the claude harness")
            if model.startswith(("gpt-", "codex/")):
                die(f"fallback model {model} still requires Codex, whose usage limit is "
                    "exhausted; approve a non-Codex fallback before resuming")
        else:
            if a.harness:
                die("--harness is only for --on-limit recovery")
            model = a.model or previous_model
        if allowed is not None and model not in allowed:
            if a.model:
                refuse_outside_policy(d, a.owner, model, allowed)
            die(f"{a.owner} cannot resume on model {model or 'unrecorded'!r}: the approved "
                f"model policy is now [{', '.join(allowed)}]. Resume with "
                f"`docket resume {a.run} {a.owner} --session {a.session} --model "
                f"{allowed[0]}` or another approved model")
        guard_review_reserve(d, model)
        session_problem = session_ownership_problem(d, a.session, a.owner)
        if session_problem:
            die(f"{a.owner} cannot resume: {session_problem}")
        if a.register:
            ensure_session_registration(a.run, a.session,
                                        str(dispatch.get("role", "implementor")),
                                        a.agent or a.session)
        reg = check_registration(d, a.session, a.run, dispatch.get("role", "implementor"))
        with capacity_lock(d):
            if bound_round != rnd:
                milestone_problem = quick_milestone_implementation_problem(d, a.owner)
                if milestone_problem:
                    die(f"{a.owner} cannot resume: {milestone_problem}")
            guard_review_reserve(d, model)
            # The same serialized read-check-write as dispatch: re-read live
            # capacity, refuse a full cap, and publish the rebound record.
            # The predecessor slot this resume takes over is excluded, so a
            # legitimate replacement is never refused by its own record.
            session_problem = session_ownership_problem(d, a.session, a.owner)
            if session_problem:
                die(f"{a.owner} cannot resume: {session_problem}")
            cap = max_concurrency_of(d)
            active = [r for r in live_dispatches(d)
                      if not (r.get("owner") == a.owner and int(r.get("round", 0) or 0) == rnd)]
            if cap and len(active) >= cap:
                die(f"{a.owner} cannot resume: provider concurrency {len(active)}/{cap} "
                    "exhausted; wait for a dispatched task to finish")
            guard_provider_concurrency(d, a.owner, model, active)
            checkpoint = write_checkpoint(d, a.owner)
            if a.on_limit:
                task_meta["harness"] = a.harness
                task_meta["requested_model"] = model
                task_meta["actual_model"] = ""
                publish(task_file, render(task_meta, task_body))
            history = dispatch.get("session_history", [])
            if not isinstance(history, list):
                history = [history] if history else []
            history.append({"at": stamp(), "from": dispatch.get("session", ""),
                            "to": a.session,
                            "reason": a.reason or ("Codex usage limit exhausted"
                                                   if a.on_limit else "resume without ready handoff")})
            dispatch["session_history"] = history
            dispatch["session"] = a.session
            dispatch.pop("worker_process", None)
            if a.on_limit:
                dispatch["harness_requested"] = a.harness
            dispatch["session_generation"] = reg.get("generation", 1)
            if a.agent:
                dispatch["agent"] = a.agent
            # Bind the current round: a correction opened a newer report round
            # since this record was written, and a record pinned to the old
            # round reads as superseded, hence not live, so the slot it still
            # holds would escape the cap. The rebound record describes the
            # round the replacement actually works.
            dispatch["round"] = rnd
            role = str(dispatch.get("role", "implementor"))
            if model != previous_model:
                model_history = dispatch.get("model_history", [])
                if not isinstance(model_history, list):
                    model_history = []
                model_history.append({"at": stamp(), "kind": "resume", "model": model})
                dispatch["model_history"] = model_history
                dispatch["model_requested"] = model
                dispatch["model_observed"] = "unobserved"
            prompt_file = None
            try:
                resumed_prompt, prompt_info = compose_prompt(d, a.run, a.owner, role, model=model)
                dispatch["prompt_digest"] = str(prompt_info.get("digest", ""))
                dispatch["model_profile"] = str(prompt_info.get("model_profile", "") or "")
                prompt_file = write_prompt_file(d, a.owner, role, rnd, resumed_prompt,
                                                dispatch["prompt_digest"])
            except SystemExit:
                diag = rendering_problem(d, a.run, a.owner, role,
                                         derive_stage(d, a.owner), model)
                if not diag:
                    raise
                dispatch["prompt_digest"] = f"unavailable: {diag}"
            dispatch["prompt_role"] = role
            dispatch["dispatch"] = "dsp:" + hashlib.sha256(
                "|".join([a.run, a.owner, str(rnd), a.session,
                          str(len(history))]).encode()).hexdigest()[:16]
            dispatch["state"] = "dispatched"
            dispatch.pop("released_reason", None)
            dispatch.pop("released_at", None)
            publish_json(dispatch_path(d, a.owner), dispatch)
            fault("resume:rebind")
            machine_feedback(d, "recovery", a.owner,
                             f"round moved to a replacement session: {a.reason or 'no reason given'}")
            print(f"resumed {a.owner} round {rnd} -> session {a.session}")
            if a.on_limit:
                print(f"Codex usage limit recovery selected approved fallback {model} on "
                      f"{a.harness}; no plan amendment")
            if resumable_checkpoint(d, a.owner, rnd) is None:
                print(f"checkpoint {checkpoint.name} measured no work; the replacement "
                      "starts the round fresh")
            else:
                print(f"checkpoint {checkpoint.name} is mechanical (semantic_handoff: no); "
                      "the replacement discovers from the task, scope, and diff")
            if prompt_file is not None:
                print(f"prompt: {prompt_file.relative_to(root())} - send these exact bytes to "
                      "the replacement")


def rebind_dispatch_prompt(d: Path, run: str, owner: str, role: str, rnd: int,
                           record: dict, model: str) -> tuple[str, str, Path | None]:
    """Re-render a dispatched round's prompt and rebind the record when its bytes moved.

    Returns the fresh digest, the prompt, and the prompt file when the binding changed,
    keeping the replaced digest in `prompt_history`; the caller publishes the record.
    """
    old = str(record.get("prompt_digest", "") or "")
    try:
        prompt, info = compose_prompt(d, run, owner, role, model=model)
    except SystemExit:
        return "", "", None
    fresh = str(info.get("digest", "") or "")
    if not fresh or fresh == old:
        return fresh, prompt, None
    history = record.get("prompt_history", [])
    if not isinstance(history, list):
        history = []
    history.append({"at": stamp(), "digest": old, "replaced_by": fresh})
    record["prompt_history"] = history
    record["prompt_digest"] = fresh
    record["model_profile"] = str(info.get("model_profile", "") or "")
    record["prompt_role"] = role
    return fresh, prompt, write_prompt_file(d, owner, role, rnd, prompt, fresh)


def cmd_switch_model(a: argparse.Namespace) -> None:
    """Move along the approved fallback list, or emit one compact exception."""
    d = need_run(a.run)
    with owner_lock(d, a.owner):
        dispatch = read_dispatch(d, a.owner)
        if not dispatch or dispatch.get("state") != "dispatched":
            die(f"no active dispatch for {a.owner} in {a.run}; dispatch it first")
        _primary, _fallbacks, _state, _run_allowed = model_policy(d)
        role = str(dispatch.get("role", "implementor") or "implementor")
        allowed = allowed_models_for_role(d, role)
        tried = [h.get("model", "") for h in dispatch.get("model_history", [])
                 if isinstance(h, dict)]
        ordered = list(allowed) if allowed is not None else []
        if a.model in tried:
            die(f"model {a.model!r} was already tried for {a.owner}; "
                f"history: {', '.join(tried)}")
        if allowed is not None and a.model not in ordered:
            refuse_outside_policy(d, a.owner, a.model, ordered)
        guard_review_reserve(d, a.model)
        if a.session:
            die(f"a new session is a new launch, not a model switch: run `docket resume "
                f"{a.run} {a.owner} --session {a.session} --model {a.model}`, which records the "
                "session history and hands the new session its prompt")
        checkpoint = write_checkpoint(d, a.owner)
        history = dispatch.get("model_history", [])
        history.append({"at": stamp(), "kind": "fallback", "model": a.model})
        dispatch["model_history"] = history
        dispatch["model_requested"] = a.model
        # The observed model was the old one; until `set-model` sees the new one, every
        # outcome of this round belongs to the model now requested, never the last seen.
        dispatch["model_observed"] = "unobserved"
        role = str(dispatch.get("role", "implementor") or "implementor")
        rnd = int(dispatch.get("round", 0) or 0)
        fresh, prompt, prompt_file = rebind_dispatch_prompt(
            d, a.run, a.owner, role, rnd, dispatch, a.model)
        publish_json(dispatch_path(d, a.owner), dispatch)
        print(f"switched {a.owner} to model {a.model}; the harness switch is unverified until "
              f"`docket set-model` confirms it (checkpoint {checkpoint.name})")
        prompt_path = prompt_file or (write_prompt_file(d, a.owner, role, rnd, prompt, fresh)
                                      if prompt and fresh else None)
        if prompt_path is not None:
            print(f"prompt: {prompt_path.relative_to(root())} - send these exact bytes to the "
                  "worker on the new model")


def exceptions_dir(d: Path) -> Path:
    return d / ".exceptions"


def emit_exception(d: Path, owner: str, kind: str, detail: str) -> dict:
    """One compact, deduplicated exception per owner and cause."""
    path = exceptions_dir(d) / f"{owner}.json"
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
            if isinstance(existing, dict) and existing.get("state") == "open" \
                    and existing.get("kind") == kind:
                return existing
        except (OSError, ValueError):
            pass
    entry = {"id": f"{owner}-{kind}", "run": d.name, "owner": owner, "kind": kind,
             "detail": detail, "state": "open", "opened_at": stamp()}
    exceptions_dir(d).mkdir(parents=True, exist_ok=True)
    publish_json(path, entry)
    return entry


def refuse_outside_policy(d: Path, owner: str, model: str, allowed: list[str]) -> None:
    """Refuse a model outside the approved list through the open exception.

    This is the single rule for out-of-policy models. Both initial dispatch
    and fallback switching call it, so the exception kind, detail shape, and
    open-until-resolved behavior cannot drift between entry points.
    """
    entry = emit_exception(
        d, owner, "model-fallback-exhausted",
        f"model {model!r} is outside the approved fallback list "
        f"[{', '.join(allowed)}]; a higher spending tier needs its owner")
    die(f"{owner}: {entry['detail']} (exception {entry['id']} is open)")
