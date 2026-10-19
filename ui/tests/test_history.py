import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('inspector_history', Path(__file__).parents[1] / 'inspection.py')
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)
import provider_history as history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.workspace = self.base / 'project'
        self.run = self.workspace / '.docket/runs/R01'
        self.run.mkdir(parents=True)
        (self.run / 'plan.mdx').write_text('---\nworkflow: five-role-v1\nmode: quick\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: draft\n---\n# Plan: History\n')
        self.slot = {'id': 'planner:a', 'generation': 'g1', 'native': {'harness': 'opencode', 'session_id': 'a'}}

    def ledger(self, rows):
        (self.run / '.harness-sessions.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def note(self, role='implementor', sid='a', owner='T01', **extra):
        return {'role': role, 'harness': 'opencode', 'session_id': sid, 'cwd': str(self.workspace), 'owner': owner, **extra}

    def db(self):
        db = self.base / 'opencode.db'
        con = sqlite3.connect(db)
        con.executescript('create table session(id text, directory text, title text, parent_id text); create table message(id text, session_id text, time_created integer, data text); create table part(id text, session_id text, message_id text, time_created integer, data text);')
        con.executemany('insert into session(id,directory) values (?,?)', [('a', str(self.workspace)), ('b', str(self.workspace)), ('foreign', str(self.base / 'other'))])
        con.executemany('insert into message values (?,?,?,?)', [('m1','a',1,'{"role":"user"}'), ('m2','a',2,'{"role":"assistant"}'), ('secret','b',3,'{"role":"assistant"}')])
        con.executemany('insert into part values (?,?,?,?,?)', [
            ('p1','a','m1',1,json.dumps({'type':'text','text':'Hello <script>alert(1)</script>'})),
            ('p2','a','m2',2,json.dumps({'type':'text','text':'Native reply'})),
            ('p3','a','m2',3,json.dumps({'type':'tool','tool':'bash','state':{'status':'running','input':{'command':'echo hello'},'output':'z'*13000}})),
            ('p4','b','secret',4,json.dumps({'type':'text','text':'Other session secret'}))])
        con.commit(); con.close()
        return db

    def test_opencode_exact_session_readonly_and_no_live_request_authority(self):
        db = self.db(); before = db.read_bytes()
        with patch.object(history, 'opencode_db', return_value=db):
            result = history.load_history(self.workspace, self.slot)
        self.assertTrue(result['capabilities']['history'])
        self.assertEqual([m['text'] for m in result['messages'][:2]], ['Hello <script>alert(1)</script>', 'Native reply'])
        self.assertEqual(result['messages'][2]['status'], 'running')
        self.assertTrue(result['partial']); self.assertTrue(result['messages'][2]['truncated'])
        self.assertEqual(len(result['messages'][2]['text']),12000)
        self.assertFalse(result['capabilities']['permissions']); self.assertFalse(result['capabilities']['send'])
        self.assertNotIn('Other session secret', json.dumps(result)); self.assertEqual(before, db.read_bytes())

    def test_foreign_unknown_and_arbitrary_session_ids_are_refused(self):
        db = self.db()
        with patch.object(history, 'opencode_db', return_value=db):
            for sid in ('foreign', 'unknown', '../a'):
                self.slot['native']['session_id'] = sid
                result = history.load_history(self.workspace, self.slot)
                self.assertFalse(result['capabilities']['history']); self.assertEqual(result['messages'], [])

    def test_native_ledger_history_is_exact_and_does_not_guess_other_sessions(self):
        self.ledger([self.note(), self.note(sid='b', owner='T02'), self.note(role='planner', sid='foreign', cwd=str(self.base / 'other'))])
        slots = inspector.snapshot(self.workspace)['runs'][0]['slots']
        native = [s for s in slots if s.get('native')]
        self.assertEqual([s['native']['session_id'] for s in native], ['a', 'b'])
        self.assertTrue(all(s['historical'] for s in native))
        a = native[0]; db = self.db()
        with patch.object(history, 'opencode_db', return_value=db):
            result = inspector.history(self.workspace, 'R01', a['id'], a['generation'])
        self.assertTrue(result['capabilities']['history'])
        with self.assertRaisesRegex(ValueError, 'generation changed'):
            inspector.history(self.workspace, 'R01', a['id'], 'stale')

    def test_released_workers_collapse_to_one_current_implementor_holding_the_latest_dispatch(self):
        directory = self.workspace / '.docket/sessions'; directory.mkdir()
        dispatch = self.run / '.dispatch'; dispatch.mkdir()
        for owner, sid, at in [('T01','impl-1','2026-10-08T10:00:00Z'), ('T02','impl-2','2026-10-08T11:00:00Z')]:
            (directory / f'{sid}.json').write_text(json.dumps({'session':sid,'role':'implementor','run':'R01','workspace':str(self.workspace)}))
            (dispatch / f'{owner}.json').write_text(json.dumps({'session':sid,'role':'implementor','run':'R01','owner':owner,'state':'released','dispatched_at':at}))
        workers = [s for s in inspector.snapshot(self.workspace)['runs'][0]['slots'] if s['role']=='implementor']
        self.assertEqual([(s['session'], s['group'], s['historical']) for s in workers],
                         [('impl-2', 'current', False), ('impl-1', 'earlier', True)])
        self.assertEqual(workers[0]['task_note'], 'last task T02')

    def test_conflicting_role_native_identity_is_refused(self):
        self.ledger([self.note(role='planner'), self.note(role='reviewer')])
        for slot in [s for s in inspector.snapshot(self.workspace)['runs'][0]['slots'] if s.get('native')]:
            result=history.load_history(self.workspace,slot)
            self.assertFalse(result['capabilities']['history']); self.assertIn('ambiguous',result['reason'])

    def test_registration_process_and_ledger_disagreement_are_not_silently_merged(self):
        directory=self.workspace/'.docket/sessions'; directory.mkdir(); process={'pid':123,'start':'42'}
        (directory/'planner.json').write_text(json.dumps({'session':'planner','run':'R01','role':'planner','workspace':str(self.workspace),'native_harness':{'harness':'opencode','session_id':'wrong'},'native_process':process}))
        self.ledger([self.note(role='planner',sid='right',worker_process=process)])
        slots=inspector.snapshot(self.workspace)['runs'][0]['slots']
        reg=next(s for s in slots if s['id']=='planner:planner')
        self.assertIn('disagree',reg['mapping_issue'])
        self.assertTrue(next(s for s in slots if s.get('native',{}).get('session_id')=='right')['historical'])

    def test_task_history_needs_one_native_identity_in_the_exact_dispatch_window(self):
        directory=self.workspace/'.docket/sessions'; directory.mkdir()
        (directory/'worker.json').write_text(json.dumps({'session':'worker','run':'R01','role':'implementor','workspace':str(self.workspace),'generation':1}))
        dispatch=self.run/'.dispatch'; dispatch.mkdir()
        (dispatch/'T01.json').write_text(json.dumps({'session':'worker','session_generation':1,'run':'R01','role':'implementor','owner':'T01','state':'dispatched','dispatched_at':'2026-10-08T10:00:00Z'}))
        self.ledger([self.note(at='2026-10-08T10:01:00Z')])
        worker=next(s for s in inspector.snapshot(self.workspace)['runs'][0]['slots'] if s['id']=='implementor:worker')
        self.assertEqual(worker['native']['session_id'],'a'); self.assertEqual(worker['history_attribution'],'task-ledger')
        self.assertFalse(worker['historical'])
        self.ledger([self.note(at='2026-10-08T09:00:00Z')])
        worker=next(s for s in inspector.snapshot(self.workspace)['runs'][0]['slots'] if s['id']=='implementor:worker')
        self.assertFalse(worker['native'])
        self.ledger([self.note(at='2026-10-08T10:01:00Z'),self.note(sid='b',at='2026-10-08T10:02:00Z')])
        worker=next(s for s in inspector.snapshot(self.workspace)['runs'][0]['slots'] if s['id']=='implementor:worker')
        self.assertFalse(worker['native'])

    def test_missing_planner_history_can_be_explicitly_linked_without_rewriting_roles(self):
        db=self.db(); self.ledger([self.note(role='implementor',sid='a')])
        planner=next(s for s in inspector.snapshot(self.workspace)['runs'][0]['slots'] if s['role']=='planner')
        before={p:p.read_bytes() for p in self.workspace.rglob('*') if p.is_file()}
        with patch.object(history,'opencode_db',return_value=db):
            automatic=inspector.history(self.workspace,'R01',planner['id'],planner['generation'])
            self.assertFalse(automatic['capabilities']['history'])
            choices=inspector.history_choices(self.workspace,'R01',planner['id'],planner['generation'])['choices']
            self.assertEqual([c['id'] for c in choices],['opencode:a'])
            self.assertTrue(choices[0]['label'].startswith('Implementor · opencode'))
            wider=inspector.history_choices(self.workspace,'R01',planner['id'],planner['generation'],'project')['choices']
            self.assertEqual([c['id'] for c in wider],['opencode:a','opencode:b'])
            with self.assertRaisesRegex(ValueError,'run.s session list'):
                inspector.history(self.workspace,'R01',planner['id'],planner['generation'],'opencode:b')
            self.assertTrue(inspector.history(self.workspace,'R01',planner['id'],planner['generation'],'opencode:b','project')['capabilities']['history'])
            selected=inspector.history(self.workspace,'R01',planner['id'],planner['generation'],'opencode:a')
            self.assertTrue(selected['capabilities']['history']); self.assertEqual(selected['recorded_roles'],['implementor'])
            self.assertIn('recorded under implementor',selected['reason'])
            self.assertFalse(selected['capabilities']['send'])
            for choice in ('opencode:foreign','opencode:unknown','opencode:../a'):
                with self.assertRaisesRegex(ValueError,'session list'):
                    inspector.history(self.workspace,'R01',planner['id'],planner['generation'],choice)
        self.assertEqual(before,{p:p.read_bytes() for p in self.workspace.rglob('*') if p.is_file()})

    def test_earlier_task_cannot_be_relinked_to_a_new_conversation(self):
        self.ledger([self.note()])
        earlier=next(s for s in inspector.snapshot(self.workspace)['runs'][0]['slots'] if s.get('historical'))
        with self.assertRaisesRegex(ValueError,'Earlier task'):
            inspector.history(self.workspace,'R01',earlier['id'],earlier['generation'],'opencode:b')

    def transcript(self, provider, sid, rows):
        home=self.base/provider
        path=home/(f'sessions/2026/10/08/rollout-date-{sid}.jsonl' if provider=='codex' else f'projects/project/{sid}.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return home,path

    def test_codex_history_deduplicates_mirrors_and_excludes_internal_reasoning(self):
        sid='exact'
        home,path=self.transcript('codex',sid,[
            {'type':'session_meta','payload':{'id':sid,'cwd':str(self.workspace)}},
            {'type':'event_msg','payload':{'type':'user_message','message':'Hello'}},
            {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'Hello'}]}},
            {'type':'response_item','payload':{'type':'reasoning','summary':[{'text':'internal'}]}},
            {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Reply'}]}},
            {'type':'response_item','payload':{'type':'function_call','name':'exec','arguments':'{"cmd":"pwd"}'}},
        ])
        self.slot['native']={'harness':'codex','session_id':sid}; before=path.read_bytes()
        with patch.object(history,'codex_home',return_value=home): result=history.load_history(self.workspace,self.slot)
        self.assertEqual([e['text'] for e in result['messages'][:2]],['Hello','Reply']); self.assertEqual(len(result['messages']),3)
        self.assertNotIn('internal',json.dumps(result)); self.assertEqual(before,path.read_bytes())

    def test_claude_history_accepts_tools_but_no_subagent_or_permission_actions(self):
        sid='claude-exact'
        home,path=self.transcript('claude',sid,[
            {'sessionId':sid,'cwd':str(self.workspace),'type':'user','message':{'content':'Hello'}},
            {'sessionId':sid,'type':'assistant','message':{'content':[{'type':'text','text':'Reply'},{'type':'thinking','thinking':'private'},{'type':'tool_use','name':'Bash','input':{'command':'pwd'}}]}},
            {'sessionId':'other','type':'user','message':{'content':'Other session'}},
        ])
        self.slot['native']={'harness':'claude','session_id':sid}
        with patch.object(history,'claude_config_dir',return_value=home): result=history.load_history(self.workspace,self.slot)
        self.assertEqual(len(result['messages']),3); self.assertNotIn('private',json.dumps(result)); self.assertNotIn('Other session',json.dumps(result))
        self.assertFalse(result['capabilities']['questions'])

    def test_claude_subagent_rounds_merge_in_time_order_and_strangers_are_ignored(self):
        sid='claude-parent'
        home,path=self.transcript('claude',sid,[
            {'sessionId':sid,'cwd':str(self.workspace),'type':'user','timestamp':'2026-10-09T10:00:00Z','message':{'content':'Coordinate'}},
            {'sessionId':sid,'type':'assistant','timestamp':'2026-10-09T10:00:05Z','message':{'content':[{'type':'tool_use','name':'Agent','input':{'description':'Run T01 round 1','prompt':'Read p.txt'}}]}},
            {'sessionId':sid,'type':'assistant','timestamp':'2026-10-09T10:09:00Z','message':{'content':[{'type':'text','text':'submitted'}]}},
        ])
        agents=path.parent/sid/'subagents'; agents.mkdir(parents=True)
        (agents/'agent-a1.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in [
            {'sessionId':sid,'isSidechain':True,'cwd':str(self.workspace),'type':'user','timestamp':'2026-10-09T10:00:06Z','message':{'content':'Read p.txt'}},
            {'sessionId':sid,'isSidechain':True,'type':'assistant','timestamp':'2026-10-09T10:01:00Z','message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':'docket submit R T01 --as implementor'}}]}},
        ]))
        (agents/'agent-b2.jsonl').write_text(json.dumps({'sessionId':'someone-else','isSidechain':True,'cwd':str(self.workspace),'type':'user','timestamp':'2026-10-09T10:02:00Z','message':{'content':'Foreign subagent'}})+'\n')
        self.slot['native']={'harness':'claude','session_id':sid}
        with patch.object(history,'claude_config_dir',return_value=home): result=history.load_history(self.workspace,self.slot)
        messages=result['messages']
        self.assertEqual(['Coordinate','Run T01 round 1','Read p.txt','docket submit R T01 --as implementor','submitted'],
                         [m.get('summary') or m['text'] for m in messages])
        self.assertEqual([False,False,True,True,False],[bool(m.get('subagent')) for m in messages])
        self.assertNotIn('Foreign subagent',json.dumps(result))

    def test_opencode_child_sessions_are_the_roles_subagent_work(self):
        db=self.db(); con=sqlite3.connect(db)
        con.execute("insert into session(id,directory,parent_id) values ('child',?, 'a')",(str(self.workspace),))
        con.execute("insert into message values ('m9','child',5,'{\"role\":\"assistant\"}')")
        con.execute("insert into part values ('p9','child','m9',5,?)",(json.dumps({'type':'tool','tool':'bash','state':{'status':'completed','input':{'command':'docket submit R T01'},'output':'ok'}}),))
        con.commit(); con.close()
        with patch.object(history,'opencode_db',return_value=db): result=history.load_history(self.workspace,self.slot)
        last=result['messages'][-1]
        self.assertEqual(('docket submit R T01',True),(last['summary'],last.get('subagent')))
        self.assertFalse(result['messages'][0].get('subagent'))

    def test_typed_slash_commands_are_activity_not_user_messages(self):
        sid='claude-slash'
        home,path=self.transcript('claude',sid,[
            {'sessionId':sid,'cwd':str(self.workspace),'type':'user','message':{'content':'<local-command-caveat>Caveat: generated</local-command-caveat>'}},
            {'sessionId':sid,'type':'user','message':{'content':'<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args>opus</command-args>'}},
            {'sessionId':sid,'type':'user','message':{'content':'<local-command-stdout>Set model to Opus</local-command-stdout>'}},
            {'sessionId':sid,'type':'user','message':{'content':'A real question'}},
        ])
        self.slot['native']={'harness':'claude','session_id':sid}
        with patch.object(history,'claude_config_dir',return_value=home): result=history.load_history(self.workspace,self.slot)
        self.assertEqual([('tool','Slash command','/model opus'),('tool','Command output','Set model to Opus'),('user','','A real question')],
                         [(m['role'],m.get('name',''),m.get('summary') or m['text']) for m in result['messages']])
        self.assertNotIn('Caveat',json.dumps(result))

    def test_large_history_and_partial_append_are_bounded_and_labelled(self):
        sid='large'; rows=[{'sessionId':sid,'cwd':str(self.workspace),'type':'user','message':{'content':'Start'}}]
        rows += [{'sessionId':sid,'type':'assistant','message':{'content':'x'*10000}} for _ in range(300)]
        home,path=self.transcript('claude',sid,rows)
        with path.open('a') as fh: fh.write('{"unfinished":')
        self.slot['native']={'harness':'claude','session_id':sid}
        with patch.object(history,'claude_config_dir',return_value=home): result=history.load_history(self.workspace,self.slot)
        self.assertTrue(result['partial']); self.assertLessEqual(len(result['messages']),200); self.assertTrue(result['capabilities']['history'])

    def test_forged_header_duplicate_file_and_symlink_are_refused(self):
        sid='exact'; home,path=self.transcript('codex',sid,[{'type':'session_meta','payload':{'id':'other','cwd':str(self.workspace)}}])
        self.slot['native']={'harness':'codex','session_id':sid}
        with patch.object(history,'codex_home',return_value=home):
            self.assertFalse(history.load_history(self.workspace,self.slot)['capabilities']['history'])
            duplicate=path.with_name(f'rollout-another-{sid}.jsonl'); duplicate.write_bytes(path.read_bytes())
            self.assertIn('ambiguous',history.load_history(self.workspace,self.slot)['reason']); duplicate.unlink()
            target=self.base/'outside.jsonl'; target.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(target)
            self.assertIn('confined',history.load_history(self.workspace,self.slot)['reason'])

    def shard_pair(self, sid='exact', base=True, **meta):
        """Root rollout plus a resumed shard declaring history_base at the end of the root's line 2."""
        def msg(role, kind, body): return {'type':'response_item','timestamp':'t','payload':{'type':'message','role':role,'content':[{'type':kind,'text':body}]}}
        rows=[{'type':'session_meta','payload':{'id':sid,'cwd':str(self.workspace)}}, msg('user','input_text','old question'), msg('assistant','output_text','old answer')]
        home,root=self.transcript('codex',sid,rows)
        offset=len(''.join(json.dumps(r)+'\n' for r in rows).encode())
        with root.open('a') as fh: fh.write(json.dumps(msg('user','input_text','aborted tail'))+'\n')
        payload={'id':sid,'cwd':str(self.workspace)}
        if base: payload['history_base']={'thread_id':sid,'end_ordinal_exclusive':3,'end_byte_offset':offset}
        payload.update(meta)
        shard=root.with_name(f'{root.stem}_01a11c82.jsonl')
        shard.write_text(''.join(json.dumps(r)+'\n' for r in [{'type':'session_meta','payload':payload}, msg('user','input_text','new question'), msg('assistant','output_text','new answer')]))
        self.slot['native']={'harness':'codex','session_id':sid}
        return home,root,shard

    def codex_history(self, home):
        with patch.object(history,'codex_home',return_value=home): return history.load_history(self.workspace,self.slot)

    def test_codex_continuation_shard_extends_root_in_order_without_overlap(self):
        home,root,shard=self.shard_pair()
        result=self.codex_history(home)
        self.assertEqual([e['text'] for e in result['messages']],['old question','old answer','new question','new answer'])
        ids=[e['id'] for e in result['messages']]; self.assertEqual(len(set(ids)),4)
        self.assertFalse(result['partial']); self.assertFalse(result['capabilities']['send'])
        self.assertEqual(ids,[e['id'] for e in self.codex_history(home)['messages']])

    def test_codex_continuation_refuses_unverified_or_ambiguous_shards(self):
        for case,meta in (('cwd',{'cwd':str(self.base/'other')}),('thread',{'history_base':{'thread_id':'other','end_byte_offset':1}})):
            with self.subTest(case):
                home,root,shard=self.shard_pair(sid=case, **meta)
                self.assertFalse(self.codex_history(home)['capabilities']['history'])
        home,root,shard=self.shard_pair(sid='nobase', base=False)
        self.assertFalse(self.codex_history(home)['capabilities']['history'])
        home,root,shard=self.shard_pair(sid='two')
        second=shard.with_name(f'{root.stem}_other.jsonl'); second.write_bytes(shard.read_bytes())
        self.assertIn('ambiguous',self.codex_history(home)['reason'])
        second.unlink(); duplicate=root.with_name('rollout-another-two.jsonl'); duplicate.write_bytes(root.read_bytes())
        self.assertIn('ambiguous',self.codex_history(home)['reason']); duplicate.unlink()
        target=self.base/'outside.jsonl'; target.write_bytes(shard.read_bytes()); shard.unlink(); shard.symlink_to(target)
        self.assertIn('confined',self.codex_history(home)['reason'])

    def test_codex_continuation_ignores_other_threads_and_keeps_bounds(self):
        home,root,shard=self.shard_pair(sid='bounded')
        other=root.with_name('rollout-newer-bounded_x.jsonl')
        other.write_text(json.dumps({'type':'session_meta','payload':{'id':'bounded_x','cwd':str(self.workspace)}})+'\n'+json.dumps({'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'other thread'}]}})+'\n')
        self.assertNotIn('other thread',json.dumps(self.codex_history(home)))
        rows=[json.loads(l) for l in shard.read_text().splitlines()]
        rows+=[{'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':f'n{i}'}]}} for i in range(250)]
        shard.write_text(''.join(json.dumps(r)+'\n' for r in rows)+'{"unfinished":')
        result=self.codex_history(home)
        self.assertEqual(len(result['messages']),200); self.assertTrue(result['partial']); self.assertEqual(result['messages'][-1]['text'],'n249')
        self.assertNotIn('old question',json.dumps(result))


if __name__ == '__main__': unittest.main()
