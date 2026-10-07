"""`docket doctor`: setup and signalling checks."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .common import SKILL_DIR, STATE_DIR, SUPPORTED_HARNESSES, _has
from .paths import root, run_dir
from .policy import is_quick_milestone, run_policy
from .evidence import git_argv, git_env
from .state import ROLES, armed
from .events import derive_events
from .delivery import paused_flag
from .qualification import probe_boundary
from .hook_config import codex_hook_configuration, skill_copy_differences, wake_hook_configured
from .sessions import codex_home


def cmd_doctor(a: argparse.Namespace) -> None:
    """Report what is available and what each missing piece costs. Never blocks."""
    ok, warn = "  ok  ", " note "
    print("docket doctor\n")

    print("Required")
    print(f"  [{ok}] python {sys.version.split()[0]}")
    print(f"  [{ok if _has('git') else warn}] git" + ("" if _has("git") else "  - needed to review implementor diffs"))
    inrepo = subprocess.run(git_argv("rev-parse", "--is-inside-work-tree"), env=git_env(),
                            capture_output=True, cwd=root()).returncode == 0 if _has("git") else False
    print(f"  [{ok if inrepo else warn}] inside a git repo"
          + ("" if inrepo else "  - no diff evidence; a run must declare"
                              " evidence_mode: documents-only"))

    print(f"\nSkill files\n  [{ok}] running {SKILL_DIR}")
    installed = Path.home() / ".agents" / "skills" / "docket"
    if not (installed / "SKILL.md").is_file():
        print(f"  [{warn}] no installed copy at {installed}")
    elif installed.resolve() != SKILL_DIR.resolve():
        try:
            differences = skill_copy_differences(SKILL_DIR, installed)
        except OSError as exc:
            print(f"  [{warn}] installed/source comparison unavailable: {exc}")
        else:
            print(f"  [{warn if differences else ok}] installed copy {installed}: "
                  + (f"differs in {len(differences)} runtime file(s)" if differences else "matches runtime files"))
            if differences:
                print("         " + ", ".join(differences[:5]))
                print("         sync the whole validated skill after approval and active runs finish.")

    print("\nDispatch (how a supervisor launches and prompts a subordinate)")
    herdr = _has("herdr")
    inside = os.environ.get("HERDR_ENV") == "1"
    if herdr and inside:
        print(f"  [{ok}] herdr, and this session is inside a herdr pane")
        print("         -> use `herdr pane split` + `herdr agent start/prompt`. Best option.")
    elif herdr:
        print(f"  [{warn}] herdr installed, but this session is NOT inside a herdr pane")
        print("         HERDR_ENV is unset, so herdr cannot be driven from here.")
        print("         -> start your planner from inside herdr, or use a fallback below.")
    else:
        print(f"  [{warn}] herdr not installed  (https://github.com/kunchenguid)")
    for m in ("tmux", "zellij"):
        if _has(m):
            print(f"  [{ok}] {m}  -> usable fallback: one window per role, prompt by hand")
    if not any(_has(m) for m in ("herdr", "tmux", "zellij")):
        print(f"  [{warn}] no multiplexer found")
        print("         -> run each role in its own terminal window, or dispatch headless.")

    print("\nAgent CLIs found (for --harness on assign)")
    found = [c for c in SUPPORTED_HARNESSES if _has(c)]
    print(f"  [{ok if found else warn}] " + (", ".join(found) if found else "none found"))
    if _has("claude"):
        print("         headless fallback available: claude -p \"$(cat <task>.mdx)\"")
        print("         note: -p is Agent SDK usage and may be metered separately from")
        print("         interactive use. Driving an interactive session is not.")

    print("\nWake signalling (optional; without it a supervisor blocks instead of idling)")
    claude_settings = (
        (root() / ".claude" / "settings.json", ".claude/settings.json"),
        (root() / ".claude" / "settings.local.json", ".claude/settings.local.json"),
        (Path.home() / ".claude" / "settings.json", "~/.claude/settings.json"),
    )
    wired_claude = [label for path, label in claude_settings
                    if wake_hook_configured(path, claude=True)]
    if wired_claude:
        print(f"  [{ok}] Stop hook (Claude) in {', '.join(wired_claude)}")
    else:
        checked = ", ".join(label for _, label in claude_settings)
        print(f"  [{warn}] Stop hook (Claude) absent in {checked}"
              "  - see `docket help signalling`")
    codex_config = codex_hook_configuration(codex_home())
    codex_label = ", ".join(str(path).replace(str(Path.home()), "~", 1)
                            for path in codex_config["locations"]) or str(
                                codex_home() / "hooks.json").replace(str(Path.home()), "~", 1)
    wired_codex = bool(codex_config["locations"] and codex_config["enabled"])
    print(f"  [{ok if wired_codex else warn}] Stop hook (Codex) "
          f"{'in' if wired_codex else 'absent in'} {codex_label}"
          + ("" if wired_codex else "  - see `docket help signalling`"))
    if codex_config["locations"]:
        print("         synchronous adapter configured; local hooks "
              + ("enabled" if codex_config["enabled"] else "disabled")
              + "; trust and effective overrides unknown; confirm in /hooks and test a wake.")
    role = os.environ.get("DOCKET_ROLE", "")
    print(f"  [{ok if role in ROLES else warn}] DOCKET_ROLE"
          + (f"={role}" if role in ROLES else " is unset - this session cannot consume wake events"))
    pairs = armed()
    if pairs:
        print(f"  [{ok}] watcher armed:")
        for r, ro in pairs:
            print(f"           {r}  role={ro}")
        for run in sorted({r for r, _ in pairs}):
            present = {ro for r, ro in pairs if r == run}
            d = run_dir(run)
            try:
                policy = run_policy(d) if d.is_dir() else None
            except SystemExit:
                policy = None
            expected = set(policy["role_sessions"].split(", ")) if policy else set()
            if policy and not is_quick_milestone(d):
                expected.discard("implementor")
            missing = expected - present
            if missing:
                print(f"  [{warn}] {run} is missing role(s): {', '.join(sorted(missing))}")
    else:
        print(f"  [{ok}] watcher not armed (correct when idle)")
    # wake.sh records a launcher or argument failure here, because a hook that
    # cannot start never wakes anyone and the harness shows nothing.
    for failure in sorted((root() / STATE_DIR).glob("hook-failure-*")):
        hook_role = failure.name.removeprefix("hook-failure-")
        try:
            detail = " | ".join(line.strip() for line in failure.read_text().splitlines()
                                if line.strip())
        except OSError as exc:
            detail = f"unreadable: {exc}"
        print(f"  [{warn}] wake hook for {hook_role} last failed, so it wakes nobody: {detail}")

    print("\ndocket itself works with none of the optional pieces above.")
    print("The protocol is files; dispatch and wake are conveniences.")

    print("\nDelivery (leased events, explicit pickup, qualified notices)")
    boundary, mode, detail = probe_boundary()
    print(f"  [note] supervised delivery service: none - never a model-launched background job")
    print(f"  [note] mode: {mode} (boundary: {boundary})")
    print(f"  [note] probe: {detail}")
    for r, ro in armed():
        d = run_dir(r)
        if not d.is_dir():
            continue
        paused = "paused" if paused_flag(d, ro).is_file() else "live"
        try:
            queued = len(derive_events(d, ro))
        except (OSError, ValueError):
            queued = -1
        print(f"  [note] {r} {ro}: delivery {paused}, {queued} queued event(s)")
