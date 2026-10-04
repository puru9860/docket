# Planner

The planner owns the plan and its intent and constraint amendments. It does not implement code, verify evidence, or review task rounds: approval, waiver, reopen, and changes verdicts belong to the reviewer working from a fresh context.

## Choose the topology

- `split`: the planner and orchestrator are separate agents. Use this when the
  planner runs on an expensive Claude Code or Codex model and implementation and
  supervision can run more cheaply in OpenCode.
- `combined`: one agent performs planner and orchestrator duties. Only under
  the legacy workflow does that agent also review task rounds; `five-role-v1`
  keeps review with the independent reviewer.
  Implementors may still be separate agents. Use the orchestrator workflow after
  the plan is approved; no redundant planner handoff occurs.

Record the choice with `docket init <run> --topology split|combined`.

## Choose the preset

New runs start on the quick preset unless `--mode standard` selects the five-session one.
The standard preset keeps planner and orchestrator as separate sessions with an independent verifier behind every decision.
The new quick preset keeps a separate planner. Define the objective, constraints,
acceptance, tasks, and milestone membership before starting the implementor.
Assign every task with `--as planner`, then create each milestone with its full-suite
command, for example `docket batch R01 --create M1 --members T01,T02 --milestone
--command 'tests/test.sh' --as planner`, and close it with `--close M1 --as planner`.
The implementor coordinates dispatch and runs that declared full suite after each
milestone's implementation. It waits for the reviewer's approval before starting
the next milestone. Each role arms and watches its own Docket events after startup.
Only the reviewer approves those milestones. Existing `combined-checker` quick runs
retain their coordinator and checker roles.
A run that outgrows quick records an escalation request instead of migrating silently.

## Choose the review evidence

`docket init` records `evidence_mode: git` when the workspace is a checkout and
`evidence_mode: documents-only` when it is not; `--evidence-mode` overrides it.

A `git` run declares its checkout roots explicitly. Name each one as
`--root <alias>=<path>` at `docket init`, or with `docket roots <run> --declare`
before the first assignment. Omitting them declares only the enclosing checkout, so
a linked worktree, a nested repository, or a sibling checkout under a non-Git parent
has to be named or it is never captured. Create the checkouts the run needs before
declaring, because the declaration cannot be changed once a baseline points at it.
A `git` run that cannot capture a complete baseline refuses to dispatch rather than
recording a partial one. A documents-only run stays fully usable and labels its diff
coverage unavailable rather than pretending the tree is unchanged.

Either way, every submitted round is frozen into an immutable evidence bundle and
every verdict binds to it, so a review is always a review of something that cannot
change afterwards.

## Choose the integration policy

`plan.mdx` carries `provisional_integration: forbidden` by default: a task may only
consume another task's evidence once that task is approved. Declare
`provisional_integration: allowed` when the plan genuinely needs one task to build on
another's verified but unapproved work. Read that word literally: the policy admits only
a round whose frozen verification passed, so a blocked or skipped task is never a
dependency, however urgent the sequencing. It pins the exact evidence consumed and forces
reverification if it moves, withdrawing the consumer's review readiness until then. That
reverification costs a full correction round: the consumed pin is frozen into the round,
so restoring readiness means a changes verdict, a re-recorded input, and another
submission, not a quiet refresh. It never turns readiness into approval, and it never
removes the independent review of either task, so prefer sequencing the tasks when
sequencing is affordable.

## Plan

Fill `plan.mdx`, including objective, approach, risks, and every task. Complexity
and ownership are independent:

- `complexity=low` requires a narrow, mechanically checkable task with a runnable
  verification command.
- `complexity=high` requires a stronger model and deeper review.
- `executor=implementor` delegates the task regardless of complexity.
- `executor=orchestrator` keeps the task with the orchestrator.

Define functional intent and constraints, not detailed repository maps. The
implementor owns discovery and proposes its change surface after dispatch.
Choose a strong, lower-cost implementor model as the default for delegated
coding work, and keep free models as fallbacks only when their measured pass
rate justifies the extra correction time. Name the actual approved models in
the plan so dispatch and recovery follow the same policy.
For a budgeted run, set flat `premium_models`, `token_budget`, and
`review_reserve` fields in `plan.mdx`. New launches of those premium models
stop when measured run tokens reach the review reserve boundary; approved
non-premium fallbacks remain available. A missing transcript makes the guard
refuse the premium launch because it cannot measure the remaining allowance.
An account-wide Codex usage warning wakes the planner once per limit window;
review the reserve and model policy before authorizing another premium launch.
Prefer fewer, larger tasks that each deliver a checkable outcome. Avoid a chain
of tiny tasks that touch the same file: every task adds a dispatch, verifier
finding, and review boundary. Split work when the outcomes have independent
acceptance criteria, can proceed separately, or need a different owner.
Write at least one task-specific, checkable `--criterion` when assigning a
task. A standard tiered run then adds criteria for sibling sites, existing behavior, and side
effects. Use `--no-default-criteria` only when those three questions genuinely
do not apply.

Assign focused per-task verification: each task's `verify:` covers only the area
it touches, and that declared command is the implementor's whole test obligation.
The full suite never belongs in a task `verify:`; in new quick runs declare it
when creating the milestone and the implementor runs `docket batch <run> --verify <bid>`
when the batch is ready. In standard runs pass it with `--command CMD` at verification;
the suite also runs at the aggregate. A task that names `tests/test.sh` as its verify is
overscoped and must be narrowed.

Approve the plan only after the human agrees to it.

When an implementor proposes a contract change with `docket propose-amendment`,
read its need, conflicting constraints, evidence, alternative, and impact. Edit
the affected task contract if the new requirement is accepted, then record
`docket amendment <run> --accept <ID>`. To keep the existing contract, record
`docket amendment <run> --reject <ID> --reason TEXT`. This is the planner's
intent decision; leave implementation findings and task verdicts to their
assigned roles. The reviewer can cite the accepted ID with `decide --changes
--amendment <ID>` to open a fresh verification round without charging an
implementation correction.

## Split-mode information boundary

Hand the approved plan to the orchestrator. Then stop supervising task work.

The planner must not monitor implementor lifecycle, read `Txx-report` files,
receive task submission wakes, or relay per-task implementation progress to the
human. Those are orchestrator responsibilities. This boundary is intentional: it
protects the expensive planner's context and token budget.

For a new plan instruction that the orchestrator needs while it waits, record
`docket instruct <run> --role orchestrator --message TEXT`; the role's watcher
wakes immediately and the orchestrator resolves the instruction after handling
it. Use this for decisions about intent, not routine task progress.

Arm only the planner role and set `DOCKET_ROLE=planner` in its session:

```bash
docket arm <run> --role planner
docket status <run> --role planner
```

## Standard preset window layout

The standard preset builds two herdr windows once, at session setup.
Build the planner window first, from the planner pane, as a 1:1 vertical split with planner and reviewer.
Split the planner pane 1:1 to the right for the reviewer and keep the new pane id from `result.pane.pane_id`.
When `launch:reviewer` arrives, start the reviewer in that prepared pane.
Then create the tab that will hold the orchestrator window.

```bash
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=reviewer --no-focus   # JSON: result.pane.pane_id
herdr tab create --cwd "$PWD" --env DOCKET_ROLE=orchestrator --no-focus   # JSON: result.root_pane.pane_id
# On launch:reviewer, use the reviewer pane id saved above.
herdr agent start reviewer --kind opencode --pane <pane_id> -- --auto
herdr agent start orchestrator --kind codex --pane <root_pane_id> -- --yolo   # or the harness the plan names
```

Start the orchestrator in that tab's root pane.
The tab's `--env DOCKET_ROLE=orchestrator` is what lets the orchestrator's Stop hook fire; a pane without it cannot wait at zero cost.

The orchestrator builds its own window inside that tab: it splits the orchestrator pane 1:1 to the right for the implementor pane and down in half for the verifier, and starts the verifier there.
Only the planner starts the reviewer in standard. The launch event appears when review work
is pending and no reviewer harness session has joined this run.
Only the orchestrator starts the verifier and each implementor.

```bash
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --no-focus   # JSON: result.pane.pane_id
herdr pane split --current --direction down --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=verifier --no-focus   # JSON: result.pane.pane_id
herdr agent start verifier --kind opencode --pane <pane_id> -- --auto
```

In the new quick preset, start one implementor session. The implementor handles
the task and milestone commands and owns `launch:reviewer` when review work is
pending; the planner does not receive that launch event. The standard layout above
uses separate orchestrator and verifier sessions.

The only implementation artifact the planner consumes is the standardized
`orch-report-NN.mdx` in legacy split runs. In `five-role-v1` runs the aggregate
wakes the reviewer instead, and the planner receives intent and constraint
amendments only. In legacy split runs, read that report and the aggregate diff,
then approve or request changes; in `five-role-v1` runs cast no verdict, because
approval, waiver, reopen, and changes verdicts belong to the reviewer. Never
infer task progress from panes or agent lifecycle state.

In legacy split runs, if a decision is interrupted, repeat the verdict alone: Docket recorded your reason
and reviewer identity before writing anything, so the retry finishes that decision
with your wording intact, and `docket status` lists any transition still unfinished.
A retry that states anything the transition did not record is refused rather than
allowed to redefine the decision, including one that supplies a reason where the
interrupted attempt recorded none. If Docket says the report changed after review
began, re-read it and either ask for the reviewed body back or re-decide with
`--re-review`, which puts the new body back through the whole report gate and then
re-verifies it; a blocked aggregate report clears the blocked checks and skips
completion verification there, exactly as it does at submission.

## Combined topology (legacy and custom runs)

Continue with the orchestrator playbook in the same agent. Do not arm the planner
role. The final orchestrator report is still required as the durable summary, but
legacy submission completes it without waking or handing it back to the same
agent. Under `five-role-v1` the aggregate records `submitted` and becomes
terminal only through a reviewer decision.

Combining roles does not relax the token boundary. Do not narrate implementor
activity merely because the user-facing planner can now observe it. Follow the
orchestrator's quiet-supervision contract exactly.

## Prompt rendering

A planner prompt about the aggregate carries the plan objective, approach,
risks, and hard constraints with the plan revision, never task placeholders
such as a goal of `see task file`. Prompts render for a workflow, role, and
stage from the canonical contracts; the stage is derived from lifecycle
documents and an explicit flag is for inspection only. Mandatory contract and
acceptance material is never truncated to fit a budget. Optional guidance
lives under a token budget that covers the profile, every card, and their
headers and separators; zero selects nothing and an oversized first card is
skipped. Model matching is exact on the normalized identifier and one-run
model defaults are removed; an unknown model receives only task-relevant
cards. Literal commands, fences, and tables survive unchanged.
