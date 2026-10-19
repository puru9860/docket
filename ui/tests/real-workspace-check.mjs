/* Layout and rendering check against this repository's real .docket workspace. EXPLICIT RUN
 * ONLY: it needs the local .docket the developer has, so it is never part of a gate. It
 * starts a second read-only companion on a free port (the running one is untouched),
 * reads Herdr's inventory without sending anything, and opens every run in Chromium.
 * For each run it asserts that every role row is reachable at 1500x960 and 390px, that no
 * placeholder text is shown, and that Rendered frontmatter keeps its lines whole.
 *
 * Run from ui/:  REAL_SHOT=/path/shot.png node tests/real-workspace-check.mjs */
import assert from 'node:assert/strict';
import { access } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { createCompanion } from '../server.mjs';
import { assertAllRolesReachable, assertNoPlaceholderText, assertFrontmatterIntact, assertFitsViewport } from './layout-assertions.mjs';

const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
await access(path.join(workspace, '.docket', 'runs'));
const socketPath = process.env.HERDR_SOCKET_PATH || path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'herdr', 'herdr.sock');
const app = await createCompanion({ workspace, socketPath });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 960 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const failed = []; page.on('requestfailed', r => failed.push(`${r.url().replace(/token=[^&]+/, '')} ${r.failure()?.errorText}`));
  await page.goto(app.privateUrl);
  await page.locator('#run-select option').first().waitFor({ state: 'attached' });
  const runs = await page.locator('#run-select option').evaluateAll(options => options.map(o => o.value));
  assert.ok(runs.length > 1, 'the real workspace has several runs');
  const summary = [];
  for (const id of runs) {
    await page.selectOption('#run-select', id);
    await page.locator('#run-title').waitFor();
    await page.waitForTimeout(500);
    const tasks = await page.locator('#tasks .task').count();
    for (const [width, height] of [[1500, 960], [390, 844]]) {
      await page.setViewportSize({ width, height });
      await page.waitForTimeout(200);
      const rows = await assertAllRolesReachable(page, `${id} at ${width}x${height}`);
      await assertFitsViewport(page, `${id} at ${width}x${height}`);
      summary.push(`${id} ${width}x${height}: ${tasks} tasks, ${rows} rows reachable`);
    }
    await page.setViewportSize({ width: 1500, height: 960 });
    // Every role's header is read once its history settles; the long runs get all of their rows.
    const rows = await page.locator('#roles > button.role').count();
    for (let i = 0; i < Math.min(rows, ['ui-full-20261008', 'audit3-finish'].includes(id) ? rows : 1); i++) {
      const row = page.locator('#roles > button.role').nth(i);
      await row.click();
      await page.locator('#history-note').filter({ hasNotText: 'Reading recorded' }).waitFor({ timeout: 30000 });
      await assertNoPlaceholderText(page, `${id} / ${(await row.textContent()).trim().slice(0, 24)}`);
    }
  }
  await page.selectOption('#run-select', 'ui-full-20261008');
  await page.locator('#run-title').waitFor();
  await page.locator('#roles > button.role').first().click();
  await page.locator('#document-path').filter({ hasText: 'ui-full-20261008/plan.mdx' }).waitFor();
  await page.locator('#history-note').filter({ hasNotText: 'Reading recorded' }).waitFor({ timeout: 30000 });
  await page.click('#doc-rendered');
  await page.waitForTimeout(500);
  await assertFrontmatterIntact(page, 'ui-full-20261008 plan');
  if (process.env.REAL_SHOT) await page.screenshot({ path: process.env.REAL_SHOT, fullPage: false });
  console.log('failed requests:', JSON.stringify(failed.slice(0, 12)));
  assert.deepEqual(errors, []);
  console.log(summary.join('\n'));
  console.log(`REAL WORKSPACE OK: ${runs.length} runs`);
} finally {
  await browser.close(); await app.close();
}
