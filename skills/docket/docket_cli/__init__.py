"""Docket: file-protocol coordination for a planner/orchestrator/implementor agent pipeline.

The CLI is this stdlib-only package, one module per region of the ARCHITECTURE.md code
map. A module imports only from modules listed before it in `MODULES`, so that order
is also the dependency order, and a name is defined in exactly one module.
`bin/docket` is the entry point: it imports this package and calls `main`.
"""

from __future__ import annotations

import os as _os
import sys as _sys


def _ensure_docket_pycache_prefix() -> None:
    """Redirect bytecode outside the skill when the caller set no prefix.

    `bin/docket` sets `sys.pycache_prefix` before importing this package, and
    `tests/test.sh` sets `PYTHONPYCACHEPREFIX`. A direct `import docket_cli`
    without either would otherwise write `__pycache__` beside the sources
    inside the skill; keep it in the user cache instead.
    """
    if _sys.pycache_prefix is None and not _sys.dont_write_bytecode:
        cache = _os.environ.get("XDG_CACHE_HOME", "")
        if not _os.path.isabs(cache):
            cache = _os.path.join(_os.path.expanduser("~"), ".cache")
        _sys.pycache_prefix = _os.path.join(cache, "docket", "pycache")


_ensure_docket_pycache_prefix()

from . import (
    common,
    frontmatter,
    paths,
    publication,
    policy,
    evidence,
    roots,
    baselines,
    bundles,
    locks,
    state,
    profiles,
    dependencies,
    freeze,
    aggregates,
    verification,
    templates,
    feedback,
    hook_config,
    sessions,
    discussions,
    models,
    liveness,
    batches,
    five_role,
    events,
    delivery,
    signalling,
    health,
    gate,
    submission,
    amendments,
    transitions,
    verifier,
    qualification,
    doctor,
    playbooks,
    prompts,
    improvements,
    suite,
    metrics,
    release,
    packets,
    dispatch,
    commands,
    cli,
)
from .cli import main

MODULES = (
    common,
    frontmatter,
    paths,
    publication,
    policy,
    evidence,
    roots,
    baselines,
    bundles,
    locks,
    state,
    profiles,
    dependencies,
    freeze,
    aggregates,
    verification,
    templates,
    feedback,
    hook_config,
    sessions,
    discussions,
    models,
    liveness,
    batches,
    five_role,
    events,
    delivery,
    signalling,
    health,
    gate,
    submission,
    amendments,
    transitions,
    verifier,
    qualification,
    doctor,
    playbooks,
    prompts,
    improvements,
    suite,
    metrics,
    release,
    packets,
    dispatch,
    commands,
    cli,
)
