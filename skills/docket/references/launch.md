# Manual launch and fallback

For the normal setup, use `docket plan apply RUN --file plan.json` then `docket launch RUN`.
Read [conductor.md](conductor.md) for transport internals and recovery.

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
Prefer a working native hook; otherwise Claude Code uses one Bash `run_in_background` watch without a short `--timeout`.
Never use Codex background terminals or shell `&` or `nohup` in any harness, and never background a watcher inside a Stop hook.
Never use `herdr agent wait`, `--wait`, a short `--timeout`, or `sleep` to wait for Docket work.
Arm the planner before handover with `docket arm R01 --role planner`, then end its turn when the native hook is available.
Do not type another prompt into a waiting pane to advance a milestone; the approval wake is the handoff.
