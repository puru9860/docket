"""Arming roles, the event ledger, and the watch loop that wakes a session."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

from .common import DELIVERY_LEASE_SECONDS, STATE_DIR, die, now_s, stamp
from .paths import root, run_dir
from .publication import fault, perturb, publish, publish_json
from .policy import need_run, require_preset_role
from .baselines import evidence_mode
from .state import armed
from .sessions import (
    calling_harness, codex_home, codex_stop_hook_installed, harness_session,
    session_noted_roles,
)
from .discussions import discussion_announce, discussion_joined, discussion_watch
from .hook_config import codex_hook_configuration
from .events import ensure_worker_exit_checkpoint, events
from .delivery import (
    announce_active, announce_delivered, announce_dir, announce_path, delivery_lock,
    delivery_log, event_actionable, inbox_ack, inbox_retry, mark_announce_delivered,
    paused_flag, read_pending, sweep_role,
    claim_event, event_is_owned, native_bound_runs, native_session_registration, read_announce,
)


def ledger_for(d: Path, role: str) -> Path:
    """Per-role ledger, so two supervisors never consume each other's events."""
    led = d / f".woke-{role}"
    legacy = d / ".woke"
    if not led.exists() and legacy.is_file():
        publish(led, legacy.read_text())  # migrate the old shared ledger once
    return led


def delivered_keys(d: Path, role: str) -> set[str]:
    """Keys already recorded for a role. Never creates, migrates, or appends a ledger."""
    led = d / f".woke-{role}"
    if not led.is_file():
        led = d / ".woke"
    if not led.is_file():
        return set()
    seen = set(led.read_text().split())
    return {key for key in seen if (notice := read_announce(d, role, key)).get("transport")
            != "native-hook" or bool(notice.get("received_at"))}


def cmd_instruct(a: argparse.Namespace) -> None:
    """Record or resolve one direct instruction for a supervising role."""
    d = need_run(a.run)
    require_preset_role(d, a.role)
    if a.role == "implementor":
        die("implementors receive dispatch prompts; instructions target supervising roles")
    directory = d / ".instructions"
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".lock"
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        if a.resolve:
            if not re.fullmatch(re.escape(a.role) + r"-\d+", a.resolve):
                die(f"instruction {a.resolve!r} does not belong to {a.role}")
            path = directory / f"{a.resolve}.json"
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                die(f"no instruction {a.resolve!r} for {a.role}")
            if record.get("state") == "resolved":
                print(f"instruction {a.resolve} already resolved")
                return
            record["state"] = "resolved"
            record["resolved_at"] = stamp()
            publish_json(path, record)
            print(f"resolved instruction {a.resolve}")
            return
        message = (a.message or "").strip()
        if not message:
            die("--message TEXT is required when sending an instruction")
        for number in range(1, 10000):
            iid = f"{a.role}-{number:02d}"
            path = directory / f"{iid}.json"
            if not path.exists():
                publish_json(path, {"id": iid, "role": a.role, "by": a.by,
                                    "message": message, "state": "open", "at": stamp()})
                print(f"recorded instruction {iid} for {a.role}; its watcher can wake now")
                return
        die(f"could not allocate an instruction for {a.role}")


def cmd_events(a: argparse.Namespace) -> None:
    """Inspect a role's derived events, or record receipt/retry for one.

    Inspection is not delivery. Peek reads documents and the existing ledger
    and writes nothing, so repeating it cannot consume a wake or migrate
    delivery state. `docket watch` remains the only legacy announcer, while
    `--ack` and `--retry` write transport records, never lifecycle state.
    """
    d = need_run(a.run)
    require_preset_role(d, a.role)
    if a.ack:
        if not a.session:
            die("--ack requires --session SESSION")
        inbox_ack(a, d)
        return
    if a.retry:
        inbox_retry(a, d)
        return
    print(f"run {a.run}   role {a.role}   evidence: {evidence_mode(d)}")
    derived = events(d, a.role)
    seen = delivered_keys(d, a.role)
    pending = [(key, message) for key, message in derived if key not in seen]
    if not derived:
        print("\nno derived events for this role")
    else:
        print(f"\n{len(pending)} pending of {len(derived)} derived event(s)\n")
        for key, message in derived:
            print(f"  [{'pending' if key not in seen else 'delivered':<9}] {key}")
            print(f"              {message}")
    role_ledger = d / f".woke-{a.role}"
    legacy = d / ".woke"
    if role_ledger.is_file():
        source = role_ledger.name
    elif legacy.is_file():
        source = f"{legacy.name} (legacy, not yet migrated)"
    else:
        source = "none yet"
    print(f"\nledger read: {source}")
    print("Nothing was claimed: no ledger was created, migrated, or appended.")


@contextlib.contextmanager
def watch_conf_lock():
    """Serialize every read-modify-write of watch.conf.

    Supervisors arm as they start, often at the same moment, and an unlocked rewrite
    let one arm republish the list without another's pair: that role was then never
    woken, silently.
    """
    path = root() / STATE_DIR / "watch.conf.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def cmd_arm(a: argparse.Namespace) -> None:
    d = need_run(a.run)
    require_preset_role(d, a.role)
    session = getattr(a, "session", "")
    if session or getattr(a, "harness_session_ref", None):
        native_session_registration(a.run, a.role, create=True, session=session)
    conf = root() / STATE_DIR / "watch.conf"
    with watch_conf_lock():
        pairs = armed()
        if (a.run, a.role) in pairs:
            print(f"already armed: {a.run} {a.role}")
        else:
            pairs.append((a.run, a.role))
            perturb("arm:before-publish")
            publish(conf, "".join(f"{r} {ro}\n" for r, ro in pairs))
            print(f"armed: {a.run} {a.role}")
    print("\narmed now:")
    for r, ro in armed():
        print(f"  {r} {ro}")


def cmd_disarm(a: argparse.Namespace) -> None:
    conf = root() / STATE_DIR / "watch.conf"
    with watch_conf_lock():
        keep = [
            (r, ro) for r, ro in armed()
            if not ((a.run in (None, r)) and (a.role in (None, ro)))
        ]
        if not keep:
            conf.unlink(missing_ok=True)
            print("disarmed everything")
            return
        publish(conf, "".join(f"{r} {ro}\n" for r, ro in keep))
    print("armed now:")
    for r, ro in keep:
        print(f"  {r} {ro}")


CODEX_HOUR_POLL_MS = 3_600_000


def codex_poll_cap() -> int:
    """The longest empty poll this Codex allows, from its `config.toml`."""
    try:
        import tomllib
        with (codex_home() / "config.toml").open("rb") as fh:
            value = tomllib.load(fh).get("background_terminal_max_timeout")
    except (ImportError, OSError, ValueError):
        value = None
    return int(value) if isinstance(value, int) and value > 0 else 300_000


def codex_hook_hint(role: str) -> str:
    """What the installed Stop hook means for this session, or '' without one."""
    if not codex_stop_hook_installed():
        return ""
    hooks = ", ".join(map(str, codex_hook_configuration(codex_home())["locations"]))
    declared = os.environ.get("DOCKET_ROLE", "").strip()
    if declared == role:
        return (f"The docket Stop hook in {hooks} serves this session (DOCKET_ROLE={role}). "
                "Local configuration is enabled; effective overrides and trust are unknown. "
                "If /hooks confirms it is trusted and a real wake works, stop this watcher and end your turn instead: "
                "the hook waits at no model cost and hands you the wake as your next prompt.")
    return (f"The docket Stop hook in {hooks} is inert here: this session's environment has "
            f"DOCKET_ROLE={declared or 'unset'}, not {role}, and prefixing one command does not "
            f"change that. Wait on this watcher; start the next {role} session in a pane "
            f"created with --env DOCKET_ROLE={role} so the hook can serve it.")


def codex_wait_hint(role: str) -> str:
    cap = codex_poll_cap()
    hook = codex_hook_hint(role)
    hook = f" {hook}" if hook else ""
    if cap >= CODEX_HOUR_POLL_MS:
        return (f"docket watch: waiting for {role} events. This Codex allows one-hour polls "
                f"(background_terminal_max_timeout = {cap}): pass yield_time_ms: "
                f"{CODEX_HOUR_POLL_MS} to every write_stdin and wait on this command. Each "
                f"return is a full model turn, so never poll in shorter windows.{hook}")
    return (f"docket watch: waiting for {role} events. This Codex caps a poll at {cap} ms, so "
            f"pass yield_time_ms: {cap} to every write_stdin and wait on this command. Add "
            f"background_terminal_max_timeout = {CODEX_HOUR_POLL_MS} at the top of "
            f"{codex_home() / 'config.toml'} for one turn per idle hour.{hook}")


# The shortest watch a harness session may run. A shell call capped below this
# cannot host a useful foreground watch; a shorter call only restarts the wait.
MIN_HARNESS_WATCH_SECONDS = 590


def short_watch_refusal(harness: str, role: str, timeout: int) -> str:
    how = {
        "codex": "Drop --timeout (the default is 8 hours) and wait on this one command.",
        "claude": "Drop --timeout (the default is 8 hours) and run it as one background command.",
        "opencode": f"Use --timeout {MIN_HARNESS_WATCH_SECONDS} only in a terminal or "
                    "adapter that allows at least 600 seconds; a shell cap below 590 seconds "
                    "cannot run this watch.",
    }[harness]
    return (f"watch --timeout {timeout} inside {harness} is a polling loop: every "
            "return re-enters the model with its whole context to start the same wait again. "
            f"The shortest watch a harness session may run is {MIN_HARNESS_WATCH_SECONDS} s. "
            f"{how} See `docket help signalling`.")


# A wake is exit 2 for a person or agent running `docket watch`. Inside the wake
# hook it is 3, which no launcher or argument error produces, and `wake.sh` turns
# only 3 into the harness's 2.
WAKE_EXIT = 2
HOOK_WAKE_EXIT = 3


def cmd_pickup(a: argparse.Namespace) -> None:
    """Receive current native announcements in one registered recipient operation."""
    d = need_run(a.run)
    require_preset_role(d, a.role)
    declared = os.environ.get("DOCKET_ROLE", "").strip()
    if declared and declared != a.role:
        die(f"pickup --role {a.role} conflicts with DOCKET_ROLE={declared}")
    reg = native_session_registration(a.run, a.role, session=a.session)
    if not reg:
        die(f"no recipient binding; run `docket arm {a.run} --role {a.role}` "
            "inside the role's session, or pass its registered --session")
    with delivery_lock(d, a.role):
        sweep_role(d, a.role)
        announcements = [read_announce(d, a.role, key) for key, _ in events(d, a.role)]
    received = []
    for notice in announcements:
        if notice.get("transport") != "native-hook" or notice.get("session") != reg["session"]:
            continue
        if notice.get("session_generation") != reg["generation"]:
            die("announcement belongs to a previous session generation; wait for its fresh wake")
        if notice.get("received_at"):
            continue
        key = notice["key"]
        _lease, pending, _status = claim_event(d, a.run, a.role, reg["session"], key,
                                             expected=notice)
        inbox_ack(argparse.Namespace(run=a.run, role=a.role, session=reg["session"], ack=key), d)
        received.append((key, pending.get("message", "")))
    print(f"\nrun {a.run} role {a.role}: picked up {len(received)} event(s)")
    for key, message in received:
        print(f"  {key}: {message}")
    print(f"Run `docket status {a.run} --role {a.role}` for the current review state.")


def refuse_watching_from_a_session_noted_for_another_role(
        d: Path, run: str, role: str) -> None:
    """Refuse a watch from a harness session noted as another role in this run.

    With DOCKET_ROLE unset, the calling harness session is still identified by
    its own environment, and the run ledger says which role it plays. A noted
    planner watching the orchestrator's events would consume a wake meant for
    another session, so it is refused before it can receive one. A terminal
    watcher with no harness session, or a session the run never noted, passes.
    """
    ref = harness_session([run, "watch"])
    if not ref:
        return
    noted = session_noted_roles(d, ref)
    if noted and role not in noted:
        die(f"watch --role {role} refused: this session "
            f"({ref.get('harness')} {ref.get('session_id')}) is noted as "
            f"{', '.join(noted)} in {run}; run `docket watch {run} "
            f"--role {noted[0]}` instead, or watch from a terminal "
            "with no registered harness session")


def cmd_watch(a: argparse.Namespace) -> None:
    """Block with no token cost until something needs a watched role, then exit 2.

    Armed from a Claude Code Stop hook with asyncRewake: true. Must run in the
    foreground of the hook's process tree - never with shell '&'. Under
    `DOCKET_WATCH_HOOK=1` a wake exits `HOOK_WAKE_EXIT` instead, because uv and
    argparse exit 2 on their own errors; `hooks/wake.sh` maps only that code to
    the harness's 2, so a broken launcher can never read as a wake.

    Each role gets its own ledger, so an orchestrator and a planner watching the
    same run never consume each other's events.

    Announcement is crash-recoverable: every announced key first gets a durable
    pending record (via reconciliation) and a bounded announcement lease under
    `.delivery/<role>/announce/`. The ledger alone is not delivery truth. A
    watcher that crashes after writing the ledger but before the harness
    accepts the wake leaves the durable pending event behind; the next watcher
    re-announces the same actionable event once the announcement lease expires,
    and explicit inbox pickup stays claimable throughout. Concurrent watchers
    converge on one wake while the lease is active and never create lifecycle
    transitions here.
    """
    if not a.role:
        die("--role is required so one supervisor cannot consume another role's events")
    declared = os.environ.get("DOCKET_ROLE", "").strip()
    native = os.environ.get("DOCKET_WATCH_HOOK") == "1"
    joined_discussions = discussion_joined() if native else []
    selected_runs = None
    if native:
        if a.armed and not a.run and not any(role == a.role for _, role in armed()):
            if joined_discussions:
                discussion_watch(argparse.Namespace(watch_joined=True, timeout=a.timeout, interval=a.interval))
            raise SystemExit(0)
        selector = os.environ.get("DOCKET_RUN", "").strip()
        if a.run and selector and a.run not in selector.split(","):
            die("watch run conflicts with DOCKET_RUN")
        if a.run and getattr(a, "harness_session_ref", None):
            native_session_registration(a.run, a.role, create=True)
        bound = native_bound_runs(a.role)
        if harness_session([]) and not bound:
            if joined_discussions:
                discussion_watch(argparse.Namespace(watch_joined=True, timeout=a.timeout, interval=a.interval))
                return
            die(f"native hook has no current recipient binding; run `docket arm RUN "
                f"--role {a.role}` inside this session before waiting")
        selected_runs = list(dict.fromkeys(selector.split(","))) if selector else bound or None
        if selected_runs and any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run) for run in selected_runs):
            die("DOCKET_RUN must name a run, or an explicit comma-separated list of runs")
        if bound and selected_runs and not set(selected_runs).issubset(bound):
            die("DOCKET_RUN conflicts with this recipient's registered runs; arm it in this session first")
    if declared and declared != a.role:
        die(f"watch --role {a.role} conflicts with this session's DOCKET_ROLE={declared}: "
            f"run `docket watch {a.run or '<run>'} --role {declared}` instead, or unset "
            "DOCKET_ROLE when waiting outside the role session")
    if a.run:
        if native and selected_runs is not None and a.run not in selected_runs:
            die("watch run conflicts with this native recipient's selected runs")
        d = need_run(a.run)
        require_preset_role(d, a.role)
        if not declared:
            refuse_watching_from_a_session_noted_for_another_role(d, a.run, a.role)
    elif a.armed:
        for run, role in armed():
            if role != a.role or not run_dir(run).is_dir():
                continue
            refuse_watching_from_a_session_noted_for_another_role(
                run_dir(run), run, a.role)
    harness = calling_harness() if os.environ.get("DOCKET_WATCH_HOOK") != "1" else ""
    if harness and a.timeout < MIN_HARNESS_WATCH_SECONDS:
        die(short_watch_refusal(harness, a.role, a.timeout)
            + (f"\n{codex_wait_hint(a.role)}" if harness == "codex" else ""))
    if harness == "codex":
        # Codex shows a command's first second of output to the model, then
        # re-enters the model on every poll. Say how long one poll may be on
        # this machine, right where the model decides it; a hook needs no line.
        print(codex_wait_hint(a.role), file=sys.stderr, flush=True)

    def watched_pairs() -> list[tuple[str, str]]:
        if a.armed:
            pairs = [pair for pair in armed() if pair[1] == a.role
                     and (not a.run or pair[0] == a.run)
                     and (selected_runs is None or pair[0] in selected_runs)]
        else:
            if not a.run:
                die("give a run id, or --armed to watch that role's configured runs")
            pairs = [(a.run, a.role)]
        if native and selected_runs is None and len(pairs) > 1:
            die("native hook has multiple armed runs for this role; set DOCKET_RUN "
                "or arm each intended run inside its recipient session")
        return [(run, role) for run, role in pairs if run_dir(run).is_dir()]

    deadline = time.monotonic() + a.timeout
    while True:
        fresh: list[tuple[str, str, Path, str, str]] = []
        discussion_notices, discussion_active = discussion_announce(discussion_joined()) if native else ([], False)
        if discussion_notices:
            print("Docket peer conversation:\n" + "\n".join(discussion_notices), file=sys.stderr, flush=True)
            raise SystemExit(HOOK_WAKE_EXIT)
        pairs = watched_pairs()
        if not pairs and not discussion_active:
            raise SystemExit(0)
        live_pairs = [(run, role) for run, role in pairs
                      if not paused_flag(run_dir(run), role).is_file()]
        for run, role in live_pairs:
            d = run_dir(run)
            recipient = native_session_registration(run, role, create=True) if native else None
            led = ledger_for(d, role)
            led.parent.mkdir(parents=True, exist_ok=True)
            claimed: list[tuple[str, str]] = []
            exited: list[str] = []
            with delivery_lock(d, role):
                sweep_role(d, role)
                led.parent.mkdir(parents=True, exist_ok=True)
                with led.open("a+") as fh:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    try:
                        fh.seek(0)
                        seen = set(fh.read().split())
                        derived = events(d, role)
                        exited = [key.rsplit(":", 2)[0] for key, _ in derived
                                  if key.endswith(":worker-exited")]
                        for k, m in derived:
                            if native and event_is_owned(d, role, k):
                                continue
                            if k not in seen:
                                claimed.append((k, m))
                                continue
                            pending = read_pending(d, role, k)
                            if not pending:
                                continue
                            ok, _ = event_actionable(d, pending)
                            if not ok:
                                continue
                            # Native output is only an announcement. Pickup
                            # records receipt for this recipient generation;
                            # an acknowledged wake stays quiet while the event
                            # usually persists because another role is still
                            # working on it, and re-waking the supervisor every
                            # lease period after receipt is the polling this exists
                            # to prevent. A moved identity retires the record
                            # in sweep_role, so a changed event still wakes.
                            if not announce_active(d, role, k, recipient) \
                                    and not announce_delivered(d, role, k, recipient):
                                claimed.append((k, m))
                        for k, _m in claimed:
                            pending = read_pending(d, role, k)
                            if pending:
                                try:
                                    attempt = int(pending.get("attempts", 0) or 0)
                                except (TypeError, ValueError):
                                    attempt = 0
                            else:
                                attempt = 0
                            now = now_s()
                            announce_dir(d, role).mkdir(parents=True, exist_ok=True)
                            publish_json(announce_path(d, role, k), {
                                "key": k,
                                "role": role,
                                "run": run,
                                "announced_at": now,
                                "lease_until": now + DELIVERY_LEASE_SECONDS,
                                "lease_seconds": DELIVERY_LEASE_SECONDS,
                                "attempt": attempt + 1,
                                "transport": "native-hook" if native else "watch",
                                **({field: pending.get(field) for field in
                                    ("revision", "round", "generation", "workflow")} if pending else {}),
                                **({"session": recipient["session"],
                                    "session_generation": recipient["generation"]} if recipient else {}),
                            })
                            delivery_log(d, role, {"kind": "announced", "key": k})
                        if claimed:
                            fh.seek(0, os.SEEK_END)
                            for k, _ in claimed:
                                if k not in seen:
                                    fh.write(k + "\n")
                            fh.flush()
                    finally:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            for owner in exited:
                try:
                    ensure_worker_exit_checkpoint(d, owner)
                except (OSError, ValueError):
                    pass
            for k, m in claimed:
                if (run, role, k) not in [(r, ro, kk) for r, ro, _led, kk, _m in fresh]:
                    fresh.append((run, role, led, k, m))
        if fresh:
            fault("watch:announce")
            lines = [f"  - [{role}] {run}: {m}" for run, role, _, _, m in fresh]
            pickup = ""
            if native:
                pickup = "\nBefore acting, record receipt in this session:\n" + "\n".join(
                    f"  docket pickup {run} --role {a.role}"
                    for run in dict.fromkeys(item[0] for item in fresh))
            print(
                f"docket: {len(fresh)} item(s) need you:\n" + "\n".join(lines)
                + "\n\nRun `docket status <run>` for the run named above, then review it."
                + f"\nThis watcher is scoped only to the {a.role} role."
                + pickup
                + "\nProcess normal review batches silently: do not send lifecycle or per-task"
                + " progress to the user. Speak only for a blocker requiring user authority,"
                + " final completion, or an explicit status request.",
                file=sys.stderr,
            )
            sys.stderr.flush()
            for run, role, _led, k, _m in fresh:
                if not native:
                    mark_announce_delivered(run_dir(run), role, k)
            raise SystemExit(HOOK_WAKE_EXIT if os.environ.get("DOCKET_WATCH_HOOK") == "1"
                             else WAKE_EXIT)
        if time.monotonic() >= deadline:
            raise SystemExit(0)
        time.sleep(min(a.interval, max(1, deadline - time.monotonic())))
