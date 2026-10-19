/* Browser navigation never focuses or restarts a Herdr pane. */
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.hash.slice(1));
const token = params.get('token') || sessionStorage.getItem('docket-token') || '';
if (token) sessionStorage.setItem('docket-token', token);
history.replaceState(null, '', location.pathname);
let state, projectId, runId, slotId, ws, selection, activeBinding, mode = 'observe', connected = false;
try { projectId = sessionStorage.getItem('docket-project') || undefined; } catch { /* Use the starting project. */ }
let documentId, discussionTab = false, reconnectTimer, lastDocument = '', pasteSelection;
const wideSessions = new Set();
let autoBinding = false;
const bindings = new Map();
const drafts = new Map(), histories = new Map(), expandedRoles = new Set();
let savedLinks = [];
try { savedLinks = JSON.parse(sessionStorage.getItem('docket-history-links') || '[]'); } catch { /* Ignore an invalid local preference. */ }
const linkedHistories = new Map(Array.isArray(savedLinks) ? savedLinks.filter(item => Array.isArray(item) && item.length === 2 && item.every(v => typeof v === 'string')) : []);
const historyChoices = new Map();
let view = 'conversation', selectedSessionKey, selectedDraftKey, carry = null;
const term = new Terminal({ fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
  fontSize: 14, lineHeight: 1.25, cursorBlink: true, scrollback: 0, disableStdin: true,
  theme: { background: '#17212B', foreground: '#E2E8F0', cursor: '#93C5FD', selectionBackground: '#334155' } });
const fit = new FitAddon.FitAddon();
term.loadAddon(fit); term.open($('terminal'));
const run = () => state?.runs.find(r => r.id === runId);
const slot = () => run()?.slots.find(s => s.id === slotId);
const bindingKey = () => `${projectId || ''}/${runId}/${slotId}`;
const roleKey = () => `${state?.workspace || ''}/${bindingKey()}/${slot()?.generation}`;
const sessionKey = () => `${roleKey()}/${linkedHistories.get(roleKey()) || 'recorded'}`;
const readSelections = () => { try { const value = JSON.parse(sessionStorage.getItem('docket-selection') || '{}'); return value && typeof value === 'object' ? value : {}; } catch { return {}; } };
function saveSelection() {
  if (!projectId) return;
  try { sessionStorage.setItem('docket-selection', JSON.stringify({ ...readSelections(), [projectId]: { run: runId, slot: slotId } })); } catch { /* Per-tab convenience only. */ }
}
const notice = text => { $('notice').textContent = text; $('notice').hidden = !text; };
const statusText = text => text.replaceAll('-', ' ');
const option = (value, label) => new Option(label, value);
// A native identity exists only when it names both a harness and a session; an empty object is none.
const nativeOf = s => (s?.native?.harness && s.native.session_id ? s.native : null);

const PROJECT_ROUTES = new Set(['state', 'document', 'history', 'history-choices', 'binding']);
async function api(route, parameters = {}) {
  if (projectId && PROJECT_ROUTES.has(route)) parameters = { project: projectId, ...parameters };
  const read = () => fetch(`/api/${route}?${new URLSearchParams(parameters)}`, { headers: { Authorization: `Bearer ${token}` } });
  // Reads are idempotent: the browser aborts in-flight loopback requests on a host network
  // change, so one quiet retry keeps that from surfacing as "Failed to fetch".
  let response;
  try { response = await read(); }
  catch (error) { if (!(error instanceof TypeError)) throw error; await new Promise(resolve => setTimeout(resolve, 200)); response = await read(); }
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}
function size() {
  const proposed = fit.proposeDimensions() || { cols: 100, rows: 24 };
  return { cols: Math.max(20, Math.min(500, proposed.cols)), rows: Math.max(5, Math.min(200, proposed.rows)) };
}
function send(message) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ ...message, selection }));
}
function input(text) {
  if (view === 'terminal' && connected && mode === 'control') send({ type: 'input', text });
}
function controls() {
  const writable = view === 'terminal' && connected && mode === 'control' && !slot()?.historical;
  term.options.disableStdin = !writable;
  $('control').disabled = !activeBinding || !connected;
  $('control').textContent = writable ? 'Release control' : 'Take control';
  $('reconnect').disabled = !activeBinding;
  $('paste').disabled = !writable;
  $('scroll-up').disabled = !writable;
  $('scroll-down').disabled = !writable;
  document.querySelectorAll('[data-key]').forEach(button => { button.disabled = !writable; });
  $('stream-mode').textContent = connected ? (writable ? 'You have control' : 'Read only') : 'Disconnected';
  $('projection-note').textContent = writable ? 'The TUI is sized for this browser panel while you have control.' :
    'Read-only view follows the existing split pane. Scroll within this panel to see the whole screen, or take control to fit the TUI here.';
}

function terminalLayout() {
  const screen = term.element?.querySelector('.xterm-screen');
  const observed = mode !== 'control';
  $('terminal').style.width = observed && screen ? `${screen.offsetWidth + 16}px` : '100%';
  $('terminal').style.height = observed && screen ? `${screen.offsetHeight + 16}px` : '100%';
  document.querySelector('.terminal-area').classList.toggle('observing', observed);
}
function disconnect() {
  clearTimeout(reconnectTimer); reconnectTimer = undefined;
  const old = ws; ws = null;
  old?.close();
  connected = false; selection = crypto.randomUUID();
  controls();
}
function attach(nextMode = 'observe', takeover = false) {
  disconnect();
  if (!activeBinding || view !== 'terminal' || slot()?.historical) return;
  mode = nextMode;
  // Control measures the whole panel, not the previous observer's native-sized screen.
  if (mode === 'control') { $('terminal').style.width = '100%'; $('terminal').style.height = '100%'; }
  $('takeover').hidden = true;
  term.reset(); $('terminal-empty').hidden = true;
  const currentSelection = selection;
  const socket = new WebSocket(`ws://${location.host}/terminal`, ['docket', token]);
  ws = socket;
  let lastSequence = null;
  socket.onopen = () => {
    if (ws !== socket) return;
    send({ type: 'attach', binding: activeBinding.id, mode: nextMode, takeover, ...size() });
  };
  socket.onmessage = event => {
    if (ws !== socket || selection !== currentSelection) return;
    const record = JSON.parse(event.data);
    if (record.selection !== selection) return;
    if (record.type === 'bridge.ready') {
      clearTimeout(reconnectTimer); reconnectTimer = undefined;
      mode = record.mode; connected = true; controls(); notice('');
      // An observer fallback also starts a fresh projection.
      lastSequence = null; term.reset();
      if (mode === 'control') term.focus();
    } else if (record.type === 'terminal.frame') {
      const firstFrame = lastSequence === null;
      if ((lastSequence === null && !record.full) ||
          (lastSequence !== null && record.seq !== lastSequence + 1)) {
        notice('Screen stream interrupted. Reconnecting...'); attach('observe'); return;
      }
      lastSequence = record.seq;
      if (record.full && (term.cols !== record.width || term.rows !== record.height)) term.resize(record.width, record.height);
      const bytes = Uint8Array.from(atob(record.bytes), c => c.charCodeAt(0));
      term.write(bytes);
      requestAnimationFrame(() => {
        terminalLayout();
        if (firstFrame && mode !== 'control') {
          const area = document.querySelector('.terminal-area'); area.scrollTop = area.scrollHeight;
        }
      });
      $('terminal-size').textContent = `${record.width} × ${record.height}`;
    } else if (record.type === 'bridge.contention') {
      $('takeover').hidden = false;
      notice('Another client has control. You can watch here or explicitly take over.');
    } else if (record.type === 'bridge.invalidated') {
      bindings.delete(bindingKey()); activeBinding = undefined; disconnect();
      $('binding-label').textContent = 'Binding invalidated. Choose the current terminal again.';
      notice(record.reason); renderRoles();
    } else if (record.type === 'bridge.closed') {
      connected = false; controls(); notice(record.reason);
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(() => { if (ws === socket && activeBinding) attach('observe'); }, 1500);
    }
  };
  socket.onclose = () => {
    if (ws !== socket) return;
    connected = false; controls();
    notice('Connection lost. Reconnecting in read-only mode...');
    reconnectTimer = setTimeout(() => { if (activeBinding && ws === socket) attach('observe'); }, 1500);
  };
  socket.onerror = () => { if (ws === socket) notice('Cannot reach the companion. Reopen its private startup URL if it restarted.'); };
}
const GLYPHS = { live: '\u25CF', working: '\u25D0', 'needs-you': '\u25B2', starting: '\u25D0', 'not-found': '\u25CB', 'needs-link': '\u25C7', ambiguous: '\u25C7', saved: '\u25AB' };
const cap = text => text ? text[0].toUpperCase() + text.slice(1) : '';
const connectionOf = s => s?.connection || { state: s?.historical ? 'saved' : 'not-found', label: s?.historical ? 'Saved only' : 'Not found', reason: s?.send_note || '', action: null };
function renderRoles() {
  // Every poll rebuilds the list; keyboard focus returns to the same entry.
  const focused = $('roles').contains(document.activeElement) ? document.activeElement : null;
  const refocus = focused?.dataset.slot ? `[data-slot="${CSS.escape(focused.dataset.slot)}"]` :
    focused?.getAttribute('aria-controls') ? `[aria-controls="${CSS.escape(focused.getAttribute('aria-controls'))}"]` : '';
  $('roles').replaceChildren();
  const slots = (run()?.slots || []).filter(s => !s.merged_into && !s.superseded_history);
  const roles = [...new Set(slots.map(s => s.role))];
  $('role-count').textContent = roles.length;
  function roleButton(s, earlier = false, several = false) {
    const bound = bindings.get(`${projectId || ''}/${runId}/${s.id}`);
    const live = state.herdr.terminals.find(t => t.terminal === bound?.terminal);
    const connection = connectionOf(s);
    const button = document.createElement('button');
    button.className = `role${s.id === slotId ? ' selected' : ''}${earlier ? ' earlier' : ''}`;
    button.setAttribute('aria-pressed', String(s.id === slotId));
    button.dataset.slot = s.id; button.dataset.historical = String(!!s.historical); button.dataset.state = connection.state;
    const icon = document.createElement('span'); icon.className = 'role-icon'; icon.textContent = s.role.slice(0, 2).toUpperCase();
    const text = document.createElement('span'); text.className = 'role-text';
    const label = document.createElement('strong');
    label.textContent = earlier ? (s.owners?.join(', ') || s.session || 'Recorded session') :
      cap(s.role) + (several && s.owners?.length ? ` \u00B7 ${s.owners.join(', ')}` : '');
    const detail = document.createElement('small');
    const harness = nativeOf(s)?.harness || (live?.agent && !earlier ? live.agent : '');
    detail.textContent = [connection.label, harness, !earlier && s.association?.state === 'linked' ? 'linked' : '', !earlier ? s.task_note : '']
      .filter(Boolean).join(' \u00B7 ');
    button.title = [s.label, s.mapping_issue, connection.reason].filter(Boolean).join(' \u00B7 ');
    text.append(label, detail);
    const glyph = document.createElement('span'); glyph.className = `glyph ${connection.state}`; glyph.setAttribute('aria-hidden', 'true'); glyph.textContent = GLYPHS[connection.state] || '\u25CB';
    button.append(icon, text, glyph);
    button.onclick = () => selectRole(s.id);
    return button;
  }
  const groupToggle = (role, kind, items) => {
    const toggle = document.createElement('button'); toggle.className = 'session-history-toggle';
    const key = `${runId}/${role}/${kind}`, expanded = expandedRoles.has(key) || items.some(s => s.id === slotId);
    toggle.dataset.historyRole = role;
    toggle.textContent = `${expanded ? '\u25BE' : '\u25B8'} ${kind === 'unattributed' ? 'Unattributed' : role === 'implementor' ? 'Earlier tasks' : `Recorded ${role} sessions`} (${items.length})`;
    toggle.setAttribute('aria-expanded', String(expanded));
    const container = document.createElement('div'); container.className = 'session-history'; container.hidden = !expanded;
    container.id = `history-${role}-${kind}`; toggle.setAttribute('aria-controls', container.id);
    items.forEach(s => container.append(roleButton(s, true)));
    toggle.onclick = () => { expandedRoles.has(key) ? expandedRoles.delete(key) : expandedRoles.add(key); renderRoles(); };
    $('roles').append(toggle, container);
  };
  for (const role of roles) {
    const all = slots.filter(s => s.role === role);
    const current = all.filter(s => !s.historical);
    current.forEach(s => $('roles').append(roleButton(s, false, current.length > 1)));
    const earlier = all.filter(s => s.historical && s.group !== 'unattributed'), unattributed = all.filter(s => s.historical && s.group === 'unattributed');
    if (earlier.length) groupToggle(role, 'earlier', earlier);
    if (unattributed.length) groupToggle(role, 'unattributed', unattributed);
  }
  if (refocus) $('roles').querySelector(refocus)?.focus({ preventScroll: true });
}
function renderTerminals() {
  const previous = $('terminal-select').value;
  const match = slot()?.terminal_match;
  const bound = bindings.get(bindingKey());
  $('terminal-select').replaceChildren(option('', 'Choose an existing terminal...'));
  state.herdr.terminals.forEach(t => $('terminal-select').append(option(t.terminal,
    `${t.name} · ${t.agent || 'shell'} · ${t.pane}${match?.terminal === t.terminal ? ' · auto-matched' : ''}`)));
  const preferred = bound?.terminal ||
    (match?.terminal && !match.ambiguous ? match.terminal : null) ||
    ([...$('terminal-select').options].some(o => o.value === previous) ? previous : '');
  $('terminal-select').value = preferred;
  $('terminal-select').disabled = !slot() || !!slot()?.historical;
  $('bind').disabled = !slot() || !!slot()?.historical || !$('terminal-select').value;
}
async function autoBind(terminal) {
  if (autoBinding) return;
  autoBinding = true;
  const key = bindingKey();
  try {
    const bound = await api('binding', { run: runId, slot: slotId, terminal });
    if (key !== bindingKey() || slot()?.historical) return;
    bindings.set(key, bound); activeBinding = bound;
    $('binding-label').textContent = `${bound.pane} \u00B7 ${bound.attribution} \u00B7 auto-matched`;
    renderRoles(); renderTerminals(); composerState();
  } catch { /* Leave the exact terminal to explicit binding; never guess a fallback. */ }
  finally { autoBinding = false; }
}
// A newly proven identity binds on the next poll; nothing is ever guessed.
function maybeAutoBind() {
  const s = slot();
  if (!activeBinding && s && !s.historical && s.terminal_match?.terminal && !s.terminal_match.ambiguous) autoBind(s.terminal_match.terminal);
}
function delivery(kind, text) {
  $('delivery').textContent = text || '';
  $('delivery').className = `delivery${kind ? ` ${kind}` : ''}`;
  $('delivery').hidden = !text;
}
// The composer is for a current role; an earlier session shows one read-only line instead.
function chrome() {
  const s = slot();
  const current = !!s && !s.historical;
  $('composer').hidden = view === 'terminal' || !current;
  $('readonly-note').hidden = view === 'terminal' || !s || current;
  $('message').disabled = !current;
  $('stop').hidden = !state?.stop_supported;
}
function composerState() {
  const s = slot();
  chrome();
  const current = !!s && !s.historical;
  const bound = !!activeBinding && current;
  const connection = connectionOf(s);
  $('send').disabled = !(bound && s?.send_ready && $('message').value.trim());
  $('stop').disabled = !(bound && s?.stop_ready);
  $('send').title = !current ? '' : (s.send_ready ? 'Send to this exact live session.' : connection.reason || s.send_note || '');
  $('stop').title = !current ? '' : (s.stop_ready ? 'Interrupt this session.' : (s.stop_note || ''));
  if (!current) $('recipient-hint').textContent = 'Read-only history. No live transport or terminal.';
  else if (s.send_ready && bound) {
    $('recipient-hint').textContent = 'Sends to this exact live session. Ctrl+Enter sends.' +
      (s.stop_ready ? ' Agent is working; Stop targets only this session.' : '');
  } else if (s.send_ready) $('recipient-hint').textContent = 'Connecting to this role\'s proven terminal...';
  else if (s.stop_ready) $('recipient-hint').textContent = `Agent is working. ${s.send_note || 'Stop targets only this session.'}`;
  else $('recipient-hint').textContent = connection.short || connection.reason || s.send_note || 'Sending chat is unavailable. Use the live terminal.';
  renderConnection();
  renderRequests();
}
// One slot, only when the session is not Live: the exact reason and the one recovery action.
const ACTIONS = { link: 'Link a session', 'link-terminal': 'Link terminal', refresh: 'Refresh', retry: 'Retry', 'choose-terminal': 'Choose terminal', 'open-terminal': 'Open terminal' };
function renderConnection() {
  const s = slot(), connection = connectionOf(s);
  const shown = !!s && !s.historical && connection.state !== 'live';
  $('connection').hidden = !shown;
  // One terminal action per state: the strip's own Open terminal replaces the composer's.
  $('open-terminal').hidden = !s || !!s.historical || (shown && connection.action === 'open-terminal');
  if (!shown) return;
  $('connection').className = `connection ${connection.state}`;
  $('connection-state').textContent = connection.label;
  $('connection-reason').textContent = connection.reason;
  $('connection-action').hidden = !connection.action;
  $('connection-action').textContent = ACTIONS[connection.action] || '';
  $('connection-action').dataset.action = connection.action || '';
}
$('connection-action').onclick = () => {
  const action = $('connection-action').dataset.action;
  if (action === 'link' || action === 'link-terminal') openLinkDialog(action === 'link-terminal');
  else if (action === 'refresh' || action === 'retry') refresh(true);
  else if (action === 'open-terminal' || action === 'choose-terminal') { setView('terminal'); if (action === 'choose-terminal') $('terminal-select').focus(); }
};
const answerDrafts = new Map(), requestOutcomes = new Map(), requestsInFlight = new Set();
let requestsSignature = '', stateOk = true;
const requestKey = (card, bound) => `${bound?.id || 'unbound'}/${card.request_id}/${card.revision}`;
function renderRequests() {
  const box = $('requests');
  const s = slot(), r = s?.requests;
  const terminal = s && state?.herdr?.terminals?.find(t => t.terminal === (activeBinding?.terminal || s.terminal_match?.terminal));
  const blocked = terminal?.status === 'blocked';
  const live = !!(r && r.state === 'live' && !s.historical && r.generation === s.generation);
  const bound = !!(activeBinding && !s?.historical && stateOk);
  const cards = live ? r.cards : [];
  const fallback = !!(r && !s.historical && r.state !== 'none' && blocked && !cards.length && (r.reason || r.note));
  const signature = JSON.stringify([sessionKey(), activeBinding?.id, live, bound, cards.map(c => c.request_id + c.revision),
    fallback ? (r.reason || r.note) : '', stateOk, [...requestsInFlight], [...requestOutcomes].map(([k, v]) => k + v.status)]);
  if (signature === requestsSignature) return;
  requestsSignature = signature;
  box.replaceChildren();
  box.hidden = !cards.length && !fallback;
  if (fallback) {
    const note = document.createElement('div'); note.className = 'request-fallback'; note.dataset.requestState = r.state;
    const text = document.createElement('span'); text.textContent = r.reason || r.note;
    const open = document.createElement('button'); open.type = 'button'; open.textContent = 'Open terminal'; open.onclick = () => setView('terminal');
    note.append(text, open); box.append(note);
  }
  for (const card of cards) box.append(requestCard(card, bound));
}
function requestCard(card, bound) {
  const key = requestKey(card, activeBinding);
  const busy = requestsInFlight.has(key);
  const enabled = bound && !busy;
  const element = document.createElement('article'); element.className = 'request-card'; element.dataset.request = card.request_id; element.dataset.kind = card.kind;
  element.setAttribute('role', 'group'); element.setAttribute('aria-label', `${card.kind === 'permission' ? 'Permission request' : 'Question'}${card.title ? ': ' + card.title : ''}`);
  const kind = document.createElement('div'); kind.className = 'request-kind'; kind.textContent = card.kind === 'permission' ? 'Permission request' : 'Question';
  element.append(kind);
  const actions = document.createElement('div'); actions.className = 'request-actions';
  const draft = answerDrafts.get(key) || {};
  answerDrafts.set(key, draft);
  const addButton = (label, onclick, primary) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.disabled = !enabled;
    if (primary) button.className = 'primary';
    button.title = bound ? '' : (stateOk ? 'Bind this role\'s exact terminal to answer here.' : 'Reconnecting. Actions stay off until the state is reconciled.');
    button.onclick = onclick; actions.append(button);
  };
  if (card.kind === 'permission') {
    const title = document.createElement('h3'); title.textContent = card.title || 'Permission'; element.append(title);
    if (card.details?.length) { const list = document.createElement('ul'); for (const d of card.details) { const li = document.createElement('li'); li.textContent = d; list.append(li); } element.append(list); }
    for (const choice of card.choices) addButton(choice.label, () => answerRequest(card, { reply: choice.id }, key), choice.id === 'once');
  } else {
    card.questions.forEach((q, index) => {
      const set = document.createElement('fieldset'); const legend = document.createElement('legend');
      legend.textContent = [q.header, q.question].filter(Boolean).join(': '); set.append(legend);
      draft[index] ||= { picked: [], custom: '' };
      for (const opt of q.options) {
        const label = document.createElement('label'); label.className = 'choice';
        const input = document.createElement('input'); input.type = q.multiple ? 'checkbox' : 'radio'; input.name = `q-${card.request_id}-${index}`;
        input.checked = draft[index].picked.includes(opt.label); input.disabled = !enabled;
        input.onchange = () => { draft[index].picked = q.multiple ?
          [...new Set(input.checked ? [...draft[index].picked, opt.label] : draft[index].picked.filter(x => x !== opt.label))] : [opt.label]; };
        const text = document.createElement('span'); text.textContent = opt.label;
        if (opt.description) { const small = document.createElement('small'); small.textContent = opt.description; text.append(small); }
        label.append(input, text); set.append(label);
      }
      if (q.custom) {
        const custom = document.createElement('input'); custom.type = 'text'; custom.placeholder = 'Other answer'; custom.value = draft[index].custom;
        custom.setAttribute('aria-label', `Other answer for ${q.header || q.question}`); custom.disabled = !enabled;
        custom.oninput = () => { draft[index].custom = custom.value; }; set.append(custom);
      }
      element.append(set);
    });
    addButton('Send answer', () => {
      const answers = card.questions.map((q, i) => {
        const d = draft[i] || { picked: [], custom: '' };
        const typed = d.custom.trim();
        return typed ? (q.multiple ? [...d.picked, typed] : [typed]) : d.picked;
      });
      answerRequest(card, { answers }, key);
    }, true);
    addButton('Reject', () => answerRequest(card, { reject: true }, key));
  }
  element.append(actions);
  const outcome = requestOutcomes.get(key);
  if (outcome) { const line = document.createElement('div'); line.className = `request-outcome ${outcome.status}`; line.textContent = outcome.text; element.append(line); }
  return element;
}
async function answerRequest(card, choice, key) {
  const bound = activeBinding;
  if (!bound || requestsInFlight.has(key)) return;
  requestsInFlight.add(key); requestsSignature = ''; renderRequests();
  try {
    const response = await fetch('/api/request/reply', { method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ binding: bound.id, request: card.request_id, revision: card.revision, ...choice }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Reply failed');
    const text = result.status === 'resolved' ? 'Answered.' :
      result.status === 'stale' ? `${result.reason} Nothing was sent.` :
      result.status === 'uncertain' ? `Uncertain: ${result.reason}` : `${result.status === 'refused' ? 'Not sent' : 'Failed'}: ${result.reason}`;
    requestOutcomes.set(key, { status: result.status, text });
    if (result.status === 'resolved') answerDrafts.delete(key);
  } catch (error) { requestOutcomes.set(key, { status: 'failed', text: `Failed: ${error.message}` }); }
  finally { requestsInFlight.delete(key); requestsSignature = ''; }
  refresh();
}
async function sendChat(event) {
  event.preventDefault();
  const s = slot(), bound = activeBinding, draft = roleKey(), key = bindingKey();
  const text = $('message').value;
  if (!s || !bound || !s.send_ready || !text.trim()) return;
  const operation = crypto.randomUUID();
  $('send').disabled = true;
  try {
    const response = await fetch('/api/send', { method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ binding: bound.id, operation, text }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Send failed');
    if (result.operation !== operation) throw new Error('Send acknowledgement mismatched the request.');
    const where = `${result.recipient?.slot || s.id}`;
    if (result.status === 'sent') {
      drafts.delete(draft);
      if (key === bindingKey()) { $('message').value = ''; delivery('sent', `Sent to ${where}.`); }
      else delivery('sent', `Sent to ${where}. Drafts for the current role are untouched.`);
    } else {
      delivery(result.status === 'uncertain' ? 'uncertain' : 'failed',
        `${result.status === 'uncertain' ? 'Uncertain' : 'Not sent'} to ${where}: ${result.reason || 'unknown reason'} Draft kept.`);
    }
  } catch (error) { delivery('failed', `Not sent: ${error.message} Draft kept.`); }
  composerState();
}
async function stopAgent() {
  const s = slot(), bound = activeBinding;
  if (!s || !bound || !s.stop_ready) return;
  $('stop').disabled = true;
  try {
    const response = await fetch('/api/stop', { method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ binding: bound.id }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Stop failed');
    const where = `${result.recipient?.slot || s.id}`;
    delivery(result.status === 'stopped' ? 'sent' : result.status,
      result.status === 'stopped' ? `Stopped ${where}.` :
      `${result.status === 'uncertain' ? 'Uncertain' : 'Stop failed'} for ${where}: ${result.reason || 'unknown reason'}`);
  } catch (error) { delivery('failed', `Stop failed: ${error.message}`); }
  composerState();
}
function attributionText(s) {
  if (s?.association?.state === 'linked') return s.association.kind === 'terminal' ? 'terminal linked by you' : 'linked by you';
  if (s?.history_attribution === 'dispatch-process') return 'matched by its dispatch';
  if (s?.history_attribution === 'task-ledger') return 'matched by its task record';
  return nativeOf(s) ? 'recorded session' : s?.process?.pid ? 'recorded process' : 'no recorded session';
}
function renderDetails() {
  const s = slot(), list = $('details-list');
  list.replaceChildren();
  if (!s) return;
  const bound = bindings.get(bindingKey());
  const rows = [
    ['Role', s.role], ['State', connectionOf(s).label],
    ['Session name', s.session || 'not registered'],
    ['Native session', nativeOf(s) ? `${nativeOf(s).harness} ${nativeOf(s).session_id}` : 'none recorded'],
    ['Process', s.process?.pid ? `${s.process.pid}:${s.process.start}` : 'none recorded'],
    ['Pane', bound?.pane || (s.terminal_match?.terminal ? 'matched, not bound yet' : 'none')],
    ['Attribution', attributionText(s)],
    ['Herdr agent', s.herdr_name || s.name_note || 'none recorded'],
    ['Recorded roles', (s.association?.state === 'linked' && s.association.kind !== 'terminal' ? s.association.recorded_roles : s.noted_roles)?.join(', ') || 'none'],
    ['Tasks', s.owners?.length ? s.owners.join(', ') : 'none'],
    ['Registrations', s.registrations?.length ? s.registrations.map(r => `${r.session} g${r.registration_generation}`).join(', ') : s.session ? `${s.session} g${s.registration_generation ?? 1}` : 'none']
  ];
  if (s.association) {
    rows.push(['Linked to', `${s.association.label} (${s.association.state})`]);
    if (s.association.reason) rows.push(['Link note', s.association.reason]);
  }
  if (s.mapping_issue) rows.push(['Mapping issue', s.mapping_issue]);
  for (const [name, value] of rows) {
    const dt = document.createElement('dt'); dt.textContent = name;
    const dd = document.createElement('dd'); dd.textContent = value;
    list.append(dt, dd);
  }
  if (s.association) {
    const unlink = document.createElement('button'); unlink.type = 'button'; unlink.id = 'unlink'; unlink.textContent = 'Remove link';
    unlink.onclick = () => changeLink({ clear: true });
    const dd = document.createElement('dd'); dd.append(unlink);
    list.append(document.createElement('dt'), dd);
  }
}
function selectRole(id) {
  if (selectedDraftKey) drafts.set(selectedDraftKey, $('message').value);
  disconnect(); slotId = id; activeBinding = bindings.get(bindingKey());
  saveSelection();
  selectedSessionKey = sessionKey(); selectedDraftKey = roleKey();
  const s = slot();
  // A companion-only link changes the role's generation, never the draft it was typing.
  if (carry?.slot === id) { if (!drafts.get(roleKey())) drafts.set(roleKey(), carry.text); carry = null; }
  $('message').value = drafts.get(roleKey()) || '';
  $('composer-label').textContent = s?.historical ? 'Earlier session \u00B7 Read only' : `Draft for ${s?.role || 'selected role'}`;
  delivery('', '');
  const connection = connectionOf(s);
  $('terminal-title').textContent = s ? `${cap(s.role)}${s.historical ? ' \u00B7 Earlier session' : ''}` : 'Select a role';
  $('crumb-role').textContent = s ? cap(s.role) : 'Select a role';
  $('role-avatar').textContent = s?.role[0].toUpperCase() || 'D';
  $('session-subtitle').textContent = s ? [nativeOf(s)?.harness || s.session || '', connection.label, attributionText(s), s.task_note].filter(Boolean).join(' \u00B7 ') : 'Your agents, one workspace.';
  $('history-state').textContent = s ? connection.label : 'Not connected';
  $('history-state').className = `badge${['needs-link', 'ambiguous', 'not-found', 'needs-you'].includes(connection.state) ? ' warning' : ''}`;
  $('session-source').textContent = s?.identity?.kind === 'native' ? `${s.identity.harness} \u00B7 ${s.identity.session_id}`
    : s?.identity?.kind === 'process' ? `process \u00B7 ${s.identity.pid}` : (s?.session || 'No session recorded');
  $('open-terminal').disabled = !s || !!s.historical;
  $('binding-label').textContent = activeBinding ? `${activeBinding.pane} \u00B7 ${activeBinding.attribution}` :
    s?.terminal_match?.ambiguous ? 'Multiple terminals match this role. Choose the exact terminal explicitly.' :
    'Choose the existing terminal for this role.';
  $('terminal-empty').hidden = !!activeBinding;
  renderRoles(); renderTerminals(); controls(); showReplaced(); renderDetails();
  composerState();
  $('terminal-select').value = activeBinding?.terminal || $('terminal-select').value;
  $('bind').disabled = !s || !!s.historical || !$('terminal-select').value;
  if (s?.historical && view === 'terminal') view = 'conversation';
  setView(view);
  renderHistoryChoices();
  loadHistoryChoices();
  loadHistory();
  maybeAutoBind();
}

const choiceKey = () => `${roleKey()}/${wideSessions.has(roleKey()) ? 'project' : 'run'}`;
function renderHistoryChoices() {
  const previous = $('history-select').value;
  const linked = linkedHistories.get(roleKey()) || '';
  const choices = historyChoices.get(choiceKey()) || [];
  $('history-select').replaceChildren(option('', 'Use recorded role session'),
    ...choices.map(c => option(c.id, `${c.label}${c.foreign ? ' \u00B7 other run' : ''}`)));
  for (const o of $('history-select').options) { const c = choices.find(x => x.id === o.value); if (c) o.title = `${c.native.harness} ${c.native.session_id}${c.recorded_roles.length ? ' \u00B7 recorded: ' + c.recorded_roles.join(', ') : ''}`; }
  $('history-select').value = choices.some(c => c.id === previous) ? previous : linked;
  $('history-scope').checked = wideSessions.has(roleKey());
  $('history-link-bar').hidden = !slot() || !!slot()?.historical;
  $('link-history').disabled = !slot() || !!slot()?.historical || $('history-select').value === linked;
}
// One read per key at a time: selecting a role, polling and re-rendering all ask for the
// same history, and each read costs the server a Python process.
const inflight = new Map();
const once = (key, work) => {
  if (inflight.has(key)) return inflight.get(key);
  const promise = work().finally(() => inflight.delete(key));
  inflight.set(key, promise);
  return promise;
};
async function loadHistoryChoices() {
  const s = slot(), key = choiceKey();
  if (!s || s.historical) return;
  return once(`choices/${key}`, () => readHistoryChoices(s, key));
}
async function readHistoryChoices(s, key) {
  try {
    const value = await api('history-choices', { run: runId, slot: s.id, generation: s.generation, scope: wideSessions.has(roleKey()) ? 'project' : 'run' });
    if (key !== choiceKey()) return;
    historyChoices.set(key, value.choices || []); renderHistoryChoices();
  } catch { if (key === choiceKey()) { historyChoices.set(key, []); renderHistoryChoices(); } }
}

function setView(next) {
  const changed = view !== next;
  view = next;
  document.querySelectorAll('[data-view]').forEach(button => {
    button.setAttribute('aria-selected', String(button.dataset.view === view));
    button.disabled = button.dataset.view === 'terminal' && !!slot()?.historical;
  });
  $('terminal-view').hidden = view !== 'terminal';
  $('history-view').hidden = view === 'terminal';
  chrome();
  if (view !== 'terminal') { if (ws) disconnect(); renderHistory(); }
  else if (activeBinding && (!ws || changed)) attach('observe');
  controls();
}
function timestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
const HISTORY_PAGE = 40, historyPages = new Map();
let pageScroll = null;
const atBottom = () => { const box = $('conversation'); return box.scrollHeight - box.scrollTop - box.clientHeight < 80; };
$('conversation').addEventListener('scroll', () => { $('history-latest').hidden = (historyPages.get(`${sessionKey()}/${view}`) || 0) === 0 && atBottom(); });
function renderPager(total, pages, page, from, to, capped) {
  const pager = $('history-pager');
  pager.hidden = pages < 2;
  $('history-older').disabled = page >= pages - 1;
  $('history-newer').disabled = page === 0;
  $('history-latest').hidden = page === 0 && atBottom();
  $('history-range').textContent = total ? `Events ${from + 1}-${to} of ${total}${capped ? ' (the latest 200 saved events are kept)' : ''}` : '';
}
function turnPage(direction) {
  const key = `${sessionKey()}/${view}`;
  const current = historyPages.get(key) || 0;
  const next = direction === 'latest' ? 0 : Math.max(0, current + (direction === 'older' ? 1 : -1));
  if (next === current) { if (direction === 'latest') { $('conversation').scrollTop = $('conversation').scrollHeight; $('history-latest').hidden = true; } return; }
  historyPages.set(key, next);
  pageScroll = direction === 'older' ? 'top' : 'bottom';
  renderHistory();
  const wanted = $(direction === 'older' ? 'history-older' : direction === 'newer' ? 'history-newer' : 'history-latest');
  (wanted.disabled || wanted.hidden ? [$('history-older'), $('history-newer'), $('history-latest')].find(b => !b.disabled && !b.hidden) || $('conversation') : wanted).focus();
}
$('history-older').onclick = () => turnPage('older');
$('history-newer').onclick = () => turnPage('newer');
$('history-latest').onclick = () => turnPage('latest');
// A round the role ran in a subagent: its own work, read from the subagent's transcript.
function subagentBadge() {
  const badge = document.createElement('span'); badge.className = 'subagent-badge'; badge.textContent = 'subagent';
  return badge;
}
function renderHistory() {
  const saved = histories.get(sessionKey()), container = $('conversation');
  const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
  const previousScroll = container.scrollTop;
  const expanded = new Set([...container.querySelectorAll('details[open]')].map(d => d.dataset.event));
  container.replaceChildren();
  if (saved?.selection) $('session-source').textContent = `${saved.provider} · ${saved.session} · Saved history you chose`;
  $('history-note').textContent = saved ? `${saved.reason}${saved.capabilities.history && saved.partial ? ' Content may be incomplete or shortened.' : ''}` : 'Reading recorded session history...';
  $('history-note').title = $('history-note').textContent;
  const all = (saved?.messages || []).filter(e => view !== 'activity' || e.role === 'tool');
  const pageKey = `${sessionKey()}/${view}`;
  const pages = Math.max(1, Math.ceil(all.length / HISTORY_PAGE));
  const page = Math.min(historyPages.get(pageKey) || 0, pages - 1);
  const end = all.length - page * HISTORY_PAGE;
  const events = all.slice(Math.max(0, end - HISTORY_PAGE), end);
  renderPager(all.length, pages, page, end - events.length, end, (saved?.messages || []).length >= 200);
  container.setAttribute('aria-busy', String(!saved));
  for (const event of events) {
    if (event.role === 'tool') {
      const card = document.createElement('details'); card.className = 'tool-card'; card.dataset.event = event.id;
      card.open = expanded.has(String(event.id));
      const summary = document.createElement('summary'); summary.textContent = event.name || 'Tool';
      // The call's own command or path says more than a status; the time stays on hover.
      const detail = document.createElement('span'); detail.className = 'tool-summary';
      detail.textContent = event.summary || event.status || 'recorded';
      if (event.subagent) { card.classList.add('subagent'); summary.prepend(subagentBadge()); }
      summary.title = [event.status || 'recorded', 'saved activity', timestamp(event.timestamp)].filter(Boolean).join(' · ');
      summary.append(detail);
      const body = document.createElement('pre'); body.textContent = event.text;
      card.append(summary, body); container.append(card);
    } else {
      const article = document.createElement('article'); article.className = `message ${event.role}`;
      const avatar = document.createElement('span'); avatar.className = 'message-avatar'; avatar.textContent = event.role === 'user' ? 'U' : (slot()?.role[0].toUpperCase() || 'A');
      const body = document.createElement('div'); body.className = 'message-content';
      const meta = document.createElement('div'); meta.className = 'message-meta'; meta.textContent = event.role === 'user' ? (event.subagent ? 'Task' : 'User') : slot()?.role || 'Assistant';
      if (event.subagent) { article.classList.add('subagent'); meta.append(subagentBadge()); }
      const time = document.createElement('small'); time.textContent = timestamp(event.timestamp); meta.append(time);
      // Inert Markdown: text nodes only, links as label plus literal URL, tokens masked.
      const text = document.createElement('div'); text.className = 'message-body';
      text.append(DocketMarkdown.render(document, DocketMarkdown.parse(event.text, { frontmatter: false })));
      body.append(meta, text); article.append(avatar, body); container.append(article);
    }
  }
  if (!saved && slot()) {
    // No result yet: a loading line, never the empty-state copy.
    const loading = document.createElement('div'); loading.className = 'skeleton'; loading.textContent = 'Loading this conversation...';
    container.append(loading);
  } else if (!events.length) {
    const empty = document.createElement('div'); empty.className = 'empty-state';
    const heading = document.createElement('h2'); heading.textContent = view === 'activity' ? 'No saved tool activity' : 'No saved messages for this session';
    const text = document.createElement('p'); text.textContent = slot() ? 'Nothing is recorded for this session. The note above says why.' : 'Choose a role to read its saved conversation.';
    empty.append(heading, text);
    container.append(empty);
  }
  container.scrollTop = pageScroll !== null ? (pageScroll === 'top' ? 0 : container.scrollHeight) : (nearBottom ? container.scrollHeight : previousScroll);
  pageScroll = null;
}
async function loadHistory() {
  const s = slot(), key = sessionKey();
  if (!s) { renderHistory(); return; }
  return once(`history/${key}`, () => readHistory(s, key));
}
async function readHistory(s, key) {
  try {
    const saved = await api('history', { run: runId, slot: s.id, generation: s.generation,
      choice: linkedHistories.get(roleKey()) || '', scope: wideSessions.has(roleKey()) ? 'project' : 'run' });
    if (key !== sessionKey()) return;
    histories.set(key, saved);
  } catch (error) {
    if (key !== sessionKey()) return;
    histories.set(key, { messages: [], capabilities: { history: false }, reason: error.message });
  }
  if (view !== 'terminal') renderHistory();
}
// The picker reads as the paper trail: the plan, then each task with its latest decision and
// report first and earlier rounds after them.
const DOC_ORDER = { decision: 0, report: 1, task: 2, scope: 3, handoff: 4 };
function documentGroups(docs, tasks) {
  const groups = [], seen = new Set();
  const take = (label, items) => { if (items.length) groups.push({ label, items }); items.forEach(i => seen.add(i.id)); };
  take('Plan', docs.filter(d => d.name === 'plan.mdx').map(d => ({ id: d.id, label: 'plan.mdx' })));
  for (const task of tasks) {
    const mine = docs.filter(d => d.name.startsWith(`${task.owner}-`)).map(d => {
      const m = /^.+?-(task|scope|report|decision|handoff)(?:-(\d+))?\.mdx$/.exec(d.name);
      return { d, kind: m?.[1] || 'other', round: Number(m?.[2] || 0) };
    });
    const latest = kind => Math.max(0, ...mine.filter(x => x.kind === kind).map(x => x.round));
    mine.sort((x, y) => (DOC_ORDER[x.kind] ?? 9) - (DOC_ORDER[y.kind] ?? 9) || y.round - x.round);
    const items = mine.map(({ d, kind, round }) => ({ id: d.id,
      label: round && round < latest(kind) ? `${cap(kind)} \u00B7 round ${round} (earlier)` : round ? `${cap(kind)} \u00B7 round ${round}${round === latest(kind) ? ' (latest)' : ''}` : cap(kind) }));
    items.sort((x, y) => Number(/earlier/.test(x.label)) - Number(/earlier/.test(y.label)));
    take(`${task.owner} \u00B7 ${statusText(task.status)}`, items);
  }
  take('Other', docs.filter(d => !seen.has(d.id)).map(d => ({ id: d.id, label: d.name })));
  return groups;
}
function renderDocuments() {
  const docs = discussionTab ? state?.conversations || [] : run()?.documents || [];
  const grouped = discussionTab ? [] : documentGroups(docs, run()?.tasks || []);
  if (discussionTab) $('document-select').replaceChildren(...docs.map(d => option(d.id, d.name)));
  else $('document-select').replaceChildren(...grouped.map(g => { const og = document.createElement('optgroup'); og.label = g.label; g.items.forEach(i => og.append(option(i.id, i.label))); return og; }));
  if (docs.some(d => d.id === documentId)) $('document-select').value = documentId;
  else documentId = (discussionTab ? docs : grouped[0]?.items || [])[0]?.id || docs[0]?.id;
  if (documentId) $('document-select').value = documentId;
  loadDocument();
}
let docView = 'rendered';
try { docView = sessionStorage.getItem('docket-doc-view') === 'source' ? 'source' : 'rendered'; } catch { /* Default view. */ }
let documentPlain = false, shownDocumentId;
function showDocument(text, plain) {
  documentPlain = !!plain;
  // Rendered output is built from text nodes only; Source shows the exact bytes.
  const rendered = plain ? false : docView === 'rendered';
  $('document-body').textContent = text;
  $('document-rendered').replaceChildren(rendered ? DocketMarkdown.render(document, DocketMarkdown.parse(text)) : '');
  $('document-body').hidden = rendered; $('document-rendered').hidden = !rendered;
  $('doc-rendered').setAttribute('aria-pressed', String(docView === 'rendered')); $('doc-source').setAttribute('aria-pressed', String(docView === 'source'));
  $('doc-rendered').disabled = $('doc-source').disabled = !!plain;
}
$('doc-rendered').onclick = () => { docView = 'rendered'; try { sessionStorage.setItem('docket-doc-view', docView); } catch { /* Convenience only. */ } showDocument(lastDocument); };
$('doc-source').onclick = () => { docView = 'source'; try { sessionStorage.setItem('docket-doc-view', docView); } catch { /* Convenience only. */ } showDocument(lastDocument); };
async function loadDocument() {
  const id = documentId;
  if (!id) {
    showDocument(discussionTab ? 'No discussion documents in this workspace.' : 'No run documents in this workspace.', true);
    $('document-name').textContent = 'No documents'; $('document-path').textContent = ''; lastDocument = ''; return;
  }
  // Never show the previous document's text under the next document's name.
  $('document-name').textContent = id.split('/').at(-1);
  $('document-path').textContent = `.docket/${id}`;
  if (shownDocumentId !== id) { showDocument('Loading document...', true); lastDocument = ''; }
  try {
    const doc = await api('document', { id });
    if (documentId !== id) return;
    shownDocumentId = id;
    if (lastDocument !== doc.text || documentPlain) { lastDocument = doc.text; showDocument(doc.text); }
  } catch (error) { if (documentId === id) showDocument(`Document unavailable: ${error.message}`, true); }
}
function renderRun() {
  const current = run();
  $('run-title').textContent = current?.title || 'Your agents, one workspace.';
  $('run-status').textContent = statusText(current?.status || 'No runs');
  $('run-status').className = `badge ${current?.status || ''}`;
  $('run-policy').textContent = current ? `${current.policy.mode || 'Unknown policy'} · ${current.policy.topology || 'unknown'} · ${current.slots.length} role sessions` : 'Start a Docket run in your project to see it here.';
  const tasks = current?.tasks || [];
  const done = tasks.filter(t => ['approved', 'waived', 'completed'].includes(t.status)).length;
  $('task-summary').textContent = tasks.length ? `${done} of ${tasks.length} tasks settled` : '';
  $('tasks').replaceChildren();
  tasks.forEach(t => {
    const button = document.createElement('button'); button.className = 'task';
    const id = document.createElement('span'); id.className = 'task-id'; id.textContent = t.owner;
    const dot = document.createElement('span'); dot.className = `dot ${['approved', 'completed'].includes(t.status) ? 'live' : t.status === 'blocked' ? 'attention' : ''}`;
    const label = document.createElement('small'); label.textContent = statusText(t.status);
    button.append(dot, id, label); button.title = t.title;
    button.onclick = () => { discussionTab = false; setDocumentTab(false); documentId = t.document; renderDocuments(); setDocs(true); setReading(true); };
    $('tasks').append(button);
  });
  renderRoles(); renderTerminals(); renderDocuments();
  if (current?.error) notice(`Run policy unavailable: ${current.error}`);
}
function renderProjects() {
  const projects = state?.projects || [];
  $('project-select').replaceChildren(...projects.map(p => option(p.id, p.name)));
  for (const [index, project] of projects.entries()) { $('project-select').options[index].title = project.path; }
  $('project-select').value = state?.project || '';
  $('remove-project').disabled = !state || state.project === projects[0]?.id;
  const current = projects.find(p => p.id === state?.project);
  $('project-path').textContent = current?.path || state?.workspace || ''; $('project-path').title = $('project-path').textContent;
}
function switchProject(id) {
  if (!id || id === projectId) return;
  // Pending sends, stops and request replies keep the binding they were made with;
  // selecting another project only changes what this page reads next.
  disconnect(); activeBinding = undefined; projectId = id; runId = undefined; slotId = undefined;
  selectedSessionKey = undefined; requestsSignature = '';
  try { sessionStorage.setItem('docket-project', id); } catch { /* Per-tab convenience only. */ }
  $('run-title').textContent = 'Loading project...';
  $('conversation').replaceChildren(Object.assign(document.createElement('div'), { className: 'skeleton', textContent: 'Loading this project\'s runs...' }));
  $('conversation').setAttribute('aria-busy', 'true');
  notice('');
  refresh(true);
}
$('project-select').onchange = () => switchProject($('project-select').value);
const projectDialog = $('project-dialog');
$('add-project').onclick = () => { $('project-path-input').value = ''; $('project-error').hidden = true; projectDialog.showModal(); $('project-path-input').focus(); };
$('project-cancel').onclick = () => projectDialog.close();
$('project-form').onsubmit = async event => {
  event.preventDefault();
  $('project-submit').disabled = true;
  try {
    const response = await fetch('/api/projects', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: $('project-path-input').value }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not add that project.');
    projectDialog.close();
    if (state) state.projects = result.projects;
    switchProject(result.project.id);
  } catch (error) { $('project-error').textContent = error.message; $('project-error').hidden = false; $('project-path-input').focus(); }
  finally { $('project-submit').disabled = false; }
};
$('remove-project').onclick = async () => {
  const id = state?.project;
  if (!id || $('remove-project').disabled) return;
  try {
    const response = await fetch('/api/projects/remove', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not remove that project.');
    for (const [key, bound] of bindings) if (bound.project === id) bindings.delete(key);
    state.projects = result.projects;
    switchProject(result.projects[0].id);
  } catch (error) { notice(error.message); }
};
const generations = new Map();
function noteReplacedSession() {
  // Every current slot of the run is tracked, not only the selected one, so a replacement is
  // seen however long ago the role was last open.
  for (const s of run()?.slots || []) {
    if (s.historical) continue;
    const key = `${state.project}/${runId}/${s.id}`;
    const before = generations.get(key), now = s.base_generation || s.generation;
    generations.set(key, now);
    if (before !== undefined && before !== now) replacedRoles.add(key);
  }
  showReplaced();
}
// Kept on the role itself, not in the shared notice line that other messages overwrite.
const replacedRoles = new Set();
function showReplaced() {
  const s = slot();
  const shown = !!(s && !s.historical && state && replacedRoles.has(`${state.project}/${runId}/${s.id}`));
  $('replaced-note').hidden = !shown;
  $('replaced-note').textContent = shown ? `${s.role[0].toUpperCase() + s.role.slice(1)} now has a new session. The previous conversation is read-only history, and its draft and bindings stay with it.` : '';
}
let refreshing = false, refreshFailed = false;
async function refresh(force = false) {
  if (refreshing && !force) return;
  refreshing = true;
  try {
    const asked = projectId;
    const received = await api('state');
    if (asked !== projectId) return;
    state = received;
    projectId = state.project;
    try { sessionStorage.setItem('docket-project', projectId); } catch { /* Per-tab convenience only. */ }
    stateOk = true;
    if (refreshFailed) { refreshFailed = false; notice(''); }
    renderProjects();
    const healthy = !state.herdr.error;
    $('server-state').textContent = healthy ? `Herdr ${state.herdr.version}` : 'Herdr offline';
    $('server-state').title = state.herdr.error || 'State badges are detection hints';
    $('server-dot').classList.toggle('live', healthy);
    $('run-select').replaceChildren(...state.runs.map(r => option(r.id, r.id)));
    const changed = !state.runs.some(r => r.id === runId);
    if (changed) {
      // Runs arrive newest first; a reload restores this tab's last run and role instead.
      const saved = readSelections()[projectId] || {};
      disconnect(); activeBinding = undefined;
      runId = state.runs.some(r => r.id === saved.run) ? saved.run : state.runs[0]?.id;
      slotId = runId === saved.run ? saved.slot : undefined;
    }
    $('run-select').value = runId || '';
    for (const [key, bound] of bindings) {
      if (bound.project !== state.project) continue;
      const r = state.runs.find(r => r.id === bound.run);
      const s = r?.slots.find(s => s.id === bound.slot);
      const terminal = state.herdr.terminals.find(t => t.terminal === bound.terminal);
      if (!s || s.historical || s.generation !== bound.generation || (healthy && (!terminal || terminal.server !== bound.server))) {
        bindings.delete(key);
        if (key === bindingKey()) { activeBinding = undefined; disconnect(); notice('Session changed. Bind its current terminal again.'); }
      }
    }
    renderRun();
    noteReplacedSession();
    if (changed || !run()?.slots.some(s => s.id === slotId) || selectedSessionKey !== sessionKey()) {
      selectRole(run()?.slots.some(s => s.id === slotId) ? slotId : run()?.slots.find(s => !s.historical)?.id || run()?.slots[0]?.id);
    } else { loadHistory(); loadHistoryChoices(); renderDetails(); composerState(); maybeAutoBind(); }
    $('updated').textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  } catch (error) { notice(error.message); refreshFailed = true; stateOk = false; requestsSignature = ''; renderRequests(); }
  finally { refreshing = false; }
}
$('run-select').onchange = () => {
  disconnect(); runId = $('run-select').value; slotId = undefined; activeBinding = undefined; documentId = undefined;
  renderRun(); selectRole(run()?.slots[0]?.id);
};
$('terminal-select').onchange = () => { $('bind').disabled = !slot() || !!slot()?.historical || !$('terminal-select').value; };
$('bind').onclick = async () => {
  const key = bindingKey();
  try {
    const bound = await api('binding', { run: runId, slot: slotId, terminal: $('terminal-select').value });
    if (key !== bindingKey()) return;
    bindings.set(key, bound); activeBinding = bound;
    $('binding-label').textContent = `${bound.pane} · ${bound.attribution}`;
    renderRoles(); renderTerminals(); composerState(); attach();
  } catch (error) { notice(error.message); }
};
$('control').onclick = () => attach(mode === 'control' ? 'observe' : 'control');
$('takeover').onclick = () => attach('control', true);
$('reconnect').onclick = () => attach();
$('open-terminal').onclick = () => setView('terminal');
$('history-select').onchange = renderHistoryChoicesState;
function renderHistoryChoicesState() {
  $('link-history').disabled = !slot() || !!slot()?.historical || $('history-select').value === (linkedHistories.get(roleKey()) || '');
}
$('link-history').onclick = () => {
  const choice = $('history-select').value;
  if (!slot() || slot().historical || (choice && !historyChoices.get(choiceKey())?.some(c => c.id === choice))) return;
  drafts.set(roleKey(), $('message').value);
  if (choice) linkedHistories.set(roleKey(), choice); else linkedHistories.delete(roleKey());
  try { sessionStorage.setItem('docket-history-links', JSON.stringify([...linkedHistories].slice(-512))); } catch { /* Per-tab convenience only. */ }
  selectRole(slotId);
  notice(choice ? 'Showing that saved conversation. Viewing history grants no input; sending still goes only to this role\'s proven live session.' : '');
};
$('history-scope').onchange = () => {
  const key = roleKey();
  if ($('history-scope').checked) wideSessions.add(key); else wideSessions.delete(key);
  renderHistoryChoices(); loadHistoryChoices();
};
document.querySelectorAll('[data-view]').forEach(button => { button.onclick = () => setView(button.dataset.view); });
$('message').oninput = () => { drafts.set(roleKey(), $('message').value); composerState(); };
$('message').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !$('send').disabled) { event.preventDefault(); $('composer').requestSubmit(); }
});
$('composer').onsubmit = sendChat;
$('stop').onclick = stopAgent;
$('refresh').onclick = () => refresh(true);
// Documents are optional. The wide-layout choice is remembered; a narrow screen always starts without the overlay.
const narrow = matchMedia('(max-width: 900px)');
let reading = false;
function docsPreference() {
  if (narrow.matches) return false;
  try { const saved = localStorage.getItem('docket-docs'); return saved ? saved === 'open' : innerWidth >= 1280; } catch { return innerWidth >= 1280; }
}
let docsOpen = docsPreference();
function applyDocs() {
  const workspace = document.querySelector('.workspace');
  workspace.classList.toggle('docs-closed', !docsOpen);
  workspace.classList.toggle('reading', docsOpen && reading && !narrow.matches);
  $('toggle-docs').setAttribute('aria-pressed', String(docsOpen));
  $('doc-reading').setAttribute('aria-pressed', String(reading)); $('doc-reading').textContent = reading ? 'Back to chat' : 'Reading mode';
}
function setDocs(open) {
  docsOpen = open;
  if (!open) reading = false;
  if (!narrow.matches) { try { localStorage.setItem('docket-docs', open ? 'open' : 'closed'); } catch { /* Preference only. */ } }
  applyDocs();
}
function setReading(on) { reading = on; if (on) docsOpen = true; applyDocs(); }
$('toggle-docs').onclick = () => setDocs(!docsOpen);
$('doc-close').onclick = () => setDocs(false);
$('doc-reading').onclick = () => setReading(!reading);
narrow.addEventListener('change', () => { docsOpen = docsPreference(); reading = false; applyDocs(); });
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return;
  if (reading) { setReading(false); $('doc-reading').focus(); }
  else if (docsOpen && narrow.matches) { setDocs(false); $('toggle-docs').focus(); }
  else if ($('project-menu').open) { $('project-menu').open = false; $('project-menu').querySelector('summary').focus(); }
});
document.addEventListener('click', event => { if (!$('project-menu').contains(event.target)) $('project-menu').open = false; });
applyDocs();

// Linking is companion-only and needs your confirmation; the server proves the live terminal again.
const linkDialog = $('link-dialog');
let linkChoices = [], linkingTerminal = false;
async function post(route, body) {
  const response = await fetch(`/api/${route}?${new URLSearchParams({ project: projectId || '' })}`, { method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
// A session link names a saved session for a role with none; a terminal link names the pane
// running a role whose own session is recorded but unproven.
async function openLinkDialog(terminal = false) {
  const s = slot();
  if (!s || s.historical) return;
  linkingTerminal = terminal;
  $('link-error').hidden = true; $('link-confirm').checked = false; $('link-submit').disabled = true;
  $('link-eyebrow').textContent = terminal ? 'LINK A TERMINAL' : 'LINK A SAVED SESSION';
  $('link-dialog-title').textContent = terminal ? 'Link a terminal to this role' : 'Link a session to this role';
  $('link-select-label').textContent = terminal ? 'Terminal' : 'Saved session';
  $('link-confirm-text').textContent = terminal ? 'I confirm this terminal runs this role\'s session.' : 'I confirm this saved session is this role\'s live terminal.';
  $('link-submit').textContent = terminal ? 'Link terminal' : 'Link as this role';
  $('link-detail').textContent = '';
  if (terminal) {
    const harness = nativeOf(s)?.harness;
    $('link-intro').textContent = `Docket cannot prove which terminal runs ${s.role}'s ${harness} session. Choose it yourself to enable chat. The link holds only while that terminal keeps the same ${harness} process; nothing in Docket is changed.`;
    linkChoices = state.herdr.terminals.filter(t => t.agent === harness).map(t => ({ id: t.terminal, label: `${t.name} \u00B7 ${t.pane}`, terminal: t }));
    $('link-select').replaceChildren(...(linkChoices.length ? linkChoices.map(c => option(c.id, c.label)) : [option('', `No ${harness} terminal in this project`)]));
    if (!linkChoices.length) $('link-detail').textContent = `Start ${harness} for this role in a Herdr terminal inside the project, then refresh.`;
    linkDialog.showModal();
    showLinkDetail();
    return;
  }
  $('link-intro').textContent = `${cap(s.role)} has no recorded session of its own. Choose the saved session of this run whose live terminal it is. Nothing in Docket is changed; this link stays in this companion.`;
  $('link-select').replaceChildren(option('', 'Loading saved sessions...'));
  linkDialog.showModal();
  try {
    const value = await api('history-choices', { run: runId, slot: s.id, generation: s.generation, scope: 'run' });
    linkChoices = (value.choices || []).filter(c => c.process);
  } catch (error) { linkChoices = []; $('link-error').textContent = error.message; $('link-error').hidden = false; }
  $('link-select').replaceChildren(...(linkChoices.length ? linkChoices.map(c => option(c.id, c.label)) : [option('', 'No provable saved session in this run')]));
  if (!linkChoices.length) $('link-detail').textContent = 'A saved session can be linked only when its exact native ID and recorded process identify one live terminal. Use the live terminal instead.';
  showLinkDetail();
}
function showLinkDetail() {
  const c = linkChoices.find(x => x.id === $('link-select').value);
  if (c?.terminal) $('link-detail').textContent = `${c.terminal.agent} \u00B7 ${statusText(c.terminal.status || 'unknown')} \u00B7 ${c.terminal.pane}`;
  else if (c) $('link-detail').textContent = `${c.native.harness} ${c.native.session_id} \u00B7 process ${c.process.pid}:${c.process.start} \u00B7 recorded: ${c.recorded_roles.join(', ') || 'none'}`;
  $('link-submit').disabled = !c || !$('link-confirm').checked;
}
$('link-select').onchange = showLinkDetail; $('link-confirm').onchange = showLinkDetail;
$('link-cancel').onclick = () => linkDialog.close();
async function changeLink(extra) {
  const s = slot(); if (!s) return;
  carry = { slot: s.id, text: $('message').value };
  try {
    await post('associate', { run: runId, slot: s.id, generation: s.generation, ...extra });
    bindings.delete(bindingKey()); activeBinding = undefined;
    await refresh(true);
    return true;
  } catch (error) { carry = null; throw error; }
}
$('link-form').onsubmit = async event => {
  event.preventDefault();
  $('link-submit').disabled = true;
  const target = linkingTerminal ? { terminal: $('link-select').value } : { choice: $('link-select').value };
  try { await changeLink({ ...target, confirm: true }); linkDialog.close(); notice(''); }
  catch (error) { $('link-error').textContent = error.message; $('link-error').hidden = false; showLinkDetail(); }
};
function setDocumentTab(discussions) {
  discussionTab = discussions;
  $('files-tab').setAttribute('aria-selected', String(!discussions));
  $('discussions-tab').setAttribute('aria-selected', String(discussions));
}
$('files-tab').onclick = () => { setDocumentTab(false); documentId = undefined; renderDocuments(); };
$('discussions-tab').onclick = () => { setDocumentTab(true); documentId = undefined; renderDocuments(); };
$('document-select').onchange = () => { documentId = $('document-select').value; loadDocument(); };
$('scroll-up').onclick = () => send({ type: 'scroll', direction: 'up', lines: 15 });
$('scroll-down').onclick = () => send({ type: 'scroll', direction: 'down', lines: 10000 });
document.querySelectorAll('[data-key]').forEach(button => {
  button.onclick = () => { input(JSON.parse(`"${button.dataset.key}"`)); term.focus(); };
});
function previewPaste(text = '') {
  if (!connected || mode !== 'control') return;
  pasteSelection = selection;
  $('paste-text').value = text; $('paste-dialog').showModal(); $('paste-text').focus();
}
$('paste').onclick = () => previewPaste();
$('send-paste').onclick = event => {
  if (pasteSelection !== selection || !connected || mode !== 'control') {
    event.preventDefault(); $('paste-dialog').close(); notice('Role selection changed. Paste was not sent.'); return;
  }
  input($('paste-text').value);
};
$('terminal').addEventListener('paste', event => {
  event.preventDefault(); event.stopImmediatePropagation();
  const text = event.clipboardData.getData('text/plain');
  if (/[\r\n]/.test(text)) previewPaste(text); else input(text);
}, true);
term.onData(input);
// Keep binary mouse reports disabled until backing application modes can be established.
term.attachCustomKeyEventHandler(event => {
  if (event.ctrlKey && event.shiftKey && event.code === 'KeyC' && term.hasSelection()) {
    if (event.type === 'keydown') navigator.clipboard.writeText(term.getSelection()).catch(() => notice('Use the browser copy menu for the selected text.'));
    return false;
  }
  return true;
});
$('terminal').addEventListener('wheel', event => {
  event.preventDefault();
  if (mode === 'control' && connected) send({ type: 'scroll', direction: event.deltaY < 0 ? 'up' : 'down', lines: 3 });
}, { passive: false, capture: true });
let resizeTimer;
new ResizeObserver(() => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (view === 'terminal' && connected && mode === 'control') send({ type: 'resize', ...size() }); }, 120);
}).observe($('terminal'));
window.addEventListener('pagehide', disconnect);
if (!token) notice('Open the private URL printed by npm start to connect this browser.');
else { refresh(); setInterval(refresh, 5000); }

// Tablists follow the arrow-key model: one tab stop, arrows move and activate.
for (const list of document.querySelectorAll('.view-tabs, .doc-tabs')) {
  list.addEventListener('keydown', event => {
    const tabs = [...list.querySelectorAll('[role="tab"]:not(:disabled)')];
    const at = tabs.indexOf(document.activeElement);
    if (at < 0 || !['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
      (at + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus(); tabs[next].click();
  });
}
function tabStops() {
  for (const list of document.querySelectorAll('.view-tabs, .doc-tabs')) {
    for (const tab of list.querySelectorAll('[role="tab"]')) tab.tabIndex = tab.getAttribute('aria-selected') === 'true' ? 0 : -1;
  }
}
new MutationObserver(tabStops).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['aria-selected'] });
tabStops();
