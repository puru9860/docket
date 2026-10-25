# Planner

Own the objective, plan, constraints, task assignments, and intent amendments.
Use a fresh planner session for each run, carrying the approved objective and plan.
The implementor owns discovery and implementation; the independent reviewer alone approves, waives, reopens, or requests changes.
Do not implement, impersonate another role, monitor routine task activity, or relay normal progress to the user.

## Plan the next capability

Quick uses planner, implementor, and reviewer sessions with `five-role-v1` and `quick-milestone-reviewer`.
Define all tasks, milestone membership, and full-suite commands before starting the implementor.
Prefer fewer, larger tasks when shared discovery or invariants make separate tasks wasteful.
Each milestone delivers one demonstrable capability, normally through 2-4 tasks.
Each task delivers one checkable behavior with meaningful tests within one working context.
Merge inseparable tasks; split when acceptance, ownership, or necessary working context differs.
Define functional intent and constraints rather than detailed repository maps.
State the failure model and excluded failure classes, including durability, interruption, rollback, and preservation where relevant.
Exclusions must qualify the promised behavior without contradicting it.

```bash
docket init R01 --title "Capability" --objective "What done means." --approach "Shape and reason."
docket assign R01 T01 --file src/x.py --verify 'python3 -m unittest tests.test_x' --as planner \
  --title "Task" --goal "Required behavior." --criterion "Observable task-specific result."
docket batch R01 --create M1 --members T01 --milestone --command 'tests/test.sh' --as planner \
  --implementor-harness claude --implementor-model MODEL --assignment-reason 'Capability risk and model cost.'
docket batch R01 --close M1 --as planner
```

Claude Code is the default worker harness; Codex is first-class and OpenCode is an explicit opt-in.
Choose a strong, lower-cost implementor model and name actual approved models in the plan.
Keep free models as fallbacks only when measured pass rates justify correction time.
Complexity and executor ownership are independent; high complexity needs a stronger model and deeper review.
New quick runs use `round_context: shared`: one implementor session works its tasks and corrections without subagents.
Existing runs retain their recorded context and authority; see [historical-runs.md](historical-runs.md).

## Record assignments and reviewer continuity

User defaults in `${XDG_CONFIG_HOME:-~/.config}/docket/config.json` resolve before `.docket/config.json`, then explicit milestone assignments.
`docket init` records configuration in the plan and batch creation records each milestone's resolved values; later config edits never change this run.
Inspect sources, allowed lists, and user pins with `docket config R01` or `docket config R01 --json`.
Use `--implementor-harness`, `--implementor-model`, and corresponding reviewer, planner, or test-author flags on batch creation.
Every planner override requires `--assignment-reason TEXT`, must fit the user's allowed models, and refuses a pinned role with its source file named.
All tasks in one quick milestone inherit its implementor assignment; omit task-level harness/model flags unless they agree with the milestone.
Before work starts, recover a conflict with `docket assign R01 T01 --clear-assignment --as planner`.
Set `--reviewer-context continue --continuity-reason TEXT` when a milestone depends on earlier work or carries deliberately deferred work.
Choose `fresh` for independent milestones and record why; the default is `continue` with its default rationale recorded.
This records continuity only; it launches no sessions.
[Assignment configuration](assignments.md) gives the flat JSON schema, constraints, and exact commands.

## Make acceptance observable

Write at least one task-specific, mechanically checkable `--criterion`.
Use `--out-of-scope TEXT`, `--decision TEXT`, and repeated `--criterion` to put every known constraint in the contract before dispatch.
Default criteria cover sibling sites, existing behavior, and side effects; use `--no-default-criteria` only when those questions do not apply.
If tests imitate a real tool, service, data set, or screen, name how the real boundary will be observed.
Put known facts and their limits in `--decision` so discovery does not repeat them.
Assign focused per-task verification covering the touched area.
The full suite belongs in the milestone command, never a task `verify:`.
The implementor's submission captures the focused command once against the final source.
The reviewer challenges the evidence and its expectations without routinely repeating that frozen suite.
Read [verification-obligations.md](verification-obligations.md) when defining evidence-sensitive criteria.

## Declare evidence and dependencies

`init` selects `evidence_mode: git` in a checkout and `documents-only` otherwise; `--evidence-mode` overrides it.
Declare each Git checkout with repeated `--root alias=path` at init, or before the first assignment:

```bash
docket roots R01 --declare app=./app lib=./lib
```

Omitting roots declares only the enclosing checkout; linked worktrees, nested repositories, and siblings need explicit names.
Create checkouts before declaration; aliases cannot change once a baseline exists.
An incomplete baseline refuses dispatch, and documents-only evidence labels diff coverage unavailable.
Never commit or stash user work to clean a baseline; never recapture one.
See [evidence.md](evidence.md) for bundle, scope, dependency, and report rules.

New plans record `milestone_dependencies: approval-v1`.
A dependency within one closed milestone can consume verified settled evidence; another milestone requires its intact approval.
Put approval-dependent tasks in different milestones or merge inseparable tasks to avoid deadlock.
Use `--depends-on T01` for declared dependencies.
Outside tiered settlement, `provisional_integration: forbidden` requires prior approval.
Allow provisional integration only deliberately: a passed current patch-bearing bundle is still required, pins are frozen, and moved inputs require a correction and reverification.
Dependency readiness never counts as final approval.

## Hand over the approved plan

Approve the plan only after the human agrees to it.
Follow [launch.md](launch.md) to run `docket conduct R01` as a plain foreground command in its own Herdr pane.
The conductor owns visible worker launches, milestone handoff, and direct worker wakes; it never types into your pane.
End your turn after starting it; the planner keeps its native hook for decisions, stalls, and completion.
Arm only the planner role from your own session:

```bash
docket arm R01 --role planner
docket status R01 --role planner
```

End your turn when the native wake hook is available; otherwise follow [signalling.md](signalling.md).
After a native wake, run `docket pickup R01 --role planner` before acting.
Use `docket instruct R01 --role implementor --message TEXT` for a new intent decision, not routine progress.
Do not read task reports or panes to track healthy work.

## Resolve an intent question or exhausted budget

Read an amendment's need, conflicting constraints, evidence, alternative, and acceptance/dependency impact.
If accepted, edit the affected contract and record the disposition; otherwise preserve the contract and explain why:

```bash
docket amendment R01 --accept ID
docket amendment R01 --reject ID --reason TEXT
```

The reviewer opens any required reverification round with the accepted amendment ID; it does not charge an implementation correction.
Intent ambiguity, contradictory requirements, or an objective the plan cannot meet belong here, not in an invented implementation requirement.
An exhausted correction budget wakes you; grant rounds only through `docket escalation` after judging the policy question.
A run that outgrows quick records `docket escalate-mode R01 --reason TEXT`; it does not silently migrate.
For interrupted work, stale evidence, model limits, or recovery commands, read [recovery.md](recovery.md).

For budgeted work set flat `premium_models`, `token_budget`, and `review_reserve` fields.
Premium launches refuse at the review reserve or when transcripts are unavailable; approved non-premium fallbacks remain available.
Review account usage warnings and model policy before authorizing another premium launch.
Account headroom is an observed provider snapshot; attributed run tokens are separate and cannot estimate quota.

## Finish

After all milestone approvals, handle the planner's run-complete event.
Record feedback, then archive with `docket usage R01 --archive`.
Use `docket feedback R01 --add --role planner --category other --body TEXT` or record `none`. [Prompt rendering](prompt-rendering.md) explains mandatory context and optional guidance; missing required artifacts refuse rather than produce placeholders.
