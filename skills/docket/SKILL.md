---
name: docket
description: Coordinate planned coding work through standardized task and aggregate reports, using Claude Code, Codex, or OpenCode in split or combined planner/orchestrator topologies. Use for multi-agent implementation pipelines or when the user invokes /docket.
metadata:
  argument-hint: <run-id> or the task to plan
---

# Docket

Docket coordinates planned coding work through files, across Claude Code (`claude`),
Codex (`codex`, temporary compatibility), and OpenCode (`opencode`).
Five logical roles (planner, orchestrator, implementor, verifier, reviewer) run as
fewer sessions under a preset.
The quick preset is the default: a coordinator (planner plus orchestrator), one
implementor per task, and a checker (verifier plus reviewer), with `combined-checker`
recorded on every artifact.
The standard preset (`--mode standard`) runs all five as separate sessions with an
independent verifier behind every decision.
Neither weakens the gate, the evidence, or the decision binding; legacy is only a
historical decode for old runs, and `docket init --workflow legacy` refuses.

## Start from a plain request

Users usually just say something like "act as planner and spawn an implementor for X".
Pick the preset from the whole request, not the word "planner" alone, then run these
commands as written; none of them needs `--help`, and this section is enough to start.
If `docket` is not on PATH, use `~/.agents/skills/docket/bin/docket`.

| The user asks for | Preset | You are | You start |
| --- | --- | --- | --- |
| a planner that spawns implementors, or names no roles | quick (default) | coordinator (planner plus orchestrator) | an implementor per task, one checker |
| a planner that spawns a separate orchestrator | standard preset, `--mode standard` | planner | one orchestrator, which starts implementors, a verifier, and a reviewer |

```bash
mkdir -p .docket
docket init R01 --title "Short name" --objective "What done means for the whole run." \
  --approach "The shape of the solution and why."
docket assign R01 T01 --harness opencode --model provider/model --file src/x.py --verify 'tests/test.sh' \
  --title "Task name" --goal "What must be true when done." \
  --criterion "A mechanically checkable criterion" --criterion "Another one"
docket dispatch R01 T01 --session impl-1 --agent impl-1 --register
```

In quick you run all of these yourself. In standard you run only `init --mode standard`,
list the tasks in `plan.mdx`, and start the orchestrator (below), which runs the rest.
`--harness` is the worker's harness: the one the user named, otherwise `opencode`.
Put every constraint in the task before dispatching it: `--out-of-scope TEXT` and
`--decision TEXT` repeat like `--criterion`. Add `--depends-on T01` when a task needs
T01 approved first; assign every task up front, since a dependent's baseline waits for
its dispatch, and dispatch it when its `dispatch-ready` wake arrives. If a task changes
after dispatch, run the same `dispatch` again: it rebinds and prints the new prompt.
Never delete `.docket` or run files to start over.
Repeat `assign` and `dispatch` per task; `dispatch` runs the task-intent gate,
registers the worker session, and prints `prompt: <file>`, the exact bytes to send.
Then start the worker and hand it that file (herdr shown; any terminal works):

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus   # JSON: result.pane.pane_id
herdr agent start impl-1 --kind opencode --pane <pane_id> -- --auto     # see harness flags below
herdr agent prompt impl-1 "$(cat <prompt file>)"                        # no --wait
docket set-model R01 T01 --actual <model label the pane shows> [--effort LEVEL]
```

Harness flags after `--`: opencode `--auto --model provider/model`, codex
`--yolo -m MODEL`, claude `--dangerously-skip-permissions --model MODEL`.
herdr agent names are global across tabs, so prefix them with the run (`r01-impl-1`).

Start each supervising session you own the same way: the checker in quick, the
orchestrator in standard (which starts the verifier and reviewer itself). For a
Claude Code session add `--env DOCKET_ROLE=<role>` to `pane split` so its wake hook
fires. Send it this prompt, with the role filled in:

```text
You are the <role> for docket run R01. Run `docket help <role>` and follow it.
Wait with `docket watch R01 --role <role>` as `docket help signalling` describes
for your harness, act on each wake, and wait again.
```

Then wait yourself: `docket arm R01 --role coordinator` (or `planner`), and
`docket watch R01 --role coordinator` the way `docket help signalling` says for your
harness. Never wait with `herdr agent wait`, `--wait`, or a `sleep` loop: they return
on idle and timers, and every return is a full-context turn. When woken, run
`docket status R01` once, act, and wait again. `docket help coordinator` covers
corrections and the final aggregate report. When the run ends, record what docket
cost you: `docket feedback R01 --add --role coordinator --category <kind> --body TEXT`,
then `docket usage R01 --archive` keeps every role's transcript and token usage.

## Read your playbook

Read the playbook for the role you are playing before you act on your first wake;
`docket help <role>` prints it, and the generated prompts read the same canonical contract in
`references/contracts/<role>.md`.

| Role | Start here | Detail |
| --- | --- | --- |
| coordinator (quick) | `docket help coordinator` | `references/coordinator.md`; planner and orchestrator detail on demand |
| checker (quick) | `docket help checker` | `references/checker.md`; verifier and reviewer detail on demand |
| planner | `docket help planner` | `references/planner.md` |
| orchestrator | `docket help orchestrator` | `references/orchestrator.md` |
| implementor | `docket help implementor` | `references/implementor.md` |
| verifier | `docket help verifier` | `references/verifier.md` |
| reviewer | `docket help reviewer` | `references/reviewer.md` |

Shared detail lives in `references/`: `signalling.md` for waiting and wake delivery
per harness, `verification-obligations.md` for evidence duties, `contracts/` for the
canonical contracts, and `model-profiles/` for guidance cards.
Installation is in the repository README.

## Request

$ARGUMENTS

If the request is non-empty, the user invoked `/docket`. Otherwise infer the run,
topology, and current role from the conversation and `docket status`.
