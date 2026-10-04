"""Conservative, read-only recognition of Docket's native wake adapters."""
from __future__ import annotations

import json
import hashlib
import shlex
from pathlib import Path


def read_hook_config(path: Path) -> dict:
    try:
        if path.suffix == ".toml":
            import tomllib
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        else:
            data = json.loads(path.read_text())
    except (ImportError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def docket_hook_command(command: object) -> bool:
    """Accept a direct adapter invocation, optionally through bash or sh.

    Mentioning wake.sh in another command is not an installed Docket adapter.
    Shell wrappers that cannot be established mechanically stay unconfirmed.
    """
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if len(words) == 2 and Path(words[0]).name in ("bash", "sh"):
        words = words[1:]
    return (len(words) == 1 and Path(words[0]).parts[-3:] == ("docket", "hooks", "wake.sh")
            and not any(char in words[0] for char in (";", "|", "`", "\n", "$(")))


def wake_hook_configured(path: Path, *, claude: bool) -> bool:
    """Recognize the correct command and the harness's required wake behavior."""
    data = read_hook_config(path)
    groups = data.get("hooks")
    stops = groups.get("Stop") if isinstance(groups, dict) else None
    for group in stops if isinstance(stops, list) else []:
        handlers = group.get("hooks") if isinstance(group, dict) else None
        for hook in handlers if isinstance(handlers, list) else []:
            if not isinstance(hook, dict) or hook.get("type") != "command" \
                    or not docket_hook_command(hook.get("command")):
                continue
            if claude:
                if hook.get("asyncRewake") is True:
                    return True
            elif hook.get("async", False) is False and hook.get("asyncRewake", False) is False:
                return True
    return False


def codex_hook_configuration(home: Path) -> dict:
    """Local configuration evidence, never a claim that the harness trusted it.

    CODEX_HOME is resolved by the caller. Project, CLI, and administrator
    overrides cannot be proven from these two files, so enabled is explicitly
    the local setting and operation still requires trust and a real wake.
    """
    config = read_hook_config(home / "config.toml")
    features = config.get("features")
    enabled = (features.get("hooks", features.get("codex_hooks", True))
               if isinstance(features, dict) else True)
    locations = [path for path in (home / "hooks.json", home / "config.toml")
                 if wake_hook_configured(path, claude=False)]
    return {"locations": locations, "enabled": enabled is True}


def skill_copy_differences(source: Path, installed: Path) -> list[str]:
    """Compare runtime skill files without reading project data or bytecode."""
    def revision(directory: Path) -> dict[str, str]:
        paths = [directory / "SKILL.md"]
        for name in ("bin", "hooks", "docket_cli", "references"):
            paths.extend(path for path in (directory / name).rglob("*") if path.is_file()
                         and "__pycache__" not in path.parts and path.suffix != ".pyc")
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths if path.is_file()}
    local, remote = revision(source), revision(installed)
    return sorted(name for name in local.keys() | remote.keys() if local.get(name) != remote.get(name))
