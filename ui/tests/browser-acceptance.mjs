/* Product acceptance in a real browser against the real companion and an isolated Herdr
 * server with plain shells. Nothing here starts an agent or calls a provider: the send
 * transport is the real Herdr adapter running a fake executable, and request cards come
 * from a real loopback HTTP server that speaks OpenCode's observed routes. Native
 * interactive-harness acceptance is recorded separately by live-browser-acceptance.mjs. */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, chmod, readdir, utimes } from 'node:fs/promises';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import os from 'node:os';
import { chromium } from 'playwright';
import { createCompanion } from '../server.mjs';
import { Herdr, rpc } from '../herdr.mjs';
import { fakeOpenCode, permissionRequest } from './fake-opencode.mjs';
import { assertAllRolesReachable, assertNoPlaceholderText, assertFrontmatterIntact, assertTableWordsWhole, assertFitsViewport, assertNoControlOverlapsComposer, assertSingleTerminalAction } from './layout-assertions.mjs';

const execute = promisify(execFile);
const directory = await mkdtemp(path.join(os.tmpdir(), 'docket-ui-accept-'));
const projectA = path.join(directory, 'alpha');
const projectB = path.join(directory, 'beta');
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(HERDR_|DOCKET_|CODEX_|CLAUDE_|OPENCODE)/.test(key)));
Object.assign(env, { XDG_CONFIG_HOME: path.join(directory, 'config'), XDG_STATE_HOME: path.join(directory, 'state'), XDG_CACHE_HOME: path.join(directory, 'cache') });
for (const key of ['PS1', 'PS2', 'PROMPT', 'RPROMPT', 'ENV', 'BASH_ENV']) delete env[key];
const config = path.join(env.XDG_CONFIG_HOME, 'herdr');
await mkdir(config, { recursive: true });
await writeFile(path.join(config, 'config.toml'), 'onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n[update]\nversion_check = false\nmanifest_check = false\n');
const socketPath = path.join(config, 'sessions', 'docket-ui-accept', 'herdr.sock');
const server = spawn('herdr', ['--session', 'docket-ui-accept', 'server'], { env, cwd: directory });
let serverLog = ''; server.stdout.on('data', b => { serverLog += b; }); server.stderr.on('data', b => { serverLog += b; });
const checks = [];
let browser, app, page, fakeProvider;
async function until(predicate, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await predicate()) return; await new Promise(r => setTimeout(r, 70)); }
  throw new Error('Browser condition timed out');
}
const shot = async name => { await mkdir('test-results', { recursive: true }); await page.screenshot({ path: `test-results/${name}.png` }); };
const openMenu = async () => { if (!(await page.locator('#project-menu').evaluate(d => d.open))) await page.click('#project-menu > summary'); };
const tree = async dir => JSON.stringify((await readdir(dir, { recursive: true })).sort());

try {
  await until(async () => { try { await rpc(socketPath, 'ping'); return true; } catch { return false; } });
  const plan = (id, title) => `---\nrun: ${id}\nmode: standard\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, orchestrator, implementor, verifier, reviewer\nstatus: draft\n---\n\n# Plan: ${title}\n\n## Objective\n\nKeep **every** role in view, with \`inert\` context.\n\n- first item\n- second item\n\n| id | title |\n| --- | --- |\n| T01 | One |\n\n<script>window.docPwned=true</script>\n\n[link](javascript:alert(1)) ![x](http://evil.test/x.png)\n`;
  for (const [dir, id, title] of [[projectA, 'alpha-run', 'Alpha'], [projectB, 'beta-run', 'Beta']]) {
    await mkdir(path.join(dir, '.docket/runs', id), { recursive: true });
    await mkdir(path.join(dir, '.docket/sessions'), { recursive: true });
    await writeFile(path.join(dir, '.docket/runs', id, 'plan.mdx'), plan(id, title));
  }
  // A run shaped like a real long one: 16 tasks, six released implementor sessions and a
  // planner whose registration carries no native identity at all.
  const big = path.join(projectA, '.docket/runs/big-run');
  await mkdir(path.join(big, '.dispatch'), { recursive: true });
  // The real plan template's five-column table, which split headers mid-word in the narrow pane.
  await writeFile(path.join(big, 'plan.mdx'), plan('big-run', 'Big').replace('| id | title |\n| --- | --- |\n| T01 | One |',
    '| id | title | complexity | executor | constraints |\n| --- | --- | --- | --- | --- |\n'
    + Array.from({ length: 16 }, (_, i) => `| T${String(i + 1).padStart(2, '0')} | Task ${i + 1} with a longer title | high | implementor | Proven mapping, no guessed roles |`).join('\n')));
  const notes = [];
  for (let i = 1; i <= 16; i++) {
    const id = `T${String(i).padStart(2, '0')}`;
    await writeFile(path.join(big, `${id}-task.mdx`), `---\ntitle: ${id} long task\n---\n# Task\n`);
    await writeFile(path.join(big, `${id}-report-01.mdx`), `---\nstatus: ${i % 3 ? 'approved' : 'submitted'}\nround: 1\n---\n# Report\n`);
  }
  for (let i = 1; i <= 6; i++) {
    const id = `T${String(i).padStart(2, '0')}`, session = `big-earlier-${i}`, native = `big-earlier-native-${i}`;
    await writeFile(path.join(projectA, '.docket/sessions', `${session}.json`), JSON.stringify({ session, role: 'implementor', run: 'big-run', workspace: projectA, generation: 1,
      native_harness: { harness: 'claude', session_id: native, cwd: projectA } }));
    await writeFile(path.join(big, '.dispatch', `${id}.json`), JSON.stringify({ run: 'big-run', role: 'implementor', session, owner: id, state: 'released', dispatched_at: `2026-10-08T0${i}:00:00Z` }));
    notes.push({ role: 'implementor', owner: id, harness: 'claude', session_id: native, cwd: projectA });
  }
  await writeFile(path.join(big, '.harness-sessions.jsonl'), notes.map(r => JSON.stringify(r)).join('\n'));
  await writeFile(path.join(big, '.dispatch', 'T09.json'), JSON.stringify({ run: 'big-run', role: 'implementor', session: 'big-worker', owner: 'T09', state: 'dispatched', dispatched_at: '2026-10-08T09:00:00Z' }));
  await writeFile(path.join(projectA, '.docket/sessions/big-planner.json'), JSON.stringify({ session: 'big-planner', role: 'planner', run: 'big-run', workspace: projectA, generation: 1 }));
  await writeFile(path.join(projectA, '.docket/sessions/big-worker.json'), JSON.stringify({ session: 'big-worker', role: 'implementor', run: 'big-run', workspace: projectA, generation: 1,
    native_harness: { harness: 'claude', session_id: 'big-worker-native', cwd: projectA } }));
  const providerHome = path.join(directory, 'claude-fixture');
  const providerProject = path.join(providerHome, 'projects', 'alpha');
  await mkdir(providerProject, { recursive: true });
  // 95 saved events so history navigation has three pages.
  const events = [{ sessionId: 'planner-demo', cwd: projectA, type: 'user', message: { content: 'Event 1' } }];
  for (let i = 2; i <= 94; i++) events.push({ sessionId: 'planner-demo', type: i % 2 ? 'user' : 'assistant', message: { content: i % 2 ? `Event ${i}` : [{ type: 'text', text: `Event ${i}` }] } });
  events.push({ sessionId: 'planner-demo', type: 'assistant', message: { content: '**Bold plan** with `code` and [the UI](http://127.0.0.1:4319/#token=0123456789abcdef0123456789abcdef)\n\n<script>window.chatPwned=true</script>' } });
  await writeFile(path.join(providerProject, 'planner-demo.jsonl'), events.map(e => JSON.stringify(e)).join('\n') + '\n');
  const { workspace, root_pane: plannerA } = await rpc(socketPath, 'workspace.create', { cwd: projectA, label: 'Alpha' });
  const { root_pane: workerA } = await rpc(socketPath, 'tab.create', { workspace_id: workspace.workspace_id, cwd: projectA, label: 'Worker' });
  const { root_pane: plannerB } = await rpc(socketPath, 'tab.create', { workspace_id: workspace.workspace_id, cwd: projectB, label: 'Beta' });
  const shellOf = async pane => (await rpc(socketPath, 'pane.process_info', { pane_id: pane })).process_info.shell_pid;
  const identity = async pane => {
    const pid = await shellOf(pane);
    const stat = await readFile(`/proc/${pid}/stat`, 'utf8');
    return { pid, start: stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19] };
  };
  const [idPlannerA, idWorkerA, idPlannerB] = [await identity(plannerA.pane_id), await identity(workerA.pane_id), await identity(plannerB.pane_id)];
  const register = async (dir, run, session, role, native, proof) => writeFile(path.join(dir, '.docket/sessions', `${session}.json`), JSON.stringify({
    session, role, run, workspace: dir, generation: 1,
    native_harness: { harness: 'claude', session_id: native, cwd: dir }, native_process: { harness: 'claude', ...proof } }));
  await register(projectA, 'alpha-run', 'planner', 'planner', 'planner-demo', idPlannerA);
  await register(projectA, 'alpha-run', 'worker', 'implementor', 'worker-demo', idWorkerA);
  await register(projectB, 'beta-run', 'planner-b', 'planner', 'planner-beta', idPlannerB);
  // A run shaped like the one that motivated the refinement: a planner registration with no identity, its
  // real session ledgered once as the implementor, a dispatched implementor with no identity, one
  // stray and two exactly duplicated native registrations.
  const legacy = path.join(projectA, '.docket/runs/legacy-run');
  await mkdir(path.join(legacy, '.dispatch'), { recursive: true });
  await writeFile(path.join(legacy, 'plan.mdx'), '---\nrun: legacy-run\nmode: quick\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: approved\n---\n\n# Plan: Legacy shape\n');
  await writeFile(path.join(legacy, 'T01-task.mdx'), '---\ntitle: Legacy task\n---\n# Task\n');
  await writeFile(path.join(legacy, 'T01-report-01.mdx'), '---\nstatus: draft\nround: 1\n---\n# Report\n');
  const legacyRegister = (session, role, extra = {}) => writeFile(path.join(projectA, '.docket/sessions', `${session}.json`),
    JSON.stringify({ session, role, run: 'legacy-run', workspace: projectA, generation: 1, ...extra }));
  await legacyRegister('legacy-planner', 'planner');
  await legacyRegister('legacy-impl', 'implementor');
  await legacyRegister('stray-native', 'implementor', { native_harness: { harness: 'opencode', session_id: 'stray-sid', cwd: projectA }, native_process: { harness: 'opencode', pid: 424242, start: '1' } });
  for (const name of ['dup-a', 'dup-b']) await legacyRegister(name, 'implementor', { native_harness: { harness: 'opencode', session_id: 'dup-sid', cwd: projectA }, native_process: { harness: 'opencode', pid: 424243, start: '2' } });
  await writeFile(path.join(legacy, '.dispatch/T01.json'), JSON.stringify({ run: 'legacy-run', role: 'implementor', session: 'legacy-impl', owner: 'T01', state: 'dispatched', dispatched_at: '2026-10-08T10:00:00Z' }));
  await writeFile(path.join(legacy, '.harness-sessions.jsonl'), JSON.stringify({ role: 'implementor', owner: '-', harness: 'opencode', session_id: 'legacy-thread', cwd: projectA,
    worker_process: { harness: 'opencode', ...idWorkerA } }) + '\n');
  // A resumed Codex conversation: the root rollout, a shard declaring history_base, and an aborted root tail past it.
  const codexHome = path.join(directory, 'codex-fixture'), codexDay = path.join(codexHome, 'sessions/2026/10/08');
  await mkdir(codexDay, { recursive: true });
  const codexMessage = (role, kind, body, timestamp) => ({ type: 'response_item', timestamp, payload: { type: 'message', role, content: [{ type: kind, text: body }] } });
  const codexRoot = [{ type: 'session_meta', payload: { id: 'codex-thread', cwd: projectA } }, codexMessage('user', 'input_text', 'Original planning question', '2026-10-08T10:00:00Z'), codexMessage('assistant', 'output_text', 'Original planning answer', '2026-10-08T10:01:00Z')];
  const codexBase = codexRoot.map(r => JSON.stringify(r) + '\n').join('');
  await writeFile(path.join(codexDay, 'rollout-2026-10-07T13-00-00-codex-thread.jsonl'), codexBase + JSON.stringify(codexMessage('user', 'input_text', 'Aborted root tail', '2026-10-08T10:02:00Z')) + '\n');
  await writeFile(path.join(codexDay, 'rollout-2026-10-08T22-00-00-codex-thread_resume1.jsonl'), [
    { type: 'session_meta', payload: { id: 'codex-thread', cwd: projectA, history_base: { thread_id: 'codex-thread', end_ordinal_exclusive: 3, end_byte_offset: Buffer.byteLength(codexBase) } } },
    codexMessage('user', 'input_text', 'Continuation question after resume', '2026-10-09T09:00:00Z'), codexMessage('assistant', 'output_text', 'Continuation answer after resume', '2026-10-09T09:01:00Z')].map(r => JSON.stringify(r)).join('\n') + '\n');
  await mkdir(path.join(projectA, '.docket/runs/resume-run'), { recursive: true });
  await writeFile(path.join(projectA, '.docket/runs/resume-run/plan.mdx'), '---\nrun: resume-run\nmode: quick\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: approved\n---\n\n# Plan: Resumed Codex\n');
  await writeFile(path.join(projectA, '.docket/sessions/resume-planner.json'), JSON.stringify({ session: 'resume-planner', role: 'planner', run: 'resume-run', workspace: projectA, generation: 1,
    native_harness: { harness: 'codex', session_id: 'codex-thread', cwd: projectA } }));
  const panesBefore = (await rpc(socketPath, 'session.snapshot')).snapshot.panes.length;

  // The real Herdr adapter runs this executable: it records the exact prompt bytes and
  // advances state_change_seq, which is what makes the transport report an observed send.
  const calls = path.join(directory, 'calls.jsonl'), seqFile = path.join(directory, 'seq');
  await writeFile(seqFile, '1');
  const executable = path.join(directory, 'herdr-fake');
  await writeFile(executable, `#!/usr/bin/env node
const fs = require('node:fs'); const a = process.argv.slice(2);
const seq = () => parseInt(fs.readFileSync(${JSON.stringify(seqFile)}, 'utf8'), 10);
if (a[0] === 'agent' && a[1] === 'get') console.log(JSON.stringify({ result: { agent: { agent_status: 'idle', state_change_seq: seq() } } }));
else if (a[0] === 'agent' && a[1] === 'prompt') { fs.appendFileSync(${JSON.stringify(calls)}, JSON.stringify({ target: a[2], text: a[3] }) + '\\n'); fs.writeFileSync(${JSON.stringify(seqFile)}, String(seq() + 1)); console.log(JSON.stringify({ result: { type: 'agent_prompted' } })); }
else { console.error('unsupported'); process.exit(2); }
`);
  await chmod(executable, 0o700);
  const agentKind = 'opencode';
  class AcceptanceHerdr extends Herdr {
    async inventory(ws) {
      const inventory = await super.inventory(ws);
      for (const terminal of inventory.terminals) if (terminal.pane === workerA.pane_id) { terminal.agent = agentKind; terminal.status = 'idle'; }
      return inventory;
    }
  }
  const adapter = new AcceptanceHerdr(socketPath, projectA, executable);
  fakeProvider = await fakeOpenCode({ permissions: [] });
  app = await createCompanion({ workspace: projectA, socketPath, herdr: adapter, ageOf: async () => 999,
    requestListeners: async pid => (pid === idWorkerA.pid ? [{ port: fakeProvider.port, loopback: true }] : []),
    historyReader: async (ws, run, slot, generation, choice, scope) => {
      // The disposable environment has no OpenCode database, so the legacy session's saved messages are canned.
      if (choice === 'opencode:legacy-thread') return { slot, generation, session: 'legacy-thread', provider: 'opencode', partial: false, selection: choice,
        title: 'Implementor · opencode · unattributed', recorded_roles: ['implementor'],
        capabilities: { history: true, send: false, interrupt: false, questions: false, permissions: false, terminal: true },
        reason: 'Saved history of Implementor · opencode, recorded under implementor. It grants no input; use the connection above to send.',
        messages: [{ id: '1', role: 'user', text: 'Plan the legacy shape.', timestamp: '2026-10-08T10:00:00Z', truncated: false }, { id: '2', role: 'assistant', text: 'Planned **three** steps.', timestamp: '2026-10-08T10:01:00Z', truncated: false }] };
      const args = ['-B', path.resolve('inspection.py'), ws, '--history', run, slot, generation, choice || '', scope || 'run'];
      const { stdout } = await execute('python3', args, { env: { ...process.env, CLAUDE_CONFIG_DIR: providerHome, CODEX_HOME: codexHome }, maxBuffer: 8 * 1024 * 1024 });
      return JSON.parse(stdout);
    } });
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1500, height: 960 } });
  const pageErrors = []; page.on('pageerror', e => pageErrors.push(e.message));
  // Runs list newest first and open on the newest; a day ahead keeps later fixture writes behind it.
  const ahead = new Date(Date.now() + 86400000);
  await utimes(path.join(projectA, '.docket/runs/alpha-run/plan.mdx'), ahead, ahead);
  await page.goto(app.privateUrl);
  await page.locator('#run-title').filter({ hasText: 'Alpha' }).waitFor();
  assert.equal(await page.locator('#run-select option').first().getAttribute('value'), 'alpha-run');
  checks.push('runs list newest first and the newest opens by default');
  const role = name => page.locator('#roles .role').filter({ hasText: name });

  // 1. Project list: add without a restart; selection launches and writes nothing.
  assert.equal(await page.locator('#project-select option').count(), 1);
  await openMenu();
  await page.click('#add-project');
  await page.fill('#project-path-input', '/definitely/not/a/project');
  await page.click('#project-submit');
  await page.locator('#project-error').waitFor();
  assert.ok((await page.locator('#project-error').textContent()).includes('does not exist'));
  assert.equal(await page.locator('#project-error').getAttribute('role'), 'alert');
  await page.fill('#project-path-input', os.tmpdir());
  await page.click('#project-submit');
  await until(async () => (await page.locator('#project-error').textContent()).includes('No .docket'));
  const beforeA = await tree(projectA), beforeB = await tree(projectB);
  await page.fill('#project-path-input', projectB);
  await page.click('#project-submit');
  await page.locator('#run-title').filter({ hasText: 'Beta' }).waitFor();
  assert.equal(await page.locator('#project-select option').count(), 2);
  assert.equal(await page.locator('#project-select option:checked').textContent(), 'beta');
  assert.deepEqual([await tree(projectA), await tree(projectB)], [beforeA, beforeB]);
  assert.equal((await rpc(socketPath, 'session.snapshot')).snapshot.panes.length, panesBefore);
  assert.equal((await rpc(socketPath, 'session.snapshot')).snapshot.agents.length, 0);
  assert.equal(await page.locator('#remove-project').isDisabled(), false);
  checks.push('a project is added and selected without restarting, creating no run, agent or file');

  // 2. A pending send keeps its project when another one is selected.
  await page.selectOption('#project-select', { label: 'alpha' });
  await page.locator('#run-title').filter({ hasText: 'Alpha' }).waitFor();
  await role('Implementor').click();
  await until(() => page.locator('#binding-label').textContent().then(t => t.includes('auto-matched')));
  await until(() => page.locator('#send').isDisabled().then(d => !d || true));
  await page.fill('#message', 'Held alpha message');
  await until(async () => await page.locator('#send').isEnabled());
  let release;
  await page.route('**/api/send', async route => {
    const request = route.request();
    await new Promise(resolve => { release = resolve; });
    await route.continue({ postData: request.postData() });
  });
  await page.click('#send');
  await until(async () => typeof release === 'function');
  await page.selectOption('#project-select', { label: 'beta' });
  await page.locator('#run-title').filter({ hasText: 'Beta' }).waitFor();
  await page.fill('#message', 'Beta draft stays');
  release();
  await until(async () => (await readFile(calls, 'utf8').catch(() => '')).includes('Held alpha message'));
  await page.unroute('**/api/send');
  const delivered = (await readFile(calls, 'utf8')).trim().split('\n').map(l => JSON.parse(l));
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].target, workerA.pane_id);
  assert.equal(await page.locator('#message').inputValue(), 'Beta draft stays');
  checks.push('selecting another project never retargets a pending send or touches that project\'s draft');

  // 3. Real send transport: exact Unicode and multiline bytes through the real adapter.
  await page.selectOption('#project-select', { label: 'alpha' });
  await page.locator('#run-title').filter({ hasText: 'Alpha' }).waitFor();
  await role('Implementor').click();
  await until(() => page.evaluate(() => !!activeBinding));
  const text = 'नमस्ते café αβ\nsecond line\n\n  indented ```fence```';
  await page.fill('#message', text);
  await until(async () => await page.locator('#send').isEnabled());
  await page.click('#send');
  await until(async () => (await readFile(calls, 'utf8').catch(() => '')).split('\n').filter(Boolean).length === 2);
  await until(async () => (await page.locator('#message').inputValue()) === '');
  const all = (await readFile(calls, 'utf8')).trim().split('\n').map(l => JSON.parse(l));
  assert.equal(all.at(-1).text, text);
  assert.equal(await page.locator('#message').inputValue(), '');
  assert.equal(await page.locator('#project-select option:checked').textContent(), 'alpha');
  checks.push('real send transport delivers exact Unicode and multiline bytes through the Herdr adapter');

  // 4. Request lifecycle: a real provider-held request becomes a card, is answered once, and leaves.
  fakeProvider.state.permissions = [permissionRequest('per_a1', 'worker-demo')];
  await page.click('#refresh');
  await page.locator('#requests .request-card[data-request="per_a1"]').waitFor();
  assert.equal(await page.locator('#requests .request-card').getAttribute('role'), 'group');
  const snapshot = await page.locator('#requests').ariaSnapshot();
  assert.match(snapshot, /group "Permission request: external_directory"/);
  assert.match(snapshot, /button "Allow once"/);
  await page.locator('#requests button', { hasText: 'Allow once' }).click();
  await page.locator('#requests .request-card').waitFor({ state: 'detached' });
  assert.equal(fakeProvider.state.posts.length, 1);
  assert.deepEqual(fakeProvider.state.posts[0].body, { reply: 'once' });
  assert.equal(fakeProvider.state.posts[0].route, '/api/session/worker-demo/permission/per_a1/reply');
  checks.push('a provider-held request shows as an accessible card, resolves once, and disappears');

  // 5. Bounded history navigation.
  await role('Planner').click();
  await page.locator('#history-pager').waitFor();
  await until(() => page.locator('#history-range').textContent().then(t => t.includes('of 95')));
  assert.equal(await page.locator('#history-range').textContent(), 'Events 56-95 of 95');
  assert.equal(await page.locator('#history-newer').isDisabled(), true);
  await page.click('#history-older');
  assert.equal(await page.locator('#history-range').textContent(), 'Events 16-55 of 95');
  assert.ok((await page.locator('#conversation').textContent()).includes('Event 16'));
  assert.ok(!(await page.locator('#conversation').textContent()).includes('Event 56'));
  await page.click('#history-older');
  assert.equal(await page.locator('#history-range').textContent(), 'Events 1-15 of 95');
  assert.equal(await page.locator('#history-older').isDisabled(), true);
  assert.notEqual(await page.evaluate(() => document.activeElement.tagName), 'BODY');
  await page.click('#history-latest');
  assert.equal(await page.locator('#history-range').textContent(), 'Events 56-95 of 95');
  checks.push('history navigates a bounded window in pages with an honest count and kept focus');

  // 6. Loading, empty and unavailable states.
  await page.route('**/api/history?**', async route => { await new Promise(r => setTimeout(r, 600)); await route.fulfill({ status: 400, json: { error: 'Provider history is unavailable right now.' } }); });
  await role('Implementor').click();
  await role('Orchestrator').click();
  await until(() => page.locator('#conversation').textContent().then(t => t.includes('Loading this conversation')));
  const loadingText = await page.locator('#conversation').textContent();
  assert.ok(!/Select a role|No saved messages|No readable conversation/.test(loadingText), `loading shows empty-state copy: ${loadingText}`);
  await role('Planner').click();
  await until(() => page.locator('#conversation').getAttribute('aria-busy').then(v => v === 'true' || v === 'false'));
  await until(() => page.locator('#history-note').textContent().then(t => t.includes('Provider history is unavailable')));
  assert.ok((await page.locator('#history-state').textContent()).includes('unavailable') || (await page.locator('#history-state').textContent()).length > 0);
  await page.unroute('**/api/history?**');
  checks.push('history shows loading and then a specific unavailable reason');

  // 7. Inert Markdown context.
  await page.locator('#document-rendered').filter({ hasText: 'Keep' }).waitFor().catch(() => {});
  await until(() => page.locator('#document-rendered h2').count().then(n => n > 0));
  assert.equal(await page.locator('#document-rendered ul li').count(), 2);
  assert.equal(await page.locator('#document-rendered table th').count(), 2);
  assert.equal(await page.locator('#document-rendered script, #document-rendered a, #document-rendered img, #document-rendered iframe').count(), 0);
  assert.ok((await page.locator('#document-rendered').textContent()).includes('link (javascript:alert(1))'));
  assert.equal(await page.evaluate(() => window.docPwned), undefined);
  await page.locator('#doc-source').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#document-body').isVisible(), true);
  assert.ok((await page.locator('#document-body').textContent()).startsWith('---\nrun: alpha-run'));
  await page.locator('#doc-rendered').focus();
  await page.keyboard.press('Space');
  assert.equal(await page.locator('#document-rendered').isVisible(), true);
  checks.push('Markdown context renders inert and keyboard-toggles to exact source');

  // 8. Keyboard, focus, contrast, narrow viewport, request and composer accessibility.
  await page.locator('#roles .role').first().focus();
  const tabsList = page.locator('.view-tabs');
  const stops = await tabsList.locator('[role=tab]').evaluateAll(tabs => tabs.filter(t => t.tabIndex === 0).length);
  assert.equal(stops, 1);
  await page.locator('.view-tabs [aria-selected="true"]').focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('.view-tabs [data-view="activity"]').getAttribute('aria-selected'), 'true');
  await page.keyboard.press('ArrowLeft');
  assert.equal(await page.locator('.view-tabs [data-view="conversation"]').getAttribute('aria-selected'), 'true');
  const reached = new Set();
  await page.evaluate(() => document.activeElement?.blur());
  for (let i = 0; i < 60; i++) { await page.keyboard.press('Tab'); reached.add(await page.evaluate(() => document.activeElement.id || document.activeElement.className || document.activeElement.tagName)); }
  for (const id of ['refresh', 'project-select', 'run-select', 'message']) assert.ok(reached.has(id), `Tab reaches ${id}`);
  for (const selector of ['#project-select', '#project-menu > summary', '#message', '#refresh', '.view-tabs [aria-selected="true"]']) {
    await page.locator(selector).focus();
    await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Tab');
    const outline = await page.evaluate(() => { const s = getComputedStyle(document.activeElement); return { style: s.outlineStyle, width: parseFloat(s.outlineWidth) }; });
    assert.ok(outline.style !== 'none' && outline.width >= 2, `visible focus ring on ${selector}: ${JSON.stringify(outline)}`);
  }
  fakeProvider.state.permissions = [permissionRequest('per_a2', 'worker-demo')];
  await role('Implementor').click();
  await page.click('#refresh');
  await page.locator('#requests .request-card').waitFor();
  const contrast = await page.evaluate(() => {
    const rgb = value => value.match(/[\d.]+/g)?.slice(0, 3).map(Number);
    const lum = c => c.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    const failures = [];
    for (const selector of ['#project-select', '#project-path', '.request-card', '.request-card h3', '.request-kind', '.request-actions button', '#history-range', '#history-note', '#document-rendered', '#document-rendered code', '#recipient-hint', '#message', '.role-text small', '#notice']) {
      const element = document.querySelector(selector);
      if (!element) continue;
      const style = getComputedStyle(element);
      let parent = element, background = [255, 255, 255];
      while (parent) { const c = getComputedStyle(parent).backgroundColor; if (c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') { background = rgb(c); break; } parent = parent.parentElement; }
      const a = lum(rgb(style.color)), b = lum(background), ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
      if (ratio < 4.5) failures.push(`${selector} ${ratio.toFixed(2)}`);
    }
    return failures;
  });
  assert.deepEqual(contrast, []);
  assert.equal(await page.locator('#composer-label').getAttribute('for') ?? await page.locator('#composer-label').evaluate(l => l.htmlFor), 'message');
  assert.equal(await page.locator('#message').getAttribute('aria-describedby'), 'recipient-hint');
  assert.equal(await page.locator('#delivery').getAttribute('role'), 'status');
  assert.equal(await page.locator('#requests').getAttribute('aria-live'), 'polite');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no horizontal overflow with a request card');
  await openMenu();
  await page.click('#add-project');
  assert.ok(await page.evaluate(() => { const r = document.getElementById('project-dialog').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth; }));
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#project-dialog').evaluate(d => d.open), false);
  await page.setViewportSize({ width: 1500, height: 960 });
  fakeProvider.state.permissions = [];
  checks.push('keyboard, focus rings, measured contrast, narrow layout and request and composer names pass');

  // 9. A long, real-shaped run keeps every role reachable and prints no placeholders.
  await page.selectOption('#run-select', 'big-run');
  await page.locator('#run-title').filter({ hasText: 'Big' }).waitFor();
  await page.locator('#tasks .task').nth(15).waitFor({ state: 'attached' });
  await page.locator('[data-history-role="implementor"]').click();
  const rowsWide = await assertAllRolesReachable(page, '1500x960', 5);
  await role('Planner').click();
  await assertNoPlaceholderText(page, 'identity-less planner');
  assert.equal(await page.locator('#session-subtitle').textContent(), 'big-planner · Needs link · no recorded session');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  const rowsNarrow = await assertAllRolesReachable(page, '390x844', 5);
  await page.setViewportSize({ width: 1500, height: 960 });
  assert.equal(rowsWide, rowsNarrow);
  await page.selectOption('#document-select', { index: 0 });
  await page.click('#doc-rendered');
  await assertFrontmatterIntact(page, 'narrow context pane');
  await assertTableWordsWhole(page, 'narrow context pane');
  await page.setViewportSize({ width: 1100, height: 900 });
  await assertFrontmatterIntact(page, '1100px');
  await page.setViewportSize({ width: 1500, height: 960 });
  checks.push('a 16-task run with six earlier sessions keeps every role reachable at 1500x960 and 390px, shows no placeholder text, and keeps frontmatter whole');

  // The Codex continuation renders through the real history endpoint; nothing is routed or injected.
  await page.selectOption('#run-select', 'resume-run');
  await page.locator('#run-title').filter({ hasText: 'Resumed Codex' }).waitFor();
  await role('Planner').click();
  await until(() => page.locator('#conversation').textContent().then(t => t.includes('Continuation answer after resume')), 20000);
  const resumed = await page.locator('#conversation').textContent();
  assert.ok(resumed.includes('Original planning question') && resumed.includes('Continuation question after resume'));
  assert.ok(resumed.indexOf('Original planning answer') < resumed.indexOf('Continuation question after resume'), 'chronological order');
  assert.ok(!resumed.includes('Aborted root tail'), 'root bytes past history_base are not shown');
  assert.equal(await page.locator('#send').isDisabled(), true, 'saved history never enables Send');
  checks.push('a resumed Codex conversation shows its continuation, in order, through the real history endpoint with Send disabled');

  // Switching runs never shows the previous document's text under the new document's name.
  await page.selectOption('#run-select', 'alpha-run');
  await page.locator('#document-path').filter({ hasText: 'alpha-run/plan.mdx' }).waitFor();
  await until(() => page.locator('#document-rendered').textContent().then(t => t.includes('Alpha')));
  await page.route('**/api/document?**', async route => { await new Promise(r => setTimeout(r, 900)); await route.continue().catch(() => {}); });
  await page.selectOption('#run-select', 'big-run');
  await page.locator('#document-path').filter({ hasText: 'big-run/plan.mdx' }).waitFor();
  const interim = await page.locator('#document-rendered').textContent() + await page.locator('#document-body').textContent();
  assert.ok(!interim.includes('Alpha'), `the old document's text is still shown: ${interim.slice(0, 80)}`);
  assert.ok(interim.includes('Loading document'));
  await page.unroute('**/api/document?**');
  await until(() => page.locator('#document-rendered').textContent().then(t => t.includes('Big')));
  checks.push('switching runs shows a loading state instead of the previous document');

  // Selecting a role, polling and re-rendering ask for the same history; one read serves them all.
  let historyReads = 0;
  await page.route('**/api/history?**', async route => {
    if (new URL(route.request().url()).searchParams.get('slot')?.startsWith('reviewer')) historyReads++;
    await new Promise(r => setTimeout(r, 1200)); await route.continue().catch(() => {});
  });
  await role('Reviewer').click();
  for (let i = 0; i < 3; i++) { await page.click('#refresh'); await page.waitForTimeout(200); }
  await until(() => page.locator('#history-note').textContent().then(t => !t.includes('Reading recorded')), 20000);
  assert.equal(historyReads, 1, `history was read ${historyReads} times for one selection`);
  await page.unroute('**/api/history?**');
  checks.push('selecting a role and refreshing while its history loads reads that history once');

  // A network change aborts one in-flight read: it is retried quietly. A lasting outage shows
  // its notice and clears it by itself once state can be read again.
  let aborts = 1;
  await page.route('**/api/state?**', async route => { if (aborts-- > 0) await route.abort('connectionreset'); else await route.continue(); });
  await page.click('#refresh');
  await page.waitForTimeout(900);
  assert.equal(await page.locator('#notice').isHidden(), true, 'one aborted read is retried without a notice');
  aborts = 3;
  await page.click('#refresh');
  await until(() => page.locator('#notice').isVisible());
  assert.ok((await page.locator('#notice').textContent()).length > 0);
  await page.click('#refresh');
  await until(() => page.locator('#notice').isHidden());
  await page.unroute('**/api/state?**');
  checks.push('an aborted read is retried quietly and an outage notice clears itself on recovery');

  // 11. Role-first recovery on the real-shaped legacy run: filtering, collapse, exact link, send.
  await page.selectOption('#run-select', 'legacy-run');
  await page.locator('#run-title').filter({ hasText: 'Legacy shape' }).waitFor();
  await until(() => page.locator('#roles .role').count().then(n => n >= 3));
  assert.deepEqual(await page.locator('#roles > .role').evaluateAll(buttons => buttons.map(b => b.querySelector('strong').textContent)), ['Planner', 'Implementor', 'Reviewer']);
  const implToggle = page.locator('[data-history-role="implementor"]');
  assert.equal(await implToggle.count(), 1, 'stray and duplicate registrations collapse into one group');
  assert.match(await implToggle.textContent(), /Unattributed \(3\)/, 'the exact duplicates merge into one; with the stray and the misrecorded planner session that is three');
  assert.equal(await role('Implementor').first().locator('small').textContent(), 'Needs link · task T01');
  assert.ok(!(await page.locator('#roles').textContent()).includes('opencode-'), 'no native IDs in the role list');
  await assertNoPlaceholderText(page, 'legacy run');
  checks.push('one current implementor, duplicates merged, stray registrations collapsed, no UUIDs in labels');

  await role('Planner').click();
  assert.equal(await page.locator('#connection-state').textContent(), 'Needs link');
  assert.match(await page.locator('#connection-reason').textContent(), /no recorded session/);
  assert.equal(await page.locator('#connection-action').textContent(), 'Link a session');
  assert.equal(await page.locator('#message').isEnabled(), true, 'a current role always accepts a draft');
  assert.equal(await page.locator('#recipient-hint').textContent(), 'Chat is off: no linked session.');
  assert.match(await page.locator('#connection-reason').textContent(), /Link a saved session to enable chat/);
  assert.match(await page.locator('#connection-reason').textContent(), /which terminal it is/);
  assert.notEqual(await page.locator('#recipient-hint').textContent(), await page.locator('#connection-reason').textContent(), 'the reason is not repeated word for word');
  await assertSingleTerminalAction(page, 'needs-link');
  assert.equal(await page.locator('#send').isDisabled(), true);
  assert.equal(await page.locator('#stop').isHidden(), true, 'unsupported Stop is hidden');
  await page.fill('#message', 'Typed before linking');
  await until(() => page.locator('#conversation').textContent().then(t => !t.includes('Loading this conversation')));
  await until(() => page.locator('#document-rendered h2, #document-rendered p').count().then(n => n > 0));
  await assertNoControlOverlapsComposer(page, 'legacy planner at 1500');
  await shot('ux-needs-link-desktop');
  await page.setViewportSize({ width: 390, height: 844 });
  await assertFitsViewport(page, 'legacy planner at 390');
  await assertNoControlOverlapsComposer(page, 'legacy planner at 390');
  await assertSingleTerminalAction(page, 'legacy planner at 390');
  await shot('ux-needs-link-narrow');
  // The assertion has teeth: a composer pinned over the page is caught.
  await page.addStyleTag({ content: '#composer { position: fixed !important; left: 0; right: 0; top: 150px; background: white; z-index: 50; }' });
  await assert.rejects(assertNoControlOverlapsComposer(page, 'pinned composer'), /controls under the composer/);
  await page.reload();
  await until(() => page.locator('#roles .role.selected').count().then(n => n === 1));
  assert.equal(await page.locator('#run-select').inputValue(), 'legacy-run', 'a reload keeps the selected run');
  assert.equal(await page.locator('#crumb-role').textContent(), 'Planner', 'a reload keeps the selected role');
  checks.push('a reload keeps the selected run and role');
  await role('Planner').focus();
  await page.evaluate(() => refresh(true));
  assert.equal(await page.evaluate(() => document.activeElement?.closest('#roles .role')?.textContent.includes('Planner')), true,
    'a poll keeps keyboard focus on the same role');
  checks.push('a poll that rebuilds the role list keeps keyboard focus');
  await page.selectOption('#run-select', 'legacy-run');
  await role('Planner').click();
  await page.fill('#message', 'Typed before linking');
  await page.setViewportSize({ width: 1500, height: 960 });
  await page.setViewportSize({ width: 1500, height: 960 });
  // Choices are this run's sessions only; the wider project scope is an explicit step.
  await page.locator('#session-details > summary').click();
  await until(() => page.locator('#history-select option').count().then(n => n >= 2));
  const runOptions = await page.locator('#history-select option').evaluateAll(options => options.map(o => o.textContent));
  assert.ok(runOptions.every(text => !/planner-demo|worker-demo/.test(text)), `run scope leaked other runs: ${runOptions}`);
  assert.ok(runOptions.some(text => text.startsWith('Implementor · opencode')), runOptions.join(' | '));
  await page.check('#history-scope');
  await until(() => page.locator('#history-select option').evaluateAll(options => options.some(o => o.textContent.includes('other run'))));
  await page.uncheck('#history-scope');
  await page.locator('#session-details > summary').click();
  checks.push('the picker is run-scoped, role-first and offers the project scope only on request');

  const savedPaneCalls = (await readFile(calls, 'utf8')).trim().split('\n').length;
  await page.click('#connection-action');
  await page.locator('#link-dialog[open]').waitFor();
  await until(() => page.locator('#link-select option').first().textContent().then(t => !t.startsWith('Loading')));
  const linkLabels = await page.locator('#link-select option').evaluateAll(options => options.map(o => o.textContent));
  assert.equal(new Set(linkLabels).size, linkLabels.length, `link choices are told apart: ${linkLabels}`);
  assert.ok(linkLabels.every(label => /^Implementor · opencode/.test(label)), linkLabels.join(' | '));
  await page.selectOption('#link-select', 'opencode:legacy-thread');
  assert.match(await page.locator('#link-detail').textContent(), /opencode legacy-thread · process \d+:\d+ · recorded: implementor/);
  assert.equal(await page.locator('#link-submit').isDisabled(), true, 'linking needs the explicit confirmation');
  await page.check('#link-confirm');
  await page.click('#link-submit');
  await until(() => page.locator('#connection').isHidden());
  assert.match(await page.locator('#session-subtitle').textContent(), /linked by you/);
  await until(() => page.locator('#conversation .message-body').filter({ hasText: 'Planned three steps' }).count().then(n => n === 1));
  await until(() => page.locator('#send').isEnabled());
  await until(() => page.locator('#recipient-hint').textContent().then(t => /Ctrl\+Enter/.test(t)));
  await assertNoControlOverlapsComposer(page, 'linked planner');
  await shot('ux-linked-desktop');
  assert.equal(await page.locator('#message').inputValue(), 'Typed before linking', 'the draft survives the link');
  assert.equal(await page.locator('#replaced-note').isHidden(), true, 'a companion-only link is not a replaced session');
  await until(() => page.locator('#send').isEnabled());
  assert.deepEqual(await readFile(calls, 'utf8').then(t => t.trim().split('\n').length), savedPaneCalls, 'linking sends nothing');
  await page.locator('#session-details > summary').click();
  const details = await page.locator('#details-list').textContent();
  assert.ok(details.includes('Recorded roles') && details.includes('implementor') && details.includes('linked by you'), 'the historical role stays visible in Details');
  assert.ok(details.includes('opencode legacy-thread'), 'Details keep the exact identity');
  await page.locator('#session-details > summary').click();
  await page.click('#send');
  await until(async () => (await readFile(calls, 'utf8')).includes('Typed before linking'));
  const lastCall = (await readFile(calls, 'utf8')).trim().split('\n').map(l => JSON.parse(l)).at(-1);
  assert.deepEqual([lastCall.target, lastCall.text], [workerA.pane_id, 'Typed before linking']);
  checks.push('an exact confirmed link enables send, keeps the draft, and delivers to the proven pane without launching anything');

  // The link is a preference re-proved on every read; a moved registration invalidates it and keeps the draft with its role.
  await page.reload();
  await page.selectOption('#run-select', 'legacy-run');
  await role('Planner').click();
  await until(() => page.locator('#session-subtitle').textContent().then(t => /linked by you/.test(t)));
  await page.fill('#message', 'Held across replacement');
  const plannerRegistration = path.join(projectA, '.docket/sessions/legacy-planner.json');
  await writeFile(plannerRegistration, JSON.stringify({ session: 'legacy-planner', role: 'planner', run: 'legacy-run', workspace: projectA, generation: 2 }));
  await page.click('#refresh');
  await until(() => page.locator('#connection-state').textContent().then(t => t === 'Needs link'));
  assert.match(await page.locator('#connection-reason').textContent(), /changed since you linked it/);
  assert.equal(await page.locator('#send').isDisabled(), true);
  checks.push('a stored link is re-proved on reload and an invalidated link grants nothing');

  // Reading mode, collapsed properties and the narrow overlay.
  await page.selectOption('#run-select', 'alpha-run');
  await role('Planner').click();
  await until(() => page.locator('#conversation .message-body strong').filter({ hasText: 'Bold plan' }).count().then(n => n === 1));
  assert.equal(await page.locator('#conversation .message-body a, #conversation .message-body script').count(), 0);
  assert.ok((await page.locator('#conversation').textContent()).includes('the UI (http://127.0.0.1:4319/#token=••••)'), 'chat shows the label and URL with the token hidden');
  assert.equal(await page.evaluate(() => window.chatPwned), undefined);
  await assertFitsViewport(page, 'desktop 1500');
  await page.setViewportSize({ width: 1500, height: 960 });
  const before = await page.locator('#document-rendered').boundingBox();
  await page.click('#doc-reading');
  await until(() => page.locator('.workspace.reading').count().then(n => n === 1));
  await page.locator('#document-rendered').waitFor({ state: 'visible' });
  const reading = await page.locator('#document-rendered').boundingBox();
  assert.ok(reading.width > before.width + 150 && reading.width <= 76 * 12.5 + 40, `reading width ${reading.width} vs side panel ${before.width}`);
  assert.equal(await page.locator('.conversation-panel').isHidden(), true);
  await shot('ux-reading-mode');
  await page.keyboard.press('Escape');
  await until(() => page.locator('.workspace.reading').count().then(n => n === 0));
  await page.click('#toggle-docs');
  assert.equal(await page.locator('#documents').isHidden(), true, 'documents are optional');
  await page.reload();
  await page.selectOption('#run-select', 'alpha-run');
  assert.equal(await page.locator('#documents').isHidden(), true, 'the choice is remembered');
  await page.click('#toggle-docs');
  await page.setViewportSize({ width: 390, height: 844 });
  await assertFitsViewport(page, 'narrow 390');
  await page.waitForTimeout(250);
  assert.equal(await page.locator('#documents').isHidden(), true, 'a narrow screen starts without the overlay');
  await page.click('#toggle-docs');
  const overlay = await page.locator('#documents').boundingBox();
  assert.ok(overlay.x === 0 && overlay.width >= 389, `overlay covers the screen: ${JSON.stringify(overlay)}`);
  await shot('ux-docs-narrow');
  await assertFitsViewport(page, 'narrow documents overlay');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#documents').isHidden(), true);
  await page.setViewportSize({ width: 1500, height: 960 });
  checks.push('chat Markdown is inert with the token hidden; documents are optional, remembered, expand to a readable measure and overlay on narrow screens');

  // 10. Role isolation and the ordinary shell survive all of the above.
  assert.equal(await shellOf(plannerA.pane_id), idPlannerA.pid);
  assert.equal(await shellOf(workerA.pane_id), idWorkerA.pid);
  assert.deepEqual(pageErrors, []);
  checks.push('ordinary shell processes are unchanged after project switching, sends and requests');
  console.log(checks.map(c => `PASS ${c}`).join('\n'));
} catch (error) {
  console.error('Isolated Herdr diagnostics:', serverLog.slice(-1500));
  console.error('Completed checks:', checks);
  if (fakeProvider) console.error('Provider posts:', JSON.stringify(fakeProvider.state.posts), 'pending:', JSON.stringify(fakeProvider.state.permissions.map(p => p.id)));
  if (page) console.error('Request area:', await page.locator('#requests').textContent().catch(() => '?'));
  if (page) { console.error('Browser notice:', await page.locator('#notice').textContent().catch(() => '?'), '| delivery:', await page.locator('#delivery').textContent().catch(() => '?'), '| hint:', await page.locator('#recipient-hint').textContent().catch(() => '?')); await mkdir('test-results', { recursive: true }); await page.screenshot({ path: 'test-results/acceptance-failure.png', fullPage: true }).catch(() => {}); }
  throw error;
} finally {
  await browser?.close(); await app?.close(); await fakeProvider?.close();
  await execute('herdr', ['--session', 'docket-ui-accept', 'server', 'stop'], { env, timeout: 3000 }).catch(() => {});
  if (server.exitCode === null) {
    server.kill('SIGTERM');
    await new Promise(resolve => { const timer = setTimeout(() => { server.kill('SIGKILL'); resolve(); }, 1500); server.once('exit', () => { clearTimeout(timer); resolve(); }); });
  }
  await rm(directory, { recursive: true, force: true });
}
