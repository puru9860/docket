#!/usr/bin/env bash
# Stop-hook watcher arm for the docket pipeline.
#
# Wire this into .claude/settings.json as a Stop hook with "asyncRewake": true and
# a long timeout (see settings.json.example). Claude Code runs it in the background
# on every Stop; on exit 2 it wakes the session even while idle and shows stderr as
# a system reminder. Only a real wake exits 2; a launcher or argument failure exits
# 1 and is recorded in .docket/hook-failure-<role>, which `docket doctor` reports.
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
FAILURE="$ROOT/.docket/hook-failure-$DOCKET_ROLE"

# The shebang's uv launcher when uv is installed, plain python3 otherwise.
if command -v uv >/dev/null 2>&1; then
  launch=("$DOCKET")
else
  launch=(python3 "$DOCKET")
fi

# A sandbox may refuse the temp directory; .docket is known to be writable here.
ERR="$(mktemp "${TMPDIR:-/tmp}/docket-wake.XXXXXX" 2>/dev/null)" \
  || ERR="$ROOT/.docket/.wake-$DOCKET_ROLE.$$"
trap 'rm -f "$ERR"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Watch only this session's role. The CLI atomically claims matching events.
# Exit 2 is the harness's wake code, and uv and argparse also exit 2 on their own
# errors, so a broken launcher used to wake the session on every Stop with the
# error as its prompt. In a hook the watcher reports a wake as 3; only 3 becomes
# 2 here, and any other failure is recorded for `docket doctor` and never wakes.
# The watch timeout stays below the harness's 28800s hook timeout, so the watcher
# ends on its own instead of racing the harness's kill.
DOCKET_WATCH_HOOK=1 "${launch[@]}" watch --armed --role "$DOCKET_ROLE" \
  --timeout "${DOCKET_WATCH_TIMEOUT:-28740}" 2>"$ERR"
code=$?
case "$code" in
  3) rm -f "$FAILURE"; cat "$ERR" >&2; exit 2 ;;
  0) rm -f "$FAILURE"; exit 0 ;;
esac
{
  printf 'at: %s\nexit: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$code"
  tail -n 5 "$ERR"
} >"$FAILURE" 2>/dev/null
printf 'docket wake hook failed (exit %s); see %s\n' "$code" "$FAILURE" >&2
exit 1
