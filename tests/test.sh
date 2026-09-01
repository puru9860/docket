#!/usr/bin/env bash
# Regression suite for docket. Run: tests/test.sh
set -eu

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/docket-pycache" \
  python3 -m unittest discover -s "$ROOT/skills/docket/tests" -v
