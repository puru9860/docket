import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, readFile, chmod } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import http from 'node:http';
import { WebSocket } from 'ws';
import { fakeOpenCode, permissionRequest, questionRequest } from './fake-opencode.mjs';
import { createCompanion, dimensions, resolveTerminal } from '../server.mjs';
import { Herdr } from '../herdr.mjs';

async function fixture(t, historyReader, choiceReader) {
  const workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-test-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const state = { runs: [{ id: 'R01', slots: [{ id: 'planner', generation: '1' },
    { id: 'implementor', generation: '1' }] }] };
  const opened = [], inputs = [];
  let identity = 'original', viewport;
  const fake = {
    inventory: async () => ({ terminals: [{ terminal: 'term_one', name: 'Shell' }, { terminal: 'term_two', name: 'Worker shell' }], version: 'test' }),
    identity: async terminal => {
      if (!['term_one', 'term_two'].includes(terminal)) throw new Error('Unknown terminal');
      return { terminal, pane: 'w1:p1', identity, viewport, attribution: 'process-only' };
    },
    open(binding, mode, takeover, size, record) {
      const session = { mode, takeover, size, closed: false, send: command => inputs.push(command),
        close() { this.closed = true; } };
      opened.push(session);
      setImmediate(() => record({ type: 'terminal.frame', full: true, seq: 1,
        width: size.cols, height: size.rows, bytes: Buffer.from('shell').toString('base64') }));
      return session;
    }
  };
  const app = await createCompanion({ workspace, herdr: fake, inspector: async () => state, historyReader, choiceReader });
  t.after(() => app.close());
  const get = (route, auth = true, options = {}) => fetch(`${app.url}${route}`, {
    headers: auth ? { Authorization: `Bearer ${app.token}` } : {}, ...options });
  const bind = async (slot = 'planner', terminal = slot === 'implementor' ? 'term_two' : 'term_one') => {
    const response = await get(`/api/binding?run=R01&slot=${slot}&terminal=${terminal}`);
    const result = await response.json(); assert.equal(response.status, 200, result.error);
    return result;
  };
  const connect = async (origin = app.url, token = app.token) => {
    const ws = new WebSocket(app.url.replace('http:', 'ws:') + '/terminal', ['docket', token], { origin });
    const messages = []; ws.on('message', bytes => messages.push(JSON.parse(bytes)));
    await once(ws, 'open');
    return { ws, messages };
  };
  return { ...app, get, bind, connect, state, opened, inputs, replace: () => { identity = 'replacement'; },
    setViewport: value => { viewport = value; } };
}
async function until(predicate) {
  const end = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > end) throw new Error('Condition timed out');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
const attach = (ws, binding, selection = 'one', mode = 'control') => ws.send(JSON.stringify({
  type: 'attach', binding: binding.id, selection, mode, cols: 80, rows: 24
}));

test('HTTP data is authenticated, local, and static files are allowlisted', async t => {
  const app = await fixture(t);
  assert.equal((await app.get('/api/state', false)).status, 401);
  assert.equal((await app.get('/api/state')).status, 200);
  const wrongHost = await new Promise(resolve => http.get(`${app.url}/api/state`, {
    headers: { host: 'evil.example', Authorization: `Bearer ${app.token}` }
  }, response => { response.resume(); resolve(response.statusCode); }));
  assert.equal(wrongHost, 403);
  assert.equal((await app.get('/api/state', true, { headers: { origin: 'https://evil.example', Authorization: `Bearer ${app.token}` } })).status, 403);
  assert.equal((await app.get('/server.mjs')).status, 404);
  assert.equal((await app.get('/api/state', true, { method: 'POST' })).status, 405);
});

test('WebSocket rejects a foreign origin and an incorrect token', async t => {
  const app = await fixture(t);
  for (const [origin, token] of [['https://evil.example', app.token], [app.url, 'wrong']]) {
    await assert.rejects(app.connect(origin, token), /403/);
  }
});

test('observer refuses input and disconnect releases its stream', async t => {
  const app = await fixture(t), binding = await app.bind();
  const { ws, messages } = await app.connect(); attach(ws, binding, 'one', 'observe');
  await until(() => messages.some(m => m.type === 'bridge.ready'));
  ws.send(JSON.stringify({ type: 'input', selection: 'one', text: 'must not execute\r' }));
  await until(() => messages.some(m => m.type === 'bridge.closed'));
  assert.deepEqual(app.inputs, []);
  assert.ok(app.opened[0].closed);
  ws.close();
});

test('selected controller forwards Unicode, resize, scroll and explicit takeover only', async t => {
  const app = await fixture(t), binding = await app.bind();
  const { ws, messages } = await app.connect();
  ws.send(JSON.stringify({ type: 'attach', binding: binding.id, selection: 'one', mode: 'control', takeover: true, cols: 80, rows: 24 }));
  await until(() => messages.some(m => m.type === 'bridge.ready'));
  for (const command of [{ type: 'input', text: 'नमस्ते café\r' },
    { type: 'resize', cols: 100, rows: 30 }, { type: 'scroll', direction: 'up', lines: 15 }]) {
    ws.send(JSON.stringify({ ...command, selection: 'one' }));
  }
  await until(() => app.inputs.length === 3);
  assert.equal(app.opened[0].takeover, true);
  assert.equal(app.inputs[0].text, 'नमस्ते café\r');
  assert.deepEqual(app.inputs[1], { type: 'terminal.resize', cols: 100, rows: 30 });
  assert.equal(app.inputs[2].direction, 'up');
  ws.close(); await until(() => app.opened[0].closed);
});

test('delayed old-selection input cannot reach a new role', async t => {
  const app = await fixture(t), planner = await app.bind(), worker = await app.bind('implementor');
  const { ws, messages } = await app.connect(); attach(ws, planner);
  await until(() => messages.some(m => m.type === 'bridge.ready'));
  attach(ws, worker, 'two');
  ws.send(JSON.stringify({ type: 'input', selection: 'one', text: 'old role\r' }));
  await until(() => messages.some(m => /inactive selection/.test(m.reason || '')));
  assert.deepEqual(app.inputs, []);
  assert.ok(app.opened[0].closed); ws.close();
});

test('native occupant replacement, ended sessions and Docket generation changes refuse input', async t => {
  for (const replacement of ['occupant', 'generation', 'ended']) {
    const app = await fixture(t), binding = await app.bind();
    const { ws, messages } = await app.connect(); attach(ws, binding);
    await until(() => messages.some(m => m.type === 'bridge.ready'));
    if (replacement === 'occupant') app.replace();
    else if (replacement === 'ended') app.state.runs[0].slots[0].historical = true;
    else app.state.runs[0].slots[0].generation = '2';
    ws.send(JSON.stringify({ type: 'input', selection: 'one', text: 'wrong recipient\r' }));
    await until(() => messages.some(m => m.type === 'bridge.invalidated'));
    assert.deepEqual(app.inputs, []); ws.close();
  }
});

test('bridge stops on frame gaps and on abrupt EOF without terminal.closed', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-stream-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  for (const sequence of [[1, 3], [1]]) {
    const executable = path.join(directory, 'herdr');
    await writeFile(executable, `#!/usr/bin/env node\n${sequence.map(seq =>
      `console.log(JSON.stringify({type:'terminal.frame',seq:${seq},full:true}));`).join('\n')}\n`);
    await chmod(executable, 0o700);
    const adapter = new Herdr('/unused', directory, executable), records = [];
    const reason = await new Promise(resolve => adapter.open({ terminal: 'term' }, 'observe', false,
      { cols: 80, rows: 24 }, record => records.push(record), resolve));
    assert.equal(records.length, 1);
    assert.match(reason, sequence.length === 2 ? /sequence lost/ : /stream ended/);
  }
});

test('dimensions reject oversized and malformed payloads', () => {
  assert.throws(() => dimensions({ cols: 10000, rows: 20 }));
  assert.throws(() => dimensions({ cols: '80', rows: 20 }));
  assert.deepEqual(dimensions({ cols: 80, rows: 24 }), { cols: 80, rows: 24 });
});

test('history requires auth, exact current slot and generation, and rechecks after reading', async t => {
  let reads = 0, moveGeneration;
  const app = await fixture(t, async (_workspace, run, slot, generation) => {
    reads++;
    assert.equal(run, 'R01'); assert.equal(slot, 'planner'); assert.equal(generation, '1');
    moveGeneration?.();
    return { messages: [{ role: 'assistant', text: 'exact history' }], capabilities: { history: true, send: false } };
  });
  const route = '/api/history?run=R01&slot=planner&generation=1';
  assert.equal((await app.get(route, false)).status, 401);
  assert.equal((await app.get(route.replace('planner', 'stranger'))).status, 400);
  assert.equal((await app.get(route.replace('generation=1', 'generation=2'))).status, 400);
  assert.equal(reads, 0);
  const response = await app.get(route);
  assert.equal(response.status, 200); assert.equal((await response.json()).messages[0].text, 'exact history');
  moveGeneration = () => { app.state.runs[0].slots[0].generation = '2'; };
  assert.equal((await app.get(route)).status, 400);
});

test('earlier sessions cannot acquire terminal bindings and another role cannot inherit a bound terminal', async t => {
  const app = await fixture(t);
  await app.bind();
  assert.equal((await app.get('/api/binding?run=R01&slot=implementor&terminal=term_one')).status, 400);
  await app.bind('implementor');
  app.state.runs[0].slots[1].historical = true;
  assert.equal((await app.get('/api/binding?run=R01&slot=implementor&terminal=term_two')).status, 400);
});

test('observer projects all rows of the native pane; control uses browser dimensions', async t => {
  const app = await fixture(t);
  app.setViewport({ cols: 75, rows: 37 });
  const binding = await app.bind(), { ws, messages } = await app.connect();
  attach(ws, binding, 'native', 'observe');
  await until(() => messages.some(m => m.type === 'bridge.ready'));
  assert.deepEqual(app.opened[0].size, { cols: 75, rows: 37 });
  attach(ws, binding, 'browser', 'control');
  await until(() => messages.some(m => m.type === 'bridge.ready' && m.selection === 'browser'));
  assert.deepEqual(app.opened[1].size, { cols: 80, rows: 24 });
  assert.deepEqual(app.inputs, []); ws.close();
});

test('explicit history choices are authenticated and pinned to the selected role generation', async t => {
  let seenChoice;
  const app = await fixture(t, async (_workspace, _run, _slot, _generation, choice) => {
    seenChoice = choice; return { selection: choice, messages: [] };
  }, async () => ({ choices: [{ id: 'opencode:exact' }] }));
  const query = 'run=R01&slot=planner&generation=1';
  assert.equal((await app.get(`/api/history-choices?${query}`, false)).status, 401);
  assert.equal((await app.get(`/api/history-choices?${query.replace('generation=1', 'generation=2')}`)).status, 400);
  assert.equal((await (await app.get(`/api/history-choices?${query}`)).json()).choices[0].id, 'opencode:exact');
  assert.equal((await app.get(`/api/history?${query}&choice=opencode%3Aexact`)).status, 200);
  assert.equal(seenChoice, 'opencode:exact');
});

test('resolveTerminal matches one proven native session or process identity, never by title', () => {
  const terminals = [
    { terminal: 't1', native: 'sess-a', process: null, name: 'Matching title is irrelevant' },
    { terminal: 't2', native: null, process: { pid: 10, start: '5' }, name: 'Shell' },
    { terminal: 't3', native: 'sess-a', process: null, name: 'Duplicate session' }
  ];
  assert.deepEqual(resolveTerminal({ native: { harness: 'opencode', session_id: 'sess-a' } }, terminals),
    { terminal: null, ambiguous: true, candidates: ['t1', 't3'] });
  assert.equal(resolveTerminal({ native: { harness: 'opencode', session_id: 'sess-b' } }, terminals).terminal, null);
  assert.equal(resolveTerminal({ process: { pid: 10, start: '5' } }, terminals).terminal, 't2');
  assert.equal(resolveTerminal({ process: { pid: 10, start: '5' } }, terminals).attribution, 'process-only');
  assert.equal(resolveTerminal({ process: { pid: 10, start: '' } }, terminals).terminal, null);
  assert.equal(resolveTerminal({ process: { pid: 99, start: '5' } }, terminals).terminal, null);
  assert.equal(resolveTerminal({}, terminals).terminal, null);
});

test('state resolves a proven native session to its exact terminal', async t => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-match-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const state = { runs: [{ id: 'R01', slots: [
    { id: 'planner', generation: '1', native: { harness: 'opencode', session_id: 'sess-a' } },
    { id: 'implementor', generation: '1', native: { harness: 'opencode', session_id: 'sess-b' } }
  ] }] };
  const fake = { inventory: async () => ({ terminals: [
    { terminal: 't1', native: 'sess-a', name: 'Planner pane' },
    { terminal: 't2', native: 'sess-b', name: 'Worker pane' }
  ], version: 'test' }) };
  const app = await createCompanion({ workspace, herdr: fake, inspector: async () => state });
  t.after(() => app.close());
  const body = await (await fetch(`${app.url}/api/state`, { headers: { Authorization: `Bearer ${app.token}` } })).json();
  assert.equal(body.runs[0].slots[0].terminal_match.terminal, 't1');
  assert.equal(body.runs[0].slots[0].terminal_match.attribution, 'native-session');
  assert.equal(body.runs[0].slots[1].terminal_match.terminal, 't2');
});

test('state resolves a both-recorded identity by process when Herdr reports no agent session, and never matches historical slots', async t => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-bothmatch-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const identity = { native: { harness: 'claude', session_id: 'sess-live' },
    process: { harness: 'claude', pid: 4242, start: '98765' } };
  const state = { runs: [{ id: 'R01', slots: [
    { id: 'planner', generation: '1', historical: false, ...identity },
    { id: 'earlier', generation: '9', historical: true, ...identity }
  ] }] };
  let identitiesCalled = false;
  const fake = {
    inventory: async () => ({ terminals: [{ terminal: 't-live', native: null, name: 'Claude' }], version: '0.8.2' }),
    terminalIdentities: async () => { identitiesCalled = true; return { 't-live': { pid: 4242, start: '98765' } }; }
  };
  const app = await createCompanion({ workspace, herdr: fake, inspector: async () => state });
  t.after(() => app.close());
  const body = await (await fetch(`${app.url}/api/state`, { headers: { Authorization: `Bearer ${app.token}` } })).json();
  assert.equal(identitiesCalled, true);
  assert.equal(body.runs[0].slots[0].terminal_match.terminal, 't-live');
  assert.equal(body.runs[0].slots[0].terminal_match.attribution, 'process-only');
  assert.equal(body.runs[0].slots[1].terminal_match, null);
});
test('state resolves a proven process identity to its exact terminal', async t => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-procmatch-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const state = { runs: [{ id: 'R01', slots: [
    { id: 'planner', generation: '1', process: { pid: 10, start: '5' } }
  ] }] };
  const fake = {
    inventory: async () => ({ terminals: [{ terminal: 't1', native: null, name: 'Shell' }], version: 'test' }),
    terminalIdentities: async () => ({ t1: { pid: 10, start: '5' } })
  };
  const app = await createCompanion({ workspace, herdr: fake, inspector: async () => state });
  t.after(() => app.close());
  const body = await (await fetch(`${app.url}/api/state`, { headers: { Authorization: `Bearer ${app.token}` } })).json();
  assert.equal(body.runs[0].slots[0].terminal_match.terminal, 't1');
  assert.equal(body.runs[0].slots[0].terminal_match.attribution, 'process-only');
});

async function agentHarness(t, agents) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-agent-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const stateFile = path.join(directory, 'state.json');
  const logFile = path.join(directory, 'calls.jsonl');
  await writeFile(stateFile, JSON.stringify({ agents }));
  const executable = path.join(directory, 'herdr');
  await writeFile(executable, `#!/usr/bin/env node
const { readFileSync, appendFileSync, writeFileSync, existsSync } = require('node:fs');
const state = JSON.parse(readFileSync(${JSON.stringify(stateFile)}, 'utf8'));
const args = process.argv.slice(2);
const log = record => appendFileSync(${JSON.stringify(logFile)}, JSON.stringify(record) + '\\n');
const seqFile = target => ${JSON.stringify(directory)} + '/seq-' + target;
const seqOf = target => {
  try { return parseInt(readFileSync(seqFile(target), 'utf8'), 10); }
  catch { return (state.agents[target] && state.agents[target].seq0) || 1; }
};
if (args[0] === 'agent' && args[1] === 'get') {
  const entry = state.agents[args[2]];
  if (!entry) { console.error(JSON.stringify({ error: { message: 'unknown agent ' + args[2] } })); process.exit(1); }
  console.log(JSON.stringify({ result: { agent: { agent_status: entry.status, state_change_seq: seqOf(args[2]) } } }));
} else if (args[0] === 'agent' && args[1] === 'prompt') {
  log({ command: 'prompt', target: args[2], text: args[3], wait: args.slice(4) });
  const entry = state.agents[args[2]];
  const mode = entry?.prompt || 'ok';
  if (mode === 'blocked') { console.error(JSON.stringify({ error: { message: 'agent_blocked: agent is waiting at a question' } })); process.exit(1); }
  else if (mode === 'matched') { writeFileSync(seqFile(args[2]), String(seqOf(args[2]) + 100)); console.log(JSON.stringify({ result: { type: 'agent_prompted', wait: { matched: 'working' } } })); }
  else if (mode === 'accepted-no-change') { console.log(JSON.stringify({ result: { type: 'agent_prompted' } })); }
  else if (mode === 'stalled') { console.error('agent_prompt_stalled: no state change within 5000ms'); process.exit(1); }
  else if (mode === 'hang') setTimeout(() => {}, 60000);
  else if (mode === 'fail') { console.error('prompt transport failed'); process.exit(1); }
  else console.log(JSON.stringify({ result: { type: 'agent_prompted' } }));
} else if (args[0] === 'agent' && args[1] === 'send-keys') {
  log({ command: 'send-keys', target: args[2], keys: args.slice(3) });
  console.log(JSON.stringify({ result: { type: 'agent_keys_sent' } }));
} else { console.error('unsupported command'); process.exit(2); }
`);
  await chmod(executable, 0o700);
  const adapter = new Herdr('/unused', directory, executable);
  const calls = async () => (await readFile(logFile, 'utf8').catch(() => ''))
    .split('\n').filter(Boolean).map(line => JSON.parse(line));
  return { adapter, calls };
}

test('agent transport delivers Unicode and multiline text exactly on observed acceptance', async t => {
  const { adapter, calls } = await agentHarness(t, { pane1: { status: 'idle', prompt: 'matched', seq0: 7 } });
  const text = 'नमस्ते café\nsecond line\r\nthird';
  const result = await adapter.agentPrompt('pane1', text);
  assert.equal(result.outcome, 'sent');
  assert.equal((await calls()).find(c => c.command === 'prompt').text, text);
  assert.deepEqual((await calls()).find(c => c.command === 'prompt').wait,
    ['--wait', '--until', 'working', '--timeout', '30000']);
});

test('agent transport reports blocked distinctly and treats stalls, timeouts, and unrecognized failures as uncertain', async t => {
  // Behavior change from round 2, reviewed here: only observed acceptance is
  // sent and only an observed block is failed; every other transport outcome is
  // uncertain, because a failure message cannot prove non-delivery.
  const { adapter } = await agentHarness(t, {
    blockedPane: { status: 'blocked', prompt: 'blocked' },
    stalledPane: { status: 'idle', prompt: 'stalled' },
    hangingPane: { status: 'idle', prompt: 'hang' },
    failingPane: { status: 'idle', prompt: 'fail' }
  });
  assert.equal((await adapter.agentPrompt('blockedPane', 'hello')).outcome, 'blocked');
  assert.equal((await adapter.agentPrompt('stalledPane', 'hello')).outcome, 'uncertain');
  assert.equal((await adapter.agentPrompt('hangingPane', 'hello', 300)).outcome, 'uncertain');
  assert.equal((await adapter.agentPrompt('failingPane', 'hello')).outcome, 'uncertain');
});

test('an accepted prompt with no state change surfaces uncertain, never sent', async t => {
  // The round-2 false-sent shape: Herdr replies agent_prompted but the TUI drops
  // the input, so no turn ever starts. The verdict must follow the seq signal.
  const { adapter } = await agentHarness(t, { stalePane: { status: 'idle', prompt: 'accepted-no-change', seq0: 3 } });
  const result = await adapter.agentPrompt('stalePane', 'hello');
  assert.equal(result.outcome, 'uncertain');
});

test('agent interrupt sends ctrl+c to the exact target', async t => {
  const { adapter, calls } = await agentHarness(t, { pane9: { status: 'working' } });
  const result = await adapter.agentInterrupt('pane9');
  assert.equal(result.outcome, 'stopped');
  assert.deepEqual(await calls(), [{ command: 'send-keys', target: 'pane9', keys: ['ctrl+c'] }]);
});

test('agent record reports the live status', async t => {
  const { adapter } = await agentHarness(t, { pane1: { status: 'working' } });
  assert.equal((await adapter.agentRecord('pane1')).agent_status, 'working');
});

async function sendFixture(t, { status = 'idle', prompt = { outcome: 'sent', detail: 'accepted' },
    interrupt = { outcome: 'stopped', detail: '' }, agents, interruptPolicy, ageOf, natives, requestOptions } = {}) {
  const workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-send-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const state = { runs: [{ id: 'R01', slots: [
    { id: 'planner', generation: '1', process: { pid: 7, start: '8' },
      ...(natives ? { native: { harness: 'opencode', session_id: natives.planner } } : {}),
      capabilities: { send: true, interrupt: true } },
    { id: 'implementor', generation: '1', process: { pid: 9, start: '1' },
      ...(natives ? { native: { harness: 'opencode', session_id: natives.implementor } } : {}),
      capabilities: { send: true, interrupt: true } },
    { id: 'reviewer', generation: '1', process: { pid: 5, start: '5' },
      capabilities: { send: true, interrupt: true } },
    { id: 'observer', generation: '1', capabilities: { send: false, interrupt: false } }
  ] }] };
  const kinds = agents || { t1: { agent: 'claude', status }, t2: { agent: 'claude', status: 'idle' }, t3: {} };
  const panes = { t1: 'w1:p1', t2: 'w1:p2', t3: 'w1:p3' };
  const identities = { t1: { pid: 7, start: '8' }, t2: { pid: 9, start: '1' }, t3: { pid: 5, start: '5' } };
  let promptCalls = [];
  const interruptCalls = [];
  const fake = {
    inventory: async () => ({ terminals: Object.entries(kinds).map(([terminal, kind]) =>
      ({ terminal, pane: panes[terminal], name: terminal, server: 's1', ...kind })), version: 'test' }),
    identity: async terminal => ({ terminal, pane: panes[terminal], identity: 'same', attribution: 'process-only' }),
    terminalIdentities: async () => ({ ...identities }),
    agentRecord: async pane => ({ agent_status: pane === 'w1:p1' ? status : 'idle' }),
    agentPrompt: async (pane, text) => { promptCalls.push({ pane, text }); return prompt; },
    agentInterrupt: async pane => { interruptCalls.push(pane); return interrupt; }
  };
  const app = await createCompanion({ workspace, herdr: fake, inspector: async () => state,
    promptTimeoutMs: 400, ageOf: ageOf || (async () => 999),
    ...(interruptPolicy ? { interruptPolicy } : {}), ...(requestOptions || {}) });
  t.after(() => app.close());
  const auth = { Authorization: `Bearer ${app.token}` };
  const bind = async (slot, terminal) => (await (await fetch(
    `${app.url}/api/binding?run=R01&slot=${slot}&terminal=${terminal}`, { headers: auth })).json());
  const post = async (route, body, raw) => {
    const response = await fetch(`${app.url}${route}`, { method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' },
      body: raw !== undefined ? raw : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  return { app, state, bind, post, identities, promptCalls: () => promptCalls, interruptCalls };
}

test('send pins the binding, delivers Unicode, and dedupes duplicate operations', async t => {
  const fix = await sendFixture(t);
  const binding = await fix.bind('planner', 't1');
  const text = 'नमस्ते café\nline two';
  const first = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text });
  assert.equal(first.status, 200);
  assert.equal(first.body.status, 'sent');
  assert.equal(first.body.operation, 'op-1');
  assert.equal(first.body.recipient.slot, 'planner');
  const second = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text });
  assert.equal(second.body.status, 'sent');
  assert.equal(second.body.duplicate, true);
  assert.equal(fix.promptCalls().length, 1);
  assert.equal(fix.promptCalls()[0].text, text);
  assert.equal(fix.promptCalls()[0].pane, 'w1:p1');
});

test('send refuses blocked, working, and unknown agents without touching transport', async t => {
  for (const [status, pattern] of [['blocked', /question or permission/], ['working', /working/], ['mystery', /unknown|state/]]) {
    const fix = await sendFixture(t, { status });
    const binding = await fix.bind('planner', 't1');
    const result = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'must not send' });
    assert.equal(result.status, 400, status);
    assert.match(result.body.error, pattern);
    assert.equal(fix.promptCalls().length, 0);
  }
});

test('send refuses stale bindings and malformed requests', async t => {
  const fix = await sendFixture(t);
  const binding = await fix.bind('planner', 't1');
  fix.state.runs[0].slots[0].generation = '2';
  const stale = await fix.post('/api/send', { binding: binding.id, operation: 'op-2', text: 'hello' });
  assert.equal(stale.status, 400);
  assert.match(stale.body.error, /generation changed|expired/);
  assert.equal((await fix.post('/api/send', { binding: 'nope', operation: 'op-3', text: 'hello' })).status, 400);
  assert.equal((await fix.post('/api/send', { binding: binding.id, operation: 'op-4', text: '   ' })).status, 400);
  assert.equal((await fix.post('/api/send', { binding: binding.id, operation: 'op-5' })).status, 400);
  assert.equal(fix.promptCalls().length, 0);
});

test('send refuses bindings that are not the proven terminal', async t => {
  const fix = await sendFixture(t);
  const wrong = await fix.bind('planner', 't2');
  const mismatch = await fix.post('/api/send', { binding: wrong.id, operation: 'op-1', text: 'wrong agent' });
  assert.equal(mismatch.status, 400);
  assert.match(mismatch.body.error, /proven terminal/);
  const shell = await fix.bind('reviewer', 't3');
  const noAgent = await fix.post('/api/send', { binding: shell.id, operation: 'op-2', text: 'hello' });
  assert.equal(noAgent.status, 400);
  assert.match(noAgent.body.error, /No live agent/);
  const stop = await fix.post('/api/stop', { binding: wrong.id });
  assert.equal(stop.status, 400);
  assert.match(stop.body.error, /proven terminal/);
  assert.equal(fix.promptCalls().length, 0);
  assert.deepEqual(fix.interruptCalls, []);
});

test('send refuses slots without proven identity even when explicitly bound', async t => {
  const fix = await sendFixture(t);
  const binding = await fix.bind('observer', 't1');
  const result = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'hello' });
  assert.equal(result.status, 400);
  assert.match(result.body.error, /ambiguous or unproven/);
  assert.equal(fix.promptCalls().length, 0);
});

test('a changed pid:start between bind and send is refused', async t => {
  const fix = await sendFixture(t);
  const binding = await fix.bind('planner', 't1');
  fix.identities.t1 = { pid: 99, start: '99' };
  const result = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'hello' });
  assert.equal(result.status, 400);
  assert.match(result.body.error, /No proven terminal/);
  assert.equal(fix.promptCalls().length, 0);
});

test('sends stay isolated per binding and report transport uncertainty without resending', async t => {
  const fix = await sendFixture(t, { prompt: { outcome: 'uncertain', detail: 'no acknowledgement' } });
  const planner = await fix.bind('planner', 't1');
  const worker = await fix.bind('implementor', 't2');
  const first = await fix.post('/api/send', { binding: planner.id, operation: 'shared-op', text: 'one' });
  const second = await fix.post('/api/send', { binding: worker.id, operation: 'shared-op', text: 'two' });
  assert.equal(first.body.status, 'uncertain');
  assert.equal(second.body.status, 'uncertain');
  assert.deepEqual(fix.promptCalls().map(call => call.pane), ['w1:p1', 'w1:p2']);
  const retry = await fix.post('/api/send', { binding: planner.id, operation: 'shared-op', text: 'one' });
  assert.equal(retry.body.duplicate, true);
  assert.equal(fix.promptCalls().length, 2);
});

test('stop targets only a working agent and refuses anything else without input', async t => {
  const working = await sendFixture(t, { status: 'working', interruptPolicy: { claude: { offered: true } } });
  const binding = await working.bind('planner', 't1');
  const stopped = await working.post('/api/stop', { binding: binding.id });
  assert.equal(stopped.status, 200);
  assert.equal(stopped.body.status, 'stopped');
  assert.deepEqual(working.interruptCalls, ['w1:p1']);
  const idle = await sendFixture(t, { status: 'idle' });
  const calm = await idle.bind('planner', 't1');
  const refused = await idle.post('/api/stop', { binding: calm.id });
  assert.equal(refused.status, 400);
  assert.match(refused.body.error, /nothing to stop/);
  assert.deepEqual(idle.interruptCalls, []);
  const shell = await idle.bind('reviewer', 't3');
  assert.equal((await idle.post('/api/stop', { binding: shell.id })).status, 400);
  assert.equal((await idle.post('/api/stop', { binding: 'missing' })).status, 400);
  assert.deepEqual(idle.interruptCalls, []);
});

test('stop is refused for harnesses without observed-safe interrupt semantics', async t => {
  for (const [kind, pattern] of [['opencode', /not offered for OpenCode/], ['claude', /not offered for Claude/],
      ['codex', /not offered for Codex/], ['futurekind', /no safe turn-interrupt semantics observed for futurekind/]]) {
    const fix = await sendFixture(t, { status: 'working',
      agents: { t1: { agent: kind, status: 'working' } } });
    const binding = await fix.bind('planner', 't1');
    const result = await fix.post('/api/stop', { binding: binding.id });
    assert.equal(result.status, 400, kind);
    assert.match(result.body.error, pattern);
    assert.deepEqual(fix.interruptCalls, []);
  }
});
test('a freshly booted agent is not send-ready and the endpoint refuses until the grace passes', async t => {
  let age = 5;
  const fix = await sendFixture(t, { ageOf: async () => age });
  const binding = await fix.bind('planner', 't1');
  const state = (await (await fetch(`${fix.app.url}/api/state`, { headers: { Authorization: `Bearer ${fix.app.token}` } })).json());
  const slot = state.runs[0].slots.find(s => s.id === 'planner');
  assert.equal(slot.send_ready, false);
  assert.match(slot.send_note, /readiness unconfirmed so soon after boot/);
  const young = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'too soon' });
  assert.equal(young.status, 400);
  assert.match(young.body.error, /readiness unconfirmed/);
  assert.equal(fix.promptCalls().length, 0);
  age = 300;
  const grown = (await (await fetch(`${fix.app.url}/api/state`, { headers: { Authorization: `Bearer ${fix.app.token}` } })).json());
  assert.equal(grown.runs[0].slots.find(s => s.id === 'planner').send_ready, true);
  const ready = await fix.post('/api/send', { binding: binding.id, operation: 'op-2', text: 'ready now' });
  assert.equal(ready.status, 200);
  assert.equal(ready.body.status, 'sent');
});

test('idle keep-alive connections outlive the 5 s poll period instead of racing it', async t => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-keepalive-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const app = await createCompanion({ workspace, herdr: { inventory: async () => ({ terminals: [] }) }, inspector: async () => ({ runs: [] }) });
  t.after(() => app.close());
  assert.ok(app.server.keepAliveTimeout >= 30000, `keepAliveTimeout is ${app.server.keepAliveTimeout}`);
  assert.ok(app.server.headersTimeout > app.server.keepAliveTimeout);
  // A real keep-alive client that waits just over the old default must still get its second answer.
  const agent = new http.Agent({ keepAlive: true, maxSockets: 1 });
  t.after(() => agent.destroy());
  const get = () => new Promise((resolve, reject) => http.get(`${app.url}/api/state`, { agent, headers: { Authorization: `Bearer ${app.token}` } },
    response => { response.resume(); response.on('end', () => resolve(response.statusCode)); }).on('error', reject));
  assert.equal(await get(), 200);
  await new Promise(resolve => setTimeout(resolve, 5300));
  assert.equal(await get(), 200);
});

test('a freshly started agentless shell reports no live agent, not a boot-window note', async t => {
  const fix = await sendFixture(t, { ageOf: async () => 3, agents: { t1: {}, t2: { agent: 'claude', status: 'idle' }, t3: {} } });
  await fix.bind('planner', 't1');
  const state = (await (await fetch(`${fix.app.url}/api/state`, { headers: { Authorization: `Bearer ${fix.app.token}` } })).json());
  const slot = state.runs[0].slots.find(s => s.id === 'planner');
  assert.equal(slot.send_ready, false);
  assert.match(slot.send_note, /No live agent on this terminal/);
  assert.doesNotMatch(slot.send_note, /so soon after boot/);
});

async function requestFixture(t, { agents, permissionsA = [], permissionsB = [], ports } = {}) {
  const a = await fakeOpenCode({ permissions: permissionsA, questions: [] });
  const b = await fakeOpenCode({ permissions: permissionsB, questions: [] });
  t.after(() => Promise.all([a.close(), b.close()]));
  const listeners = ports || { 7: [{ port: a.port, loopback: true }], 9: [{ port: b.port, loopback: true }] };
  const fix = await sendFixture(t, { natives: { planner: 'ses_p', implementor: 'ses_i' },
    agents: agents || { t1: { agent: 'opencode', status: 'blocked' }, t2: { agent: 'opencode', status: 'idle' }, t3: {} },
    requestOptions: { requestListeners: async pid => listeners[pid] || [] } });
  const stateOf = async () => (await (await fetch(`${fix.app.url}/api/state`, { headers: { Authorization: `Bearer ${fix.app.token}` } })).json());
  const requestsOf = async id => (await stateOf()).runs[0].slots.find(s => s.id === id).requests;
  return { ...fix, a, b, stateOf, requestsOf };
}

test('state attaches live request cards per slot from that slot\'s own endpoint and session', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p'), permissionRequest('per_x', 'ses_i')],
    permissionsB: [permissionRequest('per_i', 'ses_i')] });
  const planner = await fix.requestsOf('planner');
  assert.equal(planner.state, 'live');
  assert.deepEqual(planner.cards.map(c => c.request_id), ['per_p']);
  assert.equal(planner.generation, '1');
  const implementor = await fix.requestsOf('implementor');
  assert.deepEqual(implementor.cards.map(c => c.request_id), ['per_i']);
  const reviewer = await fix.requestsOf('reviewer');
  assert.deepEqual(reviewer.cards, []);
  assert.notEqual(reviewer.state, 'live');
});

test('questions and legacy permission lists never become cards unless explicitly opted in', async t => {
  const { legacyPermissionRequest: legacy } = await import('./fake-opencode.mjs');
  for (const experimental of [false, true]) {
    const a = await fakeOpenCode({ permissions: [permissionRequest('per_p', 'ses_p')], legacyPermissions: [legacy('per_l', 'ses_p')],
      questions: [questionRequest('que_p', 'ses_p')] });
    t.after(() => a.close());
    const fix = await sendFixture(t, { natives: { planner: 'ses_p', implementor: 'ses_i' },
      agents: { t1: { agent: 'opencode', status: 'blocked' }, t2: { agent: 'opencode', status: 'idle' }, t3: {} },
      requestOptions: { requestListeners: async pid => (pid === 7 ? [{ port: a.port, loopback: true }] : []), experimentalRequests: experimental } });
    const state = await (await fetch(`${fix.app.url}/api/state`, { headers: { Authorization: `Bearer ${fix.app.token}` } })).json();
    const requests = state.runs[0].slots.find(s => s.id === 'planner').requests;
    assert.deepEqual(requests.cards.map(c => c.request_id).sort(), experimental ? ['per_l', 'per_p', 'que_p'] : ['per_p']);
    assert.equal(!!requests.note, !experimental);
  }
});

test('slots without a live opencode endpoint expose explicit states, never cards', async t => {
  const absent = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')], ports: {} });
  const none = await absent.requestsOf('planner');
  assert.equal(none.state, 'endpoint-absent');
  assert.deepEqual(none.cards, []);
  const codex = await requestFixture(t, { agents: { t1: { agent: 'codex', status: 'blocked' }, t2: { agent: 'claude', status: 'blocked' }, t3: {} } });
  assert.equal((await codex.requestsOf('planner')).state, 'unsupported');
  assert.match((await codex.requestsOf('planner')).reason, /Codex 0\.160\.1/);
  assert.match((await codex.requestsOf('implementor')).reason, /Claude Code 2\.1\.294/);
  const shell = await requestFixture(t, { agents: { t1: {}, t2: {}, t3: {} } });
  assert.equal((await shell.requestsOf('planner')).state, 'none');
});

test('a request reply is pinned to its binding, posts once, and dedupes duplicates', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')], permissionsB: [permissionRequest('per_i', 'ses_i')] });
  const binding = await fix.bind('planner', 't1');
  const card = (await fix.requestsOf('planner')).cards[0];
  const body = { binding: binding.id, request: card.request_id, revision: card.revision, reply: 'once' };
  const first = await fix.post('/api/request/reply', body);
  assert.equal(first.status, 200);
  assert.equal(first.body.status, 'resolved');
  assert.equal(first.body.recipient.slot, 'planner');
  const second = await fix.post('/api/request/reply', body);
  assert.equal(second.body.duplicate, true);
  assert.equal(fix.a.state.posts.length, 1);
  assert.equal(fix.b.state.posts.length, 0);
  assert.deepEqual((await fix.requestsOf('planner')).cards, []);
  assert.equal((await fix.requestsOf('implementor')).cards.length, 1);
});

test('a role switch can never answer another role\'s request through the wrong binding', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')], permissionsB: [permissionRequest('per_i', 'ses_i')] });
  const planner = await fix.bind('planner', 't1');
  const implementorCard = (await fix.requestsOf('implementor')).cards[0];
  const crossed = await fix.post('/api/request/reply', { binding: planner.id, request: implementorCard.request_id,
    revision: implementorCard.revision, reply: 'always' });
  assert.equal(crossed.body.status, 'stale');
  assert.equal(fix.a.state.posts.length, 0);
  assert.equal(fix.b.state.posts.length, 0);
});

test('a stale or externally resolved decision and an endpoint loss are refused without a post', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')] });
  const binding = await fix.bind('planner', 't1');
  const card = (await fix.requestsOf('planner')).cards[0];
  fix.a.state.permissions = [];
  const external = await fix.post('/api/request/reply', { binding: binding.id, request: card.request_id, revision: card.revision, reply: 'once' });
  assert.equal(external.body.status, 'stale');
  fix.a.state.permissions = [permissionRequest('per_p', 'ses_p', { resources: ['changed'] })];
  const changed = await fix.post('/api/request/reply', { binding: binding.id, request: card.request_id, revision: card.revision, reply: 'once' });
  assert.equal(changed.body.status, 'stale');
  fix.a.state.auth = true;
  const auth = await fix.post('/api/request/reply', { binding: binding.id, request: card.request_id, revision: card.revision, reply: 'once' });
  assert.equal(auth.body.status, 'refused');
  assert.equal(fix.a.state.posts.length, 0);
});

test('a reply is refused when the terminal has no endpoint, the generation moved, or the body is malformed', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')] });
  const binding = await fix.bind('planner', 't1');
  const card = (await fix.requestsOf('planner')).cards[0];
  const good = { binding: binding.id, request: card.request_id, revision: card.revision, reply: 'once' };
  for (const bad of [{ ...good, binding: 'nope' }, { ...good, request: '' }, { ...good, revision: 7 }, {}, { ...good, request: 'x'.repeat(200) }]) {
    assert.equal((await fix.post('/api/request/reply', bad)).status, 400);
  }
  const unauth = await fetch(`${fix.app.url}/api/request/reply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(good) });
  assert.equal(unauth.status, 401);
  fix.state.runs[0].slots.find(s => s.id === 'planner').generation = '2';
  const moved = await fix.post('/api/request/reply', good);
  assert.equal(moved.status, 400);
  assert.match(moved.body.error, /generation changed/);
  assert.equal(fix.a.state.posts.length, 0);
});

test('an uncertain request reply is remembered and never resent by a duplicate click', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')] });
  const binding = await fix.bind('planner', 't1');
  const card = (await fix.requestsOf('planner')).cards[0];
  fix.a.state.keepAfterReply = true;
  const body = { binding: binding.id, request: card.request_id, revision: card.revision, reply: 'once' };
  assert.equal((await fix.post('/api/request/reply', body)).body.status, 'uncertain');
  const again = await fix.post('/api/request/reply', body);
  assert.equal(again.body.status, 'uncertain');
  assert.equal(again.body.duplicate, true);
  assert.equal(fix.a.state.posts.length, 1);
});

test('a reply with no endpoint is refused with the terminal fallback reason and posts nothing', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')] });
  const binding = await fix.bind('planner', 't1');
  const card = (await fix.requestsOf('planner')).cards[0];
  const bare = await requestFixture(t, { ports: {} });
  const bareBinding = await bare.bind('planner', 't1');
  const refused = await bare.post('/api/request/reply', { binding: bareBinding.id, request: card.request_id, revision: card.revision, reply: 'once' });
  assert.equal(refused.body.status, 'refused');
  assert.match(refused.body.reason, /live terminal/);
  assert.equal(fix.a.state.posts.length, 0);
  assert.ok(binding.id);
});

test('historical slots and saved running tools never hold request authority', async t => {
  const fix = await requestFixture(t, { permissionsA: [permissionRequest('per_p', 'ses_p')] });
  fix.state.runs[0].slots.push({ id: 'implementor:earlier', historical: true, generation: '1',
    native: { harness: 'opencode', session_id: 'ses_p' }, capabilities: { send: false, interrupt: false } });
  const earlier = (await fix.stateOf()).runs[0].slots.find(s => s.id === 'implementor:earlier');
  assert.equal(earlier.requests.state, 'none');
  assert.deepEqual(earlier.requests.cards, []);
});

test('send and stop require auth, local origin, POST, and well-formed bodies', async t => {
  const fix = await sendFixture(t);
  const binding = await fix.bind('planner', 't1');
  const body = { binding: binding.id, operation: 'op-1', text: 'hello' };
  const unauth = await fetch(`${fix.app.url}/api/send`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert.equal(unauth.status, 401);
  const foreign = await fetch(`${fix.app.url}/api/send`, { method: 'POST',
    headers: { Authorization: `Bearer ${fix.app.token}`, Origin: 'https://evil.example', 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  assert.equal(foreign.status, 403);
  const getSend = await fetch(`${fix.app.url}/api/send`, { headers: { Authorization: `Bearer ${fix.app.token}` } });
  assert.equal(getSend.status, 404);
  const getStop = await fetch(`${fix.app.url}/api/stop`, { headers: { Authorization: `Bearer ${fix.app.token}` } });
  assert.equal(getStop.status, 404);
  assert.equal((await fix.post('/api/send', null, 'not json')).status, 400);
  assert.equal((await fix.post('/api/send', { binding: binding.id, operation: 'x'.repeat(200), text: 'hi' })).status, 400);
});

test('transport-level prompt rejection becomes a failed delivery with its reason', async t => {
  const fix = await sendFixture(t, { prompt: { outcome: 'blocked', detail: 'agent_blocked: approval dialog' } });
  const binding = await fix.bind('planner', 't1');
  const result = await fix.post('/api/send', { binding: binding.id, operation: 'op-1', text: 'hello' });
  assert.equal(result.status, 200);
  assert.equal(result.body.status, 'failed');
  assert.match(result.body.reason, /refused.*question or permission/);
  assert.equal(fix.promptCalls().length, 1);
});

test('send and stop refuse cleanly when Herdr is unreachable', async t => {
  // Transient failure between the identity check and the inventory read.
  const workspace = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-offline-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const state = { runs: [{ id: 'R01', slots: [
    { id: 'planner', generation: '1', process: { pid: 7, start: '8' },
      capabilities: { send: true, interrupt: true } }
  ] }] };
  const fake = {
    inventory: async () => { throw new Error('socket gone'); },
    identity: async terminal => ({ terminal, pane: 'w1:p1', identity: 'same', attribution: 'process-only' })
  };
  const app = await createCompanion({ workspace, herdr: fake, inspector: async () => state });
  t.after(() => app.close());
  const auth = { Authorization: `Bearer ${app.token}` };
  const binding = await (await fetch(`${app.url}/api/binding?run=R01&slot=planner&terminal=t1`, { headers: auth })).json();
  const post = async route => {
    const response = await fetch(`${app.url}${route}`, { method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' },
      body: JSON.stringify({ binding: binding.id, operation: 'op-1', text: 'hello' }) });
    return { status: response.status, body: await response.json() };
  };
  const send = await post('/api/send');
  assert.equal(send.status, 400);
  assert.match(send.body.error, /unreachable/);
  const stop = await post('/api/stop');
  assert.equal(stop.status, 400);
  assert.match(stop.body.error, /unreachable/);
});

test('state reports Stop as supported only when a harness has an observed-safe interrupt', async t => {
  const none = await sendFixture(t);
  assert.equal((await (await fetch(`${none.app.url}/api/state`, { headers: { Authorization: `Bearer ${none.app.token}` } })).json()).stop_supported, false);
  const some = await sendFixture(t, { interruptPolicy: { claude: { offered: true }, codex: { offered: false, reason: 'x' } } });
  assert.equal((await (await fetch(`${some.app.url}/api/state`, { headers: { Authorization: `Bearer ${some.app.token}` } })).json()).stop_supported, true);
});

test('history and choices routes carry their scope, default to the run, and the exact slot generation', async t => {
  const seen = [];
  const app = await fixture(t, async (...args) => { seen.push(['history', ...args.slice(1)]); return { messages: [] }; },
    async (...args) => { seen.push(['choices', ...args.slice(1)]); return { choices: [] }; });
  const query = 'run=R01&slot=planner&generation=1';
  await app.get(`/api/history-choices?${query}`);
  await app.get(`/api/history-choices?${query}&scope=project`);
  await app.get(`/api/history-choices?${query}&scope=everything`);
  await app.get(`/api/history?${query}&choice=opencode%3Aa&scope=project`);
  await app.get(`/api/history?${query}`);
  assert.deepEqual(seen, [
    ['choices', 'R01', 'planner', '1', 'run'], ['choices', 'R01', 'planner', '1', 'project'], ['choices', 'R01', 'planner', '1', 'run'],
    ['history', 'R01', 'planner', '1', 'opencode:a', 'project'], ['history', 'R01', 'planner', '1', undefined, 'run']
  ]);
});

test('without a binding the state still carries the exact send reason and connection for every role', async t => {
  const fix = await sendFixture(t);
  const body = await (await fetch(`${fix.app.url}/api/state`, { headers: { Authorization: `Bearer ${fix.app.token}` } })).json();
  const bySlot = Object.fromEntries(body.runs[0].slots.map(s => [s.id, s]));
  assert.deepEqual([bySlot.planner.connection.state, bySlot.planner.send_ready, bySlot.planner.send_note], ['live', true, '']);
  assert.equal(bySlot.reviewer.connection.state, 'not-found', 'a proven shell without an agent');
  assert.match(bySlot.reviewer.send_note, /No live agent/);
  assert.equal(bySlot.observer.connection.state, 'needs-link');
  assert.match(bySlot.observer.send_note, /Link a saved session to enable chat/);
  assert.equal(bySlot.observer.send_ready, false);
});
