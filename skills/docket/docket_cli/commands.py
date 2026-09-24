"""Run and task commands: init, assign, scope, handoff, status, diff, bundle, depend."""

from __future__ import annotations

import argparse
import fcntl
import re
import shlex
import sys
from pathlib import Path

from .common import (
    COVERAGE_AVAILABLE, COVERAGE_UNAVAILABLE, DOCUMENTS_ONLY, MODE_CUSTOM,
    MODE_NEW_RUN_DEFAULT, MODE_QUICK, MODE_STANDARD, REQUIRED_HANDOFF_SECTIONS,
    REQUIRED_SCOPE_SECTIONS, REVIEW_COMBINED_CHECKER, REVIEW_INDEPENDENT, ROOTS_DECLARED,
    ROOTS_INVALID, STATE_DIR, SUPPORTED_HARNESSES, WORKFLOW_FIVE_ROLE,
    WORKFLOW_LEGACY_DECODE, WORKFLOW_NEW_RUN_DEFAULT, die, stamp,
)
from .frontmatter import parse, render, set_section
from .paths import (
    decisions, dispatch_path, handoffs, latest, next_numbered, owners, planned_tasks,
    read_dispatch, reports, root, scope_path,
)
from .publication import fault, publish, publish_exclusive, publish_json
from .policy import (
    artifact_policy_fields, max_concurrency_of, mode_of, need_run, require_preset_role,
    role_sessions_for,
)
from .evidence import git_root
from .roots import candidate_roots, declare_roots, read_roots, resolve_scope, root_identity
from .baselines import (
    assignment_evidence, baseline_path, evidence_mode, output_fingerprint, require_baseline,
    snapshot_path, task_verify, update_snapshot_scope,
)
from .bundles import (
    FINAL_STATES, PROVISIONAL_ALLOWED, bundle_dir, bundle_problems, bundles_for,
    latest_bundle, load_bundle, source_identity,
)
from .locks import owner_lock
from .state import (
    DECIDE_FLAGS, _cycle_for_new_edges, decide_as, generated_path_hint, handoff_state,
    list_batches, list_incidents, normalized_scope, read_transition, scope_collisions,
    scope_state, seed_report_acceptance, state_of, unfinished_decision,
)
from .dependencies import (
    consumable_problems, dependency_problems, provisional_policy, read_deps,
    withdrawn_readiness, write_deps,
)
from .aggregates import aggregate_bundle_problems, stale_aggregate
from .verification import (
    claim_verify_slot, execute_verify, latest_reusable_verification, task_env,
    verification_run_env, verify_slot_path,
)
from .templates import template
from .sessions import calling_harness
from .liveness import live_dispatches
from .batches import batch_ready
from .delivery import delivery_lock, sweep_role
from .health import execution_health
from .gate import required_section_problems, task_intent_problems


def init_policy(a: argparse.Namespace) -> dict[str, str]:
    """Resolve one new-run preset before creating any run directory."""
    selected_mode = (a.mode or "").strip()
    requested_workflow = (a.workflow or "").strip()
    requested_topology = (a.topology or "").strip()
    agents = int(a.agents or 0)
    if requested_workflow == WORKFLOW_LEGACY_DECODE:
        die("legacy workflow is a historical decode for existing runs only and "
            "cannot be selected for a new run; use --mode standard or --mode quick "
            "(workflow five-role-v1)")
    if selected_mode == MODE_QUICK:
        if agents == 2:
            die("the two-role quick variant is deferred and is not in this build; "
                "use --mode quick with three agents or choose --mode standard")
        if agents not in (0, 3):
            die("quick mode is the three-role preset; --agents must be 3 "
                "(the two-role variant is deferred)")
        if requested_workflow and requested_workflow != WORKFLOW_FIVE_ROLE:
            die("quick mode requires workflow five-role-v1; mode and workflow are "
                "separate, and this preset cannot be weakened to legacy")
        if requested_topology and requested_topology != "combined":
            die("quick mode requires topology combined for its coordinator session")
        workflow, selected_topology = WORKFLOW_FIVE_ROLE, "combined"
    elif selected_mode == MODE_STANDARD:
        if agents not in (0, 5):
            die("standard mode is the five-role preset; --agents must be 5")
        if requested_workflow and requested_workflow != WORKFLOW_FIVE_ROLE:
            die("standard mode requires workflow five-role-v1")
        if requested_topology and requested_topology != "split":
            die("standard mode requires topology split. For one coordinator session "
                "combining planning and orchestration use --mode quick, the default; for "
                "a custom configuration use --workflow and --topology without --mode")
        workflow, selected_topology = WORKFLOW_FIVE_ROLE, "split"
    else:
        if agents:
            die("--agents is only valid with --mode standard or --mode quick")
        if not requested_workflow and not requested_topology:
            selected_mode = MODE_NEW_RUN_DEFAULT
            workflow = WORKFLOW_NEW_RUN_DEFAULT
            selected_topology = "combined" if selected_mode == MODE_QUICK else "split"
        else:
            workflow = requested_workflow or WORKFLOW_NEW_RUN_DEFAULT
            selected_topology = requested_topology or "split"
            selected_mode = MODE_CUSTOM
    review_policy = (REVIEW_COMBINED_CHECKER if selected_mode == MODE_QUICK
                     else ("legacy-completion" if workflow == WORKFLOW_LEGACY_DECODE
                           else REVIEW_INDEPENDENT))
    return {
        "mode": selected_mode,
        "workflow": workflow,
        "topology": selected_topology,
        "role_sessions": role_sessions_for(selected_mode, workflow, selected_topology),
        "review_policy": review_policy,
    }


def cmd_init(a: argparse.Namespace) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", a.run):
        die("run id must contain only letters, numbers, dots, underscores, and hyphens")
    base = root() / STATE_DIR / "runs" / a.run
    if base.exists():
        die(f"run {a.run} already exists at {base}")
    policy = init_policy(a)
    (base / "docs").mkdir(parents=True)
    plan = base / "plan.mdx"
    # Declared checkout roots are Git evidence by definition, even when `.docket` sits
    # in a plain parent directory with no checkout of its own.
    evidence = a.evidence_mode or ("git" if a.root or git_root() else DOCUMENTS_ONLY)
    text = template("plan").format(
        run=a.run, harness=a.harness, evidence_mode=evidence, **policy,
    )
    # Stating the intent at creation saves the open-and-edit round trip a fresh
    # plan otherwise needs before anything can be assigned.
    if (a.title or "").strip():
        text = text.replace("# Plan: <title>", f"# Plan: {a.title.strip()}", 1)
    for section, value in (("Objective", a.objective), ("Approach", a.approach)):
        if (value or "").strip():
            meta, body = parse(text)
            body = set_section(body, section, value.strip())
            text = render(meta, body)
    publish(plan, text)
    print(f"created {plan.relative_to(root())}")
    print(f"mode: {policy['mode']} - workflow {policy['workflow']}, "
          f"topology {policy['topology']}, sessions {policy['role_sessions']}")
    if evidence == DOCUMENTS_ONLY:
        print("evidence_mode: documents-only - task diff coverage is reported as unavailable")
    else:
        print("evidence_mode: git - task diffs must be resolvable to review a report")
        if a.root or root_identity(root()):
            declared = declare_roots(base, a.root)
            print(f"declared {len(declared)} checkout root(s): "
                  + ", ".join(f"{r['alias']}={r['path']}" for r in declared))
            if not a.root:
                print("Only this checkout is declared. A linked worktree or a nested "
                      "repository is\nnever declared implicitly: name each one with "
                      f"`docket roots {a.run} --redeclare\nalias=path ...` before the "
                      "first assignment.")
        else:
            print(f"{root()} is not inside a Git checkout, so no root was declared.")
            print(f"Declare every checkout before assigning:\n"
                  f"  docket roots {a.run} --declare app=./app lib=./lib")
    if (a.objective or "").strip():
        print(f"\nNext: assign the first task with `docket assign {a.run} T01 --goal TEXT "
              "--criterion TEXT ...`.")
    else:
        print(f"\nNext: fill in the plan, then `docket status {a.run}`.")


def cmd_assign(a: argparse.Namespace) -> None:
    d = need_run(a.run)
    if a.tier:
        a.complexity = "low" if a.tier == "small" else "high"
        a.executor = "implementor" if a.tier == "small" else "orchestrator"
    if a.files:
        a.file.extend(shlex.split(a.files))
    if not a.harness:
        # The aggregate and an orchestrator-executed task run in the assigning
        # supervisor's own session, so its harness is the honest default.
        if a.owner == "orch" or a.executor == "orchestrator":
            planned = str(parse((d / "plan.mdx").read_text())[0].get("planner_harness", ""))
            a.harness = calling_harness() or (planned if planned in SUPPORTED_HARNESSES
                                              else "opencode")
        else:
            a.harness = "opencode"
    if reports(d, a.owner):
        die(f"{a.owner} already has a report; use `docket decide` to open a new round")
    # These become frontmatter lines, and a line break would write a key of its own.
    for label, value in (("--verify", a.verify), ("--model", a.model), ("--effort", a.effort),
                         *(("--file", item) for item in a.file),
                         *(("--env", item) for item in a.env),
                         *(("--depends-on", item) for item in a.depends_on)):
        if "\n" in str(value or "") or "\r" in str(value or ""):
            die(f"{label} cannot contain a line break, since it becomes one frontmatter "
                f"line: {str(value)!r}. Nothing was assigned")
    criteria = [c.strip() for c in (a.criterion or []) if c.strip()]
    if a.owner == "orch" and ((a.title or "").strip() or (a.goal or "").strip() or criteria
                              or a.out_of_scope or a.decision):
        die("--title, --goal, --criterion, --out-of-scope, and --decision describe a task; "
            "the aggregate's intent is the plan Objective (`docket init --objective`)")
    assign_deps: list[str] = []
    if a.owner != "orch":
        raw_deps = [e.strip() for e in (a.depends_on or []) if e.strip()]
        assign_deps = sorted({item.strip() for entry in raw_deps
                              for item in entry.split(",") if item.strip()})
        if assign_deps:
            cycle = _cycle_for_new_edges(d, {a.owner: assign_deps})
            if cycle is not None:
                die(f"refusing dependency cycle: {' -> '.join(cycle)} "
                    f"({a.owner} depends on {', '.join(assign_deps)}); nothing published")
    # Baselines precede the work they measure, and an incomplete one refuses it.
    # The run baseline is taken before the first subordinate can edit anything.
    require_baseline(d, "run", [], kind="run")
    # A delegated task takes its own baseline when it is dispatched, not here. A
    # batch is assigned before any implementor starts, so a baseline taken now
    # would predate the earlier tasks' work, and that work would then show up as
    # this task's own out-of-scope change. Taken at dispatch, work finished before
    # then is its starting point. It is still captured once, whole, and before the
    # task's own work. An orchestrator-executed task is started by the session that
    # assigns it and may never be dispatched, so it is captured here.
    deferred = [dep for dep in (assign_deps if a.owner != "orch" else [])
                if state_of(d, dep)[1] not in ("approved", "completed")]
    delegated = a.owner != "orch" and a.executor == "implementor"
    if a.owner != "orch" and not deferred and not delegated:
        require_baseline(d, a.owner, a.file)
    task = d / f"{a.owner}-task.mdx"
    policy_fields = artifact_policy_fields(d)
    if not task.exists() and a.owner != "orch":
        delegated = a.executor == "implementor"
        text = template("task").format(
            run=a.run, owner=a.owner, complexity=a.complexity,
            executor=a.executor, harness=a.harness, model=a.model,
            effort=a.effort,
            scope_status="discovery" if delegated else "ready",
            file_hints=" ".join(a.file), verify_hint=a.verify,
            files="" if delegated else " ".join(a.file),
            verify="" if delegated else a.verify,
            verify_timeout=a.verify_timeout,
            env=", ".join(sorted({e.strip() for e in a.env if e.strip()})),
            depends_on=", ".join(sorted({e.strip() for e in a.depends_on if e.strip()})),
            **policy_fields,
        )
        # Intent stated on the command line lands in the task directly, so a
        # simple task is dispatchable without opening and editing the template.
        # The same intent gate still decides whether it is complete.
        if (a.title or "").strip():
            text = text.replace(f"# Task {a.owner}: <title>",
                                f"# Task {a.owner}: {a.title.strip()}", 1)
        out_of_scope = [c.strip() for c in (a.out_of_scope or []) if c.strip()]
        decisions = [c.strip() for c in (a.decision or []) if c.strip()]
        if (a.goal or "").strip() or criteria or out_of_scope or decisions:
            meta, body = parse(text)
            if (a.goal or "").strip():
                body = set_section(body, "Goal", a.goal.strip())
            if criteria:
                body = set_section(body, "Acceptance criteria",
                                   "\n".join(f"- [ ] {item}" for item in criteria))
            if out_of_scope:
                body = set_section(body, "Out of scope",
                                   "\n".join(f"- {item}" for item in out_of_scope))
            if decisions:
                body = set_section(body, "Existing decisions",
                                   "\n".join(f"- {item}" for item in decisions))
            text = render(meta, body)
        publish(task, text)
        print(f"created {task.relative_to(root())}")
        if deferred:
            print(f"{a.owner} depends on {', '.join(deferred)}, not approved yet: its baseline "
                  "is captured when it is dispatched, so that work is not counted as its own")
        elif delegated and evidence_mode(d) == "git":
            print(f"{a.owner}'s baseline is captured when it is dispatched, so work finished "
                  "before then is its starting point, not its own change")
        if (a.goal or "").strip() or criteria:
            problems = task_intent_problems(d, a.owner)
            print(f"{task.name}: " + ("task intent is ready to dispatch" if not problems
                                      else "still needs " + "; ".join(problems)))
        if len(a.file) > 8:
            print(f"note: {len(a.file)} allowed paths is a guessed breadth, not a scope "
                  "verdict; start with one coherent behavior and 3-6 acceptance "
                  "obligations, then reassess after discovery. This warning never "
                  "rejects an assignment.")
    rep = d / f"{a.owner}-report-01.mdx"
    report_template = "orchestrator_report" if a.owner == "orch" else "report"
    fields: dict[str, object] = {
        "run": a.run, "owner": a.owner, "round": 1, "harness": a.harness,
        "model": a.model, "effort": a.effort,
        **policy_fields,
    }
    if a.owner == "orch":
        # The aggregate has no task file; its integration verification command
        # lives in the report frontmatter, is covered by the submission drift
        # checks like any other report byte, and freezes into the bundle.
        fields["verify"] = a.verify
        fields["verify_timeout"] = a.verify_timeout
    publish(rep, template(report_template).format(**fields))
    seed_report_acceptance(d, a.owner, rep)
    print(f"created {rep.relative_to(root())}")
    if a.owner != "orch" and a.executor == "implementor":
        capsule = scope_path(d, a.owner)
        publish(capsule, template("scope").format(
            run=a.run, owner=a.owner, files=" ".join(a.file), verify=a.verify,
            **policy_fields,
        ))
        print(f"created {capsule.relative_to(root())}")


def cmd_validate_task(a: argparse.Namespace) -> None:
    d = need_run(a.run)
    task = d / f"{a.owner}-task.mdx"
    if not task.is_file():
        die(f"missing assignment {task.name}")
    problems = task_intent_problems(d, a.owner)
    if problems:
        print(f"\nINVALID TASK: {task.name}\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(1)
    print(f"{task.name}: task intent is ready for implementor-owned discovery")


def gate_verify(assigned: str, discovered: str) -> str:
    """The command the gate runs: the assigned one, plus the capsule's own if it differs.

    The supervisor's `--verify` is the acceptance oracle and the implementor's is
    discovery, so a capsule may add checks but never replace the assigned ones. Each
    side runs in its own subshell so a `||` in either cannot mask the other's failure.
    """
    assigned, discovered = assigned.strip(), discovered.strip()
    if not assigned or discovered == assigned or discovered.startswith(f"({assigned}) && "):
        return discovered or assigned
    if not discovered:
        return assigned
    return f"({assigned}) && ({discovered})"


def cmd_scope(a: argparse.Namespace) -> None:
    """Validate an implementor-authored discovery capsule and claim its change scope."""
    d = need_run(a.run)
    task = d / f"{a.owner}-task.mdx"
    capsule = scope_path(d, a.owner)
    if not task.is_file() or not capsule.is_file():
        die(f"missing discovery assignment for {a.owner}")
    if not a.submit:
        print(f"discovery capsule -> {capsule.name}")
        print(f"Fill it, then run `docket scope {a.run} {a.owner} --submit` before editing.")
        return

    meta, body = parse(capsule.read_text())
    if meta.get("status") not in ("draft", "collision", "ready"):
        die(f"{capsule.name} has unsupported status {meta.get('status')}")
    problems = required_section_problems(body, REQUIRED_SCOPE_SECTIONS)
    proposed = normalized_scope(meta)
    if not proposed:
        problems.append("frontmatter files must name the proposed change scope")
    if not meta.get("verify", "").strip():
        problems.append("frontmatter verify must contain an executable verification command")
    if problems:
        print(f"\nINVALID SCOPE: {capsule.name}\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(1)

    lock_path = d / ".scope.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        collisions = scope_collisions(d, a.owner, proposed)
        if collisions:
            meta["status"] = "collision"
            meta["collisions"] = "; ".join(collisions)
            publish(capsule, render(meta, body))
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            print(f"\nSCOPE COLLISION: {capsule.name}\n", file=sys.stderr)
            for collision in collisions:
                print(f"  - {collision}", file=sys.stderr)
            print("The orchestrator must sequence the tasks or resolve ownership.", file=sys.stderr)
            raise SystemExit(1)

        if not snapshot_path(d, a.owner).is_file():
            # Implementation starts once the scope is accepted, so a task that was
            # never dispatched is baselined now, before its capsule is published.
            require_baseline(d, a.owner, proposed)
        meta["status"] = "ready"
        meta.pop("collisions", None)
        publish(capsule, render(meta, body))
        task_meta, task_body = parse(task.read_text())
        task_meta["scope_status"] = "ready"
        task_meta["files"] = " ".join(proposed)
        gate_command = gate_verify(str(task_meta.get("verify_hint", "")), meta["verify"])
        task_meta["verify"] = gate_command
        publish(task, render(task_meta, task_body))
        update_snapshot_scope(d, a.owner, proposed)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    print(f"{capsule.name}: scope accepted; implementation may continue")
    if gate_command != meta["verify"].strip():
        print(f"the gate runs the assigned verify command as well: {gate_command}")
    if evidence_mode(d) == "git":
        state, roots, reason = read_roots(d)
        if state != ROOTS_DECLARED:
            print(f"note: {reason}", file=sys.stderr)
        unresolved = resolve_scope(roots, proposed)[1]
        for problem in unresolved:
            print(f"note: {problem}", file=sys.stderr)
        if unresolved:
            print(
                "Docket cannot claim Git diff coverage while a scope path has no declared\n"
                "root. Qualify it as `<alias>:<path>`, declare the missing checkout, or\n"
                "expect the gate to reject the report as unavailable coverage.",
                file=sys.stderr,
            )


def cmd_handoff(a: argparse.Namespace) -> None:
    d = need_run(a.run)
    rep = latest(reports(d, a.owner))
    if not rep:
        die(f"no active report for {a.owner}")
    rm, _ = parse(rep.read_text())
    if rm.get("status") != "draft":
        die(f"handoff requires an unfinished draft report, got {rm.get('status')}")

    existing = handoffs(d, a.owner)
    current = latest(existing)
    if a.submit:
        if not current:
            die(f"no handoff draft for {a.owner}; open one without --submit first")
        meta, body = parse(current.read_text())
        if meta.get("status") != "draft":
            die(f"{current.name} is already {meta.get('status')}")
        problems = required_section_problems(body, REQUIRED_HANDOFF_SECTIONS)
        if rm.get("requested_model") and not meta.get("actual_model"):
            problems.append("handoff must record the verified active model")
        if problems:
            print(f"\nINVALID HANDOFF: {current.name}\n", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            raise SystemExit(1)
        meta["status"] = "ready"
        publish(current, render(meta, body))
        print(f"{current.name}: ready for a replacement implementor")
        with owner_lock(d, a.owner):
            dispatch = read_dispatch(d, a.owner)
            if isinstance(dispatch, dict) and dispatch.get("state") == "dispatched":
                try:
                    rnd, _ = state_of(d, a.owner)
                except (OSError, ValueError):
                    rnd = 0
                try:
                    rec_round = int(dispatch.get("round", 0) or 0)
                except (TypeError, ValueError):
                    rec_round = 0
                if rnd and rec_round == rnd:
                    dispatch["state"] = "released-handoff"
                    dispatch["released_reason"] = f"handoff {meta.get('handoff', '?')} ready for replacement"
                    dispatch["released_at"] = stamp()
                    publish_json(dispatch_path(d, a.owner), dispatch)
                    print(f"released execution capacity for {a.owner} round {rnd}; "
                          "accepted scope stays held")
        return

    with owner_lock(d, a.owner):
        existing = handoffs(d, a.owner)
        current = latest(existing)
        if current:
            cm, _ = parse(current.read_text())
            if cm.get("status") == "draft":
                die(f"finish or remove existing handoff draft {current.name}")
        for _ in range(100):
            existing = handoffs(d, a.owner)
            number = next_numbered(existing)
            target = d / f"{a.owner}-handoff-{number:02d}.mdx"
            try:
                publish_exclusive(target, template("handoff").format(
                    run=a.run,
                    owner=a.owner,
                    handoff=number,
                    round=rm.get("round", "1"),
                    harness=rm.get("harness", ""),
                    model=rm.get("actual_model", ""),
                    effort=rm.get("actual_effort", ""),
                    **artifact_policy_fields(d),
                ))
            except FileExistsError:
                continue
            break
        else:
            die(f"could not open a handoff for {a.owner}; retry the command")
    print(f"opened handoff draft -> {target.name}")
    print(f"Fill it, then run `docket handoff {a.run} {a.owner} --submit` before replacement.")


def record_actual_model(path: Path, actual: str, effort: str) -> tuple[str, str, str, str]:
    meta, body = parse(path.read_text())
    requested = meta.get("requested_model", meta.get("model", ""))
    previous = meta.get("actual_model", "")
    history = [item.strip() for item in meta.get("model_history", "").split(" -> ") if item.strip()]
    if not history and requested:
        history.append(requested)
    if not history or history[-1] != actual:
        history.append(actual)
    meta["actual_model"] = actual
    meta["model_history"] = " -> ".join(history)
    requested_effort = meta.get("requested_effort", "")
    previous_effort = meta.get("actual_effort", "")
    if effort:
        effort_history = [
            item.strip() for item in meta.get("effort_history", "").split(" -> ") if item.strip()
        ]
        if not effort_history and requested_effort:
            effort_history.append(requested_effort)
        if not effort_history or effort_history[-1] != effort:
            effort_history.append(effort)
        meta["actual_effort"] = effort
        meta["effort_history"] = " -> ".join(effort_history)
    publish(path, render(meta, body))
    return requested, previous, requested_effort, previous_effort


def cmd_set_model(a: argparse.Namespace) -> None:
    """Record the model verified in the live harness; never infer it from launch args."""
    d = need_run(a.run)
    with owner_lock(d, a.owner):
        rep = latest(reports(d, a.owner))
        if not rep:
            die(f"no report for {a.owner} in {a.run}")
        rm, _ = parse(rep.read_text())
        if rm.get("status") not in ("draft", "blocked"):
            die(f"{rep.name} is already {rm.get('status')}; actual model must be recorded before submission")

        requested, previous, requested_effort, previous_effort = record_actual_model(
            rep, a.actual, a.effort
        )
        task = d / f"{a.owner}-task.mdx"
        if task.is_file():
            record_actual_model(task, a.actual, a.effort)

        if previous and previous != a.actual:
            print(f"model switched in existing session: {previous} -> {a.actual}")
        elif requested and requested != a.actual:
            print(f"WARNING: requested model {requested!r} is not active; harness is using {a.actual!r}")
        else:
            print(f"active model verified: {a.actual}")
        if a.effort:
            if previous_effort and previous_effort != a.effort:
                print(f"effort switched in existing session: {previous_effort} -> {a.effort}")
            elif requested_effort and requested_effort != a.effort:
                print(
                    f"WARNING: requested effort {requested_effort!r} is not active; "
                    f"harness is using {a.effort!r}"
                )
            else:
                print(f"active effort verified: {a.effort}")
        print(f"recorded in {rep.name}" + (f" and {task.name}" if task.is_file() else ""))
        dispatch = read_dispatch(d, a.owner)
        if dispatch and dispatch.get("state") == "dispatched":
            history = dispatch.get("model_history", [])
            history.append({"at": stamp(), "kind": "observed", "model": a.actual})
            dispatch["model_history"] = history
            dispatch["model_observed"] = a.actual
            publish_json(dispatch_path(d, a.owner), dispatch)


def print_unfinished_decisions(d: Path, candidates: list[str]) -> None:
    """Render every recoverable, currently unlocked decision transition."""
    unfinished = []
    for owner in dict.fromkeys(candidates):
        rnd, verdict, identity = unfinished_decision(d, owner)
        if verdict:
            unfinished.append((owner, rnd, verdict, identity))
    if not unfinished:
        return
    print("\nunfinished decision transitions - repeat the recorded verdict to finish one:")
    for owner, rnd, verdict, identity in unfinished:
        journal = read_transition(d, owner)
        completed = journal.get("completed") if journal.get("state") == "in-progress" else []
        applied = ", ".join(completed or []) or "nothing"
        flag = DECIDE_FLAGS.get(verdict, f"--{verdict}")
        command = f"docket decide {d.name} {owner} {flag}{decide_as(d)}"
        print(f"  {owner} round {rnd} {verdict} {identity} - applied {applied} "
              f"(repeat `{command}` to finish it)")


def cmd_status(a: argparse.Namespace) -> None:
    d = need_run(a.run)
    if a.role:
        require_preset_role(d, a.role)
    plan = d / "plan.mdx"
    pmeta = parse(plan.read_text())[0] if plan.is_file() else {}
    print(
        f"run {a.run}   plan: {pmeta.get('status', 'missing')}   "
        f"mode: {mode_of(d)}   "
        f"topology: {pmeta.get('topology', 'split')}   "
        f"updates: {pmeta.get('progress_updates', 'quiet')}   "
        f"evidence: {evidence_mode(d)}"
    )
    if a.role == "planner":
        rnd, st = state_of(d, "orch")
        if rnd:
            print(f"\norchestrator report: round {rnd}  {st}")
        else:
            print("\norchestrator report: not opened")
        print_unfinished_decisions(d, ["orch"])
        return
    rows = planned_tasks(d)
    rows.extend(o for o in owners(d) if o != "orch" and o not in rows)
    if not rows:
        print("\nno tasks planned yet")
        print_unfinished_decisions(d, ["orch"])
        return
    print(f"\n  {'owner':<8} {'round':>5}  {'status':<18} {'exec':<18} {'scope':<10} {'handoff':<10} complexity executor     verify")
    print(f"  {'-' * 8} {'-' * 5}  {'-' * 18} {'-' * 18} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 20}")
    for o in rows:
        rnd, st = state_of(d, o)
        task = d / f"{o}-task.mdx"
        tm = parse(task.read_text())[0] if task.is_file() else {}
        hn, hs = handoff_state(d, o)
        handoff = f"{hn}:{hs}" if hn else "-"
        exec_state, _ = execution_health(d, o)
        print(
            f"  {o:<8} {rnd:>5}  {st:<18} {exec_state:<18} {scope_state(d, o):<10} {handoff:<10} "
            f"{tm.get('complexity', tm.get('tier', '-')):<10} "
            f"{tm.get('executor', '-'):<12} {tm.get('verify', '-')}"
        )
    terminal = {"approved", "waived", "completed"}
    decided = [o for o in rows if state_of(d, o)[1] in terminal]
    approved = [o for o in rows if state_of(d, o)[1] in {"approved", "completed"}]
    waived = [o for o in rows if state_of(d, o)[1] == "waived"]
    print(f"\n{len(decided)}/{len(rows)} decided  ({len(approved)} approved/completed, {len(waived)} waived)")
    try:
        live = live_dispatches(d)
    except (OSError, ValueError):
        live = []
    try:
        cap = max_concurrency_of(d)
    except ValueError:
        cap = 0
    if cap:
        print(f"live execution capacity: {len(live)} live dispatch record(s) (cap {cap})")
    else:
        print(f"live execution capacity: {len(live)} live dispatch record(s)")
    consumed = [(o, read_deps(d, o)) for o in rows]
    consumed = [(o, entries) for o, entries in consumed if entries]
    # Readiness is checked for every owner, not only for those with a live record: a
    # round frozen against an input whose record has since been emptied is exactly as
    # stale as one whose input moved, and must not go quiet here.
    stale = [(o, withdrawn_readiness(d, o)) for o in rows]
    withdrawn = [(o, problems) for o, problems in stale if problems]
    if consumed or withdrawn:
        print("\nconsumed inputs - provisional readiness is never an approval:")
        for owner, entries in consumed:
            for entry in entries:
                print(
                    f"  {owner} consumes {entry.get('on')} ({entry.get('kind')}, "
                    f"{entry.get('state')}) at {entry.get('bundle')}"
                )
        for owner, problems in withdrawn:
            for problem in problems:
                print(f"  stale: {problem}")
        if withdrawn:
            print(
                "  review readiness withdrawn for " + ", ".join(o for o, _ in withdrawn)
                + ": open a changes round, re-record with `docket depend`, and resubmit"
            )
    agg = stale_aggregate(d)
    if agg:
        print("\naggregate bundle staleness:")
        for problem in agg:
            print(f"  stale: {problem}")
            # Extract the constituent that caused the staleness.
            m = re.search(r"pins (\w+) at", problem)
            if m:
                print(f"    {m.group(1)} was reopened or its evidence moved, invalidating the aggregate")
        print("  re-submit the orchestrator report to freeze a new aggregate bundle")
    reopened_owners = [
        o for o in rows
        if read_transition(d, o).get("verdict") == "reopen-waived"
    ]
    if reopened_owners:
        print(f"\nreopened from waiver: {', '.join(reopened_owners)}")
        print("  these preserve their prior round and waiver reason; an aggregate")
        print("  that pinned them is stale and must be resubmitted")
    batches = list_batches(d)
    if batches:
        print("\nreview batches (closed membership only; later tasks cannot join):")
        for batch in batches:
            ready, _ = batch_ready(d, batch) if batch.get("state") == "closed" else (False, "")
            print(f"  {batch.get('batch')}: {batch.get('state')} "
                  f"generation={batch.get('generation', 1)} "
                  f"members={','.join(str(m) for m in (batch.get('members') or []))} "
                  f"ready={'yes' if ready else 'no'}")
    open_incidents = [i for i in list_incidents(d) if i.get("state") == "open"]
    if open_incidents:
        print(f"\nopen stall incidents ({len(open_incidents)}; one incident yields one recovery event):")
        for incident in open_incidents:
            print(f"  {incident.get('id')}: {incident.get('cause')}")
    print_unfinished_decisions(d, [*rows, "orch"])


def cmd_diff(a: argparse.Namespace) -> None:
    """Show task-local changes, or say plainly that there is no diff evidence."""
    d = need_run(a.run)
    coverage, detail, changed, outside = assignment_evidence(d, a.target)
    if coverage == DOCUMENTS_ONLY:
        print(f"{a.target}: diff coverage unavailable - {detail}")
        print("Review the submitted documents. Docket is not claiming an unchanged worktree.")
        return
    if coverage == COVERAGE_UNAVAILABLE:
        print(f"{a.target}: diff coverage unavailable - {detail}", file=sys.stderr)
        print(
            "This is not an empty diff. Docket cannot show task-local changes, so do not\n"
            "review this task as unchanged. Restore the Git evidence, or declare\n"
            "`evidence_mode: documents-only` in plan.mdx for a run without it.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not changed:
        print(f"{a.target}: no worktree changes since its baseline ({detail})")
        return
    print(f"{a.target}: changes since its baseline ({detail})")
    outside_set = set(outside)
    for path in changed:
        marker = "OUTSIDE SCOPE" if path in outside_set else "in scope"
        print(f"  [{marker}] {path}")
    if outside:
        hint = generated_path_hint(outside)
        if hint:
            print(hint.strip())


def cmd_bundle(a: argparse.Namespace) -> None:
    """Show the frozen review evidence for a round, and whether it is intact."""
    d = need_run(a.run)
    entries = bundles_for(d, a.owner, a.round)
    if not entries:
        scope = f" round {a.round}" if a.round else ""
        print(f"{a.owner}{scope}: no frozen evidence bundle", file=sys.stderr)
        print(
            "A task round is frozen by `docket submit`. Nothing here is claiming an\n"
            "unchanged workspace or an empty patch.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if a.list:
        print(f"{a.owner}: {len(entries)} frozen bundle(s), oldest first")
        for entry in entries:
            problems = aggregate_bundle_problems(d, entry) if a.owner == "orch" else bundle_problems(d, a.owner, entry)
            state = "DAMAGED" if problems else "intact"
            print(
                f"  round {entry.get('round')}  {entry.get('digest')}  "
                f"{entry.get('trigger')}  {entry.get('frozen_at')}  {state}"
            )
        return
    entry = entries[-1]
    problems = aggregate_bundle_problems(d, entry) if a.owner == "orch" else bundle_problems(d, a.owner, entry)
    manifest = load_bundle(d, a.owner, entry)
    where = bundle_dir(d, a.owner, entry)
    kind = str((manifest or {}).get("kind", "task-round"))
    print(f"{a.owner} round {entry.get('round')} {kind} bundle {entry.get('digest')}")
    print(f"  frozen {entry.get('frozen_at')} by {entry.get('trigger')} -> {where.relative_to(root())}")
    if problems or manifest is None:
        for problem in problems or [f"{entry.get('digest')} is unreadable"]:
            print(f"  DAMAGED: {problem}", file=sys.stderr)
        raise SystemExit(1)
    contract = manifest["contract"]
    print(f"  contract {contract.get('source')} revision {contract.get('revision') or '(none)'}")
    baseline = manifest["baseline"]
    print(f"  baseline {baseline.get('snapshot')} revision {baseline.get('revision') or '(none)'}")
    for record in baseline.get("roots", []):
        print(f"    {record.get('alias')}  base tree {str(record.get('base_tree'))[:12]}")
    print(f"  report {manifest['report'].get('source')} body {manifest['report'].get('body_digest')}")
    verification = manifest["verification"]
    print(
        f"  verification {verification.get('status')} exit {verification.get('returncode')}"
        f"  {verification.get('command') or '(none registered)'}"
    )
    if kind == "aggregate":
        pinned = manifest.get("constituents")
        pinned = pinned if isinstance(pinned, list) else []
        print(f"  pins {len(pinned)} constituent task-round bundle(s):")
        for item in pinned:
            if isinstance(item, dict):
                print(
                    f"    {item.get('owner')} round {item.get('round')}  "
                    f"{item.get('bundle')}"
                )
    else:
        for section, label in (("delta", "correction delta"),):
            block = manifest.get(section, {})
            if block.get("coverage") != COVERAGE_AVAILABLE:
                continue
            for record in block.get("roots", []):
                print(
                    f"  {label} {record.get('alias')}  {record.get('file')}  "
                    f"{len(record.get('paths', []))} path(s)"
                )
    for section, label in (("patch", "patch"),):
        block = manifest.get(section, {})
        if block.get("coverage") != COVERAGE_AVAILABLE:
            print(f"  {label} coverage unavailable - {block.get('reason')}")
            if section == "patch":
                print("  This is not an empty patch.")
            continue
        for record in block.get("roots", []):
            print(
                f"  {label} {record.get('alias')}  {record.get('file')}  "
                f"{len(record.get('paths', []))} path(s)"
            )
    for record in manifest["source"].get("roots", []):
        print(
            f"  source {record.get('alias')}  tree {str(record.get('tree'))[:12]}"
            f"  head {str(record.get('head'))[:12] or '(unborn)'}"
        )
    for path in manifest["changed_paths"]:
        print(f"    {path}")


def cmd_depend(a: argparse.Namespace) -> None:
    """Record that one task consumes another task's frozen evidence, and on what terms."""
    d = need_run(a.run)
    entries = read_deps(d, a.owner)
    if not a.on:
        if not entries:
            print(f"{a.owner}: consumes no other task's evidence")
            return
        print(f"{a.owner}: consumed inputs")
        for entry in entries:
            print(
                f"  {entry.get('on')}  {entry.get('kind')}  {entry.get('state')}  "
                f"round {entry.get('round')}  {entry.get('bundle')}"
            )
        for problem in dependency_problems(d, a.owner):
            print(f"  stale: {problem}", file=sys.stderr)
        return
    if a.on == a.owner:
        die("a task cannot consume its own evidence")

    # Recording an input is part of doing the work, so it belongs to a draft round.
    # Refreshing a pin under a submitted, blocked, or decided round would replace the
    # inputs a captured verification described without any of it being run again.
    # The read-check-write below holds the owner lock, so two concurrent records
    # serialize instead of one silently dropping the other's pin.
    with owner_lock(d, a.owner):
        entries = read_deps(d, a.owner)
        rnd, state = state_of(d, a.owner)
        if state not in ("unassigned", "draft"):
            pinned = latest_bundle(d, a.owner, rnd)
            held = f" Round {rnd} stays frozen at {pinned.get('digest')}." if pinned else ""
            if state in ("submitted", "blocked"):
                die(
                    f"{a.owner} is {state}, so its round {rnd} evidence is already frozen against the "
                    f"inputs it recorded, and a reviewer may be reading it.{held} Nothing was "
                    f"re-recorded.\n    Open a fresh round first: `docket decide {a.run} {a.owner} "
                    f"--changes`, then `docket depend {a.run} {a.owner} --on {a.on}` and "
                    f"`docket submit {a.run} {a.owner}` so the new input is verified."
                )
            die(
                f"{a.owner} is {state}, so its round {rnd} evidence is decided and stays exactly as "
                f"it was recorded.{held} Nothing was re-recorded. A moved input leaves decided work "
                f"visibly stale in `docket status` rather than quietly refreshed; reopening it is "
                f"audited work this build does not do."
            )
        latest = latest_bundle(d, a.on)
        if not latest:
            die(
                f"{a.on} has frozen no evidence bundle, so there is nothing to consume. A task "
                "round is frozen by `docket submit`"
            )
        state = state_of(d, a.on)[1]
        final = state in FINAL_STATES
        unusable = consumable_problems(d, a.on, latest, final)
        if unusable:
            die(
                f"{a.on} cannot be consumed:\n      "
                + "\n      ".join(f"- {item}" for item in unusable)
                + f"\n    Consume {a.on} once it freezes a round whose verification passed."
            )
        policy = provisional_policy(d)
        if not final and policy != PROVISIONAL_ALLOWED:
            die(
                f"{a.on} is {state}, not approved, and this run declares provisional_integration: "
                f"{policy}. Declare `provisional_integration: allowed` in plan.mdx to consume "
                "verified-but-unapproved work deliberately, or wait for the reviewer"
            )
        entry = {
            "on": a.on,
            "kind": "final" if final else "provisional",
            "state": state,
            "round": str(latest.get("round", "")),
            "bundle": str(latest.get("digest", "")),
            "recorded_at": stamp(),
        }
        kept = [item for item in entries if item.get("on") != a.on]
        write_deps(d, a.owner, sorted([*kept, entry], key=lambda item: item.get("on", "")))
        print(f"{a.owner} consumes {a.on} round {entry['round']} at {entry['bundle']}")
    if final:
        print(f"{a.on} is {state}, so this input is final.")
    else:
        print(
            f"{a.on} is {state}. This is provisional integration: readiness, never approval.\n"
            f"If {a.on} freezes different evidence, {a.owner} must reverify against it and\n"
            f"record it again before {a.owner} can be submitted or approved."
        )


def cmd_roots(a: argparse.Namespace) -> None:
    """Show, or explicitly declare, the checkout roots baselines and scope paths use."""
    d = need_run(a.run)
    mode = evidence_mode(d)
    specs = list(a.declare or []) + list(a.redeclare or [])
    if mode != "git":
        print(f"{a.run}: evidence_mode: {mode} - Git baseline coverage is unavailable")
        print("No checkout roots are declared, and Docket is not claiming an unchanged tree.")
        return
    if a.declare is not None and read_roots(d)[0] == ROOTS_DECLARED and specs:
        die(
            "this run already declares its checkout roots; use --redeclare, which is "
            "refused once any baseline references the declaration"
        )
    if a.declare is not None or a.redeclare is not None:
        roots = declare_roots(d, specs, replace=a.redeclare is not None)
    else:
        state, roots, reason = read_roots(d)
        if state == ROOTS_INVALID:
            die(f"{reason}; repair or remove it deliberately - it is never rediscovered")
    if not roots:
        print(f"{a.run}: no checkout root is declared", file=sys.stderr)
        print(
            "Declare every checkout this run may change, explicitly, before dispatch:\n"
            f"  docket roots {a.run} --declare root=. lib=vendor/lib\n"
            "or declare `evidence_mode: documents-only` in plan.mdx for a run without Git\n"
            "evidence. Only a declared root is captured, so a linked worktree or a nested\n"
            "repository has to be named.",
            file=sys.stderr,
        )
        candidates = candidate_roots(root())
        if candidates:
            print("\nCheckouts you may want to declare (none of these are captured):",
                  file=sys.stderr)
            for candidate in candidates:
                print(f"  {candidate}", file=sys.stderr)
        raise SystemExit(1)
    print(f"{a.run}: {len(roots)} declared checkout root(s)")
    for record in roots:
        print(f"  {record.get('alias', '')}  {record.get('path', '')}")
        print(
            f"      branch {record.get('branch', '') or '-'}"
            f"  head {(record.get('head', '') or '(unborn)')[:12]}"
        )
        print(f"      git dir {record.get('git_dir', '')}")
        print(f"      common  {record.get('common_dir', '')}")
    shared = [
        record.get("common_dir", "") for record in roots
        if [r.get("common_dir", "") for r in roots].count(record.get("common_dir", "")) > 1
    ]
    if shared:
        print("\nSome roots share Git storage. They remain distinct change surfaces.")
    snap = snapshot_path(d, "run")
    print(f"\nrun baseline: {'captured' if snap.is_file() else 'not captured yet'}")


def cmd_preflight(a: argparse.Namespace) -> None:
    """Record the task gate's state before implementation without treating failure as success."""
    d = need_run(a.run)
    verify, task_timeout = task_verify(d, a.owner)
    if not verify:
        die(f"{a.owner} has no registered verify command")
    timeout = a.verify_timeout or task_timeout
    declared = task_env(d, a.owner)
    run_env, effective = verification_run_env(declared)
    slot = verify_slot_path(d, a.owner)
    holder = claim_verify_slot(slot, verify)
    try:
        source_owner = "run" if a.owner == "orch" else a.owner
        current_source, source_problem = source_identity(d, source_owner, "preflight")
        reusable = None
        if not source_problem:
            reusable = latest_reusable_verification(
                d, verify, declared, effective, current_source,
            )
        if reusable is not None:
            verification = reusable["verification"]
            if not isinstance(verification, dict):
                die("the reusable verification record is malformed")
            digest = str(reusable["entry"].get("digest", ""))
            data = {
                "command": verify,
                "returncode": 0,
                "fingerprint": verification["output_fingerprint"],
                "output_tail": list(verification.get("output_tail", [])),
                "status": "passed",
                "declared_env": declared,
                "effective_env": effective,
                "source": current_source,
                "reused": True,
                "reused_bundle": digest,
                "reused_owner": reusable["owner"],
            }
            target = baseline_path(d, a.owner)
            publish_json(target, data)
            print(
                f"baseline recorded: passing (reused bundle {digest} from "
                f"{reusable['owner']}; command not run) -> {target.relative_to(root())}"
            )
            return

        print(f"running baseline verify: {verify}")
        code, out_bytes, err_bytes = execute_verify(
            verify, timeout, run_env, holder=holder,
        )
        stdout, stderr = out_bytes.decode("utf-8", "replace"), err_bytes.decode("utf-8", "replace")
        data = {
            "command": verify,
            "returncode": "timeout" if code is None else code,
            "fingerprint": output_fingerprint(stdout, stderr),
            "output_tail": (stdout + stderr).strip().splitlines()[-30:],
            "status": "timeout" if code is None else ("passed" if code == 0 else "failed"),
            "declared_env": declared,
            "effective_env": effective,
            "source": current_source,
            "reused": False,
        }
        if code is None:
            outcome = f"timed out after {timeout}s"
        else:
            outcome = "passing" if code == 0 else f"failing (exit {code})"
        target = baseline_path(d, a.owner)
        publish_json(target, data)
        print(f"baseline recorded: {outcome} -> {target.relative_to(root())}")
    finally:
        holder.close()


def cmd_migrate(a: argparse.Namespace) -> None:
    """Migrate a run to five-role-v1 explicitly, preserving every artifact."""
    d = need_run(a.run)
    if a.to != WORKFLOW_FIVE_ROLE:
        die(f"only --to {WORKFLOW_FIVE_ROLE} is supported")
    plan = d / "plan.mdx"
    meta, body = parse(plan.read_text())
    if meta.get("workflow", WORKFLOW_LEGACY_DECODE) == WORKFLOW_FIVE_ROLE:
        with delivery_lock(d, "orchestrator"):
            sweep_role(d, "orchestrator")
        with delivery_lock(d, "planner"):
            sweep_role(d, "planner")
        print(f"run {a.run} is already {WORKFLOW_FIVE_ROLE}; event identities reconciled")
        return
    previous = meta.get("workflow", WORKFLOW_LEGACY_DECODE) or WORKFLOW_LEGACY_DECODE
    meta["workflow"] = WORKFLOW_FIVE_ROLE
    meta["migrated_from"] = previous
    meta["migrated_at"] = stamp()
    publish(plan, render(meta, body))
    fault("migrate:plan")
    with delivery_lock(d, "orchestrator"):
        sweep_role(d, "orchestrator")
    with delivery_lock(d, "planner"):
        sweep_role(d, "planner")
    print(f"migrated {a.run} from {previous} to {WORKFLOW_FIVE_ROLE}; "
          "legacy artifacts preserved and legacy event identities retired")
