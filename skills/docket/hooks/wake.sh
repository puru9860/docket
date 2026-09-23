#!/usr/bin/env bash
# Stop-hook watcher arm for the docket pipeline.
#
# Wire this into .claude/settings.json as a Stop hook with "asyncRewake": true and
# a long timeout (see settings.json.example). Claude Code runs it in the background
# on every Stop; on exit 2 it wakes the session even while idle and shows stderr as
# a system reminder.
#
# Codex runs the same script as a synchronous Stop hook from ~/.codex/hooks.json
# (see codex-hooks.json.example; trust it once in /hooks). Exit 2 blocks the stop
# and hands stderr, the wake banner, to the session as its next prompt.
#
# It runs `docket watch` in the FOREGROUND of this hook's own process tree, never
# with shell '&'. Claude owns the process group, so the hook's timeout and session
# teardown kill the watcher along with it. Backgrounding it here would orphan the
# watcher and break that guarantee.
#
# This arm-a-foreground-watcher-from-Stop pattern, including the never-background rule
# above and the per-role announcement ledger in `docket watch`, is taken from firstmate's
# event-driven supervision: https://github.com/kunchenguid/firstmate (Kun Chen, MIT).
# No code copied; see that project for the full-featured version.
# Delivery is at-least-once with a bounded announcement lease: a crash after the
# ledger write but before the harness accepts the wake re-announces the same
# actionable event after lease expiry, and duplicates are harmless.
#
# Inert unless .docket/watch.conf and DOCKET_ROLE exist, so an idle or unscoped
# session costs nothing and cannot consume another supervisor's event.
# Arm:    docket arm R01 --role orchestrator     (repeat per waiting role)
# Disarm: docket disarm R01
set -u

ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
CONF="$ROOT/.docket/watch.conf"

[ -f "$CONF" ] || exit 0
[ -n "${DOCKET_ROLE:-}" ] || exit 0
case "$DOCKET_ROLE" in
  planner|orchestrator|verifier|reviewer|coordinator|checker) ;;
  *) exit 0 ;;
esac
cd "$ROOT" || exit 0

DOCKET="$(cd "$(dirname "${BASH_SOURCE[0]}")/../bin" && pwd)/docket"

# Watch only this session's role. The CLI atomically claims matching events.
DOCKET_WATCH_HOOK=1 exec "$DOCKET" watch --armed --role "$DOCKET_ROLE" \
  --timeout "${DOCKET_WATCH_TIMEOUT:-28800}"
