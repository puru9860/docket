/* Native request-endpoint probe for the companion. EXPLICIT RUN ONLY, never in a gate.
 *
 * Boots two real interactive OpenCode TUIs on a disposable Herdr server, one default
 * and one started with --port, and checks what the companion reports for each. No
 * model turn is requested, so no provider call is spent. On the --port TUI it creates
 * one real pending permission through the provider's own v2 route (POST
 * /api/session/{id}/permission, action external_directory, which asks), then shows
 * through the companion that /api/state lists exactly that card and /api/request/reply
 * resolves it once. It records, without asserting, whether the TUI pane draws it.
 * It touches no user pane and removes its server, sockets and temp dirs afterwards.
 * PROBE_OUT, when set, receives the observed JSON.
 *
 * Run from ui/:  node tests/live-request-probe.mjs
 */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, copyFile, chmod } from 'node:fs/promises';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import os from 'node:os';
import { createCompanion } from '../server.mjs';
import { rpc, Herdr } from '../herdr.mjs';

const execute = promisify(execFile);
const sessionName = `docket-ui-reqprobe-${process.pid}`;
const rootDir = path.join(os.tmpdir(), `docket-ui-reqprobe-${process.pid}`);
const serverEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !/^(HERDR_|DOCKET_|CODEX_|CLAUDE_|OPENCODE)/.test(key)));
for (const key of ['XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME']) {
  serverEnv[key] = path.join(rootDir, key.toLowerCase().replace('xdg_', ''));
}
const socketPath = path.join(serverEnv.XDG_CONFIG_HOME, 'herdr', 'sessions', sessionName, 'herdr.sock');
const workspaceDir = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-reqprobe-work-'));
await mkdir(path.join(serverEnv.XDG_DATA_HOME, 'opencode'), { recursive: true });
await copyFile(path.join(os.homedir(), '.local', 'share', 'opencode', 'auth.json'), path.join(serverEnv.XDG_DATA_HOME, 'opencode', 'auth.json'));
await chmod(path.join(serverEnv.XDG_DATA_HOME, 'opencode', 'auth.json'), 0o600);
const herdr = (args, timeout = 60000) => execute('herdr', args, { env: { ...serverEnv, HERDR_SOCKET_PATH: socketPath }, timeout });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const log = (...args) => console.log('[request-probe]', ...args);
const model = ['-m', 'opencode/muse-spark-1.3-contributor-free'];

async function occupant(pane) {
  const info = (await rpc(socketPath, 'pane.process_info', { pane_id: pane })).process_info;
  const pid = info.foreground_process_group_id;
  const stat = await readFile(`/proc/${pid}/stat`, 'utf8');
  return { pid, start: stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19] };
}

const observed = {};
async function nativeCard({ item, before, root_pane, state, port }) {
  const call = async (method, route, body) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, { method, signal: AbortSignal.timeout(8000),
      headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  };
  // Observed: the first permission created on a fresh server is denied outright; later
  // ones ask. A throwaway session absorbs that and its effect is recorded.
  const warm = await call('POST', '/session', {});
  const warmEffect = (await call('POST', `/api/session/${warm.body.id}/permission`,
    { action: 'external_directory', resources: ['/tmp/probe-warmup'], metadata: {} })).body.data.effect;
  log(`warm-up permission effect on the fresh server: ${warmEffect}`);
  const created = await call('POST', '/session', {});
  const sid = created.body.id;
  assert.match(sid, /^ses_/);
  await writeFile(path.join(workspaceDir, '.docket', 'sessions', `${item.session}.json`), JSON.stringify({
    session: item.session, role: 'implementor', run: 'probe', workspace: workspaceDir, generation: 1,
    native_harness: { harness: 'opencode', session_id: sid, cwd: workspaceDir },
    native_process: { harness: 'opencode', pid: before.pid, start: before.start } }));
  const slotNow = async () => (await state()).runs[0].slots.find(s => s.id === `implementor:${item.session}`);
  assert.deepEqual((await slotNow()).requests.cards, [], 'a fresh session has no request');
  const paneBefore = (await rpc(socketPath, 'pane.read', { pane_id: root_pane.pane_id, source: 'visible' })).read.text;
  const made = await call('POST', `/api/session/${sid}/permission`, { action: 'external_directory', resources: ['/tmp/probe-outside'], metadata: {} });
  assert.equal(made.body.data.effect, 'ask', 'the provider must hold the request pending');
  const requestId = made.body.data.id;
  const legacy = await call('GET', '/permission');
  const listed = await call('GET', `/api/session/${sid}/permission`);
  await sleep(1500);
  const paneAfter = (await rpc(socketPath, 'pane.read', { pane_id: root_pane.pane_id, source: 'visible' })).read.text;
  const current = await slotNow();
  assert.equal(current.requests.state, 'live');
  assert.equal(current.requests.cards.length, 1, 'exactly the created request');
  const card = current.requests.cards[0];
  assert.equal(card.request_id, requestId);
  assert.equal(card.session_id, sid);
  assert.match(card.revision, /^sha256:/);
  assert.deepEqual(card.choices.map(c => c.id), ['once', 'always', 'reject']);
  const binding = (await (await fetch(`${app.url}/api/binding?run=probe&slot=implementor:${item.session}&terminal=${current.terminal_match.terminal}`,
    { headers: { Authorization: `Bearer ${app.token}` } })).json());
  const reply = async () => (await fetch(`${app.url}/api/request/reply`, { method: 'POST',
    headers: { Authorization: `Bearer ${app.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ binding: binding.id, request: requestId, revision: card.revision, reply: 'reject' }) })).json();
  const first = await reply();
  assert.equal(first.status, 'resolved', first.reason);
  const second = await reply();
  assert.ok(second.duplicate === true || second.status === 'stale', `second reply must not post again: ${JSON.stringify(second)}`);
  assert.deepEqual((await call('GET', `/api/session/${sid}/permission`)).body.data, []);
  assert.deepEqual((await slotNow()).requests.cards, []);
  const direct = await call('POST', `/api/session/${sid}/permission/${requestId}/reply`, { reply: 'reject' });
  assert.equal(direct.status, 404, 'the provider no longer knows the request');
  assert.deepEqual(await occupant(root_pane.pane_id), before, 'occupant pid:start changed');
  Object.assign(observed, { warmup_first_permission_effect: warmEffect, session: sid, request_id: requestId, create_effect: made.body.data.effect,
    legacy_permission_list_after_create: legacy.body, v2_session_list_after_create: listed.body, card,
    first_reply: first, second_reply: second, direct_second_reply_status: direct.status,
    pane_changed_after_create: paneBefore !== paneAfter, occupant_before: before, occupant_after: await occupant(root_pane.pane_id) });
  log(`native card: created ${requestId} (effect ${made.body.data.effect}); legacy list ${JSON.stringify(legacy.body)}; ` +
    `v2 list ${listed.body.data.length}; companion card ${card.request_id}; reply ${first.status}; again ${second.duplicate ? 'duplicate' : second.status}; ` +
    `TUI pane changed: ${paneBefore !== paneAfter}`);
  if (process.env.PROBE_OUT) await writeFile(process.env.PROBE_OUT, JSON.stringify(observed, null, 2));
}

let server, app;
try {
  await mkdir(path.join(serverEnv.XDG_CONFIG_HOME, 'herdr'), { recursive: true });
  await writeFile(path.join(serverEnv.XDG_CONFIG_HOME, 'herdr', 'config.toml'),
    'onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n[update]\nversion_check = false\nmanifest_check = false\n');
  server = spawn('herdr', ['--session', sessionName, 'server'], { env: serverEnv, cwd: workspaceDir, stdio: 'ignore' });
  for (let i = 0; ; i++) {
    try { await rpc(socketPath, 'ping'); break; } catch { if (i > 60) throw new Error('disposable server never came up'); await sleep(500); }
  }
  const { workspace } = await rpc(socketPath, 'workspace.create', { cwd: workspaceDir, label: 'Probe' });
  const runDir = path.join(workspaceDir, '.docket', 'runs', 'probe');
  await mkdir(runDir, { recursive: true });
  await mkdir(path.join(workspaceDir, '.docket', 'sessions'), { recursive: true });
  await writeFile(path.join(runDir, 'plan.mdx'),
    '---\nrun: probe\nmode: quick\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: draft\n---\n\n# Plan: Probe\n');
  app = await createCompanion({ workspace: workspaceDir, socketPath, herdr: new Herdr(socketPath, workspaceDir) });
  const state = async () => (await (await fetch(`${app.url}/api/state`, { headers: { Authorization: `Bearer ${app.token}` } })).json());
  const cases = [{ session: 'probe-default', args: model, expect: 'endpoint-absent' },
    { session: 'probe-port', args: ['--port', '47391', ...model], expect: 'live' }];
  for (const [index, item] of cases.entries()) {
    const { root_pane } = await rpc(socketPath, 'tab.create', { workspace_id: workspace.workspace_id, cwd: workspaceDir, label: item.session });
    await herdr(['agent', 'start', item.session, '--kind', 'opencode', '--pane', root_pane.pane_id, '--timeout', '240000', '--', ...item.args], 300000);
    const before = await occupant(root_pane.pane_id);
    await writeFile(path.join(workspaceDir, '.docket', 'sessions', `${item.session}.json`), JSON.stringify({
      session: item.session, role: 'implementor', run: 'probe', workspace: workspaceDir, generation: 1,
      native_harness: { harness: 'opencode', session_id: `ses_probe${index}`, cwd: workspaceDir },
      native_process: { harness: 'opencode', pid: before.pid, start: before.start } }));
    // The listener can bind a moment after the TUI reports idle, so the --port case gets a
    // bounded window. Its recorded id is not a session that server holds: the provider
    // answers 404 and the companion must say so rather than show cards.
    const want = item.expect === 'live' ? 'session-unknown' : item.expect;
    let slot;
    for (let attempt = 0; attempt < 20; attempt++) {
      slot = (await state()).runs[0].slots.find(s => s.id === `implementor:${item.session}`);
      if (slot.requests.state === want || item.expect !== 'live') break;
      await sleep(1500);
    }
    log(`${item.session}: state=${slot.requests.state} cards=${slot.requests.cards.length}`);
    assert.equal(slot.requests.state, want, slot.requests.reason);
    assert.deepEqual(slot.requests.cards, []);
    assert.deepEqual(await occupant(root_pane.pane_id), before, 'occupant pid:start changed');
    if (item.expect === 'live') await nativeCard({ item, before, root_pane, state, port: 47391 });
  }
  log('PROBE DONE: default TUI exposes no endpoint; --port TUI held a real pending permission that the companion listed and resolved once');
} finally {
  await app?.close().catch(() => {});
  if (server?.exitCode === null) {
    await execute('herdr', ['--session', sessionName, 'server', 'stop'], { env: serverEnv, timeout: 5000 }).catch(() => {});
    server.kill('SIGTERM');
  }
  await rm(rootDir, { recursive: true, force: true }).catch(() => {});
  await rm(workspaceDir, { recursive: true, force: true }).catch(() => {});
}
