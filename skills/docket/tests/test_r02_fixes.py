"""Regressions for verification cost, managed exports, and planner accounting."""
from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import test_token_cost as cost
import test_usage_accounting as accounting


class R02Fixes(unittest.TestCase):
    def setUp(self):
        self.t = cost.TokenCost('runTest')
        self.t.setUp()
        self.cli, self.mod = self.t.cli, self.t.mod

    def tearDown(self):
        self.t.tearDown()

    def export(self, *args, owner="T01"):
        output = self.cli('bundle', 'demo', owner, *args, '--checkout').stdout
        destination = Path(next(line.split(': ', 1)[1] for line in output.splitlines()
                                if line.startswith('materialized ')))
        self.addCleanup(shutil.rmtree, destination.parent, True)
        return destination

    def test_implementor_prompt_uses_focused_tests_and_submit_capture(self):
        self.t.ready()
        prompt = self.cli('prompt', 'demo', 'T01', '--role', 'implementor').stdout
        for text in ('run only focused tests', 'Do not run the registered verify command',
                     'submit will capture it', 'docket submit demo T01 --check'):
            self.assertIn(text, prompt)
        self.assertNotIn('any observed output (or say', prompt)

    def test_playbooks_state_verification_and_session_policy(self):
        refs = cost.fixtures.DOCKET.resolve().parents[1] / 'references'
        planner = (refs / 'planner.md').read_text()
        for text in ('Assign focused per-task verification; reserve the full suite for the milestone command',
                     '`complexity: high`', 'own milestone', 'fresh implementor session'):
            self.assertIn(text, planner)
        implementor = (refs / 'implementor.md').read_text()
        for text in ('run only focused tests', 'Do not run the registered verify command',
                     'submit will capture it'):
            self.assertIn(text, implementor)
        self.assertIn('The full suite belongs once per milestone, never in a task round', implementor)
        example = json.loads(planner.split('```json\n', 1)[1].split('```', 1)[0])
        self.assertEqual('python3 -m unittest tests.test_settings', example['tasks'][0]['verify'])
        self.assertEqual('tests/test.sh', example['milestones'][0]['command'])
        conductor = (refs / 'conductor.md').read_text()
        self.assertIn('Do not run task verify commands', conductor)
        for text in (planner, implementor, conductor):
            self.assertIn('docket batch', text)
        repo = refs.parents[2]
        self.assertIn('full suite runs once per milestone', (repo / 'README.md').read_text())
        self.assertIn('Task verify commands contain only focused tests',
                      (repo / 'ARCHITECTURE.md').read_text())
        reviewer = (refs / 'reviewer.md').read_text()
        self.assertIn('Do not routinely rerun the identical frozen suite', reviewer)

    def tasks(self, commands, high=False):
        data = self.t.plan()
        task = data['tasks'][0]
        data['tasks'] = [dict(task, owner=f'T{i:02}', file=[f'src/{i}.py'], verify=command,
                              complexity='high' if high and i == 1 else 'low')
                         for i, command in enumerate(commands, 1)]
        data['milestones'][0]['members'] = [t['owner'] for t in data['tasks']]
        return data

    def test_plan_warns_for_each_chained_verify_without_rejecting_it(self):
        data = self.tasks(['true && true', 'true; true'])
        output = self.t.apply(data).stdout
        for owner in ('T01', 'T02'):
            self.assertIn(f'warning: {owner} verify chains commands', output)
        self.assertIn('a task verify should be the focused tests only; the full suite runs once per milestone', output)

    def test_plan_accepts_different_focused_commands_within_one_milestone(self):
        data = self.tasks(['printf focused-one', 'printf focused-two'])
        data['milestones'][0]['command'] = 'true'
        self.assertNotIn('warning:', self.t.apply(data).stdout)

    def test_plan_warns_when_a_task_repeats_its_milestone_suite(self):
        data = self.tasks(['printf focused', '  true  '])
        output = self.t.apply(data).stdout
        self.assertIn('warning: T02 verify equals milestone M1 full-suite command', output)
        self.assertNotIn('warning: T01 verify', output)
        self.assertNotIn('different task verify commands', output)

    def test_plan_warns_for_high_complexity_with_two_siblings(self):
        output = self.t.apply(self.tasks(['true'] * 3, high=True)).stdout
        self.assertIn('warning: milestone M1 mixes high-complexity task T01', output)
        self.assertIn('own milestone', output)

    def test_plan_does_not_warn_for_simple_or_separate_milestones(self):
        data = self.tasks(['true', 'printf ok'], high=True)
        data['milestones'] = [dict(id=f'M{i}', members=[task['owner']], command='printf full-suite')
                              for i, task in enumerate(data['tasks'], 1)]
        self.assertNotIn('warning:', self.t.apply(data).stdout)

    def submitted(self):
        run = self.t.ready(git=True)
        (self.t.f.root / 'src/a.py').write_text('verified work\n')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        return run

    def test_same_bundle_export_is_reused_but_baseline_is_separate(self):
        self.submitted()
        first = self.export()
        self.assertEqual(first, self.export())
        baseline = self.export('--baseline')
        self.assertNotEqual(first, baseline)
        self.assertEqual('baseline\n', (baseline / 'src/a.py').read_text())
        self.assertEqual('verified work\n', (first / 'src/a.py').read_text())

    def test_exploratory_edits_are_not_reused_as_frozen_source(self):
        self.submitted()
        first = self.export()
        (first / 'src/a.py').write_text('exploratory edit\n')
        fresh = self.export()
        self.assertEqual('verified work\n', (fresh / 'src/a.py').read_text())
        self.assertFalse(first.parent.exists())

    def test_missing_tree_is_rebuilt_without_leaking_its_parent(self):
        self.submitted()
        first = self.export()
        shutil.rmtree(first)
        fresh = self.export()
        self.assertEqual('verified work\n', (fresh / 'src/a.py').read_text())
        self.assertFalse(first.parent.exists())

    def test_concurrent_exports_share_one_managed_directory(self):
        from concurrent.futures import ThreadPoolExecutor
        run = self.submitted()
        with ThreadPoolExecutor(max_workers=2) as pool:
            exports = list(pool.map(lambda _: self.cli('bundle', 'demo', 'T01', '--checkout').stdout,
                                    range(2)))
        paths = [Path(next(line.split(': ', 1)[1] for line in text.splitlines()
                           if line.startswith('materialized '))) for text in exports]
        for path in paths:
            self.addCleanup(shutil.rmtree, path.parent, True)
        self.assertEqual(paths[0], paths[1])
        self.assertEqual(1, len(list((run / '.checkouts').glob('*.json'))))

    def correct(self, run):
        self.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer',
                 '--change', '1. Correct the implementation.')
        self.cli('dispatch', 'demo', 'T01', '--session', 'worker')
        (self.t.f.root / 'src/a.py').write_text('corrected work\n')
        self.t.f.fill_task_report(run / 'T01-report-02.mdx')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')

    def test_new_bundle_prunes_only_older_exports_for_its_owner_and_kind(self):
        run = self.submitted()
        first = self.export()
        baseline = self.export('--baseline')
        other_owner = self.export('--baseline', owner='orch')
        custom = Path(self.t.f._feedback_tmp.name) / 'explicit-checkout'
        self.cli('bundle', 'demo', 'T01', '--checkout', str(custom))
        self.correct(run)
        second = self.export()
        self.assertNotEqual(first, second)
        self.assertFalse(first.parent.exists())
        self.assertTrue(baseline.is_dir())
        self.assertTrue(other_owner.is_dir())
        self.assertTrue(custom.is_dir())
        self.assertEqual('corrected work\n', (second / 'src/a.py').read_text())
        newer_baseline = self.export('--baseline')
        self.assertFalse(baseline.parent.exists())
        self.assertTrue(newer_baseline.is_dir())
        self.assertTrue(second.is_dir())
        self.assertTrue(other_owner.is_dir())
        self.assertTrue(custom.is_dir())
        self.assertEqual(3, len(list((run / '.checkouts').glob('*.json'))))

    def test_failed_export_keeps_the_previous_owner_export(self):
        run = self.submitted()
        first = self.export()
        self.correct(run)
        globals_ = self.mod.materialize_bundle_tree.__globals__
        def fail_export(*args):
            raise RuntimeError('export failed')
        with patch.dict(globals_, export_index_entries=fail_export):
            with self.mod.owner_lock(run, 'checkout-exports'):
                with self.assertRaisesRegex(RuntimeError, 'export failed'):
                    self.mod.materialize_bundle_tree(run, 'T01', None)
        self.assertTrue(first.is_dir())
        self.assertEqual(1, len(list((run / '.checkouts').glob('*.json'))))
        second = self.export()
        self.assertFalse(first.parent.exists())
        self.assertEqual('corrected work\n', (second / 'src/a.py').read_text())

    def test_export_fingerprint_does_not_read_file_contents(self):
        tree = self.t.f.root / 'tree'
        tree.mkdir()
        (tree / 'file').write_bytes(b'payload')
        (tree / 'runtime').symlink_to(self.t.f._feedback_tmp.name, target_is_directory=True)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('read file contents')):
            fingerprint = self.mod.checkout_export_fingerprint(tree)
            self.assertEqual(fingerprint, self.mod.checkout_export_fingerprint(tree))

    def test_export_fingerprint_detects_metadata_and_link_changes(self):
        tree = self.t.f.root / 'tree'
        tree.mkdir()
        file = tree / 'file'
        file.write_text('payload')
        fingerprint = self.mod.checkout_export_fingerprint(tree)
        info = file.lstat()
        os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
        touched = self.mod.checkout_export_fingerprint(tree)
        self.assertNotEqual(fingerprint, touched)
        file.chmod(file.stat().st_mode ^ 0o100)
        mode_changed = self.mod.checkout_export_fingerprint(tree)
        self.assertNotEqual(touched, mode_changed)
        file.write_text('larger payload')
        os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
        size_changed = self.mod.checkout_export_fingerprint(tree)
        self.assertNotEqual(mode_changed, size_changed)
        file.rename(tree / 'renamed')
        renamed = self.mod.checkout_export_fingerprint(tree)
        self.assertNotEqual(size_changed, renamed)
        link = tree / 'link'
        link.symlink_to('one')
        linked = self.mod.checkout_export_fingerprint(tree)
        link.unlink()
        link.symlink_to('two')
        self.assertNotEqual(linked, self.mod.checkout_export_fingerprint(tree))

    def test_finish_cleans_managed_exports_and_preserves_explicit_destination(self):
        self.submitted()
        managed = self.export()
        custom = Path(self.t.f._feedback_tmp.name) / 'explicit-checkout'
        self.cli('bundle', 'demo', 'T01', '--checkout', str(custom))
        self.cli('batch', 'demo', '--verify', 'M1', '--as', 'implementor')
        self.cli('batch', 'demo', '--approve', 'M1', '--as', 'reviewer')
        self.cli('finish', 'demo', '--feedback', 'none')
        self.assertFalse(managed.parent.exists())
        self.assertTrue((custom / 'src/a.py').is_file())
        self.cli('finish', 'demo', '--feedback', 'none')

    def test_disarm_run_cleans_exports_even_when_no_watcher_was_armed(self):
        self.submitted()
        managed = self.export()
        self.cli('disarm', 'demo', '--role', 'reviewer')
        self.assertTrue(managed.is_dir())
        self.cli('disarm', 'demo')
        self.assertFalse(managed.parent.exists())

    def test_cleanup_never_follows_a_replaced_managed_parent(self):
        self.submitted()
        managed = self.export()
        saved = managed.parent.with_name(managed.parent.name + '-saved')
        managed.parent.rename(saved)
        self.addCleanup(shutil.rmtree, saved, True)
        stranger = Path(self.t.f._feedback_tmp.name) / 'stranger'
        stranger.mkdir()
        (stranger / 'keep').write_text('user data')
        managed.parent.symlink_to(stranger, target_is_directory=True)
        self.addCleanup(managed.parent.unlink, missing_ok=True)
        self.cli('disarm', 'demo')
        self.assertEqual('user data', (stranger / 'keep').read_text())
        self.assertTrue(saved.is_dir())

    def legacy_aggregate(self, topology):
        self.t.f.repo()
        run = self.t.f.init(topology, evidence_mode='git')
        self.t.f.assign('T01', executor='orchestrator')
        self.t.f.fill_task(run / 'T01-task.mdx')
        self.t.f.fill_task_report(run / 'T01-report-01.mdx')
        (self.t.f.root / 'src/a.py').write_text('verified work\n')
        self.cli('submit', 'demo', 'T01')
        managed = self.export()
        self.t.f.assign('orch', executor='orchestrator')
        self.t.f.fill_orch_report(run / 'orch-report-01.mdx')
        self.cli('submit', 'demo', 'orch', '--skip-verify')
        return managed

    def test_final_aggregate_verdict_cleans_exports(self):
        managed = self.legacy_aggregate('split')
        self.assertTrue(managed.is_dir())
        self.cli('decide', 'demo', 'orch', '--approve')
        self.assertFalse(managed.parent.exists())

    def test_combined_aggregate_completion_cleans_exports(self):
        managed = self.legacy_aggregate('combined')
        self.assertFalse(managed.parent.exists())

    def test_global_disarm_cleans_unarmed_runs_and_does_not_delete_a_forged_path(self):
        run = self.submitted()
        managed = self.export()
        record_path = next((run / '.checkouts').glob('*.json'))
        record = json.loads(record_path.read_text())
        stranger = Path(self.t.f._feedback_tmp.name) / 'docket-checkout-user-owned'
        stranger.mkdir()
        (stranger / 'keep').write_text('user data')
        (run / '.checkouts/forged.json').write_text(json.dumps({**record, 'parent': str(stranger)}))
        self.cli('disarm')
        self.assertFalse(managed.parent.exists())
        self.assertEqual('user data', (stranger / 'keep').read_text())

    def test_custom_exports_are_outside_all_checkouts_and_remain_caller_owned(self):
        self.submitted()
        refused = self.cli('bundle', 'demo', 'T01', '--checkout', str(self.t.f.root / 'export'), ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertFalse((self.t.f.root / 'export').exists())
        custom = Path(self.t.f._feedback_tmp.name) / 'explicit-checkout'
        self.cli('bundle', 'demo', 'T01', '--checkout', str(custom))
        refused = self.cli('bundle', 'demo', 'T01', '--checkout', str(custom), ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.cli('disarm')
        self.assertTrue((custom / 'src/a.py').is_file())


class R02PlanningUsage(unittest.TestCase):
    def setUp(self):
        self.t = accounting.UsageAccounting('runTest')
        self.t.setUp()
        self.sessions = self.t.submodule('sessions')
        self.env, _ = self.t.f.fake_claude_session()
        self.sid = self.env['CLAUDE_CODE_SESSION_ID']
        self.run = self.t.f.init5()

    def tearDown(self):
        self.t.tearDown()

    def usage(self, run='demo', json_output=True):
        args = ['usage', run] + (['--json'] if json_output else [])
        output = self.t.f.cli_env({'CLAUDE_CONFIG_DIR': self.env['CLAUDE_CONFIG_DIR']}, *args).stdout
        return json.loads(output)[0] if json_output else output

    def test_planner_counts_before_join_and_labels_planning_separately(self):
        self.t.note(self.run, self.sid, '2026-09-23T01:00:09Z', role='planner')
        row = self.usage()
        self.assertEqual((3, 2295), (row['calls'], row['total']))
        self.assertEqual((2, 1168), (row['planning']['calls'], row['planning']['total']))
        self.assertEqual((1, 1127), (row['run_work']['calls'], row['run_work']['total']))
        self.assertEqual('2026-09-23T01:00:09Z', row['planning']['until'])
        self.assertIn('planning:', self.usage(json_output=False))

    def test_previous_run_end_bounds_planning_and_next_join_bounds_run_work(self):
        self.t.cli('init', 'previous', '--evidence-mode', 'documents-only')
        previous = self.run.parent / 'previous'
        self.t.note(previous, self.sid, '2026-09-23T00:00:00Z', role='planner')
        self.t.note(self.run, self.sid, '2026-09-23T01:00:09Z', role='planner')
        self.t.cli('init', 'next', '--evidence-mode', 'documents-only')
        self.t.note(self.run.parent / 'next', self.sid, '2026-09-23T01:00:10Z', role='planner')
        original = self.sessions.run_completed_at
        cutoff = self.sessions._instant('2026-09-23T01:00:07Z').timestamp()
        with patch.object(self.sessions, 'run_completed_at',
                          side_effect=lambda d: cutoff - self.sessions.RUN_WRAP_UP_SECONDS
                          if d == previous else original(d)):
            with patch.dict(os.environ, CLAUDE_CONFIG_DIR=self.env['CLAUDE_CONFIG_DIR']):
                [row] = self.sessions.collect_run_usage(self.run, archive=False)
        self.assertEqual((1, 53), (row['calls'], row['total']))
        self.assertEqual('2026-09-23T01:00:07Z', row['since'])
        self.assertEqual('2026-09-23T01:00:10Z', row['until'])
        self.assertEqual('empty', row['run_work']['telemetry'])

    def test_open_previous_run_does_not_donate_its_work_to_planning(self):
        self.t.cli('init', 'previous', '--evidence-mode', 'documents-only')
        self.t.note(self.run.parent / 'previous', self.sid, '2026-09-23T00:00:00Z', role='planner')
        self.t.note(self.run, self.sid, '2026-09-23T01:00:09Z', role='planner')
        row = self.usage()
        self.assertEqual((1, 1127), (row['calls'], row['total']))
        self.assertEqual('empty', row['planning']['telemetry'])
        self.assertIn('planning: unknown (no call in span)', self.usage(json_output=False))

    def test_codex_planning_counts_cumulative_deltas_before_join(self):
        tid = '01a1153c-7a58-7470-96f9-d74f52e3d6aa'
        home = Path(self.t.f._feedback_tmp.name) / 'codex-home'
        rollout = home / 'sessions/2026/09/23' / f'rollout-2026-09-23T01-00-00-{tid}.jsonl'
        self.t.rollout(rollout, tid, [
            ('2026-09-23T01:00:05Z', (100, 80, 10, 2, 110)),
            ('2026-09-23T01:00:10Z', (300, 250, 30, 5, 330))])
        self.t.note(self.run, tid, '2026-09-23T01:00:09Z', role='planner', harness='codex')
        with patch.dict(os.environ, CODEX_HOME=str(home)):
            [row] = self.sessions.collect_run_usage(self.run, archive=False)
        self.assertEqual((330, 110, 220),
                         (row['total'], row['planning']['total'], row['run_work']['total']))

    def test_opencode_planning_uses_the_same_window_split(self):
        rows = {'sessions': [{'directory': '/work/project'}], 'messages': [
            {'info': {'role': 'assistant', 'time': {'created': at},
                      'tokens': {'input': total - 5, 'output': 5, 'total': total},
                      'modelID': 'model', 'providerID': 'test'}}
            for at, total in ((1000, 15), (3000, 27))]}
        with patch.object(self.sessions, 'opencode_session_rows', return_value=rows):
            usage, _, _ = self.sessions.session_usage({
                'harness': 'opencode', 'session_id': 'noted', 'roles': ['planner'],
                'first_seen': '1970-01-01T00:00:02Z', 'usage_since': '',
                'until': '1970-01-01T00:00:04Z'})
        self.assertEqual((42, 15, 27),
                         (usage['total'], usage['planning']['total'], usage['run_work']['total']))

    def test_planning_with_no_calls_is_a_gap_and_never_guesses_a_session(self):
        self.t.note(self.run, self.sid, '2026-09-23T00:00:00Z', role='planner')
        self.t.cli('init', 'next', '--evidence-mode', 'documents-only')
        self.t.note(self.run.parent / 'next', self.sid, '2026-09-23T01:00:04Z', role='planner')
        self.assertEqual('empty', self.usage()['telemetry'])
        self.t.cli('init', 'unnoted', '--evidence-mode', 'documents-only')
        self.assertIn('no harness session noted', self.usage('unnoted', json_output=False))


if __name__ == '__main__':
    unittest.main()
