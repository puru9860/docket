#!/usr/bin/env bash
# Stop-hook watcher arm for the docket pipeline.
#
# Wire this into .claude/settings.json as a Stop hook with "asyncRewake": true and
# a long timeout (see settings.json.example). Claude Code runs it in the background
# on every Stop; on exit 2 it wakes the session even while idle and shows stderr as
# a system reminder.
#
# It runs `docket watch` in the FOREGROUND of this hook's own process tree, never
# with shell '&'. Claude owns the process group, so the hook's timeout and session
# teardown kill the watcher along with it. Backgrounding it here would orphan the
# watcher and break that guarantee.
#
# This arm-a-foreground-watcher-from-Stop pattern, including the never-background rule
# above and the exactly-once event ledger in `docket watch`, is taken from firstmate's
# event-driven supervision: https://github.com/kunchenguid/firstmate (Kun Chen, MIT).
# No code copied; see that project for the full-featured version.
#
# Inert unless .docket/watch.conf exists, so an idle project costs nothing.
# Arm:    docket arm R01 --role orchestrator     (repeat per waiting role)
# Disarm: docket disarm R01
set -u

ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
CONF="$ROOT/.docket/watch.conf"

[ -f "$CONF" ] || exit 0
cd "$ROOT" || exit 0

DOCKET="$(cd "$(dirname "${BASH_SOURCE[0]}")/../bin" && pwd)/docket"

# Watch EVERY armed (run, role) pair, not just the first line. Each role has its own
# ledger, so an orchestrator and a planner watching the same run never consume each
# other's events. Set DOCKET_ROLE to filter this session to one role and avoid being
# woken for another role's work.
if [ -n "${DOCKET_ROLE:-}" ]; then
  exec "$DOCKET" watch --armed --role "$DOCKET_ROLE" --timeout "${DOCKET_WATCH_TIMEOUT:-28800}"
fi
exec "$DOCKET" watch --armed --timeout "${DOCKET_WATCH_TIMEOUT:-28800}"
