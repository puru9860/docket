/* Inert Markdown view. Parses a small subset to plain data and renders it from
 * createElement and textContent only: no HTML, no anchors, no images, and no URL
 * is ever interpreted, so document text can never run or fetch anything. */
(() => {
  const MAX_BLOCKS = 5000, MAX_LINES = 20000;
  const fence = /^\s{0,3}(```|~~~)/;

  // A private-URL token fragment is a credential: rendered views hide it, Source keeps the bytes.
  const mask = text => text.replace(/(#|[?&])token=[0-9A-Za-z_-]{16,}/g, '$1token=\u2022\u2022\u2022\u2022');

  function inline(text) {
    const parts = [];
    // A link is shown as its label followed by the literal URL, never as an anchor.
    const pattern = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|!?\[([^\]\n]+)\]\(([^)\s]+)\)/g;
    let last = 0, match;
    while ((match = pattern.exec(text))) {
      if (match.index > last) parts.push({ t: 'text', v: text.slice(last, match.index) });
      if (match[1] !== undefined) parts.push({ t: 'code', v: match[1] });
      else if (match[2] !== undefined) parts.push({ t: 'bold', v: match[2] });
      else parts.push({ t: 'text', v: `${match[3]} (${match[4]})` });
      last = pattern.lastIndex;
    }
    if (last < text.length) parts.push({ t: 'text', v: text.slice(last) });
    return parts;
  }

  function parse(source, options = {}) {
    const lines = String(source ?? '').split('\n').slice(0, MAX_LINES);
    const blocks = [];
    let i = 0;
    if (options.frontmatter !== false && lines[0] === '---') {
      const end = lines.indexOf('---', 1);
      if (end > 0 && end < 200) { blocks.push({ type: 'frontmatter', text: lines.slice(1, end).join('\n') }); i = end + 1; }
    }
    while (i < lines.length && blocks.length < MAX_BLOCKS) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const marker = fence.exec(line);
      if (marker) {
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trimStart().startsWith(marker[1])) body.push(lines[i++]);
        i++;
        blocks.push({ type: 'code', text: body.join('\n') });
        continue;
      }
      if (/^\s{0,3}<!--/.test(line)) {
        while (i < lines.length && !lines[i].includes('-->')) i++;
        i++;
        continue;
      }
      const heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) { blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] }); i++; continue; }
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { blocks.push({ type: 'rule' }); i++; continue; }
      if (/^\s*>/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''));
        blocks.push({ type: 'quote', text: quote.join('\n') });
        continue;
      }
      const item = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
      if (item) {
        const ordered = /\d/.test(item[1]);
        const items = [];
        while (i < lines.length) {
          const next = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i]);
          if (!next || /\d/.test(next[1]) !== ordered) break;
          items.push(next[2]); i++;
        }
        blocks.push({ type: 'list', ordered, items });
        continue;
      }
      if (/^\s*\|/.test(line)) {
        const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) {
          const cells = lines[i].trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
          if (!cells.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cells);
          i++;
        }
        blocks.push({ type: 'table', rows });
        continue;
      }
      const paragraph = [];
      while (i < lines.length && lines[i].trim() && !fence.test(lines[i]) && !/^(#{1,6}\s|\s*>|\s*\|)/.test(lines[i]) &&
             !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) paragraph.push(lines[i++]);
      blocks.push({ type: 'paragraph', text: paragraph.join('\n') });
    }
    return blocks;
  }

  function render(doc, blocks) {
    const make = (tag, text, className) => {
      const element = doc.createElement(tag);
      if (text !== undefined) element.textContent = text;
      if (className) element.className = className;
      return element;
    };
    const fill = (element, text) => {
      for (const part of inline(text)) {
        element.append(part.t === 'text' ? doc.createTextNode(mask(part.v)) : make(part.t === 'code' ? 'code' : 'strong', mask(part.v)));
      }
      return element;
    };
    const root = make('div', undefined, 'markdown');
    for (const block of blocks) {
      if (block.type === 'frontmatter') {
        // Properties stay collapsed; the exact bytes live in the Source view.
        const details = make('details', undefined, 'md-properties');
        const pre = make('pre', block.text, 'md-frontmatter'); pre.setAttribute('aria-label', 'Document frontmatter');
        details.append(make('summary', 'Properties'), pre); root.append(details);
      } else if (block.type === 'heading') root.append(fill(make(`h${Math.min(6, block.level + 1)}`), block.text));
      else if (block.type === 'paragraph') root.append(fill(make('p'), block.text));
      else if (block.type === 'code') root.append(make('pre', mask(block.text), 'md-code'));
      else if (block.type === 'quote') root.append(fill(make('blockquote'), block.text));
      else if (block.type === 'rule') root.append(make('hr'));
      else if (block.type === 'list') {
        const list = make(block.ordered ? 'ol' : 'ul');
        for (const item of block.items) list.append(fill(make('li'), item));
        root.append(list);
      } else if (block.type === 'table') {
        const table = make('table'); const [head, ...rest] = block.rows;
        const row = (cells, cell) => { const tr = make('tr'); for (const c of cells) tr.append(fill(make(cell), c)); return tr; };
        if (head) table.append(row(head, 'th'));
        for (const cells of rest) table.append(row(cells, 'td'));
        root.append(table);
      }
    }
    return root;
  }

  globalThis.DocketMarkdown = { parse, inline, render };
})();
