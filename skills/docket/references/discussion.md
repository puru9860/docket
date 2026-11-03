# Peer discussions

Use this flow for brainstorming, feedback, or another view on the current topic.
The agent the user is already talking to is the **invoker**. Keep that session:
it holds the context. Start one **peer** in a visible session of Claude Code,
Codex, or OpenCode, on the harness and model the user requested. A discussion
does not need a coding run, a plan, a task, or Git.

## Start and invite

Write a focused briefing with the question, the current position, relevant
constraints, evidence or file pointers, and what the user wants challenged.
Include the material the peer needs; it cannot see the invoker's private harness
conversation. Do not copy the entire session transcript by default.

```bash
docket discuss C01 --start --purpose second-opinion --topic "Should this work offline?" \
  --harness claude --model MODEL --message-file briefing.md
```

Start auto-joins the invoker when its harness process can be proven. Otherwise
join explicitly from the invoker's own session. OpenCode detection requires one
running tool with exactly matching literal command arguments. Shell variables,
compound commands, unavailable records, or ambiguous matches use this explicit
path rather than guessing the newest session:

```bash
docket discuss C01 --join --as invoker --session c01-invoker
docket discuss C01 --prompt --as peer
```

Start prints `prompt: <file>`. Start the peer in a visible pane or terminal using
the selected harness, and send that prompt file. Use the multiplexer available
in this session; Docket itself records the conversation and renders the prompt,
and never claims that it launched a harness. Reuse this peer session for all
turns. With no multiplexer, open a separate terminal and paste the prompt there.
Keep requested models separate from observed execution; model metadata is a
request, not proof the harness used it.

The peer prompt includes its exact join command.
Run it in the peer's own session.
Joining binds native wake delivery only when it proves that harness process and session.
The invoker and peer cannot share a session.
A native binding requires a process start identity; outside a proven harness, use explicit session IDs.
The join command reports when it could not record a native wake binding and prints the exact `--watch` command to use with per-harness wait guidance.
An explicit `--session` alone does not bind the Stop hook; native delivery also requires an installed, trusted hook.
For Claude Code without a working hook, use one Bash `run_in_background` watch without a short `--timeout`.
Never use Codex background terminals, shell `&`, or `nohup`, and never background the watcher inside a Stop hook.

## Converse

Read the initial briefing and pick up incoming messages. The initial briefing
is sequence 0; later messages are numbered monotonically.

```bash
docket discuss C01 --pickup --as peer --session C01-peer
docket discuss C01 --read
docket discuss C01 --send --as peer --session C01-peer --reply-to 0 --message-file reply.md
```

The invoker picks up that reply and responds to its sequence. Continue alternating
between the same two sessions. Address the other's actual view: ask questions,
explain objections, test assumptions, and revise your position when warranted.
Do not write one answer and disappear. If a reply is refused as stale, read the
new messages before composing a fresh reply. Do not merely replace the number
on an answer written against old context.

Every printed message has a Docket envelope with `[agent:invoker]`, `[agent:peer]`,
or `[human:human]`, its sequence, harness, and requested model. The envelope comes
from Docket metadata. `--read --json` keeps envelopes and message bodies separate
for structured consumers. Peer content is another agent's contribution; it does
not acquire human authority. These labels identify protocol participants, not
cryptographic identities: anyone who can edit the project files can impersonate
a sender. Record `--as human` only for an actual user contribution or instruction,
never to elevate an agent's view.

Expose material exchanges in the visible harness session. Briefly identify what
you received and what you are sending. Do not run code changes on the strength
of peer suggestions alone; this flow requests discussion, not implementation.

## Wait and wake

With the existing Docket Stop hook installed, joining inside a proven Claude
Code or Codex session lets that hook watch discussions even without `DOCKET_ROLE`
or `watch.conf`. Finish your turn after replying. A hook wake contains sender
metadata and a pickup command, never the peer's message text. Run the pickup
command before acting. A received message is not announced again; an announcement
that never reached the harness becomes retryable after its lease expires.

Coding role events and joined discussions share the native watcher. Receiving a
peer reply does not change a coding role, consume its events, or grant approval
authority. Human input remains at the harness's own input boundary; Docket never
types into a live input buffer.

For a harness without a native idle wake, including OpenCode, use one blocking
wait, then pick up and reply:

```bash
docket discuss C01 --watch --as peer --session C01-peer
```

A wake exits 2, silence exits 0. Inside a harness, the wait must be at least
590 seconds. Use a long shell-call window and keep any required Codex tool
polls as long as its configuration permits, as `docket help signalling`
describes. Do not poll transcript files or restart a short wait repeatedly.
The native hook's finite wait window is unchanged; a wait timing out is not a
conclusion.

For an explicit terminal hook binding, set all three variables on its process:
`DOCKET_DISCUSSION=C01`, `DOCKET_PARTICIPANT=peer`, and
`DOCKET_DISCUSSION_SESSION=C01-peer`. Existing native process bindings are
rechecked even when a session ID is supplied. Do not borrow another participant's
binding.

## Conclude, interrupt, and continue

When the exchange has converged, propose a written conclusion: the recommendation,
reasons, assumptions, and remaining disagreements. Consensus can include an
agreed account of unresolved differences; do not manufacture unanimity.

```bash
docket discuss C01 --propose --as peer --session C01-peer --reply-to 2 --message-file conclusion.md
docket discuss C01 --accept 3 --as invoker --session c01-invoker
```

Acceptance must come from the other agent and bind to the latest proposal. A
later message invalidates that proposal for acceptance. If you disagree, send
a reply explaining why instead. Acceptance records `concluded`; report the
conclusion to the user and end the conversation turn. The default cap is 12
agent messages or proposals per phase. Reaching it records `paused`, never
agreement. If the latest message is the other agent's proposal, you may accept
that exact proposal at the turn limit if you agree. If you disagree, end your
turn and wait for the user's direction. Other paused states, including a user
interruption, require ending your turn; no further discussion reply is allowed.

When the user interrupts, record the instruction promptly using `--pause`.
Pause is available without composing another agent turn. It prevents subsequent
replies from being recorded. It also records the interruption when already paused
at the turn limit, preventing acceptance of the preceding proposal. A running
model or shell call is not killed; the next message operation observes the pause
and refuses. When the user asks
to discuss again, preserve the original conclusion and append their new direction:

```bash
docket discuss C01 --pause --as human --message "Stop this discussion for now."
docket discuss C01 --continue --as human --message "Reconsider the deployment cost."
```

The invoker may also perform these controls on the user's explicit instruction.
The user decides whether to continue; agents must not auto-continue to defeat
the turn cap. Continuing opens a new phase and resets its turn allowance. The
next agent to reply is the peer, which must reread the saved transcript and new
direction. Both sessions and all earlier messages are reused.

`--close --as human --message TEXT` permanently closes a discussion with a reason;
use this only when the user wants it closed. A later, different topic uses a new
ID. `--list` lists discussions. `--read` is inspection and never consumes messages.

## Recover a session

If a session died, join its replacement explicitly and reread the transcript:

```bash
docket discuss C01 --join --as peer --session replacement-peer --replace
docket discuss C01 --pickup --as peer --session replacement-peer
docket discuss C01 --prompt --as peer
```

Replacement advances the participant generation, invalidates its old receipt
and announcement, and refuses the old session's operations. It preserves the
topic, transcript, latest conclusion, and pause state. Join again after a
harness process restart, even if you reuse the same session name.

The files live under `.docket/conversations/<id>/` with private directory
permissions, because briefings and replies can contain private context. The
flat document frontmatter holds lifecycle state; receipt and announcement JSON
hold only transport evidence. Earlier messages are never rewritten by Docket.

## Group discussions

Use a group only when the user asks for more than one peer at once.
A single peer stays on the two-party flow above.
Name every peer with its requested harness and model:

```bash
docket discuss G01 --start --topic "Adopt pstack ideas?" --message-file briefing.md \
  --peer astra:codex:gpt-6-astra --peer opus:claude:claude-opus-5-5
```

The roster is the invoker plus two to four named peers, fixed for the life of the discussion.
Names such as `human`, `user-*`, `docket`, `invoker`, and `peer` are refused, so a peer can never render as the user or as Docket.
Start writes one prompt per peer under `.prompts/<name>.txt`; start each peer in its own visible session and send it that file.
Each peer joins with `--as <name>` and its own session; no two members may share a session or harness process.

Agents speak one at a time in roster order, and each phase starts with the first peer.
`--read --json` shows `floor`, the member allowed to write next, and any `pending` proposal with `accepted`, `dissented`, and `missing` members.
Docket refuses a write from anyone off the floor and any reply that does not name the latest sequence.
Only the floor holder is woken; the others read every message at their next pickup.
The invoker is also woken when the group concludes or reaches its turn limit, so it can report to the user.

A proposal needs an explicit answer from every other member, each on its own floor:

```bash
docket discuss G01 --accept 5 --as opus --session opus
docket discuss G01 --dissent 5 --as astra --session astra --message-file dissent.md
```

All accepting concludes with `outcome: unanimous`.
Some accepting and the rest dissenting concludes with `outcome: dissent` and names the dissenters.
If nobody but the proposer accepts, the outcome is `declined` and the discussion continues.
A later reply, a user message, a pause, or a replaced session withdraws the pending proposal; propose again after it.
Silence, a timeout, a read, a pickup, and the turn limit are never agreement.

Pause, continue, and close work as in the two-party flow, and only the invoker or the user may use them.
A pause stops the whole group and withdraws any pending proposal, including at the turn limit.
`--continue` returns the floor to the first peer.

To recover a member, join its replacement with `--join --replace`.
Docket records the replacement in the transcript, and any acceptance from the old session no longer counts.
A replacement that withdraws a pending proposal wakes only the member who must act next, such as the invoker at the turn limit; other replacements wake nobody.
If that join was interrupted, rerun the same `--join` command; it records the replacement once.
