/* Design preview only. No live data, network calls, agents or filesystem actions. */
const $ = id => document.getElementById(id);
const sessions = [
  { id: 'planner', name: 'Planner', glyph: 'P', provider: 'OpenCode', native: 'r01-planner', responsibility: 'Defines intent and constraints', mode: 'chat', state: 'Needs your answer', task: 'plan', request: { id: 'planner-q1', type: 'question', title: 'Should the file viewer allow editing?', detail: 'This changes the scope of T03. Read-only viewing is enough for the first iteration.', choices: ['Read-only viewer', 'Allow file editing'] }, messages: [
    { author: 'user', text: 'Build a chat UI with an in-app file viewer. Keep all agents running in their normal interactive TUI.' },
    { author: 'agent', text: 'I’ll keep the existing sessions running and give each role its own conversation. Here’s how I would split the work:', list: ['T01 · Connect each role to its exact running session', 'T02 · Messages, tools, questions and permissions', 'T03 · View the plan and reports beside the chat'], file: 'plan.mdx' }
  ], tool: { name: 'Read architecture and task constraints', status: 'Completed', body: 'Read-only discovery\nARCHITECTURE.md\n.docket/runs/R01/plan.mdx\n\nThe normal interactive TUI remains the execution surface.' } },
  { id: 'orchestrator', name: 'Orchestrator', glyph: 'O', provider: 'OpenCode', native: 'r01-orchestrator', responsibility: 'Coordinates tasks and routes blockers', mode: 'chat', state: 'Ready', task: 'task', messages: [
    { author: 'user', text: 'Show me what is ready to work on.' },
    { author: 'agent', text: 'T01 is ready. T02 depends on the session connection, and T03 can be developed independently. The planner has a question about whether the file viewer should allow editing.' },
    { author: 'agent', text: 'My chat is separate from the planner’s, even though we are split in the same window.' }
  ], tool: { name: 'Inspect task dependencies', status: 'Completed', body: 'T01 → T02\nT03: independent\n\nInspection does not acknowledge a Docket delivery event.' } },
  { id: 'worker-a', name: 'T01 · Earlier task', glyph: 'I', provider: 'OpenCode', native: 'r01-impl-a', responsibility: 'T01 · Earlier session in the reused pane', mode: 'history', state: 'Session ended', task: 'task', request: { id: 'worker-a-p1', type: 'permission', expired: true, resolved: true, title: 'Run the focused session tests?' }, messages: [
    { author: 'user', text: 'Connect the selected role to its existing native session. Preserve its TUI.' },
    { author: 'agent', text: 'The connection now uses the native session ID. Three roles share a window, but each has a separate message stream. I’d like to run the focused tests before reporting the result.' }
  ], tool: { name: 'Inspect the running TUI connection', status: 'Completed', body: 'Sample native session: r01-impl-a\nSample window: test / Window 1\n\nNo new agent process is started in this design.' } },
  { id: 'worker-b', name: 'Implementor', glyph: 'I', provider: 'OpenCode', native: 'r01-impl-b', responsibility: 'T03 · Current task in the implementor pane', mode: 'chat', state: 'Needs permission', task: 'report', request: { id: 'worker-b-p1', type: 'permission', title: 'Run the document viewer checks?', detail: 'This permits one tool call for the current task. It does not approve its report.', command: 'python3 -m unittest tests.test_documents', cwd: '~/Documents/Projects/test', choices: ['Allow once', 'Deny'] }, messages: [
    { author: 'user', text: 'Design a read-only document panel beside the conversation.' },
    { author: 'agent', text: 'The panel distinguishes live drafts from frozen submitted evidence. Opening a plan or report will not mark a task approved or acknowledge a lifecycle event.', file: 'T03-report-01.mdx' }
  ], tool: { name: 'Inspect document sections', status: 'Completed', body: 'Plan / Task / Report / Verification / Decision\n\nThis worker has a separate session and draft from worker A.' } },
  { id: 'verifier', name: 'Verifier', glyph: 'V', provider: 'Codex CLI', native: 'r01-verifier', responsibility: 'Checks frozen evidence', mode: 'history', state: 'History only', task: 'report', messages: [
    { author: 'agent', text: 'This is a sample of a saved transcript. The current normal CLI session has not exposed a proven live chat connection.' },
    { author: 'agent', text: 'You can read the history here. Until a supported native CLI integration is verified, interact with the same agent through its terminal.' }
  ], tool: { name: 'Read saved verification history', status: 'Historical', body: 'Saved evidence is readable.\nLive input, question answers and tool decisions are unavailable in this adapter state.' } },
  { id: 'reviewer', name: 'Reviewer', glyph: 'R', provider: 'Claude Code', native: 'r01-reviewer', responsibility: 'Owns task decisions', mode: 'history', state: 'History only', task: 'report', messages: [
    { author: 'agent', text: 'The default interactive Claude Code session stays open in its existing terminal. Live chat attachment has not been proven for this design.' },
    { author: 'agent', text: 'Tool permissions in the chat are distinct from a Docket reviewer verdict. A task remains submitted until its recorded reviewer decision.' }
  ], tool: { name: 'Read the recorded review context', status: 'Historical', body: 'Native interactive CLI integration is a separate feasibility check.\nNo SDK-controlled execution is substituted.' } }
];
const drafts = new Map();
let selected = sessions[0], view = 'conversation', documentView = 'plan', historyExpanded = false;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function pending() { return sessions.filter(s => s.request && !s.request.resolved); }
function feedback(text) { $('feedback').textContent = text; }
function showContext() {
  const area = document.querySelector('.workarea');
  area.classList.remove('context-hidden');
  if (matchMedia('(max-width:950px)').matches) area.classList.add('context-mobile');
  $('context-toggle').setAttribute('aria-pressed', 'true');
}
function renderSidebar() {
  $('sessions').replaceChildren();
  const sessionButton = s => {
    const button = node('button', `session${s.id === selected.id ? ' selected' : ''}${s.id.startsWith('worker') ? ' worker' : ''}`);
    button.dataset.session = s.id;
    button.setAttribute('aria-pressed', String(s === selected));
    const avatar = node('span', 'session-avatar', s.glyph);
    const copy = node('span', 'session-copy');
    copy.append(node('strong', '', s.name), node('small', '', `${s.provider}${s.id === 'worker-b' ? ' · T03' : ''} · ${s.state}`));
    button.append(avatar, copy);
    if (s.request && !s.request.resolved) button.append(node('span', 'request-count', '1'));
    else button.append(node('span', `status-dot ${s.mode === 'chat' ? 'green' : 'gray'}`));
    button.onclick = () => selectSession(s.id);
    return button;
  };
  for (const s of sessions.filter(s => s.id !== 'worker-a')) {
    $('sessions').append(sessionButton(s));
    if (s.id === 'worker-b') {
      const toggle = node('button', 'session-history-toggle', `${historyExpanded ? '▾' : '▸'} Earlier tasks (1)`);
      toggle.id = 'implementor-history-toggle';
      toggle.setAttribute('aria-expanded', String(historyExpanded));
      toggle.setAttribute('aria-controls', 'implementor-history');
      toggle.onclick = () => { historyExpanded = !historyExpanded; renderSidebar(); };
      const history = node('div', 'session-history');
      history.id = 'implementor-history'; history.hidden = !historyExpanded;
      history.append(sessionButton(sessions.find(s => s.id === 'worker-a')));
      $('sessions').append(toggle, history);
    }
  }
  const count = pending().length;
  $('attention').textContent = count ? `${count} need you ↗` : 'No pending requests';
  $('attention').disabled = count === 0;
}
function selectSession(id) {
  drafts.set(selected.id, $('message').value);
  selected = sessions.find(s => s.id === id);
  $('message').value = drafts.get(selected.id) || '';
  view = 'conversation';
  feedback('Design preview: chat actions are simulated.');
  render();
}
function messageCard(message) {
  const article = node('article', `message ${message.author}`);
  article.dataset.session = selected.id;
  const avatar = node('span', 'message-avatar', message.author === 'user' ? 'Y' : selected.glyph);
  const content = node('div', 'message-content');
  const meta = node('div', 'message-meta', message.author === 'user' ? 'You' : selected.name);
  meta.append(node('small', '', 'Sample · 10:42'));
  content.append(meta, node('p', '', message.text));
  if (message.list) {
    const list = node('ul'); message.list.forEach(item => list.append(node('li', '', item))); content.append(list);
  }
  if (message.file) {
    const file = node('button', 'file-card');
    const copy = node('div'); copy.append(node('strong', '', message.file), node('small', '', 'Docket document · open in context'));
    file.append(node('span', '', '▤'), copy, node('span', '', '↗'));
    file.onclick = () => { documentView = message.file === 'plan.mdx' ? 'plan' : 'report'; renderDocument(); showContext(); };
    content.append(file);
  }
  article.append(avatar, content); return article;
}
function toolCard() {
  const details = node('details', 'tool-card');
  const summary = node('summary'); summary.append(node('strong', '', selected.tool.name), node('span', '', selected.tool.status));
  details.append(summary, node('pre', '', selected.tool.body)); return details;
}
function resolveRequest(session, request, answer) {
  if (session.request !== request || request.resolved) return;
  request.resolved = true; request.answer = answer; session.state = 'Ready';
  session.messages.push({ author: 'user', text: request.type === 'question' ? `Answer: ${answer}` : `Tool permission: ${answer}` });
  session.messages.push({ author: 'agent', text: request.type === 'question' ?
    `Understood. I’ll record “${answer}” as the requested direction in this sample conversation.` :
    `Your “${answer}” decision was recorded for this sample tool request only.` });
  render(); feedback(`Preview only: ${answer} recorded for ${session.native}. No real agent was contacted.`);
}
function requestCard() {
  const session = selected, request = session.request;
  if (!request) return null;
  if (request.expired) return node('div', 'expired-card', `Expired tool request · ${request.title} · This earlier task session has ended.`);
  if (request.resolved) return node('div', 'resolved-card', `✓ ${request.type === 'question' ? 'Question answered' : 'Tool decision recorded'} · ${request.answer} · Sample request ${request.id}`);
  const card = node('section', 'request-card'); card.dataset.request = request.id;
  card.append(node('span', 'request-tag', request.type === 'question' ? 'QUESTION · NEEDS YOUR ANSWER' : 'TOOL PERMISSION · NEEDS YOUR DECISION'), node('h2', '', request.title), node('p', '', request.detail));
  if (request.command) {
    card.append(node('div', 'command-preview', request.command), node('p', '', `Working directory: ${request.cwd}`));
  }
  const actions = node('div', 'request-actions');
  actions.append(node('small', '', `To ${session.native} only`));
  if (request.type === 'question') {
    const choices = node('div', 'choices');
    const submit = node('button', 'primary', 'Submit answer'); submit.disabled = true;
    request.choices.forEach((answer, index) => {
      const button = node('button', 'choice', `${answer}${index === 0 ? ' · recommended' : ''}`);
      button.setAttribute('aria-pressed', 'false');
      button.onclick = () => {
        request.selection = answer;
        choices.querySelectorAll('button').forEach(b => { b.classList.toggle('chosen', b === button); b.setAttribute('aria-pressed', String(b === button)); });
        submit.disabled = false;
      };
      choices.append(button);
    });
    submit.onclick = () => resolveRequest(session, request, request.selection);
    card.append(choices); actions.append(submit);
  } else {
    request.choices.forEach(answer => {
      const button = node('button', answer === 'Allow once' ? 'primary' : '', answer);
      button.onclick = () => resolveRequest(session, request, answer); actions.append(button);
    });
  }
  card.append(actions); return card;
}
function renderConversation() {
  const target = $('conversation'); target.replaceChildren();
  if (view === 'terminal') {
    target.append(node('h2', '', 'The same agent, in its normal TUI'), node('p', 'terminal-caption', 'This is the manual fallback for the selected session only. The production version would attach to its exact terminal, with explicit control ownership. The sample below is not a live screen.'));
    target.append(node('pre', 'terminal-fallback', `${selected.provider} · ${selected.native}\n~/Documents/Projects/${$('project-select').value === 'test' ? 'test' : 'demo-project'}\n\n› This agent stays in its default interactive TUI/CLI.\n› Only this selected session would appear here.\n\n[Design placeholder. No process is started or controlled.]`));
    return;
  }
  if (view === 'activity') {
    target.append(node('div', 'timestamp', 'ACTIVITY FOR THIS SESSION ONLY'));
    target.append(toolCard());
    const request = requestCard(); if (request) target.append(request);
    const item = node('div', 'activity-item', `Native session: ${selected.native}`);
    item.append(node('small', '', 'Sample topology: all six sessions share Window 1. Each conversation is separate.'));
    target.append(item); return;
  }
  target.append(node('div', 'timestamp', selected.mode === 'history' ? 'SAVED HISTORY · SAMPLE' : 'TODAY · SAMPLE CONVERSATION'));
  selected.messages.forEach(message => target.append(messageCard(message)));
  target.append(toolCard());
  const request = requestCard(); if (request) target.append(request);
  if (selected.request && !selected.request.resolved) target.scrollTop = target.scrollHeight;
}
function renderDocument() {
  for (const tab of ['plan', 'task', 'report']) $(`${tab}-tab`).classList.toggle('active', tab === documentView);
  const target = $('document'); target.replaceChildren();
  if (documentView === 'plan') {
    $('document-title').textContent = 'plan.mdx'; $('document-status').textContent = 'Sample live plan · draft';
    target.append(node('h2', '', 'Chat UI with an in-app file viewer'), node('h3', '', 'Objective'), node('p', '', 'Interact with each role in a dedicated conversation while every agent keeps running in its default TUI/CLI.'), node('h3', '', 'Tasks'));
    const list = node('ul'); ['T01 · Exact native session connection', 'T02 · Chat and actionable requests', 'T03 · Read-only document viewer'].forEach(t => list.append(node('li', '', t)));
    target.append(list, node('h3', '', 'Hard constraints'), node('p', '', 'No headless agents or SDK replacements. Shared windows never merge different role conversations. Docket gates and reviewer authority remain intact.'));
  } else if (documentView === 'task') {
    $('document-title').textContent = 'T01-task.mdx'; $('document-status').textContent = 'Sample task contract';
    target.append(node('h2', '', 'Connect the exact running session'), node('h3', '', 'Acceptance criteria'), node('p', '', 'A message sent to worker A reaches worker A’s existing native session.'), node('p', '', 'Worker B’s messages and pending permissions remain isolated, including when both agents share one window.'), node('h3', '', 'Execution'), node('p', '', 'Default interactive TUI/CLI. Reopening a chat does not launch a replacement process.'));
  } else {
    $('document-title').textContent = 'T03-report-01.mdx'; $('document-status').textContent = 'Sample draft report · not submitted';
    target.append(node('h2', '', 'Document viewer report'), node('h3', '', 'Progress'), node('p', '', 'The design separates the plan, task contract and report from conversation messages.'), node('h3', '', 'Evidence'), node('p', '', 'No frozen evidence exists in this visual preview. A production report would link its exact immutable bundle after passing the ordinary Docket submission gate.'), node('h3', '', 'Status'), node('p', '', 'Draft. A chat response or tool permission never changes this lifecycle status.'));
  }
}
function composerState() {
  const pendingQuestion = selected.request?.type === 'question' && !selected.request.resolved;
  const available = selected.mode === 'chat' && view === 'conversation' && !pendingQuestion && !(selected.request && !selected.request.resolved);
  $('composer-label').textContent = `Message ${selected.name}`;
  $('recipient-hint').textContent = `To ${selected.native} only · Enter to send · Shift+Enter for a new line`;
  $('message').disabled = !available; $('send').disabled = !available || !$('message').value.trim();
  $('message').placeholder = available ? `Send a message to ${selected.name}...` : selected.mode === 'history' ? 'Live chat is not connected for this normal CLI session.' : view !== 'conversation' ? 'Return to Conversation to send a message.' : 'Resolve the request above to continue this conversation.';
  $('state-explanation').replaceChildren();
  $('state-explanation').hidden = selected.mode !== 'history';
  if (selected.mode === 'history') {
    $('state-explanation').append(node('span', '', 'Saved history only. Live attachment to this default CLI has not been verified.'));
    const button = node('button', '', 'Open terminal');
    button.onclick = () => { view = 'terminal'; render(); }; $('state-explanation').append(button);
  }
}
function render() {
  renderSidebar();
  $('crumb-role').textContent = selected.name; $('role-name').textContent = selected.name; $('role-avatar').textContent = selected.glyph;
  $('session-subtitle').textContent = `${selected.provider} · ${selected.native} · ${selected.responsibility}`;
  $('session-source').textContent = `${selected.provider} · ${selected.native}`;
  $('connection-state').replaceChildren(node('span', `status-dot ${selected.mode === 'chat' ? 'green' : 'amber'}`), node('span', '', selected.mode === 'chat' ? 'Chat available' : 'History only'));
  $('connection-state').classList.toggle('limited', selected.mode !== 'chat');
  document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.view === view)));
  renderConversation(); renderDocument(); composerState();
}
$('composer').onsubmit = event => {
  event.preventDefault();
  if ($('send').disabled) return;
  const text = $('message').value.trim(), recipient = selected;
  recipient.messages.push({ author: 'user', text });
  recipient.messages.push({ author: 'agent', text: `Preview reply from ${recipient.name}: your message was added to this sample conversation. In the product, the native TUI’s existing live connection would process it.` });
  drafts.delete(recipient.id); $('message').value = '';
  render(); $('conversation').scrollTop = $('conversation').scrollHeight;
  feedback(`Preview only: message stayed in ${recipient.native}. No real agent was contacted.`);
};
$('message').oninput = () => { drafts.set(selected.id, $('message').value); composerState(); };
$('message').onkeydown = event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('composer').requestSubmit(); }
};
document.querySelectorAll('[data-view]').forEach(button => { button.onclick = () => { view = button.dataset.view; render(); }; });
$('attention').onclick = () => { if (pending()[0]) selectSession(pending()[0].id); };
for (const tab of ['plan', 'task', 'report']) $(`${tab}-tab`).onclick = () => { documentView = tab; renderDocument(); };
$('context-toggle').onclick = () => {
  const area = document.querySelector('.workarea');
  const small = matchMedia('(max-width:950px)').matches;
  if (small) area.classList.toggle('context-mobile'); else area.classList.toggle('context-hidden');
  $('context-toggle').setAttribute('aria-pressed', String(small ? area.classList.contains('context-mobile') : !area.classList.contains('context-hidden')));
};
$('project-select').onchange = () => {
  $('project-path').textContent = `~/Documents/Projects/${$('project-select').value === 'test' ? 'test' : 'demo-project'}`;
  drafts.clear(); $('message').value = '';
  feedback('Preview project selection changed. Sample sessions are reused to illustrate the layout.');
};
render();
