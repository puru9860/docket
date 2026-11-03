# Pstack adoption and continuation plan

Updated 2026-11-03. Working branch: `audit-3-fixes`.

The user authorized continuous implementation, Opus 5.5 for heavy design and
implementation, Sol for lighter work, and committing and pushing the plan and
tested milestones so work can continue from a home PC. Do not stop at a plan or
ask again for routine implementation, review, commits, or pushes within this scope.

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
| M1 | Safety fact/probe, runtime proof descriptions, repeated-premise correction guidance | Ready to implement separately from the recipe-only treatment | Sol, Opus review |
| M2 | Broader recipe coverage and maintained feature map | One additional meaningful boundary plus a documented maintenance workflow | Sol implementation, Opus boundary review |
| M3 | Blinded recipe-only comparison with fixed model assignments | Design, run, and report; a small result permits opt-in reuse only | Opus design/candidates, Sol bounded adjudication |
| M4 | Focused refactor-equivalence and performance guidance | Small explicit work-type selection, when the relevant task needs it | Opus design, Sol mechanical integration |
| M5 | Native group discussions | Design and assess actual relay friction; implement an opt-in versioned extension if justified | Opus |
| M6 | Optional PR/CI babysitting | Assess existing tools, then implement a bounded deterministic adapter if useful | Opus |
| M7 | Independent early oracle checks | Separate risk-triggered trial; retain three roles and final independent review | Opus |
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
