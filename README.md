# docket

Run a **planner / orchestrator / implementor / verifier / reviewer** pipeline across
coding agents, coordinated through numbered `.mdx` docket files, with a mechanical
report-validation gate and zero-token wake signalling.

Five logical roles, one file protocol:

| Role | Model tier | Does |
| --- | --- | --- |
| **planner** | biggest | writes the plan, owns intent and constraint amendments |
| **orchestrator** | flexible | dispatches work, handles real blockers or scope collisions, routes rounds to review |
| **implementor** | small | implements exactly one task, writes an honest report |
| **verifier** | small | checks whether the frozen evidence proves acceptance, records pass, fail, or uncertain |
| **reviewer** | biggest | judges from a fresh context and alone approves, waives, or requests changes |

The planner and orchestrator may be separate (`split`) or one agent (`combined`).
Implementors can run cheaply in another harness. Docket supports Claude Code, Codex
(temporary compatibility), and OpenCode.

Two presets select tested combinations of mode, workflow, topology, and review policy.
The quick preset is the default for new runs: three roles, a coordinator combining planning
and orchestration, an implementor, and a checker combining verification and review.
The standard preset is five sessions (planner, orchestrator, implementor, verifier,
reviewer) with an independent verifier behind every decision; select it with `--mode standard`.
Quick records `combined-checker` on every artifact, so no reader mistakes a quick decision
for independently verified work.
Quick merges duties through explicit recorded policy only.
It never uses `--skip-verify`, a blanket `verifier_exempt`, or a legacy completion shortcut,
and it keeps scope claims, complete baselines, immutable evidence, source-drift rejection,
honest blocking, and decisions bound to exact bundle digests.
A run that outgrows quick records an escalation request; no command silently changes its mode.
The two-role quick variant is deferred and refuses explicitly.
Old runs keep their meaning as a historical legacy decode: a missing workflow
key still reads as legacy and an explicit legacy plan keeps its semantics.
New runs cannot select legacy: `docket init` uses standard or quick
(workflow five-role-v1) and refuses `--workflow legacy`.

## Why this instead of prompting one agent to "use subagents"

Three things make it hold up on real work:

- **`docket submit` is a gate, not a formality.** It rejects a report with unfilled
  placeholders, empty required sections, unchecked acceptance criteria, or a failing
  `verify:` command. A rejected report is never handed to the reviewer, so a small model
  cannot report success it did not achieve.
- **`status: blocked` is a first-class good outcome.** A blocked report skips the
  acceptance requirement but *requires* a stated question. The honest path is the easy path.
- **Lifecycle transitions survive interruption.** Every artifact is published
  through a temporary file and an atomic replace, and a decision transition holds an
  owner lock and records a durable identity together with the reviewer's own text.
  Retrying an interrupted approve, waive, or changes request finishes it from the
  bare verdict - the recorded reason and reviewer survive, and a retry that states
  anything different is refused, including one that fills in a reason the interrupted
  attempt recorded as absent - without overwriting review evidence or opening a
  second round. A verdict that would land on a report body edited after review is
  refused until that body passes the whole gate again and is re-verified, and an
  edited blocked report stays waivable because its re-review skips completion
  verification exactly as its submission did.
- **Signalling costs nothing while idle.** A supervisor finishes its turn and goes idle.
  A watcher sleeps in the background and re-enters the session only when something is
  actionable, using the harness's own wake mechanism rather than a polling loop that
  burns a turn each time.

## Requirements

Run `docket doctor` at any time to see what you have and what each missing piece costs.

**Required**

- Python 3.11+ (via [uv](https://docs.astral.sh/uv/), or swap the shebang for `python3`)
- git, if you want reviewers to read diffs. The validation gate works without it:
  a run without Git declares `evidence_mode: documents-only` and Docket then reports
  diff coverage as unavailable instead of as an unchanged worktree. A `git` run
  declares every checkout root it may change before dispatch, explicitly and by name -
  nested repositories and separate worktrees included - and captures a baseline it can
  reconstruct, without ever committing or stashing your work. A root that was not
  declared is never captured, and a run that cannot capture a complete baseline refuses
  to dispatch rather than recording a partial one. Every submitted round is then frozen
  as a full patch against that baseline, alongside the contract, the report body, the
  captured verification, and the resulting revision - still without writing anything
  into your checkout, not even a loose Git object.

**Optional, and what you lose without each**

| | Without it |
| --- | --- |
| A multiplexer for dispatch - [herdr](https://github.com/kunchenguid) recommended, tmux or zellij fine | Run each role in its own terminal window, or dispatch headless with `claude -p`. The file protocol is unchanged. |
| Claude Code, for the `asyncRewake` wake hook | Supervisors wait on a blocking `docket watch` instead of idling for free, and every return costs one full-context model turn; the Wake signalling section says how to keep that to one turn per event on each harness. Headless dispatch needs no watcher at all, since process exit is the signal. |
| Several agent CLIs (`claude`, `codex`, `opencode`) | Model tiering collapses. One agent can still play the roles sequentially; you keep the paper trail and gate but lose the cost saving. |

None of the optional pieces are enforced and nothing is blocked if they are absent.
`docket` is a file protocol plus a validation gate; dispatch and wake are conveniences
layered on top. If you install one thing, install herdr - it gives each agent a real
interactive pane, and driving an interactive session avoids the separate metering that
applies to headless Agent SDK use.

Note that herdr can only be *driven* from inside a herdr pane (`HERDR_ENV=1`). Having it
on PATH is not enough; `docket doctor` distinguishes the two, because that is the most
common way this trips people up.

## Install

### Recommended: the `skills` CLI (handles 14 agents)

```bash
npx skills add puru9860/docket --all
```

`--all` installs every skill in the repo to every detected agent. Scope it if you prefer:

```bash
npx skills add puru9860/docket -g -a claude-code,codex,opencode
```

### Manual

Clone once into the shared cross-harness location:

```bash
git clone https://github.com/puru9860/docket.git /tmp/docket-src
mkdir -p ~/.agents/skills
cp -r /tmp/docket-src/skills/docket ~/.agents/skills/docket
```

Then per harness:

**Codex** - nothing to configure. It scans `$HOME/.agents/skills` (user scope) and
`.agents/skills` walking up from the cwd (repo scope). Skills are enabled by default;
a `[[skills.config]]` entry in `~/.codex/config.toml` is only needed to *disable* one.

**OpenCode** - nothing to configure. It scans `~/.agents/skills/*/SKILL.md` globally and
`.agents/skills/<name>/SKILL.md` per project. Placement alone is enough.

**Claude Code** - does not read `~/.agents/skills`, so symlink it in:

```bash
mkdir -p ~/.claude/skills
ln -sfn ~/.agents/skills/docket ~/.claude/skills/docket
```

### Put the CLI on PATH

```bash
ln -sfn ~/.agents/skills/docket/bin/docket ~/.local/bin/docket
docket help planner
```

`bin/docket` is a single stdlib-only [uv](https://docs.astral.sh/uv/) script with no
dependencies. Replace the shebang with `#!/usr/bin/env python3` if you would rather not
use uv; nothing else changes.

## Quick start

```bash
cd <your project> && mkdir -p .docket
docket init R01 --title "Short name" --objective "What done means for the whole run."
docket assign R01 T01 --harness opencode --file src/x.py --verify 'tests/test.sh' \
  --title "Task name" --goal "What must be true when done." \
  --criterion "A mechanically checkable criterion" --criterion "Another one"
docket dispatch R01 T01 --session impl-1 --agent impl-1 --register
# dispatch prints `prompt: .docket/runs/R01/.prompts/<file>.txt`; start the worker in
# its harness and send it exactly that file, e.g. herdr agent prompt impl-1 "$(cat <file>)"
docket set-model R01 T01 --actual <model label the harness shows>
docket arm R01 --role coordinator      # then wait: docket help signalling, per harness
```

That is the whole start: a bare `docket init` is the quick preset, `assign` takes the
goal and criteria so nothing has to be opened and edited first, and `dispatch` runs the
task-intent gate, registers the worker session, and writes the exact prompt to send.
A checker session waits with `docket watch R01 --role checker` and verifies then reviews
each submission. Use `--mode standard` for five sessions with an independent verifier.

The role playbooks ship beside the skill and are surfaced by the CLI:

```bash
docket help planner
docket help orchestrator
docket help implementor
docket help verifier
docket help reviewer
docket help signalling
```

## Layout of a run

```
.docket/runs/<run>/
  plan.mdx                 planner's functional plan and task ownership
  T03-task.mdx             assignment intent, constraints, and optional hints
  T03-scope.mdx            implementor-owned discovery and claimed change surface
  T03-handoff-01.mdx       resumable partial-work checkpoint when replacement is needed
  T03-report-01.mdx        implementor fills this; round 1
  T03-decision-01.mdx      reviewer's verdict, reason, and submitted-evidence digest
  T03-report-02.mdx        opened automatically when changes are requested
  orch-report-01.mdx       orchestrator's own report, reviewed by the planner (legacy) or reviewer (five-role-v1)
  docs/                    supporting docs
  .snapshots/roots.json    the run's declared checkout roots
  .snapshots/run.json      run baseline, taken before the first implementor edit
  .snapshots/T03.json      T03's own baseline and accepted scope
  .snapshots/T03/          that baseline's patches and untracked content
  .bundles/T03/rounds.json every frozen round for T03, oldest first
  .bundles/T03/01/<addr>/  round 1 frozen whole, named by its own content digest
  .bundles/orch/rounds.json every frozen aggregate round, oldest first
  .bundles/orch/01/<addr>/ aggregate round 1, pinning constituent tasks
  .deps/T03.json           frozen evidence T03 consumed, and on what terms
  .transitions/T03.json    in-flight decision transition; a retry finishes it
  .woke-<role>             role-scoped wake ledger; delivers each event at-least-once
```

Round numbers are per-owner, so `T03-decision-02.mdx` is unambiguously round 2 of T03.

A bundle is immutable and named by its own `sha256`, so a later edit, a retry, a
re-review, or a later round can only ever produce a new one. It carries its own copy of
the baseline it was measured from, so tidying `.snapshots/` later cannot hollow out
evidence somebody already reviewed. `docket decide` refuses a verdict when the bundle for
that round is missing, damaged, or stale for the report body in front of it, which is
what stops an approval from quietly covering work nobody read. Damaged includes a bundle
whose pinned Git trees are gone: a round nobody can rebuild is not intact evidence, however
well the manifest hashes. Under `evidence_mode: documents-only` a round still freezes, and
records its patch coverage as unavailable rather than as an empty patch. The orchestrator's
aggregate bundle pins its constituent task bundles, and a reopened or moved constituent
makes the aggregate stale. A waived task may be reopened with
`docket decide <run> <owner> --reopen --reason ...`, preserving its prior round evidence
and invalidating any aggregate that pinned it.

One submission runs at a time per owner. The verify command, the freeze, and the report
write are a single locked step, and the contract, report, scope, and consumed inputs are
rechecked afterwards: if any of them moved while the command ran, the submission is
refused and your edit is left alone rather than being overwritten by a stale copy. What
gets frozen is the bytes that were verified, captured before the command ran, so nothing
that lands afterwards can slip into the bundle.

A round also freezes the inputs it consumed, including "none" as an explicit record, and
an approval or a waiver checks the live record against that frozen one. Re-recording a
pin is not a reverification, so `docket depend --on` only records into a draft round: for
submitted or blocked work it tells you to open a fresh round with `docket decide ...
--changes` first, and for decided work it refuses and leaves the round visibly stale in
`docket status` rather than quietly refreshing it.

## Commands

```
docket init <run> [--mode quick|standard] [--title T] [--objective TEXT] [--harness H]
                                  [--evidence-mode M] [--root alias=path ...]
docket assign <run> <owner> [--goal TEXT] [--criterion TEXT ...] [--file PATH ...] [--verify CMD]
                                  [--complexity low|high] [--executor implementor|orchestrator]
docket dispatch <run> <owner> --session S [--agent NAME] [--register]
                                                     bind one round; writes the prompt to send
docket validate-task <run> <owner>                   validate functional intent
docket scope <run> <owner> --submit                  claim implementor-discovered paths
docket handoff <run> <owner> [--submit]              partial-work checkpoint
docket set-model <run> <owner> --actual MODEL        record the verified live model
docket preflight <run> <owner>                       record the verification baseline
docket roots <run> [--declare|--redeclare alias=path ...]
                                                     the run's declared checkout roots
docket submit <run> <owner> [--as implementor]      validate, freeze, and hand off <- the gate
docket verify <run> <owner> --result pass --as verifier
docket bundle <run> <owner> [--round N] [--list]     the frozen evidence for a round
docket depend <run> <owner> [--on TASK]              consume another task's frozen evidence
docket decide <run> <owner> --approve|--changes|--waive [--reason TEXT] [--re-review] --as reviewer
                                                     repeat the bare verdict to finish an interrupted one
docket status <run> [--role planner|orchestrator|verifier|reviewer]
docket events <run> --role R --peek                  inspect events, consuming none
docket arm <run> --role R                            arm the watcher for a waiting role
docket disarm [<run>] [--role R]                      disarm
docket doctor                                        what dispatch and wake options you have
docket watch <run> --role coordinator|checker|orchestrator|planner|verifier|reviewer
                                                     block until something needs you
docket help <role>                                   the playbooks
```

`owner` is a task id such as `T03`, or `orch` for the orchestrator's own report.

Report and task schemas are built in. Override them per project by dropping a
`.docket/templates/<plan|task|scope|report|handoff|orchestrator_report|decision>.mdx`.

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
          "command": "\"$HOME\"/.agents/skills/docket/hooks/wake.sh",
          "asyncRewake": true, "timeout": 28800 } ] }
    ]
  }
}
```

Arm **every role that will wait**, then disarm when the run ends:

```bash
docket arm R01 --role coordinator    # quick: waiting on the checker and escalations
docket arm R01 --role checker        # quick: waiting on submitted rounds
docket arm R01 --role orchestrator   # waiting on implementors
docket arm R01 --role planner        # waiting on the orchestrator (split only)
docket arm R01 --role verifier       # waiting on submissions (five-role runs)
docket arm R01 --role reviewer       # waiting on milestone batches (five-role runs)
docket disarm R01                    # done
```

A role that is not armed is never woken, silently. `docket doctor` lists every armed
pair so you can check. Each role keeps its own ledger, so an orchestrator and a planner
watching the same run cannot consume each other's events, and each wake line is tagged
with the role it belongs to. Ordinary implementor submissions produce one wake only
when the current review batch is ready; blockers, collisions, and handoffs remain
immediate. Set `DOCKET_ROLE=<role>` in a session to be woken only for that role.

The watcher is inert unless `watch.conf` exists, so an idle project costs nothing. It must
run in the hook's own foreground process tree - never with shell `&` - so the harness can
tear it down with the session. See `docket help signalling`.

On Codex, merge `hooks/codex-hooks.json.example` into `~/.codex/hooks.json` and trust it once in `/hooks`.
It is a synchronous Stop hook: when a supervisor's turn ends, `docket watch` waits inside the hook at no model cost, and a wake is handed back as the next prompt.
Like the Claude hook it is inert unless `watch.conf` and `DOCKET_ROLE` exist, so a Codex implementor is unaffected.

Harnesses without an equivalent wake hook wait with one blocking `docket watch` instead.
Every return re-enters the model with its whole context, so the wait window is the cost lever.
On Codex, a blocking call is sliced into polls: set `background_terminal_max_timeout = 3600000` at the top level of `~/.codex/config.toml` so one poll can cover an hour rather than the default five minutes, and never wait in 30-second slices.
Wait on Docket events, not on `herdr agent wait` or a `sleep` loop, which return on idle transitions and timers.
`docket help signalling` has the table for each harness.
The file protocol is identical either way.

A delivered wake is not repeated while the event behind it is unchanged, so a supervisor that routed work to another role is left alone until that role's answer lands.

## Feedback loop

Every role records what docket itself cost it in each round - a missing instruction, a refusal it had to work around, a command it had to look up, waiting - or `none`, as the last step of its prompt.
Records also go to one user-level log, `~/.local/state/docket/feedback.jsonl`, across every project, next to machine observations docket records by itself: gate refusals, prompts rebound after a task changed, resumes, and exhausted correction budgets.
Review it periodically:

```bash
docket feedback --digest              # friction by role, category, and recurring item
docket feedback --digest --since 2026-10-01
```

Docket also notes each role's harness session (Claude Code, Codex, or OpenCode) whenever the role runs its own docket commands, so token usage per role and the full conversation behind any report can be reviewed later:

```bash
docket usage R01              # tokens per role and session, read from each harness transcript
docket usage R01 --archive    # also keep a copy of every transcript and log its usage
```

The final aggregate verdict archives automatically, into `~/.local/state/docket/sessions/` beside the log, with private permissions because transcripts can hold secrets.
The digest then reports token usage by role across runs.

### Per-model learning

Every time a task's work is rejected, docket records a case about the model that did it: a submit refused by the gate, a failed verification, requested changes (with the checker's numbered required changes), a block, or an escalation.
Verdicts are recorded too, so every rate has a denominator.
Review them periodically and turn what recurs into a short profile for that model:

```bash
docket models                              # scorecard per model: first-pass rate, rounds, rejects, tokens
docket models --review deepseek/deepseek-flash   # evidence packet: new cases since the last review
docket models --adopt profile.md           # install the reviewed profile for that model
docket models --import                     # once per project: backfill cases from existing runs
docket models --alias 'DeepSeek V4.1 Flash=deepseek/deepseek-flash'  # one model, two names
```

Hand the review packet to a strong session ("review this docket model packet and draft the profile"); it ends with the exact format and rules.
A rule is kept only when cases from at least two tasks support it, and raw cases never enter prompts.
An adopted profile lives in `~/.config/docket/model-profiles/` (`DOCKET_MODEL_PROFILES` moves it), survives skill updates, and is carried by every later prompt for a task assigned with that exact `--model`.
Prompts record the profile revision they carried, so the next review shows each model's first-pass rate per profile revision, and the feedback digest says when a model has enough new cases to review.

`DOCKET_FEEDBACK_LOG=path` moves the log and `DOCKET_FEEDBACK_LOG=off` disables it and the archive; `DOCKET_SESSION_CAPTURE=off` stops noting sessions; feedback never blocks a run.

## Terminal layout

Any multiplexer works, since the protocol is just files. If you use
[herdr](https://github.com/kunchenguid), the orchestrator playbook has ready-made
`pane split` / `agent start` / `agent prompt` commands, plus the split-direction and
zoom guidance for keeping 2-3 agent panes readable.

## Contributing / modifying docket

Read **[ARCHITECTURE.md](ARCHITECTURE.md)** for the data model, the lifecycle, why the
signalling works the way it does, the invariants, and the known gaps.
**[AGENTS.md](AGENTS.md)** is the entry point for an agent working on this repo.

```bash
tests/test.sh    # behavioral regression suite; must be 0 failures
```

## Acknowledgements

The signalling design here is not original. It is a deliberately small take on ideas
worked out in more depth elsewhere.

- **[firstmate](https://github.com/kunchenguid/firstmate)** by Kun Chen (MIT) - the
  wake mechanism comes from firstmate's event-driven, zero-token supervision: arm a
  watcher from a Claude Code `Stop` hook with `asyncRewake`, keep it in the hook's own
  **foreground** process tree instead of backgrounding it so the harness tears it down
  with the session, and keep a ledger so each event is delivered at-least-once. Its
  `docs/turnend-guard.md` and `.agents/skills/harness-adapters/SKILL.md` also map which
  harnesses can block on turn-end and which only allow a bounded follow-up, which is
  research this project simply relies on.

  No code is copied here. If you want the full-featured version of this idea - parallel
  crews, git worktree isolation, restart-proof reconciliation, many multiplexer backends,
  and a real test suite for all of it - use firstmate instead of this. `docket` covers a
  much narrower case: one repo, five roles, a numbered paper trail.

- **[lavish](https://github.com/kunchenguid/lavish-axi)** by Kun Chen (MIT) - the pattern
  of keeping the real instructions in the CLI (`docket help <role>`) and leaving `SKILL.md`
  as a thin pointer, so an installed copy cannot go stale.

- **[skills](https://github.com/vercel-labs/skills)** by Vercel Labs - the cross-harness
  installer that makes one skill directory work across a dozen agents.

The `plan.mdx` / `report-NN.mdx` / `decision-NN.mdx` convention predates all of this; it
started as a manual review workflow and this project just mechanised it.

## License

MIT
