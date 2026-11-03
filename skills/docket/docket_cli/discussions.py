"""Attributed, resumable dialogue between an existing session and a visible peer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from .common import DELIVERY_LEASE_SECONDS, STATE_DIR, die, stamp
from .frontmatter import parse, render, sections
from .paths import root
from .publication import fault, publish, publish_json
from .locks import run_lock
from .sessions import calling_harness, harness_process_ref, harness_session
from .discussion_groups import (
    GROUP_PEERS, GROUP_PROTOCOL, GROUP_TURNS, group_actionable, group_peer, group_prompt_text,
    group_record_valid, group_replay, group_roster, group_settle, group_transition,
)


DISCUSSION_PARTICIPANTS = ("invoker", "peer", "human")
DISCUSSION_AGENTS = ("invoker", "peer")
DISCUSSION_STATES = ("open", "paused", "concluded", "closed")


def discussion_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        die("discussion and session IDs must use letters, digits, dots, underscores, or hyphens")
    return value


def discussion_protocol(d: Path) -> str:
    path = d / "discussion.mdx"
    return discussion_document(path)[0].get("protocol", "") if path.exists() else ""


def discussion_agents(d: Path) -> tuple[str, ...]:
    if discussion_protocol(d) != GROUP_PROTOCOL:
        return DISCUSSION_AGENTS
    return tuple(discussion_document(d / "discussion.mdx")[0]["roster"].split(","))


def discussion_load(d: Path) -> dict:
    return group_view(d) if discussion_protocol(d) == GROUP_PROTOCOL else discussion_view(d)


def discussion_directory(cid: str) -> Path:
    return root() / STATE_DIR / "conversations" / discussion_id(cid)


def discussion_document(path: Path) -> tuple[dict[str, str], str, str]:
    try:
        data = path.read_bytes()
        meta, body = parse(data.decode())
    except (OSError, UnicodeError) as exc:
        die(f"cannot read discussion artifact {path}: {exc}")
    return meta, body, "sha256:" + hashlib.sha256(data).hexdigest()


def discussion_view(d: Path) -> dict:
    """Read a complete chained transcript without changing delivery state."""
    meta, body, parent = discussion_document(d / "discussion.mdx")
    if meta.get("protocol") != "discussion-v1" or meta.get("discussion") != d.name:
        die(f"invalid discussion document: {d / 'discussion.mdx'}")
    try:
        limit = int(meta["max_turns"])
        if limit < 1:
            raise ValueError()
    except (KeyError, ValueError):
        die(f"invalid max_turns in {d / 'discussion.mdx'}")
    messages = []
    status, phase, turns = "open", 1, 0
    for seq, path in enumerate(sorted((d / "messages").glob("*.mdx")), 1):
        record, text, digest = discussion_document(path)
        if (path.name != f"{seq:06d}.mdx" or record.get("sequence") != str(seq)
                or record.get("parent_digest") != parent
                or record.get("reply_to") != str(seq - 1)
                or record.get("sender") not in DISCUSSION_PARTICIPANTS
                or record.get("sender_kind") != ("human" if record.get("sender") == "human" else "agent")
                or record.get("status") not in DISCUSSION_STATES):
            die(f"DAMAGED discussion history: {path}; missing, changed, or invalid message")
        if record.get("kind") == "continue":
            phase, turns = phase + 1, 0
        elif record.get("sender") in DISCUSSION_AGENTS and record.get("kind") in ("message", "proposal"):
            turns += 1
        status = record["status"]
        messages.append({**record, "sequence": seq, "body": text,
                         "digest": digest, "path": str(path)})
        parent = digest
    return {"id": d.name, "topic": sections(body).get("Topic", ""), "briefing": body,
            "status": status, "phase": phase, "turns": turns, "max_turns": limit,
            "messages": messages, "digest": parent, "metadata": meta}


def group_view(d: Path) -> dict:
    """Read a complete chained group transcript without changing delivery state."""
    meta, body, parent = discussion_document(d / "discussion.mdx")
    roster = group_roster(meta)
    if meta.get("protocol") != GROUP_PROTOCOL or meta.get("discussion") != d.name or not roster:
        die(f"invalid discussion document: {d / 'discussion.mdx'}")
    try:
        limit = int(meta["max_turns"])
        if limit < 1:
            raise ValueError()
    except (KeyError, ValueError):
        die(f"invalid max_turns in {d / 'discussion.mdx'}")
    messages, status = [], "open"
    for seq, path in enumerate(sorted((d / "messages").glob("*.mdx")), 1):
        record, text, digest = discussion_document(path)
        if (path.name != f"{seq:06d}.mdx" or record.get("sequence") != str(seq)
                or record.get("parent_digest") != parent or record.get("reply_to") != str(seq - 1)
                or not group_record_valid(record, roster, DISCUSSION_STATES)):
            die(f"DAMAGED discussion history: {path}; missing, changed, or invalid message")
        status = record["status"]
        messages.append({**record, "sequence": seq, "body": text, "digest": digest, "path": str(path)})
        parent = digest
    identities = {}
    for name in roster:
        reg = discussion_registration(d, name)
        if reg:
            identities[name] = (reg["session"], reg["generation"])
    state = group_replay(roster, messages, identities)
    return {"id": d.name, "protocol": GROUP_PROTOCOL, "topic": sections(body).get("Topic", ""),
            "briefing": body, "status": status, "roster": roster, "phase": state["phase"],
            "turns": state["turns"], "max_turns": limit, "floor": state["floor"],
            "pending": state["pending"], "conclusion": state["conclusion"],
            "replaced": state["replaced"], "withdrawn": state["withdrawn"],
            "actionable": group_actionable(status, state),
            "interrupted": state["interrupted"], "identities": identities,
            "messages": messages, "digest": parent, "metadata": meta}


def discussion_native_identity() -> dict[str, str]:
    process = harness_process_ref()
    if not process or not process.get("start"):
        return {}
    command_argv = None
    if process["harness"] == "opencode":
        # Compare complete literal arguments: substring selectors confuse C01
        # with C010, and shell variables cannot prove this command's session.
        args = sys.argv[1:]
        if len(args) < 2 or args[0] != "discuss" or args[1].startswith("-"):
            return {}
        command_argv = args
    session = harness_session([], require_unique=True, command_argv=command_argv) or {}
    if process["harness"] == "opencode" and not session.get("session_id"):
        return {}
    return {"native_harness": str(process["harness"]), "native_pid": str(process["pid"]),
            "native_start": str(process["start"]),
            "native_session": str(session.get("session_id", ""))}


def discussion_registration(d: Path, who: str) -> dict[str, str]:
    agents = discussion_agents(d)
    if who not in agents:
        die("an agent participant must be invoker or peer" if agents == DISCUSSION_AGENTS
            else f"an agent participant must be one of {', '.join(agents)}")
    path = d / "participants" / f"{who}.mdx"
    if not path.exists():
        return {}
    reg, _, _ = discussion_document(path)
    if (reg.get("participant") != who or reg.get("discussion") != d.name
            or not reg.get("session") or not reg.get("generation", "").isdigit()):
        die(f"invalid participant registration: {path}")
    return reg


def discussion_matches_native(reg: dict, identity: dict) -> bool:
    return bool(identity and reg.get("native_pid") and all(
        reg.get(key, "") == value for key, value in identity.items()))


def discussion_check_participant(d: Path, who: str, session: str = "") -> dict:
    reg = discussion_registration(d, who)
    if not reg:
        die(f"join first: docket discuss {d.name} --join --as {who} --session SESSION")
    native = discussion_native_identity()
    if session:
        discussion_id(session)
        if session != reg["session"]:
            die(f"{who} belongs to session {reg['session']}; this session is stale")
        if native and reg.get("native_pid") and not discussion_matches_native(reg, native):
            die("participant belongs to another harness session; join with --replace to recover")
    elif not discussion_matches_native(reg, native):
        die("cannot identify this participant session; supply --session or join inside its harness")
    return reg


def discussion_join(d: Path, a: argparse.Namespace) -> None:
    who = a.as_participant
    agents = discussion_agents(d)
    if who not in agents:
        die("--join needs --as invoker or --as peer" if agents == DISCUSSION_AGENTS
            else f"--join needs --as one of {', '.join(agents)}")
    native = discussion_native_identity()
    session = a.session
    if not session and native:
        session = "native-" + hashlib.sha256(json.dumps(native, sort_keys=True).encode()).hexdigest()[:24]
    if not session:
        die("--join outside a detected harness requires --session SESSION")
    discussion_id(session)
    old = discussion_registration(d, who)
    same = bool(old and old["session"] == session and all(
        old.get(key, "") == native.get(key, "")
        for key in ("native_harness", "native_pid", "native_start", "native_session")))
    if old and not same and not a.replace:
        die(f"{who} already joined as {old['session']}; use --join --replace to recover this participant")
    for name in agents:
        other = discussion_registration(d, name) if name != who else {}
        if other and (session == other["session"] or discussion_matches_native(other, native)):
            die("invoker and peer must use different sessions" if agents == DISCUSSION_AGENTS
                else "group participants must use different sessions")
    if same:
        print(f"already joined {d.name} as {who} (generation {old['generation']})")
        view = discussion_load(d)
        if "roster" in view and group_replacement_pending(view, who, old):
            group_record_replacement(d, view, who, old)
            print("recorded the interrupted replacement in the transcript")
        discussion_join_guidance(d, who, session, native)
        return
    view = discussion_load(d)
    harness = native.get("native_harness", "") or view["metadata"].get(f"{who}_harness", "unknown")
    record = {"discussion": d.name, "participant": who, "sender_kind": "agent",
              "session": session, "generation": str(int(old.get("generation", "0")) + 1),
              "harness": harness, "harness_observed": "yes" if native else "no",
              "model_requested": view["metadata"].get(f"{who}_model", "unknown"),
              "joined_at": stamp(), **native}
    publish(d / "participants" / f"{who}.mdx", render(record, ""))
    if old and "roster" in view:
        fault("discuss:after-group-registration")
        group_record_replacement(d, group_view(d), who, record)
    print(f"joined {d.name} as {who}, session {session} (generation {record['generation']})")
    discussion_join_guidance(d, who, session, native)


def discussion_join_guidance(d: Path, who: str, session: str, native: dict) -> None:
    if not native:
        print("No native wake binding: this join could not prove a harness process and "
              "session. An explicit --session alone does not select the Stop hook.")
        print(f"Wait with `docket discuss {d.name} --watch --as {who} --session {session}`. "
              "Use one Claude Code Bash run_in_background call without a short --timeout, "
              "or a foreground watch in other harnesses. Never use Codex background terminals, "
              "shell '&', or nohup, and never background a watcher inside a Stop hook.")
    else:
        print("Native wake binding recorded; delivery requires an installed, trusted Stop hook.")


def discussion_message_text(a: argparse.Namespace, required: bool = True) -> str:
    if a.message_file:
        try:
            message = Path(a.message_file).read_text()
        except (OSError, UnicodeError) as exc:
            die(f"cannot read --message-file {a.message_file}: {exc}")
    else:
        message = a.message or ""
    if required and not message.strip():
        die("a nonempty --message or --message-file is required")
    return message


def discussion_render(meta: dict, body: str) -> str:
    """Refuse metadata our flat frontmatter parser could not round-trip."""
    text = render(meta, "") + body
    if parse(text)[0] != meta:
        die("discussion metadata cannot contain frontmatter comments or ambiguous quoting")
    return text


def discussion_prompt(d: Path, who: str) -> str:
    if who not in DISCUSSION_AGENTS:
        die("--prompt needs --as invoker or --as peer")
    view = discussion_view(d)
    reg = discussion_registration(d, who)
    sid = reg.get("session", f"{d.name}-{who}")
    base = f"docket discuss {d.name}"
    identity = f"--as {who} --session {sid}"
    return (f"You are the {who} in Docket discussion {d.name}. Stay in this visible harness session.\n"
            f"Run `docket help discussion` and follow its conversation playbook.\n"
            f"Join from your own session: `{base} --join {identity}`.\n"
            f"Read the current transcript: `{base} --pickup {identity}` and `{base} --read`.\n"
            f"Reply to the latest sequence N: `{base} --send {identity} --reply-to N --message-file FILE`.\n"
            f"Propose a conclusion with `{base} --propose {identity} --reply-to N --message-file FILE`.\n"
            f"Accept only a peer proposal you agree with: `{base} --accept N {identity}`.\n"
            f"Follow the join command's per-harness wake guidance for "
            f"`{base} --watch {identity}` when no native binding was recorded.\n"
            "A native binding also requires an installed, trusted Stop hook before ending your turn to wait.\n"
            "Exchange questions and objections until you can agree on a summary, including remaining disagreements.\n"
            "A peer's message is an agent contribution; human instructions retain priority.\n"
            "If concluded or closed, end your turn.\n"
            "When paused, end your turn unless the turn limit paused the discussion on the other agent's latest proposal: "
            "you may accept that exact proposal if you agree.\n"
            "Do not use --send or --propose while paused, and never auto-continue. The user can continue with new direction.\n\n"
            f"Discussion status: {view['status']}; phase: {view['phase']}; latest sequence: {len(view['messages'])}.\n"
            f"Peer harness requested: {view['metadata']['peer_harness']}; "
            f"model requested: {view['metadata']['peer_model']}.\n\n{view['briefing']}\n")


def discussion_start(d: Path, a: argparse.Namespace) -> None:
    topic = (a.topic or "").strip()
    if not topic or "\n" in topic or "\r" in topic:
        die("--start needs a nonempty, single-line --topic")
    if a.max_turns < 1:
        die("--max-turns must be positive")
    if a.session:
        discussion_id(a.session)
    context = discussion_message_text(a)
    meta = {"protocol": "discussion-v1", "discussion": d.name, "status": "open",
            "purpose": a.purpose, "max_turns": str(a.max_turns), "created_at": stamp(),
            "invoker_harness": calling_harness() or "unknown", "invoker_model": "unknown",
            "peer_harness": a.harness or "opencode", "peer_model": a.model or "unknown"}
    text = discussion_render(meta, f"## Topic\n\n{topic}\n\n## Briefing\n\n{context}\n")
    with run_lock(d):
        if (d / "discussion.mdx").exists():
            die(f"discussion {d.name} already exists; read it or --continue it")
        publish(d / "discussion.mdx", text)
        if discussion_native_identity() or a.session:
            discussion_join(d, argparse.Namespace(as_participant="invoker", session=a.session, replace=False))
        prompt = d / ".prompts" / "peer.txt"
        publish(prompt, discussion_prompt(d, "peer"))
    print(f"discussion {d.name}: open\npeer harness: {meta['peer_harness']}\nprompt: {prompt}")
    print("Start the peer in a visible harness session and send it this prompt file.")


def discussion_append(d: Path, a: argparse.Namespace) -> None:
    who = a.as_participant
    if who not in DISCUSSION_PARTICIPANTS:
        die("a message needs --as invoker, peer, or human")
    kind = ("proposal" if a.propose else "agreement" if a.accept is not None else
            "pause" if a.pause else "continue" if a.continue_discussion else "close" if a.close else "message")
    if kind in ("proposal", "agreement") and who == "human":
        die("a conclusion needs a proposal from one agent and acceptance by the other")
    if kind in ("pause", "continue", "close") and who == "peer":
        die("only the invoker or human controls pausing, continuing, and closing")
    text = discussion_message_text(a, required=kind != "agreement")
    with run_lock(d):
        view = discussion_view(d)
        reg = discussion_check_participant(d, who, a.session) if who != "human" else {}
        seq = len(view["messages"])
        reply = a.accept if kind == "agreement" else a.reply_to
        if reply is None and kind in ("pause", "continue", "close"):
            reply = seq
        if reply != seq:
            die(f"--reply-to must name the latest sequence {seq}; read the updated transcript first")
        status = view["status"]
        if status == "closed":
            die("discussion is closed; start a new discussion with a new ID")
        if kind == "continue":
            if status not in ("paused", "concluded"):
                die("--continue requires a paused or concluded discussion")
            status = "open"
        elif kind == "pause":
            if status not in ("open", "paused"):
                die("only an open or paused discussion can be paused")
            # Record user interruptions even at the cap: the new latest message
            # invalidates a proposal that would otherwise remain acceptable.
            status = "paused"
        elif kind == "close":
            status = "closed"
        elif kind == "agreement":
            proposal = view["messages"][-1] if seq else {}
            if (status not in ("open", "paused") or proposal.get("kind") != "proposal"
                    or proposal.get("sender") == who):
                die("--accept requires the other agent's latest, unchanged proposal")
            text = text or f"Accepted proposal {seq}.\n\n{proposal['body']}"
            status = "concluded"
        elif status != "open":
            die(f"discussion is {status}; the invoker or human can --continue with new direction")
        if who in DISCUSSION_AGENTS and kind in ("message", "proposal", "agreement"):
            last_agent = next((m for m in reversed(view["messages"])
                               if m["sender"] in DISCUSSION_AGENTS or m["kind"] == "continue"), None)
            previous = last_agent["sender"] if last_agent and last_agent["sender"] in DISCUSSION_AGENTS else "invoker"
            if previous == who:
                die("wait for the other agent's reply; agents take alternating turns")
        recipient = "both" if who == "human" or kind in ("pause", "close") else "peer" if who == "invoker" else "invoker"
        if kind == "continue":
            recipient = "peer"
        if kind in ("message", "proposal") and who in DISCUSSION_AGENTS and view["turns"] + 1 >= view["max_turns"]:
            status = "paused"
        meta = {"discussion": d.name, "sequence": str(seq + 1), "reply_to": str(seq),
                "parent_digest": view["digest"], "sender": who,
                "sender_kind": "human" if who == "human" else "agent", "to": recipient,
                "kind": kind, "status": status, "at": stamp(),
                "session": reg.get("session", "human"), "generation": reg.get("generation", "0"),
                "harness": reg.get("harness", "human"),
                "harness_observed": reg.get("harness_observed", "no"),
                "model_requested": reg.get("model_requested", "unknown")}
        path = d / "messages" / f"{seq + 1:06d}.mdx"
        publish(path, discussion_render(meta, text))
    print(f"[{meta['sender_kind']}:{who}] {d.name} message {seq + 1} -> {recipient}; status: {status}")


def group_publish(d: Path, view: dict, fields: dict, reg: dict, text: str) -> dict:
    """Append one group message under the caller's lock."""
    seq = len(view["messages"])
    fields = {**fields, "session": reg.get("session", "human"), "generation": reg.get("generation", "0")}
    fields, to = group_settle(view, fields)
    meta = {"discussion": d.name, "sequence": str(seq + 1), "reply_to": str(seq),
            "parent_digest": view["digest"], "sender": fields["sender"],
            "sender_kind": "human" if fields["sender"] == "human" else "agent", "to": ",".join(to) or "none",
            "kind": fields["kind"], "status": fields["status"], "at": stamp(),
            "session": fields["session"], "generation": fields["generation"],
            "harness": reg.get("harness", "human"),
            "harness_observed": reg.get("harness_observed", "no"),
            "model_requested": reg.get("model_requested", "unknown"),
            **{k: v for k, v in fields.items() if k not in ("sender", "kind", "status", "session", "generation")}}
    publish(d / "messages" / f"{seq + 1:06d}.mdx", discussion_render(meta, text))
    return meta


def group_replacement_pending(view: dict, who: str, reg: dict) -> bool:
    """A replacement generation whose transcript event an interruption never wrote."""
    return int(reg["generation"]) > 1 and not any(
        m["kind"] == "generation" and m["sender"] == who and m["session"] == reg["session"]
        and m["generation"] == reg["generation"] for m in view["messages"])


def group_record_replacement(d: Path, view: dict, who: str, reg: dict) -> None:
    fields = {"sender": who, "kind": "generation", "status": view["status"]}
    # The registration may already have withdrawn the proposal; record which one either way.
    withdrew = view["pending"]["sequence"] if view["pending"] else view["withdrawn"]
    if withdrew:
        fields["withdrew"] = str(withdrew)
    group_publish(d, view, fields, reg,
                  f"{who} rejoined as session {reg['session']} (generation {reg['generation']}).\n")


def group_start(d: Path, a: argparse.Namespace) -> None:
    topic = (a.topic or "").strip()
    if not topic or "\n" in topic or "\r" in topic:
        die("--start needs a nonempty, single-line --topic")
    if a.max_turns < 1:
        die("--max-turns must be positive")
    if a.harness or a.model:
        die("a group names each peer's harness and model in --peer NAME:HARNESS[:MODEL]")
    peers = [group_peer(value) for value in a.peer]
    if not GROUP_PEERS[0] <= len(peers) <= GROUP_PEERS[1]:
        die(f"a group discussion needs {GROUP_PEERS[0]} to {GROUP_PEERS[1]} --peer entries; "
            "a single peer uses --harness and --model")
    if len({name for name, _, _ in peers}) != len(peers):
        die("--peer names must be distinct")
    if a.session:
        discussion_id(a.session)
    context = discussion_message_text(a)
    meta = {"protocol": GROUP_PROTOCOL, "discussion": d.name, "status": "open",
            "purpose": a.purpose, "max_turns": str(a.max_turns), "created_at": stamp(),
            "roster": ",".join(["invoker", *(name for name, _, _ in peers)]),
            "invoker_harness": calling_harness() or "unknown", "invoker_model": "unknown"}
    for name, harness, model in peers:
        meta[f"{name}_harness"], meta[f"{name}_model"] = harness, model
    text = discussion_render(meta, f"## Topic\n\n{topic}\n\n## Briefing\n\n{context}\n")
    with run_lock(d):
        if (d / "discussion.mdx").exists():
            die(f"discussion {d.name} already exists; read it or --continue it")
        publish(d / "discussion.mdx", text)
        if discussion_native_identity() or a.session:
            discussion_join(d, argparse.Namespace(as_participant="invoker", session=a.session, replace=False))
        prompts = []
        for name, harness, _ in peers:
            prompt = d / ".prompts" / f"{name}.txt"
            publish(prompt, group_prompt(d, name))
            prompts.append(f"{name} harness: {harness}\n{name} prompt: {prompt}")
    print(f"discussion {d.name}: open\nroster: {meta['roster']}\n" + "\n".join(prompts))
    print("Start each peer in its own visible harness session and send it its prompt file.")


def group_prompt(d: Path, who: str) -> str:
    view = group_view(d)
    if who not in view["roster"]:
        die(f"--prompt needs --as one of {', '.join(view['roster'])}")
    return group_prompt_text(view, who, discussion_registration(d, who).get("session", f"{d.name}-{who}"))


def group_append(d: Path, a: argparse.Namespace) -> None:
    who = a.as_participant
    kind = ("proposal" if a.propose else "agreement" if a.accept is not None else
            "dissent" if a.dissent is not None else "pause" if a.pause else
            "continue" if a.continue_discussion else "close" if a.close else "message")
    text = discussion_message_text(a, required=kind != "agreement")
    with run_lock(d):
        view = group_view(d)
        reg = discussion_check_participant(d, who, a.session) if who != "human" else {}
        if reg and group_replacement_pending(view, who, reg):
            die(f"this replacement was interrupted before the transcript recorded it; rerun "
                f"`docket discuss {d.name} --join --as {who} --session {reg['session']}` first")
        fields = group_transition(view, who, kind, a.accept if kind == "agreement" else a.dissent, a.reply_to)
        meta = group_publish(d, view, fields, reg, text or f"Accepted proposal {fields.get('proposal')}.\n")
    outcome = f"; outcome: {meta['outcome']}" if "outcome" in meta else ""
    print(f"[{meta['sender_kind']}:{who}] {d.name} message {meta['sequence']} -> {meta['to']}; "
          f"status: {meta['status']}{outcome}")


def group_unread(d: Path, who: str, reg: dict, view: dict) -> list[dict]:
    cursor = discussion_receipt(d, who, reg)
    # A reply proves this generation consumed its parent; a replacement and its own
    # generation event prove nothing, so it picks up the whole transcript again.
    cursor = max([cursor, *[m["sequence"] for m in view["messages"]
                          if m["sender"] == who and m["generation"] == reg["generation"]
                          and m["session"] == reg["session"] and m["kind"] in GROUP_TURNS]])
    unread = [m for m in view["messages"] if m["sequence"] > cursor and m["sender"] != who]
    if who != "invoker" and cursor < 0:
        unread.insert(0, {"sequence": 0, "sender": "invoker", "sender_kind": "agent",
                          "kind": "briefing", "body": view["briefing"],
                          "harness": view["metadata"]["invoker_harness"], "model_requested": "unknown",
                          "digest": discussion_document(d / "discussion.mdx")[2]})
    return unread


def discussion_receipt(d: Path, who: str, reg: dict) -> int:
    path = d / ".delivery" / f"{who}-receipt.json"
    if not path.exists():
        return -1
    try:
        data = json.loads(path.read_text())
        if data["generation"] != reg["generation"] or data["session"] != reg["session"]:
            return -1
        return int(data["through"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        die(f"invalid discussion receipt {path}: {exc}")


def discussion_unread(d: Path, who: str, reg: dict, view: dict) -> list[dict]:
    if "roster" in view:
        return group_unread(d, who, reg, view)
    cursor = discussion_receipt(d, who, reg)
    # A reply proves this generation consumed its parent. A replacement must pick up again.
    cursor = max([cursor, *[m["sequence"] for m in view["messages"]
                          if m["sender"] == who and m["generation"] == reg["generation"]
                          and m["session"] == reg["session"]
                          and m["kind"] in ("message", "proposal", "agreement")]])
    unread = [m for m in view["messages"] if m["sequence"] > cursor
              and m["sender"] != who and m["to"] in (who, "both")]
    if who == "peer" and cursor < 0:
        unread.insert(0, {"sequence": 0, "sender": "invoker", "sender_kind": "agent",
                          "kind": "briefing", "body": view["briefing"],
                          "harness": view["metadata"]["invoker_harness"], "model_requested": "unknown",
                          "digest": discussion_document(d / "discussion.mdx")[2]})
    return unread


def discussion_print_messages(view: dict, messages: list[dict]) -> None:
    print(f"discussion {view['id']}: {view['status']} | phase {view['phase']} | "
          f"turns {view['turns']}/{view['max_turns']}")
    if "roster" in view:
        print(f"roster {','.join(view['roster'])} | floor {view['floor']}")
        pending = view["pending"]
        if pending:
            print(f"pending proposal {pending['sequence']} by {pending['proposer']} | "
                  f"accepted {','.join(pending['accepted']) or 'none'} | "
                  f"dissented {','.join(pending['dissented']) or 'none'} | "
                  f"missing {','.join(pending['missing'])}")
    for m in messages:
        marker = f"[{m['sender_kind']}:{m['sender']}]"
        answer = "".join(f" {key}={m[key]}" for key in ("proposal", "outcome", "dissenters", "withdrew")
                         if key in m)
        print(f"\n{marker} discussion={view['id']} reply={m['sequence']} kind={m['kind']}{answer} "
              f"harness={m['harness']} harness-observed={m.get('harness_observed', 'no')} "
              f"model-requested={m['model_requested']}")
        print(m["body"], end="" if m["body"].endswith("\n") else "\n")
        print(f"[end {m['sender_kind']} message {m['sequence']}]")


def discussion_pickup(d: Path, a: argparse.Namespace) -> None:
    with run_lock(d):
        reg = discussion_check_participant(d, a.as_participant, a.session)
        view = discussion_load(d)
        unread = discussion_unread(d, a.as_participant, reg, view)
        discussion_print_messages(view, unread)
        sys.stdout.flush()
        publish_json(d / ".delivery" / f"{a.as_participant}-receipt.json",
                     {"session": reg["session"], "generation": reg["generation"],
                      "through": len(view["messages"]), "received_at": stamp()})


def discussion_joined() -> list[tuple[Path, str, str]]:
    """Select only explicit bindings or participants of this proven harness process."""
    selected = os.environ.get("DOCKET_DISCUSSION", "").strip()
    who = os.environ.get("DOCKET_PARTICIPANT", "").strip()
    session = os.environ.get("DOCKET_DISCUSSION_SESSION", "").strip()
    if selected or who or session:
        if not selected or not who:
            die("discussion hook selection needs DOCKET_DISCUSSION and DOCKET_PARTICIPANT=invoker|peer")
        d = discussion_directory(selected)
        agents = discussion_agents(d)
        if who not in agents:
            die("discussion hook selection needs DOCKET_DISCUSSION and DOCKET_PARTICIPANT=invoker|peer"
                if agents == DISCUSSION_AGENTS else
                f"discussion hook selection needs DOCKET_PARTICIPANT naming one of {', '.join(agents)}")
        reg = discussion_check_participant(d, who, session)
        return [(d, who, reg["session"])]
    directory = root() / STATE_DIR / "conversations"
    if not directory.is_dir():
        return []
    native = discussion_native_identity()
    if not native:
        return []
    found = []
    for path in sorted(directory.glob("*/participants/*.mdx")):
        d = path.parent.parent
        if path.stem not in discussion_agents(d):
            continue
        reg = discussion_registration(d, path.stem)
        if discussion_matches_native(reg, native):
            found.append((d, path.stem, reg["session"]))
    return found


def discussion_announce(bindings: list[tuple[Path, str, str]]) -> tuple[list[str], bool]:
    """Lease notices, not message prose; pickup proves receipt after a native wake."""
    notices, active = [], False
    for d, who, session in bindings:
        with run_lock(d):
            reg = discussion_check_participant(d, who, session)
            view = discussion_load(d)
            unread = discussion_unread(d, who, reg, view)
            if "roster" in view:
                # Observing another member's message is not a turn; only the floor wakes.
                # A replacement is bookkeeping unless it withdrew a live proposal,
                # which changes what the floor holder or invoker must do next.
                unread = [m for m in unread if m["kind"] != "generation" or m.get("withdrew")]
                actionable = who in view["actionable"]
                active = (active or view["status"] == "open" or bool(view["pending"])
                          or (actionable and bool(unread)))
                if not actionable:
                    continue
            else:
                active = active or view["status"] == "open" or bool(unread)
            if not unread:
                continue
            event = f"{reg['generation']}:{unread[-1]['sequence']}:{unread[-1]['digest']}"
            path = d / ".delivery" / f"{who}-announce.json"
            old = {}
            if path.exists():
                try:
                    old = json.loads(path.read_text())
                    if not isinstance(old, dict):
                        raise ValueError("expected an announcement object")
                    lease_until = float(old.get("lease_until", 0))
                except (OSError, ValueError, TypeError) as exc:
                    die(f"invalid discussion announcement {path}: {exc}")
                if old.get("event") == event and lease_until > time.time():
                    continue
            publish_json(path, {"event": event, "lease_until": time.time() + DELIVERY_LEASE_SECONDS})
            sender = unread[-1]
            reason = (f"message {sender['sequence']} for {who}" if "roster" not in view else
                      f"floor for {who} at message {sender['sequence']}" if who == view["floor"]
                      and (view["status"] == "open" or view["pending"]) else
                      f"{view['status']} at message {sender['sequence']}, for {who} to report")
            notices.append(f"[{sender['sender_kind']}:{sender['sender']}] discussion {d.name}: "
                           f"{reason}; status {view['status']}.\n"
                           f"  docket discuss {d.name} --pickup --as {who} --session {session}")
    return notices, active


def discussion_watch(a: argparse.Namespace) -> None:
    native = os.environ.get("DOCKET_WATCH_HOOK") == "1"
    if a.timeout < 0 or a.interval < 1:
        die("--timeout must be nonnegative and --interval must be positive")
    if not native and calling_harness() and a.timeout < 590:
        die("inside a harness, discussion --watch needs --timeout of at least 590 seconds")
    deadline = time.monotonic() + a.timeout
    while True:
        bindings = discussion_joined() if a.watch_joined else [
            (discussion_directory(a.discussion), a.as_participant, a.session)]
        notices, active = discussion_announce(bindings)
        if notices:
            print("Docket peer conversation:\n" + "\n".join(notices), file=sys.stderr, flush=True)
            raise SystemExit(3 if native else 2)
        if not active or time.monotonic() >= deadline:
            return
        time.sleep(min(a.interval, max(0, deadline - time.monotonic())))


def cmd_discuss(a: argparse.Namespace) -> None:
    if a.watch_joined:
        discussion_watch(a)
        return
    if a.list:
        for path in sorted((root() / STATE_DIR / "conversations").glob("*/discussion.mdx")):
            view = discussion_load(path.parent)
            print(f"{view['id']}  {view['status']}  {view['topic']}")
        return
    if not a.discussion:
        die("give a discussion ID, or use --list")
    d = discussion_directory(a.discussion)
    group = discussion_protocol(d) == GROUP_PROTOCOL
    if a.peer and not a.start:
        die("--peer names a group roster at --start only")
    if a.dissent is not None and not group:
        die("--dissent answers a group proposal; in a two-party discussion, --send your disagreement")
    if a.start:
        (group_start if a.peer else discussion_start)(d, a)
    elif a.join:
        with run_lock(d):
            discussion_load(d)
            discussion_join(d, a)
    elif a.prompt:
        print((group_prompt if group else discussion_prompt)(d, a.as_participant), end="")
    elif a.pickup:
        discussion_pickup(d, a)
    elif a.watch:
        discussion_watch(a)
    elif (a.send or a.propose or a.accept is not None or a.dissent is not None
          or a.pause or a.continue_discussion or a.close):
        (group_append if group else discussion_append)(d, a)
    else:
        view = discussion_load(d)
        if a.json:
            print(json.dumps(view, indent=2))
        else:
            print(view["briefing"])
            discussion_print_messages(view, view["messages"])
