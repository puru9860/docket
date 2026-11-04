# Recipe-only comparison protocol

This is the reproducible procedure for M3 in the pstack adoption plan.
It compares current Docket with the same Docket plus one explicitly selected verification recipe, on two matched task pairs.
Two pairs give a directional, opt-in-only finding.
No result from this procedure promotes recipes to a default.

Preparation does not run candidates and produces no experimental result.

## What is held equal

- Source: one pinned revision, sanitized identically for both arms of a pair.
- Docket CLI: the pinned revision's own `skills/docket`, copied beside each project; never the installed global copy. A copy alone is not provenance; see "CLI provenance".
- Contract: the same run objective, approach, task goal, criteria, files, out-of-scope notes and operational notes.
  Each contract also carries one mandatory existing decision naming that candidate's own absolute pinned launcher: "Run every Docket command through `<launcher>`, including the startup and obligation commands above that name docket without a path; never invoke docket by bare name."
  The wording never puts `docket` after a backtick, after ": " or at a line start, so the pinned renderer's bare-command rewrite leaves it verbatim; `prepare` asserts it appears verbatim in both the implementor and the reviewer contract.
  The two arms differ there only in that per-candidate path.
- Models: implementor Claude `claude-opus-5-5` and reviewer Codex `gpt-6.1-sol`, both at effort `high`, recorded as milestone assignments in the plan.
- Lifecycle: the normal quick milestone path driven by the pinned conductor with `round_context: shared`: one fresh visible implementor and one independent fresh visible reviewer per candidate, no subagents.
  The implementor submits through `submit`, the reviewer decides on the frozen bundle, and the milestone command runs `DOCKET_TEST_JOBS=8 tests/test.sh && (cd ui && npm test)`, the exported repository's own finish gate, which the conductor runs once per case.
  It runs as `env -u DOCKET_SESSION_CAPTURE PATH=<candidate>/tools/bin:<clean conductor PATH> /bin/sh -c '...'`: the suite gets session capture back and finds the candidate's own pinned `docket` first, as its tests expect, while the conductor itself keeps capture off and no `docket` on its PATH.
  Only the candidate path differs between arms.
  Before any candidate, this exact wrapped suite must pass once without models on a candidate-shaped sanitized `fe58b1a` tree, within the conductor's 900 s suite timeout and leaving the tree clean; that evidence lives in the private research notes, not here.

The only difference is the verification floor.
The control task's registered `verify` is `node --test ui/tests/projects.test.mjs`.
The treatment task selects `ui/verification/companion-projects/recipe.json` and adds the recipe helper to the same command.
M1 guidance and every other prompt change are excluded.

## Tasks and hidden checks

Each task seeds one companion project-persistence defect that the existing UI tests (`cd ui && npm test`) do not catch.
The sensitivity proof runs those UI tests on each seed; it does not run the broader CLI suite on the seeds, and nothing here claims it does.
The tasks were fixed before any candidate launch and are never swapped after outcomes are seen.

| Task | Recipe coverage | Seeded defect | Decisive hidden checks |
| --- | --- | --- | --- |
| `added-projects-lost` | Covered | The companion's stop-signal handler in `ui/server.mjs` cleans up "temporary" list files with a prefix match that also deletes the saved `projects.json` | Browser add, real SIGTERM restart, fresh browser context; API add across SIGINT and SIGTERM restarts; saved list written while running versus present after exit |
| `removal-persists` | Adjacent | Saving merges entries already on disk, so a removed project returns after restart | Browser remove, real restart, fresh browser context; API remove across two restarts |

Regression checks cover refusing a directory without `.docket`, removal persistence, re-adding after removal, and the non-removable starting project.
Every check starts the graded copy's real `ui/server.mjs` processes with private HOME and XDG state.
Browser checks use real Chromium through the copy's own Playwright.
Expected behavior comes from the task specification, not from the recipe or from observed output.
Each task also carries an alternative correct repair that is not a revert, so the checks test behavior rather than code identity.

Task definitions live in `tools/recipe-comparison/tasks/`, the checks in `tools/recipe-comparison/hidden/check.mjs`.
They never enter a candidate directory.

## Recipe coverage of these tasks

The companion recipe covers browser Add project followed by a restart from the same starting project.
The covered task's defect lies on that journey; the adjacent task's does not.
`prove` runs the unchanged pinned helper on each seeded source and records its actual result: exit, status, phase, error and passed checks.
The covered seed must fail for a non-prerequisite reason tied to the missing saved list, classified from that evidence as either `saved-list-not-written` (the helper's read of the saved file during browser add fails) or `saved-list-lost-after-restart` (that read passed and the restart read-back is missing).
No particular error code is required.
The adjacent seed must pass the helper, and that limitation is stated in the study and report.
The covered pair therefore measures detection and cost at the recipe boundary; the adjacent pair measures only operational guidance and preservation value, and an unfavorable result there does not disprove the recipe boundary.

## Blinding and its limits

Each candidate snapshot excludes the experiment documents named in `tools/recipe-comparison/design.json`: the adoption plan, the resume handoff, this protocol, and the comparison tools and tests.
The study records which exclusions were present, the pinned Git tree, and the sanitized digest.
The sanitized tree intentionally differs from the pinned Git tree; never report it as unchanged.
Remaining operational mentions, such as the recipe's own note that promotion needs a paired trial, are recorded as residual mentions and kept, because the recipe instructions and project tests are part of what candidates legitimately use.

Candidate directories are neutral labels under a neutral root, start sealed (mode 000), and contain no rubric, hidden check, mapping or private path.
The variant mapping lives only in the private `key.json`.
Candidates may still discover operational files naturally, including the recipe in the control arm.
Grading measures exposure from actual execution rather than assuming blindness.

## Procedure

Run from a checkout of this repository, with Node.js 22+, Python 3.11+, Git, and Playwright Chromium.
Choose two new directories outside the checkout: a private root and a candidate root whose path does not name the study.

```sh
tools/recipe-comparison/compare.py prove --source-repo "$PWD" --revision fe58b1a \
  --node-modules /abs/docket/ui/node_modules --out /abs/private-proof
tools/recipe-comparison/compare.py prepare --source-repo "$PWD" --revision fe58b1a \
  --node-modules /abs/docket/ui/node_modules --proof /abs/private-proof/proof.json \
  --private-root /abs/private --candidate-root /abs/work
```

`prove` must report no problems before any candidate runs.
It shows, at the pinned revision, that the reference source and an alternative correct fix pass every hidden check, that each seed fails at least one decisive check, that the existing UI tests pass on the seeded source, and the recipe helper outcomes described above.
It records its runtime: the `node_modules` digest, the Node version and the Playwright Chromium builds under `--browsers-path`.
Raw hidden-check and helper outputs stay in the proof directory.
`prepare` re-validates every expectation itself and refuses a proof that fails one, was made from different tool inputs or another runtime, or describes a different revision, sanitization or task set; it freezes the summary into `study.json`.

`prepare` exports the pinned revision without touching the source checkout, copies only the named `ui/node_modules`, and creates a one-commit Git repository per candidate with a fixed author and date.
It then applies the generated plan with the pinned CLI (`plan apply`), which captures the run baseline, renders the initial implementor prompt (`prompt`) as the frozen equality and renderer record, and computes the conductor's own worker names for both roles with the pinned `agent_name`.
That prompt is never handed to a role.
No planner model turn occurs, and `DOCKET_SESSION_CAPTURE=off` keeps preparation from being noted as a role session.
Both arms of a task must have the identical commit and CLI digest, or preparation fails.
Outputs in the private root: `study.json` (frozen tool hashes, source and runtime records, candidates without arms, prepared prompt records and worker names), `key.json`, `schedule.md`, and `launch/<label>/` with each plan, prompt and file manifest.
Task notes tell candidates the npm dependencies are already installed and must not be reinstalled, and give the manual start command a private `--socket` so a manual check never reaches a live Herdr server.

## CLI provenance

The operator never relies on `PATH`: the user's shell profile puts `~/.local/bin` first, and Codex tools run under a shared app-server whose environment no pane controls.
Instead each contract's existing decision names the candidate's absolute pinned launcher for every Docket command, the initial prompt is rendered with no `docket` on the render `PATH` so its commands are absolute too, and every operator command in `schedule.md` is absolute with `DOCKET_SESSION_CAPTURE=off`.
The core conductor, `~/.local/bin/docket`, user configuration and permission or sandbox flags are not changed for this experiment.

Grading decides provenance from actual execution.
Runtime renderer revisions come only from the run's `.prompts` directory: dispatch and resume prompt files, and any `prompt` record JSON (`renderer_revision`) that was not present at preparation.
Execution evidence comes only from the explicitly supplied transcripts, and only from records of execution.
- Claude: the `command` input of a `Bash` tool call, which Claude Code runs as written; a command-carrying call to any other tool is opaque.
- Codex: the harness's own `CommandExecution` records (`event_msg` `item_completed`), whose `command` argv and `status` say what actually ran, and for older formats `function_call` exec arguments and `local_shell_call` actions.
  The model's freeform `exec` JavaScript (`custom_tool_call`) is never read for commands and never evaluated.
  It is only checked for coverage: every native record must start inside exactly one freeform call's window (call record to its output, or to the next call when a turn aborted), every call that names `tools.exec_command` must have at least one such record, and a call may name tools only literally from a known set (`exec_command`, `write_stdin`, `apply_patch`, `view_image`, `web__run`, `clock__*`).
  An alias, a computed or interpolated tool reference, another tool name, an uncovered call, or a record in two windows is opaque.
  A native record outside every window, such as a command the user typed into the pane, is still classified from its real command.
- Stdin a model writes to a running process is not execution; starting an interactive shell or interpreter that could read commands from stdin is ambiguous.

Each executed script is classified by a narrow recognizer, not a shell parser.
It tokenizes twice, as written and with quoted strings and escapes masked, so a quoted `&&` is never an operator.
A Docket execution counts as pinned or foreign only at a command position that certainly runs: the first command, or one reached only through `cd DIR &&`, after assignments and the supported `env` options (`-i`, `-u NAME`).
A Docket path or name anywhere else is ambiguous: a later or conditional command, a control structure, a substitution, a here-document, an interpreter, an unknown command's arguments, an unsupported `env` option, or syntax the recognizer cannot tokenize.
Data commands such as `cat`, `printf` or `grep` may name the launcher without executing it, and tool outputs and quoted documentation are counted as mentions only.
Any bare `docket <subcommand>` execution, for every subcommand of the pinned parser, is counted and leaves provenance `unknown`: neither a pane `PATH` attestation nor an earlier `readlink` shows what a later bare command resolved.
- `foreign` when a runtime renderer revision differs from the pinned one, or a certainly executed command runs a Docket path other than the pinned launcher.
- `pinned` only when runtime renderer revisions exist, all match and all were readable, every transcript parsed, no call is opaque, no Docket command is ambiguous or bare, and at least one pinned-launcher execution occurred.
- `unknown` otherwise.
Anything but `pinned` makes the report inconclusive.
The conductor's own startup register, pickup and obligation commands are built outside the contract and stay bare at the pinned revision; only the decision tells roles to run them through the launcher, so the first live case must show compliance in actual tool calls.
Reads of the installed skill's documents are counted and reported separately.

## Running candidates

Follow `schedule.md`, one label at a time, in its counterbalanced order: the second pair reverses the first pair's arm order.
For each label:
1. Unseal it.
2. Prestart both roles under the conductor's exact worker names, which `prepare` computes with the pinned `agent_name` from the resolved run path, so the conductor adopts them instead of starting its own.
   Each role gets its own Herdr pane, shared with nothing else, with cwd in the project and the environment the conductor would set: `DOCKET_RUN`, `DOCKET_ROLE`, and `DOCKET_SESSION` equal to that exact worker name.
   Then `herdr agent start NAME --kind HARNESS --pane PANE -- ARGS`: Herdr runs the operator's existing harness alias, and `ARGS` carry only the model and effort (`--model claude-opus-5-5 --effort high` for Claude, `-m gpt-6.1-sol -c model_reasoning_effort=high` for Codex), never approval or sandbox flags.
   Before the conductor starts, `herdr agent list` must show both exact names, each with `agent` equal to its harness, idle at the prompt; an unnamed or misnamed agent is not adopted, and the conductor would start a second session for that role.
3. Start the pinned conductor in the foreground in its own pane: `env PATH=CLEAN DOCKET_SESSION_CAPTURE=off /abs/pinned/launcher conduct RUN`.
   `CLEAN` is the `conductor_path` that `prepare` records: a neutral `tools-bin` directory under the candidate root holding only `herdr` and `uv` links, the real Node directory with `npm`, `/usr/bin` and `/bin`.
   `prepare` refuses one that would resolve any `docket`, so the conductor renders every contract command with the absolute launcher, and the milestone suite it runs still finds Node, npm, `uv` and the system `python3`.
   Never run `launch`, and never hand a prompt to a role: the conductor delivers every obligation, including the first.
4. Let the normal lifecycle reach an approval or an honest block, then end both role sessions within 900 s of the final decision so their transcripts stay inside the run's recorded end.

A setup intervention is a trust prompt or hook-review keypress before the conductor's first delivery to a role.
A human intervention is any text typed into a role pane outside the conductor after that.
Both counts are typed by an operator who holds the mapping, so they are reported and never scored.
Afterwards, save `DOCKET_SESSION_CAPTURE=off /abs/pinned/launcher usage RUN --json` and write an observations file:

```json
{"setup_interventions": 1, "human_interventions": 0, "process_overhead": "text",
 "operational_saving": "text, listed but never scored"}
```

Routine model turns are not typed: grade counts the conductor's successful deliveries (`delivered` records in `.delivery/<role>/log.jsonl`), and a missing log leaves turns unknown.
Missing fields stay unknown.

## Execution-cost measurement

`docket usage` starts each implementor and reviewer window at the role's first in-harness Docket registration (`session_since` returns `first_seen`), so calls before that registration are deliberately excluded.
Its totals are therefore a post-registration lower bound, not the full execution cost of a fresh candidate, and the report labels them that way.
This experiment does not change that attribution.

The cost rule uses whole fresh role sessions instead, passed to `grade` as `--full-session ROLE=HARNESS:/abs/transcript`.
New quick runs at the pinned revision record `round_context: shared`, so each candidate has one fresh visible implementor session and one independent fresh visible reviewer session, and this trial uses no subagents.
If subagent transcripts are nevertheless found beside a Claude session, they are counted so cost is not understated, and reported as a protocol deviation that makes the report inconclusive.
They are parsed by the pinned copy's own Docket parsers with no time window, so categories match `docket usage` exactly.
A role's measurement counts only when all of these hold:
- the transcript is the single session the run ledger noted for that role (same harness, session id in its path);
- its first activity lies after preparation and no later than the role's registration, so pre-registration calls are included and no earlier work is;
- its last activity is within the run's recorded end (an open run cannot bound it);
- every working directory the transcript records is inside the candidate project;
- the parser reports measured telemetry.
Otherwise full-session tokens are unknown, the cost rule is inconclusive, and no zero or attributed figure stands in for them.
Raw transcript copies and their digests stay in the private grading directory.
Run each role in a fresh session dedicated to one candidate, and end it when the run completes.

## Grading and adjudication

```sh
tools/recipe-comparison/compare.py grade --private-root /abs/private --candidate-root /abs/work \
  --label LABEL --usage /abs/usage.json --observations /abs/obs.json \
  --full-session implementor=claude:/abs/impl.jsonl --full-session reviewer=codex:/abs/rollout.jsonl
```

Grade copies the candidate's source and `.docket` state into the private root, reseals the candidate, and never writes to the candidate's checkout.
For an intact approval it materializes the approved round's frozen tree with the pinned `bundle RUN T01 --round N --checkout DIR`, run against a private copy of the run state so the export's private index and objects never land in the candidate.
The hidden checks, the candidate's own UI tests and the pinned UI tests run on that approved tree; the live tree is only compared with it, and the drift is reported.
Without an intact approval the live tree is graded for the blind packet only, and the pair is inconclusive anyway.
A hidden-check result other than `passed` or `failed`, such as a browser that would not start, is an infrastructure result: the pair's regression stays unknown and grade says to rerun once the runtime works.
Grade also records whether the candidate's `node_modules` and the grading runtime still match the proof's; either change makes the report inconclusive.
Before resealing, it checks that the pinned CLI copy still matches its prepared digest, then runs that copy's own readers against the original candidate: `state_of`, `current_bundle` (with `bundle_problems`, which re-hashes every artifact and checks pinned trees), `run_complete_event` (the gate `docket finish` uses for milestone approval bindings), and `recipe_approval_problem`.
An approval is `intact` only when those readers report an approved current round whose decision binds the current bundle digest in a complete run; a reported problem is `damaged`; a changed CLI copy or a failing reader leaves integrity `unknown`, and nothing is executed from a changed copy.
On top of an intact approval, the frozen recipe `result.json` must match its `sha256:` artifact digest and record a passing, non-negative-control run whose recipe files the patch did not edit; Docket's own gate only requires the evidence to exist.
Rounds and verdicts are read from the frozen files for display only.
It counts recipe and private markers in the run state and in explicitly named transcripts, without copying transcript content; a transcript supplied twice is counted once, and the control's recipe-helper use counts only executed commands, never reading.
Caught defects are reported, never scored.
At the pinned revision a failing recipe helper refuses the submission, so its evidence stays unfrozen in `.recipe-evidence`.
Grade lists every helper run there and places it mechanically: its `result.json` and `doctor.json` must share an invocation, and the helper's own `build.sha256` of the UI sources is compared with the prepared seed (`baseline`), the approved tree (`approved-source`) or neither (`intermediate-edit`), with its start time before or after the approved freeze and whether the helper itself was unchanged.
Change requests in both arms are listed with their required-change counts.
Neither can be attributed to the recipe from one arm's evidence, so neither is a saving.
Grade refuses if any frozen tool input changed since preparation, and refuses to grade a label twice.

An independent adjudicator reviews `blind/<label>/` (source patch and behavior results, without arm, model or recipe labels) and writes `adjudication/<label>.json` as `{"escaped_defects": [], "notes": ""}`.

## Report and frozen rubric

```sh
tools/recipe-comparison/compare.py report --private-root /abs/private
```

The rubric in `tools/recipe-comparison/rubric.json` is frozen at preparation.
The report is inconclusive when a pair lacks an intact approval in either arm, the treatment was not actually recipe-verified, the control selected the recipe, an observed model or effort from the proven full sessions (Codex per turn context, Claude per assistant record) differs from the request or is unknown, dependencies or the runtime changed, or hidden checks, rubric or mapping reached a candidate.
When the treatment's recipe correctly blocks a broken fix and no approval follows, the pair is inconclusive rather than a win; the report says so.
It is not supported on an observed treatment regression, an extra routine model turn, or a median execution-token overhead above 15%, a budget choice rather than a quality threshold.
Unknown regression, turns or tokens keep it inconclusive.
Usage is measured only when exactly one measured session exists per required role and every token category is present; otherwise every total is unknown, never zero.
Promotion needs a measured saving: a hidden-check, pinned-test or adjudicated failure only the control shows, or fewer correction rounds in the treatment.
Recipe helper runs, change requests, intervention counts and free-text notes are reported but never scored, and a tie without a measured saving does not promote.
Authoring and maintenance effort, from an optional `maintenance.json`, is reported separately from execution cost.
Raw tokens are not money.

## Privacy

Candidate directories, the private root, transcripts, usage files, proof and graded copies are local data that can hold secrets.
Keep them out of Git.
Commit only a sanitized aggregate report and this procedure.
