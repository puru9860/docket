import http from 'node:http';
import { readFile, realpath } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { parseArgs } from 'node:util';
import { WebSocketServer, WebSocket } from 'ws';
import { Herdr, processAgeSeconds } from './herdr.mjs';
import { listenersOf, requestState, replyToRequest } from './requests.mjs';
import { createProjects, ProjectError } from './projects.mjs';
import { createAssociations, applyAssociations, eligibility, proveAssociation, recordFor,
  proveTerminalLink, terminalEligibility, terminalRecordFor } from './associations.mjs';

const BOOT_GRACE_SECONDS = 60;

const directory = path.dirname(fileURLToPath(import.meta.url));
const execute = promisify(execFile);
const assets = new Map([
  ['/', ['public/index.html', 'text/html']],
  ['/app.js', ['public/app.js', 'text/javascript']],
  ['/markdown.js', ['public/markdown.js', 'text/javascript']],
  ['/style.css', ['public/style.css', 'text/css']],
  ['/vendor/xterm.js', ['node_modules/@xterm/xterm/lib/xterm.js', 'text/javascript']],
  ['/vendor/fit.js', ['node_modules/@xterm/addon-fit/lib/addon-fit.js', 'text/javascript']],
  ['/vendor/xterm.css', ['node_modules/@xterm/xterm/css/xterm.css', 'text/css']]
]);

export function dimensions(message) {
  const { cols, rows } = message;
  if (!Number.isInteger(cols) || cols < 20 || cols > 500 ||
      !Number.isInteger(rows) || rows < 5 || rows > 200) throw new Error('Invalid terminal dimensions');
  return { cols, rows };
}

export function resolveTerminal(slot, terminals) {
  // Exactly one proven match by native session or process identity, never a guess.
  const native = slot.native || {};
  const process = slot.process || {};
  const matches = terminals.filter(terminal => {
    if (native.session_id && terminal.native === native.session_id) return true;
    if (process.pid && process.start && terminal.process &&
        terminal.process.pid === process.pid && terminal.process.start === process.start) return true;
    // Herdr keeps an agent name unique among live agents and clears it when that agent exits.
    if (slot.herdr_name && terminal.agent_name === slot.herdr_name && terminal.agent &&
        (!native.harness || terminal.agent === native.harness)) return true;
    return false;
  });
  if (matches.length === 1) {
    const match = matches[0];
    return { terminal: match.terminal,
      attribution: native.session_id && match.native === native.session_id ? 'native-session' :
        process.pid && match.process?.pid === process.pid ? 'process-only' : 'herdr-name' };
  }
  return { terminal: null, ambiguous: matches.length > 1, candidates: matches.map(t => t.terminal) };
}

const OPERATION_LIMIT = 512;
const SENDABLE = new Set(['idle', 'done']);

// Interrupt is offered only for harnesses with observed safe turn-interrupt
// semantics. Live testing showed send-keys ctrl+c terminates the OpenCode TUI
// instead of stopping its turn, and Escape leaves the turn running; Claude and
// Codex have no safe-interrupt observation at all. Unknown kinds default to
// denied. The transport stays implemented and tested; policy alone gates it.
const INTERRUPT_POLICY = {
  opencode: { offered: false,
    reason: 'Interrupt is not offered for OpenCode: ctrl+c terminated its TUI in live testing and Escape did not stop the turn. Use the live terminal.' },
  claude: { offered: false,
    reason: 'Interrupt is not offered for Claude: no safe turn-interrupt semantics observed. Use the live terminal.' },
  codex: { offered: false,
    reason: 'Interrupt is not offered for Codex: no safe turn-interrupt semantics observed. Use the live terminal.' }
};
const interruptRule = kind => INTERRUPT_POLICY[kind] ||
  { offered: false, reason: `Interrupt is not offered: no safe turn-interrupt semantics observed for ${kind || 'this harness'}. Use the live terminal.` };

function findTerminal(terminals, terminalId) {
  return terminals.find(t => t.terminal === terminalId) || null;
}

export function provenTerminal(slot, terminals, capability) {
  // The slot's single proven terminal for a send-family capability, or null.
  // Identity proof only; liveness (agent presence, status) is checked by callers.
  if (slot.historical || slot.mapping_issue || !slot.capabilities?.[capability]) {
    return { terminal: null, ambiguous: false };
  }
  const match = resolveTerminal(slot, terminals);
  if (!match.terminal) return { terminal: null, ambiguous: !!match.ambiguous };
  return { terminal: findTerminal(terminals, match.terminal), ambiguous: false };
}

function sendNoteFor(slot, terminal, offline, ambiguous, youngAge) {
  if (slot.historical) return 'Earlier sessions are read-only.';
  if (slot.mapping_issue) return 'Role identity is ambiguous. Resolve the mapping before sending.';
  if (!slot.capabilities?.send) return slot.association && slot.association.state !== 'linked' ? slot.association.reason :
    'This role has no recorded session identity, so Docket cannot tell which terminal it is. Link a saved session to enable chat.';
  if (offline) return 'Herdr is offline. Use saved history.';
  if (ambiguous) return 'Multiple terminals match this role. Resolve the exact terminal first.';
  if (!terminal) return 'No proven terminal for this role right now.';
  if (!terminal.agent) return 'No live agent on this terminal. Use the live terminal.';
  if (youngAge !== undefined) {
    return `Agent process started ${Math.max(0, Math.round(youngAge))}s ago; readiness unconfirmed so soon after boot. Try again shortly.`;
  }
  const status = terminal.status || 'unknown';
  if (status === 'working') return 'Agent is working. Stop it first or wait.';
  if (status === 'blocked') return 'Agent is waiting at a question or permission. Answer in the live terminal.';
  if (!SENDABLE.has(status)) return `Agent state is ${status}. Use the live terminal.`;
  return '';
}

// One plain state word per role plus the exact reason and the single recovery action.
const SHORT_REASONS = { 'needs-link': 'Chat is off: no linked session.', ambiguous: 'Chat is off: the session is ambiguous.',
  'not-found:retry': 'Chat is off: Herdr is offline.', 'not-found:refresh': 'Chat is off: no matching terminal.', 'not-found:link-terminal': 'Chat is off: link this role\'s terminal.',
  'not-found:open-terminal': 'Chat is off: no agent in that terminal. Use the live terminal.', starting: 'Chat is off: the agent just started.',
  working: 'Agent is working; Send waits.', 'needs-you': 'Agent is waiting at a question. Use the live terminal.' };
// The strip holds the full reason; the composer carries this short one that still names the blocker.
export function connectionOf(slot, context = {}) {
  const connection = connectionDetail(slot, context);
  connection.short = SHORT_REASONS[`${connection.state}:${connection.action}`] || SHORT_REASONS[connection.state] || '';
  return connection;
}
function connectionDetail(slot, { terminal, ambiguous = false, offline = false, young } = {}) {
  const association = slot.association;
  if (slot.historical) return { state: 'saved', label: 'Saved only', reason: 'Earlier sessions are read-only history.', action: null };
  if (slot.mapping_issue) return { state: 'ambiguous', label: 'Ambiguous', reason: slot.mapping_issue, action: 'link' };
  if (!slot.capabilities?.send) {
    return { state: 'needs-link', label: 'Needs link',
      reason: association && association.state !== 'linked' ? association.reason :
        'This role has no recorded session, so Docket cannot tell which terminal it is. Link a saved session to enable chat.',
      action: 'link' };
  }
  if (offline) return { state: 'not-found', label: 'Not found', reason: 'Herdr is offline. Saved history stays readable.', action: 'retry' };
  if (ambiguous) return { state: 'ambiguous', label: 'Ambiguous', reason: 'Several terminals match this role. Choose the exact terminal.', action: 'choose-terminal' };
  if (!terminal && slot.herdr_name) return { state: 'not-found', label: 'Not found', reason: `No live Herdr agent named ${slot.herdr_name}${slot.native?.harness ? ` running ${slot.native.harness}` : ''} is in this project. Refresh once it is running again.`, action: 'refresh' };
  if (!terminal && association?.kind === 'terminal') return { state: 'not-found', label: 'Not found', reason: association.reason, action: 'link-terminal' };
  // Codex runs tools under a shared app-server, so its sessions record no terminal process.
  if (!terminal && slot.native?.session_id) return { state: 'not-found', label: 'Not found', reason: 'No terminal Herdr can see proves this role\'s recorded session. If you know which terminal runs it, link that terminal to enable chat.', action: 'link-terminal' };
  if (!terminal) return { state: 'not-found', label: 'Not found', reason: 'No terminal Herdr can see matches this role\'s recorded session. Refresh, or open a terminal to bind one yourself (view and control only, no chat).', action: 'refresh' };
  if (!terminal.agent) return { state: 'not-found', label: 'Not found', reason: 'A terminal matches but no agent is running in it. Use the live terminal.', action: 'open-terminal' };
  if (young !== undefined) {
    return { state: 'starting', label: 'Starting', reason: `Agent process started ${Math.max(0, Math.round(young))}s ago; readiness unconfirmed so soon after boot. Try again shortly.`, action: 'refresh' };
  }
  const status = terminal.status || 'unknown';
  if (status === 'working') return { state: 'working', label: 'Working', reason: 'Agent is working. Wait for it to finish.', action: null };
  if (status === 'blocked') return { state: 'needs-you', label: 'Needs you', reason: 'Agent is waiting at a question or permission. Answer in the live terminal.', action: 'open-terminal' };
  if (!SENDABLE.has(status)) return { state: 'not-found', label: 'Not ready', reason: `Agent state is ${status}. Use the live terminal.`, action: 'open-terminal' };
  return { state: 'live', label: 'Live', reason: '', action: null };
}

export async function inspect(workspace, document) {
  const args = ['-B', path.join(directory, 'inspection.py'), workspace];
  if (document !== undefined) args.push(document);
  const { stdout } = await execute('python3', args, { maxBuffer: 8 * 1024 * 1024, timeout: 5000 });
  return JSON.parse(stdout);
}

export async function history(workspace, run, slot, generation, choice, scope = 'run') {
  const args = ['-B', path.join(directory, 'inspection.py'), workspace, '--history', run, slot, generation, choice || '', scope];
  const { stdout } = await execute('python3', args, { maxBuffer: 8 * 1024 * 1024, timeout: 10000 });
  return JSON.parse(stdout);
}

export async function historyChoices(workspace, run, slot, generation, scope = 'run') {
  const { stdout } = await execute('python3', ['-B', path.join(directory, 'inspection.py'), workspace,
    '--history-choices', run, slot, generation, scope], { maxBuffer: 1024 * 1024, timeout: 5000 });
  return JSON.parse(stdout);
}

export async function createCompanion({ workspace, socketPath, port = 0, herdr, inspector = inspect,
  historyReader = history, choiceReader = historyChoices, promptTimeoutMs = 30000, interruptTimeoutMs = 10000,
  interruptPolicy = INTERRUPT_POLICY, ageOf = processAgeSeconds, requestListeners = listenersOf,
  requestFetch = fetch, requestTimeoutMs = 3000, experimentalRequests = false, preferencesPath, associationsPath }) {
  workspace = await realpath(workspace);
  const registry = await createProjects({ initial: workspace, preferencesPath });
  const associations = await createAssociations({
    preferencesPath: associationsPath || (preferencesPath && path.join(path.dirname(preferencesPath), 'associations.json')) });
  herdr ||= new Herdr(socketPath, workspace);
  const token = randomBytes(32).toString('hex');
  const bindings = new Map();
  const clients = new Set();
  const operations = new Map();
  const pendingSends = new Map();
  const answered = new Map();
  const pendingAnswers = new Map();
  let host;
  const authorized = value => {
    if (typeof value !== 'string') return false;
    const received = Buffer.from(value), expected = Buffer.from(token);
    return received.length === expected.length && timingSafeEqual(received, expected);
  };
  const json = (response, status, data) => {
    response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify(data));
  };
  const ruleFor = kind => interruptPolicy[kind] || interruptRule(kind);
  // A binding pins its project: a later selection elsewhere never changes where it acts.
  function projectOf(binding) {
    const project = registry.get(binding.project);
    if (!project) throw new Error('This project was removed from the list. Bind a terminal again.');
    return project;
  }
  async function validate(binding) {
    const state = await stateOf(projectOf(binding));
    const run = state.runs.find(r => r.id === binding.run);
    const slot = run?.slots.find(s => s.id === binding.slot);
    if (!slot || slot.historical || slot.generation !== binding.generation) {
      throw new Error('Docket role or session generation changed. Bind a terminal again.');
    }
    const identity = await herdr.identity(binding.terminal);
    if (identity.identity !== binding.identity) throw new Error('Terminal occupant changed. Bind a terminal again.');
    return identity;
  }
  function operationKey(binding, operation) {
    return [binding.project, binding.run, binding.slot, binding.generation, binding.terminal,
      binding.identity, operation].join('');
  }
  function rememberOperation(key, result) {
    operations.delete(key);
    operations.set(key, result);
    while (operations.size > OPERATION_LIMIT) {
      operations.delete(operations.keys().next().value);
    }
  }
  async function withProcessIdentities(inventory, slots) {
    const needed = !inventory.error && (inventory.terminals.some(t => t.agent) ||
      slots.some(s => !s.historical && s.process?.pid && s.process?.start));
    if (!needed) return inventory.terminals;
    const identities = await herdr.terminalIdentities(inventory);
    return inventory.terminals.map(t => ({ ...t, process: identities[t.terminal] ?? null }));
  }
  // Fresh Docket state with stored associations re-proved against the live terminals.
  async function stateOf(project, prefetched) {
    const state = await inspector(project.path);
    if (!prefetched && !associations.list().some(a => a.project === project.id)) {
      for (const run of state.runs) for (const slot of run.slots) slot.base_generation = slot.generation;
      return state;
    }
    let terminals = prefetched;
    if (!terminals) {
      try { terminals = await withProcessIdentities(await herdr.inventory(project.path), state.runs.flatMap(run => run.slots)); }
      catch { terminals = []; }
    }
    return applyAssociations(state, project.id, associations, terminals);
  }
  async function liveAgent(binding, capability, { grace = capability === 'send' } = {}) {
    const project = projectOf(binding);
    const state = await stateOf(project);
    const slot = state.runs.find(r => r.id === binding.run)?.slots.find(s => s.id === binding.slot);
    if (!slot || slot.historical || slot.generation !== binding.generation) {
      throw new Error('Docket role or session generation changed. Bind a terminal again.');
    }
    if (slot.mapping_issue || !slot.capabilities?.[capability]) {
      throw new Error('Role identity is ambiguous or unproven. Resolve the mapping before sending.');
    }
    const identity = await herdr.identity(binding.terminal);
    if (identity.identity !== binding.identity) throw new Error('Terminal occupant changed. Bind a terminal again.');
    let inventory;
    try { inventory = await herdr.inventory(project.path); }
    catch (error) { throw new Error(`Herdr is unreachable: ${error.message}`); }
    const enriched = await withProcessIdentities(inventory, [slot]);
    const { terminal: proven, ambiguous } = provenTerminal(slot, enriched, capability);
    if (!proven) {
      throw new Error(ambiguous ? 'Multiple terminals match this role. Resolve the exact terminal first.' :
        'No proven terminal for this role right now.');
    }
    if (proven.terminal !== binding.terminal) {
      throw new Error('This binding is not the role\'s proven terminal. Bind its exact terminal.');
    }
    if (!proven.agent) throw new Error('No live agent on this terminal. Use the live terminal.');
    if (grace && proven.process) {
      const age = await ageOf(proven.process.pid, proven.process.start);
      if (typeof age === 'number' && age < BOOT_GRACE_SECONDS) {
        throw new Error(`Agent process started ${Math.max(0, Math.round(age))}s ago; readiness unconfirmed so soon after boot. Try again shortly.`);
      }
    }
    let status = 'unknown';
    try {
      const record = await herdr.agentRecord(proven.pane);
      status = record?.agent_status || record?.status || 'unknown';
    } catch (error) { throw new Error(`Agent is unreachable: ${error.message || error}`); }
    return { slot, terminal: proven, status };
  }
  const recipientOf = (binding, terminal) => ({ project: binding.project, run: binding.run, slot: binding.slot,
    agent: terminal.agent, terminal: terminal.terminal });
  async function sendMessage(body) {
    const bindingId = body?.binding, operation = body?.operation, text = body?.text;
    if (typeof bindingId !== 'string' || !bindingId ||
        typeof operation !== 'string' || !operation || operation.length > 128 ||
        typeof text !== 'string' || !text.trim() || text.length > 65536) {
      throw new Error('Invalid send request');
    }
    const binding = bindings.get(bindingId);
    if (!binding) throw new Error('Binding expired. Bind a terminal again.');
    const key = operationKey(binding, operation);
    if (operations.has(key)) return { ...operations.get(key), duplicate: true };
    if (pendingSends.has(key)) return pendingSends.get(key);
    const job = (async () => {
      const { terminal, status } = await liveAgent(binding, 'send');
      const recipient = recipientOf(binding, terminal);
      if (!SENDABLE.has(status)) {
        throw new Error(status === 'working' ? 'Agent is working. Stop it first or wait.' :
          status === 'blocked' ? 'Agent is waiting at a question or permission. Answer in the live terminal.' :
          `Agent state is ${status}. Use the live terminal.`);
      }
      const submitted = await herdr.agentPrompt(terminal.pane, text, promptTimeoutMs);
      if (submitted.outcome === 'sent') {
        return { operation, status: 'sent', recipient, detail: submitted.detail };
      }
      if (submitted.outcome === 'blocked') {
        return { operation, status: 'failed', recipient,
          reason: `Agent refused the prompt at a question or permission: ${submitted.detail}` };
      }
      if (submitted.outcome === 'uncertain') {
        return { operation, status: 'uncertain', recipient,
          reason: `Delivery is uncertain and was not resent: ${submitted.detail}` };
      }
      return { operation, status: 'failed', recipient,
        reason: submitted.detail || 'Prompt submission failed' };
    })();
    pendingSends.set(key, job);
    try {
      const result = await job;
      rememberOperation(key, result);
      return result;
    } finally {
      pendingSends.delete(key);
    }
  }
  async function stopAgent(body) {
    const bindingId = body?.binding;
    if (typeof bindingId !== 'string' || !bindingId) throw new Error('Invalid stop request');
    const binding = bindings.get(bindingId);
    if (!binding) throw new Error('Binding expired. Bind a terminal again.');
    const { terminal, status } = await liveAgent(binding, 'interrupt');
    const recipient = recipientOf(binding, terminal);
    if (status !== 'working') throw new Error(`Agent is ${status}; nothing to stop.`);
    const rule = ruleFor(terminal.agent);
    if (!rule.offered) throw new Error(rule.reason);
    const interrupted = await herdr.agentInterrupt(terminal.pane, interruptTimeoutMs);
    if (interrupted.outcome === 'stopped') return { status: 'stopped', recipient, detail: interrupted.detail };
    if (interrupted.outcome === 'uncertain') {
      return { status: 'uncertain', recipient, reason: `Interrupt outcome is uncertain: ${interrupted.detail}` };
    }
    return { status: 'failed', recipient, reason: interrupted.detail || 'Interrupt failed' };
  }
  async function replyRequest(body) {
    const bindingId = body?.binding, requestId = body?.request, revision = body?.revision;
    if (typeof bindingId !== 'string' || !bindingId || typeof requestId !== 'string' || !requestId ||
        requestId.length > 128 || typeof revision !== 'string' || !revision || revision.length > 80) {
      throw new Error('Invalid request reply');
    }
    const binding = bindings.get(bindingId);
    if (!binding) throw new Error('Binding expired. Bind a terminal again.');
    const key = [binding.project, binding.run, binding.slot, binding.generation, binding.terminal, binding.identity,
      requestId, revision].join('');
    if (answered.has(key)) return { ...answered.get(key), duplicate: true };
    if (pendingAnswers.has(key)) return pendingAnswers.get(key);
    const job = (async () => {
      const { slot, terminal } = await liveAgent(binding, 'send', { grace: false });
      const current = await requestState({ slot, terminal, listeners: requestListeners,
        fetchImpl: requestFetch, timeoutMs: requestTimeoutMs, experimental: experimentalRequests });
      const recipient = recipientOf(binding, terminal);
      if (current.state !== 'live') return { status: 'refused', recipient, request: requestId, reason: current.reason };
      const outcome = await replyToRequest({ port: current.port, sessionId: current.session_id, requestId, revision,
        body: { reply: body.reply, answers: body.answers, reject: body.reject === true },
        fetchImpl: requestFetch, timeoutMs: requestTimeoutMs, experimental: experimentalRequests });
      return { ...outcome, recipient, request: requestId };
    })();
    pendingAnswers.set(key, job);
    try {
      const result = await job;
      // Only outcomes that must never be repeated are remembered; a stale or
      // refused attempt posted nothing and is judged again on the next call.
      if (result.status === 'resolved' || result.status === 'uncertain') {
        answered.delete(key);
        answered.set(key, result);
        while (answered.size > OPERATION_LIMIT) answered.delete(answered.keys().next().value);
      }
      return result;
    } finally { pendingAnswers.delete(key); }
  }
  // Link a saved session to a current role, or clear that link. Companion-only: no Docket
  // state is written, and the proof is repeated on every state read.
  async function associate(project, body) {
    const { run: runId, slot: slotId, generation, choice: choiceId } = body || {};
    for (const value of [runId, slotId, generation]) if (typeof value !== 'string' || !value || value.length > 300) throw new Error('Invalid link request');
    const first = await stateOf(project);
    const run = first.runs.find(r => r.id === runId);
    const slot = run?.slots.find(s => s.id === slotId);
    if (!slot || slot.generation !== generation) throw new Error('Docket role or session generation changed. Select the role again.');
    if (body.clear === true) {
      await associations.clear(project.id, runId, slotId);
      return { cleared: true };
    }
    if (body.confirm !== true) throw new Error('Linking needs your confirmation.');
    if (body.terminal !== undefined) return linkTerminal(project, first, run, slot, body.terminal);
    if (typeof choiceId !== 'string' || !choiceId || choiceId.length > 300) throw new Error('Choose a saved session to link.');
    const reason = eligibility(slot);
    if (reason) throw new Error(reason);
    const { choices } = await choiceReader(project.path, runId, slotId, slot.base_generation, 'run');
    const choice = choices.find(c => c.id === choiceId);
    if (!choice) throw new Error('That session is not in this run\'s list. Linking never reaches outside the run.');
    if (!choice.process) throw new Error('That saved session has no single recorded process, so no live terminal can prove it.');
    const inventory = await herdr.inventory(project.path);
    const terminals = await withProcessIdentities(inventory, run.slots);
    const proof = proveAssociation(choice, terminals);
    if (!proof.ok) throw new Error(proof.reason);
    const rival = run.slots.find(other => other !== slot && !other.historical && other.capabilities?.send &&
      resolveTerminal(other, terminals).terminal === proof.terminal.terminal);
    if (rival) throw new Error(`That terminal is already ${rival.role}'s recorded session.`);
    const after = await stateOf(project);
    if (after.runs.find(r => r.id === runId)?.slots.find(s => s.id === slotId)?.generation !== generation) {
      throw new Error('Docket role or session generation changed while linking. Nothing was saved.');
    }
    await associations.set(recordFor({ project: project.id, run, slot, choice }));
    return { linked: true, label: choice.label };
  }
  async function linkTerminal(project, state, run, slot, terminalId) {
    if (typeof terminalId !== 'string' || !terminalId || terminalId.length > 300) throw new Error('Choose a terminal to link.');
    const reason = terminalEligibility(slot);
    if (reason) throw new Error(reason);
    const inventory = await herdr.inventory(project.path);
    const terminals = await withProcessIdentities(inventory, run.slots);
    if (resolveTerminal(slot, terminals).terminal) throw new Error('This role\'s terminal is already proven. Nothing needs linking.');
    const terminal = terminals.find(t => t.terminal === terminalId);
    if (!terminal) throw new Error('That terminal is not in this project. Refresh and choose again.');
    if (!terminal.process) throw new Error('Herdr cannot identify the program in that terminal, so no link can hold.');
    const record = terminalRecordFor({ project: project.id, run, slot, terminal });
    const proof = proveTerminalLink(record, terminals);
    if (!proof.ok) throw new Error(proof.reason);
    for (const otherRun of state.runs) for (const other of otherRun.slots) {
      if (other === slot || other.historical || !other.capabilities?.send) continue;
      if (resolveTerminal(other, terminals).terminal === terminal.terminal) {
        throw new Error(`That terminal is already ${other.role}'s${otherRun.id === run.id ? '' : ` in run ${otherRun.id}`}.`);
      }
    }
    const after = await stateOf(project);
    if (after.runs.find(r => r.id === run.id)?.slots.find(s => s.id === slot.id)?.generation !== slot.generation) {
      throw new Error('Docket role or session generation changed while linking. Nothing was saved.');
    }
    await associations.set(record);
    return { linked: true, label: record.label };
  }
  const projectFrom = url => {
    const project = registry.resolve(url.searchParams.get('project'));
    if (!project) throw new Error('Unknown project. Choose one from the list.');
    return project;
  };
  function readBody(request) {
    return new Promise((resolve, reject) => {
      let bytes = 0;
      const chunks = [];
      request.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 256 * 1024) { reject(new Error('Request body too large')); request.destroy(); return; }
        chunks.push(chunk);
      });
      request.on('end', () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
        catch { reject(new Error('Invalid JSON body')); }
      });
      request.on('error', reject);
    });
  }

  const server = http.createServer(async (request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Referrer-Policy', 'no-referrer');
    response.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    if (request.headers.host !== host ||
        (request.headers.origin && request.headers.origin !== `http://${host}`)) {
      return json(response, 403, { error: 'Local origin required' });
    }
    if (request.method !== 'GET' && request.method !== 'POST') {
      return json(response, 405, { error: 'Only GET and POST are supported' });
    }
    try {
      const url = new URL(request.url, `http://${host}`);
      if (url.pathname.startsWith('/api/')) {
        if (!authorized(request.headers.authorization?.replace(/^Bearer /, ''))) {
          return json(response, 401, { error: 'Open the private URL printed by the companion' });
        }
        if (request.method === 'POST') {
          if (url.pathname === '/api/send') return json(response, 200, await sendMessage(await readBody(request)));
          if (url.pathname === '/api/stop') return json(response, 200, await stopAgent(await readBody(request)));
          if (url.pathname === '/api/associate') return json(response, 200, await associate(projectFrom(url), await readBody(request)));
          if (url.pathname === '/api/request/reply') return json(response, 200, await replyRequest(await readBody(request)));
          if (url.pathname === '/api/projects') {
            try { return json(response, 200, { project: await registry.add((await readBody(request))?.path), projects: registry.list() }); }
            catch (error) { if (error instanceof ProjectError) return json(response, 400, { error: error.message }); throw error; }
          }
          if (url.pathname === '/api/projects/remove') {
            try {
              const id = (await readBody(request))?.id;
              await registry.remove(id);
              for (const [key, old] of bindings) if (old.project === id) bindings.delete(key);
              return json(response, 200, { projects: registry.list() });
            } catch (error) { if (error instanceof ProjectError) return json(response, 400, { error: error.message }); throw error; }
          }
          return json(response, 405, { error: 'Method not allowed for this route' });
        }
        if (url.pathname === '/api/projects') return json(response, 200, { projects: registry.list() });
        if (url.pathname === '/api/state') {
          const project = projectFrom(url);
          const state = await inspector(project.path);
          let inventory;
          try { inventory = await herdr.inventory(project.path); }
          catch (error) { inventory = { terminals: [], error: error.message }; }
          const enriched = await withProcessIdentities(inventory,
            state.runs.flatMap(run => run.slots));
          applyAssociations(state, project.id, associations, enriched);
          const ages = new Map();
          await Promise.all(enriched.map(async terminal => {
            if (terminal.process) {
              const age = await ageOf(terminal.process.pid, terminal.process.start);
              if (typeof age === 'number') ages.set(terminal.terminal, age);
            }
          }));
          for (const run of state.runs) for (const slot of run.slots) {
            // Earlier sessions are inert history; they never resolve a live terminal.
            slot.terminal_match = slot.historical ? null : resolveTerminal(slot, enriched);
            // Readiness describes the slot's proven terminal only; bindings name the
            // recipient context but never confer authority on their own.
            const { terminal: sendTerminal, ambiguous: sendAmbiguous } = provenTerminal(slot, enriched, 'send');
            const sendStatus = sendTerminal?.agent ? sendTerminal.status || 'unknown' : 'unknown';
            // A freshly booted agent can report idle before its input loop is live
            // (observed drops); readiness stays unclaimed inside the grace window.
            // The send transport itself stays honest through the --wait stall guard.
            const sendAge = sendTerminal ? ages.get(sendTerminal.terminal) : undefined;
            const sendFresh = sendAge === undefined || sendAge >= BOOT_GRACE_SECONDS;
            slot.send_ready = !!(sendTerminal && sendTerminal.agent && SENDABLE.has(sendStatus) && sendFresh);
            slot.send_note = slot.send_ready ? '' : sendNoteFor(slot, sendTerminal, !!inventory.error, sendAmbiguous,
              sendFresh ? undefined : sendAge);
            slot.connection = connectionOf(slot, { terminal: sendTerminal || (slot.historical ? null : findTerminal(enriched, slot.terminal_match?.terminal)),
              ambiguous: sendAmbiguous || !!slot.terminal_match?.ambiguous, offline: !!inventory.error, young: sendFresh ? undefined : sendAge });
            const { terminal: stopTerminal } = provenTerminal(slot, enriched, 'interrupt');
            const stopStatus = stopTerminal?.agent ? stopTerminal.status || 'unknown' : 'unknown';
            const stopRule = stopTerminal?.agent ? ruleFor(stopTerminal.agent) : null;
            slot.stop_ready = !!(stopTerminal && stopTerminal.agent && stopStatus === 'working' && stopRule.offered);
            slot.stop_note = slot.stop_ready ? '' :
              (stopTerminal && stopTerminal.agent && stopStatus === 'working' ? stopRule.reason :
              (!stopTerminal || !stopTerminal.agent ? 'No live agent on the proven terminal.' :
                `Agent is ${stopStatus}; nothing to stop.`));
          }
          // Requests are re-read on every state response; a failed read grants nothing.
          await Promise.all(state.runs.flatMap(run => run.slots.map(async slot => {
            if (slot.historical) { slot.requests = { state: 'none', reason: 'Earlier sessions never hold live requests.', cards: [] }; return; }
            const { terminal } = provenTerminal(slot, enriched, 'send');
            slot.requests = await requestState({ slot, terminal, listeners: requestListeners,
              fetchImpl: requestFetch, timeoutMs: requestTimeoutMs, experimental: experimentalRequests });
            slot.requests.generation = slot.generation;
          })));
          return json(response, 200, { ...state, herdr: inventory, project: project.id, projects: registry.list(),
            stop_supported: Object.values(interruptPolicy).some(rule => rule.offered) });
        }
        if (url.pathname === '/api/document') {
          return json(response, 200, await inspector(projectFrom(url).path, url.searchParams.get('id') || ''));
        }
        if (['/api/history', '/api/history-choices'].includes(url.pathname)) {
          const runId = url.searchParams.get('run'), slotId = url.searchParams.get('slot');
          const generation = url.searchParams.get('generation');
          const find = state => state.runs.find(r => r.id === runId)?.slots.find(s => s.id === slotId);
          const project = projectFrom(url);
          const slot = find(await stateOf(project));
          if (!slot || slot.generation !== generation) throw new Error('Session generation changed. Select its current history again.');
          const scope = url.searchParams.get('scope') === 'project' ? 'project' : 'run';
          // A linked role reads the saved session it was linked to, unless another is chosen.
          const linked = slot.association?.state === 'linked' ? `${slot.association.native.harness}:${slot.association.native.session_id}` : '';
          const value = url.pathname === '/api/history-choices' ? await choiceReader(project.path, runId, slotId, slot.base_generation, scope) :
            await historyReader(project.path, runId, slotId, slot.base_generation, url.searchParams.get('choice') || linked || undefined, scope);
          if (find(await stateOf(project))?.generation !== generation) throw new Error('Session generation changed while reading history.');
          return json(response, 200, value);
        }
        if (url.pathname === '/api/binding') {
          const project = projectFrom(url);
          const state = await stateOf(project);
          const run = state.runs.find(r => r.id === url.searchParams.get('run'));
          const slot = run?.slots.find(s => s.id === url.searchParams.get('slot'));
          if (!slot) throw new Error('Select a current Docket role');
          if (slot.historical) throw new Error('Earlier sessions are read-only history. Select a current role to bind its live terminal.');
          const identity = await herdr.identity(url.searchParams.get('terminal'));
          for (const old of bindings.values()) {
            if (old.project === project.id && old.run === run.id && old.slot !== slot.id && old.terminal === identity.terminal &&
                old.identity === identity.identity && run.policy?.topology !== 'combined') {
              const oldSlot = run.slots.find(s => s.id === old.slot && s.generation === old.generation);
              if (oldSlot && !oldSlot.historical) throw new Error('This terminal is already bound to another role session.');
            }
          }
          const binding = { ...identity, project: project.id, run: run.id, slot: slot.id, generation: slot.generation,
            id: randomUUID() };
          if (bindings.size >= 2048) throw new Error('Binding limit reached. Restart the companion.');
          bindings.set(binding.id, binding);
          return json(response, 200, binding);
        }
        return json(response, 404, { error: 'Unknown API' });
      }
      const asset = assets.get(url.pathname);
      if (!asset) return json(response, 404, { error: 'Not found' });
      const bytes = await readFile(path.join(directory, asset[0]));
      response.writeHead(200, { 'Content-Type': `${asset[1]}; charset=utf-8` });
      response.end(bytes);
    } catch (error) { json(response, 400, { error: error.stderr?.trim() || error.message }); }
  });
  // The page polls every 5 s. Node's default 5 s keep-alive timeout closes an idle
  // connection just as the next poll reuses it, which the browser reports as an
  // intermittent "Failed to fetch". Hold idle connections well past the poll period.
  server.keepAliveTimeout = 65000;
  server.headersTimeout = 66000;
  const wss = new WebSocketServer({ noServer: true, maxPayload: 128 * 1024,
    handleProtocols: protocols => protocols.has('docket') ? 'docket' : false });
  server.on('upgrade', (request, socket, head) => {
    const protocols = request.headers['sec-websocket-protocol']?.split(',').map(s => s.trim()) || [];
    if (request.url !== '/terminal' || request.headers.host !== host ||
        request.headers.origin !== `http://${host}` || !protocols.some(authorized)) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return;
    }
    wss.handleUpgrade(request, socket, head, ws => wss.emit('connection', ws));
  });
  wss.on('connection', ws => {
    clients.add(ws);
    let stream = null, binding = null, selection = null, mode = 'observe', ready = false;
    let queue = Promise.resolve(), alive = true, responsive = true, pending = 0;
    const send = record => {
      if (ws.readyState !== WebSocket.OPEN) return;
      if (ws.bufferedAmount > 2 * 1024 * 1024) { ws.close(1013, 'Slow terminal viewer; reconnect'); return; }
      ws.send(JSON.stringify({ ...record, selection }));
    };
    const release = () => { ready = false; stream?.close(); stream = null; };
    const end = (reason, invalidated = false) => {
      release();
      send({ type: invalidated ? 'bridge.invalidated' : 'bridge.closed', reason });
    };
    const attach = async (message, fallback = false) => {
      const next = bindings.get(message.binding);
      if (!next) throw new Error('Binding expired. Bind a terminal again.');
      if (!['observe', 'control'].includes(message.mode)) throw new Error('Invalid stream mode');
      if (typeof message.selection !== 'string' || message.selection.length > 100) throw new Error('Invalid selection');
      const size = dimensions(message);
      release();
      binding = next; selection = message.selection; mode = message.mode;
      const liveIdentity = await validate(binding);
      if (!alive) return;
      let opened = false;
      let current;
      const projectionSize = mode === 'observe' && liveIdentity.viewport ? dimensions(liveIdentity.viewport) : size;
      current = herdr.open(binding, mode, message.takeover === true && mode === 'control', projectionSize,
        record => {
          if (stream !== current || !alive) return;
          if (record.type === 'terminal.frame') {
            if (!opened) {
              opened = true; ready = true;
              send({ type: 'bridge.ready', mode, attribution: binding.attribution });
            }
            send(record);
          } else if (record.type === 'terminal.closed') {
            const contention = !opened && mode === 'control' && /already has.*client|attached|takeover/i.test(record.reason);
            end(record.reason);
            if (contention && !fallback) {
              send({ type: 'bridge.contention', reason: record.reason });
              queue = queue.then(() => alive && attach({ ...message, mode: 'observe', takeover: false }, true))
                .catch(error => end(error.message));
            }
          }
        }, reason => { if (stream === current && alive) end(reason); });
      stream = current;
    };
    ws.on('message', bytes => {
      if (++pending > 128) { ws.close(1008, 'Too many queued commands'); return; }
      queue = queue.then(async () => {
        if (!alive) return;
        const message = JSON.parse(bytes.toString());
        if (message.type === 'attach') return attach(message);
        if (message.type === 'release') { release(); return; }
        if (!stream || message.selection !== selection) throw new Error('Input belongs to an inactive selection');
        if (!ready) throw new Error('Wait for the terminal stream before sending input');
        if (mode !== 'control') throw new Error('Take control before sending terminal input');
        await validate(binding);
        if (!alive || !stream) return;
        if (message.type === 'input') {
          if (typeof message.text !== 'string' || message.text.length > 65536) throw new Error('Invalid terminal input');
          stream.send({ type: 'terminal.input', text: message.text });
        } else if (message.type === 'resize') {
          stream.send({ type: 'terminal.resize', ...dimensions(message) });
        } else if (message.type === 'scroll') {
          if (!['up', 'down'].includes(message.direction) || !Number.isInteger(message.lines) ||
              message.lines < 1 || message.lines > 10000) throw new Error('Invalid scroll request');
          stream.send({ type: 'terminal.scroll', direction: message.direction, lines: message.lines });
        } else throw new Error('Unsupported terminal command');
      }).catch(error => end(error.message, /changed|disappeared|process.*available/.test(error.message)))
        .finally(() => { pending--; });
    });
    ws.on('pong', () => { responsive = true; });
    const heartbeat = setInterval(() => {
      if (!responsive) { ws.terminate(); return; }
      responsive = false; ws.ping();
    }, 15000);
    // Identity checks run in the same queue as input and attachment, avoiding stale callbacks.
    const identities = setInterval(() => {
      queue = queue.then(async () => {
        if (!stream || !alive) return;
        try { await validate(binding); } catch (error) { end(error.message, true); }
      }).catch(error => end(error.message, true));
    }, 2000);
    ws.on('close', () => {
      alive = false; release(); clearInterval(heartbeat); clearInterval(identities); clients.delete(ws);
    });
    ws.on('error', () => ws.close());
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject); server.listen(port, '127.0.0.1', resolve);
  });
  host = `127.0.0.1:${server.address().port}`;
  return { server, token, url: `http://${host}`, privateUrl: `http://${host}/#token=${token}`,
    async close() {
      for (const ws of clients) ws.terminate();
      await new Promise(resolve => wss.close(resolve));
      await new Promise(resolve => server.close(resolve));
    } };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({ options: {
    workspace: { type: 'string', default: path.resolve(directory, '..') },
    socket: { type: 'string' }, port: { type: 'string', default: '4317' }, help: { type: 'boolean' }
  } });
  if (values.help) {
    console.log('Usage: npm start -- [--workspace /path/to/starting/project] [--socket /path/to/herdr.sock] [--port 4317]');
  } else {
    const port = Number(values.port);
    if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid port');
    const socketPath = values.socket || process.env.HERDR_SOCKET_PATH ||
      path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'herdr', 'herdr.sock');
    const preferencesPath = path.join(process.env.XDG_STATE_HOME || path.join(os.homedir(), '.local', 'state'), 'docket-ui', 'projects.json');
    const companion = await createCompanion({ workspace: values.workspace, socketPath, port, preferencesPath });
    console.log(`Docket UI\nStarting project: ${path.resolve(values.workspace)}\nProject list: ${preferencesPath}\nHerdr socket: ${socketPath}\nOpen this private local URL:\n${companion.privateUrl}`);
    for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, async () => {
      await companion.close(); process.exit(0);
    });
  }
}
