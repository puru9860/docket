#!/usr/bin/env bash
# Regression suite for docket. Run: tests/test.sh
# Tests run in parallel processes (DOCKET_TEST_JOBS, default half the CPUs);
# DOCKET_TEST_JOBS=1 runs the plain serial unittest suite.
set -eu

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/docket-pycache" \
  python3 "$ROOT/tests/parallel.py" "$ROOT/skills/docket/tests"
