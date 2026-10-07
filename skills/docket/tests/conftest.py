"""Keep pytest bytecode outside the skill.

`tests/test.sh` sets `PYTHONPYCACHEPREFIX`, but a direct `pytest` run without
it would write `__pycache__` beside the test sources inside the skill.
Redirect it to the user cache instead, the same place `bin/docket` uses.
"""

from __future__ import annotations

import os
import sys

if sys.pycache_prefix is None and not sys.dont_write_bytecode:
    cache = os.environ.get("XDG_CACHE_HOME", "")
    if not os.path.isabs(cache):
        cache = os.path.join(os.path.expanduser("~"), ".cache")
    sys.pycache_prefix = os.path.join(cache, "docket", "pycache")
