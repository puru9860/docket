#!/usr/bin/env python3
"""Prepare, prove, grade and report the two-pair recipe-only comparison (stdlib only)."""

import argparse
import difflib
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = ("compare.py", "design.json", "rubric.json", "hidden/check.mjs")
# Words that would label a candidate path with the experiment's purpose.
BLINDING_WORDS = ("comparison", "treatment", "control", "experiment", "eval", "hidden",
                  "rubric", "blind", "recipe", "trial", "candidate", "variant", "arm")
RUN_OWNER = "T01"
SEAL, OPEN = 0o000, 0o700


def die(message: str) -> None:
    raise SystemExit(f"compare: {message}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, value: object, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    if private:
        path.chmod(0o600)


def skipped(rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return top in (".git", ".docket") or rel == "ui/node_modules" or rel.startswith("ui/node_modules/")


def manifest(root: Path, everything: bool = False) -> dict[str, str]:
    """Per-file sha256 (or symlink target) of a tree, without VCS, Docket state or dependencies."""
    out = {}
    for current, dirs, files in os.walk(root):
        base = Path(current)
        for name in sorted(dirs):
            if (base / name).is_symlink():
                files.append(name)
        dirs[:] = [d for d in sorted(dirs) if not (base / d).is_symlink()
                   and (everything or not skipped((base / d).relative_to(root).as_posix()))]
        for name in sorted(files):
            path = base / name
            rel = path.relative_to(root).as_posix()
            if not everything and skipped(rel):
                continue
            out[rel] = f"link:{os.readlink(path)}" if path.is_symlink() else sha(path.read_bytes())
    return out


def digest(entries: dict[str, str]) -> str:
    return sha(json.dumps(sorted(entries.items())).encode())


def frozen_hashes() -> dict[str, str]:
    files = [*FROZEN, *(f"tasks/{t}.json" for t in design()["tasks"])]
    return {rel: sha((HERE / rel).read_bytes()) for rel in files}


def design() -> dict:
    return load_json(HERE / "design.json")


def task(name: str) -> dict:
    return load_json(HERE / "tasks" / f"{name}.json")


def private_env(base: Path, browsers: str = "") -> dict[str, str]:
    """A process environment with no user config, credentials or harness identity."""
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
    dirs = {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_STATE_HOME": "state",
            "XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache", "TMPDIR": "tmp",
            "PYTHONPYCACHEPREFIX": "pycache"}
    for key, name in dirs.items():
        env[key] = str(base / name)
        (base / name).mkdir(parents=True, exist_ok=True)
    env.update(PYTHONDONTWRITEBYTECODE="1", DOCKET_SESSION_CAPTURE="off", GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    if browsers:
        env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
    return env


def default_browsers() -> str:
    return os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright")


def runtime(deps: Path, browsers: str, env: dict[str, str]) -> dict:
    """The dependencies, Node and browser builds the hidden checks run with."""
    base = Path(browsers)
    node = subprocess.run(["node", "--version"], env=env, capture_output=True, text=True)
    return {"node_modules_digest": digest(manifest(deps, everything=True)) if deps.is_dir() else None,
            "node": node.stdout.strip() if node.returncode == 0 else None,
            "browsers": sorted(p.name for p in base.iterdir() if p.name.startswith("chromium")) if base.is_dir() else []}


def run(args: list[str], cwd: Path, env: dict[str, str], check: bool = True,
        timeout: int = 1800) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        die(f"{' '.join(args[:4])} failed ({result.returncode}): {(result.stderr or result.stdout).strip()[-600:]}")
    return result


def absolute(value: str, label: str) -> Path:
    if not value or not os.path.isabs(value):
        die(f"{label} must be an explicit absolute path")
    return Path(os.path.abspath(value))


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def owned_root(path: Path, label: str) -> Path:
    """Create a new or empty directory this tool owns; never adopt a populated one."""
    if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
        die(f"{label} {path} must be a new or empty directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def export(source: Path, revision: str, dest: Path, excluded: list[str], env: dict[str, str]) -> dict:
    """Extract one pinned revision without its experiment documents; the checkout is only read."""
    commit = run(["git", "-C", str(source), "rev-parse", "--verify", f"{revision}^{{commit}}"], source, env).stdout.strip()
    tree = run(["git", "-C", str(source), "rev-parse", f"{commit}^{{tree}}"], source, env).stdout.strip()
    date = run(["git", "-C", str(source), "show", "-s", "--format=%cI", commit], source, env).stdout.strip()
    dest.mkdir(parents=True)
    present = set()
    archive = subprocess.Popen(["git", "-C", str(source), "archive", "--format=tar", commit],
                               stdout=subprocess.PIPE, env=env)
    with tarfile.open(fileobj=archive.stdout, mode="r|") as tar:
        for member in tar:
            hit = next((e for e in excluded if member.name == e or member.name.startswith(e + "/")), None)
            if hit:
                present.add(hit)
                continue
            tar.extract(member, dest, filter="data")
    archive.stdout.close()
    if archive.wait():
        die(f"git archive {commit} failed")
    # Git stores no empty directory, so any left here exists only because of an exclusion.
    for current, _, _ in sorted(os.walk(dest), key=lambda w: -len(w[0])):
        if current != str(dest) and not os.listdir(current):
            os.rmdir(current)
    entries = manifest(dest)
    return {"revision": commit, "pinned_tree": tree, "committer_date": date,
            "excluded_present": sorted(present), "excluded_absent": sorted(set(excluded) - present),
            "sanitized_files": len(entries), "sanitized_digest": digest(entries),
            "note": "Sanitized export: intentionally differs from the pinned Git tree by the excluded paths."}


def residuals(tree: Path, markers: list[str]) -> list[dict]:
    """Remaining mentions candidates can read; recorded, not removed."""
    found = []
    for rel in sorted(manifest(tree)):
        path = tree / rel
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text().lower()
        except UnicodeDecodeError:
            continue
        for marker in markers:
            if count := text.count(marker.lower()):
                found.append({"path": rel, "marker": marker, "count": count})
    return found


def edit(tree: Path, edits: list[dict]) -> None:
    for change in edits:
        path = tree / change["path"]
        text = path.read_text()
        if text.count(change["old"]) != 1:
            die(f"{change['path']}: edit anchor must occur exactly once at this revision")
        path.write_text(text.replace(change["old"], change["new"]))


def copy_tree(source: Path, dest: Path) -> None:
    shutil.copytree(source, dest, symlinks=True)


def cli_decision(launcher: str) -> str:
    # Worded so the renderer's bare-command rewrite never touches it: no backtick, line start or ": " before "docket".
    return (f"Docket CLI: run every Docket command through the absolute launcher `{launcher}`, including the startup "
            "register, pickup and instruct commands in a conductor message and any example that shows the bare command "
            f"name; for role help use `{launcher} help ROLE`. Never run Docket through PATH, an alias or another copy.")


def plan_for(spec: dict, arm: str, d: dict, launcher: str) -> dict:
    task_spec = {"owner": RUN_OWNER, "title": spec["title"], "goal": spec["goal"],
                 "criterion": spec["criteria"], "file": spec["files"], "verify": d["arms"][arm]["verify"],
                 "verify_timeout": d["verify_timeout"], "risk": "low", "complexity": "low",
                 "executor": "implementor", "out_of_scope": spec["out_of_scope"],
                 "decision": [cli_decision(launcher), *spec["notes"]]}
    if "recipe" in d["arms"][arm]:
        task_spec["recipe"] = d["arms"][arm]["recipe"]
    m = d["models"]
    return {"run": {"title": spec["title"], "objective": spec["objective"], "approach": d["approach"],
                    "evidence_mode": "git"},
            "roots": ["app=."], "tasks": [task_spec],
            "milestones": [{"id": d["milestone"], "members": [RUN_OWNER], "command": d["milestone_command"],
                            "implementor_harness": m["implementor_harness"], "implementor_model": m["implementor_model"],
                            "reviewer_harness": m["reviewer_harness"], "reviewer_model": m["reviewer_model"],
                            "assignment_reason": "Fixed model assignment held equal across matched tasks."}]}


def helper_outcome(exit_code: int, result: dict) -> dict:
    """The recipe helper's actual result, classified by where the saved list went missing."""
    error = result.get("error") or {}
    passed = [c.get("name") for c in result.get("checks", []) if c.get("passed")]
    status, phase, code, message = result.get("status"), result.get("phase"), error.get("code"), error.get("message", "")
    if status == "passed" and exit_code == 0:
        kind = "passed"
    elif status == "prerequisite-failed" or code == "PREREQUISITE" or exit_code == 2:
        kind = "prerequisite"
    elif "browser-add-persisted" in passed and phase == "restart-readback" and code == "PERSISTENCE_MISSING":
        kind = "saved-list-lost-after-restart"
    elif "browser-add-persisted" not in passed and phase == "browser-add" and "projects.json" in message:
        kind = "saved-list-not-written"
    else:
        kind = "other-failure"
    return {"exit": exit_code, "status": status, "phase": phase, "code": code, "message": message,
            "passed_checks": passed, "kind": kind}


def proof_problems(name: str, record: dict) -> list[str]:
    """Pre-run sensitivity every task must show; shared by `prove` and `prepare`."""
    problems = []
    try:
        for variant in ("reference", "alternate-fix"):
            if record[variant]["status"] != "passed":
                problems.append(f"{name}: {variant} failed {record[variant]['failed'] or record[variant]['error']}")
        seeded = record["seeded"]
        if not seeded["decisive_failed"]:
            problems.append(f"{name}: seeded defect passed every decisive check")
        if not seeded["production_tests"]["passed"]:
            problems.append(f"{name}: existing UI tests (ui npm test) already catch the seed")
        # A covered seed must fail the original journey because the saved list is missing,
        # either never written or lost by the restart; a prerequisite or unrelated failure is no proof.
        helper = seeded["recipe_helper"]
        allowed = {"saved-list-not-written", "saved-list-lost-after-restart"} if task(name)["recipe_covered"] else {"passed"}
        if helper.get("kind") not in allowed or helper.get("exit") != (1 if task(name)["recipe_covered"] else 0):
            problems.append(f"{name}: recipe helper on the seed gave {helper.get('kind')} "
                            f"(exit {helper.get('exit')}, {helper.get('phase')}, {helper.get('code')}), expected {sorted(allowed)}")
    except (KeyError, TypeError) as error:
        problems.append(f"{name}: incomplete proof record ({error})")
    return problems


def sensitivity(proof_path: Path, source_record: dict, now: dict) -> dict:
    """The passing `prove` result this preparation depends on, frozen into the study."""
    if not proof_path.is_file():
        die("--proof must name proof.json from a passing `prove` run")
    proof = load_json(proof_path)
    if proof.get("problems") != [] or proof.get("frozen") != frozen_hashes():
        die("the proof failed or was made with different tool inputs; rerun `prove`")
    if {k: proof["source"].get(k) for k in ("revision", "sanitized_digest")} != \
            {k: source_record[k] for k in ("revision", "sanitized_digest")}:
        die("the proof was made for a different source revision or sanitization")
    if sorted(proof.get("tasks", {})) != sorted(design()["tasks"]):
        die("the proof does not cover exactly the design's tasks")
    if proof.get("runtime") != now:
        die("the proof was made with other dependencies, Node or browser builds; rerun `prove` with these")
    if problems := [p for name, record in proof["tasks"].items() for p in proof_problems(name, record)]:
        die(f"the proof does not show the required sensitivity: {'; '.join(problems)}")
    tasks = {}
    for name, record in proof["tasks"].items():
        seeded = record["seeded"]
        tasks[name] = {"recipe_covered": task(name)["recipe_covered"],
                       "reference": record["reference"]["status"], "alternate_fix": record["alternate-fix"]["status"],
                       "seeded_decisive_failed": seeded["decisive_failed"],
                       "seeded_production_tests_passed": seeded["production_tests"]["passed"],
                       "seeded_recipe_helper": seeded["recipe_helper"]}
    covered = sorted(n for n, t in tasks.items() if t["recipe_covered"])
    adjacent = sorted(n for n, t in tasks.items() if not t["recipe_covered"])
    coverage = " ".join(part for part in (
        f"Covered: the selected recipe's own journey (browser add, same-startup restart) detects the seeded defect of "
        f"{', '.join(covered)}; that pair measures detection and cost at the recipe boundary." if covered else "",
        f"Adjacent: the journey passes on the seeded source of {', '.join(adjacent)}; that pair measures only the "
        "recipe's operational guidance and preservation value, and an unfavorable result there does not disprove "
        "the recipe boundary." if adjacent else "") if part)
    return {"proof_sha256": sha(proof_path.read_bytes()), "runtime": now, "tasks": tasks, "covered": covered, "adjacent": adjacent,
            "coverage": coverage}


def label_for(seed: str, text: str) -> str:
    return "w" + hmac.new(bytes.fromhex(seed), text.encode(), "sha256").hexdigest()[:8]


def schedule(seed: str, tasks: list[str]) -> list[tuple[str, str]]:
    """Counterbalanced ABBA order: the second pair reverses the first pair's arm order."""
    first = ["treatment", "control"] if hmac.new(bytes.fromhex(seed), b"order", "sha256").digest()[0] & 1 \
        else ["control", "treatment"]
    return [(tasks[0], arm) for arm in first] + [(tasks[1], arm) for arm in reversed(first)]


def cmd_prepare(a: argparse.Namespace) -> None:
    d = design()
    source = absolute(a.source_repo, "--source-repo")
    private = absolute(a.private_root, "--private-root")
    candidates = absolute(a.candidate_root, "--candidate-root")
    deps = absolute(a.node_modules, "--node-modules")
    if deps.name != "node_modules" or not deps.is_dir() or deps.is_symlink():
        die("--node-modules must be an existing ui/node_modules directory; nothing else is reused")
    real = [p.resolve() for p in (source, private, candidates)]
    if inside(real[1], real[2]) or inside(real[2], real[1]):
        die("--private-root and --candidate-root must not contain each other")
    if inside(real[1], real[0]) or inside(real[2], real[0]):
        die("output roots must be outside the measured source checkout")
    if hits := [w for w in BLINDING_WORDS if re.search(rf"(?<![a-z]){w}", str(candidates).lower())]:
        die(f"--candidate-root names the study ({', '.join(hits)}); choose a neutral path")
    seed = a.seed or secrets.token_hex(16)
    owned_root(private, "--private-root")
    owned_root(candidates, "--candidate-root")
    env = private_env(private / "env")
    sanitized = private / "source" / "tree"
    source_record = export(source, a.revision, sanitized, d["excluded_paths"], env)
    proven = sensitivity(absolute(a.proof, "--proof"), source_record, runtime(deps, a.browsers_path, env))
    order = schedule(seed, d["tasks"])
    labels = {(t, arm): label_for(seed, f"{t}|{arm}") for t, arm in order}
    if len(set(labels.values())) != len(labels):
        die("label collision; rerun with another seed")
    study = {"version": 1, "frozen": frozen_hashes(), "source": source_record, "sensitivity": proven,
             "prepared_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "residual_mentions": residuals(sanitized, d["residual_markers"]),
             "node_modules_digest": proven["runtime"]["node_modules_digest"],
             "tasks": {t: {"spec_sha256": sha((HERE / "tasks" / f"{t}.json").read_bytes())} for t in d["tasks"]},
             "sequence": [labels[k] for k in order], "candidates": {}}
    for t, arm in order:
        label, spec = labels[(t, arm)], task(t)
        home = candidates / label
        project, tools = home / "docket", home / "tools"
        launch = private / "launch" / label
        launch.mkdir(parents=True)
        copy_tree(sanitized, project)
        edit(project, spec["seed"])
        copy_tree(deps, project / "ui" / "node_modules")
        copy_tree(sanitized / "skills" / "docket", tools / "docket")
        (tools / "bin").mkdir()
        (tools / "bin" / "docket").symlink_to("../docket/bin/docket")
        git_env = {**env, "GIT_AUTHOR_NAME": "dev", "GIT_AUTHOR_EMAIL": "dev@localhost",
                   "GIT_COMMITTER_NAME": "dev", "GIT_COMMITTER_EMAIL": "dev@localhost",
                   "GIT_AUTHOR_DATE": source_record["committer_date"],
                   "GIT_COMMITTER_DATE": source_record["committer_date"]}
        run(["git", "init", "-q", "-b", "main"], project, git_env)
        run(["git", "add", "-A"], project, git_env)
        run(["git", "commit", "-q", "-m", "Import project"], project, git_env)
        commit = run(["git", "rev-parse", "HEAD"], project, git_env).stdout.strip()
        launcher = str(tools / "docket" / "bin" / "docket")
        plan = plan_for(spec, arm, d, launcher)
        write_json(launch / "plan.json", plan, private=True)
        cli = [sys.executable, launcher]
        applied = run([*cli, "plan", "apply", t, "--file", str(launch / "plan.json")], project, env)
        # With no `docket` on the render PATH the renderer writes the pinned launcher's absolute path.
        bare = {**env, "PATH": os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                                if not os.access(os.path.join(p, "docket"), os.X_OK))}
        prompt = run([*cli, "prompt", t, RUN_OWNER, "--role", "implementor",
                      "--model", d["models"]["implementor_model"]], project, bare).stdout
        if launcher not in prompt or cli_decision(launcher) not in prompt or BARE_DOCKET.search(prompt):
            die(f"{label}: the initial prompt does not carry the pinned-CLI decision and absolute commands")
        (launch / "implementor-prompt.md").write_text(prompt)
        prompts = project / ".docket" / "runs" / t / ".prompts"
        agents = json.loads(run([sys.executable, "-c", AGENT_NAMES, str(tools / "docket"), t, d["milestone"]],
                                project, env).stdout)
        if dirty := run(["git", "status", "--porcelain", "--untracked-files=all"], project, git_env).stdout.strip():
            die(f"{label}: preparation left the candidate checkout dirty: {dirty[:300]}")
        entries = manifest(project)
        write_json(launch / "manifest.json", entries, private=True)
        state = manifest(project / ".docket", everything=True)
        study["candidates"][label] = {
            "task": t, "run": t, "commit": commit, "project_digest": digest(entries),
            "plan_sha256": sha((launch / "plan.json").read_bytes()), "prompt_sha256": sha(prompt.encode()),
            "docket_state_digest": digest(state),
            "run_baseline": {k: v for k, v in state.items() if "/.snapshots/" in f"/{k}"},
            "cli_digest": cli_digest(tools / "docket"),
            "pinned_launcher": launcher, "pinned_renderer": RENDERER.findall(prompt)[-1],
            "prepared_prompt_records": {p.name: sha(p.read_bytes()) for p in sorted(prompts.iterdir())},
            "agent_names": agents, "apply_output": (applied.stdout + applied.stderr).strip()}
        home.chmod(SEAL)
    for t in d["tasks"]:
        pair = [c for c in study["candidates"].values() if c["task"] == t]
        if len({c["commit"] for c in pair}) != 1 or len({c["cli_digest"] for c in pair}) != 1:
            die(f"{t}: arms do not start from identical source and CLI")
    write_json(private / "study.json", study, private=True)
    write_json(private / "key.json", {"seed": seed, "arms": {labels[k]: k[1] for k in order}}, private=True)
    (private / "schedule.md").write_text(schedule_text(study, candidates, private))
    print(json.dumps({"study": str(private / "study.json"), "sequence": study["sequence"],
                      "sanitized_digest": source_record["sanitized_digest"],
                      "excluded": source_record["excluded_present"]}, indent=2))


def harness_args(harness: str, model: str, effort: str) -> str:
    """Only model and effort; the operator's own harness alias supplies everything else."""
    return f"--model {model} --effort {effort}" if harness == "claude" else f"-m {model} -c model_reasoning_effort={effort}"


def schedule_text(study: dict, candidates: Path, private: Path) -> str:
    """Operator steps; identical for both arms, so they reveal no mapping. Every Docket command is absolute."""
    m = design()["models"]
    lines = ["# Launch schedule", "", "Launch one label at a time, in this order.",
             "Every label uses the same steps; the variant lives only in its frozen Docket plan.",
             "Nobody hands a prompt to a role and nobody runs `launch`: the pinned conductor delivers every obligation.",
             "Setup interventions are trust or hook-review keypresses before the conductor's first delivery to a role; "
             "any text typed into a role pane after that, outside the conductor, is a human intervention.", ""]
    for n, label in enumerate(study["sequence"], 1):
        c, home = study["candidates"][label], candidates / label
        project, cli = home / "docket", f"DOCKET_SESSION_CAPTURE=off {c['pinned_launcher']}"
        lines += [f"## {n}. {label}", "",
                  f"- Unseal: `chmod 700 {home}`",
                  f"- Project: `{project}`, run `{c['run']}`, milestone `{design()['milestone']}`, commit `{c['commit']}`",
                  f"- Pinned CLI: `{c['pinned_launcher']}` (renderer `{c['pinned_renderer']}`). The frozen contract's "
                  "existing decision tells both roles to use it for every Docket command; operator commands below use "
                  "it absolutely with session capture off.",
                  f"- Check before starting: `{cli} status {c['run']}` (cwd `{project}`)."]
        for role in ("implementor", "reviewer"):
            harness, name = m[f"{role}_harness"], c["agent_names"][role]
            lines.append(f"- Prestart the {role} so the conductor adopts it: a Herdr pane with cwd `{project}` and "
                         f"`--env DOCKET_RUN={c['run']} --env DOCKET_ROLE={role} --env DOCKET_SESSION={name}`, then "
                         f"`herdr agent start {name} --kind {harness} --pane PANE -- "
                         f"{harness_args(harness, m[f'{role}_model'], m[f'{role}_effort'])}`. A fresh session used for nothing else.")
        lines += [f"- Conductor, in its own pane in the foreground: `cd {project} && {cli} conduct {c['run']}`",
                  "- End both role sessions within 900 s of the final decision; keep each whole transcript for `--full-session`.",
                  f"- After finish: `cd {project} && {cli} usage {c['run']} --json > "
                  f"{private / 'telemetry' / (label + '-usage.json')}`",
                  f"- Record `setup_interventions` and `human_interventions` in the observations file, then "
                  f"`compare.py grade --label {label} ...` (it reseals the directory)", ""]
    return "\n".join(lines)


AGENT_NAMES = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from docket_cli.conductor import agent_name
from docket_cli.paths import run_dir
print(json.dumps({role: agent_name(run_dir(sys.argv[2]), sys.argv[3], role) for role in ("implementor", "reviewer")}))
"""


RENDERER = re.compile(r"renderer: (derived-[0-9a-f]+)")
# Every subcommand the pinned fe58b1a parser accepts.
SUBCOMMANDS = ("amendment arm assign batch bundle conduct config decide delivery depend diff disarm discuss dispatch "
               "doctor escalate-mode escalation events feedback finish handoff health help hook-role improvements inbox "
               "init instruct launch metrics migrate models pickup plan preflight prompt propose-amendment reconcile "
               "release reopen resume retrospective review-packet roots route scope session set-model status submit suite "
               "switch-model usage validate-task verify watch").split()
BARE_DOCKET = re.compile(r"(?<![\w/.-])docket\s+(?:%s|-h|--help|--version)(?![\w-])"
                         % "|".join(map(re.escape, sorted(SUBCOMMANDS, key=len, reverse=True))))
RESOLVE_DOCKET = re.compile(r'readlink -f (?:"\$\(command -v docket\)"|\$\(command -v docket\))')


def command_fields(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    out = []
    for key in ("command", "cmd"):
        field = value.get(key)
        if isinstance(field, str):
            out.append(field)
        elif isinstance(field, list) and all(isinstance(x, str) for x in field):
            out.append(" ".join(field))
    return out


# Codex's freeform `exec` tool wraps calls in JavaScript, e.g. text(await tools.exec_command({cmd:"..."})).
# Only a call whose first argument key is a literal string is read; every other mention of a tool name
# (a variable, an alias, a later key, a name inside a string) is opaque rather than guessed.
WRAPPED_TOOLS = {"exec_command": "cmd", "write_stdin": "chars"}
WRAPPED_CALL = re.compile(r'tools\.(exec_command|write_stdin)\(\s*\{\s*(?:[\w"]+\s*:\s*\d+\s*,\s*)?"?(cmd|chars)"?'
                          r'\s*:\s*("(?:[^"\\\n]|\\.)*")')


def wrapped_commands(source: str) -> tuple[list[str], int]:
    """Literal commands and stdin in a Codex exec wrapper, and how many tool calls stayed opaque."""
    commands, read = [], 0
    for match in WRAPPED_CALL.finditer(source):
        if WRAPPED_TOOLS[match[1]] != match[2]:
            continue
        try:
            commands.append(json.loads(match[3]))
        except ValueError:
            continue
        read += 1
    return [c for c in commands if c], sum(source.count(name) for name in WRAPPED_TOOLS) - read


def output_text(value: object) -> str:
    """Every string a tool result carries, including JSON-encoded result envelopes."""
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                return value + "\n" + output_text(json.loads(value))
            except ValueError:
                pass
        return value
    if isinstance(value, list):
        return "\n".join(output_text(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(output_text(v) for k, v in value.items() if k in ("text", "output", "content", "stdout"))
    return ""


def transcript_calls(path: Path) -> dict | None:
    """Tool calls a Claude or Codex transcript recorded, with their commands and paired outputs.

    Tool outputs are never execution. None means nothing in the file parsed, so it gives no evidence either way.
    """
    calls, outputs, opaque, parsed = [], {}, 0, False
    for line in path.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        parsed = True
        message, payload = record.get("message"), record.get("payload")
        content = message.get("content") if isinstance(message, dict) else None
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if record.get("type") == "assistant" and item.get("type") == "tool_use":
                calls.append({"id": item.get("id"), "commands": command_fields(item.get("input"))})
            elif record.get("type") == "user" and item.get("type") == "tool_result" and item.get("tool_use_id"):
                outputs[item["tool_use_id"]] = output_text(item.get("content"))
        if record.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        kind, call_id = payload.get("type"), payload.get("call_id")
        if kind == "function_call":
            try:
                calls.append({"id": call_id, "commands": command_fields(json.loads(payload.get("arguments") or "{}"))})
            except ValueError:
                opaque += 1
        elif kind == "local_shell_call":
            calls.append({"id": call_id, "commands": command_fields(payload.get("action"))})
        elif kind == "custom_tool_call" and payload.get("name") == "exec":
            source = payload.get("input")
            found, hidden_calls = wrapped_commands(source) if isinstance(source, str) else ([], 1)
            calls.append({"id": call_id, "commands": found})
            opaque += hidden_calls
        elif kind in ("function_call_output", "custom_tool_call_output") and call_id:
            outputs[call_id] = output_text(payload.get("output"))
    return {"calls": calls, "outputs": outputs, "opaque": opaque} if parsed else None


def executed_commands(path: Path) -> tuple[list[str], int] | None:
    """Commands a transcript asked a tool to run, and the opaque calls it made."""
    parsed = transcript_calls(path)
    return None if parsed is None else ([c for call in parsed["calls"] for c in call["commands"]], parsed["opaque"])


def runtime_renderers(prompts: Path, prepared: dict[str, str]) -> tuple[set[str], int]:
    """Renderer revisions of prompts the CLI wrote after preparation, and records it could not read."""
    found, unreadable = set(), 0
    for path in sorted(prompts.iterdir()) if prompts.is_dir() else []:
        if not path.is_file() or prepared.get(path.name) == sha(path.read_bytes()):
            continue
        if path.suffix == ".txt":
            found |= set(RENDERER.findall(path.read_text(errors="replace")))
        elif path.suffix == ".json":
            try:
                found.add(str(json.loads(path.read_text())["renderer_revision"]))
            except (ValueError, KeyError, TypeError):
                unreadable += 1
    return found, unreadable


def provenance(prompts: Path, prepared: dict[str, str], transcripts: list[Path], pinned: str, launcher: str,
               observations: dict, d: dict) -> dict:
    """Whether the roles actually ran the pinned CLI: runtime prompts the CLI wrote, and executed commands.

    A bare `docket` counts only where the same transcript ran `readlink -f "$(command -v docket)"` and its
    paired output named the pinned launcher; a pane's PATH does not decide what a harness tool shell resolves.
    """
    renderers, unreadable = runtime_renderers(prompts, prepared)
    foreign = pinned_runs = bare = proven_bare = foreign_resolution = unparsed = opaque = doc_mentions = skill_docs = 0
    for path in transcripts:
        text = path.read_bytes().decode("utf-8", "replace")
        skill_docs += sum(text.count(m) for m in d["installed_skill_markers"])
        doc_mentions += sum(text.count(m) for m in d["foreign_cli_markers"])
        if (parsed := transcript_calls(path)) is None:
            unparsed += 1
            continue
        opaque += parsed["opaque"]
        here, proofs = 0, []
        for call in parsed["calls"]:
            for command in call["commands"]:
                foreign += any(m in command for m in d["foreign_cli_markers"])
                pinned_runs += launcher in command
                here += bool(BARE_DOCKET.search(command.replace(launcher, "")))
            if len(call["commands"]) == 1 and RESOLVE_DOCKET.fullmatch(call["commands"][0].strip()):
                result = parsed["outputs"].get(call["id"]) if call["id"] else None
                proofs.append(None if result is None else launcher in (x.strip() for x in result.splitlines()))
        bare += here
        if here and proofs and all(proofs):
            proven_bare += here
        elif here and False in proofs:
            foreign_resolution += 1
    resolved = transcripts and not unparsed and not opaque and pinned_runs and bare == proven_bare
    if foreign or foreign_resolution or renderers - {pinned}:
        status = "foreign"
    elif renderers and not unreadable and resolved:
        status = "pinned"
    else:
        status = "unknown"
    return {"status": status, "pinned_renderer": pinned, "renderers": sorted(renderers),
            "unreadable_prompt_records": unreadable,
            "foreign_cli_executions": foreign, "pinned_cli_executions": pinned_runs, "bare_invocations": bare,
            "bare_proven_pinned": proven_bare, "bare_resolved_elsewhere": foreign_resolution,
            "unparsed_transcripts": unparsed, "opaque_tool_calls": opaque, "foreign_cli_doc_mentions": doc_mentions,
            "installed_skill_doc_mentions": skill_docs, "pane_path_verified": observations.get("pane_path_verified") is True}


def check_frozen(study: dict) -> None:
    if (now := frozen_hashes()) != study["frozen"]:
        changed = sorted(k for k in now if now[k] != study["frozen"].get(k))
        die(f"frozen study inputs changed since preparation: {', '.join(changed)}")


def hidden(tree: Path, task_id: str, out: Path, env: dict[str, str]) -> dict:
    run(["node", str(HERE / "hidden" / "check.mjs"), "--checkout", str(tree), "--task", task_id,
         "--out", str(out)], tree, env, check=False)
    if not out.exists():
        return {"status": "harness-failed", "checks": []}
    return load_json(out)


def production_tests(ui: Path, tests: str, log: Path, env: dict[str, str]) -> dict:
    """The companion's ordinary unit tests (`npm test`), run without npm's user state."""
    py = run([sys.executable, "-B", "-m", "unittest", "discover", "-s", tests, "-p", "test_*.py"], ui, env, check=False)
    node = run(["node", "--test", *sorted(str(p.relative_to(ui)) for p in (ui / tests).glob("*.test.mjs"))],
               ui, env, check=False)
    log.write_text(f"$ python unittest {tests}\n{py.stdout}{py.stderr}\n$ node --test {tests}\n{node.stdout}{node.stderr}")
    log.chmod(0o600)
    return {"python_exit": py.returncode, "node_exit": node.returncode,
            "passed": py.returncode == 0 and node.returncode == 0}


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    out = {}
    if lines[:1] == ["---"]:
        for line in lines[1:]:
            if line == "---":
                break
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def recipe_verification(bundle: Path | None, manifest_data: dict | None, selected: str) -> dict:
    """Whether the approved bundle itself froze a passing, unedited run of the selected recipe."""
    if not selected:
        return {"verified": False, "reason": "no recipe selected"}
    if manifest_data is None or bundle is None:
        return {"verified": False, "reason": "no approved bundle"}
    section = manifest_data.get("recipe") or {}
    if section.get("reference") != selected or section.get("run") != "yes":
        return {"verified": False, "reason": f"recipe not run in the approved bundle ({section.get('reason', 'absent')})"}
    if section.get("changed_in_patch"):
        return {"verified": False, "reason": "the round's patch edited the recipe"}
    entry = next((e for e in section.get("evidence", []) if e.get("name") == "result.json"), None)
    path = bundle / str(entry.get("file", "")) if entry else None
    if not entry or not path.is_file() or f"sha256:{sha(path.read_bytes())}" != entry.get("sha256"):
        return {"verified": False, "reason": "frozen recipe result missing or altered"}
    result = json.loads(path.read_bytes())
    if result.get("status") != "passed" or result.get("negative_control"):
        return {"verified": False, "reason": f"frozen recipe result {result.get('status')}"}
    return {"verified": True, "reason": ""}


# Runs inside the pinned CLI copy against the original candidate: Docket's own readers decide
# whether the approval is intact, so no looser local gate can call it intact.
INTEGRITY = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from docket_cli.bundles import bundle_dir, latest_bundle
from docket_cli.five_role import run_complete_event
from docket_cli.freeze import current_bundle
from docket_cli.frontmatter import parse
from docket_cli.paths import decisions, run_dir
from docket_cli.recipes import recipe_approval_problem
from docket_cli.state import state_of
d, owner = run_dir(sys.argv[2]), sys.argv[3]
rnd, state = state_of(d, owner)
frozen, problems = current_bundle(d, owner, rnd)
entry = latest_bundle(d, owner, rnd)
selected = parse((d / f"{owner}-task.mdx").read_text())[0].get("verification_recipe", "")
found = decisions(d, owner)
decision = parse(found[-1].read_text())[0] if found else {}
print(json.dumps({
    "round": rnd, "state": state, "run_complete": run_complete_event(d) is not None,
    "bundle_digest": (frozen or {}).get("digest", ""), "bundle_problems": problems,
    "bundle_path": str(bundle_dir(d, owner, entry).relative_to(d)) if entry and frozen else "",
    "decision_verdict": decision.get("verdict", ""), "decision_round": decision.get("round", ""),
    "decision_bundle": decision.get("bundle_digest", ""), "selected_recipe": selected,
    "recipe_problem": recipe_approval_problem(frozen, selected) if frozen else "no intact current bundle"}))
"""


def cli_digest(tools: Path) -> str:
    """Digest of the pinned CLI source; bytecode caches are not part of what runs."""
    return digest({k: v for k, v in manifest(tools, everything=True).items()
                   if "__pycache__/" not in f"/{k}" and not k.endswith(".pyc")})


def approval_integrity(tools: Path, expected_cli: str, project: Path, run_name: str, env: dict[str, str]) -> dict:
    """Intact only when the pinned CLI's own readers say so; anything unproven stays unknown."""
    if cli_digest(tools) != expected_cli:
        return {"status": "unknown", "reason": "the pinned CLI copy no longer matches its prepared digest"}
    result = run([sys.executable, "-c", INTEGRITY, str(tools), run_name, RUN_OWNER], project, env, check=False)
    if result.returncode:
        return {"status": "unknown", "reason": f"pinned integrity reader failed: {(result.stderr or result.stdout).strip()[-300:]}"}
    facts = json.loads(result.stdout)
    if facts["bundle_problems"]:
        status = "damaged"
    elif (facts["state"] == "approved" and facts["run_complete"] and facts["decision_verdict"] == "approved"
          and facts["decision_round"] == str(facts["round"]) and facts["decision_bundle"] == facts["bundle_digest"]):
        status = "intact"
    else:
        status = "not-approved"
    return {"status": status, **facts}


def docket_outcome(state: Path, run_name: str, integrity: dict) -> dict:
    """Display facts from the run's frozen files; approval and recipe proof come from `integrity`."""
    d = state / "runs" / run_name
    reports = sorted(d.glob(f"{RUN_OWNER}-report-*.mdx"))
    decisions = sorted(d.glob(f"{RUN_OWNER}-decision-*.mdx"))
    task_file = d / f"{RUN_OWNER}-task.mdx"
    task_meta = frontmatter(task_file) if task_file.is_file() else {}
    selected = task_meta.get("verification_recipe", "")
    status = integrity.get("status", "unknown")
    approved = None if status == "unknown" else status == "intact"
    if approved and integrity.get("recipe_problem"):
        recipe = {"verified": False, "reason": integrity["recipe_problem"]}
    elif approved:
        bundle = d / integrity["bundle_path"]
        recipe = recipe_verification(bundle, load_json(bundle / "bundle.json"), selected)
    else:
        recipe = {"verified": False, "reason": f"approval integrity {status}"}
    return {"rounds": len(reports), "report_status": [frontmatter(p).get("status", "") for p in reports],
            "verdicts": [frontmatter(p).get("verdict", "") for p in decisions],
            "approved": approved, "integrity": integrity,
            "actual_implementor_model": task_meta.get("actual_model", ""),
            "recipe_selected": selected, "recipe": recipe}


def helper_runs(state: Path) -> int:
    """Recipe helper results anywhere in the run's private state, frozen or not."""
    count = 0
    for path in state.rglob("result.json"):
        try:
            data = load_json(path)
        except (ValueError, OSError):
            continue
        count += isinstance(data, dict) and {"invocation", "checks", "cleanup"} <= data.keys()
    return count


def exposure(sources: dict[str, list[Path]], markers: dict[str, list[str]]) -> dict:
    """Marker counts only; transcript content is never copied."""
    out = {}
    for kind, paths in sources.items():
        counts = {group: {m: 0 for m in ms} for group, ms in markers.items()}
        for path in paths:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 50_000_000:
                continue
            text = path.read_bytes().decode("utf-8", "replace")
            for group, ms in markers.items():
                for m in ms:
                    counts[group][m] += text.count(m)
        out[kind] = counts
    return out


REQUIRED_ROLES = ("implementor", "reviewer")
CATEGORIES = ("input", "cache_read", "cache_write", "output", "reasoning", "total", "calls")


def count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def usage_summary(path: Path | None) -> dict:
    """Execution tokens from `docket usage --json`; any gap leaves every total unknown, never zero."""
    unknown = {c: None for c in CATEGORIES}
    if not path:
        return {"telemetry": "unknown", "gaps": ["no usage file supplied"], "models": {}, **unknown}
    rows = load_json(path)
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return {"telemetry": "unknown", "gaps": ["usage file is not a list of session rows"], "models": {}, **unknown}
    gaps, by_role = [], {}
    for r in rows:
        roles = r.get("roles") if isinstance(r.get("roles"), list) else []
        if len(roles) != 1:
            gaps.append(f"session {r.get('session_id', '?')} attributed to roles {roles or 'none'}")
        for role in roles:
            by_role.setdefault(role, []).append(r)
        if r.get("telemetry") != "measured":
            gaps.append(f"{'+'.join(roles) or 'unknown'}: telemetry {r.get('telemetry', 'missing')}")
    for role in REQUIRED_ROLES:
        if len(by_role.get(role, [])) != 1:
            gaps.append(f"{role}: {len(by_role.get(role, []))} sessions, expected exactly one")
    totals = {}
    for c in CATEGORIES:
        values = [count(r.get(c)) for r in rows]
        if None in values:
            gaps.append(f"{c}: missing or invalid in a session row")
        totals[c] = None if gaps or None in values else sum(values)
    if gaps:
        totals = unknown
    models = {role: sorted({m for r in rs for m in (r.get("models") or []) if m != "<synthetic>"})
              for role, rs in by_role.items()}
    return {"telemetry": "measured" if not gaps else "unknown", "gaps": sorted(set(gaps)),
            "sessions": len(rows), "models": models, **totals}


# Runs inside the pinned CLI copy so the parser and categories are exactly Docket's own.
MEASURE = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from docket_cli import sessions
run, request = Path(sys.argv[2]), json.loads(sys.argv[3])
refs = sessions.run_harness_sessions(run)
out = {}
for role, (harness, files) in request.items():
    noted = [r for r in refs if role in r["roles"]]
    parse = {"claude": sessions.claude_session_usage, "codex": sessions.codex_session_usage}[harness]
    calls, times, _ = {"claude": sessions.claude_calls, "codex": sessions.codex_calls}[harness]([Path(f) for f in files])
    out[role] = {"usage": parse([Path(f) for f in files]), "first": min(times, default=""), "last": max(times, default=""),
                 "noted": [{"harness": r["harness"], "session_id": r["session_id"], "first_seen": r["first_seen"],
                            "until": sessions.session_until(run, r)} for r in noted]}
print(json.dumps(out))
"""


def instant(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def transcript_cwds(harness: str, files: list[Path]) -> set[str]:
    cwds = set()
    for path in files:
        for line in path.read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if harness == "claude" and record.get("cwd"):
                cwds.add(str(record["cwd"]))
            if harness == "codex" and record.get("type") == "session_meta":
                cwds.add(str((record.get("payload") or {}).get("cwd", "")))
    return cwds


def full_session_problems(harness: str, files: list[str], m: dict, prepared_at: str, project: Path) -> list[str]:
    """Why one role transcript is not a whole fresh session of that role inside this candidate."""
    problems = []
    if len(m["noted"]) != 1:
        problems.append(f"{len(m['noted'])} noted sessions")
    else:
        ref = m["noted"][0]
        if ref["harness"] != harness or not all(ref["session_id"] in f for f in files):
            problems.append("transcript is not the noted session")
        first, last = instant(m["first"]), instant(m["last"])
        joined, until = instant(ref["first_seen"]), instant(ref["until"])
        if None in (first, joined) or not (instant(prepared_at) <= first <= joined):
            problems.append(f"first activity {m['first'] or 'none'} not between preparation and registration")
        if None in (last, until) or last > until:
            problems.append(f"activity until {m['last'] or 'none'} beyond the run end {ref['until'] or '(open)'}")
    cwds = transcript_cwds(harness, [Path(f) for f in files])
    if not cwds or any(not inside(Path(c), project) for c in cwds):
        problems.append(f"working directories {sorted(cwds) or 'unrecorded'} outside the candidate project")
    if m["usage"].get("telemetry") != "measured":
        problems.append(f"telemetry {m['usage'].get('telemetry')}")
    return problems


def full_sessions(specs: list[str], tools: Path, run_dir: Path, project: Path, prepared_at: str,
                  out: Path, env: dict[str, str]) -> dict:
    """Whole fresh role sessions, measured only when their bounds and ownership are proven."""
    unknown = {"telemetry": "unknown", "roles": {}, **{c: None for c in CATEGORIES}}
    request: dict[str, tuple[str, list[str]]] = {}
    for spec in specs:
        role, _, rest = spec.partition("=")
        harness, _, value = rest.partition(":")
        path = absolute(value, "--full-session path")
        if role not in REQUIRED_ROLES or harness not in ("claude", "codex") or not path.is_file():
            die("--full-session must be ROLE=HARNESS:/abs/transcript with role implementor|reviewer, harness claude|codex")
        if request.setdefault(role, (harness, []))[0] != harness:
            die(f"--full-session gives two harnesses for {role}")
        request[role][1].append(str(path))
        if harness == "claude":  # Unexpected delegated work still costs tokens; it is counted and flagged.
            request[role][1].extend(sorted(str(p) for p in (path.parent / path.stem / "subagents").glob("*.jsonl")))
    if not request:
        return {**unknown, "gaps": ["no full-session transcript supplied"]}
    copies = out / "full-sessions"
    copies.mkdir(mode=0o700)
    raw = {}
    for role, (_, files) in request.items():
        for n, f in enumerate(files):
            target = copies / f"{role}-{n}{Path(f).suffix}"
            shutil.copyfile(f, target)
            target.chmod(0o600)
            raw[target.name] = {"sha256": sha(target.read_bytes()), "subagent": "/subagents/" in f}
    measured = json.loads(run([sys.executable, "-c", MEASURE, str(tools), str(run_dir), json.dumps(request)],
                              run_dir, env).stdout)
    gaps, roles = [], {}
    for role in REQUIRED_ROLES:
        if role not in measured:
            gaps.append(f"{role}: no full-session transcript")
            continue
        m, (harness, files) = measured[role], request[role]
        usage = m["usage"]
        problems = full_session_problems(harness, files, m, prepared_at, project)
        roles[role] = {"proven": not problems, "problems": problems, "first": m["first"], "last": m["last"],
                       "files": len(files), "delegated_transcripts": sum("/subagents/" in f for f in files), **{c: usage.get(c) for c in CATEGORIES},
                       "models": usage.get("models", []),
                       "efforts": codex_efforts([Path(f) for f in files]) if harness == "codex" else []}
        gaps += [f"{role}: {p}" for p in problems]
    if gaps:
        return {**unknown, "roles": roles, "gaps": gaps, "raw": raw}
    totals = {c: sum(count(roles[r][c]) for r in REQUIRED_ROLES) if all(count(roles[r][c]) is not None for r in REQUIRED_ROLES)
              else None for c in CATEGORIES}
    return {"telemetry": "measured" if None not in totals.values() else "unknown", "roles": roles, "gaps": [],
            "raw": raw, **totals}


def delegated(full: dict) -> int:
    """Subagent transcripts found beside a role session: counted, but a deviation from one visible session."""
    return sum(int(r.get("delegated_transcripts") or 0) for r in (full.get("roles") or {}).values())


def identity(requested: dict, models: dict, efforts: dict) -> dict:
    """Requested models and effort versus what each role's own proven session recorded.

    Codex records effort per turn; Claude transcripts do not, so its effort is reported as unobservable.
    """
    out = {}
    for role in REQUIRED_ROLES:
        want, seen = requested[f"{role}_model"], models.get(role)
        effort = efforts.get(role) if requested[f"{role}_harness"] == "codex" else "unobservable"
        model_ok = None if not seen else seen == [want]
        effort_ok = True if effort == "unobservable" else (None if not effort else effort == [requested[f"{role}_effort"]])
        out[role] = {"requested": want, "observed": seen, "requested_effort": requested[f"{role}_effort"],
                     "observed_effort": effort,
                     "matches": None if None in (model_ok, effort_ok) else model_ok and effort_ok}
    return out


def codex_efforts(files: list[Path]) -> list[str]:
    efforts = set()
    for path in files:
        for line in path.read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("type") == "turn_context":
                if effort := (record.get("payload") or {}).get("effort"):
                    efforts.add(str(effort))
    return sorted(efforts)


def materialize(tools: Path, project: Path, run_name: str, rnd: object, dest: Path, env: dict[str, str]) -> dict:
    """The approved round's frozen source, exported by the pinned CLI from a private copy of the run state.

    Git reads the candidate's object store; the private index and objects land in the copy, never the candidate.
    """
    work = Path(tempfile.mkdtemp(prefix="export-", dir=dest.parent))
    copy_tree(project / ".docket", work / ".docket")
    result = run([sys.executable, str(tools / "bin" / "docket"), "bundle", run_name, RUN_OWNER, "--round", str(rnd),
                  "--checkout", str(dest)], work, env, check=False)
    ok = result.returncode == 0 and dest.is_dir()
    return {"status": "materialized" if ok else "failed", "round": rnd,
            "detail": "" if ok else (result.stderr or result.stdout).strip()[-600:]}


def routine_turns(run_state: Path) -> dict:
    """Prompts the conductor actually sent to each role, from its append-only delivery logs."""
    by_role = {}
    for role in REQUIRED_ROLES:
        log = run_state / ".delivery" / role / "log.jsonl"
        try:
            entries = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
            by_role[role] = sum(isinstance(e, dict) and e.get("kind") == "delivered" for e in entries)
        except (OSError, ValueError):
            by_role[role] = None
    return {"by_role": by_role, "total": None if None in by_role.values() else sum(by_role.values())}


def section(body: str, heading: str) -> str:
    match = re.search(rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", body)
    return re.sub(r"(?s)<!--.*?-->", "", match[1]).strip() if match else ""


def defect_problem(run_state: Path, rel: object, approved_round: int | None) -> str:
    """Why a cited artifact is not a defect caught after an edit and before approval; empty when it is."""
    if approved_round is None:
        return "no intact approval to have caught it before"
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or ".." in Path(rel).parts:
        return "not a path inside the graded run state"
    path = run_state / rel
    if not path.is_file() or path.is_symlink():
        return "cited file is not in the graded run state"
    if re.fullmatch(rf"{RUN_OWNER}-decision-\d+\.mdx", rel):
        meta, body = frontmatter(path), path.read_text()
        if meta.get("verdict") != "changes-requested" or not meta.get("bundle_digest"):
            return f"decision verdict {meta.get('verdict') or 'missing'} is not a change request bound to a bundle"
        if not meta.get("round", "").isdigit() or int(meta["round"]) >= approved_round:
            return "change request is not from a round before the approved one"
        return "" if section(body, "Required changes") else "change request names no required change"
    bundle = next((b for b in path.parents if (b / "bundle.json").is_file() and run_state in b.parents), None)
    if path.name != "result.json" or bundle is None or ".bundles" not in Path(rel).parts:
        return "not a frozen recipe result or a change request"
    manifest_data = load_json(bundle / "bundle.json")
    file = path.relative_to(bundle).as_posix()
    entry = next((e for e in (manifest_data.get("recipe") or {}).get("evidence", []) if e.get("file") == file), None)
    if not entry or entry.get("sha256") != f"sha256:{sha(path.read_bytes())}":
        return "recipe result is not recorded unaltered in its bundle"
    result = load_json(path)
    if result.get("negative_control"):
        return "a deliberate negative control is not a caught defect"
    if result.get("status") != "failed":
        return f"recipe result {result.get('status')} is not a behavioral failure"
    if not isinstance(manifest_data.get("round"), int) or manifest_data["round"] >= approved_round:
        return "recipe failure is not from a round before the approved one"
    if not any(r.get("paths") for r in (manifest_data.get("patch") or {}).get("roots", [])):
        return "the failing round changed nothing, so it is a baseline failure"
    return ""


def caught_defects(run_state: Path, cited: object, approved_round: int | None) -> dict:
    """Only cited post-edit recipe failures or change requests before approval count; free text never does."""
    counted, rejected = [], []
    for item in cited if isinstance(cited, list) else ([] if cited is None else [cited]):
        rel = item.get("evidence") if isinstance(item, dict) else item
        if problem := defect_problem(run_state, rel, approved_round):
            rejected.append({"evidence": rel, "reason": problem})
        else:
            counted.append(rel)
    return {"counted": counted, "rejected": rejected}


def prepared_bytes(sanitized: Path, spec: dict, rel: str) -> bytes | None:
    path = sanitized / rel
    if not path.exists():
        return None
    data = path.read_bytes()
    for change in spec["seed"]:
        if change["path"] == rel:
            data = data.decode().replace(change["old"], change["new"]).encode()
    return data


def patch(sanitized: Path, spec: dict, tree: Path, paths: list[str]) -> str:
    chunks = []
    for rel in paths:
        old, new = prepared_bytes(sanitized, spec, rel), (tree / rel).read_bytes() if (tree / rel).exists() else None
        try:
            a = old.decode().splitlines(keepends=True) if old is not None else []
            b = new.decode().splitlines(keepends=True) if new is not None else []
        except UnicodeDecodeError:
            chunks.append(f"Binary file {rel} changed\n")
            continue
        chunks.extend(difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}"))
    return "".join(chunks)


def cmd_grade(a: argparse.Namespace) -> None:
    private = absolute(a.private_root, "--private-root")
    candidates = absolute(a.candidate_root, "--candidate-root")
    study = load_json(private / "study.json")
    check_frozen(study)
    if a.label not in study["candidates"]:
        die(f"unknown label {a.label}")
    c, d = study["candidates"][a.label], design()
    spec = task(c["task"])
    out = private / "grading" / a.label
    if out.exists():
        die(f"{a.label} is already graded; grading output is immutable")
    out.mkdir(parents=True, mode=0o700)
    home = candidates / a.label
    project, live, approved = home / "docket", out / "live", out / "approved"
    tools = home / "tools" / "docket"
    home.chmod(OPEN)
    try:
        def ignore(directory: str, names: list[str]) -> set[str]:
            rel = Path(directory).relative_to(project).as_posix()
            return {n for n in names if skipped(n if rel == "." else f"{rel}/{n}")}

        shutil.copytree(project, live, symlinks=True, ignore=ignore)
        copy_tree(project / ".docket", out / "docket-state")
        copy_tree(project / "ui" / "node_modules", live / "ui" / "node_modules")
        env = private_env(out / "env", a.browsers_path)
        head = run(["git", "rev-parse", "HEAD"], project, env, check=False).stdout.strip()
        integrity = approval_integrity(tools, c["cli_digest"], project, c["run"], env)
        cli_ok = integrity.get("reason") != "the pinned CLI copy no longer matches its prepared digest"
        materialized = materialize(tools, project, c["run"], integrity.get("round"), approved, env) \
            if integrity["status"] == "intact" else {"status": f"not materialized (approval {integrity['status']})"}
        full = full_sessions(a.full_session, tools, out / "docket-state" / "runs" / c["run"],
                             project, study["prepared_at"], out, env) if cli_ok else \
            {"telemetry": "unknown", "roles": {}, "gaps": ["pinned CLI copy changed; not executed"],
             **{k: None for k in CATEGORIES}}
    finally:
        home.chmod(SEAL)
    # Behavior is graded on the approved bytes; the live tree is only compared with them.
    tree = approved if materialized["status"] == "materialized" else live
    if tree == approved:
        copy_tree(live / "ui" / "node_modules", approved / "ui" / "node_modules")
    prepared, now, current = load_json(private / "launch" / a.label / "manifest.json"), manifest(tree), manifest(live)
    added = sorted(set(now) - set(prepared))
    deleted = sorted(set(prepared) - set(now))
    modified = sorted(k for k in set(now) & set(prepared) if now[k] != prepared[k])
    changed = added + modified + deleted
    drift = {"added": sorted(set(current) - set(now)), "deleted": sorted(set(now) - set(current)),
             "modified": sorted(k for k in set(now) & set(current) if now[k] != current[k])} if tree == approved else None
    sanitized = private / "source" / "tree"
    result = hidden(tree, c["task"], out / "hidden.json", env)
    candidate_tests = production_tests(tree / "ui", "tests", out / "candidate-tests.log", env)
    copy_tree(sanitized / "ui" / "tests", tree / "ui" / "pinned-tests")
    pinned_tests = production_tests(tree / "ui", "pinned-tests", out / "pinned-tests.log", env)
    run_state = out / "docket-state" / "runs" / c["run"]
    state_files = [p for p in (out / "docket-state").rglob("*") if p.is_file() and "/objects/" not in p.as_posix()]
    markers = {"recipe": d["recipe_markers"],
               "private": [*d["private_markers"], str(private), *[x for x in study["candidates"] if x != a.label]]}
    usage = absolute(a.usage, "--usage") if a.usage else None
    observations = load_json(absolute(a.observations, "--observations")) if a.observations else {}
    if usage:
        shutil.copyfile(usage, out / "usage.json")
    telemetry = usage_summary(usage)
    # A transcript given both explicitly and as a full session is one transcript.
    transcripts, seen = [], set()
    for path in [absolute(p, "--transcript") for p in a.transcript] + \
            sorted((out / "full-sessions").glob("*") if (out / "full-sessions").is_dir() else []):
        if (key := sha(path.read_bytes())) not in seen:
            seen.add(key)
            transcripts.append(path)
    cli = provenance(run_state / ".prompts", c["prepared_prompt_records"], transcripts, c["pinned_renderer"],
                     c["pinned_launcher"], observations, d)
    outcome = docket_outcome(out / "docket-state", c["run"], integrity)
    outcome["helper_runs"] = helper_runs(out / "docket-state")
    helper_executions = sum(sum("verify.mjs" in command for command in (executed_commands(p) or ([], 0))[0])
                            for p in transcripts)
    roles = full.get("roles") or {}
    proven = {r: v for r, v in roles.items() if v.get("proven")}
    hidden_status = result.get("status")
    graded_runtime = runtime(live / "ui" / "node_modules", a.browsers_path, env)
    grade = {"label": a.label, "task": c["task"], "candidate_head": head, "prepared_commit": c["commit"],
             "graded_tree": "approved" if tree == approved else "live", "materialized": materialized,
             "live_drift": drift,
             "changes": {"added": added, "modified": modified, "deleted": deleted,
                         "test_files": [p for p in changed if "/tests/" in f"/{p}" or "test" in Path(p).name],
                         "docket_cli": [p for p in changed if p.startswith("skills/docket/")],
                         "recipe_files": [p for p in changed if p.startswith("ui/verification/")]},
             "node_modules_unchanged": graded_runtime["node_modules_digest"] == study["node_modules_digest"],
             "runtime_matches_proof": graded_runtime == study["sensitivity"]["runtime"],
             "hidden": {"status": hidden_status, "error": result.get("error"),
                        "failed": [x["id"] for x in result.get("checks", []) if not x.get("passed")],
                        "decisive_failed": [x["id"] for x in result.get("checks", []) if not x.get("passed") and x.get("kind") == "decisive"]},
             "candidate_tests": candidate_tests, "pinned_tests": pinned_tests,
             "docket": outcome,
             "turns": routine_turns(run_state),
             "caught_defects": caught_defects(run_state, observations.get("defects_caught_before_approval"),
                                              integrity.get("round") if integrity["status"] == "intact" else None),
             "recipe_helper_executions": helper_executions,
             "exposure": exposure({"docket_state": state_files, "transcripts": transcripts}, markers),
             "transcripts_scanned": len(transcripts),
             "usage": telemetry, "full_sessions": full,
             "identity": identity(d["models"], {r: v["models"] for r, v in proven.items()},
                                  {r: v["efforts"] for r, v in proven.items()}),
             "cli": cli, "observations": observations}
    write_json(out / "grade.json", grade, private=True)
    blind = private / "blind" / a.label
    blind.mkdir(parents=True)
    (blind / "source.patch").write_text(patch(sanitized, spec, tree, changed))
    write_json(blind / "behavior.json", {"label": a.label, "task": c["task"], "objective": spec["objective"],
                                         "criteria": spec["criteria"], "changed_files": changed,
                                         "hidden": result, "pinned_tests_passed": pinned_tests["passed"],
                                         "candidate_tests_passed": candidate_tests["passed"]})
    summary = {"label": a.label, "graded_tree": grade["graded_tree"], "hidden": grade["hidden"],
               "pinned_tests": pinned_tests["passed"], "changed": changed}
    if hidden_status not in ("passed", "failed"):
        summary["rerun"] = (f"hidden checks ended {hidden_status}, an infrastructure result rather than behavior; "
                            "the pair stays inconclusive until they reach a verdict; move this label's grading and blind "
                            "directories aside and grade it again once the runtime works")
    print(json.dumps(summary, indent=2))


def pair_row(task_id: str, labels: dict[str, str], grades: dict, adjudications: dict, budget: float) -> dict:
    arms = ("treatment", "control")
    g = {arm: grades.get(labels.get(arm, "")) for arm in arms}
    row = {"task": task_id, "labels": labels}
    if not all(g.values()):
        return {**row, "graded": False}

    def failures(arm: str) -> set[str]:
        x = g[arm]
        out = {f"hidden:{i}" for i in x["hidden"]["failed"]}
        if not x["pinned_tests"]["passed"]:
            out.add("pinned-tests")
        return out | {f"adjudicated:{i}" for i in adjudications.get(labels[arm], {}).get("escaped_defects", [])}

    f = {arm: failures(arm) for arm in arms}
    obs = {arm: g[arm]["observations"] for arm in arms}
    turns = {arm: count(g[arm]["turns"]["total"]) for arm in arms}
    help_ = {arm: count(obs[arm].get("human_interventions")) for arm in arms}
    tokens = {arm: g[arm]["full_sessions"]["total"] for arm in arms}
    overhead = None if None in tokens.values() or not tokens["control"] else \
        (tokens["treatment"] - tokens["control"]) / tokens["control"]
    unadjudicated = [arm for arm in arms if labels[arm] not in adjudications]
    # A browser that would not start, or no approved tree to check, says nothing about behavior.
    no_verdict = [arm for arm in arms if g[arm]["hidden"]["status"] not in ("passed", "failed")
                  or g[arm]["graded_tree"] != "approved"]
    savings = []
    if f["control"] - f["treatment"]:
        savings.append(f"control escaped {sorted(f['control'] - f['treatment'])} that treatment did not")
    if g["treatment"]["docket"]["rounds"] < g["control"]["docket"]["rounds"]:
        savings.append("fewer correction rounds")
    if None not in help_.values() and help_["treatment"] < help_["control"]:
        savings.append("fewer human interventions")
    savings += [f"caught before approval: {x}" for x in g["treatment"]["caught_defects"]["counted"]]
    contamination = sorted({f"{arm}:{group}:{m}" for arm in arms for source in g[arm]["exposure"].values()
                            for group, ms in source.items() for m, n in ms.items()
                            if n and (group == "private" or arm == "control")})
    control_ran_helper = bool(g["control"]["docket"].get("helper_runs") or g["control"]["recipe_helper_executions"])
    pick = lambda fn: {arm: fn(arm) for arm in arms}
    return {**row, "graded": True,
            "regression": None if unadjudicated or no_verdict else bool(f["treatment"] - f["control"]),
            "unadjudicated": unadjudicated, "hidden_without_verdict": no_verdict,
            "failures": pick(lambda arm: sorted(f[arm])),
            "rounds": pick(lambda arm: g[arm]["docket"]["rounds"]),
            "approved": pick(lambda arm: g[arm]["docket"]["approved"]),
            "integrity": pick(lambda arm: g[arm]["docket"].get("integrity", {}).get("status", "unknown")),
            "graded_tree": pick(lambda arm: g[arm]["graded_tree"]),
            "live_drift": pick(lambda arm: g[arm]["live_drift"]),
            "node_modules_unchanged": pick(lambda arm: g[arm]["node_modules_unchanged"]),
            "runtime_matches_proof": pick(lambda arm: g[arm]["runtime_matches_proof"]),
            "treatment_recipe": g["treatment"]["docket"]["recipe"],
            "control_recipe_selected": bool(g["control"]["docket"]["recipe_selected"]),
            "identity": pick(lambda arm: g[arm]["identity"]),
            "cli": pick(lambda arm: g[arm]["cli"]),
            "routine_model_turns": turns,
            "extra_turn": None if None in turns.values() else turns["treatment"] > turns["control"],
            "execution_tokens": tokens,
            "token_categories": pick(lambda arm: {c: g[arm]["full_sessions"][c] for c in CATEGORIES}),
            "full_session_gaps": pick(lambda arm: g[arm]["full_sessions"]["gaps"]),
            "delegated_transcripts": pick(lambda arm: delegated(g[arm]["full_sessions"])),
            "attributed_tokens_lower_bound": pick(lambda arm: g[arm]["usage"]["total"]),
            "telemetry_gaps": pick(lambda arm: g[arm]["usage"]["gaps"]),
            "overhead": overhead, "human_interventions": help_,
            "setup_interventions": pick(lambda arm: obs[arm].get("setup_interventions")),
            "process_overhead": pick(lambda arm: obs[arm].get("process_overhead")),
            "rejected_defect_citations": g["treatment"]["caught_defects"]["rejected"],
            "unscored_notes": pick(lambda arm: obs[arm].get("operational_saving")),
            "control_ran_recipe_helper": control_ran_helper,
            "contamination": contamination, "savings": savings}


def decide(rows: list[dict], rubric: dict) -> tuple[str, list[str]]:
    """Validity first, then safety and cost limits, then the benefit requirement."""
    graded = [r for r in rows if r.get("graded")]
    if len(graded) < rubric["pairs_required"]:
        return "incomplete", [f"{len(graded)} of {rubric['pairs_required']} pairs graded"]
    invalid = []
    for r in graded:
        if any(c.split(":")[1] == "private" for c in r["contamination"]):
            invalid.append(f"{r['task']}: hidden checks, rubric or mapping exposed to a candidate")
        invalid += [f"{r['task']}: {arm} approval integrity {r['integrity'][arm]}" for arm, ok in r["approved"].items() if not ok]
        if not r["treatment_recipe"]["verified"]:
            invalid.append(f"{r['task']}: treatment was not recipe-verified ({r['treatment_recipe']['reason']})")
        invalid += [f"{r['task']}: {arm} CLI provenance {v['status']} (renderers {v['renderers'] or 'none'}, "
                    f"foreign executions {v['foreign_cli_executions']}, bare {v['bare_invocations']}, "
                    f"opaque calls {v.get('opaque_tool_calls', 0)})"
                    for arm, v in r["cli"].items() if v["status"] != "pinned"]
        invalid += [f"{r['task']}: {arm} delegated work to {n} subagent transcript(s); the protocol is one visible "
                    "session per role" for arm, n in r["delegated_transcripts"].items() if n]
        if r["control_recipe_selected"]:
            invalid.append(f"{r['task']}: control selected the recipe")
        invalid += [f"{r['task']}: {arm} changed its installed dependencies" for arm, ok in r["node_modules_unchanged"].items() if not ok]
        invalid += [f"{r['task']}: {arm} graded with dependencies, Node or browsers other than the proof's"
                    for arm, ok in r["runtime_matches_proof"].items() if not ok]
        invalid += [f"{r['task']}: {arm} {role} model {v['observed'] or 'unknown'}, requested {v['requested']}; "
                    f"effort {v['observed_effort'] or 'unknown'}, requested {v['requested_effort']}"
                    for arm, roles in r["identity"].items() for role, v in roles.items() if not v["matches"]]
    if invalid:
        return "inconclusive", invalid
    overheads = [r["overhead"] for r in graded]
    median = statistics.median(overheads) if None not in overheads else None
    if any(r["regression"] for r in graded):
        return "not-supported", ["observed behavioral regression in the treatment"]
    if any(r["extra_turn"] for r in graded):
        return "not-supported", ["treatment needed an extra routine model turn"]
    if median is not None and median > rubric["token_overhead_budget"]:
        return "not-supported", [f"median execution-token overhead {median:.1%} exceeds the budget"]
    unknown = [name for name, missing in (
        ("regression (adjudication missing or hidden checks without a verdict on the approved tree)",
         any(r["regression"] is None for r in graded)),
        ("routine model turns", any(r["extra_turn"] is None for r in graded)),
        ("full-session execution tokens", median is None)) if missing]
    if unknown:
        return "inconclusive", [f"{name} unknown" for name in unknown]
    if not any(r["savings"] for r in graded):
        return "not-supported", ["tie without a concrete operational saving or caught defect"]
    return "opt-in-supported (directional; two pairs; no default)", []


def cmd_report(a: argparse.Namespace) -> None:
    private = absolute(a.private_root, "--private-root")
    study, key = load_json(private / "study.json"), load_json(private / "key.json")
    check_frozen(study)
    rubric = load_json(HERE / "rubric.json")
    grades = {p.parent.name: load_json(p) for p in (private / "grading").glob("*/grade.json")}
    adjudications = {p.stem: load_json(p) for p in (private / "adjudication").glob("*.json")}
    rows = []
    for t in design()["tasks"]:
        labels = {key["arms"][label]: label for label, c in study["candidates"].items() if c["task"] == t}
        rows.append(pair_row(t, labels, grades, adjudications, rubric["token_overhead_budget"]))
    outcome, reasons = decide(rows, rubric)
    maintenance = load_json(private / "maintenance.json") if (private / "maintenance.json").exists() else None
    report = {"version": 1, "outcome": outcome, "reasons": reasons, "pairs": rows,
              "frozen": study["frozen"], "source": {k: study["source"][k] for k in
                                                    ("revision", "pinned_tree", "sanitized_digest", "excluded_present")},
              "sensitivity": study["sensitivity"],
              "authoring_and_maintenance": maintenance or "unknown (not recorded)"}
    write_json(private / "report.json", report, private=True)
    (private / "report.md").write_text(report_text(report, rubric))
    print(json.dumps({"outcome": outcome, "reasons": reasons}, indent=2))


def report_text(report: dict, rubric: dict) -> str:
    fmt = lambda v: "unknown" if v is None else (f"{v:.1%}" if isinstance(v, float) else str(v))
    lines = ["# Recipe-only comparison report", "",
             f"Outcome: **{report['outcome']}**" + (f" ({'; '.join(report['reasons'])})" if report["reasons"] else ""), "",
             f"Source `{report['source']['revision']}`, sanitized digest `{report['source']['sanitized_digest']}`, "
             f"excluded: {', '.join(report['source']['excluded_present']) or 'none'}.",
             "Two pairs give a directional, opt-in-only finding. Raw tokens are not money.", "",
             f"Recipe coverage: {report['sensitivity']['coverage']}", "",
             "Pre-run sensitivity (frozen at preparation): " + "; ".join(
                 f"{t}: reference {v['reference']}, alternate fix {v['alternate_fix']}, seeded decisive failures "
                 f"{', '.join(v['seeded_decisive_failed'])}, existing UI tests (ui npm test) on seed "
                 f"{'passed' if v['seeded_production_tests_passed'] else 'failed'}, recipe helper exit "
                 f"{v['seeded_recipe_helper']['exit']} ({v['seeded_recipe_helper']['kind']} at {v['seeded_recipe_helper']['phase']}, "
                 f"{v['seeded_recipe_helper']['code'] or v['seeded_recipe_helper']['status']})"
                 for t, v in report["sensitivity"]["tasks"].items()) + ".", "",
             "Execution tokens are whole fresh role sessions with proven bounds; Docket's attributed tokens start at "
             "each role's first in-harness registration and are only a lower bound.",
             "Behavior is graded on the approved round's frozen tree, exported by the pinned CLI; live drift is reported. "
             "Routine turns are the conductor's successful deliveries. A caught defect counts only when it cites a "
             "post-edit recipe failure or change request frozen before approval; free-text notes are listed, never scored.",
             "Limitation: when the treatment's recipe correctly blocks a broken fix and no approval follows, the pair is "
             "inconclusive rather than a win.", "",
             "| Task | Approved T / C | Recipe verified (T) | Failures T / C | Rounds T / C | Turns T / C | Full-session tokens T / C | Attributed lower bound T / C | Overhead | Savings | Contamination |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in report["pairs"]:
        if not r.get("graded"):
            lines.append(f"| {r['task']} | not graded | | | | | | | | | |")
            continue
        pair = lambda k: f"{fmt(r[k]['treatment'])} / {fmt(r[k]['control'])}"
        recipe = "yes" if r["treatment_recipe"]["verified"] else f"no ({r['treatment_recipe']['reason']})"
        lines.append(f"| {r['task']} | {pair('approved')} | {recipe} | "
                     f"{len(r['failures']['treatment'])} / {len(r['failures']['control'])} | "
                     f"{pair('rounds')} | {pair('routine_model_turns')} | {pair('execution_tokens')} | "
                     f"{pair('attributed_tokens_lower_bound')} | "
                     f"{fmt(r['overhead'])} | {'; '.join(r['savings']) or 'none'} | "
                     f"{', '.join(r['contamination']) or 'none'}{' (control ran recipe helper)' if r['control_ran_recipe_helper'] else ''} |")
    lines += ["", "## CLI provenance", ""]
    for r in report["pairs"]:
        for arm, v in (r.get("cli") or {}).items():
            lines.append(f"- {r['task']} {arm}: {v['status']}; runtime renderers {', '.join(v['renderers']) or 'none'} "
                         f"(pinned {v['pinned_renderer']}); pinned executions {v['pinned_cli_executions']}, foreign "
                         f"{v['foreign_cli_executions']}, bare {v['bare_invocations']} (proven pinned {v['bare_proven_pinned']}), "
                         f"opaque calls {v['opaque_tool_calls']}, unparsed transcripts {v['unparsed_transcripts']}; "
                         f"installed-skill doc mentions {v['installed_skill_doc_mentions']}")
    lines += ["", "## Graded trees and evidence notes", ""]
    for r in report["pairs"]:
        if not r.get("graded"):
            continue
        for arm in ("treatment", "control"):
            drift = r["live_drift"][arm]
            lines.append(f"- {r['task']} {arm}: graded {r['graded_tree'][arm]} tree; live drift "
                         + ("not compared" if drift is None else ", ".join(f"{k} {len(v)}" for k, v in drift.items()))
                         + f"; setup interventions {fmt(r['setup_interventions'][arm])}, human interventions "
                         f"{fmt(r['human_interventions'][arm])}; unscored note: {r['unscored_notes'][arm] or 'none'}")
        for item in r["rejected_defect_citations"]:
            lines.append(f"- {r['task']} treatment: citation {item['evidence']!r} not counted ({item['reason']})")
    lines += ["", "## Model identity: requested versus observed", ""]
    for r in report["pairs"]:
        for arm, roles in (r.get("identity") or {}).items():
            for role, v in roles.items():
                lines.append(f"- {r['task']} {arm} {role}: requested `{v['requested']}` at effort {v['requested_effort']}, "
                             f"observed {', '.join(f'`{m}`' for m in v['observed']) if v['observed'] else 'unknown'} at effort "
                             f"{v['observed_effort'] if isinstance(v['observed_effort'], str) else ', '.join(v['observed_effort'] or []) or 'unknown'}")
    lines += ["", "## Authoring and maintenance (reported separately)", "",
              f"`{json.dumps(report['authoring_and_maintenance'])}`", "", "## Frozen rubric", ""]
    lines += [f"- {rule}" for rule in rubric["rules"]]
    return "\n".join(lines) + "\n"


def cmd_prove(a: argparse.Namespace) -> None:
    """Sensitivity proof before any candidate runs: seeds fail, correct behavior passes."""
    d = design()
    source = absolute(a.source_repo, "--source-repo")
    deps = absolute(a.node_modules, "--node-modules")
    out = owned_root(absolute(a.out, "--out"), "--out")
    env = private_env(out / "env", a.browsers_path)
    sanitized = out / "source"
    proof = {"version": 1, "frozen": frozen_hashes(),
             "source": export(source, a.revision, sanitized, d["excluded_paths"], env),
             "runtime": runtime(deps, a.browsers_path, env), "tasks": {}}
    problems = []
    for t in a.task or d["tasks"]:
        spec, record = task(t), {}
        variants = {"reference": [], "seeded": spec["seed"], "alternate-fix": spec["seed"] + spec["alternate_fix"]}
        for name, edits in variants.items():
            base = out / t / name
            tree = base / "tree"
            copy_tree(sanitized, tree)
            edit(tree, edits)
            copy_tree(deps, tree / "ui" / "node_modules")
            result = hidden(tree, t, base / "hidden.json", env)
            record[name] = {"status": result.get("status"), "error": result.get("error"),
                            "failed": [x["id"] for x in result.get("checks", []) if not x["passed"]],
                            "decisive_failed": [x["id"] for x in result.get("checks", [])
                                                if not x["passed"] and x["kind"] == "decisive"],
                            "errors": {x["id"]: x.get("error") for x in result.get("checks", []) if not x["passed"]},
                            "checks": len(result.get("checks", []))}
            if name == "seeded":
                record[name]["production_tests"] = production_tests(tree / "ui", "tests", base / "tests.log", env)
                evidence = base / "recipe-evidence"
                helper = run(["node", "ui/verification/companion-projects/verify.mjs", "--evidence-dir", str(evidence)],
                             tree, env, check=False, timeout=300)
                outcome = load_json(evidence / "result.json") if (evidence / "result.json").is_file() else {}
                record[name]["recipe_helper"] = helper_outcome(helper.returncode, outcome)
        proof["tasks"][t] = record
        problems += proof_problems(t, record)
    proof["problems"] = problems
    write_json(out / "proof.json", proof, private=True)
    print(json.dumps({t: {k: {"status": v["status"], "decisive_failed": v["decisive_failed"]} for k, v in r.items()}
                      for t, r in proof["tasks"].items()} | {"problems": problems}, indent=2))
    if problems:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    browsers = default_browsers()
    pr = sub.add_parser("prepare", help="create sealed candidate projects, Docket runs and the private key")
    pv = sub.add_parser("prove", help="show seeds fail and correct behavior passes the hidden checks")
    for s in (pr, pv):
        s.add_argument("--source-repo", required=True)
        s.add_argument("--revision", required=True)
        s.add_argument("--node-modules", required=True, help="an existing ui/node_modules, copied as-is")
    pr.add_argument("--private-root", required=True)
    pr.add_argument("--candidate-root", required=True)
    pr.add_argument("--proof", required=True, help="proof.json from a passing `prove` with the same inputs")
    pr.add_argument("--seed", help="hex seed for labels and order (default: random, kept in key.json)")
    pv.add_argument("--out", required=True)
    pv.add_argument("--task", action="append", choices=design()["tasks"])
    for s in (pr, pv):
        s.add_argument("--browsers-path", default=browsers)
    g = sub.add_parser("grade", help="snapshot and grade one finished candidate")
    g.add_argument("--private-root", required=True)
    g.add_argument("--candidate-root", required=True)
    g.add_argument("--label", required=True)
    g.add_argument("--transcript", action="append", default=[], help="explicit transcript of this candidate's sessions")
    g.add_argument("--usage", help="`docket usage RUN --json` output for this candidate")
    g.add_argument("--full-session", action="append", default=[],
                   help="ROLE=HARNESS:/abs/transcript of a whole fresh role session")
    g.add_argument("--observations", help="coordinator observations JSON")
    g.add_argument("--browsers-path", default=browsers)
    r = sub.add_parser("report", help="aggregate graded pairs against the frozen rubric")
    r.add_argument("--private-root", required=True)
    a = p.parse_args(argv)
    {"prepare": cmd_prepare, "prove": cmd_prove, "grade": cmd_grade, "report": cmd_report}[a.command](a)


if __name__ == "__main__":
    main()
