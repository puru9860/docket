"""Explicitly selected project-local verification recipes.

A recipe is a JSON manifest naming the instructions, helpers, and evidence files of
one repeatable real-boundary check. A task selects it in its contract; the task's own
verify command runs it. Submission captures the recipe bytes before verify, gives the
command a fresh private evidence directory, and freezes the named evidence after.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path, PurePosixPath

from .frontmatter import parse
from .common import ROOTS_ABSENT, ROOTS_DECLARED
from .roots import read_roots, resolve_scope, split_qualified
from .paths import root

RECIPE_KEY = "verification_recipe"
RECIPE_ENV = "DOCKET_RECIPE_EVIDENCE_DIR"
RECIPE_MANIFEST_KEYS = ("version", "name", "instructions", "helpers", "evidence")


def encode_recipe_reference(reference: str) -> str:
    """Protect this field from flat-frontmatter quoting/comments; keep plain paths plain."""
    if not any(ch.isspace() or ch in "#\"'" for ch in reference):
        return reference
    # parse() tracks quotes but deliberately does not interpret backslash escapes.
    # A literal \" inside a JSON string would end its quoted region there, exposing
    # a later whitespace-# as a comment. Unicode escapes contain no quote to toggle.
    return json.dumps(reference, ensure_ascii=True).replace(r'\"', r'\u0022')


def decode_recipe_reference(value: object) -> str:
    """Decode only verification_recipe; other frontmatter values retain their semantics."""
    text = str(value or "").strip()
    if text.startswith('"'):
        try:
            decoded = json.loads(text)
        except ValueError:
            pass  # A legacy raw path may begin with a literal, unmatched quote.
        else:
            if isinstance(decoded, str):
                return decoded
    return text


def recipe_reference(contract_text: str) -> str:
    """The recipe a contract selects, or empty."""
    return decode_recipe_reference(parse(contract_text)[0].get(RECIPE_KEY, ""))


def relative_reference_problem(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return f"{label} must be a nonempty relative path"
    if "\n" in value or "\r" in value or "\0" in value:
        return f"{label} {value!r} contains a control character"
    pure = PurePosixPath(value)
    if pure.is_absolute() or "\\" in value:
        return f"{label} {value!r} must be relative, not absolute"
    if any(part in ("..", ".") or not part for part in value.split("/")):
        return f"{label} {value!r} must not contain '.', '..', or empty components"
    return ""


def normalize_recipe_reference(d: Path, token: str) -> tuple[str, str]:
    """A selection as stored in the contract: unqualified in one root, `alias:path` in many."""
    token = (token or "").strip()
    if token.startswith("./"):
        token = token[2:]
    alias, relative = split_qualified(token)
    problem = relative_reference_problem(relative if alias else token, "--recipe")
    if problem:
        return "", problem
    state, records, reason = read_roots(d)
    if state != ROOTS_DECLARED:
        if alias or state != ROOTS_ABSENT:
            return "", f"--recipe {token} cannot be resolved: {reason}"
        return token, ""
    qualified, problems = resolve_scope(records, [token])
    if problems:
        return "", problems[0].replace("scope path", "--recipe", 1)
    if len(records) == 1:
        return qualified[0].split(":", 1)[1], ""
    return qualified[0], ""


def recipe_location(d: Path, reference: str) -> tuple[Path | None, str, str, str]:
    """The checkout a reference lives in, its root alias, the relative path, or a problem.

    The contract is re-validated here, not trusted from assign: frontmatter can be
    amended by hand, and an unreadable root declaration is never read as absent.
    """
    state, records, reason = read_roots(d)
    alias, relative = split_qualified(reference)
    problem = relative_reference_problem(relative if alias else reference, "recipe")
    if problem:
        return None, "", "", problem
    if state != ROOTS_DECLARED:
        if alias or state != ROOTS_ABSENT:
            return None, "", "", f"recipe {reference} cannot be resolved: {reason}"
        return root().resolve(), "", reference, ""
    if not alias:
        if len(records) != 1:
            return None, "", "", f"recipe {reference} is unqualified in a multi-root run"
        return Path(records[0]["path"]), records[0]["alias"], reference, ""
    record = next((r for r in records if r["alias"] == alias), None)
    if record is None:
        return None, "", "", f"recipe {reference} names an undeclared root alias {alias!r}"
    return Path(record["path"]), alias, relative, ""


def read_regular(base: Path, relative: str, label: str) -> tuple[bytes | None, str]:
    """Bytes of a regular file under base, refusing every symlinked component."""
    problem = relative_reference_problem(relative, label)
    if problem:
        return None, problem
    current = base
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = os.lstat(current)
        except OSError as exc:
            return None, f"{label} {relative} is unreadable: {exc.strerror or exc}"
        if stat.S_ISLNK(info.st_mode):
            return None, f"{label} {relative} passes through a symlink, which a recipe may not use"
        last = index == len(parts) - 1
        if last and not stat.S_ISREG(info.st_mode):
            return None, f"{label} {relative} is not a regular file"
        if not last and not stat.S_ISDIR(info.st_mode):
            return None, f"{label} {relative} is not under a directory"
    try:
        return current.read_bytes(), ""
    except OSError as exc:
        return None, f"{label} {relative} is unreadable: {exc.strerror or exc}"


def manifest_problems(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["the recipe manifest must be a JSON object"]
    problems = []
    unknown = sorted(set(data) - set(RECIPE_MANIFEST_KEYS))
    missing = [key for key in RECIPE_MANIFEST_KEYS if key not in data]
    if unknown:
        problems.append(f"the recipe manifest has unsupported key(s): {', '.join(unknown)}")
    if missing:
        problems.append(f"the recipe manifest is missing: {', '.join(missing)}")
    if problems:
        return problems
    if data["version"] != 1 or isinstance(data["version"], bool):
        problems.append("the recipe manifest version must be 1")
    if not isinstance(data["name"], str) or not data["name"].strip():
        problems.append("the recipe manifest name must be a nonempty string")
    problem = relative_reference_problem(data["instructions"], "recipe instructions")
    if problem:
        problems.append(problem)
    for key in ("helpers", "evidence"):
        if not isinstance(data[key], list):
            problems.append(f"recipe {key} must be a list of relative paths")
            continue
        problems.extend(p for p in (relative_reference_problem(item, f"recipe {key} entry")
                                    for item in data[key]) if p)
    if isinstance(data["evidence"], list) and not data["evidence"]:
        problems.append("recipe evidence must name at least one proof file")
    if not problems:
        named = [data["instructions"], *data["helpers"]]
        if len(set(named)) != len(named) or len(set(data["evidence"])) != len(data["evidence"]):
            problems.append("the recipe manifest names a file more than once")
    return problems


def capture_recipe(d: Path, reference: str) -> tuple[dict[str, object] | None, str]:
    """The recipe's manifest, instructions, and helpers as bytes, read once, before verify."""
    base, alias, relative, problem = recipe_location(d, reference)
    if problem:
        return None, problem
    raw, problem = read_regular(base, relative, "recipe manifest")
    if problem:
        return None, problem

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate key {key!r}")
            out[key] = value
        return out

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f"recipe manifest {reference} is not valid JSON: {exc}"
    problems = manifest_problems(data)
    if problems:
        return None, f"recipe manifest {reference}: " + "; ".join(problems)
    directory = PurePosixPath(relative).parent
    manifest_name = PurePosixPath(relative).name
    if manifest_name in (data["instructions"], *data["helpers"]):
        return None, f"recipe manifest {reference} names itself as a recipe file"
    files: list[dict[str, object]] = [
        {"role": "manifest", "path": manifest_name, "base": base, "relative": relative, "data": raw},
    ]
    for role, path in (("instructions", data["instructions"]),
                       *(("helper", item) for item in data["helpers"])):
        inside = str(directory / path) if str(directory) != "." else path
        content, problem = read_regular(base, inside, f"recipe {role}")
        if problem:
            return None, problem
        files.append({"role": role, "path": path, "base": base, "relative": inside, "data": content})
    return {"reference": reference, "name": data["name"], "alias": alias, "files": files,
            "evidence": list(data["evidence"])}, ""


def recipe_drift(capture: dict[str, object]) -> list[str]:
    """Recipe files that changed or vanished since they were captured."""
    moved = []
    for item in capture["files"]:
        now, problem = read_regular(item["base"], item["relative"], f"recipe {item['role']}")
        if problem:
            moved.append(f"{problem} after verification ran")
        elif now != item["data"]:
            moved.append(f"recipe {item['role']} {item['relative']} changed while the verification ran")
    return moved


def new_evidence_dir(d: Path, owner: str) -> Path:
    """A fresh, empty, private directory for exactly one verify invocation."""
    parent = d / ".recipe-evidence" / owner
    parent.mkdir(parents=True, exist_ok=True)
    os.chmod(d / ".recipe-evidence", 0o700)
    while True:
        where = parent / f"{time.time_ns()}-{os.urandom(4).hex()}"
        try:
            where.mkdir(mode=0o700)
            return where
        except FileExistsError:
            continue


def collect_evidence(capture: dict[str, object], where: Path) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Every declared proof file this invocation wrote, or one problem per missing file."""
    found: list[tuple[str, bytes]] = []
    problems: list[str] = []
    for name in capture["evidence"]:
        data, problem = read_regular(where, name, "recipe evidence")
        if problem:
            problems.append(f"{problem}; the recipe run at {where} did not produce its declared proof")
        else:
            found.append((name, data))
    return found, problems


def recipe_section(capture: dict[str, object], evidence: list[tuple[str, bytes]],
                   where: Path | None, d: Path, store: object,
                   changed: list[str]) -> dict[str, object]:
    """The bundle's recipe record, storing every captured byte as a digested artifact.

    `changed_in_patch` lists recipe files the round's own patch touched: an edited
    recipe is a changed oracle, never independent evidence for the change beside it.
    """
    section: dict[str, object] = {"reference": capture["reference"], "name": capture["name"],
                                  "run": "yes"}
    for item in capture["files"]:
        row = {"path": item["path"], **store(f"recipe/{item['role']}/{item['path']}", item["data"])}
        if item["role"] == "helper":
            section.setdefault("helpers", []).append(row)
        else:
            section[item["role"]] = row
    section.setdefault("helpers", [])
    # Shown like the change list: unqualified in a single-root run, `alias:path` in a
    # multi-root one, whose selection is always qualified.
    qualified = {item if ":" in item else f"{capture['alias']}:{item}" for item in changed}
    shown = "{alias}:{path}" if split_qualified(capture["reference"])[0] else "{path}"
    section["changed_in_patch"] = sorted(
        shown.format(alias=capture["alias"], path=item["relative"]) for item in capture["files"]
        if f"{capture['alias']}:{item['relative']}" in qualified)
    section["evidence"] = [{"name": name, **store(f"recipe/evidence/{name}", data)}
                           for name, data in evidence]
    if where is not None:
        try:
            section["evidence_dir"] = str(where.relative_to(d))
        except ValueError:
            section["evidence_dir"] = str(where)
    return section


def recipe_not_run(reference: str, reason: str) -> dict[str, object]:
    return {"reference": reference, "run": "no", "reason": reason}


def recipe_approval_problem(frozen: dict[str, object] | None, selected: str) -> str:
    """Why a bundle cannot support approving a contract that selects a recipe, or empty."""
    if not selected:
        return ""
    recipe = frozen.get("recipe") if isinstance(frozen, dict) else None
    if not isinstance(recipe, dict) or recipe.get("reference") != selected:
        return (f"the contract selects verification recipe {selected}, but the frozen bundle "
                "records no run of it")
    if recipe.get("run") != "yes":
        return (f"the selected verification recipe {recipe.get('reference')} was not run "
                f"({recipe.get('reason', 'no reason recorded')}); approval needs its frozen evidence")
    if not recipe.get("evidence"):
        return f"the frozen recipe record for {recipe.get('reference')} holds no evidence"
    return ""
