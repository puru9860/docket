"""Recipe field round trips through actual assign, plan, submit, and declared roots."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import test_docket as fixtures


CRITERION = "The selected recipe freezes proof from this invocation."


class RecipePaths(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.cli = self.f.cli
        self.mod = fixtures.load_cli("recipe_path_tests")
        self.root = self.f.root

    def recipe(self, base: Path, reference: str, marker: str = "SELECTED") -> Path:
        directory = base / Path(reference).parent
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "recipe.json").write_text(json.dumps({
            "version": 1, "name": "path-proof", "instructions": "instructions.md",
            "helpers": ["helper.py"], "evidence": ["proof.json"],
        }))
        (directory / "instructions.md").write_text(f"{marker} instructions\n")
        (directory / "helper.py").write_text(
            "import json, os, uuid\nfrom pathlib import Path\n"
            "where = Path(os.environ['DOCKET_RECIPE_EVIDENCE_DIR'])\n"
            "assert list(where.iterdir()) == [], 'proof must come from this invocation'\n"
            f"proof = {{'marker': {marker!r}, 'pid': os.getpid(), 'invocation': str(uuid.uuid4())}}\n"
            "(where / 'proof.json').write_text(json.dumps(proof))\n")
        return directory

    def verify_wrapper(self, directory: Path):
        # The shell command has no quoting requirements of its own. The recipe's
        # actual helper still runs, at its real special-character filesystem path.
        (self.root / "verify.py").write_text(
            "import runpy\n" + f"runpy.run_path({str(directory / 'helper.py')!r}, run_name='__main__')\n")

    def init(self, run_id: str = "rr", roots: tuple[str, ...] = ()) -> Path:
        self.cli("init", run_id, "--evidence-mode", "git" if roots else "documents-only",
                 *(arg for spec in roots for arg in ("--root", spec)))
        return self.root / ".docket/runs" / run_id

    def assign(self, reference: str, run_id: str = "rr", owner: str = "T01", file: str = "src/a.py"):
        self.cli("assign", run_id, owner, "--as", "planner", "--title", "Preserve recipe path",
                 "--goal", "Capture the explicitly selected verification recipe.",
                 "--criterion", CRITERION, "--no-default-criteria", "--file", file,
                 "--verify", "python3 verify.py", "--recipe", reference)

    def ready(self, run: Path, file: str = "src/a.py", close: bool = True):
        if close:
            self.cli("batch", run.name, "--create", "M1", "--members", "T01", "--milestone",
                     "--command", "true", "--as", "planner")
            self.cli("batch", run.name, "--close", "M1", "--as", "planner")
        self.f.fill_scope(run / "T01-scope.mdx")
        self.cli("scope", run.name, "T01", "--submit")
        self.f.fill_task_report(run / "T01-report-01.mdx", criterion=CRITERION,
                                files=f"- `{file}:1` - exercised the selected recipe.")

    def assert_frozen(self, run: Path, reference: str, directory: Path, marker: str = "SELECTED"):
        self.cli("submit", run.name, "T01", "--as", "implementor")
        ledger = json.loads((run / ".bundles/T01/rounds.json").read_text())
        bundle = run / ".bundles/T01" / ledger["entries"][-1]["dir"]
        frozen = json.loads((bundle / "bundle.json").read_text())
        self.assertEqual(reference, frozen["recipe"]["reference"])
        self.assertEqual("yes", frozen["recipe"]["run"])
        self.assertEqual((directory / "instructions.md").read_bytes(),
                         (bundle / frozen["recipe"]["instructions"]["file"]).read_bytes())
        proof = json.loads((bundle / frozen["recipe"]["evidence"][0]["file"]).read_text())
        self.assertEqual(marker, proof["marker"])
        self.assertGreater(proof["pid"], 0)
        self.assertTrue(proof["invocation"])
        self.assertEqual(reference, self.mod.recipe_reference((run / "T01-task.mdx").read_text()))
        self.assertIn("intact", self.cli("bundle", run.name, "T01", "--list").stdout)

    def test_assign_round_trips_whitespace_hash_and_quote_characters(self):
        run = self.init()
        paths = [
            "recipes/project #1/recipe.json",
            'recipes/double" quote #1/recipe.json',
            "recipes/single' quote #1/recipe.json",
            'recipes/both\'" quotes #1/recipe.json',
            "recipes/tab\t#1/recipe.json",
            "recipes/unicode\u2028#1/recipe.json",
        ]
        for index, reference in enumerate(paths, 1):
            with self.subTest(reference=reference):
                self.recipe(self.root, reference)
                owner = f"T{index:02d}"
                self.assign(reference, owner=owner, file=f"src/{owner}.py")
                task = (run / f"{owner}-task.mdx").read_text()
                self.assertEqual(reference, self.mod.recipe_reference(task))
                # The recipe remains intact through unrelated flat-meta rewrites.
                meta, body = self.mod.parse(task)
                meta["actual_model"] = "test-model"
                self.assertEqual(reference, self.mod.recipe_reference(self.mod.render(meta, body)))

    def test_submit_freezes_the_actual_hash_and_quote_path(self):
        references = ["recipes/project #1/recipe.json", 'recipes/project "quote #1/recipe.json',
                      'recipes/project "quote\' #1/recipe.json']
        for index, reference in enumerate(references):
            with self.subTest(reference=reference):
                directory = self.recipe(self.root, reference)
                self.verify_wrapper(directory)
                run = self.init(f"submit{index}")
                self.assign(reference, run_id=run.name)
                self.ready(run)
                self.assert_frozen(run, reference, directory)

    def test_json_plan_apply_and_submit_preserve_the_recipe_reference(self):
        reference = "recipes/plan #1/recipe.json"
        directory = self.recipe(self.root, reference)
        self.verify_wrapper(directory)
        plan = {
            "run": {"title": "Recipe path", "objective": CRITERION, "evidence_mode": "documents-only"},
            "tasks": [{"owner": "T01", "title": "Path", "goal": CRITERION, "criterion": [CRITERION],
                       "no_default_criteria": True, "file": ["src/a.py"], "verify": "python3 verify.py",
                       "recipe": reference}],
            "milestones": [{"id": "M1", "members": ["T01"], "command": "true"}],
        }
        file = self.root / "plan.json"
        file.write_text(json.dumps(plan))
        self.cli("plan", "apply", "rr", "--file", str(file))
        run = self.root / ".docket/runs/rr"
        self.ready(run, close=False)
        self.assert_frozen(run, reference, directory)

    def test_qualified_root_freezes_only_the_named_recipe(self):
        relative = "recipes/project #1/recipe.json"
        for alias in ("api", "web"):
            checkout = self.f.repo(self.root / alias)
            self.recipe(checkout, relative, marker="SELECTED web" if alias == "web" else "DECOY api")
        directory = self.root / "web" / Path(relative).parent
        self.verify_wrapper(directory)
        run = self.init(roots=("api=./api", "web=./web"))
        reference = "web:" + relative
        self.assign(reference, file="web:src/a.py")
        self.ready(run, file="web:src/a.py")
        (self.root / "web/src/a.py").write_text("allowed = 2\n")
        self.assert_frozen(run, reference, directory, marker="SELECTED web")

    def test_ordinary_unquoted_references_and_absent_field_keep_their_bytes(self):
        run = self.init()
        reference = "recipes/ordinary/recipe.json"
        self.recipe(self.root, reference)
        self.assign(reference)
        task = (run / "T01-task.mdx").read_text()
        self.assertIn(f"verification_recipe: {reference}\n", task)
        self.assertEqual(reference, self.mod.recipe_reference(task))
        self.cli("assign", "rr", "T02", "--as", "planner", "--goal", "Plain task.",
                 "--criterion", "Plain works.", "--verify", "true")
        self.assertNotIn("verification_recipe:", (run / "T02-task.mdx").read_text())

    def test_prompts_show_decoded_reference_after_renderer_integration(self):
        # prompts.py belongs to the coordinator's integrated M1 checkout. This
        # assertion intentionally stays red here until its field read is decoded.
        reference = 'recipes/prompt "quoted" #1/recipe.json'
        directory = self.recipe(self.root, reference)
        self.verify_wrapper(directory)
        run = self.init()
        self.assign(reference)
        encoded = self.mod.parse((run / "T01-task.mdx").read_text())[0][self.mod.RECIPE_KEY]
        for role in ("implementor", "reviewer"):
            with self.subTest(role=role):
                if role == "reviewer":
                    self.ready(run)
                    self.cli("submit", "rr", "T01", "--as", "implementor")
                text = self.cli("prompt", "rr", "T01", "--role", role, "--max-tokens", "0").stdout
                self.assertIn(f"verification recipe: {reference} (", text)
                self.assertNotIn(f"verification recipe: {encoded} (", text)

    def test_global_flat_frontmatter_comments_and_quotes_are_unchanged(self):
        text = '---\nverify: echo ok # stripped\nordinary: "quoted value"\nfragment: path#fragment\n---\n\nbody\n'
        meta, body = self.mod.parse(text)
        self.assertEqual({"verify": "echo ok", "ordinary": '"quoted value"', "fragment": "path#fragment"}, meta)
        self.assertEqual("body\n", body)
        self.assertEqual((meta, body), self.mod.parse(self.mod.render(meta, body)))

    def test_codec_keeps_string_delimiters_but_backslash_paths_remain_refused(self):
        run = self.init()
        for reference in ('recipes/end slash\\', 'recipes/quoted "path" \\'):
            with self.subTest(reference=reference):
                encoded = self.mod.encode_recipe_reference(reference)
                task = self.mod.render({self.mod.RECIPE_KEY: encoded}, "")
                self.assertEqual(reference, self.mod.recipe_reference(task))
                refused = self.cli("assign", run.name, "T01", "--as", "planner",
                                   "--goal", "Invalid path.", "--criterion", CRITERION,
                                   "--verify", "true", "--recipe", reference, ok=False)
                self.assertNotEqual(0, refused.returncode)
                self.assertFalse((run / "T01-task.mdx").exists())


if __name__ == "__main__":
    unittest.main()
