"""Bundle storage, integrity checks, and the run-private Git reconstruction behind them."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .common import EMPTY_TREE_SHA1, SHA_DIGEST_RE
from .paths import root
from .publication import fault, keep_private, publish_bytes, publish_json
from .evidence import empty_tree, git_argv, git_env, git_raw, is_state_path, state_prefix
from .roots import declared_roots, root_identity
from .baselines import evidence_mode, snapshot_dir, snapshot_path


BUNDLE_VERSION = 2
BUNDLE_DIR = ".bundles"
BUNDLE_MANIFEST = "bundle.json"
DEPS_DIR = ".deps"
PROVISIONAL_FORBIDDEN = "forbidden"
PROVISIONAL_ALLOWED = "allowed"
PROVISIONAL_POLICIES = (PROVISIONAL_FORBIDDEN, PROVISIONAL_ALLOWED)
FINAL_STATES = ("approved", "completed")
# Git file modes for content a baseline captured outside the index.
UNTRACKED_MODES = {"file": "100644", "executable": "100755", "symlink": "120000"}


def bundles_root(d: Path) -> Path:
    """Bundle store root: the run's mutable `.bundles`, or a scoped override.

    A frozen release validates and renders from its own carried bundle store;
    the override only selects which bytes are read, and a missing or damaged
    store refuses rather than falling back to live state.
    """
    override = os.environ.get("DOCKET_BUNDLES_ROOT", "").strip()
    if override:
        return Path(override)
    return d / BUNDLE_DIR


def bundle_objects(d: Path) -> Path:
    return bundles_root(d) / "objects"


def bundle_work(d: Path) -> Path:
    """Writable Git scratch for private reconstruction.

    A frozen release store is immutable evidence, so its work and index files
    are redirected to a scratch directory; only reads touch the release.
    """
    override = os.environ.get("DOCKET_BUNDLE_WORK", "").strip()
    if override:
        return Path(override)
    return bundles_root(d) / "work"


def owner_bundles(d: Path, owner: str) -> Path:
    return bundles_root(d) / owner


def bundle_ledger(d: Path, owner: str) -> Path:
    return owner_bundles(d, owner) / "rounds.json"


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def manifest_digest(manifest: dict[str, object]) -> str:
    """Content address of one bundle: every field except the address itself."""
    payload = {key: value for key, value in manifest.items() if key != "digest"}
    return digest_of(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def freeze_record(record: dict[str, object]) -> dict[str, object]:
    """Address a frozen record by its own content, excluding the address itself.

    Readers recompute with check_frozen_digest, so any hand edit - including an
    edited status inheriting trust - breaks the address instead of passing.
    """
    frozen = dict(record)
    frozen.pop("digest", None)
    frozen["digest"] = digest_of(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode())
    return frozen


def check_frozen_digest(record: dict) -> list[str]:
    """Why a frozen record's content no longer matches its own address."""
    digest = record.get("digest", "")
    if not isinstance(digest, str) or not SHA_DIGEST_RE.match(digest):
        return ["record carries no content digest; only frozen evidence counts"]
    payload = {key: value for key, value in record.items() if key != "digest"}
    if digest_of(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()) != digest:
        return ["record digest mismatch: content was edited after freezing; refusing"]
    return []


def private_git_env(d: Path, record: dict[str, object], index: str) -> dict[str, str]:
    """A Git environment that writes only into `.docket`, never into the checkout.

    New trees and blobs land in a run-private object store and the real store is
    read through alternates, so rebuilding a baseline never adds an object, a ref,
    an index entry, a stash, or a working file to the checkout being measured.
    """
    objects = bundle_objects(d)
    (objects / "info").mkdir(parents=True, exist_ok=True)
    (objects / "pack").mkdir(parents=True, exist_ok=True)
    keep_private(objects)
    bundle_work(d).mkdir(parents=True, exist_ok=True)
    env = git_env()
    env["GIT_DIR"] = str(record.get("git_dir", ""))
    env["GIT_INDEX_FILE"] = str(bundle_work(d) / index)
    env["GIT_OBJECT_DIRECTORY"] = str(objects)
    alternates = [
        str(Path(str(record.get("common_dir", ""))) / "objects"),
        str(Path(str(record.get("git_dir", ""))) / "objects"),
    ]
    env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = ":".join(dict.fromkeys(alternates))
    return env


def moved_paths(d: Path, owner: str, before: dict[str, str], after: dict[str, str],
                limit: int = 12) -> str:
    """The paths that differ between two source captures, for a refusal to name."""
    records, _ = baseline_roots(d, owner)
    by_alias = {str(r.get("alias", "")): r for r in records}
    names: list[str] = []
    for alias in sorted(set(before) | set(after)):
        old, new = before.get(alias, ""), after.get(alias, "")
        if old == new:
            continue
        record = by_alias.get(alias)
        changed: list[str] = []
        if record and old and new:
            env = private_git_env(d, record, "moved.index")
            code, out, _ = private_git(env, Path(str(record.get("path", "."))), "diff-tree",
                                       "-r", "--name-only", "-z", old, new)
            if code == 0:
                changed = [n for n in out.decode(errors="surrogateescape").split("\0") if n]
        prefix = f"{alias}:" if len(by_alias) > 1 else ""
        names.extend(prefix + name for name in changed) if changed else names.append(f"{alias}:")
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def private_git(
    env: dict[str, str], cwd: Path, *args: str,
    stdin: bytes | None = None, timeout: int = 600,
) -> tuple[int, bytes, bytes]:
    """Run one Git command against the private object store and index."""
    try:
        result = subprocess.run(
            git_argv(*args), cwd=str(cwd), env=env, input=stdin,
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, b"", str(exc).encode()
    return result.returncode, result.stdout, result.stderr


def first_line(data: bytes) -> str:
    text = data.decode(errors="replace").strip().splitlines()
    return text[0] if text else ""


def prune_index(env: dict[str, str], cwd: Path, embedded: list[str]) -> str:
    """Drop Docket's own state and undeclared embedded checkouts from a private index.

    Baseline capture never recorded either of them, so leaving them in one side of a
    comparison would invent a change that no implementor made.
    """
    code, out, err = private_git(env, cwd, "ls-files", "-z")
    if code:
        return first_line(err) or "Git cannot read the private index"
    state = state_prefix(cwd)
    drop = [
        entry for entry in out.decode(errors="surrogateescape").split("\0")
        if entry and (
            is_state_path(state, entry)
            or any(entry == item or entry.startswith(item + "/") for item in embedded)
        )
    ]
    if not drop:
        return ""
    payload = b"\0".join(item.encode("utf-8", "surrogateescape") for item in drop) + b"\0"
    code, _, err = private_git(
        env, cwd, "update-index", "--force-remove", "-z", "--stdin", stdin=payload,
    )
    if code:
        return first_line(err) or "Git cannot prune the private index"
    return ""


def baseline_tree(d: Path, owner: str, record: dict[str, object]) -> tuple[str, str]:
    """Rebuild one root's captured baseline as a Git tree, or say why it cannot be.

    The pinned commit, the staged patch, the worktree patch, and the captured
    untracked content are exactly what `capture_root_baseline` stored, so the tree
    this produces is the state the implementor was dispatched over, dirt included.
    """
    alias = str(record.get("alias", ""))
    path = Path(str(record.get("path", "")))
    if not path.is_dir():
        return "", f"the declared root {path} is missing"
    env = private_git_env(d, record, f"{owner}.{alias}.baseline.index")
    Path(env["GIT_INDEX_FILE"]).unlink(missing_ok=True)
    base = str(record.get("base_tree") or EMPTY_TREE_SHA1)
    code, _, _ = private_git(env, path, "read-tree", base)
    if code:
        return "", f"Git cannot read the pinned baseline tree {base[:12]} for {alias}"
    snap = snapshot_dir(d, owner)
    for key, label in (("staged_patch", "staged"), ("worktree_patch", "worktree")):
        name = str(record.get(key, ""))
        if not name:
            continue
        patch = snap / name
        if not patch.is_file():
            return "", f"the captured {label} baseline patch for {alias} is missing"
        if not patch.stat().st_size:
            continue
        code, _, err = private_git(
            env, path, "apply", "--cached", "--binary", "--whitespace=nowarn", str(patch),
        )
        if code:
            return "", (
                f"the captured {label} baseline patch for {alias} no longer applies: "
                f"{first_line(err)}"
            )
    name = str(record.get("untracked_manifest", ""))
    entries: dict[str, object] = {}
    if name:
        manifest = snap / name
        if not manifest.is_file():
            return "", f"the captured untracked manifest for {alias} is missing"
        try:
            entries = dict(json.loads(manifest.read_text()).get("entries", {}))
        except (OSError, ValueError) as exc:
            return "", f"the captured untracked manifest for {alias} is unreadable: {exc}"
    records: list[bytes] = []
    for relative, entry in sorted(entries.items()):
        if not isinstance(entry, dict):
            continue
        mode = UNTRACKED_MODES.get(str(entry.get("kind", "")), "")
        if not mode:
            continue
        if mode == "120000":
            content = str(entry.get("target", "")).encode()
            args = ("hash-object", "-w", "--no-filters", "--stdin")
        else:
            content = base64.b64decode(str(entry.get("content_base64", "")))
            args = ("hash-object", "-w", "--path", relative, "--stdin")
        code, out, _ = private_git(env, path, *args, stdin=content)
        if code:
            return "", f"Git cannot store the captured untracked content of {alias}:{relative}"
        line = f"{mode} {out.decode().strip()}\t{relative}"
        records.append(line.encode("utf-8", "surrogateescape"))
    if records:
        code, _, err = private_git(
            env, path, "update-index", "-z", "--index-info",
            stdin=b"\0".join(records) + b"\0",
        )
        if code:
            return "", f"Git cannot rebuild the captured untracked state of {alias}: {first_line(err)}"
    problem = prune_index(env, path, [str(item) for item in record.get("embedded_checkouts") or []])
    if problem:
        return "", problem
    code, out, _ = private_git(env, path, "write-tree")
    if code:
        return "", f"Git cannot write the rebuilt baseline tree for {alias}"
    return out.decode().strip(), ""


def carry_hidden_flags(env: dict[str, str], path: Path) -> str:
    """Copy the checkout's skip-worktree and assume-unchanged bits into a private index.

    Baseline capture reads Git's own view, which hides those paths' local edits, so the
    source tree must hide them too. Rebuilt from HEAD without the bits, `git add -A`
    froze a hidden local edit, a secret included, as the implementor's own change.
    """
    listing = git_raw(path, "ls-files", "-v", "-z")
    if listing is None:
        return f"Git cannot read the index flags of {path}"
    flagged: dict[str, list[str]] = {"--skip-worktree": [], "--assume-unchanged": []}
    for item in listing.decode("utf-8", "surrogateescape").split("\0"):
        if len(item) < 3:
            continue
        tag, name = item[0], item[2:]
        if tag == "S":
            flagged["--skip-worktree"].append(name)
        elif tag.islower():
            flagged["--assume-unchanged"].append(name)
    code, present, _ = private_git(env, path, "ls-files", "-z")
    if code:
        return f"Git cannot list the private index for {path}"
    known = set(present.decode("utf-8", "surrogateescape").split("\0"))
    for flag, names in flagged.items():
        names = [name for name in names if name in known]
        if not names:
            continue
        data = "\0".join(names).encode("utf-8", "surrogateescape") + b"\0"
        code, _, err = private_git(env, path, "update-index", flag, "-z", "--stdin", stdin=data)
        if code:
            return f"Git cannot carry {flag} into the private index: {first_line(err)}"
    return ""


def source_tree(
    d: Path, owner: str, record: dict[str, object], slot: str
) -> tuple[dict[str, str], str]:
    """One root's current state as a Git tree, with the HEAD and branch it sits on.

    Work committed during the task is inside this tree, because the tree is built
    from the worktree rather than from what Git currently calls dirty.
    """
    alias = str(record.get("alias", ""))
    path = Path(str(record.get("path", "")))
    if not path.is_dir():
        return {}, f"the declared root {path} is missing"
    identity = root_identity(path)
    if not identity:
        return {}, f"Git cannot identify the declared root {path}"
    env = private_git_env(d, record, f"{owner}.{alias}.{slot}.index")
    Path(env["GIT_INDEX_FILE"]).unlink(missing_ok=True)
    head = identity["head"] or empty_tree(path)
    code, _, _ = private_git(env, path, "read-tree", head)
    if code:
        return {}, f"Git cannot read the current HEAD of {alias}"
    problem = carry_hidden_flags(env, path)
    if problem:
        return {}, problem
    work = dict(env)
    work["GIT_WORK_TREE"] = str(path)
    # Docket's own state is pruned from the index either way. Excluding it up front
    # keeps Git from hashing a run's accumulated evidence into the private store on
    # every freeze - but only when it is not already ignored, because an exclude
    # pathspec naming an ignored path makes `git add` refuse the whole invocation.
    pathspecs: list[str] = []
    state = state_prefix(path)
    if state and private_git(work, path, "check-ignore", "-q", state)[0] != 0:
        pathspecs = [
            "--", ":/", f":(exclude,top){state}", f":(exclude,top,glob){state}/**",
        ]
    code, _, err = private_git(work, path, "add", "-A", *pathspecs)
    if code:
        return {}, f"Git cannot read the current state of {alias}: {first_line(err)}"
    problem = prune_index(env, path, [str(item) for item in record.get("embedded_checkouts") or []])
    if problem:
        return {}, problem
    code, out, _ = private_git(env, path, "write-tree")
    if code:
        return {}, f"Git cannot write the current source tree for {alias}"
    return {
        "alias": alias,
        "path": str(path),
        "tree": out.decode().strip(),
        "head": identity["head"],
        "branch": identity["branch"],
    }, ""


def retain_tree_objects(d: Path, record: dict[str, object], trees: tuple[str, ...]) -> str:
    """Copy into the private store every object a pinned tree adds to the pinned HEAD.

    Git does not write an object that already exists through an alternate, so content
    the user had staged was pinned by a bundle while living only in the user's store,
    and a later `git gc` after restaging deleted it. Objects the pinned commit reaches
    are the user's history; everything a tree adds on top of it is kept here.
    """
    path = Path(str(record.get("path", "")))
    env = private_git_env(d, record, "retain.index")
    head = str(record.get("base_tree") or EMPTY_TREE_SHA1)
    wanted: set[str] = set()
    for tree in trees:
        code, out, err = private_git(env, path, "diff-tree", "-r", "-t", "-z", "--no-renames",
                                     head, tree)
        if code:
            return f"Git cannot list the objects of tree {tree[:12]}: {first_line(err)}"
        wanted.add(tree)
        for field in out.split(b"\0"):
            if field.startswith(b":"):
                sha = field.split()[3].decode()
                if sha.strip("0"):
                    wanted.add(sha)
    solo = {k: v for k, v in env.items() if k != "GIT_ALTERNATE_OBJECT_DIRECTORIES"}
    query = "".join(f"{sha}\n" for sha in sorted(wanted)).encode()
    code, out, err = private_git(solo, path, "cat-file", "--batch-check", stdin=query)
    if code:
        return f"Git cannot check the private object store: {first_line(err)}"
    missing = [line.split()[0] for line in out.decode().splitlines() if line.endswith(" missing")]
    if not missing:
        return ""
    names = "".join(f"{sha}\n" for sha in missing).encode()
    code, pack, err = private_git(env, path, "pack-objects", "--stdout", "-q", stdin=names)
    if code:
        return f"Git cannot pack the pinned objects: {first_line(err)}"
    code, _, err = private_git(solo, path, "index-pack", "--stdin", stdin=pack)
    if code:
        return f"Git cannot keep the pinned objects in the private store: {first_line(err)}"
    return ""


def tree_patch(
    d: Path, record: dict[str, object], left: str, right: str
) -> tuple[bytes, list[str], str]:
    """A binary-safe, rename-detecting patch between two trees, plus its paths."""
    alias = str(record.get("alias", ""))
    path = Path(str(record.get("path", "")))
    env = private_git_env(d, record, f"diff.{alias}.index")
    code, patch, err = private_git(
        env, path, "diff", "--binary", "-M", "--no-color", "--no-ext-diff", "--no-textconv",
        left, right,
    )
    if code:
        return b"", [], f"Git cannot render the patch for {alias}: {first_line(err)}"
    code, names, err = private_git(env, path, "diff", "--name-only", "-z", left, right)
    if code:
        return b"", [], f"Git cannot list the changed paths for {alias}: {first_line(err)}"
    return patch, [item for item in names.decode(errors="surrogateescape").split("\0") if item], ""


def baseline_roots(d: Path, owner: str) -> tuple[list[dict[str, object]], str]:
    """The captured baseline root records for one owner, or why there are none."""
    snap = snapshot_path(d, owner)
    if not snap.is_file():
        return [], f"no assignment snapshot at {snap.name}"
    try:
        data = json.loads(snap.read_text())
    except (OSError, ValueError) as exc:
        return [], f"unreadable assignment snapshot {snap.name}: {exc}"
    if str(data.get("baseline", "")) != "recorded":
        return [], str(data.get("reason") or f"no complete baseline was captured for {owner}")
    records = [record for record in data.get("roots", []) if isinstance(record, dict)]
    if not records:
        return [], "no checkout root was declared for this run"
    return records, ""


def source_identity(d: Path, owner: str, slot: str) -> tuple[list[dict[str, str]], str]:
    """Resulting source revision of every declared root, or why it is unavailable."""
    if evidence_mode(d) != "git":
        return [], f"the run declares evidence_mode: {evidence_mode(d)}"
    records, reason = baseline_roots(d, owner)
    if reason:
        return [], reason
    out: list[dict[str, str]] = []
    for record in records:
        identity, problem = source_tree(d, owner, record, slot)
        if problem:
            return [], problem
        out.append(identity)
    return out, ""


def read_ledger(d: Path, owner: str) -> list[dict[str, object]]:
    """Append-only record of every bundle frozen for one owner, oldest first."""
    path = bundle_ledger(d, owner)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def bundle_dir(d: Path, owner: str, entry: dict[str, object]) -> Path:
    return owner_bundles(d, owner) / str(entry.get("dir", ""))


def load_bundle(d: Path, owner: str, entry: dict[str, object]) -> dict[str, object] | None:
    path = bundle_dir(d, owner, entry) / BUNDLE_MANIFEST
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def manifest_files(manifest: dict[str, object]) -> list[dict[str, object]]:
    """Every artifact the manifest claims, flattened for integrity checking."""
    out: list[dict[str, object]] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            if "file" in value and "sha256" in value:
                out.append(value)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(manifest)
    return out


def pinned_trees(manifest: dict[str, object]) -> dict[str, list[str]]:
    """Every Git tree a bundle's patch, delta, and source identity are stated against.

    These are content-addressed references out of the bundle into the run-private
    object store. They are part of the evidence, so their availability is checked
    like any artifact digest rather than assumed.
    """
    wanted: dict[str, list[str]] = {}

    def want(alias: str, *ids: object) -> None:
        for item in ids:
            value = str(item or "")
            if alias and value and value not in wanted.setdefault(alias, []):
                wanted[alias].append(value)

    for key, fields in (
        ("patch", ("baseline_tree", "source_tree")),
        ("delta", ("from_tree", "to_tree")),
        ("source", ("tree",)),
    ):
        block = manifest.get(key)
        rows = block.get("roots", []) if isinstance(block, dict) else []
        for record in rows if isinstance(rows, list) else []:
            if isinstance(record, dict):
                want(str(record.get("alias", "")), *(record.get(field) for field in fields))
    return wanted


def tree_problems(d: Path, record: dict[str, object], trees: list[str]) -> list[str]:
    """Whether every object under one root's pinned trees is still readable.

    A tree identity that Git can no longer resolve makes the round unreconstructable,
    however intact the manifest and its files happen to look.
    """
    alias = str(record.get("alias", ""))
    if not str(record.get("git_dir", "")):
        return [f"records no Git directory for {alias}, so its pinned trees cannot be checked"]
    env = private_git_env(d, record, f"{alias}.verify.index")
    problems: list[str] = []
    for tree in trees:
        code, out, err = private_git(env, root(), "ls-tree", "-r", "-t", "-z", tree)
        if code:
            problems.append(
                f"pins the tree {tree[:12]} for {alias}, which Git can no longer read: "
                f"{first_line(err) or 'the object is gone'}"
            )
            continue
        ids = []
        for entry in out.decode(errors="surrogateescape").split("\0"):
            fields = entry.split("\t", 1)[0].split(" ") if entry else []
            # A gitlink names a commit in another repository, which this store never held.
            if len(fields) == 3 and fields[1] != "commit":
                ids.append(fields[2])
        if not ids:
            continue
        code, listed, err = private_git(
            env, root(), "cat-file", "--batch-check", stdin=("\n".join(ids) + "\n").encode(),
        )
        # A check that did not run is not a pass: no output would otherwise read as
        # nothing missing, and an unverifiable bundle as intact.
        if code or len(listed.decode(errors="replace").splitlines()) != len(ids):
            problems.append(
                f"pins the tree {tree[:12]} for {alias}, whose objects Git could not check: "
                f"{first_line(err) or 'incomplete answer'}"
            )
            continue
        missing = [
            line.split(" ")[0] for line in listed.decode(errors="replace").splitlines()
            if line.endswith(" missing")
        ]
        if missing:
            problems.append(
                f"pins the tree {tree[:12]} for {alias}, which is missing {len(missing)} of the "
                f"{len(ids)} object(s) it needs"
            )
    return problems


def bundle_problems(d: Path, owner: str, entry: dict[str, object]) -> list[str]:
    """Everything that stops a frozen bundle from being usable review evidence.

    Four things have to hold together: the manifest still addresses itself, every
    artifact it claims is present with the recorded bytes, the consumed-input record
    it froze is present and hashes to the revision it names, and every Git tree it
    pins is still resolvable. A bundle that fails any of them cannot reconstruct the
    round it froze, so it is reported as damaged rather than as intact evidence.
    """
    where = bundle_dir(d, owner, entry)
    manifest = load_bundle(d, owner, entry)
    if manifest is None:
        return [f"the frozen bundle at {where.name} is missing or unreadable"]
    recorded = str(manifest.get("digest", ""))
    if recorded != str(entry.get("digest", "")):
        return [f"{where.name} records digest {recorded} but the ledger expects {entry.get('digest')}"]
    if manifest_digest(manifest) != recorded:
        return [f"the manifest of {recorded} no longer hashes to its own digest"]
    problems: list[str] = []
    for item in manifest_files(manifest):
        target = where / str(item.get("file", ""))
        if not target.is_file():
            problems.append(f"{recorded} is missing its artifact {item.get('file')}")
            continue
        if digest_of(target.read_bytes()) != str(item.get("sha256", "")):
            problems.append(f"{recorded} no longer matches its recorded artifact {item.get('file')}")
    consumed = manifest.get("dependencies")
    consumed = consumed if isinstance(consumed, dict) else {}
    kind = str(manifest.get("kind", "task-round"))
    if consumed.get("file"):
        stored = where / str(consumed["file"])
        if stored.is_file() and digest_of(stored.read_bytes()) != str(consumed.get("revision", "")):
            problems.append(
                f"{recorded} names a consumed-input revision that its frozen "
                f"{consumed['file']} does not hash to"
            )
    elif kind != "aggregate" and int(manifest.get("version", 0) or 0) >= BUNDLE_VERSION:
        problems.append(
            f"{recorded} froze no consumed-input record, so nothing states which inputs the "
            "captured verification was taken against"
        )
    baseline = manifest.get("baseline")
    rows = baseline.get("roots", []) if isinstance(baseline, dict) else []
    described = {
        str(record.get("alias", "")): record
        for record in rows if isinstance(record, dict)
    }
    declared = {str(item.get("alias", "")): item for item in declared_roots(d)}
    for alias, trees in pinned_trees(manifest).items():
        record = described.get(alias)
        if record is None:
            problems.append(f"{recorded} pins trees for {alias}, which its frozen baseline omits")
            continue
        if not str(record.get("git_dir", "")):
            # A bundle frozen before baselines carried their own Git identity. Fall back
            # to the run's declaration so an older round stays checkable rather than
            # unreadable; a bundle frozen now is self-contained and never needs this.
            record = {
                **record,
                **{key: value for key, value in declared.get(alias, {}).items()
                   if key in ("git_dir", "common_dir")},
            }
        problems.extend(f"{recorded} {item}" for item in tree_problems(d, record, trees))
    return problems


def bundles_for(d: Path, owner: str, rnd: int | None = None) -> list[dict[str, object]]:
    entries = read_ledger(d, owner)
    if rnd is None:
        return entries
    return [entry for entry in entries if int(entry.get("round", 0) or 0) == rnd]


def latest_bundle(d: Path, owner: str, rnd: int | None = None) -> dict[str, object] | None:
    entries = bundles_for(d, owner, rnd)
    return entries[-1] if entries else None


def append_ledger(d: Path, owner: str, entry: dict[str, object]) -> None:
    """Record one frozen bundle. An entry already present is never written twice."""
    entries = read_ledger(d, owner)
    if any(item.get("digest") == entry.get("digest") for item in entries):
        return
    publish_json(bundle_ledger(d, owner), {"version": BUNDLE_VERSION, "entries": [*entries, entry]})


def publish_bundle(
    d: Path, owner: str, manifest: dict[str, object], artifacts: dict[str, bytes],
    validate: object = None,
) -> tuple[dict[str, object], Path, list[str]]:
    """Freeze one bundle at its content address, then record it in the ledger.

    The address is the manifest digest, so a later round, a later report edit, or a
    retry can only ever produce a different address. An earlier bundle is never
    reopened for writing, and re-freezing identical evidence is a no-op rather than
    a second copy. The whole directory is assembled beside its address and moved into
    place in one rename, so an interrupted freeze leaves no half-bundle behind and no
    ledger entry pointing at one. The published unit is validated before the ledger
    names it, so a freeze that fails its own integrity check is never a round.
    """
    digest = manifest_digest(manifest)
    manifest["digest"] = digest
    relative = f"{int(manifest['round']):02d}/{digest.split(':')[1][:12]}"
    where = owner_bundles(d, owner) / relative
    entry = {
        "round": manifest["round"],
        "digest": digest,
        "dir": relative,
        "frozen_at": manifest["frozen_at"],
        "trigger": manifest["trigger"],
        "report": manifest["report"]["source"],
    }
    if not (where / BUNDLE_MANIFEST).is_file():
        for stale in owner_bundles(d, owner).glob(".staging-*"):
            shutil.rmtree(stale, ignore_errors=True)
        staging = owner_bundles(d, owner) / f".staging-{digest.split(':')[1][:12]}"
        for name, data in artifacts.items():
            publish_bytes(staging / name, data)
        publish_json(staging / BUNDLE_MANIFEST, manifest)
        where.parent.mkdir(parents=True, exist_ok=True)
        fault("bundle:publish")
        if where.exists():
            shutil.rmtree(staging, ignore_errors=True)
        else:
            os.replace(staging, where)
    problems = list(validate(entry)) if callable(validate) else []
    if not problems:
        append_ledger(d, owner, entry)
    return entry, where, problems


VERIFY_OUTPUT_KEYS = ("stdout_text", "stderr_text", "stdout_bytes", "stderr_bytes")
