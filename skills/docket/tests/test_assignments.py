"""Recorded role and milestone assignments extend the approved-model policy."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import test_docket as fixtures


class Assignments(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI("runTest")
        self.f.setUp()
        self.cli = self.f.cli
        self.user = Path(os.environ["XDG_CONFIG_HOME"]) / "docket/config.json"
        self.project = self.f.root / ".docket/config.json"

    def tearDown(self):
        self.f.tearDown()

    def write_config(self, path, **values):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, **values}))

    def init(self, name="demo"):
        self.cli("init", name, "--evidence-mode", "documents-only")
        return self.f.root / ".docket/runs" / name

    def assign(self, name="demo", owner="T01", *args):
        self.cli("assign", name, owner, "--file", "src/" + owner.lower() + ".py",
                 "--verify", "true", "--as", "planner", *args)
        self.f.fill_task(self.f.root / ".docket/runs" / name / (owner + "-task.mdx"))

    def batch(self, *args, name="demo", bid="M1", members="T01", ok=True):
        return self.cli("batch", name, "--create", bid, "--members", members,
                        "--milestone", "--command", "true", "--as", "planner", *args, ok=ok)

    def config(self, name="demo"):
        return json.loads(self.cli("config", name, "--json").stdout)

    def test_defaults_cover_all_roles_without_version_names(self):
        self.init()
        roles = self.config()["roles"]
        self.assertEqual({"planner", "implementor", "reviewer", "test_author"}, set(roles))
        for role, harness in (("planner", "claude"), ("implementor", "claude"),
                              ("reviewer", "codex"), ("test_author", "codex")):
            self.assertEqual(harness, roles[role]["harness"])
            self.assertEqual("", roles[role]["model"])
            self.assertFalse(roles[role]["pinned"])
            self.assertEqual("default", roles[role]["harness_source"])

    def test_resolution_order_and_sources_are_recorded_per_field(self):
        self.write_config(self.user, implementor_harness="claude", implementor_model="small",
                          reviewer_model="review", test_author_model="oracle",
                          implementor_allowed_models=["small", "project", "large"])
        self.write_config(self.project, implementor_model="project", reviewer_harness="claude")
        self.init()
        self.assign()
        self.batch("--implementor-model", "large", "--assignment-reason", "Shared invariant risk.")
        conf = self.config()
        self.assertEqual("project", conf["roles"]["implementor"]["model"])
        self.assertEqual("project", conf["roles"]["implementor"]["model_source"])
        impl = conf["milestones"]["M1"]["roles"]["implementor"]
        self.assertEqual("large", impl["model"])
        self.assertEqual("milestone", impl["model_source"])
        self.assertEqual("user", impl["harness_source"])
        self.assertEqual(str(self.user), impl["allowed_models_source_file"])
        self.assertEqual("Shared invariant risk.", impl["assignment_reason"])
        self.assertEqual("oracle", conf["roles"]["test_author"]["model"])

    def test_allowlist_refuses_project_and_milestone_and_dispatch_and_resume_bypasses(self):
        self.write_config(self.user, implementor_model="small", implementor_allowed_models=["small", "large"])
        self.write_config(self.project, implementor_model="outside")
        refused = self.cli("init", "bad", ok=False)
        self.assertIn("allowed", refused.stderr)
        self.assertFalse((self.f.root / ".docket/runs/bad").exists())
        self.project.unlink()
        run = self.init()
        self.assign()
        refused = self.batch("--implementor-model", "outside", "--assignment-reason", "Risk.", ok=False)
        self.assertIn("allowed", refused.stderr)
        self.assertFalse((run / ".batches/M1.json").exists())
        self.batch()
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        refused = self.cli("dispatch", "demo", "T01", "--session", "worker", "--register",
                           "--model", "outside", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertFalse((run / ".dispatch/T01.json").exists())
        self.cli("dispatch", "demo", "T01", "--session", "worker", "--register")
        refused = self.cli("resume", "demo", "T01", "--session", "new", "--register",
                           "--model", "outside", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("outside", refused.stderr)
        self.cli("resume", "demo", "T01", "--session", "new", "--register", "--model", "large")

    def test_pin_refuses_overrides_with_source_and_cannot_be_widened_by_run_policy(self):
        self.write_config(self.user, implementor_model="small", implementor_pinned=True,
                          implementor_allowed_models=["small", "large"])
        run = self.init()
        self.assign()
        refused = self.batch("--implementor-model", "small", "--assignment-reason", "Same.", ok=False)
        self.assertIn("pinned", refused.stderr)
        self.assertIn(str(self.user), refused.stderr)
        self.batch()
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        plan = run / "plan.mdx"
        plan.write_text(plan.read_text().replace("run: demo", "run: demo\nprimary_model: large"))
        refused = self.cli("dispatch", "demo", "T01", "--model", "large", "--session", "bad",
                           "--register", ok=False)
        self.assertNotEqual(0, refused.returncode)

    def test_later_config_changes_do_not_affect_existing_runs_or_new_batches(self):
        self.write_config(self.user, implementor_model="small")
        self.write_config(self.project, reviewer_model="review")
        run = self.init()
        self.assign()
        before = (run / "plan.mdx").read_bytes()
        self.user.write_text("broken json")
        self.project.write_text("broken json")
        self.batch()
        self.assertEqual(before, (run / "plan.mdx").read_bytes())
        conf = self.config()
        self.assertEqual("small", conf["milestones"]["M1"]["roles"]["implementor"]["model"])
        self.assertEqual("review", conf["roles"]["reviewer"]["model"])
        self.cli("status", "demo")

    def test_task_conflicts_refuse_before_batch_publication_and_have_working_recovery(self):
        run = self.init()
        self.assign("demo", "T01", "--model", "task-choice")
        refused = self.batch("--implementor-model", "milestone-choice", "--assignment-reason", "Risk.", ok=False)
        self.assertIn("T01", refused.stderr)
        self.assertIn("--clear-assignment", refused.stderr)
        self.assertFalse((run / ".batches/M1.json").exists())
        self.cli("assign", "demo", "T01", "--clear-assignment", "--as", "planner")
        self.assign(owner="T02")
        self.batch("--implementor-harness", "codex", "--implementor-model", "milestone-choice",
                   "--assignment-reason", "Same session.", members="T01,T02")
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        for owner in ("T01", "T02"):
            task = fixtures.parse_meta(run / (owner + "-task.mdx"))
            report = fixtures.parse_meta(run / (owner + "-report-01.mdx"))
            self.assertEqual("codex", task["harness"])
            self.assertEqual("milestone-choice", task["requested_model"])
            self.assertEqual("milestone-choice", report["requested_model"])
        refused = self.cli("assign", "demo", "T01", "--harness", "claude", "--as", "planner", ok=False)
        self.assertIn("milestone", refused.stderr)
        self.assertIn("codex", refused.stderr)

    def test_continuity_records_default_and_requires_reason_for_explicit_choice(self):
        run = self.init()
        self.assign()
        self.batch()
        record = json.loads((run / ".batches/M1.json").read_text())
        self.assertEqual("continue", record.get("reviewer_context"))
        self.assertTrue(record["continuity_reason"])
        self.assign(owner="T02")
        refused = self.batch("--reviewer-context", "fresh", bid="M2", members="T02", ok=False)
        self.assertIn("--continuity-reason", refused.stderr)
        self.batch("--reviewer-context", "fresh", "--continuity-reason", "Independent capability.",
                   bid="M2", members="T02")
        self.assertEqual("fresh", self.config()["milestones"]["M2"]["reviewer_context"])
        self.assertEqual("Independent capability.", self.config()["milestones"]["M2"]["continuity_reason"])

    def test_invalid_unreadable_or_unknown_config_is_not_absent(self):
        for data in ("{", '[]', '{"implementor_harness":"bogus"}',
                     '{"implementor_allowed_models":"small"}', '{"implementor_pinned":"yes"}',
                     '{"unknown_key":true}', '{"implementor_model":"x\\ny"}',
                     '{"implementor_model":"x", "implementor_model":"y"}'):
            self.user.parent.mkdir(parents=True, exist_ok=True)
            self.user.write_text(data)
            refused = self.cli("init", "invalid", ok=False)
            self.assertNotEqual(0, refused.returncode, data)
            self.assertIn(str(self.user), refused.stderr)
            self.assertFalse((self.f.root / ".docket/runs/invalid").exists())
        self.user.unlink()
        self.user.mkdir()
        refused = self.cli("config", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn(str(self.user), refused.stderr)

    def test_config_and_status_separate_requested_and_observed_models(self):
        self.write_config(self.user, implementor_model="requested", reviewer_model="independent")
        self.init()
        self.assign()
        self.batch()
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        self.cli("set-model", "demo", "T01", "--actual", "observed")
        text = self.cli("config", "demo").stdout
        for value in ("user", str(self.user), "requested", "independent", "allowed", "pin"):
            self.assertIn(value, text)
        status = self.cli("status", "demo").stdout
        self.assertIn("implementor=claude/requested", status)
        self.assertIn("reviewer=codex/independent", status)
        self.assertIn("observed", status)

    def test_milestone_override_requires_recorded_reason(self):
        self.init()
        self.assign()
        refused = self.batch("--reviewer-model", "review", ok=False)
        self.assertIn("--assignment-reason", refused.stderr)

    def test_project_cannot_override_or_remove_a_user_pin(self):
        self.write_config(self.user, reviewer_model="review", reviewer_pinned=True)
        self.write_config(self.project, reviewer_harness="codex")
        refused = self.cli("init", "pin-project", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("pinned", refused.stderr)
        self.assertIn(str(self.user), refused.stderr)
        self.write_config(self.project, reviewer_pinned=False)
        refused = self.cli("config", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn(str(self.project), refused.stderr)

    def test_task_policy_and_draft_only_recovery_refuse_before_mutation(self):
        self.write_config(self.user, implementor_model="small", implementor_allowed_models=["small"])
        run = self.init()
        refused = self.cli("assign", "demo", "T01", "--model", "other", "--as", "planner", ok=False)
        self.assertIn("allowed", refused.stderr)
        self.assertFalse((run / "T01-task.mdx").exists())
        self.assign()
        self.batch()
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        self.cli("dispatch", "demo", "T01", "--session", "worker", "--register")
        before = {p.name: p.read_bytes() for p in run.glob("T01-*.mdx")}
        refused = self.cli("assign", "demo", "T01", "--clear-assignment", "--as", "planner", ok=False)
        self.assertIn("already started", refused.stderr)
        self.assertEqual(before, {p.name: p.read_bytes() for p in run.glob("T01-*.mdx")})

    def test_milestone_model_is_used_by_dispatch_and_sources_survive_close(self):
        run = self.init()
        self.assign()
        self.batch("--implementor-model", "chosen/provider-model", "--assignment-reason", "Risk.",
                   "--test-author-model", "oracle", "--reviewer-model", "milestone-review")
        before = self.config()["milestones"]["M1"]
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        self.assertEqual(before, self.config()["milestones"]["M1"])
        self.cli("dispatch", "demo", "T01", "--session", "worker", "--register")
        dispatch = json.loads((run / ".dispatch/T01.json").read_text())
        self.assertEqual("chosen/provider-model", dispatch["model_requested"])
        self.assertEqual("unobserved", dispatch["model_observed"])
        prompt = self.cli("prompt", "demo", "T01", "--role", "reviewer").stdout
        self.assertIn("reviewer model: milestone-review", prompt)

    def test_invalid_project_and_dangling_config_do_not_create_run(self):
        self.write_config(self.project, implementor_model="small")
        self.project.write_text("invalid")
        refused = self.cli("init", "invalid-project", ok=False)
        self.assertIn(str(self.project), refused.stderr)
        self.assertFalse((self.f.root / ".docket/runs/invalid-project").exists())
        self.project.unlink()
        self.user.parent.mkdir(parents=True, exist_ok=True)
        self.user.symlink_to(self.user.parent / "missing.json")
        refused = self.cli("config", ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn(str(self.user), refused.stderr)

    def test_close_finishes_inheritance_after_a_partial_draft_write(self):
        run = self.init()
        self.assign()
        self.batch("--implementor-model", "chosen", "--assignment-reason", "Risk.")
        task = run / "T01-task.mdx"
        task.write_text("\n".join("requested_model: chosen" if line.startswith("requested_model:") else line
                                   for line in task.read_text().splitlines()) + "\n")
        self.assertEqual("chosen", fixtures.parse_meta(task)["requested_model"])
        self.cli("batch", "demo", "--close", "M1", "--as", "planner")
        report = fixtures.parse_meta(run / "T01-report-01.mdx")
        self.assertEqual("chosen", report["requested_model"])

    def test_config_source_paths_survive_frontmatter_comment_characters(self):
        config_home = self.user.parents[2] / 'config #quoted'
        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        self.user = config_home / "docket/config.json"
        self.write_config(self.user, implementor_model="chosen", implementor_pinned=True,
                          implementor_allowed_models=["chosen"])
        self.init()
        assignment = self.config()["roles"]["implementor"]
        for field in ("model_source_file", "pin_source_file", "allowed_models_source_file"):
            self.assertEqual(str(self.user), assignment[field])


if __name__ == "__main__":
    unittest.main()
