---
name: docket
description: Coordinate planned coding work or visible peer discussions across Claude Code, Codex, and OpenCode. Use for multi-agent implementation pipelines, brainstorming, feedback or another agent's view on the current topic, or when the user invokes /docket.
metadata:
  argument-hint: <run-id>, coding task, or discussion topic
---

# Docket

Choose coding work or a peer discussion from the request.
For a discussion, read `docket help discussion`; remain the invoker and start one visible peer on the requested harness and model.
Use `docket discuss` for attributed replies, conclusions, and user interruptions; do not create a coding run for discussion.
Use `docket discuss --list` to find an existing discussion.

## Quick coding flow

New runs use quick: the planner defines intent, one visible implementor per milestone works its tasks and corrections, and an independent reviewer decides.
The planner plans, starts `docket conduct`, and ends its turn.
Start a fresh planner session for each run, carrying its approved objective and plan.
The conductor is a plain foreground process in its own Herdr pane, with no model tokens or hidden subagents.
It opens workers in a dedicated run tab, delivers work only to idle agents, and retains native session identities before closing panes.
Claude Code and Codex are first-class; OpenCode is an explicit opt-in for simple or personal work.
Use approved model identifiers from config, with a capable planner and reviewer and a lower-cost implementor appropriate to the risk.
Never silently weaken the reviewer or switch models to get past a usage limit.

If the user requests a different planner harness or model, launch that planner with their request unchanged and end your turn.
That session owns planning and starting the conductor; see `references/launch.md` for the manual launch fallback.
If `docket` is absent from PATH, use `~/.agents/skills/docket/bin/docket`.

```bash
mkdir -p .docket
docket init R01 --title "Capability" --objective "Observable outcome." --approach "Shape and reason."
docket assign R01 T01 --file src/x.py --verify 'python3 -m unittest tests.test_x' --as planner \
  --title "Task" --goal "Required behavior." --criterion "Observable task-specific result."
docket batch R01 --create M1 --members T01 --milestone --command 'tests/test.sh' --as planner
docket batch R01 --close M1 --as planner
```

Define every task and close every milestone before work starts, and obtain the user's approval of the plan.
Put constraints and dependencies in the task using `--out-of-scope`, `--decision`, and `--depends-on`.
Assignments resolve from user config, project config, and milestone overrides; inspect sources, allowed models, and pins with `docket config R01`.
Every milestone override needs `--assignment-reason`; reviewer `continue` or `fresh` needs its recorded continuity reason.
Choose `continue` for dependent milestones or deliberately deferred work and `fresh` for independent milestones.
Only user config may supply harness launch arguments; launch requests never prove the observed model.
Read `references/assignments.md` for configuration and `references/launch.md` for conductor setup and recovery.

Arm the planner's existing hook and run the conductor as a plain command in its own Herdr pane:

```bash
docket arm R01 --role planner
docket conduct R01
```

Then end your turn; do not launch workers, monitor panes, poll status, or relay healthy progress.
The conductor delivers worker wakes and starts a fresh implementor only after intact milestone approval.
Workers register themselves, pick up the announced obligation, and end their turns after submitting, verifying the milestone suite, or deciding.
Worker hooks stay silent; the planner keeps its own hook for intent decisions, budgets, stalls, and completion.
After a planner wake, run `docket pickup R01 --role planner`, inspect status once, and answer the decision it needs.
For a status request, read `docket status R01` once, answer, and end your turn.

## Recover and preserve evidence

```bash
docket status R01
docket reopen R01 --milestone M1 --role implementor
docket reopen R01 --milestone M1 --role reviewer
docket help planner
docket help implementor
docket help reviewer
```

Restart `docket conduct R01` after restoring Herdr; reopening resumes a saved native session and refuses when its identity is missing.
Stalls wake the planner through existing incidents; inspect the cause and resolve it with `docket health` after recovery.
Use `references/recovery.md` for interrupted work, changed inputs, and model policy; existing runs retain their recorded behavior.
Read `references/historical-runs.md` for an existing run's older policy.
Never delete run files, edit lifecycle status, commit or stash user work to clean a baseline, or recapture immutable evidence.
Submit completion or an honest blocker through the gate; missing evidence never counts as success, and `--skip-verify` cannot support approval.
The reviewer reads frozen patches and changed tests, challenges consequential assumptions and expectations, and binds decisions to exact evidence.
Use `references/evidence.md`, `references/verification-obligations.md`, and `references/signalling.md` for the detailed rules.
When complete, the planner reports the outcome, records feedback, archives with `docket usage R01 --archive`, and disarms the run.

## Request

$ARGUMENTS
