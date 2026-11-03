# Companion project removal persistence recipe

Select `removal.json` explicitly for this boundary and run the helper with
`--scenario remove-projects`. The default `recipe.json` and helper invocation
remain the add-persistence journey. There is no inferred work type or Docket CLI
field. Review expected behavior against the objective and approved contract;
these operational instructions do not define an independent oracle.

## Prerequisites, isolation and doctor

From the repository root, use Node.js 22+, Python 3.11+ available as `python3`,
the locked companion npm dependencies, and installed Playwright Chromium with
its system libraries. Prepare dependencies in your own checkout when needed:

```sh
(cd ui && npm ci && npx playwright install chromium)
node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir "$DOCKET_RECIPE_EVIDENCE_DIR"
```

Docket supplies a fresh private directory under the run's excluded
`.recipe-evidence/` state. A standalone caller passes an explicit absolute empty
directory it owns outside measured source. Checkout `.docket/` descendants are
allowed; other checkout paths, symlink traversal and existing proof are refused.
`--browser-executable /absolute/chromium` selects an installed compatible browser;
this helper never installs dependencies or downloads a browser.

The helper starts only its own real companion processes with temporary loopback
ports, a private HOME and XDG config/data/state/cache, synthetic disposable
projects/runs, no inherited provider credentials, and a nonexistent private Herdr
socket. It does not use or stop a user's companion/Herdr, launch agents, call a
provider model, or replace HTTP responses. Readiness requires the actual startup
URL, HTTP 200, and the expected run in real Chromium.

`doctor.json` records scenario, invocation, app file hashes, Git revision when
available, package and actual Node/Python/Playwright/Chromium versions, plus each
owned server PID, assigned port, readiness and exit. Authentication URLs/tokens
remain in memory and are not retained in JSON or logs.

## Exact interaction and expected effects

1. Create startup, removed, retained and unrelated projects, each with its own
   synthetic run. Only startup is initially listed, and its Remove from list
   action is disabled. Use the page's authenticated fetch to require the real
   server's 400 startup-removal refusal and exact message; never retain its token.
   Hash all project/run files and separate unrelated
   preferences before interaction.
2. Add removed and retained through the real Add project menu/form. Require the
   selected project and its own run after each add; require the saved private list
   to contain exactly startup, removed and retained.
3. Select removed, require its own run, then use its enabled Remove from list
   action. Require return to startup and both the browser list and actual saved
   preferences to contain exactly startup and retained.
4. Close the browser context and stop that companion. Start a distinct companion
   process against the same private state and open a fresh browser context with
   its new token. Require removed to stay absent and startup/retained to remain.
   Select retained and require its own run title.
5. Add the removed path again through the browser. Require it to be selected and
   saved alongside startup/retained. Close that context and companion, start a
   third distinct companion and fresh browser context, then require all three
   memberships. Startup must still be protected. Select removed and require its
   own run. This proves removal did not permanently exclude the directory.
6. Require unrelated membership absent, unrelated preference bytes unchanged, and
   every project/run file unchanged. Removal changes only the private project
   list, not project data. Wait on actual DOM conditions for the selected fixture
   plan heading/body, completed empty-history explanation and Herdr offline
   before capturing the successful `journey.png`. No fixed sleep establishes
   screenshot readiness.

`result.json` records scenario, phases, check observations and cleanup. Inspect
both removal and re-add read-back observations, not just the final screenshot
where the project has intentionally been re-added. Project membership persists;
restoration of the last selection across restart is not promised.

## Decisive negative control

```sh
node ui/verification/companion-projects/verify.mjs --scenario remove-projects --evidence-dir /absolute/fresh/removal-negative --negative-control retain-removed
```

After the browser's removal and the first process exit, this deliberately inserts
only the helper-owned removed path into its private saved project list. Validate
that directory with the application's actual validateProject first. Preserve all
other saved bytes, fields, entry order and mode 0600; publish the change by atomic
rename. Record the injected fixture path/validity and pre/post digests in result.
It changes no source or project files. The real second process then lists removed again.
The command must exit 1 with `REMOVAL_NOT_PERSISTED` at
`removal-restart-readback`, retaining the failure screenshot and named fault in
result. Detecting the injected wrong outcome is a failed verification, not a pass.
Never put this flag in the normal passing verify command. `lose-projects` remains
the default add scenario's separate control; controls are scenario-specific.

## Cleanup, evidence and exit semantics

Exit 0 means all journey and cleanup assertions passed. Exit 1 means behavioral,
launch/readiness or cleanup failure, including the injected removal failure.
Exit 2 means an actionable prerequisite or invocation failure. Missing browser,
Python or npm dependencies never count as skip/pass. A prerequisite failure may
have no screenshot, so it cannot satisfy the manifest's required proof.

The removal manifest additionally requires `removal-proof.json`, written only
after the removal scenario passes its journey and cleanup. It records the same
invocation, removal/re-add observations and server protection. A default Add run
never writes it, so it cannot satisfy the removal manifest accidentally. Negative
and prerequisite failures do not claim that passing receipt. Scenario/control
mismatches are usage errors before proof-directory creation or runtime launch.

`doctor.json`, `result.json` and `journey.png` are fresh, private observations from
this invocation; unique invocation IDs bind the JSON records. No old proof is
reused. After success or handled failure the helper closes its browser, stops
only its companion children (SIGTERM, then SIGKILL if needed), and removes its
private sandbox, fixtures and state. Cleanup errors fail verification. Evidence
survives in the caller's separate evidence directory, which the helper never
removes. Tests check owned PID exit, port closure, sandbox absence, private
artifact permissions and retained valid PNG/JSON.

Docket freezes the manifest/instructions/helper bytes captured before verify and
required outputs captured after it in the existing immutable bundle. The reviewer
checks that selection and command match this scenario, derives expectations from
the objective/contract, scrutinizes changed oracles and chooses an independent
material boundary or negative case. Missing proof cannot complete normally;
honest blocked submission claims no pass.

This recipe covers project-list removal, retained-project usability and re-add
persistence. Herdr remains offline. Synthetic empty-history completion is only
screenshot readiness, not real history, live-agent/send, terminal, binding or
provider-request coverage. The fixtures have no terminal bindings, so removal
invalidation of those bindings is explicitly unobserved. No cost/quality improvement
or default promotion is claimed. The separate recipe-only comparison remains pinned to `fe58b1a`.
