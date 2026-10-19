/* Native interactive-harness acceptance through the real browser. EXPLICIT RUN ONLY,
 * never in a gate. Disposable sessions only, on a disposable Herdr server:
 *  - an OpenCode TUI started with --port holds one real pending permission (created
 *    through the provider's own route, no model turn); the browser answers it once.
 *  - one Claude Code TUI (claude-sonnet-5-5, medium) receives one short Unicode,
 *    multiline message typed into the composer; the pane and its reply are checked.
 * It touches no user pane or session and removes everything afterwards. Set
 * ACCEPT_OUT to receive a JSON record and ACCEPT_SHOT for a screenshot.
 *
 * Run from ui/:  node tests/live-browser-acceptance.mjs */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, copyFile, chmod } from 'node:fs/promises';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import os from 'node:os';
import { chromium } from 'playwright';
import { createCompanion } from '../server.mjs';
import { rpc, Herdr } from '../herdr.mjs';

const execute = promisify(execFile);
const sessionName = `docket-ui-liveaccept-${process.pid}`;
const rootDir = path.join(os.tmpdir(), `docket-ui-liveaccept-${process.pid}`);
const serverEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(HERDR_|DOCKET_|CODEX_|CLAUDE_|OPENCODE)/.test(k)));
for (const key of ['XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME']) serverEnv[key] = path.join(rootDir, key.toLowerCase().replace('xdg_', ''));
const socketPath = path.join(serverEnv.XDG_CONFIG_HOME, 'herdr', 'sessions', sessionName, 'herdr.sock');
const workspaceDir = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-liveaccept-work-'));
const claudeDir = path.join(rootDir, 'claude-config');
await mkdir(claudeDir, { recursive: true });
const credentials = JSON.parse(await readFile(path.join(os.homedir(), '.claude', '.credentials.json'), 'utf8'));
await writeFile(path.join(claudeDir, '.credentials.json'), JSON.stringify({ claudeAiOauth: credentials.claudeAiOauth }), { mode: 0o600 });
await writeFile(path.join(claudeDir, '.claude.json'), JSON.stringify({ hasCompletedOnboarding: true, theme: 'dark',
  projects: { [workspaceDir]: { hasTrustDialogAccepted: true, hasCompletedProjectOnboarding: true } } }), { mode: 0o600 });
serverEnv.CLAUDE_CONFIG_DIR = claudeDir;
// A disposable Claude Code re-links $HOME/.local/bin/claude to its own data dir, which once
// left the user's launcher dangling. A private HOME keeps every such write inside rootDir.
serverEnv.HOME = path.join(rootDir, 'home');
serverEnv.DISABLE_AUTOUPDATER = '1';
await mkdir(serverEnv.HOME, { recursive: true });
await mkdir(path.join(serverEnv.XDG_DATA_HOME, 'opencode'), { recursive: true });
await copyFile(path.join(os.homedir(), '.local', 'share', 'opencode', 'auth.json'), path.join(serverEnv.XDG_DATA_HOME, 'opencode', 'auth.json'));
await chmod(path.join(serverEnv.XDG_DATA_HOME, 'opencode', 'auth.json'), 0o600);
const herdr = (args, timeout = 60000) => execute('herdr', args, { env: { ...serverEnv, HERDR_SOCKET_PATH: socketPath }, timeout });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...args) => console.log('[live-accept]', ...args);
const record = {};
async function occupant(pane) {
  const info = (await rpc(socketPath, 'pane.process_info', { pane_id: pane })).process_info;
  const pid = info.foreground_process_group_id;
  const stat = await readFile(`/proc/${pid}/stat`, 'utf8');
  return { pid, start: stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19] };
}
async function until(predicate, timeout, label) {
  const deadline = Date.now() + timeout;
  for (;;) { if (await predicate()) return; if (Date.now() > deadline) throw new Error(`${label} timed out`); await sleep(500); }
}

let server, app, browser;
try {
  await mkdir(path.join(serverEnv.XDG_CONFIG_HOME, 'herdr'), { recursive: true });
  await writeFile(path.join(serverEnv.XDG_CONFIG_HOME, 'herdr', 'config.toml'), 'onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n[update]\nversion_check = false\nmanifest_check = false\n');
  server = spawn('herdr', ['--session', sessionName, 'server'], { env: serverEnv, cwd: workspaceDir, stdio: 'ignore' });
  for (let i = 0; ; i++) { try { await rpc(socketPath, 'ping'); break; } catch { if (i > 60) throw new Error('server never came up'); await sleep(500); } }
  const { workspace } = await rpc(socketPath, 'workspace.create', { cwd: workspaceDir, label: 'Accept' });
  const runDir = path.join(workspaceDir, '.docket', 'runs', 'accept');
  await mkdir(runDir, { recursive: true }); await mkdir(path.join(workspaceDir, '.docket', 'sessions'), { recursive: true });
  await writeFile(path.join(runDir, 'plan.mdx'), '---\nrun: accept\nmode: quick\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: draft\n---\n\n# Plan: Live acceptance\n');
  const start = async (label, kind, args) => {
    const { root_pane } = await rpc(socketPath, 'tab.create', { workspace_id: workspace.workspace_id, cwd: workspaceDir, label });
    try { await herdr(['agent', 'start', label, '--kind', kind, '--pane', root_pane.pane_id, '--timeout', '240000', '--', ...args], 300000); }
    catch (error) {
      const screen = await rpc(socketPath, 'pane.read', { pane_id: root_pane.pane_id, source: 'visible' }).then(r => r.read.text).catch(() => 'unreadable');
      console.log(`--- ${label} did not start; its screen:\n${screen.split('\n').filter(l => l.trim()).slice(-20).join('\n')}`);
      throw error;
    }
    return root_pane.pane_id;
  };
  const claudePane = await start('claude-accept', 'claude', ['--model', 'claude-sonnet-5-5', '--effort', 'medium']);
  const opencodePane = await start('opencode-accept', 'opencode', ['--port', '47392', '-m', 'opencode/muse-spark-1.3-contributor-free']);
  const claudeBefore = await occupant(claudePane), opencodeBefore = await occupant(opencodePane);
  const api = async (method, route, body) => {
    const r = await fetch(`http://127.0.0.1:47392${route}`, { method, signal: AbortSignal.timeout(8000), headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    const t = await r.text(); return { status: r.status, body: t ? JSON.parse(t) : null };
  };
  await until(async () => (await api('GET', '/global/health').catch(() => ({}))).status === 200, 60000, 'opencode endpoint');
  const warm = await api('POST', '/session', {});
  await api('POST', `/api/session/${warm.body.id}/permission`, { action: 'external_directory', resources: ['/tmp/warm'], metadata: {} });
  const session = (await api('POST', '/session', {})).body.id;
  const made = (await api('POST', `/api/session/${session}/permission`, { action: 'external_directory', resources: ['/tmp/accept-outside'], metadata: {} })).body.data;
  assert.equal(made.effect, 'ask');
  const register = (name, role, harness, native, proof) => writeFile(path.join(workspaceDir, '.docket', 'sessions', `${name}.json`), JSON.stringify({
    session: name, role, run: 'accept', workspace: workspaceDir, generation: 1,
    native_harness: { harness, session_id: native, cwd: workspaceDir }, native_process: { harness, ...proof } }));
  await register('planner-live', 'planner', 'claude', 'claude-accept-native', claudeBefore);
  await register('implementor-live', 'implementor', 'opencode', session, opencodeBefore);
  app = await createCompanion({ workspace: workspaceDir, socketPath, herdr: new Herdr(socketPath, workspaceDir) });
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 960 } });
  await page.goto(app.privateUrl);
  await page.locator('#run-title').filter({ hasText: 'Live acceptance' }).waitFor();
  const role = name => page.locator('#roles .role').filter({ hasText: name });

  // OpenCode: the provider-held request becomes a card in the browser and is answered once.
  await role('Implementor').click();
  await page.locator(`#requests .request-card[data-request="${made.id}"]`).waitFor({ timeout: 30000 });
  await until(() => page.evaluate(() => !!activeBinding), 20000, 'opencode binding');
  await page.locator('#requests button', { hasText: 'Allow once' }).click();
  await page.locator('#requests .request-card').waitFor({ state: 'detached', timeout: 20000 });
  assert.deepEqual((await api('GET', `/api/session/${session}/permission`)).body.data, []);
  assert.deepEqual(await occupant(opencodePane), opencodeBefore);
  record.opencode = { request: made.id, session, answered: 'once', occupant: opencodeBefore, occupant_after: await occupant(opencodePane) };
  log(`opencode: browser answered ${made.id} once; provider list empty; occupant unchanged`);

  // Claude Code: one short real send typed in the composer; the 60s boot grace is waited out.
  await role('Planner').click();
  await until(() => page.evaluate(() => !!activeBinding), 20000, 'claude binding');
  await until(async () => await page.locator('#send').isEnabled().catch(() => false) || (await page.fill('#message', 'x').then(() => page.locator('#send').isEnabled()).catch(() => false)), 120000, 'claude send readiness');
  const message = 'Acceptance αβ café ACC_1: नमस्ते\nSecond line: reply with exactly RESP_ACC and nothing else. Do not run any tools.';
  await page.fill('#message', message);
  await page.click('#send');
  await page.locator('#delivery').filter({ hasText: 'Sent to' }).waitFor({ timeout: 60000 });
  // While the agent is working only the visible screen can be read; history is read once idle.
  const readPane = async () => {
    try { return (await herdr(['agent', 'read', claudePane, '--source', 'recent-unwrapped', '--lines', '200'])).stdout; }
    catch { return (await rpc(socketPath, 'pane.read', { pane_id: claudePane, source: 'visible' })).read.text; }
  };
  await until(async () => { const text = await readPane(); return text.includes('ACC_1') && (text.match(/RESP_ACC/g) || []).length >= 2; }, 120000, 'claude reply');
  const pane = await readPane();
  assert.ok(pane.includes('αβ') && pane.includes('café'), 'the Unicode text reached the pane');
  assert.deepEqual(await occupant(claudePane), claudeBefore);
  record.claude = { delivery: 'sent', occupant: claudeBefore, occupant_after: await occupant(claudePane), pane_excerpt: pane.split('\n').filter(l => l.trim()).slice(-14) };
  log('claude: composer Send delivered one prompt; pane shows it and the reply; occupant unchanged');
  if (process.env.ACCEPT_SHOT) await page.screenshot({ path: process.env.ACCEPT_SHOT, fullPage: true });
  if (process.env.ACCEPT_OUT) await writeFile(process.env.ACCEPT_OUT, JSON.stringify(record, null, 2));
  log('ACCEPTANCE DONE');
} finally {
  await browser?.close().catch(() => {});
  await app?.close().catch(() => {});
  if (server?.exitCode === null) {
    await execute('herdr', ['--session', sessionName, 'server', 'stop'], { env: serverEnv, timeout: 5000 }).catch(() => {});
    server.kill('SIGTERM');
  }
  await rm(rootDir, { recursive: true, force: true }).catch(() => {});
  await rm(workspaceDir, { recursive: true, force: true }).catch(() => {});
}
