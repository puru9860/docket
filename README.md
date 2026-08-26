# handoff

Run a **planner / orchestrator / implementor** pipeline across coding agents, coordinated
through numbered `.mdx` handoff files, with a mechanical report-validation gate and
zero-token wake signalling.

Three roles, three model tiers, one file protocol:

| Role | Model tier | Does |
| --- | --- | --- |
| **planner / reviewer** | biggest | writes the plan, does the final review |
| **orchestrator / implementor** | mid | splits the plan, delegates the simple half, implements the complex half, reviews everything the small models produce |
| **implementor** | small | implements exactly one task, writes an honest report |

Roles run as separate agents, on any harness, in any mix. They never talk directly.
They coordinate only through files, so no role needs to know what harness the others use.

## Why this instead of prompting one agent to "use subagents"

Three things make it hold up on real work:

- **`handoff submit` is a gate, not a formality.** It rejects a report with unfilled
  placeholders, empty required sections, unchecked acceptance criteria, or a failing
  `verify:` command. A rejected report is never handed to the reviewer, so a small model
  cannot report success it did not achieve.
- **`status: blocked` is a first-class good outcome.** A blocked report skips the
  acceptance requirement but *requires* a stated question. The honest path is the easy path.
- **Signalling costs nothing while idle.** A supervisor finishes its turn and goes idle.
  A watcher sleeps in the background and re-enters the session only when something is
  actionable, using the harness's own wake mechanism rather than a polling loop that
  burns a turn each time.

## Install

### Recommended: the `skills` CLI (handles 14 agents)

```bash
npx skills add <owner>/handoff --all
```

`--all` installs every skill in the repo to every detected agent. Scope it if you prefer:

```bash
npx skills add <owner>/handoff -g -a claude-code,codex,opencode
```

### Manual

Clone once into the shared cross-harness location:

```bash
git clone https://github.com/<owner>/handoff.git /tmp/handoff-src
mkdir -p ~/.agents/skills
cp -r /tmp/handoff-src/skills/handoff ~/.agents/skills/handoff
```

Then per harness:

**Codex** — nothing to configure. It scans `$HOME/.agents/skills` (user scope) and
`.agents/skills` walking up from the cwd (repo scope). Skills are enabled by default;
a `[[skills.config]]` entry in `~/.codex/config.toml` is only needed to *disable* one.

**OpenCode** — nothing to configure. It scans `~/.agents/skills/*/SKILL.md` globally and
`.agents/skills/<name>/SKILL.md` per project. Placement alone is enough.

**Claude Code** — does not read `~/.agents/skills`, so symlink it in:

```bash
mkdir -p ~/.claude/skills
ln -sfn ~/.agents/skills/handoff ~/.claude/skills/handoff
```

Any other harness: symlink into its skills directory the same way
(`~/.cursor/skills/`, `~/.config/crush/skills/`, `~/.copilot/skills/`, and so on).

### Put the CLI on PATH

```bash
ln -sfn ~/.agents/skills/handoff/bin/handoff ~/.local/bin/handoff
handoff help planner
```

`bin/handoff` is a single stdlib-only [uv](https://docs.astral.sh/uv/) script with no
dependencies. Replace the shebang with `#!/usr/bin/env python3` if you would rather not
use uv; nothing else changes.

## Quick start

```bash
cd <your project> && mkdir -p .handoff
handoff init R01          # scaffolds .handoff/runs/R01/plan.mdx
handoff help planner      # then follow the playbook
```

The playbooks are the source of truth and they live in the CLI, so an installed copy
cannot go stale:

```bash
handoff help planner
handoff help orchestrator
handoff help implementor
handoff help signalling
```

## Layout of a run

```
.handoff/runs/<run>/
  plan.mdx                 planner's output; tasks table with a tier per task
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

```
handoff init <run>                                    scaffold the run and plan.mdx
handoff assign <run> <owner> [--tier small|self]
    [--harness H] [--model M] [--files ...] [--verify CMD]
handoff submit <run> <owner>                          validate and hand off  <- the gate
handoff decide <run> <owner> --approve | --changes    verdict; --changes opens next round
handoff status <run>                                  every owner's round and state
handoff watch <run> --role orchestrator|planner       block until something needs you
handoff help <role>                                   the playbooks
```

`owner` is a task id such as `T03`, or `orch` for the orchestrator's own report.

Report and task schemas are built in. Override them per project by dropping a
`.handoff/templates/<report|task|plan|decision>.mdx`.

## Wake signalling (optional but recommended)

Without it, a supervisor blocks on a foreground call while a subordinate works, which
caps out around 8 minutes on some harnesses. With it, the supervisor idles for free and
is woken when a report lands.

On Claude Code, merge `hooks/settings.json.example` into the project's
`.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "\"$HOME\"/.agents/skills/handoff/hooks/wake.sh",
          "asyncRewake": true, "timeout": 28800 } ] }
    ]
  }
}
```

Arm and disarm it around a run:

```bash
echo "R01 orchestrator" > .handoff/watch.conf   # orchestrator waiting on implementors
echo "R01 planner"      > .handoff/watch.conf   # planner waiting on the orchestrator
rm .handoff/watch.conf                          # done
```

The watcher is inert unless `watch.conf` exists, so an idle project costs nothing. It must
run in the hook's own foreground process tree — never with shell `&` — so the harness can
tear it down with the session. See `handoff help signalling`.

Harnesses without an equivalent wake hook fall back to the blocking mode described in the
same playbook. The file protocol is identical either way.

## Terminal layout

Any multiplexer works, since the protocol is just files. If you use
[herdr](https://github.com/kunchenguid), the orchestrator playbook has ready-made
`tab create` / `agent start` / `agent prompt` commands and the pane-versus-tab guidance.

## License

MIT
