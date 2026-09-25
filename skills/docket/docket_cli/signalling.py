"""Arming roles, the event ledger, and the watch loop that wakes a session."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import sys
import time
from pathlib import Path

from .common import DELIVERY_LEASE_SECONDS, STATE_DIR, die, now_s
from .paths import root, run_dir
from .publication import fault, perturb, publish, publish_json
from .policy import need_run, require_preset_role
from .baselines import evidence_mode
from .state import armed
from .sessions import calling_harness, codex_home
from .events import events
from .delivery import (
    announce_active, announce_delivered, announce_dir, announce_path, delivery_lock,
    delivery_log, event_actionable, inbox_ack, inbox_retry, mark_announce_delivered,
    paused_flag, read_pending, sweep_role,
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
    return set(led.read_text().split())


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


def codex_wait_hint(role: str) -> str:
    cap = codex_poll_cap()
    if cap >= CODEX_HOUR_POLL_MS:
        return (f"docket watch: waiting for {role} events. This Codex allows one-hour polls "
                f"(background_terminal_max_timeout = {cap}): pass yield_time_ms: "
                f"{CODEX_HOUR_POLL_MS} to every write_stdin and wait on this command. Each "
                "return is a full model turn, so never poll in shorter windows.")
    return (f"docket watch: waiting for {role} events. This Codex caps a poll at {cap} ms, so "
            f"pass yield_time_ms: {cap} to every write_stdin and wait on this command. Add "
            f"background_terminal_max_timeout = {CODEX_HOUR_POLL_MS} at the top of "
            f"{codex_home() / 'config.toml'} for one turn per idle hour.")


# A wake is exit 2 for a person or agent running `docket watch`. Inside the wake
# hook it is 3, which no launcher or argument error produces, and `wake.sh` turns
# only 3 into the harness's 2.
WAKE_EXIT = 2
HOOK_WAKE_EXIT = 3


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
    if a.run:
        require_preset_role(need_run(a.run), a.role)
    if os.environ.get("DOCKET_WATCH_HOOK") != "1" and calling_harness() == "codex":
        # Codex shows a command's first second of output to the model, then
        # re-enters the model on every poll. Say how long one poll may be on
        # this machine, right where the model decides it; a hook needs no line.
        print(codex_wait_hint(a.role), file=sys.stderr, flush=True)

    def watched_pairs() -> list[tuple[str, str]]:
        if a.armed:
            pairs = [pair for pair in armed() if pair[1] == a.role]
        else:
            if not a.run:
                die("give a run id, or --armed to watch that role's configured runs")
            pairs = [(a.run, a.role)]
        return [(run, role) for run, role in pairs if run_dir(run).is_dir()]

    deadline = time.monotonic() + a.timeout
    while True:
        fresh: list[tuple[str, str, Path, str, str]] = []
        pairs = watched_pairs()
        if not pairs:
            raise SystemExit(0)
        live_pairs = [(run, role) for run, role in pairs
                      if not paused_flag(run_dir(run), role).is_file()]
        for run, role in live_pairs:
            d = run_dir(run)
            led = ledger_for(d, role)
            led.parent.mkdir(parents=True, exist_ok=True)
            claimed: list[tuple[str, str]] = []
            with delivery_lock(d, role):
                sweep_role(d, role)
                led.parent.mkdir(parents=True, exist_ok=True)
                with led.open("a+") as fh:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    try:
                        fh.seek(0)
                        seen = set(fh.read().split())
                        derived = events(d, role)
                        for k, m in derived:
                            if k not in seen:
                                claimed.append((k, m))
                                continue
                            pending = read_pending(d, role, k)
                            if not pending:
                                continue
                            ok, _ = event_actionable(d, pending)
                            if not ok:
                                continue
                            # Only an announcement that never reached the
                            # harness is owed again. A delivered wake stays
                            # delivered while its identity holds: the event
                            # usually persists because another role is still
                            # working on it, and re-waking the supervisor every
                            # lease period is exactly the polling this exists
                            # to prevent. A moved identity retires the record
                            # in sweep_role, so a changed event still wakes.
                            if not announce_active(d, role, k) \
                                    and not announce_delivered(d, role, k):
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
            for k, m in claimed:
                if (run, role, k) not in [(r, ro, kk) for r, ro, _led, kk, _m in fresh]:
                    fresh.append((run, role, led, k, m))
        if fresh:
            fault("watch:announce")
            lines = [f"  - [{role}] {run}: {m}" for run, role, _, _, m in fresh]
            print(
                f"docket: {len(fresh)} item(s) need you:\n" + "\n".join(lines)
                + "\n\nRun `docket status <run>` for the run named above, then review it."
                + f"\nThis watcher is scoped only to the {a.role} role."
                + "\nProcess normal review batches silently: do not send lifecycle or per-task"
                + " progress to the user. Speak only for a blocker requiring user authority,"
                + " final completion, or an explicit status request.",
                file=sys.stderr,
            )
            sys.stderr.flush()
            for run, role, _led, k, _m in fresh:
                mark_announce_delivered(run_dir(run), role, k)
            raise SystemExit(HOOK_WAKE_EXIT if os.environ.get("DOCKET_WATCH_HOOK") == "1"
                             else WAKE_EXIT)
        if time.monotonic() >= deadline:
            raise SystemExit(0)
        time.sleep(min(a.interval, max(1, deadline - time.monotonic())))
