---
name: docket
description: Coordinate planned coding work through standardized task and aggregate reports, using Claude Code, Codex, or OpenCode in split or combined planner/orchestrator topologies. Use for multi-agent implementation pipelines or when the user invokes /docket.
metadata:
  argument-hint: <run-id> or the task to plan
---

# Docket

Docket minimizes expensive supervisor context by coordinating work through files.
It supports only Claude Code (`claude`), Codex (`codex`, temporary compatibility),
and OpenCode (`opencode`).

The logical roles are planner/reviewer, orchestrator, and implementor. They need
not always be three agents:

- `split`: planner and orchestrator are separate; the planner sees only the
  orchestrator's aggregate report.
- `combined`: one agent performs planner/reviewer and orchestrator duties; cheap
  implementors may remain separate.

Choose and read the relevant playbook completely before acting:

```bash
docket help planner
docket help orchestrator
docket help implementor
docket help signalling
```

The task file defines intent; the implementor-owned discovery capsule defines the
change surface and verification. The latest ready partial-work checkpoint and
task-local diff preserve continuity. The submitted report and task-local diff are
the review truth. Agent chat, terminal scrollback, and lifecycle state are not
completion evidence.

Repository understanding belongs in the cheap implementor harness. Supervisors
provide goals, acceptance criteria, constraints, and optional hints - not mandatory
code maps. The implementor discovers progressively, submits its capsule through
`docket scope`, and continues silently when Docket finds no collision. Only a real
scope collision wakes the orchestrator for sequencing or ownership.

In split topology, preserve the cost boundary: the planner must not monitor
implementors, read task reports, receive task-level wakes, or give the human
per-task implementation updates. The orchestrator handles all of that and submits
one standardized `orch-report-NN.mdx`.

`progress_updates: quiet` is the default in both topologies. After announcing a
dispatch, do not narrate healthy task activity such as coding, file edits, test
execution, lifecycle state, or “still working.” Wait without polling or producing
model turns. Speak again only for an actionable blocker/decision, a batched review
outcome, completion, or when the user explicitly asks for status.

```bash
docket init <run> --topology split|combined --harness claude|codex|opencode
docket assign <run> T03 --complexity low|high \
  --executor implementor|orchestrator --harness opencode \
  --model <requested-model> [--effort <requested-effort>]
docket validate-task <run> T03
docket scope <run> T03
docket scope <run> T03 --submit
docket set-model <run> T03 --actual <verified-active-model> [--effort <verified-effort>]
docket handoff <run> T03
docket handoff <run> T03 --submit
docket submit <run> T03 [--blocked]
docket preflight <run> T03
docket diff <run> T03
docket decide <run> T03 --approve | --changes | --waive --reason TEXT
docket status <run> [--role planner|orchestrator]
docket assign <run> orch --executor orchestrator --harness opencode
```

Templates may be overridden in
`.docket/templates/<plan|task|scope|report|handoff|orchestrator_report|decision>.mdx`.

For Claude Code wake signalling, merge `hooks/settings.json.example` into the
project's `.claude/settings.json`, set `DOCKET_ROLE`, and read
`docket help signalling`.

## Request

$ARGUMENTS

If the request is non-empty, the user invoked `/docket`. Otherwise infer the run,
topology, and current role from the conversation and `docket status`.
