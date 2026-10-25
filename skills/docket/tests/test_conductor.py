"""Conductor process control against a disposable Herdr, never the real server."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

import test_docket as fixtures


FAKE_HERDR = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
p = Path(__file__).with_name('state.json')
s = json.loads(p.read_text())
a = sys.argv[1:]
s['calls'].append(a)
def save(): p.write_text(json.dumps(s))
def reply(result):
    save()
    print(json.dumps({'result': result}))
    sys.exit(0)
def fail(code):
    save()
    print(json.dumps({'error': {'code': code}}), file=sys.stderr)
    sys.exit(1)
if s.get('failures_left', 0) and (not s.get('fail_command') or a[:2] == s['fail_command']):
    s['failures_left'] -= 1
    fail('socket_error')
if a[:2] == ['tab', 'list']: reply({'tabs': s.get('tabs', [])})
if a[:2] == ['tab', 'create']:
    tab = {'tab_id': 'fake:t1', 'label': a[a.index('--label')+1]}
    s['tabs'] = s.get('tabs', []) + [tab]
    s['panes'].append('fake:anchor')
    reply({'tab': tab, 'root_pane': {'pane_id': 'fake:anchor'}})
if a[:2] == ['tab', 'get']:
    reply({'tab': next(t for t in s['tabs'] if t['tab_id'] == a[2])})
if a[:2] == ['pane', 'list']:
    reply({'panes': [{'pane_id': v, 'tab_id': 'fake:t1'} for v in s['panes']]})
if a == ['agent', 'list']: reply({'agents': list(s['agents'].values())})
if a[:2] == ['pane', 'layout']: reply({'width': 160, 'height': 40})
if a[:2] == ['pane', 'split']:
    s['serial'] += 1
    pane = 'fake:p' + str(s['serial'])
    s['panes'].append(pane)
    reply({'pane': {'pane_id': pane}})
if a[:2] == ['pane', 'get']:
    if a[2] not in s['panes']: fail('pane_not_found')
    reply({'pane': {'pane_id': a[2]}})
if a[:2] == ['pane', 'close']:
    s['panes'] = [p for p in s['panes'] if p != a[2]]
    s['agents'] = {n: v for n, v in s['agents'].items() if v['pane_id'] != a[2]}
    reply({})
if a[:2] == ['agent', 'start']:
    name = a[2]
    agent = {'name': name, 'pane_id': a[a.index('--pane')+1],
             'kind': a[a.index('--kind')+1], 'agent_status': 'idle'}
    if s.get('trust'):
        agent['agent_status'] = 'blocked'
        agent['output'] = s['trust']
    s['agents'][name] = agent
    if s.get('trust'): fail('agent_not_ready')
    reply({'agent': agent})
if a[:2] == ['agent', 'get']:
    if a[2] not in s['agents']: fail('agent_not_found')
    reply({'agent': s['agents'][a[2]]})
if a[:2] == ['agent', 'read']:
    save()
    agent = s['agents'][a[2]]
    source = a[a.index('--source')+1] if '--source' in a else 'recent'
    output = agent.get(source, agent.get('output', ''))
    if '--lines' in a:
        output = '\n'.join(output.splitlines()[-int(a[a.index('--lines')+1]):])
    print(output)
    sys.exit(0)
if a[:2] == ['agent', 'send-keys']:
    s['agents'][a[2]]['agent_status'] = 'idle'
    s['agents'][a[2]]['output'] = ''
    reply({})
if a[:2] == ['agent', 'prompt']:
    agent = s['agents'][a[2]]
    if agent['agent_status'] not in ('idle', 'done'): fail('agent_busy')
    if s.pop('fail_prompt', False): fail('transport_failed')
    s['prompts'].append({'name': a[2], 'text': a[3]})
    reply({})
fail('unsupported_fake_command')
'''


class Conductor(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocketCLI('runTest')
        self.f.setUp()
        self.cli = self.f.cli
        self.bin = Path(self.f._feedback_tmp.name) / 'conductor-bin'
        self.bin.mkdir()
        (self.bin / 'herdr').write_text(FAKE_HERDR)
        (self.bin / 'herdr').chmod(0o755)
        self.fake = self.bin / 'state.json'
        self.fake.write_text(json.dumps(dict(calls=[], agents={}, panes=[], serial=0, prompts=[])))
        self.f._fake_herdr_path = os.environ['PATH']
        os.environ['PATH'] = str(self.bin) + os.pathsep + os.environ['PATH']
        os.environ['HERDR_ENV'] = '1'
        self.user = Path(os.environ['XDG_CONFIG_HOME']) / 'docket/config.json'
        self.user.parent.mkdir(parents=True)
        self.user.write_text(json.dumps({'implementor_model': 'small', 'reviewer_model': 'capable'}))
        self.cli('init', 'demo', '--evidence-mode', 'documents-only')
        self.run = self.f.root / '.docket/runs/demo'
        for n in (1, 2):
            owner, bid = f'T0{n}', f'M{n}'
            self.cli('assign', 'demo', owner, '--file', f'src/t0{n}.py', '--verify', 'true', '--as', 'planner')
            self.f.fill_task(self.run / f'{owner}-task.mdx')
            assignment = (['--implementor-harness', 'codex', '--implementor-model', 'next-model',
                           '--assignment-reason', 'Exercise the next milestone adapter.'] if n == 2 else [])
            self.cli('batch', 'demo', '--create', bid, '--members', owner, '--milestone',
                     '--command', 'true', '--as', 'planner', *assignment)
            self.cli('batch', 'demo', '--close', bid, '--as', 'planner')

    def tearDown(self):
        self.f.tearDown()

    def state(self):
        return json.loads(self.fake.read_text())

    def update(self, **values):
        state = self.state()
        state.update(values)
        self.fake.write_text(json.dumps(state))

    def tick(self, **kwargs):
        return self.cli('conduct', 'demo', '--once', **kwargs)

    def launches(self):
        return [c for c in self.state()['calls'] if c[:2] == ['agent', 'start']]

    def records(self):
        return [json.loads(p.read_text()) for p in (self.run / '.conductor').glob('*-*.json')]

    def record(self, bid='M1', role='implementor'):
        return next(r for r in self.records() if r.get('milestone') == bid and r.get('role') == role)

    def submitted(self, n=1):
        owner = f'T0{n}'
        self.cli('set-model', 'demo', owner, '--actual', 'observed-test-model')
        self.f.fill_scope(self.run / f'{owner}-scope.mdx')
        self.cli('scope', 'demo', owner, '--submit')
        self.f.fill_task_report(self.run / f'{owner}-report-01.mdx', files=f'- `src/t0{n}.py:1` - done.')
        self.cli('submit', 'demo', owner, '--as', 'implementor')
        self.cli('batch', 'demo', '--verify', f'M{n}', '--as', 'implementor')

    def approve(self, n=1):
        self.cli('batch', 'demo', '--approve', f'M{n}', '--as', 'reviewer', '--reason', 'Checked exact evidence.')

    def test_launch_uses_recorded_assignment_and_registers_only_inside_worker(self):
        self.tick()
        launch = self.launches()[0]
        self.assertEqual('claude', launch[launch.index('--kind')+1])
        self.assertEqual(['--model', 'small'], launch[launch.index('--')+1:])
        rec = self.record()
        self.assertEqual('small', rec['requested_model'])
        self.assertEqual('claude', rec['requested_harness'])
        self.assertTrue(rec['launched_at'])
        self.assertFalse((self.f.root / '.docket/sessions' / (rec['session']+'.json')).exists())
        prompt = self.state()['prompts'][0]['text']
        self.assertIn('docket dispatch demo T01', prompt)
        self.assertIn('--register', prompt)
        self.assertIn('end your turn', prompt.lower())
        self.assertNotIn('docket watch demo', prompt)
        self.assertNotIn('actual_model', rec)
        self.assertEqual('', fixtures.parse_meta(self.run / 'T01-task.mdx').get('actual_model', ''))
        self.assertNotIn('M2', [r['milestone'] for r in self.records()])

    def test_restart_reconciles_alive_agent_and_does_not_repeat_delivery(self):
        self.tick()
        first = self.record()
        self.tick()
        self.assertEqual(1, len(self.launches()))
        self.assertEqual(1, len(self.state()['prompts']))
        self.assertEqual(first['pane_id'], self.record()['pane_id'])

    def test_busy_agent_is_never_prompted(self):
        self.update(trust='An unrelated approval dialog')
        self.tick()
        self.assertEqual([], self.state()['prompts'])
        self.assertFalse(any(c[:2] == ['agent', 'send-keys'] for c in self.state()['calls']))
        agents = self.state()['agents']
        for a in agents.values(): a['agent_status'] = 'working'
        self.update(agents=agents)
        self.tick()
        self.assertEqual([], self.state()['prompts'])
        for a in agents.values(): a['agent_status'] = 'idle'
        self.update(agents=agents)
        self.tick()
        self.assertEqual(1, len(self.state()['prompts']))

    def test_repository_trust_screen_is_passed_but_permission_bypass_is_not_added(self):
        self.update(trust='Do you trust this folder?')
        self.tick()
        self.tick()
        self.assertEqual(1, len(self.state()['prompts']))
        self.assertTrue(any(c[:2] == ['agent', 'send-keys'] for c in self.state()['calls']))
        self.assertNotIn('--dangerously-skip-permissions', self.launches()[0])

    def test_failed_transport_is_retryable_after_announcement_lease(self):
        self.update(fail_prompt=True)
        self.tick()
        self.assertEqual([], self.state()['prompts'])
        for p in (self.run / '.delivery/implementor/announce').glob('*.json'):
            data = json.loads(p.read_text()); data['lease_until'] = 0
            p.write_text(json.dumps(data))
        self.tick()
        self.assertEqual(1, len(self.state()['prompts']))
        self.tick()
        self.assertEqual(1, len(self.state()['prompts']))

    def test_hook_also_armed_has_one_delivery_and_pickup_uses_existing_receipts(self):
        self.tick()
        rec = self.record()
        self.cli('arm', 'demo', '--role', 'implementor', '--session', rec['session'])
        hook = self.f.cli_env({'DOCKET_WATCH_HOOK': '1', 'DOCKET_SESSION': rec['session'],
                               'DOCKET_ROLE': 'implementor'},
                              'watch', 'demo', '--role', 'implementor', '--timeout', '0', ok=False)
        self.assertEqual(0, hook.returncode, hook.stderr)
        self.cli('pickup', 'demo', '--role', 'implementor', '--session', rec['session'])
        received = list((self.run / '.delivery/implementor/received').glob('*.json'))
        self.assertTrue(received)
        self.tick()
        self.assertEqual(1, len(self.state()['prompts']))
        self.assertNotIn('launch:reviewer', self.cli('events', 'demo', '--role', 'implementor', '--peek').stdout)

    def test_worker_startup_numbers_registration_before_pickup(self):
        self.tick()
        rec = self.record()
        prompt = self.state()['prompts'][-1]['text']
        self.assertRegex(prompt, r'(?m)^1\. .*docket dispatch demo T01 .*--register')
        self.assertRegex(prompt, r'(?m)^2\. .*docket pickup demo --role implementor')
        self.cli('dispatch', 'demo', 'T01', '--session', rec['session'], '--register')
        self.submitted(); self.tick()
        prompt = self.state()['prompts'][-1]['text']
        self.assertRegex(prompt, r'(?m)^1\. .*docket arm demo --role reviewer')
        self.assertRegex(prompt, r'(?m)^2\. .*docket pickup demo --role reviewer')

    def test_implementor_pickup_reads_delivered_startup_after_dispatch_without_redelivery(self):
        self.tick()
        rec = self.record()
        key = rec['last_delivery_key']
        self.cli('dispatch', 'demo', 'T01', '--session', rec['session'], '--register')
        output = self.cli('pickup', 'demo', '--role', 'implementor', '--session', rec['session']).stdout
        self.assertIn(key, output)
        self.assertIn('already delivered', output)
        self.assertIn('no longer actionable', output)
        self.assertNotIn('unknown or already retired', output)
        count = len(self.state()['prompts'])
        self.tick(); self.tick()
        self.assertEqual(count, len(self.state()['prompts']))
        duplicate = self.cli('pickup', 'demo', '--role', 'implementor', '--session', rec['session']).stdout
        self.assertNotIn(key + ':', duplicate)

    def test_continued_reviewer_pickup_reads_receipt_without_claiming_or_redelivering(self):
        self.tick(); self.submitted(); self.tick()
        rec = self.record(role='reviewer')
        self.native(rec, 'continued-review-thread')
        self.cli('pickup', 'demo', '--role', 'reviewer', '--session', rec['session'])
        self.approve(); self.tick(); self.submitted(2); self.tick()
        second = self.record('M2', 'reviewer')
        self.assertEqual(rec['session'], second['session'])
        output = self.cli('pickup', 'demo', '--role', 'reviewer', '--session', rec['session']).stdout
        self.assertIn('batch:M2:ready:1', output)
        self.assertIn('already delivered', output)
        self.assertNotIn('unknown or already retired', output)
        self.assertFalse(any((self.run / '.delivery/reviewer/leases').glob('*.json')))
        count = len(self.state()['prompts'])
        self.tick(); self.tick()
        self.assertEqual(count, len(self.state()['prompts']))

    def test_superseded_reviewer_delivery_is_only_non_actionable_receipt_context(self):
        self.tick(); self.submitted(); self.tick()
        rec = self.record(role='reviewer')
        self.cli('arm', 'demo', '--role', 'reviewer', '--session', rec['session'])
        self.approve()
        output = self.cli('pickup', 'demo', '--role', 'reviewer', '--session', rec['session']).stdout
        self.assertIn('batch:M1:ready:1', output)
        self.assertIn('already delivered; no longer actionable', output)
        self.assertFalse(any((self.run / '.delivery/reviewer/leases').glob('*.json')))

    def test_delivered_receipt_is_not_replayed_to_a_new_session_generation(self):
        self.tick(); self.submitted(); self.tick()
        rec = self.record(role='reviewer')
        self.cli('arm', 'demo', '--role', 'reviewer', '--session', rec['session'])
        path = self.f.root / '.docket/sessions' / (rec['session'] + '.json')
        reg = json.loads(path.read_text()); reg['generation'] += 1
        path.write_text(json.dumps(reg))
        out = self.cli('pickup', 'demo', '--role', 'reviewer', '--session', rec['session'], ok=False)
        self.assertNotIn('already delivered', out.stdout)
        self.assertNotIn('batch:M1:ready:1:', out.stdout)

    def test_user_launch_arguments_are_used_and_project_arguments_are_refused(self):
        self.user.write_text(json.dumps({'claude_launch_args': ['--dangerously-skip-permissions'],
                                         'implementor_model': 'later-config'}))
        self.tick()
        self.assertEqual(['--dangerously-skip-permissions', '--model', 'small'],
                         self.launches()[0][self.launches()[0].index('--')+1:])
        project = self.f.root / '.docket/config.json'
        project.write_text(json.dumps({'claude_launch_args': ['--dangerously-skip-permissions']}))
        refused = self.cli('init', 'unsafe', ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('user-only', refused.stderr)

    def test_project_cannot_inject_launch_arguments_into_existing_run(self):
        (self.f.root / '.docket/config.json').write_text(json.dumps({'claude_launch_args': ['--evil']}))
        self.tick()
        self.assertNotIn('--evil', self.launches()[0])

    def test_correction_returns_to_same_implementor(self):
        self.tick()
        rec = self.record()
        self.cli('dispatch', 'demo', 'T01', '--session', rec['session'], '--register')
        self.submitted()
        self.tick()
        self.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer',
                 '--change', '1. Preserve the acceptance boundary.')
        self.tick()
        prompts = [p for p in self.state()['prompts'] if p['name'] == rec['agent_name']]
        self.assertEqual(2, len(prompts))
        self.assertIn('Preserve the acceptance boundary', prompts[-1]['text'])
        self.assertEqual(rec['pane_id'], self.record()['pane_id'])

    def test_approval_closes_implementor_and_starts_fresh_milestone_assignment(self):
        self.tick()
        first = self.record()
        self.submitted()
        self.tick()
        self.approve()
        self.tick()
        self.assertNotIn(first['pane_id'], self.state()['panes'])
        second = self.record('M2')
        self.assertNotEqual(first['pane_id'], second['pane_id'])
        launch = self.launches()[-1]
        self.assertEqual('codex', launch[launch.index('--kind')+1])
        self.assertEqual(['-m', 'next-model'], launch[launch.index('--')+1:])
        self.assertTrue(self.record()['closed_at'])

    def test_continue_reuses_live_reviewer_and_fresh_closes_it(self):
        self.tick()
        self.submitted()
        self.tick()
        reviewer = self.record(role='reviewer')
        self.approve()
        self.tick()
        self.submitted(2)
        self.tick()
        self.assertEqual(reviewer['pane_id'], self.record('M2', 'reviewer')['pane_id'])
        review_starts = [c for c in self.launches() if c[2].endswith('-review')]
        self.assertEqual(1, len(review_starts))
        self.assertIn('docket review-packet demo --role reviewer --batch M2', self.state()['prompts'][-1]['text'])

    def test_fresh_reviewer_closes_after_approval(self):
        batch = self.run / '.batches/M1.json'
        data = json.loads(batch.read_text()); data['reviewer_context'] = 'fresh'
        batch.write_text(json.dumps(data))
        self.tick()
        self.submitted()
        self.tick()
        reviewer = self.record(role='reviewer')
        self.approve()
        self.tick()
        self.assertNotIn(reviewer['pane_id'], self.state()['panes'])
        self.submitted(2)
        self.tick()
        self.assertNotEqual(reviewer['pane_id'], self.record('M2', 'reviewer')['pane_id'])

    def test_status_reports_process_lock_panes_agents_and_last_delivery_without_writing(self):
        self.tick()
        rec = self.record()
        out = self.cli('status', 'demo').stdout
        self.assertIn('conductor: not running', out)
        self.assertIn(rec['pane_id'], out)
        self.assertIn(rec['agent_name'], out)
        self.assertIn('last delivery:', out)

    def test_single_conductor_lock_and_foreground_completion_exit(self):
        proc = subprocess.Popen([sys.executable, str(fixtures.DOCKET), 'conduct', 'demo', '--interval', '1'],
                                cwd=self.f.root, env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not list((self.run / '.conductor').glob('*-implementor.json')):
                if proc.poll() is not None: break
                time.sleep(.02)
            self.assertIsNone(proc.poll(), 'conductor exited before acquiring its lock')
            refused = self.tick(ok=False)
            self.assertNotEqual(0, refused.returncode)
            self.assertIn('already running', refused.stderr)
            self.assertIn('conductor: running', self.cli('status', 'demo').stdout)
            self.submitted(); self.approve()
            self.submitted(2); self.approve(2)
            stdout, stderr = proc.communicate(timeout=15)
            self.assertEqual(0, proc.returncode, stderr)
            self.assertIn('complete', stdout)
        finally:
            if proc.poll() is None: proc.terminate(); proc.communicate(timeout=5)

    def test_refuses_outside_herdr_before_process_control(self):
        out = self.f.cli_env({'HERDR_ENV': '0'}, 'conduct', 'demo', '--once', ok=False)
        self.assertNotEqual(0, out.returncode)
        self.assertIn('HERDR_ENV=1', out.stderr)
        self.assertEqual([], self.state()['calls'])

    def test_successful_delivery_is_visible_as_delivered_in_existing_events_view(self):
        self.tick()
        out = self.cli('events', 'demo', '--role', 'implementor', '--peek').stdout
        self.assertIn('[delivered] T01:1:next-task', out)

    def test_crash_after_start_adopts_recorded_agent_instead_of_launching_again(self):
        stopped = self.tick(ok=False, fault='conduct:started')
        self.assertEqual(70, stopped.returncode)
        self.assertEqual(1, len(self.launches()))
        self.assertEqual([], self.state()['prompts'])
        self.tick()
        self.assertEqual(1, len(self.launches()))
        self.assertEqual(1, len(self.state()['prompts']))

    def test_worker_instructions_are_delivered_through_the_existing_event_system(self):
        self.tick(); self.submitted(); self.tick()
        rec = self.record(role='reviewer')
        self.cli('instruct', 'demo', '--role', 'reviewer', '--message', 'Check the preservation boundary.')
        self.tick()
        self.assertEqual(3, len(self.state()['prompts']))
        self.assertEqual(rec['agent_name'], self.state()['prompts'][-1]['name'])
        self.assertIn('Check the preservation boundary.', self.state()['prompts'][-1]['text'])

    def test_busy_final_worker_is_closed_before_conductor_exits(self):
        for n in (1, 2):
            self.tick(); self.submitted(n); self.tick(); self.approve(n)
        agents = self.state()['agents']
        impl = self.record('M2')
        agents[impl['agent_name']]['agent_status'] = 'working'
        self.update(agents=agents)
        self.assertNotIn('run complete', self.tick().stdout)
        self.assertNotIn('closed_at', {k: v for k, v in self.record('M2').items() if v})
        agents[impl['agent_name']]['agent_status'] = 'idle'
        self.update(agents=agents)
        self.assertIn('run complete', self.tick().stdout)
        self.assertNotIn(impl['pane_id'], self.state()['panes'])

    def test_native_session_identity_is_saved_before_pane_close(self):
        self.tick()
        rec = self.record()
        self.cli('arm', 'demo', '--role', 'implementor', '--session', rec['session'])
        path = self.f.root / '.docket/sessions' / (rec['session']+'.json')
        reg = json.loads(path.read_text())
        reg['native_harness'] = {'harness': 'claude', 'session_id': 'proven-native'}
        path.write_text(json.dumps(reg))
        self.submitted(); self.tick(); self.approve(); self.tick()
        self.assertEqual('proven-native', self.record()['native_session_id'])
        self.assertEqual('claude', self.record()['native_harness'])

    def test_blocked_worker_needs_no_completion_evidence_or_observed_model(self):
        self.tick()
        self.f.fill_scope(self.run / 'T01-scope.mdx')
        self.cli('scope', 'demo', 'T01', '--submit')
        self.f.fill_task_report(self.run / 'T01-report-01.mdx', blocked=True)
        self.cli('submit', 'demo', 'T01', '--blocked', '--as', 'implementor')
        self.tick()
        self.assertEqual('blocked', fixtures.parse_meta(self.run / 'T01-report-01.mdx')['status'])

    def test_empty_model_has_no_model_flag_and_invalid_user_args_fail_closed(self):
        batch = self.run / '.batches/M1.json'
        data = json.loads(batch.read_text()); data['implementor_model'] = ''
        batch.write_text(json.dumps(data))
        self.tick()
        self.assertEqual([], self.launches()[0][self.launches()[0].index('--')+1:])
        for value in ('--yolo', [42], ['bad\nargument'], {'args': []}):
            self.user.write_text(json.dumps({'codex_launch_args': value}))
            refused = self.cli('config', ok=False)
            self.assertNotEqual(0, refused.returncode)
            self.assertIn('codex_launch_args', refused.stderr)

    def test_fresh_reviewer_and_re_review_return_to_same_milestone_session(self):
        self.tick(); self.submitted(); self.tick()
        reviewer = self.record(role='reviewer')
        self.cli('decide', 'demo', 'T01', '--changes', '--as', 'reviewer',
                 '--change', '1. Preserve the acceptance boundary.')
        self.tick()
        self.cli('set-model', 'demo', 'T01', '--actual', 'correction-test-model')
        self.f.fill_task_report(self.run / 'T01-report-02.mdx', files='- `src/t01.py:1` - corrected.')
        self.cli('submit', 'demo', 'T01', '--as', 'implementor')
        self.cli('batch', 'demo', '--verify', 'M1', '--as', 'implementor')
        self.tick()
        review_prompts = [p for p in self.state()['prompts'] if p['name'] == reviewer['agent_name']]
        self.assertEqual(2, len(review_prompts))
        self.assertEqual(reviewer['pane_id'], self.record(role='reviewer')['pane_id'])

    def test_missing_registered_worker_is_preserved_and_reused_name_in_other_pane_refused(self):
        self.tick()
        rec = self.record()
        self.cli('arm', 'demo', '--role', 'implementor', '--session', rec['session'])
        agents = self.state()['agents']
        agent = agents.pop(rec['agent_name'])
        self.update(agents=agents)
        self.tick()
        self.assertEqual(1, len(self.launches()))
        agent['pane_id'] = 'unrelated:pane'
        agents[rec['agent_name']] = agent
        self.update(agents=agents)
        refused = self.tick(ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('replaced', refused.stderr)

    def test_conductor_refuses_fresh_subagent_policy_without_changing_it(self):
        plan = self.run / 'plan.mdx'
        plan.write_text(plan.read_text().replace('round_context: shared', 'round_context: fresh'))
        refused = self.tick(ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('round_context: shared', refused.stderr)
        self.assertEqual('fresh', fixtures.parse_meta(plan)['round_context'])
        self.assertEqual([], self.state()['calls'])

    def test_re_registered_recipient_cannot_inherit_old_worker_generation(self):
        self.tick()
        rec = self.record()
        self.cli('arm', 'demo', '--role', 'implementor', '--session', rec['session'])
        self.tick()
        self.cli('session', 'demo', '--register', '--role', 'implementor', '--session', rec['session'])
        refused = self.tick(ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('generation', refused.stderr)
        self.assertEqual(1, len(self.state()['prompts']))

    def test_live_agent_kind_must_match_launch_record(self):
        self.tick()
        agents = self.state()['agents']
        agents[self.record()['agent_name']]['kind'] = 'opencode'
        self.update(agents=agents)
        refused = self.tick(ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('harness', refused.stderr)

    def test_live_native_session_must_match_worker_registration(self):
        self.tick()
        rec = self.record()
        self.cli('arm', 'demo', '--role', 'implementor', '--session', rec['session'])
        path = self.f.root / '.docket/sessions' / (rec['session']+'.json')
        reg = json.loads(path.read_text())
        reg['native_harness'] = {'harness': 'claude', 'session_id': 'registered-native'}
        path.write_text(json.dumps(reg))
        agents = self.state()['agents']
        agents[rec['agent_name']]['agent_session'] = 'replaced-native'
        self.update(agents=agents)
        refused = self.tick(ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('native session', refused.stderr)

    def test_planner_events_remain_for_its_existing_hook(self):
        self.tick()
        self.cli('instruct', 'demo', '--role', 'planner', '--message', 'Resolve the intent question.')
        self.tick()
        self.assertEqual(1, len(self.state()['prompts']))
        out = self.cli('events', 'demo', '--role', 'planner', '--peek').stdout
        self.assertIn('pending', out)
        self.assertIn('Resolve the intent question.', out)
        self.assertFalse((self.run / '.delivery/planner').exists())


    def incidents(self):
        return [json.loads(p.read_text()) for p in (self.run / '.incidents').glob('*.json')]

    def native(self, record, native_id='native-thread'):
        self.cli('arm', 'demo', '--role', record['role'], '--session', record['session'])
        path = self.f.root / '.docket/sessions' / (record['session']+'.json')
        reg = json.loads(path.read_text())
        reg['native_harness'] = {'harness': record['requested_harness'], 'session_id': native_id}
        path.write_text(json.dumps(reg))

    def age_worker(self, role='implementor', bid='M1'):
        path = self.run / '.conductor' / f'{bid}-{role}.json'
        record = json.loads(path.read_text())
        record['obligation_since'] = time.time() - 600
        record['idle_since'] = time.time() - 600
        path.write_text(json.dumps(record))

    def test_b_transient_outage_retries_without_exiting_or_opening_an_incident(self):
        self.update(failures_left=1)
        out = self.cli('conduct', 'demo', '--once', '--interval', '.01', '--outage-seconds', '2')
        self.assertEqual(0, out.returncode)
        self.assertEqual(1, len(self.state()['prompts']))
        self.assertEqual([], self.incidents())
        calls = [c for c in self.state()['calls'] if c == ['agent', 'list']]
        self.assertGreaterEqual(len(calls), 2)

    def test_b_sustained_outage_stops_with_one_planner_incident(self):
        self.update(failures_left=1000)
        out = self.cli('conduct', 'demo', '--once', '--interval', '.01', '--outage-seconds', '.05', ok=False)
        self.assertNotEqual(0, out.returncode)
        self.assertEqual(1, len(self.incidents()))
        self.assertEqual('planner', self.incidents()[0]['recipient'])
        self.assertIn('outage', out.stderr)
        planner = self.cli('events', 'demo', '--role', 'planner', '--peek').stdout
        self.assertIn('stalled', planner)
        self.assertNotIn('stalled', self.cli('events', 'demo', '--role', 'implementor', '--peek').stdout)
        self.cli('arm', 'demo', '--role', 'planner', '--session', 'plan-session')
        hook = self.f.cli_env({'DOCKET_WATCH_HOOK': '1', 'DOCKET_SESSION': 'plan-session',
                               'DOCKET_ROLE': 'planner'}, 'watch', 'demo', '--role', 'planner',
                              '--timeout', '0', ok=False)
        self.assertEqual(3, hook.returncode, hook.stderr)
        self.assertIn('stalled', hook.stderr)
        self.cli('pickup', 'demo', '--role', 'planner', '--session', 'plan-session')
        self.cli('conduct', 'demo', '--once', '--interval', '.01', '--outage-seconds', '.01', ok=False)
        self.assertEqual(1, len(self.incidents()))

    def test_b_worker_tab_created_once_without_splitting_the_conductor(self):
        self.tick(); self.submitted(); self.tick(); self.approve(); self.tick()
        calls = self.state()['calls']
        creates = [c for c in calls if c[:2] == ['tab', 'create']]
        self.assertEqual(1, len(creates))
        layout = json.loads((self.run / '.conductor/layout.json').read_text())
        self.assertEqual('fake:t1', layout['tab_id'])
        splits = [c for c in calls if c[:2] == ['pane', 'split']]
        self.assertTrue(splits)
        for split in splits:
            self.assertNotIn('--current', split)
            self.assertEqual('fake:anchor', split[split.index('--pane')+1])
        self.tick()
        self.assertEqual(1, len([c for c in self.state()['calls'] if c[:2] == ['tab', 'create']]))

    def test_b_codex_hook_review_screen_is_passed_then_prompted(self):
        self.tick(); self.submitted(); self.update(trust='Hooks need review')
        self.tick(); self.tick()
        reviewer = self.record(role='reviewer')
        prompts = [p for p in self.state()['prompts'] if p['name'] == reviewer['agent_name']]
        self.assertEqual(1, len(prompts))
        keys = [c for c in self.state()['calls'] if c[:3] == ['agent', 'send-keys', reviewer['agent_name']]]
        self.assertTrue(keys)
        self.assertEqual([], self.incidents())

    def test_b_unknown_blocked_dialog_immediately_opens_one_planner_incident(self):
        self.update(trust='Approve access to the private credential?')
        self.tick(); self.tick()
        self.assertEqual(1, len(self.incidents()))
        self.assertEqual('planner', self.incidents()[0]['recipient'])
        self.assertIn('blocked dialog', self.incidents()[0]['cause'])
        self.assertFalse(any(c[:2] == ['agent', 'send-keys'] for c in self.state()['calls']))
        self.assertIn('stalled', self.cli('events', 'demo', '--role', 'planner', '--peek').stdout)

    def test_b_continue_reviewers_close_at_completion(self):
        self.tick(); self.submitted(); self.tick()
        reviewer = self.record(role='reviewer')
        self.native(reviewer, 'review-thread')
        self.approve(); self.tick(); self.submitted(2); self.tick(); self.approve(2)
        out = self.tick()
        self.assertIn('run complete', out.stdout)
        self.assertNotIn(reviewer['pane_id'], self.state()['panes'])
        for bid in ('M1', 'M2'):
            rec = self.record(bid, 'reviewer')
            self.assertTrue(rec['closed_at'])
            self.assertEqual('review-thread', rec['native_session_id'])

    def test_b_fresh_successor_closes_superseded_continue_reviewer(self):
        batch = self.run / '.batches/M2.json'
        data = json.loads(batch.read_text()); data['reviewer_context'] = 'fresh'
        batch.write_text(json.dumps(data))
        self.tick(); self.submitted(); self.tick()
        old = self.record(role='reviewer'); self.native(old, 'old-review')
        self.approve(); self.tick(); self.submitted(2); self.tick()
        self.assertNotIn(old['pane_id'], self.state()['panes'])
        self.assertTrue(self.record(role='reviewer')['closed_at'])
        self.assertEqual('old-review', self.record(role='reviewer')['native_session_id'])
        self.assertNotEqual(old['pane_id'], self.record('M2', 'reviewer')['pane_id'])

    def test_b_idle_unanswered_obligation_opens_one_incident_without_reprompting(self):
        self.tick(); self.age_worker()
        self.cli('conduct', 'demo', '--once', '--stall-seconds', '1')
        self.cli('conduct', 'demo', '--once', '--stall-seconds', '1')
        self.assertEqual(1, len(self.incidents()))
        self.assertIn('unanswered obligation', self.incidents()[0]['cause'])
        self.assertEqual(1, len(self.state()['prompts']))

    def test_b_idle_undelivered_obligation_stalls_but_busy_work_does_not(self):
        self.update(fail_prompt=True)
        self.tick(); self.age_worker()
        self.cli('conduct', 'demo', '--once', '--stall-seconds', '1')
        self.assertEqual(1, len(self.incidents()))
        self.assertIn('undelivered obligation', self.incidents()[0]['cause'])
        incident = self.incidents()[0]
        self.cli('health', 'demo', '--resolve-stall', incident['id'])
        agents = self.state()['agents']; agents[self.record()['agent_name']]['agent_status'] = 'working'
        self.update(agents=agents)
        self.age_worker()
        self.cli('conduct', 'demo', '--once', '--stall-seconds', '1')
        self.assertEqual(1, len(self.incidents()))
        self.assertEqual('resolved', self.incidents()[0]['state'])

    def test_c_provider_usage_limit_waits_for_idle_without_model_switch(self):
        self.tick()
        agents = self.state()['agents']
        agents[self.record()['agent_name']].update(agent_status='working', output="You've hit your usage limit. Try again at 7pm.")
        self.update(agents=agents)
        self.tick(); self.tick()
        self.assertEqual([], self.incidents())
        agents[self.record()['agent_name']]['agent_status'] = 'idle'
        self.update(agents=agents)
        self.tick(); self.tick()
        self.assertEqual(1, len(self.incidents()))
        self.assertIn('provider usage limit', self.incidents()[0]['cause'])
        self.assertEqual('small', self.record()['requested_model'])
        self.assertEqual(1, len(self.launches()))

    def test_b_reopen_claude_uses_native_session_and_new_pane_without_registering_for_worker(self):
        self.tick(); rec = self.record(); self.native(rec, 'claude-native')
        self.submitted(); self.tick(); self.approve(); self.tick()
        old_bytes = (self.run / 'T01-report-01.mdx').read_bytes()
        self.cli('reopen', 'demo', '--milestone', 'M1', '--role', 'implementor')
        reopened = self.record()
        self.assertNotEqual(rec['pane_id'], reopened['pane_id'])
        launch = self.launches()[-1]
        self.assertEqual(['--model', 'small', '--resume', 'claude-native'], launch[launch.index('--')+1:])
        self.assertFalse((self.f.root / '.docket/sessions' / (reopened['session']+'.json')).exists())
        self.assertEqual(old_bytes, (self.run / 'T01-report-01.mdx').read_bytes())
        self.cli('reopen', 'demo', '--milestone', 'M1', '--role', 'implementor')
        self.assertEqual(reopened['pane_id'], self.record()['pane_id'])

    def test_b_reopen_codex_uses_resume_and_refuses_missing_identity(self):
        self.tick(); self.submitted(); self.tick(); self.approve(); self.tick()
        self.submitted(2); self.tick()
        reviewer = self.record('M2', 'reviewer'); self.native(reviewer, 'codex-native')
        self.approve(2); self.tick()
        self.cli('reopen', 'demo', '--milestone', 'M2', '--role', 'reviewer')
        launch = self.launches()[-1]
        self.assertEqual(['resume', 'codex-native', '-m', 'capable'], launch[launch.index('--')+1:])
        before = len(self.launches())
        refused = self.cli('reopen', 'demo', '--milestone', 'M1', '--role', 'implementor', ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('no recorded native session', refused.stderr)
        self.assertEqual(before, len(self.launches()))

    def test_c_earlier_code_output_is_not_a_provider_limit_for_busy_or_idle_agents(self):
        self.tick()
        name = self.record()['agent_name']
        code = ('```python\n'
                'GEMINI_ERRORS = ["too many requests", "rate limit exceeded", "usage limit reached"]\n'
                '```\n')
        output = code + '\n'.join(f'Checked service behavior {i}.' for i in range(12)) + '\nReady.\n> '
        for status in ('idle', 'working', 'done'):
            with self.subTest(status=status):
                agents = self.state()['agents']
                agents[name].update(agent_status=status, output=output)
                self.update(agents=agents)
                self.tick()
                self.assertEqual([], self.incidents())

    def test_c_harness_final_limit_line_uses_only_the_detection_tail(self):
        self.tick()
        name = self.record()['agent_name']
        output = '\n'.join(f'Agent output {i}' for i in range(20)) + '\nError: usage limit reached'
        for status in ('idle', 'blocked'):
            with self.subTest(status=status):
                agents = self.state()['agents']
                agents[name].update(agent_status=status, output=output)
                self.update(agents=agents)
                self.tick()
                self.assertEqual(1, len(self.incidents()))
                self.assertIn('provider usage limit', self.incidents()[0]['cause'])
                reads = [c for c in self.state()['calls'] if c[:3] == ['agent', 'read', name]]
                self.assertTrue(any(c[c.index('--source')+1] == 'detection' and
                                    int(c[c.index('--lines')+1]) <= 5 for c in reads))
                self.assertFalse(any(c[c.index('--source')+1].startswith('recent') for c in reads))

    def test_b_skill_front_door_routes_launch_to_conductor_and_recovery_to_reopen(self):
        skill = fixtures.DOCKET.resolve().parents[1] / 'SKILL.md'
        text = skill.read_text()
        self.assertGreaterEqual(len(text.splitlines()), 60)
        self.assertLessEqual(len(text.splitlines()), 90)
        self.assertIn('docket conduct R01', text)
        self.assertIn('docket reopen R01', text)
        self.assertIn('end your turn', text)
        self.assertNotIn('implementor owns `launch:reviewer`', text)
        self.assertNotIn('docket watch R01', text)

    def test_b_busy_superseded_reviewer_is_not_reused_across_a_fresh_milestone(self):
        self.cli('assign', 'demo', 'T03', '--file', 'src/t03.py', '--verify', 'true', '--as', 'planner')
        self.f.fill_task(self.run / 'T03-task.mdx')
        self.cli('batch', 'demo', '--create', 'M3', '--members', 'T03', '--milestone',
                 '--command', 'true', '--as', 'planner')
        self.cli('batch', 'demo', '--close', 'M3', '--as', 'planner')
        batch = self.run / '.batches/M2.json'
        data = json.loads(batch.read_text()); data['reviewer_context'] = 'fresh'
        batch.write_text(json.dumps(data))
        self.tick(); self.submitted(); self.tick()
        old = self.record(role='reviewer')
        agents = self.state()['agents']; agents[old['agent_name']]['agent_status'] = 'working'
        self.update(agents=agents)
        self.approve(); self.tick(); self.submitted(2); self.tick(); self.approve(2)
        self.tick()
        agents = self.state()['agents']; agents[old['agent_name']]['agent_status'] = 'idle'
        self.update(agents=agents)
        self.tick()
        self.assertNotIn(old['pane_id'], self.state()['panes'])
        self.submitted(3); self.tick()
        self.assertNotEqual(old['agent_name'], self.record('M3', 'reviewer')['agent_name'])

    def test_b_reopen_draft_closes_old_shell_and_requires_the_worker_to_resume_its_binding(self):
        self.tick(); old = self.record(); self.native(old, 'native-draft')
        self.cli('dispatch', 'demo', 'T01', '--session', old['session'], '--register')
        agents = self.state()['agents']; agents.pop(old['agent_name'])
        self.update(agents=agents)
        self.cli('reopen', 'demo', '--milestone', 'M1', '--role', 'implementor')
        self.assertNotIn(old['pane_id'], self.state()['panes'])
        new = self.record()
        dispatch = self.run / '.dispatch/T01.json'
        self.assertEqual(old['session'], json.loads(dispatch.read_text())['session'])
        self.cli('resume', 'demo', 'T01', '--session', new['session'], '--agent', new['agent_name'], '--register')
        self.assertEqual(new['session'], json.loads(dispatch.read_text())['session'])
        self.submitted(); self.tick(); self.approve(); self.tick()
        self.assertNotIn(new['pane_id'], self.state()['panes'])

    def test_b_reopen_interrupted_after_launch_reconciles_without_another_pane(self):
        self.tick(); old = self.record(); self.native(old, 'resume-thread')
        self.submitted(); self.tick(); self.approve(); self.tick()
        stopped = self.cli('reopen', 'demo', '--milestone', 'M1', '--role', 'implementor',
                           fault='conduct:started', ok=False)
        self.assertEqual(70, stopped.returncode)
        count = len(self.launches())
        pane = self.record()['pane_id']
        self.cli('reopen', 'demo', '--milestone', 'M1', '--role', 'implementor')
        self.assertEqual(pane, self.record()['pane_id'])
        self.assertEqual(count, len(self.launches()))

    def test_b_existing_codex_limit_event_cannot_prompt_an_automatic_model_switch(self):
        self.tick(); self.submitted(); self.tick(); self.approve(); self.tick()
        worker = self.record('M2')
        plan = self.run / 'plan.mdx'
        plan.write_text(plan.read_text().replace('workflow: five-role-v1',
                        'primary_model: next-model\nfallback_models: opencode/test\n'
                        'implementor_fallback_models: opencode/test\nworkflow: five-role-v1'))
        self.cli('dispatch', 'demo', 'T02', '--session', worker['session'], '--register')
        home = Path(self.f._feedback_tmp.name) / 'codex-limit'
        day = home / 'sessions/2026/10/25'; day.mkdir(parents=True)
        (day / 'rollout-limit.jsonl').write_text(json.dumps({'timestamp': '2026-10-25T00:00:00Z',
            'payload': {'rate_limits': {'primary': {'used_percent': 100, 'window_minutes': 300,
                                                  'resets_at': 1003600}}}}) + '\n')
        self.f.cli_env({'CODEX_HOME': str(home), 'DOCKET_NOW': '1000000'},
                       'conduct', 'demo', '--once')
        self.assertTrue(self.incidents())
        prompts = [p for p in self.state()['prompts'] if p['name'] == worker['agent_name']]
        self.assertEqual(1, len(prompts))
        self.assertFalse(any('--on-limit' in p['text'] for p in prompts))

    def test_b_claude_short_limit_banner_opens_a_planner_incident(self):
        self.tick()
        agents = self.state()['agents']
        agents[self.record()['agent_name']]['output'] = "You've hit your limit · resets 7pm"
        self.update(agents=agents)
        self.tick()
        self.assertEqual(1, len(self.incidents()))
        self.assertEqual('planner', self.incidents()[0]['recipient'])
        self.assertIn('provider usage limit', self.incidents()[0]['cause'])

    def test_b_reopen_refuses_a_closed_workers_registration_for_another_role(self):
        self.tick(); old = self.record(); self.native(old, 'saved-native')
        self.submitted(); self.tick(); self.approve(); self.tick()
        path = self.f.root / '.docket/sessions' / (old['session']+'.json')
        reg = json.loads(path.read_text()); reg['role'] = 'reviewer'
        path.write_text(json.dumps(reg))
        before = len(self.launches())
        refused = self.cli('reopen', 'demo', '--milestone', 'M1', '--role', 'implementor', ok=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertEqual(before, len(self.launches()))


if __name__ == '__main__':
    unittest.main()
