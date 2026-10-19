"""Read-only companion projection. Never invokes lifecycle or delivery commands."""
import contextlib
import hashlib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "docket"))
from docket_cli.frontmatter import parse
from docket_cli.paths import latest, _seq_key
from docket_cli.policy import run_policy
from provider_history import load_history, project_sessions

LIMIT = 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def safe_record(path, workspace):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(workspace):
        raise ValueError("Record path escapes the project")
    return json.loads(read(path))


def physical_identity(slot):
    """One proven physical identity shared by history, transport and actions."""
    native = slot.get("native") or {}
    process = slot.get("process") or {}
    if native.get("session_id") and native.get("harness"):
        return {"kind": "native", "harness": native["harness"], "session_id": native["session_id"]}
    if process.get("pid"):
        return {"kind": "process", "pid": process["pid"], "start": process.get("start", "")}
    return {"kind": "none"}


def slot_capabilities(slot):
    """Honest capability states, distinguishing history, transport and terminal."""
    identity = physical_identity(slot)
    # A Herdr name proves a live terminal, never a saved conversation.
    proven = (identity["kind"] in ("native", "process") or bool(slot.get("herdr_name"))) and not slot.get("mapping_issue")
    current = not slot.get("historical")
    return {
        "history": identity["kind"] == "native" and not slot.get("mapping_issue"),
        "send": proven and current,
        "interrupt": proven and current,
        "questions": False,
        "permissions": False,
        "terminal": current,
    }


def read_notes(workspace, directory, roles):
    """Ledger notes grouped by role and native session, with the process each recorded."""
    ledger = directory / ".harness-sessions.jsonl"
    noted = {}
    if ledger.is_file() and not any(p.is_symlink() for p in (ledger, *ledger.parents)):
        for line in read(ledger).splitlines():
            try:
                row = json.loads(line)
                role, harness, sid = row["role"], row["harness"], row["session_id"]
                if role not in roles or harness not in ("opencode", "codex", "claude"):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sid):
                    continue
                if row.get("cwd") and not Path(row["cwd"]).resolve().is_relative_to(workspace):
                    continue
                owner = row.get("owner", "")
                if not isinstance(owner, str) or not isinstance(row.get("worker_process", {}), dict):
                    continue
                key = (role, harness, sid)
                item = noted.setdefault(key, {"native": {"harness": harness, "session_id": sid,
                    "cwd": row.get("cwd", "")}, "owners": [], "process": row.get("worker_process", {}),
                    "at": row.get("at", ""), "owner_times": {}, "processes": []})
                proc = row.get("worker_process", {})
                if proc.get("pid") and proc not in item["processes"]:
                    item["processes"].append(proc)
                if owner and owner != "-" and owner not in item["owners"]:
                    item["owners"].append(owner)
                if owner and owner != "-" and row.get("at"):
                    item["owner_times"].setdefault(owner, []).append(row["at"])
            except (ValueError, KeyError, TypeError):
                continue
    return noted


def read_dispatches(workspace, directory):
    dispatches = []
    for path in sorted((directory / ".dispatch").glob("*.json")):
        try:
            record = safe_record(path, workspace)
            if (isinstance(record, dict) and record.get("run") == directory.name and
                    record.get("role") == "implementor" and isinstance(record.get("owner"), str)):
                dispatches.append(record)
        except (OSError, ValueError, TypeError):
            continue
    return dispatches


def same_process(a, b):
    return bool(a.get("pid")) and all(a.get(k) == b.get(k) for k in ("pid", "start"))


def merge_duplicates(slots):
    """Consolidate registrations of one role that name the same native session.

    Only an exact native match merges, and only when their recorded processes do not
    disagree. The registration holding dispatches is kept; the rest are its details.
    """
    kept = []
    for s in slots:
        native = s.get("native") or {}
        key = (s["role"], native.get("harness"), native.get("session_id"))
        twin = next((k for k in kept if native.get("session_id") and
                     (k["role"], (k.get("native") or {}).get("harness"),
                      (k.get("native") or {}).get("session_id")) == key and k.get("session") and s.get("session")), None)
        if twin is None:
            kept.append(s)
            continue
        a, b = twin.get("process") or {}, s.get("process") or {}
        if a.get("pid") and b.get("pid") and not same_process(a, b):
            twin["mapping_issue"] = s["mapping_issue"] = (
                "Two registrations claim this native session with different processes. "
                "Its role mapping is ambiguous.")
            kept.append(s)
            continue
        first, second = (twin, s) if twin.get("owners") or not s.get("owners") else (s, twin)
        first["owners"] = list(dict.fromkeys(first.get("owners", []) + second.get("owners", [])))
        first["dispatches"] = first.get("dispatches", []) + second.get("dispatches", [])
        first["process"] = first.get("process") or second.get("process") or {}
        first["registrations"] = first.get("registrations", [registration_detail(first)]) + \
            second.get("registrations", [registration_detail(second)])
        if first is s:
            kept[kept.index(twin)] = s
    return kept


def fold_dispatch_proofs(slots, dispatches):
    """A dispatch that recorded its worker's pid:start folds in the one registration proving it."""
    for holder in [s for s in slots if s.get("session") and not (s.get("native") or {}).get("session_id")]:
        procs = [d["worker_process"] for d in dispatches if d.get("session") == holder["session"]
                 and isinstance(d.get("worker_process"), dict) and d["worker_process"].get("pid")]
        if not procs:
            continue
        twins = [t for t in slots if t is not holder and t["role"] == holder["role"] and t.get("session")
                 and (t.get("native") or {}).get("session_id")
                 and any(same_process(p, t.get("process") or {}) and p.get("harness") == t["native"].get("harness")
                         for p in procs)]
        if len(twins) != 1:
            continue
        twin = twins[0]
        holder.update(native=twin["native"], process=twin.get("process") or {}, history_attribution="dispatch-process")
        holder["owners"] = list(dict.fromkeys(holder.get("owners", []) + twin.get("owners", [])))
        holder["registrations"] = [registration_detail(holder), registration_detail(twin)]
        slots.remove(twin)


def registration_detail(slot):
    return {"session": slot.get("session", ""), "registration_generation": slot.get("registration_generation", 1),
            "registered_at": slot.get("registered_at", "")}


def classify_sessions(slots, dispatches, settled):
    """Group each role's sessions as current, earlier or unattributed.

    A role has one current session: for implementors it is the registration holding
    active dispatches (several only when several run at once), else the one holding
    the latest dispatch. A registration with no dispatch and no proof is unattributed.
    """
    for role in dict.fromkeys(s["role"] for s in slots):
        mine = [s for s in slots if s["role"] == role and s.get("session")]
        owned = [s for s in mine if s.get("dispatches")]
        if role == "implementor" and owned:
            active = [s for s in owned if any(d.get("state") == "dispatched" and d["owner"] not in settled
                                              for d in s["dispatches"])]
            if active:
                current = active
                for s in active:
                    s["task_note"] = "task " + ", ".join(d["owner"] for d in s["dispatches"]
                                                         if d.get("state") == "dispatched" and d["owner"] not in settled)
            else:
                latest = max(owned, key=lambda s: max(d.get("dispatched_at", "") for d in s["dispatches"]))
                current = [latest]
                latest["task_note"] = "last task " + sorted(
                    latest["dispatches"], key=lambda d: d.get("dispatched_at", ""))[-1]["owner"]
            for s in mine:
                s["group"] = "current" if s in current else ("earlier" if s in owned else "unattributed")
        elif mine:
            newest = max(mine, key=lambda s: (s.get("registered_at", ""), s["id"]))
            for s in mine:
                s["group"] = "current" if s is newest else "earlier"
        for s in slots:
            if s["role"] == role and not s.get("session") and s.get("historical"):
                s["group"] = "earlier" if s.get("owners") else "unattributed"
    for s in slots:
        s["historical"] = s.get("group", "current") != "current" if s.get("session") else s.get("historical", False)
        if s.get("session") is None and not s.get("historical"):
            s["group"] = "current"


HERDR_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def herdr_name(slot):
    """The Herdr agent name Docket launched a current worker under, if it recorded one."""
    if slot.get("historical") or not slot.get("session"):
        return ""
    named = [d for d in slot.get("dispatches", []) if d.get("agent")]
    name = max(named, key=lambda d: d.get("dispatched_at", ""))["agent"] if named else slot.get("registered_name", "")
    return name if isinstance(name, str) and HERDR_NAME.fullmatch(name) else ""


def release_shared_names(runs):
    """Herdr names are unique only among live agents, so a name two current roles claim proves neither."""
    claims = {}
    for run in runs:
        for s in run["slots"]:
            if s.get("herdr_name"):
                claims.setdefault(s["herdr_name"], []).append(s)
    for name, slots in claims.items():
        if len(slots) < 2:
            continue
        for s in slots:
            del s["herdr_name"]
            s["name_note"] = f"Herdr agent {name} is recorded for more than one current role, so it identifies neither."
            s["generation"] = digest({"generation": s["generation"], "name_note": s["name_note"]})
            s["capabilities"] = slot_capabilities(s)


def enrich_sessions(workspace, directory, slots, roles, tasks):
    """Combine exact registrations and noted native identities, never recent-session guesses."""
    noted = read_notes(workspace, directory, roles)
    dispatches = read_dispatches(workspace, directory)
    settled = {t["owner"] for t in tasks if t["status"] in ("approved", "waived", "completed")}
    matched = set()
    for s in slots:
        native = s.get("native") or {}
        process = s.get("process") or {}
        own = [d for d in dispatches if d.get("session") == s.get("session")]
        s["dispatches"] = own
        s["owners"] = [d["owner"] for d in own]
        # An explicit dispatch plus exactly one task's noted history in that launch
        # window can supply saved conversation, not an automatic terminal binding.
        if s["role"] == "implementor" and not native and len(own) == 1:
            dispatch = own[0]
            start, end = dispatch.get("dispatched_at", ""), dispatch.get("released_at", "")
            candidates = [(k, n) for k, n in noted.items() if k[0] == "implementor" and start and
                dispatch.get("session_generation") == s.get("registration_generation") and
                any(at >= start and (not end or at <= end) for at in n["owner_times"].get(dispatch["owner"], []))]
            if len(candidates) == 1:
                key, note = candidates[0]
                s["native"] = native = note["native"]
                s["history_attribution"] = "task-ledger"
                matched.add(key)
        for key, note in noted.items():
            same_proc = same_process(process, note["process"])
            same_native = native.get("harness") == key[1] and native.get("session_id") == key[2]
            if s["role"] == key[0] and (same_proc or same_native):
                if same_native and (not process or same_proc):
                    s["owners"] = list(dict.fromkeys(s["owners"] + note["owners"]))
                    s["noted_processes"] = s.get("noted_processes", []) + note["processes"]
                    matched.add(key)
                else:
                    s["mapping_issue"] = "Registration and recorded native session disagree. Choose an exact recorded history or bind the terminal explicitly."
    fold_dispatch_proofs(slots, dispatches)
    slots[:] = merge_duplicates(slots)
    for key, note in noted.items():
        if key in matched:
            continue
        role, harness, sid = key
        slots.append({"id": f"history:{role}:{harness}:{sid}", "role": role,
                      "label": role.title(), "generation": digest(note), **note,
                      "historical": True})
    # An identity claimed by different roles has no trustworthy role attribution.
    identities = {}
    for s in slots:
        n = s.get("native") or {}
        if n.get("session_id"):
            identities.setdefault((n.get("harness"), n["session_id"]), set()).add(s["role"])
    for s in slots:
        n = s.get("native") or {}
        if len(identities.get((n.get("harness"), n.get("session_id")), set())) > 1:
            s["mapping_issue"] = "This native session is recorded for different roles. Its role mapping is ambiguous."
        n_key = (n.get("harness"), n.get("session_id"))
        s["noted_roles"] = sorted({k[0] for k in noted if (k[1], k[2]) == n_key}) if n.get("session_id") else []
    classify_sessions(slots, dispatches, settled)
    for s in slots:
        name = herdr_name(s)
        if name:
            s["herdr_name"] = name
    for s in slots:
        s["dispatches"] = [{k: d.get(k, "") for k in ("owner", "state", "dispatched_at")}
                           for d in s.get("dispatches", [])]
        n = s.get("native") or {}
        if s["role"] == "implementor" and s.get("historical") and not n and s.get("owners"):
            s["superseded_history"] = any(x.get("native") and set(s["owners"]) <= set(x.get("owners", [])) for x in slots if x["role"] == "implementor")
    if not any(s["role"] == "implementor" and not s.get("historical") for s in slots) and "implementor" in roles:
        slots.append({"id": "implementor", "role": "implementor", "label": "Implementor",
                      "generation": "unregistered", "owners": [], "historical": False, "group": "current"})
    for s in slots:
        # The companion revision also binds attribution and history/live authority.
        s["generation"] = digest({k: s.get(k) for k in
            ("id", "generation", "native", "process", "owners", "historical", "mapping_issue", "group", "herdr_name")})
        s["identity"] = physical_identity(s)
        s["capabilities"] = slot_capabilities(s)
    return sorted(slots, key=lambda s: (roles.index(s["role"]), s.get("historical", False), s["id"]))


def read(path):
    if path.stat().st_size > LIMIT:
        raise ValueError(f"Document exceeds 1 MiB: {path.name}")
    return path.read_text(encoding="utf-8")


def documents(workspace):
    """Only public protocol documents, never captures, objects, or arbitrary files."""
    state = workspace / ".docket"
    found = {}
    for category in ("runs", "conversations"):
        directory = state / category
        if directory.is_symlink() or not directory.is_dir():
            continue
        for group in sorted(directory.iterdir()):
            if group.is_symlink() or not group.is_dir() or group.name.startswith("."):
                continue
            candidates = list(group.glob("*.mdx"))
            for child in ("docs",) if category == "runs" else ("messages", "participants"):
                folder = group / child
                if folder.is_dir() and not folder.is_symlink():
                    candidates.extend(folder.rglob("*.md"))
                    candidates.extend(folder.rglob("*.mdx"))
            for path in candidates:
                relative = path.relative_to(state)
                # Refuse symlink components, including .docket itself.
                if any((workspace / part).is_symlink() for part in
                       [Path(".docket"), *[Path(".docket") / p for p in relative.parents],
                        Path(".docket") / relative]):
                    continue
                if any(p.startswith(".") for p in relative.parts) or not path.is_file():
                    continue
                if not path.resolve().is_relative_to(state.resolve()):
                    continue
                found[relative.as_posix()] = path
    return found


def run_activity(paths, run):
    """Newest modification time, in seconds, of the run's documents."""
    times = []
    for key, path in paths.items():
        if key.startswith(f"runs/{run}/"):
            with contextlib.suppress(OSError):
                times.append(path.stat().st_mtime)
    return max(times, default=0)


def snapshot(workspace):
    paths = documents(workspace)
    runs = []
    for key, plan in paths.items():
        if not re.fullmatch(r"runs/[^/]+/plan\.mdx", key):
            continue
        run = plan.parent.name
        try:
            meta, body = parse(read(plan))
            policy = run_policy(plan.parent)
            roles = [r.strip() for r in policy["role_sessions"].split(",")]
            slots = [{"id": r, "role": r, "label": r.title(), "generation": "unregistered"}
                     for r in roles]
            session_dir = workspace / ".docket" / "sessions"
            if session_dir.is_dir() and not session_dir.is_symlink():
                registered_roles = set()
                for path in sorted(session_dir.glob("*.json")):
                    if path.is_symlink():
                        continue
                    try:
                        record = safe_record(path, workspace)
                        if not isinstance(record, dict):
                            continue
                        role = record.get("role")
                        if (record.get("run") != run or role not in roles or
                                record.get("workspace") != str(workspace)):
                            continue
                        sid = record["session"]
                        if (not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sid) or
                                not isinstance(record.get("native_harness", {}), dict) or
                                not isinstance(record.get("native_process", {}), dict)):
                            continue
                        native = record.get("native_harness", {})
                        if native and (native.get("harness") not in ("codex", "claude", "opencode") or
                                not isinstance(native.get("session_id"), str) or
                                not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", native["session_id"])):
                            continue
                        registered_roles.add(role)
                        generation = digest(record)
                        slots.append({"id": f"{role}:{sid}", "role": role,
                                      "label": record.get("name") or sid,
                                      "session": sid, "generation": generation,
                                      "native": record.get("native_harness", {}),
                                      "process": record.get("native_process", {}),
                                      "registration_generation": record.get("generation", 1),
                                      "registered_name": record.get("name", ""),
                                      "registered_at": record.get("registered_at", "")})
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
                slots = [s for s in slots if s.get("session") or s["role"] not in registered_roles]
                slots.sort(key=lambda slot: (roles.index(slot["role"]), slot["id"]))
            docs = []
            tasks = []
            for doc_key, path in paths.items():
                if not doc_key.startswith(f"runs/{run}/"):
                    continue
                document_meta, _ = parse(read(path))
                docs.append({"id": doc_key, "name": str(path.relative_to(plan.parent)),
                             "status": document_meta.get("status", "")})
                if path.name.endswith("-task.mdx"):
                    owner = path.name.removesuffix("-task.mdx")
                    reports = [p for k, p in paths.items() if
                               k.startswith(f"runs/{run}/") and p.parent == plan.parent and
                               p.name.startswith(f"{owner}-report-")]
                    report = latest(reports)
                    report_meta = parse(read(report))[0] if report else {}
                    tasks.append({"owner": owner, "status": report_meta.get("status", "draft"),
                                  "round": report_meta.get("round", ""),
                                  "title": document_meta.get("title", owner),
                                  "document": f"runs/{run}/{report.name}" if report else doc_key})
            docs.sort(key=lambda doc: (doc["name"] != "plan.mdx", _seq_key(Path(doc["name"]))))
            tasks.sort(key=lambda task: _seq_key(Path(task["owner"])))
            slots = enrich_sessions(workspace, plan.parent, slots, roles, tasks)
            heading = re.search(r"^#\s+(?:Plan:\s*)?(.+)$", body, re.M)
            runs.append({"id": run, "title": heading[1] if heading else run,
                         "status": meta.get("status", "unknown"), "policy": policy,
                         "slots": slots, "tasks": tasks, "documents": docs,
                         "updated_at": run_activity(paths, run)})
        except (OSError, ValueError, SystemExit) as error:
            runs.append({"id": run, "title": run, "error": str(error), "slots": [],
                         "tasks": [], "documents": [{"id": key, "name": "plan.mdx"}],
                         "status": "unavailable", "policy": {}, "updated_at": run_activity(paths, run)})
    runs.sort(key=lambda r: (-r["updated_at"], r["id"]))
    release_shared_names(runs)
    conversations = [{"id": key, "name": key.removeprefix("conversations/")}
                     for key in paths if key.startswith("conversations/")]
    return {"workspace": str(workspace), "runs": runs, "conversations": conversations}


def document(workspace, key):
    paths = documents(workspace)
    if key not in paths:
        raise ValueError("Document is not in the public Docket document inventory")
    return {"id": key, "text": read(paths[key])}


def selected_slot(workspace, run, slot_id, generation):
    current = next((r for r in snapshot(workspace)["runs"] if r["id"] == run), None)
    slot = next((s for s in current["slots"] if s["id"] == slot_id), None) if current else None
    if not slot or slot["generation"] != generation:
        raise ValueError("Session generation changed. Select its current history again.")
    return current, slot


def choice_label(role, harness, owners, group):
    """Role and provider first; exact identity stays in the details."""
    where = ", ".join(owners[:3])
    suffix = {"earlier": "earlier", "unattributed": "unattributed"}.get(group, "")
    return " · ".join(part for part in (role.title() if role else "Saved session", harness, where, suffix) if part)


def run_choices(run):
    """Saved sessions this run recorded or registered, merged by exact native identity."""
    choices = {}
    for s in run["slots"]:
        native = s.get("native") or {}
        if not native.get("session_id") or not native.get("harness"):
            continue
        key = f"{native['harness']}:{native['session_id']}"
        c = choices.setdefault(key, {"id": key, "title": native["session_id"], "native": native,
                                     "recorded_roles": [], "owners": [], "groups": [], "processes": []})
        for role in (s["role"], *s.get("noted_roles", [])):
            if role not in c["recorded_roles"]:
                c["recorded_roles"].append(role)
        c["owners"] = list(dict.fromkeys(c["owners"] + s.get("owners", [])))
        c["groups"].append(s.get("group", "current"))
        for proc in [s.get("process") or {}, *s.get("processes", []), *s.get("noted_processes", [])]:
            if proc.get("pid"):
                c["processes"].append(proc)
    return choices


def finish_choice(c, run_id=""):
    """Add the label and the process proof only when every record agrees on one pid:start."""
    processes = c.pop("processes", [])
    distinct = {(p.get("pid"), p.get("start"), p.get("harness") or c["native"]["harness"]) for p in processes}
    if len(distinct) == 1:
        pid, start, harness = next(iter(distinct))
        if harness == c["native"]["harness"]:
            c["process"] = {"harness": harness, "pid": pid, "start": start}
    groups = c.pop("groups", [])
    group = "current" if "current" in groups else (groups[0] if groups else "")
    c["group"] = group
    role = c["recorded_roles"][0] if c.get("recorded_roles") else ""
    c["label"] = choice_label(role, c["native"]["harness"], c.get("owners", []), group)
    if run_id:
        c["run"] = run_id
    return c


def history_choices(workspace, run, slot_id, generation, scope="run"):
    """Saved sessions to choose from: this run's by default, the whole project only on request."""
    current, slot = selected_slot(workspace, run, slot_id, generation)
    if slot.get("historical"):
        return {"choices": [], "scope": scope}
    choices = run_choices(current)
    if scope == "project":
        for other in snapshot(workspace)["runs"]:
            if other["id"] == run:
                continue
            for key, c in run_choices(other).items():
                if key not in choices:
                    choices[key] = {**c, "run": other["id"], "recorded_roles": [], "owners": [],
                                    "foreign": True, "groups": [], "processes": []}
        for c in project_sessions(workspace):
            choices.setdefault(c["id"], {**c, "recorded_roles": [], "owners": [], "groups": [],
                                         "processes": []})
    finished = [finish_choice(c, c.get("run", "")) for c in choices.values()]
    # Identical labels get a short tail so two saved sessions never look like one.
    counts = {}
    for c in finished:
        counts[c["label"]] = counts.get(c["label"], 0) + 1
    for c in finished:
        if counts[c["label"]] > 1:
            c["label"] += f" · …{c['native']['session_id'][-6:]}"
    return {"choices": sorted(finished, key=lambda c: (c["label"].lower(), c["id"])), "scope": scope}


def history(workspace, run, slot_id, generation, choice=None, scope="run"):
    _, slot = selected_slot(workspace, run, slot_id, generation)
    if choice:
        if slot.get("historical"):
            raise ValueError("Earlier task history cannot be relinked to another session")
        listed = history_choices(workspace, run, slot_id, generation, scope)["choices"]
        selected = next((c for c in listed if c["id"] == choice), None)
        if not selected:
            raise ValueError("Conversation is not in this run's session list. Show project sessions to choose another.")
        slot = {**slot, "native": selected["native"]}
        slot.pop("mapping_issue", None)  # The user chose history explicitly; no Docket state is rewritten.
        result = load_history(workspace, slot)
        result["selection"] = choice
        result["title"] = selected["label"]
        result["recorded_roles"] = selected["recorded_roles"]
        if result["capabilities"]["history"]:
            provenance = ', '.join(selected["recorded_roles"]) or 'no Docket role'
            result["reason"] = f"Saved history of {selected['label']}, recorded under {provenance}. It grants no input; use the connection above to send."
        return result
    return load_history(workspace, slot)


if __name__ == "__main__":
    try:
        workspace = Path(sys.argv[1]).resolve(strict=True)
        if len(sys.argv) > 2 and sys.argv[2] == "--history":
            run_id, slot_id, generation, *extra = sys.argv[3:8]
            result = history(workspace, run_id, slot_id, generation, (extra + [""])[0] or None,
                             (extra + ["", "run"])[1] or "run")
        elif len(sys.argv) > 2 and sys.argv[2] == "--history-choices":
            result = history_choices(workspace, *sys.argv[3:7])
        else:
            result = document(workspace, sys.argv[2]) if len(sys.argv) > 2 else snapshot(workspace)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
