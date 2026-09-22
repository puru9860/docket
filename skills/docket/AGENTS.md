# Docket

Read `SKILL.md` completely before using or modifying this skill. Role playbooks
are canonical in `references/` and available through `docket help <role>`.

Essential invariant: `progress_updates: quiet` applies in split and combined
topologies. Supervisors wait without model-driven polling and never narrate normal
implementor lifecycle activity. A split planner receives only the aggregate
orchestrator report. Use only Claude Code, Codex, or OpenCode; switch an OpenCode
model in the existing session with `Ctrl+X`, then `M` (or `/models`) before
considering a restart, and verify the live model label instead of trusting
`--model`. Claude Code accepts full model IDs and supports in-session `/model` and
`/effort`; preserve its current session too. Repository discovery and scope
proposals belong to the cheap implementor; Docket silently accepts disjoint scopes
and wakes the orchestrator only for collisions. Ordinary task reports wake once
per ready review batch, never once per task, and that review remains user-silent.
Every artifact publishes atomically and a decision transition is serialized per
owner with a durable identity that carries the reviewer's own decision text, so
repeating the bare verdict finishes an interrupted one exactly as recorded, never
opens a second round, and refuses a retry that states anything different, including
one that fills in a reason recorded as absent; a verdict never applies to a report
body edited after review until that body passes the whole report gate again and is
re-verified, while a blocked report skips completion verification there exactly as it
does at submission. Every submitted task round freezes into one immutable,
content-addressed bundle holding the task revision, the task baseline, the
root-qualified full patch against it, the resulting source revision, the report body,
and the captured verification, plus a reproducible delta from the previous bundle on a
correction round; a later edit, retry, re-review, or round only ever produces a new
address, a verdict binds to that digest, and missing, damaged, or stale bundle evidence
blocks the decision. A bundle holds its own copy of the baseline record and
reconstruction artifacts and is damaged when a tree it pins is unavailable, so evidence
nobody can rebuild never reads as intact. Rebuilding that evidence writes nothing into
the checkout it measures, not even a loose object. One owner submits at a time: the
verify run, the freeze, the ledger append, and the report write are one locked
transition, and a submission whose contract, report, scope, or consumed inputs moved
while the command ran is refused with the concurrent edit left in place. Accepted scope
stays owned through captured verification and the review boundary, and consuming
verified-but-unapproved work requires an explicit run policy, admits only a bundle whose
frozen verification passed, pins the consumed digest, and is reverified when that input
moves; an input that moves afterwards withdraws review readiness, blocking approval and
waiver alike, and dependency readiness is never final approval. The round freezes the
consumed-input record itself, the empty set included, an accepting verdict binds to that
frozen record rather than to the live one, and `docket depend --on` records only into a
draft round, so re-recording a pin can never stand in for a reverification. A Git-backed run
declares every checkout root
explicitly, as `alias=path`, before dispatch, and only a declared root is ever
captured, so two worktrees sharing Git storage and a nested repository stay distinct
change surfaces while an undeclared checkout stays out of local evidence entirely.
The declaration is immutable and fail-closed: absent and invalid are different
states, and a corrupt one is refused rather than rediscovered. The run baseline
precedes the first assignment and each task baseline precedes its own dispatch, both
immutable, both captured in full with capture-time branch and HEAD, and both taken
without committing or stashing user work; a `git` run that cannot capture one refuses
to dispatch instead of recording a partial baseline. Changed paths are
`<alias>:<path>` once a run has more than one root, work committed during a task
still counts as changed, so does a mode-only or staging change on a path that was
already dirty, and an unresolved, ambiguous, missing, or incompletely captured root
blocks any claim of diff coverage instead of reporting an empty diff. Before
replacing an implementor, require a ready standardized `docket handoff` checkpoint
and have the replacement resume directly from it, the accepted scope capsule, and
the task-local diff.
