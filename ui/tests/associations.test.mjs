import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createCompanion, connectionOf } from '../server.mjs';
import { proveAssociation, eligibility, applyAssociations, createAssociations, recordFor } from '../associations.mjs';

const saved = { native: { harness: 'codex', session_id: 'thread-1' }, process: { harness: 'codex', pid: 42, start: '7' } };
const terminal = (extra = {}) => ({ terminal: 't1', pane: 'w1:p1', agent: 'codex', native: null, status: 'idle', server: 's1',
  process: { pid: 42, start: '7' }, ...extra });

test('proof needs the exact native id and its recorded pid:start to name one live terminal of that harness', () => {
  assert.equal(proveAssociation(saved, [terminal()]).ok, true);
  for (const [why, candidate, terminals, reason] of [
    ['no process recorded', { native: saved.native }, [terminal()], /no recorded process/],
    ['process not running', saved, [terminal({ process: { pid: 43, start: '7' } })], /not running/],
    ['reused pid with another start', saved, [terminal({ process: { pid: 42, start: '8' } })], /not running/],
    ['two terminals', saved, [terminal(), terminal({ terminal: 't2' })], /More than one/],
    ['no agent', saved, [terminal({ agent: '' })], /No live agent/],
    ['another harness', saved, [terminal({ agent: 'claude' })], /runs claude, not codex/],
    ['contradicting native', saved, [terminal({ native: 'thread-2' })], /different native/],
    ['mixed harness record', { native: saved.native, process: { ...saved.process, harness: 'claude' } }, [terminal()], /different harnesses/]
  ]) assert.match(proveAssociation(candidate, terminals).reason || '', reason, why);
  assert.equal(proveAssociation(saved, [terminal({ native: 'thread-1' })]).ok, true);
});

test('only a current role without its own session, or with a contradiction, may be linked', () => {
  assert.equal(eligibility({ historical: false }), '');
  assert.equal(eligibility({ historical: false, native: { session_id: 'a' }, mapping_issue: 'ambiguous' }), '');
  assert.match(eligibility({ historical: false, native: { session_id: 'a' } }), /already has its own/);
  assert.match(eligibility({ historical: true }), /read-only/);
  assert.match(eligibility(null), /Select a current/);
});

async function fixture(t, { slotExtra = {}, terminals, identities, associationsPath, historyReader, choices, workspace } = {}) {
  if (!workspace) {
    workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-assoc-'));
    t.after(() => rm(workspace, { recursive: true, force: true }));
  }
  const state = { runs: [{ id: 'R01', slots: [
    { id: 'planner:plan-reg', role: 'planner', session: 'plan-reg', registration_generation: 1, generation: 'g-plan',
      historical: false, group: 'current', capabilities: { send: false, interrupt: false, history: false, terminal: true },
      identity: { kind: 'none' }, ...slotExtra },
    { id: 'history:implementor:codex:thread-1', role: 'implementor', historical: true, group: 'unattributed', generation: 'g-old',
      native: saved.native, process: saved.process, capabilities: { send: false, history: true, terminal: false } },
    { id: 'reviewer', role: 'reviewer', historical: false, generation: 'g-rev', process: { pid: 5, start: '5' },
      capabilities: { send: true, interrupt: true, terminal: true } }
  ] }] };
  const inventory = terminals || [terminal(), terminal({ terminal: 't2', pane: 'w1:p2', agent: 'claude', process: undefined })];
  const ids = identities || { t1: { pid: 42, start: '7' }, t2: { pid: 5, start: '5' } };
  const prompts = [], historyCalls = [];
  const fake = {
    inventory: async () => ({ terminals: inventory.map(({ process: _p, ...rest }) => rest), version: 'test' }),
    terminalIdentities: async () => ids,
    identity: async id => ({ terminal: id, pane: 'w1:p1', identity: 'same', attribution: 'process-only' }),
    agentRecord: async () => ({ agent_status: 'idle' }),
    agentPrompt: async (pane, text) => { prompts.push({ pane, text }); return { outcome: 'sent', detail: 'ok' }; }
  };
  const app = await createCompanion({ workspace, herdr: fake, inspector: async () => structuredClone(state), ageOf: async () => 999,
    associationsPath,
    choiceReader: async () => ({ choices: choices || [{ id: 'codex:thread-1', label: 'Implementor · codex · unattributed',
      native: saved.native, process: saved.process, recorded_roles: ['implementor'] }] }),
    historyReader: historyReader || (async (...args) => { historyCalls.push(args); return { messages: [], capabilities: { history: true } }; }) });
  t.after(() => app.close());
  const auth = { Authorization: `Bearer ${app.token}` };
  const get = async route => (await fetch(`${app.url}${route}`, { headers: auth })).json();
  const post = async (route, body) => {
    const response = await fetch(`${app.url}${route}`, { method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  const planner = async () => (await get('/api/state')).runs[0].slots.find(s => s.id === 'planner:plan-reg');
  const link = async (extra = {}) => post('/api/associate', { run: 'R01', slot: 'planner:plan-reg', generation: (await planner()).generation,
    choice: 'codex:thread-1', confirm: true, ...extra });
  return { app, state, get, post, planner, link, prompts, historyCalls, workspace };
}

test('without a link the role shows its exact reason, Link as the one action, and offers no chat or Stop', async t => {
  const fix = await fixture(t);
  const body = await fix.get('/api/state');
  const planner = body.runs[0].slots[0];
  assert.deepEqual([planner.connection.state, planner.connection.action, planner.send_ready], ['needs-link', 'link', false]);
  assert.match(planner.send_note, /Link a saved session to enable chat/);
  assert.match(planner.connection.reason, /no recorded session/);
  assert.match(planner.connection.reason, /which terminal it is/);
  assert.equal(planner.connection.short, 'Chat is off: no linked session.');
  assert.equal(body.stop_supported, false);
  assert.equal(body.runs[0].slots[2].connection.state, 'live', 'a role with its own proven session stays Live');
});

test('a confirmed link of a saved session proves one live terminal, enables send, and keeps provenance', async t => {
  const fix = await fixture(t);
  const before = await fix.planner();
  assert.equal((await fix.link({ confirm: false })).status, 400);
  assert.equal((await fix.planner()).association, undefined, 'an unconfirmed link saves nothing');
  const bindingBefore = await (await fetch(`${fix.app.url}/api/binding?run=R01&slot=planner%3Aplan-reg&terminal=t1`,
    { headers: { Authorization: `Bearer ${fix.app.token}` } })).json();
  const linked = await fix.link();
  assert.equal(linked.status, 200, JSON.stringify(linked.body));
  const after = await fix.planner();
  assert.deepEqual([after.association.state, after.association.by, after.connection.state, after.send_ready],
    ['linked', 'you', 'live', true]);
  assert.deepEqual(after.association.recorded_roles, ['implementor']);
  assert.notEqual(after.generation, before.generation);
  assert.equal(after.base_generation, before.generation);
  const history = (await fix.get('/api/state')).runs[0].slots.find(s => s.id.startsWith('history:'));
  assert.equal(history.merged_into, 'planner:plan-reg');
  assert.equal(history.role, 'implementor', 'recorded provenance is not rewritten');
  // A binding made before the link names a different generation and can no longer send.
  const stale = await fix.post('/api/send', { binding: bindingBefore.id, operation: 'op-0', text: 'hi' });
  assert.equal(stale.status, 400);
  assert.match(stale.body.error, /generation changed/);
  const binding = await (await fetch(`${fix.app.url}/api/binding?run=R01&slot=planner%3Aplan-reg&terminal=t1`,
    { headers: { Authorization: `Bearer ${fix.app.token}` } })).json();
  const sent = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'ship it' });
  assert.equal(sent.body.status, 'sent');
  assert.deepEqual(fix.prompts, [{ pane: 'w1:p1', text: 'ship it' }]);
});

test('linking is refused with the exact reason and saves nothing when proof is missing or contradicted', async t => {
  const noProcess = { id: 'codex:thread-1', label: 'x', native: saved.native, recorded_roles: [] };
  const cases = [
    ['a choice with no single recorded process', { choices: [noProcess] }, /no single recorded process/],
    ['a process that is not running', { identities: { t1: { pid: 1, start: '1' }, t2: { pid: 5, start: '5' } } }, /not running/],
    ['another harness in the terminal', { terminals: [terminal({ agent: 'claude' })] }, /runs claude, not codex/],
    ['a different native session in the terminal', { terminals: [terminal({ native: 'other' })] }, /different native/],
    ['a terminal already another role\'s session', { identities: { t1: { pid: 42, start: '7' }, t2: { pid: 5, start: '5' } },
      terminals: [terminal(), terminal({ terminal: 't2', process: undefined })], slotExtra: {} }, null]
  ];
  for (const [why, options, reason] of cases) {
    if (!reason) continue;
    const fix = await fixture(t, options);
    const result = await fix.link();
    assert.equal(result.status, 400, why);
    assert.match(result.body.error, reason, why);
    assert.equal((await fix.planner()).association, undefined, why);
    assert.equal((await fix.planner()).send_ready, false, why);
  }
  const rival = await fixture(t, { identities: { t1: { pid: 42, start: '7' }, t2: { pid: 5, start: '5' } } });
  rival.state.runs[0].slots[2].process = { pid: 42, start: '7' };
  const refused = await rival.link();
  assert.equal(refused.status, 400);
  assert.match(refused.body.error, /already reviewer's recorded session/);
  const outside = await fixture(t);
  const drift = await outside.link({ choice: 'codex:other-run' });
  assert.equal(drift.status, 400);
  assert.match(drift.body.error, /not in this run/);
  const owned = await fixture(t, { slotExtra: { native: { harness: 'claude', session_id: 'own' }, identity: { kind: 'native' },
    capabilities: { send: true, interrupt: true, terminal: true } } });
  assert.match((await owned.link()).body.error, /already has its own/);
  const earlier = await outside.post('/api/associate', { run: 'R01', slot: 'history:implementor:codex:thread-1',
    generation: 'g-old', choice: 'codex:thread-1', confirm: true });
  assert.equal(earlier.status, 400);
});

test('a link is a preference: it is re-proved on every read and grants nothing once identity or registration moves', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-assoc-pref-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const associationsPath = path.join(directory, 'associations.json');
  const fix = await fixture(t, { associationsPath });
  assert.equal((await fix.link()).status, 200);
  const stored = JSON.parse(await readFile(associationsPath, 'utf8'));
  assert.equal(stored.associations.length, 1);
  assert.equal(JSON.stringify(stored).includes('token'), false);
  // The process moving away leaves the saved choice in place but unproven.
  fix.state.runs[0].slots[0].capabilities.send = false;
  const ids = { t1: { pid: 99, start: '7' }, t2: { pid: 5, start: '5' } };
  const moved = await fixture(t, { associationsPath, identities: ids, workspace: fix.workspace });
  const unproven = await moved.planner();
  assert.deepEqual([unproven.association.state, unproven.send_ready, unproven.capabilities.send, unproven.connection.state],
    ['unproven', false, false, 'needs-link']);
  assert.match(unproven.association.reason, /not running/);
  // A reload with the live process back proves it again; a moved registration invalidates it.
  const back = await fixture(t, { associationsPath, workspace: fix.workspace });
  assert.equal((await back.planner()).association.state, 'linked');
  const replaced = await fixture(t, { associationsPath, workspace: fix.workspace, slotExtra: { registration_generation: 2 } });
  const invalid = await replaced.planner();
  assert.deepEqual([invalid.association.state, invalid.send_ready], ['invalidated', false]);
  assert.match(invalid.association.reason, /Link it again/);
  const cleared = await back.post('/api/associate', { run: 'R01', slot: 'planner:plan-reg', generation: (await back.planner()).generation, clear: true });
  assert.equal(cleared.body.cleared, true);
  assert.equal((await back.planner()).connection.state, 'needs-link');
});

test('a linked role reads its saved session by the original generation, and a link never reaches history through another run', async t => {
  const fix = await fixture(t);
  await fix.link();
  const planner = await fix.planner();
  const response = await fix.get(`/api/history?run=R01&slot=${encodeURIComponent(planner.id)}&generation=${planner.generation}`);
  assert.equal(response.capabilities.history, true);
  const [, run, slot, generation, choice, scope] = fix.historyCalls[0];
  assert.deepEqual([run, slot, generation, choice, scope], ['R01', 'planner:plan-reg', planner.base_generation, 'codex:thread-1', 'run']);
  await fix.get(`/api/history?run=R01&slot=${encodeURIComponent(planner.id)}&generation=${planner.generation}&choice=codex:x&scope=project`);
  assert.deepEqual(fix.historyCalls[1].slice(4), ['codex:x', 'project']);
});

test('applyAssociations leaves historical slots and other roles untouched', async () => {
  const slot = (id, extra = {}) => ({ id, role: 'planner', generation: `g-${id}`, ...extra });
  const state = { runs: [{ id: 'R01', slots: [slot('a', { historical: true }), slot('b', { session: 's', registration_generation: 1 })] }] };
  const store = await createAssociations();
  await store.set(recordFor({ project: 'p', run: state.runs[0], slot: { ...slot('a'), historical: false, session: 's', registration_generation: 1 },
    choice: { id: 'codex:thread-1', label: 'L', ...saved } }));
  applyAssociations(state, 'p', store, [terminal()]);
  assert.equal(state.runs[0].slots[0].association, undefined, 'the stored slot is historical now and inert');
  assert.equal(state.runs[0].slots[1].association, undefined, 'another slot id is never linked');
  assert.equal(state.runs[0].slots[1].base_generation, 'g-b');
});

test('connection states cover every precise reason with one action', () => {
  const slot = { historical: false, capabilities: { send: true } };
  const live = terminal({ status: 'idle' });
  const cases = [
    [{ historical: true }, {}, 'saved', null],
    [{ ...slot, mapping_issue: 'x' }, {}, 'ambiguous', 'link'],
    [{ ...slot, capabilities: { send: false } }, {}, 'needs-link', 'link'],
    [slot, { offline: true }, 'not-found', 'retry'],
    [slot, { ambiguous: true }, 'ambiguous', 'choose-terminal'],
    [slot, {}, 'not-found', 'refresh'],
    [slot, { terminal: terminal({ agent: '' }) }, 'not-found', 'open-terminal'],
    [slot, { terminal: live, young: 3 }, 'starting', 'refresh'],
    [slot, { terminal: terminal({ status: 'working' }) }, 'working', null],
    [slot, { terminal: terminal({ status: 'blocked' }) }, 'needs-you', 'open-terminal'],
    [slot, { terminal: terminal({ status: 'mystery' }) }, 'not-found', 'open-terminal'],
    [slot, { terminal: live }, 'live', null]
  ];
  for (const [input, context, state, action] of cases) {
    const result = connectionOf(input, context);
    assert.deepEqual([result.state, result.action], [state, action], JSON.stringify([input, context]));
    assert.ok(state === 'live' || result.reason, 'every non-live state explains itself');
  }
});

// A Codex role records its native thread but no terminal process: tools run under a shared app-server.
const codexRole = { native: { harness: 'codex', session_id: 'plan-thread' }, process: {}, identity: { kind: 'native' },
  capabilities: { send: true, interrupt: true, history: true, terminal: true } };
const linkTerminal = async (fix, extra = {}) => fix.post('/api/associate', { run: 'R01', slot: 'planner:plan-reg',
  generation: (await fix.planner()).generation, terminal: 't1', confirm: true, ...extra });

test('a role with its own session but no provable terminal offers Link terminal, and a confirmed link enables send', async t => {
  const fix = await fixture(t, { slotExtra: codexRole });
  const before = await fix.planner();
  assert.deepEqual([before.connection.state, before.connection.action, before.send_ready], ['not-found', 'link-terminal', false]);
  assert.equal(before.connection.short, 'Chat is off: link this role\'s terminal.');
  assert.equal((await linkTerminal(fix, { confirm: false })).status, 400);
  assert.equal((await fix.planner()).association, undefined, 'an unconfirmed link saves nothing');
  const linked = await linkTerminal(fix);
  assert.equal(linked.status, 200, JSON.stringify(linked.body));
  const after = await fix.planner();
  assert.deepEqual([after.association.state, after.association.kind, after.connection.state, after.send_ready],
    ['linked', 'terminal', 'live', true]);
  assert.deepEqual(after.native, codexRole.native, 'the role keeps its own native session');
  assert.notEqual(after.generation, before.generation);
  const binding = await (await fetch(`${fix.app.url}/api/binding?run=R01&slot=planner%3Aplan-reg&terminal=t1`,
    { headers: { Authorization: `Bearer ${fix.app.token}` } })).json();
  const sent = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'continue' });
  assert.equal(sent.body.status, 'sent');
  assert.deepEqual(fix.prompts, [{ pane: 'w1:p1', text: 'continue' }]);
});

test('a terminal link is refused with the exact reason and saves nothing', async t => {
  for (const [why, options, extra, reason] of [
    ['a role with no session of its own', {}, {}, /no recorded session of its own/],
    ['another harness in the terminal', { slotExtra: codexRole, terminals: [terminal({ agent: 'claude' })] }, {}, /runs claude, not codex/],
    ['a terminal outside the project', { slotExtra: codexRole }, { terminal: 'gone' }, /not in this project/],
    ['a contradicting native session', { slotExtra: codexRole, terminals: [terminal({ native: 'other-thread' })] }, {}, /different native/]
  ]) {
    const fix = await fixture(t, options);
    const result = await linkTerminal(fix, extra);
    assert.equal(result.status, 400, why);
    assert.match(result.body.error, reason, why);
    assert.equal((await fix.planner()).association, undefined, why);
  }
  const rival = await fixture(t, { slotExtra: codexRole });
  rival.state.runs[0].slots[2].process = { pid: 42, start: '7' };
  assert.match((await linkTerminal(rival)).body.error, /already reviewer's/);
  const proven = await fixture(t, { slotExtra: { ...codexRole, process: { harness: 'codex', pid: 42, start: '7' } } });
  assert.match((await linkTerminal(proven)).body.error, /already proven/);
});

test('a terminal link grants nothing once another program runs in that terminal', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-assoc-term-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const associationsPath = path.join(directory, 'associations.json');
  const fix = await fixture(t, { associationsPath, slotExtra: codexRole });
  assert.equal((await linkTerminal(fix)).status, 200);
  const moved = await fixture(t, { associationsPath, workspace: fix.workspace, slotExtra: codexRole,
    identities: { t1: { pid: 42, start: '8' }, t2: { pid: 5, start: '5' } } });
  const unproven = await moved.planner();
  assert.deepEqual([unproven.association.state, unproven.send_ready, unproven.connection.action], ['unproven', false, 'link-terminal']);
  assert.match(unproven.connection.reason, /different program/);
  const back = await fixture(t, { associationsPath, workspace: fix.workspace, slotExtra: codexRole });
  assert.equal((await back.planner()).send_ready, true);
});

// A worker Docket launched as `herdr agent start <name>`: no native session or process, only its recorded name.
const namedWorker = { herdr_name: 'r01-impl', identity: { kind: 'none' },
  capabilities: { send: true, interrupt: true, history: false, terminal: true } };

test('a worker matches the live Herdr agent carrying its recorded name, and sends there', async t => {
  const fix = await fixture(t, { slotExtra: namedWorker, terminals: [terminal({ agent_name: 'r01-impl' })] });
  const worker = await fix.planner();
  assert.deepEqual([worker.connection.state, worker.send_ready, worker.terminal_match.attribution], ['live', true, 'herdr-name']);
  const binding = await (await fetch(`${fix.app.url}/api/binding?run=R01&slot=planner%3Aplan-reg&terminal=t1`,
    { headers: { Authorization: `Bearer ${fix.app.token}` } })).json();
  assert.equal((await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'go' })).body.status, 'sent');
  assert.deepEqual(fix.prompts, [{ pane: 'w1:p1', text: 'go' }]);
});

test('a name proves nothing when it is gone, runs another harness, or disagrees with another proof', async t => {
  const gone = await (await fixture(t, { slotExtra: namedWorker, terminals: [terminal({ agent_name: 'other' })] })).planner();
  assert.deepEqual([gone.connection.state, gone.send_ready], ['not-found', false]);
  assert.match(gone.connection.reason, /No live Herdr agent named r01-impl/);
  const harness = await (await fixture(t, { slotExtra: { ...namedWorker, native: { harness: 'claude', session_id: 's' } },
    terminals: [terminal({ agent_name: 'r01-impl' })] })).planner();
  assert.equal(harness.send_ready, false, 'a codex agent never answers for a claude role');
  assert.match(harness.connection.reason, /running claude/);
  const split = await (await fixture(t, { slotExtra: { ...namedWorker, process: { pid: 5, start: '5' } },
    terminals: [terminal({ agent_name: 'r01-impl' }), terminal({ terminal: 't2', pane: 'w1:p2', process: undefined })] })).planner();
  assert.deepEqual([split.connection.state, split.send_ready], ['ambiguous', false], 'name and process naming two panes is ambiguous');
});
