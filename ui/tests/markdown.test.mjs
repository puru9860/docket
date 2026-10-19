import test from 'node:test';
import assert from 'node:assert/strict';
import '../public/markdown.js';

const { parse, inline, render } = globalThis.DocketMarkdown;

// A DOM stub that records every element and refuses every HTML-injection surface.
function fakeDocument() {
  const created = [];
  class Node { constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this._text = ''; created.push(this); }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute(k, v) { this.attrs[k] = v; }
    set textContent(v) { this._text = String(v); } get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set innerHTML(_) { throw new Error('innerHTML is forbidden'); } set outerHTML(_) { throw new Error('outerHTML is forbidden'); }
    insertAdjacentHTML() { throw new Error('insertAdjacentHTML is forbidden'); } }
  return { created, createElement: tag => new Node(tag), createTextNode: v => { const n = new Node('#text'); n._text = String(v); return n; } };
}
const ALLOWED = new Set(['div', 'p', 'pre', 'code', 'strong', 'blockquote', 'hr', 'ul', 'ol', 'li', 'table', 'tr', 'th', 'td', '#text', 'h2', 'h3', 'h4', 'h5', 'h6', 'h7', 'details', 'summary']);

test('headings, lists, quotes, tables, rules and fences parse to structure', () => {
  const blocks = parse('# Title\n\nSome **bold** and `code`.\n\n- one\n- two\n\n1. first\n2. second\n\n> quoted\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n---\n\n```js\nlet x = 1;\n```\n');
  assert.deepEqual(blocks.map(b => b.type), ['heading', 'paragraph', 'list', 'list', 'quote', 'table', 'rule', 'code']);
  assert.equal(blocks[2].ordered, false);
  assert.equal(blocks[3].ordered, true);
  assert.deepEqual(blocks[5].rows, [['a', 'b'], ['1', '2']]);
  assert.equal(blocks[7].text, 'let x = 1;');
  assert.deepEqual(inline('x **b** `c` y').map(p => p.t), ['text', 'bold', 'text', 'code', 'text']);
});

test('frontmatter is kept as its own exact block and an unterminated fence stays bounded', () => {
  const blocks = parse('---\nrun: R01\nstatus: draft\n---\n# Body\n');
  assert.equal(blocks[0].type, 'frontmatter');
  assert.equal(blocks[0].text, 'run: R01\nstatus: draft');
  const open = parse('```\nnever closed\nstill code');
  assert.equal(open.length, 1);
  assert.equal(open[0].text, 'never closed\nstill code');
  assert.equal(parse('---\nno end\n# not frontmatter').some(b => b.type === 'frontmatter'), false);
});

test('hostile document text becomes text: no tag, anchor, image or markup ever exists', () => {
  const corpus = ['<script>window.pwned=1</script>', '<img src=x onerror=alert(1)>', '[click](javascript:alert(1))', '![x](http://evil.test/x.png)',
    '<a href="http://evil.test">link</a>', '<iframe src="//evil.test"></iframe>', '<svg onload=alert(1)>', 'http://evil.test/auto', '# <b>h</b>',
    '- <u>item</u>', '> <style>*{display:none}</style>', '| <img> | x |', '`<script>`', '**<i>x</i>**', '```\n<script>1</script>\n```'];
  const doc = fakeDocument();
  const node = render(doc, parse(corpus.join('\n\n')));
  for (const element of doc.created) {
    assert.ok(ALLOWED.has(element.tag), `unexpected element ${element.tag}`);
    for (const attr of Object.keys(element.attrs)) assert.ok(['aria-label'].includes(attr), `unexpected attribute ${attr}`);
  }
  const text = node.textContent;
  for (const needle of ['<script>window.pwned=1</script>', '<img src=x onerror=alert(1)>', 'javascript:alert(1)', 'http://evil.test/x.png', '<a href="http://evil.test">', '<iframe', '<svg onload=alert(1)>']) {
    assert.ok(text.includes(needle), `${needle} must survive as visible text`);
  }
});

test('rendering is bounded for huge or odd input', () => {
  const big = Array.from({ length: 30000 }, (_, i) => `- item ${i}`).join('\n');
  assert.ok(parse(big).length <= 5000);
  assert.deepEqual(parse(''), []);
  assert.deepEqual(parse(undefined), []);
  assert.doesNotThrow(() => render(fakeDocument(), parse('|\n||\n| |\n#\n> \n-\n```')));
});

test('links read as label plus literal URL and a private-URL token is hidden in rendered output only', () => {
  assert.deepEqual(inline('see [the UI](http://127.0.0.1:4319/#token=0123456789abcdef0123456789abcdef) now').map(p => p.v),
    ['see ', 'the UI (http://127.0.0.1:4319/#token=0123456789abcdef0123456789abcdef)', ' now']);
  const doc = fakeDocument();
  const node = render(doc, parse('[Open](http://127.0.0.1:4319/#token=0123456789abcdef0123456789abcdef) or `?token=abcdefabcdefabcdefabcdef`'));
  assert.equal(node.textContent, 'Open (http://127.0.0.1:4319/#token=\u2022\u2022\u2022\u2022) or ?token=\u2022\u2022\u2022\u2022');
  assert.equal(doc.created.some(e => e.attrs.href !== undefined || e.tag === 'a'), false);
  const short = render(fakeDocument(), parse('token=abc and #token=short'));
  assert.equal(short.textContent, 'token=abc and #token=short', 'short values are not credentials');
});

test('frontmatter renders as a collapsed Properties disclosure holding the exact text', () => {
  const doc = fakeDocument();
  const node = render(doc, parse('---\nrun: R01\nstatus: draft\n---\n# Body\n'));
  const details = node.children[0];
  assert.deepEqual([details.tag, details.children[0].tag, details.children[0].textContent, details.children[1].textContent],
    ['details', 'summary', 'Properties', 'run: R01\nstatus: draft']);
  assert.equal(details.attrs.open, undefined, 'collapsed by default');
});

test('an HTML comment block is hidden in rendered view and ends at its closing marker', () => {
  const blocks = parse('## Tasks\n\n<!-- one\n   two -->\n\n| a |\n| --- |\n| 1 |\n\n<!-- inline -->\nAfter\n');
  assert.deepEqual(blocks.map(b => b.type), ['heading', 'table', 'paragraph']);
  assert.equal(blocks[2].text, 'After');
  assert.deepEqual(parse('Text\n\n<!-- never closed\nhidden').map(b => b.type), ['paragraph']);
});
