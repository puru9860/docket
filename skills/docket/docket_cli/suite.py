"""Suite qualification: unittest summary parsing and suite artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .common import die, stamp
from .paths import root
from .publication import publish_bytes, publish_json
from .policy import need_run
from .bundles import check_frozen_digest, digest_of, freeze_record
from .qualification import worktree_identity


SUITE_COUNT_RE = re.compile(r"^Ran (\d+) tests?( in \S+s)?$")

SUITE_COUNT_SHAPE_RE = re.compile(r"^Ran \d+ tests?\b.*$")

SUITE_OK_RE = re.compile(r"^OK(\s+\(.*\))?$")

SUITE_FAILED_RE = re.compile(r"^FAILED \((.*)\)$")

SUITE_FAILED_SHAPE_RE = re.compile(r"^FAILED\b.*$")


SUITE_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_suite_ansi(text: str) -> str:
    """Strip ANSI presentation (color) from suite output without changing meaning.

    Runners colorize their terminal verdict when FORCE_COLOR is set; the bytes
    still carry exactly one unittest summary. Removing CSI sequences lets the
    strict summary rule see it, while every existing refusal still refuses
    because only presentation is removed, never summary structure. Both the
    live qualifier and frozen re-validation call this one helper through the
    shared parser, so the two can never disagree about what is measurable.
    """
    return SUITE_ANSI_RE.sub("", text)


def suite_summary_shaped(stripped: str) -> bool:
    """Whether one output line is unittest summary material rather than noise.

    Counts and terminal verdicts are the only lines a unittest summary is made
    of. Treating them as ordinary output whenever they are malformed is what
    lets a contradictory verdict ride along beside a valid one, so any line of
    this shape must take part in exactly one complete terminal summary.
    """
    plain = strip_suite_ansi(stripped)
    return bool(SUITE_COUNT_SHAPE_RE.match(plain)
                or SUITE_OK_RE.match(plain)
                or SUITE_FAILED_SHAPE_RE.match(plain))


def parse_suite_summary(text: str) -> tuple[tuple[int, int, bool] | None, str | None]:
    """One complete unittest summary in a suite log, or why it is unusable.

    Returns (summary, None) only for exactly one complete `Ran N tests` plus
    terminal `OK` / `FAILED (...)` pair, as (tests, failures, passed).
    Anything else returns (None, reason) and must refuse rather than guess:
    a bare count with no verdict is not a result, a terminal status must be
    the last non-blank line, and multiple counts or verdicts never resolve to
    one silently. Ordinary test-progress output before the summary is allowed;
    only blank lines may follow the terminal status.
    """
    pending: int | None = None
    done: tuple[int, int, bool] | None = None
    for line in text.splitlines():
        stripped = strip_suite_ansi(line.strip())
        if not stripped:
            continue
        ran = SUITE_COUNT_RE.match(stripped)
        if ran is None and SUITE_COUNT_SHAPE_RE.match(stripped):
            return None, ("malformed unittest test count "
                          f"({stripped!r}); a summary-shaped count must be a "
                          "complete 'Ran N tests' line, refusing")
        if ran:
            if done is not None:
                return None, ("output after the terminal unittest status "
                              f"({stripped!r}); the verdict must be terminal, refusing")
            if pending is not None:
                return None, ("multiple unittest test counts without a terminal "
                              "verdict; refusing to select one silently")
            pending = int(ran.group(1))
            continue
        terminal: tuple[int, bool] | None = None
        if SUITE_OK_RE.match(stripped):
            terminal = (0, True)
        else:
            failed = SUITE_FAILED_RE.match(stripped)
            if failed:
                failures = sum(int(n) for _, n in re.findall(
                    r"(?:^|,\s*)(failures|errors)=(\d+)(?=,|$)", failed.group(1)))
                terminal = (failures, False)
            elif SUITE_FAILED_SHAPE_RE.match(stripped):
                return None, ("malformed terminal unittest status "
                              f"({stripped!r}); a failing verdict must name its "
                              "failure detail, refusing")
        if terminal is not None:
            if done is not None:
                return None, ("output after the terminal unittest status "
                              f"({stripped!r}); the verdict must be terminal, refusing")
            if pending is None:
                return None, ("terminal unittest status without a preceding "
                              "'Ran N tests' line; refusing")
            failures, passed = terminal
            done = (pending, failures, passed)
            pending = None
            continue
        if done is not None:
            return None, ("output after the terminal unittest status "
                          f"({stripped!r}); the verdict must be terminal, refusing")
    if done is not None:
        return done, None
    if pending is not None:
        return None, ("unittest test count without a terminal verdict; a bare "
                      "'Ran N tests' line is not a result, refusing")
    return None, ("no supported summary (supported: unittest 'Ran N tests' "
                  "plus a trailing OK/FAILED line)")


def parse_suite_streams(stdout_text: str, stderr_text: str
                        ) -> tuple[tuple[int, int, bool] | None, str | None]:
    """One unittest summary from the stream that holds it, or why not.

    Stdout and stderr stay separate frozen artifacts; this only decides which
    one carries the supported summary. Python's unittest runner writes its
    `Ran N tests` plus `OK`/`FAILED` summary to stderr, so a real green suite
    presents an empty stdout and a valid stderr summary. Ordinary progress
    output in the other stream is fine. Summary-shaped output there is not: a
    count or terminal verdict that does not form its own stream's one complete
    terminal summary refuses the whole capture, even when the other stream
    reads green, because the two streams then disagree about what happened.
    When neither stream holds a summary the output is unmeasurable. When both
    hold summaries the result is ambiguous - even identical summaries refuse,
    because the capture cannot attribute the result to one stream.
    Contradictory summaries refuse for the same reason: the record would be
    guessing which stream to trust.
    """
    out, out_problem = parse_suite_summary(stdout_text)
    err, err_problem = parse_suite_summary(stderr_text)
    for label, text, parsed in (("stdout", stdout_text, out),
                                ("stderr", stderr_text, err)):
        if parsed is not None:
            continue
        stray = [line.strip() for line in text.splitlines()
                 if line.strip() and suite_summary_shaped(line.strip())]
        if stray:
            problem = out_problem if label == "stdout" else err_problem
            return None, (f"{label} carries summary material that forms no complete "
                          f"terminal summary ({stray[0]!r}): {problem}; a summary "
                          "token in either stream must complete that stream's own "
                          "verdict, refusing")
    if out is None and err is None:
        return None, ("no complete unittest summary in stdout or stderr "
                      f"(stdout: {out_problem}; stderr: {err_problem})")
    if out is not None and err is not None:
        if out != err:
            return None, ("contradictory suite summaries in stdout and stderr "
                          f"(stdout {out[0]} tests, {out[1]} failures "
                          f"({'OK' if out[2] else 'FAILED'}) vs stderr "
                          f"{err[0]} tests, {err[1]} failures "
                          f"({'OK' if err[2] else 'FAILED'})); refusing")
        return None, ("ambiguous suite summaries in both stdout and stderr "
                      f"(both report {out[0]} tests, {out[1]} failures "
                      f"({'OK' if out[2] else 'FAILED'})); refusing")
    return (out if out is not None else err), None


def suite_dir(d: Path) -> Path:
    return d / ".suite"


def cmd_suite(a: argparse.Namespace) -> None:
    """Run the suite once with its source and outputs captured, or refuse.

    Captures the content tree and command bytes before execution, runs the
    suite, then captures bounded timestamps, exit status, outputs, and the
    content tree afterward. Source drift during the run refuses without
    freezing an artifact: output that cannot be attributed to one tree proves
    nothing. A red suite still freezes an honest artifact; release refuses
    red separately.
    """
    d = need_run(a.run)
    if not a.qualify:
        die("only --qualify is supported: `docket suite RUN --qualify --command CMD`")
    command = (a.command or "").strip()
    if not command:
        die("suite --qualify needs --command TEXT with the exact suite invocation")
    try:
        timeout = int(a.timeout or 1800)
    except (TypeError, ValueError):
        die("--timeout must be an integer number of seconds")
    if timeout <= 0:
        die("--timeout must be an integer number of seconds")
    before = worktree_identity()
    started = time.time()
    try:
        proc = subprocess.run(command, shell=True, cwd=str(root()),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        die(f"suite timed out after {timeout}s; no artifact frozen")
    except OSError as exc:
        die(f"suite could not start ({exc}); no artifact frozen")
    ended = time.time()
    after = worktree_identity()
    if before != after:
        die("source drifted during the suite run; rerun against a settled tree "
            "so the outputs attribute to one content tree")
    stdout_bytes = proc.stdout.encode()
    stderr_bytes = proc.stderr.encode()
    summary, stream_problem = parse_suite_streams(proc.stdout, proc.stderr)
    if summary is None:
        die(f"suite output holds no qualifiable summary: {stream_problem}; "
            "refusing to freeze unmeasurable or ambiguous output as observed "
            "results")
    tests, failures, verdict_ok = summary
    if tests <= 0:
        die("suite result records no tests; a release needs observed results")
    slug = f"qual-{int(started)}-{os.getpid()}"
    suite_dir(d).mkdir(parents=True, exist_ok=True)
    out_dir = suite_dir(d)
    publish_bytes(out_dir / f"{slug}.stdout", stdout_bytes)
    publish_bytes(out_dir / f"{slug}.stderr", stderr_bytes)
    record: dict[str, object] = {
        "run": a.run,
        "command": command,
        "cwd": str(root()),
        "started_at": stamp(started),
        "ended_at": stamp(ended),
        "duration_s": round(ended - started, 3),
        "exit": proc.returncode,
        "tests": tests,
        "failures": failures,
        "ok": proc.returncode == 0 and failures == 0 and verdict_ok,
        "source_before": before,
        "source_after": after,
        "stdout_file": f"{slug}.stdout",
        "stdout_digest": digest_of(stdout_bytes),
        "stdout_bytes": len(stdout_bytes),
        "stderr_file": f"{slug}.stderr",
        "stderr_digest": digest_of(stderr_bytes),
        "stderr_bytes": len(stderr_bytes),
    }
    publish_json(out_dir / f"{slug}.json", freeze_record(record))
    print(f"qualified suite {slug}: exit {proc.returncode}, {tests} tests, "
          f"{failures} failures")
    print(f"artifact: {out_dir / f'{slug}.json'}")


def resolve_suite_artifact(d: Path, arg: str) -> tuple[Path | None, list[str]]:
    """One suite qualification artifact by path or digest, or why not."""
    base = suite_dir(d)
    if arg:
        path = Path(arg)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            inside = path.resolve().relative_to(base.resolve())
        except (OSError, ValueError):
            inside = None
        if inside is None or str(inside).startswith(".."):
            return None, [f"suite artifact {arg!r} is outside this run's .suite/ "
                          "directory; evidence must live in-run"]
        if not path.is_file():
            return None, [f"suite artifact {arg!r} is missing or unreadable"]
        return path, []
    found = sorted(base.glob("qual-*.json")) if base.is_dir() else []
    if not found:
        return None, ["no suite qualification artifact; run docket suite RUN --qualify"]
    if len(found) > 1:
        return None, ["multiple suite qualification artifacts; pass --suite-artifact PATH"]
    return found[0], []


def suite_problems(path: Path, run: str) -> list[str]:
    """Why one suite qualification artifact cannot support release.

    Checks frozen integrity, run binding, required fields, timestamp order,
    exit/count consistency, the unittest summary re-parsed from the frozen
    stdout and stderr under the same stream rule the qualify command uses,
    and equal before/after source trees. A sha256 source identity is required:
    unknown trees are honest diagnostics but never qualified release evidence.
    A red suite is well-formed evidence and passes here; freezing a release on
    it is refused separately.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return [f"suite artifact {path.name} is unreadable or malformed"]
    if not isinstance(data, dict):
        return [f"suite artifact {path.name} is malformed; only frozen suite "
                "records count"]
    digest_problems = check_frozen_digest(data)
    if digest_problems:
        return [f"suite artifact {path.name} is not frozen evidence ("
                + "; ".join(digest_problems) + ")"]
    problems: list[str] = []
    if str(data.get("run", "")) != run:
        problems.append(f"suite artifact {path.name} belongs to run "
                        f"{data.get('run', '')!r}, not {run!r}")
    if not str(data.get("command", "") or "").strip():
        problems.append(f"suite artifact {path.name} records no command")
    if not str(data.get("cwd", "") or "").strip():
        problems.append(f"suite artifact {path.name} records no working directory")
    started = str(data.get("started_at", ""))
    ended = str(data.get("ended_at", ""))
    if not started or not ended:
        problems.append(f"suite artifact {path.name} records no bounded timestamps")
    elif ended < started:
        problems.append(f"suite artifact {path.name} timestamps run backwards")
    for field in ("exit", "tests", "failures"):
        value = data.get(field, None)
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"suite artifact {path.name} records no integer {field}")
    if not isinstance(data.get("ok", None), bool):
        problems.append(f"suite artifact {path.name} records no boolean ok")
    before = str(data.get("source_before", "") or "")
    after = str(data.get("source_after", "") or "")
    if not before or not after:
        problems.append(f"suite artifact {path.name} binds no source tree")
    elif before != after:
        problems.append(f"suite artifact {path.name} source drifted during the run; "
                        "rerun against a settled tree")
    elif not before.startswith("sha256:"):
        if before.startswith("unknown "):
            problems.append(f"suite artifact {path.name} binds unknown source tree "
                            f"{before!r}; unknown trees are diagnostic only, never "
                            "release evidence - re-run against a git checkout with "
                            "a valid sha256 identity")
        else:
            problems.append(f"suite artifact {path.name} carries a malformed "
                            "content tree binding")
    if isinstance(data.get("exit"), int) and isinstance(data.get("failures"), int) \
            and isinstance(data.get("ok"), bool):
        if data["ok"] and (data["exit"] != 0 or data["failures"] != 0):
            problems.append(f"suite artifact {path.name} ok flag contradicts its "
                            "exit status and failure count")
    for key in ("stdout", "stderr"):
        ref = data.get(f"{key}_file", "")
        anchor = data.get(f"{key}_digest", "")
        target = path.parent / str(ref) if isinstance(ref, str) and ref else None
        if target is None or not target.is_file():
            problems.append(f"suite artifact {path.name} is missing its frozen {key}")
            continue
        try:
            actual = digest_of(target.read_bytes())
        except OSError:
            problems.append(f"suite artifact {path.name} frozen {key} is unreadable")
            continue
        if actual != anchor:
            problems.append(f"suite artifact {path.name} frozen {key} is damaged "
                            "(digest mismatch)")
    out_path = path.parent / str(data.get("stdout_file", "") or "")
    err_path = path.parent / str(data.get("stderr_file", "") or "")
    if out_path.is_file() and err_path.is_file():
        try:
            out_text = out_path.read_text(errors="replace")
        except OSError:
            out_text = None
        try:
            err_text = err_path.read_text(errors="replace")
        except OSError:
            err_text = None
        if out_text is None or err_text is None:
            problems.append(f"suite artifact {path.name} frozen output is unreadable")
        else:
            summary, stream_problem = parse_suite_streams(out_text, err_text)
            if summary is None:
                problems.append(f"suite artifact {path.name} frozen output holds no "
                                f"qualifiable summary: {stream_problem}")
            elif (summary[0] != data.get("tests") or summary[1] != data.get("failures")
                    or data.get("ok") != (summary[2] and data.get("exit") == 0
                                          and summary[1] == 0)):
                problems.append(f"suite artifact {path.name} frozen output reports "
                                f"{summary[0]} tests, {summary[1]} failures "
                                f"({'OK' if summary[2] else 'FAILED'}) but the record claims "
                                f"{data.get('tests')} tests, {data.get('failures')} failures; "
                                "refusing")
    return problems
