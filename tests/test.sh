#!/usr/bin/env bash
# Regression suite for docket. Run: tests/test.sh
# Tests run in reusable worker processes (DOCKET_TEST_JOBS, default half the CPUs);
# DOCKET_TEST_JOBS=1 runs them in one worker. A test running longer than
# DOCKET_TEST_TIMEOUT seconds (default 300) is killed and reported as an error.
set -eu

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/docket-pycache" \
  python3 "$ROOT/tests/parallel.py" "$ROOT/skills/docket/tests"
