import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { mkdir } from 'node:fs/promises';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });
  const errors = [], network = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', request => { if (/^https?:/.test(request.url())) network.push(request.url()); });
  await page.goto('file://' + fileURLToPath(new URL('../../docs/frontend-design/index.html', import.meta.url)));
  assert.equal(await page.locator('#sessions [data-session]:visible').count(), 5);
  assert.equal(await page.locator('[data-session="worker-a"]').isVisible(), false);
  assert.equal(await page.locator('#implementor-history-toggle').getAttribute('aria-expanded'), 'false');
  assert.match(await page.locator('#attention').textContent(), /^2 need you/);
  assert.equal(await page.locator('#conversation [data-request="planner-q1"]').count(), 1);
  const requestBox = await page.locator('[data-request="planner-q1"]').boundingBox();
  const conversationBox = await page.locator('#conversation').boundingBox();
  assert.ok(requestBox.y >= conversationBox.y && requestBox.y + requestBox.height <= conversationBox.y + conversationBox.height,
    'The pending question must fit above the composer without requiring discovery by scrolling');
  // Verify readability from actual computed colors, not token-name snapshots.
  const readability = await page.evaluate(() => {
    const luminance = color => {
      const channels = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(n => n / 255);
      return channels.map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
        .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    };
    return ['.message p', '.message-meta small', '#document p', '.request-card p', '.choice', '.session-copy small'].map(selector => {
      const element = document.querySelector(selector), style = getComputedStyle(element);
      let parent = element, background;
      while (parent) {
        background = getComputedStyle(parent).backgroundColor;
        if (background !== 'rgba(0, 0, 0, 0)' && background !== 'transparent') break;
        parent = parent.parentElement;
      }
      const values = [luminance(style.color), luminance(background)].sort((a, b) => a - b);
      return { selector, ratio: (values[1] + .05) / (values[0] + .05), size: parseFloat(style.fontSize) };
    });
  });
  for (const item of readability) {
    assert.ok(item.ratio >= 4.5, `${item.selector}: contrast ${item.ratio.toFixed(2)}:1`);
    assert.ok(item.size >= 12, `${item.selector}: metadata must remain readable`);
  }
  assert.ok(readability[0].size >= 15, 'Conversation text must be at least 15px');
  assert.ok(!(await page.locator('.sidebar-foot').textContent()).includes('one window'));
  await mkdir('test-results', { recursive: true });
  await page.screenshot({ path: 'test-results/chat-design.png', fullPage: true });
  await page.getByRole('button', { name: 'Read-only viewer · recommended', exact: true }).click();
  await page.getByRole('button', { name: 'Submit answer', exact: true }).click();
  assert.equal(await page.locator('#message').isEnabled(), true);
  await page.fill('#message', 'planner-only draft');
  await page.locator('[data-session="worker-b"]').click();
  assert.match(await page.locator('#session-source').textContent(), /r01-impl-b/);
  assert.equal(await page.locator('#conversation [data-request="worker-b-p1"]').count(), 1);
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  assert.match(await page.locator('#feedback').textContent(), /r01-impl-b/);
  assert.match(await page.locator('#document-status').textContent(), /draft/);
  assert.equal(await page.locator('#message').inputValue(), '');
  await page.fill('#message', '<script>window.designPwned=true</script>');
  await page.getByRole('button', { name: 'Send ↑', exact: true }).click();
  assert.equal(await page.evaluate(() => window.designPwned), undefined);
  assert.ok((await page.locator('#conversation').textContent()).includes('<script>'));
  await page.locator('[data-session="planner"]').click();
  assert.equal(await page.locator('#message').inputValue(), 'planner-only draft');
  assert.ok(!(await page.locator('#conversation').textContent()).includes('<script>'));
  await page.locator('[data-session="worker-b"]').click();
  await page.fill('#message', 'current implementor draft');
  await page.locator('#implementor-history-toggle').click();
  await page.locator('[data-session="worker-a"]').click();
  assert.equal(await page.locator('#message').isDisabled(), true);
  assert.match(await page.locator('.expired-card').textContent(), /Expired tool request/);
  assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 0);
  await page.locator('[data-session="worker-b"]').click();
  assert.equal(await page.locator('#message').inputValue(), 'current implementor draft');
  assert.ok((await page.locator('#conversation').textContent()).includes('<script>'));
  assert.equal(await page.locator('.expired-card').count(), 0);
  await page.locator('#implementor-history-toggle').click();
  assert.equal(await page.locator('[data-session="worker-a"]').isVisible(), false);
  await page.locator('[data-session="verifier"]').click();
  assert.equal(await page.locator('#message').isDisabled(), true);
  await page.getByRole('button', { name: 'Open terminal', exact: true }).click();
  assert.match(await page.locator('#conversation').textContent(), /r01-verifier/);
  assert.ok(!(await page.locator('#conversation').textContent()).includes('r01-impl-a'));
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole('button', { name: '◫ Context', exact: true }).click();
  assert.equal(await page.locator('#context-panel').isVisible(), true);
  assert.deepEqual(errors, []);
  assert.deepEqual(network, []);
  console.log('PASS design: readable contrast/type, visible question, one current implementor, collapsed inert task history, separate sessions/drafts, question/tool actions, inert input, history-only fallback, mobile context, no network calls');
  console.log('Screenshot: ui/test-results/chat-design.png');
} finally { await browser.close(); }
