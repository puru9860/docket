# Pstack adoption and continuation plan

Updated 2026-11-04. Working branch: `audit-3-fixes`.

**Resumed by the user on 2026-11-04.** Opus owns the comparison review fixes
and independent milestone qualification; Sol completed a targeted provenance audit. Read [the continuation handoff](pstack-resume-handoff.md)
for saved WIP branches, validation state and home-PC instructions.

The user authorized continuous implementation, Opus 5.5 for heavy design and
implementation, Sol for lighter work, and committing and pushing the plan and
tested milestones so work can continue from a home PC. The explicit pause has been lifted.
Do not ask again for routine implementation, review, commits, or pushes within
this scope.

## Product direction and constraints

- Keep one active coding flow: planner, implementor, independent reviewer, with
  deterministic coordination. Claude Code and Codex are the primary harnesses.
  Preserve recorded historical runs and their authority.
- User evals found similar quality and cost for regular Opus/Sol versus Opus
  planner, Sonnet implementor, Sol reviewer. Do not reintroduce model tiering
  merely because another framework uses it.
- Keep visible sessions and explicit ownership. Use isolated worktrees for
  independent parallel work; agree interfaces before editing shared code.
- Keep every evidence, locking, source-drift, immutable baseline, consumed-input,
  honest-blocking and exact-bundle verdict invariant in `ARCHITECTURE.md`.
- CLI remains stdlib-only. Do not add hidden subagents, default review panels,
  automatic instruction mutation, automatic merge/deploy permission, or a
  runtime-reproducer-only rule that excludes rigorous causal findings.
- Use the existing configured `codex` alias for launch settings. Do not add
  approval/sandbox flags that conflict with it. Select a requested model explicitly.
- Add meaningful assertions for changes. Bug-fix assertions must fail before
  and pass after. `tests/test.sh` must finish with zero failures.
- Preserve unrelated work, including the pre-existing untracked `reasonix.toml`.
  Stage explicit task files only. No force-push or default-branch merge.

## Evidence and design sources

The primary pstack source inspected was `cursor/plugins`, pstack v0.15.36,
commit `ae5650febf4320793844c12c4adaa937f417f8da`:
<https://github.com/cursor/plugins/tree/ae5650febf4320793844c12c4adaa937f417f8da/pstack>.
The relevant concepts are verification recipes/feature maps, blast-radius safety
facts, attack-the-premise, blinded evaluation, focused work-type methods, and PR
babysitting. Adapt methods to Docket's invariants rather than copying its policies.

The consultation with Astra and Opus converged on an opt-in recipe pilot first.
A shared recipe is an operational driver, not the independent authority for
expected behavior. Its expected outcomes remain claims to review against the
objective and approved contract. Freeze actual recipe/helper bytes and observed
runtime evidence; a revision label or screenshot alone does not establish truth.

## Milestones

| ID | Work | State | Main owner |
| --- | --- | --- | --- |
| M0 | Verification-recipe infrastructure and one real companion recipe | Implemented, verified and committed in this checkpoint | Opus core, Sol UI |
| M1 | Safety fact/probe, runtime proof descriptions, repeated-premise correction guidance | Implemented and reviewed; final combined suite 823 passed | Sol, Opus/coordinator review |
| M2 | Broader recipe coverage and maintained feature map | Implemented and reviewed; 13 browser tests and real positive/wrong-scenario core integration passed | Sol implementation, Opus/coordinator boundary review |
| M3 | Blinded recipe-only comparison with fixed model assignments | Resumed from acb9cd1; independent provenance/scoring fixes and milestone qualification before candidates | Opus design/candidates, Sol bounded adjudication |
| M4 | Focused refactor-equivalence and performance guidance | Implemented and reviewed; 8 focused tests and final combined suite passed | Opus design, Sol mechanical integration |
| M5 | Native group discussions | Integrated and regression-tested; direct three-party pilot concluded unanimously with manual delivery | Opus, coordinator review |
| M6 | Optional PR/CI babysitting | Assessed and deferred until concrete forge/CI waiting friction exists | Opus assessment, G05 advice |
| M7 | Independent early oracle checks | Assessed and deferred pending material residual need after M0/M1 use | Opus assessment, G05 advice |
| M8 | Final review, installed-copy sync, commits, push, portable handoff | After corresponding checks and approvals | Coordinator |

M5-M7 were deliberately deferred in the initial consultation. Continuous work
now includes their design and usefulness assessment, not a requirement to add
machinery that fails its own benefit/risk test. Record a concrete rejection or
deferral and its evidence rather than inventing a favorable result. Do not let
these later candidates block shipping independently useful earlier milestones.

## M0 implementation and current validation

Implemented `docket assign --recipe PATH`, JSON plan task `recipe`, and flat
`verification_recipe` frontmatter. A strict JSON manifest explicitly names its
instructions, helpers, and required proof. Paths are validated against declared
roots, with traversal, invalid declarations and symlink refusals.

Preflight, submission and re-review use fresh private proof directories. Recipe
inputs are captured before verification, checked for drift and frozen with the
named outputs. Missing proof cannot support normal completion. Blocked work
stays lightweight. Review packets retain immutable pointers, independent-check
duties, and changed-oracle scrutiny.

The opt-in companion recipe exercises a real browser Add project interaction,
actual persisted state, a new companion process and fresh browser context. It
includes controlled persistence loss, actionable prerequisite failures, isolation,
owned-process cleanup, and retained proof. It does not claim live-agent/send or
provider request coverage. See [the pilot guide](verification-recipe-pilot.md).

Latest verified source: 789 CLI regression tests, 43 UI Python tests, 104 UI Node
tests, and 8 explicit recipe browser tests passed. A real recipe preflight and
submission froze doctor/result/screenshot proof and produced an intact bundle
and review packet. That integration fixture used documents-only evidence; Git
capture, tamper, multi-root and drift behavior are covered by CLI regression tests.
No comparative cost or quality improvement is claimed.

M2 adds an explicit [removal recipe](../ui/verification/companion-projects/removal.md)
and [maintained feature map](verification-feature-map.md). A removed project
stays absent across a real restart, retained membership remains usable, and
re-addition survives another restart. It observes the starting project's server
refusal and preserves unrelated state. A removal-only passing proof file prevents
the default Add journey from accidentally satisfying the removal manifest.
Each current manifest requires its own passing receipt (`add-proof.json` or
`removal-proof.json`), so neither successful scenario can stand in for the other.
This identifies the scenario, not the correctness of its expected outcomes.
The named injected regression and unavailable-browser cases remain non-success.
All 13 explicit browser tests passed, as did 43 UI Python and 104 UI Node tests.
Real core preflight/submission froze all four required outputs in an intact
bundle with distinct proof directories; a default Add verification against the
removal manifest refused submission without changing the report or publishing a
bundle. That integration fixture is documents-only, not a Git-freeze claim.
Both current scenarios were separately preflighted, submitted and frozen with
four outputs, and both wrong-scenario directions refused with unchanged reports
and no bundle. M3 keeps the original three-output Add manifest at `fe58b1a`.

## M1 acceptance

- Use the implementor's existing discovery/scope turn, after dispatch and before
  editing, to identify a consequential safety fact and run a discriminating probe
  when warranted. Do not add a routine model turn or a new global gate.
- Keep supported/unresolved/contradictory. For consequential runtime claims,
  name proof type, execution conditions and the actual observed boundary.
  Runtime evidence does not override user intent or prove all interleavings safe.
- Repeated criterion numbers are a signal to inspect, not proof of one shared
  premise. Use judgment to identify the common assumption, choose a probe, and
  carry its result or explicit unrun state into existing correction/escalation
  records. Preserve correction budgets and planner authority.
- Keep these changes distinct from recipe-only evaluation variants.

## M2 and maintenance acceptance

Add reusable coverage only for a concrete user-visible boundary. A feature map
names entry points, prerequisites, launch/doctor/drive instructions, observations
and gotchas. Prove generated or authored instructions through an actual run.
Distinguish documentation drift, harness gaps and product regressions. Missing
prerequisites block honestly; do not weaken expectations to make a replay green.
Maintenance is ordinary reviewed work, not an automatic skill rewrite.

## M3 evaluation protocol

Use a narrow portable procedure, not the previously retired evaluation subsystem.
Freeze task versions, hidden checks and a concrete promotion criterion before
running candidates. Hold planner/implementor/reviewer model assignments fixed.
Compare the same CLI revision with and without explicitly selected recipes;
keep M1 or other prompt changes out of this first treatment.

Candidates receive ordinary work requests and their operational instructions.
Hide comparator identity and assessment rubric, not their tools or legitimate
project tests. Use separate clean task environments, counterbalance order, and
judge sanitized artifacts without model/variant labels. Verify behavior with
independent hidden checks and inspect actual execution, not self-reports.

Start with two matched task pairs at the selected boundary. Record every pair,
escaped defects, corrections, human interventions, model turns, cache-separated
token categories, authoring/maintenance effort and execution effort. Report
missing telemetry as unknown. Do not equate raw tokens with money or claim
statistical assurance from this tiny sample. Replicate before any global default.

First establish that the recipe detects a deliberately broken outcome and an
unmet prerequisite, isolates its state and retains proof. Proposed operating
targets are no extra routine model turn and at most 15% median paired execution
token overhead; this percentage is a budget choice, not an empirical quality
threshold. Require no observed safety regression and a concrete operational
saving or a meaningful defect caught before approval. A tie without a saving
does not promote. Preserve an unfavorable or inconclusive result honestly.

Run model work in visible approved Claude/Codex sessions. Do not silently switch
heavy work to a weaker model to bypass a limit. Keep raw transcripts, local proof,
credentials, patches and secret-bearing evidence under private local state, not
in Git. Commit a sanitized aggregate report and reproducible procedure.

## Later feature acceptance

For group discussions, preserve `discussion-v1` semantics. A future version needs
explicit participants/generations, serialized speaking rules, per-recipient
receipts, stale-message rejection, a user pause across all participants, and
acceptance bound to one exact proposal. Distinguish unanimous agreement, named
dissent and missing participation. Silence, timeouts and a launch request are
never agreement or proof of actual execution/model identity.

For PR babysitting, begin with deterministic status classification and bounded
CI/review/conflict handling. Bind observations and rechecks to the exact head;
deduplicate wakes and cap retries. Keep forge query failure distinct from green.
Preparing a PR never grants permission to merge, deploy or message other people.

For early oracle checks, justify any extra independent reviewer turn with a
separate trial. A discovery probe is not a pre-dispatch review. Preserve final
review independence and the existing objective/contract authority.

Current assessment: this repository has no CI configuration or demonstrated
PR-wait problem to justify a forge adapter. Revisit M6 when a managed project
provides that evidence, beginning with read-only exact-head status, bounded
retries and deduplicated wakes. Existing forge tools can serve occasional checks.

For M7, Opus reviewed 20 required changes in five earlier rejected tasks and
classified five as plausibly knowable from the pre-code scope. Every task also
had post-implementation findings. This is a small selected retrospective and a
judgment about possible early detection, not measured counterfactual savings.
M0/M1 and observable criteria already target those pre-code categories. First
measure the residual after use; an extra early turn needs its own high-risk trial
and a separate final review session. Neither deferral is a permanent rejection.

## Validation, commits and home-PC continuation

After each source milestone run focused checks, independent review and required
regressions. Do not rerun identical checks merely to generate activity. Before
the final checkpoint run:

```sh
tests/test.sh
(cd ui && npm test)
node --test ui/verification/companion-projects/verify.test.mjs
git diff --check
```

The explicit browser suite needs Node 22+, Python 3.11+, `cd ui && npm ci`, and
Playwright Chromium/system libraries. Ordinary `npm test` keeps its existing
prerequisites. The CLI works as a plain `python3 skills/docket/bin/docket`.

Commit and push this plan first. Commit tested milestones on `audit-3-fixes`,
update this table and a concise public validation report, and push without force.
The `no-mistakes` CLI is unavailable on the current machine; use repository tests
and independent review unless that tool becomes available. Do not claim it ran.

On the home PC, fetch origin and switch to `audit-3-fixes`, then fast-forward
only. Start a fresh session with this document, `AGENTS.md`, `ARCHITECTURE.md` and
the milestone's changed files. No task depends on `/tmp` worktrees, private
conversation files or a transcript on the first machine. Use the source CLI
during development. After final approval, sync the installed skill according to
`AGENTS.md`, excluding `__pycache__` and `*.pyc`; avoid changing tools underneath
an active run. Keep local model launch configuration outside Git.

## Progress log

- 2026-11-03: Initial recipe pilot implemented, independently reviewed and
  verified in separate Opus/Sol worktrees, then integrated without overlap.
- 2026-11-03: User authorized continued work, heavy Opus/light Sol routing,
  pushing this plan and committing/pushing finished milestones for home-PC use.
- 2026-11-03: Plan e4119fd and verified recipe checkpoint fe58b1a pushed.
  M1 passed 41 focused checks; the full suite was interrupted for an explicit
  user pause. Opus auto-resume was cancelled. Separate WIP group/comparison
  branches and the tracked resume handoff preserve unfinished work.
- 2026-11-03: User explicitly resumed. The existing two Opus lanes and Sol
  follow-up resumed in their isolated worktrees; the coordinator restarted the
  interrupted M1 regression suite. No model substitution or installed-copy sync.
- 2026-11-03: Integrated the recipe-path round-trip fix (7 tests) and opt-in
  group discussions (11 new tests, 31 existing bilateral tests unchanged).
  Combined CLI suite passed 814 tests. Its first attempt caught a reviewer
  prompt-size regression; duplicated mandatory reasoning was removed without
  raising the existing limit. Independent Opus review then tightened correction
  probe isolation and omitted correction advice after accepting decisions;
  all 7 guidance tests passed after those refinements. A final combined suite
  remains due after the next source milestone.
- 2026-11-03: M3 sensitivity proof showed both original seeded defects escaped
  the Add-project helper because they concern adjacent behavior. No candidates
  ran. Before launches, replace one task with a covered server-startup wiring
  defect and independently prove helper sensitivity; retain an adjacent task
  to measure preservation value. No quality/cost result or default promotion.
- 2026-11-03: Started G05 using the new group protocol with the coordinator,
  Opus and Sol. Delivery is manually initiated while the installed copy remains
  unchanged; direct participant identity and protocol execution are the pilot's
  scope, not an assertion that installed automatic hooks have been exercised.
- 2026-11-03: M2 removal/re-add recipe and maintenance map integrated after Opus
  boundary review. The scenario receipt, server refusal and atomic negative
  control were exercised; all 13 browser tests and UI unit tests passed. Real
  core freezing and wrong-scenario refusal passed before committing this step.
- 2026-11-03: G05 concluded unanimously on the scoped M4 design and M6/M7
  deferrals. Opus and Sol joined from distinct native Claude/Codex sessions,
  exchanged attributed messages directly and each accepted exact proposal 3.
  The coordinator used an explicit session because native ancestry was
  unavailable. Delivery was manually initiated; installed automatic hook
  integration is not claimed. Agreement is design advice, not coding approval.
- 2026-11-04: Integrated explicit task methods and symmetric recipe receipts.
  The planner example retains a runnable backticked help pointer and fits the
  existing 8,000-character limit; the limit was not raised. All 823 CLI tests
  passed after the final help-size correction. Against an actual archived
  `bf3941c`, unselected implementor/reviewer prompt bytes and revisions were
  identical. Eight task-method and eight path-codec tests pass; the codec also
  preserves its own string delimiters while public backslash paths stay refused.
  The 13 browser tests passed on the receipt changes, and real core integration
  verified positive freezing and wrong-scenario refusal in both directions.
- 2026-11-04: Independent M3 review found launch/provenance, frozen-tree grading,
  infrastructure classification and scoring issues before any candidates ran.
  Fixes remain in the isolated comparison lane. There are still no comparative
  model outcomes, cost/quality claims or default promotion.
