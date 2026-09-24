"""Running verify commands and matching verification records to a round."""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .common import COVERAGE_AVAILABLE, die, stamp
from .frontmatter import parse
from .paths import owners, root
from .publication import publish_json
from .policy import plan_flag
from .baselines import evidence_digest, output_fingerprint
from .bundles import bundle_problems, bundles_for, digest_of, load_bundle
from .state import verification_paths
from .freeze import current_bundle
from .aggregates import aggregate_bundle_problems


def parse_framework_counts(text: str) -> dict[str, object]:
    """Test counts from a supported parser, or unknown when none applies.

    Counts are recognized only with a supported parser; anything else reports
    unknown rather than a guessed number.
    """
    # pytest ends with one summary such as `3 failed, 12 passed, 1 skipped in 0.5s`,
    # whose counts come in any order; the last such line is the run's own verdict.
    kinds = r"passed|failed|skipped|errors?|xfailed|xpassed|warnings?|deselected"
    summaries = [line for line in text.splitlines()
                 if re.search(rf"\d+ (?:{kinds})\b.*\bin [\d.]+s", line)]
    if summaries:
        counts: dict[str, object] = {"parser": "pytest", "passed": 0, "failed": 0, "skipped": 0}
        for number, kind in re.findall(rf"(\d+) ({kinds})\b", summaries[-1]):
            key = {"error": "errors", "warning": "warnings"}.get(kind, kind)
            if key in ("passed", "failed", "skipped", "errors"):
                counts[key] = int(number)
        return counts
    oks = len(re.findall(r"(?m)^ok\b", text))
    not_oks = len(re.findall(r"(?m)^not ok\b", text))
    skips = len(re.findall(r"(?mi)^ok\b.*#\s*skip", text))
    if oks or not_oks:
        return {"parser": "tap", "passed": oks - skips,
                "failed": not_oks, "skipped": skips}
    return {"parser": "unknown", "passed": None, "failed": None, "skipped": None}


def task_env(d: Path, owner: str) -> list[str]:
    """Declared environment inputs for a task, from flat frontmatter `env:`."""
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return []
    raw = parse(task.read_text())[0].get("env", "")
    return sorted({item.strip() for item in raw.split(",") if item.strip()})


def split_env_item(item: str) -> tuple[str, str | None]:
    """One declared input into key and declared value, or None when bare."""
    text = (item or "").strip()
    if not text:
        return "", None
    if "=" in text:
        key, value = text.split("=", 1)
        return key.strip(), value.strip()
    return text, None


def verification_run_env(declared: list[str] | None) -> tuple[dict[str, str], list[str]]:
    """Environment a verification runs under, plus the effective values it ran with.

    Declared `KEY=VALUE` inputs override ambient state so the declared value is
    what the command sees; a bare `KEY` leaves ambient alone. Only declared keys
    are ever captured, so unrelated ambient state is never recorded.
    """
    run_env = dict(os.environ)
    # A verify command must not write into the checkout it is measured
    # against; Python bytecode caches are the commonest such write. A task
    # that declares the variable keeps its own value.
    run_env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for item in declared or []:
        key, value = split_env_item(item)
        if key and value is not None:
            run_env[key] = value
    effective: list[str] = []
    for item in declared or []:
        key, _ = split_env_item(item)
        if not key:
            continue
        effective.append(f"{key}={run_env.get(key, '(unset)')}")
    return run_env, sorted(effective)


def frozen_verification_status(frozen: dict[str, object] | None) -> str:
    """The captured verification status of a frozen bundle, or empty when none."""
    if not isinstance(frozen, dict):
        return ""
    verification = frozen.get("verification")
    if not isinstance(verification, dict):
        return ""
    return str(verification.get("status", ""))


def canonical_declared_env(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(values) != len(value):
        return None
    return sorted(set(values))


def source_identity_key(rows: object) -> tuple[tuple[str, str, str, str, str], ...] | None:
    if not isinstance(rows, list) or not rows:
        return None
    records: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        values = tuple(str(row.get(field, "")) for field in ("alias", "path", "tree", "head", "branch"))
        if not values[0] or not values[2]:
            return None
        records.append(values)
    if len(set(records)) != len(records):
        return None
    return tuple(sorted(records))


def latest_reusable_verification(
    d: Path, command: str, declared: list[str], effective: list[str],
    source: list[dict[str, str]],
) -> dict[str, object] | None:
    source_key = source_identity_key(source)
    declared_key = canonical_declared_env(declared)
    effective_key = canonical_declared_env(effective)
    if source_key is None or declared_key is None or effective_key is None:
        return None
    candidates: list[tuple[str, int, str, dict[str, object], dict[str, object]]] = []
    sequence = 0
    for owner in owners(d):
        for entry in bundles_for(d, owner):
            sequence += 1
            try:
                manifest = load_bundle(d, owner, entry)
            except (OSError, TypeError, ValueError):
                continue
            if not isinstance(manifest, dict):
                continue
            verification = manifest.get("verification")
            source_block = manifest.get("source")
            if not isinstance(verification, dict) or not isinstance(source_block, dict):
                continue
            if verification.get("status") != "passed":
                continue
            if verification.get("command") != command:
                continue
            if canonical_declared_env(verification.get("declared_env")) != declared_key:
                continue
            if canonical_declared_env(verification.get("effective_env")) != effective_key:
                continue
            if source_block.get("coverage") != COVERAGE_AVAILABLE:
                continue
            if source_identity_key(source_block.get("roots")) != source_key:
                continue
            if not isinstance(verification.get("output_fingerprint"), str):
                continue
            cwd = verification.get("cwd")
            if cwd and cwd != str(root()):
                continue
            try:
                if str(manifest.get("kind", "")) == "aggregate":
                    problems = aggregate_bundle_problems(d, entry)
                else:
                    problems = bundle_problems(d, owner, entry)
            except (OSError, TypeError, ValueError):
                continue
            if problems:
                continue
            candidates.append((
                str(manifest.get("frozen_at", "")), sequence, owner, entry, verification,
            ))
    if not candidates:
        return None
    _, _, owner, entry, verification = max(candidates, key=lambda item: (item[0], item[1]))
    return {
        "owner": owner,
        "entry": entry,
        "verification": verification,
    }


def skipped_approval_problem(owner: str, rnd: int, run: str) -> str:
    """Why an approval cannot stand over a skipped verification, and what to do instead."""
    return (
        f"{owner} round {rnd} cannot be approved: its frozen verification was skipped, "
        "so there is no passing result to approve. Recording a skip stays possible, "
        "but concluding a pass from one is not. Accept the work without claiming it "
        f"passed with `docket decide {run} {owner} --waive --reason TEXT`, or open a "
        f"fresh round with `docket decide {run} {owner} --changes` and resubmit."
    )


def run_verification(command: str, timeout: int, requested_model: str = "",
                     observed_model: str = "",
                     declared_env: list[str] | None = None,
                     slot: Path | None = None) -> dict[str, object]:
    """Execute the registered verification and capture what it actually did."""
    started = time.time()
    if observed_model:
        model_source = "observed"
    elif requested_model:
        model_source = "requested"
    else:
        model_source = "unknown"
    record: dict[str, object] = {
        "command": command,
        "cwd": str(root()),
        "timeout_seconds": timeout,
        "started_at": stamp(started),
        "model_requested": requested_model,
        "model_observed": observed_model or "unknown",
        "model_source": model_source,
        "declared_env": list(declared_env or []),
    }
    run_env, effective = verification_run_env(list(declared_env or []))
    record["effective_env"] = effective
    code, out_bytes, err_bytes = execute_verify(command, timeout, run_env, slot)
    stdout, stderr = out_bytes.decode("utf-8", "replace"), err_bytes.decode("utf-8", "replace")
    record["status"] = "timeout" if code is None else ("passed" if code == 0 else "failed")
    record["returncode"] = code
    ended = time.time()
    record["ended_at"] = stamp(ended)
    record["duration_seconds"] = round(ended - started, 3)
    record["output_fingerprint"] = output_fingerprint(stdout, stderr)
    record["framework"] = parse_framework_counts(stdout + "\n" + stderr)
    record["output_tail"] = (stdout + stderr).strip().splitlines()[-15:]
    record["stdout_text"] = stdout
    record["stderr_text"] = stderr
    # The frozen artifacts keep the exact bytes; the text fields above are for reading.
    record["stdout_bytes"] = out_bytes
    record["stderr_bytes"] = err_bytes
    return record


def end_process_group(pgid: int, grace: float = 2.0) -> None:
    """Terminate every process a verify command started, then kill what remains."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def verify_slot_path(d: Path, owner: str) -> Path:
    return d / ".locks" / f"{owner}.verify.lock"


def verify_slot_refusal(slot: Path, command: str) -> str:
    try:
        info = json.loads(slot.with_suffix(".json").read_text())
    except (OSError, ValueError):
        info = {}
    return (
        f"a verify command for this owner is already running "
        f"(pid {info.get('pid', '?')}, since {info.get('started_at', '?')}): "
        f"{info.get('command', command)}. Wait for it to finish and read its result; "
        "do not start another, since each retry adds a second copy of the same suite. "
        "Run long docket commands with your longest shell timeout"
    )


def verify_slot_busy(slot: Path) -> bool:
    if not slot.is_file():
        return False
    try:
        with slot.open("r") as holder:
            try:
                fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    except OSError:
        return False
    return False


def claim_verify_slot(slot: Path, command: str):
    """Hold one owner's verify slot, or refuse while a verify command still holds it."""
    slot.parent.mkdir(parents=True, exist_ok=True)
    holder = slot.open("a+")
    try:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder.close()
        die(verify_slot_refusal(slot, command))
    try:
        publish_json(slot.with_suffix(".json"), {
            "pid": os.getpid(), "started_at": stamp(), "command": command,
        })
    except BaseException:
        holder.close()
        raise
    return holder


def execute_verify(command: str, timeout: int, env: dict[str, str],
                   slot: Path | None = None, holder: object | None = None) -> tuple[int | None, bytes, bytes]:
    """Run a verify command in its own process group: exit code (None on timeout) and output.

    Output goes to temporary files rather than pipes, so a helper the command left in
    the background cannot hold a pipe open and turn a finished pass into a timeout.
    The whole group ends when the command does or when its timeout passes, so no test
    process keeps running, or writing into the checkout, after the result is taken.
    """
    owns_holder = holder is None and slot is not None
    if owns_holder:
        holder = claim_verify_slot(slot, command)
    handled = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    previous = {sig: signal.getsignal(sig) for sig in handled}
    proc: subprocess.Popen | None = None
    pending: list[int] = []

    def stop(signum: int, _frame: object) -> None:
        if proc is None:
            pending.append(signum)
            return
        end_process_group(proc.pid)
        raise SystemExit(128 + signum)

    try:
        for sig in handled:
            signal.signal(sig, stop)
        with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
            try:
                proc = subprocess.Popen(
                    command, shell=True, cwd=root(), stdout=out_f, stderr=err_f,
                    env=env, start_new_session=True,
                    pass_fds=(holder.fileno(),) if holder else (),
                )
            except OSError as exc:
                if pending:
                    raise SystemExit(128 + pending[0])
                return 127, b"", f"cannot start the verify command: {exc}\n".encode()
            if pending:
                stop(pending[0], None)
            try:
                code: int | None = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                code = None
            end_process_group(proc.pid)
            proc.wait()
            out_f.seek(0)
            err_f.seek(0)
            return code, out_f.read(), err_f.read()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if owns_holder and holder is not None:
            holder.close()


def verifier_exempt_set(d: Path) -> set[str]:
    """Members covered by verifier_exempt, which counts as resolved verification."""
    raw = plan_flag(d, "verifier_exempt", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def current_task_revision(d: Path, owner: str) -> str:
    """Digest of the current task contract, or empty when there is none."""
    task = d / f"{owner}-task.mdx"
    if not task.is_file():
        return ""
    try:
        return digest_of(task.read_bytes())
    except OSError:
        return ""


def current_round_digest(d: Path, owner: str, rnd: int) -> str:
    """Bundle digest for the current round, falling back to report evidence."""
    try:
        frozen, _ = current_bundle(d, owner, rnd)
    except (OSError, ValueError):
        frozen = None
    if frozen:
        digest = str(frozen.get("digest", ""))
        if digest:
            return digest
    rep = d / f"{owner}-report-{rnd:02d}.mdx"
    if rep.is_file():
        try:
            return evidence_digest(rep.read_text())
        except (OSError, ValueError):
            return ""
    return ""


def matching_verifications(d: Path, owner: str, rnd: int,
                           bundle_digest: str, contract_rev: str) -> list[tuple[Path, dict, str]]:
    """Verification artifacts for this round bound to this exact evidence."""
    out: list[tuple[Path, dict, str]] = []
    try:
        paths = verification_paths(d, owner, rnd)
    except (OSError, ValueError):
        return []
    for path in paths:
        try:
            meta, body = parse(path.read_text())
        except (OSError, ValueError):
            continue
        if bundle_digest and str(meta.get("bundle_digest", "")) != bundle_digest:
            continue
        if contract_rev and str(meta.get("contract_revision", "")) != contract_rev:
            continue
        out.append((path, meta, body))
    return out


def round_failed_verification(d: Path, owner: str, rnd: int) -> bool:
    """Whether the current round's latest verification for its evidence is a fail.

    One shared rule for failure routing and batch honesty: a submitted round
    whose verifier recorded fail is never approval-ready and never reviewable
    as a batch, while an unverified round or a round whose latest verdict is
    pass or uncertain is unaffected. A correction that moved the round, or a
    reviewer decision that settled it, leaves no submitted fail behind.
    """
    if not rnd:
        return False
    try:
        digest = current_round_digest(d, owner, rnd)
        contract_rev = current_task_revision(d, owner)
        matched = matching_verifications(d, owner, rnd, digest, contract_rev)
    except (OSError, ValueError):
        return False
    if not matched:
        return False
    _, vmeta, _ = matched[-1]
    return str(vmeta.get("result", "")) == "fail"
