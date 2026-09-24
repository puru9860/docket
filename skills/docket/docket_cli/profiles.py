"""Model profile cards and the model aliases that select them."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .common import SKILL_DIR
from .frontmatter import parse
from .bundles import digest_of


# Role contracts live in exactly one place: references/contracts/<role>.md.
# Help and prompts both read them through read_contract; nothing here holds
# a second copy of that text.

PROFILE_TOKEN_BUDGET = 600


def profiles_dir() -> Path:
    """Profile-card tree: the repo references, or an injected test root.

    Like the fixture and arm roots, this override only selects which reviewed
    bytes are read; card digests still bind the exact content.
    """
    override = os.environ.get("DOCKET_PROFILES_ROOT", "").strip()
    if override:
        return Path(override)
    return SKILL_DIR / "references" / "model-profiles"


def user_profiles_dir() -> Path:
    """Reviewed per-model profiles the user adopted, kept outside the skill.

    A skill update replaces the skill's own tree, so learned per-model
    instructions live in the user's config and apply in every project.
    `DOCKET_MODEL_PROFILES` names another directory.
    """
    override = os.environ.get("DOCKET_MODEL_PROFILES", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or str(Path.home() / ".config")
    return Path(base) / "docket" / "model-profiles"


def profile_path(name: str) -> Path:
    """Where a profile's bytes live: the user's adopted copy wins over the skill's."""
    user = user_profiles_dir() / f"{name}.md"
    return user if user.is_file() else profiles_dir() / f"{name}.md"


def read_profile(name: str) -> tuple[dict[str, str], str]:
    """One reviewed profile: flat frontmatter plus guidance body."""
    path = profile_path(name)
    if not path.is_file():
        return {}, ""
    try:
        return parse(path.read_text())
    except (OSError, ValueError):
        return {}, ""


def list_cards() -> list[str]:
    if not profiles_dir().is_dir():
        return []
    names = []
    for path in sorted(profiles_dir().glob("*.md")):
        try:
            meta, _ = parse(path.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("card", "") == "true":
            names.append(path.stem)
    return names


def profile_revision(name: str) -> str:
    path = profile_path(name)
    try:
        data = path.read_bytes()
    except OSError:
        return f"{name}:missing"
    meta, _ = parse(data.decode(errors="replace"))
    return f"{name}:v{meta.get('version', '?')}:{digest_of(data).split(':')[1][:12]}"


def profile_version(meta: dict[str, str]) -> int | None:
    """A profile's version as a whole number, or None when it is not one."""
    try:
        return int(str(meta.get("version", "")).strip())
    except ValueError:
        return None


def profile_candidates() -> list[Path]:
    """Every model profile file, user profiles first, in matching order."""
    user = sorted(user_profiles_dir().glob("*.md")) if user_profiles_dir().is_dir() else []
    return [*user, *sorted(profiles_dir().glob("*.md"))]


def match_model_profile(model: str) -> str:
    """The reviewed profile for a model string, or empty for unknown models.

    Matching is exact on the normalized identifier: a declared alias selects
    only when it equals the whole requested model string. A name that merely
    contains another name never selects.
    """
    want = normalized_model(model).lower()
    if not want:
        return ""
    for path in profile_candidates():
        try:
            meta, _ = parse(path.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("card", "") == "true":
            continue
        models = [normalized_model(item.strip()).lower() for item in meta.get("models", "").split(",")]
        if any(item and item == want for item in models):
            return path.stem
    return ""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def normalized_model(value: str) -> str:
    """A model's canonical id: display suffix dropped, recorded aliases resolved.

    `x/y (Some Name)` becomes `x/y`, and a name recorded with `docket models
    --alias NAME=ID` becomes that ID, so one model never splits into two
    scorecards or misses its own profile.
    """
    name = re.split(r"\s+\(", (value or "").strip(), maxsplit=1)[0].strip()
    return model_aliases().get(name.lower(), name)


def model_aliases_path() -> Path:
    return user_profiles_dir() / "aliases.txt"


def model_aliases() -> dict[str, str]:
    """Recorded `NAME = ID` lines, keyed by the lowercased name."""
    try:
        lines = model_aliases_path().read_text().splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        name, sep, target = line.partition("=")
        if sep and name.strip() and target.strip() and not line.lstrip().startswith("#"):
            out[name.strip().lower()] = target.strip()
    return out
