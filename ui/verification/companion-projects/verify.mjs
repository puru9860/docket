#!/usr/bin/env node
// Real browser journeys against private companion processes. No provider or
// Herdr process is started, and no app route or persistence method is replaced.
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile, readdir, lstat, realpath, rm, chmod, access, rename } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs, promisify } from 'node:util';

const execute = promisify(execFile);
const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const checkout = path.dirname(ui);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const limits = ['Project-list and run-list journey only; Herdr is intentionally offline.',
  'No live-agent, terminal, send, provider model, question or permission coverage.',
  'Project membership persists; the last selected project is not promised across restart.'];

class PrerequisiteError extends Error {}
class OutcomeError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

async function outputDirectory(input) {
  if (!input || !path.isAbsolute(input)) throw new Error('--evidence-dir must name an explicit absolute directory outside measured source.');
  const dir = path.resolve(input);
  // Refuse symlinks in every existing component, including the final directory.
  for (let cursor = dir; ; cursor = path.dirname(cursor)) {
    try { if ((await lstat(cursor)).isSymbolicLink()) throw new Error('Evidence paths must not traverse symlinks.'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (path.dirname(cursor) === cursor) break;
  }
  const repo = await realpath(checkout);
  const docketState = path.join(repo, '.docket');
  if ((dir === repo || dir.startsWith(`${repo}${path.sep}`)) && !dir.startsWith(`${docketState}${path.sep}`)) {
    throw new Error('Evidence must be outside measured source: use private .docket state or a directory outside the checkout.');
  }
  await mkdir(dir, { recursive: true, mode: 0o700 });
  if ((await readdir(dir)).length) throw new Error('Evidence directory must be empty; use a fresh directory for each invocation.');
  await chmod(dir, 0o700);
  return dir;
}

async function treeDigest(dir) {
  const entries = [];
  async function walk(current) {
    for (const entry of (await readdir(current, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(current, entry.name), relative = path.relative(dir, full);
      if (entry.isDirectory()) { entries.push([relative, 'directory']); await walk(full); }
      else entries.push([relative, hash(await readFile(full))]);
    }
  }
  await walk(dir);
  return hash(JSON.stringify(entries));
}

async function fixture(root, name, run, title) {
  const dir = path.join(root, 'projects', name);
  await mkdir(path.join(dir, '.docket', 'runs', run), { recursive: true });
  await writeFile(path.join(dir, '.docket', 'runs', run, 'plan.mdx'),
    `---\nrun: ${run}\nmode: quick\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: draft\n---\n\n# Plan: ${title}\n\n## Objective\n\nDisposable recipe fixture. No agent sessions.\n`);
  return dir;
}

function privateEnvironment(root) {
  // Only runtime necessities are inherited. Credentials, harness variables and
  // provider directories never enter the server or browser environment.
  const env = Object.fromEntries(['PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR']
    .filter(key => process.env[key]).map(key => [key, process.env[key]]));
  return Object.assign(env, {
    HOME: path.join(root, 'home'), XDG_CONFIG_HOME: path.join(root, 'config'),
    XDG_STATE_HOME: path.join(root, 'state'), XDG_DATA_HOME: path.join(root, 'data'),
    XDG_CACHE_HOME: path.join(root, 'cache'), TMPDIR: path.join(root, 'tmp'),
    CODEX_HOME: path.join(root, 'providers/codex'), CLAUDE_CONFIG_DIR: path.join(root, 'providers/claude'),
    PYTHONDONTWRITEBYTECODE: '1', PYTHONPYCACHEPREFIX: path.join(root, 'pycache')
  });
}

async function startServer(root, workspace, env, doctor, servers) {
  const child = spawn(process.execPath, [path.join(ui, 'server.mjs'), '--workspace', workspace,
    '--port', '0', '--socket', path.join(root, 'no-herdr.sock')], { cwd: ui, env, stdio: ['ignore', 'pipe', 'pipe'] });
  const record = { pid: child.pid, ready: false, stopped: false };
  const server = { child, record, log: '', error: null };
  servers.push(server); doctor.instances.push(record);
  child.on('error', error => { server.error = error; });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', bytes => { server.log = (server.log + bytes).slice(-16000); });
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (server.error || child.exitCode !== null) throw new Error(`Private companion failed to start: ${server.error?.message || server.log}`);
    const url = server.log.match(/http:\/\/127\.0\.0\.1:(\d+)\/#token=([a-f0-9]{64})/);
    if (url) {
      const response = await fetch(`http://127.0.0.1:${url[1]}/`, { signal: AbortSignal.timeout(3000) });
      assert.equal(response.status, 200, 'Private companion root must be ready');
      Object.assign(record, { ready: true, port: Number(url[1]), root_http_status: response.status });
      server.privateUrl = url[0];
      return server;
    }
    await delay(40);
  }
  throw new Error('Private companion did not print its startup identity within 15 seconds.');
}

async function stopServer(server) {
  const { child, record } = server;
  if (record.stopped) return;
  if (child.pid && child.exitCode === null && child.signalCode === null) {
    child.kill('SIGTERM');
    const deadline = Date.now() + 5000;
    while (child.exitCode === null && child.signalCode === null && Date.now() < deadline) await delay(40);
    if (child.exitCode === null && child.signalCode === null) {
      record.forced_kill = true;
      child.kill('SIGKILL');
      const killed = Date.now() + 3000;
      while (child.exitCode === null && child.signalCode === null && Date.now() < killed) await delay(40);
    }
  }
  record.exit_code = child.exitCode; record.signal = child.signalCode;
  record.stopped = child.exitCode !== null || child.signalCode !== null || !!server.error;
  if (!record.stopped) throw new Error('Private companion did not exit during cleanup.');
}

async function addProject(page, directory, title) {
  if (!(await page.locator('#project-menu').evaluate(menu => menu.open))) await page.click('#project-menu > summary');
  await page.click('#add-project');
  await page.fill('#project-path-input', directory);
  await page.click('#project-submit');
  await page.locator('#run-title').filter({ hasText: title }).waitFor();
  assert.equal(await page.locator('#project-select option:checked').textContent(), path.basename(directory));
}

async function run(values, evidenceDir) {
  const invocation = randomUUID(), started = new Date().toISOString();
  const doctor = { version: 1, invocation, scenario: values.scenario, started_at: started, runtime: { node: process.version,
    platform: process.platform, arch: process.arch }, build: {}, prerequisites: [], instances: [], limits };
  const result = { version: 1, invocation, scenario: values.scenario, started_at: started, status: 'failed', phase: 'prerequisites',
    negative_control: values['negative-control'] || null, checks: [], cleanup: {} };
  const servers = [];
  let root, browser, page, env;
  const check = (name, observation) => result.checks.push({ name, passed: true, observation });
  const redact = value => String(value).replace(/http:\/\/127\.0\.0\.1:\d+\/(?:#token=[a-f0-9]+)?/g, '[private-companion]')
    .replace(/(?:#token=|Bearer )[a-f0-9]+/g, '[redacted]')
    .replaceAll(root || '\0', '[sandbox]').replaceAll(evidenceDir, '[evidence]');
  try {
    if (Number(process.versions.node.split('.')[0]) < 22) throw new PrerequisiteError('Node.js 22 or newer is required.');
    doctor.prerequisites.push({ name: 'node', available: true });
    const pkg = JSON.parse(await readFile(path.join(ui, 'package.json'), 'utf8'));
    doctor.build.package = { name: pkg.name, version: pkg.version };
    try { doctor.build.git_head = (await execute('git', ['rev-parse', 'HEAD'], { cwd: checkout, timeout: 3000 })).stdout.trim(); }
    catch { doctor.build.git_head = 'unavailable'; }
    doctor.build.sha256 = {};
    for (const file of ['server.mjs', 'projects.mjs', 'inspection.py', 'public/app.js', 'public/index.html',
      'public/style.css', 'package-lock.json', 'verification/companion-projects/verify.mjs']) {
      doctor.build.sha256[file] = hash(await readFile(path.join(ui, file)));
    }
    let chromium;
    try {
      ({ chromium } = await import('playwright'));
      doctor.runtime.playwright = JSON.parse(await readFile(path.join(ui, 'node_modules/playwright/package.json'), 'utf8')).version;
      await import('ws');
      for (const file of ['@xterm/xterm/lib/xterm.js', '@xterm/addon-fit/lib/addon-fit.js', '@xterm/xterm/css/xterm.css']) {
        await access(path.join(ui, 'node_modules', file));
      }
    } catch (error) { throw new PrerequisiteError(`Companion/browser dependencies unavailable. Run cd ui && npm ci. ${error.message}`); }
    doctor.prerequisites.push({ name: 'npm-dependencies', available: true });
    try {
      const python = await execute('python3', ['-B', '-c', 'import sys; print(sys.version.split()[0]); assert sys.version_info >= (3, 11)'], { timeout: 5000 });
      doctor.runtime.python = python.stdout.trim();
    } catch { throw new PrerequisiteError('Python 3.11 or newer must be available as python3 on PATH.'); }
    doctor.prerequisites.push({ name: 'python3', available: true });
    root = await mkdtemp(path.join(os.tmpdir(), 'docket-recipe-projects-'));
    await chmod(root, 0o700);
    result.cleanup.sandbox = root;
    env = privateEnvironment(root);
    for (const key of ['HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'TMPDIR', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR']) {
      await mkdir(env[key], { recursive: true });
    }
    try {
      browser = await chromium.launch({ headless: true, env, executablePath: values['browser-executable'], timeout: 15000 });
      doctor.runtime.chromium = browser.version();
    } catch (error) {
      throw new PrerequisiteError(`Chromium could not launch. Run cd ui && npx playwright install chromium; install system libraries if requested, or supply --browser-executable /absolute/path. ${error.message}`);
    }
    doctor.prerequisites.push({ name: 'chromium', available: true });
    const removal = values.scenario === 'remove-projects';
    const initial = await fixture(root, 'startup', 'startup-run', 'Recipe startup');
    const targetName = removal ? 'removed' : 'added', targetTitle = removal ? 'Recipe removed' : 'Recipe added';
    const target = await fixture(root, targetName, `${targetName}-run`, targetTitle);
    const retained = removal ? await fixture(root, 'retained', 'retained-run', 'Recipe retained') : null;
    const unrelated = await fixture(root, 'unrelated', 'unrelated-run', 'Unrelated must stay absent');
    const unrelatedPrefs = path.join(root, 'unrelated-state/docket-ui/projects.json');
    await mkdir(path.dirname(unrelatedPrefs), { recursive: true });
    await writeFile(unrelatedPrefs, JSON.stringify({ version: 1, projects: [unrelated] }));
    const unrelatedBefore = hash(await readFile(unrelatedPrefs));
    const before = await treeDigest(path.join(root, 'projects'));
    const preferences = path.join(env.XDG_STATE_HOME, 'docket-ui/projects.json');
    result.phase = 'first-launch';
    let server = await startServer(root, initial, env, doctor, servers);
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    page = await context.newPage(); page.setDefaultTimeout(10000);
    await page.goto(server.privateUrl);
    await page.locator('#run-title').filter({ hasText: 'Recipe startup' }).waitFor();
    assert.deepEqual(await page.locator('#project-select option').allTextContents(), ['startup']);
    check('initial-isolation', { listed: ['startup'], unrelated_absent: true });
    if (removal) {
      assert.equal(await page.locator('#remove-project').isDisabled(), true, 'Starting project must be protected');
      const startupRemoval = await page.evaluate(async () => {
        // Use this page's token for its real server request; return only the
        // public rejection, never the token or authenticated request headers.
        const response = await fetch('/api/projects/remove', { method: 'POST',
          headers: { Authorization: `Bearer ${sessionStorage.getItem('docket-token')}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: document.getElementById('project-select').value }) });
        return { status: response.status, error: (await response.json()).error };
      });
      assert.equal(startupRemoval.status, 400, 'Server must refuse startup removal');
      assert.equal(startupRemoval.error, 'The project this companion started with stays in the list.');
      check('starting-project-protected', { selected: 'startup', remove_disabled: true, server: startupRemoval });
      result.phase = 'browser-add-removal-fixtures';
      await addProject(page, target, targetTitle);
      await addProject(page, retained, 'Recipe retained');
      assert.deepEqual(JSON.parse(await readFile(preferences, 'utf8')), { version: 1, projects: [initial, target, retained] });
      check('browser-add-removal-fixtures', { listed: ['startup', 'removed', 'retained'] });
      result.phase = 'browser-remove';
      await page.selectOption('#project-select', { label: 'removed' });
      await page.locator('#run-title').filter({ hasText: targetTitle }).waitFor();
      if (!(await page.locator('#project-menu').evaluate(menu => menu.open))) await page.click('#project-menu > summary');
      assert.equal(await page.locator('#remove-project').isEnabled(), true, 'Added project must be removable');
      await page.click('#remove-project');
      await page.locator('#run-title').filter({ hasText: 'Recipe startup' }).waitFor();
      assert.deepEqual(await page.locator('#project-select option').allTextContents(), ['startup', 'retained']);
      assert.deepEqual(JSON.parse(await readFile(preferences, 'utf8')), { version: 1, projects: [initial, retained] });
      check('browser-remove-persisted', { listed: ['startup', 'retained'], removed_absent: true,
        preferences_sha256: hash(await readFile(preferences)) });

      // Restart only processes this invocation owns. The optional fault changes
      // just its saved membership, after the previous process has fully exited.
      const restart = async (previousContext, retainRemoved = false) => {
        await previousContext.close(); page = null;
        await stopServer(server);
        if (retainRemoved) {
          const { validateProject } = await import('../../projects.mjs');
          assert.equal(await validateProject(target), target, 'Removed fixture must still be a valid project');
          const beforeText = await readFile(preferences, 'utf8');
          const saved = JSON.parse(beforeText);
          assert.deepEqual(saved.projects, [initial, retained]);
          const mode = (await lstat(preferences)).mode & 0o777;
          assert.equal(mode, 0o600, 'Saved preferences must remain private');
          // Splice only an appended member into the JSON array. Leave all other
          // bytes, fields and entry order intact, including whitespace.
          const marker = /"projects"\s*:\s*\[/.exec(beforeText);
          assert.ok(marker);
          let quoted = false, escaped = false, closing = -1;
          for (let i = marker.index + marker[0].length; i < beforeText.length; i++) {
            const ch = beforeText[i];
            if (quoted) {
              if (escaped) escaped = false;
              else if (ch === '\\') escaped = true;
              else if (ch === '"') quoted = false;
            } else if (ch === '"') quoted = true;
            else if (ch === ']') { closing = i; break; }
          }
          assert.ok(closing >= 0);
          const at = beforeText.slice(0, closing).trimEnd().length;
          const insertion = `,\n    ${JSON.stringify(target)}`;
          const afterText = beforeText.slice(0, at) + insertion + beforeText.slice(at);
          assert.deepEqual(JSON.parse(afterText), { ...saved, projects: [...saved.projects, target] });
          const temporary = `${preferences}.retain-removed-${invocation}.tmp`;
          await writeFile(temporary, afterText, { flag: 'wx', mode });
          await chmod(temporary, mode);
          await rename(temporary, preferences);
          const observedAfter = await readFile(preferences, 'utf8');
          assert.equal(observedAfter, afterText);
          assert.equal(observedAfter.slice(0, at) + observedAfter.slice(at + insertion.length), beforeText);
          assert.equal((await lstat(preferences)).mode & 0o777, mode);
          result.fault_injection = { kind: 'retain-removed', after_first_server_exit: true,
            changed: 'private-state/docket-ui/projects.json', added_project: 'removed',
            removed_fixture: { path: target, valid: true, validator: 'ui/projects.mjs:validateProject' },
            pre_sha256: hash(beforeText), post_sha256: hash(observedAfter), mode_before: mode, mode_after: mode,
            other_bytes_preserved: true, atomic: true };
        }
        server = await startServer(root, initial, env, doctor, servers);
        assert.ok(servers.slice(0, -1).every(prior => prior.record.pid !== server.record.pid), 'Restart must launch a distinct companion process');
        const fresh = await browser.newContext({ viewport: { width: 1440, height: 960 } });
        page = await fresh.newPage(); page.setDefaultTimeout(10000);
        await page.goto(server.privateUrl);
        await page.locator('#run-title').filter({ hasText: 'Recipe startup' }).waitFor();
        return fresh;
      };
      result.phase = 'removal-restart';
      const afterRemoval = await restart(context, values['negative-control'] === 'retain-removed');
      result.phase = 'removal-restart-readback';
      const listed = await page.locator('#project-select option').allTextContents();
      result.removal_restart_observation = { listed, fresh_browser_context: true, distinct_server_process: true };
      if (listed.includes('removed')) throw new OutcomeError('REMOVAL_NOT_PERSISTED', 'The browser-removed project reappeared after a real companion restart.');
      assert.deepEqual(listed, ['startup', 'retained']);
      check('removal-restart-readback', { listed, removed_absent: true, fresh_browser_context: true });
      await page.selectOption('#project-select', { label: 'retained' });
      await page.locator('#run-title').filter({ hasText: 'Recipe retained' }).waitFor();
      assert.equal(await page.locator('#project-select option:checked').textContent(), 'retained');
      check('retained-project-readback', { selected: 'retained', run_title: await page.locator('#run-title').textContent() });

      result.phase = 'browser-readd';
      const beforeReadd = JSON.parse(await readFile(preferences, 'utf8')).projects;
      assert.deepEqual(beforeReadd, [initial, retained]);
      const expectedReadd = [...beforeReadd, target], expectedReaddNames = expectedReadd.map(project => path.basename(project));
      await addProject(page, target, targetTitle);
      assert.deepEqual(JSON.parse(await readFile(preferences, 'utf8')), { version: 1, projects: expectedReadd });
      check('browser-readd-persisted', { selected: 'removed', listed: expectedReaddNames, order_rule: 'append to previously saved membership' });
      result.phase = 'readd-restart';
      await restart(afterRemoval);
      result.phase = 'readd-restart-readback';
      const readded = await page.locator('#project-select option').allTextContents();
      result.readd_restart_observation = { listed: readded, fresh_browser_context: true, distinct_server_process: true };
      assert.deepEqual(readded, expectedReaddNames);
      assert.equal(await page.locator('#remove-project').isDisabled(), true, 'Starting project stays protected after restart');
      await page.selectOption('#project-select', { label: 'removed' });
      await page.locator('#run-title').filter({ hasText: targetTitle }).waitFor();
      assert.equal(await page.locator('#project-select option:checked').textContent(), 'removed');
      check('readd-restart-readback', { listed: readded, selected: 'removed', run_title: await page.locator('#run-title').textContent(),
        fresh_browser_context: true, startup_remove_disabled: true });
    } else {
      result.phase = 'browser-add';
      await page.click('#project-menu > summary');
      await page.click('#add-project');
      await page.fill('#project-path-input', root);
      await page.click('#project-submit');
      await page.locator('#project-error').filter({ hasText: 'No .docket' }).waitFor();
      assert.equal(await page.locator('#project-select option').count(), 1);
      check('invalid-project-refused', { reason: 'No .docket', listed_count: 1 });
      await page.fill('#project-path-input', target);
      await page.click('#project-submit');
      await page.locator('#run-title').filter({ hasText: 'Recipe added' }).waitFor();
      assert.equal(await page.locator('#project-select option:checked').textContent(), 'added');
      const persisted = JSON.parse(await readFile(preferences, 'utf8'));
      assert.deepEqual(persisted, { version: 1, projects: [initial, target] });
      check('browser-add-persisted', { selected: 'added', project_paths: ['startup', 'added'], preferences_sha256: hash(await readFile(preferences)) });
      result.phase = 'restart';
      await context.close(); page = null;
      await stopServer(server);
      if (values['negative-control']) {
        await rm(preferences);
        result.fault_injection = { kind: 'lose-projects', after_first_server_exit: true, removed: 'private-state/docket-ui/projects.json' };
      }
      server = await startServer(root, initial, env, doctor, servers);
      assert.notEqual(servers[0].record.pid, server.record.pid, 'Restart must launch a distinct companion process');
      const fresh = await browser.newContext({ viewport: { width: 1440, height: 960 } });
      page = await fresh.newPage(); page.setDefaultTimeout(10000);
      await page.goto(server.privateUrl);
      await page.locator('#run-title').filter({ hasText: 'Recipe startup' }).waitFor();
      result.phase = 'restart-readback';
      const listed = await page.locator('#project-select option').allTextContents();
      result.restart_observation = { listed, fresh_browser_context: true, distinct_server_process: true };
      if (!listed.includes('added')) throw new OutcomeError('PERSISTENCE_MISSING', 'The browser-added project is absent after a real companion restart.');
      assert.deepEqual(listed, ['startup', 'added'], 'Only explicitly added projects may appear after restart');
      await page.selectOption('#project-select', { label: 'added' });
      await page.locator('#run-title').filter({ hasText: 'Recipe added' }).waitFor();
      assert.equal(await page.locator('#project-select option:checked').textContent(), 'added');
      check('restart-readback', { listed, selected: 'added', run_title: await page.locator('#run-title').textContent(), fresh_browser_context: true });
    }
    assert.equal(hash(await readFile(unrelatedPrefs)), unrelatedBefore, 'Unrelated state must not change');
    check('private-state-isolation', { unrelated_preferences_unchanged: true });
    assert.equal(await treeDigest(path.join(root, 'projects')), before, 'No project/run file may change');
    check('project-files-unchanged', { before_sha256: before, after_sha256: await treeDigest(path.join(root, 'projects')) });
    // Capture settled fixture content, rather than the loading placeholders that
    // precede the asynchronous document and empty-history reads.
    const planHeading = page.locator('#document-rendered').getByRole('heading', { name: `Plan: ${targetTitle}`, exact: true });
    await planHeading.waitFor();
    await page.locator('#document-rendered').filter({ hasText: 'Disposable recipe fixture. No agent sessions.' }).waitFor();
    await page.locator('#conversation[aria-busy="false"] .empty-state h2').filter({ hasText: 'No saved messages for this session' }).waitFor();
    await page.locator('#history-note').filter({ hasText: 'No exact native session was recorded. Use the terminal fallback.' }).waitFor();
    await page.locator('#server-state').filter({ hasText: 'Herdr offline' }).waitFor();
    result.screenshot_observation = { plan_heading: await planHeading.textContent(), fixture_rendered: true,
      history_state: 'empty', history_note: await page.locator('#history-note').textContent(),
      herdr: await page.locator('#server-state').textContent() };
    await page.screenshot({ path: path.join(evidenceDir, 'journey.png'), fullPage: true });
    result.screenshot = 'journey.png';
    result.status = 'passed'; result.phase = 'complete';
  } catch (error) {
    result.status = error instanceof PrerequisiteError ? 'prerequisite-failed' : 'failed';
    result.error = { code: error.code || (error instanceof PrerequisiteError ? 'PREREQUISITE' : 'VERIFICATION_FAILED'), message: redact(error.message) };
    if (page && !page.isClosed()) {
      try {
        await page.screenshot({ path: path.join(evidenceDir, 'journey.png'), fullPage: true, timeout: 5000 });
        result.screenshot = 'journey.png';
      } catch (shotError) { result.screenshot_error = redact(shotError.message); }
    }
  } finally {
    const errors = [];
    if (browser) {
      try { await browser.close(); result.cleanup.browser_closed = true; }
      catch (error) { errors.push(redact(error.message)); }
    } else result.cleanup.browser_closed = null;
    for (const server of servers) {
      try { await stopServer(server); }
      catch (error) { errors.push(redact(error.message)); }
    }
    result.cleanup.servers_stopped = servers.every(server => server.record.stopped);
    result.cleanup.server_pids = servers.map(server => server.record.pid);
    if (root) {
      try { await rm(root, { recursive: true, force: true }); result.cleanup.sandbox_removed = true; }
      catch (error) { errors.push(redact(error.message)); result.cleanup.sandbox_removed = false; }
    } else result.cleanup.sandbox_removed = null;
    result.cleanup.errors = errors;
    if (errors.length) { result.status = 'failed'; result.error = { code: 'CLEANUP_FAILED', message: errors.join('; ') }; }
    result.finished_at = new Date().toISOString();
    if (result.status === 'passed') {
      // Each manifest requires the receipt only its own passing scenario writes,
      // so neither successful journey can satisfy the other manifest's proof.
      const cleanup = { servers_stopped: result.cleanup.servers_stopped, sandbox_removed: result.cleanup.sandbox_removed };
      const [field, receipt] = values.scenario === 'remove-projects'
        ? ['removal_proof', { removal: result.removal_restart_observation, readd: result.readd_restart_observation,
          startup_removal: result.checks.find(check => check.name === 'starting-project-protected').observation }]
        : ['add_proof', { restart: result.restart_observation }];
      result[field] = values.scenario === 'remove-projects' ? 'removal-proof.json' : 'add-proof.json';
      await writeFile(path.join(evidenceDir, result[field]), `${JSON.stringify({
        version: 1, invocation, scenario: values.scenario, status: result.status, ...receipt, cleanup
      }, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    }
    // Do not retain the private launch URL, token, environment, or child logs.
    for (const [name, value] of [['doctor.json', doctor], ['result.json', result]]) {
      await writeFile(path.join(evidenceDir, name), `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    }
    if (result.screenshot) await chmod(path.join(evidenceDir, result.screenshot), 0o600);
  }
  return result;
}

async function main() {
  const { values } = parseArgs({ options: {
    'evidence-dir': { type: 'string' }, 'negative-control': { type: 'string' },
    'browser-executable': { type: 'string' }, scenario: { type: 'string', default: 'add-projects' }, help: { type: 'boolean' }
  } });
  if (values.help) {
    console.log('Usage: node ui/verification/companion-projects/verify.mjs --evidence-dir /absolute/fresh/directory [--scenario add-projects|remove-projects] [--browser-executable /absolute/chromium] [--negative-control lose-projects|retain-removed]');
    return;
  }
  if (!['add-projects', 'remove-projects'].includes(values.scenario)) throw new Error('--scenario must be add-projects or remove-projects.');
  const negative = values.scenario === 'remove-projects' ? 'retain-removed' : 'lose-projects';
  if (values['negative-control'] && values['negative-control'] !== negative) throw new Error(`The negative control for ${values.scenario} is ${negative}.`);
  if (values['browser-executable'] && !path.isAbsolute(values['browser-executable'])) throw new Error('--browser-executable must be absolute.');
  const evidenceDir = await outputDirectory(values['evidence-dir']);
  const result = await run(values, evidenceDir);
  console.log(JSON.stringify({ status: result.status, evidence_dir: evidenceDir, error: result.error || null }));
  process.exitCode = result.status === 'passed' ? 0 : result.status === 'prerequisite-failed' ? 2 : 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 2; });
