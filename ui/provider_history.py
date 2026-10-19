"""Bounded, read-only provider history. Saved events never grant live authority."""
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from docket_cli.sessions import claude_config_dir, codex_home, opencode_db

MAX_MESSAGES = 200
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 12000
MAX_SUBAGENTS = 32
SID = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def belongs(cwd, workspace):
    return bool(cwd) and Path(cwd).resolve().is_relative_to(workspace)


def text(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value[:MAX_TEXT]


SUMMARY_KEYS = ("description", "command", "cmd", "file_path", "filePath", "path", "pattern",
                "url", "query", "prompt")
MAX_SUMMARY = 140


def tool_summary(content):
    """One line saying what a tool call did, or what its result began with."""
    value = content
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    if isinstance(value, dict) and isinstance(value.get("input"), dict):
        value = value["input"]  # an OpenCode tool part carries its call under input
    if isinstance(value, dict):
        named = next((value[key] for key in SUMMARY_KEYS if value.get(key)), None)
        value = named if named is not None else json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        value = " ".join(part.get("text", "") if isinstance(part, dict) else str(part)
                         for part in value)
    line = next((line.strip() for line in str(value).splitlines() if line.strip()), "")
    return line if len(line) <= MAX_SUMMARY else line[:MAX_SUMMARY - 1] + "\u2026"


# Claude Code records a slash command the user typed as user turns wrapped in these
# tags; they are harness bookkeeping, not something the user said to the agent.
LOCAL_COMMAND = re.compile(r"\s*<(local-command-caveat|command-name|command-message|local-command-stdout|local-command-stderr)>")


def local_command_event(identifier, body, timestamp):
    """A typed slash command or its output as a compact activity row; the caveat is dropped."""
    if body.lstrip().startswith("<local-command-caveat>"):
        return None
    name = re.search(r"<command-name>(.*?)</command-name>", body, re.S)
    if name:
        args = re.search(r"<command-args>(.*?)</command-args>", body, re.S)
        line = " ".join(part for part in (name.group(1).strip(), args.group(1).strip() if args else "") if part)
        return event(identifier, "tool", line, timestamp, name="Slash command", status="typed by the user")
    output = re.search(r"<local-command-std(?:out|err)>(.*?)</local-command-std(?:out|err)>", body, re.S)
    return event(identifier, "tool", output.group(1).strip() if output else body, timestamp,
                 name="Command output", status="harness output")


def event(identifier, role, content, timestamp="", **extra):
    original = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if role == "tool":
        extra = {"summary": tool_summary(content), **extra}
    return {"id": str(identifier), "role": role, "text": text(content),
            "timestamp": timestamp, "truncated": len(original) > MAX_TEXT, **extra}


def project_sessions(workspace):
    """Project-scoped OpenCode metadata for explicit selection, never auto-attribution."""
    db = opencode_db()
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute("select id, title from session where directory = ? and parent_id is null "
                               "order by title, id limit 100", (str(workspace),)).fetchall()
        finally:
            con.close()
        return [{"id": f"opencode:{sid}", "title": text(title or sid),
                 "native": {"harness": "opencode", "session_id": sid, "cwd": str(workspace)}}
                for sid, title in rows if isinstance(sid, str) and SID.fullmatch(sid)]
    except (sqlite3.Error, OSError):
        return []


def opencode(ref, workspace):
    db = opencode_db()
    if not db.is_file():
        raise ValueError("OpenCode history database is unavailable")
    con = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    try:
        con.execute("begin")  # One consistent read snapshot, no provider writes.
        session = con.execute("select directory from session where id = ?", (ref["session_id"],)).fetchone()
        if not session or not belongs(session[0], workspace):
            raise ValueError("Native session is missing or belongs to another project")
        # A role that runs each round in a subagent works in a child session.
        try:
            children = [row[0] for row in con.execute(
                "select id from session where parent_id = ? order by id limit ?",
                (ref["session_id"], MAX_SUBAGENTS))]
        except sqlite3.OperationalError:  # a schema without subagent sessions
            children = []
        ids = [ref["session_id"], *children]
        rows = list(con.execute(
            "select id, session_id, substr(data, 1, ?) as data, time_created from message "
            f"where session_id in ({','.join('?' * len(ids))}) "
            "order by time_created desc, id desc limit ?",
            (MAX_BYTES + 1, *ids, MAX_MESSAGES + 1)))
        partial = len(rows) > MAX_MESSAGES
        rows = rows[:MAX_MESSAGES]
        events, used = [], 0
        for row in reversed(rows):
            used += len(row["data"])
            if len(row["data"]) > MAX_BYTES or used > MAX_BYTES:
                partial = True
                continue
            info = json.loads(row["data"])
            role = info.get("role")
            if role not in ("user", "assistant"):
                continue
            subagent = {"subagent": True} if row["session_id"] != ref["session_id"] else {}
            parts = con.execute(
                "select id, substr(data, 1, ?) as data from part "
                "where message_id = ? and session_id = ? order by time_created, id limit 257",
                (MAX_BYTES + 1, row["id"], row["session_id"]))
            for index, part in enumerate(parts):
                used += len(part["data"])
                if index == 256 or len(part["data"]) > MAX_BYTES or used > MAX_BYTES:
                    partial = True
                    break
                value = json.loads(part["data"])
                kind = value.get("type")
                if kind == "text" and value.get("text"):
                    body = value["text"]
                    partial |= len(body) > MAX_TEXT
                    events.append(event(part["id"], role, body, row["time_created"], **subagent))
                elif kind == "tool":
                    state = value.get("state") or {}
                    body = {key: state[key] for key in ("input", "output", "error") if key in state}
                    partial |= len(json.dumps(body)) > MAX_TEXT
                    events.append(event(part["id"], "tool", body, row["time_created"],
                                        name=text(value.get("tool", "Tool")), status=text(state.get("status", "recorded")),
                                        **subagent))
        return events, partial
    finally:
        con.close()


def confined(path, home):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(home.resolve()):
        raise ValueError("Native transcript path is not confined to its provider directory")
    return path


def jsonl_path(ref):
    """The exact transcript, or for Codex `(root, shard)` where shard is a verified resumed continuation or None."""
    sid = ref["session_id"]
    if ref["harness"] == "codex":
        home = codex_home()
        pattern = "sessions/*/*/*/rollout-*-{}.jsonl", "archived_sessions/rollout-*-{}.jsonl"
        roots = [f for p in pattern for f in home.glob(p.format(sid))]
        # An exact identity must resolve uniquely, never to the newest file.
        if len(roots) != 1:
            raise ValueError("Native transcript is unavailable or its identity is ambiguous")
        shards = [f for p in pattern for f in home.glob(p.format(sid + "_*"))]
        return confined(roots[0], home), shard_for(sid, shards, home)
    home = claude_config_dir() / "projects"
    files = list(home.glob(f"*/{sid}.jsonl"))
    if len(files) != 1:
        raise ValueError("Native transcript is unavailable or its identity is ambiguous")
    return confined(files[0], home)


def shard_for(sid, candidates, home):
    """The one candidate whose own metadata claims this thread; other threads' files are ignored."""
    claimed = [path for path in candidates if (session_meta(path) or {}).get("id") == sid]
    if len(claimed) > 1:
        raise ValueError("Native continuation is ambiguous")
    return confined(claimed[0], home) if claimed else None


def session_meta(path):
    with path.open("rb") as fh:
        for line in fh.read(min(MAX_BYTES, 128 * 1024)).splitlines():
            try:
                value = json.loads(line)
                if value.get("type") == "session_meta":
                    return value.get("payload", {})
            except (ValueError, TypeError, AttributeError):
                continue
    return None


def tail_lines(path, end, budget):
    """Complete records ending before byte `end` within `budget` bytes, as (offset, line); True if older bytes remain."""
    with path.open("rb") as fh:
        fh.seek(0, 2)
        end = min(end if end is not None else fh.tell(), fh.tell())
        start = max(0, end - budget)
        fh.seek(start)
        if start:
            start += len(fh.readline())  # Discard the partial first record in the bounded tail.
        data = fh.read(max(0, end - start))
    out, offset = [], start
    for line in data.splitlines(keepends=True):
        out.append((offset, line))
        offset += len(line)
    return out, bool(start), end - start


def claude_subagent_lines(root, sid, workspace):
    """Bounded (tag, offset, line) records of the subagents a Claude session started.

    A role that runs each round in a subagent does its work there, so the parent
    transcript alone would show only the hand-offs. A file counts only when its
    own records name this session as a sidechain in this project; newest first
    shares one byte budget.
    """
    folder = root.parent / sid / "subagents"
    if not folder.is_dir() or folder.is_symlink():
        return [], False
    home = root.parent.parent
    files = sorted((f for f in folder.glob("agent-*.jsonl") if not f.is_symlink()),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    partial = len(files) > MAX_SUBAGENTS
    out, budget = [], MAX_BYTES
    for path in files[:MAX_SUBAGENTS]:
        try:
            confined(path, home)
        except ValueError:
            continue
        with path.open("rb") as fh:
            head = fh.read(min(MAX_BYTES, 64 * 1024)).splitlines()
        first = None
        for line in head:
            try:
                first = json.loads(line)
                break
            except ValueError:
                continue
        if not (isinstance(first, dict) and first.get("sessionId") == sid and first.get("isSidechain")
                and belongs(first.get("cwd"), workspace)):
            continue
        if budget <= 0:
            partial = True
            break
        lines, cut, used = tail_lines(path, None, budget)
        budget -= used
        partial |= cut
        out += [(f"sub:{path.stem[6:]}:", o, l) for o, l in lines]
    return out, partial


def native_lines(ref, workspace):
    """Bounded (tag, offset, line) records in chronological order; a Codex continuation follows its root."""
    found = jsonl_path(ref)
    root, shard = found if isinstance(found, tuple) else (found, None)
    harness, sid = ref["harness"], ref["session_id"]

    def verify(path, continuation):
        bound = False
        with path.open("rb") as fh:
            for line in fh.read(min(MAX_BYTES, 128 * 1024)).splitlines():
                try:
                    value = json.loads(line)
                    if harness == "codex" and value.get("type") == "session_meta":
                        meta = value.get("payload", {})
                        bound = meta.get("id") == sid and belongs(meta.get("cwd"), workspace)
                        if continuation:
                            base = meta.get("history_base")
                            bound = bound and isinstance(base, dict) and base.get("thread_id") == sid
                        break
                    if harness == "claude" and value.get("sessionId") == sid and value.get("cwd"):
                        bound = belongs(value["cwd"], workspace)
                        break
                except (ValueError, TypeError, AttributeError):
                    continue
        if not bound:
            raise ValueError("Transcript identity or project directory could not be verified")

    verify(root, False)
    if shard is None:
        lines, partial, _ = tail_lines(root, None, MAX_BYTES)
        out = [("", o, l) for o, l in lines]
        if harness == "claude":
            extra, extra_partial = claude_subagent_lines(root, sid, workspace)
            out, partial = out + extra, partial or extra_partial
        return out, partial
    verify(shard, True)
    lines, partial, used = tail_lines(shard, None, MAX_BYTES)
    out = [("s", o, l) for o, l in lines]
    base = session_meta(shard)["history_base"].get("end_byte_offset")
    if partial or used >= MAX_BYTES or len(out) >= MAX_MESSAGES:
        return out, True  # Older conversation exists but the bounded window is already full.
    # The shard declares where in the root its history begins; later root bytes are not part of it.
    head, head_partial, _ = tail_lines(root, base if isinstance(base, int) and base >= 0 else None, MAX_BYTES - used)
    return [("", o, l) for o, l in head] + out, head_partial


def jsonl(ref, workspace):
    lines, partial = native_lines(ref, workspace)
    events = []
    for tag, offset, line in lines:
        index = f"{tag}{offset}"
        try:
            value = json.loads(line)
        except ValueError:
            partial = True  # Active JSONL append may have an incomplete final record.
            continue
        if not isinstance(value, dict):
            continue
        stamp = value.get("timestamp", "")
        if ref["harness"] == "codex":
            if value.get("type") != "response_item":
                continue
            item = value.get("payload", {})
            if item.get("type") == "message" and item.get("role") in ("user", "assistant"):
                body = "\n".join(c.get("text", "") for c in item.get("content", [])
                                 if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"))
                if body:
                    partial |= len(body) > MAX_TEXT
                    events.append(event(index, item["role"], body, stamp))
            elif item.get("type") in ("function_call", "custom_tool_call"):
                body = item.get("arguments", item.get("input", ""))
                events.append(event(index, "tool", body, stamp, name=text(item.get("name", "Tool")), status="recorded call"))
            elif item.get("type") in ("function_call_output", "custom_tool_call_output"):
                events.append(event(index, "tool", item.get("output", ""), stamp, name="Tool result", status="recorded result"))
        else:
            if value.get("sessionId") != ref["session_id"]:
                continue
            role = value.get("type")
            if role not in ("user", "assistant"):
                continue
            content = (value.get("message") or {}).get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for part_index, part in enumerate(content):
                if not isinstance(part, dict):
                    continue
                identifier = f"{tag}{value.get('uuid', index)}:{part_index}"
                kind = part.get("type")
                if kind == "text" and part.get("text") and role == "user" \
                        and LOCAL_COMMAND.match(part["text"]):
                    local = local_command_event(identifier, part["text"], stamp)
                    if local:
                        events.append(local)
                elif kind == "text" and part.get("text"):
                    events.append(event(identifier, role, part["text"], stamp))
                elif kind == "tool_use":
                    events.append(event(identifier, "tool", part.get("input", {}), stamp, name=text(part.get("name", "Tool")), status="recorded call"))
                elif kind == "tool_result":
                    events.append(event(identifier, "tool", part.get("content", ""), stamp, name="Tool result", status="recorded result"))
                if len(json.dumps(part)) > MAX_TEXT:
                    partial = True
    if any(tag.startswith("sub:") for tag, _, _ in lines):
        for item in events:
            if item["id"].startswith("sub:"):
                item["subagent"] = True
        events.sort(key=lambda item: str(item.get("timestamp", "")))
    partial |= len(events) > MAX_MESSAGES
    return events[-MAX_MESSAGES:], partial


def load_history(workspace, slot):
    ref = slot.get("native") or {}
    result = {"slot": slot["id"], "generation": slot["generation"], "session": ref.get("session_id"),
              "provider": ref.get("harness"), "messages": [], "capabilities": {
                  "history": False, "send": False, "questions": False, "permissions": False},
              "partial": True, "source": "saved provider history"}
    if slot.get("mapping_issue"):
        result["reason"] = slot["mapping_issue"]
        return result
    if ref.get("harness") not in ("codex", "claude", "opencode") or not SID.fullmatch(str(ref.get("session_id", ""))):
        result["reason"] = "No exact native session was recorded. Use the terminal fallback."
        return result
    try:
        messages, partial = opencode(ref, workspace) if ref["harness"] == "opencode" else jsonl(ref, workspace)
        result.update(messages=messages[-MAX_MESSAGES:], partial=partial or len(messages) > MAX_MESSAGES or any(m.get("truncated") for m in messages))
        result["capabilities"]["history"] = True
        result["reason"] = "Saved history only. Questions and permissions must be answered in the live terminal."
    except sqlite3.Error:
        result["reason"] = "OpenCode history is unavailable or its stored format is unsupported. Use the live terminal."
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        result["reason"] = str(error)
    return result
