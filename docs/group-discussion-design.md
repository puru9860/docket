# Group discussions (`discussion-group-v1`)

## Why

The pstack consultation needed one invoker and two peers.
`discussion-v1` is bilateral, so it ran as C03 (GPT-6-Astra) and C04 (Opus 5.5).
The invoker relayed each peer's view by path and digest (C04 messages 2 and 4, C03 message 3).
The relays kept provenance, but the invoker spent a turn on each relay, the peers never answered each other directly, and the two conclusions had to be reconciled by hand.
A native group protocol removes the relay turns and records one conclusion that names who agreed and who dissented.

## Scope

Group discussion is an opt-in, separately versioned protocol.
`discussion-v1` documents, prompts, wakes, and refusals stay byte for byte unchanged.
Readers dispatch on the `protocol` key, so neither protocol ever parses the other's transcript.
Nothing here launches a harness, sends anything outside `.docket/conversations/`, creates a coding run, or changes a model policy.

## Roster

`docket discuss G01 --start --peer NAME:HARNESS[:MODEL] --peer NAME:HARNESS[:MODEL] ...` creates a group with the invoker plus two to four named peers.
A single `--peer` is refused: a bilateral discussion is `discussion-v1`.
Names are lowercase identifiers.
`invoker`, `peer`, `human`, `all`, `none`, and any name starting with `human`, `user`, `system`, or `docket` are reserved, so a dynamic participant cannot render as human or Docket authority.
The roster and topic are immutable for the life of the discussion; a different roster uses a new ID.
`discussion.mdx` stores `roster: invoker,astra,opus` and flat `<name>_harness` and `<name>_model` keys.
A requested harness or model is a request and is never reported as observed.

## Identity

Each roster member joins as itself into `participants/<name>.mdx`, with the same session, generation, and native process proof as v1.
Every member needs a distinct session and a distinct native identity.
No session is inferred from the newest harness record.
`--join --replace` advances that member's generation and appends a `generation` event to the transcript, so the replacement is ordered against every message and the old session's writes refuse.

## Floor

Speaking is serialized by a floor computed from the transcript, never stored separately.
Agents speak in roster order.
The invoker authored the briefing, so each phase starts with the first peer, as in v1.
Each agent turn (`message`, `proposal`, `agreement`, `dissent`) passes the floor to the next roster member.
A human message, pause, continue, close, or generation event does not move the floor, except that `continue` returns it to the first peer.
Every write names the latest sequence (or, for a response, the pending proposal) under the lock, so a stale reply refuses.

## Conclusion

A proposal is pending until a later `message`, `proposal`, human message, `pause`, `continue`, `close`, or `generation` event invalidates it.
Every other roster member must answer the pending proposal on their floor with `--accept N` or `--dissent N --message REASON`, or invalidate it with a substantive reply.
When the last required answer lands:

- all accepted: `concluded`, `outcome: unanimous`;
- at least one accepted and some dissented: `concluded`, `outcome: dissent`, naming the dissenters;
- nobody but the proposer accepted: `outcome: declined`, and the discussion stays as it was.

Silence, a timeout, a missed wake, a pickup, a read, a withdrawal, and the turn cap are never consent.
`--read --json` reports the pending proposal with `accepted`, `dissented`, and `missing` members, so missing participation is distinct from both agreement and dissent.

## Cap, pause, continue

The turn cap counts `message` and `proposal` turns per phase.
Reaching it records `paused`; members still owed on the pending proposal may accept or dissent, which can conclude at the cap.
Only the invoker or human may pause, continue, or close.
A pause stops the whole group and invalidates the pending proposal, including at the cap.
`continue` opens a new phase with a fresh allowance and the first peer on the floor.
No agent continues on its own.

## Delivery

Each member has its own generation-bound receipt and announcement lease under `.delivery/`.
Pickup prints every message from others since that member's cursor, so whoever speaks has the full context; it never implies agreement.
A wake is announced only to the member who holds the floor, or to the invoker when a conclusion lands, and only if it has unread messages.
Other members are not woken merely to observe a message; they read it at their next pickup.
Wake notices hold fixed metadata and the pickup command, never peer prose.
The existing Stop hook and `--watch` select group members exactly as they select v1 participants.

## Limits

The protocol is a trusted local-file convention, not authentication, like v1.
A member that stops responding holds the floor; the user pauses, replaces that session, or closes the discussion.
The floor order is fixed; there is no skip, vote, or majority rule beyond the dissent outcome above.
