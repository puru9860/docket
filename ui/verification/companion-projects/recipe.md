# Companion project persistence recipe

Use this explicitly selected pilot for work that must preserve the companion's
browser project-list behavior. Derive acceptance from the assigned objective and
approved contract. This recipe supplies one material verification boundary, not a
replacement for the task's other verification requirements.

## Prerequisites and launch

Run from the repository root with Node.js 22+, Python 3.11+ available as `python3`,
the companion's locked npm dependencies, and Playwright Chromium installed:

```sh
(cd ui && npm ci && npx playwright install chromium)
node ui/verification/companion-projects/verify.mjs --evidence-dir "$DOCKET_RECIPE_EVIDENCE_DIR"
```

Docket supplies a fresh, private evidence directory under the run's
`.recipe-evidence/` state, inside the checkout's excluded `.docket/` directory.
It is outside measured source.
For a standalone run, pass an explicit absolute empty directory you own instead.
The helper refuses existing proof, symlink traversal and checkout-local evidence
outside private `.docket/` state.
If system libraries are missing, follow Playwright's launch diagnostic and retry
with a new evidence directory. `--browser-executable /absolute/path/to/chromium`
selects an already installed compatible browser; it never downloads a browser.

The helper starts only its own real `ui/server.mjs` processes, on `127.0.0.1` and
OS-assigned ports, with synthetic disposable projects, isolated HOME and XDG
config/data/state/cache, no provider credentials, and a nonexistent private Herdr
socket. It neither connects to nor stops a user's companion or Herdr. Readiness
requires the private server's launch URL, an actual HTTP 200, and the expected run
in the actual browser. No HTTP response, server implementation or provider is
mocked. No model calls or agent launches occur.

## Doctor and exact journey

1. Record Node, Python, Playwright and Chromium versions, OS/architecture, package
   version, Git revision when available and SHA-256 of explicit app/runtime input
   files. Record each owned server PID, assigned port, readiness and exit status.
   Keep the per-launch authentication URL/token in memory only.
2. Create startup, target and unrelated synthetic projects with distinct run
   titles. Create separate unrelated preference data as a canary. Only startup
   appears initially. Hash all synthetic project files before interaction.
3. Through the project menu, try a directory without `.docket/`. Require the
   visible rejection and an unchanged list. Then add the target through the same
   form. Require the target to be selected and its own run title to appear.
   Read the actual private preferences file and require exactly startup and target.
4. Close the browser context and gracefully stop that companion. Start a distinct
   companion process against the same private state and startup project. Use a
   fresh browser context with its new launch token, without saved session storage.
5. Require target membership in the project list, select it, and require its own
   run title. Require unrelated membership to stay absent, unrelated preference
   bytes unchanged and every synthetic project/run file unchanged. Wait for the
   selected fixture plan's heading and body to render, the synthetic role's
   empty-history state and missing-session explanation, and the Herdr-offline
   indicator, using DOM completion conditions. Capture the browser screenshot
   after this read-back; loading placeholders are not success proof.

The persisted promise is project membership. The recipe selects the saved target
again after restart; it does not claim the last selection survives a restart.
Herdr remains offline. This journey covers project/run lists, not live-agent/send,
terminal, history, question or permission behavior.

## Negative control and independent review

```sh
node ui/verification/companion-projects/verify.mjs --evidence-dir /absolute/fresh/negative-proof --negative-control lose-projects
```

This deliberately deletes only the helper's saved private `projects.json` after
the first process exits. The real second process then loses the added project.
The command must exit 1 with `PERSISTENCE_MISSING`, not pass because failure was
expected. The result labels the injected fault and retains the failure screenshot.
Never add the negative-control flag to a normal passing verification command.

The reviewer reads the frozen recipe and observed outputs, checks their identity
against the submitted bundle, and chooses an independent material boundary or
negative case from the objective and contract. For example, removing a saved
project or changing an invalid-path boundary is different from repeating the
same positive journey. Expectation/oracle changes need explicit scrutiny. A
passing screenshot alone does not establish that the objective was met.

## Results, cleanup and retained evidence

- Exit 0: all journey assertions and cleanup passed.
- Exit 1: behavior, launch/readiness or cleanup failed. The phase and error explain
  where; a controlled broken outcome is also exit 1.
- Exit 2: prerequisites or invocation failed. Missing Python, npm dependencies or
  browser is an actionable non-success, never a skip. A bad evidence path is
  refused before artifacts are written.

`doctor.json` and `result.json` share a unique invocation ID and record actual
observations from that invocation. `journey.png` shows the successful final state,
or the failure state when a page exists. A prerequisite failure can have no
screenshot and therefore cannot satisfy normal required proof. No prior output
is reused. The helper writes private JSON/PNG files only into the requested empty
evidence directory.
`add-proof.json` is written only after a passing add scenario's cleanup.
It repeats the invocation, scenario, restart observation, and cleanup outcome.
The manifest requires it, so a passing removal run, which writes `removal-proof.json` instead, cannot satisfy this manifest.
The receipt marks which journey passed; it is not an oracle for whether the behavior is right.

On success and handled failure, close the owned browser, stop only the owned
companion children (SIGTERM, then SIGKILL if needed), and remove private fixture,
state and cache directories. Cleanup errors fail verification. Result cleanup
fields retain owned PIDs, the removed sandbox path, and cleanup outcomes so tests
can check process exit and removal. Evidence stays outside that sandbox and
survives cleanup. Nothing removes the caller's evidence directory.

Docket freezes the manifest, instructions and helper bytes captured before verify,
and the named proof captured after verify, in the task's immutable evidence bundle.
Review those frozen bytes. Standalone evidence remains the caller's responsibility
until captured in a bundle. This is an opt-in recipe-only pilot; no default
promotion or cost/quality improvement is claimed without a separately authorized
paired trial.
