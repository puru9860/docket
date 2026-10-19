/* Exercises real terminals on an isolated Herdr server. No agents or user's panes. */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import os from 'node:os';
import { chromium } from 'playwright';
import { createCompanion } from '../server.mjs';
import { Herdr, rpc } from '../herdr.mjs';

const execute = promisify(execFile);
const directory = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-browser-'));
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !/^(HERDR_|DOCKET_|CODEX_|CLAUDE_|OPENCODE)/.test(key)));
Object.assign(env, { XDG_CONFIG_HOME: path.join(directory, 'config'),
  XDG_STATE_HOME: path.join(directory, 'state'), XDG_CACHE_HOME: path.join(directory, 'cache') });
for (const key of ['PS1', 'PS2', 'PROMPT', 'RPROMPT', 'ENV', 'BASH_ENV']) delete env[key];
const config = path.join(env.XDG_CONFIG_HOME, 'herdr');
await mkdir(config, { recursive: true });
await writeFile(path.join(config, 'config.toml'), 'onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n[update]\nversion_check = false\nmanifest_check = false\n');
const socketPath = path.join(config, 'sessions', 'docket-ui-poc-test', 'herdr.sock');
const server = spawn('herdr', ['--session', 'docket-ui-poc-test', 'server'], { env, cwd: directory });
let serverLog = ''; server.stdout.on('data', bytes => { serverLog += bytes; }); server.stderr.on('data', bytes => { serverLog += bytes; });
let browser, app, competitor, page, plannerPane;
const checks = [];
const openDetails = async () => { if (!(await page.locator('#session-details').evaluate(d => d.open))) await page.click('#session-details > summary'); };
async function until(predicate, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 70));
  }
  throw new Error('Browser condition timed out');
}
try {
  await until(async () => { try { await rpc(socketPath, 'ping'); return true; } catch { return false; } });
  const run = path.join(directory, '.docket/runs/browser-spike');
  await mkdir(run, { recursive: true });
  await writeFile(path.join(run, 'plan.mdx'), '---\nrun: browser-spike\nmode: standard\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, orchestrator, implementor, verifier, reviewer\nstatus: draft\n---\n\n# Plan: Bring every role into focus\n\n## Objective\n\nA focused workspace for five agents, with a durable paper trail.\n\n## Tasks\n\n| id | title |\n| --- | --- |\n| T01 | Role navigation |\n| T02 | Terminal bridge |\n| T03 | Document inspection |\n');
  for (const [owner, status] of [['T01', 'approved'], ['T02', 'submitted'], ['T03', 'blocked']]) {
    await writeFile(path.join(run, `${owner}-task.mdx`), `---\ntitle: ${owner} browser spike\n---\n# Task\n`);
    await writeFile(path.join(run, `${owner}-report-01.mdx`), `---\nstatus: ${status}\nround: 1\n---\n# Report\n\n<script>window.documentPwned=true</script>\n`);
  }
  const messages = path.join(directory, '.docket/conversations/C01/messages');
  await mkdir(messages, { recursive: true });
  await writeFile(path.join(messages, '000001.mdx'), '---\nsender: human\nsequence: 1\n---\n# Discussion\n\nKeep terminal sessions alive when switching roles.\n');
  const providerHome = path.join(directory, 'claude-fixture');
  const providerProject = path.join(providerHome, 'projects', 'test');
  await mkdir(providerProject, { recursive: true });
  const registrations = path.join(directory, '.docket/sessions');
  await mkdir(registrations, { recursive: true });
  for (const [session, native] of [['planner', 'planner-demo'], ['worker', 'worker-demo'], ['earlier', 'earlier-demo']]) {
    await writeFile(path.join(providerProject, `${native}.jsonl`), [
      { sessionId: native, cwd: directory, type: 'user', message: { content: `Hello ${native}` } },
      { sessionId: native, type: 'assistant', message: { content: [
        { type: 'text', text: `Reply for ${native} <script>window.historyPwned=true</script>` },
        { type: 'tool_use', name: 'Bash', input: { command: 'Recorded command, not a current permission request' } }
      ] } }
    ].map(r => JSON.stringify(r)).join('\n') + '\n');
  }
  const { workspace, root_pane: planner } = await rpc(socketPath, 'workspace.create', { cwd: directory, label: 'POC test' });
  plannerPane = planner;
  const { root_pane: worker } = await rpc(socketPath, 'tab.create', { workspace_id: workspace.workspace_id, cwd: directory, label: 'Implementor' });
  const pidBefore = (await rpc(socketPath, 'pane.process_info', { pane_id: planner.pane_id })).process_info.shell_pid;
  // The isolated server hosts plain shells, so the worker pane's proven occupant is its
  // shell process; registering its real pid:start exercises the live resolution path.
  const workerShell = (await rpc(socketPath, 'pane.process_info', { pane_id: worker.pane_id })).process_info.shell_pid;
  const workerStat = await readFile(`/proc/${workerShell}/stat`, 'utf8');
  const workerStart = workerStat.slice(workerStat.lastIndexOf(')') + 2).split(' ')[19];
  const workerProcess = { harness: 'claude', pid: workerShell, start: workerStart };
  const notes = [];
  for (const [role, session, native, owner] of [['planner', 'planner', 'planner-demo', '-'],
    ['implementor', 'worker', 'worker-demo', 'T02'], ['implementor', 'earlier', 'earlier-demo', 'T01']]) {
    const live = session === 'worker' ? { native_process: workerProcess } : {};
    await writeFile(path.join(registrations, `${session}.json`), JSON.stringify({
      session, role, run: 'browser-spike', workspace: directory, generation: 1,
      native_harness: { harness: 'claude', session_id: native, cwd: directory }, ...live
    }));
    notes.push({ role, owner, harness: 'claude', session_id: native, cwd: directory,
      ...(session === 'worker' ? { worker_process: workerProcess } : {}) });
  }
  await writeFile(path.join(run, '.harness-sessions.jsonl'), notes.map(r => JSON.stringify(r)).join('\n'));
  await mkdir(path.join(run, '.dispatch'));
  await writeFile(path.join(run, '.dispatch/T01.json'), JSON.stringify({
    run: 'browser-spike', role: 'implementor', session: 'earlier', owner: 'T01', state: 'released', dispatched_at: '2026-10-08T10:00:00Z'
  }));
  await writeFile(path.join(run, '.dispatch/T02.json'), JSON.stringify({
    run: 'browser-spike', role: 'implementor', session: 'worker', owner: 'T02', state: 'dispatched', dispatched_at: '2026-10-08T11:00:00Z'
  }));
  const adapter = new Herdr(socketPath, directory);
  app = await createCompanion({ workspace: directory, socketPath, herdr: adapter,
    historyReader: async (workspace, run, slot, generation, choice, scope) => {
      const args = ['-B', path.resolve('inspection.py'), workspace, '--history', run, slot, generation, choice || '', scope || 'run'];
      const { stdout } = await execute('python3', args, { env: { ...process.env, CLAUDE_CONFIG_DIR: providerHome }, maxBuffer: 8 * 1024 * 1024 });
      return JSON.parse(stdout);
    }
  });
  const inventory = await adapter.inventory();
  const plannerTerminal = inventory.terminals.find(t => t.pane === planner.pane_id).terminal;
  const workerTerminal = inventory.terminals.find(t => t.pane === worker.pane_id).terminal;
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1500, height: 960 } });
  const pageErrors = []; page.on('pageerror', error => pageErrors.push(error.message));
  await page.goto(app.privateUrl);
  await page.locator('#run-title').filter({ hasText: 'Bring every role into focus' }).waitFor();
  assert.equal(await page.locator('#roles .role:visible').count(), 5);
  checks.push('actual five-role inventory and run documents');
  await page.locator('#conversation .message-body').filter({ hasText: 'Reply for planner-demo' }).waitFor();
  assert.equal(await page.locator('#send').isDisabled(), true);
  assert.equal(await page.evaluate(() => window.historyPwned), undefined);
  await page.fill('#message', 'Planner draft only');
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).click();
  await page.locator('#conversation .message-body').filter({ hasText: 'Reply for worker-demo' }).waitFor();
  assert.ok(!(await page.locator('#conversation').textContent()).includes('Reply for planner-demo'));
  await page.fill('#message', 'Current worker draft only');
  await until(() => page.locator('#binding-label').textContent().then(t => t.includes('auto-matched')));
  assert.equal(await page.locator('#terminal-select').inputValue(), workerTerminal);
  assert.equal(await page.locator('#stream-mode').textContent(), 'Disconnected');
  checks.push('real /api/state resolves the recorded pid:start and auto-binds the exact pane observe-only');
  assert.equal(await page.locator('#send').isDisabled(), true);
  assert.equal(await page.locator('#stop').isDisabled(), true);
  assert.ok((await page.locator('#recipient-hint').textContent()).includes('live terminal'));
  assert.ok((await page.locator('#stop').getAttribute('title') || '').includes('No live agent'));
  checks.push('send and stop stay disabled on agent-less shells with a terminal-fallback hint');
  const earlierToggle = page.locator('[data-history-role="implementor"]');
  assert.equal(await earlierToggle.getAttribute('aria-expanded'), 'false');
  await earlierToggle.click();
  await page.locator('#roles .role').filter({ hasText: 'T01' }).click();
  await page.locator('#conversation .message-body').filter({ hasText: 'Reply for earlier-demo' }).waitFor();
  assert.equal(await page.locator('#message').isDisabled(), true);
  assert.equal(await page.locator('[data-view="terminal"]').isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 0);
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).click();
  assert.equal(await page.locator('#message').inputValue(), 'Current worker draft only');
  await earlierToggle.click();
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  assert.equal(await page.locator('#message').inputValue(), 'Planner draft only');
  const plannerTranscript = path.join(providerProject, 'planner-demo.jsonl');
  const bytes = await readFile(plannerTranscript, 'utf8');
  await writeFile(plannerTranscript, bytes + JSON.stringify({ sessionId: 'planner-demo', type: 'assistant', message: { content: 'New saved update' } }) + '\n');
  await page.click('#refresh');
  await page.locator('#conversation .message-body').filter({ hasText: 'New saved update' }).waitFor();
  checks.push('real saved JSONL history, inert markup/tool records, refresh, isolated drafts, collapsed read-only earlier task');
  let heldHistory;
  await page.route('**/api/history?**', async route => {
    if (new URL(route.request().url()).searchParams.get('slot') === 'planner:planner') {
      heldHistory = () => route.continue();
    } else await route.continue();
  });
  await page.click('#refresh'); await until(() => !!heldHistory);
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).click();
  await page.locator('#conversation .message-body').filter({ hasText: 'Reply for worker-demo' }).waitFor();
  const oldResponse = page.waitForResponse(response => response.url().includes('slot=planner%3Aplanner'));
  await heldHistory(); await oldResponse;
  await page.unroute('**/api/history?**');
  assert.ok(!(await page.locator('#conversation').textContent()).includes('Reply for planner-demo'));
  const workerRegistration = path.join(registrations, 'worker.json');
  const workerRecord = JSON.parse(await readFile(workerRegistration, 'utf8'));
  await writeFile(workerRegistration, JSON.stringify({ ...workerRecord, generation: 2 }));
  await page.click('#refresh');
  await until(() => page.locator('#message').inputValue().then(value => value === ''));
  await page.locator('#replaced-note').waitFor({ state: 'visible', timeout: 20000 });
  assert.ok((await page.locator('#replaced-note').textContent()).includes('previous conversation is read-only history'));
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  assert.equal(await page.locator('#replaced-note').isHidden(), true, 'the note belongs to the replaced role only');
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).first().click();
  await page.locator('#replaced-note').waitFor({ state: 'visible' });
  checks.push('delayed history stays with its recipient and replacement generations do not inherit drafts');
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  await page.locator('#conversation .message-body').filter({ hasText: 'New saved update' }).waitFor();
  await page.evaluate(() => {
    const rgb = value => value.match(/[\d.]+/g)?.slice(0, 3).map(Number);
    const luminance = color => color.map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4)
      .reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
    for (const selector of ['.message-body', '.message-meta', '.tool-card summary', '#document-body', '.role-text small', '.history-note', '.composer-footer>span']) {
      const element = document.querySelector(selector), style = getComputedStyle(element);
      let parent = element, background;
      while (parent) {
        const color = getComputedStyle(parent).backgroundColor;
        if (color !== 'rgba(0, 0, 0, 0)' && color !== 'transparent') { background = rgb(color); break; }
        parent = parent.parentElement;
      }
      const a = luminance(rgb(style.color)), b = luminance(background || [255, 255, 255]);
      const contrast = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
      if (contrast < 4.5 || parseFloat(style.fontSize) < 12) throw Error(`Unreadable ${selector}: ${contrast}`);
    }
    if (parseFloat(getComputedStyle(document.querySelector('.message-body')).fontSize) < 15) throw Error('Conversation type is too small');
  });
  checks.push('live layout meets readable text contrast and type sizes');
  await page.locator('#roles .role').filter({ hasText: 'Reviewer' }).click();
  await openDetails();
  await page.locator('#history-select option[value="claude:planner-demo"]').waitFor({ state: 'attached' });
  await page.selectOption('#history-select', 'claude:planner-demo'); await page.click('#link-history');
  await page.locator('#conversation .message-body').filter({ hasText: 'Reply for planner-demo' }).waitFor();
  assert.ok((await page.locator('#history-note').textContent()).includes('recorded under planner'));
  await page.fill('#message', 'Draft for explicitly selected planner history');
  await page.selectOption('#history-select', 'claude:worker-demo'); await page.click('#link-history');
  await page.locator('#conversation .message-body').filter({ hasText: 'Reply for worker-demo' }).waitFor();
  assert.equal(await page.locator('#message').inputValue(), 'Draft for explicitly selected planner history', 'viewing history never moves the role\'s recipient or draft');
  assert.equal(await page.locator('#send').isDisabled(), true, 'a history view alone grants no send');
  await page.selectOption('#history-select', 'claude:planner-demo'); await page.click('#link-history');
  assert.equal(await page.locator('#message').inputValue(), 'Draft for explicitly selected planner history');
  await page.reload();
  await page.locator('#roles .role').filter({ hasText: 'Reviewer' }).click();
  await openDetails();
  await page.locator('#conversation .message-body').filter({ hasText: 'Reply for planner-demo' }).waitFor();
  await until(() => page.locator('#history-select').inputValue().then(value => value === 'claude:planner-demo'));
  checks.push('missing role history can be explicitly linked; provenance, separate drafts and links survive reload');
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  await page.locator('#conversation .message-body').filter({ hasText: 'New saved update' }).waitFor();
  await mkdir('test-results', { recursive: true });
  await page.screenshot({ path: 'test-results/docket-chat.png', fullPage: true });
  await page.click('[data-view="terminal"]');
  await page.selectOption('#terminal-select', plannerTerminal);
  await page.click('#bind');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'Read only'));
  const nativeViewport = (await adapter.identity(plannerTerminal)).viewport;
  if (nativeViewport) {
    await until(() => page.evaluate(({ cols, rows }) => term.cols === cols && term.rows === rows, nativeViewport));
    await until(() => page.evaluate(() => {
      const host = document.getElementById('terminal'), screen = host.querySelector('.xterm-screen');
      return host.offsetHeight >= screen.offsetHeight && host.offsetWidth >= screen.offsetWidth;
    }));
    assert.equal(await page.locator('.terminal-area').evaluate(element => getComputedStyle(element).overflowY), 'auto');
  }
  checks.push('read-only projection preserves native pane rows with a scrollable full-size screen');
  await page.click('#control');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'You have control'));
  await page.click('[data-view="conversation"]');
  assert.equal(await page.locator('#terminal-view').isVisible(), false);
  await page.click('[data-view="terminal"]');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'Read only'));
  await page.click('#control');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'You have control'));
  checks.push('leaving terminal releases control; returning starts read-only');
  const visible = async (text, pane = planner) => {
    await until(async () => (await rpc(socketPath, 'pane.read', { pane_id: pane.pane_id, source: 'visible' })).read.text.includes(text));
  };
  const type = async text => {
    await page.locator('.xterm-helper-textarea').focus();
    await page.keyboard.insertText(text);
    await page.keyboard.press('Enter');
  };
  await type('python3 -u -c \'a=input("UI_QUESTION? "); print("UI_ANSWER:"+a)\'');
  await visible('UI_QUESTION?');
  await type('नमस्ते café');
  await visible('UI_ANSWER:नमस्ते café');
  await until(() => page.evaluate(() => Array.from({ length: term.rows }, (_, n) =>
    term.buffer.active.getLine(n)?.translateToString()).join('\n').includes('UI_ANSWER:नमस्ते café')));
  checks.push('browser ANSI rendering, interactive question, Unicode keyboard input');
  const menu = path.join(directory, 'menu.py');
  await writeFile(menu, "import os,sys,termios,tty\nfd=sys.stdin.fileno(); old=termios.tcgetattr(fd)\ntry:\n tty.setraw(fd); os.write(1,b'\\x1b[?1049h\\x1b[2J\\x1b[HUI_MENU: Down and Enter')\n data=b''\n while not data.endswith(b'\\r'): data+=os.read(fd,1)\nfinally:\n termios.tcsetattr(fd,termios.TCSADRAIN,old); os.write(1,b'\\x1b[?1049l')\nprint('UI_MENU_RESULT:'+data.hex(),flush=True)\n");
  await type(`python3 ${menu}`); await visible('UI_MENU:');
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
  await visible('UI_MENU_RESULT:1b5b420d');
  checks.push('alternate-screen keyboard menu');
  await type('python3 -u -c \'import time; print("UI_WAITING"); time.sleep(60)\'');
  await visible('\nUI_WAITING');
  await page.keyboard.press('Control+c'); await visible('KeyboardInterrupt');
  checks.push('Ctrl+C reaches the real process');
  await page.setViewportSize({ width: 1360, height: 900 });
  await until(async () => {
    const expected = await page.evaluate(() => size());
    return await page.locator('#terminal-size').textContent() === `${expected.cols} × ${expected.rows}`;
  });
  await type('printf "UI_SIZE:"; stty size');
  const dimensions = await page.locator('#terminal-size').textContent();
  const [cols, rows] = dimensions.split(' × ');
  await visible(`UI_SIZE:${rows} ${cols}`);
  checks.push('browser resize reaches PTY');
  await type('python3 -c \'print("\\n".join("UI_LINE_%03d"%i for i in range(150)))\'');
  await visible('UI_LINE_149');
  await page.click('#scroll-up');
  await until(async () => (await rpc(socketPath, 'pane.get', { pane_id: planner.pane_id })).pane.scroll.offset_from_bottom === 15);
  await page.click('#scroll-down'); await visible('UI_LINE_149');
  checks.push('server-side viewport history');
  await page.click('#paste');
  await page.fill('#paste-text', 'printf "UI_PASTE_A\\n"\nprintf "UI_PASTE_B\\n"\n');
  await page.click('#send-paste'); await visible('UI_PASTE_B');
  checks.push('explicit multiline paste preview and send');
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).click();
  await page.selectOption('#terminal-select', workerTerminal); await page.click('#bind');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'Read only'));
  await page.click('#control');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'You have control'));
  await type('printf "UI_OTHER_ROLE\\n"'); await visible('UI_OTHER_ROLE', worker);
  const plannerView = (await rpc(socketPath, 'pane.read', { pane_id: planner.pane_id, source: 'visible' })).read.text;
  assert.ok(!plannerView.includes('UI_OTHER_ROLE'));
  checks.push('role switching sends input only to selected existing terminal');
  await page.click('#control'); // Release our controller before opening a competing client.
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'Read only'));
  const competitorReady = new Promise((resolve, reject) => {
    competitor = adapter.open({ terminal: workerTerminal }, 'control', false, { cols: 90, rows: 24 }, record => {
      if (record.type === 'terminal.frame') resolve(); else if (record.type === 'terminal.closed') reject(new Error(record.reason));
    }, reject);
  });
  await competitorReady;
  await page.click('#control');
  await page.locator('#takeover').waitFor({ state: 'visible' });
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'Read only'));
  await page.click('#takeover');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'You have control'));
  checks.push('control contention falls back to observer and explicit takeover works');
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'Read only'));
  assert.equal(await page.locator('#terminal-select').inputValue(), plannerTerminal);
  await page.click('#reconnect');
  await until(() => page.locator('#stream-mode').textContent().then(t => t === 'Read only'));
  const previousSelection = await page.evaluate(() => selection);
  await page.evaluate(() => ws.close());
  await until(async () => await page.evaluate(() => selection) !== previousSelection &&
    await page.locator('#stream-mode').textContent() === 'Read only');
  assert.equal((await rpc(socketPath, 'pane.process_info', { pane_id: planner.pane_id })).process_info.shell_pid, pidBefore);
  checks.push('manual and automatic read-only reconnect preserve original shell PID');
  await page.selectOption('#document-select', 'runs/browser-spike/T02-report-01.mdx');
  await page.locator('#document-rendered').filter({ hasText: '<script>window.documentPwned=true</script>' }).waitFor();
  assert.equal(await page.locator('#document-rendered script, #document-rendered a, #document-rendered img, #document-rendered iframe').count(), 0);
  assert.equal(await page.evaluate(() => window.documentPwned), undefined);
  await page.click('#doc-source');
  await page.locator('#document-body').filter({ hasText: '<script>' }).waitFor();
  assert.equal(await page.evaluate(() => window.documentPwned), undefined);
  await page.click('#discussions-tab');
  await page.locator('#document-body').filter({ hasText: 'Keep terminal sessions alive' }).waitFor();
  assert.equal(await readFile(path.join(messages, '000001.mdx'), 'utf8'), '---\nsender: human\nsequence: 1\n---\n# Discussion\n\nKeep terminal sessions alive when switching roles.\n');
  checks.push('document markup is inert and discussion reading preserves bytes');
  await page.click('#files-tab');
  await page.locator('#document-body').filter({ hasText: 'Bring every role into focus' }).waitFor();
  await page.setViewportSize({ width: 1500, height: 960 });
  await mkdir('test-results', { recursive: true });
  await page.screenshot({ path: 'test-results/docket-ui.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/docket-ui-mobile.png', fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.click('#toggle-docs');
  assert.equal(await page.locator('.documents').isVisible(), true);
  checks.push('mobile layout has no page overflow');
  await page.setViewportSize({ width: 1500, height: 960 });
  await page.route('**/api/state?**', async route => {
    try {
      const response = await route.fetch();
      const body = await response.json();
      const orchestrator = body.runs?.[0]?.slots?.find(s => s.role === 'orchestrator');
      if (orchestrator) orchestrator.terminal_match = { terminal: workerTerminal, attribution: 'native-session' };
      await route.fulfill({ json: body });
    } catch { /* A refresh racing test teardown must not fail the run. */ }
  });
  await page.click('#refresh');
  await page.locator('#roles .role').filter({ hasText: 'Orchestrator' }).click();
  await until(() => page.locator('#terminal-select').inputValue().then(value => value === workerTerminal));
  assert.ok((await page.locator('#terminal-select option:checked').textContent()).includes('auto-matched'));
  await page.unroute('**/api/state?**');
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  checks.push('browser auto-matches a proven native session to its exact terminal');
  // The isolated shells host no agents, so send readiness is injected to exercise the
  // composer contract; transport itself is proven by the fake-executable server tests.
  let requestStub = null, stateFailure = false;
  await page.route('**/api/state?**', async route => {
    try {
      if (stateFailure) { await route.abort(); return; }
      const response = await route.fetch();
      const body = await response.json();
      for (const slot of body.runs?.[0]?.slots || []) {
        if (['planner:planner', 'implementor:worker'].includes(slot.id)) {
          slot.send_ready = true; slot.send_note = '';
        }
        if (requestStub?.[slot.id]) {
          slot.requests = { generation: slot.generation, ...structuredClone(requestStub[slot.id]) };
          if (requestStub.blocked?.includes(slot.id)) for (const terminal of body.herdr.terminals) terminal.status = 'blocked';
        }
      }
      await route.fulfill({ json: body });
    } catch { /* A refresh racing test teardown must not fail the run. */ }
  });
  await page.click('#refresh');
  await until(() => page.evaluate(() => {
    const run = state?.runs.find(r => r.id === runId);
    return run?.slots.find(s => s.id === slotId)?.send_ready === true;
  }));
  await page.click('[data-view="conversation"]');
  await page.fill('#message', 'Stubbed hello');
  assert.equal(await page.locator('#send').isDisabled(), false);
  const stubSend = status => page.route('**/api/send', async route => {
    const body = await route.request().postDataJSON();
    await route.fulfill({ json: { operation: body.operation, status,
      recipient: { run: 'browser-spike', slot: 'planner:planner' },
      ...(status === 'sent' ? {} : { reason: `Stubbed ${status}` }) } });
  });
  await stubSend('sent');
  await page.click('#send');
  await until(() => page.locator('#delivery').textContent().then(t => t.includes('Sent to')));
  assert.equal(await page.locator('#message').inputValue(), '');
  await page.unroute('**/api/send');
  await page.fill('#message', 'Stubbed kept draft');
  await stubSend('failed');
  await page.click('#send');
  await until(() => page.locator('#delivery').textContent().then(t => t.includes('Not sent')));
  assert.equal(await page.locator('#message').inputValue(), 'Stubbed kept draft');
  await page.unroute('**/api/send');
  await stubSend('uncertain');
  await page.click('#send');
  await until(() => page.locator('#delivery').textContent().then(t => t.includes('Uncertain')));
  assert.equal(await page.locator('#message').inputValue(), 'Stubbed kept draft');
  await page.unroute('**/api/send');
  checks.push('composer renders stubbed send outcomes and preserves failed and uncertain drafts');
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).click();
  await page.fill('#message', 'Race message');
  let releaseSend;
  await page.route('**/api/send', async route => {
    const body = await route.request().postDataJSON();
    await new Promise(resolve => { releaseSend = resolve; });
    await route.fulfill({ json: { operation: body.operation, status: 'sent',
      recipient: { run: 'browser-spike', slot: 'implementor:worker' } } });
  });
  await page.click('#send');
  await until(async () => typeof releaseSend === 'function');
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  releaseSend();
  await until(() => page.locator('#delivery').textContent().then(t => t.includes('implementor:worker')));
  assert.equal(await page.locator('#message').inputValue(), 'Stubbed kept draft');
  await page.unroute('**/api/send');
  checks.push('a late send acknowledgement never retargets another role or its draft');
  // Request cards: server state is stubbed (the isolated shells host no OpenCode TUI);
  // the adapter itself is proven against real HTTP and /proc in the server suites.
  const permission = { kind: 'permission', request_id: 'per_ui1', session_id: 'ses_x', title: 'bash', details: ['rm -rf build'], always: [],
    choices: [{ id: 'once', label: 'Allow once' }, { id: 'always', label: 'Always allow' }, { id: 'reject', label: 'Reject' }], revision: 'sha256:aaaa' };
  const question = { kind: 'question', request_id: 'que_ui1', session_id: 'ses_x', revision: 'sha256:bbbb', questions: [
    { header: 'DB', question: 'Which database?', multiple: false, custom: true, options: [
      { label: 'sqlite', description: 'local file' }, { label: 'postgres', description: 'server' }] }] };
  requestStub = { 'planner:planner': { state: 'live', harness: 'opencode', cards: [permission, question], reason: '' } };
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  await page.click('#refresh');
  await page.locator('#requests .request-card[data-kind="permission"]').waitFor();
  assert.equal(await page.locator('#requests .request-card').count(), 2);
  assert.deepEqual(await page.locator('#requests [data-kind="permission"] button').allTextContents(), ['Allow once', 'Always allow', 'Reject']);
  assert.equal(await page.locator('#requests [data-kind="permission"] button').first().isDisabled(), false);
  assert.ok((await page.locator('#requests [data-kind="permission"]').textContent()).includes('rm -rf build'));
  const posted = [];
  const stubReply = status => page.route('**/api/request/reply', async route => {
    const body = await route.request().postDataJSON(); posted.push(body);
    await route.fulfill({ json: { status, request: body.request, recipient: { slot: 'planner:planner' }, reason: `Stubbed ${status}` } });
  });
  await page.fill('#message', 'Draft kept while answering');
  await stubReply('uncertain');
  await page.locator('#requests [data-kind="permission"] button', { hasText: 'Allow once' }).click();
  await page.locator('#requests [data-kind="permission"] .request-outcome').waitFor();
  assert.ok((await page.locator('#requests [data-kind="permission"] .request-outcome').textContent()).includes('Uncertain'));
  assert.equal(posted.length, 1);
  assert.equal(posted[0].request, 'per_ui1'); assert.equal(posted[0].revision, 'sha256:aaaa'); assert.equal(posted[0].reply, 'once');
  assert.ok(posted[0].binding);
  assert.equal(await page.locator('#message').inputValue(), 'Draft kept while answering');
  await page.unroute('**/api/request/reply');
  await stubReply('stale');
  await page.locator('#requests [data-kind="permission"] button', { hasText: 'Reject' }).click();
  await until(() => page.locator('#requests [data-kind="permission"] .request-outcome').textContent().then(t => t.includes('Nothing was sent')));
  await page.unroute('**/api/request/reply');
  checks.push('permission cards post the pinned request id, revision and offered choice, and show uncertain and stale outcomes');
  await page.locator('#requests [data-kind="question"] input[type=radio]').nth(1).check();
  await page.fill('#requests [data-kind="question"] input[type=text]', 'duckdb');
  await page.click('#refresh');
  await page.waitForTimeout(400);
  assert.equal(await page.locator('#requests [data-kind="question"] input[type=text]').inputValue(), 'duckdb');
  assert.equal(await page.locator('#requests [data-kind="question"] input[type=radio]').nth(1).isChecked(), true);
  await stubReply('resolved');
  await page.locator('#requests [data-kind="question"] button', { hasText: 'Send answer' }).click();
  await until(async () => posted.length === 3);
  assert.deepEqual(posted[2].answers, [['duckdb']]);
  await page.unroute('**/api/request/reply');
  await stubReply('resolved');
  await page.locator('#requests [data-kind="question"] button', { hasText: 'Reject' }).click();
  await until(async () => posted.length === 4);
  assert.equal(posted[3].reject, true);
  await page.unroute('**/api/request/reply');
  checks.push('question answers survive refreshes and post offered or typed answers, or a rejection');
  stateFailure = true;
  await page.click('#refresh');
  await until(() => page.locator('#requests [data-kind="permission"] button').first().isDisabled());
  assert.equal(await page.locator('#requests [data-kind="question"] button', { hasText: 'Send answer' }).isDisabled(), true);
  stateFailure = false;
  await page.click('#refresh');
  await until(() => page.locator('#requests [data-kind="permission"] button').first().isEnabled());
  checks.push('request actions stay disabled while state cannot be reconciled and return after it does');
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).click();
  assert.equal(await page.locator('#requests .request-card').count(), 0);
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  assert.equal(await page.locator('#requests .request-card').count(), 2);
  let releaseReply;
  await page.route('**/api/request/reply', async route => {
    const body = await route.request().postDataJSON();
    await new Promise(resolve => { releaseReply = resolve; });
    await route.fulfill({ json: { status: 'resolved', request: body.request, recipient: { slot: 'planner:planner' } } });
  });
  await page.locator('#requests [data-kind="permission"] button', { hasText: 'Allow once' }).click();
  await until(async () => typeof releaseReply === 'function');
  assert.equal(await page.locator('#requests [data-kind="permission"] button').first().isDisabled(), true);
  await page.locator('#roles .role').filter({ hasText: 'Implementor' }).click();
  releaseReply();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#requests .request-card').count(), 0);
  await page.unroute('**/api/request/reply');
  checks.push('a role switch hides and never retargets another role\'s request cards, even with a reply in flight');
  requestStub = { blocked: ['planner:planner'], 'planner:planner': { state: 'endpoint-absent', harness: 'opencode', cards: [],
    reason: 'This OpenCode TUI exposes no request endpoint (it was started without --port). Answer in the live terminal.' } };
  await page.locator('#roles .role').filter({ hasText: 'Planner' }).click();
  await page.click('#refresh');
  await page.locator('#requests .request-fallback').waitFor();
  assert.equal(await page.locator('#requests .request-card').count(), 0);
  assert.ok((await page.locator('#requests .request-fallback').textContent()).includes('without --port'));
  await page.locator('#requests .request-fallback button', { hasText: 'Open terminal' }).click();
  assert.equal(await page.locator('#terminal-view').isVisible(), true);
  await page.click('[data-view="conversation"]');
  requestStub = { blocked: ['planner:planner'], 'planner:planner': { state: 'unsupported', harness: 'codex', cards: [],
    reason: 'Codex 0.160.1 delivers approval and input requests only to the client that owns the turn; no supported way to answer a TUI-owned request from another client was found. Answer in the live terminal.' } };
  await page.click('#refresh');
  await until(() => page.locator('#requests .request-fallback').textContent().then(t => t.includes('Codex 0.160.1')));
  assert.equal(await page.locator('#requests .request-card').count(), 0);
  requestStub = { blocked: ['planner:planner'], 'planner:planner': { state: 'live', harness: 'opencode', cards: [], reason: '',
    note: 'Questions and the TUI\'s own permission prompts were not observed through this endpoint, so they are not offered here. Answer them in the live terminal.' } };
  await page.click('#refresh');
  await until(() => page.locator('#requests .request-fallback').textContent().then(t => t.includes('not observed')));
  assert.equal(await page.locator('#requests .request-card').count(), 0);
  requestStub = null;
  await page.click('#refresh');
  await until(async () => (await page.locator('#requests').getAttribute('hidden')) !== null);
  checks.push('absent or unsupported endpoints show their reason and terminal fallback and never a card');
  await page.unroute('**/api/state?**');
  assert.deepEqual(pageErrors, []);
  console.log(checks.map(check => `PASS ${check}`).join('\n'));
  console.log('Screenshot: ui/test-results/docket-ui.png');
} catch (error) {
  console.error('Isolated Herdr diagnostics:', serverLog.slice(-2000));
  console.error('Completed checks:', checks);
  if (plannerPane) console.error('Disposable planner viewport:', await rpc(socketPath, 'pane.read', { pane_id: plannerPane.pane_id, source: 'visible' }).catch(() => 'unavailable'));
  if (page) {
    console.error('Browser notice:', await page.locator('#notice').textContent());
    await mkdir('test-results', { recursive: true });
    await page.screenshot({ path: 'test-results/failure.png', fullPage: true }).catch(() => {});
  }
  throw error;
} finally {
  competitor?.close();
  await browser?.close(); await app?.close();
  await execute('herdr', ['--session', 'docket-ui-poc-test', 'server', 'stop'], { env, timeout: 3000 }).catch(() => {});
  if (server.exitCode === null) {
    server.kill('SIGTERM');
    await new Promise(resolve => { const timer = setTimeout(() => { server.kill('SIGKILL'); resolve(); }, 1500); server.once('exit', () => { clearTimeout(timer); resolve(); }); });
  }
  await rm(directory, { recursive: true, force: true });
}
