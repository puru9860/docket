import net from 'node:net';
import { stat, readFile, realpath } from 'node:fs/promises';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createInterface } from 'node:readline';

const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const execute = promisify(execFile);

export function rpc(socketPath, method, params = {}) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    let data = '';
    const finish = (error, result) => { socket.destroy(); error ? reject(error) : resolve(result); };
    socket.setTimeout(3000, () => finish(new Error('Herdr request timed out')));
    socket.on('error', reject);
    socket.on('connect', () => socket.write(JSON.stringify({ id: 'docket-ui', method, params }) + '\n'));
    socket.on('data', chunk => {
      data += chunk;
      if (data.length > 8 * 1024 * 1024) return finish(new Error('Herdr response too large'));
      if (!data.includes('\n')) return;
      try {
        const response = JSON.parse(data.slice(0, data.indexOf('\n')));
        finish(response.error ? new Error(response.error.message || JSON.stringify(response.error)) : null,
          response.result);
      } catch (error) { finish(error); }
    });
    socket.on('end', () => finish(new Error('Herdr closed before a response')));
  });
}

async function processRef(pid) {
  if (!pid) return null;
  // Linux start ticks protect against PID reuse. Other platforms retain explicit PID telemetry.
  try {
    const text = await readFile(`/proc/${pid}/stat`, 'utf8');
    return { pid, start: text.slice(text.lastIndexOf(')') + 2).split(' ')[19] };
  } catch (error) {
    if (process.platform === 'linux') return null;
    return { pid, start: '' };
  }
}

async function processIdentity(pid) {
  const ref = await processRef(pid);
  if (!ref) throw new Error('Terminal process is no longer available');
  return `${ref.pid}:${ref.start}`;
}

let cachedClockTick = null, cachedBootTime = null;
async function clockTick() {
  if (cachedClockTick) return cachedClockTick;
  try {
    const { stdout } = await execute('getconf', ['CLK_TCK'], { timeout: 2000, maxBuffer: 1024 });
    cachedClockTick = parseInt(String(stdout).trim(), 10) || 100;
  } catch { cachedClockTick = 100; }
  return cachedClockTick;
}

export async function processAgeSeconds(pid, startTicks) {
  // Wall-clock age of a Linux process from its start ticks, or null when unknown.
  // Unknown never proves youth; callers treat it as no grace either way.
  if (process.platform !== 'linux' || !pid || startTicks === '' || startTicks == null) return null;
  try {
    if (cachedBootTime === null) {
      const stat = await readFile('/proc/stat', 'utf8');
      const line = stat.split('\n').find(entry => entry.startsWith('btime '));
      cachedBootTime = parseInt(line.split(' ')[1], 10);
    }
    const tick = await clockTick();
    return Date.now() / 1000 - (cachedBootTime + parseInt(startTicks, 10) / tick);
  } catch { return null; }
}

export class Herdr {
  constructor(socketPath, workspace, executable = 'herdr') {
    this.socketPath = socketPath;
    this.workspace = workspace;
    this.executable = executable;
  }

  async inventory(workspace = this.workspace) {
    const info = await stat(this.socketPath);
    const { snapshot } = await rpc(this.socketPath, 'session.snapshot');
    const server = digest([this.socketPath, info.dev, info.ino, info.ctimeMs]);
    const terminals = [];
    for (const pane of snapshot.panes) {
      let cwd;
      try { cwd = await realpath(pane.foreground_cwd || pane.cwd || ''); } catch { continue; }
      if (cwd !== workspace && !cwd.startsWith(workspace + '/')) continue;
      const agent = snapshot.agents.find(a => a.terminal_id === pane.terminal_id) || {};
      terminals.push({ terminal: pane.terminal_id, pane: pane.pane_id,
        name: agent.name || pane.terminal_title_stripped || pane.pane_id,
        agent: agent.agent || '', agent_name: agent.name || '', native: agent.agent_session || null,
        status: agent.agent_status || 'unknown', cwd, server });
    }
    return { terminals, version: snapshot.version, protocol: snapshot.protocol, server };
  }

  async terminalIdentities(inventory) {
    // Proven foreground/shell process identity per terminal, for auto-matching.
    const result = {};
    for (const candidate of inventory.terminals) {
      try {
        const { process_info: info } = await rpc(this.socketPath, 'pane.process_info', { pane_id: candidate.pane });
        const pid = candidate.agent ? info.foreground_process_group_id : info.shell_pid;
        result[candidate.terminal] = await processRef(pid);
      } catch { result[candidate.terminal] = null; }
    }
    return result;
  }

  agentArgs(command) {
    return { env: { ...process.env, HERDR_SOCKET_PATH: this.socketPath } };
  }

  herdrErrorText(error) {
    // Herdr reports server errors as JSON on stderr with exit status 1.
    const text = error.stderr || error.message || String(error);
    try {
      const parsed = JSON.parse(text);
      return parsed.error?.message || parsed.error?.code || text;
    } catch { return text; }
  }

  async agentRecord(target) {
    const { stdout } = await execute(this.executable, ['agent', 'get', target],
      { ...this.agentArgs(), timeout: 10000, maxBuffer: 512 * 1024 });
    const parsed = JSON.parse(stdout);
    return parsed.result?.agent ?? parsed.agent ?? parsed;
  }

  async agentStatusSeq(target) {
    try {
      const { stdout } = await execute(this.executable, ['agent', 'get', target],
        { ...this.agentArgs(), timeout: 10000, maxBuffer: 512 * 1024 });
      const parsed = JSON.parse(stdout);
      const record = parsed.result?.agent ?? parsed.agent ?? parsed;
      const seq = Number(record?.state_change_seq);
      return { status: record?.agent_status || record?.status || 'unknown',
        seq: Number.isSafeInteger(seq) ? seq : null };
    } catch { return { status: 'unknown', seq: null }; }
  }

  async agentPrompt(target, text, timeoutMs = 30000) {
    // Acceptance is observed, never assumed: submit through Herdr's --wait path
    // and bind the verdict to a state_change_seq advance, which a fast turn that
    // completes before observation cannot evade. Anything unrecognized, and any
    // timeout or stall without a corroborating turn, is uncertain, never sent.
    // The one exception is a transport that never spawned: certain non-delivery.
    const before = await this.agentStatusSeq(target);
    let output = '';
    try {
      const { stdout } = await execute(this.executable,
        ['agent', 'prompt', target, text, '--wait', '--until', 'working', '--timeout', String(timeoutMs)],
        { ...this.agentArgs(), timeout: timeoutMs + 5000, maxBuffer: 512 * 1024 });
      output = String(stdout);
    } catch (error) {
      if (error.code === 'ENOENT' || String(error.syscall || '').startsWith('spawn')) {
        return { outcome: 'failed', detail: `Prompt transport unavailable: ${error.message}`.slice(0, 2000) };
      }
      output = String(error.stdout || '') + String(error.stderr || '');
      if (!output.trim()) output = error.killed ? 'prompt wait timed out' : 'prompt returned no output';
    }
    const detail = output.trim().slice(-2000);
    if (/agent_blocked/i.test(output)) return { outcome: 'blocked', detail };
    const after = await this.agentStatusSeq(target);
    const advanced = before.seq !== null && after.seq !== null && after.seq > before.seq;
    if (advanced && ['working', 'idle', 'done', 'blocked'].includes(after.status)) {
      return { outcome: 'sent', detail };
    }
    return { outcome: 'uncertain', detail };
  }

  async agentInterrupt(target, timeoutMs = 10000) {
    try {
      const { stdout } = await execute(this.executable, ['agent', 'send-keys', target, 'ctrl+c'],
        { ...this.agentArgs(), timeout: timeoutMs, maxBuffer: 512 * 1024 });
      return { outcome: 'stopped', detail: String(stdout).trim().slice(0, 2000) };
    } catch (error) {
      const detail = String(this.herdrErrorText(error)).slice(0, 2000);
      if (error.killed || /timed out|timeout/i.test(detail)) return { outcome: 'uncertain', detail };
      return { outcome: 'failed', detail };
    }
  }

  async identity(terminal) {
    const inventory = await this.inventory();
    const candidate = inventory.terminals.find(t => t.terminal === terminal);
    if (!candidate) throw new Error('Terminal disappeared or left this project. Bind a terminal again.');
    const { process_info: info } = await rpc(this.socketPath, 'pane.process_info', { pane_id: candidate.pane });
    const shell = await processIdentity(info.shell_pid);
    const occupant = candidate.agent ? await processIdentity(info.foreground_process_group_id) : shell;
    let viewport;
    if (process.platform === 'linux') {
      try {
        const { stdout } = await execute('python3', ['-B', fileURLToPath(new URL('./terminal_dimensions.py', import.meta.url)),
          String(info.shell_pid), shell.split(':')[1]], { timeout: 2000, maxBuffer: 1024 });
        viewport = JSON.parse(stdout) || undefined;
      } catch { /* Unreadable PTY dimensions fall back to explicit pane geometry. */ }
    }
    if (!viewport) try {
      const { layout } = await rpc(this.socketPath, 'pane.layout', { pane_id: candidate.pane });
      const rect = layout.panes.find(p => p.pane_id === candidate.pane)?.rect;
      // Herdr pane rectangles include a one-cell border on each side.
      if (rect && rect.width >= 22 && rect.height >= 7) viewport = { cols: rect.width - 2, rows: rect.height - 2 };
    } catch { /* Older servers can still project at the requested dimensions. */ }
    return { ...candidate, identity: digest([candidate.server, terminal, shell, occupant,
      candidate.agent, candidate.native]), viewport, attribution: candidate.native ? 'native-session' : 'process-only' };
  }

  open(binding, mode, takeover, dimensions, onRecord, onEnd) {
    const args = ['terminal', 'session', mode, binding.terminal,
      '--cols', String(dimensions.cols), '--rows', String(dimensions.rows)];
    if (takeover) args.push('--takeover');
    const child = spawn(this.executable, args, {
      env: { ...process.env, HERDR_SOCKET_PATH: this.socketPath }, stdio: ['pipe', 'pipe', 'pipe']
    });
    let errors = '', ended = false, sequence = null;
    const finish = reason => { if (!ended) { ended = true; onEnd(reason); } };
    const lines = createInterface({ input: child.stdout });
    lines.on('line', line => {
      if (ended) return;
      try {
        const record = JSON.parse(line);
        if (record.type === 'terminal.frame') {
          if (!Number.isSafeInteger(record.seq) ||
              (sequence === null && !record.full) ||
              (sequence !== null && record.seq !== sequence + 1)) {
            finish('Terminal frame sequence lost. Reconnect for a full screen.');
            child.kill(); return;
          }
          sequence = record.seq;
        }
        onRecord(record);
      } catch { finish('Invalid Herdr stream. Reconnect for a full screen.'); child.kill(); }
    });
    child.stderr.on('data', chunk => { errors = (errors + chunk).slice(-4096); });
    child.on('error', error => finish(error.message));
    child.on('close', () => finish(errors.trim() || 'Terminal stream ended'));
    child.stdin.on('error', error => finish(error.message));
    return {
      send(command) {
        if (ended || child.stdin.destroyed) throw new Error('Terminal stream is closed');
        if (child.stdin.writableLength > 128 * 1024) throw new Error('Terminal input buffer is full');
        child.stdin.write(JSON.stringify(command) + '\n');
      },
      close() {
        ended = true;
        if (!child.stdin.destroyed) child.stdin.end(JSON.stringify({ type: 'terminal.release' }) + '\n');
        const timer = setTimeout(() => child.kill(), 500);
        timer.unref();
        child.once('close', () => clearTimeout(timer));
      }
    };
  }
}
