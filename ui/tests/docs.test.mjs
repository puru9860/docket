import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const read = file => readFile(new URL(`../${file}`, import.meta.url), 'utf8');

test('the live product carries no POC or simulated-action wording', async () => {
  for (const file of ['public/index.html', 'public/app.js', 'public/markdown.js', 'README.md', '../README.md']) {
    // The separate design preview is described as simulated on purpose, in its own section.
    const text = (await read(file)).split('## Design preview')[0];
    assert.doesNotMatch(text, /\bPOC\b|browser spike|simulated action|sample data interactions/i, file);
  }
  assert.doesNotMatch(await read('README.md'), /Send button stays disabled|not enabled in the live companion/);
});

test('the README documents exactly the options and defaults the server has', async () => {
  const readme = await read('README.md');
  const { stdout } = await execute(process.execPath, ['server.mjs', '--help'], { cwd: new URL('..', import.meta.url).pathname });
  for (const option of ['--workspace', '--socket', '--port']) {
    assert.ok(stdout.includes(option), `server help lists ${option}`);
    assert.ok(readme.includes(option), `README documents ${option}`);
  }
  const defaultPort = /\[--port (\d+)\]/.exec(stdout)[1];
  assert.ok(readme.includes(`127.0.0.1:${defaultPort}`), 'README states the default listen address');
});

test('the README names the real capability limits and recovery states the UI reports', async () => {
  const readme = await read('README.md');
  for (const needle of ['endpoint-absent', 'unsupported', 'Add project', '.docket/', 'projects.json', 'Load earlier', 'Rendered', 'Source',
    'never resent', 'uncertain', 'Stop is hidden because no harness offers it', 'private URL', 'Link a session', 'linked by you', 'associations.json', 'Reading mode', 'Unattributed']) {
    assert.ok(readme.includes(needle), `README mentions ${needle}`);
  }
  const app = await read('public/app.js');
  for (const needle of ['Add project', 'Rendered', 'Load earlier', 'Open terminal']) assert.ok((await read('public/index.html')).includes(needle) || app.includes(needle), needle);
});
