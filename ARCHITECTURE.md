# How docket works

For anyone - human or agent - modifying docket itself. If you only want to *use* it,
read the README and `docket help <role>`.

## The model in one paragraph

Five logical roles (planner, orchestrator, implementor, verifier, reviewer) may
run as separate agents, or the planner and orchestrator may be combined.
Supported harnesses are Claude Code,
Codex (temporary compatibility), and OpenCode.
They coordinate entirely through Markdown files in `.docket/runs/<run>/`.
A supervisor learns that a subordinate finished because the *harness* re-enters it, not
because it polled.
Mechanical gates validate task intent, atomically claim implementor-discovered scopes,
detect active-scope collisions, and refuse to mark a report reviewable unless its schema,
diff boundary, acceptance fidelity, and verification all hold.
A run declares its lifecycle policy in `plan.mdx` as `workflow: five-role-v1`
(separate planner and reviewer registrations, verifier findings, correction
budgets, and reviewer-owned approval). `workflow: legacy` is a historical decode
for runs that already exist: the original three-role behavior is preserved
exactly, a missing workflow key still reads as legacy, and `docket migrate`
moves such a run explicitly. New runs cannot select legacy: `docket init`
records five-role-v1 through the quick preset by default, or the standard preset
with `--mode standard`, and refuses `--workflow legacy` with a diagnostic naming
standard and quick.

## Why files, not messages

Three constraints forced this, and they are worth keeping in mind before you replace it
with something cleverer.

- **Harness independence.** A planner on Claude Code and an implementor on OpenCode share
  no API. A directory is the only interface both can use.
- **Terminal output is lossy.** Agents render on the terminal's alternate screen; rows
  that scroll off are unrecoverable. So a supervisor can never reliably read a
  subordinate's *output* - it must read a file the subordinate wrote.
- **Sessions die.** A file-based run survives a killed session, a compacted context, and
  a machine restart. In-memory coordination does not.

## Data model

```
.docket/
  watch.conf                 armed (run, role) pairs, one per line
  templates/                 optional per-project schema overrides
  runs/<run>/
    plan.mdx                 planner output; functional tasks and ownership
    <owner>-task.mdx         assignment intent, constraints, and optional hints
    <owner>-scope.mdx        implementor discovery capsule and claimed paths
    <owner>-handoff-NN.mdx   resumable partial-work checkpoint
    <owner>-report-NN.mdx    the subordinate's report for round NN
    <owner>-decision-NN.mdx  the reviewer's verdict, reason, and evidence digest
    docs/                    supporting material
    .snapshots/roots.json    the run's declared checkout roots
    .snapshots/run.json      run baseline, taken before the first implementor edit
    .snapshots/<owner>.json  task baseline and accepted scope
    .snapshots/<owner>/      that baseline's patches and untracked content
    .baselines/<owner>.json  preflight verification result
    .bundles/<owner>/rounds.json   append-only ledger of that owner's frozen bundles
    .bundles/<owner>/NN/<addr>/    one frozen task-round bundle, named by its digest
    .bundles/objects/        run-private Git object store used to rebuild trees
    .bundles/work/           run-private Git index files; scratch, never evidence
    .deps/<owner>.json       inputs this owner consumed, and on what terms
    .transitions/<owner>.json  in-flight decision transition identity and progress
    .locks/<owner>.lock      owner-scoped decision transition lock
    .woke-<role>             per-role wake ledger, append-only
```

`owner` is a task id (`T03`) or the literal `orch`.
Round numbers are **per-owner**, so `T03-decision-02.mdx` is unambiguously round 2 of T03.
Sequence-numbered artifacts are ordered by their number, not by text.
Every newest-record selection uses one shared numeric ordering, so attempt 100 always outranks attempt 99 for verifications, reports, decisions, handoffs, checkpoints, and any other numbered series.

A run also declares whether a task may consume another task's verified but
unapproved work, in `plan.mdx` as `provisional_integration: forbidden` (the default)
or `provisional_integration: allowed`.

A run declares its review evidence in `plan.mdx` as `evidence_mode: git` or
`evidence_mode: documents-only`. `docket init` records the mode the workspace actually
supports and `--evidence-mode` overrides it. The mode decides whether unresolvable Git
evidence is a submission failure or an expected, labelled condition.

A `git` run also declares its **checkout roots** before dispatch, into
`.snapshots/roots.json`. Each root has a stable alias, a canonical worktree path, its
own Git directory, the Git common directory it shares, a branch, and a pinned HEAD.
Two worktrees of one repository share a common directory and are still two change
surfaces, so identity is the worktree, never the storage.

Declaration is **explicit**. A root is named as `alias=path`, at `docket init --root`
or at `docket roots <run> --declare`, and only a declared root is ever captured.
Omitting the specs declares the enclosing checkout alone as `root`, which is a
convenience for the common single-repository case and nothing more: a linked worktree
or a nested repository has to be named. Two things forced that. `.docket` may sit in a
plain directory that holds several repositories and no enclosing checkout at all, which
discovery cannot represent; and a worktree found by discovery can carry dirt from
unrelated work into baseline evidence that is stored locally and can contain secrets.
`docket roots` lists candidate checkouts when nothing is declared, as a hint that
captures nothing.

The declaration is immutable and fail-closed. `read_roots` distinguishes *absent* from
*invalid* and reports both with their reason, so a corrupt, incomplete, or duplicated
declaration is refused rather than read as "nothing declared yet" - which is exactly
what would let a later assignment rediscover aliases that existing baselines already
point at. `--redeclare` is refused once any baseline exists, and declaring afresh under
a baseline whose declaration has gone missing is refused too. An alias that quietly
starts meaning a different checkout invalidates every baseline under it.

**Frontmatter is the single source of truth.** There is deliberately no `state.json`.
A parallel state file drifts from the documents agents actually edit, and then you have
two truths and no way to tell which is stale. If you need new state, add a frontmatter key.

The frontmatter parser (`parse`/`render` in `bin/docket`) is intentionally a flat
`key: value` reader, not YAML. It has no dependencies and cannot execute anything. Keep it
that way; nothing in the protocol needs nested structures.

## Lifecycle

```
                    docket assign
                          |
                          v
                implementor discovery
                          |
                  docket scope --submit
                          |
                    scope ready
                          |
                       draft ------------------.
                          |                    | implementor sets
              docket submit (THE GATE)         | status: blocked
                          |                    | + states a question
                          v                    v
                     submitted              blocked
                          |                    |
                          '------- docket decide -------.
                                                        |
                    .-----------------------------------'
                    |                                   |
              --approve                            --changes
                    |                                   |
                    v                                   v
                approved                        changes-requested
                                                        |
                                          opens <owner>-report-NN+1.mdx
                                                        |
                                                        '--> draft (next round)
```

Only `submit` moves a report out of `draft`, and only `decide` moves it out of
`submitted`/`blocked`. Nothing else may write `status:`.

Every artifact is published through a temporary file and one `os.replace`, and
a decision transition runs under an owner-scoped lock against a durable transition
identity, so an interruption leaves complete files and a finishable transition.

`decide` never rewrites a submitted report body. It updates that report's lifecycle
keys - `status:` and `decision:` - and writes everything reviewer-authored into the
separate decision artifact: `verdict:`, `reviewer:`, `applied:`, an `evidence_digest:`
of the submitted body, and the `## Verdict`, `## Reason`, and `## Required changes`
sections. Approval and waiver reasons are recorded, not discarded. A decision that has
already been applied is not reapplied: repeating the same verdict reports the recorded
decision and returns, and a different verdict is refused, so no repeat can overwrite
review evidence or open a second round.

## The gate

`cmd_submit` is the load-bearing function. In order, it rejects a report that:

1. has a `status:` other than `draft` or `blocked` (so a report cannot be submitted twice)
2. is missing any required report section for its owner, or has one that is empty once HTML
   comments are stripped
3. still contains a placeholder (`<!-- TODO`, `TODO:`, `FIXME`, `XXX`)
4. has unchecked acceptance criteria, or no checked ones - unless `status: blocked`
5. is `blocked` but says "none" under *Decisions needed* - a block must state its question
6. changes paths outside the accepted implementor scope
7. omits task-local changed files from the report
8. cannot produce the diff coverage its run declares - a `git` run whose root, baseline,
   or snapshot is unreadable is rejected rather than treated as unchanged work
9. fails the task's `verify:` command

Verify runs **last, and only if the structural and scope checks passed**. Spending a test
run on a report that is already incomplete is waste, and the ordering means the failure a
subordinate sees is the most fundamental one first.

Acceptance maps to evidence through stable IDs. The task's criteria in order are
A1, A2, and so on; a report may carry an optional `## Evidence` table with one
row per ID holding state (`met`, `partial`, `not-met`, `not-verified`), linked
artifacts, and named gaps. When present, the gate enforces it mechanically:
every required ID exactly once, no unknown IDs or states, artifacts for every
`met` row (frozen names like `verify.stdout` or paths that exist), and no gap
attached to a `met` row. Free-text prose is never keyword-policed: "no
limitations" and coverage-by-integration notes pass or fail on structure, not
wording. A blocked submission skips evidence enforcement entirely, exactly as it
skips completion verification.

Checks 2 to 8 live in `gate_problems()`, which `cmd_submit` and the changed-evidence
re-review both call. That sharing is the point: a report body edited after review is a
fresh submission in everything but name, so it clears the same list in the same order
rather than a reduced one.

`status: blocked` is a designed escape hatch, not a failure path. It lets a subordinate
stop honestly instead of fabricating a pass, which is the single most valuable behaviour
the gate buys. Do not make blocking harder than submitting.

The captured verification describes one exact run: registered command, cwd,
start and end time, exit code, timeout, stdout and stderr artifacts, source
identity pinned before and after (a checkout that moved in between is refused,
never frozen), declared environment inputs from flat task frontmatter `env:`,
model identity as observed, requested, or unknown (a requested launch flag is
never reported as observed execution), and framework counts recognized only by a
supported parser (`pytest`, `tap`) and otherwise unknown. Declared `KEY=VALUE`
inputs override ambient state so the declared value is what the command sees;
a bare `KEY` leaves ambient alone. The bundle records both the declared list and
the effective value each declared key actually had when the command ran, and only
declared keys are ever captured, so unrelated ambient state is never recorded.
Verification is never
reused: any change to command, roots, source content, or declared inputs runs
again, so a frozen result always describes the bytes beside it. The frozen
contract revision binds the verdict too: an accepting decision against a task
edited after the freeze is refused until a fresh round verifies the new oracle.
An approval additionally requires a passed captured verification for the exact
frozen bundle: a round whose verification is `skipped` cannot be approved in any
mode, and the refusal names waiving as the honest alternative. Waiving accepts
without claiming a pass, so it stays available for a skipped round. Recording a
skip with `--skip-verify` and a reason stays possible; concluding a pass from one
does not.

## Atomic publication

`publish()` writes every document and every JSON artifact the same way: fill a
temporary file beside the target, flush and `fsync` it, copy the target's mode when
one exists, then `os.replace`. A reader therefore sees either the previous artifact
or the new one, never a truncated document, and an interrupted publication leaves
an inert `.<name>.<pid>.tmp` file rather than a half-written report. Nothing globs
those dotfiles, so a leftover temporary is never mistaken for a round or a ledger.

Appends are the exception. `docket watch` appends a delivered key to its ledger
under `flock` rather than republishing it, because rewriting a ledger to add one
line is how a concurrent watcher loses a claim.

## Retry-safe transitions

A verdict is not one write. `changes-requested` writes three files - the decision
artifact, the report's lifecycle keys, and the next round - and approve and waive
write two. Interruption anywhere in that sequence used to be unrecoverable in one
specific place: a crash after the report status write left `status: changes-requested`
with no round 2, and every retry answered "already changes-requested" and returned,
so the round was never opened and the task was stuck for good.

Three things make the sequence recoverable.

- **An owner-scoped lock.** `owner_lock()` holds `.locks/<owner>.lock` for the whole
  of `docket decide`, so two reviewers cannot interleave the writes of one owner.
- **A durable transition identity, carrying the decision itself.** `transition_id()`
  hashes run, owner, round, verdict, and reviewed evidence digest into a `txn:` value.
  It is written into the decision frontmatter, which keeps lifecycle identity in the
  document, and into a journal at `.transitions/<owner>.json` that records the planned
  steps, the ones already applied, and the complete reviewer-authored payload -
  verdict, reviewer, reason, and decision body. The journal is published *before* the
  first artifact write, so the reviewer's text exists on disk even when the transition
  is interrupted before any document exists. `docket status` lists any journal still
  `in-progress`.
- **Artifact-derived reconciliation.** `applied_steps()` recomputes which steps are
  done from the artifacts themselves, before every attempt. The journal records
  intent and progress; the artifacts are the truth. That is what lets a retry finish
  a transition whose journal was never written, including one an older docket left
  behind, and what keeps `open_next_round()` from ever opening a second round.

A retry therefore finishes the recorded transition, and finishes *that* decision.
`pending_payload()` recovers the journalled reviewer text and `adopt_pending_payload()`
applies it, so repeating the bare verdict is enough: the reviewer never restates a
reason they already gave, and a waiver retry does not need its mandatory `--reason`
again, because the reason is already recorded. The mandatory-reason check therefore
lives in `decide_locked`, where the recovery state is known, not in argument parsing.

Recovery is not an opening to redecide. The journalled payload is immutable in every
field: a retry may repeat exactly what was recorded, or supply nothing at all, and
anything else is refused. That includes filling in a reason the interrupted attempt
recorded as absent. The journal is written before the first artifact precisely so
that it, and not the invocation, is the decision, so a retry that added a reason
would publish a decision nobody made under an identity somebody else began. Anything
new belongs in the next round. Repeating a *different verdict* while a transition is
unfinished is refused too, so a retry can never turn an interrupted changes request
into an approval.

A waived-task reopen follows the same rule through its own journal. `reopen_decide()`
detects an in-progress `reopen-waived` journal before selecting the latest report,
so a retry after the next-round draft already exists finishes the journalled round
instead of refusing that the new draft is not waived. `reopen_finish()` reuses the
existing draft and never opens a second round; the recorded reason may be omitted
on retry but never replaced, and concurrent retries serialize under the owner lock
and settle as one reopen. Collision validation runs before any mutation and again
before completion, and the prior report, decision, and bundle are never rewritten.

## Five-role lifecycle

Under `workflow: five-role-v1` every lifecycle operation carries role identity
(`--as`) and is checked against this table before any mutation. Legacy runs
skip the checks entirely and keep their original semantics.

| Operation | May perform it | Guards |
| --- | --- | --- |
| `submit` a task round | implementor (orchestrator for the aggregate) | gate, scope, diff, verification |
| `verify` a submitted round | verifier | round submitted; one numbered artifact per attempt |
| `decide --approve`, `--waive` | reviewer | passing verification for the exact frozen bundle and live contract; low-risk exempt owners only by explicit policy |
| `decide --changes` | reviewer | numbered required changes; correction budget |
| verifier-triggered correction | verifier in standard, checker in quick, only under `verifier_correction: allowed` | records the acting role, never reviewer approval |
| `decide --reopen` | reviewer | waived round, collision check, one next round |
| `migrate --to five-role-v1` | explicit human authorization | preserves artifacts, retires legacy event identities |

Authority is exclusive, not additive. Verifiers pass, fail, or record
uncertainty; they never approve or waive. Orchestrators assign, recover, route,
and assemble packets; they never approve disputed work, waive requirements, or
alter acceptance. Reviewers alone approve and decide technical waivers. Planners
own intent and constraint amendments. Deterministic verification is
infrastructure and cannot impersonate verifier or reviewer judgment.

A verifier pass leaves the report `submitted`: it means ready for review, never
approved. Submission is never terminal under `five-role-v1`: an `executor:
orchestrator` task and a combined-topology aggregate both record `submitted`
on `docket submit` and become terminal only through a reviewer decision.
Legacy runs and runs with no workflow key keep their completion shortcuts
exactly: the same submissions record `completed` with the same message and
bundle. Findings live in numbered `<owner>-verification-NN.mdx` artifacts
carrying round, attempt, contract revision, and source digest; a retry on the
same submission takes the next attempt number. A recorded verdict for the
exact round and evidence resolves its verifier event, so the event is no
longer derived as pending; a new round or a re-review that freezes a second
bundle for the same round derives a new event with a new identity. Local corrections - verifier
returns and reviewer returns - share one budget per chain
(`correction_limit`, default 2). A gate refusal is the implementor's own check before handoff, so it is recorded as a machine observation and never charged. One return to the implementor is charged once:
a verification fail is charged only when it opens the correction itself, since a
fail left for the reviewer returns nothing until the reviewer's changes do, and
a retried changes request is charged by its decision artifact. Exhaustion writes
one durable escalation under `.escalations/` and refuses another round; it never
auto-approves, auto-waives, or opens endless rounds. The budget is plan policy,
so the escalation wakes the plan owner (the planner, or the coordinator in
quick), not the reviewer that hit it: `docket escalation <run> <owner> --grant N
--reason TEXT` extends that chain, and the reviewer is then woken with
`budget-granted` to apply its refused changes. An accepting verdict closes any
escalation it leaves open. An approval packet names the exact task
revision, bundle digest, and verification artifact it binds. A closed
milestone batch under `five-role-v1` is review-ready only when every
submitted member has a resolved verification for its current round and
evidence, where resolved means a recorded `pass` or `uncertain` verdict or
`verifier_exempt` coverage; a `fail` verdict or a correction keeps it
 unready. A changed verdict or finding moves the readiness revision so the
 reviewer receives a new event, while unchanged state derives the same
 identity. A closed batch under `five-role-v1` also derives one reviewer
 frontier when a submitted member with resolved verification blocks a stalled
 member of the same batch that explicitly depends on it through task
 frontmatter or the batch edge. The frontier names exactly those members, is a
 wake and never a combined verdict, and each decision still binds to its own
 bundle digest. It is suppressed when milestone readiness is already derived
 for the same evidence, so one decision never wakes twice. Its revision reuses
 the verification shape above, so a changed verdict, finding, round, or
  second bundle moves it while unchanged state stays stable. Legacy runs derive
  no frontier. Reopen generations, correction rounds, blocked members, and
  `verifier_exempt` keep their current effect and are never bypassed.
  A recorded `fail` verdict routes to someone who can act without ever making
  failed work look approval-ready. The verifier event resolves, milestone
  readiness and review frontiers still exclude the failed member exactly as
  above, and the legacy whole-run review batch no longer claims readiness for
  it either. Instead the reviewer receives one `verification-failed` event per
  failed submitted round, carrying the round, the failure, and a revision bound
  to the findings so a new attempt moves the identity while unchanged state
  stays stable. Under `verifier_correction: forbidden` that reviewer event is
  the route: only the reviewer may decide the required changes. When a verifier
  opens a correction under `verifier_correction: allowed`, the failure is
  answered by the new round and the reviewer event resolves; the orchestrator
  then receives one `correction-ready` event per pending correction round so it
  can re-dispatch the implementor. Neither event makes failed work reviewable:
  approval still needs a passing verification for the exact frozen bundle.

Every open lifecycle state wakes exactly the role that can move it, and `test_every_open_lifecycle_state_wakes_a_supervisor` holds that across both presets and both executors.
Under `five-role-v1` review readiness means verified: the whole-run review batch and every closed batch, milestone or not, wait until each submitted member holds a resolved verification, and verdicts and findings are part of the identity.
Readiness at submission woke a supervisor with nothing to route and no later event to wait for, so it polled until the verifier finished.
A standard run routes that wake to the orchestrator, which sends verified work to the reviewer; a quick run routes it to the checker, which holds the review duty itself, and leaves the coordinator asleep.
An orchestrator-owned task counts toward review readiness and the all-decided wake like delegated work, since five-role submission is never terminal for it; a blocked one wakes the reviewer directly, as a blocked aggregate does.
A delegated task's block wakes the orchestrator first, since it may own the answer, but only the reviewer can settle the round, so the hand-off is durable: `docket route <run> --kind blocked --owner T01 --note TEXT` writes `.routes/T01-01-blocked.json`, the orchestrator's event retires, and the reviewer (the checker in quick) derives `T01:1:blocked-routed` carrying the note until a verdict moves the round.
A message typed into the reviewer's session would sit behind a Codex poll for up to an hour, while a derived event ends `docket watch` at once.
An accepted aggregate derives `orch:N:complete` for the plan owner (the planner, or the coordinator in quick), because the reviewer settles the run and the session the user talks to would otherwise keep waiting without ever reporting the outcome.
`docket assign ... orch` (and any orchestrator-executed task) defaults its harness to the assigning session's own harness, since that session executes it.
A `correction-ready` event retires as soon as a live dispatch record binds its round, and the same wake exists for an orchestrator-owned task and for changes requested on the aggregate.
An interrupted decision transition derives one `unfinished-<verdict>` event naming the exact finishing command, read from the artifacts first and the journal second, as `applied_steps` does: a report left `changes-requested` with no next round, an applied decision over a report still submitted or blocked, or an in-progress journal.
It goes to the reviewer in five-role runs and to the legacy orchestrator or split planner otherwise, and a transition whose owner lock is held is still running and derives nothing.

A verifier-opened correction is retry-safe in every window.
The verification records `opened_correction: requested` in the same write as the finding, after the policy and the numbered-change checks pass, so nothing is recorded for a correction that could not open.
The budget charge is keyed by that verification, so a retry never charges it twice.
Repeating `--open-correction` finishes the recorded correction from the journal, the applied decision, or the requested verification, writes no second verification, refuses different findings or a different verdict until it is finished, and works even after the report step, when the round already reads `changes-requested`.
The reviewer can finish the same transition by repeating `--changes`.
A verifier-opened correction carries numbered required changes derived from
the recorded findings, never prose, so the round it opens renders in the
implementor prompt; findings that cannot yield a numbered change refuse the
opening with a diagnostic instead of writing an unworkable decision.
The decision records the role that actually acted under the run preset,
checker in a quick run and verifier in a standard run, derived from the same
authority mapping that gates who may act, so no artifact names a role the run
would refuse in `--as`. Who may trigger the correction and what it does are
unchanged, and the verification artifact keeps its acting identity as recorded
at verify time.

Routing is deterministic (`docket route --kind`): scope changes to the
implementor and scope gate, collisions and recovery to the orchestrator,
verification defects back to the implementor for verifier recheck, requirement
conflicts and plan gaps to the planner (and the user past authorized scope),
disputes and exhausted budgets to the reviewer, review defects to the
implementor and back, waivers to the reviewer, and out-of-policy spending to
the authorized budget owner.

## Standard and quick presets

Mode, workflow version, topology, and model policy are four separate concepts.
A mode selects a tested preset of the other three and records that selection.
It never weakens evidence validation and never reinterprets an existing run.

The quick preset is the default for new runs, held in `MODE_NEW_RUN_DEFAULT`.
Most runs have one clear outcome, and three sessions start and wait far more cheaply than five: every waiting supervisor is a session the harness re-enters with its whole context.
The standard preset is selected with `--mode standard`.
It uses workflow `five-role-v1` with `split` topology and five sessions: planner, orchestrator, implementor, verifier, reviewer.
Its review policy is `independent-verifier-reviewer`, so a later reader can tell an independent verifier stood behind each decision.
The quick preset is three roles for work with one clear outcome, established local verification, one writer, and no unresolved requirement or architecture decision.
A coordinator combines planning and orchestration, an implementor implements, and a checker combines verification and review.
It uses workflow `five-role-v1` with `combined` topology and review policy `combined-checker`.
Quick merges duties through explicit recorded policy, never through `--skip-verify`, a blanket `verifier_exempt`, or a legacy completion shortcut. A skipped verification may still be recorded in quick, but it cannot support an approval there either: the same rule refuses an approval over skipped evidence in every mode, and only a waiver accepts such work without claiming it passed.
Every artifact a quick run produces records `review_policy: combined-checker`, so no reader can mistake a quick decision for one an independent verifier stood behind.
Every place that names a role accepts the roles the active preset declares from one declaration: session, prompt, watch, events, inbox, arm, help, and the wake hook all advertise the union of acting roles, and quick coordinator and checker succeed in a quick run while they are rejected in a standard or legacy run with a diagnostic naming the run's actual preset. A role that does not apply fails visibly, never silently.
Both presets keep every invariant: scope claims, complete baselines, immutable content-addressed evidence, source-drift rejection, honest blocking, and decisions bound to exact bundle digests.
The two-role quick variant is deferred: `--mode quick --agents 2` refuses explicitly and never silently produces the three-role preset.
A run that outgrows quick records an explicit escalation request with `docket escalate-mode --reason TEXT`.
The request names the reason, preserves every artifact, leaves the mode unchanged, and recaptures no baseline.
No command silently changes a live run's mode.
The new-run default and the legacy decode use different constants.
A run whose `plan.mdx` has no workflow key is still read as legacy, and a run that declares `legacy` or `five-role-v1` keeps exactly its current semantics.
An unknown or malformed mode or workflow value refuses with a concrete diagnostic and creates nothing.
It never falls back to legacy or to standard.
`docket status` shows each run's mode, so the operating preset is visible without reading `plan.mdx`.

## Prompts, feedback, and improvements

`docket prompt` renders each role prompt deterministically for its workflow,
role, and stage from the canonical contracts, the task or aggregate contract,
selected guidance, and resume and evidence pointers. Same inputs render
byte-identical output. The stage is derived from lifecycle documents (initial,
correction, resume, verification, review); an explicit flag is for inspection
only and never the authority, and dispatch always derives. The renderer carries
a revision and selects on mode as well as workflow, role, and stage. A planner prompt about the aggregate carries the plan objective
and aggregate material, never task placeholders. A missing required input
refuses with a concrete diagnostic naming the artifact and never emits a
placeholder; dispatch still records its binding with the digest explicitly
unavailable. A correction carries every numbered required change with bundle
and verification pointers; resume names only the latest ready handoff, names the mechanical checkpoint when no ready handoff exists, and
otherwise reports mechanical recovery with targeted rediscovery from the task, the accepted capsule, and the task-local diff. A draft is never named as ready. Literal
commands, code fences, and Markdown tables survive unchanged. The digest record
carries workflow, stage, renderer revision, and the revision of every source
rendered. Guidance comes from reviewed cards under `references/model-profiles/`
selected by task triggers inside a token budget that is never padded. The mandatory contract is the relevant constraints from the plan, the task, Existing decisions and Discovery constraints, not Out of scope alone: the prompt carries the task Out of scope plus Existing decisions plus Discovery constraints plus the plan hard constraints and plan risks where the plan states them. Mandatory contract, acceptance criteria, and hard constraints are mandatory and never
truncated; mandatory size is reported separately from optional size. The budget
covers everything optional (model profile, every card, and their headers and
separators) and the reported guidance size matches the bytes carried. Zero
means no optional guidance at all and still carries every mandatory section; an oversized first card is skipped. Adding mandatory sections never drags playbook prose or the whole task file into the prompt.
Selected and rejected guidance is recorded with reasons in the prompt digest.
Model matching is exact on the normalized identifier, never a substring, and
one-run model defaults are removed. Unknown models receive no model profile
and only task-relevant cards under that policy. Model, provider, and effort
are execution metadata, never persona filler. Raw observations never enter
prompts. Verifier prompts add a universal `honest-acceptance` duty and select
the offline-operation, changed-oracle, event-aggregation, content-fingerprint,
and configuration duties by applicability to claims in the frozen contract and
submitted report. Every selected duty states why it applies. The selector is
never an allowlist: the prompt explicitly invites the verifier to raise any
other obligation or risk that discovery reveals.

Every prompt has one shape: the role contract, one metadata line (`stage | workflow | mode | review_policy`), the authority line, the task contract, the latest decision on a correction, the applicable obligations for verification, selected guidance, numbered steps, pointers, and one revisions line.
The steps, rendered by `prompt_steps`, carry the run's real file paths and the exact commands for that role and stage with the `--as` identity the preset requires, so a worker can act on the prompt alone instead of reading its playbook first.
A contract field nobody stated is left out rather than rendered as `none`; `hard constraints` always appears so its absence is explicit.
Reference prose is hard-wrapped for human editors, and `unwrap_markdown` joins it so each paragraph and list item is one line in prompts and in `docket help`, while fences, tables, and headings keep their bytes.
Guidance-card triggers match at the start of a word, so a stem like `concurren` still selects while `input` no longer pulls in harness guidance and `lock` no longer fires on `block`.
The quick checker receives the verification obligations whenever its round still awaits verification, since it performs the verifier duty.
Every draft report round, at assignment, at dispatch, and when a correction opens, is seeded with the task's acceptance criteria as unchecked boxes when it still holds the template placeholder, because retyping them exactly is the commonest way a worker fails the gate; nothing a worker wrote is ever replaced.

Feedback is embedded in every run so bottlenecks surface as docket is used.
Every rendered prompt ends with a step asking the role to record what docket itself cost it that round, or `none`, and `docket feedback --add` ties the report to the dispatch's prompt digest and model automatically.
Each record is also appended to a user-level log (`~/.local/state/docket/feedback.jsonl`, `DOCKET_FEEDBACK_LOG` to move it, `off` to disable), so observations accumulate across runs and projects.
docket appends machine observations of its own at the points that cost roles effort, at no model cost: a gate refusal, a prompt rebound after the task changed, a resume, and an exhausted correction budget.
`docket feedback --digest` summarizes the log by role, category, and recurring item; the `none` reports are the denominator that shows which roles and stages run without friction.

Each role's harness session is noted too, so its token usage and full conversation can be reviewed later without asking the agent for anything.
When a role runs one of its own commands (`watch`, `arm`, `submit`, `verify`, `decide`, dispatch commands, `feedback --add`) inside a harness, docket appends that session to the run's `.harness-sessions.jsonl`.
Claude Code and Codex name their session in every command's environment (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`).
OpenCode names only its process (`OPENCODE_PID`), so its session is the one whose running shell call, in OpenCode's own database, is this very command; newest-session guesses are wrong whenever several sessions are open.
Variables inherited from an unrelated session are never trusted: the harness process must be an ancestor of the docket process (`CLAUDE_PID`, `OPENCODE_PID`, or a `codex` process).
`docket usage <run>` reads each session's own transcript (Claude Code and Codex JSONL, OpenCode's database) and prints tokens per role; `--archive` also copies each transcript (subagents included, OpenCode via `opencode export`) into a private `sessions/` directory beside the feedback log and logs the usage there, and the final aggregate verdict archives automatically.
`docket feedback --digest` then shows token usage by role across runs.
Transcripts can hold secrets, so the copies stay in the user's own state directory with private permissions, never in the project; `DOCKET_SESSION_CAPTURE=off` disables noting sessions.

Feedback is optional evidence, never a gate. Any role may record a short
observation at its handoff (`docket feedback --add`); absent or failed
feedback never blocks submission, blocking, handoff, approval, waiver, or
completion. Machine observations stay separate from causal interpretation, and
durable transport records reconcile into machine observations after a crash
(`--import-ops`) without duplicating incidents: corroborating roles on one
task incident count once. The cross-run backlog (`.docket/improvements/`)
shows category, severity, independent incident and run counts, and status
along `observed -> proposed -> trial -> adopted` (plus `rejected`); proposals
link incidents, benefit, change, and evaluation, and adoption links the change
revision and trial result. Promotion never edits instructions, gates, or
policy by itself - adoption records the change, and a project-local finding
stays local until explicitly promoted. `docket retrospective` summarizes a run
mechanically with no model calls and reports premium-token use as unknown.

Legacy runs migrate only through `docket migrate --to five-role-v1`, which
records the old and new workflow versions, preserves every artifact
byte-for-byte, and reconciles event identities so stale legacy events cannot
act on revised state. An interrupted migration resumes; a migrated run reports
`already five-role-v1` instead of rewriting history.

## Dispatch, budgets, and amendments

`docket dispatch` binds one task round to one session: dependency readiness
(task and batch edges), exclusive scope ownership, registration validity, task intent, and
provider concurrency are all enforced before launch, and every refusal names
the exact unmet condition.
A dependency edge that would create a cycle, including a self edge, is refused at ingress by both `docket assign --depends-on` and `docket batch --depends-on` with a diagnostic naming the cycle, and nothing is published.
A batch edge whose source is not a member of that batch is refused at ingress with a diagnostic naming the non-member source, and nothing is published.
The source check runs against the members declared in the same command, so an edge for a member added in that command still succeeds.
A diamond, a chain, and re-declaring an unchanged edge still succeed.
A run that already holds a cycle stays readable and new unrelated work still enters, because only a cycle using a new edge refuses.
A batch written before the source rule stays readable: dispatch and readiness only read edges keyed by members, so a stored non-member edge is inert and never blocks member work.
`docket dispatch` runs the same task-intent check as `docket validate-task` and refuses before writing a dispatch record, naming the same problems.
A prompt whose mandatory contract would carry unresolved template placeholders is refused as a missing required artifact rather than rendered.
A same-session retry after the task or plan changed re-renders the prompt and rebinds `prompt_digest`, keeping the replaced digest in `prompt_history`, so a record never keeps describing bytes nobody should send; nothing else about the binding moves.
A retry with the same session and the same
registration generation adopts the same record, so a crash before or after the
launch acknowledgement never creates a second writer. A retry with the same
session name but a newer generation is a different writer after
re-registration and is refused with a pointer to
`docket resume` as the recovery. Execution capacity and held scope are
different quantities counted separately: live dispatch records measure
execution slots derived from the lifecycle documents, while accepted scope
stays owned through `scope_collisions` across verification and the review
boundary, so releasing a slot never releases scope. A record stops counting as
live when its round reaches `approved`, `waived`, or `completed`, when its
round is superseded by a later report round, when its owner or session no
longer describes live work in the task, report, or registration documents, or
when a ready handoff hands the task to a replacement. A ready handoff releases
the slot and keeps scope held; `docket resume` then moves the same round to
the new session and holds exactly one live record, preserving every model
field. `docket reconcile` reconciles dispatch records the same way it
reconciles delivery pending events: it derives liveness from the documents
rather than trusting the record, persists a release that keeps the model
fields, and names each reconciled owner with its document-derived reason.
`docket status` and `docket health` report the same live count, so the number
shown always matches the number of live dispatch records. The concurrency
claim is serialized across owners with a run-wide capacity lock held only for
the read-check-write of the cap inside `cmd_dispatch` and `cmd_resume`;
dependency, scope, and
registration checks stay outside, so an ordinary dispatch with no contender
completes promptly and unrelated work is never serialized. `docket resume`
claims capacity the same serialized way dispatch does: it re-reads live
capacity inside the capacity lock, refuses with the cap condition when the cap
is full, and excludes its own predecessor owner and round so a legitimate
takeover of a slot the departing worker still holds is never refused by its
own record. `docket resume` checkpoints first - diff identity, last
verification, session and model history, task and decision pointers - and the
checkpoint is always mechanical: an automatic snapshot is never a ready
semantic handoff, and abrupt loss without one still recovers through targeted
discovery. The checkpoint records whether measured work exists (`work: none`,
`changed`, or `unknown` when the evidence cannot be measured); a replacement for
a worker that changed nothing renders the initial prompt, since there is nothing
to rediscover. `docket switch-model` walks the approved fallback list; past its
end, or past the spending tier, exactly one compact exception opens instead of
silent higher-tier spend. An initial `docket dispatch --model` outside the
approved list is refused through that same open exception before any record is
published, so an approved policy cannot be bypassed by a new dispatch.
`docket resume` launches a new session too, so it is held to the policy in force now: a recorded model the plan no longer approves is refused, and `--model` names an approved replacement, recorded in `model_history` as a resume and read back as unobserved until `docket set-model` verifies it.
Carrying the predecessor's model forward unchecked let a policy change be bypassed by resuming a correction round instead of dispatching it. Model
policy has three states: absent means no `primary_model` and no
`fallback_models`, and any model dispatches with one plain line stating the run
has no approved model policy so nothing is being enforced; single means a
`primary_model` with an empty `fallback_models`, and exactly that one model is
approved; list means a primary with fallbacks, and exactly those models are
approved. Every approved or exceptional transition keeps `model_history`, and an
exception names its owner and stays open. Each dispatch record binds the
rendered prompt bytes as `prompt_digest`, recomputable from `docket prompt`
output for the same role and model, without changing those bytes. Docket
records bindings and never launches models: a new record reads
`model_observed: unobserved` until `docket set-model` verifies the live
harness, and dispatch output says it recorded a binding rather than that it
started anything. Policy lives in flat plan frontmatter
(`primary_model`, `fallback_models`, `max_concurrency`, `correction_limit`)
or not at all - never nested. No numeric spend limit is enforced, because usage
is not observable; missing cost and model
telemetry reads `unknown`, with invocation counts only as a labelled proxy and
never as a spend guarantee,
and task sizing warns on guessed breadth without ever rejecting on file
count.

Amendments version the contract. `docket propose-amendment` carries only the
decision needed, conflicting constraints, evidence pointers, a recommended
alternative, and impact; the planner accepts against the edited task, which
records old and new revisions. Acceptance retires the affected dependents'
events and blocks their accepting verdicts until a fresh round reverifies the
new contract, while unaffected tasks continue and delayed old-revision events
cannot act.

## Review packets, release, and qualification

`docket review-packet --role reviewer` assembles a self-contained packet for a
fresh context: objective, per-task acceptance coverage with exact bundle,
verification, and decision revisions plus stable acceptance IDs and evidence
states, evidence deltas with root-qualified patches, the aggregate bundle with
its digest, root-qualified aggregate diff, and constituent pins when one exists,
verifier findings, integration evidence, waivers, open stalls, escalations,
pending amendments, and complete plan risks. The default packet selects only
owners actually under review, those with a submitted or decided round carrying
frozen evidence, so a planned task that has never been submitted no longer
refuses work that is ready. `docket review-packet --role reviewer --batch B1`
scopes the same packet to exactly that batch's reviewable members and no other
owner. Every referenced task and aggregate
bundle is validated with the full integrity checks before rendering; a missing,
damaged, stale, or unbound bundle refuses the packet with a specific error
rather than rendering reviewable-looking evidence. A `--correction` packet and
a `--final` packet keep their current selection and behavior exactly. Evidence bodies shrink first
toward roughly 1,000-2,000 estimated tokens. The substantive Findings body of
every named verifier artifact, each complete waiver reason including its final
qualification, every outstanding numbered required change, and every report's
actual Decisions needed text are mandatory and are never shortened. A late
finding is rendered in the same order as the earlier findings, not discarded
by a summary. When mandatory material alone exceeds the target, the packet
states the overage and adds an explicit required-reading manifest naming the
artifacts the reviewer must open. Every pointer resolves into an immutable bundle. A
`--correction` packet for one task adds old and new bundle digests, the
correction delta, refreshed verification, expanded scope, and the addressed
findings. An approval binds the revisions named here, never a verifier pass
alone. The aggregate carries its own integration verification: the orch report
declares an integration `verify:` command at assignment, the submit gate runs
it, and the frozen bundle keeps the command, timing, exit status, outputs, and
source revision together. Delivery qualification artifacts are content-addressed
like bundles: every record carries its own digest plus the content tree it
exercised, and only a complete `passed` qualification with every required M6.4
check, captured commands, identities, exits, probe, event, session, adapter
version, and source tree satisfies the release gate; `blocked`, `failed`,
unreadable, malformed, and hand-written artifacts each keep it blocked with
their exact reason. `docket suite --qualify` captures the command bytes,
working directory, bounded timestamps, exit status, outputs, and the content
tree before and after one suite execution, refusing source drift during the run
or output without a unittest summary.
Stdout and stderr stay separate frozen artifacts and exactly one of them must
carry one complete terminal `Ran N tests` plus `OK`/`FAILED` pair.
Ordinary progress output in the other stream is fine, but summary-shaped output
there is not: a count or terminal verdict that forms no complete terminal
summary of its own refuses the whole capture even when the selected stream
reads green, because the two streams then disagree about what the run did.
A record is green only when the parsed verdict, the failure count, and the
process exit all agree, so a complete `FAILED` summary can never turn green
because a wrapper returned zero. Presentation is normalized before parsing by
one shared ANSI helper: runners colorize the terminal verdict when
FORCE_COLOR is set, and stripping those ANSI sequences lets a genuinely green
colorized suite qualify without weakening any refusal, since only presentation
is removed and never summary structure. A bare count with no verdict, a summary
split across streams, a doubled summary, and trailing noise all still refuse
with their current diagnostics, and the same normalization applies wherever
frozen suite output is re-validated, so a release frozen from colorized output
still validates later.
The identical rule applies in capture, artifact validation, release freeze, and
final validation. `docket release --freeze` consumes that
artifact rather than caller assertions, binds the suite and every qualification
to the exact content tree they exercised (HEAD plus staged, unstaged, and
untracked content with paths, types, and modes, never a bare HEAD); and it pins
qualification artifacts, suite
execution record, aggregate digest, plan, task contracts, verifier findings,
decisions, run-state snapshots, and M4-M11 milestone digests (or explicit
unavailable entries for history that predates milestone bundles, never invented
digests) under one content digest. `review-packet --final` renders purely from that frozen release:
milestone inventory, full-suite result, real-adapter result,
unresolved risks, installation state, and remaining decisions, each with its
immutable digest; later live edits are ignored in favor of frozen bytes while
any source-tree movement refuses as stale; it refuses unfrozen, damaged, stale,
or fabricated release inputs. A task or milestone packet legitimately precedes
a frozen release and says so, and must never be mistaken for final
qualification.
It carries every pinned bundle - the aggregate, each constituent, and each milestone - with its manifest, report, patches, baselines, verification output, a ledger filtered to exactly the pinned entries, and the run-private Git object store.
The release manifest lists the digest of every frozen file and a complete inventory over names, entry kinds, modes, symlink targets, and bytes, and the whole directory is staged beside its address and published by one atomic rename.
Re-freezing identical content converges on the existing address only after the staged bytes validate and match the published unit byte for byte; conflicting content at an address refuses.
Frozen validation reads the release's own bundle copies, so deleting or editing the live bundle store after a freeze cannot change the packet.
Source-tree staleness for the suite and delivery qualifications is still checked, and damaged, added, removed, retargeted, mode-changed, or reused frozen evidence reports `DAMAGED` precisely.

Operational usage accounting is kept separate from review judgment.
`docket metrics` keeps approval counts from reviewer decisions, prompt renders as an invocation proxy and never proven model calls, wall time as the first-to-last artifact span, and delivery receipts and duplicates as observed.
A required unknown stays unknown and never blocks ordinary verification; READY means ready for independent review, never approved. `delivery --qualify`
exercises a real hook announcement, a SIGKILLed hook restart, a real
claim/send/ack/retry round-trip with generation-reuse refusal, an
active-input refusal, and a disposable herdr pane lifecycle, freezing commands,
process identities, exit statuses, event and lease history, and outputs into
`.delivery/qualification-<role>.json`; where no safe disposable mechanism
exists it records an honest blocked artifact naming the capability, production
stays manual, and nothing is ever typed into a live input surface.

## Reviewed evidence must still be the evidence

A `changes-requested` draft records the digest of the report body it was written
against. If that body changes before the transition applies, the verdict would be
applying to evidence nobody reviewed. `decide` refuses. The reviewer either
restores the reviewed body or passes `--re-review`.

`--re-review` re-runs the whole gate, not only the tests. A changed body is a fresh
submission in everything but name, so `reverify_changed_evidence()` calls
`gate_problems()` - the same structural, placeholder, acceptance-mapping, scope, and
diff-coverage checks `cmd_submit` runs, in the same order - and only a body that
clears all of them is put back through the registered `verify:` command. That
ordering matters for the same reason it matters at submission: a report edited into
an unchecked, truncated, or out-of-scope state must be refused on the report, not
excused by a green test run. Only then does the decision record the new digest and
the `superseded_evidence:` it replaces and apply. A failing gate or a failing
re-verification refuses the decision outright.

A blocked report clears the blocked list and stops there, exactly as submission does,
and the decision records `reverified: blocked`. The reason is the same one that makes
`docket submit` skip verification for a blocker: blocking is the honest way to stop
when the command cannot pass, so requiring a passing run before the waiver may be
reviewed would make an edited blocker undecidable and blocking harder to use than
submitting. The structural blocked checks - required sections, placeholders,
acceptance mapping, scope, coverage, and a stated question - all still apply.

## Baselines

A baseline is what a later patch is generated against, so a fingerprint is not enough:
a file that was already dirty at assignment cannot be reconstructed from its hash. Each
root's baseline records the pinned commit, the staged difference against it, the
unstaged difference against the index, and every untracked entry's content, executable
bit, and symlink target. Patches are captured with `--binary`, so binary content,
deletions, and mode changes survive. Nothing is committed or stashed to obtain any of
it, and everything stays under `.docket/` as local project data that can contain
secrets.

Branch and HEAD are re-read at the instant of capture and the pinned base tree is that
same HEAD, so a commit made between declaration and assignment cannot leave the
metadata describing one state and the patches another. The declaration's own values are
kept beside them as `declared_branch` and `declared_head`. A root whose Git identity no
longer matches its declaration fails capture rather than being captured as itself.

Each path that was already dirty at capture is recorded with its porcelain state, its
worktree mode, its content digest, and its index entry. A content hash alone cannot see
an executable-bit change or a staging change layered on a path that was dirty when the
baseline was taken, and either one would then be missing from the task diff.

A task assigned with a `--depends-on` edge whose dependency is not approved yet defers its task baseline to `docket dispatch`, which captures it once, whole, before the dispatch record exists, and refuses the dispatch when it cannot.
Captured at assignment, the baseline predated the dependency's work, so the dependency's approved but uncommitted change showed up as the dependent's own out-of-scope change: the gate refused it, and the only way past was to widen the dependent's scope over the dependency's files and freeze that work into the wrong patch.
Deferral keeps invariant 17, since the baseline still precedes the task's own dispatch and is never recaptured, and the dependency's work becomes the dependent's starting point.
When the dependency is approved, the orchestrator (the coordinator in quick) receives one `dispatch-ready` event for the dependent, which retires once the round is dispatched; nothing else re-entered it at that moment, because the dependent is still a draft.

`docket assign` takes the run baseline before it opens the first task, because a
baseline taken when the aggregate report is assigned has already lost the run's initial
state. Each task then records its own baseline at assignment and never again: discovery
only reads, so a capsule submitted later refines `scope`, never the baseline underneath
it.

**A baseline is captured in full or dispatch is refused.** Under `evidence_mode: git`,
`docket assign` captures the run baseline and the task baseline *before* it creates the
task, the report, or the discovery capsule, and dies if either is incomplete - no
declared roots, a missing or re-pointed checkout, an unreadable index, or an untracked
file over `UNTRACKED_CONTENT_LIMIT` that cannot be stored whole. A partial capture is
discarded rather than published, so a snapshot that exists on disk is a complete
baseline and nothing downstream has to ask whether the evidence beneath it is. Truncated
untracked content used to be recorded with a `reconstructable: partial` marker while
coverage still read `available`; a partial baseline is now never labelled available.
Documents-only assignment is untouched and stays usable.

## Diff coverage

`assignment_evidence` returns an explicit coverage state, never a bare change list:

| Coverage | Meaning |
| --- | --- |
| `available` | the recorded Git root answered, so the change list is real |
| `documents-only` | the run declared it has no Git evidence, and says so |
| `unavailable` | `git` evidence was expected and could not be produced |

Change lists are computed per declared root and are root-qualified as `<alias>:<path>`
whenever a run declares more than one root, so a single-root run reads exactly as it
did. Paths committed during the task count: current dirty status alone misses work an
implementor committed, which used to present as an empty diff. A mode-only or
index-only change on a path that was already dirty at assignment counts too, because a
path is compared on state, mode, content, and index entry rather than content alone.
Scope paths resolve to
the deepest declared root containing them, so a nested repository owns its own paths
instead of being absorbed by its parent, and `<alias>:<path>` addresses a checkout no
relative path can reach. An unresolved alias, an ambiguous path, a missing root, an
incomplete baseline, or a root whose baseline was never recorded makes coverage
`unavailable` with the precise reason - never an empty diff.

The distinction exists because the alternative shipped: `dirty_paths` returned an empty
set whenever Git failed, so a missing root, a deleted worktree, and genuinely unchanged
work were indistinguishable, and `docket diff` reported "no worktree changes" for all
three. `dirty_paths` now returns `None` when Git cannot answer, an assignment snapshot
records whether a baseline was captured, and `docket diff` exits non-zero on
`unavailable` instead of printing a clean diff. Unavailable evidence is never described
as an empty diff.

## Frozen task-round and aggregate bundles

A change list describes a change surface. It does not preserve one. `docket submit`
therefore freezes the whole round into an immutable, content-addressed **bundle** under
`.bundles/<owner>/NN/<addr>/`, and every later verdict binds to that address:

| Inside a task bundle | What it pins |
| --- | --- |
| `contract.mdx` | the exact task revision the work was done against |
| `baseline/snapshot.json` | the task baseline record itself, copied out of `.snapshots` |
| `baseline/<alias>.staged.patch` | the staged half of that root's captured baseline |
| `baseline/<alias>.worktree.patch` | the unstaged half of that root's captured baseline |
| `baseline/<alias>.untracked.json` | the untracked content, modes, and symlinks it captured |
| `<alias>.patch` | the root-qualified full patch from that baseline to the result |
| `source` | the resulting tree, HEAD, and branch of every declared root |
| `report.mdx` | the report body exactly as submitted |
| `dependencies.json` | the consumed-input record the verification was taken against |
| `verify.stdout` / `verify.stderr` | the verification as captured, with exit code and timing |
| `delta/<alias>.patch` | the correction relative to the previous frozen bundle |

An aggregate (`kind: "aggregate"`) bundle under `.bundles/orch/` pins the same
artifacts, but its baseline is the run baseline (taken before the first implementor
edit), its patch is the full aggregate diff against that run baseline, its contract is
the plan it fulfills, and it additionally records a `constituents` list of every decided
task-round bundle digest it was submitted against. An aggregate whose pinned constituent
has moved, is missing, or is damaged is reported stale rather than read as current, and a
waived constituent reopened with `docket decide ... --reopen` visibly invalidates the
aggregate.

The address is `sha256` over the manifest, which itself carries a `sha256` of every
artifact beside it. So a bundle cannot be edited without changing its own name, and a
later workspace edit, a later report edit, a retry, or a whole later round can only ever
produce a *different* address. `docket bundle <run> <owner>` recomputes every artifact
digest and reports `DAMAGED` rather than showing evidence it cannot vouch for.

A bundle is self-contained on purpose. `.snapshots/` is where a baseline is *captured*,
not where it is kept for review, so the record and the three reconstruction artifacts of
every root are copied into the bundle and covered by its digest. Deleting or rewriting
the capture afterwards cannot reach evidence that was already frozen.

Integrity is four things, not one: the manifest still addresses itself, every artifact
it claims is present with the recorded bytes, the consumed-input record it froze is
present and hashes to the revision it names, and every Git tree it pins is still
resolvable. That last check matters because a patch, a delta, and a source revision are
all stated against trees in `.bundles/objects`. Losing that store leaves a manifest that
hashes perfectly to itself and a round nobody can rebuild, so `bundle_problems` walks
each pinned tree and every object under it and reports `DAMAGED` when one is gone.

A capture that vanishes *during* the freeze is a refusal, not a note. If a baseline
reconstruction artifact cannot be read between rebuilding the trees and copying it into
the bundle, the freeze fails and nothing is handed off. Recording it as `unavailable`
inside a bundle that still reads as intact would be the exact false assurance the freeze
exists to prevent.

A bundle frozen before baselines carried their own Git identity has no `git_dir` to check
against. Those fall back to the run's root declaration by alias, so an older round stays
checkable rather than becoming unreadable. The fallback is compatibility only: a bundle
frozen now carries its own identity, and a missing object still reports `DAMAGED` on
either path.

### How a patch against a dirty baseline is produced

The baseline is usually not a commit: it is a commit *plus* staged content, unstaged
content, untracked files, modes, and symlinks. Diffing against `HEAD` would therefore
attribute somebody else's uncommitted work to the implementor, and would lose an
untracked file entirely. Docket instead rebuilds both sides as real Git trees and diffs
the trees:

1. `baseline_tree` reads the pinned `base_tree` into a **private index**, applies the
   captured staged and worktree patches with `git apply --cached`, hashes the captured
   untracked content back in with its recorded mode, and writes a tree.
2. `source_tree` reads the root's current `HEAD` into a second private index and stages
   the whole worktree with `git add -A`, so work committed *during* the task is inside
   the tree rather than invisible to it, and writes a tree.
3. `tree_patch` renders `git diff --binary -M` between the two.

Both indexes live under `.docket`, and `GIT_OBJECT_DIRECTORY` sends every new object
into the run-private store while `GIT_ALTERNATE_OBJECT_DIRECTORIES` reads the real one.
**Nothing is written into the checkout being measured**: no commit, no stash, no index
entry, no ref, no working file, and not even a loose object. That is the same rule
baseline capture follows, for the same reason.

Docket's own state directory and any undeclared embedded checkout are pruned from both
indexes, because baseline capture never recorded either of them and leaving one in a
single side would invent a change nobody made.

A bundle's `changed_paths` are always `<alias>:<path>`, even in a single-root run.
`docket diff` drops the alias for a single root because it is a display; a bundle is
stored evidence that may be read next to another run's, so it stays unambiguous.

The correction delta is `git diff` between the previous bundle's pinned source tree and
this one's. Both tree ids are recorded, so the delta is reproducible from the pins and
not only readable from the stored file.

### What binds to a bundle

A submission is one transition, held under the owner's lock from the moment the report is
read to the moment it is published. Verification, the freeze, the ledger append, and the
report write happen inside that lock, because two submitters interleaving them can leave a
published report pointing at a bundle the last read-modify-write of `rounds.json` no
longer retains. The second submitter finds a report that is already `submitted` and is
refused, which is the coherent outcome rather than a second visible round.

`docket submit` records the frozen address in the report's `bundle_digest`, and
`docket decide` refuses to record a verdict without an intact bundle for that round.
A report body that no longer matches the frozen one is *stale evidence*: the verdict is
refused until `--re-review`, which re-runs the whole report gate, re-verifies, and
freezes the re-reviewed body as its own new bundle. The decision then records
`bundle_digest`, `superseded_bundle`, and `superseded_evidence`, and the superseded
bundle keeps its own address and its own bytes. The decided report names the
replacement digest, matching the decision, and a retry of an interrupted re-review
finishes the recorded transition without opening a second round or leaving the
report on the superseded digest: the report step copies the decided digest every
time it runs.

Verification binds to the same source revision the bundle pins. The source tree is
built once before the verify command runs and again after it, and a submission whose
checkout moved in between is refused rather than frozen: a green result that does not
describe the frozen source is not evidence of anything.

It binds to the same documents, too. A captured result is evidence about one exact task
contract, report, accepted scope, and record of consumed inputs, so `submission_identity`
takes a revision of all four before the command runs and `drifted_inputs` rechecks them
after it, along with the consumed inputs themselves. Anything that moved refuses the
submission: nothing is handed off, and the concurrent edit is left exactly as its writer
left it rather than being overwritten by the body read before the run.

The freeze is given bytes, never a path to re-read. The report body, the contract, and
the consumed-input record are all captured *before* the verification runs and handed to
`freeze_task_bundle` as they stood then. Re-reading any of them after the drift recheck
would reopen the window the recheck just closed: a writer landing between the two reads
would be frozen into the bundle as though the captured result had described it.

Under `evidence_mode: documents-only`, a round still freezes - contract, baseline
reference, report body, and captured verification - and records patch coverage as
`unavailable` with its reason. It is never recorded as an empty patch.

### Provisional dependencies

A task may consume another task's frozen evidence with
`docket depend <run> <owner> --on <other>`, which pins the exact bundle digest it read.
If `<other>` is approved or completed the input is `final`. If it is merely submitted,
the consumption is `provisional` and is refused outright unless the plan declares
`provisional_integration: allowed`.

The policy permits consuming *verified*-but-unapproved work, and `consumable_problems`
holds it to that word. A bundle is refused as an input when it is damaged, when the
consumed task has moved to another round or edited its report since freezing, when the
task is blocked, when the frozen verification failed, timed out, was skipped, or never
ran, or when a `git` round froze no root-qualified patch or source revision. That last
refusal says "This is not an empty patch" for the same reason every other coverage
message does.

A provisional input is readiness, never approval. If the consumed task later freezes
different evidence, the consumer's own submission is refused, and so is any accepting
verdict: neither approval nor waiver can settle work whose input moved, because the
result was formed against evidence that no longer exists. Approving the consumer never
approves the dependency, and `docket status` and `docket depend` both say so in those
words.

Re-recording the pin is not a way out of that, on either side of the boundary.
`docket depend --on` only records into a **draft** round, because recording an input is
part of doing the work. Under a submitted or blocked round it is refused with the exact
recovery: `docket decide <run> <owner> --changes` opens a fresh round, and the input is
re-recorded and reverified there. Under a decided round it is refused outright, because recording an input belongs to
a draft round, not to a re-recording over decided evidence; a waived task is reopened
through the separate audited `docket decide ... --reopen` transition, never by editing
the pin, and the round stays visibly stale in `docket status` instead of being quietly
refreshed.

And a verdict does not trust the live record either. `dependency_problems` compares the
live record against each input's newest bundle, so re-recording a pin makes it go quiet
without anything having been reverified. `refreshed_inputs` therefore compares the live
record against the one *frozen into the round being decided*: a record that moved at all
since the freeze means the captured verification described different inputs, and an
accepting verdict is refused until a fresh round verifies against the current ones.
`docket status` reports both checks under "review readiness withdrawn".

Accepted scope is held for the whole of this: `scope_collisions` releases a path only
when its owner reaches `approved`, `waived`, or `completed`, so ownership survives
verification, submission, review, and a changes round.

## Signalling

A supervisor must be re-entered while **idle**, having already ended its turn. Three
approaches were considered:

| Approach | Why it loses |
| --- | --- |
| Interval polling from inside the agent | Each poll costs a turn and tokens, and it cannot wake an idle session |
| A long-poll the agent re-enters itself | Depends on the model voluntarily calling a blocking command forever - the one thing a prompt cannot guarantee |
| **Harness re-entry (chosen)** | The harness wakes the model. Nothing depends on model compliance |

On Claude Code that is a `Stop` hook with `asyncRewake: true`:

> If `true`, runs in the background and wakes Claude on exit code 2. The hook's stderr is
> shown to Claude as a system reminder.

So `hooks/wake.sh` runs `docket watch --armed`, which sleeps at zero token cost and exits
2 with a banner on stderr when something is actionable. This design is
[firstmate's](https://github.com/kunchenguid/firstmate); see the README acknowledgements.

Three rules keep it correct:

- **Foreground only.** The watcher runs in the hook's own process tree. Never `&`,
  `nohup`, or `disown`: the harness owns the process group and relies on that to tear the
  watcher down with the session. A backgrounded watcher outlives its session and wakes
  the wrong one.
- **Per-role ledgers.** `.woke-<role>` is append-only, one file per role. A shared ledger
  let whichever session woke first consume an event the other role needed - that was a
  real shipped bug.
- **Every waiting role must be armed.** An unarmed role is never woken, silently.
  `docket doctor` warns when only one role is armed.

`docket watch` announces derived events by reconciling durable pending records,
writing a bounded announcement lease under `.delivery/<role>/announce/`, and
appending new keys to the role ledger. The ledger alone is not delivery truth:
a crash after the ledger write but before the harness accepts the wake leaves
the durable pending event behind, and the next watcher re-announces the same
actionable event once the announcement lease expires. An active lease suppresses
duplicate wakes so concurrent watchers converge on one exit 2, and explicit
inbox pickup stays claimable throughout.
The watcher marks an announcement `delivered_at` only after writing its banner and immediately before exiting 2, and a delivered announcement is not re-announced while its identity holds.
An event usually stays derived after delivery because another role is still working on it: the reviewer deciding a routed batch, an implementor answering a correction.
Re-announcing it every lease period re-entered the supervisor every five minutes for news it had already acted on, which is polling by another name.
An identity that moves is retired and re-derived by `sweep_role`, which removes the announcement, so a changed event still wakes.

A harness re-entry replays the whole session context, so how a supervisor waits is the dominant token cost of a run.
Claude Code waits for free through the Stop hook, or once per event through a background `docket watch`.
Codex slices a blocking call into polls, each a full-context turn, capped per empty poll by `background_terminal_max_timeout`; its own awaiter agent sets that to an hour, and the signalling playbook tells supervisors to do the same.
OpenCode blocks for the whole shell timeout and has no idle wake.
Waiting on agent state (`herdr agent wait`) or timers instead of Docket events returns on idle transitions that need nobody, which is why the playbooks forbid it. `docket events <run> --role R --peek`
derives the same list, marks each key `pending` or `delivered`, and writes
nothing: it never creates, migrates, or appends a ledger, so repeated inspection
cannot consume a wake. `status` and `doctor` touch no delivery state either.
The native hook routes planner, orchestrator, verifier, and reviewer through the
same foreground watcher with separate registrations and ledgers.

Events come from `derive_events(run_dir, role)`, a pure projection of lifecycle
documents into records carrying key, destination role, workflow version, owner,
round, reopen generation, artifact revision, and message. `events()` is the
`(key, message)` view over it. Under legacy the planner sees `orch`
(submitted and blocked aggregates) and amendments; under `five-role-v1` the
planner sees amendments only and a submitted or blocked aggregate derives a
reviewer event with the same key and revision shape instead. The
orchestrator receives blockers, collisions, and handoffs immediately, but ordinary
submitted reports are held until every delegated task is submitted or terminal.
That ready set is hashed into one batch key, so a changes-requested resubmission
creates exactly one new wake without waking once per task. The aggregate-readiness
key additionally hashes the decided set, so a reopen or a later batch yields a new
identity instead of reusing the old one. To add an event type,
give it a stable key: the ledger dedupes on keys, so an unstable key causes a loop.

Durable delivery is a separate layer from announcement. `docket reconcile`
rebuilds `.delivery/<role>/pending/` records from current derivation and retires
records that are no longer derived or whose identity moved; it is idempotent and
explicit, never a side effect of inspection. `docket inbox --claim` grants one
bounded lease per event to one registered session, `docket events --ack` records
receipt (never completion), and `docket events --retry` records why another
attempt is due. A lease that expires becomes claimable again; a durable decision
or resolution retires the event automatically, and duplicate receipts, retries,
and deliveries are harmless. Sessions are registered with workspace, run, role,
session ID, generation, and name; re-registration advances the generation, and a
stale generation, wrong role, or moved workspace can neither claim nor
acknowledge. Delivery promises at-least-once, never exactly-once.

Review batches are explicit. `docket batch` opens a batch with a stable ID,
explicit members, explicit dependency edges, and a milestone flag, and `--close`
freezes membership before dispatch: a later task can neither join a closed batch
nor change an emitted readiness event. Readiness uses only closed membership and
explicit dependencies, so a future task waiting on a member never blocks the
batch; blockers, collisions, handoffs, and stall incidents stay immediate, and
capable-review readiness is reserved for milestone batches. Under `five-role-v1`
a verifier event is derived from `submitted` state alone until a recorded
verdict for that exact round and evidence resolves it, and milestone readiness
additionally requires resolved `pass` or `uncertain` verification or exempt
coverage with a revision bound to verdict and findings. A closed batch whose
verified submission blocks a dependent derives one reviewer frontier for exactly
those blocking members, suppressed when milestone readiness already covers the
same evidence and never derived for legacy runs. Once any closed batch
exists, the legacy whole-run review batch no longer fires. A reopen moves every
batch holding the owner into a new generation with journalled targets, so a retry
writes the same numbers instead of incrementing twice, and reconciliation retires
readiness derived from the old generation. A frontier key carries the batch and
generation while its revision carries the verification shape, so consumed
frontiers retire on revision movement and unchanged frontiers stay stable.

Execution health is derived separately from report lifecycle: `report=` is the
lifecycle truth while `execution=` (running, idle-unsubmitted, awaiting-review,
correction, done, stopped-recoverable, unknown) describes what the checkout
appears to be doing. Quiet work is healthy by construction: long verification
runs and unchanged worktrees are never stalls, and provider rate-limit text is a
best-effort hint, never state. A stall becomes durable only through an explicit
`docket health --flag-stall` report, which opens exactly one incident (and one
recovery event) per cause until resolved or its generation changes.

Qualified delivery never types into an uncertain input buffer. `docket delivery`
pauses and resumes per role with a visible queued count - pausing consumes no
events and pauses no implementation - capability-tests the boundary with
`--probe`, and delivers through fixed-format outbox notices carrying only run,
role, event, session, and an inbox pointer, never report prose. The probe uses
a positive, versioned capability contract: help must positively advertise a
flag-like `--send-if-idle`, `--queue`, or `--turn-boundary` capability with no
negative statement, exit zero, and pass an executable probe of that exact flag.
Ambiguous output, negative statements, unknown versions, timeouts, and nonzero
exits all report manual mode, and only a positively verified queue reports
unattended. The probed command, version, and result are recorded so `doctor`
can explain the decision. With no verified turn-boundary or queue mechanism
delivery stays queued for explicit inbox pickup or a native hook. Every
delivery attempt revalidates the recipient registration, so a renamed agent,
reused pane, or restarted session cannot inherit an old delivery. A normal
receipt and an actual duplicate are logged separately, so metrics never count
every receipt as a duplicate.

## Invariants

Break these and the system stops being trustworthy.

1. `docket submit` is the only path to a reviewer-visible state. Never hand-edit `status:`,
   and never pass `--skip-verify` to get a report through.
2. The submitted report and task-local diff are review truth. The discovery capsule and
   ready checkpoint are implementation context, not completion evidence.
3. A `verify:` command must run under `/bin/sh` with no aliases, shell functions, or
   personal `PATH` additions. A command written in an interactive zsh session can
   reference a function that does not exist in `sh`, and will then fail the gate forever.
4. The watcher runs in the hook's foreground process tree. See above.
5. Ledgers are per-role and append-only. Deduping is what prevents wake loops.
6. Round numbers are per-owner and monotonic. Only `decide --changes` opens a round.
7. Document frontmatter is lifecycle state. Snapshot, baseline, and transition JSON
   contain only mechanical evidence and progress, never competing lifecycle state.
   The transition journal records intent; `applied_steps` re-derives the truth from
   the artifacts, so a lost or stale journal cannot change an outcome.
8. A reviewer reads the diff, not only the report. The gate cannot check whether the work
   is *right*, only whether the report is *complete*.
9. A submitted report body is evidence. `decide` writes only lifecycle keys back to it;
   verdicts, reasons, and the evidence digest live in the decision artifact.
10. Unavailable diff coverage is reported as unavailable. Never as an empty diff, and
    never silently accepted by the gate under `evidence_mode: git`.
11. Inspection is not delivery. `events --peek`, `status`, and `doctor` never
    write or migrate delivery state. A claim binds one bounded lease to one
    registered session generation; acknowledgement is receipt, never completion;
    only a durable lifecycle resolution retires an event. A stale event - moved
    round, revision, role, workflow, or generation - can never be acted on.
    Batch membership closes before dispatch and never reopens; execution state
    never overrides report lifecycle; paused delivery queues without consuming.
12. Artifacts are published atomically. Nothing writes a lifecycle or evidence file
    in place, so a reader never sees a partial document.
  13. One owner's transition is serialized and recoverable. A retry finishes the
      recorded transition and never opens a second round; a different verdict against
      an unfinished transition is refused. A waived-task reopen is such a transition:
      its in-progress journal is detected before the latest report is selected, its
      recorded reason may be omitted but never replaced, and concurrent retries settle
      as one reopen.
  14. Five-role authority is exclusive. Only the reviewer approves or waives, only
      against a passing verification for the exact frozen bundle and live contract;
      a verifier pass never completes work. An approval over a skipped captured
      verification is refused in every mode with the waiver path named, while a waiver
      stays available for that round because it claims no pass. Correction budgets bound round-opening
      and exhaust into one durable escalation, never an automatic verdict.
15. A verdict applies only to the evidence it reviewed. Changed evidence requires
    `--re-review`, which re-runs the full report gate and then re-verifies before the
    decision may apply. A passing verify command alone is never enough. A blocked
    report clears the blocked gate and skips completion verification, as it does at
    submission, so an edited blocker stays reviewable and waivable.
16. The reviewer's decision text is part of the transition, not of the invocation.
    It is journalled before the first artifact write, recovered on retry, and never
    changed by a retry - not by different text, and not by filling in a reason the
    transition recorded as absent.
17. A baseline is immutable, complete, and captured before the work it measures,
    with capture-time identity. A `git` run that cannot capture one refuses to
    dispatch instead of recording a partial baseline. The run
    baseline precedes the first assignment and each task baseline precedes its own
    dispatch. A later scope change rewrites `scope` and nothing else.
18. A checkout root is identified by its worktree, not by its Git storage, and is
    only ever captured when it was declared by name. Two worktrees of one repository
    are two change surfaces. A declaration is never silently rediscovered, and an
    unreadable one is refused rather than treated as absent.
19. Every submitted task round is frozen into a content-addressed bundle before it
    becomes reviewable, and an earlier bundle is never rewritten. Later edits,
    retries, re-reviews, and later rounds produce new addresses. A round that cannot
    be frozen is not handed off.
20. A verdict binds to a bundle digest. Missing, damaged, or stale bundle evidence
    blocks the decision instead of approving whatever the workspace happens to hold.
    A bundle carries the baseline evidence it depends on and every tree it pins is
    checked for availability, so unreconstructable evidence never reads as intact.
    A baseline artifact that cannot be copied fails the freeze rather than being
    recorded as unavailable inside an otherwise intact bundle.
21. Rebuilding evidence never writes to the checkout it measures. Private indexes and
    a run-private object store are the reason a full patch can be produced without a
    commit, a stash, an index change, or a loose object in the user's repository.
22. Consuming unapproved work is an explicit run policy, the consumed bundle digest is
    pinned, and a consumer is reverified when that input moves. Only a bundle holding a
    passed, current, patch-bearing verification is consumable at all, and an input that
    moves after the consumer submitted withdraws its review readiness rather than
    leaving an accepting verdict available. Dependency readiness is never final approval.
  23. One owner submits at a time. The verification, the freeze, the ledger append, and
      the report write are a single locked transition, and the verification is bound to
      the exact contract, report, scope, and consumed inputs it described. The freeze is
      handed the bytes that were verified and never re-reads a document after the drift
      recheck.
  24. Verification is captured whole - command, environment, source, model, timing,
      outputs, and parsed counts - and never reused across a changed identity. An
      accepting verdict binds to the frozen contract revision as well as the frozen
      evidence: a task edited after the freeze needs a fresh verified round.
  25. Consumed inputs are frozen into the round, including the explicit empty set, and an
      accepting verdict binds to that frozen record. Re-recording a pin is not a
      reverification: `docket depend --on` records only into a draft round, and a live
      record that differs from the frozen one refuses approval and waiver alike.
  26. One round has one writer. Dispatch, resume, and model switches are serialized
      per owner and idempotent per session; a retry never creates a second writer.
      Amendments preserve old and new contract revisions and invalidate only the
      dependents that consumed the old one.

## Known gaps

Stated plainly so nobody assumes otherwise.

- **An interrupted publication leaves its temporary file.** The artifact is always
  intact, but the `.<name>.<pid>.tmp` beside it is never swept, since a live writer's
  temporary file cannot be distinguished from a dead one's without a claim lease.
- **The orchestrator's aggregate report now freezes its own bundle.** Aggregate
  submissions freeze an `aggregate` kind bundle that pins constituent task-round
  digests, records the run baseline, holds the aggregate patch against it, carries
  the aggregate report body and verification, and is checked for constituent
  staleness alongside artifact integrity. `docket status` names the aggregate and
  the constituent that invalidated it. A waived task may be reopened with
  `docket decide ... --reopen`, which preserves all prior evidence and invalidates
  any aggregate that depended on it. An interrupted reopen is finished by repeating
  `--reopen`; the retry reuses the journalled round and draft, never opens a second
  round, and never rewrites the prior report, decision, or bundle.
- **A run-private object store only grows.** Bundle trees are kept so correction deltas
  stay reproducible across rounds, and nothing prunes `.bundles/objects`. Deleting it is
  not a tidy-up: every bundle that pinned a tree in it is reported `DAMAGED` and cannot
  be decided. Like the stored baseline patches, it is local project data that can contain
  secrets.
- **A verify command that writes non-ignored files into the checkout fails the freeze.**
  The submission is refused because the captured result no longer describes the frozen
  source. Make such a command clean up after itself, or ignore what it writes.
- **Root declaration is a one-time choice.** A checkout created after the declaration
  is not picked up, because redeclaring would repoint aliases the existing baselines
  depend on. Declare every checkout the run needs before dispatch.
- **An untracked file over `UNTRACKED_CONTENT_LIMIT` blocks a `git` assignment.** The
  alternative is a baseline that cannot reconstruct what it claims to. Ignore the file,
  move it out of the checkout, or run the milestone under `documents-only`.
- **`events --peek` reports pending work but cannot acknowledge it.** Leased
  acknowledgement exists (`inbox --claim`, `events --ack/--retry`, `reconcile`),
  but `watch` still announces directly from derivation without taking a lease,
  so a delivery lost after the ledger append relies on the next derivation to
  re-announce it rather than on lease recovery.
- **The 3-round cap is advice**, enforced only by the orchestrator playbook.
- **No `verify:` sanity check at scope submission.** Docket could warn when the first word
  of a discovered verification command is not resolvable in `sh`. It does not yet.
- **Wake events are not authenticated.** A wake banner telling an agent to review and
  approve something is shaped exactly like a prompt injection, and reports are written by
  small models. A supervisor should confirm the run exists before acting on a wake.

## Code map

Everything lives in `skills/docket/bin/docket`, a single stdlib-only script.

| Region | Contents |
| --- | --- |
| frontmatter | `parse`, `render`, `sections`, `is_empty` |
| publication | `publish_bytes`, `publish`, `publish_json`, `fault`, `perturb` |
| paths | `root`, `run_dir`, `need_run`, reports, decisions, scopes, and handoffs |
| templates | `TEMPLATES` dict and `template()`, which honours per-project overrides |
| evidence | `porcelain_records`, `dirty_paths`, `evidence_mode`, `assignment_evidence`, `legacy_evidence`, `root_changes`, `baseline_states`, `evidence_digest` |
| roots | `root_identity`, `linked_worktrees`, `nested_checkouts`, `candidate_roots`, `build_root_records`, `declare_roots`, `read_roots`, `declared_roots`, `resolve_scope`, `split_qualified` |
| baselines | `snapshot_assignment`, `require_baseline`, `capture_root_baseline`, `capture_untracked`, `discard_capture`, `path_state`, `index_entries`, `ensure_run_baseline`, `update_snapshot_scope` |
| bundles | `private_git_env`, `private_git`, `prune_index`, `baseline_tree`, `source_tree`, `tree_patch`, `source_identity`, `frozen_baseline_root`, `freeze_task_bundle`, `publish_bundle`, `read_ledger`, `manifest_files`, `pinned_trees`, `tree_problems`, `bundle_problems`, `current_bundle`, `require_bundle`, `run_verification` |
| dependencies | `provisional_policy`, `parse_deps`, `deps_record_bytes`, `read_deps`, `write_deps`, `dependency_problems`, `refreshed_inputs`, `withdrawn_readiness`, `provisional_dependencies`, `consumable_problems` |
| submission | `submission_identity`, `drifted_inputs`, `cmd_submit` and `submit_locked` under `owner_lock` |
| commands | task validation, discovery scope, handoff, report, review, diff, bundle, depend, and preflight |
| gate | `gate_problems`, shared by `cmd_submit` and changed-evidence re-review; `task_criterion_ids`, `parse_evidence_table`, `evidence_artifact_problems`, `evidence_problems` |
| verification | `task_env`, `parse_framework_counts`, `run_verification` |
| transitions | `owner_lock`, `owner_lock_held`, `transition_id`, `read_transition`, `applied_steps`, `pending_payload`, `adopt_pending_payload`, `open_next_round`, `commit_transition`, `reopen_waived`, `reopen_finish`, `reopen_decide`, `reopen_collision_problems` |
| five-role | `routes_dir`, `blocked_route`, `route_blocked`, `is_five_role`, `plan_flag`, `correction_limit_of`, `verifier_correction_allowed`, `require_five_role`, `task_executor`, `submit_op_for`, `note_correction`, `correction_budget`, `guard_correction_budget`, `open_escalation`, `open_escalations`, `escalation_events`, `run_complete_event`, `cmd_escalation`, `latest_verification`, `cmd_verify`, `interrupted_verifier_correction`, `finish_verifier_correction`, `open_verifier_correction`, `cmd_route`, `cmd_migrate` |
| prompts | `ROLE_CONTRACTS`, `read_profile`, `list_cards`, `profile_revision`, `match_model_profile`, `select_cards`, `compose_prompt`, `cmd_prompt` |
| feedback | `cmd_feedback`, `harness_session`, `calling_harness`, `opencode_running_session`, `note_command_session`, `run_harness_sessions`, `session_usage`, `archive_session`, `collect_run_usage`, `cmd_usage`, `import_operational_feedback`, `cmd_improvements`, `advance_finding`, `finding_incidents`, `cmd_retrospective` |
| dispatch | `task_depends_on`, `read_dispatch`, `round_dispatched`, `dispatch_dependencies_unmet`, `dispatch_ownership_problems`, `policy_models`, `max_concurrency_of`, `cmd_dispatch`, `write_checkpoint`, `resumable_checkpoint`, `cmd_resume`, `cmd_switch_model`, `emit_exception` |
| amendments | `list_amendments`, `amendment_consumers`, `amendment_blocks`, `cmd_propose_amendment`, `cmd_amendment` |
| packets | `task_coverage_row`, `packet_tokens`, `cmd_review_packet` |
| metrics | `cmd_metrics`, `run_artifact_span` |
| delivery | `workflow_of`, `reopen_epoch`, `derive_events`, `reviewer_verifier_events`, `review_scope_states`, `review_readiness_events`, `unfinished_decision`, `announce_delivered`, `now_s`, `delivery_lock`, `delivery_log`, `check_registration`, `event_actionable`, `retire_event`, `ensure_pending`, `sweep_role`, `cmd_reconcile`, `cmd_inbox`, `cmd_session`, `inbox_ack`, `inbox_retry` |
| batches | `batch_path`, `read_batch`, `list_batches`, `batch_ready`, `cmd_batch` |
| health | `execution_health`, `latest_verify_text`, `list_incidents`, `plan_grace_seconds`, `cmd_health` |
| qualified delivery | `paused_flag`, `input_active_flag`, `outbox_path`, `probe_boundary`, `claim_event`, `fixed_notice`, `cmd_delivery` |
| signalling | `armed`, `ledger_for`, `delivered_keys`, `cmd_arm`, `cmd_disarm`, `events`, `cmd_watch`, `cmd_events` |
| doctor | `cmd_doctor` |
| playbooks | `references/*.md`, surfaced by `cmd_help` |

Role playbooks live in `skills/docket/references/`. `SKILL.md` routes each role to the
relevant playbook, while `docket help <role>` exposes the same installed files.

## Testing

```bash
tests/test.sh
```

`tests/test.sh` runs every test in its own process from a shared work queue (`tests/parallel.py`), half the CPUs by default and `DOCKET_TEST_JOBS` to override, and prints exactly one unittest summary so `docket suite --qualify` reads it unchanged.
`DOCKET_TEST_JOBS=1` runs the plain serial suite.
The suite is also the `verify:` command of Docket's own runs, and a waiting supervisor pays for every minute of it, so its wall time is a token cost, not only a convenience.

The Python behavioral suite uses a fresh temporary directory per test. It covers task
intent, implementor-owned discovery, atomic collision detection, scope amendments,
handoffs, report gates, model history, review, review-evidence preservation, diff
coverage honesty, non-consuming event inspection, atomic publication, retry-safe
transitions, decision-payload recovery and payload immutability, interrupted reopen
recovery at every material-write boundary with concurrent retries, leased event
derivation with claim, lease, receipt, retry, and session identity, explicit
batches with generations, execution health separate from report state, qualified
delivery with pause, probe, and fixed-format notices, complete verification
capture with rerun on every identity change, acceptance evidence tables without
prose policing, five-role
authority with verifier findings and correction budgets, deterministic routing,
legacy migration that preserves evidence, changed-evidence
refusal, re-review gating for ordinary and blocked reports, multi-root declaration and
baseline capture, frozen task-round bundles, correction deltas, bundle immutability
and integrity, self-contained baseline evidence, pinned-tree availability, serialized
submission, verification bound to the documents it described, frozen consumed-input
records, provisional dependency policy and readiness withdrawal, topology, and
signalling isolation.

Root and baseline tests use real repositories, real linked worktrees, and real nested
checkouts, because Git's own behaviour is the thing under test: an embedded repository
is reported as one untracked directory rather than as its files, and that is exactly
what used to lose a nested change. `test_a_task_baseline_reconstructs_the_dirt_it_was
_assigned_over` clones the repository, checks out the pinned commit, and applies the
captured patches, so reconstruction is proved rather than asserted.
`test_a_frozen_patch_carries_every_change_shape_against_the_task_baseline` dispatches a
task over real staged, unstaged, untracked, symlinked, and mode-changed dirt, then makes
committed, staged, unstaged, untracked, deleted, renamed, mode, symlink, and binary
changes, and reads the frozen patch and the pinned source tree back out of the
run-private object store. `DOCKET_BIN` points
the suite at another build of the CLI, which is how a change is demonstrated failing
against the previous implementation before it ships.

Failure injection is deterministic, not simulated. `fault()` aborts the process at a
named write boundary when `DOCKET_FAULT` names it, and is inert otherwise, so the
suite interrupts a real transition and then asserts what a restart actually finds.
The boundaries are `publish:<artifact>` for a torn write, `bundle:publish` for the
single rename that publishes a frozen bundle, and `transition:begin`,
`transition:decision`, `transition:report`, `transition:next-round`, and
`transition:complete` for each step of a decision and a waived-task reopen. This is a test seam and nothing else; no command sets it.

`perturb()` is its companion, for the races an abort cannot show. A window between two
adjacent reads is real but invisible from outside the process, so `DOCKET_PERTURB` holds
`point=command` lines and runs the command synchronously at that point. The only
boundary is `submit:before-freeze`, which is how the suite proves that a writer landing
between the drift recheck and the freeze never reaches the frozen contract or the frozen
consumed-input record. It is a test seam on the same terms: inert unless named, and no
command sets it.

**Add a test whenever you fix a bug.** The planner-wake bug shipped precisely because the
planner signal path had never been exercised - the suite now asserts both directions and
that an unarmed role is *not* woken.
