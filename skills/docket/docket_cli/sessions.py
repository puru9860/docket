"""Harness session discovery, token usage, and session archives."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .common import MODE_QUICK, stamp
from .paths import root, run_dir
from .publication import publish_json
from .policy import mode_of, need_run
from .feedback import ensure_private_dir, feedback_log_path, log_feedback


#
# Which harness session played which role, so each role's token usage and full
# conversation can be reviewed after a run. Claude Code and Codex name their
# session in the environment of every command they run. OpenCode names only its
# process, so its session is the one whose running shell call is this very
# command. Environment inherited from an unrelated session is never trusted:
# the harness process has to be an ancestor of this one.

HARNESS_SESSION_LEDGER = ".harness-sessions.jsonl"
SESSION_ROLE_COMMANDS = ("watch", "arm", "feedback", "submit", "handoff", "verify", "decide",
                         "assign", "dispatch", "resume", "batch", "release")


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or Path.home() / ".claude")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "").strip() or Path.home() / ".codex")


def opencode_db() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(Path.home() / ".local" / "share")
    return Path(base) / "opencode" / "opencode.db"


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


def opencode_running_session(needles: list[str]) -> tuple[str, str]:
    """The OpenCode session whose running shell call is this command, and its version."""
    try:
        import sqlite3
    except ImportError:
        return "", ""
    db = opencode_db()
    if not db.is_file():
        return "", ""
    since = int((time.time() - 3600) * 1000)
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
                    if all(needle in command for needle in needles):
                        return str(sid), str(version or "")
        finally:
            con.close()
    except sqlite3.Error:
        return "", ""
    return "", ""


def harness_session(needles: list[str]) -> dict[str, str] | None:
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
        ref["session_id"], version = opencode_running_session(needles)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", ref["session_id"]):
        return None
    if version:
        ref["version"] = version
    return ref


def note_harness_session(d: Path, role: str, owner: str,
                         needles: list[str]) -> dict[str, str] | None:
    """Append the calling session to the run's ledger once per role and task. Never raises."""
    ref = harness_session(needles)
    if not ref:
        return None
    entry = {"role": role, "owner": owner or "-", **ref}
    identity = ("harness", "session_id", "role", "owner")
    try:
        with (d / HARNESS_SESSION_LEDGER).open("a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                for line in fh:
                    try:
                        seen = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(seen, dict) and all(seen.get(k) == entry[k] for k in identity):
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
    if a.cmd == "feedback":
        return (str(a.role or ""), owner) if a.add else ("", "")
    quick = mode_of(d) == MODE_QUICK
    explicit = str(getattr(a, "as_role", "") or "")
    if a.cmd in ("submit", "handoff"):
        if explicit:
            return explicit, owner
        if owner == "orch":
            return ("coordinator" if quick else "orchestrator"), owner
        return "implementor", owner
    if a.cmd == "verify":
        return explicit or ("checker" if quick else "verifier"), owner
    if a.cmd == "decide":
        return explicit or ("checker" if quick else "reviewer"), owner
    return ("coordinator" if quick else "orchestrator"), ""


def note_command_session(a: argparse.Namespace) -> None:
    """Record the calling harness session when a command speaks for a role."""
    a.harness_session_ref = None
    if a.cmd not in SESSION_ROLE_COMMANDS or not getattr(a, "run", ""):
        return
    try:
        d = run_dir(a.run)
        if not d.is_dir():
            return
        role, owner = command_session_role(a, d)
    except SystemExit:
        return
    if role:
        a.harness_session_ref = note_harness_session(d, role, owner, [a.run, a.cmd])


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
        for field, value in (("roles", entry.get("role")), ("owners", entry.get("owner"))):
            if value and value != "-" and value not in ref[field]:
                ref[field].append(str(value))
    return list(merged.values())


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
        home = codex_home()
        found = sorted(home.glob(f"sessions/*/*/*/rollout-*-{sid}.jsonl"))
        return (found or sorted(home.glob(f"archived_sessions/rollout-*-{sid}.jsonl")))[:1]
    return []


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


def empty_usage() -> dict[str, object]:
    return {"found": False, "calls": 0, "input": 0, "cache_read": 0, "cache_write": 0,
            "output": 0, "reasoning": 0, "total": 0, "cost": 0.0, "models": [],
            "started": "", "ended": "", "harness_cwd": "", "transcript": ""}


def _span(usage: dict[str, object], when: str) -> None:
    if when:
        usage["started"] = min(str(usage["started"]) or when, when)
        usage["ended"] = max(str(usage["ended"]), when)


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


def claude_session_usage(files: list[Path]) -> dict[str, object]:
    """Tokens from a Claude Code transcript and its subagents, one count per API message."""
    usage = empty_usage()
    counted: dict[str, dict] = {}
    for path in (p for p in files if p.suffix == ".jsonl"):
        for record in _jsonl(path):
            _span(usage, str(record.get("timestamp", "") or ""))
            if not usage["harness_cwd"] and record.get("cwd"):
                usage["harness_cwd"] = str(record["cwd"])
            message = record.get("message")
            if record.get("type") != "assistant" or not isinstance(message, dict):
                continue
            model = str(message.get("model", "") or "")
            if not model or model.startswith("<"):
                continue
            if model not in usage["models"]:
                usage["models"].append(model)
            if isinstance(message.get("usage"), dict):
                key = f"{path.name}:{message.get('id') or record.get('uuid')}"
                counted[key] = message["usage"]
    for counts in counted.values():
        usage["calls"] += 1
        usage["input"] += int(counts.get("input_tokens") or 0)
        usage["cache_write"] += int(counts.get("cache_creation_input_tokens") or 0)
        usage["cache_read"] += int(counts.get("cache_read_input_tokens") or 0)
        usage["output"] += int(counts.get("output_tokens") or 0)
    usage["total"] = (usage["input"] + usage["cache_write"] + usage["cache_read"]
                      + usage["output"])
    return usage


def codex_session_usage(files: list[Path]) -> dict[str, object]:
    """Tokens from a Codex rollout: its last cumulative token count."""
    usage = empty_usage()
    last: dict = {}
    for record in (r for path in files for r in _jsonl(path)):
        _span(usage, str(record.get("timestamp", "") or ""))
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "session_meta" and payload.get("cwd"):
            usage["harness_cwd"] = str(payload["cwd"])
        elif record.get("type") == "turn_context" and payload.get("model"):
            if payload["model"] not in usage["models"]:
                usage["models"].append(str(payload["model"]))
        elif (record.get("type") == "event_msg" and payload.get("type") == "token_count"
              and isinstance(payload.get("info"), dict)):
            usage["calls"] += 1
            last = payload["info"].get("total_token_usage") or last
    cached = int(last.get("cached_input_tokens") or 0)
    written = int(last.get("cache_write_input_tokens") or 0)
    usage["input"] = max(0, int(last.get("input_tokens") or 0) - cached - written)
    usage["cache_read"], usage["cache_write"] = cached, written
    usage["output"] = int(last.get("output_tokens") or 0)
    usage["reasoning"] = int(last.get("reasoning_output_tokens") or 0)
    usage["total"] = int(last.get("total_tokens") or 0)
    return usage


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


def opencode_session_usage(rows: dict[str, object] | None) -> dict[str, object]:
    """Tokens summed over an OpenCode session's assistant messages, subagents included."""
    usage = empty_usage()
    if not rows:
        return usage
    usage["harness_cwd"] = str(rows["sessions"][0].get("directory", "") or "")
    for message in rows["messages"]:
        info = message["info"] if isinstance(message.get("info"), dict) else {}
        created = (info.get("time") or {}).get("created")
        if isinstance(created, (int, float)):
            _span(usage, stamp(created / 1000))
        if info.get("role") != "assistant":
            continue
        tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        usage["calls"] += 1
        usage["input"] += int(tokens.get("input") or 0)
        usage["output"] += int(tokens.get("output") or 0)
        usage["reasoning"] += int(tokens.get("reasoning") or 0)
        usage["cache_read"] += int(cache.get("read") or 0)
        usage["cache_write"] += int(cache.get("write") or 0)
        usage["total"] += int(tokens.get("total") or 0) or sum(
            int(v or 0) for v in (tokens.get("input"), tokens.get("output"),
                                  tokens.get("reasoning"), cache.get("read"), cache.get("write")))
        usage["cost"] += float(info.get("cost") or 0)
        model = "/".join(str(v) for v in (info.get("providerID"), info.get("modelID")) if v)
        if model and model not in usage["models"]:
            usage["models"].append(model)
    return usage


def session_usage(ref: dict[str, object]) -> tuple[dict[str, object], list[Path], object]:
    """Usage, transcript files, and OpenCode rows for one noted session."""
    if ref.get("harness") == "opencode":
        rows = opencode_session_rows(str(ref.get("session_id", "")))
        usage = opencode_session_usage(rows)
        usage["found"] = rows is not None
        usage["transcript"] = f"{opencode_db()}#{ref.get('session_id')}" if rows else ""
        return usage, [], rows
    files = session_transcripts(ref)
    usage = (claude_session_usage if ref.get("harness") == "claude"
             else codex_session_usage)(files)
    usage["found"] = bool(files)
    usage["transcript"] = str(files[0]) if files else ""
    return usage, files, None


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
        print(f"  {roles:<14} {task:<8} {r['harness']:<9} {r['calls']:>6} "
              f"{tokens_h(r['input']):>8} {tokens_h(r['cache_read']):>9} "
              f"{tokens_h(r['cache_write']):>9} {tokens_h(r['output']):>8} "
              f"{tokens_h(r['total']):>9}  {', '.join(r['models']) or '-'}")
    by_role: dict[str, list[int]] = {}
    for r in rows:
        by_role.setdefault("+".join(r["roles"]) or "unknown", []).append(int(r["total"]))
    print("  by role: " + "; ".join(f"{role} {tokens_h(sum(v))} ({len(v)})"
                                    for role, v in sorted(by_role.items())))
    for r in rows:
        print(f"  {r['harness']} {r['session_id']}: "
              f"{r['transcript'] or 'no transcript'}")
    archived = [r["archive"] for r in rows if r["archive"]]
    if archived:
        print(f"archived {len(archived)} session(s) under {Path(archived[0]).parent}")
    elif a.archive:
        print("nothing archived: the user-level feedback log is off, or no transcript was found")
