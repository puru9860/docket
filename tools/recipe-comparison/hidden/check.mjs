#!/usr/bin/env node
// Hidden behavior checks for the recipe comparison. They live outside every candidate
// directory and run against a graded copy of the candidate's source. Each check
// starts the copy's real ui/server.mjs processes with private state; browser checks
// drive real Chromium through the copy's own Playwright dependency.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { parseArgs } from 'node:util';

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

class PrerequisiteError extends Error {}

async function treeDigest(dir) {
  const entries = [];
  async function walk(current) {
    for (const entry of (await readdir(current, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) { entries.push([path.relative(dir, full), 'dir']); await walk(full); }
      else entries.push([path.relative(dir, full), hash(await readFile(full))]);
    }
  }
  await walk(dir);
  return hash(JSON.stringify(entries));
}

// One isolated world per check: its own projects, HOME, XDG dirs and saved list.
async function world(sandbox, name) {
  const root = path.join(sandbox, name);
  const env = Object.fromEntries(['PATH', 'LANG', 'LC_ALL'].filter(k => process.env[k]).map(k => [k, process.env[k]]));
  Object.assign(env, {
    HOME: path.join(root, 'home'), XDG_CONFIG_HOME: path.join(root, 'config'), XDG_STATE_HOME: path.join(root, 'state'),
    XDG_DATA_HOME: path.join(root, 'data'), XDG_CACHE_HOME: path.join(root, 'cache'), TMPDIR: path.join(root, 'tmp'),
    CODEX_HOME: path.join(root, 'providers/codex'), CLAUDE_CONFIG_DIR: path.join(root, 'providers/claude'),
    PYTHONDONTWRITEBYTECODE: '1'
  });
  for (const key of ['HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'TMPDIR']) await mkdir(env[key], { recursive: true });
  const projects = path.join(root, 'projects');
  const canary = path.join(root, 'elsewhere/docket-ui/projects.json');
  await mkdir(path.dirname(canary), { recursive: true });
  await writeFile(canary, JSON.stringify({ version: 1, projects: ['/never/listed'] }));
  return {
    root, env, projects, canary, canaryHash: hash(await readFile(canary)),
    prefs: path.join(env.XDG_STATE_HOME, 'docket-ui/projects.json'),
    async project(name) {
      const dir = path.join(projects, name), run = `${name}-run`;
      await mkdir(path.join(dir, '.docket/runs', run), { recursive: true });
      await writeFile(path.join(dir, '.docket/runs', run, 'plan.mdx'),
        `---\nrun: ${run}\nmode: quick\nworkflow: five-role-v1\ntopology: split\nrole_sessions: planner, implementor, reviewer\nreview_policy: quick-milestone-reviewer\nstatus: draft\n---\n\n# Plan: Project ${name}\n\n## Objective\n\nDisposable fixture.\n`);
      return dir;
    }
  };
}

function harness(checkout, owned) {
  const server = path.join(checkout, 'ui/server.mjs');
  async function start(w, workspace) {
    const child = spawn(process.execPath, [server, '--workspace', workspace, '--port', '0', '--socket', path.join(w.root, 'no-herdr.sock')],
      { cwd: path.join(checkout, 'ui'), env: w.env, stdio: ['ignore', 'pipe', 'pipe'] });
    const app = { child, log: '', error: null };
    owned.push(app);
    child.on('error', error => { app.error = error; });
    for (const stream of [child.stdout, child.stderr]) stream.on('data', bytes => { app.log = (app.log + bytes).slice(-16000); });
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (app.error || child.exitCode !== null) throw new Error(`companion exited at startup: ${app.error?.message || app.log.slice(-400)}`);
      const url = app.log.match(/http:\/\/127\.0\.0\.1:(\d+)\/#token=([a-f0-9]{64})/);
      if (url) {
        Object.assign(app, { base: `http://127.0.0.1:${url[1]}`, token: url[2], privateUrl: url[0] });
        return app;
      }
      await delay(40);
    }
    throw new Error('companion printed no private URL within 15 seconds');
  }
  async function stop(app) {
    const { child } = app;
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      for (let i = 0; i < 125 && child.exitCode === null && child.signalCode === null; i++) await delay(40);
      if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await delay(200); }
    }
  }
  async function call(app, route, body) {
    const response = await fetch(`${app.base}${route}`, body === undefined
      ? { headers: { Authorization: `Bearer ${app.token}` } }
      : { method: 'POST', headers: { Authorization: `Bearer ${app.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }
  const listed = async app => (await call(app, '/api/projects')).body.projects;
  async function add(app, dir) {
    const r = await call(app, '/api/projects', { path: dir });
    assert.equal(r.status, 200, `adding ${path.basename(dir)}: ${JSON.stringify(r.body)}`);
    return r.body.project;
  }
  async function remove(app, dir) {
    const project = (await listed(app)).find(p => p.path === dir);
    assert.ok(project, `${path.basename(dir)} is listed before removal`);
    const r = await call(app, '/api/projects/remove', { id: project.id });
    assert.equal(r.status, 200, `removing ${path.basename(dir)}: ${JSON.stringify(r.body)}`);
  }
  // One restart: stop the running companion, start a distinct process on the same state.
  async function restart(w, app, workspace) { await stop(app); return start(w, workspace); }
  return { start, stop, call, listed, add, remove, restart };
}

// Startup first, then exactly the expected other members in any order, with no duplicates.
function membership(list, startup, others) {
  const paths = list.map(p => p.path);
  assert.equal(paths[0], startup, `startup project is listed first (listed: ${names(paths)})`);
  assert.equal(new Set(paths).size, paths.length, `no project is listed twice (listed: ${names(paths)})`);
  assert.deepEqual([...paths.slice(1)].sort(), [...others].sort(), `listed after restart: ${names(paths)}`);
  return names(paths);
}
const names = paths => paths.map(p => path.basename(p));

async function isolation(w, fixturesBefore) {
  assert.equal(hash(await readFile(w.canary)), w.canaryHash, 'unrelated saved list is unchanged');
  assert.equal(await treeDigest(w.projects), fixturesBefore, 'no project or run file changed');
  const saved = JSON.parse(await readFile(w.prefs, 'utf8'));
  assert.equal(saved.version, 1, 'saved list keeps version 1');
  assert.ok(Array.isArray(saved.projects) && saved.projects.every(p => typeof p === 'string' && path.isAbsolute(p)), 'saved list holds only absolute paths');
  assert.equal((await stat(w.prefs)).mode & 0o777, 0o600, 'saved list stays private');
}

async function browserOptions(page) { return page.locator('#project-select option').allTextContents(); }
async function browserAdd(page, dir) {
  await page.click('#project-menu > summary');
  await page.click('#add-project');
  await page.fill('#project-path-input', dir);
  await page.click('#project-submit');
  await page.locator('#run-title').filter({ hasText: `Project ${path.basename(dir)}` }).waitFor();
}

const TASKS = {
  // A removed project must stay removed across a real restart.
  'removal-persists': [
    ['browser-remove-survives-restart', 'decisive', async ({ h, w, browser }) => {
      const [s, a, b] = [await w.project('home'), await w.project('alpha'), await w.project('bravo')];
      const before = await treeDigest(w.projects);
      let app = await h.start(w, s);
      let context = await browser.newContext();
      let page = await context.newPage(); page.setDefaultTimeout(10000);
      await page.goto(app.privateUrl);
      await page.locator('#run-title').filter({ hasText: 'Project home' }).waitFor();
      await browserAdd(page, a);
      await browserAdd(page, b);
      await page.selectOption('#project-select', { label: 'alpha' });
      await page.locator('#run-title').filter({ hasText: 'Project alpha' }).waitFor();
      await page.click('#project-menu > summary');
      await page.click('#remove-project');
      await page.waitForFunction(() => ![...document.querySelectorAll('#project-select option')].some(o => o.textContent === 'alpha'));
      assert.deepEqual(await browserOptions(page), ['home', 'bravo'], 'browser list right after removal');
      await context.close();
      app = await h.restart(w, app, s);
      context = await browser.newContext();
      page = await context.newPage(); page.setDefaultTimeout(10000);
      await page.goto(app.privateUrl);
      await page.locator('#run-title').filter({ hasText: 'Project home' }).waitFor();
      const shown = await browserOptions(page);
      await context.close();
      assert.deepEqual(shown, ['home', 'bravo'], `fresh browser after restart lists ${shown.join(', ')}`);
      await h.stop(app);
      await isolation(w, before);
      return { after_restart: shown };
    }],
    ['api-remove-survives-restarts', 'decisive', async ({ h, w }) => {
      const [s, a, b, c] = [await w.project('home'), await w.project('alpha'), await w.project('bravo'), await w.project('charlie')];
      let app = await h.start(w, s);
      for (const dir of [a, b, c]) await h.add(app, dir);
      await h.remove(app, b);
      app = await h.restart(w, app, s);
      const first = membership(await h.listed(app), s, [a, c]);
      await h.remove(app, a);
      app = await h.restart(w, app, s);
      const second = membership(await h.listed(app), s, [c]);
      await h.stop(app);
      return { after_first_restart: first, after_second_restart: second };
    }],
    ['readd-after-remove', 'regression', async ({ h, w }) => {
      const [s, a, b] = [await w.project('home'), await w.project('alpha'), await w.project('bravo')];
      let app = await h.start(w, s);
      await h.add(app, a); await h.add(app, b);
      await h.remove(app, a);
      app = await h.restart(w, app, s);
      await h.add(app, a);
      app = await h.restart(w, app, s);
      const listed = membership(await h.listed(app), s, [a, b]);
      await h.stop(app);
      return { listed };
    }],
    ['startup-stays', 'regression', async ({ h, w }) => {
      const [s, a] = [await w.project('home'), await w.project('alpha')];
      let app = await h.start(w, s);
      await h.add(app, a);
      const startup = (await h.listed(app)).find(p => p.path === s);
      const refused = await h.call(app, '/api/projects/remove', { id: startup.id });
      assert.equal(refused.status, 400, 'removing the starting project is refused');
      app = await h.restart(w, app, s);
      const listed = membership(await h.listed(app), s, [a]);
      await h.stop(app);
      return { refused_status: refused.status, listed };
    }]
  ],
  // Every saved project survives a restart from any listed project.
  'startup-switch': [
    ['api-switch-startup', 'decisive', async ({ h, w }) => {
      const [x, y, z] = [await w.project('xray'), await w.project('yankee'), await w.project('zulu')];
      const before = await treeDigest(w.projects);
      let app = await h.start(w, x);
      await h.add(app, y); await h.add(app, z);
      app = await h.restart(w, app, y);
      const fromY = membership(await h.listed(app), y, [x, z]);
      app = await h.restart(w, app, z);
      const fromZ = membership(await h.listed(app), z, [x, y]);
      await h.stop(app);
      await isolation(w, before);
      return { from_yankee: fromY, from_zulu: fromZ };
    }],
    ['switch-then-save', 'decisive', async ({ h, w }) => {
      const [x, y, z, v] = [await w.project('xray'), await w.project('yankee'), await w.project('zulu'), await w.project('victor')];
      let app = await h.start(w, x);
      await h.add(app, y); await h.add(app, z);
      app = await h.restart(w, app, y);
      await h.add(app, v);
      app = await h.restart(w, app, x);
      const listed = membership(await h.listed(app), x, [y, z, v]);
      await h.stop(app);
      return { listed };
    }],
    ['browser-switch-startup', 'decisive', async ({ h, w, browser }) => {
      const [x, y] = [await w.project('xray'), await w.project('yankee')];
      let app = await h.start(w, x);
      let context = await browser.newContext();
      let page = await context.newPage(); page.setDefaultTimeout(10000);
      await page.goto(app.privateUrl);
      await page.locator('#run-title').filter({ hasText: 'Project xray' }).waitFor();
      await browserAdd(page, y);
      await context.close();
      app = await h.restart(w, app, y);
      context = await browser.newContext();
      page = await context.newPage(); page.setDefaultTimeout(10000);
      await page.goto(app.privateUrl);
      await page.locator('#run-title').filter({ hasText: 'Project yankee' }).waitFor();
      const shown = await browserOptions(page);
      assert.deepEqual(shown, ['yankee', 'xray'], `fresh browser after restarting from yankee lists ${shown.join(', ')}`);
      await page.selectOption('#project-select', { label: 'xray' });
      await page.locator('#run-title').filter({ hasText: 'Project xray' }).waitFor();
      await context.close();
      await h.stop(app);
      return { after_restart: shown, selected: 'xray' };
    }],
    ['same-startup-restart', 'regression', async ({ h, w }) => {
      const [x, y] = [await w.project('xray'), await w.project('yankee')];
      let app = await h.start(w, x);
      await h.add(app, y);
      app = await h.restart(w, app, x);
      const listed = membership(await h.listed(app), x, [y]);
      await h.stop(app);
      return { listed };
    }],
    ['vanished-dropped', 'regression', async ({ h, w }) => {
      const [x, y, z] = [await w.project('xray'), await w.project('yankee'), await w.project('zulu')];
      let app = await h.start(w, x);
      await h.add(app, y); await h.add(app, z);
      await h.stop(app);
      await rename(z, `${z}-moved`);
      app = await h.start(w, x);
      const listed = membership(await h.listed(app), x, [y]);
      await h.stop(app);
      return { listed };
    }]
  ]
};

async function main() {
  const { values } = parseArgs({ options: { checkout: { type: 'string' }, task: { type: 'string' }, out: { type: 'string' },
    'browser-executable': { type: 'string' } } });
  if (!values.checkout || !path.isAbsolute(values.checkout) || !TASKS[values.task] || !values.out || !path.isAbsolute(values.out)) {
    console.error(`Usage: node check.mjs --checkout /abs/copy --task ${Object.keys(TASKS).join('|')} --out /abs/result.json`);
    process.exit(2);
  }
  const result = { version: 1, task: values.task, status: 'failed', checks: [], started_at: new Date().toISOString() };
  const owned = [];
  let browser, sandbox;
  try {
    let chromium;
    try { ({ chromium } = createRequire(path.join(values.checkout, 'ui/package.json'))('playwright')); }
    catch (error) { throw new PrerequisiteError(`playwright is not installed in the graded copy: ${error.message}`); }
    sandbox = await mkdtemp(path.join(os.tmpdir(), 'hidden-check-'));
    try { browser = await chromium.launch({ headless: true, executablePath: values['browser-executable'], timeout: 15000 }); }
    catch (error) { throw new PrerequisiteError(`Chromium could not launch: ${error.message.split('\n')[0]}`); }
    const h = harness(values.checkout, owned);
    for (const [id, kind, body] of TASKS[values.task]) {
      const w = await world(sandbox, id);
      const record = { id, kind, passed: false };
      try { record.observation = await body({ h, w, browser }); record.passed = true; }
      catch (error) { record.error = String(error.message).split('\n')[0].replaceAll(sandbox, '[sandbox]'); }
      finally { for (const app of owned.splice(0)) await h.stop(app); }
      result.checks.push(record);
    }
    result.status = result.checks.every(c => c.passed) ? 'passed' : 'failed';
  } catch (error) {
    result.status = error instanceof PrerequisiteError ? 'prerequisite-failed' : 'failed';
    result.error = String(error.message).split('\n')[0];
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (sandbox) await rm(sandbox, { recursive: true, force: true });
    result.finished_at = new Date().toISOString();
    await writeFile(values.out, `${JSON.stringify(result, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
  }
  process.exitCode = result.status === 'passed' ? 0 : result.status === 'prerequisite-failed' ? 2 : 1;
}

await main();
