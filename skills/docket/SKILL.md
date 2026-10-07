---
name: docket
description: Coordinate planned coding work or visible peer discussions across Claude Code, Codex, and OpenCode. Use for multi-agent implementation pipelines, brainstorming, feedback or another agent's view on the current topic, or when the user invokes /docket.
metadata:
  argument-hint: <run-id>, coding task, or discussion topic
---

# Docket

Docket coordinates coding work and peer discussions through files across Claude Code (`claude`), Codex (`codex`, temporary compatibility), and OpenCode (`opencode`).
For coding, quick is the default: a planner defines tasks and milestones, an implementor coordinates its work, and a reviewer acts once per milestone.
The standard preset (`--mode standard`) runs planner, orchestrator, implementor, verifier, and reviewer as separate sessions.
Both presets retain the gate, evidence, and decision binding. Old runs keep their recorded policy; new runs cannot select legacy.

## Start from a plain request

For brainstorming, feedback, or another view, read `docket help discussion` (`references/discussion.md`).
You remain the invoker in the current session and start one visible peer on the requested harness/model.
Use `docket discuss` to exchange replies, agree on a conclusion, pause on user interruption, and continue with their new direction. Do not create a coding run for discussion.

Users usually just say something like "act as planner and spawn an implementor for X".
Pick the preset from the whole request, not the word "planner" alone; this section is enough to start without `--help`.
Start a fresh planner session for each run, carrying only its approved objective and plan.
If `docket` is not on PATH, use `~/.agents/skills/docket/bin/docket`.

| The user asks for | Preset | You are | You start |
| --- | --- | --- | --- |
| a planner that spawns implementors, or names no roles | quick (default) | planner | one implementor, which starts the milestone reviewer on its launch event |
| a planner that spawns a separate orchestrator | standard preset, `--mode standard` | planner | one orchestrator (the planner starts the reviewer; the orchestrator starts the verifier and each implementor) |

If the user wants the planner on another harness or model, you are only its launcher: start that session in a pane split with `--env DOCKET_ROLE=planner`, send it the request unchanged, tell the user to continue in that pane, and end your turn.
Run no docket command and start no other role; after the handoff never sleep, check status, read panes, prompt a role, or post progress, and answer a progress question from one `docket status` before ending your turn again.

```bash
mkdir -p .docket
docket init R01 --title "Short name" --objective "What done means for the whole run." \
  --approach "The shape of the solution and why."
docket assign R01 T01 --harness opencode --model provider/model --file src/x.py --verify 'python3 -m unittest tests/test_x.py' --as planner \
  --title "Task name" --goal "What must be true when done." \
  --criterion "A mechanically checkable criterion" --criterion "Another one"
docket batch R01 --create M1 --members T01 --milestone --command 'tests/test.sh' --as planner
docket batch R01 --close M1 --as planner
```

In quick the planner defines all tasks and milestones, then starts the implementor,
which begins with `docket dispatch R01 T01 --session impl-1 --agent impl-1 --register`.
In quick, the implementor owns `launch:reviewer`. Start each role with `DOCKET_ROLE`, arm and watch each role, and wait for reviewer approval before the next milestone; see `docket help implementor`.
In standard the planner starts the orchestrator instead.
`--harness` is the worker's harness: the one the user named, otherwise `opencode`.
Put every constraint in the task before dispatching it: `--out-of-scope TEXT` and
`--decision TEXT` repeat like `--criterion`. Add `--depends-on T01` when a task needs
T01 approved first; assign every task up front, since each task's baseline waits for
its dispatch, and dispatch a dependent when its `dispatch-ready` wake arrives. Dispatch
the next task only once the work before it is done, so that work is its starting point. If a task changes
after dispatch, run the same `dispatch` again: it rebinds and prints the new prompt.
Never delete `.docket` or run files to start over.
The implementor's `dispatch` checks task intent and prints `prompt: <file>`.
In standard, hand the worker that file (herdr shown); the quick implementor reads it in its existing session:

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus   # JSON: result.pane.pane_id
herdr agent start impl-1 --kind opencode --pane <pane_id> -- --auto     # see harness flags below
herdr agent prompt impl-1 "$(cat <prompt file>)"                        # no --wait
docket set-model R01 T01 --actual <provider/model id the pane shows> [--effort LEVEL]
```

Harness flags after `--`: opencode `--auto --model provider/model`, codex
`--yolo -m MODEL`, claude `--dangerously-skip-permissions --model MODEL`.
herdr agent names are global across tabs, so prefix them with the run (`r01-impl-1`).
In standard, start each implementor in the layout's implementor pane instead of splitting a new pane: reuse its pane id with `herdr agent start <name> --kind opencode --pane <implementor-pane-id> -- ...`, one implementor at a time.
A new Codex worker can stop at a directory-trust screen and a "Hooks need review" screen: trust the project directory. The quick implementor also needs the Docket hook when it waits for milestone decisions.

Prepare supervisor panes at setup. Launch the checker, reviewer, or verifier
when its launch event says work is pending and no harness session has joined.
For a Claude Code or Codex session add `--env DOCKET_ROLE=<role>` to `pane split` so its wake hook fires. Send it this prompt, with the role filled in:

```text
You are the <role> for docket run R01. Run `docket help <role>` and follow it.
Arm your wake once with `docket arm R01 --role <role>`, then wait with
`docket watch R01 --role <role>` as `docket help signalling` describes for your
harness, act on each wake, and wait again.
```

Then wait as planner with `docket arm R01 --role planner` and `docket watch R01
--role planner`. Never wait with `herdr agent wait`, `--wait`, a short `--timeout`, or `sleep`:
they return on idle and timers, and every return is a full-context turn. After a native wake,
run `docket pickup R01 --role "$DOCKET_ROLE"`, read status once, act, and wait again. `docket help implementor` covers
coordination and milestones. When the run ends, record what docket
cost you: `docket feedback R01 --add --role planner --category <kind> --body TEXT`,
then `docket usage R01 --archive` keeps every role's transcript and token usage.
`assign --model` selects that model's learned profile; see `docket models` (README).

## Standard preset window layout

The standard preset builds two herdr windows once, at session setup.
The planner window is a 1:1 vertical split with planner and reviewer: the planner splits its pane 1:1 to the right, opens a new tab for the orchestrator window, and starts the reviewer in its prepared pane when `launch:reviewer` arrives. The new tab's root pane is the orchestrator's and needs `--env DOCKET_ROLE=orchestrator` for its wake hook to fire.
The orchestrator splits its pane 1:1 to the right for the implementor pane, which every dispatch reuses, and down in half for the verifier. It starts the verifier in that pane when `launch:verifier` arrives.

```bash
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=reviewer --no-focus   # JSON: result.pane.pane_id
herdr tab create --cwd "$PWD" --env DOCKET_ROLE=orchestrator --no-focus   # JSON: result.root_pane.pane_id
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --no-focus   # JSON: result.pane.pane_id
herdr pane split --current --direction down --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=verifier --no-focus   # JSON: result.pane.pane_id
herdr agent start reviewer --kind opencode --pane <pane_id> -- --auto
herdr agent start verifier --kind opencode --pane <pane_id> -- --auto
```

## Read your playbook

Read the playbook before your first wake with `docket help <role>`; prompts read the same canonical contract in `references/contracts/<role>.md`.
For an assigned task, `docket help <role> --run R01 --owner T01` renders compact guidance for its recorded policy and current stage through the prompt renderer.

| Role | Start here | Detail |
| --- | --- | --- |
| coordinator (old quick) | `docket help coordinator` | `references/coordinator.md` |
| checker (old quick) | `docket help checker` | `references/checker.md` |
| planner | `docket help planner` | `references/planner.md` |
| orchestrator | `docket help orchestrator` | `references/orchestrator.md` |
| implementor | `docket help implementor` | `references/implementor.md` |
| verifier | `docket help verifier` | `references/verifier.md` |
| reviewer | `docket help reviewer` | `references/reviewer.md` |

Shared detail lives in `references/`: `signalling.md` for waiting and wake delivery
per harness, `verification-obligations.md` for evidence duties, `contracts/` for the
canonical contracts, and `model-profiles/` for guidance cards.

## Request

$ARGUMENTS

If the request is non-empty, the user invoked `/docket`. Otherwise infer coding work or a peer discussion from the conversation.
Use `docket status` for coding state, or `docket discuss --list` for an existing discussion.
