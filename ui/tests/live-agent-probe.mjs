/* Live-agent acceptance probe for the Docket companion send/stop transport.
 *
 * EXPLICIT RUN ONLY. Never wired into any gate (`npm test` ignores it: the name
 * does not match *.test.mjs). Each run starts real interactive agent TUIs on a
 * disposable Herdr server and spends free-model turns under the user's own
 * opencode auth (copied, never printed). It creates no Docket runs, touches no
 * user pane, and removes its server, sockets, and temp dirs afterwards.
 *
 * The delivery oracle is the pane itself: a send is only accepted as delivered
 * when the settled pane shows the probe text and the requested reply, not when
 * an endpoint merely returned sent. PROBE_BOOTS (default 3) fresh agent boots
 * each run this check; PROBE_STOP_KEY (default ctrl+c) is the direct platform
 * interrupt experiment sent to the final working pane.
 *
 * PROBE_HARNESS=claude runs one disposable Claude Code boot instead (no OpenCode
 * call, no working or stop phase). It authenticates from a copy of only the OAuth block in a
 * private CLAUDE_CONFIG_DIR and writes the settled pane text to PROBE_CAPTURE.
 *
 * Run from ui/:  node tests/live-agent-probe.mjs
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
const harness = process.env.PROBE_HARNESS || 'opencode';
assert.ok(['opencode', 'claude'].includes(harness), `unsupported PROBE_HARNESS ${harness}`);
const boots = harness === 'claude' ? 1 : Number(process.env.PROBE_BOOTS || 3);
const sessionName = `docket-ui-probe-${process.pid}`;
const rootDir = path.join(os.tmpdir(), `docket-ui-probe-${process.pid}`);
const serverEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !/^(HERDR_|DOCKET_|CODEX_|CLAUDE_|OPENCODE)/.test(key) &&
  !['PS1', 'PS2', 'PROMPT', 'RPROMPT', 'ENV', 'BASH_ENV'].includes(key)));
for (const key of ['XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME']) {
  serverEnv[key] = path.join(rootDir, key.toLowerCase().replace('xdg_', ''));
}
const socketPath = path.join(serverEnv.XDG_CONFIG_HOME, 'herdr', 'sessions', sessionName, 'herdr.sock');
// A disposable Claude Code re-links $HOME/.local/bin/claude to its own data dir, which once
// left the user's launcher dangling. A private HOME keeps every such write inside rootDir.
serverEnv.HOME = path.join(rootDir, 'home');
serverEnv.DISABLE_AUTOUPDATER = '1';
await mkdir(serverEnv.HOME, { recursive: true });
const workspaceDir = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-probe-work-'));
// The disposable agent authenticates as the user from a 0600 copy of only its auth
// material, removed with rootDir. Claude Code gets a private config dir that
// pre-trusts the temp workspace so no user file is written.
if (harness === 'claude') {
  const claudeDir = path.join(rootDir, 'claude-config');
  await mkdir(claudeDir, { recursive: true });
  const credentials = JSON.parse(await readFile(path.join(os.homedir(), '.claude', '.credentials.json'), 'utf8'));
  await writeFile(path.join(claudeDir, '.credentials.json'), JSON.stringify({ claudeAiOauth: credentials.claudeAiOauth }), { mode: 0o600 });
  await writeFile(path.join(claudeDir, '.claude.json'), JSON.stringify({ hasCompletedOnboarding: true, theme: 'dark',
    projects: { [workspaceDir]: { hasTrustDialogAccepted: true, hasCompletedProjectOnboarding: true } } }), { mode: 0o600 });
  serverEnv.CLAUDE_CONFIG_DIR = claudeDir;
} else {
  await mkdir(path.join(serverEnv.XDG_DATA_HOME, 'opencode'), { recursive: true });
  await copyFile(path.join(os.homedir(), '.local', 'share', 'opencode', 'auth.json'),
    path.join(serverEnv.XDG_DATA_HOME, 'opencode', 'auth.json'));
  await chmod(path.join(serverEnv.XDG_DATA_HOME, 'opencode', 'auth.json'), 0o600);
}
const socketEnv = { ...serverEnv, HERDR_SOCKET_PATH: socketPath };
const herdr = (args, timeout = 60000) => execute('herdr', args, { env: socketEnv, timeout, maxBuffer: 4 * 1024 * 1024 });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const log = (...args) => console.log('[probe]', ...args);

async function statusOf(pane) {
  const { stdout } = await herdr(['agent', 'get', pane]);
  return JSON.parse(stdout).result.agent.agent_status;
}
async function untilStatus(pane, want, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    let status = 'unknown';
    try { status = await statusOf(pane); } catch (error) { log('agent get:', String(error).split('\n')[0]); }
    if (want.includes(status)) return status;
    if (Date.now() > deadline) throw new Error(`${label}: still ${status} after ${timeoutMs}ms`);
    await sleep(2000);
  }
}
async function occupant(pane) {
  const info = (await rpc(socketPath, 'pane.process_info', { pane_id: pane })).process_info;
  const pid = info.foreground_process_group_id;
  const stat = await readFile(`/proc/${pid}/stat`, 'utf8');
  return { pid, start: stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19] };
}
async function paneText(pane, lines = 80, source = 'recent-unwrapped') {
  const { stdout } = await herdr(['agent', 'read', pane, '--source', source, '--lines', String(lines)]);
  return stdout;
}
async function stateSeq(pane) {
  const { stdout } = await herdr(['agent', 'get', pane]);
  return JSON.parse(stdout).result.agent.state_change_seq;
}
async function paneVisible(pane) {
  return (await rpc(socketPath, 'pane.read', { pane_id: pane, source: 'visible' })).read.text;
}

let server, app;
try {
  await mkdir(path.join(serverEnv.XDG_CONFIG_HOME, 'herdr'), { recursive: true });
  await writeFile(path.join(serverEnv.XDG_CONFIG_HOME, 'herdr', 'config.toml'),
    'onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n[update]\nversion_check = false\nmanifest_check = false\n');
  server = spawn('herdr', ['--session', sessionName, 'server'], { env: serverEnv, cwd: workspaceDir });
  server.stdout.on('data', () => {});
  server.stderr.on('data', () => {});
  const deadline = Date.now() + 30000;
  for (;;) {
    try { await rpc(socketPath, 'ping'); break; } catch { /* not up yet */ }
    if (Date.now() > deadline) throw new Error('disposable server never came up');
    await sleep(500);
  }
  log('disposable server up');
  const { workspace } = await rpc(socketPath, 'workspace.create', { cwd: workspaceDir, label: 'Probe' });
  const runDir = path.join(workspaceDir, '.docket', 'runs', 'probe');
  await mkdir(runDir, { recursive: true });
  await mkdir(path.join(workspaceDir, '.docket', 'sessions'), { recursive: true });
  await writeFile(path.join(runDir, 'plan.mdx'),
    '---\nrun: probe\nmode: quick\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: draft\n---\n\n# Plan: Probe\n');
  app = await createCompanion({ workspace: workspaceDir, socketPath, herdr: new Herdr(socketPath, workspaceDir) });
  const api = async (route, init = {}) => {
    const response = await fetch(`${app.url}${route}`, { ...init,
      headers: { Authorization: `Bearer ${app.token}`, ...(init.headers || {}) } });
    return { status: response.status, body: await response.json() };
  };
  const post = (route, body) => api(route, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  const slotOf = async session => (await api('/api/state')).body.runs[0].slots
    .find(s => s.id === `implementor:${session}`);

  for (let boot = 1; boot <= boots; boot++) {
    const agent = `probe-agent-${boot}`;
    const session = `probe-${boot}`;
    log(`--- boot ${boot}: starting a fresh agent ---`);
    const { root_pane } = await rpc(socketPath, 'tab.create', { workspace_id: workspace.workspace_id,
      cwd: workspaceDir, label: `Probe ${boot}` });
    const pane = root_pane.pane_id;
    const agentArgs = harness === 'claude' ? ['--model', 'claude-sonnet-5-5', '--effort', 'medium']
      : ['-m', 'opencode/muse-spark-1.3-contributor-free'];
    await herdr(['agent', 'start', agent, '--kind', harness, '--pane', pane,
      '--timeout', '240000', '--', ...agentArgs], 300000);
    await untilStatus(pane, ['idle', 'done'], 240000, `boot ${boot}`);
    const before = await occupant(pane);
    log(`boot ${boot}: idle, occupant pid:start ${before.pid}:${before.start}`);
    await writeFile(path.join(workspaceDir, '.docket', 'sessions', `${session}.json`), JSON.stringify({
      session, role: 'implementor', run: 'probe', workspace: workspaceDir, generation: 1,
      native_harness: { harness, session_id: `${session}-native`, cwd: workspaceDir },
      native_process: { harness, pid: before.pid, start: before.start }
    }));
    const slot = await slotOf(session);
    assert.equal(slot.terminal_match?.terminal !== undefined && slot.terminal_match.terminal !== null, true,
      'proven terminal must resolve');
    log(`boot ${boot}: send_ready=${slot.send_ready} note=${JSON.stringify(slot.send_note)}`);
    const binding = (await api(`/api/binding?run=probe&slot=implementor:${session}&terminal=${slot.terminal_match.terminal}`)).body;
    assert.ok(binding.id);

    // Attempt inside the boot window (recorded, not asserted): the grace only
    // needs to prevent a claimed 'sent' that the TUI never accepted.
    const early = await post('/api/send', { binding: binding.id, operation: `boot-${boot}-early`,
      text: 'early probe message that must not be claimed as sent' });
    log(`boot ${boot}: early attempt -> ${early.status} ${JSON.stringify(early.body.error || early.body.status)}`);
    if (early.status === 200 && early.body.status === 'sent') {
      log(`boot ${boot}: WARNING early send was reported sent; watched a state change so the TUI accepted it`);
    }

    // Wait for confirmed readiness, then deliver and verify against the pane.
    const readyDeadline = Date.now() + 180000;
    for (;;) {
      const current = (await api('/api/state')).body.runs[0].slots.find(s => s.id === `implementor:${session}`);
      if (current.send_ready) break;
      if (Date.now() > readyDeadline) throw new Error(`boot ${boot}: send_ready never became true (${current.send_note})`);
      await sleep(3000);
    }
    const marker = `RESP_${boot}`;
    // The delivery anchor must survive the TUI's own renderer. Observed on
    // OpenCode 1.18.35: αβ and café render exactly while the Devanagari cluster
    // renders as gaps, so the assertion anchors on DLVR/αβ/café and the
    // Devanagari stays in the message as Unicode transport evidence only.
    const message = `Probe αβ café DLVR_${boot}: नमस्ते\nSecond line: reply with exactly ${marker} and nothing else. Do not run any tools.`;
    const seqBefore = await stateSeq(pane);
    log(`boot ${boot}: state_change_seq before send ${seqBefore}`);
    const send = await post('/api/send', { binding: binding.id, operation: `boot-${boot}-send`, text: message });
    log(`boot ${boot}: send -> ${send.body.status} ${send.body.reason || ''}`);
    if (send.body.status !== 'sent') throw new Error(`boot ${boot}: expected sent, got ${send.body.status}: ${send.body.reason}`);
    log(`boot ${boot}: state_change_seq after send ${await stateSeq(pane)}`);
    await untilStatus(pane, ['idle', 'done', 'blocked'], 300000, `boot ${boot} settle`);
    // Delivery oracle: the pane must echo the message. The reply marker proves the
    // turn completed; a provider error (for example the free-tier rate limit) is an
    // environmental condition recorded, not a delivery failure.
    let text = '';
    let delivered = false, replied = false;
    for (const attempt of [1, 2, 3]) {
      text = await paneText(pane, 200);
      delivered = text.includes(`DLVR_${boot}`) && text.includes('αβ') && text.includes('café');
      replied = (text.match(new RegExp(marker, 'g')) || []).length >= 2;
      if (delivered && replied) break;
      await sleep(4000);
    }
    if (!delivered) {
      console.log('--- pane after settle ---\n' + text.slice(-4000));
      throw new Error(`boot ${boot}: pane never showed the probe message`);
    }
    if (process.env.PROBE_CAPTURE) await writeFile(process.env.PROBE_CAPTURE, text);
    const providerError = /Rate limit exceeded|Free limit reached|Error from provider/i.test(text);
    if (!replied && !providerError) {
      console.log('--- pane after settle ---\n' + text.slice(-4000));
      throw new Error(`boot ${boot}: message echoed but no reply and no provider error`);
    }
    const after = await occupant(pane);
    assert.deepEqual(after, before, `boot ${boot}: occupant pid:start changed`);
    log(`boot ${boot}: pane shows the delivered message; reply ${replied ? marker : 'blocked by provider rate limit'}; occupant unchanged`);

    if (boot === boots && replied && harness === 'opencode') {
      // Final boot with a completed turn: working-state refusal, Stop policy
      // refusal, and the direct platform interrupt experiment (may kill the TUI).
      const count = await post('/api/send', { binding: binding.id, operation: `boot-${boot}-count`,
        text: 'Count from 1 to 2000, printing each number on its own line. Do not stop until done. Do not run any tools.' });
      assert.equal(count.body.status, 'sent');
      const stopDeadline = Date.now() + 180000;
      for (;;) {
        if ((await statusOf(pane)) === 'working') break;
        if (Date.now() > stopDeadline) throw new Error('counting finished before the stop attempt; rerun the probe');
        await sleep(1000);
      }
      log(`boot ${boot}: working observed; second send must be refused:`);
      const extra = await post('/api/send', { binding: binding.id, operation: `boot-${boot}-extra`, text: 'must be refused' });
      log(`boot ${boot}: second send -> ${extra.status} ${JSON.stringify(extra.body.error || extra.body.status)}`);
      assert.equal(extra.status, 400);
      assert.match(extra.body.error, /working/);
      const stop = await post('/api/stop', { binding: binding.id });
      log(`boot ${boot}: /api/stop -> ${stop.status} ${JSON.stringify(stop.body.error || stop.body.status)}`);
      assert.equal(stop.status, 400);
      assert.match(stop.body.error, /not offered/);
      const directKey = process.env.PROBE_STOP_KEY || 'ctrl+c';
      log(`boot ${boot}: sending key ${directKey} directly (platform experiment, not /api/stop)`);
      try {
        const { stdout } = await herdr(['agent', 'send-keys', pane, directKey], 10000);
        log(`boot ${boot}: direct send-keys -> ${String(stdout).trim().slice(0, 120)}`);
      } catch (error) {
        log(`boot ${boot}: direct send-keys failed -> ${String(error.stderr || error.message).slice(0, 160)}`);
      }
      await sleep(5000);
      let alive = false;
      try { await readFile(`/proc/${before.pid}/stat`, 'utf8'); alive = true; } catch { alive = false; }
      let statusAfter = 'gone';
      try { statusAfter = await statusOf(pane); } catch { /* occupant gone */ }
      log(`boot ${boot}: after direct ${directKey}: occupant alive=${alive} status=${statusAfter}`);
      console.log(`--- boot ${boot}: pane after direct ${directKey} ---\n` + (await paneVisible(pane)).slice(-2500));
    } else if (boot === boots) {
      log(`boot ${boot}: ${harness === 'opencode' ? 'provider-blocked' : 'single-prompt harness run'}; skipping the working/stop experiment`);
    }
  }
  log('PROBE DONE: all boots delivered and verified on the pane');
} finally {
  await app?.close().catch(() => {});
  if (server?.exitCode === null) {
    await execute('herdr', ['--session', sessionName, 'server', 'stop'], { env: serverEnv, timeout: 5000 }).catch(() => {});
    server.kill('SIGTERM');
  }
  await rm(rootDir, { recursive: true, force: true }).catch(() => {});
  await rm(workspaceDir, { recursive: true, force: true }).catch(() => {});
}
