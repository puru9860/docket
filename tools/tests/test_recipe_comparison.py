"""Focused tests for the recipe-only comparison tool."""

import ast
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "recipe-comparison"
spec = importlib.util.spec_from_file_location("compare", TOOL / "compare.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)
PINNED = "fe58b1a"
RUBRIC = json.loads((TOOL / "rubric.json").read_text())


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


CAUGHT = {"exit": 1, "status": "failed", "phase": "restart-readback", "code": "PERSISTENCE_MISSING",
          "kind": "saved-list-lost-after-restart"}
PASSED = {"exit": 0, "status": "passed", "phase": "complete", "code": None, "kind": "passed"}


def proof_file(root: Path, helpers: dict | None = None, deps: Path | None = None, **override) -> Path:
    """A proof.json shaped like `prove` output for the pinned revision, current tool inputs and `deps`."""
    work = Path(tempfile.mkdtemp(prefix="proof-", dir=root))
    env = compare.private_env(work / "env")
    source = compare.export(REPO, PINNED, work / "src", compare.design()["excluded_paths"], env)
    helpers = {t: CAUGHT if compare.task(t)["recipe_covered"] else PASSED for t in compare.design()["tasks"]} | (helpers or {})
    proof = {"frozen": compare.frozen_hashes(), "source": source, "problems": [],
             "runtime": compare.runtime(deps or work / "none", compare.default_browsers(), env),
             "tasks": {t: {"reference": {"status": "passed"}, "alternate-fix": {"status": "passed"},
                           "seeded": {"status": "failed", "decisive_failed": ["x"], "production_tests": {"passed": True},
                                      "recipe_helper": helpers[t]}}
                       for t in compare.design()["tasks"]}, **override}
    path = work / "proof.json"
    path.write_text(json.dumps(proof))
    return path


class Temp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="work-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        for root, dirs, _ in os.walk(self.tmp):
            for name in dirs:
                os.chmod(os.path.join(root, name), 0o700)
        self._tmp.cleanup()


class ExportTest(Temp):
    def test_export_drops_experiment_documents_and_records_residual_mentions(self) -> None:
        repo = self.tmp / "repo"
        for rel, text in {"docs/pstack-adoption-plan.md": "rubric", "tools/recipe-comparison/x.json": "{}",
                          "ui/projects.mjs": "code", "README.md": "a paired trial is required"}.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "x")
        before = git(repo, "status", "--porcelain")
        dest = self.tmp / "out"
        record = compare.export(repo, "HEAD", dest, compare.design()["excluded_paths"],
                                compare.private_env(self.tmp / "env"))
        self.assertEqual(["docs/pstack-adoption-plan.md", "tools/recipe-comparison"], record["excluded_present"])
        self.assertFalse((dest / "docs/pstack-adoption-plan.md").exists())
        self.assertFalse((dest / "tools").exists())
        self.assertEqual({"README.md", "ui/projects.mjs"}, set(compare.manifest(dest)))
        self.assertEqual(git(repo, "rev-parse", "HEAD^{tree}"), record["pinned_tree"])
        self.assertIn("differs from the pinned Git tree", record["note"])
        self.assertEqual(before, git(repo, "status", "--porcelain"), "export never touches the measured checkout")
        self.assertEqual([{"path": "README.md", "marker": "paired trial", "count": 1}],
                         compare.residuals(dest, compare.design()["residual_markers"]))

    def test_seed_and_alternate_fix_anchor_exactly_once_at_the_pinned_revision(self) -> None:
        dest = self.tmp / "src"
        compare.export(REPO, PINNED, dest, compare.design()["excluded_paths"], compare.private_env(self.tmp / "env"))
        for name in compare.design()["tasks"]:
            t = compare.task(name)
            tree = self.tmp / name
            compare.copy_tree(dest, tree)
            original = compare.manifest(tree)
            compare.edit(tree, t["seed"])
            seeded = compare.manifest(tree)
            self.assertEqual({e["path"] for e in t["seed"]}, {k for k in original if original[k] != seeded[k]})
            compare.edit(tree, t["alternate_fix"])
            with self.assertRaises(SystemExit):
                compare.edit(tree, t["seed"])


class PrepareTest(Temp):
    @classmethod
    def setUpClass(cls) -> None:
        cls._root = tempfile.TemporaryDirectory(prefix="work-")
        root = Path(cls._root.name)
        deps = root / "deps" / "node_modules"
        (deps / "pkg").mkdir(parents=True)
        (deps / "pkg" / "index.js").write_text("export {};\n")
        cls.private, cls.candidates = root / "private", root / "projects"
        cls.seed = "11" * 16
        cls.proof = proof_file(root, deps=deps)
        compare.main(["prepare", "--source-repo", str(REPO), "--revision", PINNED, "--node-modules", str(deps),
                      "--proof", str(cls.proof),
                      "--private-root", str(cls.private), "--candidate-root", str(cls.candidates), "--seed", cls.seed])
        cls.study = json.loads((cls.private / "study.json").read_text())
        cls.key = json.loads((cls.private / "key.json").read_text())["arms"]
        cls.modes = {label: stat.S_IMODE((cls.candidates / label).stat().st_mode) for label in cls.study["candidates"]}
        for label in cls.study["candidates"]:
            os.chmod(cls.candidates / label, 0o700)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._root.cleanup()

    def labels(self, task: str) -> dict[str, str]:
        return {self.key[l]: l for l, c in self.study["candidates"].items() if c["task"] == task}

    def test_sanitized_source_is_recorded_and_excluded_from_every_candidate(self) -> None:
        source = self.study["source"]
        self.assertEqual(git(REPO, "rev-parse", f"{PINNED}^{{commit}}"), source["revision"])
        self.assertIn("docs/pstack-adoption-plan.md", source["excluded_present"])
        self.assertNotEqual(source["pinned_tree"], source["sanitized_digest"])
        for label in self.study["candidates"]:
            project = self.candidates / label / "docket"
            self.assertFalse((project / "docs/pstack-adoption-plan.md").exists())
            self.assertFalse((project / "tools/recipe-comparison").exists())
            self.assertTrue((project / "ui/verification/companion-projects/recipe.md").exists())
            self.assertTrue((project / "ui/tests/projects.test.mjs").exists())

    def test_pairs_start_identically_and_differ_only_in_verification_floor(self) -> None:
        for task in compare.design()["tasks"]:
            arms = self.labels(task)
            t, c = (self.study["candidates"][arms[a]] for a in ("treatment", "control"))
            self.assertEqual(t["commit"], c["commit"])
            self.assertEqual(t["project_digest"], c["project_digest"])
            self.assertEqual(t["cli_digest"], c["cli_digest"])
            # Only each candidate's own launcher path differs in the plumbing; normalize exactly that.
            plans = {a: json.loads((self.private / "launch" / l / "plan.json").read_text().replace(
                self.study["candidates"][l]["pinned_launcher"], "/LAUNCHER")) for a, l in arms.items()}
            treatment, control = plans["treatment"]["tasks"][0], plans["control"]["tasks"][0]
            self.assertEqual(compare.cli_decision("/LAUNCHER"), control["decision"][0])
            self.assertEqual("ui/verification/companion-projects/recipe.json", treatment.pop("recipe"))
            self.assertNotIn("recipe", control)
            self.assertIn("verify.mjs", treatment.pop("verify"))
            self.assertEqual("node --test ui/tests/projects.test.mjs", control.pop("verify"))
            self.assertEqual(treatment, control)
            self.assertEqual(plans["treatment"]["milestones"], plans["control"]["milestones"])
            self.assertEqual("gpt-6.1-sol", plans["control"]["milestones"][0]["reviewer_model"])
            self.assertEqual("claude-opus-5-5", plans["control"]["milestones"][0]["implementor_model"])
            self.assertEqual("DOCKET_TEST_JOBS=8 tests/test.sh && (cd ui && npm test)", plans["control"]["milestones"][0]["command"],
                             "the repository's own finish gate, once per case, identical in both arms")

    def test_runs_are_real_docket_contracts_with_baselines_and_no_noted_session(self) -> None:
        for label, c in self.study["candidates"].items():
            run = self.candidates / label / "docket" / ".docket" / "runs" / c["run"]
            self.assertEqual("shared", compare.frontmatter(run / "plan.mdx").get("round_context"),
                             "one visible implementor session, no subagents")
            task = compare.frontmatter(run / "T01-task.mdx")
            self.assertEqual("claude-opus-5-5", task["requested_model"])
            self.assertEqual(self.key[label] == "treatment", bool(task.get("verification_recipe")))
            self.assertTrue((run / ".snapshots" / "run.json").is_file())
            self.assertTrue(c["run_baseline"])
            self.assertFalse(list(run.rglob("*session*")), "preparation is never noted as a role session")
            self.assertEqual("", git(self.candidates / label / "docket", "status", "--porcelain"))
            prompt = (self.private / "launch" / label / "implementor-prompt.md").read_text()
            self.assertEqual(c["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
            self.assertEqual(self.key[label] == "treatment", "verification recipe:" in prompt)
            launcher = str(self.candidates / label / "tools" / "docket" / "bin" / "docket")
            self.assertEqual(launcher, c["pinned_launcher"])
            self.assertIn(f"{launcher} submit {c['run']} T01", prompt, "commands address the pinned CLI absolutely")
            self.assertIsNone(compare.BARE_DOCKET.search(prompt))
            self.assertIn(f"renderer: {c['pinned_renderer']}", prompt)
            self.assertIn(compare.cli_decision(launcher), prompt, "the contract names its own CLI for every command")
            for role, suffix in (("implementor", "impl"), ("reviewer", "review")):
                address = hashlib.sha256(f"{run.resolve()}:M1:{role}".encode()).hexdigest()[:16]
                self.assertEqual(f"docket-{address}-{suffix}", c["agent_names"][role], "the conductor's own worker name")
            self.assertEqual(["T01-implementor-01.json"], list(c["prepared_prompt_records"]))

    def test_schedule_drives_the_pinned_conductor_with_absolute_commands_only(self) -> None:
        text = (self.private / "schedule.md").read_text()
        self.assertIsNone(compare.BARE_DOCKET.search(text), "no operator command resolves docket through PATH")
        self.assertNotIn(" launch ", text)
        for label, c in self.study["candidates"].items():
            cli = f"DOCKET_SESSION_CAPTURE=off {c['pinned_launcher']}"
            self.assertIn(f"{cli} conduct {c['run']}`", text)
            self.assertIn(f"{cli} usage {c['run']} --json", text)
            self.assertIn(f"herdr agent start {c['agent_names']['implementor']} --kind claude --pane PANE -- "
                          "--model claude-opus-5-5 --effort high`", text)
            self.assertIn(f"herdr agent start {c['agent_names']['reviewer']} --kind codex --pane PANE -- "
                          "-m gpt-6.1-sol -c model_reasoning_effort=high`", text)
        self.assertIn("Setup interventions are trust or hook-review keypresses", text)

    def test_candidates_carry_no_mapping_rubric_or_private_path(self) -> None:
        forbidden = [str(self.private), "hidden/check.mjs", "recipe-comparison", "rubric.json"]
        for label in self.study["candidates"]:
            home = self.candidates / label
            for path in home.rglob("*"):
                if path.is_file() and not path.is_symlink() and not {"node_modules", ".git"} & set(path.parts):
                    text = path.read_bytes().decode("utf-8", "replace")
                    for word in forbidden:
                        self.assertNotIn(word, text, f"{path} leaks {word}")
            self.assertNotIn("treatment", label)
        self.assertNotIn("treatment", json.dumps(self.study))
        self.assertNotIn("treatment", (self.private / "schedule.md").read_text())
        self.assertEqual(0o600, stat.S_IMODE((self.private / "key.json").stat().st_mode))

    def test_order_is_counterbalanced_and_candidates_start_sealed(self) -> None:
        order = [self.key[l] for l in self.study["sequence"]]
        self.assertEqual(order[:2], list(reversed(order[2:])))
        self.assertEqual({"treatment", "control"}, set(order[:2]))
        self.assertEqual({0}, set(self.modes.values()), "every candidate starts sealed")

    def test_grade_snapshots_reseals_and_writes_an_arm_free_blind_packet(self) -> None:
        label = self.labels("added-projects-lost")["control"]
        project = self.candidates / label / "docket"
        source = project / "ui/server.mjs"
        original = source.read_text()
        self.addCleanup(source.write_text, original)
        fix = compare.task("added-projects-lost")["alternate_fix"][0]
        source.write_text(original.replace(fix["old"], fix["new"]))
        nested = project / "docs/sample/.docket/note.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("fixture")
        self.addCleanup(shutil.rmtree, project / "docs/sample")
        transcript = self.tmp / "session.jsonl"
        transcript.write_text("ran node ui/verification/companion-projects/verify.mjs\nopened rubric.json\n")
        usage = self.tmp / "usage.json"
        usage.write_text(json.dumps([row("implementor"), row("reviewer")]))
        observations = self.tmp / "observations.json"
        observations.write_text(json.dumps({"defects_caught_before_approval": ["the recipe exposed a lost project"],
                                            "operational_saving": "faster"}))
        args = ["grade", "--private-root", str(self.private), "--candidate-root", str(self.candidates),
                "--label", label, "--transcript", str(transcript), "--transcript", str(transcript),
                "--usage", str(usage), "--observations", str(observations)]
        real = compare.frozen_hashes
        compare.frozen_hashes = lambda: {**real(), "rubric.json": "changed"}
        try:
            with self.assertRaises(SystemExit):
                compare.main(args)
        finally:
            compare.frozen_hashes = real
        compare.main(args)
        self.assertEqual(0, stat.S_IMODE((self.candidates / label).stat().st_mode), "graded candidate is resealed")
        os.chmod(self.candidates / label, 0o700)
        g = json.loads((self.private / "grading" / label / "grade.json").read_text())
        self.assertEqual((["ui/server.mjs"], ["docs/sample/.docket/note.md"], []),
                         (g["changes"]["modified"], g["changes"]["added"], g["changes"]["deleted"]))
        self.assertEqual("prerequisite-failed", g["hidden"]["status"], "placeholder dependencies cannot pass hidden checks")
        self.assertEqual(1, g["exposure"]["transcripts"]["private"]["rubric.json"], "a transcript given twice counts once")
        self.assertEqual(1, g["exposure"]["transcripts"]["recipe"]["verify.mjs"])
        self.assertEqual(0, g["recipe_helper_executions"], "reading about the helper is not running it")
        self.assertEqual(("live", None), (g["graded_tree"], g["live_drift"]), "nothing approved to materialize")
        self.assertEqual("not materialized (approval not-approved)", g["materialized"]["status"])
        self.assertEqual({"by_role": {"implementor": None, "reviewer": None}, "total": None}, g["turns"])
        self.assertEqual([], g["caught_defects"]["counted"], "free text is never a caught defect")
        self.assertTrue(g["node_modules_unchanged"])
        self.assertTrue(g["runtime_matches_proof"])
        self.assertEqual(200, g["usage"]["total"])
        self.assertEqual(("not-approved", False), (g["docket"]["integrity"]["status"], g["docket"]["approved"]))
        self.assertTrue((self.private / "grading" / label / "docket-state" / "runs").is_dir())
        blind = self.private / "blind" / label
        patch = (blind / "source.patch").read_text()
        self.assertIn("+" + fix["new"], patch)
        packet = patch + (blind / "behavior.json").read_text()
        for word in ("treatment", "control", "recipe.json", "verify.mjs", "gpt-6.1-sol", "claude-opus"):
            self.assertNotIn(word, packet)
        with self.assertRaises(SystemExit):
            compare.main(args)
        compare.main(["report", "--private-root", str(self.private)])
        report = json.loads((self.private / "report.json").read_text())
        self.assertEqual("incomplete", report["outcome"])
        text = (self.private / "report.md").read_text()
        self.assertIn("Recipe coverage: Covered: the selected recipe's own journey (browser add, same-startup restart) "
                      "detects the seeded defect of added-projects-lost", text)
        self.assertIn("Adjacent: the journey passes on the seeded source of removal-persists", text)
        self.assertIn("added-projects-lost: reference passed, alternate fix passed", text)
        self.assertIn("recipe helper exit 1 (saved-list-lost-after-restart at restart-readback, PERSISTENCE_MISSING)", text)
        self.assertIn("recipe helper exit 0 (passed at complete, passed)", text)

    def test_freezes_proven_sensitivity_and_states_the_coverage_mismatch(self) -> None:
        sensitivity = self.study["sensitivity"]
        self.assertEqual((["added-projects-lost"], ["removal-persists"]), (sensitivity["covered"], sensitivity["adjacent"]))
        self.assertIn("does not disprove the recipe boundary", sensitivity["coverage"])
        self.assertEqual("PERSISTENCE_MISSING", sensitivity["tasks"]["added-projects-lost"]["seeded_recipe_helper"]["code"])
        self.assertEqual(0, sensitivity["tasks"]["removal-persists"]["seeded_recipe_helper"]["exit"])

    def test_refuses_missing_failed_stale_or_foreign_proof(self) -> None:
        deps = self.tmp / "node_modules"
        (deps / "pkg").mkdir(parents=True)
        stale = dict(compare.frozen_hashes(), **{"rubric.json": "older"})
        prerequisite = {"exit": 2, "status": "prerequisite-failed", "phase": "prerequisites", "code": "PREREQUISITE",
                        "kind": "prerequisite"}
        proof = lambda **kw: proof_file(self.tmp, deps=deps, **kw)
        other_deps = self.tmp / "other" / "node_modules"
        (other_deps / "pkg").mkdir(parents=True)
        (other_deps / "pkg" / "index.js").write_text("export const v = 2;\n")
        for n, proof in enumerate((self.tmp / "absent.json", proof(problems=["seed passed"]),
                                   proof(frozen=stale),
                                   proof(source={"revision": "0" * 40, "sanitized_digest": "x"}),
                                   proof(helpers={"added-projects-lost": PASSED}),
                                   proof(helpers={"added-projects-lost": prerequisite}),
                                   proof(helpers={"added-projects-lost": {**CAUGHT, "kind": "other-failure"}}),
                                   proof(helpers={"added-projects-lost": {**CAUGHT, "exit": 2}}),
                                   proof(helpers={"removal-persists": CAUGHT}),
                                   proof_file(self.tmp, deps=other_deps))):
            with self.assertRaises(SystemExit):
                compare.main(["prepare", "--source-repo", str(REPO), "--revision", PINNED, "--node-modules", str(deps),
                              "--proof", str(proof), "--private-root", str(self.tmp / f"p{n}"),
                              "--candidate-root", str(self.tmp / f"w{n}")])
            self.assertFalse((self.tmp / f"w{n}").exists() and any((self.tmp / f"w{n}").iterdir()))

    def test_full_sessions_use_dockets_own_parsers_and_keep_raw_provenance(self) -> None:
        label = self.labels("removal-persists")["control"]
        home = self.candidates / label
        project = home / "docket"
        run_dir = self.tmp / "run"
        shutil.copytree(project / ".docket" / "runs" / "removal-persists", run_dir)
        prepared = self.study["prepared_at"]
        (run_dir / ".harness-sessions.jsonl").write_text(
            json.dumps({"harness": "claude", "session_id": "aaa", "role": "implementor", "owner": "T01", "at": "2099-01-01T00:00:05Z"}) + "\n"
            + json.dumps({"harness": "codex", "session_id": "bbb", "role": "reviewer", "owner": "T01", "at": "2099-01-01T00:10:05Z"}) + "\n")
        claude = self.tmp / "aaa.jsonl"
        claude.write_text(json.dumps({"type": "assistant", "timestamp": "2099-01-01T00:00:01.5Z", "cwd": str(project),
                                      "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {
                                          "input_tokens": 10, "cache_creation_input_tokens": 5,
                                          "cache_read_input_tokens": 100, "output_tokens": 20}}}) + "\n")
        sub = self.tmp / "aaa" / "subagents" / "agent-1.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text(json.dumps({"type": "assistant", "timestamp": "2099-01-01T00:00:03Z", "cwd": str(project / "ui"),
                                   "message": {"id": "s1", "model": "claude-opus-5-5", "usage": {
                                       "input_tokens": 1, "cache_creation_input_tokens": 0,
                                       "cache_read_input_tokens": 0, "output_tokens": 2}}}) + "\n")
        codex = self.tmp / "rollout-bbb.jsonl"
        codex.write_text("\n".join(json.dumps(r) for r in (
            {"type": "session_meta", "timestamp": "2099-01-01T00:10:00Z", "payload": {"cwd": str(project)}},
            {"type": "turn_context", "timestamp": "2099-01-01T00:10:01Z", "payload": {"model": "gpt-6.1-sol", "effort": "high"}},
            {"type": "event_msg", "timestamp": "2099-01-01T00:10:02Z", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 200, "cached_input_tokens": 150, "cache_write_input_tokens": 0,
                                      "output_tokens": 30, "reasoning_output_tokens": 12, "total_tokens": 230}}}})) + "\n")
        out = self.tmp / "grading"
        out.mkdir()
        result = compare.full_sessions([f"implementor=claude:{claude}", f"reviewer=codex:{codex}"], home / "tools" / "docket",
                                       run_dir, project, prepared, out, compare.private_env(self.tmp / "env"))
        impl, rev = result["roles"]["implementor"], result["roles"]["reviewer"]
        self.assertEqual((11, 5, 100, 22, 138, ["claude-opus-5-5"], 2),
                         (impl["input"], impl["cache_write"], impl["cache_read"], impl["output"], impl["total"], impl["models"],
                          impl["files"]), "unexpected delegated work is still counted")
        self.assertEqual((1, 0, 1), (impl["delegated_transcripts"], rev["delegated_transcripts"],
                                     compare.delegated(result)), "and flagged as a deviation")
        self.assertEqual((50, 150, 30, 12, 230, ["gpt-6.1-sol"], ["high"]),
                         (rev["input"], rev["cache_read"], rev["output"], rev["reasoning"], rev["total"], rev["models"],
                          rev["efforts"]))
        self.assertEqual(["activity until 2099-01-01T00:00:03Z beyond the run end (open)"], impl["problems"],
                         "an open run cannot bound the session, so nothing is presented as full execution")
        self.assertEqual(("unknown", None), (result["telemetry"], result["total"]))
        self.assertEqual({"implementor-0.jsonl", "implementor-1.jsonl", "reviewer-0.jsonl"}, set(result["raw"]))
        self.assertTrue(result["raw"]["implementor-1.jsonl"]["subagent"])
        self.assertEqual(hashlib.sha256(claude.read_bytes()).hexdigest(), result["raw"]["implementor-0.jsonl"]["sha256"])
        missing = compare.full_sessions([], home / "tools" / "docket", run_dir, project, prepared, self.tmp, {})
        self.assertEqual((["no full-session transcript supplied"], None), (missing["gaps"], missing["total"]))
        with self.assertRaises(SystemExit):
            compare.full_sessions([f"planner=claude:{claude}"], home / "tools" / "docket", run_dir, project, prepared, self.tmp, {})

    def test_refuses_unsafe_roots(self) -> None:
        deps = self.tmp / "node_modules"
        deps.mkdir()
        base = ["prepare", "--source-repo", str(REPO), "--revision", PINNED, "--node-modules", str(deps),
                "--proof", str(self.proof)]
        (self.tmp / "busy").mkdir()
        (self.tmp / "busy" / "x").write_text("x")
        for private, candidates in ((self.tmp / "p", self.tmp / "comparison-runs"),
                                    (self.tmp / "busy", self.tmp / "work"),
                                    (self.tmp / "p", self.tmp / "p" / "work"),
                                    (REPO / ".docket" / "p", self.tmp / "work"),
                                    ("relative", self.tmp / "work")):
            with self.assertRaises(SystemExit):
                compare.main([*base, "--private-root", str(private), "--candidate-root", str(candidates)])
        self.assertFalse((self.tmp / "work").exists())
        self.assertFalse((self.tmp / "p").exists())
        with self.assertRaises(SystemExit):
            compare.main(["prepare", "--source-repo", str(REPO), "--revision", PINNED, "--node-modules", str(self.tmp / "busy"),
                          "--proof", str(self.proof),
                          "--private-root", str(self.tmp / "q"), "--candidate-root", str(self.tmp / "w2")])


def row(role: str, total: int = 100, telemetry: str = "measured", model: str = "", **extra) -> dict:
    model = model or {"implementor": "claude-opus-5-5", "reviewer": "gpt-6.1-sol"}[role]
    return {"roles": [role], "session_id": role, "telemetry": telemetry, "models": [model],
            "input": 10, "cache_read": 50, "cache_write": 20, "output": 15, "reasoning": 5, "total": total, "calls": 4, **extra}


class UsageTest(Temp):
    def summary(self, rows: object) -> dict:
        path = self.tmp / "usage.json"
        path.write_text(json.dumps(rows))
        return compare.usage_summary(path)

    def test_measured_single_sessions_sum_each_category(self) -> None:
        s = self.summary([row("implementor", 300), row("reviewer", 200)])
        self.assertEqual(("measured", 500, 100, 8), (s["telemetry"], s["total"], s["cache_read"], s["calls"]))
        self.assertEqual({"implementor": ["claude-opus-5-5"], "reviewer": ["gpt-6.1-sol"]}, s["models"])

    def test_gaps_leave_every_total_unknown_never_zero(self) -> None:
        cases = {"missing reviewer": [row("implementor")],
                 "two implementor sessions": [row("implementor"), row("implementor"), row("reviewer")],
                 "unmeasured": [row("implementor", telemetry="empty"), row("reviewer")],
                 "missing category": [row("implementor", reasoning=None), row("reviewer")],
                 "shared session": [{**row("implementor"), "roles": ["implementor", "reviewer"]}],
                 "empty": [], "not rows": {"total": 5}}
        for name, rows in cases.items():
            s = self.summary(rows)
            self.assertEqual("unknown", s["telemetry"], name)
            self.assertTrue(all(s[c] is None for c in compare.CATEGORIES), name)
            self.assertTrue(s["gaps"], name)
        self.assertIsNone(compare.usage_summary(None)["total"])


HELPER = """import json, os, pathlib
out = pathlib.Path(os.environ["DOCKET_RECIPE_EVIDENCE_DIR"])
status = pathlib.Path("recipes/demo/status").read_text().strip()
(out / "result.json").write_text(json.dumps({"invocation": "i", "status": status, "negative_control": None,
                                             "checks": [], "cleanup": {}}))
"""


def real_round(base: Path, cli: list[str], env: dict[str, str], *, status: str = "passed", approve: bool = True) -> Path:
    """One real quick-milestone round selecting a recipe, driven through the pinned CLI with no model."""
    repo = base / "proj"
    recipe = repo / "recipes" / "demo"
    recipe.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("allowed = 1\n")
    (repo / ".gitignore").write_text(".docket/\n")
    (recipe / "recipe.json").write_text(json.dumps({"version": 1, "name": "demo", "instructions": "recipe.md",
                                                    "helpers": ["verify.py"], "evidence": ["result.json"]}))
    (recipe / "recipe.md").write_text("Run the helper.\n")
    (recipe / "verify.py").write_text(HELPER)
    (recipe / "status").write_text(status)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "x")

    def docket(*args: str) -> None:
        subprocess.run([*cli, *args], cwd=repo, env=env, check=True, capture_output=True, text=True)

    def fill(path: Path, pairs: dict[str, str]) -> None:
        text = path.read_text()
        for old, new in pairs.items():
            text = text.replace(old, new)
        path.write_text(text)

    run = repo / ".docket" / "runs" / "rr"
    docket("init", "rr", "--evidence-mode", "git")
    docket("assign", "rr", "T01", "--file", "src/a.py", "--verify", "python3 recipes/demo/verify.py",
           "--recipe", "recipes/demo/recipe.json", "--as", "planner")
    fill(run / "T01-task.mdx", {"# Task T01: <title>": "# Task T01: Feature",
                                "<!-- TODO: one paragraph. What must be true when this is done. -->": "Implement it.",
                                "- [ ] <!-- TODO -->": "- [ ] The feature works."})
    docket("batch", "rr", "--create", "M1", "--members", "T01", "--milestone", "--command", "true", "--as", "planner")
    docket("batch", "rr", "--close", "M1", "--as", "planner")
    fill(run / "T01-scope.mdx", {
        "<!-- TODO: what you inspected and why this is enough context to implement. -->": "Read it.",
        "<!-- TODO: explain every proposed path in frontmatter `files:`. -->": "`src/a.py` changes.",
        "<!-- TODO: useful paths and symbols for implementation and later recovery. -->": "- `src/a.py` - entry.",
        "<!-- TODO: explain the exact frontmatter `verify:` command and what it proves. -->": "Runs the recipe.",
        '<!-- TODO: unresolved discovery uncertainty, or "none". -->': "none"})
    docket("scope", "rr", "T01", "--submit")
    fill(run / "T01-report-01.mdx", {
        "<!-- TODO: what you actually did, 2-4 sentences. No plans, only past tense. -->": "Implemented it.",
        '<!-- TODO: one bullet per change as `path/to/file.py:120` plus a short note. Write "none" if nothing changed. -->':
            "- `src/a.py:1` - implemented it.",
        "- [ ] <!-- TODO -->": "- [x] The feature works.", "- [ ] ": "- [x] ",
        "<!-- TODO: name the registered command and any checks already run. If submit will run\n"
        "     the final command, state that its output will be captured in the frozen bundle.\n"
        "     Never claim a result you did not see. -->": "Command: `python3 recipes/demo/verify.py`; submit captures it."})
    (repo / "src" / "a.py").write_text("allowed = 2\n")
    docket("submit", "rr", "T01", "--as", "implementor")
    docket("batch", "rr", "--verify", "M1", "--as", "implementor")
    if approve:
        docket("batch", "rr", "--approve", "M1", "--as", "reviewer", "--reason", "Checked.")
    return repo


class RealArtifactTest(unittest.TestCase):
    """Approval and recipe proof read from rounds the pinned fe58b1a CLI really froze."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = tempfile.TemporaryDirectory(prefix="work-")
        root = Path(cls._root.name)
        compare.export(REPO, PINNED, root / "src", compare.design()["excluded_paths"], compare.private_env(root / "x"))
        cls.tools = root / "src" / "skills" / "docket"
        cls.env = compare.private_env(root / "env")
        cli = [sys.executable, str(cls.tools / "bin" / "docket")]
        cls.repos = {name: real_round(root / name, cli, cls.env, **kw) for name, kw in (
            ("intact", {}), ("unapproved", {"approve": False}), ("failed-result", {"status": "failed"}),
            ("changes", {"approve": False}))}
        subprocess.run([*cli, "decide", "rr", "T01", "--changes", "--change", "Keep the saved list on restart.",
                        "--as", "reviewer"], cwd=cls.repos["changes"], env=cls.env, check=True, capture_output=True)
        cls.cli_digest = compare.cli_digest(cls.tools)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._root.cleanup()

    def outcome(self, name: str, expected_cli: str | None = None) -> dict:
        repo = self.repos[name]
        integrity = compare.approval_integrity(self.tools, expected_cli or self.cli_digest, repo, "rr", self.env)
        return compare.docket_outcome(repo / ".docket", "rr", integrity)

    def test_intact_real_approval_verifies_the_frozen_recipe_in_wire_format(self) -> None:
        o = self.outcome("intact")
        self.assertEqual("intact", o["integrity"]["status"], o["integrity"])
        self.assertTrue(o["approved"])
        self.assertTrue(o["integrity"]["bundle_digest"].startswith("sha256:"))
        self.assertEqual({"verified": True, "reason": ""}, o["recipe"])

    def test_tampered_frozen_artifact_is_damaged_not_intact(self) -> None:
        repo = self.repos["intact"]
        bundle = repo / ".docket" / "runs" / "rr" / compare.approval_integrity(
            self.tools, self.cli_digest, repo, "rr", self.env)["bundle_path"]
        result = bundle / "recipe" / "evidence" / "result.json"
        original = result.read_bytes()
        self.addCleanup(result.write_bytes, original)
        result.write_bytes(original.replace(b"passed", b"PASSED"))
        o = self.outcome("intact")
        self.assertEqual("damaged", o["integrity"]["status"])
        self.assertIn("no longer matches its recorded artifact recipe/evidence/result.json", " ".join(o["integrity"]["bundle_problems"]))
        self.assertFalse(o["approved"])
        self.assertFalse(o["recipe"]["verified"])

    def test_unapproved_round_and_changed_cli_are_not_intact(self) -> None:
        o = self.outcome("unapproved")
        self.assertEqual(("not-approved", False), (o["integrity"]["status"], o["approved"]))
        changed = self.outcome("intact", expected_cli="other")
        self.assertEqual(("unknown", None), (changed["integrity"]["status"], changed["approved"]))
        self.assertIn("no longer matches its prepared digest", changed["integrity"]["reason"])
        self.assertEqual("approval integrity unknown", changed["recipe"]["reason"])

    def test_docket_approval_of_a_failed_recipe_result_is_not_recipe_verified(self) -> None:
        o = self.outcome("failed-result")
        self.assertEqual("intact", o["integrity"]["status"], "Docket requires evidence presence, not a passing journey")
        self.assertEqual({"verified": False, "reason": "frozen recipe result failed"}, o["recipe"])

    def test_grader_never_writes_to_the_measured_candidate(self) -> None:
        repo = self.repos["intact"]
        before = compare.manifest(repo, everything=True)
        self.outcome("intact")
        self.assertEqual(before, compare.manifest(repo, everything=True))

    def test_the_approved_round_is_materialized_by_the_pinned_cli_without_touching_the_candidate(self) -> None:
        repo = self.repos["intact"]
        (repo / "src" / "a.py").write_text("allowed = 3\n")
        self.addCleanup((repo / "src" / "a.py").write_text, "allowed = 2\n")
        before = compare.manifest(repo, everything=True)
        with tempfile.TemporaryDirectory(prefix="work-") as out:
            result = compare.materialize(self.tools, repo, "rr", 1, Path(out) / "approved", self.env)
            self.assertEqual("materialized", result["status"], result)
            self.assertEqual("allowed = 2\n", (Path(out) / "approved" / "src" / "a.py").read_text(),
                             "the approved bytes, not the post-approval live edit")
            failed = compare.materialize(self.tools, repo, "rr", 7, Path(out) / "none", self.env)
            self.assertEqual("failed", failed["status"])
        self.assertEqual(before, compare.manifest(repo, everything=True), "export state lands in a private copy")

    def test_only_cited_post_edit_failures_or_change_requests_before_approval_count(self) -> None:
        failed = self.repos["failed-result"] / ".docket" / "runs" / "rr"
        result = next(failed.glob(".bundles/T01/01/*/recipe/evidence/result.json")).relative_to(failed).as_posix()
        self.assertEqual({"counted": [result], "rejected": []}, compare.caught_defects(failed, [result], 2))
        same_round = compare.caught_defects(failed, [{"evidence": result}], 1)["rejected"][0]["reason"]
        self.assertIn("not from a round before the approved one", same_round)
        passed = self.repos["intact"] / ".docket" / "runs" / "rr"
        ok = next(passed.glob(".bundles/T01/01/*/recipe/evidence/result.json")).relative_to(passed).as_posix()
        self.assertIn("recipe result passed", compare.caught_defects(passed, [ok], 2)["rejected"][0]["reason"])
        changes = self.repos["changes"] / ".docket" / "runs" / "rr"
        self.assertEqual(["T01-decision-01.mdx"], compare.caught_defects(changes, ["T01-decision-01.mdx"], 2)["counted"])
        approval = self.repos["intact"] / ".docket" / "runs" / "rr"
        decision = sorted(p.name for p in approval.glob("T01-decision-*.mdx"))[-1]
        cases = {"no intact approval": (changes, "T01-decision-01.mdx", None),
                 "not a change request": (approval, decision, 2),
                 "not a path inside": (changes, "../../../etc/passwd", 2),
                 "not in the graded run state": (changes, "free text about a lost project", 2),
                 "not a frozen recipe result": (changes, "T01-task.mdx", 2)}
        for reason, (run, cited, rnd) in cases.items():
            got = compare.caught_defects(run, [cited], rnd)
            self.assertEqual([], got["counted"], reason)
            self.assertIn(reason, got["rejected"][0]["reason"])


def wire_bundle(root: Path, *, selected: str = "ui/x/recipe.json", status: str = "passed", negative: str | None = None,
                changed: list | None = None, tamper: bool = False, run_flag: str = "yes") -> tuple[Path, dict]:
    """A bundle directory in Docket's wire format (`sha256:<hex>` artifact digests)."""
    bundle = root / f"b{len(list(root.iterdir()))}"
    (bundle / "recipe/evidence").mkdir(parents=True)
    result = json.dumps({"invocation": "i", "checks": [], "cleanup": {}, "status": status, "negative_control": negative}).encode()
    (bundle / "recipe/evidence/result.json").write_bytes(result + (b" " if tamper else b""))
    manifest = {"recipe": {"reference": selected, "run": run_flag, "changed_in_patch": changed or [],
                           "evidence": [{"name": "result.json", "file": "recipe/evidence/result.json",
                                         "sha256": "sha256:" + hashlib.sha256(result).hexdigest()}]}}
    return bundle, manifest


class RecipeVerificationTest(Temp):
    def test_recipe_must_be_selected_run_passing_unedited_and_untampered(self) -> None:
        self.assertEqual({"verified": True, "reason": ""},
                         compare.recipe_verification(*wire_bundle(self.tmp), "ui/x/recipe.json"))
        cases = {"no recipe selected": ({}, ""), "frozen recipe result failed": ({"status": "failed"}, None),
                 "frozen recipe result passed": ({"negative": "lose-projects"}, None),
                 "edited the recipe": ({"changed": ["ui/x/verify.mjs"]}, None),
                 "missing or altered": ({"tamper": True}, None), "recipe not run": ({"run_flag": "no"}, None),
                 "no approved bundle": (None, None)}
        for reason, (kw, selected) in cases.items():
            bundle, manifest = wire_bundle(self.tmp, **(kw or {}))
            if kw is None:
                bundle, manifest = None, None
            r = compare.recipe_verification(bundle, manifest, "ui/x/recipe.json" if selected is None else selected)
            self.assertFalse(r["verified"], reason)
            self.assertIn(reason, r["reason"])


class DefectEvidenceTest(Temp):
    def bundle(self, n: int, *, status: str = "failed", negative: str | None = None, paths: list | None = None,
               tamper: bool = False) -> str:
        b = self.tmp / ".bundles" / "T01" / "01" / f"b{n}"
        (b / "recipe" / "evidence").mkdir(parents=True)
        data = json.dumps({"status": status, "negative_control": negative}).encode()
        (b / "recipe" / "evidence" / "result.json").write_bytes(data + (b" " if tamper else b""))
        (b / "bundle.json").write_text(json.dumps({
            "round": 1, "patch": {"roots": [{"paths": ["ui/server.mjs"] if paths is None else paths}]},
            "recipe": {"evidence": [{"name": "result.json", "file": "recipe/evidence/result.json",
                                     "sha256": "sha256:" + hashlib.sha256(data).hexdigest()}]}}))
        return f".bundles/T01/01/b{n}/recipe/evidence/result.json"

    def test_negative_controls_baseline_failures_and_altered_results_never_count(self) -> None:
        valid = self.bundle(0)
        self.assertEqual({"counted": [valid], "rejected": []}, compare.caught_defects(self.tmp, [valid], 2))
        cases = {"deliberate negative control": self.bundle(1, negative="lose-projects"),
                 "baseline failure": self.bundle(2, paths=[]),
                 "not recorded unaltered": self.bundle(3, tamper=True),
                 "not a behavioral failure": self.bundle(4, status="prerequisite-failed")}
        for reason, cited in cases.items():
            got = compare.caught_defects(self.tmp, [cited], 2)
            self.assertEqual([], got["counted"], reason)
            self.assertIn(reason, got["rejected"][0]["reason"])


class RoutineTurnTest(Temp):
    def test_turns_are_the_conductors_successful_deliveries(self) -> None:
        for role, kinds in (("implementor", ["derived", "announced", "delivered", "send-failed", "announced", "delivered"]),
                            ("reviewer", ["announced", "delivered"])):
            log = self.tmp / ".delivery" / role / "log.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text("".join(json.dumps({"kind": k, "key": "x"}) + "\n" for k in kinds))
        self.assertEqual({"by_role": {"implementor": 2, "reviewer": 1}, "total": 3}, compare.routine_turns(self.tmp))
        (self.tmp / ".delivery" / "reviewer" / "log.jsonl").unlink()
        self.assertIsNone(compare.routine_turns(self.tmp)["total"], "no delivery log is unknown, never zero")


class FullSessionJudgeTest(Temp):
    PROJECT = Path("/work/w1/docket")

    def judge(self, cwd: str = "/work/w1/docket/ui", first: str = "2026-11-03T10:00:00.250Z",
              last: str = "2026-11-03T11:00:00Z", noted: list | None = None, telemetry: str = "measured",
              files: list[str] | None = None) -> list[str]:
        transcript = self.tmp / "sid-1.jsonl"
        transcript.write_text(json.dumps({"cwd": cwd, "timestamp": first}) + "\n")
        noted = noted if noted is not None else [{"harness": "claude", "session_id": "sid-1",
                                                  "first_seen": "2026-11-03T10:00:01Z", "until": "2026-11-03T12:00:00Z"}]
        m = {"usage": {"telemetry": telemetry}, "first": first, "last": last, "noted": noted}
        return compare.full_session_problems("claude", files or [str(transcript)], m, "2026-11-03T10:00:00Z", self.PROJECT)

    def test_whole_fresh_session_inside_the_candidate_is_proven(self) -> None:
        self.assertEqual([], self.judge())

    def test_each_unproven_bound_or_ownership_is_named(self) -> None:
        ref = {"harness": "claude", "session_id": "sid-1", "first_seen": "2026-11-03T10:00:01Z", "until": "2026-11-03T12:00:00Z"}
        cases = {"noted sessions": {"noted": [ref, {**ref, "session_id": "sid-2"}]},
                 "not the noted session": {"noted": [{**ref, "session_id": "other"}]},
                 "between preparation and registration": {"first": "2026-11-03T09:59:59.999Z"},
                 "registration": {"first": "2026-11-03T10:00:02Z"},
                 "beyond the run end": {"last": "2026-11-03T12:00:00.001Z"},
                 "(open)": {"noted": [{**ref, "until": ""}]},
                 "outside the candidate project": {"cwd": "/home/u/other"},
                 "telemetry empty": {"telemetry": "empty"}}
        for needle, kw in cases.items():
            problems = self.judge(**kw)
            self.assertTrue(any(needle in p for p in problems), (needle, problems))


def claude_bash(command: str, call_id: str = "toolu_1") -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": call_id, "name": "Bash",
                                                          "input": {"command": command}}]}}


def claude_result(text: str, call_id: str = "toolu_1") -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": text}]}}


def codex_exec(command: str) -> dict:
    return {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                                 "arguments": json.dumps({"cmd": command})}}


def codex_output(text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "function_call_output", "output": text}}


def codex_wrapped(source: object, call_id: str = "call_1") -> dict:
    """Codex freeform `exec` call, shaped from the record schema of a real session."""
    return {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "status": "completed",
                                                 "call_id": call_id, "input": source}}


def codex_wrapped_output(text: str, call_id: str = "call_1") -> dict:
    return {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": call_id,
                                                 "output": [{"type": "input_text", "text": text}]}}


RESOLVE = 'readlink -f "$(command -v docket)"'


class ProvenanceTest(Temp):
    LAUNCHER = "/work/w1/tools/docket/bin/docket"
    README = ("Install: ln -s ~/.agents/skills/docket/bin/docket ~/.local/bin/docket\n"
              "Then run ~/.local/bin/docket help and ~/.agents/skills/docket/bin/docket status.\n")

    def classify(self, records: list[dict] | str | None, renderer: str = "derived-aaa", runtime_json: str = "",
                 prepared_json: str = "", extra: list[list[dict]] = (), **observations) -> dict:
        prompts = self.tmp / f"prompts-{len(list(self.tmp.iterdir()))}"
        prompts.mkdir()
        if renderer:
            (prompts / "T01-implementor-r01-abc.txt").write_text(f"Steps\n\nrenderer: {renderer} | task revision: x\n")
        if runtime_json:
            (prompts / "T01-reviewer-01.json").write_text(runtime_json)
        prepared = {}
        if prepared_json:
            (prompts / "T01-implementor-01.json").write_text(prepared_json)
            prepared = {"T01-implementor-01.json": hashlib.sha256(prepared_json.encode()).hexdigest()}
        files = []
        for transcript in ([] if records is None else [records]) + list(extra):
            files.append(self.tmp / f"t-{len(list(self.tmp.iterdir()))}.jsonl")
            files[-1].write_text(transcript if isinstance(transcript, str) else "\n".join(json.dumps(r) for r in transcript))
        return compare.provenance(prompts, prepared, files, "derived-aaa", self.LAUNCHER, observations, compare.design())

    def test_quoted_documentation_in_tool_output_is_not_foreign_execution(self) -> None:
        result = self.classify([codex_exec("cat README.md"), codex_output(self.README),
                                codex_exec(f"{self.LAUNCHER} submit r T01 --as implementor")], pane_path_verified=True)
        self.assertEqual(("pinned", 0, 4), (result["status"], result["foreign_cli_executions"], result["foreign_cli_doc_mentions"]))

    def test_an_actual_foreign_cli_command_is_flagged(self) -> None:
        for record in (claude_bash("~/.local/bin/docket submit r T01 --as implementor"),
                       codex_exec("/home/u/.agents/skills/docket/bin/docket status r")):
            result = self.classify([record], pane_path_verified=True)
            self.assertEqual(("foreign", 1), (result["status"], result["foreign_cli_executions"]), record)

    def test_an_actual_pinned_command_is_recognized_without_path_attestation(self) -> None:
        for record in (claude_bash(f"{self.LAUNCHER} submit r T01 --as implementor"),
                       {"type": "response_item", "payload": {"type": "local_shell_call",
                                                             "action": {"command": ["bash", "-lc", f"{self.LAUNCHER} status r"]}}}):
            result = self.classify([record, codex_output(self.README)])
            self.assertEqual(("pinned", 1, 0), (result["status"], result["pinned_cli_executions"], result["bare_invocations"]), record)

    def test_unresolved_or_unparsed_evidence_stays_unknown(self) -> None:
        pinned = claude_bash(f"{self.LAUNCHER} help implementor")
        self.assertEqual("unknown", self.classify([pinned, claude_bash("docket submit r T01")])["status"], "bare command, PATH unproven")
        attested = self.classify([pinned, claude_bash("docket submit r T01")], pane_path_verified=True)
        self.assertEqual(("unknown", True), (attested["status"], attested["pane_path_verified"]),
                         "a pane's PATH does not decide what a harness tool shell resolves")
        self.assertEqual("unknown", self.classify(f"$ {self.LAUNCHER} submit r T01\n")["status"], "plain text is not parsed execution")
        self.assertEqual("unknown", self.classify(None)["status"], "a copied CLI alone proves nothing")
        self.assertEqual("unknown", self.classify([pinned], renderer="")["status"], "no runtime prompt was rendered")

    def test_bare_commands_count_only_with_a_paired_resolution_to_the_pinned_launcher(self) -> None:
        pinned = claude_bash(f"{self.LAUNCHER} status r", "toolu_0")
        bare = claude_bash("docket pickup r --role implementor --session s", "toolu_2")
        proven = self.classify([pinned, claude_bash(RESOLVE), claude_result(self.LAUNCHER + "\n"), bare])
        self.assertEqual(("pinned", 1, 1), (proven["status"], proven["bare_invocations"], proven["bare_proven_pinned"]))

        codex = [codex_wrapped(f'text(await tools.exec_command({{cmd:"{self.LAUNCHER} status r"}}));'),
                 codex_wrapped('text(await tools.exec_command({cmd:"readlink -f \\"$(command -v docket)\\"",'
                               '"yield_time_ms":1000}));', "c2"),
                 codex_wrapped_output(f"Chunk ID: 1\nOutput:\n{self.LAUNCHER}\n", "c2"),
                 codex_wrapped('text(await tools.exec_command({cmd:"docket instruct r --role implementor --resolve 1"}));')]
        self.assertEqual("pinned", self.classify(codex)["status"], "the native Codex wrapper pairs by call_id")

        elsewhere = self.classify([pinned, claude_bash(RESOLVE), claude_result("/home/u/.agents/skills/docket/bin/docket"), bare])
        self.assertEqual(("foreign", 1), (elsewhere["status"], elsewhere["bare_resolved_elsewhere"]))
        for records, extra, why in (([pinned, claude_bash(RESOLVE), bare], [], "no paired output"),
                                    ([pinned, bare], [[claude_bash(RESOLVE), claude_result(self.LAUNCHER)]], "another transcript"),
                                    ([pinned, claude_bash(f"{RESOLVE} && ls"), claude_result(self.LAUNCHER), bare], [], "compound")):
            self.assertEqual("unknown", self.classify(records, extra=extra)["status"], why)

    def test_every_pinned_subcommand_counts_as_bare_but_paths_and_prose_do_not(self) -> None:
        source = subprocess.run(["git", "show", f"{PINNED}:skills/docket/docket_cli/cli.py"], cwd=REPO, check=True,
                                capture_output=True, text=True).stdout
        parsers = {n.args[0].value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
                   and getattr(n.func, "attr", "") == "add_parser" and n.args and isinstance(n.args[0], ast.Constant)}
        self.assertEqual(parsers, set(compare.SUBCOMMANDS), "exactly the pinned parser's subcommands")
        for command in ("docket instruct r --role implementor --resolve 1", "docket finish r", "cd ui && docket route r",
                        "docket arm r --role reviewer --session s", "env X=1 docket depend r T02 --on T01", "docket --help"):
            self.assertTrue(compare.BARE_DOCKET.search(command), command)
        for command in (f"{self.LAUNCHER} finish r", "git -C /w/w1/docket status", "ls docket/ui", "cd docket && npm test",
                        "docket-ui start"):
            self.assertFalse(compare.BARE_DOCKET.search(command), command)

    def test_runtime_prompt_json_counts_but_the_prepared_record_does_not(self) -> None:
        pinned = [claude_bash(f"{self.LAUNCHER} status r")]
        record = lambda renderer: json.dumps({"renderer_revision": renderer, "prompt": "renderer: derived-aaa"})
        self.assertEqual("foreign", self.classify(pinned, runtime_json=record("derived-bbb"))["status"],
                         "a runtime `prompt` by another CLI is caught")
        self.assertEqual(("pinned", ["derived-aaa"]), (lambda r: (r["status"], r["renderers"]))(
            self.classify(pinned, renderer="", runtime_json=record("derived-aaa"))))
        self.assertEqual(("unknown", []), (lambda r: (r["status"], r["renderers"]))(
            self.classify(pinned, renderer="", prepared_json=record("derived-aaa"))), "preparation is not runtime evidence")
        self.assertEqual(("unknown", 1), (lambda r: (r["status"], r["unreadable_prompt_records"]))(
            self.classify(pinned, runtime_json="{not json")))

    def test_runtime_renderer_mismatch_is_foreign_but_a_quoted_one_is_not(self) -> None:
        self.assertEqual("foreign", self.classify([claude_bash(f"{self.LAUNCHER} status r")], renderer="derived-bbb")["status"])
        quoted = self.classify([claude_bash(f"{self.LAUNCHER} status r"), codex_output("renderer: derived-bbb")])
        self.assertEqual(("pinned", ["derived-aaa"]), (quoted["status"], quoted["renderers"]))

    def test_codex_exec_wrapper_literal_commands_are_execution(self) -> None:
        pinned = codex_wrapped(f'text((await tools.exec_command({{cmd:"{self.LAUNCHER} status r","workdir":"/w",'
                               '"yield_time_ms":2500})).output);')
        parallel = codex_wrapped(f'const results = await Promise.allSettled([ tools.exec_command({{cmd:"cat README.md",'
                                 f'"yield_time_ms":7000}}), tools.exec_command({{cmd:"{self.LAUNCHER} submit r T01 '
                                 '--as implementor","yield_time_ms":9000}) ]); for (const r of results) text(r.value.output);')
        result = self.classify([pinned, parallel, codex_wrapped_output(self.README)])
        self.assertEqual(("pinned", 2, 0, 0, 4), (result["status"], result["pinned_cli_executions"], result["foreign_cli_executions"],
                                                  result["opaque_tool_calls"], result["foreign_cli_doc_mentions"]))

        foreign = codex_wrapped('text(await tools.apply_patch("*** Begin Patch")); '
                                'text(await tools.exec_command({cmd:"~/.local/bin/docket submit r T01 --as implementor","yield_time_ms":1000}));')
        self.assertEqual(("foreign", 1), (lambda r: (r["status"], r["foreign_cli_executions"]))(self.classify([pinned, foreign])))

        escaped = codex_wrapped('text(await tools.exec_command({cmd:"cd \\"/w x\\" && ' + self.LAUNCHER + ' status r\\n"}));')
        self.assertEqual(1, compare.executed_commands(self.write([escaped]))[0][0].count(self.LAUNCHER))

    def test_codex_exec_wrapper_opaque_input_and_prose_are_not_execution(self) -> None:
        pinned = codex_wrapped(f'text(await tools.exec_command({{cmd:"{self.LAUNCHER} status r","yield_time_ms":1000}}));')
        prose = codex_wrapped('text("Next I would run ~/.local/bin/docket status r");')
        result = self.classify([pinned, prose])
        self.assertEqual(("pinned", 0, 0, 1), (result["status"], result["foreign_cli_executions"], result["opaque_tool_calls"],
                                               result["foreign_cli_doc_mentions"]))

        for source in ('const c = "~/.local/bin/docket submit r T01"; text(await tools.exec_command({cmd: c}));',
                       f'const run = tools.exec_command; text(await run({{cmd:"{self.LAUNCHER} status r"}}));',
                       f'text(await tools.exec_command({{workdir:"/w", cmd:"{self.LAUNCHER} status r"}}));',
                       f'text(await tools.exec_command({{cmd:`{self.LAUNCHER} status ${{run}}`}}));',
                       {"cmd": f"{self.LAUNCHER} status r"}):
            result = self.classify([pinned, codex_wrapped(source)])
            self.assertEqual(("unknown", 0, 1), (result["status"], result["foreign_cli_executions"], result["opaque_tool_calls"]),
                             source)

        typed = codex_wrapped('text(await tools.write_stdin({session_id:60307,chars:"docket submit r T01\\n","yield_time_ms":1000}));'
                              ' text(await tools.write_stdin({session_id:60307,chars:"","yield_time_ms":3000}));')
        result = self.classify([pinned, typed])
        self.assertEqual(("unknown", 1, 0), (result["status"], result["bare_invocations"], result["opaque_tool_calls"]))

    def write(self, records: list[dict]) -> Path:
        path = self.tmp / f"w-{len(list(self.tmp.iterdir()))}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records))
        return path

    def test_installed_skill_document_reads_are_reported_separately(self) -> None:
        result = self.classify([claude_bash(f"cat /home/u/.agents/skills/docket/SKILL.md && {self.LAUNCHER} help implementor")])
        self.assertEqual(("pinned", 1), (result["status"], result["installed_skill_doc_mentions"]))


class HelperOutcomeTest(unittest.TestCase):
    def result(self, status: str, phase: str, code: str | None = None, message: str = "", checks: tuple = ()) -> dict:
        return {"status": status, "phase": phase, "error": {"code": code, "message": message} if code else None,
                "checks": [{"name": c, "passed": True} for c in checks]}

    def test_classifies_where_the_saved_list_went_missing(self) -> None:
        added = ("initial-isolation", "invalid-project-refused", "browser-add-persisted")
        cases = [
            (0, self.result("passed", "complete", checks=added), "passed"),
            (2, self.result("prerequisite-failed", "prerequisites", "PREREQUISITE", "Chromium could not launch"), "prerequisite"),
            (1, self.result("failed", "restart-readback", "PERSISTENCE_MISSING", "absent after restart", added), "saved-list-lost-after-restart"),
            (1, self.result("failed", "browser-add", "VERIFICATION_FAILED",
                            "ENOENT: no such file or directory, open '[sandbox]/state/docket-ui/projects.json'", added[:2]),
             "saved-list-not-written"),
            (1, self.result("failed", "first-launch", "VERIFICATION_FAILED", "Private companion failed to start"), "other-failure"),
            (1, self.result("failed", "restart-readback", "PERSISTENCE_MISSING", "absent", added[:2]), "other-failure"),
            (1, {}, "other-failure"),
        ]
        for exit_code, result, kind in cases:
            outcome = compare.helper_outcome(exit_code, result)
            self.assertEqual(kind, outcome["kind"], result)
            self.assertEqual(exit_code, outcome["exit"])

    def test_covered_proof_accepts_either_missing_list_outcome_only(self) -> None:
        base = {"reference": {"status": "passed"}, "alternate-fix": {"status": "passed"}}
        seeded = lambda helper: {**base, "seeded": {"decisive_failed": ["x"], "production_tests": {"passed": True},
                                                    "recipe_helper": helper}}
        for kind, ok in (("saved-list-not-written", True), ("saved-list-lost-after-restart", True),
                         ("prerequisite", False), ("other-failure", False), ("passed", False)):
            problems = compare.proof_problems("added-projects-lost", seeded({"exit": 0 if kind == "passed" else 1, "kind": kind}))
            self.assertEqual(ok, not problems, kind)
        self.assertTrue(compare.proof_problems("removal-persists", seeded(CAUGHT)))
        self.assertTrue(compare.proof_problems("removal-persists", {"seeded": {}}))


def grade(arm: str, *, total: int = 1000, turns: object = 3, approved: bool = True, verified: bool = True,
          failed: list | None = None, observations: dict | None = None, models: dict | None = None,
          efforts: dict | None = None, private_hit: int = 0, helper: int = 0, cli: str = "pinned", full: bool = True,
          caught: list | None = None, hidden: str = "", deps: bool = True, runtime_ok: bool = True) -> dict:
    usage = {c: 1 for c in compare.CATEGORIES} | {"total": total, "telemetry": "measured", "gaps": [],
                                                  "models": {"implementor": ["claude-opus-5-5"], "reviewer": ["gpt-6.1-sol"]}}
    return {"hidden": {"status": hidden or ("failed" if failed else "passed"), "failed": failed or []},
            "pinned_tests": {"passed": True}, "graded_tree": "approved", "live_drift": {"added": [], "deleted": [], "modified": []},
            "node_modules_unchanged": deps, "runtime_matches_proof": runtime_ok,
            "docket": {"rounds": 1, "approved": approved, "integrity": {"status": "intact" if approved else "not-approved"},
                       "recipe_selected": "x" if arm == "treatment" else "",
                       "recipe": {"verified": verified if arm == "treatment" else False, "reason": "" if verified else "no approved bundle"},
                       "helper_runs": helper},
            "turns": {"total": turns}, "caught_defects": {"counted": caught or [], "rejected": []},
            "recipe_helper_executions": 0,
            "exposure": {"transcripts": {"recipe": {"verify.mjs": 0}, "private": {"rubric.json": private_hit}}},
            "usage": usage,
            "identity": compare.identity(compare.design()["models"], usage["models"] if models is None else models,
                                         {"reviewer": ["high"]} if efforts is None else efforts),
            "full_sessions": {c: (usage[c] if full else None) for c in compare.CATEGORIES}
            | {"telemetry": "measured" if full else "unknown", "gaps": [] if full else ["implementor: no full-session transcript"]},
            "cli": {"status": cli, "renderers": ["derived-1"], "pinned_renderer": "derived-1",
                    "foreign_cli_executions": 0, "installed_skill_doc_mentions": 0, "bare_invocations": 0},
            "observations": {"human_interventions": 0, **(observations or {})}}


SAVED = {"caught": [".bundles/T01/01/x/recipe/evidence/result.json"]}


class DecideTest(unittest.TestCase):
    def decide(self, pairs: list[tuple[dict, dict]], adjudicate: bool = True) -> tuple[str, list[str]]:
        rows = []
        for n, (t, c) in enumerate(pairs):
            labels = {"treatment": f"t{n}", "control": f"c{n}"}
            adj = {l: {"escaped_defects": []} for l in labels.values()} if adjudicate else {}
            rows.append(compare.pair_row(f"task{n}", labels, {f"t{n}": t, f"c{n}": c}, adj, RUBRIC["token_overhead_budget"]))
        return compare.decide(rows, RUBRIC)

    def valid(self, **t) -> list[tuple[dict, dict]]:
        return [(grade("treatment", **{**SAVED, **t}), grade("control")), (grade("treatment", **t), grade("control"))]

    def test_supported_only_with_complete_valid_evidence_and_a_saving(self) -> None:
        self.assertTrue(self.decide(self.valid())[0].startswith("opt-in-supported"))
        tie = [(grade("treatment"), grade("control"))] * 2
        self.assertEqual(("not-supported", ["tie without a concrete operational saving or caught defect"]), self.decide(tie))

    def test_unverified_treatment_or_missing_approval_is_inconclusive(self) -> None:
        for t, c in (({"verified": False}, {}), ({"approved": False}, {}), ({}, {"approved": False}),
                     ({"approved": False, "verified": False}, {"approved": False})):
            pairs = [(grade("treatment", **SAVED, **t), grade("control", **c)), (grade("treatment"), grade("control"))]
            outcome, reasons = self.decide(pairs)
            self.assertEqual("inconclusive", outcome, (t, c))
            self.assertTrue(reasons)

    def test_unproven_or_foreign_cli_is_inconclusive(self) -> None:
        for status in ("unknown", "foreign"):
            outcome, reasons = self.decide(self.valid(cli=status))
            self.assertEqual("inconclusive", outcome, status)
            self.assertIn(f"CLI provenance {status}", reasons[0])

    def test_identity_mismatch_unknown_identity_or_exposure_is_inconclusive(self) -> None:
        for kw in ({"models": {"implementor": ["claude-opus-5-5"], "reviewer": ["gpt-5.6-sol"]}},
                   {"models": {"implementor": ["claude-opus-5-5"]}}, {"private_hit": 1},
                   {"efforts": {"reviewer": ["medium"]}}, {"efforts": {}}):
            self.assertEqual("inconclusive", self.decide(self.valid(**kw))[0], kw)

    def test_attributed_tokens_never_stand_in_for_unproven_full_sessions(self) -> None:
        outcome, reasons = self.decide(self.valid(full=False))
        self.assertEqual(("inconclusive", ["full-session execution tokens unknown"]), (outcome, reasons))
        row = compare.pair_row("t", {"treatment": "t", "control": "c"},
                               {"t": grade("treatment", full=False), "c": grade("control")}, {"t": {}, "c": {}}, 0.15)
        self.assertEqual({"treatment": None, "control": 1000}, row["execution_tokens"])
        self.assertEqual({"treatment": 1000, "control": 1000}, row["attributed_tokens_lower_bound"])
        self.assertIsNone(row["overhead"])

    def test_delegated_subagent_work_is_a_reported_deviation(self) -> None:
        pairs = self.valid()
        pairs[0][1]["full_sessions"]["roles"] = {"implementor": {"delegated_transcripts": 2}}
        outcome, reasons = self.decide(pairs)
        self.assertEqual("inconclusive", outcome)
        self.assertIn("control delegated work to 2 subagent transcript(s)", reasons[0])

    def test_unknown_telemetry_turns_or_adjudication_stay_inconclusive(self) -> None:
        unknown_tokens = grade("treatment", **SAVED)
        unknown_tokens["full_sessions"]["total"] = None
        self.assertEqual("inconclusive", self.decide([(unknown_tokens, grade("control")), self.valid()[1]])[0])
        self.assertEqual("inconclusive", self.decide(self.valid(turns=None))[0])
        self.assertEqual("inconclusive", self.decide(self.valid(turns="3"))[0])
        self.assertEqual("inconclusive", self.decide(self.valid(), adjudicate=False)[0])
        self.assertEqual("incomplete", self.decide(self.valid()[:1])[0])

    def test_regression_extra_turn_or_budget_overrun_is_not_supported(self) -> None:
        for kw, reason in (({"failed": ["api-switch-startup"]}, "regression"), ({"turns": 4}, "extra routine"),
                           ({"total": 1200}, "exceeds the budget")):
            outcome, reasons = self.decide(self.valid(**kw))
            self.assertEqual("not-supported", outcome, kw)
            self.assertIn(reason, reasons[0])
        self.assertTrue(self.decide(self.valid(total=1150))[0].startswith("opt-in-supported"))

    def test_control_recipe_use_is_reported_and_selection_invalidates(self) -> None:
        control = grade("control", helper=1)
        rows = [compare.pair_row("t", {"treatment": "t", "control": "c"},
                                 {"t": grade("treatment", **SAVED), "c": control},
                                 {"t": {}, "c": {}}, 0.15)]
        self.assertTrue(rows[0]["control_ran_recipe_helper"])
        control = copy.deepcopy(control)
        control["docket"]["recipe_selected"] = "x"
        outcome, reasons = self.decide([(grade("treatment", **SAVED), control), self.valid()[1]])
        self.assertEqual("inconclusive", outcome)
        self.assertIn("control selected the recipe", reasons[0])
        ran = grade("control")
        ran["recipe_helper_executions"] = 1
        self.assertTrue(compare.pair_row("t", {"treatment": "t", "control": "c"}, {"t": grade("treatment"), "c": ran},
                                         {"t": {}, "c": {}}, 0.15)["control_ran_recipe_helper"])

    def test_an_infrastructure_failure_is_unknown_not_a_regression(self) -> None:
        for status in ("prerequisite-failed", "harness-failed"):
            outcome, reasons = self.decide([(grade("treatment", **SAVED, hidden=status), grade("control")), self.valid()[1]])
            self.assertEqual("inconclusive", outcome, status)
            self.assertIn("hidden checks without a verdict", reasons[0])
        live = grade("treatment", **SAVED)
        live["graded_tree"] = "live"
        self.assertEqual("inconclusive", self.decide([(live, grade("control")), self.valid()[1]])[0])

    def test_free_text_savings_are_listed_but_never_scored(self) -> None:
        noted = [(grade("treatment", observations={"operational_saving": "much faster"}), grade("control"))] * 2
        self.assertEqual(("not-supported", ["tie without a concrete operational saving or caught defect"]), self.decide(noted))
        row = compare.pair_row("t", {"treatment": "t", "control": "c"}, {"t": noted[0][0], "c": noted[0][1]},
                               {"t": {}, "c": {}}, 0.15)
        self.assertEqual("much faster", row["unscored_notes"]["treatment"])

    def test_changed_dependencies_or_runtime_invalidate_the_pair(self) -> None:
        for kw, reason in (({"deps": False}, "changed its installed dependencies"), ({"runtime_ok": False}, "other than the proof's")):
            outcome, reasons = self.decide(self.valid(**kw))
            self.assertEqual("inconclusive", outcome, kw)
            self.assertIn(reason, reasons[0])


@unittest.skipUnless(os.environ.get("RECIPE_COMPARISON_NODE_MODULES"),
                     "set RECIPE_COMPARISON_NODE_MODULES=/abs/ui/node_modules to run the browser sensitivity proof")
class SensitivityTest(Temp):
    def test_seeded_defects_fail_decisive_checks_and_correct_behavior_passes(self) -> None:
        out = self.tmp / "proof"
        compare.main(["prove", "--source-repo", str(REPO), "--revision", PINNED,
                      "--node-modules", os.environ["RECIPE_COMPARISON_NODE_MODULES"], "--out", str(out)])
        proof = json.loads((out / "proof.json").read_text())
        self.assertEqual([], proof["problems"])
        for name, record in proof["tasks"].items():
            self.assertEqual("passed", record["reference"]["status"], name)
            self.assertEqual("passed", record["alternate-fix"]["status"], name)
            self.assertTrue(record["seeded"]["decisive_failed"], name)
            self.assertTrue(record["seeded"]["production_tests"]["passed"], f"{name}: existing tests miss the seed")
            helper = record["seeded"]["recipe_helper"]
            if compare.task(name)["recipe_covered"]:
                self.assertEqual(1, helper["exit"], name)
                self.assertIn(helper["kind"], ("saved-list-not-written", "saved-list-lost-after-restart"), helper)
            else:
                self.assertEqual((0, "passed"), (helper["exit"], helper["kind"]))


if __name__ == "__main__":
    unittest.main()
