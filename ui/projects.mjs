import { realpath, stat, readFile, writeFile, rename, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

export const PROJECT_LIMIT = 32;
const PATH_LIMIT = 4096;

export class ProjectError extends Error {}

const idOf = real => createHash('sha256').update(real).digest('hex').slice(0, 12);

// Exactly the path the user named, resolved to its real directory. Nothing is
// discovered: no parent, sibling or linked checkout is considered.
export async function validateProject(input) {
  if (typeof input !== 'string' || !input.trim() || input.length > PATH_LIMIT || input.includes('\0')) {
    throw new ProjectError('Enter the absolute path of a project directory.');
  }
  if (!path.isAbsolute(input.trim())) throw new ProjectError('The project path must be absolute.');
  let real;
  try { real = await realpath(input.trim()); }
  catch { throw new ProjectError('That path does not exist.'); }
  if (!(await stat(real)).isDirectory()) throw new ProjectError('That path is not a directory.');
  let docket;
  try { docket = await stat(path.join(real, '.docket')); } catch { docket = null; }
  if (!docket?.isDirectory()) {
    throw new ProjectError('No .docket directory in that path. Docket projects keep their runs in .docket/.');
  }
  return real;
}

// The explicit project list. Only paths are persisted, to one preferences file the
// caller names; with none, the list lives in memory. It never writes inside a project.
export async function createProjects({ initial, preferencesPath }) {
  const projects = new Map();
  const add = real => { const id = idOf(real); if (!projects.has(id)) projects.set(id, { id, path: real, name: path.basename(real) || real }); return projects.get(id); };
  const first = add(await realpath(initial));
  let stored = [];
  if (preferencesPath) {
    try { stored = JSON.parse(await readFile(preferencesPath, 'utf8')).projects; } catch { stored = []; }
    if (!Array.isArray(stored)) stored = [];
  }
  for (const candidate of stored.slice(0, PROJECT_LIMIT)) {
    try { add(await validateProject(candidate)); } catch { /* A project that vanished is dropped from the list. */ }
  }
  async function save() {
    if (!preferencesPath) return;
    await mkdir(path.dirname(preferencesPath), { recursive: true });
    const temporary = `${preferencesPath}.${process.pid}.tmp`;
    await writeFile(temporary, JSON.stringify({ version: 1, projects: [...projects.values()].map(p => p.path) }, null, 2), { mode: 0o600 });
    await rename(temporary, preferencesPath);
  }
  return {
    startup: first,
    list: () => [...projects.values()],
    get: id => projects.get(id) || null,
    resolve: id => (id ? projects.get(id) || null : first),
    async add(input) {
      const real = await validateProject(input);
      if (!projects.has(idOf(real)) && projects.size >= PROJECT_LIMIT) throw new ProjectError(`The project list is limited to ${PROJECT_LIMIT} entries.`);
      const project = add(real);
      await save();
      return project;
    },
    async remove(id) {
      if (id === first.id) throw new ProjectError('The project this companion started with stays in the list.');
      if (!projects.delete(id)) throw new ProjectError('That project is not in the list.');
      await save();
    }
  };
}
