# Pstack continuation handoff

**Paused at the user's request on 2026-11-04.** All three visible agents are
idle and the comparison test/proof processes have stopped. Wait for an explicit
continue before resuming work. No comparison candidates have been launched.
The working branch is `audit-3-fixes`; read
[the adoption plan](pstack-adoption-plan.md) for scope.

Verified implementation is pushed through `b1a6fbe`. The comparison lane is
preserved separately at `acb9cd1` on `pstack-recipe-comparison`; its latest
review fixes are WIP. Its final test/proof run was interrupted at this pause,
so do not treat the latest draft as validated. The installed skill remains
unchanged.

## Source and validation

- `e4119fd`: portable adoption plan.
- `fe58b1a`: verified recipe infrastructure and real companion Add-project recipe.
- `42a406a`: preserved M1 draft and graceful pause handoff.
- The continuation checkpoint containing this file integrates M1 reasoning
  guidance, the recipe-path fix and native opt-in group discussions.

The final combined CLI suite passed 823 tests on 2026-11-04. Existing size
limits caught duplicate reviewer guidance and an oversized planner example;
both were corrected without raising a limit. Final reviewer guidance isolates
correction probes and applies correction advice only to changes-requested
work. Unselected implementor/reviewer prompt bytes and revisions match an actual
archived `bf3941c`. Eight task-method and eight recipe-path tests pass.

The recipe-path fix has 8 passing integration tests: real assign, plan, prompt,
submit, bundle and qualified-root behavior with spaces, hashes and quotes. The
field alone uses a JSON string codec; ordinary references and global flat
frontmatter behavior retain their existing bytes. Old truncated fields cannot
recover missing suffixes automatically. Bug assertions failed before the fix.

Group discussions have 11 new tests plus 31 unchanged existing bilateral tests.
The protocol is opt-in with two to four named peers, a serialized floor, explicit
proposal-bound acceptance or named dissent, per-recipient delivery, whole-group
user pause, honest caps and retry-safe generation replacement. Both interrupted
replacement and withdrawal-at-cap wake regressions were reproduced before their
fixes. See [the design](group-discussion-design.md). G05 is the real three-party
pilot, with manually initiated delivery until the installed copy is synced.
Its unanimous conclusion is design advice, never coding approval or a passing test.

M0's existing checks passed 789 CLI tests, 43 UI Python tests, 104 UI Node tests,
and 8 explicit browser tests. Real preflight/submission used distinct proof
folders and froze an intact doctor/result/screenshot bundle. That integration
fixture used documents-only evidence; Git freezing, multi-root, drift and tamper
cases are covered by the CLI suite. No comparative cost/quality gain is claimed.

## Completed methods and remaining comparison work

M2 is complete and independently reviewed. The real browser removes a project,
restarts a distinct server with a fresh context, preserves other projects, and
permits re-addition. Each current scenario has its own required passing receipt:
`add-proof.json` or `removal-proof.json`, in addition to doctor/result/screenshot.
All 13 focused browser tests passed. Real core preflight/submission froze four
outputs for each scenario, while both wrong-scenario directions refused with
unchanged reports and no bundle. Startup removal is refused by the actual
server; the controlled failure is atomic and preserves other state. See the
maintained feature map. Terminal-binding invalidation remains unobserved.

M3: Opus is preparing the narrow blinded procedure on `pstack-recipe-comparison`.
Its current WIP preservation checkpoint is `acb9cd1`, including the latest
review fixes. Review and rerun verification before integration or candidates.
Preparation and sensitivity tests ran, but no model candidates have run.
The first two seeds escaped the original Add-project helper because they concern
adjacent behavior. One task now covers server shutdown deleting the saved list, while the other
remains adjacent removal behavior. Sensitivity tests prove reference/alternate repairs pass,
the seeds escape existing tests, and the original helper catches the covered
persistence failure while passing on the adjacent seed. Independent review found
additional provenance/launch, frozen-tree grading and scoring issues; the
isolated lane has draft fixes awaiting final review and verification before any candidates. Use the final reviewed
protocol, not a draft launch procedure. Keep applicability limits explicit.
Pin `fe58b1a` and identical Opus implementor/Sol reviewer assignments in both arms;
exclude M1 and M2 to isolate the first treatment. Freeze source/tasks/checks/rubric
before candidates, keep private mapping/checks outside candidate directories,
and require actual independent approvals and measured identity/usage. Unknown
telemetry remains unknown. Two pairs permit directional opt-in findings only.

M4 is implemented. Use an explicit existing `--decision` / plan-task `decision`
selection and `docket help task-methods` for refactor-equivalence/performance.
There is no `--constraint` flag or automatic work-type parser. Guidance preserves
acceptance, historical compatibility, mandatory contract rendering and final
review, with no new schema, roles or routine model turns. The selected command
uses backticks so an absent PATH entry can fall back to the absolute launcher.

M6/M7: Opus recommends deferring forge babysitting and extra early-oracle turns.
There is no current CI/PR waiting problem in this repository. An early-review
retrospective found only a subset of corrections knowable before code, and all
sampled rejected tasks also had later implementation defects. Treat these as
bounded design assessments, not measured counterfactual savings or a global
rejection. Revisit on concrete forge friction or material residual oracle errors
after the discovery/recipe guidance has been used.

## Continue from another machine

Resume only after the user explicitly continues. On this machine, untracked
`docs/recipe-comparison-protocol.md` and `tools/` in the main checkout contain an
older comparison integration that passed 40 tests, not the latest paused draft.
Do not stage those stale copies as the current comparison implementation.
The authoritative draft is the `pstack-recipe-comparison` branch at `acb9cd1`.
Preserve unrelated files when replacing the older integration on resume.

Remaining comparison work includes independent review of the launch/provenance,
frozen-tree grading and scoring fixes, final toolkit tests and sensitivity proof,
and actual launch qualification before any candidates. A real conductor with
fake Herdr is useful plumbing evidence but cannot establish that model commands
use the pinned CLI. Candidate traces must prove that separately. Do not change
global aliases, permissions or installed Docket to make the experiment launch.
The fake-Herdr qualification proved the pinned conductor adopts already idle
agents only by its exact path-derived name and harness kind, without reading
launch flags. Each role needs its own pane with `DOCKET_RUN`, `DOCKET_ROLE` and
`DOCKET_SESSION` equal to that expected agent name. Set the mandatory decision
to: "Run every Docket command through `<launcher>`, including the startup and
obligation commands above that name docket without a path; never invoke docket
by bare name." Assert that this wording survives rendering verbatim; quoted
bare command examples inside the decision are rewritten by the pinned renderer.
Three startup/obligation commands outside the contract remain bare, so the first
live case must prove compliance through actual tool calls. Codex can record these
as `custom_tool_call` wrapping `functions.exec`, not only `function_call`.
The clean conductor PATH needs the real Node/npm directory for milestone checks
and a separate Herdr shim, without introducing installed Docket. Keep capture off
and use absolute `conduct`, not `launch`.

Keep the fixed `fe58b1a` treatment source and mandatory repository completion
checks identical in both arms. Docket usage alone is a lower bound; full fresh
role telemetry and actual model identity remain required for cost claims.

Fetch origin, switch to `audit-3-fixes`, and fast-forward only. Read this file,
`docs/pstack-adoption-plan.md`, `AGENTS.md` and `ARCHITECTURE.md` in a fresh
session. All approved source and lasting intent belong in Git. Local worktrees,
private evidence, pane identities and transcripts are not portable requirements.
The older `pstack-group-discussions` checkpoint `e23dda0` preserves the original
partial draft; use the integrated continuation branch for current group code.

Use the existing `codex` alias launch settings without adding competing approval
or sandbox flags. Use source Docket during development. Heavy work uses Opus 5.5,
light work Sol; do not silently substitute a weaker model at a usage limit.

Required final checks:

```sh
tests/test.sh
(cd ui && npm test)
node --test ui/verification/companion-projects/verify.test.mjs
git diff --check
```

Python test commands need `PYTHONPYCACHEPREFIX` outside the skill. Explicit browser
checks need locked UI dependencies and Playwright Chromium/system libraries;
ordinary `npm test` retains its existing prerequisites. The `no-mistakes` CLI was
unavailable and was not run; use repository checks and independent review.

The installed skill has not been synced. Sync only approved source according to
`AGENTS.md`, excluding bytecode and avoiding changes underneath active runs.
Keep raw local proof/transcripts out of Git. The unrelated pre-existing
`reasonix.toml` remains untracked and untouched.
