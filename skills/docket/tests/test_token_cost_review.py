"""Regression cases for review of the token-cost changes."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_token_cost as token_fixtures


class PlanReview(unittest.TestCase):
    def setUp(self):
        self.t = token_fixtures.TokenCost('runTest')
        self.t.setUp()
        self.cli = self.t.cli
        self.mod = self.t.mod

    def tearDown(self):
        self.t.tearDown()

    def test_plan_refuses_unknown_dependency_without_publishing_any_run(self):
        plan = self.t.plan()
        plan['tasks'][0]['depends_on'] = ['T09']
        refused = self.t.apply(plan, ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('T09', refused.stderr)
        self.assertFalse((self.t.f.root / '.docket/runs/demo').exists())
        self.assertFalse(list((self.t.f.root / '.docket/runs').glob('.plan-*')))

    def test_close_refuses_unassigned_task_dependency_then_accepts_forward_assignment(self):
        self.cli('init', 'demo', '--evidence-mode', 'documents-only')
        self.cli('assign', 'demo', 'T01', '--as', 'planner', '--depends-on', 'T09',
                 '--goal', 'The feature works.', '--criterion', 'The feature works.', '--verify', 'true')
        self.cli('batch', 'demo', '--create', 'M1', '--members', 'T01', '--milestone',
                 '--command', 'true', '--as', 'planner')
        run = self.t.f.root / '.docket/runs/demo'
        before = self.mod.batch_path(run, 'M1').read_bytes()
        refused = self.cli('batch', 'demo', '--close', 'M1', '--as', 'planner', ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('T01', refused.stderr)
        self.assertIn('T09', refused.stderr)
        self.assertEqual(before, self.mod.batch_path(run, 'M1').read_bytes())
        self.cli('assign', 'demo', 'T09', '--as', 'planner', '--goal', 'The predecessor works.',
                 '--criterion', 'The predecessor works.', '--verify', 'true')
        self.cli('batch', 'demo', '--close', 'M1', '--as', 'planner')
        self.assertEqual('closed', self.mod.read_batch(run, 'M1')['state'])

    def test_close_and_startup_check_unassigned_batch_edge_targets(self):
        self.t.apply()
        run = self.t.f.root / '.docket/runs/demo'
        batch = self.mod.read_batch(run, 'M1')
        batch.update(state='open', depends_on={'T01': ['T09']})
        self.mod.publish_json(self.mod.batch_path(run, 'M1'), batch)
        refused = self.cli('batch', 'demo', '--close', 'M1', '--as', 'planner', ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('T09', refused.stderr)
        batch['state'] = 'closed'
        self.mod.publish_json(self.mod.batch_path(run, 'M1'), batch)
        self.assertTrue(any('T09' in problem for problem in self.mod.quick_milestone_plan_problems(run, 'T01')))
        with patch.dict(os.environ, HERDR_ENV='1'):
            for command in (('launch', 'demo'), ('conduct', 'demo', '--once')):
                refused = self.cli(*command, ok=False)
                self.assertNotEqual(0, refused.returncode)
                self.assertIn('T09', refused.stderr)
        self.assertFalse((run / '.conductor/launch.json').exists())

    def test_startup_rechecks_task_dependencies_in_preexisting_closed_milestone(self):
        self.t.apply()
        run = self.t.f.root / '.docket/runs/demo'
        task = run / 'T01-task.mdx'
        task.write_text(task.read_text().replace('depends_on:', 'depends_on: T09'))
        problems = self.mod.quick_milestone_plan_problems(run, 'T01')
        self.assertTrue(any('T09' in problem for problem in problems))
        with patch.dict(os.environ, HERDR_ENV='1'):
            for command in (('launch', 'demo'), ('conduct', 'demo', '--once')):
                refused = self.cli(*command, ok=False)
                self.assertNotEqual(0, refused.returncode)
                self.assertIn('T09', refused.stderr)

    def test_plan_still_accepts_dependencies_assigned_later_in_the_same_file(self):
        plan = self.t.plan()
        predecessor = dict(plan['tasks'][0], owner='T09')
        plan['tasks'][0]['depends_on'] = ['T09']
        plan['tasks'].append(predecessor)
        plan['milestones'][0]['members'].append('T09')
        self.t.apply(plan)

    def test_self_review_is_bulleted_in_initial_and_correction_prompts(self):
        run = self.t.ready()
        initial = self.cli('prompt', 'demo', 'T01', '--role', 'implementor').stdout
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        self.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer', '--change', 'Add the regression assertion.')
        correction = self.cli('prompt', 'demo', 'T01', '--role', 'implementor').stdout
        for prompt in (initial, correction):
            self.assertGreaterEqual(len(re.findall(r'^\s+- ', prompt, re.M)), 7)
            for phrase in ('the reviewer reruns claims instead of trusting them',
                           'see it fail once against the old code', 'never on fixed sleeps',
                           '--baseline --checkout`', 'git stash'):
                self.assertIn(phrase, prompt)
            self.assertLess(prompt.index('reviewer reruns claims'), prompt.index('Fill `.docket/runs/demo/T01-report-'))

    def export(self, *args):
        output = self.cli('bundle', 'demo', 'T01', *args, '--checkout').stdout
        path = Path(next(line.split(': ', 1)[1] for line in output.splitlines() if line.startswith('materialized ')))
        self.assertTrue(path.is_dir())
        self.assertTrue(path.parent.name.startswith("docket-checkout-"))
        self.assertEqual(Path(tempfile.gettempdir()).resolve(), path.parent.parent)
        self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
        self.addCleanup(shutil.rmtree, path.parent)
        self.assertFalse(self.t.f.root == path or self.t.f.root in path.parents)
        return path

    def test_round_two_correction_and_re_review_export_fresh_private_trees(self):
        run = self.t.ready(git=True)
        source = self.t.f.root / 'src/a.py'
        source.write_text('round one\n')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        first = self.export()
        first_baseline = self.export('--baseline')
        self.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer', '--change', 'Handle the second input.')
        self.cli('dispatch', 'demo', 'T01', '--session', 'worker')
        source.write_text('round two\n')
        self.t.f.fill_task_report(run / 'T01-report-02.mdx')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        second = self.export()
        second_baseline = self.export('--baseline')
        report = run / 'T01-report-02.mdx'
        report.write_text(report.read_text() + '\nAdditional observed evidence.\n')
        source.write_text('round two re-reviewed\n')
        self.cli('decide', 'demo', 'T01', '--changes', '--re-review', '--as', 'reviewer',
                 '--change', 'Check the remaining input boundary.')
        reviewed = self.export('--round', '2')
        self.assertEqual(5, len({first, second, reviewed, first_baseline, second_baseline}))
        self.assertEqual('round one\n', (first / 'src/a.py').read_text())
        self.assertEqual('round two\n', (second / 'src/a.py').read_text())
        self.assertEqual('round two re-reviewed\n', (reviewed / 'src/a.py').read_text())
        self.assertEqual('baseline\n', (first_baseline / 'src/a.py').read_text())
        self.assertEqual('round one\n', (second_baseline / 'src/a.py').read_text())
        self.assertEqual('draft', self.mod.state_of(run, 'T01')[1])

    def test_automatic_export_does_not_allocate_under_a_checkout_tmpdir(self):
        self.t.ready(git=True)
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        allocated = []
        make = tempfile.mkdtemp
        def outside(*args, **kwargs):
            parent = Path(kwargs['dir']).resolve()
            if kwargs.get('prefix') == 'index-':
                self.assertEqual(self.mod.bundle_work(self.t.f.root / '.docket/runs/demo').resolve(), parent)
                return make(*args, **kwargs)
            self.assertNotEqual(self.t.f.root, parent)
            self.assertNotIn(self.t.f.root, parent.parents)
            path = make(*args, **kwargs)
            allocated.append(Path(path))
            return path
        with patch.dict(os.environ, TMPDIR=str(self.t.f.root)), patch.object(tempfile, 'mkdtemp', side_effect=outside), contextlib.redirect_stdout(io.StringIO()):
            self.mod.materialize_bundle_tree(self.t.f.root / '.docket/runs/demo', 'T01', None)
        self.assertTrue(allocated)
        parent = allocated[0]
        self.assertTrue(parent.name.startswith('docket-checkout-'))
        self.addCleanup(shutil.rmtree, parent)
        self.assertEqual('baseline\n', (parent / 'tree/src/a.py').read_text())

    def test_two_projects_with_identical_ids_get_distinct_checkouts(self):
        self.t.ready(git=True)
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        first = self.export()
        other = token_fixtures.TokenCost('runTest')
        other.setUp()
        try:
            other.ready(git=True)
            other.cli('submit', 'demo', 'T01', '--as', 'implementor')
            output = other.cli('bundle', 'demo', 'T01', '--checkout').stdout
            second = Path(next(line.split(': ', 1)[1] for line in output.splitlines() if line.startswith('materialized ')))
            self.assertTrue(second.parent.name.startswith("docket-checkout-"))
            self.assertEqual(Path(tempfile.gettempdir()).resolve(), second.parent.parent)
            self.addCleanup(shutil.rmtree, second.parent)
            self.assertNotEqual(first, second)
            self.assertEqual((first / 'src/a.py').read_bytes(), (second / 'src/a.py').read_bytes())
        finally:
            other.tearDown()


class ConductorReview(unittest.TestCase):
    def setUp(self):
        self.t = token_fixtures.ConductorTokenCost('runTest')
        self.t.setUp()
        self.c, self.mod = self.t.c, self.t.mod
        self.c.tick()
        self.c.cli('set-model', 'demo', 'T01', '--actual', 'observed-test-model')
        self.c.f.fill_scope(self.c.run / 'T01-scope.mdx')
        self.c.cli('scope', 'demo', 'T01', '--submit')
        self.c.f.fill_task_report(self.c.run / 'T01-report-01.mdx', files='- `src/t01.py:1` - done.')
        self.c.cli('submit', 'demo', 'T01', '--as', 'implementor')

    def tearDown(self):
        self.t.tearDown()

    def test_suite_refusal_stalls_once_per_event_identity_and_loop_stays_alive(self):
        g = self.mod.conduct_once.__globals__
        events = self.mod.derive_events(self.c.run, 'implementor')
        ready = next(e for e in events if ':verify-ready:' in e['key'])
        original_derive = g['derive_events']
        def derived(d, role):
            return [ready] if role == 'implementor' else original_derive(d, role)
        def refuse(*_):
            self.mod.die('batch M1: the checkout moved while the full-suite command ran')
        with patch.dict(g, derive_events=derived, cmd_batch_verify=refuse), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.mod.conduct_once(self.c.run))
            self.assertFalse(self.mod.conduct_once(self.c.run))
            incidents = self.mod.list_incidents(self.c.run)
            self.assertEqual(1, len(incidents))
            self.assertEqual('planner', incidents[0]['recipient'])
            self.assertIn('checkout moved', incidents[0]['cause'])
            self.assertTrue(any('checkout moved' in e['message'] for e in original_derive(self.c.run, 'planner')))
            ready = dict(ready, revision='sha256:' + 'a' * 64)
            self.assertFalse(self.mod.conduct_once(self.c.run))
            self.assertEqual(2, len(self.mod.list_incidents(self.c.run)))
        self.assertFalse(any(':verify-ready:' in p['text'] for p in self.c.state()['prompts']))

    def test_real_member_movement_refusal_does_not_exit_conductor(self):
        batch = self.mod.read_batch(self.c.run, 'M1')
        command = "python3 -c \"from pathlib import Path; p=Path('.docket/runs/demo/T01-report-01.mdx'); p.write_text(p.read_text()+'\\nConcurrent edit.\\n')\""
        batch['verify_command'] = command
        self.mod.publish_json(self.mod.batch_path(self.c.run, 'M1'), batch)
        result = self.c.tick()
        self.assertEqual(0, result.returncode)
        incidents = self.mod.list_incidents(self.c.run)
        self.assertEqual(1, len(incidents))
        self.assertIn('member bundle', incidents[0]['cause'])
        self.assertNotIn('verification', self.mod.read_batch(self.c.run, 'M1'))
        self.assertFalse(any('You are the reviewer' in p['text'] for p in self.c.state()['prompts']))


if __name__ == '__main__':
    unittest.main()
