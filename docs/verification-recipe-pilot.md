# Verification recipe pilot

The first recipe checks one real companion boundary: a project added in the
browser remains available after an actual companion restart. It uses synthetic
project/run data, a private state directory, two owned server processes and real
Playwright Chromium. It does not call a model, start an agent or use the user's
companion/Herdr instance.

Select `ui/verification/companion-projects/recipe.json` explicitly for a task.
Recipe selection is a project-local contract, not relevance-ranked optional
guidance. Existing tasks without recipes keep their normal verification behavior.
The task's verification floor still applies; the helper belongs in its verify
command alongside any other required focused checks.

A task in a JSON plan can select it like this (the surrounding plan still needs
its normal roles, milestones, model policy and other required fields):

```json
{
  "owner": "T01",
  "title": "Preserve companion project persistence",
  "goal": "A browser-added project remains available after a companion restart.",
  "criterion": [
    "The added project remains listed after restart and selecting it shows its own runs.",
    "Only explicitly selected project directories are listed and unrelated state is unchanged."
  ],
  "file": ["ui/projects.mjs", "ui/public/app.js"],
  "verify": "node ui/verification/companion-projects/verify.mjs --evidence-dir \"$DOCKET_RECIPE_EVIDENCE_DIR\"",
  "recipe": "ui/verification/companion-projects/recipe.json"
}
```

The task's flat frontmatter records the `recipe` selection as
`verification_recipe`. Assignment also accepts `--recipe PATH`. In a multi-root
run qualify the recipe with the declared root alias. No undeclared checkout or
whole directory is captured.

## Run it independently

From the repository root:

```sh
(cd ui && npm ci && npx playwright install chromium)
node ui/verification/companion-projects/verify.mjs --evidence-dir /absolute/fresh/private-proof
node --test ui/verification/companion-projects/verify.test.mjs
```

The recipe tests live outside the ordinary `npm test` glob. Run them explicitly;
the pilot adds no Chromium prerequisite to the default tests.

The absolute evidence directory must be empty, outside measured source and owned
by the caller. A checkout's private `.docket/` state is excluded from measured
source and is allowed; other checkout descendants are refused. Docket supplies a
unique private directory under the run's `.recipe-evidence/` state as
`DOCKET_RECIPE_EVIDENCE_DIR` for the selected task's verify invocation. A missing
prerequisite is exit 2 with an explanation; it is never counted as a passed or
skipped journey. The browser suites need installed system libraries as well as
the Playwright browser. Herdr is not required for this recipe.

To challenge the oracle with a controlled broken outcome:

```sh
node ui/verification/companion-projects/verify.mjs --evidence-dir /absolute/fresh/broken-proof --negative-control lose-projects
```

This command must exit 1 with `PERSISTENCE_MISSING`. It removes only the
helper-owned saved list between its two real companion processes, then observes
the missing project in a fresh browser context. Positive and negative tests also
assert process exit, scratch removal, private retained proof and port closure.
Prerequisite tests require actionable non-success results, and stale-proof tests
ensure old evidence cannot be silently reused.

## Review the evidence

The manifest declares `recipe.md`, `verify.mjs`, and required outputs
`doctor.json`, `result.json`, `journey.png`, `add-proof.json`.
`add-proof.json` is written only by a passing add scenario after cleanup.
The M3 comparison stays pinned at fe58b1a, whose historical manifest requires only the first three outputs.
Read the
[full recipe](../ui/verification/companion-projects/recipe.md) for startup,
prerequisites, interaction, expected effects, cleanup and exit semantics.
Doctor records actual build/runtime identity and owned server readiness/exit;
result records the invocation, phases, assertions and cleanup. Screenshot and
JSON survive cleanup. The success screenshot waits for the selected fixture plan
and its synthetic role's empty/offline state to finish rendering after restart,
using DOM conditions rather than a sleep. Authentication tokens, environment variables and reusable
private launch URLs are not retained.

Core freezes the original recipe inputs and current invocation's required outputs
under the existing bundle digest. Missing required proof cannot complete normally;
a blocked submission does not claim a passing journey. The reviewer reads those
immutable inputs and outputs, derives expected behavior from the objective and
approved contract, and chooses an independent material boundary or negative case.
Changed expectations require scrutiny of the oracle, not just a new green run.

This pilot covers project membership persistence and project/run isolation only.
The last selected project is not promised across restart. Herdr stays offline;
live-agent/send, terminal, history and provider request behavior need their own
verification. A separately authorized paired trial is required before claiming
cost/quality gains or promoting recipes to a default.

## Explicit removal and re-add scenario

A second opt-in selection checks removal persistence and retained-project usability:
`ui/verification/companion-projects/removal.json`. The existing `recipe.json` and
default helper command keep the add-persistence journey. Choose removal explicitly
and pair it with the scenario flag in the task's registered verify command:

```json
{
  "owner": "T02",
  "title": "Preserve project removal across restart",
  "goal": "Removing a project persists without affecting retained projects or project data.",
  "criterion": [
    "The removed project stays absent after restart while the retained project opens its own run.",
    "The starting project stays protected and a removed project can be re-added and persisted."
  ],
  "file": ["ui/projects.mjs", "ui/public/app.js"],
  "recipe": "ui/verification/companion-projects/removal.json",
  "verify": "node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir \"$DOCKET_RECIPE_EVIDENCE_DIR\""
}
```

From the repository root, standalone positive and negative invocations are:

```sh
node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir /absolute/fresh/removal-proof
node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir /absolute/fresh/removal-negative --negative-control retain-removed
```

The real browser adds removable and retained projects, removes the selected one,
requires saved absence, then checks absence and retained-project usability in a
new companion process and fresh browser context. It re-adds the removed path and
uses a third process/context to prove the addition persists. Startup stays
protected in the UI and the real server returns its observed 400 removal refusal;
unrelated preferences and all project/run files stay unchanged, and
success capture waits for the final fixture plan and empty/offline DOM to settle.

The negative control inserts only the helper-owned removed path into saved state
after the first process exits, validating the removed directory and preserving
other saved bytes, entry order and mode by atomic rename. Result records fixture
validity and pre/post hashes. It must exit 1/`REMOVAL_NOT_PERSISTED` at removal
read-back, retaining failed proof. Prerequisite failures remain exit 2, never
skip/pass. Both scenarios share the doctor/result/screenshot contract and owned
cleanup.
Each manifest also requires its own scenario receipt, written only after that scenario passes and cleans up.
Removal requires `removal-proof.json` and add requires `add-proof.json`, so neither successful journey satisfies the other manifest.
Scenario/control pair errors are rejected before evidence
creation. Removal of terminal bindings is unobserved because fixtures have none.
For removal, inspect the intermediate absence observation in result;
the final screenshot intentionally follows re-add.

Read the [removal instructions](../ui/verification/companion-projects/removal.md)
and [maintained feature map](verification-feature-map.md) for entry points,
prerequisites, observations, limits and the reviewed maintenance workflow.
The explicit browser test file covers both scenarios outside ordinary `npm test`.
The M3 comparison remains pinned to `fe58b1a`, excluding this expansion and M1;
no cost/quality improvement or default promotion is claimed.
