---
name: docket
description: Coordinate planned coding work through standardized task and aggregate reports, using Claude Code, Codex, or OpenCode in split or combined planner/orchestrator topologies. Use for multi-agent implementation pipelines or when the user invokes /docket.
metadata:
  argument-hint: <run-id> or the task to plan
---

# Docket

Docket coordinates planned coding work through files.
It supports only Claude Code (`claude`), Codex (`codex`, temporary compatibility),
and OpenCode (`opencode`).

Five logical roles: planner, orchestrator, implementor, verifier, reviewer.
They need not always be five agents: `split` keeps planner and orchestrator
separate, `combined` merges them, and `workflow: five-role-v1` (declared in
`plan.mdx`) selects separate reviewer registration, verifier findings, correction
budgets, and reviewer-owned approval.

Two presets select tested combinations of those dimensions.
The standard preset is the default for new runs: five sessions with an independent
verifier behind every decision.
The quick preset is three roles for small certain work: a coordinator combining
planning and orchestration, an implementor, and a checker combining verification
and review with `combined-checker` recorded on every artifact.
Quick never weakens the gate, the evidence, or the decision binding.
A run that outgrows quick records an escalation request instead of migrating silently.
Legacy is a historical decode for existing runs only: old plans keep their
meaning and migrate explicitly, while new runs use standard or quick and
`docket init --workflow legacy` refuses.

## Read your playbook first

Choose and read the relevant playbook completely before acting.
The one canonical contract per role lives in `references/contracts/<role>.md`;
both `docket help <role>` and the generated prompt read that same file.

```bash
docket help planner
docket help orchestrator
docket help implementor
docket help verifier
docket help reviewer
docket help signalling
```

| Role | Start here | Detail |
| --- | --- | --- |
| planner | `docket help planner` | `references/planner.md`, contract in `references/contracts/planner.md` |
| orchestrator | `docket help orchestrator` | `references/orchestrator.md`, contract in `references/contracts/orchestrator.md` |
| implementor | `docket help implementor` | `references/implementor.md`, contract in `references/contracts/implementor.md` |
| verifier | `docket help verifier` | `references/verifier.md`, contract in `references/contracts/verifier.md` |
| reviewer | `docket help reviewer` | `references/reviewer.md`, contract in `references/contracts/reviewer.md` |

Shared detail lives in `references/`: `signalling.md` for wake delivery,
`verification-obligations.md` for evidence duties, `contracts/` for the five
canonical contracts, and `model-profiles/` for guidance cards.
The full command surface is `docket --help`; per-role usage is in the playbooks.

## Install

### Recommended: the `skills` CLI (handles 14 agents)

```bash
npx skills add puru9860/docket --all
```

### Manual

```bash
git clone https://github.com/puru9860/docket.git /tmp/docket-src
mkdir -p ~/.agents/skills
cp -r /tmp/docket-src/skills/docket ~/.agents/skills/docket
```

**Claude Code** does not read `~/.agents/skills`, so symlink it in:

```bash
mkdir -p ~/.claude/skills
ln -sfn ~/.agents/skills/docket ~/.claude/skills/docket
```

### Put the CLI on PATH

```bash
ln -sfn ~/.agents/skills/docket/bin/docket ~/.local/bin/docket
docket help planner
```

`bin/docket` is a single stdlib-only script with no dependencies.

## Quick start

```bash
cd <your project> && mkdir -p .docket
docket init R01          # scaffolds .docket/runs/R01/plan.mdx
docket help planner      # then follow the playbook
```

## Request

$ARGUMENTS

If the request is non-empty, the user invoked `/docket`. Otherwise infer the run,
topology, and current role from the conversation and `docket status`.
