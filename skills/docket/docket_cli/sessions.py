"""Harness session discovery, token usage, and session archives."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .common import MODE_QUICK, now_s, stamp
from .frontmatter import parse
from .paths import planned_tasks, root, run_dir
from .publication import publish_json
from .policy import is_quick_milestone, mode_of, need_run
from .state import list_batches, state_of
from .feedback import ensure_private_dir, feedback_log_path, log_feedback
from .hook_config import codex_hook_configuration


#
# Which harness session played which role, so each role's token usage and full
# conversation can be reviewed after a run. Claude Code and Codex name their
# session in the environment of every command they run. OpenCode names only its
# process, so its session is the one whose running shell call is this very
# command. Environment inherited from an unrelated session is never trusted:
# the harness process has to be an ancestor of this one.

HARNESS_SESSION_LEDGER = ".harness-sessions.jsonl"
SESSION_ROLE_COMMANDS = ("watch", "arm", "feedback", "submit", "handoff", "verify", "decide",
                         "assign", "dispatch", "resume", "batch", "release", "session")


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or Path.home() / ".claude")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "").strip() or Path.home() / ".codex")


def opencode_db() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(Path.home() / ".local" / "share")
    return Path(base) / "opencode" / "opencode.db"


def opencode_default_model() -> str:
    """The model a new OpenCode session starts on: the config's `model`, else the last one used."""
    config = Path(os.environ.get("XDG_CONFIG_HOME", "").strip() or Path.home() / ".config") / "opencode"
    for name in ("opencode.json", "opencode.jsonc"):
        try:
            text = (config / name).read_text()
        except OSError:
            continue
        # JSONC: drop comments and trailing commas; an unparseable file names no model.
        text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text,
                      flags=re.S)
        try:
            model = json.loads(re.sub(r",(\s*[}\]])", r"\1", text)).get("model")
        except (ValueError, AttributeError):
            model = None
        if isinstance(model, str) and model.strip():
            return model.strip()
    state = Path(os.environ.get("XDG_STATE_HOME", "").strip() or Path.home() / ".local" / "state")
    try:
        recent = json.loads((state / "opencode" / "model.json").read_text()).get("recent") or []
        first = recent[0]
        return f"{first['providerID']}/{first['modelID']}"
    except (OSError, ValueError, AttributeError, IndexError, KeyError, TypeError):
        return ""


def process_ancestors(limit: int = 48) -> list[tuple[int, str]] | None:
    """(pid, command name) from the parent upward, or None when unreadable."""
    chain: list[tuple[int, str]] = []
    pid = os.getppid()
    proc = Path("/proc")
    for _ in range(limit):
        if pid <= 0:
            break
        try:
            if proc.is_dir():
                comm = (proc / str(pid) / "comm").read_text().strip()
                ppid = int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
            else:
                out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                     capture_output=True, text=True, timeout=5).stdout.split(None, 1)
                ppid, comm = int(out[0]), Path(out[1].strip()).name
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return chain or None
        chain.append((pid, comm))
        if pid == 1:
            break
        pid = ppid
    return chain


def calling_harness() -> str:
    """claude, codex, or opencode when that harness runs this command, else ''."""
    env = os.environ
    markers = (("claude", "CLAUDE_CODE_SESSION_ID"), ("codex", "CODEX_THREAD_ID"),
               ("opencode", "OPENCODE_PID"))
    candidates = [name for name, var in markers if env.get(var, "").strip()]
    if not candidates:
        return ""
    chain = process_ancestors()
    if chain is None:
        return candidates[0] if len(candidates) == 1 else ""
    # A harness that names its own process is matched by that pid alone, so a
    # stale variable never attaches to an unrelated process of the same name.
    claude_pid = env.get("CLAUDE_PID", "").strip()
    for pid, comm in chain:
        name = comm.lower()
        if "claude" in candidates and (str(pid) == claude_pid if claude_pid
                                       else name == "claude"):
            return "claude"
        if "opencode" in candidates and str(pid) == env.get("OPENCODE_PID", "").strip():
            return "opencode"
        if "codex" in candidates and name.startswith("codex"):
            return "codex"
    return ""


def codex_app_server(pid: int) -> bool:
    """Whether pid is a Codex app-server rather than a Codex window."""
    try:
        argv = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        return b"app-server" in argv[1:]
    except OSError:
        pass
    try:
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
    return "app-server" in args[1:]


def harness_process_ref() -> dict[str, object]:
    """The calling harness process identity, when an ancestor proves the claim."""
    harness = calling_harness()
    if not harness:
        return {}
    chain = process_ancestors() or []
    for pid, comm in chain:
        if harness == "claude":
            marker = os.environ.get("CLAUDE_PID", "").strip()
            if (marker and str(pid) != marker) or (not marker and comm.lower() != "claude"):
                continue
        if harness == "opencode" and str(pid) != os.environ.get("OPENCODE_PID", "").strip():
            continue
        if harness == "codex" and not comm.lower().startswith("codex"):
            continue
        # Codex runs tools under one app-server shared by every Codex window; its
        # pid names no session, and its parent is whichever window started it.
        if harness == "codex" and codex_app_server(pid):
            return {}
        start = ""
        try:
            start = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]
        except (OSError, ValueError, IndexError):
            pass
        return {"harness": harness, "pid": pid, "start": start, "noted_at": stamp()}
    return {}


def opencode_command_argv(command: str) -> list[str] | None:
    """Decode one literal Docket invocation; never expand or execute shell text."""
    if any(char in command for char in ("$", "`", "\n", "\r")):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    if not tokens or any(token and all(char in ";&|()<>" for char in token) for token in tokens):
        return None
    if Path(tokens[0]).name == "docket":
        return tokens[1:]
    if (len(tokens) >= 2 and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(tokens[0]).name)
            and Path(tokens[1]).name == "docket"):
        return tokens[2:]
    return None


def _opencode_root(con: object, sid: str) -> str:
    """The top-level session of a subagent session: the TUI the user sees.

    A subagent's usage is already summed under its root, so noting the child as
    its own session would count those tokens twice and hide whose they are.
    """
    import sqlite3
    seen = {sid}
    while True:
        try:
            row = con.execute("select parent_id from session where id = ?", (sid,)).fetchone()
        except sqlite3.Error:  # an older schema without parent_id has no subagents
            return sid
        parent = str(row[0]) if row and row[0] else ""
        if not parent or parent in seen or len(seen) > 64:
            return sid
        seen.add(parent)
        sid = parent


def opencode_running_session(needles: list[str], *, require_unique: bool = False,
                             command_argv: list[str] | None = None) -> tuple[str, str]:
    """The matching running shell session and version; optionally refuse ambiguity."""
    try:
        import sqlite3
    except ImportError:
        return "", ""
    db = opencode_db()
    if not db.is_file():
        return "", ""
    since = int((time.time() - 3600) * 1000)
    matched: tuple[str, str] = ("", "")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            # OpenCode indexes part.session_id, but its session update time is
            # not indexed. Sort the small session table first, then probe parts
            # by session until this command is found.
            sessions = con.execute(
                "select id, version from session where time_updated >= ? "
                "order by time_updated desc", (since,))
            for sid, version in sessions:
                parts = con.execute(
                    "select data from part where session_id = ? and time_updated >= ? "
                    "and data like '%\"running\"%' order by time_updated desc",
                    (sid, since))
                for (data,) in parts:
                    try:
                        part = json.loads(data)
                    except ValueError:
                        continue
                    state = part.get("state") if isinstance(part, dict) else None
                    if not isinstance(state, dict) or part.get("type") != "tool" \
                            or state.get("status") != "running":
                        continue
                    command = str((state.get("input") or {}).get("command", ""))
                    matches = (opencode_command_argv(command) == command_argv
                               if command_argv is not None else all(needle in command for needle in needles))
                    if matches:
                        candidate = (_opencode_root(con, str(sid)), str(version or ""))
                        if not require_unique:
                            return candidate
                        if matched[0] and matched[0] != candidate[0]:
                            return "", ""
                        matched = candidate
        finally:
            con.close()
    except sqlite3.Error:
        return "", ""
    return matched


def harness_session(needles: list[str], *, require_unique: bool = False,
                    command_argv: list[str] | None = None) -> dict[str, str] | None:
    """The harness session running this command, or None outside a known harness.

    `DOCKET_SESSION_CAPTURE=off` disables detection entirely.
    """
    if os.environ.get("DOCKET_SESSION_CAPTURE", "").strip().lower() in ("off", "0", "no", "none"):
        return None
    harness = calling_harness()
    if not harness:
        return None
    try:
        ref = {"harness": harness, "cwd": os.getcwd()}
    except OSError:
        ref = {"harness": harness, "cwd": ""}
    if harness == "claude":
        ref["session_id"] = os.environ["CLAUDE_CODE_SESSION_ID"].strip()
        version = Path(os.environ.get("CLAUDE_CODE_EXECPATH", "")).name
    elif harness == "codex":
        ref["session_id"] = os.environ["CODEX_THREAD_ID"].strip()
        version = os.environ.get("CODEX_VERSION", "").strip()
    else:
        ref["session_id"], version = (
            opencode_running_session(needles, require_unique=require_unique, command_argv=command_argv)
            if require_unique or command_argv is not None else opencode_running_session(needles))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", ref["session_id"]):
        return None
    if version:
        ref["version"] = version
    return ref


def note_harness_session(d: Path, role: str, owner: str,
                         needles: list[str]) -> dict[str, str] | None:
    """Append session identity and newly measured reviewer models. Never raises."""
    ref = harness_session(needles)
    if not ref:
        return None
    process = harness_process_ref()
    entry = {"role": role, "owner": owner or "-", **ref,
             **({"worker_process": process} if process else {})}
    identity = ("harness", "session_id", "role", "owner")
    try:
        with (d / HARNESS_SESSION_LEDGER).open("a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                matching = []
                for line in fh:
                    try:
                        seen = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(seen, dict) or any(
                            seen.get(k) != entry[k] for k in ("harness", "session_id")):
                        continue
                    # A session plays one role per run. A supervisor arming another
                    # role or handing off a task speaks for that role without
                    # playing it, and noting it would attribute its tokens there.
                    if seen.get("role") != role:
                        return None
                    matching.append(seen)
                if role == "reviewer":
                    first_seen = min((str(seen.get("at", "")) for seen in matching), default=stamp())
                    observed_ref = {**ref, "first_seen": first_seen}
                    observed_ref["until"] = session_until(d, observed_ref)
                    models = observed_session_models(observed_ref)
                    if models:
                        entry["models_observed"] = models
                for seen in matching:
                    same_process = all((seen.get("worker_process") or {}).get(k)
                                       == process.get(k) for k in ("pid", "start"))
                    same_models = (not entry.get("models_observed") or
                                   seen.get("models_observed") == entry["models_observed"])
                    if all(seen.get(k) == entry[k] for k in identity) and same_process and same_models:
                        return ref
                fh.write(json.dumps({"at": stamp(), **entry}, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    return ref


def command_session_role(a: argparse.Namespace, d: Path) -> tuple[str, str]:
    """The role and task a command speaks for, or ('', '') when it names none."""
    owner = str(getattr(a, "owner", "") or getattr(a, "task", "") or "")
    if a.cmd in ("watch", "arm"):
        return str(getattr(a, "role", "") or ""), ""
    if a.cmd == "session":
        return (str(getattr(a, "role", "") or "") if getattr(a, "register", False) else ""), ""
    if a.cmd == "feedback":
        return (str(a.role or ""), owner) if a.add else ("", "")
    quick = mode_of(d) == MODE_QUICK and not is_quick_milestone(d)
    explicit = str(getattr(a, "as_role", "") or "")
    if a.cmd in ("submit", "handoff"):
        if explicit:
            return explicit, owner
        if owner == "orch":
            return ("coordinator" if quick else "implementor" if is_quick_milestone(d)
                    else "orchestrator"), owner
        return "implementor", owner
    if a.cmd == "verify":
        return explicit or ("checker" if quick else "verifier"), owner
    if a.cmd == "decide":
        return explicit or ("checker" if quick else "reviewer"), owner
    if explicit:
        return explicit, ""
    if a.cmd == "batch" and getattr(a, "list", False):
        return "", ""
    return ("coordinator" if quick else "implementor" if is_quick_milestone(d)
            else "orchestrator"), ""


def note_command_session(a: argparse.Namespace) -> None:
    """Record the calling harness session when a command speaks for a role."""
    a.harness_session_ref = None
    a.harness_session_role = ""
    if a.cmd not in SESSION_ROLE_COMMANDS or not getattr(a, "run", ""):
        return
    try:
        d = run_dir(a.run)
        if not d.is_dir():
            return
        role, owner = command_session_role(a, d)
    except SystemExit:
        return
    # A session started for one role says so in DOCKET_ROLE; a command it runs on
    # another role's behalf is not that role's session.
    declared = os.environ.get("DOCKET_ROLE", "").strip()
    if role and (not declared or declared == role):
        a.harness_session_ref = note_harness_session(d, role, owner, [a.run, a.cmd])
        a.harness_session_role = role if a.harness_session_ref else ""


def run_harness_sessions(d: Path) -> list[dict[str, object]]:
    """Every harness session noted in a run, merged across the roles it played."""
    try:
        lines = (d / HARNESS_SESSION_LEDGER).read_text().splitlines()
    except OSError:
        return []
    merged: dict[tuple[str, str], dict[str, object]] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not entry.get("session_id"):
            continue
        key = (str(entry.get("harness", "")), str(entry["session_id"]))
        ref = merged.setdefault(key, {"harness": key[0], "session_id": key[1], "roles": [],
                                      "owners": [], "cwd": str(entry.get("cwd", "")),
                                      "version": str(entry.get("version", "")),
                                      "first_seen": str(entry.get("at", ""))})
        seen_at = str(entry.get("at", ""))
        if seen_at and (not ref["first_seen"] or seen_at < ref["first_seen"]):
            ref["first_seen"] = seen_at
        for field, value in (("roles", entry.get("role")), ("owners", entry.get("owner"))):
            if value and value != "-" and value not in ref[field]:
                ref[field].append(str(value))
        if isinstance(entry.get("worker_process"), dict):
            ref["worker_process"] = entry["worker_process"]
        if isinstance(entry.get("models_observed"), list):
            ref["models_observed"] = entry["models_observed"]
    return list(merged.values())


def session_noted_roles(d: Path, ref: dict[str, str]) -> list[str]:
    """Roles this run noted for one harness session, or [] for a stranger.

    A harness session plays one role per run, and the ledger is the
    registration a watcher checks when DOCKET_ROLE is unset. A terminal
    watcher with no harness session has no entry and is never refused here.
    """
    roles: list[str] = []
    for noted in run_harness_sessions(d):
        if str(noted.get("harness", "")) != str(ref.get("harness", "")) \
                or str(noted.get("session_id", "")) != str(ref.get("session_id", "")):
            continue
        for role in noted.get("roles", []):
            if role and role not in roles:
                roles.append(str(role))
    return roles


def session_transcripts(ref: dict[str, object]) -> list[Path]:
    """A session's own transcript files, main first. OpenCode keeps its in a database."""
    sid = str(ref.get("session_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sid):
        return []
    if ref.get("harness") == "claude":
        mains = sorted((claude_config_dir() / "projects").glob(f"*/{sid}.jsonl"))
        if not mains:
            return []
        extra = mains[0].parent / sid
        return [mains[0], *sorted(p for p in extra.rglob("*") if p.is_file())]
    if ref.get("harness") == "codex":
        return codex_thread_shards(sid)
    return []


def _codex_shard_thread(path: Path) -> str:
    """The thread id a rollout's opening session_meta names, or ''."""
    try:
        with path.open(errors="replace") as fh:
            for _ in range(8):
                line = fh.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                payload = record.get("payload") if isinstance(record, dict) else None
                if record.get("type") == "session_meta" and isinstance(payload, dict):
                    return str(payload.get("id", "") or "")
    except OSError:
        pass
    return ""


def codex_thread_shards(sid: str) -> list[Path]:
    """Every rollout of one Codex thread, oldest first.

    A resumed thread writes a new `rollout-<time>-<sid>_<shard>.jsonl`, often in a
    later day's directory, whose cumulative token counter continues the original's.
    Only a shard whose session_meta names this exact thread is accepted.
    """
    home = codex_home()
    by_name: dict[str, Path] = {}
    for base in ("sessions/*/*/*", "archived_sessions"):
        for path in home.glob(f"{base}/rollout-*-{sid}.jsonl"):
            by_name.setdefault(path.name, path)
        for path in home.glob(f"{base}/rollout-*-{sid}_*.jsonl"):
            if path.name not in by_name and _codex_shard_thread(path) == sid:
                by_name[path.name] = path
    # The name starts with the shard's creation time, so name order is time order.
    return [by_name[name] for name in sorted(by_name)]


def session_transcript_path(ref: dict[str, object]) -> str:
    """The transcript path for a session without parsing the transcript itself."""
    sid = str(ref.get("session_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sid):
        return ""
    if ref.get("harness") == "opencode":
        try:
            import sqlite3
        except ImportError:
            return ""
        db = opencode_db()
        if not db.is_file():
            return ""
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                row = con.execute("select 1 from session where id = ?", (sid,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return ""
        return f"{db}#{sid}" if row else ""
    files = session_transcripts(ref)
    return str(files[0]) if files else ""


USAGE_COUNTS = ("input", "cache_read", "cache_write", "output", "reasoning", "total")


def empty_usage() -> dict[str, object]:
    """`telemetry` is `missing` without a transcript, `empty` when one holds no call
    inside the window, and `measured` otherwise; only `measured` is a token count."""
    return {"found": False, "telemetry": "missing", "calls": 0,
            **{field: 0 for field in USAGE_COUNTS}, "cost": 0.0, "models": [], "by_model": {},
            "started": "", "ended": "", "since": "", "until": "", "harness_cwd": "",
            "transcript": ""}


def _span(usage: dict[str, object], when: str) -> None:
    if when:
        usage["started"] = min(str(usage["started"]) or when, when)
        usage["ended"] = max(str(usage["ended"]), when)


def _instant(when: str) -> datetime | None:
    try:
        return datetime.fromisoformat(when.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def _in_window(when: str, since: str, until: str = "") -> bool:
    """`since` is inclusive and `until` exclusive; an empty bound is open."""
    if not since and not until:
        return True
    moment = _instant(when) if when else None
    if moment is None:
        return False
    lower, upper = _instant(since) if since else None, _instant(until) if until else None
    return (lower is None or moment >= lower) and (upper is None or moment < upper)


def _call(when: str, model: str, counts: dict[str, int], cost: float = 0.0) -> dict[str, object]:
    return {"at": when, "model": model, "cost": cost,
            **{field: int(counts.get(field) or 0) for field in USAGE_COUNTS}}


def _jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def claude_calls(files: list[Path]) -> tuple[list[dict], list[str], str]:
    """(calls, activity times, cwd) from a Claude Code transcript and its subagents."""
    calls: dict[str, dict] = {}
    times: list[str] = []
    cwd = ""
    for path in (p for p in files if p.suffix == ".jsonl"):
        for record in _jsonl(path):
            when = str(record.get("timestamp", "") or "")
            if not cwd and record.get("cwd"):
                cwd = str(record["cwd"])
            if when:
                times.append(when)
            message = record.get("message")
            if record.get("type") != "assistant" or not isinstance(message, dict):
                continue
            model = str(message.get("model", "") or "")
            counts = message.get("usage")
            if not model or model.startswith("<") or not isinstance(counts, dict):
                continue
            call = calls.setdefault(f"{path.name}:{message.get('id') or record.get('uuid')}",
                                    _call(when, model, {}))
            # A streamed API message repeats its usage on each content block, and the
            # largest value of each field is the message's final count.
            for field, source in (("input", "input_tokens"),
                                  ("cache_write", "cache_creation_input_tokens"),
                                  ("cache_read", "cache_read_input_tokens"),
                                  ("output", "output_tokens")):
                call[field] = max(int(call[field]), int(counts.get(source) or 0))
    for call in calls.values():
        call["total"] = sum(int(call[f]) for f in ("input", "cache_write", "cache_read", "output"))
    return list(calls.values()), times, cwd


CODEX_COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                  "output_tokens", "reasoning_output_tokens", "total_tokens")


def codex_calls(files: list[Path]) -> tuple[list[dict], list[str], str]:
    """(calls, activity times, cwd) from a Codex thread's rollouts, oldest shard first.

    Codex logs a cumulative counter, so a call is the advance between two readings.
    A repeated reading advances nothing and is not another call; a counter that
    goes backwards started over.
    """
    calls: list[dict] = []
    times: list[str] = []
    cwd = ""
    model = ""
    previous: dict[str, int] = {}
    when = ""
    for record in (r for path in files for r in _jsonl(path)):
        when = str(record.get("timestamp", "") or "") or when
        if when:
            times.append(when)
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "session_meta" and payload.get("cwd") and not cwd:
            cwd = str(payload["cwd"])
        if record.get("type") == "turn_context" and payload.get("model"):
            model = str(payload["model"])
        if not (record.get("type") == "event_msg" and payload.get("type") == "token_count"
                and isinstance(payload.get("info"), dict)):
            continue
        counts = payload["info"].get("total_token_usage")
        if not isinstance(counts, dict):
            continue
        current = {key: int(counts.get(key) or 0) for key in CODEX_COUNTERS}
        if current["total_tokens"] < previous.get("total_tokens", 0):
            previous = {}
        delta = {key: max(0, current[key] - previous.get(key, 0)) for key in CODEX_COUNTERS}
        previous = current
        if not delta["total_tokens"]:
            continue
        cached, written = delta["cached_input_tokens"], delta["cache_write_input_tokens"]
        calls.append(_call(when, model, {
            "input": max(0, delta["input_tokens"] - cached - written), "cache_read": cached,
            "cache_write": written, "output": delta["output_tokens"],
            "reasoning": delta["reasoning_output_tokens"], "total": delta["total_tokens"]}))
    return calls, times, cwd


def opencode_calls(rows: dict[str, object] | None) -> tuple[list[dict], list[str], str]:
    """(calls, activity times, cwd) from an OpenCode session and its subagent sessions."""
    if not rows:
        return [], [], ""
    calls: list[dict] = []
    times: list[str] = []
    for message in rows["messages"]:
        info = message["info"] if isinstance(message.get("info"), dict) else {}
        created = (info.get("time") or {}).get("created")
        if not isinstance(created, (int, float)):
            continue
        when = stamp(created / 1000)
        times.append(when)
        if info.get("role") != "assistant":
            continue
        tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        parts = {"input": tokens.get("input"), "output": tokens.get("output"),
                 "reasoning": tokens.get("reasoning"), "cache_read": cache.get("read"),
                 "cache_write": cache.get("write")}
        total = int(tokens.get("total") or 0) or sum(int(v or 0) for v in parts.values())
        model = "/".join(str(v) for v in (info.get("providerID"), info.get("modelID")) if v)
        calls.append(_call(when, model, {**parts, "total": total}, float(info.get("cost") or 0)))
    return calls, times, str(rows["sessions"][0].get("directory", "") or "")


def summarize_calls(calls: list[dict], times: list[str], since: str = "",
                    until: str = "") -> dict[str, object]:
    """Usage inside [since, until), with a per-model split taken call by call."""
    usage = empty_usage()
    usage["since"], usage["until"] = since, until
    for when in times:
        if _in_window(when, since, until):
            _span(usage, when)
    for call in calls:
        if not _in_window(str(call["at"]), since, until):
            continue
        usage["calls"] += 1
        usage["cost"] += float(call.get("cost") or 0)
        for field in USAGE_COUNTS:
            usage[field] += int(call[field])
        model = str(call["model"])
        if not model:
            continue
        if model not in usage["models"]:
            usage["models"].append(model)
        slot = usage["by_model"].setdefault(model, {"calls": 0, **{f: 0 for f in USAGE_COUNTS}})
        slot["calls"] += 1
        for field in USAGE_COUNTS:
            slot[field] += int(call[field])
    usage["telemetry"] = "measured" if usage["calls"] else "empty"
    return usage


def claude_session_usage(files: list[Path], since: str = "", until: str = "") -> dict[str, object]:
    """Tokens from a Claude Code transcript and its subagents, one count per API message."""
    calls, times, cwd = claude_calls(files)
    usage = summarize_calls(calls, times, since, until)
    usage["harness_cwd"] = cwd
    return usage


def codex_session_usage(files: list[Path], since: str = "", until: str = "") -> dict[str, object]:
    """Tokens from a Codex thread's cumulative counter after the role joined the run."""
    calls, times, cwd = codex_calls(files)
    usage = summarize_calls(calls, times, since, until)
    usage["harness_cwd"] = cwd
    return usage


CODEX_LIMIT_WARN_PERCENT = {"primary": 70, "secondary": 90}


def _codex_limit_reading(path: Path) -> dict | None:
    """The last usage-limit reading in one Codex rollout, read from its tail."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            for window in (256 * 1024, 4 * 1024 * 1024):
                fh.seek(max(0, size - window))
                lines = fh.read().splitlines()
                if size > window:
                    lines = lines[1:]
                for raw in reversed(lines):
                    if b'"rate_limits"' not in raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except ValueError:
                        continue
                    payload = record.get("payload") if isinstance(record, dict) else None
                    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
                    if isinstance(limits, dict) and (isinstance(limits.get("primary"), dict)
                                                     or isinstance(limits.get("secondary"), dict)):
                        return {"at": str(record.get("timestamp", "")),
                                "primary": limits.get("primary"),
                                "secondary": limits.get("secondary")}
                if size <= window:
                    break
    except OSError:
        return None
    return None


def codex_rate_limits(now: float | None = None) -> dict | None:
    """The current Codex usage-limit reading on this machine, or None.

    The limits are account-wide, so every recent session's rollout is a reading,
    not only the run's. A session can log a stale snapshot after newer ones, so
    each window takes the highest use among readings for its current period
    (usage only grows within one), and a window that has reset since is dropped.
    """
    days = sorted(p for p in (codex_home() / "sessions").glob("*/*/*") if p.is_dir())[-2:]
    try:
        rollouts = sorted((p for day in days for p in day.glob("rollout-*.jsonl")),
                          key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    except OSError:
        return None
    readings = [r for r in map(_codex_limit_reading, rollouts) if r]
    now = now_s() if now is None else now
    out: dict = {}
    for window in ("primary", "secondary"):
        live = []
        for reading in readings:
            w = reading.get(window)
            if not isinstance(w, dict) or not isinstance(w.get("used_percent"), (int, float)) \
                    or not isinstance(w.get("resets_at"), (int, float)) or w["resets_at"] <= now:
                continue
            live.append((float(w["resets_at"]), float(w["used_percent"]),
                         int(w.get("window_minutes") or 0), reading["at"]))
        if not live:
            continue
        # One period's reset time jitters by a few seconds between responses.
        current = max(r[0] for r in live)
        period = [r for r in live if r[0] >= current - 600]
        top = max(period, key=lambda r: r[1])
        out[window] = {"used_percent": top[1], "window_minutes": top[2], "resets_at": top[0]}
        out["at"] = max([out.get("at", ""), *(r[3] for r in period)])
    return out or None


def codex_limit_lines(limits: dict) -> list[str]:
    """Human lines for a Codex limit reading, a warning last when one is due."""
    def when(epoch: float) -> str:
        return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(epoch))
    names = {"primary": "5h window", "secondary": "weekly window"}
    parts, hot = [], []
    for window in ("primary", "secondary"):
        w = limits.get(window)
        if not w:
            continue
        minutes = w["window_minutes"]
        name = names[window] if minutes in (0, 300, 10080) else f"{minutes}-minute window"
        parts.append(f"{name} {w['used_percent']:.0f}% used, resets {when(w['resets_at'])}")
        if w["used_percent"] >= CODEX_LIMIT_WARN_PERCENT[window]:
            hot.append(f"{name} at {w['used_percent']:.0f}%")
    lines = [f"codex limits (account-wide, read {limits['at'][:16].replace('T', ' ')}Z): "
             + "; ".join(parts)]
    if hot:
        lines.append("warning: Codex " + " and ".join(hot) + ". Tell the user once now: every"
                     " Codex role stalls mid-task when a window reaches 100%. An approved worker"
                     " fallback, when available, is routed to the orchestrator; moving supervisor"
                     " roles to another harness is the user's call.")
    return lines


def codex_limit_exhausted(limits: dict | None) -> bool:
    """Whether a live Codex account window has reached its reported cap."""
    if not isinstance(limits, dict):
        return False
    return any(isinstance(limits.get(window), dict)
               and float(limits[window].get("used_percent", 0) or 0) >= 100
               for window in ("primary", "secondary"))


def codex_stop_hook_installed() -> bool:
    """Whether local Codex config enables a synchronous Docket Stop adapter."""
    config = codex_hook_configuration(codex_home())
    return bool(config["locations"] and config["enabled"])


def opencode_session_rows(sid: str) -> dict[str, object] | None:
    """An OpenCode session and its subagent sessions, read from OpenCode's database."""
    try:
        import sqlite3
    except ImportError:
        return None
    db = opencode_db()
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            sessions, frontier = [], [sid]
            while frontier and len(sessions) < 256:
                current = frontier.pop()
                row = con.execute("select * from session where id = ?", (current,)).fetchone()
                if row is None:
                    continue
                sessions.append(dict(row))
                frontier += [r[0] for r in con.execute(
                    "select id from session where parent_id = ?", (current,))]
            if not sessions:
                return None
            messages = []
            for session in sessions:
                for message in con.execute(
                        "select id, data from message where session_id = ? "
                        "order by time_created, id", (session["id"],)):
                    parts = [json.loads(p[0]) for p in con.execute(
                        "select data from part where message_id = ? order by id",
                        (message["id"],))]
                    messages.append({"session": session["id"],
                                     "info": json.loads(message["data"]), "parts": parts})
        finally:
            con.close()
    except (sqlite3.Error, ValueError):
        return None
    return {"sessions": sessions, "messages": messages}


def opencode_session_usage(rows: dict[str, object] | None, since: str = "",
                           until: str = "") -> dict[str, object]:
    """Tokens summed over an OpenCode session's assistant messages, subagents included."""
    calls, times, cwd = opencode_calls(rows)
    usage = summarize_calls(calls, times, since, until)
    usage["harness_cwd"] = cwd
    return usage


def session_usage(ref: dict[str, object]) -> tuple[dict[str, object], list[Path], object]:
    """Usage inside the session's run window, transcript files, and OpenCode rows."""
    since = str(ref.get("first_seen", "") or "")
    until = str(ref.get("until", "") or "")
    if ref.get("harness") == "opencode":
        rows = opencode_session_rows(str(ref.get("session_id", "")))
        usage = opencode_session_usage(rows, since, until)
        usage["found"] = rows is not None
        usage["transcript"] = f"{opencode_db()}#{ref.get('session_id')}" if rows else ""
    else:
        files = session_transcripts(ref)
        usage = (claude_session_usage if ref.get("harness") == "claude"
                 else codex_session_usage)(files, since, until)
        usage["found"] = bool(files)
        usage["transcript"] = str(files[0]) if files else ""
        return (usage if usage["found"] else {**usage, "telemetry": "missing"}), files, None
    if not usage["found"]:
        usage["telemetry"] = "missing"
    return usage, [], rows


def observed_session_models(ref: dict[str, object]) -> list[str]:
    """Models measured in this session's window, excluding delegated Claude work."""
    since, until = str(ref.get("first_seen", "")), str(ref.get("until", ""))
    harness = ref.get("harness")
    try:
        if harness == "opencode":
            calls, times, _ = opencode_calls(opencode_session_rows(str(ref.get("session_id", ""))))
        else:
            files = session_transcripts(ref)
            calls, times, _ = (claude_calls(files[:1]) if harness == "claude" else codex_calls(files))
        return summarize_calls(calls, times, since, until)["models"]
    except (OSError, ValueError, TypeError, KeyError):
        # Optional telemetry must never prevent a lifecycle command.
        return []


def reviewer_observed_models(d: Path) -> list[str]:
    """Run-wide reviewer observations, never inferred from launch requests."""
    models = set()
    for ref in run_harness_sessions(d):
        if "reviewer" not in ref.get("roles", []):
            continue
        models.update(ref.get("models_observed", []))
        models.update(observed_session_models({**ref, "until": session_until(d, ref)}))
    return sorted(models)


# The planner reports the outcome, records feedback, and archives usage after the
# last decision; a session's later activity belongs to whatever it did next.
RUN_WRAP_UP_SECONDS = 900


def run_completed_at(d: Path) -> float | None:
    """When a finished run's last decision landed, or None while it is open.

    Only bounds a usage window, so it reads decisions without revalidating the
    evidence they bind; `run_complete_event` stays the authority for waking.
    """
    terminal = ("approved", "waived", "completed")
    try:
        if is_quick_milestone(d):
            milestones = [batch for batch in list_batches(d) if batch.get("milestone")]
            done = bool(milestones) and all(
                (batch.get("milestone_decision") or {}).get("verdict") == "approved"
                for batch in milestones) and all(
                state_of(d, owner)[1] in terminal for owner in planned_tasks(d))
        else:
            done = state_of(d, "orch")[1] in terminal
    except (OSError, ValueError):
        return None
    if not done:
        return None
    moments = [_instant(str((batch.get("milestone_decision") or {}).get("decided_at", "")))
               for batch in list_batches(d)]
    latest = max((m.timestamp() for m in moments if m is not None), default=0.0)
    for path in d.glob("*-decision-*.mdx"):
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest or None


def session_until(d: Path, ref: dict[str, object]) -> str:
    """Where a session stops counting for this run: its run's end or its next run.

    A role session kept open (or a TUI reused after /clear keeps its process) goes
    on to other work, and counting that here would inflate a closed run.
    """
    bounds: list[float] = []
    done = run_completed_at(d)
    if done is not None:
        # Feedback recorded for this run after its last decision is still its work.
        closing = [done]
        for path in d.glob("feedback/*.mdx"):
            try:
                observed = _instant(str(parse(path.read_text())[0].get("observed_at", "")))
            except (OSError, ValueError):
                continue
            if observed is not None:
                closing.append(observed.timestamp())
        bounds.append(max(done + RUN_WRAP_UP_SECONDS, max(closing) + 60))
    joined = _instant(str(ref.get("first_seen", "") or ""))
    for ledger in d.parent.glob(f"*/{HARNESS_SESSION_LEDGER}"):
        if ledger.parent == d:
            continue
        try:
            lines = ledger.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("session_id") != ref.get("session_id") \
                    or entry.get("harness") != ref.get("harness"):
                continue
            moment = _instant(str(entry.get("at", "") or ""))
            if moment is not None and (joined is None or moment > joined):
                bounds.append(moment.timestamp())
    return stamp(min(bounds)) if bounds else ""


def opencode_export(sid: str) -> bytes | None:
    """`opencode export` of one session, or None when OpenCode cannot export it."""
    binary = shutil.which("opencode")
    if not binary or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sid):
        return None
    try:
        done = subprocess.run([binary, "export", sid], capture_output=True, timeout=120)
        data = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if done.returncode or not isinstance(data, dict) or "info" not in data:
        return None
    return done.stdout


def session_archive_root() -> Path | None:
    """Where session transcripts are kept: beside the user-level feedback log."""
    log = feedback_log_path()
    return None if log is None else log.parent / "sessions"


def _gzip_to(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with gzip.open(tmp, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def archive_session(d: Path, ref: dict[str, object], usage: dict[str, object],
                    files: list[Path], rows: object) -> Path | None:
    """Copy one session's transcript and usage under the archive root; None if disabled.

    Transcripts can hold secrets, so the copies stay in a private directory in
    the user's own state, never in the project.
    """
    base = session_archive_root()
    if base is None or not usage.get("found"):
        return None
    project = re.sub(r"[^A-Za-z0-9._-]+", "-", str(root())).strip("-") or "project"
    label = "-".join(part for part in (
        "+".join(ref.get("roles") or ["unknown"]), "+".join(ref.get("owners") or []),
        str(ref.get("harness")), str(ref.get("session_id"))[:16]) if part)
    target = base / project / d.name / re.sub(r"[^A-Za-z0-9+._-]+", "-", label)
    for directory in (base, base / project, base / project / d.name, target):
        ensure_private_dir(directory)
    if rows is not None:
        # `opencode export` is the format `opencode import` loads back; the raw
        # database rows are kept only when the export is unavailable.
        exported = opencode_export(str(ref.get("session_id")))
        if exported is None:
            _gzip_to(target / "opencode-session.json.gz",
                     json.dumps(rows, indent=1, sort_keys=True).encode())
        else:
            _gzip_to(target / "opencode-export.json.gz", exported)
            for child in rows["sessions"][1:]:
                child_export = opencode_export(str(child.get("id")))
                if child_export is not None:
                    _gzip_to(target / "subagents" / f"{child.get('id')}.json.gz", child_export)
    elif files:
        _gzip_to(target / "transcript.jsonl.gz", files[0].read_bytes())
        for extra in files[1:]:
            relative = extra.relative_to(files[0].parent / str(ref.get("session_id")))
            _gzip_to(target / "subagents-and-tools" / f"{relative}.gz", extra.read_bytes())
    publish_json(target / "usage.json", {**ref, **usage, "run": d.name,
                                          "archived_at": stamp()})
    return target


def collect_run_usage(d: Path, archive: bool) -> list[dict[str, object]]:
    """Usage per noted session; archiving also copies transcripts and logs usage."""
    out = []
    for ref in run_harness_sessions(d):
        ref = {**ref, "until": session_until(d, ref)}
        usage, files, rows = session_usage(ref)
        target = None
        if archive:
            try:
                target = archive_session(d, ref, usage, files, rows)
            except OSError:
                target = None
            if usage["found"]:
                log_feedback({"origin": "usage", "run": d.name,
                              "role": "+".join(ref["roles"]) or "unknown",
                              "task": "+".join(ref["owners"]) or "-",
                              "harness": ref["harness"], "session_id": ref["session_id"],
                              "cwd": ref["cwd"], "archive": str(target or ""),
                              **{k: v for k, v in usage.items() if k != "found"}})
        out.append({**ref, **usage, "archive": str(target or "")})
    return out


def tokens_h(count: object) -> str:
    n = int(count or 0)
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k" if n < 1_000_000 else f"{n / 1_000_000:.2f}M"


def cmd_usage(a: argparse.Namespace) -> None:
    """Token usage per role, read from each harness session's own transcript."""
    d = need_run(a.run)
    rows = collect_run_usage(d, a.archive)
    if a.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        print(f"run {a.run}: no harness session noted yet. Sessions are noted when a role "
              "runs its own docket commands inside Claude Code, Codex, or OpenCode")
        return
    total = sum(int(r["total"]) for r in rows)
    print(f"run {a.run}: {len(rows)} harness session(s), {tokens_h(total)} tokens processed")
    print(f"  {'role':<14} {'task':<8} {'harness':<9} {'calls':>6} {'input':>8} "
          f"{'cache rd':>9} {'cache wr':>9} {'output':>8} {'total':>9}  model")
    for r in sorted(rows, key=lambda r: (",".join(r["roles"]), ",".join(r["owners"]))):
        roles = "+".join(r["roles"]) or "unknown"
        task = "+".join(r["owners"]) or "-"
        if not r["found"]:
            print(f"  {roles:<14} {task:<8} {r['harness']:<9} transcript not found "
                  f"for session {r['session_id']}")
            continue
        if r.get("telemetry") == "empty":
            print(f"  {roles:<14} {task:<8} {r['harness']:<9} no call inside the run window "
                  f"({r.get('since') or 'start'} to {r.get('until') or 'now'}); unknown, not zero")
            continue
        print(f"  {roles:<14} {task:<8} {r['harness']:<9} {r['calls']:>6} "
              f"{tokens_h(r['input']):>8} {tokens_h(r['cache_read']):>9} "
              f"{tokens_h(r['cache_write']):>9} {tokens_h(r['output']):>8} "
              f"{tokens_h(r['total']):>9}  {', '.join(r['models']) or '-'}")
    by_role: dict[str, list[int]] = {}
    for r in rows:
        by_role.setdefault("+".join(r["roles"]) or "unknown", []).append(int(r["total"]))
    print("  by role: " + "; ".join(f"{role} {tokens_h(sum(v))} ({len(v)})"
                                    for role, v in sorted(by_role.items())))
    by_model: dict[str, int] = {}
    for r in rows:
        for model, counts in (r.get("by_model") or {}).items():
            by_model[model] = by_model.get(model, 0) + int(counts.get("total") or 0)
    if by_model:
        print("  by model: " + "; ".join(f"{model} {tokens_h(count)}" for model, count
                                         in sorted(by_model.items(), key=lambda i: -i[1])))
    for r in rows:
        print(f"  {r['harness']} {r['session_id']}: "
              f"{r['transcript'] or 'no transcript'}")
    archived = [r["archive"] for r in rows if r["archive"]]
    if archived:
        print(f"archived {len(archived)} session(s) under {Path(archived[0]).parent}")
    elif a.archive:
        print("nothing archived: the user-level feedback log is off, or no transcript was found")
