#!/usr/bin/env bash
# Regression suite for docket. Run: tests/test.sh
#
# Every test runs in a throwaway directory. Nothing touches a real run.
# Add a test whenever you fix a bug - the planner-wake bug shipped because the
# planner signal path was never exercised.
set -u

DOCKET="$(cd "$(dirname "${BASH_SOURCE[0]}")/../skills/docket" && pwd)"
BIN="$DOCKET/bin/docket"
HOOK="$DOCKET/hooks/wake.sh"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); printf '  \033[32mok\033[0m   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; }
is()   { [ "$2" = "$3" ] && ok "$1" || bad "$1" "expected '$3', got '$2'"; }
has()  { grep -q "$3" <<<"$2" && ok "$1" || bad "$1" "output lacked '$3'"; }
lacks(){ grep -q "$3" <<<"$2" && bad "$1" "output unexpectedly had '$3'" || ok "$1"; }

sandbox() { T=$(mktemp -d); mkdir -p "$T/.docket"; cd "$T" || exit 1; }
cleanup() { cd /; rm -rf "$T"; }

# Fill a report skeleton so it passes the gate.
fill() {
  python3 - "$1" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
s = re.sub(r"<!--.*?-->", "", p.read_text(), flags=re.S)
s = s.replace("## Summary\n", "## Summary\nDid the work.\n")
s = s.replace("## Files changed\n", "## Files changed\n- `a.py:1` change\n")
s = re.sub(r"- \[ \]\s*\n", "- [x] criterion met\n", s)
s = s.replace("## Verification\n", "## Verification\nRan `true`: exit 0.\n")
p.write_text(s)
PY
}

echo; echo "scaffolding"
sandbox
$BIN init R01 >/dev/null 2>&1
is "init creates plan.mdx" "$([ -f .docket/runs/R01/plan.mdx ] && echo y)" "y"
$BIN init R01 >/dev/null 2>&1; is "init refuses an existing run" "$?" "1"
$BIN assign R01 T01 --tier small --verify "true" >/dev/null 2>&1
is "assign creates task"   "$([ -f .docket/runs/R01/T01-task.mdx ] && echo y)" "y"
is "assign creates report" "$([ -f .docket/runs/R01/T01-report-01.mdx ] && echo y)" "y"
cleanup

echo; echo "the gate"
sandbox
$BIN init R01 >/dev/null; $BIN assign R01 T01 --verify "true" >/dev/null
OUT=$($BIN submit R01 T01 2>&1); is "rejects an untouched skeleton" "$?" "1"
has "names empty sections"      "$OUT" "is empty"
has "names placeholders"        "$OUT" "unresolved placeholder"
has "names unchecked criteria"  "$OUT" "unchecked acceptance"
fill .docket/runs/R01/T01-report-01.mdx
OUT=$($BIN submit R01 T01 2>&1); is "accepts a complete report" "$?" "0"
has "reports submitted" "$OUT" "submitted for review"
OUT=$($BIN submit R01 T01 2>&1); is "refuses to re-submit" "$?" "1"
cleanup

sandbox
$BIN init R01 >/dev/null; $BIN assign R01 T01 --verify "exit 7" >/dev/null
fill .docket/runs/R01/T01-report-01.mdx
OUT=$($BIN submit R01 T01 2>&1); is "rejects a failing verify" "$?" "1"
has "names the exit code" "$OUT" "exit 7"
cleanup

sandbox   # verify must work in /bin/sh, not the caller's interactive shell
$BIN init R01 >/dev/null; $BIN assign R01 T01 --verify "definitely_not_a_real_binary_xyz" >/dev/null
fill .docket/runs/R01/T01-report-01.mdx
OUT=$($BIN submit R01 T01 2>&1); is "rejects a verify absent from /bin/sh" "$?" "1"
cleanup

echo; echo "blocked escape hatch"
sandbox
$BIN init R01 >/dev/null; $BIN assign R01 T01 >/dev/null
R=.docket/runs/R01/T01-report-01.mdx
fill $R; sed -i 's/^status: draft/status: blocked/' $R
sed -i 's/^none$/none/' $R
OUT=$($BIN submit R01 T01 2>&1); is "rejects blocked with no question" "$?" "1"
has "explains why" "$OUT" "Decisions needed"
python3 - "$R" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
s = re.sub(r"(## Decisions needed\s*\n)\s*none\s*\n",
           r"\1\nWhich interface is canonical?\n", p.read_text())
p.write_text(s)
PY
OUT=$($BIN submit R01 T01 2>&1); is "accepts blocked with a question" "$?" "0"
has "marks it blocked" "$OUT" "blocked, needs a decision"
cleanup

echo; echo "review rounds"
sandbox
$BIN init R01 >/dev/null; $BIN assign R01 T01 >/dev/null
fill .docket/runs/R01/T01-report-01.mdx; $BIN submit R01 T01 >/dev/null
OUT=$($BIN decide R01 T01 --changes 2>&1); is "decide --changes succeeds" "$?" "0"
is "writes decision-01"  "$([ -f .docket/runs/R01/T01-decision-01.mdx ] && echo y)" "y"
is "opens report-02"     "$([ -f .docket/runs/R01/T01-report-02.mdx ] && echo y)" "y"
fill .docket/runs/R01/T01-report-02.mdx; $BIN submit R01 T01 >/dev/null
$BIN decide R01 T01 --approve >/dev/null 2>&1; is "decide --approve succeeds" "$?" "0"
has "status shows approved" "$($BIN status R01 2>&1)" "approved"
$BIN decide R01 T01 --approve >/dev/null 2>&1; is "refuses to re-decide an approved report" "$?" "1"
cleanup

echo; echo "arming"
sandbox
$BIN init R01 >/dev/null; $BIN assign R01 T01 >/dev/null
$BIN arm R01 --role orchestrator >/dev/null; $BIN arm R01 --role planner >/dev/null
is "watch.conf holds both roles" "$(wc -l < .docket/watch.conf)" "2"
$BIN arm R01 --role planner >/dev/null
is "arm is idempotent" "$(wc -l < .docket/watch.conf)" "2"
$BIN disarm R01 --role planner >/dev/null
is "disarm removes one role" "$(wc -l < .docket/watch.conf)" "1"
$BIN disarm R01 >/dev/null
is "disarm all removes watch.conf" "$([ -f .docket/watch.conf ] || echo gone)" "gone"
cleanup

echo; echo "signalling  (both directions - the planner path is the regression)"
sandbox
$BIN init R01 >/dev/null; $BIN assign R01 T01 >/dev/null; $BIN assign R01 orch >/dev/null
$BIN arm R01 --role orchestrator >/dev/null; $BIN arm R01 --role planner >/dev/null

OUT=$(CLAUDE_PROJECT_DIR=$T DOCKET_WATCH_TIMEOUT=1 $HOOK 2>&1); is "hook exits 0 when nothing is pending" "$?" "0"

fill .docket/runs/R01/T01-report-01.mdx; $BIN submit R01 T01 >/dev/null
OUT=$(CLAUDE_PROJECT_DIR=$T DOCKET_WATCH_TIMEOUT=1 $HOOK 2>&1); is "implementor submit wakes (exit 2)" "$?" "2"
has "wake names the orchestrator role" "$OUT" "\[orchestrator\]"

OUT=$(CLAUDE_PROJECT_DIR=$T DOCKET_WATCH_TIMEOUT=1 $HOOK 2>&1); is "same event does not wake twice" "$?" "0"

fill .docket/runs/R01/orch-report-01.mdx; $BIN submit R01 orch >/dev/null
OUT=$(CLAUDE_PROJECT_DIR=$T DOCKET_WATCH_TIMEOUT=1 $HOOK 2>&1); is "orchestrator submit wakes PLANNER (exit 2)" "$?" "2"
has "wake names the planner role" "$OUT" "\[planner\]"

is "ledgers are per-role" \
  "$([ -f .docket/runs/R01/.woke-orchestrator ] && [ -f .docket/runs/R01/.woke-planner ] && echo y)" "y"
lacks "planner ledger holds no task events" "$(cat .docket/runs/R01/.woke-planner)" "T01"
lacks "orchestrator ledger holds no orch events" "$(cat .docket/runs/R01/.woke-orchestrator)" "orch"

OUT=$(CLAUDE_PROJECT_DIR=$T DOCKET_ROLE=planner DOCKET_WATCH_TIMEOUT=1 $HOOK 2>&1)
is "DOCKET_ROLE filters to one role" "$?" "0"

$BIN disarm R01 >/dev/null
CLAUDE_PROJECT_DIR=$T $HOOK >/dev/null 2>&1; is "disarmed hook is inert (exit 0)" "$?" "0"
cleanup

sandbox   # a role that is not armed must never be woken - the original bug
$BIN init R01 >/dev/null; $BIN assign R01 orch >/dev/null
$BIN arm R01 --role orchestrator >/dev/null
fill .docket/runs/R01/orch-report-01.mdx; $BIN submit R01 orch >/dev/null
CLAUDE_PROJECT_DIR=$T DOCKET_WATCH_TIMEOUT=1 $HOOK >/dev/null 2>&1
is "unarmed planner is not woken (documents the trap)" "$?" "0"
has "doctor warns only one role is armed" "$($BIN doctor 2>&1)" "will NEVER be woken"
cleanup

echo; echo "misc"
sandbox
$BIN doctor >/dev/null 2>&1; is "doctor runs outside a run" "$?" "0"
for r in planner orchestrator implementor signalling; do
  $BIN help $r >/dev/null 2>&1 || bad "help $r" "exited non-zero"
done
ok "every role playbook renders"
$BIN status nope >/dev/null 2>&1; is "status on a missing run fails" "$?" "1"
cleanup

echo
printf '%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
