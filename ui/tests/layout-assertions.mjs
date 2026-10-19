/* Layout checks shared by the synthetic acceptance suite and the real-workspace check:
 * the failures they guard only show up with the many tasks, many earlier sessions and
 * identity-less slots of a real run, not with a small fixture. */
import assert from 'node:assert/strict';

// Every role row, and every collapsed earlier-session group, can be scrolled into the viewport.
export async function assertAllRolesReachable(page, label, minimum = 1) {
  const viewport = page.viewportSize();
  const box = await page.locator('#roles').boundingBox();
  assert.ok(box && box.height > 0, `${label}: #roles has no height`);
  const buttons = page.locator('#roles > button');
  const count = await buttons.count();
  assert.ok(count >= minimum, `${label}: expected at least ${minimum} role rows, found ${count}`);
  for (let i = 0; i < count; i++) {
    // The page re-renders its roles on every poll, so a row can be replaced mid-scroll.
    let button, at;
    for (let attempt = 0; attempt < 5 && !at; attempt++) {
      button = buttons.nth(i);
      try { await button.scrollIntoViewIfNeeded({ timeout: 3000 }); at = await button.boundingBox(); } catch { at = null; }
    }
    assert.ok(at && at.height >= 20, `${label}: row ${i} has no height`);
    assert.ok(at.y >= 0 && at.y + at.height <= viewport.height + 1 && at.x >= 0 && at.x + at.width <= viewport.width + 1,
      `${label}: row ${i} "${(await button.textContent()).trim().slice(0, 30)}" is outside the ${viewport.width}x${viewport.height} viewport at ${JSON.stringify(at)}`);
  }
  return count;
}

// A slot without a native identity must never print the language's placeholders.
export async function assertNoPlaceholderText(page, label) {
  for (const selector of ['#roles', '.conversation-heading', '#session-source', '.topbar', '#history-note']) {
    const text = await page.locator(selector).first().innerText();
    assert.doesNotMatch(text, /\bundefined\b|\bnull\b|\bNaN\b|\[object/, `${label}: ${selector} shows a placeholder: ${text.slice(0, 120)}`);
  }
}

// Properties are collapsed by design. Opened, frontmatter values never break inside a token, so the
// block keeps its lines and scrolls sideways.
export async function assertFrontmatterIntact(page, label) {
  const details = page.locator('#document-rendered details.md-properties').first();
  await details.waitFor({ state: 'attached', timeout: 10000 });
  assert.equal(await details.evaluate(element => element.open), false, `${label}: Properties start collapsed`);
  await details.locator('summary').click();
  const block = page.locator('#document-rendered .md-frontmatter').first();
  await block.waitFor({ timeout: 10000 });
  const measured = await block.evaluate(element => {
    const style = getComputedStyle(element);
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.6;
    const lines = element.textContent.split('\n').length;
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    return { whiteSpace: style.whiteSpace, overflowWrap: style.overflowWrap, wordBreak: style.wordBreak, lines,
      renderedLines: Math.round((element.clientHeight - padding) / lineHeight) };
  });
  assert.equal(measured.whiteSpace, 'pre', `${label}: frontmatter wraps (${JSON.stringify(measured)})`);
  assert.equal(measured.overflowWrap, 'normal', label);
  assert.equal(measured.wordBreak, 'normal', label);
  assert.ok(measured.renderedLines <= measured.lines, `${label}: frontmatter lines wrapped: ${JSON.stringify(measured)}`);
  await details.locator('summary').click();
}

// The page never scrolls sideways, and the roles, connection strip, composer and header actions are reachable.
export async function assertFitsViewport(page, label) {
  const viewport = page.viewportSize();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 1, `${label}: page scrolls sideways by ${overflow}px`);
  for (const selector of ['#refresh', '#toggle-docs', '#roles .role.selected', '#message']) {
    // The role list is rebuilt on every poll, so a measured node can detach; measure again.
    const target = page.locator(selector).first();
    let box = null;
    for (const deadline = Date.now() + 3000; !(box?.width > 0 && box.height > 0) && Date.now() < deadline;) {
      try { await target.scrollIntoViewIfNeeded({ timeout: 1000 }); box = await target.boundingBox(); }
      catch { box = null; }
      if (!(box?.width > 0)) await page.waitForTimeout(50);
    }
    assert.ok(box && box.width > 0 && box.height > 0, `${label}: ${selector} has no box`);
    assert.ok(box.x >= -1 && box.x + box.width <= viewport.width + 1, `${label}: ${selector} sits outside the ${viewport.width}px width: ${JSON.stringify(box)}`);
  }
}

// No visible control may sit under the composer or other fixed chrome: every control outside it must
// stay clear of its box.
export async function assertNoControlOverlapsComposer(page, label) {
  const hits = await page.evaluate(() => {
    const composer = document.getElementById('composer');
    if (!composer || composer.hidden) return [];
    const box = composer.getBoundingClientRect();
    const found = [];
    for (const element of document.querySelectorAll('button, a[href], select, input, textarea, summary, [tabindex="0"]')) {
      if (composer.contains(element) || element.closest('[hidden], dialog:not([open])')) continue;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (style.visibility === 'hidden' || !rect.width || !rect.height) continue;
      const across = Math.min(rect.right, box.right) - Math.max(rect.left, box.left);
      const down = Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top);
      if (across > 1 && down > 1) found.push(element.id || element.textContent.trim().slice(0, 24) || element.tagName);
    }
    return found;
  });
  assert.deepEqual(hits, [], `${label}: controls under the composer: ${hits.join(', ')}`);
}

// One terminal action per state: at most one visible button in the conversation panel opens the terminal.
export async function assertSingleTerminalAction(page, label) {
  const names = await page.locator('.conversation-panel button:visible').evaluateAll(buttons =>
    buttons.filter(b => !b.closest('[role="tablist"]') && /terminal/i.test(b.textContent)).map(b => b.textContent.trim()));
  assert.ok(names.length <= 1, `${label}: several terminal actions: ${names.join(' | ')}`);
}

// A rendered table never splits a word across lines: "complexity" must not become "co/mpl/exit/y".
export async function assertTableWordsWhole(page, label) {
  const split = await page.evaluate(() => {
    const broken = [];
    for (const cell of document.querySelectorAll('#document-rendered th, #document-rendered td')) {
      for (const node of cell.childNodes) {
        if (node.nodeType !== Node.TEXT_NODE) continue;
        for (const match of node.textContent.matchAll(/\S+/g)) {
          const range = document.createRange();
          range.setStart(node, match.index); range.setEnd(node, match.index + match[0].length);
          const tops = new Set([...range.getClientRects()].map(rect => Math.round(rect.top)));
          if (tops.size > 1) broken.push(match[0]);
        }
      }
    }
    return broken;
  });
  assert.deepEqual(split, [], `${label}: table words split across lines`);
}
