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
The planner applies the plan, runs `docket launch`, and ends its turn.
Start a fresh planner session for each run, carrying its approved objective and plan.
The conductor is a plain foreground process in its own Herdr pane, with no model tokens or hidden subagents.
It opens workers in a dedicated run tab, delivers work only to idle agents, and retains native session identities before closing panes.
Claude Code and Codex are first-class; OpenCode is an explicit opt-in for simple or personal work.
Use approved model identifiers from config, with a capable planner and reviewer and a lower-cost implementor appropriate to the risk.
Never silently weaken the reviewer or switch models to get past a usage limit.

If the user requests a different planner harness or model, launch that planner with their request unchanged and end your turn.
That session owns planning and starting the conductor; see `references/launch.md` for the manual launch fallback.
If `docket` is absent from PATH, use `~/.agents/skills/docket/bin/docket`.

Read `docket help planner` for one complete JSON plan example and the normal two-command setup:

```bash
docket plan apply R01 --file plan.json
docket launch R01
```

Define every task and close every milestone before work starts, using one milestone unless an intermediate approval is a real checkpoint.
Assignments resolve from user config, project config, and explicit milestone overrides, respecting user pins and allow-lists.
The planner needs no transport internals for normal setup; `references/conductor.md` and `references/launch.md` are recovery references only.

Then end your turn; do not launch workers, monitor panes, poll status, or relay healthy progress.
The conductor delivers worker wakes and starts a fresh implementor only after intact milestone approval.
Workers register themselves, pick up the announced obligation, and end their turns after submitting or deciding.
Worker hooks stay silent; the planner keeps its own hook for intent decisions, budgets, stalls, and completion.
For a decision wake use `docket pickup R01 --role planner` and answer its question; completion carries its evidence and uses `docket finish` directly.
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
When complete, the planner reports the evidence carried by its wake and runs `docket finish R01 --feedback TEXT|none`.

## Request

$ARGUMENTS
