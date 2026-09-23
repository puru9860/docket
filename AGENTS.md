# Working on docket

Instructions for an agent modifying this repository. If you want to *use* docket in a
project, that is a different thing: read `skills/docket/SKILL.md` and run
`docket help <role>`.

## Read first

1. **`ARCHITECTURE.md`** - the data model, the lifecycle, why signalling works the way it
   does, the invariants, and the known gaps. Do not change behaviour before reading the
   invariants section; several of them exist because the alternative shipped and broke.
2. `README.md` - what users are promised.

## Where things live

The stdlib-only CLI is `skills/docket/bin/docket`; role playbooks live in
`skills/docket/references/`. `ARCHITECTURE.md` has the complete code and data map.

The repo layout is fixed by the `skills` CLI convention - `skills/<name>/SKILL.md` is how
installers find it. Do not move it.

## Before you finish

```bash
tests/test.sh          # must be 0 failures
```

**Add a test for whatever you changed.** This is not a formality: the one significant bug
this project has shipped - the planner never being woken - existed because that code path
had never been exercised, while the neighbouring path had. A change with no new assertion
is a change nobody can verify later.

If you fix a bug, the test should fail before your fix and pass after. Check that.

## Two copies exist

The repo copy and the installed copy at `~/.agents/skills/docket/` are separate
directories. After editing, keep them in sync or you will test one and ship the other:

```bash
cp -r ~/Documents/Projects/docket/skills/docket/. ~/.agents/skills/docket/
diff -r ~/.agents/skills/docket ~/Documents/Projects/docket/skills/docket
```

## House rules

- No dependencies. `bin/docket` is stdlib-only and must stay runnable as a plain
  `python3` script if someone swaps the shebang.
- No em dashes in prose, use a plain dash.
- Detailed playbooks live in `skills/docket/references/`, not duplicated in `SKILL.md`.
  `docket help <role>` reads those installed files directly.
- Do not weaken the gate. If a report is getting rejected, the report is wrong, not the
  gate. `--skip-verify` exists for a broken verify command, not for a hurried report.
- Do not make `status: blocked` harder to use than submitting. An honest block is the
  behaviour the whole design is trying to buy.
- Keep frontmatter flat. No YAML library or nested structures. Snapshot and baseline JSON
  may hold mechanical evidence, but lifecycle state stays in document frontmatter.
- Baselines are immutable, complete, and captured before the work they measure. Never
  recapture one to make a diff look tidy, never publish a partial one, and never commit
  or stash a user's work to get a clean baseline. Under `evidence_mode: git`, a baseline
  that cannot be captured whole refuses the dispatch instead. Stored patches and
  untracked content are local project data that can contain secrets; they stay under
  `.docket/`.
- Checkout roots are declared explicitly, as `alias=path`, and only a declared root is
  captured. Do not reintroduce implicit discovery of linked worktrees or nested
  repositories: `.docket` may sit in a non-Git parent holding several checkouts, and an
  undeclared worktree's dirt does not belong in local evidence that can hold secrets.
- Evidence bundles are content-addressed and immutable. A frozen bundle is never
  reopened for writing and never deleted to tidy a history; a correction, a retry, a
  re-review, or a later round publishes a new address. Publish the whole directory in
  one rename, and keep `sha256` of every artifact in the manifest so tampering shows.
- A bundle carries the evidence it depends on. Copy the baseline record and its
  reconstruction artifacts in rather than pointing at mutable `.snapshots`, and check
  every tree the bundle pins for availability, not only the artifact digests. Evidence
  nobody can rebuild must report `DAMAGED`, never `intact`.
- One owner submits at a time. Hold `owner_lock` across the verify run, the freeze, the
  ledger append, and the report write. Recheck the contract, report, scope, and consumed
  inputs afterwards and refuse a submission whose documents moved; never republish a
  report body read before the command ran, because that overwrites a concurrent edit.
- Reconstructing evidence must not write to the checkout it measures. Use a private
  `GIT_INDEX_FILE` and `GIT_OBJECT_DIRECTORY` under `.docket`, with the real store
  read through `GIT_ALTERNATE_OBJECT_DIRECTORIES`. No commit, no stash, no index
  change, no ref, no working file, and no loose object in the user's repository. The
  private object store is local project data that can contain secrets, like the
  baseline patches beside it.
- A verdict binds to a bundle digest. Do not let a decision fall back to "whatever the
  workspace holds now" when the bundle is missing, damaged, or stale for the body in
  front of it. Blocking there is the point.
- Hand the freeze bytes, not paths. Capture the report body, the contract, and the
  consumed-input record before the verify command runs and pass those exact bytes to
  `freeze_task_bundle`. Re-reading a document after `drifted_inputs` reopens the window
  the recheck just closed.
- A round freezes the inputs it consumed, the empty set included, and an accepting
  verdict compares the live record against that frozen one. Never treat a re-recorded
  pin as a reverification: `dependency_problems` alone goes quiet the moment the pin is
  refreshed, which is exactly the bypass `refreshed_inputs` exists to close.
- Recording an input belongs to a draft round. `docket depend --on` must refuse a
  submitted, blocked, or decided consumer, preserve its frozen bundle untouched, and
  name the exact recovery. Reopening decided work is audited and is not this build's job.
- A capture that disappears mid-freeze fails the freeze. Never write an `unavailable`
  placeholder into a bundle that will still report `intact`.

## Things that look like bugs but are not

- The watcher exits 0 silently when nothing is actionable. That is the common case.
- An unarmed role is never woken. That is by design; `docket doctor` warns about it.
- `verify:` runs after the structural checks, not before. Ordering is deliberate.
- `docket submit` refuses a report that is already `submitted`. Also deliberate.
- `docket roots --redeclare` is refused once a baseline exists. An alias that quietly
  starts meaning a different checkout invalidates every baseline under it.
- An unreadable `roots.json` is an error, not an empty declaration. Absent and invalid
  are different states on purpose.
- `docket assign` refuses a `git` run it cannot baseline, and leaves no task behind.
  That is the fail-before-dispatch rule, not a lost assignment.
- A change list is unqualified in a single-root run and `<alias>:<path>` in a multi-root
  one. Both are the same code path; only the display differs.
- A re-review freezes a second bundle for the same round. The round number does not
  move; only the evidence address does, and the superseded bundle stays on disk.
- `docket submit` refuses a round whose checkout changed while its verify command ran.
  The captured result would not describe the frozen source, so it is not frozen at all.
- `.bundles/objects` grows and is never pruned. Correction deltas across rounds need
  the earlier trees to stay readable, and deleting the store makes every bundle that
  pinned a tree in it report `DAMAGED`.
- `docket depend` refuses a blocked, skipped, failed, stale, or patchless bundle even
  under `provisional_integration: allowed`. The policy permits consuming verified work
  early, not consuming work nothing verified.
- A consumed input that moves blocks a waiver as well as an approval. Both are accepting
  verdicts, and neither may settle work whose evidence no longer exists.
- `docket depend --on` refuses a submitted or decided consumer. The pin is part of the
  round's frozen evidence; refreshing it in place would swap the inputs a captured
  verification described without running anything again.
- A verdict is refused even after the pin was refreshed to the current bundle. The round
  was verified against the old one, and re-recording is not reverifying.
- `docket submit --blocked` refuses a round whose baseline capture was deleted. A
  lightweight blocked submission still has to freeze evidence somebody can rebuild.
- A supervisor woken once for an event is not woken again while that event is
  unchanged, even long after the announcement lease. Only an announcement that never
  reached the harness is repeated; a moved identity is a new wake.
- Under `five-role-v1` the orchestrator's review batch does not fire at submission.
  It waits until every submitted member's verification resolves, and in a quick run it
  wakes the checker instead of the coordinator.
- `docket resume` refuses a recorded model the current plan policy no longer approves.
  Pass `--model` with an approved one; a resume is a new launch, not a continuation.
- A model with many recorded reject cases still gets no model guidance until a
  reviewed profile is adopted with `docket models --adopt`. Raw cases never enter
  prompts; only reviewed rules backed by at least two tasks do.
- An exhausted correction budget wakes the planner (the coordinator in quick), not
  the reviewer that hit it. The budget is plan policy; only its owner may grant
  more rounds with `docket escalation`.
- A blocked implementor round wakes the orchestrator, not the reviewer, until the
  orchestrator runs `docket route --kind blocked`. It may own the answer; the route
  record is what hands the verdict to the reviewer.
- A `docket resume` of a round whose worker changed nothing renders the initial
  prompt, not the resume one. Its checkpoint measured no work to rediscover.
- `docket usage` shows no session for a role that never ran a docket command inside
  its harness, and ignores harness variables whose process is not an ancestor.
  Guessing the newest session would attribute another session's tokens.
- A task assigned with an unmet `--depends-on` has no task baseline until it is
  dispatched. That is deliberate: its baseline must include the dependency's approved
  work, and it is still captured once, before its own dispatch, and never recaptured.
