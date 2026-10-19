# Docket UI implementation plan

The 2026-10-08 [chat UI design](frontend-chat-design.md) supersedes this plan's
terminal-first product direction. The live companion now implements the approved
layout with saved provider history and an exact-terminal fallback. Structured live
chat sending and request decisions remain unavailable.

## Approved history and terminal implementation

1. Reuse the authenticated local companion and existing Herdr bridge. Keep provider
   processes running in their normal interactive TUI/CLI.
2. Read exact session identities from project-scoped Docket registrations and the
   noted harness ledger. Preserve their provenance, flag conflicting role/native
   mappings, and avoid any newest-session discovery.
3. Normalize saved OpenCode SQLite, Codex rollout JSONL, and Claude main-session
   JSONL into readable messages and expandable saved tool records. Verify provider
   identity and project directory, bound reads, and label incomplete history.
4. Apply the approved slate/blue layout, use one current implementor with collapsed
   released task history, and keep local drafts separate per session generation.
5. Keep Send and structured request actions disabled. Provide a live terminal tab
   for explicit binding, observation, and optional keyboard control. Returning to
   the conversation releases control; earlier sessions never bind a reused pane.
6. Test exact session isolation, history races, generation replacement, confinement,
   inert provider content, real browser terminal transport, and the CLI regressions.

Implemented in `ui/inspection.py`, `ui/provider_history.py`, the local server, and
the browser assets. The installed Docket skill has no new dependency or behavior.
Real OpenCode saved history and read-only attachment to the existing reviewer TUI
in `Documents/Projects/test` were verified without launching or prompting an agent.
Provider format fixtures cover Codex and Claude; their live permission interaction
remains unverified.

## Outcome

Build the browser spike recommended by [the feasibility study](frontend-feasibility.md):
select a run, explicitly bind its roles to existing Herdr terminals, switch between
them in one terminal panel, and inspect Docket documents beside the session.
The first deliverable is a local proof of concept, not a replacement agent harness.

## Architecture and boundaries

- Put the optional companion in `ui/`. Node.js serves local assets and bridges a
  browser WebSocket to `herdr terminal session`. Use xterm.js for ANSI rendering
  and `ws` for the server transport. Dependencies belong only to the companion;
  the installed skill, launcher, and stdlib-only CLI retain their current layout.
- Use a small read-only Python inspector to reuse Docket's flat frontmatter parser,
  numeric artifact ordering, and preset policy decoder. Read plans, tasks, reports,
  decisions, findings, and discussion documents directly. Do not call commands that
  register sessions, acknowledge events, or alter lifecycle state.
- Discover terminals using `session.snapshot` on one explicitly selected local
  Herdr socket. Filter candidates to the selected project. Bind a role or worker
  session only after the user selects a candidate. Store bindings in the browser
  session, never in Docket lifecycle files, and discard them when identities move.
- Start in observer mode. A separate Take control action acquires the sole writable
  stream. Contention returns to observation with an explicit Take over action.
  Switching roles, navigating away, and transport loss release the bridge process,
  preserving the underlying shell and agent. Navigation never focuses Herdr panes.
- Pin server socket identity, terminal ID, shell process identity, agent/native
  occupant identity when available, and Docket session generation. Revalidate on
  attachment and before input; periodically invalidate replaced occupants. This is
  best-effort local identity checking because Herdr offers no atomic compare-and-input
  primitive. Bindings without native telemetry must not claim verified attribution.
- Preserve frame order, require a full first frame, and reconnect after gaps,
  malformed output, EOF, or bounded-buffer overflow. Reset the emulator on a new
  stream. Use Herdr scroll commands for history; screen blits are not transcripts.
- Listen on `127.0.0.1`, issue a random per-launch token in the startup URL fragment,
  and authenticate every data request and WebSocket. Require the exact local Host
  and WebSocket Origin. Serve an allowlist of assets and document paths; never offer
  command execution or arbitrary filesystem endpoints.

## Implementation sequence

1. Implement and test the read-only inspector, document allowlist, and actual-role
   inventory, including quick/combined policies and multiple implementor sessions.
2. Implement the authenticated local server and Herdr bridge. Cover stream ownership,
   sequence checks, disconnect cleanup, identity changes, and stale-switch input.
3. Build a responsive sidebar, run/task overview, selected-role terminal, manual
   binding picker, ownership controls, and document/discussion drawer. Include clear
   empty, offline, disconnected, and replacement states.
4. Exercise the UI in Chromium and a disposable Herdr server with ordinary shell
   terminals. Validate Unicode, interactive questions, keyboard menus, role switching,
   resize, server scroll, reconnect, control contention, takeover, and preserved PIDs.
5. Document startup, dependency installation, limitations, and remaining acceptance
   checks. Run companion tests and `tests/test.sh` before finishing.

## Acceptance and later work

The POC passes when a browser renders and interacts with existing disposable
terminals, switching does not send delayed input to another role, another controller
is never silently displaced, inspection writes no Docket state, and replacement or
lost transport disables input. An absent Herdr server must leave documents usable.

Native Claude Code, Codex, and OpenCode permission menus, concurrent Herdr TUI sizing,
mode-aware multiline paste, image clipboard, graphics, complete transcripts, durable
launch-time role bindings, and event-driven inventory subscriptions remain follow-up
work. The POC uses bounded periodic backend snapshots instead of claiming event replay.
Multiline paste must be previewed and explicitly sent because projection does not prove
the application's bracketed-paste mode. The UI offers no approval or submit shortcuts.

Implementation references: [Herdr terminal bridge](https://herdr.dev/docs/persistence-remote/#direct-terminal-attach),
[Herdr socket API](https://herdr.dev/docs/socket-api/), and
[xterm.js terminal API](https://xtermjs.org/docs/api/terminal/classes/terminal/).

## Original terminal POC validation

Implemented in `ui/`. The companion's six inspection tests and eight bridge/server
tests pass. Chromium checks passed against an isolated Herdr 0.8.2 server: rendered
ANSI frames, Unicode question/answer, alternate-screen keyboard selection, Ctrl+C,
PTY resize, server history, previewed multiline paste, role switching, contention,
explicit takeover, manual and automatic read-only reconnect with preserved PID,
inert document markup, discussion inspection, and mobile navigation. Docket's
`tests/test.sh` passed all 552 tests. Native harness and concurrent-TUI checks remain
pending as listed above. Startup and test instructions are in [ui/README.md](../ui/README.md).

## History and terminal validation

The updated companion passes 20 Python inspection/history/PTY tests and 12 Node server
tests. Chromium exercises saved provider JSONL through the real history reader,
inert message/tool content, periodic refresh, isolated drafts, delayed response
isolation, replacement generations, collapsed earlier task history, and release
of control on leaving the terminal. All original isolated Herdr terminal checks
continue to pass. `tests/test.sh` passes all 552 CLI tests.

The missing-planner regression is covered by an explicit history-link test that
keeps the misrecorded role intact, checks project confinement, and refuses historical
task relinking. Browser checks cover the picker, provenance, draft isolation,
reload persistence, and full native-pane observer sizing with independent scrolling.
On Linux observer sizing reads the live shell's PTY dimensions after checking its
process start identity. This avoids trusting stale split geometry after another
client resized the terminal. The PTY test verifies that reading dimensions sends
no input and leaves the size unchanged.
