"""Explicitly selected verification recipes: selection, capture, freeze, and review."""
from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

import test_docket as fixtures

HELPER = """\
import json, os, pathlib, sys
out = pathlib.Path(os.environ["DOCKET_RECIPE_EVIDENCE_DIR"])
mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if pathlib.Path("no-proof.flag").exists():
    mode = "no-proof"
flags = pathlib.Path(".docket/flags")
if (flags / "edit-task").exists():
    task = pathlib.Path(".docket/runs/rr/T01-task.mdx")
    task.write_text(task.read_text().replace("verification_recipe: recipes/demo/recipe.json",
                                             "verification_recipe: recipes/other/recipe.json"))
if (flags / "edit-src").exists():
    pathlib.Path("src/a.py").write_text("allowed = 3\\n")
if (flags / "fail-once").exists():
    (flags / "fail-once").unlink()
    sys.exit(1)
if (flags / "touch-recipe").exists():
    mode = "touch-recipe"
start = sorted(os.listdir(out))
if mode == "touch-recipe":
    pathlib.Path("recipes/demo/recipe.md").write_text("edited during verify\\n")
if mode != "no-proof":
    (out / "doctor.json").write_text(json.dumps({"dir": str(out), "start": start}))
    if mode != "partial":
        (out / "shots").mkdir()
        (out / "shots" / "journey.png").write_bytes(b"\\x89PNG fake journey")
print("recipe", mode)
"""

MANIFEST = {"version": 1, "name": "demo", "instructions": "recipe.md",
            "helpers": ["verify.py"], "evidence": ["doctor.json", "shots/journey.png"]}


class Recipes(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.cli = self.f.cli
        self.root = self.f.root

    def write_recipe(self, manifest: dict | None = None) -> Path:
        where = self.root / "recipes" / "demo"
        where.mkdir(parents=True, exist_ok=True)
        (where / "recipe.json").write_text(json.dumps(manifest or MANIFEST))
        (where / "recipe.md").write_text("Launch, doctor, drive, evidence, cleanup.\n")
        (where / "verify.py").write_text(HELPER)
        return where

    def start(self, mode: str = "git", verify: str = "python3 recipes/demo/verify.py",
              extra: tuple[str, ...] = (), files: tuple[str, ...] = ("src/a.py",)) -> Path:
        if mode == "git":
            self.f.repo()
            self.write_recipe()
            self.f.git("add", "recipes")
            self.f.git("commit", "-qm", "recipe")
        else:
            self.write_recipe()
        self.cli("init", "rr", "--evidence-mode", mode)
        run = self.root / ".docket/runs/rr"
        self.cli("assign", "rr", "T01", *(a for p in files for a in ("--file", p)),
                 "--verify", verify, "--recipe", "recipes/demo/recipe.json",
                 *extra, "--as", "planner")
        self.f.fill_task(run / "T01-task.mdx")
        self.cli("batch", "rr", "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner")
        self.cli("batch", "rr", "--close", "M1", "--as", "planner")
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "rr", "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx")
        if mode == "git":
            (self.root / "src" / "a.py").write_text("allowed = 2\n")
        return run

    def bundle(self, run: Path) -> tuple[dict, Path]:
        ledger = json.loads((run / ".bundles" / "T01" / "rounds.json").read_text())
        where = run / ".bundles" / "T01" / ledger["entries"][-1]["dir"]
        return json.loads((where / "bundle.json").read_text()), where

    def test_recipe_bytes_and_fresh_evidence_freeze_into_the_bundle(self):
        run = self.start()
        task = (run / "T01-task.mdx").read_text()
        self.assertIn("verification_recipe: recipes/demo/recipe.json\n", task)
        self.cli("submit", "rr", "T01", "--as", "implementor")
        manifest, where = self.bundle(run)
        recipe = manifest["recipe"]
        self.assertEqual(("recipes/demo/recipe.json", "demo", "yes"),
                         (recipe["reference"], recipe["name"], recipe["run"]))
        source = self.root / "recipes" / "demo"
        for row, name in ((recipe["manifest"], "recipe.json"), (recipe["instructions"], "recipe.md"),
                          (recipe["helpers"][0], "verify.py")):
            self.assertEqual((source / name).read_bytes(), (where / row["file"]).read_bytes())
        evidence = {row["name"]: (where / row["file"]).read_bytes() for row in recipe["evidence"]}
        self.assertEqual(b"\x89PNG fake journey", evidence["shots/journey.png"])
        doctor = json.loads(evidence["doctor.json"])
        # The helper saw a directory that was empty when it started, owned by this run.
        self.assertEqual([], doctor["start"])
        evidence_dir = Path(doctor["dir"])
        self.assertEqual(run / recipe["evidence_dir"], evidence_dir)
        self.assertEqual(0o700, stat.S_IMODE(evidence_dir.stat().st_mode))
        self.assertEqual([], recipe["changed_in_patch"])
        self.assertEqual(["DOCKET_RECIPE_EVIDENCE_DIR"], manifest["verification"]["docket_env"])
        shown = self.cli("bundle", "rr", "T01").stdout
        self.assertIn("recipe recipes/demo/recipe.json run yes  2 evidence file(s)", shown)

        # Tampering with a frozen evidence artifact is detected as damage.
        tampered = where / recipe["evidence"][0]["file"]
        original = tampered.read_bytes()
        tampered.write_bytes(b"{}")
        damaged = self.cli("bundle", "rr", "T01", ok=False)
        self.assertIn(f"no longer matches its recorded artifact {recipe['evidence'][0]['file']}",
                      damaged.stderr)
        tampered.write_bytes(original)

        self.cli("batch", "rr", "--verify", "M1", "--as", "implementor")
        packet = self.cli("review-packet", "rr", "--role", "reviewer", "--batch", "M1").stdout
        self.assertIn("verification recipe recipes/demo/recipe.json for T01: run yes", packet)
        self.assertIn(f"evidence shots/journey.png (17 bytes): {where.relative_to(run)}/recipe/evidence/shots/journey.png", packet)
        self.assertIn("it is not an oracle", packet)
        self.assertNotIn("changed oracle:", packet)
        prompt = self.cli("prompt", "rr", "T01", "--role", "reviewer").stdout
        self.assertIn("verification recipe: recipes/demo/recipe.json (frozen", prompt)
        self.assertIn("check at least one material boundary or negative case", prompt)
        self.cli("batch", "rr", "--approve", "M1", "--as", "reviewer", "--reason", "Checked.")

    def test_each_verify_gets_its_own_directory_and_cannot_be_redirected(self):
        run = self.start(mode="documents-only", extra=("--env", "DOCKET_RECIPE_EVIDENCE_DIR=/tmp/stale"))
        self.cli("submit", "rr", "T01", "--as", "implementor")
        manifest, where = self.bundle(run)
        doctor = json.loads((where / manifest["recipe"]["evidence"][0]["file"]).read_bytes())
        self.assertNotEqual("/tmp/stale", doctor["dir"])
        self.assertTrue(Path(doctor["dir"]).is_relative_to(run / ".recipe-evidence" / "T01"))

    def test_missing_proof_refuses_submission_and_freezes_nothing(self):
        for mode, missing in (("no-proof", "doctor.json"), ("partial", "shots/journey.png")):
            with self.subTest(mode=mode):
                self.setUp()
                run = self.start(mode="documents-only", verify=f"python3 recipes/demo/verify.py {mode}")
                refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
                self.assertIn(f"recipe evidence {missing} is unreadable", refused.stderr)
                self.assertIn("did not produce its declared proof", refused.stderr)
                self.assertFalse((run / ".bundles" / "T01" / "rounds.json").exists())
                self.assertIn("status: draft", (run / "T01-report-01.mdx").read_text())

    def test_verify_that_never_runs_the_recipe_is_refused(self):
        run = self.start(mode="documents-only", verify="true")
        refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
        self.assertIn("recipe evidence doctor.json is unreadable", refused.stderr)
        self.assertFalse((run / ".bundles" / "T01" / "rounds.json").exists())

    def test_recipe_edited_while_verify_runs_is_refused(self):
        run = self.start(mode="documents-only", verify="python3 recipes/demo/verify.py touch-recipe")
        refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
        self.assertIn("recipe instructions recipes/demo/recipe.md changed while the verification ran",
                      refused.stderr)
        self.assertFalse((run / ".bundles" / "T01" / "rounds.json").exists())

    def test_recipe_file_vanishing_after_capture_refuses_the_freeze(self):
        run = self.start(mode="documents-only")
        refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False,
                           perturb="submit:after-recipe-capture=rm recipes/demo/recipe.md")
        self.assertIn("running verify", refused.stdout)
        self.assertIn("recipe instructions recipes/demo/recipe.md is unreadable", refused.stderr)
        self.assertIn("after verification ran", refused.stderr)
        self.assertFalse((run / ".bundles" / "T01" / "rounds.json").exists())
        self.assertIn("status: draft", (run / "T01-report-01.mdx").read_text())

    def preflight_record(self, run: Path) -> dict:
        self.cli("preflight", "rr", "T01")
        return json.loads((run / ".baselines" / "T01.json").read_text())

    def test_preflight_runs_the_recipe_with_fresh_proof_per_attempt(self):
        run = self.start(mode="documents-only")
        flags = self.root / ".docket" / "flags"
        flags.mkdir()
        record = self.preflight_record(run)
        self.assertEqual("passed", record["status"])
        attempts = record["recipe"]["attempts"]
        self.assertEqual(1, len(attempts))
        self.assertEqual(["doctor.json", "shots/journey.png"], [e["name"] for e in attempts[0]["evidence"]])
        self.assertEqual(json.loads((self.root / "recipes/demo/recipe.json").read_text()), MANIFEST)
        self.assertIn("recipes/demo/recipe.md", record["recipe"]["files"])

        (flags / "fail-once").write_text("")
        record = self.preflight_record(run)
        self.assertEqual("nondeterministic", record["status"])
        dirs = [attempt["evidence_dir"] for attempt in record["recipe"]["attempts"]]
        self.assertEqual(2, len(set(dirs)))
        self.assertEqual([], record["recipe"]["attempts"][0]["evidence"])
        doctor = json.loads((run / dirs[1] / "doctor.json").read_text())
        self.assertEqual([], doctor["start"])

        (self.root / "no-proof.flag").write_text("")
        record = self.preflight_record(run)
        self.assertEqual(("recipe-proof-missing", 0), (record["status"], record["returncode"]))
        self.assertEqual(2, len(record["recipe"]["attempts"]))
        self.assertIn("doctor.json", record["recipe"]["attempts"][0]["missing"][0])
        (self.root / "no-proof.flag").unlink()

        (flags / "touch-recipe").write_text("")
        baseline = (run / ".baselines" / "T01.json").read_bytes()
        refused = self.cli("preflight", "rr", "T01", ok=False)
        self.assertIn("recipe instructions recipes/demo/recipe.md changed while the verification ran",
                      refused.stderr)
        self.assertEqual(baseline, (run / ".baselines" / "T01.json").read_bytes())
        (flags / "touch-recipe").unlink()

        (self.root / "recipes/demo/recipe.md").unlink()
        record = self.preflight_record(run)
        self.assertEqual("recipe-unavailable", record["status"])
        self.assertIn("recipe instructions recipes/demo/recipe.md is unreadable",
                      record["recipe"]["problem"])

    def test_preflight_never_reuses_a_bundle_for_a_recipe_task(self):
        run = self.start()
        self.cli("submit", "rr", "T01", "--as", "implementor")
        record = self.preflight_record(run)
        self.assertFalse(record["reused"])
        self.assertEqual("passed", record["status"])
        self.assertEqual(1, len(record["recipe"]["attempts"]))

    def test_invalid_manifests_are_refused_before_verify(self):
        cases = [
            ({**MANIFEST, "expected": "pass"}, "unsupported key(s): expected"),
            ({**MANIFEST, "helpers": ["../escape.py"]}, "must not contain '.', '..'"),
            ({**MANIFEST, "instructions": "/etc/passwd"}, "must be relative, not absolute"),
            ({**MANIFEST, "evidence": []}, "must name at least one proof file"),
            ({**MANIFEST, "version": 2}, "version must be 1"),
            ({**MANIFEST, "helpers": ["verify.py", "verify.py"]}, "names a file more than once"),
            ({**MANIFEST, "helpers": ["missing.py"]}, "recipe helper recipes/demo/missing.py is unreadable"),
        ]
        run = self.start(mode="documents-only")
        source = self.root / "recipes" / "demo" / "recipe.json"
        for manifest, message in cases:
            with self.subTest(message=message):
                source.write_text(json.dumps(manifest))
                checked = self.cli("submit", "rr", "T01", "--check", ok=False)
                self.assertIn(message, checked.stdout)
                refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
                self.assertIn(message, refused.stderr)
                self.assertNotIn("running verify", refused.stdout)
        source.write_text(json.dumps(MANIFEST))
        helper = self.root / "recipes" / "demo" / "verify.py"
        helper.rename(self.root / "real.py")
        helper.symlink_to(self.root / "real.py")
        refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
        self.assertIn("passes through a symlink", refused.stderr)
        self.assertFalse((run / ".bundles" / "T01" / "rounds.json").exists())

    def test_hand_amended_selection_is_revalidated_at_capture(self):
        run = self.start()
        task = run / "T01-task.mdx"
        original = task.read_text()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        foreign = Path(outside.name) / "recipe.json"
        foreign.write_text(json.dumps(MANIFEST))
        for reference, message in (
            ("recipes/../recipes/demo/recipe.json", "must not contain '.', '..'"),
            (str(foreign), "must be relative, not absolute"),
            ("nope:recipes/demo/recipe.json", "names an undeclared root alias 'nope'"),
        ):
            with self.subTest(reference=reference):
                task.write_text(original.replace("verification_recipe: recipes/demo/recipe.json",
                                                 f"verification_recipe: {reference}"))
                refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
                self.assertIn(message, refused.stderr)
                self.assertNotIn("running verify", refused.stdout)
        self.assertFalse((run / ".bundles" / "T01" / "rounds.json").exists())
        self.assertFalse((run / ".recipe-evidence").exists())

    def test_multi_root_selection_reads_only_the_named_checkout(self):
        for name, label in (("api", "DECOY api"), ("web", "SELECTED web")):
            checkout = self.f.repo(self.root / name)
            where = checkout / "recipes" / "demo"
            where.mkdir(parents=True)
            (where / "recipe.json").write_text(json.dumps(MANIFEST))
            (where / "recipe.md").write_text(f"{label} instructions\n")
            (where / "verify.py").write_text(HELPER + f"# {label}\n")
            self.f.git("add", "recipes", cwd=checkout)
            self.f.git("commit", "-qm", "recipe", cwd=checkout)
        self.write_recipe()
        (self.root / "recipes" / "demo" / "recipe.md").write_text("DECOY workspace instructions\n")
        self.cli("init", "rr", "--root", "api=./api", "--root", "web=./web")
        run = self.root / ".docket/runs/rr"
        refused = self.cli("assign", "rr", "T01", "--verify", "true", "--recipe",
                           "recipes/demo/recipe.json", "--as", "planner", ok=False)
        self.assertIn("resolves to no declared checkout root", refused.stderr)
        refused = self.cli("assign", "rr", "T01", "--verify", "true", "--recipe",
                           "nope:recipes/demo/recipe.json", "--as", "planner", ok=False)
        self.assertIn("undeclared root alias 'nope'", refused.stderr)
        self.cli("assign", "rr", "T01", "--file", "web:src/a.py", "--file", "web:recipes/demo/recipe.md",
                 "--verify", "python3 web/recipes/demo/verify.py",
                 "--recipe", "web/recipes/demo/recipe.json", "--as", "planner")
        task = run / "T01-task.mdx"
        self.assertIn("verification_recipe: web:recipes/demo/recipe.json\n", task.read_text())
        self.f.fill_task(task)
        self.cli("batch", "rr", "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner")
        self.cli("batch", "rr", "--close", "M1", "--as", "planner")
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "rr", "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx", files=(
            "- `web:src/a.py:1` - implemented the feature.\n"
            "- `web:recipes/demo/recipe.md:1` - changed the recipe expectation."))
        (self.root / "web" / "src" / "a.py").write_text("allowed = 2\n")
        (self.root / "web" / "recipes" / "demo" / "recipe.md").write_text("SELECTED web, edited\n")

        original = task.read_text()
        for reference, message in (("recipes/demo/recipe.json", "is unqualified in a multi-root run"),
                                   ("nope:recipes/demo/recipe.json", "undeclared root alias 'nope'"),
                                   ("web:../api/recipes/demo/recipe.json", "must not contain '.', '..'")):
            with self.subTest(reference=reference):
                task.write_text(original.replace("verification_recipe: web:recipes/demo/recipe.json",
                                                 f"verification_recipe: {reference}"))
                refused = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
                self.assertIn(message, refused.stderr)
                self.assertNotIn("running verify", refused.stdout)
        task.write_text(original)

        self.cli("submit", "rr", "T01", "--as", "implementor")
        manifest, where = self.bundle(run)
        recipe = manifest["recipe"]
        self.assertEqual("web:recipes/demo/recipe.json", recipe["reference"])
        self.assertEqual(b"SELECTED web, edited\n", (where / recipe["instructions"]["file"]).read_bytes())
        self.assertTrue((where / recipe["helpers"][0]["file"]).read_bytes().endswith(b"# SELECTED web\n"))
        self.assertEqual((self.root / "web" / "recipes" / "demo" / "recipe.json").read_bytes(),
                         (where / recipe["manifest"]["file"]).read_bytes())
        self.assertEqual(["web:recipes/demo/recipe.md"], recipe["changed_in_patch"])
        self.assertIn("web:recipes/demo/recipe.md", manifest["changed_paths"])
        self.cli("batch", "rr", "--verify", "M1", "--as", "implementor")
        packet = self.cli("review-packet", "rr", "--role", "reviewer", "--batch", "M1").stdout
        self.assertIn("verification recipe web:recipes/demo/recipe.json for T01: run yes", packet)
        self.assertIn("edits recipe file(s) web:recipes/demo/recipe.md", packet)

    def test_unreadable_root_declaration_is_not_read_as_absent(self):
        run = self.start()
        roots = run / ".snapshots" / "roots.json"
        roots.write_text("{not json")
        checked = self.cli("submit", "rr", "T01", "--check", ok=False)
        self.assertIn("recipe recipes/demo/recipe.json cannot be resolved: the root declaration "
                      "in roots.json is unreadable", checked.stdout)
        refused = self.cli("assign", "rr", "T02", "--verify", "true", "--recipe",
                           "recipes/demo/recipe.json", "--as", "planner", ok=False)
        self.assertIn("--recipe recipes/demo/recipe.json cannot be resolved", refused.stderr)
        self.assertFalse((run / "T02-task.mdx").exists())

    def test_assign_refuses_escaping_or_misplaced_selections(self):
        self.cli("init", "rr", "--evidence-mode", "documents-only")
        for token, message in (("../x.json", "must not contain"), ("/abs/recipe.json", "must be relative")):
            refused = self.cli("assign", "rr", "T01", "--verify", "true", "--recipe", token,
                               "--as", "planner", ok=False)
            self.assertIn(message, refused.stderr)
            self.assertFalse((self.root / ".docket/runs/rr/T01-task.mdx").exists())
        refused = self.cli("assign", "rr", "orch", "--verify", "true", "--recipe", "r.json",
                           "--as", "planner", ok=False)
        self.assertIn("the aggregate and orchestrator-executed tasks take none", refused.stderr)
        made = self.cli("assign", "rr", "T01", "--verify", "true", "--recipe", "later/recipe.json",
                        "--as", "planner")
        self.assertIn("it cannot be read yet", made.stdout)

    def test_blocked_submission_stays_lightweight_and_claims_no_evidence(self):
        self.cli("init", "rr", "--evidence-mode", "documents-only")
        run = self.root / ".docket/runs/rr"
        self.cli("assign", "rr", "T01", "--file", "src/a.py", "--verify", "true",
                 "--recipe", "absent/recipe.json", "--as", "planner")
        self.f.fill_task(run / "T01-task.mdx")
        self.cli("batch", "rr", "--create", "M1", "--members", "T01", "--milestone",
                 "--command", "true", "--as", "planner")
        self.cli("batch", "rr", "--close", "M1", "--as", "planner")
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", "rr", "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx", blocked=True)
        normal = self.cli("submit", "rr", "T01", "--as", "implementor", ok=False)
        self.assertIn("recipe manifest absent/recipe.json is unreadable", normal.stderr)
        self.cli("submit", "rr", "T01", "--blocked", "--as", "implementor")
        manifest, _ = self.bundle(run)
        self.assertEqual({"reference": "absent/recipe.json", "run": "no",
                          "reason": "a blocked submission runs no verification"}, manifest["recipe"])
        self.assertEqual("blocked", manifest["verification"]["status"])
        self.assertFalse((run / ".recipe-evidence").exists())

    def test_patch_editing_the_recipe_is_flagged_as_changed_oracle(self):
        run = self.start(files=("src/a.py", "recipes/demo/recipe.md"))
        (self.root / "recipes" / "demo" / "recipe.md").write_text("Expect a different result.\n")
        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text().replace(
            "- `src/a.py:1` - implemented the feature.",
            "- `src/a.py:1` - implemented the feature.\n"
            "- `recipes/demo/recipe.md:1` - changed the recipe expectation."))
        self.cli("submit", "rr", "T01", "--as", "implementor")
        manifest, _ = self.bundle(run)
        self.assertEqual(["recipes/demo/recipe.md"], manifest["recipe"]["changed_in_patch"])
        self.cli("batch", "rr", "--verify", "M1", "--as", "implementor")
        packet = self.cli("review-packet", "rr", "--role", "reviewer", "--batch", "M1").stdout
        self.assertIn("changed oracle: this round's patch edits recipe file(s) recipes/demo/recipe.md",
                      packet)

    def test_re_review_refreezes_fresh_recipe_evidence(self):
        run = self.start(mode="documents-only")
        self.cli("submit", "rr", "T01", "--as", "implementor")
        first, _ = self.bundle(run)
        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Implemented the feature, verified its behavior, and noted a limit."))
        self.cli("decide", "rr", "T01", "--changes", "--re-review", "--as", "reviewer",
                 "--change", "1. The feature works: retest the restart path.")
        second, _ = self.bundle(run)
        self.assertEqual("re-review", second["trigger"])
        self.assertEqual("yes", second["recipe"]["run"])
        self.assertNotEqual(first["recipe"]["evidence_dir"], second["recipe"]["evidence_dir"])

    def test_re_review_refuses_documents_or_source_moved_during_verify(self):
        run = self.start()
        self.cli("submit", "rr", "T01", "--as", "implementor")
        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Implemented the feature, verified its behavior, and noted a limit."))
        task = run / "T01-task.mdx"
        original_task = task.read_text()
        flags = self.root / ".docket" / "flags"
        flags.mkdir()
        for flag, message in (("edit-task", "the task contract changed while the verification ran"),
                              ("edit-src", "the checkout changed while the verification ran")):
            with self.subTest(flag=flag):
                (flags / flag).write_text("")
                refused = self.cli("decide", "rr", "T01", "--changes", "--re-review", "--as", "reviewer",
                                   "--change", "1. The feature works: retest the restart path.", ok=False)
                self.assertIn(message, refused.stderr)
                ledger = json.loads((run / ".bundles" / "T01" / "rounds.json").read_text())
                self.assertEqual(["submission"], [entry["trigger"] for entry in ledger["entries"]])
                (flags / flag).unlink()
                task.write_text(original_task)
                (self.root / "src" / "a.py").write_text("allowed = 2\n")
        self.cli("decide", "rr", "T01", "--changes", "--re-review", "--as", "reviewer",
                 "--change", "1. The feature works: retest the restart path.")
        refrozen, where = self.bundle(run)
        self.assertEqual("re-review", refrozen["trigger"])
        self.assertEqual(task.read_bytes(), (where / "contract.mdx").read_bytes())

    def test_re_review_without_fresh_proof_is_refused(self):
        run = self.start(mode="documents-only")
        self.cli("submit", "rr", "T01", "--as", "implementor")
        report = run / "T01-report-01.mdx"
        report.write_text(report.read_text().replace(
            "Implemented the feature and verified its behavior.",
            "Implemented the feature, verified its behavior, and noted a limit."))
        (self.root / "no-proof.flag").write_text("")
        refused = self.cli("decide", "rr", "T01", "--changes", "--re-review", "--as", "reviewer",
                           "--change", "1. The feature works: retest the restart path.", ok=False)
        self.assertIn("re-verified recipe evidence cannot be frozen", refused.stderr)
        self.assertIn("recipe evidence doctor.json is unreadable", refused.stderr)
        ledger = json.loads((run / ".bundles" / "T01" / "rounds.json").read_text())
        self.assertEqual(["submission"], [entry["trigger"] for entry in ledger["entries"]])

    def test_plan_apply_selects_a_recipe_and_unselected_tasks_are_unchanged(self):
        self.write_recipe()
        plan = {
            "run": {"title": "Recipe", "objective": "Prove the journey.", "evidence_mode": "documents-only"},
            "tasks": [
                {"owner": "T01", "title": "With recipe", "goal": "Journey holds.",
                 "criterion": ["Journey holds."], "file": ["src/a.py"],
                 "verify": "python3 recipes/demo/verify.py", "recipe": "./recipes/demo/recipe.json"},
                {"owner": "T02", "title": "Without", "goal": "Plain works.",
                 "criterion": ["Plain works."], "file": ["src/b.py"], "verify": "true"},
            ],
            "milestones": [{"id": "M1", "members": ["T01", "T02"], "command": "true"}],
        }
        path = self.root / "plan.json"
        path.write_text(json.dumps(plan))
        self.cli("plan", "apply", "rr", "--file", str(path))
        run = self.root / ".docket/runs/rr"
        self.assertIn("verification_recipe: recipes/demo/recipe.json\n", (run / "T01-task.mdx").read_text())
        self.assertNotIn("verification_recipe", (run / "T02-task.mdx").read_text())
        prompt = self.cli("prompt", "rr", "T01", "--role", "implementor").stdout
        self.assertIn("verification recipe: recipes/demo/recipe.json (required)", prompt)
        self.assertNotIn("verification recipe", self.cli("prompt", "rr", "T02", "--role", "implementor").stdout)

    def test_approval_requires_a_frozen_run_of_the_selected_recipe(self):
        module = fixtures.load_cli("docket_recipe_tests")
        problem = module.recipe_approval_problem
        self.assertEqual("", problem({}, ""))
        self.assertIn("records no run of it", problem({}, "r.json"))
        self.assertIn("records no run of it", problem({"recipe": {"reference": "other.json", "run": "yes",
                                                                   "evidence": [{}]}}, "r.json"))
        self.assertIn("was not run", problem({"recipe": {"reference": "r.json", "run": "no",
                                                          "reason": "blocked"}}, "r.json"))
        self.assertEqual("", problem({"recipe": {"reference": "r.json", "run": "yes",
                                                  "evidence": [{"name": "a"}]}}, "r.json"))
        capture, problem = module.recipe_capture_for(
            Path("/nonexistent"), "---\nverification_recipe: r.json\n---\n", "")
        self.assertIsNone(capture)
        self.assertIn("registers no verify command", problem)


if __name__ == "__main__":
    unittest.main()
