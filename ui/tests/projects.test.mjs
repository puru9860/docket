import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile, readFile, readdir, symlink, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createProjects, validateProject, PROJECT_LIMIT } from '../projects.mjs';
import { createCompanion } from '../server.mjs';

const temp = async (t, withDocket = true) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-project-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  if (withDocket) await mkdir(path.join(dir, '.docket'));
  return dir;
};
const listing = async dir => JSON.stringify((await readdir(dir, { recursive: true })).sort());

test('validateProject accepts only an absolute real directory that holds .docket', async t => {
  const good = await temp(t), bare = await temp(t, false);
  assert.equal(await validateProject(good), await (await import('node:fs/promises')).realpath(good));
  await writeFile(path.join(bare, 'file'), 'x');
  for (const [input, pattern] of [[42, /absolute path/], ['', /absolute path/], ['   ', /absolute path/], ['relative/dir', /absolute/],
    [path.join(good, 'missing'), /does not exist/], [path.join(bare, 'file'), /not a directory|No \.docket/], [bare, /No \.docket/],
    ['x'.repeat(5000), /absolute path/], [`${good}\0`, /absolute path/]]) {
    await assert.rejects(validateProject(input), pattern, String(input).slice(0, 40));
  }
});

test('a project is exactly the named directory: parents and children are never discovered', async t => {
  const parent = await temp(t);
  const child = path.join(parent, 'child');
  await mkdir(path.join(child, 'inner'), { recursive: true });
  await assert.rejects(validateProject(child), /No \.docket/);
  await mkdir(path.join(parent, 'inner'));
  await assert.rejects(validateProject(path.join(parent, 'inner')), /No \.docket/);
});

test('the registry dedupes by real path, caps its size and refuses to drop the starting project', async t => {
  const start = await temp(t);
  const registry = await createProjects({ initial: start });
  const link = path.join(os.tmpdir(), `docket-ui-link-${process.pid}`);
  await symlink(start, link);
  t.after(() => rm(link, { force: true }));
  assert.equal((await registry.add(link)).id, registry.startup.id);
  assert.equal(registry.list().length, 1);
  await assert.rejects(registry.remove(registry.startup.id), /stays in the list/);
  await assert.rejects(registry.remove('nope'), /not in the list/);
  const made = [];
  for (let i = 1; i < PROJECT_LIMIT; i++) made.push(await registry.add(await temp(t)));
  assert.equal(registry.list().length, PROJECT_LIMIT);
  await assert.rejects(registry.add(await temp(t)), /limited to/);
  await registry.remove(made[0].id);
  assert.equal(registry.list().length, PROJECT_LIMIT - 1);
  assert.equal(registry.resolve(undefined).id, registry.startup.id);
  assert.equal(registry.resolve('missing'), null);
});

test('only paths persist, privately and atomically, and a vanished project is dropped on load', async t => {
  const start = await temp(t), other = await temp(t), gone = await temp(t);
  const prefs = path.join(await temp(t, false), 'state', 'projects.json');
  const first = await createProjects({ initial: start, preferencesPath: prefs });
  await first.add(other);
  await first.add(gone);
  const saved = JSON.parse(await readFile(prefs, 'utf8'));
  assert.equal(saved.version, 1);
  assert.equal(saved.projects.length, 3);
  assert.equal((await stat(prefs)).mode & 0o777, 0o600);
  assert.deepEqual((await readdir(path.dirname(prefs))).sort(), ['projects.json']);
  await rm(gone, { recursive: true, force: true });
  const second = await createProjects({ initial: start, preferencesPath: prefs });
  assert.deepEqual(second.list().map(p => p.name).sort(), [path.basename(start), path.basename(other)].sort());
  await writeFile(prefs, '{not json');
  assert.equal((await createProjects({ initial: start, preferencesPath: prefs })).list().length, 1);
  await writeFile(prefs, JSON.stringify({ projects: 'nope' }));
  assert.equal((await createProjects({ initial: start, preferencesPath: prefs })).list().length, 1);
});

test('adding a project writes nothing into it', async t => {
  const start = await temp(t), added = await temp(t);
  await mkdir(path.join(added, '.docket', 'runs'), { recursive: true });
  const before = await listing(added);
  const registry = await createProjects({ initial: start, preferencesPath: path.join(await temp(t, false), 'p.json') });
  await registry.add(added);
  await registry.remove((await registry.add(added)).id);
  assert.equal(await listing(added), before);
});

async function twoProjects(t) {
  const a = await temp(t);
  // B nests inside A, so one terminal and one run id are visible from both projects.
  const b = path.join(a, 'nested');
  await mkdir(path.join(b, '.docket'), { recursive: true });
  const c = path.join(a, 'twin');
  await mkdir(path.join(c, '.docket'), { recursive: true });
  const real = async p => (await import('node:fs/promises')).realpath(p);
  const [ra, rb, rc] = [await real(a), await real(b), await real(c)];
  const inspected = [];
  const slot = generation => ({ id: 'planner', generation, process: { pid: 7, start: '8' }, capabilities: { send: true, interrupt: true } });
  const states = { [ra]: { workspace: ra, runs: [{ id: 'R01', slots: [slot('1')] }] }, [rb]: { workspace: rb, runs: [{ id: 'R01', slots: [slot('2')] }] },
    [rc]: { workspace: rc, runs: [{ id: 'R01', slots: [slot('1')] }] } };
  const inventories = [];
  const prompts = [];
  const fake = {
    inventory: async workspace => { inventories.push(workspace); return { terminals:
      [{ terminal: 'ta', pane: 'w1:p1', name: 'ta', server: 's', agent: 'claude', status: 'idle' }], version: 't' }; },
    identity: async terminal => ({ terminal, pane: 'w1:p1', identity: 'same', attribution: 'process-only' }),
    terminalIdentities: async () => ({ ta: { pid: 7, start: '8' } }),
    agentRecord: async () => ({ agent_status: 'idle' }),
    agentPrompt: async (pane, text) => { prompts.push({ pane, text }); return { outcome: 'sent', detail: 'ok' }; },
    agentInterrupt: async () => ({ outcome: 'stopped', detail: '' })
  };
  const readers = [];
  const app = await createCompanion({ workspace: a, herdr: fake, ageOf: async () => 999,
    historyReader: async (workspace, run, slot, generation) => { readers.push({ workspace, run, slot }); return { messages: [], capabilities: { history: true }, reason: 'ok' }; },
    choiceReader: async workspace => { readers.push({ workspace, choices: true }); return { choices: [] }; },
    inspector: async workspace => { inspected.push(workspace); return structuredClone(states[workspace]); } });
  t.after(() => app.close());
  const auth = { Authorization: `Bearer ${app.token}` };
  const get = async route => (await fetch(`${app.url}${route}`, { headers: auth })).json();
  const post = async (route, body) => { const r = await fetch(`${app.url}${route}`, { method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return { status: r.status, body: await r.json() }; };
  return { a, b, c, ra, rb, rc, readers, app, get, post, prompts, inspected, inventories, states };
}

test('project routes list, add and remove without restarting, and require auth', async t => {
  const fix = await twoProjects(t);
  assert.equal((await fix.get('/api/projects')).projects.length, 1);
  const unauth = await fetch(`${fix.app.url}/api/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: fix.b }) });
  assert.equal(unauth.status, 401);
  const added = await fix.post('/api/projects', { path: fix.b });
  assert.equal(added.status, 200);
  assert.equal(added.body.projects.length, 2);
  assert.equal((await fix.post('/api/projects', { path: fix.b })).body.projects.length, 2);
  for (const bad of [{ path: '/definitely/not/here' }, { path: 'relative' }, {}, { path: 7 }]) {
    assert.equal((await fix.post('/api/projects', bad)).status, 400);
  }
  const removed = await fix.post('/api/projects/remove', { id: added.body.project.id });
  assert.equal(removed.body.projects.length, 1);
  assert.equal((await fix.post('/api/projects/remove', { id: 'nope' })).status, 400);
});

test('state, documents and history resolve the chosen project and refuse unknown ones', async t => {
  const fix = await twoProjects(t);
  const { project } = (await fix.post('/api/projects', { path: fix.b })).body;
  const first = await fix.get('/api/state');
  assert.equal(first.workspace, fix.ra);
  const second = await fix.get(`/api/state?project=${project.id}`);
  assert.equal(second.workspace, fix.rb);
  assert.equal(second.project, project.id);
  assert.equal(second.projects.length, 2);
  assert.ok(fix.inventories.includes(fix.rb), 'inventory is read for the chosen project');
  assert.equal((await fetch(`${fix.app.url}/api/state?project=nope`, { headers: { Authorization: `Bearer ${fix.app.token}` } })).status, 400);
});

test('a binding stays in its project after another project is selected, and projects never collide', async t => {
  const fix = await twoProjects(t);
  const { project } = (await fix.post('/api/projects', { path: fix.b })).body;
  const bindA = await fix.get('/api/binding?run=R01&slot=planner&terminal=ta');
  const bindB = await fix.get(`/api/binding?project=${project.id}&run=R01&slot=planner&terminal=ta`);
  assert.notEqual(bindA.project, bindB.project);
  assert.equal(bindA.terminal, bindB.terminal);
  await fix.get(`/api/state?project=${project.id}`);
  const sentA = await fix.post('/api/send', { binding: bindA.id, operation: 'op-1', text: 'to A' });
  const sentB = await fix.post('/api/send', { binding: bindB.id, operation: 'op-1', text: 'to B' });
  assert.equal(sentA.body.status, 'sent');
  assert.equal(sentB.body.status, 'sent');
  assert.equal(sentA.body.recipient.project, bindA.project);
  assert.deepEqual(fix.prompts, [{ pane: 'w1:p1', text: 'to A' }, { pane: 'w1:p1', text: 'to B' }]);
  assert.equal(sentB.body.duplicate, undefined);
});

test('removing a project invalidates its bindings and nothing in the other project', async t => {
  const fix = await twoProjects(t);
  const { project } = (await fix.post('/api/projects', { path: fix.b })).body;
  const bindA = await fix.get('/api/binding?run=R01&slot=planner&terminal=ta');
  const bindB = await fix.get(`/api/binding?project=${project.id}&run=R01&slot=planner&terminal=ta`);
  await fix.post('/api/projects/remove', { id: project.id });
  const dead = await fix.post('/api/send', { binding: bindB.id, operation: 'op-9', text: 'late' });
  assert.equal(dead.status, 400);
  assert.match(dead.body.error, /expired|removed/);
  assert.equal((await fix.post('/api/send', { binding: bindA.id, operation: 'op-9', text: 'still fine' })).body.status, 'sent');
  assert.deepEqual(fix.prompts, [{ pane: 'w1:p1', text: 'still fine' }]);
});

test('selecting or adding a project launches nothing and writes nothing into either project', async t => {
  const fix = await twoProjects(t);
  const before = [await listing(fix.a), await listing(fix.b)];
  const { project } = (await fix.post('/api/projects', { path: fix.b })).body;
  await fix.get(`/api/state?project=${project.id}`);
  await fix.get('/api/state');
  assert.deepEqual([await listing(fix.a), await listing(fix.b)], before);
  assert.deepEqual(fix.prompts, []);
});

test('two projects that share a run id, slot, generation and terminal never share an operation id', async t => {
  const fix = await twoProjects(t);
  const twin = (await fix.post('/api/projects', { path: fix.c })).body.project;
  const bindA = await fix.get('/api/binding?run=R01&slot=planner&terminal=ta');
  const bindC = await fix.get(`/api/binding?project=${twin.id}&run=R01&slot=planner&terminal=ta`);
  const first = await fix.post('/api/send', { binding: bindA.id, operation: 'same-op', text: 'from A' });
  const second = await fix.post('/api/send', { binding: bindC.id, operation: 'same-op', text: 'from C' });
  assert.equal(first.body.duplicate, undefined);
  assert.equal(second.body.duplicate, undefined);
  assert.deepEqual(fix.prompts.map(p => p.text), ['from A', 'from C']);
});

test('documents, history and history choices are read from the selected project only', async t => {
  const fix = await twoProjects(t);
  const { project } = (await fix.post('/api/projects', { path: fix.b })).body;
  await fix.get(`/api/document?project=${project.id}&id=runs/R01/plan.mdx`);
  assert.equal(fix.inspected.at(-1), fix.rb);
  await fix.get('/api/document?id=runs/R01/plan.mdx');
  assert.equal(fix.inspected.at(-1), fix.ra);
  await fix.get(`/api/history?project=${project.id}&run=R01&slot=planner&generation=2`);
  await fix.get(`/api/history-choices?project=${project.id}&run=R01&slot=planner&generation=2`);
  await fix.get('/api/history?run=R01&slot=planner&generation=1');
  assert.deepEqual(fix.readers.map(r => [r.workspace, !!r.choices]), [[fix.rb, false], [fix.rb, true], [fix.ra, false]]);
  const wrong = await fetch(`${fix.app.url}/api/history?project=${project.id}&run=R01&slot=planner&generation=1`, { headers: { Authorization: `Bearer ${fix.app.token}` } });
  assert.equal(wrong.status, 400);
});
