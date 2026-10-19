import http from 'node:http';

// Scripted loopback server speaking the OpenCode request routes observed on 1.18.35.
// Real sockets and bytes; `state` is mutable so a test can resolve, replace or
// contradict requests between reads.
export async function fakeOpenCode({ healthy = true, permissions = [], legacyPermissions = [], questions = [] } = {}) {
  const state = { healthy, permissions, legacyPermissions, questions, auth: false, hang: false, replyStatus: 200, health: undefined,
    keepAfterReply: false, posts: [], malformed: false, knownSessions: undefined };
  const server = http.createServer((request, response) => {
    const send = (status, body) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(body)); };
    const chunks = [];
    request.on('data', c => chunks.push(c));
    request.on('end', () => {
      const route = request.url.split('?')[0];
      if (state.auth) return send(401, { error: 'unauthorized' });
      if (request.method === 'POST') {
        const raw = Buffer.concat(chunks).toString('utf8');
        state.posts.push({ route, body: raw ? JSON.parse(raw) : null });
        if (state.hang) return; // never answered
        if (state.replyStatus !== 200) return send(state.replyStatus, { error: 'refused' });
        const v2 = /^\/api\/session\/([^/]+)\/permission\/([^/]+)\/reply$/.exec(route);
        if (v2) {
          const known = state.permissions.some(i => i.id === v2[2]);
          if (!known) return send(404, { _tag: 'PermissionNotFoundError', requestID: v2[2] });
          if (!state.keepAfterReply) state.permissions = state.permissions.filter(i => i.id !== v2[2]);
          response.writeHead(204); return response.end();
        }
        const match = /^\/(permission|question)\/([^/]+)\/(reply|reject)$/.exec(route);
        if (!match) return send(404, {});
        const field = match[1] === 'permission' ? 'legacyPermissions' : 'questions';
        if (!state.keepAfterReply) state[field] = state[field].filter(i => i.id !== match[2]);
        return send(200, true);
      }
      if (route === '/global/health') return state.health ? send(state.health.status, state.health.body) :
        send(200, { healthy: state.healthy, version: '1.18.35' });
      if (state.malformed) return send(200, { not: 'a list' });
      const listed = /^\/api\/session\/([^/]+)\/permission$/.exec(route);
      if (listed) {
        if (state.knownSessions && !state.knownSessions.includes(listed[1])) return send(404, { _tag: 'SessionNotFoundError' });
        return send(200, { data: state.permissions });
      }
      if (route === '/permission') return send(200, state.legacyPermissions);
      if (route === '/question') return send(200, state.questions);
      return send(404, {});
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { state, port: server.address().port, close: () => new Promise(resolve => { server.closeAllConnections?.(); server.close(resolve); }) };
}

// The v2 request shape observed on OpenCode 1.18.35 (POST /api/session/{id}/permission).
export const permissionRequest = (id = 'per_1', sessionID = 'ses_a', extra = {}) =>
  ({ id, sessionID, action: 'external_directory', resources: ['/tmp/outside'], metadata: {}, ...extra });
export const legacyPermissionRequest = (id = 'per_1', sessionID = 'ses_a', extra = {}) =>
  ({ id, sessionID, permission: 'bash', patterns: ['rm -rf build'], metadata: {}, always: ['rm *'], ...extra });
export const questionRequest = (id = 'que_1', sessionID = 'ses_a', extra = {}) =>
  ({ id, sessionID, questions: [{ question: 'Which database?', header: 'DB', options: [
    { label: 'sqlite', description: 'local file' }, { label: 'postgres', description: 'server' }], multiple: false, custom: false }], ...extra });
