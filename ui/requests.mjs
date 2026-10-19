import { readdir, readlink, readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

// Live question and permission requests, per harness. Authority comes only from a
// verified process endpoint owned by the slot's proven terminal process. Nothing is
// read from screens, saved transcripts or tool statuses.
//
// Observed 2026-10-08 (see ui/README.md):
// - OpenCode 1.18.35: a default TUI listens on nothing; started with --port it serves
//   /global/health on loopback from the TUI pid. A permission created through the
//   v2 route POST /api/session/{id}/permission appears only in that route's pending
//   list (the legacy GET /permission stays empty), is answered by the v2 reply route
//   (204, then 404 for a second reply), and is NOT drawn by the TUI. The route that
//   the TUI's own model-triggered prompts use, and questions, were not observed.
//   Only the observed v2 permission path yields cards unless `experimental` is set.
// - Codex 0.160.1: approvals are server requests addressed to the owning client only.
// - Claude Code 2.1.294: no external endpoint for the interactive TUI.
export const OBSERVED = {
  opencode: { version: '1.18.35' },
  codex: { version: '0.160.1' },
  claude: { version: '2.1.294' }
};

const UNSUPPORTED = {
  claude: 'Claude Code 2.1.294 exposes no external request endpoint for its interactive TUI (permission prompt routing exists only for headless --print). Answer in the live terminal.',
  codex: 'Codex 0.160.1 delivers approval and input requests only to the client that owns the turn; no supported way to answer a TUI-owned request from another client was found. Answer in the live terminal.'
};

export const WITHHELD_NOTE = 'Questions and the TUI\'s own permission prompts were not observed through this endpoint, so they are not offered here. Answer them in the live terminal.';
const MAX_CARDS = 20;
const MAX_TEXT = 4000;
const REQUEST_ID = /^(per|que)[A-Za-z0-9_-]{1,120}$/;

const canonical = value => JSON.stringify(value, (_, v) =>
  v && typeof v === 'object' && !Array.isArray(v) ?
    Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : v);
const revisionOf = value => `sha256:${createHash('sha256').update(canonical(value)).digest('hex').slice(0, 24)}`;
const clip = value => (typeof value === 'string' ? value.slice(0, MAX_TEXT) : '');

class AdapterError extends Error {
  constructor(state, reason) { super(reason); this.state = state; this.reason = reason; }
}

const LOOPBACK = new Set(['0100007F', '00000000000000000000000001000000']);

function parseListeners(table, inodes) {
  const found = [];
  for (const line of table.split('\n').slice(1)) {
    const fields = line.trim().split(/\s+/);
    if (fields.length < 10 || fields[3] !== '0A' || !inodes.has(fields[9])) continue;
    const [address, port] = fields[1].split(':');
    found.push({ port: parseInt(port, 16), loopback: LOOPBACK.has(address) });
  }
  return found;
}

// TCP listeners owned by one process, from its own fd table and network namespace.
export async function listenersOf(pid, proc = '/proc') {
  const inodes = new Set();
  for (const fd of await readdir(`${proc}/${pid}/fd`)) {
    try {
      const match = /^socket:\[(\d+)\]$/.exec(await readlink(`${proc}/${pid}/fd/${fd}`));
      if (match) inodes.add(match[1]);
    } catch { /* descriptor closed while reading */ }
  }
  if (!inodes.size) return [];
  const tables = await Promise.all(['tcp', 'tcp6'].map(name =>
    readFile(`${proc}/${pid}/net/${name}`, 'utf8').catch(() => '')));
  return tables.flatMap(table => parseListeners(table, inodes));
}

async function call(fetchImpl, port, route, { method = 'GET', body, timeoutMs = 3000, headers } = {}) {
  let response;
  try {
    response = await fetchImpl(`http://127.0.0.1:${port}${route}`, {
      method, headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) });
  } catch (error) {
    throw new AdapterError(method === 'GET' ? 'unavailable' : 'uncertain',
      `Endpoint did not answer: ${error.name === 'TimeoutError' ? 'timed out' : error.message}`);
  }
  if (response.status === 401 || response.status === 403) {
    throw new AdapterError('auth-failed', 'The OpenCode endpoint requires credentials this companion does not hold. Answer in the live terminal.');
  }
  let data;
  try { data = await response.json(); } catch { data = undefined; }
  return { status: response.status, data };
}

const asList = (value, what) => {
  if (!Array.isArray(value)) throw new AdapterError('endpoint-unverified', `Unexpected ${what} response from the endpoint.`);
  return value;
};

function permissionCard(item) {
  return { kind: 'permission', source: 'legacy', request_id: item.id, session_id: item.sessionID,
    title: clip(item.permission), details: (Array.isArray(item.patterns) ? item.patterns : []).slice(0, 20).map(clip),
    always: (Array.isArray(item.always) ? item.always : []).slice(0, 20).map(clip),
    // The provider's reply enum: once, always, reject.
    choices: [{ id: 'once', label: 'Allow once' }, { id: 'always', label: 'Always allow' }, { id: 'reject', label: 'Reject' }],
    revision: revisionOf({ kind: 'permission', item }) };
}

function permissionV2Card(item) {
  return { kind: 'permission', source: 'v2', request_id: item.id, session_id: item.sessionID,
    title: clip(item.action), details: (Array.isArray(item.resources) ? item.resources : []).slice(0, 20).map(clip),
    always: (Array.isArray(item.save) ? item.save : []).slice(0, 20).map(clip),
    // The v2 reply enum: once, always, reject.
    choices: [{ id: 'once', label: 'Allow once' }, { id: 'always', label: 'Always allow' }, { id: 'reject', label: 'Reject' }],
    revision: revisionOf({ kind: 'permission', source: 'v2', item }) };
}

function questionCard(item) {
  const questions = (Array.isArray(item.questions) ? item.questions : []).slice(0, 10).map(q => ({
    header: clip(q.header), question: clip(q.question), multiple: !!q.multiple, custom: q.custom !== false,
    options: (Array.isArray(q.options) ? q.options : []).slice(0, 30)
      .map(o => ({ label: clip(o.label), description: clip(o.description) })) }));
  return { kind: 'question', request_id: item.id, session_id: item.sessionID, questions,
    revision: revisionOf({ kind: 'question', item }) };
}

function collect(items, prefix, sessionId, build) {
  const seen = new Map();
  const cards = [];
  for (const item of asList(items, prefix === 'per' ? 'permission' : 'question')) {
    if (!item || typeof item.id !== 'string' || !REQUEST_ID.test(item.id) || !item.id.startsWith(prefix) ||
        typeof item.sessionID !== 'string') {
      throw new AdapterError('endpoint-unverified', 'The endpoint returned a malformed request.');
    }
    const digest = canonical(item);
    if (seen.has(item.id)) {
      if (seen.get(item.id) !== digest) {
        throw new AdapterError('contradictory', `The provider reported request ${item.id} with conflicting content. Answer in the live terminal.`);
      }
      continue;
    }
    seen.set(item.id, digest);
    // Another session's request is never this role's authority.
    if (item.sessionID === sessionId) cards.push(build(item));
  }
  return cards;
}

// Fresh authoritative read of one OpenCode endpoint, scoped to one native session.
// The default read is the observed v2 permission list only; the legacy permission and
// question lists are read just when `experimental` opts in.
export async function openCodeSnapshot({ port, sessionId, fetchImpl = fetch, timeoutMs = 3000, headers, experimental = false }) {
  const health = await call(fetchImpl, port, '/global/health', { timeoutMs, headers });
  if (health.status !== 200 || health.data?.healthy !== true) {
    throw new AdapterError('endpoint-unverified', 'The listener did not identify itself as a healthy OpenCode server.');
  }
  const get = route => call(fetchImpl, port, route, { timeoutMs, headers });
  const v2 = await get(`/api/session/${encodeURIComponent(sessionId)}/permission`);
  if (v2.status === 404) {
    // Observed: the provider answers 404 for a session id it does not hold.
    throw new AdapterError('session-unknown', 'This OpenCode server does not know the role\'s recorded native session. Answer in the live terminal.');
  }
  if (v2.status !== 200) throw new AdapterError('endpoint-unverified', `The endpoint answered ${v2.status} for its request list.`);
  const cards = collect(v2.data?.data, 'per', sessionId, permissionV2Card);
  if (experimental) {
    const [permissions, questions] = await Promise.all([get('/permission'), get('/question')]);
    for (const part of [permissions, questions]) {
      if (part.status !== 200) throw new AdapterError('endpoint-unverified', `The endpoint answered ${part.status} for its request list.`);
    }
    cards.push(...collect(permissions.data, 'per', sessionId, permissionCard), ...collect(questions.data, 'que', sessionId, questionCard));
  }
  return { version: health.data.version || null, cards: cards.slice(0, MAX_CARDS) };
}

// The request state of one slot's proven terminal, never throwing.
export async function requestState({ slot, terminal, listeners = listenersOf, fetchImpl = fetch, timeoutMs = 3000, experimental = false }) {
  const harness = terminal?.agent;
  if (!terminal || !harness) return { state: 'none', reason: 'No live agent on the proven terminal.', cards: [] };
  if (harness !== 'opencode') {
    return { state: 'unsupported', harness, reason: UNSUPPORTED[harness] ||
      `No verified request endpoint is known for ${harness}. Answer in the live terminal.`, cards: [] };
  }
  const sessionId = slot.native?.session_id;
  if (!sessionId) {
    return { state: 'no-session', harness, cards: [],
      reason: 'No native session is recorded for this role, so requests cannot be attributed to it. Answer in the live terminal.' };
  }
  if (!terminal.process?.pid) {
    return { state: 'endpoint-unverified', harness, cards: [], reason: 'The terminal process identity is unavailable. Answer in the live terminal.' };
  }
  let found;
  try { found = await listeners(terminal.process.pid); }
  catch { return { state: 'endpoint-unverified', harness, cards: [], reason: 'The terminal process could not be inspected. Answer in the live terminal.' }; }
  const local = found.filter(item => item.loopback);
  if (!found.length) {
    return { state: 'endpoint-absent', harness, cards: [],
      reason: 'This OpenCode TUI exposes no request endpoint (it was started without --port). Answer in the live terminal.' };
  }
  if (local.length !== 1 || found.length !== local.length) {
    return { state: local.length > 1 ? 'endpoint-ambiguous' : 'endpoint-unverified', harness, cards: [],
      reason: local.length > 1 ? 'The process listens on several loopback ports; none is trusted. Answer in the live terminal.' :
        'The process listens on a non-loopback address; it is not trusted. Answer in the live terminal.' };
  }
  try {
    const snapshot = await openCodeSnapshot({ port: local[0].port, sessionId, fetchImpl, timeoutMs, experimental });
    return { state: 'live', harness, version: snapshot.version, port: local[0].port, session_id: sessionId,
      cards: snapshot.cards, reason: '', note: experimental ? '' : WITHHELD_NOTE };
  } catch (error) {
    if (error instanceof AdapterError) return { state: error.state, harness, cards: [], reason: error.reason };
    return { state: 'unavailable', harness, cards: [], reason: `Request state could not be read: ${error.message}` };
  }
}

function checkAnswers(card, body) {
  if (card.kind === 'permission') {
    if (!card.choices.some(choice => choice.id === body.reply)) {
      throw new AdapterError('refused', 'That reply is not one the provider offered for this request.');
    }
    const route = card.source === 'v2' ?
      `/api/session/${encodeURIComponent(card.session_id)}/permission/${encodeURIComponent(card.request_id)}/reply` :
      `/permission/${encodeURIComponent(card.request_id)}/reply`;
    return { route, payload: { reply: body.reply } };
  }
  if (body.reject === true) return { route: `/question/${encodeURIComponent(card.request_id)}/reject`, payload: undefined };
  const { answers } = body;
  if (!Array.isArray(answers) || answers.length !== card.questions.length ||
      !answers.every(a => Array.isArray(a) && a.every(x => typeof x === 'string' && x.length <= 2000))) {
    throw new AdapterError('refused', 'Answer every question in order.');
  }
  answers.forEach((answer, index) => {
    const q = card.questions[index];
    if (!answer.length || (!q.multiple && answer.length > 1)) throw new AdapterError('refused', `Question ${index + 1} needs ${q.multiple ? 'at least one' : 'exactly one'} answer.`);
    for (const value of answer) {
      if (!q.custom && !q.options.some(o => o.label === value)) {
        throw new AdapterError('refused', `Question ${index + 1} only accepts the offered options.`);
      }
    }
  });
  return { route: `/question/${encodeURIComponent(card.request_id)}/reply`, payload: { answers } };
}

// One guarded reply. It re-reads the endpoint, requires the same request and revision,
// posts exactly once, and never retries: an unclear outcome stays uncertain.
export async function replyToRequest({ port, sessionId, requestId, revision, body, fetchImpl = fetch, timeoutMs = 3000, experimental = false }) {
  let before;
  try { before = await openCodeSnapshot({ port, sessionId, fetchImpl, timeoutMs, experimental }); }
  catch (error) {
    return { status: 'refused', reason: error instanceof AdapterError ? error.reason : `Endpoint unavailable: ${error.message}` };
  }
  const card = before.cards.find(c => c.request_id === requestId);
  if (!card) return { status: 'stale', reason: 'That request is no longer pending. It was resolved elsewhere or replaced.' };
  if (card.revision !== revision) return { status: 'stale', reason: 'That request changed after it was shown. Review it again.' };
  let plan;
  try { plan = checkAnswers(card, body); }
  catch (error) { return { status: 'refused', reason: error.reason || error.message }; }
  let posted;
  try { posted = await call(fetchImpl, port, plan.route, { method: 'POST', body: plan.payload, timeoutMs }); }
  catch (error) {
    return error.state === 'auth-failed' ? { status: 'failed', reason: error.reason } :
      { status: 'uncertain', reason: `${error.reason || error.message} It was not resent; check the live terminal.` };
  }
  if (posted.status === 404) return { status: 'stale', reason: 'The provider no longer knows that request.' };
  if (posted.status < 200 || posted.status >= 300 || posted.data === false) {
    return { status: 'failed', reason: `The provider refused the reply (${posted.status}).` };
  }
  try {
    const after = await openCodeSnapshot({ port, sessionId, fetchImpl, timeoutMs, experimental });
    if (after.cards.some(c => c.request_id === requestId)) {
      return { status: 'uncertain', reason: 'The provider accepted the reply but still lists the request. It was not resent.' };
    }
  } catch { return { status: 'uncertain', reason: 'The reply was posted but its result could not be confirmed. It was not resent.' }; }
  return { status: 'resolved', kind: card.kind };
}
