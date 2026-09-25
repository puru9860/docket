"""Lifecycle state several stages share: report, decision, and scope state, batches, and stages."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path

from .common import ACTING_ROLES, STATE_DIR, die, stamp
from .frontmatter import checkbox_items, parse, render, sections, set_section
from .paths import _ordered, decisions, handoffs, latest, owners, reports, root, scope_path
from .publication import publish, publish_json
from .policy import authority_for, is_five_role
from .roots import declared_roots, resolve_scope, split_qualified
from .locks import owner_lock_held


def seed_report_acceptance(d: Path, owner: str, rep: Path | None = None) -> bool:
    """Copy the task's acceptance criteria into a report still holding the template.

    The gate requires the report's criteria to match the task's exactly, and a
    worker retyping them is the commonest way to fail it; seeded unchecked
    boxes leave the worker only the honest part, checking what passes. Only an
    untouched placeholder is replaced, so nothing a worker wrote is overwritten.
    """
    if owner == "orch":
        return False
    rep = rep or latest(reports(d, owner))
    task = d / f"{owner}-task.mdx"
    if rep is None or not rep.is_file() or not task.is_file():
        return False
    try:
        meta, body = parse(rep.read_text())
        _, task_body = parse(task.read_text())
    except (OSError, ValueError):
        return False
    if meta.get("status", "draft") != "draft":
        return False
    criteria = [item for item in
                raw_checkbox_lines(sections(task_body).get("Acceptance criteria", ""))
                if "<!--" not in item]
    current = sections(body).get("Acceptance", "")
    written = [item for item in checkbox_items(current) if "<!--" not in item]
    if not criteria or "- [ ] <!-- TODO -->" not in current or written:
        return False
    comment = re.search(r"<!--.*?-->", current.replace("- [ ] <!-- TODO -->", ""), re.S)
    seeded = "\n".join(f"- [ ] {item}" for item in criteria)
    body = set_section(body, "Acceptance",
                       (comment.group(0) + "\n\n" if comment else "") + seeded)
    publish(rep, render(meta, body))
    return True


def normalized_scope(meta: dict[str, str]) -> list[str]:
    """Claimed paths from `files:`, separated by spaces or commas."""
    try:
        values = shlex.split(meta.get("files", ""))
    except ValueError:
        values = []
    parts = [part for value in values for part in value.split(",")]
    return sorted({part.strip("`/ ") for part in parts if part.strip("`/ ")})


def paths_overlap(left: str, right: str) -> bool:
    """Whether two root-qualified scope paths name the same change surface.

    Paths in different checkout roots never overlap, so two worktrees of one
    repository can hold identical relative paths without colliding.
    """
    left_alias, left_path = split_qualified(left)
    right_alias, right_path = split_qualified(right)
    if left_alias != right_alias:
        return False
    if not left_path or not right_path:
        return True
    return (
        left_path == right_path
        or left_path.startswith(right_path + "/")
        or right_path.startswith(left_path + "/")
    )


def scope_collisions(d: Path, owner: str, proposed: list[str]) -> list[str]:
    """Overlapping writers, compared root-qualified so distinct checkouts stay distinct."""
    collisions: list[str] = []
    terminal = {"approved", "waived", "completed"}
    roots = declared_roots(d)
    mine = resolve_scope(roots, proposed)[0]
    for other in owners(d):
        if other in (owner, "orch") or state_of(d, other)[1] in terminal:
            continue
        other_files: list[str] = []
        other_task = d / f"{other}-task.mdx"
        if other_task.is_file():
            tm, _ = parse(other_task.read_text())
            if tm.get("scope_status") == "ready":
                other_files = normalized_scope(tm)
        if not other_files:
            other_capsule = scope_path(d, other)
            if other_capsule.is_file():
                om, _ = parse(other_capsule.read_text())
                if om.get("status") == "ready":
                    other_files = normalized_scope(om)
        theirs = resolve_scope(roots, other_files)[0]
        for path in mine:
            for other_path in theirs:
                if paths_overlap(path, other_path):
                    collisions.append(f"{path} overlaps {other} at {other_path}")
    return sorted(set(collisions))


def names_path(text: str, path: str) -> bool:
    """Whether a report names this exact path, optionally followed by `:line`.

    A substring test let `docs/src/a.py.orig`, `src/a.pyi`, or `tests/src/a.py` stand
    in for `src/a.py`, so a report could omit a change it made and still pass.
    """
    return re.search(rf"(?<![\w./-]){re.escape(path)}(?![\w/.-])", text) is not None


def generated_path_hint(paths: list[str]) -> str:
    """Suggest cleanup only for familiar generated paths outside an owner's scope."""
    generated = []
    for path in paths:
        parts = path.split(":", 1)[-1].split("/")
        if any(part in ("__pycache__", "node_modules") for part in parts) \
                or path.endswith(".pyc"):
            generated.append(path)
    if not generated:
        return ""
    return (" Generated-looking path(s): " + ", ".join(generated)
            + ". Ignore them if disposable, or delete only files you created.")


def applied_decision(d: Path, owner: str) -> tuple[Path, dict[str, str]] | None:
    """The newest decision whose transition already completed, if any."""
    for path in reversed(decisions(d, owner)):
        meta, _ = parse(path.read_text())
        if meta.get("applied") == "yes":
            return path, meta
    return None


def unfinished_decision(d: Path, owner: str) -> tuple[int, str, str]:
    """An interrupted decision transition: (round, verdict, identity), or (0, "", "").

    Derived from the artifacts first and the journal second, as `applied_steps`
    does: a report left changes-requested with no next round, or an applied
    decision over a report still submitted or blocked, is unfinished whatever
    the journal says. A transition whose lock is held is still running, not
    interrupted, and yields nothing.
    """
    try:
        rnd, status = state_of(d, owner)
    except (OSError, ValueError):
        return 0, "", ""
    if not rnd or owner_lock_held(d, owner):
        return 0, "", ""
    journal = read_transition(d, owner)
    txn = str(journal.get("transition", "")) if journal.get("state") == "in-progress" else ""
    if status == "changes-requested":
        return rnd, "changes-requested", txn or f"{owner}-report-{rnd:02d}"
    dec = d / f"{owner}-decision-{rnd:02d}.mdx"
    if status in ("submitted", "blocked") and dec.is_file():
        try:
            dmeta, _ = parse(dec.read_text())
        except (OSError, ValueError):
            dmeta = {}
        if dmeta.get("applied") == "yes" and dmeta.get("verdict"):
            return rnd, str(dmeta["verdict"]), txn or str(dmeta.get("transition", dec.name))
    if txn:
        try:
            journal_round = int(journal.get("round", 0) or 0)
        except (TypeError, ValueError):
            journal_round = 0
        verdict = str(journal.get("verdict", ""))
        if journal_round == rnd or (verdict == "reopen-waived" and journal_round):
            return journal_round, verdict, txn
    return 0, "", ""


DECIDE_FLAGS = {"approved": "--approve", "waived": "--waive",
                "changes-requested": "--changes", "reopen-waived": "--reopen"}


def unfinished_decision_event(d: Path, run: str, owner: str) -> tuple[str, str, int, str] | None:
    """The (key, message, round, revision) waking whoever finishes an interrupted decision."""
    rnd, verdict, txn = unfinished_decision(d, owner)
    if not verdict:
        return None
    flag = DECIDE_FLAGS.get(verdict, f"--{verdict}")
    label = "aggregate report" if owner == "orch" else owner
    return (f"{owner}:{rnd}:unfinished-{verdict}",
            f"{label} round {rnd} has an interrupted {verdict} decision; repeat "
            f"`docket decide {run} {owner} {flag}{decide_as(d)}` to finish it exactly as "
            "recorded",
            rnd, f"sha256:{hashlib.sha256(txn.encode()).hexdigest()}")


def decide_as(d: Path) -> str:
    """The `--as` suffix a decision command needs in this run, or nothing for legacy."""
    if not is_five_role(d):
        return ""
    acting = authority_for(d, "decide:changes")
    return f" --as {acting[0]}" if acting else ""


def transition_path(d: Path, owner: str) -> Path:
    return d / ".transitions" / f"{owner}.json"


def read_transition(d: Path, owner: str) -> dict:
    """The owner's transition journal, or an empty dict when there is none."""
    path = transition_path(d, owner)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def state_of(d: Path, owner: str) -> tuple[int, str]:
    rep = latest(reports(d, owner))
    if not rep:
        return 0, "unassigned"
    meta, _ = parse(rep.read_text())
    return int(meta.get("round", "1")), meta.get("status", "?")


def handoff_state(d: Path, owner: str) -> tuple[int, str]:
    checkpoint = latest(handoffs(d, owner))
    if not checkpoint:
        return 0, "-"
    meta, _ = parse(checkpoint.read_text())
    return int(meta.get("handoff", "1")), meta.get("status", "?")


def scope_state(d: Path, owner: str) -> str:
    capsule = scope_path(d, owner)
    if capsule.is_file():
        meta, _ = parse(capsule.read_text())
        return meta.get("status", "?")
    task = d / f"{owner}-task.mdx"
    if task.is_file():
        meta, _ = parse(task.read_text())
        return meta.get("scope_status", "-")
    return "-"


def reopens_path(d: Path, owner: str) -> Path:
    return d / ".reopens" / f"{owner}.json"


def read_reopens(d: Path, owner: str) -> list[dict] | None:
    """Every completed reopen of an owner, oldest first, or None for a run from before."""
    path = reopens_path(d, owner)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return [item for item in data.get("reopens", []) if isinstance(item, dict)] \
        if isinstance(data, dict) else None


def record_reopen(d: Path, owner: str, journal: dict) -> None:
    """Append one reopen to the owner's durable ledger, once per transition.

    The transition journal is one file per owner that the next decision replaces, so
    a generation read from it fell back to 1 after the reopened round's first verdict,
    and an incident from before the reopen came back as live.
    """
    entries = read_reopens(d, owner) or []
    txn = str(journal.get("transition", ""))
    if any(str(item.get("transition", "")) == txn for item in entries):
        return
    entries.append({"transition": txn, "round": int(journal.get("round", 0) or 0),
                    "at": stamp()})
    publish_json(reopens_path(d, owner), {"owner": owner, "reopens": entries})


def reopen_epoch(d: Path, owner: str, rnd: int) -> int:
    """The reopen generation an event for this owner and round belongs to.

    The first derivation epoch is 1. A completed `reopen-waived` transition
    moves later rounds into epoch 2, so an event derived before the reopen and
    one derived after it never share an identity even when owner and round
    text match. Batch generations (milestone 6) extend this, not replace it.
    """
    if not owner:
        return 1
    reopens = read_reopens(d, owner)
    if reopens is not None:
        # One generation per completed reopen of this round or an earlier one.
        return 1 + sum(1 for item in reopens if int(item.get("round", 0) or 0) <= rnd)
    # A run from before the ledger still reads its one reopen from the journal.
    journal = read_transition(d, owner)
    if journal.get("verdict") == "reopen-waived" and journal.get("state") == "complete":
        try:
            journal_round = int(journal.get("round", 0) or 0)
        except (TypeError, ValueError):
            return 1
        if journal_round <= rnd:
            return 2
    return 1


ROLES = ACTING_ROLES


def armed() -> list[tuple[str, str]]:
    """Every (run, role) pair armed in .docket/watch.conf. One pair per line."""
    conf = root() / STATE_DIR / "watch.conf"
    if not conf.is_file():
        return []
    out: list[tuple[str, str]] = []
    for line in conf.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        role = parts[1] if len(parts) > 1 else "orchestrator"
        if role in ROLES and (parts[0], role) not in out:
            out.append((parts[0], role))
    return out


def batches_dir(d: Path) -> Path:
    return d / ".batches"


def batch_path(d: Path, bid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", bid or ""):
        die(f"invalid batch id {bid!r}: use letters, digits, '.', '_' or '-'")
    return batches_dir(d) / f"{bid}.json"


def read_batch(d: Path, bid: str) -> dict:
    path = batch_path(d, bid)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_batches(d: Path) -> list[dict]:
    out = []
    if batches_dir(d).is_dir():
        for path in sorted(batches_dir(d).glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("batch"):
                out.append(data)
    return out


def incidents_dir(d: Path) -> Path:
    return d / ".incidents"


def list_incidents(d: Path, owner: str = "") -> list[dict]:
    out = []
    if incidents_dir(d).is_dir():
        for path in sorted(incidents_dir(d).glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and (not owner or data.get("owner") == owner):
                out.append(data)
    return out


def corrections_path(d: Path, owner: str) -> Path:
    return d / ".corrections" / f"{owner}.json"


def read_corrections(d: Path, owner: str) -> dict:
    path = corrections_path(d, owner)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Verifier returns are derived from the verifications they name, so a
    # charge for a fail that never opened a correction (an older build charged
    # every fail) stops counting against the budget.
    counted = data.get("counted") if isinstance(data.get("counted"), list) else []
    named = [str(t) for t in counted if re.fullmatch(rf"{re.escape(owner)}-verification-\d+\.mdx",
                                                    str(t))]
    if named:
        opened = 0
        for name in named:
            try:
                meta, _ = parse((d / name).read_text())
            except (OSError, ValueError):
                opened += 1
                continue
            # Only a correction that opened, or is opening, returned anything. One
            # the budget refused (`escalated`) never reached the implementor.
            opened += str(meta.get("opened_correction", "")) in ("requested", "yes")
        data["verifier_returns"] = opened
    return data


def verification_paths(d: Path, owner: str, rnd: int = 0) -> list[Path]:
    found = _ordered(d.glob(f"{owner}-verification-*.mdx"))
    if rnd:
        found = [p for p in found
                 if parse(p.read_text())[0].get("round", "") == str(rnd)]
    return found


def latest_verification(d: Path, owner: str, rnd: int) -> tuple[Path | None, dict, str]:
    """The newest verification artifact for a round, with its metadata and body."""
    paths = verification_paths(d, owner, rnd)
    newest = latest(paths)
    if newest is None:
        return None, {}, ""
    meta, body = parse(newest.read_text())
    return newest, meta, body


def group_numbered_items(raw_lines: list[str]) -> list[str]:
    """Fold continuation lines into the numbered item they follow.

    A numbered line (`1. ...`) starts a new item; a following non-blank,
    non-numbered line - typically an indented constraint - belongs to that
    item and is carried verbatim, so multiline findings survive into the
    generated decision and the implementation prompt. Stray prose before the
    first numbered line is dropped: only numbered items count as required
    changes, which keeps the correction validation intact.
    """
    items: list[str] = []
    for line in raw_lines:
        if re.match(r"^\s*\d+[.)]\s+\S", line):
            items.append(line.strip())
        elif items:
            items[-1] += "\n" + line.rstrip()
    return items


def raw_checkbox_lines(text: str) -> list[str]:
    """Acceptance labels exactly as written, without whitespace normalization.

    The gate normalizes for comparison; the prompt preserves literals so a
    command, fence, or table round-trips byte-identical.
    """
    out: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*-\s*\[[ xX]\]\s*(.+?)\s*$", line)
        if match:
            out.append(match.group(1).strip())
    return out


def ready_handoff(d: Path, owner: str) -> Path | None:
    """The latest handoff that is actually ready, or None when none is ready."""
    ready: list[Path] = []
    for path in handoffs(d, owner):
        try:
            meta, _ = parse(path.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("status", "") == "ready":
            ready.append(path)
    return latest(ready)


def latest_checkpoint(d: Path, owner: str) -> Path | None:
    """The latest mechanical resume checkpoint, or None when none exists."""
    try:
        paths = _ordered(checkpoints_dir(d).glob(f"{owner}-*.json"))
    except OSError:
        return None
    return latest(paths)


def resumable_checkpoint(d: Path, owner: str, rnd: int) -> Path | None:
    """The latest checkpoint of round rnd that measured work, or None.

    A replacement for a worker that never changed anything starts the round
    fresh; only measured changes, or evidence that could not be measured,
    make it a resume.
    """
    path = latest_checkpoint(d, owner)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return path
    if str(data.get("round", rnd)) != str(rnd):
        return None
    return None if data.get("work") == "none" else path


def derive_stage(d: Path, owner: str) -> str:
    """Derive the prompt stage from lifecycle documents. Never trusts a flag.

    Stages: initial implementation, correction, resume, verification, review.
    An explicit flag is for inspection only; dispatch always derives.
    """
    try:
        rnd, status = state_of(d, owner)
    except (OSError, ValueError):
        return "initial"
    decs = _ordered(d.glob(f"{owner}-decision-*.mdx"))
    last_verdict = ""
    last_dec_round = 0
    if decs:
        try:
            dmeta, _ = parse(decs[-1].read_text())
            last_verdict = str(dmeta.get("verdict", ""))
            last_dec_round = int(dmeta.get("round", "0") or 0)
        except (OSError, ValueError):
            last_verdict = ""
    if status == "submitted":
        paths = _ordered(d.glob(f"{owner}-verification-*.mdx"))
        resolved = False
        for path in paths:
            try:
                vmeta, _ = parse(path.read_text())
            except (OSError, ValueError):
                continue
            if str(vmeta.get("round", "")) != str(rnd):
                continue
            if str(vmeta.get("result", "")) in ("pass", "uncertain"):
                resolved = True
                break
        return "review" if resolved else "verification"
    if status == "draft" and rnd:
        if last_verdict == "changes-requested" and rnd > last_dec_round:
            return "correction"
        if ready_handoff(d, owner) is not None:
            return "resume"
        if resumable_checkpoint(d, owner, rnd) is not None:
            return "resume"
        if decs and rnd > 1:
            return "correction"
        return "initial"
    if status == "blocked":
        # An honest block awaits a reviewer verdict (waive or changes), exactly
        # like a submission awaits review. It carries no decision artifact and
        # no numbered required changes, so deriving correction would point the
        # reviewer at a decision that does not exist.
        return "review"
    if status == "changes-requested":
        return "correction"
    if status in ("approved", "completed", "waived"):
        return "review"
    return "initial"


def task_depends_on(d: Path, owner: str) -> list[str]:
    """Explicit task dependencies from flat frontmatter `depends_on:`."""
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return []
    try:
        raw = parse(task.read_text())[0].get("depends_on", "")
    except (OSError, ValueError):
        return []
    return sorted({item.strip() for item in raw.split(",") if item.strip()})


def _dependency_graph(d: Path) -> dict[str, set[str]]:
    """Existing task and batch edges, one shared graph every ingress check uses."""
    graph: dict[str, set[str]] = {}
    for owner in owners(d):
        if owner == "orch":
            continue
        for dep in task_depends_on(d, owner):
            graph.setdefault(owner, set()).add(dep)
            graph.setdefault(dep, set())
    for batch in list_batches(d):
        deps_map = batch.get("depends_on") or {}
        if not isinstance(deps_map, dict):
            continue
        for member, ds in deps_map.items():
            try:
                dep_list = [str(x) for x in (ds or [])]
            except (TypeError, ValueError):
                continue
            for dep in dep_list:
                graph.setdefault(str(member), set()).add(dep)
                graph.setdefault(dep, set())
    return graph


def _has_path(graph: dict[str, set[str]], src: str, dst: str,
              exclude: tuple[str, str] | None = None) -> list[str] | None:
    """A path from src to dst, or None. Excludes one edge when named."""
    stack: list[tuple[str, list[str]]] = [(src, [src])]
    visited: set[str] = set()
    while stack:
        node, path = stack.pop()
        if node == dst:
            return path
        if node in visited:
            continue
        visited.add(node)
        for nxt in sorted(graph.get(node, ())):
            if exclude is not None and node == exclude[0] and nxt == exclude[1]:
                continue
            if nxt not in visited:
                stack.append((nxt, path + [nxt]))
    return None


def _cycle_for_new_edges(d: Path, new_edges: dict[str, list[str]]) -> list[str] | None:
    """A cycle involving the proposed edges, or None.

    One shared check for every ingress surface, so assign and batch cannot
    drift. Only a cycle that uses a new edge refuses: a run that already
    holds a cycle stays readable and new unrelated work still enters, which
    is the honest behavior for history created before this refusal existed.
    Re-declaring an unchanged edge changes nothing and still succeeds.
    """
    existing = _dependency_graph(d)
    combined: dict[str, set[str]] = {k: set(v) for k, v in existing.items()}
    for owner, deps in new_edges.items():
        for dep in deps:
            combined.setdefault(owner, set()).add(dep)
            combined.setdefault(dep, set())
    for owner, deps in new_edges.items():
        for dep in deps:
            if owner == dep:
                return [owner, owner]
            path = _has_path(combined, dep, owner, exclude=(owner, dep))
            if path is not None:
                return [owner] + path
    return None


def checkpoints_dir(d: Path) -> Path:
    return d / ".checkpoints"


def amendments_dir(d: Path) -> Path:
    return d / ".amendments"


def list_amendments(d: Path, owner: str = "") -> list[dict]:
    out = []
    if amendments_dir(d).is_dir():
        for path in sorted(amendments_dir(d).glob("*.json")):
            if path.name == "accepted.json":
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and (not owner or data.get("owner") == owner):
                out.append(data)
    return out
