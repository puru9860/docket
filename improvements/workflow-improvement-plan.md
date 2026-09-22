# Docket workflow improvement plan

Status: final planning revision for implementation handoff, 2026-09-05.
This revision incorporates the agreed five-role workflow and supersedes the earlier
three-role recommendation in this document.

Execution checkpoint, 2026-09-07: milestones 1-11 are implemented and verified:
section 11.1 interrupted-reopen recovery, 11.2 leased event delivery with
session registration and reconciliation, 11.3 explicit batches with execution
health and qualified delivery, 11.4 captured verification with acceptance
mapping and adversarial obligations, 11.5 five-role lifecycle with authority
enforcement, routing, correction budgets, and legacy migration, 11.6
deterministic prompts with profiles and the improvement cycle, 11.7 dispatch
with recovery, budgets, and amendments, and 11.8 milestone, correction, and
review packets with the comparative pilot harness. The repository and its
immutable evidence are ready for independent review; final verification is
not claimed here. The requirements in sections 1-10 remain authoritative.

## 1. Recommended direction

Use five logical roles and retain the file protocol. Move routine orchestration into
deterministic commands, make notifications recoverable, and give cheap models short
guidance selected for the task they are actually doing.

Use `split` with a budget policy as the default for limited premium-model tokens:

| Participant | Responsibility | When model tokens are spent |
| --- | --- | --- |
| Planner, capable model | Compact intent contract, acceptance obligations, material plan amendments | Initial planning and decisions about intent |
| Orchestrator, cheap model | Assignments, dependencies, recovery, rule-based routing, packet assembly | Actionable coordination events |
| Implementor, cheap model | Discover, implement, produce evidence, explain remaining uncertainty | One bounded task or correction round |
| Verifier, fresh context, cheap by default | Challenge whether evidence proves acceptance; report specific defects or uncertainty | Task verification, stronger model for difficult evidence |
| Reviewer, capable model, separate from planner | Independently judge correctness, design, integration, and outcome; approve or request changes | Planned milestones, final review, qualifying technical exceptions |
| Docket CLI and delivery adapter | Dispatch bookkeeping, verification capture, routing, retries, health checks, evidence packaging | No model turns |

A cheap orchestrator is a policy choice. Five roles do not require five continuously
active agents. Planner and reviewer use separate contexts; the reviewer receives
the objective and approved contract without the planner's conversation history.
The verifier checks whether evidence holds up; the reviewer decides whether the
solution is acceptable. Deterministic test execution is infrastructure, not a
replacement for the verifier's judgment about test adequacy.

Introduce an explicit versioned workflow policy, proposed `workflow: five-role-v1`,
with separate planner and reviewer registrations. Preserve existing split/combined
runs as legacy behavior unless explicitly migrated. Do not silently reinterpret
their planner events or completion semantics. New five-role runs never give the
orchestrator substantive approval authority. A task may omit an agent verifier
only under an explicit low-risk policy; capable milestone review still applies.

The planner produces the outcome, hard constraints, observable acceptance
conditions, major risks, review milestones, and escalation boundaries once.
The orchestrator expands that contract into assignments without changing intent;
implementors discover code and verification commands. Docket generates repetitive
metadata, templates, and evidence tables. The planner does not author repository
maps or detailed implementation prompts for every task.

The principal cost metric should be premium tokens per accepted outcome, with
escaped defects as a quality constraint. Cheap implementation that requires repeated
expensive reconstruction is not economical.

## 2. Findings and confidence

Sources: `docket-improvement.mdx`, `improvement.md`,
`opencode-muse-spark-1.3.md`, and the supplied `video-transcript.md`, all beside this
plan. The architecture, README, four role playbooks, CLI, tests, and local Herdr
command help were also inspected. Existing uncommitted repository changes were
left intact.

| Finding | Evidence and implication |
| --- | --- |
| Approval and waiver destroy report bodies | Confirmed in `cmd_decide`: the report-body variable is reused for decision text and written back to the report. Approval also discards `--reason`. Fix before collecting further review history. |
| Wake delivery can be lost | Confirmed in `cmd_watch`: keys are appended before the wake banner is delivered or processed. Locking prevents competing claims, but provides no acknowledgement or recovery. |
| Role environment variables are not identity | Confirmed: a caller supplies the role. This prevents accidental mixing only when everyone follows the convention. |
| Missing Git evidence looks like no changes | Confirmed: an empty Git root produces empty change lists. `cmd_diff` reports no changes. |
| A task diff is currently a path inventory | Confirmed: `cmd_diff` prints paths and scope labels, not a patch. Snapshots retain dirty-file fingerprints, not prior content, and omit a pinned HEAD. |
| Batching works, but membership is implicit | Confirmed: readiness considers all delegated task files currently present. Planned dependent tasks can prevent readiness; assignment timing changes batch membership. |
| Model/session behavior needs adapter tests | Reported OpenCode picker and resume failures are useful observations, not universal guarantees about every installed version. |
| Better verification obligations helped Muse | The retrospective reports six later first-round approvals. Promising, but one evolving run is not a controlled comparison. |
| Layered review caught correlated mistakes | The retrospective reports three important defects missed by both implementor and orchestrator. Preserve independent final review. |

The video summary supports testing smaller prompts and enforcing transitions in
code. Its reported percentages should not be treated as predictions for Docket.
A SHA-256 digest establishes artifact identity, not that a test actually ran or
that its assertion proves the requirement.

## 3. Preserve review evidence first

### Reports and decisions

- Keep submitted report bodies unchanged through approve, waive, and changes.
  Continue recording lifecycle transitions in report frontmatter.
- Keep verdict text, reviewer identity, reason, and the submitted evidence digest
  in the separate decision artifact. Preserve a reviewer-written decision body.
- Publish individual files with temporary-file plus atomic-replace writes. For
  multi-file transitions, lock the owner, record transition identity, and make
  retries finish an interrupted transition without creating another round.
- Reject decisions against evidence that changed since review began. New edits
  require fresh verification and review, not a silent reuse of approval.
- Add an audited reopen transition for a waived task: preserve the old round and
  reason, open exactly one next round, reclaim scope subject to collisions, and
  invalidate any aggregate readiness based on the waiver. Implement this as an
  explicit extension of the current monotonic-round invariant.

### Real task and run diffs

Declare checkout roots before dispatch. Give each a stable alias, canonical
worktree path, Git common directory identity, branch, and HEAD. Two worktrees of
one repository are distinct change surfaces even if they share Git storage.

At run baseline and task assignment, retain enough content to reconstruct the
actual baseline: pinned commit identity plus initial index/worktree differences,
untracked content, deletions, executable modes, symlinks, and relevant binary
metadata. Fingerprints alone cannot reconstruct a previously dirty file. Do not
commit or stash user work to capture the baseline. Treat stored patches and logs
as local project data, including their potential to contain secrets.

Resolve scope paths to declared roots and generate root-qualified patches against
these baselines. Include edits committed during the task; current dirty status
alone misses those. Take the run baseline before any implementor edits, not when
the aggregate report is assigned. Produce the aggregate from that baseline rather
than concatenating overlapping task patches.

For code workflows, unresolved roots or unreadable snapshots must block claims of
verified diff coverage. Keep the README's Git-optional file workflow through an
explicit `evidence_mode: documents-only` choice that clearly reports diff coverage
as unavailable. Never describe unavailable evidence as an empty diff.

Initially require explicit serialization for writers with overlapping scopes.
Worktree isolation can be added later; supporting existing worktrees does not
require building an automatic merge system now.

Freeze a submission bundle at every task round: contract revision, task baseline,
root-qualified full patch, resulting source identity, report body, and captured
verification. Retain a correction delta against the previous submission as well
as the full baseline-relative patch. Later workspace edits must not rewrite earlier
review evidence. Verifier findings and reviewer decisions reference the exact
bundle digest. An aggregate bundle pins its constituent submissions and integrated
source revision. Reopening a constituent invalidates dependent readiness.

Keep accepted scope ownership through verification and the applicable review
boundary. A dependency may consume verified-but-unapproved work only when the run
policy explicitly permits provisional integration; it must be reverified if that
input changes. Never equate dependency readiness with final approval.

## 4. Reliable notifications without disrupting conversation

### Separate events from delivery

Use durable report state to determine what needs attention. Herdr or a native
harness hook is a delivery adapter, not the source of completion truth.

The desired sequence is:

```text
validated report publication
    -> derive pending verifier event for that submission revision
    -> claim with a bounded lease
    -> queue for the registered session's safe delivery boundary
    -> recipient accepts the event and starts handling
    -> durable verification finding / decision / resolution retires the event
```

Aim for at-least-once delivery with idempotent handling. Do not promise exactly-once
delivery across process crashes. Keep the append-only per-role ledger, extending
it to record claims, attempts, receipts, lease expiry, and completion. These are
transport records; task lifecycle remains in document frontmatter. Reconstruct
actionable events by reconciling documents even if a delivery process died between
report publication and ledger update.

Proposed interfaces, not existing commands:

```text
docket events RUN --role orchestrator --peek
docket inbox RUN --role orchestrator --claim
docket events RUN --ack EVENT --session SESSION
docket events RUN --retry EVENT --reason TEXT
docket reconcile RUN
```

Define acknowledgement as receipt, not completion. A recipient that crashes after
acknowledging must not strand the task: expire its handling lease and redeliver if
the underlying event is still actionable. Decisions and other durable resolutions
retire events automatically. Recheck current round and digest before acting on a
delayed event. Duplicate delivery must not duplicate a decision or dispatch.

Peek, status, and doctor must never claim or migrate delivery state as a side
effect. Bind claims to a registration containing workspace, run, role, session ID,
and session generation. Reject stale registrations and wrong-role claims. This
prevents accidental theft; it is not a security boundary against another process
with the same filesystem permissions.

### Native hooks and Herdr

Keep the current foreground hook invariant for native hook delivery. Do not detach
the watcher from its owning harness. Hook termination leaves an unhandled event
pending, and the next hook or reconciliation pass can recover it.

For Herdr, add an optional delivery service owned by a real process supervisor,
with restart policy, heartbeat, logs, and an explicit workspace/session registry.
It must not be another shell job that a model starts with `&`. A stopped service
must be visible in `doctor`, and restart must reconcile documents before sending.
When no supported supervisor is available, report the bounded/manual delivery
mode honestly; do not claim unattended wake reliability.

Use Herdr only through a capability-tested adapter. The installed
`herdr agent prompt --help` says it does not track turns; waiting can match the
completion of an already active turn. It does not establish a safe idle queue or
an atomic send-if-idle operation. Reading `idle` and then sending has a race.

Therefore:

- Queue ordinary notifications while the recipient is working or paused.
- Deliver through a verified turn-boundary/queue mechanism where available.
- If the integration cannot avoid racing user input, keep the event queued and
  expose it through a native hook or explicit inbox pickup. Automatic delivery
  should not type into an uncertain input buffer.
- Provide a `pause-delivery`/`resume-delivery` control with visible queued count.
  Pausing delivery does not pause implementation or consume events.
- Send a small fixed-format notice with run, role, event ID, and a pointer to the
  inbox. Never interpolate implementor report prose into executable instructions.
- Revalidate the registered target before every attempt. A renamed agent, reused
  pane, or restarted session must not inherit an old delivery accidentally.

This lets the user talk to the planner or orchestrator during implementation.
The report remains pending until a safe opportunity to process it. An implementor
can trigger reconciliation after submission, but should not compose and directly
send arbitrary supervisor prompts. Transport failure must not roll back a valid
report submission.

### Explicit review batches and stall handling

Assign a batch ID and explicit dependency list to each task. Close batch membership
before dispatch. Future tasks waiting on dependencies must not block the current
batch, and a task added later must not silently change an emitted event.

Route submissions to verifiers promptly; batch capable-review readiness at explicit
milestones. Blockers, collisions, and recovery needs remain immediate for the
orchestrator. Planner routing is limited to intent/constraint amendments; reviewer
routing covers milestone review and technical exceptions. Event identities include
workflow version, destination role, artifact revision, and round
or batch generation; the current fixed `all:decided` identity is insufficient after
reopen or later batches.

Track execution health separately from report state. Show, for example,
`report=draft, execution=running`, `execution=idle-unsubmitted`, or
`execution=unknown`. Agent `done` is never task approval.

Prefer structured process/provider signals. Terminal rate-limit strings can be
best-effort hints, not authoritative completion state. Use configurable grace
periods and one deduplicated recovery event per incident. Long verification jobs
and lack of file changes are not sufficient evidence of a stall.

## 5. Collect model experience without growing a giant skill

Separate raw observations from approved prompting guidance:

```text
skills/docket/references/model-profiles/<profile>.md  concise, reviewed guidance
.docket/runs/<run>/feedback/<observation>.mdx         run-specific evidence
.docket/improvements/<finding>.mdx                   cross-run improvement backlog
project-local eval results                          comparison measurements
```

Use flat frontmatter for each observation: model ID, provider, harness and version,
effort, task type, run/round reference, profile version, failure category, evidence
path, intervention, outcome, and confidence. Keep details in the body. Distinguish
model behavior from provider limits, harness bugs, ambiguous task instructions,
and reviewer mistakes.

Seed the corpus from the Muse retrospective, preserving both strengths and
failures. Mark it as one-run evidence rather than universal model behavior. Avoid
loading the raw retrospective into every implementor. Do not automatically promote
a model's own explanation into a permanent rule.

At dispatch, assemble a bounded prompt from:

1. The short common implementor contract.
2. The task, acceptance IDs, hard constraints, and explicitly labelled starting hints.
3. A few applicable model/task gotchas.
4. Pointers to scope, latest decision, ready handoff, and evidence.

Apply the same composition to all five roles: role contract, task/decision
contract, selected guidance, and resume pointers. Remove identity theatre such as
"you are Opus 5 implementor". Model/provider/effort belong in execution metadata;
instructions specify ownership, inputs, permitted actions, required evidence, and
stop/escalation conditions. Docket renders these prompts deterministically rather
than asking the capable planner to rewrite them each dispatch.

Example implementor core, followed only by applicable guidance and artifact paths:

> Implement the assigned task. Read its contract and discover relevant code before
> choosing an approach. Submit scope before editing. Demonstrate acceptance using
> observable behavior and independently derived expected values. State unverified
> conditions explicitly. Submit through Docket; on a blocker identify the precise
> decision or capability needed. Preserve a handoff before interruption if possible.

The orchestrator prompt enumerates routing rules and prohibits inventing technical
fixes, modifying acceptance, approving disputed work, or waiving requirements. The
verifier prompt requests adversarial evidence checks, not stylistic redesign. The
reviewer prompt permits challenging the plan against the original objective.

Start with an approximately 300-600 token budget for model-specific guidance,
then measure. Prefer task relevance over filling the budget. Unknown models get
the common contract. Record the selected profile revision and rendered prompt
digest so the effect can be evaluated later.

Initial Muse guidance should emphasize:

- Compare output actually produced by the system; never supply the asserted output
  from a fixture and present that as a system comparison.
- For a negative test, establish the triggering condition and show that removing
  the relevant behavior makes the test fail. Use an isolated copy for mutations.
- For configuration-dependent claims, discover and cite the actual relevant
  configuration. The planner need not build a repository map in advance.
- Derive changed fixture/oracle values from an independent source; report an
  unavailable derivation as a gap.
- Reuse existing policy after targeted discovery. Distinguish hard constraints
  from defaults that repository evidence may challenge.
- Put limitations near the top, keep the report short, and state unverified claims
  explicitly. Attempt a handoff before capacity exhaustion when possible.

Use specialized cards for fixture changes, concurrency, deployment configuration,
and harness/input plumbing. Do not inject every card into every task. If a rule
helps all models, promote it into the common task/evidence contract. If it is
mechanically enforceable, implement it in the CLI and remove repetitive prose.

Resolve conflicting session advice with policy: fresh context at task boundaries
and, initially, correction rounds; mid-round in-place switching only when a
verified adapter supports it. Otherwise use a checkpoint and a new session.
Abrupt crashes require mechanical recovery even when no ready handoff exists.

### Embedded feedback collection

Feedback is part of the workflow, not an orchestrator-only retrospective. Each
role may record a short observation at its natural handoff or decision boundary:

| Contributor | Useful observations | Collection point |
| --- | --- | --- |
| Implementor | Ambiguous instructions, discovery friction, verification obstacles, recovery needs | Submission, blocked report, or handoff |
| Verifier | Hollow evidence, ineffective tests, repeated defects, interventions that helped | Verification finding |
| Reviewer | Escaped defects, incorrect assumptions, missing integration checks, unnecessary review effort | Review decision |
| Orchestrator | Dispatch failures, stalls, routing problems, repeated correction cycles | Incident resolution and run completion |
| Docket | Gate failures, delivery attempts/incidents, retries, model transitions, timings and available usage | Automatically at the corresponding operation |

Record an observation only when useful. Do not require a "no feedback" artifact,
another long report, or feedback as a precondition for blocking, handoff, approval,
or completion. A failure to record optional feedback must not roll back a valid
lifecycle operation. Existing durable operational records can be reconciled into
feedback after a crash. Preserve factual machine observations separately from an
agent's interpretation of their cause.

Each observation has a stable ID and flat metadata: author role, category,
run/task/round or incident reference, model/provider/harness versions, prompt and
profile revisions, evidence paths, confidence, and collection time. Its short body
states observed behavior, impact, intervention, outcome, and candidate improvement.
Use existing evidence links rather than copying logs or conversations. Include
successful interventions as well as failures; unknown causes stay unknown.

For example: a verifier records that T03's parity assertion supplied its own actual
value, links the submission and numbered finding, records the independent-output
obligation used in the correction, and links the passing resubmission. This is an
observation about that incident, not proof that the model always behaves that way.

### Discover and prioritize improvements

Proposed interfaces, to finalize with the CLI implementation:

```text
docket feedback RUN                         # list run observations
docket feedback RUN --add                   # scaffold a short observation
docket improvements                        # list cross-run findings
docket improvements --category signalling  # filter findings and evidence
docket retrospective RUN                    # build a compact run summary
```

The backlog view should show category, affected role/model/harness, severity,
independent incident and run counts, known impact, latest occurrence, status,
candidate intervention, and supporting evidence. Support filtering by model,
role, status, and run as well as category. Group related observations without
discarding their provenance. Multiple roles describing the same task incident
count as one incident, not independent confirmations. Uncertain grouping remains
reviewable rather than silently merging unrelated failures.

Retrospective output summarizes corrections, escaped defects, manual reminders,
delivery/recovery failures, successful interventions, and premium-token usage
where available. Docket generates mechanical summaries without model calls. An
optional cheap pass may organize narrative findings; it is not a sixth permanent
agent and must not wake the planner or reviewer just to write a retrospective.
Raw observations are excluded from ordinary implementation prompts.

### Promote evidence into tested changes

Use backlog status `observed -> proposed -> trial -> adopted` with `rejected` for
unsupported or ineffective proposals. Record these states in flat frontmatter of
the finding document, separate from task lifecycle. Every proposal links incidents,
its expected benefit, the intended change, and a verification/evaluation case.
Every adoption links the actual change revision and trial results. Rejected
proposals retain their rationale so later runs do not repeatedly rediscover them.

| Finding type | Improvement destination |
| --- | --- |
| Lifecycle, diff, dispatch, or delivery defect | CLI fix plus regression test |
| Generally unclear or repetitive instructions | Common role/task prompt contract |
| Evidence-supported model-specific behavior | Short, selectively loaded profile card |
| Escaped correctness defect | Verifier/reviewer obligation and evaluation fixture |
| Ineffective guidance or needless review overhead | Remove/narrow the rule and compare outcomes |

Feedback never edits skill instructions, gates, permissions, or model policy
automatically. Adoption happens through an explicitly authorized improvement task,
with proportional review and tests. Substantive judgment can use a capable model
at that point; routine observation collection stays cheap. Project-local findings
remain local unless explicitly promoted to the shared skill. Record scope and
version on promoted rules, and retire or revise adopted guidance if later evidence
shows regression. This creates a discoverable improvement cycle without loading
an ever-growing history into future agents.

## 6. Evidence that reduces review work

Have Docket execute and capture the registered verification command, cwd, start/end
time, exit code, timeout, stdout/stderr artifacts, and source snapshot digest.
Recognize framework counts/skips only with a supported parser; otherwise report
them as unknown. Record model identity as observed, requested, or unknown, rather
than presenting a requested launch flag as observation.

Preserve structural checks before verification. If explicit prior verification
evidence is reused, require matching command, roots, source content, and declared
environment inputs; otherwise rerun. Recheck content after verification so an
edit during a test run cannot silently inherit the green result.

Give acceptance criteria stable IDs and an evidence table with `met`, `partial`,
`not-met`, or `not-verified`, linked artifacts, and named gaps. Normal submission
requires all required criteria to be met. Partial work uses the existing honest
blocked path or an approved amendment, never a weaker success status.

Mechanically reject missing artifacts, stale evidence, absent criteria, duplicate
IDs, explicit gaps attached to a met criterion, and missing required sections.
Do not attempt to decide arbitrary prose contradictions with a keyword gate.
"No limitations" and "not covered by this unit test but covered by integration"
are counterexamples. Free-text contradiction detection belongs in advisory review;
structured contradictions can be hard failures.

Require stronger evidence for the failure patterns actually observed:

| Risk | Required verification obligation |
| --- | --- |
| Runner ignores its input | Two materially different inputs produce independently expected differences; capture the selected input identity. |
| Circular parity assertion | Separate actual-output provenance from expected-value derivation. |
| Fingerprint hashes filenames/status only | Change bytes without changing the status shape; the fingerprint must change. |
| Accidental one-to-one mapping | Exercise zero, one, and multiple callbacks/events per aggregation boundary. |
| Changed oracle | Per-value independent derivation and explicit review of expectation changes. |
| Claimed offline behavior | A controlled network-disabled execution or explicit unverified gap; logs alone do not establish absence. |

Hashes support identity and stale-evidence detection, not semantic proof. Read
restrictions likewise require harness/OS enforcement: a Docket scope document
cannot prevent arbitrary shell reads. Express desired discovery boundaries and
report whether the selected harness actually enforces them.

Keep blocked submission lightweight. Missing dependencies, broken verification,
and provider loss must remain reportable without satisfying completion evidence.

## 7. Dispatch, recovery, and model budgets

Add first-class dispatch and resume only after the event and evidence foundations:

```text
docket dispatch RUN TASK
docket resume RUN TASK
docket switch-model RUN TASK --model MODEL
docket propose-amendment RUN TASK
docket review-packet RUN --role reviewer
```

Use run/task/round/session identity in registrations, with human-readable agent
names. A dispatch retry must not create a second writer. Enforce dependency
readiness, active ownership, and provider concurrency before starting work.

Store primary/fallback model choices, concurrency, escalation rules, and budget
thresholds in flat configuration fields or separate flat policy documents. Do not
copy the nested YAML sketch from the feedback into Docket's parser.

Fallback within an already approved policy is routine orchestration. Exhausted
allowed choices, a higher spending tier, or changed scope produces one compact
exception. Reserve some premium budget for final review and late-run recovery;
do not spend it all on early task supervision. Record unavailable token/cost
telemetry as unknown, using invocation counts only as an explicitly labelled proxy.

Automate mechanical checkpoints containing current diff identity, last verification,
session/model history, and task/decision pointers. A killed process cannot explain
its unfinished reasoning, so do not mark an automatic snapshot as a ready semantic
handoff. A replacement performs targeted discovery and supplies that reasoning.

Task sizing should initially warn, not reject on guessed file counts. Start with
one coherent behavior per task and roughly 3-6 acceptance obligations. Reassess
after implementor discovery reveals cross-system scope or verification breadth.
Hard budgets may be configured explicitly after data supports the thresholds.

## 8. Review and escalation policy

### Normal flow and authority

```text
User objective -> Planner compact contract -> Orchestrator assignments
    -> Implementor discovery/implementation -> Docket mechanical gate
    -> Fresh verifier -> Orchestrator milestone assembly -> Capable reviewer
    -> Approved milestone / final outcome
```

Mechanical failures return exact diagnostics to the implementor without a premium
turn. A verifier passes the exact submission, files numbered defects, or records
uncertainty. A pass means ready for review, never approved. Concrete defects return
to the implementor within the correction budget. The orchestrator transports
findings unchanged and chooses actions from explicit state/routing rules. If that
choice requires understanding disputed implementation details, escalate it.

Introduce separate numbered verification artifacts, proposed
`T03-verification-02.mdx`, with flat metadata identifying task submission round,
attempt, verifier session, contract revision, source digest, and result. Separate
review stage from report status: retain `submitted` while verification/review is
pending; record verification result in its own document. Only CLI transitions may
create a correction round. A verifier failure may trigger that transition under
configured policy, recording provenance without impersonating reviewer approval.
Assign distinct attempt IDs when a verifier retries the same submission.

The implementation must define a transition table before changing lifecycle code:
report publication, verification pass/fail/uncertain, local correction, milestone
review, approval, waiver, amendment, reopen, and stale-evidence invalidation.
Require role and artifact revision on each operation. Verifiers cannot approve;
orchestrators cannot waive or alter acceptance; reviewers own approval. Changes
to intended behavior require planner amendment and user input when outside already
authorized scope. An approval packet identifies exactly which task revisions it
approves; aggregate approval is not inferred from verifier passes.

The premium reviewer receives a generated aggregate packet: objective coverage,
root-qualified aggregate diff, integration evidence, waivers, unresolved risks,
and review findings. Keep the main packet around 1,000-2,000 tokens initially,
with links to complete artifacts; preserve risks when shortening it. Do not dump
task chatter or require manual reconstruction.

Allow the final reviewer to inspect relevant source and evidence when needed.
Protecting its context must not prohibit the independent checks that caught the
three reported integration defects. Routine evidence checks belong to the verifier.
Cheap verifiers can share implementor blind spots: route concurrency, security,
oracle changes, and harness/input plumbing to stronger verification or early focused
review according to risk. Do not make a cheap verifier pass a correctness guarantee.

### Exception routing

| Discovery or event | Next action | Who decides |
| --- | --- | --- |
| Additional file or better implementation approach within contract | Amend scope; collision check; explain approach | Implementor and deterministic scope gate |
| Overlapping work or scheduling conflict | Serialize/reassign within approved intent | Orchestrator |
| Concrete test/verification defect | Forward exact diagnostics or numbered findings | Implementor corrects; verifier rechecks |
| Rate limit or crash | Resume/checkpoint recovery or approved fallback | Orchestrator within policy |
| Requirement contradicts repository behavior | Compact amendment request with evidence and proposed alternative | Planner; user if scope requires it |
| Dependency invalidates intended plan | Adjust schedule if sufficient; otherwise request amendment | Orchestrator or planner respectively |
| Implementor disputes verifier's technical finding | Preserve both claims and evidence; stop argument loop | Reviewer or stronger verifier |
| Local correction budget exhausted | Compact diagnostic packet, no automatic waiver | Stronger verifier/reviewer |
| Reviewer finds implementation defect | Numbered corrections and fresh verification | Implementor, then reviewer |
| Reviewer finds plan insufficient | Explain objective/contract gap | Planner amendment |
| Proposed waiver or spending outside policy | Explicit decision packet | Reviewer for waiver, authorized budget owner/user for spending |

Planner requests contain only: decision needed, conflicting constraints, evidence
pointers, recommended alternative, and impact on acceptance/dependencies. Preserve
old and new contract revisions. Invalidate affected evidence and events, pause
affected tasks, and continue unaffected tasks. The planner is not expected to
reconstruct terminal history or troubleshoot ordinary test failures.

### Bound expensive review cycles

Start with two local correction attempts after initial submission, configurable
per run. Count gate-repair attempts as well as verifier returns so repeated
mechanical failures cannot create an unbounded loop. Exhaustion produces one
durable escalation; it never auto-approves, auto-waives, or opens endless rounds.

Review at planner-selected integration milestones and final completion, with early
focused review for risky foundations. Avoid both premium review per tiny task and
deferring foundational uncertainty until the entire run is implemented. On review
corrections, deliver findings addressed, the delta from the reviewed bundle, and
refreshed evidence. Preserve access to the full diff; expand review when corrections
affect other work. Record premium tokens for initial planning and every subsequent
review/amendment round, not merely first-round approval rate.

## 9. Implementation sequence and acceptance tests

Each row is a milestone to divide into narrow implementation tasks. Do not hand
the entire plan to one cheap implementor.

| Order | Deliverable | Acceptance / regression proof |
| --- | --- | --- |
| 1 | Preserve report bodies and decision reasons | Approve, waive, and changes retain report bodies; reasons survive; repeated transitions cannot overwrite evidence. Demonstrate the body-preservation regression fails before the fix. |
| 2 | Honest diff diagnostics and non-consuming event inspection | Missing Git root never reports an empty diff; explicit documents-only mode is labelled; repeated peek leaves ledgers and delivery unchanged. |
| 3 | Atomic publication and retry-safe transitions | Interrupt writes and each decision transition step; restart sees complete artifacts and exactly one next round. |
| 4 | Multi-root baselines and frozen task/round/aggregate patches | Nested repositories, worktrees, committed changes, initial dirt, deletions, renames, symlinks, binaries, and untracked content are correct; later edits preserve old bundles; correction delta is reproducible. |
| 5 | Leased event delivery and reconciliation | Crash before send, after send, after receipt, and before resolution; work recovers. Competing consumers cannot own one lease; stale sessions fail; duplicate handling is harmless. |
| 6 | Explicit batches, execution health, and delivery qualification | Dependencies do not deadlock readiness; reopen creates new events; one stall yields one incident. Kill/restart hooks, service, recipient; reuse panes and simulate user input. Unsafe delivery remains queued. |
| 7 | Captured verification and acceptance mapping | Missing/stale artifacts and explicit contradictions fail; code edits invalidate results; blocked stays usable; adversarial obligations expose ignored inputs and circular oracles. |
| 8 | Five-role transition table, artifacts, routing, legacy migration | Only reviewer approves; verifier pass cannot complete a task; planner receives amendments, reviewer receives milestones; local retry limit holds; stale events cannot act on revised contracts. Legacy runs preserve semantics. |
| 9 | Deterministic prompts, profiles, and embedded improvement cycle | Role duties and stop conditions are present; model-persona filler absent; relevant cards only; prompts bounded/reproducible. Every role can record evidence-linked feedback; duplicate incident counts stay correct; backlog filters and retrospective work; no-feedback and feedback-write failure cannot block handoff/completion; adoption requires linked trial/change evidence and never occurs automatically. |
| 10 | Dispatch/resume, budgets, and amendments | One writer after retry; fallback obeys limits; actual model may remain unknown; recovery works without handoff; affected work is invalidated after amendment and unaffected work continues. |
| 11 | Milestone/correction packets and comparative pilot | Planner writes a compact contract; reviewer has fresh context and pinned aggregate/delta evidence; no routine premium wakes; measure all planning/review tokens and missed defects. |

Dependencies: 4-5 build on 2-3 and can be developed independently; 6 builds on 5;
7 builds on 4; 8 requires 4-7 before activation; 9 can be drafted early but is wired
to the role contracts in 8; 10 requires 6-9; 11 integrates the workflow. First prove
trustworthy diffs and handoffs, then enable the additional roles. Drafting profiles and
constructing evaluation fixtures can proceed independently of delivery work.

For every behavior change, add meaningful assertions and run `tests/test.sh` with
zero failures. For fixes, demonstrate the relevant test fails against the prior
behavior and passes after. Test adapters with deterministic fake processes first,
then a real disposable harness session. Unit tests cannot prove real terminal
delivery behavior.

Update architecture invariants and playbooks alongside each shipped change. Sync
the installed skill only after the relevant checks pass, following repository
instructions and filesystem permissions; verify the two copies match. This plan
does not modify or install the skill.

## 10. Pilot and success measures

Build a small reusable task set from the observed failures: ignored dataset input,
tautological parity, changed fixture oracle, negative test missing its trigger,
configuration assumptions, long report truncation, provider exit without handoff,
and multi-root diff loss. Include straightforward tasks to measure unnecessary
overhead and false rejection.

Compare the same model/harness configurations using the current prompt, the short
common contract, and common contract plus relevant profile cards. Pin versions
where possible, repeat tasks, and use review criteria held constant across arms.
Do not attribute improvement to a prompt if model, task difficulty, and reviewer
instructions changed at the same time.

Track first-round approval, final correctness, seeded defects missed, review rounds,
premium tokens, total model cost, wall time, prompt size, false blocks, manual
reminders, and notifications delayed or duplicated. Record provider outages
separately from model failures.

Initial release conditions:

- All lifecycle/evidence regressions and delivery failure-injection cases pass.
- No healthy-work polling turns are required from a model.
- No lost actionable event in the crash/restart suite; duplicates are harmless.
- No notification is injected into an unqualified active input surface.
- Verifier pass cannot grant approval; planner and reviewer use separate contexts.
- Routine failures never wake a capable model before policy requires escalation.
- Every decision references frozen code/evidence; later edits cannot inherit it.
- Run feedback is discoverable across runs, deduplicated by incident, and excluded
  from prompts until an authorized, evaluated promotion selects guidance.
- Retrospective collection introduces no mandatory premium-model invocation and
  never blocks the primary workflow.
- The pilot reduces premium review effort without worsening observed correctness.

Set a numeric cost target only after baseline measurement. Keep profile rules that
demonstrably help, remove ineffective ones, and retain independent final review
even if first-round approval improves.

The first implementation slice should be milestones 1-2: preserve the evidence,
make missing diffs explicit, and allow event inspection without consuming it.
Then deliver frozen diffs and notification recovery before activating five-role
routing or dispatch automation. Keep the CLI stdlib-only, preserve flat lifecycle
frontmatter, and keep detailed new role playbooks in `references/` with `SKILL.md`
as a thin router. The implementation handoff is this document; no implementation,
agent dispatch, or installation is authorized by the planning update itself.

## 11. Detailed remaining execution plan

This section expands milestones 4-11 into bounded implementation and review tasks. It
does not add a second source of product requirements. When a detail here is ambiguous,
sections 1-10 control. Files elsewhere under `improvements/` are not implementation
requirements.

### 11.0 Handoff and execution rules

The receiving agent owns orchestration of the remaining work. Use Opus 5 at high effort
as orchestrator and OpenCode as implementor. Give each implementation task and each
correction round a fresh OpenCode context. Do not give milestones 4-11 to one cheap
implementor in a single prompt. A task should normally contain one coherent behavior
and roughly 3-6 observable acceptance obligations.

Use this sequence for every task:

1. The orchestrator derives a narrow task contract from this document. It records the
   exact section and acceptance row being implemented and does not modify intent.
2. The implementor performs targeted discovery, declares scope, and records the real
   verification command before editing.
3. The implementor adds meaningful assertions. For a defect, preserve a demonstration
   that fails against the prior behavior and passes after the repair. Mutation tests or
   a prior binary should be used when a green test could be tautological.
4. Run the narrow tests while iterating, then `tests/test.sh` with zero failures. Run
   repository-required static and documentation checks after the behavior is stable.
5. Update architecture invariants and the applicable role playbooks in the same task.
   Keep the CLI stdlib-only, lifecycle frontmatter flat, and `SKILL.md` a thin router.
6. Freeze the exact task evidence and review that bundle, not the later live workspace.
   The orchestrator returns numbered, independently actionable defects or accepts the
   task. Use at most two local correction attempts after the initial submission unless
   the run policy explicitly configures another limit.
7. Do not start the next milestone until the current milestone's task bundles,
   integration checks, and aggregate review are accepted. A real blocker produces a
   durable blocked report or escalation packet; it never produces a waiver or weaker
   success claim automatically.

Preserve all existing user work and immutable evidence. Do not recapture a baseline to
make a diff cleaner, and do not commit or stash user work to capture a baseline. Do not
rewrite approved reports, decisions, frozen bundles, or transition history. Continue to
use private Git indexes and object storage for reconstruction.

The shared Docket installation is currently absent. Use the repository CLI explicitly,
for example `python3 skills/docket/bin/docket`, until installation is separately
authorized. The instruction to synchronize an installed copy applies only when such a
copy exists or installation has been authorized. This planning handoff does not itself
authorize installation.

At each milestone boundary, leave a compact checkpoint containing:

- accepted task and bundle digests;
- the aggregate or milestone bundle digest;
- verification commands and captured outcomes;
- unresolved risks, waivers, and decisions needed;
- correction count and model/session history;
- the next milestone's satisfied prerequisites.

Do not rely on Docket's own `intact`, `in scope`, or status labels as the only proof of
Docket behavior. Independently inspect artifacts and use adversarial or mutation tests
for the property being claimed. The final reviewer must be able to reproduce evidence
without reconstructing terminal conversation.

### 11.1 Milestone 4 closure: interrupted reopen recovery

Milestone 4 has one known blocking defect. A waived task can begin `--reopen`, create its
next-round draft, and crash at the `transition:next-round` fault boundary. A retry then
selects the new draft as the latest report and refuses because it is not waived, leaving
the reopen journal permanently in progress. The existing suite covers aggregate freeze
and decision retry safety but does not cover this reopen boundary.

Implement this as a corrective milestone-4 task without rewriting the approved T43
artifacts or bundles.

Required behavior:

- An in-progress reopen journal is detected before selecting the latest report.
- Recovery uses the journal's original owner, waived round, report, decision, evidence
  digest, and recorded reason. A retry may omit the reason, but cannot replace it.
- A retry after the next-round file exists completes the same transition and does not
  open another round.
- The old waived report, decision body, waiver reason, and bundle remain byte-for-byte
  unchanged.
- Scope is reclaimed only after collision validation. Collision refusal happens before
  partial lifecycle mutation.
- Completion still invalidates every aggregate that pinned the waiver and names the
  task and aggregate in status/events.
- Concurrent reopen attempts serialize under the owner lock and settle as one reopen.

Regression proof:

- Inject failure at `transition:begin`, publication of the next report,
  `transition:next-round`, and publication of the completed journal.
- Restart through a new process and repeat `--reopen` with no free-text reconstruction.
- Assert one new report round, one completed transition identity, unchanged prior
  artifacts, reclaimed scope, and stale aggregate readiness.
- Repeat the test with two concurrent retry processes and assert that no third round or
  second transition appears.
- Demonstrate that the `transition:next-round` test fails against the current binary
  with the refusal that the new draft is not waived, then passes after the repair.

Milestone-4 exit gate:

- All prior baseline, task-bundle, correction-delta, aggregate, documents-only,
  staleness, and tamper tests remain green.
- Every reopen material-write boundary recovers in a new process.
- `tests/test.sh` has zero failures and the correction receives independent review.
- A new aggregate or correction packet identifies the repaired revision without
  changing the earlier approved evidence.

### 11.2 Milestone 5: leased event delivery and reconciliation

Prerequisites: milestones 2-3 and the milestone-4 correction are accepted. Build the
durable event foundation before implementing unattended Herdr delivery or five-role
routing.

#### Task M5.1: deterministic actionable-event derivation

Implement a deterministic projection from durable lifecycle documents to actionable
events. An event identity includes the workflow version, destination role, artifact
revision, owner, round, and the relevant round or batch generation. Reconciliation must
recreate a missing pending event after a process dies between valid report publication
and ledger update.

Required interfaces and behavior:

- `docket events RUN --role ROLE --peek` remains read-only.
- `docket reconcile RUN` derives missing events and retires events whose durable
  resolution already exists.
- Reconciliation is idempotent. Repeated runs do not duplicate the logical event.
- A delayed event is actionable only when its round, artifact digest, destination, and
  generation still match current durable state.
- Event transport records stay outside lifecycle frontmatter.

Proof:

- Kill immediately before and after event-ledger publication, then reconcile.
- Delete only the derived pending record while preserving the submitted report and show
  that reconciliation restores it.
- Run reconcile repeatedly and concurrently and assert one logical event identity.
- Prove peek, status, and doctor leave event and lease files byte-for-byte unchanged.

#### Task M5.2: append-only claim, lease, receipt, retry, and completion records

Extend the per-role ledger to record claims, attempts, receipts, lease expiry, retries,
and completion while preserving its append-only nature. Implement the proposed inbox,
acknowledgement, and retry interfaces.

Required behavior:

- `docket inbox RUN --role ROLE --claim` grants one bounded lease to one eligible
  consumer.
- `docket events RUN --ack EVENT --session SESSION` records receipt, not task
  completion.
- `docket events RUN --retry EVENT --reason TEXT` records why another attempt is due.
- Lease expiry makes an unresolved actionable event claimable again.
- A durable task decision, verification resolution, or other applicable resolution
  retires its event automatically.
- Duplicate receipt, retry, delivery, or handling is idempotent and cannot create a
  second decision or dispatch.
- The implementation promises at-least-once delivery, not exactly-once delivery.

Proof:

- Crash before send, after send, after receipt, and before resolution. In every case the
  durable event eventually becomes claimable or retired according to current state.
- Race at least two consumers and prove only one owns the active lease.
- Expire a lease using deterministic test time and prove safe redelivery.
- Apply duplicate acks, retries, and resolutions and prove lifecycle artifacts remain
  singular.

#### Task M5.3: session registration and claim identity

Add explicit registrations containing workspace, run, role, session ID, session
generation, and a human-readable name. Bind claims and receipts to this registration.

Required behavior:

- A stale generation, wrong role, wrong run/workspace, renamed target, reused pane, or
  restarted session cannot inherit or acknowledge an old claim.
- Re-registration advances generation without rewriting prior transport history.
- Revalidation occurs before every delivery attempt and acknowledgement.
- The registration is an accidental-misrouting guard, not a security boundary against
  another process with the same filesystem permissions.

Proof:

- Exercise every mismatched identity dimension independently.
- Restart and reuse a session identifier with a new generation; the old lease must fail.
- Race registration replacement with claim/ack and prove no event is lost or resolved by
  the stale registration.

#### Task M5.4: milestone-5 recovery matrix

Create one integrated failure-injection matrix spanning publication, derivation, claim,
send intent, receipt, lease expiry, resolution, and reconciliation. Use deterministic
fake processes and clocks here. Real terminal delivery belongs to milestone 6.

Milestone-5 exit gate:

- No actionable event is lost in the crash/restart matrix.
- Competing consumers cannot hold one lease simultaneously.
- Stale registrations cannot claim, acknowledge, or resolve work.
- Duplicate handling is harmless and event resolution follows durable lifecycle state.
- Read-only inspection commands have no delivery side effects.

### 11.3 Milestone 6: explicit batches, execution health, and qualified delivery

Prerequisite: milestone 5 is accepted. This milestone turns durable events into safe,
observable delivery and removes implicit batch membership.

#### Task M6.1: explicit review batches and generations

Add a stable batch ID, explicit dependency list, membership state, and generation. Batch
membership closes before dispatch. A later task cannot silently join an emitted batch or
change an existing readiness event.

Required behavior:

- Future tasks waiting on dependencies do not block the current closed batch.
- Batch readiness uses only closed membership and explicit dependencies.
- Submissions route promptly to verifiers; capable-review readiness is emitted only at
  configured integration milestones.
- Blockers, scope collisions, and recovery events remain immediate for the orchestrator.
- Reopen creates a new event generation and invalidates readiness derived from the old
  waiver or batch generation.

Proof:

- Add a task before and after batch closure and compare event identities.
- Create dependency chains outside the closed batch and prove they do not deadlock it.
- Reopen a constituent and prove exactly one new actionable generation appears while the
  old event becomes stale and cannot act.

#### Task M6.2: execution health separate from report state

Represent execution state independently from report lifecycle, including at least
running, idle-unsubmitted, unknown, and the applicable stopped/recovery conditions.
Agent `done` never means task approval.

Required behavior:

- Prefer structured process/provider signals.
- Terminal rate-limit text remains a best-effort hint, never authoritative state.
- Configurable grace periods prevent long verification or an unchanged worktree from
  being called stalled by themselves.
- One incident yields one deduplicated recovery event until it is resolved or its
  generation changes.

Proof:

- Simulate long tests, quiet healthy work, rate-limit text without process failure, real
  process exit, and unknown provider state.
- Re-run health evaluation repeatedly and prove one incident count/event.
- Show status as separate report and execution fields.

#### Task M6.3: native-hook and Herdr delivery adapters

Keep native hooks foreground-owned. Add an optional Herdr service only through a real
process supervisor with restart policy, heartbeat, logs, and an explicit session
registry. Never implement it as a model-launched background shell job.

Required behavior:

- Native-hook termination leaves the event pending for the next hook or reconciliation.
- Service restart reconciles documents before attempting delivery.
- Doctor reports stopped service state and the honest bounded/manual mode when no
  supported supervisor exists.
- Delivery queues while a recipient is working or paused.
- `pause-delivery` and `resume-delivery` expose queued count; pausing neither consumes
  events nor pauses implementation.
- Notices use a fixed format containing only run, role, event ID, and inbox pointer.
  Report prose is never interpolated into executable instructions.
- Revalidate the session registration before every attempt.

#### Task M6.4: safe-boundary capability qualification

Build a capability-tested Herdr adapter. Do not infer safe delivery from an idle read
followed by send, because that sequence races user input. If the installed integration
cannot provide a verified queue or turn boundary, leave the event queued for explicit
inbox pickup or a native hook.

Proof, first with deterministic fakes and then a disposable real harness session:

- Kill and restart the hook, service, and recipient at each delivery boundary.
- Reuse and rename panes; restart a session with a new generation.
- Simulate the user typing while delivery becomes eligible.
- Prove no notice is inserted into an uncertain active input buffer.
- Prove unsafe delivery stays queued and later succeeds through a qualified boundary or
  explicit pickup.
- Prove a transport failure never rolls back a valid report submission.

Milestone-6 exit gate:

- Batch membership and event generations are explicit and stable.
- Reopen creates one new event generation.
- One execution stall yields one incident and one recovery event.
- Real disposable harness tests cover kill/restart, pane reuse, and user input.
- No unsafe automatic notification is injected.

### 11.4 Milestone 7: captured verification and acceptance mapping

Prerequisite: milestone 4 is accepted. Complete milestone 5 first in the serial plan so
verification artifacts can participate in durable event resolution.

#### Task M7.1: complete verification artifacts

Capture the registered command, cwd, start/end time, exit code, timeout, stdout and
stderr artifacts, source snapshot digest, declared environment inputs, and model identity
as observed, requested, or unknown. Framework counts and skips are recognized only by a
supported parser; otherwise record them as unknown.

Required behavior:

- Structural checks still run before verification.
- Content is rechecked after verification so concurrent edits cannot inherit a green
  result.
- Explicit evidence reuse requires an exact match of command, roots, source content, and
  declared environment inputs; any mismatch reruns verification.
- Requested model flags are never reported as observed execution identity without
  evidence.

Proof:

- Change each reuse identity dimension independently and prove rerun.
- Modify source during verification and prove refusal.
- Exercise pass, fail, timeout, unsupported-output parser, missing artifacts, and unknown
  model identity.

#### Task M7.2: stable acceptance IDs and evidence table

Give each acceptance criterion a stable ID. Replace positional checklists as the
mechanical contract with an evidence table whose state is `met`, `partial`, `not-met`, or
`not-verified`, with linked artifacts and named gaps.

Required behavior:

- Normal submission requires every required criterion to be `met`.
- Missing artifacts, stale evidence, absent or duplicate IDs, missing required sections,
  and a structured gap attached to `met` are hard failures.
- Partial work uses the lightweight blocked path or an approved amendment.
- Free-text keyword matching does not attempt to decide arbitrary contradictions.
- Blocked submission remains usable when dependencies, verification, or providers fail.

Proof:

- Exercise every table state, duplicate and absent IDs, stale links, missing files, and
  structured contradictions.
- Include prose counterexamples such as `No limitations` and a limitation covered by
  integration evidence, proving the gate does not misclassify them by keyword.
- Prove feedback or full completion evidence is not required to submit an honest block.

#### Task M7.3: adversarial verification obligations

Turn the observed failure patterns into reusable evaluation fixtures and verifier
obligations:

- ignored input: two materially different inputs with independently expected output and
  captured selected-input identity;
- circular parity: separate actual-output provenance from expected-value derivation;
- weak fingerprint: change bytes without changing path/status shape;
- accidental one-to-one mapping: zero, one, and multiple events per boundary;
- changed oracle: independent per-value derivation and explicit expectation review;
- offline claim: controlled network-disabled execution or an explicit unverified gap.

For every negative test, remove or mutate the relevant behavior in an isolated copy and
prove the test fails. A digest proves identity only; it is not semantic evidence. Report
whether read restrictions are actually enforced by the harness or only requested by the
scope document.

Milestone-7 exit gate:

- Verification artifacts are complete, immutable, and source-bound.
- Reuse invalidates on every required identity change.
- Acceptance mapping rejects structural gaps without brittle prose policing.
- Blocked work remains lightweight.
- Every adversarial fixture catches the seeded defect it names.

### 11.5 Milestone 8: five-role lifecycle, authority, routing, and legacy migration

Prerequisites: milestones 4-7 are accepted. Define the transition table and artifacts
before changing lifecycle behavior.

#### Task M8.1: versioned transition table and artifacts

Specify every allowed transition for report publication, verification
pass/fail/uncertain, local correction, milestone review, approval, waiver, amendment,
reopen, and stale-evidence invalidation. Each operation requires role identity and exact
artifact revision.

Add separate numbered verification artifacts such as
`T03-verification-02.mdx`, with flat metadata for task submission round, attempt,
verifier session, contract revision, source digest, and result. A verifier retry on the
same submission receives a distinct attempt ID. Keep the report `submitted` while
verification and review are pending.

#### Task M8.2: five-role-v1 authority enforcement

Add an explicit `workflow: five-role-v1` policy with separate planner,
orchestrator, implementor, verifier, and reviewer registrations.

Required authority:

- Verifiers pass, fail, or record uncertainty; they never approve or waive.
- Orchestrators assign, recover, route, and assemble packets; they never approve
  disputed work, waive requirements, or alter acceptance.
- Reviewers alone approve and decide technical waivers.
- Planners own intent and constraint amendments. User input is required only when an
  amendment exceeds already authorized scope.
- Deterministic verification is infrastructure and cannot impersonate verifier or
  reviewer judgment.
- A low-risk task may omit an agent verifier only under explicit policy; capable
  milestone review still applies.

Proof:

- Attempt every forbidden cross-role operation and verify refusal before mutation.
- Prove verifier pass leaves the task submitted and cannot complete it.
- Prove approval packets identify exact task and bundle revisions.

#### Task M8.3: routing, corrections, and escalation

Encode the exception table from section 8 as deterministic routing. Planner receives
only intent/constraint amendments. Reviewer receives milestone reviews, technical
exceptions, disputes, exhausted local corrections, waivers, and out-of-policy spending.
Concrete gate or verification defects return to the implementor without a premium turn.

Start with two local correction attempts after initial submission, counting mechanical
gate repairs and verifier returns. Exhaustion creates one durable escalation and never
auto-approves, auto-waives, or opens endless rounds. Reviewer corrections require a new
implementation round, fresh verification, correction delta, and refreshed evidence.

Proof:

- Exercise every routing-table row and assert destination role and artifact pointer.
- Exhaust local corrections and prove one escalation with no extra round.
- Preserve implementor and verifier claims when disputed; route to reviewer instead of
  continuing an argument loop.

#### Task M8.4: explicit legacy preservation and migration

Preserve existing split and combined runs with their original planner events and
completion semantics. Never reinterpret them as five-role runs automatically. Provide an
explicit migration that preserves old artifacts, records old and new workflow versions,
and generates new event identities so stale legacy events cannot act on revised state.

Proof:

- Replay representative legacy runs through status, events, submit, decide, blocked,
  correction, and completion paths with unchanged semantics.
- Prove a new five-role run follows the new authority table.
- Migrate only through the explicit command/path; interrupted migration resumes without
  rewriting legacy evidence.

Milestone-8 exit gate:

- The transition table is complete and mechanically enforced.
- Only the reviewer can approve; verifier pass cannot complete work.
- Planner and reviewer registrations and routing are distinct.
- Correction limits and durable escalation work.
- Legacy runs preserve semantics unless explicitly migrated.

### 11.6 Milestone 9: deterministic prompts, profiles, and improvement cycle

Milestone 9 content may be drafted earlier, but wire it to live role contracts only
after milestone 8 is accepted.

#### Task M9.1: deterministic bounded prompt compositor

Render each role prompt from four ordered inputs: short role contract, task/decision
contract, selected guidance, and resume/evidence pointers. Record the rendered prompt
digest and every selected profile revision.

Required behavior:

- The common implementor contract includes discovery before approach, scope before edit,
  observable and independently derived evidence, explicit unverified conditions, honest
  blocking, and handoff before interruption when possible.
- Orchestrator prompts enumerate routing rules and prohibit technical invention,
  acceptance changes, disputed approval, and waiver.
- Verifier prompts request adversarial evidence checks rather than stylistic redesign.
- Reviewer prompts may challenge the plan against the original objective.
- Model/provider/effort remain execution metadata; remove model-persona filler.
- Model-specific guidance starts around 300-600 tokens, is relevance-selected, and is
  never padded to fill the budget. Unknown models receive only the common contract.
- Fresh context is the default at task and correction boundaries. Unsupported mid-round
  switching produces a checkpoint and new session.

Proof:

- Same inputs produce byte-identical prompt and digest.
- Changing one contract/profile revision changes the digest.
- Irrelevant cards and raw observations never appear.
- Token-boundary fixtures prove deterministic selection and preserved hard constraints.

#### Task M9.2: reviewed profiles and specialized cards

Create concise reviewed profile guidance separate from raw observations. Seed Muse
guidance from the one-run retrospective, preserving both strengths and failures and
labelling its confidence correctly. Add specialized cards for fixture changes,
concurrency, deployment configuration, and harness/input plumbing.

Selection tests must prove that only relevant cards load. Guidance that applies to all
models moves into the common contract; mechanically enforceable guidance moves into the
CLI and is removed from repetitive prompt prose.

#### Task M9.3: optional evidence-linked feedback

Allow every role and Docket itself to record concise observations at natural handoff or
decision boundaries. Use stable IDs and flat metadata for role, category, run/task/round
or incident, model/provider/harness versions, prompt/profile revisions, evidence paths,
confidence, and collection time.

Required behavior:

- Record feedback only when useful. No `no feedback` artifact is required.
- Feedback failure never rolls back or blocks submission, blocking, handoff, approval,
  waiver, or completion.
- Machine observations remain separate from an agent's causal interpretation.
- Multiple roles describing one task incident count as one incident, not independent
  confirmation.
- Existing operational records can be reconciled into feedback after a crash.

#### Task M9.4: backlog and retrospective commands

Implement the proposed feedback, improvements, and retrospective interfaces. Backlog
views include category, affected role/model/harness, severity, independent incident and
run counts, impact, latest occurrence, status, candidate intervention, and evidence.
Support filtering by category, model, role, status, and run. Preserve provenance and keep
uncertain grouping reviewable.

Retrospectives mechanically summarize corrections, escaped defects, reminders,
delivery/recovery failures, successful interventions, and available premium-token use.
They require no model call. An optional cheap narrative pass is not a permanent sixth
role and does not wake planner or reviewer. Raw observations stay out of ordinary
prompts.

#### Task M9.5: tested promotion lifecycle

Implement `observed -> proposed -> trial -> adopted`, plus `rejected`, in flat finding
frontmatter separate from task lifecycle. Proposals link incidents, expected benefit,
intended change, and an evaluation case. Adoption requires the actual change revision
and trial results. Rejection preserves its rationale.

No feedback automatically edits instructions, gates, permissions, profiles, or model
policy. Promotion requires an explicitly authorized improvement task and proportional
review. Project-local findings remain local until explicitly promoted. Promoted rules
record scope/version and can be retired when later evidence shows regression.

Milestone-9 exit gate:

- Prompts are deterministic, bounded, role-correct, and free of identity theatre.
- Only relevant reviewed guidance appears; raw feedback never appears.
- Every role can record useful evidence-linked feedback, while absent or failed feedback
  cannot block the primary workflow.
- Incident counts deduplicate corroborating roles correctly.
- Backlog filters and retrospective output work without mandatory model calls.
- Adoption cannot occur without linked authorization, change revision, and trial result.

### 11.7 Milestone 10: dispatch, recovery, budgets, and amendments

Prerequisites: milestones 6-9 are accepted.

#### Task M10.1: idempotent dispatch and ownership

Add `docket dispatch RUN TASK`. Bind dispatch to run, task, round, role registration,
session generation, and a human-readable agent name. Enforce dependencies, active scope
ownership, and provider concurrency before launch.

Required behavior:

- A retry after crash or uncertain launch cannot create a second writer.
- Duplicate delivery of a dispatch event is harmless.
- Dispatch refusal records the exact unmet dependency, ownership, registration, policy,
  or concurrency condition.

#### Task M10.2: resume, model switching, and checkpoints

Add `docket resume RUN TASK` and `docket switch-model RUN TASK --model MODEL`.
Mechanical checkpoints record current diff identity, last verification, session/model
history, task/decision pointers, and whether a semantic handoff exists.

Required behavior:

- Abrupt process loss is recoverable when no handoff exists. The replacement performs
  targeted discovery and supplies missing reasoning.
- An automatic snapshot is never labelled a ready semantic handoff.
- Unsupported in-place model switching creates a checkpoint and new session.
- Session/model history records requested, observed, fallback, and unknown states
  honestly.

#### Task M10.3: flat budget, concurrency, and fallback policy

Store primary/fallback models, concurrency, escalation rules, correction limits, and
budget thresholds in flat configuration fields or separate flat policy documents.

Required behavior:

- Fallback inside approved policy is routine orchestration.
- Exhausted choices, a higher spending tier, or changed scope emits one compact
  exception.
- Reserve configured premium budget for final review and late recovery.
- Missing cost/token telemetry is `unknown`; invocation count is labelled as a proxy.
- Task sizing initially warns on guessed breadth and never rejects solely on file count.

#### Task M10.4: versioned amendments and selective invalidation

Add `docket propose-amendment RUN TASK`. Amendment requests contain only the decision
needed, conflicting constraints, evidence pointers, recommended alternative, and impact
on acceptance/dependencies. Preserve old and new contract revisions.

Required behavior:

- Affected tasks, evidence, and events pause or invalidate against the new revision.
- Unaffected tasks continue.
- Delayed events for the old revision cannot act.
- Scheduling changes within approved intent stay with the orchestrator; intent changes
  route to planner and, when outside authorization, the user.

Proof for milestone 10:

- Crash before and after launch acknowledgement, then retry and prove one writer.
- Recover a killed process with and without semantic handoff.
- Exhaust allowed fallbacks and prove exactly one exception with no silent higher-tier
  spend.
- Exercise unknown model and unavailable usage telemetry.
- Amend one branch of a dependency graph and prove only affected work invalidates.

Milestone-10 exit gate:

- Dispatch/resume are idempotent and never create two writers.
- Fallback, concurrency, correction, and spending boundaries are enforced from flat
  policy.
- Recovery works without pretending an automatic checkpoint contains reasoning.
- Amendments preserve revisions and selectively invalidate work and events.

### 11.8 Milestone 11: packets, comparative pilot, and release gate

Prerequisite: milestones 4-10 are accepted. This milestone integrates the complete
workflow and produces the evidence for final human verification.

#### Task M11.1: milestone, correction, and review packets

Add `docket review-packet RUN --role reviewer`. Generate a reviewer packet containing
objective and acceptance coverage, exact approved task/bundle revisions, root-qualified
aggregate diff, integration evidence, verifier findings, waivers, unresolved risks, and
decisions needed. Keep the main packet around 1,000-2,000 tokens initially with pointers
to full immutable artifacts. Shortening must preserve risks.

Correction packets identify findings addressed, the delta from the reviewed bundle,
refreshed verification, and any expanded change surface. Keep access to the full diff.
The reviewer uses a fresh context from the planner and may inspect relevant source and
evidence. An approval binds exact revisions and is never inferred from verifier passes.

#### Task M11.2: reusable pilot fixtures

Build reusable tasks for:

1. ignored dataset input;
2. tautological parity;
3. changed fixture oracle;
4. negative test missing its trigger;
5. configuration assumptions;
6. long report truncation;
7. provider exit without handoff;
8. multi-root diff loss;
9. straightforward controls that expose unnecessary overhead and false rejection.

Each seeded defect has a held-constant reviewer criterion and a known independent oracle.
Tests prove the fixture actually triggers its intended failure.

#### Task M11.3: controlled prompt comparison

Compare the same model/harness configuration across current prompt, short common
contract, and common contract plus relevant profile cards. Pin versions where possible,
repeat tasks, and hold task difficulty and reviewer instructions constant. Record
provider outages separately and do not attribute improvements when model, task, or
review criteria changed simultaneously.

#### Task M11.4: measurement and retrospective

Capture first-round approval, final correctness, seeded defects missed, review rounds,
premium tokens, total model cost, wall time, prompt size, false blocks, manual reminders,
and delayed/duplicate notifications. Record all premium planning, amendment, milestone,
and final-review turns. Do not set a numeric cost target before baseline measurement.

#### Task M11.5: final integrated qualification

Run the full lifecycle, evidence, delivery, authority, prompt, feedback, dispatch,
amendment, legacy, and pilot suites. Include real disposable delivery tests in addition
to deterministic unit tests. Then generate one final pinned review packet for an
independent capable reviewer.

Release requires all of the following:

- All lifecycle/evidence regressions and delivery failure-injection cases pass.
- Healthy work requires no model polling turns.
- No crash/restart case loses an actionable event; duplicate delivery is harmless.
- No notification enters an unqualified active input surface.
- Verifier pass cannot approve; planner and reviewer contexts are separate.
- Routine failures do not wake a capable model before escalation policy requires it.
- Every decision references frozen code/evidence and later edits cannot inherit it.
- Feedback is discoverable across runs, deduplicated by incident, and excluded from
  prompts until explicitly authorized and evaluated promotion selects it.
- Retrospective collection requires no premium model and never blocks primary work.
- The pilot reduces premium review effort without worsening observed correctness.

Keep only profile rules supported by the comparison, remove or narrow ineffective rules,
and retain independent final review even if first-round approval improves.

### 11.9 Dependency and overnight handoff map

Execute serially unless isolated drafting can be proven not to alter shared lifecycle
state:

```text
M4 reopen recovery
    -> M5 durable event derivation, leases, and registrations
        -> M6 batches, health, and delivery qualification
M4 reopen recovery
    -> M7 captured verification and acceptance mapping
M5 + M6 + M7
    -> M8 five-role lifecycle and legacy migration
        -> M9 prompt profiles and improvement cycle wiring
M6 + M7 + M8 + M9
    -> M10 dispatch, recovery, budgets, and amendments
M4 through M10
    -> M11 packets, pilot, and final qualification
```

The receiving orchestrator may prepare M9 profile drafts or M11 pilot fixtures while an
independent prerequisite task runs, but must not wire them into lifecycle behavior before
the dependency gate above. Shared CLI, templates, tests, and playbooks are one change
surface; serialize their writers unless they use isolated worktrees and an explicit
integration step.

If the user is unavailable, continue through routine implementation and correction
choices already defined here. Stop and leave a durable escalation only for an actual
requirement conflict, unavailable required capability after allowed fallbacks, exhausted
correction budget, spending outside policy, or an action needing separate authorization.
Do not treat elapsed time, a provider failure with another allowed fallback, or a hard
implementation problem as user input.

At the end, do not claim final verification on behalf of the user. Leave the repository
and immutable evidence ready for the user's independent verifier, with a single final
packet listing every milestone digest, full-suite result, real-adapter result, pilot
measure, unresolved risk, installation state, and any decision still needed.
