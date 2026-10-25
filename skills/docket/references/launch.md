# Launch and wait for quick roles

## Conductor

After plan approval, task assignment, and closing every milestone, run the conductor as a plain foreground command in its own Herdr pane.
The planner arms its own hook and ends its turn; the conductor never types into that pane.

```bash
herdr pane split --current --direction down --cwd "$PWD" --no-focus
herdr pane run <returned-pane-id> 'docket conduct R01'
docket arm R01 --role planner
```

`docket conduct` requires `HERDR_ENV=1`, holds one process lock per run, and exits when the existing completion event confirms intact approval of every milestone.
It requires the recorded `round_context: shared` policy; historical subagent-policy runs keep manual launch.
It creates one dedicated worker tab per run, records its tab and anchor pane IDs, and splits workers inside that tab without shrinking the conductor pane.
It opens one implementor per milestone with that milestone's recorded harness and model, handles repository trust and Codex "Hooks need review" screens, and delivers the rendered task prompt.
Corrections return to that implementor; review and re-review use the milestone's recorded reviewer continuity.
A `continue` reviewer reuses only the preceding milestone's surviving continuation session with the same requested assignment.
A `fresh` milestone is a continuity boundary; an older reviewer is never reused across it.
If no reviewer remains alive, a new reviewer launches; a different requested assignment requires `fresh` with a reason.
After approval the implementor pane and a `fresh` reviewer's pane close once idle, with registration-derived native session identities retained when available.
A continued reviewer also closes when it is no longer the reuse candidate, and every worker closes at run completion.
If a superseded reviewer is still busy, cleanup waits until it becomes idle and remains pending across later milestones.
The conductor records pane IDs, agent names, requested assignments, launch times, and last deliveries under `.conductor/`.
These are transport records, never observed models or lifecycle authority.
Workers register themselves and acknowledge announcements with the pickup commands in their prompts.
They end each turn instead of starting a watcher; see [signalling.md](signalling.md).

Only user `${XDG_CONFIG_HOME:-~/.config}/docket/config.json` may provide harness arguments:

```json
{
  "claude_launch_args": ["--dangerously-skip-permissions"],
  "codex_launch_args": ["--yolo"]
}
```

These examples are explicit user opt-ins, never defaults or project options.
Without them, launch passes only `--model MODEL` for Claude/OpenCode or `-m MODEL` for Codex, omitting the flag for an empty model request.
Permission and unrecognized question dialogs remain visible and immediately open a planner-directed stall incident; repository trust and the known Codex hook-review screen are passed automatically.
Run `docket status R01` to see whether the conductor lock is held and each milestone's panes, agent names, and last delivery.
Restart `docket conduct R01` after interruption; it reconciles recorded launch intent against `herdr agent list` before launching or delivering again.
A vanished registered worker is preserved for recovery instead of silently replacing its context.
`--once` performs one successful reconciliation pass for diagnostics, and `--interval SECONDS` sets deterministic process polling without involving a model.
Transient Herdr errors retry with exponential backoff capped at 30 seconds.
A continuous outage lasting `--outage-seconds SECONDS` (default 180) raises one existing-format stall incident for the planner before stopping.
An idle pane with an undelivered or unanswered obligation for `--stall-seconds SECONDS` (default 300) also raises an incident; healthy busy work and a pane without obligations do not.
Provider usage-limit detection runs only for idle or blocked agents, including Herdr's idle-equivalent `done` state, and checks the final five lines of the harness detection snapshot without reading scrollback.
Earlier code output containing rate-limit phrases does not raise a provider incident, and the conductor never switches models automatically.
After restoring service, restart the conductor and resolve the incident with `docket health RUN --resolve-stall ID` once recovery is confirmed.

## Reopen a recorded worker

```bash
docket reopen R01 --milestone M1 --role implementor
docket reopen R01 --milestone M1 --role reviewer
```

Reopen resumes the saved native session in a new pane in the run's worker tab; it refuses if no unambiguous native identity was recorded.
Claude uses `--resume ID`, Codex uses `resume ID`, and the opt-in OpenCode adapter uses `--session ID`.
User-only launch arguments and current model policy still apply; neither a requested model nor an agent name is native session proof.
A live recorded session is already open, so retrying never creates a second writer.
The resumed worker receives a new transport session ID and registers itself; an active task binding moves only through the worker's ordinary `docket resume ... --register` command.
An explicitly reopened historical pane remains available during an active run and closes at final completion.
The command records launch intent before starting the harness, so a retry reconciles an interrupted launch.

## Manual fallback

The planner starts one implementor after the human approves the plan and all milestones are closed.
The implementor owns `launch:reviewer`; claim that event through the registered inbox before starting the reviewer when work is pending and no surviving reviewer session is noted.
A claim retry in the same session keeps its lease; another session cannot claim it while the lease holds.
Choose the harness and model the plan names, with Claude Code as the default worker harness and OpenCode only by explicit opt-in.
The commands below describe the existing launch path.

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus   # JSON: result.pane.pane_id
herdr agent start impl-1 --kind claude --pane <pane_id> -- --dangerously-skip-permissions     # see harness flags below
herdr agent prompt impl-1 "$(cat <prompt file>)"                        # no --wait
docket set-model R01 T01 --actual <provider/model id the pane shows> [--effort LEVEL]
```

Harness flags after `--`: opencode `--auto --model provider/model`, codex `--yolo -m MODEL`, claude `--dangerously-skip-permissions --model MODEL`.
herdr agent names are global across tabs, so prefix them with the run (`r01-impl-1`).

A new Codex worker can stop at a directory-trust screen and a "Hooks need review" screen: trust the project directory.
The quick implementor also needs the Docket hook when it waits for milestone decisions.

Prepare supervisor panes at setup.
Launch the reviewer when its launch event says work is pending and no harness session has joined.
For a Claude Code or Codex session add `--env DOCKET_ROLE=<role>` to `pane split` so its wake hook fires.
Send it this prompt, with the role filled in:

```text
You are the <role> for docket run R01. Run `docket help <role>` and follow it.
Arm your wake once with `docket arm R01 --role <role>`, then wait with
`docket watch R01 --role <role>` as `docket help signalling` describes for your
harness, act on each wake, and wait again.
```

Arm each waiting role in its own session and follow [signalling.md](signalling.md) for the harness wait method.
After a native wake, run `docket pickup R01 --role "$DOCKET_ROLE"`, read status once, act, and wait again.
Never use `herdr agent wait`, `--wait`, a short `--timeout`, or `sleep` to wait for Docket work.
Arm the planner before handover with `docket arm R01 --role planner`, then end its turn when the native hook is available.
Do not type another prompt into a waiting pane to advance a milestone; the approval wake is the handoff.
