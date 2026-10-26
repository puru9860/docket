# Quick-only Docket with a conductor

Design agreed on 2026-10-25 and implemented in `16cd78d` and `5004803`.
It follows a small eval on three real tasks and two peer discussions.
The eval found that a strong implementor with a fresh cross-family reviewer matched or beat delegation to free models on quality, and that the approved defects that escaped were stated assumptions and claims evidenced below the boundary where they hold, not missing tests.

## Goals

Robust, bug-free code at low premium usage; wall time is acceptable.
Every working session is visible in herdr; nothing runs as a hidden subagent.
The planner spends tokens only on planning and on decisions that need it, and never implements.

## Flow

Quick is the only offered flow for new coding runs: planner, implementor, reviewer.
Default assignment: planner and implementor on Claude Code, reviewer and test author on Codex; models come from configuration, never hardcoded.
The quick reviewer carries the verification obligations formerly owned by a separate verifier, plus an assumption and inference audit: consequential assumptions are classified as supported (with a source), unresolved, or contradictory, and outcome claims are evidenced at their boundary (persistence read-back, effective deployment launch, process table).
`--mode standard` still creates a run for compatibility but warns that it is deprecated; its code stays until a historical support boundary is tested.

## Model and harness assignment

Roles: planner, implementor, reviewer, test author.
Resolution, later overriding earlier: built-in defaults, user config (`${XDG_CONFIG_HOME:-~/.config}/docket/config.json`), project config (`.docket/config.json`), then per-milestone flags on `docket batch --create`.
The user config may list allowed models per role and pin a role; project config can change harness and model but cannot widen an allowed list or remove a pin.
A planner override needs `--assignment-reason`, and a pinned role refuses it.
Resolution is recorded into the run at creation, so later config edits never change an existing run.
Every task in a quick milestone resolves to the same implementor.
Reviewer continuity is recorded per milestone: `continue` when a milestone depends on an earlier one or carries deliberately deferred work, `fresh` for independent milestones.
See `skills/docket/references/assignments.md`.

## Conductor

`docket conduct RUN` is a deterministic foreground loop in its own herdr pane that spends no model tokens.
The planner starts it after planning and ends its turn.

| Run state | Conductor action |
| --- | --- |
| Milestone ready to start | Open a pane in the run's worker tab for that milestone's implementor, pass trust screens, send the rendered prompt |
| Milestone review-ready | Start the reviewer, or reuse the live one when the milestone records `continue` |
| Changes requested | Prompt the same milestone implementor, which keeps its context |
| Milestone approved | Close the implementor pane after recording what reopening needs, then start the next milestone's implementor |
| Needs the planner (blocked, budget, intent, stall, complete) | Leave it to the planner's own wake hook; the conductor never types into the planner's pane |
| Idle with an unanswered obligation, unrecognized dialog, provider limit, or sustained herdr outage | Raise a stall incident so the planner is woken |

Worker wakes are `herdr agent prompt` deliveries made only while the agent is idle, recorded through the same announcement and receipt records as hook delivery, so an event is delivered once.
Launch is not proof of identity: a launched session registers itself, and observed models come from session telemetry, never from the launch request.
Launch arguments come only from the user config, so a repository cannot enable a permission bypass.
`docket reopen RUN --milestone M1 --role implementor|reviewer` resumes a recorded native session in a new pane.

## Status

| Step | State |
| --- | --- |
| 1. Quick-only surface, reviewer duties, `round_context: shared` default | Done |
| 2. Model and harness assignment | Done |
| 3. Conductor, reopen, stall detection | Done; verified in live runs with one and two milestones |
| 4. Friction from the eval and the live runs | Done |
| 5. Risk-triggered pre-dispatch oracle check, historical support boundary, removal of obsolete paths | Not started |
| 6. Premium-turn reduction | Done; one live rerun measured |

Planner-as-writer was considered and dropped: the planner never implements.

## Premium-turn reduction

In Claude Code every model turn rereads a prefix of at least 43k tokens, so premium cost follows the number of turns, not the size of each output.
The live runs showed most planner turns going to Docket mechanics rather than planning.
Changes:

- `docket plan apply RUN --file plan.json` builds the whole run from one JSON plan in a private staging directory and publishes it only when every existing command accepted it; errors name the plan path.
- `docket launch RUN` opens the conductor pane and arms the planner; `docket finish RUN --feedback TEXT|none` acknowledges completion, records feedback, archives usage, and disarms.
- Informational provider budget warnings appear in status and the conductor output instead of waking the planner.
- The conductor runs ready milestone suites without a model, and a refused suite becomes a planner stall instead of stopping the conductor.
- `docket bundle RUN OWNER --checkout [DIR]` and `--baseline` rebuild a frozen or baseline tree outside the checkout, linking only ignored dependency directories such as `.venv`; negative controls no longer touch the measured checkout.
- Submission records the model observed in the registered session's telemetry and accepts `--feedback`.
- Planner reading dropped from about 34k to 11k characters; continued worker sessions receive only changed prompt sections.
- The planner defaults to one milestone unless an intermediate approval is a real checkpoint.
- Fixed a race in which concurrent source captures shared one scratch index and refused an unchanged approval; refusals now name the moved member, root, or paths.

On a two-milestone eval task, the planner went from 25 turns to 13, and its premium tokens fell by about half.
