# Docket companion

A local browser companion for Docket projects.
It lists the runs of the project directories you add, shows each role's saved conversation and the run's documents, and connects to the interactive agent sessions that are already running in Herdr.
All harnesses stay in their ordinary interactive TUI or CLI.
The companion never launches, resumes, restarts or replaces an agent, and it never changes Docket state.

## Requirements

- Node.js 22 or newer and Python 3.11 or newer.
- Herdr with the JSON terminal session bridge, tested with 0.8.2 (protocol 20).
- The companion has its own npm dependencies and is not part of the stdlib-only skill installation.

## Install and start

```sh
cd ui
npm ci
npm start
```

`npm start` reads this repository as its starting project.
Pass `--workspace /absolute/path/to/project` to start with another one.
The starting project always stays in the project list.

Open the private URL printed at startup.
It carries a random per-launch token in its fragment.
The browser keeps the token for that tab only and removes it from the address bar.
Anyone holding the URL can use this local companion, including taking control of a terminal, so keep it private.
After a restart, open the newly printed URL, because the old token no longer works.

The server listens on `127.0.0.1:4317`.
Use `--port 0` for any free port or `--port NUMBER` for a specific one.
Herdr socket selection uses `--socket`, then `HERDR_SOCKET_PATH`, then `$XDG_CONFIG_HOME/herdr/herdr.sock`, then `~/.config/herdr/herdr.sock`.
For a named Herdr server, pass its exact socket:

```sh
npm start -- --socket ~/.config/herdr/sessions/my-session/herdr.sock
```

The companion connects to an existing Herdr server and never starts, upgrades or stops it.
Run documents stay readable while that server is offline.
Stop the companion with Ctrl+C.
Its terminal bridges detach, and the original shells and agents keep running.

## Projects

The header's project list holds only directories you added.
Open the project menu (the dots beside it), choose **Add project**, enter the absolute path of a directory that contains `.docket/`, and its runs are listed at once, without restarting.
Only that exact directory is used: parents, siblings and linked checkouts are never discovered, and a path without `.docket/` is refused.
Adding or selecting a project creates no Docket run, starts no agent and writes nothing into the project.
**Remove from list** drops a project from the list and invalidates the terminal bindings made for it.
The starting project cannot be removed.

The list of paths, and nothing else, is saved to `$XDG_STATE_HOME/docket-ui/projects.json` (default `~/.local/state/docket-ui/projects.json`) so it survives a restart.
A project that no longer exists is dropped when the file is read.

Every terminal binding belongs to the project it was made in.
Switching projects only changes what the page reads next, so a send, stop or request reply that is already under way still reaches the exact session it was started for.
Drafts are kept per project, run, role and session generation.

## Use

1. Choose a project and a run in the header.
   The sidebar lists the run's roles with plain names, one state word (**Live**, **Working**, **Needs you**, **Starting**, **Not found**, **Needs link**, **Ambiguous** or **Saved only**) and a shape, never a UUID.
   Each role has one current session.
   An implementor is the registration holding the run's active dispatches, several only when several run at once, otherwise the one holding the latest dispatch, which reads "last task T04".
   Registrations that record the same native session with agreeing processes, or whose dispatch recorded the same pid:start, are one session.
   Earlier tasks and registrations with no dispatch and no proof sit in collapsed **Earlier tasks** and **Unattributed** groups.
   Earlier sessions are read-only and can never control a reused pane.
2. **Details** under the role name holds the exact identity: session name, native session ID, pid:start, pane, how it is attributed, every role Docket recorded for that native session, and its registrations.
   **Conversation** and **Activity** show the role's saved native messages and tool records as inert Markdown: a link reads as its label followed by the literal URL, and a private-URL token is hidden in rendered text.
   Exact session IDs come from Docket registrations and the run's noted harness ledger.
   Conflicting IDs are flagged, and the companion never picks the newest provider session or merges subagent conversations.
   **Change conversation** in Details chooses which saved conversation to read.
   It lists this run's sessions only; **Show all project sessions** is a separate, explicit step.
   Reading a saved conversation grants no input to anything.
3. The latest 200 saved events are kept and shown 40 at a time.
   **Load earlier** and **Newer** move through that window, **Jump to latest** returns, and the footer count says exactly which events you see.
   Events older than the window are not reachable here, and the page says so instead of implying a full transcript.
   A loading note, an empty state and a specific unavailable reason are shown while history is read, missing or refused.
4. **Docs** opens the run's documents and discussions beside the chat.
   The choice is remembered on wide screens, and a narrow screen always starts with documents closed and opens them as an overlay.
   **Reading mode** gives the document the whole workspace at a readable line length, and Escape or **Back to chat** returns.
   The picker is grouped as the plan, then each task with its latest decision and report first and earlier rounds after them, and a task chip opens that task's report in reading mode.
   **Rendered** is a small safe subset (headings, lists, tables, quotes, code, bold and code spans) built from text only.
   No HTML, link, image or URL in a document is ever interpreted.
   Frontmatter is a collapsed **Properties** row, and **Source** shows the exact bytes.
5. A role connects to its exact terminal automatically only when a native session ID or a process identity proves it, and the page retries on every refresh until one is proven.
   When a role is not **Live**, one strip under its name gives the exact reason and the one recovery action: **Link a session**, **Refresh**, **Retry**, **Choose terminal** or **Open terminal**.
   Otherwise open **Live terminal**, choose an existing terminal in this project, and **Bind** it; a manually bound terminal can be watched and controlled but has no chat.
   Titles and recency never prove identity.
   When a role has a replaced session, the page says so, and the previous conversation, draft and binding stay with the old session.
6. The composer always accepts a draft for a current role, and beside **Send** it states why chat is or is not ready, with or without a bound terminal.
   **Send** (Ctrl+Enter) delivers the text to the bound agent only when its session is proven, its state is idle or done, and it is past its start-up window.
   The result is shown as sent, failed or uncertain.
   An uncertain delivery is never resent, and an unsent draft is always kept.
   **Stop** is hidden because no supported harness has a known-safe interrupt; it appears only if a harness is later proven to have one.
   An earlier session shows a single read-only line instead of a composer.
7. Terminals open **Read only**.
   **Take control** types into the real terminal.
   Another controller is never displaced implicitly: contention offers **Take over** as a separate action.
   A lost browser connection reconnects read-only, and a changed occupant or session registration needs a new bind.
   Use the history buttons or wheel to scroll Herdr's viewport, and **Paste text** to review multiline input before it is sent.
8. Question and permission cards appear above the composer when a provider exposes a verified endpoint.
   See the section below for exactly which.

The keyboard works throughout.
The view and document tabs are arrow-key tablists with one tab stop, every control shows a focus ring, and request cards are labelled groups.

## Exact session connection and recovery

- **Link a session** repairs a role whose own record has no usable identity, for example a planner that was ledgered under another role.
  Choose one of this run's saved sessions and confirm.
  It works only when that session's exact native ID and the pid:start recorded beside it prove exactly one live terminal that runs the same harness and reports no different native session, and the terminal is not another role's session.
  Otherwise the page says which proof is missing and links nothing.
  The link lives only in this companion (`$XDG_STATE_HOME/docket-ui/associations.json`), writes nothing into Docket, and the role then reads "linked by you" while the recorded role stays in Details.
  A saved link is a preference, not authority: it is proved again on every state read, and it grants nothing once the process, the registration generation, the session or the role's own record changes.
  Binding generations change with a link, so earlier bindings and operations cannot move onto it, and the draft you were typing stays with the role.
  **Remove link** in Details undoes it.
  Codex sessions run in an app server whose recorded process is not the terminal, so they usually cannot be proven and stay **Not found** until a native ID is reported.
- A binding pins the Herdr socket instance, the terminal, the detected foreground agent process, the native session when reported, the Docket registration generation and the project.
  Linux process start ticks protect against PID reuse.
  Herdr has no atomic identity-check-and-input call, so a small replacement race remains and is narrowed by validating just before each input.
- Terminal occupant changed, session replaced, or project removed: bind the role's terminal again.
- Delivery uncertain: look at the live terminal before sending again, because the companion will not resend by itself.
- **Herdr offline**: documents and saved history still work.
  Start Herdr or fix `--socket`, then refresh.
- A browser read that is aborted by a network change is retried once quietly, and an outage notice clears itself when state can be read again.
- Token rejected: open the URL the companion printed most recently.
- A role that reports `endpoint-absent` was not started in a way that exposes requests: use its live terminal.

## Saved history sources

- OpenCode: an exact session in the local SQLite database, opened read-only with
  a consistent transaction. The stored session directory must match the project.
  Its direct child sessions, the subagents the role started, are read in the same
  transaction and their events are marked **subagent**.
- Codex: one exact rollout JSONL in the configured provider directory. The session
  header must prove its ID and project directory. Mirrored UI events and internal
  reasoning records are omitted.
- Claude Code: one exact main-session JSONL, with a matching session ID and project
  directory, plus the session's own subagent files: each must name the same session
  as a sidechain in this project, and their events, merged by time and marked
  **subagent**, share the same byte budget. Thinking records are omitted. Slash
  commands the user typed (`/model`, `/clear`) appear as activity rows, not as user
  messages, and Claude Code's local-command caveat is dropped.

Under Docket's `round_context: fresh` a role does each round in a subagent, so its
work appears there while the main session shows the hand-off. A tool row shows the
call's description, command, or path, or the first line of a result; open the row for
the full saved text, and hover it for the time.

The view refreshes every five seconds. Reads are bounded to recent records, at
most 200 displayed events (paged 40 at a time) and 12,000 characters per event. Large histories may
be incomplete or shortened; this is a saved snapshot, not an authoritative live
request stream. A tool's saved `running` status does not grant permission authority.
Older provider formats, missing transcripts, ambiguous paths, and mismatched
project directories produce an explicit unavailable state.

## Questions and permissions

Cards for pending requests come only from a live process endpoint owned by the role's proven terminal process.
Nothing is read from the screen, from saved transcripts, or from a tool that was saved as running.
Each state response reads the endpoint fresh, so a failed or stale read grants nothing, and the browser keeps actions disabled while its own state read is failing.
A reply is pinned to the binding, the request ID and the card's revision, and the companion re-reads the endpoint before posting once.
A request that was resolved elsewhere, replaced, or answered through another role's binding is reported as stale and nothing is sent.
A reply that cannot be confirmed is reported as uncertain and is never resent.

Default-launched OpenCode TUIs, which are all of the user's current ones, report `endpoint-absent`.
Request cards need an OpenCode TUI started with `--port <free port>`.
The companion never starts, restarts or proxies a TUI for this.

Per-harness capability, observed on 2026-10-08 with no model turn.
Evidence files are under `.docket/t03-native/`.

OpenCode 1.18.35:
- Observed natively, working: a TUI started with `--port` listens on 127.0.0.1 from its own process.
  A permission created through the provider's v2 route `POST /api/session/{id}/permission` (action `external_directory`, which asks) appears in that route's pending list, is listed by the companion as exactly one card with the provider session, request id and revision, and is resolved once through `POST /api/request/reply`.
  A second reply is a duplicate and posts nothing, the provider then answers 404 for that request, and the occupant pid:start is unchanged.
  Three consecutive runs agree (`native-permission.json`, `-b`, `-c`; logs `request-probe-round2*.log`).
- Observed natively, limits: the same request does not appear in the legacy `GET /permission` list, which stayed empty.
  The TUI pane did not change when the request was created, so the TUI does not draw requests made through this route.
  The first permission created on a fresh server was denied outright (effect `deny`); later ones ask.
  A session id the server does not hold answers 404, reported as `session-unknown`.
- Observed absent: a default TUI holds no listening socket and no socket descriptors (five running user TUIs checked, plus a disposable one), reported as `endpoint-absent`.
- Not attempted, so not offered by default: questions (there is no creating route, so they need a model turn), the legacy permission list, and the route the TUI's own model-triggered permission prompts use.
  A model-triggered permission may therefore still show only the terminal fallback.
  The code to read them exists and is tested against a loopback fake, but it stays off until a real request is observed.

Codex 0.160.1:
- Observed absent: no supported way to answer a TUI-owned request from another client was found (offline protocol schema: approvals are server requests addressed to the owning connection).
- Not attempted: connecting to the user's app-server daemon, because that would touch other sessions.
- Companion state: `unsupported` with terminal fallback.

Claude Code 2.1.294:
- Observed absent: the interactive TUI exposes no external request endpoint; permission prompt routing exists only with `--print`, which is headless.
- Companion state: `unsupported` with terminal fallback.

Other safeguards: an endpoint that answers 401 or 403 reports `auth-failed`, because no credential is read or guessed.
Several listening ports, a non-loopback listener, an unhealthy answer, a malformed list or contradictory duplicates leave the role on terminal fallback.
A live endpoint with no cards on a blocked agent shows a note and an Open terminal button, never a guessed card.


## Unsupported and limits

- Codex and Claude Code questions and permissions are not offered as cards, and Stop is hidden because no harness offers it.
  Use the live terminal for them.
- Herdr 0.8.2 supplies screen projections rather than semantic conversations.
  Application input modes cannot be inferred from them.
  Raw keyboard and deliberate paste are supported.
  Terminal mouse applications, graphics and image clipboard are not.
- A projected screen and Herdr history are not a complete transcript, and local xterm scrollback is disabled.
- State badges are detection hints, and `unknown` stays unknown.
- The document endpoint exposes only public run `.mdx` files, supporting `docs/` Markdown and public discussions.
  Captures, object stores, delivery records and raw transcripts are excluded.
  Documents over 1 MiB are refused rather than truncated.
- Local actions are authenticated, accept only the local origin, and expose no arbitrary path, command or endpoint.
- No command here submits, approves, launches or changes Docket lifecycle state.

## Checks

```sh
npm test
npx playwright install chromium
npm run test:browser
```

`npm test` runs the Python inspector and history tests and every Node test: provider history formats, exact session isolation, the project registry and its validation, project-scoped routes, binding pinning, the send and stop transport against a real subprocess, request adapters against a real loopback HTTP server and real process discovery, and the inert Markdown renderer.

`npm run test:browser` needs Herdr and Chromium.
Two suites run on a named disposable Herdr server with its own temporary config, state and workspaces and ordinary shells, and remove only that server afterwards.
No agent runs and no provider is called.
`browser-smoke.mjs` covers terminals, drafts, history, role isolation, contention, takeover, reconnect with a preserved shell PID, inert documents and request-card behavior.
`browser-acceptance.mjs` covers adding and switching projects without side effects, a pending send that keeps its project, exact Unicode and multiline bytes through the real Herdr adapter, a provider-held request answered once, history paging, inert Markdown, keyboard operation, focus rings, measured contrast and a narrow viewport.
It also drives a real-shaped legacy run (a planner with no identity, a misrecorded session, duplicate and stray registrations): run filtering, the confirmed link and its delivery to the proven pane, invalidation of a stored link, inert chat Markdown, reading mode and the narrow documents overlay.

`node tests/real-workspace-check.mjs` opens every run of this repository's own `.docket` in Chromium against a read-only second companion and checks role reachability at 1500x960 and 390px, placeholder text and frontmatter wrapping.
It needs the local `.docket`, so it is explicit-run only.

Two scripts record native acceptance and are never part of a gate.
They use disposable sessions only and remove them afterwards.
`node tests/live-agent-probe.mjs` exercises the send transport against real OpenCode TUIs and spends free-model turns.
`node tests/live-browser-acceptance.mjs` drives the browser against a disposable OpenCode TUI started with `--port` (one real pending permission, no model turn) and one disposable Claude Code TUI that receives one short message.
`node tests/live-request-probe.mjs` records the request endpoint behavior without a model turn.

From the repository root, also run the CLI regression suite:

```sh
tests/test.sh
```

## Design preview

A separate static [chat design preview](../docs/frontend-chat-design.md) uses sample data and simulated actions.
Run `npm run design` and open <http://127.0.0.1:4320/frontend-design/>.
It does not connect to or start any harness, and none of its actions exist in this companion.
`npm run test:design` checks its navigation, drafts and mobile layout.
