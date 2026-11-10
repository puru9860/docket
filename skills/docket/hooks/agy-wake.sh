#!/usr/bin/env bash
# Antigravity Stop-hook adapter for wake.sh.
#
# Wire it into ~/.gemini/config/hooks.json (see agy-hooks.json.example). Antigravity
# runs a Stop hook synchronously in the folder holding hooks.json, passes the session
# as JSON on stdin, and reads one JSON object from stdout: {"decision": "continue",
# "reason": ...} re-enters the loop with the reason as a system message.
#
# The project is the payload's first workspace path, never this hook's cwd. wake.sh
# keeps its own exit contract: only its exit 2 is a wake; anything else stops quietly
# and wake.sh has already recorded a failure for `docket doctor`.
set -u

payload="$(cat)"
workspace="$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    paths = json.load(sys.stdin).get("workspacePaths")
except (ValueError, AttributeError):
    paths = None
print(paths[0] if isinstance(paths, list) and paths and isinstance(paths[0], str) else "")
')"
if [ -z "$workspace" ] || ! cd "$workspace" 2>/dev/null; then
  echo '{}'
  exit 0
fi

# An agy started from another harness's shell inherits its project variable.
banner="$(env -u CLAUDE_PROJECT_DIR bash "$(dirname "${BASH_SOURCE[0]}")/wake.sh" \
  2>&1 >/dev/null </dev/null)"
code=$?
if [ "$code" = 2 ]; then
  printf '%s' "$banner" | python3 -c '
import json, sys
print(json.dumps({"decision": "continue", "reason": sys.stdin.read()}))
'
  exit 0
fi
[ -n "$banner" ] && printf '%s\n' "$banner" >&2
echo '{}'
