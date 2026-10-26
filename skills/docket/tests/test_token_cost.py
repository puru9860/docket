"""Token-cost paths exercise the same gates without extra worker turns."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_docket as fixtures
import test_conductor as conductor_fixtures


class TokenCost(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI('runTest')
        self.f.setUp()
        self.cli = self.f.cli
        self.mod = fixtures.load_cli('token_cost_tests')
        self.cwd = Path.cwd()
        os.chdir(self.f.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.f.tearDown()

    def plan(self):
        return {'run': {'title': 'Small capability', 'objective': 'The feature works.',
                        'approach': 'Change one behavior.', 'evidence_mode': 'documents-only'},
                'roots': [], 'tasks': [{'owner': 'T01', 'title': 'Feature',
                    'goal': 'The feature works.', 'criterion': ['The feature works.'],
                    'file': ['src/a.py'], 'verify': 'true', 'no_default_criteria': True}],
                'milestones': [{'id': 'M1', 'members': ['T01'], 'command': 'true'}]}

    def apply(self, data=None, ok=True, run='demo'):
        path = self.f.root / 'plan.json'
        path.write_text(json.dumps(data or self.plan()))
        return self.cli('plan', 'apply', run, '--file', str(path), ok=ok)

    def ready(self, git=False):
        data = self.plan()
        if git:
            subprocess.run(['git', 'init', '-q'], cwd=self.f.root, check=True)
            (self.f.root / '.gitignore').write_text('.docket/\nplan.json\n')
            (self.f.root / 'src').mkdir()
            (self.f.root / 'src/a.py').write_text('baseline\n')
            subprocess.run(['git', 'add', '.'], cwd=self.f.root, check=True)
            subprocess.run(['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test',
                            'commit', '-qm', 'base'], cwd=self.f.root, check=True)
            (self.f.root / 'untracked.bin').write_bytes(b'\x00baseline\xff')
            (self.f.root / 'executable').write_text('#!/bin/sh\nexit 0\n')
            (self.f.root / 'executable').chmod(0o755)
            (self.f.root / 'link').symlink_to('src/a.py')
            data['run']['evidence_mode'] = 'git'
        self.apply(data)
        run = self.f.root / '.docket/runs/demo'
        self.cli('dispatch', 'demo', 'T01', '--session', 'worker', '--register')
        self.f.fill_scope(run / 'T01-scope.mdx')
        self.cli('scope', 'demo', 'T01', '--submit')
        self.f.fill_task_report(run / 'T01-report-01.mdx')
        return run

    def test_plan_publishes_complete_closed_run_and_refuses_duplicate(self):
        self.apply()
        run = self.f.root / '.docket/runs/demo'
        self.assertEqual('closed', self.mod.list_batches(run)[0]['state'])
        before = {str(p.relative_to(run)): p.read_bytes() for p in run.rglob('*') if p.is_file()}
        self.assertNotEqual(0, self.apply(ok=False).returncode)
        self.assertEqual(before, {str(p.relative_to(run)): p.read_bytes() for p in run.rglob('*') if p.is_file()})
        self.assertFalse(list(run.parent.glob('.plan-*')))
        self.cli('dispatch', 'demo', 'T01', '--session', 'worker', '--register')

    def test_invalid_late_milestone_intent_cycle_and_json_leave_no_run(self):
        cases = []
        data = self.plan(); data['milestones'][0]['command'] = ''; cases.append(data)
        data = self.plan(); data['tasks'][0]['goal'] = ''; cases.append(data)
        data = self.plan(); data['milestones'][0]['reviewer_context'] = 'fresh'; cases.append(data)
        data = self.plan(); data['tasks'][0]['depends_on'] = ['T01']; cases.append(data)
        data = self.plan(); data['tasks'][0]['unknown'] = 'bad'; cases.append(data)
        data = self.plan(); data['tasks'][0].update(verify_timeout=900, **{'verify-timeout': 800}); cases.append(data)
        data = self.plan(); data['milestones'][0]['members'] *= 2; cases.append(data)
        for data in cases:
            with self.subTest(data=data):
                refused = self.apply(data, ok=False)
                self.assertNotEqual(0, refused.returncode, refused.stdout)
                self.assertFalse((self.f.root / '.docket/runs/demo').exists())
                self.assertFalse(list((self.f.root / '.docket/runs').glob('.plan-*')))

    def test_plan_errors_name_the_json_path_without_another_commands_usage(self):
        data = self.plan(); data['tasks'][0]['risk'] = 'medium'
        refused = self.apply(data, ok=False)
        self.assertIn('tasks[0].risk must be one of: low, high', refused.stderr)
        self.assertNotIn('usage:', refused.stderr)
        data = self.plan(); data['tasks'][0]['verify_timeout'] = 'soon'
        refused = self.apply(data, ok=False)
        self.assertIn('tasks[0]:', refused.stderr)
        self.assertNotIn('usage:', refused.stderr)
        self.assertFalse((self.f.root / '.docket/runs/demo').exists())

    def test_plan_accepts_risks_and_out_of_scope_as_lists(self):
        data = self.plan()
        data['run'].update(risks=['Malformed model output.', 'Lost user additions.'],
                           out_of_scope=['No migration.'])
        self.apply(data)
        plan = (self.f.root / '.docket/runs/demo/plan.mdx').read_text()
        self.assertIn('- Malformed model output.\n- Lost user additions.', plan)
        self.assertIn('- No migration.', plan)

    def test_plan_preserves_assignment_pins_and_refuses_bad_roots(self):
        config = Path(os.environ['XDG_CONFIG_HOME']) / 'docket/config.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'implementor_model': 'small', 'implementor_pinned': True}))
        data = self.plan()
        data['milestones'][0].update(implementor_model='other', assignment_reason='Test refusal.')
        self.assertNotEqual(0, self.apply(data, ok=False).returncode)
        self.assertFalse((self.f.root / '.docket/runs/demo').exists())
        config.unlink()
        data = self.plan(); data['run']['evidence_mode'] = 'git'; data['roots'] = ['bad=/missing']
        self.assertNotEqual(0, self.apply(data, ok=False).returncode)
        self.assertFalse((self.f.root / '.docket/runs/demo').exists())

    def test_submit_feedback_is_one_operation_and_check_has_no_side_effect(self):
        run = self.ready()
        before = (run / 'T01-report-01.mdx').read_bytes()
        self.cli('submit', 'demo', 'T01', '--check', '--feedback', 'none')
        self.assertEqual(before, (run / 'T01-report-01.mdx').read_bytes())
        self.assertFalse((run / '.feedback').exists())
        self.cli('submit', 'demo', 'T01', '--as', 'implementor', '--feedback', 'none')
        feedback = self.cli('feedback', 'demo', '--list').stdout
        self.assertIn('1 observation', feedback)
        self.assertIn('implementor', feedback)

    def test_completion_contains_verdicts_suite_and_finish_archives_disarms(self):
        run = self.ready()
        self.cli('arm', 'demo', '--role', 'planner')
        self.assertNotEqual(0, self.cli('finish', 'demo', '--feedback', 'none', ok=False).returncode)
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        self.cli('batch', 'demo', '--verify', 'M1', '--as', 'implementor')
        self.cli('batch', 'demo', '--approve', 'M1', '--as', 'reviewer')
        wake = self.cli('events', 'demo', '--role', 'planner', '--peek').stdout
        for required in ('M1: approved', 'T01: approved', "suite 'true' passed (exit 0)", 'docket finish demo'):
            self.assertIn(required, wake)
        self.cli('finish', 'demo', '--feedback', 'none')
        self.assertFalse((self.f.root / '.docket/watch.conf').exists())
        self.assertIn('planner', self.cli('feedback', 'demo', '--list').stdout)
        # A moved approval is refused even after completion.
        batch = self.mod.list_batches(run)[0]
        batch['milestone_decision']['member_bundles'] = {}
        self.mod.publish_json(self.mod.batch_path(run, 'M1'), batch)
        self.assertNotEqual(0, self.cli('finish', 'demo', '--feedback', 'none', ok=False).returncode)

    def test_provider_warning_is_visible_but_only_exhaustion_derives_wake(self):
        run = self.f.init5()
        (run / '.harness-sessions.jsonl').write_text(json.dumps({
            'at': '2026-01-01T00:00:00Z', 'harness': 'codex', 'session_id': 's',
            'role': 'planner', 'owner': '-', 'cwd': str(self.f.root)}) + '\n')
        globals_ = self.mod.codex_budget_events.__globals__
        limits = {'primary': {'used_percent': 75, 'resets_at': 9999999999}}
        with patch.dict(globals_, codex_rate_limits=lambda: limits):
            self.assertEqual([], self.mod.codex_budget_events(run))
            self.assertIn('Informational', self.mod.codex_budget_events(run, actionable=False)[0][1])
            limits['primary']['used_percent'] = 100
            self.assertIn('exhausted', self.mod.codex_budget_events(run)[0][1])

    def test_materialize_baseline_and_frozen_tree_preserves_repository_bytes(self):
        run = self.ready(git=True)
        (self.f.root / 'src/a.py').write_text('changed\n')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        def fingerprint():
            return {str(p.relative_to(self.f.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.f.root.rglob('*') if p.is_file() and '.docket' not in p.parts}
        before = fingerprint()
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'frozen'
            self.cli('bundle', 'demo', 'T01', '--checkout', str(dest))
            self.assertEqual('changed\n', (dest / 'src/a.py').read_text())
            self.assertEqual(b'\x00baseline\xff', (dest / 'untracked.bin').read_bytes())
            self.assertEqual('src/a.py', os.readlink(dest / 'link'))
            self.assertEqual(0o755, (dest / 'executable').stat().st_mode & 0o777)
            baseline = Path(directory) / 'baseline'
            self.cli('bundle', 'demo', 'T01', '--baseline', '--checkout', str(baseline))
            self.assertEqual('baseline\n', (baseline / 'src/a.py').read_text())
            self.assertNotEqual(0, self.cli('bundle', 'demo', 'T01', '--checkout', str(dest), ok=False).returncode)
        self.assertEqual(before, fingerprint())
        self.assertNotEqual(0, self.cli('bundle', 'demo', 'T01', '--checkout', str(self.f.root / 'unsafe'), ok=False).returncode)
        self.assertFalse((self.f.root / 'unsafe').exists())

    def test_damaged_bundle_and_missing_baseline_refuse_checkout(self):
        run = self.ready(git=True)
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        entry = self.mod.latest_bundle(run, 'T01')
        where = self.mod.bundle_dir(run, 'T01', entry)
        (where / 'report.mdx').write_text('tampered')
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'result'
            refused = self.cli('bundle', 'demo', 'T01', '--checkout', str(dest), ok=False)
            self.assertIn('DAMAGED', refused.stderr)
            self.assertFalse(dest.exists())
            (run / '.snapshots/T01.json').unlink()
            self.assertNotEqual(0, self.cli('bundle', 'demo', 'T01', '--baseline', '--checkout', str(dest), ok=False).returncode)
            self.assertFalse(dest.exists())

    def test_observation_uses_only_registered_session_latest_call_and_check_is_read_only(self):
        run = self.ready()
        dispatch = self.mod.read_dispatch(run, 'T01')
        registration = self.mod.read_registration('worker')
        ref = {'harness': 'claude', 'session_id': 'native'}
        registration['native_harness'] = ref
        self.mod.publish_json(self.mod.session_path('worker'), registration)
        (run / '.harness-sessions.jsonl').write_text(json.dumps({
            'at': '2026-01-01T00:00:00Z', **ref, 'role': 'implementor', 'owner': 'T01'}) + '\n')
        g = self.mod.submission_model.__globals__
        calls = [{'at': '2026-01-02T00:00:00Z', 'model': 'observed-small'},
                 {'at': '2026-01-03T00:00:00Z', 'model': 'observed-new'}]
        with patch.dict(g, harness_session=lambda _: ref, session_transcripts=lambda _: [],
                        claude_calls=lambda _: (calls, [], '')):
            self.assertEqual('observed-new', self.mod.submission_model(run, 'T01'))
            registration['native_harness'] = {**ref, 'session_id': 'other'}
            self.mod.publish_json(self.mod.session_path('worker'), registration)
            self.assertEqual('', self.mod.submission_model(run, 'T01'))

    def test_missing_observation_keeps_actual_model_refusal(self):
        run = self.ready()
        report = run / 'T01-report-01.mdx'
        report.write_text(report.read_text().replace('requested_model: ', 'requested_model: requested'))
        refused = self.cli('submit', 'demo', 'T01', '--check', ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('actual_model', refused.stdout)
        self.assertEqual('', fixtures.parse_meta(report).get('actual_model', ''))

    def test_observed_model_is_persisted_before_verification_and_optional_feedback_cannot_block(self):
        run = self.ready()
        report = run / 'T01-report-01.mdx'
        text = report.read_text().replace('requested_model: ', 'requested_model: requested')
        report.write_text(text)
        args = argparse.Namespace(run='demo', owner='T01', check=True, blocked=False,
            background=False, background_worker=False, skip_verify=False, skip_verify_reason='',
            verify_timeout=None, as_role='implementor', feedback='none')
        g = self.mod.submit_locked.__globals__
        with patch.dict(g, submission_model=lambda *_: 'observed', cmd_feedback=lambda _: (_ for _ in ()).throw(OSError('unavailable'))), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.mod.cmd_submit(args)
            self.assertEqual(text, report.read_text())
            args.check = False
            self.mod.cmd_submit(args)
        self.assertEqual('submitted', fixtures.parse_meta(report)['status'])
        self.assertEqual('observed', fixtures.parse_meta(report)['actual_model'])
        self.assertEqual('observed', fixtures.parse_meta(run / 'T01-task.mdx')['actual_model'])
        self.assertEqual('observed', self.mod.read_dispatch(run, 'T01')['model_observed'])
        manifest = self.mod.load_bundle(run, 'T01', self.mod.latest_bundle(run, 'T01'))
        self.assertEqual('observed', manifest['verification']['model_observed'])

    def test_plan_carries_constraints_and_budget_policy_and_parses_its_documented_example(self):
        data = self.plan()
        data['run'].update(risks='Existing data must survive.', out_of_scope='No migration.',
                           policy={'token_budget': 1000, 'review_reserve': 100, 'premium_models': 'capable'})
        self.apply(data)
        plan = (self.f.root / '.docket/runs/demo/plan.mdx').read_text()
        self.assertIn('token_budget: 1000', plan)
        self.assertIn('No migration.', plan)
        self.assertIn('Existing data must survive.', plan)
        help_text = self.cli('help', 'planner').stdout
        example = help_text.split('```json\n', 1)[1].split('```', 1)[0]
        example = json.loads(example)
        example['run']['evidence_mode'] = 'documents-only'
        example['roots'] = []
        self.apply(example, run='example')
        self.assertIn('default to one milestone', help_text.lower())
        self.assertNotIn('Read `docket assign --help`', help_text)
        self.assertLess(len(help_text), 8000)

    def test_duplicate_json_keys_and_invalid_policy_leave_no_run(self):
        path = self.f.root / 'bad.json'
        path.write_text('{"tasks": [], "tasks": []}')
        self.assertNotEqual(0, self.cli('plan', 'apply', 'demo', '--file', str(path), ok=False).returncode)
        data = self.plan(); data['run']['policy'] = {'token_budget': 100, 'review_reserve': 100}
        self.assertNotEqual(0, self.apply(data, ok=False).returncode)
        self.assertFalse((self.f.root / '.docket/runs/demo').exists())

    def test_frozen_baseline_export_uses_bundle_even_after_snapshot_capture_is_removed(self):
        run = self.ready(git=True)
        (self.f.root / 'src/a.py').write_text('changed\n')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        (run / '.snapshots/T01.json').unlink()
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'baseline'
            self.cli('bundle', 'demo', 'T01', '--round', '1', '--baseline', '--checkout', str(dest))
            self.assertEqual('baseline\n', (dest / 'src/a.py').read_text())


    def test_materialization_never_runs_measured_checkout_smudge_filters(self):
        run = self.ready(git=True)
        marker = self.f.root / 'smudge-ran'
        (self.f.root / '.git/info/attributes').write_text('src/a.py filter=docket\n')
        subprocess.run(['git', 'config', 'filter.docket.smudge', 'touch ' + str(marker)],
                       cwd=self.f.root, check=True)
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'result'
            self.cli('bundle', 'demo', 'T01', '--checkout', str(dest))
            self.assertEqual('baseline\n', (dest / 'src/a.py').read_text())
        self.assertFalse(marker.exists())

    def test_multi_root_export_keeps_explicit_roots_under_their_aliases(self):
        data = self.plan()
        data['run']['evidence_mode'] = 'git'
        data['roots'] = []
        for alias in ('app', 'lib'):
            repo = self.f.root / alias
            repo.mkdir()
            subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
            (repo / 'value').write_text(alias + ' original\n')
            subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
            subprocess.run(['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test',
                            'commit', '-qm', 'base'], cwd=repo, check=True)
            data['roots'].append(alias + '=' + str(repo))
        data['tasks'][0]['file'] = ['app:value', 'lib:value']
        self.apply(data)
        run = self.f.root / '.docket/runs/demo'
        self.cli('dispatch', 'demo', 'T01', '--session', 'worker', '--register')
        self.f.fill_scope(run / 'T01-scope.mdx')
        self.cli('scope', 'demo', 'T01', '--submit')
        self.f.fill_task_report(run / 'T01-report-01.mdx', files='none')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'result'
            self.cli('bundle', 'demo', 'T01', '--checkout', str(dest))
            for alias in ('app', 'lib'):
                self.assertEqual(alias + ' original\n', (dest / alias / 'value').read_text())


    def test_background_submission_checks_authority_before_observation_or_launch(self):
        run = self.ready()
        report = run / 'T01-report-01.mdx'
        original = report.read_bytes()
        args = argparse.Namespace(run='demo', owner='T01', check=False, blocked=False,
            background=True, background_worker=False, skip_verify=False, skip_verify_reason='',
            verify_timeout=None, as_role='reviewer', feedback='none')
        with patch.dict(self.mod.submit_locked.__globals__, submission_model=lambda *_: 'observed'), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.mod.cmd_submit(args)
        self.assertEqual(original, report.read_bytes())
        self.assertFalse((run / '.submit-jobs').exists())



class ConductorTokenCost(unittest.TestCase):
    def setUp(self):
        self.c = conductor_fixtures.Conductor('runTest')
        self.c.setUp()
        self.mod = fixtures.load_cli('token_conductor_tests')
        self.cwd = Path.cwd()
        os.chdir(self.c.f.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.c.tearDown()

    def test_ready_suite_runs_without_worker_wake_and_records_conductor_actor(self):
        self.c.tick()
        self.c.cli('set-model', 'demo', 'T01', '--actual', 'observed-test-model')
        self.c.f.fill_scope(self.c.run / 'T01-scope.mdx')
        self.c.cli('scope', 'demo', 'T01', '--submit')
        self.c.f.fill_task_report(self.c.run / 'T01-report-01.mdx', files='- `src/t01.py:1` - done.')
        self.c.cli('submit', 'demo', 'T01', '--as', 'implementor')
        self.c.tick()
        batch = self.mod.read_batch(self.c.run, 'M1')
        self.assertEqual('passed', batch['verification']['status'])
        self.assertEqual('conductor', batch['verification']['actor'])
        self.assertFalse(any(':verify-ready' in p['text'] for p in self.c.state()['prompts']))
        self.assertEqual(1, len([p for p in self.c.state()['prompts'] if 'You are the reviewer' in p['text']]))

    def test_prompts_omit_help_and_continuations_repeat_only_changed_sections(self):
        self.c.tick()
        initial = self.c.state()['prompts'][0]['text']
        self.assertNotIn('docket help implementor', initial)
        self.assertIn('--baseline --checkout', initial)
        self.assertIn('Never sleep or poll', initial)
        self.assertIn('--feedback TEXT|none', initial)
        record = self.c.record()
        batch = self.mod.read_batch(self.c.run, 'M1')
        ev = self.mod.derive_events(self.c.run, 'implementor')[0]
        continued = self.mod.worker_prompt(self.c.run, batch, record, ev)
        self.assertLess(len(continued), len(initial) // 2)
        self.assertIn('Continued context', continued)
        self.assertNotIn('# Task contract', continued)

    def test_launch_opens_plain_conductor_and_arms_planner_once(self):
        # The fake records pane run but deliberately never executes a live loop.
        fake = self.c.bin / 'herdr'
        # Real `herdr pane run` exits 0 and prints nothing.
        source = fake.read_text().replace("if a[:2] == ['pane', 'get']:",
            "if a[:2] == ['pane', 'run']:\n    save()\n    sys.exit(0)\nif a[:2] == ['pane', 'get']:")
        fake.write_text(source)
        result = self.c.cli('launch', 'demo')
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('conductor started', result.stdout)
        calls = self.c.state()['calls']
        self.assertEqual(1, len([a for a in calls if a[:2] == ['pane', 'run']]))
        self.assertIn('demo planner', (self.c.f.root / '.docket/watch.conf').read_text())
        self.assertNotEqual(0, self.c.cli('launch', 'demo', ok=False).returncode)
        self.assertEqual(1, len([a for a in self.c.state()['calls'] if a[:2] == ['pane', 'run']]))

    def test_failed_suite_stays_frozen_and_is_not_rerun_or_sent_to_reviewer(self):
        path = self.mod.batch_path(self.c.run, 'M1')
        batch = self.mod.read_batch(self.c.run, 'M1')
        batch['verify_command'] = 'exit 1'
        self.mod.publish_json(path, batch)
        self.c.tick()
        self.c.cli('set-model', 'demo', 'T01', '--actual', 'observed-test-model')
        self.c.f.fill_scope(self.c.run / 'T01-scope.mdx')
        self.c.cli('scope', 'demo', 'T01', '--submit')
        self.c.f.fill_task_report(self.c.run / 'T01-report-01.mdx', files='- `src/t01.py:1` - done.')
        self.c.cli('submit', 'demo', 'T01', '--as', 'implementor')
        self.c.tick()
        failed = self.mod.read_batch(self.c.run, 'M1')['verification']
        self.assertEqual('failed', failed['status'])
        self.c.tick()
        self.assertEqual(failed, self.mod.read_batch(self.c.run, 'M1')['verification'])
        self.assertFalse(any('You are the reviewer' in p['text'] for p in self.c.state()['prompts']))

    def test_premium_launch_refusal_wakes_planner_without_launching_worker(self):
        plan = self.c.run / 'plan.mdx'
        plan.write_text(plan.read_text().replace('correction_limit: 2',
            'correction_limit: 2\npremium_models: small\ntoken_budget: 100\nreview_reserve: 50'))
        (self.c.run / '.harness-sessions.jsonl').write_text(json.dumps({
            'at': '2026-01-01T00:00:00Z', 'harness': 'claude', 'session_id': 'missing',
            'role': 'planner', 'owner': '-', 'cwd': str(self.c.f.root)}) + '\n')
        self.c.tick()
        self.assertEqual([], self.c.launches())
        events = self.c.cli('events', 'demo', '--role', 'planner', '--peek').stdout
        self.assertIn('premium launch refused', events)


    def test_continued_reviewer_retains_duties_and_receives_new_contract_with_smaller_prompt(self):
        self.c.tick(); self.c.submitted(); self.c.tick()
        first = next(p['text'] for p in self.c.state()['prompts'] if 'You are the reviewer' in p['text'])
        self.assertLess(len(first), 6000)
        self.assertNotIn('docket help reviewer', first)
        self.assertIn('docket bundle demo T01 --checkout`', first)
        self.assertNotIn('/tmp/demo-T01-review', first)
        self.assertIn('cd PRINTED_CHECKOUT && (true)', first)
        self.c.approve(); self.c.tick()
        task = self.c.run / 'T02-task.mdx'
        text = task.read_text().replace('## Out of scope\n',
            '## Out of scope\n\nPreserve earlier data, including the new checkpoint.\n')
        task.write_text(text)
        self.c.submitted(2)
        batch = self.mod.read_batch(self.c.run, 'M2')
        record = self.c.record('M1', 'reviewer')
        event = next(e for e in self.mod.derive_events(self.c.run, 'reviewer')
                     if str(e['key']).startswith('batch:M2:'))
        continued = self.mod.worker_prompt(self.c.run, batch, record, event)
        self.assertLess(len(continued), 4000)
        self.assertIn('Preserve earlier data, including the new checkpoint.', continued)
        self.assertNotIn('# Applicable verification obligations', continued)
        self.assertIn('retain unchanged contract and duties', continued)


    def test_informational_warning_prints_once_without_a_planner_event(self):
        path = Path(os.environ['XDG_CONFIG_HOME']) / 'codex-home/sessions/2026/10/26/rollout-test.jsonl'
        path.parent.mkdir(parents=True)
        import time
        path.write_text(json.dumps({'timestamp': self.mod.stamp(), 'payload': {'rate_limits': {
            'primary': {'used_percent': 75, 'resets_at': time.time() + 3600, 'window_minutes': 300}}}}) + '\n')
        with patch.dict(os.environ, CODEX_HOME=str(path.parents[4])):
            # Mark the configured reviewer as a noted Codex participant.
            (self.c.run / '.harness-sessions.jsonl').write_text(json.dumps({
                'at': '2026-01-01T00:00:00Z', 'harness': 'codex', 'session_id': 'warning-session',
                'role': 'reviewer', 'owner': '-', 'cwd': str(self.c.f.root)}) + '\n')
            first = self.c.tick().stdout
            second = self.c.tick().stdout
            self.assertIn('Codex budget warning', first)
            self.assertNotIn('Codex budget warning', second)
            self.assertNotIn('codex-budget:', self.c.cli('events', 'demo', '--role', 'planner', '--peek').stdout)


    def test_continuation_hashes_bind_sent_bytes_when_contract_changes_during_delivery(self):
        self.c.tick(); self.c.submitted(); self.c.tick()
        self.c.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer',
                   '--change', 'Add the regression assertion.')
        g = self.mod.deliver.__globals__
        real_herdr = g['herdr']
        task = self.c.run / 'T01-task.mdx'
        def edit_after_send(*args, **kwargs):
            response = real_herdr(*args, **kwargs)
            if args[:2] == ('agent', 'prompt'):
                task.write_text(task.read_text().replace('The feature works.',
                                                        'New contract published during delivery.'))
            return response
        with patch.dict(g, herdr=edit_after_send):
            self.mod.conduct_once(self.c.run)
        record = self.c.record()
        batch = self.mod.read_batch(self.c.run, 'M1')
        event = next(e for e in self.mod.derive_events(self.c.run, 'implementor')
                     if e.get('owner') == 'T01')
        followup = self.mod.worker_prompt(self.c.run, batch, record, event)
        self.assertIn('# Task contract', followup)
        self.assertIn('New contract published during delivery.', followup)



if __name__ == '__main__':
    unittest.main()
