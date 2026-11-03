# Maintained verification feature map

This map lists real companion boundaries covered by the opt-in recipes. It is an
authored navigation aid, not an automatic relevance selector or acceptance oracle.
Use the objective and approved contract to derive expected behavior independently.
The [pilot guide](verification-recipe-pilot.md) shows explicit task selection.

| Covered boundary | Recipe and driver | Actual entry points | Required observations |
| --- | --- | --- | --- |
| Add project and persist membership | [recipe.json](../ui/verification/companion-projects/recipe.json), [recipe.md](../ui/verification/companion-projects/recipe.md); default helper scenario | Browser project menu/Add project, `ui/public/app.js` project form; `ui/server.mjs` `/api/projects`; `ui/projects.mjs` registry save/load | Selected added project shows its own run; actual saved preferences contain only named projects; new companion process and fresh browser context retain membership |
| Reject a path without `.docket/` | Same add recipe | Browser Add project error; `validateProject` in `ui/projects.mjs` | Visible rejection, unchanged list, no project file changes |
| Remove project and persist absence | [removal.json](../ui/verification/companion-projects/removal.json), [removal.md](../ui/verification/companion-projects/removal.md); `--scenario remove-projects` | Browser Remove from list; `ui/public/app.js` remove action; server `/api/projects/remove`; registry remove/save/load | Browser and saved list exclude removed path; after actual restart it stays absent, retained project selects its own run; startup removal is disabled and its real server request returns 400 |
| Re-add a removed project | Same removal recipe | Browser Add project and registry add/save/load | Removed directory remains intact and can be added again; a third distinct companion and fresh context retain it and both other memberships |
| Isolation and evidence lifecycle | Both scenarios in [verify.mjs](../ui/verification/companion-projects/verify.mjs) | Private companion CLI launch, real Chromium, isolated HOME/XDG, owned-process cleanup | Separate unrelated preferences and all synthetic project/run bytes unchanged; private retained proof, process exit, closed ports and removed sandbox |

## Common prerequisites and launch/doctor/drive

Both recipes need Node 22+, Python 3.11+ as `python3`, locked UI npm dependencies,
and installed Playwright Chromium/system libraries. Herdr is intentionally offline
for these project/run-list boundaries. The driver never installs dependencies.
For an owned checkout that needs setup, use `cd ui && npm ci`, then
`npx playwright install chromium`. Never install through another checkout's shared
read-only dependency symlink.

From the repository root, each command both launches private instances, records
doctor identity and drives the real UI:

```sh
node ui/verification/companion-projects/verify.mjs --evidence-dir /absolute/fresh/add-proof
node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir /absolute/fresh/removal-proof
node --test ui/verification/companion-projects/verify.test.mjs
```

Docket's verify command instead passes `--evidence-dir
"$DOCKET_RECIPE_EVIDENCE_DIR"`. Recipe selection and scenario flag are explicit and
must match. The unchanged default manifest still selects add persistence. No new
Docket field or inferred work type is involved.

Read `doctor.json` for actual runtime/build identity and owned server readiness/
exit, `result.json` for scenario, assertions, restart observations and cleanup,
and `journey.png` for the settled final DOM. Removal additionally requires
`removal-proof.json`, emitted only by that passing scenario after cleanup; a
default Add run lacks this required file. JSON and PNG survive sandbox cleanup.
For removal, the final screenshot follows re-add; the intermediate removal
observation in result establishes absence at its own restart boundary. Startup is
selected afresh on restart; last-selected persistence is not an expectation.

## Negative and prerequisite observations

```sh
node ui/verification/companion-projects/verify.mjs --evidence-dir /absolute/fresh/add-broken --negative-control lose-projects
node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir /absolute/fresh/removal-broken --negative-control retain-removed
node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir /absolute/fresh/removal-prerequisite --browser-executable /absolute/missing-chromium
```

Expected outcomes are respectively exit 1/`PERSISTENCE_MISSING`,
exit 1/`REMOVAL_NOT_PERSISTED`, and exit 2/`PREREQUISITE`. Fault injection touches
only the helper-owned saved list between owned server processes. The removal
control first validates the still-present project, preserves other bytes, order
and mode by atomic rename, and records fixture validity plus pre/post hashes.
Scenario/control pair errors are refused before evidence creation. Invalid
invocation or evidence paths can be refused before artifacts exist. Missing prerequisites
are actionable non-success and may have no screenshot; never silently skip/pass.
Every invocation needs an absolute empty evidence directory outside measured
source; excluded checkout `.docket/` state is allowed. Existing proof and symlink
traversal are refused. Do not commit private raw proof, tokens or launch URLs.

## Maintenance workflow

1. For a change to the listed UI entry points, registry storage, companion launch,
   runtime dependencies or screenshot readiness, inspect the mapped recipe and
   actual accepted behavior. Add a row only for a concrete boundary demonstrated
   by a real run. Update selectors, instructions and this map in ordinary reviewed
   work; there is no automatic skill rewrite.
2. Classify a failure using retained observations. A **product regression** breaks
   the approved boundary with a valid driver. **Recipe drift** means the accepted
   interface/launch changed and the driver or instructions no longer describe it.
   An **unavailable harness/prerequisite** means the boundary was not exercised;
   retain that gap, diagnose it and retry in a fresh evidence directory after
   repairing the environment. A failing control alone does not select a category.
3. Independently review every changed expected value, fixture or oracle. Never
   rewrite instructions/expectations to match incorrect behavior or conceal a
   missing prerequisite. Preserve an unfavorable or inconclusive result.
4. After relevant changes run the focused explicit browser suite plus a fresh
   positive, negative and prerequisite invocation for affected scenarios. Inspect
   the screenshots and JSON, confirm owned cleanup and evidence retention, and
   record commands/results with the patch. Unaffected full suites remain the
   coordinator's responsibility at the combined checkpoint.

## Uncovered behavior and comparison boundary

These recipes do not cover live agents, send, terminal/control/takeover, binding
invalidation, real provider history, questions or permissions. Removal's terminal-binding
invalidation is explicitly unobserved: these synthetic fixtures have no bindings.
The empty synthetic history state only ensures final screenshots are settled. Such boundaries need
separate observed instructions and proof before receiving covered rows. Herdr
being offline here is deliberate, not evidence of terminal behavior.

The M3 recipe-only comparison stays on `fe58b1a`; do not incorporate this expanded
removal recipe or M1 guidance into its treatment. This map establishes coverage
and maintenance steps, not cost/quality gains or promotion to a global default.
