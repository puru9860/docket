import { readFile, writeFile, rename, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

// An explicit, companion-only association links a saved session to a current Docket role
// whose own record has no usable identity. It never edits Docket state, is a stored
// preference rather than authority, and is re-proved against the live terminals on every
// state read: an unproven or invalidated association grants nothing.

export const ASSOCIATION_LIMIT = 64;
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fingerprintOf = slot => digest({ native: slot.native || null, process: slot.process || null });
export const keyOf = (project, run, slot) => `${project}\u0000${run}\u0000${slot}`;

// The saved session's exact native ID and the pid:start recorded beside it must prove exactly
// one live terminal, running that harness, with nothing contradicting the native ID.
export function proveAssociation(candidate, terminals) {
  const native = candidate?.native || {}, process = candidate?.process || {};
  if (!native.harness || !native.session_id || !process.pid || !process.start) {
    return { ok: false, reason: 'This saved session has no recorded process to prove a live terminal.' };
  }
  if (process.harness && process.harness !== native.harness) {
    return { ok: false, reason: 'The recorded process and native session name different harnesses.' };
  }
  const hits = terminals.filter(t => t.process && t.process.pid === process.pid && t.process.start === process.start);
  if (!hits.length) return { ok: false, reason: 'The recorded process is not running in any terminal Herdr can see.' };
  if (hits.length > 1) return { ok: false, reason: 'More than one terminal matches the recorded process.' };
  const [terminal] = hits;
  if (!terminal.agent) return { ok: false, reason: 'No live agent is running in the matching terminal.' };
  if (terminal.agent !== native.harness) {
    return { ok: false, reason: `The matching terminal runs ${terminal.agent}, not ${native.harness}.` };
  }
  if (terminal.native && terminal.native !== native.session_id) {
    return { ok: false, reason: 'Herdr reports a different native session in that terminal.' };
  }
  return { ok: true, terminal };
}

// A terminal link is your statement that one pane runs a role whose own native session is
// recorded but whose terminal nothing proves. It holds only while that pane keeps the same
// process, running the role's harness, with nothing contradicting the role's native ID.
export function proveTerminalLink(record, terminals) {
  const want = record.terminal, terminal = terminals.find(t => t.terminal === want.id);
  if (!terminal || terminal.server !== want.server) return { ok: false, reason: 'The linked terminal is gone. Link the role again.' };
  if (!terminal.process || terminal.process.pid !== want.process.pid || terminal.process.start !== want.process.start) {
    return { ok: false, reason: 'A different program now runs in the linked terminal. Link the role again.' };
  }
  if (!terminal.agent) return { ok: false, reason: 'No live agent is running in the linked terminal.' };
  if (terminal.agent !== record.native.harness) {
    return { ok: false, reason: `The linked terminal runs ${terminal.agent}, not ${record.native.harness}.` };
  }
  if (terminal.native && terminal.native !== record.native.session_id) {
    return { ok: false, reason: 'Herdr reports a different native session in that terminal.' };
  }
  return { ok: true, terminal };
}

// Only a current role with its own native session, not yet linked, may be linked to a terminal.
export function terminalEligibility(slot) {
  if (!slot) return 'Select a current Docket role.';
  if (slot.historical) return 'Earlier sessions are read-only history.';
  if (slot.association?.state === 'linked') return 'Remove the current link first.';
  if (slot.mapping_issue) return slot.mapping_issue;
  if (!slot.native?.session_id || !slot.capabilities?.send) return 'This role has no recorded session of its own. Link a saved session instead.';
  return '';
}

// Only a current role that has no identity of its own, or a contradictory one, may be linked.
export function eligibility(slot) {
  if (!slot) return 'Select a current Docket role.';
  if (slot.historical) return 'Earlier sessions are read-only history.';
  const own = slot.native?.session_id || slot.process?.pid;
  if (own && !slot.mapping_issue) return 'This role already has its own recorded session. Nothing needs linking.';
  return '';
}

export async function createAssociations({ preferencesPath } = {}) {
  const records = new Map();
  if (preferencesPath) {
    try {
      const stored = JSON.parse(await readFile(preferencesPath, 'utf8')).associations;
      for (const record of Array.isArray(stored) ? stored.slice(0, ASSOCIATION_LIMIT) : []) {
        if (record && typeof record.project === 'string' && typeof record.run === 'string' && typeof record.slot === 'string') {
          records.set(keyOf(record.project, record.run, record.slot), record);
        }
      }
    } catch { /* No saved preferences yet. */ }
  }
  async function save() {
    if (!preferencesPath) return;
    await mkdir(path.dirname(preferencesPath), { recursive: true });
    const temporary = `${preferencesPath}.${process.pid}.tmp`;
    await writeFile(temporary, JSON.stringify({ version: 1, associations: [...records.values()] }, null, 2), { mode: 0o600 });
    await rename(temporary, preferencesPath);
  }
  return {
    get: (project, run, slot) => records.get(keyOf(project, run, slot)) || null,
    list: () => [...records.values()],
    async set(record) {
      if (!records.has(keyOf(record.project, record.run, record.slot)) && records.size >= ASSOCIATION_LIMIT) {
        records.delete(records.keys().next().value);
      }
      records.set(keyOf(record.project, record.run, record.slot), record);
      await save();
    },
    async clear(project, run, slot) {
      const removed = records.delete(keyOf(project, run, slot));
      if (removed) await save();
      return removed;
    }
  };
}

export function recordFor({ project, run, slot, choice, now = new Date() }) {
  return {
    project, run: run.id, slot: slot.id, session: slot.session || '', registration_generation: slot.registration_generation ?? null,
    role: slot.role, own: fingerprintOf(slot), choice: choice.id, label: choice.label,
    native: { harness: choice.native.harness, session_id: choice.native.session_id },
    process: { harness: choice.process.harness, pid: choice.process.pid, start: choice.process.start },
    recorded_roles: choice.recorded_roles || [], at: now.toISOString()
  };
}

export function terminalRecordFor({ project, run, slot, terminal, now = new Date() }) {
  return {
    kind: 'terminal', project, run: run.id, slot: slot.id, session: slot.session || '',
    registration_generation: slot.registration_generation ?? null, role: slot.role, own: fingerprintOf(slot),
    label: `${terminal.name} · ${terminal.pane}`,
    native: { harness: slot.native.harness, session_id: slot.native.session_id },
    terminal: { id: terminal.terminal, server: terminal.server, pane: terminal.pane,
      process: { pid: terminal.process.pid, start: terminal.process.start } },
    recorded_roles: [], at: now.toISOString()
  };
}

// Apply stored associations to a fresh state. A linked slot gains the saved session's identity,
// and its generation changes so earlier bindings and pending operations cannot move onto it.
export function applyAssociations(state, projectId, associations, terminals) {
  for (const run of state.runs) {
    for (const slot of run.slots) {
      slot.base_generation = slot.generation;
      const record = associations.get(projectId, run.id, slot.id);
      if (!record || slot.historical) continue;
      const terminalLink = record.kind === 'terminal';
      const outcome = { by: 'you', kind: terminalLink ? 'terminal' : 'session', label: record.label,
        recorded_roles: record.recorded_roles, at: record.at, native: record.native,
        process: terminalLink ? record.terminal.process : record.process };
      const stale = slot.role !== record.role || (slot.session || '') !== record.session ||
        (slot.registration_generation ?? null) !== record.registration_generation ||
        (!slot.mapping_issue && fingerprintOf(slot) !== record.own);
      const proof = stale ? null : terminalLink ? proveTerminalLink(record, terminals) : proveAssociation(record, terminals);
      if (stale) Object.assign(outcome, { state: 'invalidated', reason: 'The role\'s own record changed since you linked it. Link it again.' });
      else if (!proof.ok) Object.assign(outcome, { state: 'unproven', reason: proof.reason });
      else Object.assign(outcome, { state: 'linked', reason: '' });
      slot.association = outcome;
      if (outcome.state !== 'linked') continue;
      if (terminalLink) {
        // The role keeps its own native session; only the terminal's process stands in as proof.
        slot.process = { harness: record.native.harness, ...record.terminal.process };
        slot.capabilities = { ...slot.capabilities, send: true, interrupt: true, terminal: true };
        slot.generation = digest({ base: slot.base_generation, association: [record.terminal, record.at] });
        continue;
      }
      slot.noted_native = slot.native || null;
      slot.native = { ...record.native };
      slot.process = { ...record.process };
      delete slot.mapping_issue;
      slot.identity = { kind: 'native', harness: record.native.harness, session_id: record.native.session_id };
      slot.capabilities = { ...slot.capabilities, history: true, send: true, interrupt: true, terminal: true };
      slot.generation = digest({ base: slot.base_generation, association: [record.native, record.process, record.at] });
      for (const other of run.slots) {
        if (other !== slot && other.historical && other.native?.session_id === record.native.session_id &&
            other.native?.harness === record.native.harness) other.merged_into = slot.id;
      }
    }
  }
  return state;
}
