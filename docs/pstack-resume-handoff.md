# Pstack continuation handoff

The user resumed on 2026-11-03 around 21:55 Nepal time after the earlier graceful
pause. Continuous implementation, heavy Opus/light Sol routing, disjoint
worktrees, commits and pushes remain authorized. The working branch is
`audit-3-fixes`; read [the adoption plan](pstack-adoption-plan.md) for scope.

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
Its WIP preservation checkpoint is `9f66fbe`; it is not the latest reviewed code.
Preparation and sensitivity tests ran, but no model candidates have run.
The first two seeds escaped the original Add-project helper because they concern
adjacent behavior. One task now covers server shutdown deleting the saved list, while the other
remains adjacent removal behavior. Sensitivity tests prove reference/alternate repairs pass,
the seeds escape existing tests, and the original helper catches the covered
persistence failure while passing on the adjacent seed. Independent review found
additional provenance/launch, frozen-tree grading and scoring issues; the
isolated lane is fixing those before any candidates. Use the final reviewed
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
