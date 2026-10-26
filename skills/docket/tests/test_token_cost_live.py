"""Regressions reproduced from the ml-t3s4 reviewer run."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path
from unittest.mock import patch

import test_token_cost as fixtures


class LiveReviewer(unittest.TestCase):
    def setUp(self):
        self.t = fixtures.TokenCost('runTest')
        self.t.setUp()
        self.cli, self.mod = self.t.cli, self.t.mod

    def tearDown(self):
        self.t.tearDown()

    def verified(self):
        run = self.t.ready(git=True)
        (self.t.f.root / 'src/a.py').write_text('verified work\n')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        self.cli('batch', 'demo', '--verify', 'M1', '--as', 'implementor')
        return run, self.mod.read_batch(run, 'M1')

    def export(self):
        output = self.cli('bundle', 'demo', 'T01', '--checkout').stdout
        directory = Path(next(line.split(': ', 1)[1] for line in output.splitlines() if line.startswith('materialized ')))
        self.assertTrue(directory.parent.name.startswith('docket-checkout-'))
        self.addCleanup(shutil.rmtree, directory.parent)
        return directory, output

    def test_conductor_and_approval_identity_captures_cannot_reset_each_others_index(self):
        run, batch = self.verified()
        records, _ = self.mod.baseline_roots(run, 'run')
        g = self.mod.source_tree.__globals__
        git = g['private_git']
        state = {'nested': False, 'done': False}
        def interleave(env, cwd, *args, **kwargs):
            if args[0] == 'write-tree' and not state['nested'] and not state['done']:
                state['nested'] = True
                state['outer_env'] = dict(env)
                self.mod.source_tree(run, 'run', records[0], 'batch-verify')
                state['nested'] = False
                state['done'] = True
                return state['outer_result']
            result = git(env, cwd, *args, **kwargs)
            if state['nested'] and args[0] == 'read-tree':
                # The second capture is paused after loading HEAD but before git add.
                state['outer_result'] = git(state['outer_env'], cwd, 'write-tree')
            return result
        index_before = (self.t.f.root / '.git/index').read_bytes()
        with patch.dict(g, private_git=interleave):
            overlapped = self.mod.batch_verification_valid(run, batch)
        self.assertTrue(self.mod.batch_verification_valid(run, batch))
        self.assertTrue(overlapped)
        self.assertTrue(state['done'])
        self.assertFalse(list(self.mod.bundle_work(run).glob('index-*')))
        self.assertEqual(index_before, (self.t.f.root / '.git/index').read_bytes())
        self.assertTrue(self.mod.batch_verification_valid(run, batch))
        self.cli('batch', 'demo', '--approve', 'M1', '--as', 'reviewer')

    def test_approval_source_refusal_names_root_paths_and_both_tree_values(self):
        run, batch = self.verified()
        frozen = batch['verification']['source']['roots'][0]['tree']
        (self.t.f.root / 'src/a.py').write_text('unverified edit\n')
        current = self.mod.current_batch_source(run)[0][0]['tree']
        refused = self.cli('batch', 'demo', '--approve', 'M1', '--as', 'reviewer', ok=False)
        self.assertNotEqual(0, refused.returncode)
        for value in ('root', 'src/a.py', frozen, current, 'frozen=', 'current='):
            self.assertIn(value, refused.stderr)
        self.assertFalse((run / '.transitions/batch-M1.json').exists())

    def test_approval_member_refusal_names_member_and_frozen_current_digests(self):
        run, batch = self.verified()
        frozen = batch['verification']['member_bundles']['T01']
        self.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer', '--change', 'Add a regression assertion.')
        self.cli('dispatch', 'demo', 'T01', '--session', 'worker')
        self.t.f.fill_task_report(run / 'T01-report-02.mdx')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        current = self.mod.latest_bundle(run, 'T01')['digest']
        refused = self.cli('batch', 'demo', '--approve', 'M1', '--as', 'reviewer', ok=False)
        self.assertNotEqual(0, refused.returncode)
        for value in ('T01', frozen, current):
            self.assertIn(value, refused.stderr)

    def test_approval_integrity_refusal_names_the_damaged_artifact(self):
        run, batch = self.verified()
        where = self.mod.batch_verification_dir(run, 'M1', batch['verification']['digest'])
        (where / 'stdout').write_text('tampered output')
        refused = self.cli('batch', 'demo', '--approve', 'M1', '--as', 'reviewer', ok=False)
        self.assertIn('stdout', refused.stderr)
        self.assertIn('frozen=', refused.stderr)
        self.assertIn('current=', refused.stderr)

    def test_export_runs_registered_venv_command_and_excludes_ignored_secrets(self):
        run = self.t.ready(git=True)
        environment = Path(self.t.f._feedback_tmp.name) / 'runtime'
        venv.EnvBuilder(with_pip=False).create(environment)
        (self.t.f.root / '.venv').symlink_to(environment, target_is_directory=True)
        (self.t.f.root / '.env').write_text('SECRET=do-not-export\n')
        (self.t.f.root / 'private-data').mkdir()
        (self.t.f.root / 'private-data/key').write_text('do-not-export')
        (self.t.f.root / '.git/info/exclude').write_text('.venv\n.env\nprivate-data/\n.docket/\n')
        task = run / 'T01-task.mdx'
        meta, body = self.mod.parse(task.read_text())
        command = ".venv/bin/python -c 'import sys; assert sys.prefix != sys.base_prefix'"
        meta['verify'] = command
        self.mod.publish(task, self.mod.render(meta, body))
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        before = {str(p.relative_to(self.t.f.root)): p.read_bytes() for p in self.t.f.root.rglob('*')
                  if p.is_file() and '.docket' not in p.parts}
        destination, output = self.export()
        result = subprocess.run(command, cwd=destination, shell=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('linked runtime: .venv', output)
        self.assertTrue((destination / '.venv').is_symlink())
        for name in ('.env', 'private-data', '.docket'):
            self.assertFalse((destination / name).exists())
        self.assertNotIn('do-not-export', output)
        after = {str(p.relative_to(self.t.f.root)): p.read_bytes() for p in self.t.f.root.rglob('*')
                 if p.is_file() and '.docket' not in p.parts}
        self.assertEqual(before, after)

    def test_runtime_links_cannot_expose_docket_state_or_env_style_targets(self):
        run = self.t.ready(git=True)
        (self.t.f.root / '.git/info/exclude').write_text('.venv\nvenv\nnode_modules\n.env-secrets/\n')
        (self.t.f.root / '.venv').symlink_to(run.parent.parent, target_is_directory=True)
        (self.t.f.root / '.env-secrets').mkdir()
        (self.t.f.root / 'venv').symlink_to(self.t.f.root / '.env-secrets', target_is_directory=True)
        other_state = Path(self.t.f._feedback_tmp.name) / 'other-project/.docket'
        other_state.mkdir(parents=True)
        (self.t.f.root / 'node_modules').symlink_to(other_state, target_is_directory=True)
        record = self.mod.baseline_roots(run, 'T01')[0][0]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            self.assertEqual([], self.mod.link_checkout_runtime(record, destination))
            self.assertEqual([], list(destination.iterdir()))

    def test_unignored_runtime_entry_is_not_linked(self):
        run = self.t.ready(git=True)
        (self.t.f.root / 'venv').mkdir()
        record = self.mod.baseline_roots(run, 'T01')[0][0]
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual([], self.mod.link_checkout_runtime(record, Path(directory)))

    def test_reviewer_prompt_uses_only_the_frozen_patch_source(self):
        run, _ = self.verified()
        prompt = self.cli('prompt', 'demo', 'T01', '--role', 'reviewer').stdout
        self.assertNotIn('docket diff demo T01', prompt)
        self.assertIn('frozen patch', prompt.lower())
        self.assertIn('review-packet demo --role reviewer --batch M1', prompt)

    def test_reviewer_guidance_does_not_use_the_implementors_model_profile(self):
        run, _ = self.verified()
        task = run / 'T01-task.mdx'
        meta, body = self.mod.parse(task.read_text())
        meta.update(actual_model='worker-model', requested_model='worker-model')
        self.mod.publish(task, self.mod.render(meta, body))
        profile = Path(os.environ['DOCKET_MODEL_PROFILES']) / 'model-worker-model.md'
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text('---\nname: model-worker-model\nversion: 1\nmodels: worker-model\nreviewed_through: 1\ntriggers:\n---\n\nWorker-only report formatting advice.\n')
        prompt, _ = self.mod.compose_prompt(run, 'demo', 'T01', 'reviewer')
        self.assertNotIn('Worker-only report formatting advice.', prompt)


class LiveWorkerPrompt(unittest.TestCase):
    def setUp(self):
        self.t = fixtures.ConductorTokenCost('runTest')
        self.t.setUp()
        self.c, self.mod = self.t.c, self.t.mod

    def tearDown(self):
        self.t.tearDown()

    def test_reviewer_delivery_has_one_packet_command_and_one_wait_instruction(self):
        self.c.tick(); self.c.submitted(); self.c.tick()
        prompt = next(p['text'] for p in self.c.state()['prompts'] if 'You are the reviewer' in p['text'])
        self.assertEqual(1, prompt.count('docket review-packet demo --role reviewer --batch M1'))
        self.assertEqual(1, prompt.lower().count('after deciding, end your turn'))
        self.assertEqual(1, prompt.count('Do not rerun an identical captured suite.'))

    def test_correction_delivery_retains_only_changed_steps(self):
        self.c.tick(); self.c.submitted(); self.c.tick()
        self.c.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer',
                   '--change', 'Preserve the earlier boundary as a regression assertion.')
        self.c.cli('dispatch', 'demo', 'T01', '--session', self.c.record()['session'], '--register')
        self.c.cli('set-model', 'demo', 'T01', '--actual', 'observed-test-model')
        self.c.f.fill_task_report(self.c.run / 'T01-report-02.mdx', files='- `src/t01.py:1` - corrected.')
        self.c.cli('submit', 'demo', 'T01', '--as', 'implementor')
        self.c.cli('batch', 'demo', '--verify', 'M1', '--as', 'implementor')
        batch = self.mod.read_batch(self.c.run, 'M1')
        event = next(e for e in self.mod.derive_events(self.c.run, 'reviewer') if e['key'].startswith('batch:M1:'))
        prompt = self.mod.worker_prompt(self.c.run, batch, self.c.record(role='reviewer'), event)
        self.assertIn('Preserve the earlier boundary as a regression assertion.', prompt)
        self.assertIn('# Steps (changed)', prompt)
        self.assertNotIn('docket review-packet demo --role reviewer --batch M1', prompt)
        self.assertNotIn('docket bundle demo T01 --checkout', prompt)
        self.assertNotIn('docket batch demo --approve M1', prompt)
