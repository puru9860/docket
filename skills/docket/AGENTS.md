# Docket

Agent instructions for the docket pipeline. Same content as the Claude Code
SKILL.md, for harnesses that read AGENTS.md (codex, opencode, cursor).

Three roles, three model tiers, one file protocol.

- **planner/reviewer** (biggest model) writes the plan and does the final review
- **orchestrator/implementor** (mid model) splits the plan, delegates the simple half,
  implements the complex half, and reviews everything the small models produce
- **implementor** (small model) implements exactly one task and writes an honest report

Roles run as separate agents, on any harness, in any mix. They never talk directly.
They coordinate only through files, so no role needs to know what harness the others use.

## Read your role playbook first

The playbooks are the source of truth and they live in the CLI, so an installed copy
of this file cannot go stale:

```bash
docket help planner        # or: orchestrator, implementor, signalling
```

Read `docket help signalling` before delegating anything - it explains how a
supervisor learns a subordinate finished without polling or burning tokens.

## The two rules that matter

**The report file is the truth.** Not an agent's chat message, not a terminal read,
not a lifecycle state. Terminal scrollback is lossy (agents run on the alternate
screen and rows that scroll off are gone), and a settled `idle` state does not mean
the work is correct. Read the `.mdx` file, and read the diff.

**`docket submit` is a gate, not a formality.** It rejects a report with unfilled
placeholders, empty required sections, unchecked acceptance criteria, or a failing
`verify:` command. A rejected report is never handed to the reviewer, so a small
model cannot report success it did not achieve. Rejections cost nothing but the
implementor's own time.

## Layout

```
.docket/runs/<run>/
  plan.mdx                 planner's output; tasks table with tier per task
  T03-task.mdx             assignment: goal, acceptance criteria, files, verify command
  T03-report-01.mdx        implementor fills this; round 1
  T03-decision-01.mdx      reviewer's verdict and required changes
  T03-report-02.mdx        opened automatically when changes are requested
  orch-report-01.mdx       orchestrator's own report, reviewed by the planner
  docs/                    supporting docs
  .woke                    wake ledger; delivers each event exactly once
```

Round numbers are per-owner, so `T03-decision-02.mdx` is unambiguously round 2 of T03.

## Commands

```bash
docket init <run>                                    scaffold the run and plan.mdx
docket assign <run> <owner> [--tier small|self] \
    [--harness H] [--model M] [--files ...] [--verify CMD]
docket submit <run> <owner>                          validate and hand off  <- the gate
docket decide <run> <owner> --approve | --changes    verdict; --changes opens next round
docket status <run>                                  every owner's round and state
docket watch <run> --role orchestrator|planner       block until something needs you
docket help <role>                                   the playbooks
```

`owner` is a task id such as `T03`, or `orch` for the orchestrator's own report.

Templates are built in. To customize a report schema for a project, drop an override
in `.docket/templates/<report|task|plan|decision>.mdx`.

## Install

`bin/docket` is a stdlib-only `uv run` script with no dependencies. Put it on PATH:

```bash
ln -s "$HOME"/.agents/skills/docket/bin/docket ~/.local/bin/docket
```

For wake signalling on Claude Code, merge `hooks/settings.json.example` into the
project's `.claude/settings.json`. See `docket help signalling`.
