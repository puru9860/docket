# Interactive Docket companion

This extends the approved chat design and existing UI, authorized by the user's
2026-10-08 request to leave the POC and implement the full phase using Docket quick.

The planner is the current Codex session. One normal OpenCode TUI implements the
work. An independent Claude Code TUI reviews with `claude-opus-5-5` at medium.
The run is `ui-full-20261008`; each milestone requires captured full-suite results
and reviewer approval before the next begins.

## Outcome

A locally installed companion that selects explicitly added project directories,
finds proven role sessions, displays their conversations, sends to those same
interactive harnesses, and exposes supported live requests and terminal fallback.
Keep the approved slate/blue layout, one current implementor, collapsed earlier
tasks, and separate drafts and pending operations for every native generation.

## Milestones

1. **Identity and capabilities.** Establish a shared physical-session identity for
   history, live transport, drafts and actions. Automatically connect unambiguous
   recorded sessions to exact live agents using native identity or proven process
   evidence. Conflicts and missing role attribution need explicit recovery; titles
   and recency never prove identity. Persist explicit mappings as companion
   preferences, with provenance, without rewriting Docket registration files.
2. **Conversation input.** Deliver text from Conversation to the pinned existing
   interactive session. Prefer a verified native endpoint where available; support
   Herdr's exact-agent prompt transport for ready interactive agents when it can
   prove the destination. Validate identity and readiness immediately before send,
   protect against duplicate retries, retain uncertain delivery as uncertain, and
   never silently clear or retarget drafts. Expose interrupt only where supported.
3. **Requests and recovery.** Implement provider adapters with independent verified
   capabilities. Show authoritative question and permission cards only with live
   request IDs, revisions and offered choices. Expire resolved or replaced requests;
   reconnect reconciles before granting authority. Investigate installed OpenCode,
   Codex and Claude interfaces without replacing their TUI engines. Unsupported
   actions show a specific reason and exact-terminal fallback. Native-provider
   experiments and acceptance gaps must be recorded, not hidden by fixtures.
4. **Product integration.** Add an explicit project list, usable inert Markdown
   context, bounded history navigation and activity states, accessible responsive
   controls, installation/startup documentation and end-to-end acceptance. Verify
   role isolation, pane reuse, contention, reconnect, requests and input in an
   isolated Herdr server, then provide the updated local live companion.

## Constraints and escalation

- Harnesses stay in default interactive TUI/CLI mode. No headless launch, SDK
  engine, hidden replacement session, or restart of existing user agents.
- The installed Docket CLI remains stdlib-only. Optional dependencies stay in UI.
- Read-only inspection never consumes events or changes Docket lifecycle. Decisions
  on tool requests never become Docket task approvals.
- Preserve pre-existing uncommitted files, declared roots, baselines and bundles.
  No commit, stash, push, skill sync, server upgrade or user-agent interruption.
- Authenticate local actions; never expose arbitrary paths, commands or endpoints.
- No actionable cards synthesized from terminal output or saved transcript state.
- Request an intent amendment if an essential capability cannot be supplied while
  retaining the interactive harness, or if the work needs additional role sessions.
  A fallback must not be described as implementing an unsupported native feature.

## Verification

Each task adds assertions for the behavior it changes, including a before/after
failure for a bug fix. Focused companion tests belong in task gates. Every milestone
runs `tests/test.sh`, companion tests, and isolated browser acceptance through its
declared batch command. The reviewer reads the frozen changes and evidence, and
casts the milestone verdict independently. A failed check is corrected or honestly
blocked; no verification skips or weakened assertions.
