import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { rmSync, readdirSync, rmdirSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, writeFile, readdir, lstat, access, symlink } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const execute = promisify(execFile);
const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const helper = path.join(ui, 'verification/companion-projects/verify.mjs');
const sha = value => createHash('sha256').update(value).digest('hex');

async function temporary(t) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'docket-recipe-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}
async function invoke(evidence, args = [], env = process.env) {
  try {
    const output = await execute(process.execPath, [helper, '--evidence-dir', evidence, ...args], { cwd: path.dirname(ui), env, timeout: 90000 });
    return { code: 0, ...output };
  } catch (error) {
    if (typeof error.code !== 'number') throw error;
    return { code: error.code, stdout: error.stdout, stderr: error.stderr };
  }
}
const json = async (dir, name) => JSON.parse(await readFile(path.join(dir, name), 'utf8'));
async function retainedProof(dir, screenshot, removal = false) {
  const expected = screenshot ? ['doctor.json', 'journey.png', 'result.json'] : ['doctor.json', 'result.json'];
  if (removal) expected.push('removal-proof.json');
  assert.deepEqual((await readdir(dir)).sort(), expected.sort());
  const doctor = await json(dir, 'doctor.json'), result = await json(dir, 'result.json');
  assert.equal(doctor.invocation, result.invocation);
  assert.ok(Date.parse(result.finished_at) >= Date.parse(result.started_at));
  for (const file of await readdir(dir)) {
    assert.equal((await lstat(path.join(dir, file))).mode & 0o777, 0o600, `${file} is private`);
  }
  assert.equal((await lstat(dir)).mode & 0o777, 0o700);
  assert.doesNotMatch(JSON.stringify({ doctor, result }), /#token=|Bearer [a-f0-9]|http:\/\/127\.0\.0\.1/);
  if (screenshot) assert.equal((await readFile(path.join(dir, 'journey.png'))).subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.equal(result.cleanup.servers_stopped, true);
  assert.deepEqual(result.cleanup.errors, []);
  if (result.cleanup.sandbox) {
    assert.equal(result.cleanup.sandbox_removed, true);
    await assert.rejects(access(result.cleanup.sandbox), { code: 'ENOENT' });
  }
  for (const instance of doctor.instances) {
    assert.equal(instance.stopped, true);
    assert.throws(() => process.kill(instance.pid, 0), { code: 'ESRCH' }, 'helper-owned companion process exited');
    if (instance.port) await assert.rejects(fetch(`http://127.0.0.1:${instance.port}/`, { signal: AbortSignal.timeout(2000) }), /fetch failed/);
  }
  return { doctor, result };
}

test('manifest names only present, explicit recipe inputs and required proof', async () => {
  const base = path.dirname(helper), manifest = await json(base, 'recipe.json');
  assert.deepEqual(manifest, { version: 1, name: 'companion-projects', instructions: 'recipe.md', helpers: ['verify.mjs'], evidence: ['doctor.json', 'result.json', 'journey.png'] });
  for (const file of [manifest.instructions, ...manifest.helpers]) {
    assert.equal((await lstat(path.join(base, file))).isFile(), true);
    assert.equal((await lstat(path.join(base, file))).isSymbolicLink(), false);
  }
});

test('real browser add persists through a new companion process and fresh browser context; cleanup retains proof', { timeout: 100000 }, async t => {
  const root = await temporary(t), evidence = path.join(root, 'proof'), unrelated = path.join(root, 'unrelated');
  await mkdir(unrelated);
  const canary = JSON.stringify({ version: 1, projects: ['/unrelated-do-not-read'] });
  await writeFile(path.join(unrelated, 'projects.json'), canary);
  const run = await invoke(evidence, [], { ...process.env, XDG_STATE_HOME: unrelated, XDG_CONFIG_HOME: unrelated,
    HERDR_SOCKET_PATH: path.join(unrelated, 'never-connect.sock'), DOCKET_RUN: 'never-use-inherited-run' });
  assert.equal(run.code, 0, run.stdout + run.stderr);
  const { doctor, result } = await retainedProof(evidence, true);
  assert.equal(result.status, 'passed');
  assert.equal(result.scenario, 'add-projects');
  assert.equal(doctor.scenario, 'add-projects');
  assert.equal(result.negative_control, null);
  assert.equal(result.cleanup.browser_closed, true);
  assert.equal(doctor.instances.length, 2);
  assert.notEqual(doctor.instances[0].pid, doctor.instances[1].pid);
  for (const instance of doctor.instances) { assert.equal(instance.ready, true); assert.equal(instance.exit_code, 0); assert.equal(instance.forced_kill, undefined); }
  assert.deepEqual(result.restart_observation, { listed: ['startup', 'added'], fresh_browser_context: true, distinct_server_process: true });
  assert.deepEqual(result.screenshot_observation, { plan_heading: 'Plan: Recipe added', fixture_rendered: true,
    history_state: 'empty', history_note: 'No exact native session was recorded. Use the terminal fallback.', herdr: 'Herdr offline' });
  assert.deepEqual(result.checks.map(check => check.name), ['initial-isolation', 'invalid-project-refused', 'browser-add-persisted', 'restart-readback', 'private-state-isolation', 'project-files-unchanged']);
  assert.ok(result.checks.every(check => check.passed));
  const unchanged = result.checks.find(check => check.name === 'project-files-unchanged').observation;
  assert.equal(unchanged.before_sha256, unchanged.after_sha256);
  assert.equal(doctor.build.sha256['verification/companion-projects/verify.mjs'], sha(await readFile(helper)));
  assert.match(doctor.runtime.chromium, /^\d+\./);
  assert.match(doctor.runtime.python, /^3\./);
  assert.equal(await readFile(path.join(unrelated, 'projects.json'), 'utf8'), canary);
  assert.deepEqual(await readdir(unrelated), ['projects.json'], 'inherited unrelated state remains untouched');
});

test('controlled persistence loss is a failure after real save/restart, with failure screenshot and complete cleanup', { timeout: 100000 }, async t => {
  const evidence = path.join(await temporary(t), 'proof');
  const run = await invoke(evidence, ['--negative-control', 'lose-projects']);
  assert.equal(run.code, 1, run.stdout + run.stderr);
  const { doctor, result } = await retainedProof(evidence, true);
  assert.equal(result.status, 'failed');
  assert.equal(result.phase, 'restart-readback');
  assert.equal(result.error.code, 'PERSISTENCE_MISSING');
  assert.equal(result.negative_control, 'lose-projects');
  assert.equal(result.fault_injection.after_first_server_exit, true);
  assert.deepEqual(result.restart_observation.listed, ['startup']);
  assert.ok(result.checks.some(check => check.name === 'browser-add-persisted'));
  assert.ok(!result.checks.some(check => check.name === 'restart-readback'));
  assert.equal(result.cleanup.browser_closed, true);
  assert.equal(doctor.instances.length, 2);
});

test('missing browser is an actionable prerequisite failure, never a skipped pass; private scratch is cleaned', { timeout: 30000 }, async t => {
  const root = await temporary(t), evidence = path.join(root, 'proof');
  const run = await invoke(evidence, ['--browser-executable', path.join(root, 'missing-chromium')]);
  assert.equal(run.code, 2, run.stdout + run.stderr);
  const { doctor, result } = await retainedProof(evidence, false);
  assert.equal(result.status, 'prerequisite-failed');
  assert.match(result.error.message, /npx playwright install chromium/);
  assert.equal(result.error.code, 'PREREQUISITE');
  assert.equal(doctor.instances.length, 0, 'no companion launched with missing browser');
  assert.deepEqual(result.checks, []);
});

test('missing Python is a prerequisite failure with diagnostics before any browser/server launch', { timeout: 30000 }, async t => {
  const root = await temporary(t), evidence = path.join(root, 'proof');
  const run = await invoke(evidence, [], { ...process.env, PATH: root });
  assert.equal(run.code, 2, run.stdout + run.stderr);
  const { doctor, result } = await retainedProof(evidence, false);
  assert.equal(result.status, 'prerequisite-failed');
  assert.match(result.error.message, /Python 3\.11.*python3 on PATH/);
  assert.equal(doctor.instances.length, 0);
  assert.equal(result.cleanup.sandbox_removed, null);
});

test('old proof is never reused or overwritten, including a partially populated evidence directory', async t => {
  const root = await temporary(t), evidence = path.join(root, 'old');
  await mkdir(evidence);
  await writeFile(path.join(evidence, 'journey.png'), 'old-proof-must-survive');
  const run = await invoke(evidence);
  assert.equal(run.code, 2);
  assert.match(run.stderr, /must be empty/);
  assert.deepEqual(await readdir(evidence), ['journey.png']);
  assert.equal(await readFile(path.join(evidence, 'journey.png'), 'utf8'), 'old-proof-must-survive');
});

test('relative, checkout-local and symlink evidence paths are refused before running the app', async t => {
  const root = await temporary(t), target = path.join(root, 'target'), link = path.join(root, 'link');
  await mkdir(target); await symlink(target, link);
  for (const dir of ['relative-proof', path.join(ui, 'verification/companion-projects/forbidden-proof'), link, path.join(link, 'child')]) {
    const run = await invoke(dir);
    assert.equal(run.code, 2, dir);
    assert.match(run.stderr, /absolute directory|outside measured source|must not traverse symlinks/);
  }
  assert.deepEqual(await readdir(target), []);
  await assert.rejects(access(path.join(ui, 'verification/companion-projects/forbidden-proof')), { code: 'ENOENT' });
});

test('core-shaped checkout/.docket evidence accepts a real positive journey and retains proof outside measured source', { timeout: 100000 }, async t => {
  // This lane owns no Docket lifecycle state. The unique directory models only
  // core's invocation-owned output path and is removed by this test afterwards.
  const state = path.join(path.dirname(ui), '.docket');
  let stateExisted = true;
  try { await access(state); } catch { stateExisted = false; }
  await mkdir(state, { recursive: true });
  const invocationRoot = await mkdtemp(path.join(state, 'recipe-ui-test-'));
  t.after(() => {
    rmSync(invocationRoot, { recursive: true, force: true });
    if (!stateExisted && readdirSync(state).length === 0) rmdirSync(state);
  });
  const evidence = path.join(invocationRoot, '.recipe-evidence', 'T01', 'invocation');
  const run = await invoke(evidence);
  assert.equal(run.code, 0, run.stdout + run.stderr);
  const { result } = await retainedProof(evidence, true);
  assert.equal(result.status, 'passed');
  assert.equal(result.cleanup.sandbox_removed, true);
  assert.equal(result.restart_observation.fresh_browser_context, true);
  assert.equal(result.screenshot_observation.fixture_rendered, true);
  assert.equal(result.screenshot_observation.history_state, 'empty');
  const digest = sha(await readFile(path.join(evidence, 'result.json')));
  const stale = await invoke(evidence);
  assert.equal(stale.code, 2);
  assert.match(stale.stderr, /must be empty/);
  assert.equal(sha(await readFile(path.join(evidence, 'result.json'))), digest, 'a Docket-shaped old proof cannot be overwritten');
});

test('removal manifest declares its own instructions and the same explicit helper/proof contract', async () => {
  const base = path.dirname(helper), manifest = await json(base, 'removal.json');
  assert.deepEqual(manifest, { version: 1, name: 'companion-project-removal', instructions: 'removal.md',
    helpers: ['verify.mjs'], evidence: ['doctor.json', 'result.json', 'journey.png', 'removal-proof.json'] });
  assert.ok(!(await json(base, 'recipe.json')).evidence.includes('removal-proof.json'));
  for (const file of [manifest.instructions, ...manifest.helpers]) {
    assert.equal((await lstat(path.join(base, file))).isFile(), true);
    assert.equal((await lstat(path.join(base, file))).isSymbolicLink(), false);
  }
});

test('real browser removal survives restart, retained project stays usable, and re-add survives another restart', { timeout: 100000 }, async t => {
  const evidence = path.join(await temporary(t), 'proof');
  const run = await invoke(evidence, ['--scenario', 'remove-projects']);
  assert.equal(run.code, 0, run.stdout + run.stderr);
  const { doctor, result } = await retainedProof(evidence, true, true);
  assert.equal(result.status, 'passed');
  assert.equal(result.scenario, 'remove-projects');
  assert.equal(doctor.scenario, 'remove-projects');
  assert.equal(result.negative_control, null);
  assert.equal(result.cleanup.browser_closed, true);
  assert.equal(doctor.instances.length, 3);
  assert.equal(new Set(doctor.instances.map(instance => instance.pid)).size, 3);
  for (const instance of doctor.instances) {
    assert.equal(instance.ready, true); assert.equal(instance.exit_code, 0); assert.equal(instance.forced_kill, undefined);
  }
  assert.deepEqual(result.removal_restart_observation, { listed: ['startup', 'retained'], fresh_browser_context: true, distinct_server_process: true });
  assert.deepEqual(result.readd_restart_observation, { listed: ['startup', 'retained', 'removed'], fresh_browser_context: true, distinct_server_process: true });
  assert.deepEqual(result.checks.map(check => check.name), ['initial-isolation', 'starting-project-protected',
    'browser-add-removal-fixtures', 'browser-remove-persisted', 'removal-restart-readback',
    'retained-project-readback', 'browser-readd-persisted', 'readd-restart-readback', 'private-state-isolation', 'project-files-unchanged']);
  assert.ok(result.checks.every(check => check.passed));
  const observation = name => result.checks.find(check => check.name === name).observation;
  assert.deepEqual(observation('starting-project-protected').server, { status: 400,
    error: 'The project this companion started with stays in the list.' });
  assert.deepEqual(observation('browser-remove-persisted').listed, ['startup', 'retained']);
  assert.equal(observation('retained-project-readback').run_title, 'Recipe retained');
  assert.equal(observation('readd-restart-readback').run_title, 'Recipe removed');
  assert.equal(observation('readd-restart-readback').startup_remove_disabled, true);
  assert.equal(observation('private-state-isolation').unrelated_preferences_unchanged, true);
  assert.equal(observation('project-files-unchanged').before_sha256, observation('project-files-unchanged').after_sha256);
  assert.equal(result.screenshot_observation.plan_heading, 'Plan: Recipe removed');
  assert.equal(result.screenshot_observation.fixture_rendered, true);
  assert.equal(result.screenshot_observation.history_state, 'empty');
  assert.equal(doctor.build.sha256['verification/companion-projects/verify.mjs'], sha(await readFile(helper)));
  const proof = await json(evidence, 'removal-proof.json');
  assert.equal(proof.invocation, result.invocation);
  assert.equal(proof.scenario, 'remove-projects');
  assert.equal(proof.status, 'passed');
  assert.deepEqual(proof.removal.listed, ['startup', 'retained']);
  assert.deepEqual(proof.readd.listed, ['startup', 'retained', 'removed']);
  assert.equal(proof.startup_removal.server.status, 400);
  assert.deepEqual(proof.cleanup, { servers_stopped: true, sandbox_removed: true });
});

test('controlled restored membership fails at removal read-back with private retained proof and cleanup', { timeout: 100000 }, async t => {
  const evidence = path.join(await temporary(t), 'proof');
  const run = await invoke(evidence, ['--scenario', 'remove-projects', '--negative-control', 'retain-removed']);
  assert.equal(run.code, 1, run.stdout + run.stderr);
  const { doctor, result } = await retainedProof(evidence, true);
  assert.equal(result.status, 'failed');
  assert.equal(result.scenario, 'remove-projects');
  assert.equal(result.phase, 'removal-restart-readback');
  assert.equal(result.error.code, 'REMOVAL_NOT_PERSISTED');
  assert.equal(result.negative_control, 'retain-removed');
  const fault = result.fault_injection;
  assert.equal(fault.kind, 'retain-removed');
  assert.equal(fault.after_first_server_exit, true);
  assert.equal(fault.changed, 'private-state/docket-ui/projects.json');
  assert.equal(fault.added_project, 'removed');
  assert.deepEqual(fault.removed_fixture, { path: path.join(result.cleanup.sandbox, 'projects/removed'),
    valid: true, validator: 'ui/projects.mjs:validateProject' });
  assert.equal(fault.pre_sha256, result.checks.find(check => check.name === 'browser-remove-persisted').observation.preferences_sha256);
  assert.match(fault.post_sha256, /^[a-f0-9]{64}$/);
  assert.notEqual(fault.post_sha256, fault.pre_sha256);
  assert.equal(fault.mode_before, 0o600);
  assert.equal(fault.mode_after, fault.mode_before);
  assert.equal(fault.other_bytes_preserved, true);
  assert.equal(fault.atomic, true);
  assert.deepEqual(result.removal_restart_observation.listed, ['startup', 'retained', 'removed']);
  assert.ok(result.checks.some(check => check.name === 'browser-remove-persisted'));
  assert.ok(!result.checks.some(check => check.name === 'removal-restart-readback'));
  assert.ok(!result.checks.some(check => check.name === 'browser-readd-persisted'));
  assert.equal(result.cleanup.browser_closed, true);
  assert.equal(doctor.instances.length, 2);
});

test('explicit removal scenario keeps an unavailable browser as a cleaned prerequisite non-success', { timeout: 30000 }, async t => {
  const root = await temporary(t), evidence = path.join(root, 'proof');
  const run = await invoke(evidence, ['--scenario', 'remove-projects', '--browser-executable', path.join(root, 'missing-chromium')]);
  assert.equal(run.code, 2, run.stdout + run.stderr);
  const { doctor, result } = await retainedProof(evidence, false);
  assert.equal(result.status, 'prerequisite-failed');
  assert.equal(result.scenario, 'remove-projects');
  assert.equal(result.error.code, 'PREREQUISITE');
  assert.match(result.error.message, /npx playwright install chromium/);
  assert.equal(doctor.instances.length, 0);
  assert.equal(result.cleanup.sandbox_removed, true);
});

test('invalid scenario/control pairs are usage errors before any evidence directory or runtime launch', async t => {
  const root = await temporary(t);
  for (const [index, args] of [
    ['--negative-control', 'retain-removed'],
    ['--scenario', 'remove-projects', '--negative-control', 'lose-projects'],
    ['--scenario', 'unknown'],
  ].entries()) {
    const evidence = path.join(root, `proof-${index}`);
    const run = await invoke(evidence, args);
    assert.equal(run.code, 2);
    assert.match(run.stderr, /negative control for|--scenario must be/);
    await assert.rejects(access(evidence), { code: 'ENOENT' });
  }
});
