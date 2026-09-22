# Remaining work after independent review

Status: continuation handoff, updated after tenth independent review, 2026-09-10.

This file records findings from the independent review of the milestones 4-11
implementation. It is an execution aid, not a second requirements document.
`improvements/workflow-improvement-plan.md` remains the sole source of product
requirements. When this file and that plan differ, follow the plan.

Read the historical findings for context, then continue from **Tenth independent review
checkpoint, 2026-09-10**. Earlier numbered tasks describe already-corrected behavior and
must not be repeated blindly.

Do not claim milestones 4-11 complete yet. The implementation is substantial and the
164-test suite passes, but the release gate accepts blocked delivery qualification and
the final packet omits required release evidence. The actual comparative pilot, profile
pruning, final frozen qualification, and independent capable review also remain.

## Verified starting state

- Repository: `/home/purushottam/Documents/Projects/docket`
- Branch and reviewed HEAD: `main` at
  `74d81691c38763267d238894149d899b8ab48ee1`
- Working tree: uncommitted milestone implementation and documentation changes remain.
- Full suite: `tests/test.sh` passed 143 tests with 0 failures in 338.63 seconds.
- Static checks passed:
  - `python3 -m py_compile skills/docket/bin/docket skills/docket/tests/test_docket.py`
  - `sh -n` for every shell script under `skills/docket/eval/`
  - `git diff --check`
- The M4 interrupted-reopen regression now passes at every injected boundary.
- Targeted M5 and M7-M10 tests pass, but those milestones have no recorded milestone
  runs or immutable acceptance bundles.
- The eight installed shell fixtures accept their correct subjects and reject their
  seeded-defect subjects.
- No shared Docket installation exists at `~/.agents/skills/docket/` or
  `~/.claude/skills/docket/`. Continue to use
  `python3 skills/docket/bin/docket` unless installation is separately authorized.
- Nothing from this independent review was committed, installed, or pushed.

The formal `no-mistakes` executable is not installed. Its local review and test gates
were performed directly where applicable.

## Blocking finding 1: native hook delivery can lose a wake

Plan references: sections 4, 11.2 M5.2 and M5.4, and 11.3 M6.3-M6.4.

`cmd_watch` writes every derived event key to `.woke-ROLE` before it prints the wake
notice and exits 2. If the watcher or owning hook stops after the ledger write but before
the harness accepts the notice, the next watcher sees the key as delivered and exits 0.
The leased pending event still exists, but the native hook does not announce it again.

Observed isolated reproduction:

```text
first watcher exit: 2
second watcher exit: 0
durable pending events: 1
reconcile: 1 derived event, 0 retired
```

The current M6 test starts a watcher while an event is already actionable, sleeps for one
second, and then calls `kill`. The watcher normally exits before the kill, so the test does
not interrupt a delivery boundary.

Required correction:

1. Make the native hook use the durable claim, bounded lease, receipt, retry, and
   resolution protocol, or add an equivalent journaled announcement transition whose
   interruption is recoverable.
2. Do not make `.woke-ROLE` final delivery truth before the harness has accepted a wake.
3. Add a fault boundary after durable claim or announcement state is written and before
   the notice is returned.
4. Kill at that exact boundary, restart the hook, and prove the same actionable event is
   announced or claimable again after lease expiry.
5. Prove repeated hooks and competing hooks remain harmless and do not create duplicate
   lifecycle transitions.

Exit evidence:

- A regression test fails against the current implementation and passes after the fix.
- The test proves interruption at the actual vulnerable boundary, rather than killing a
  process that has already exited.
- An event stays recoverable until a durable resolution retires it.

## Blocking finding 2: the Claude hook excludes verifier and reviewer

Plan references: sections 4 and 7, and 11.5 M8.1-M8.3.

`skills/docket/hooks/wake.sh` accepts only `planner|orchestrator` in its `DOCKET_ROLE`
case statement. `five-role-v1` derives events for verifier and reviewer, and `doctor`
expects those roles to be armed, but the documented hook exits without watching them.

Required correction:

1. Route `verifier` and `reviewer` through the native hook.
2. Keep role registrations and ledgers separate.
3. Update README and signalling examples that still describe only planner and
   orchestrator watchers.
4. Add hook-level tests for all roles, including wrong-role isolation and a real verifier
   submission followed by reviewer milestone readiness.

Exit evidence:

- Each five-role session can receive only its own events through the actual hook script.
- A verifier submission cannot wake or act as reviewer, and a reviewer event cannot be
  consumed by another role.

## Blocking finding 3: Herdr capability detection can invert a negative statement

Plan references: section 4 and 11.3 M6.4.

`probe_boundary` treats the presence of any of these substrings in Herdr help as proof of
an unattended safe boundary: `queue`, `turn-boundary`, `send-if-idle`, or `atomic`.
A fake adapter whose help said `does not support queue or atomic send-if-idle` was
reported as:

```text
boundary: verified-queue
mode: unattended
```

Required correction:

1. Replace help-text keyword inference with a positive, versioned capability contract or
   an executable queue/turn-boundary probe.
2. Treat ambiguous output, negative statements, unknown versions, timeouts, and nonzero
   exits as manual mode.
3. Do not permit an unattended send unless the exact positive capability was verified.
4. Record the probed command/version and result so `doctor` can explain the decision.

Required tests:

- Positive supported capability.
- Negative text containing every positive keyword.
- Unknown and changed help formats.
- Timeout and nonzero exit.
- Advertised capability whose executable probe fails.

Exit evidence:

- Every uncertain case remains queued in manual mode.
- Only a positively verified queue or turn boundary reports unattended mode.

## Blocking finding 4: real delivery qualification has not been performed

Plan reference: 11.3 M6.4 and the milestone-6 exit gate.

The current tests launch real Docket subprocesses, but they do not create a disposable
Herdr, Claude, OpenCode, or equivalent recipient session. Input activity is represented by
manually creating `.delivery/ROLE/input-active`. Pane reuse is represented by
re-registering a session ID. No service supervisor is started or restarted.

Required continuation:

1. Keep manual mode if this installed Herdr exposes no safe boundary.
2. Test the native Claude hook in a disposable real session if the harness supports it.
3. Test recipient termination, restart, reused session/pane name, new generation, active
   user input, and transport interruption.
4. Capture commands, versions, process IDs, exit statuses, event IDs, lease history, and
   outbox or hook results as artifacts.
5. If a required real capability is unavailable, record that limitation honestly. Do not
   convert the deterministic fake into a claimed real-adapter result.

Exit evidence:

- Real disposable results cover the cases required by plan lines 946-954.
- No notice enters an input surface whose safety has not been positively established.

## Blocking finding 5: reviewer packets are incomplete and can hide damaged evidence

Plan reference: 11.8 M11.1.

The main reviewer packet currently iterates task owners and excludes `orch`, so it does
not include the required root-qualified aggregate bundle and diff. It reports only a
summary row for acceptance, not the complete acceptance-to-evidence coverage or
integration evidence required by the plan.

The packet also truncates the plan `Risks` section to 300 characters before its
`risks never shortened` preservation loop. An isolated test with a marker after character
300 confirmed that the marker disappears.

When an artifact in an approved frozen bundle was modified, `docket bundle --list`
reported the bundle as damaged, but `docket review-packet` still succeeded. It silently
showed no usable bundle instead of reporting the damage or refusing to generate a packet
that could be mistaken for reviewable evidence.

Required correction:

1. Validate every referenced task and aggregate bundle with the existing full bundle
   integrity checks before rendering.
2. Refuse a reviewable packet when required evidence is missing, damaged, stale, or no
   longer bound to the current report, verification, decision, or contract revision.
3. Include the exact aggregate bundle digest, root-qualified aggregate diff, constituent
   bundle digests, integration verification, verifier findings, waivers, unresolved
   risks, and decisions needed.
4. Preserve complete risks. Reduce diff excerpts or other replaceable evidence first and
   point to immutable full artifacts.
5. Include stable acceptance IDs and their evidence states rather than only task-level
   status.
6. Make correction packets identify each addressed finding, old and new bundle digests,
   correction delta, refreshed verification, and expanded scope.

Required regressions:

- Damaged artifact, manifest, pinned Git object, and stale bundle each block packet
  generation with a specific error.
- A long risk ending in a unique marker appears intact in the packet.
- A completed aggregate's digest and root-qualified patch appear in the packet.
- Integration evidence and every required acceptance ID appear.
- Packet reduction stays within the configured token target without removing risks.

## Blocking finding 6: the controlled three-arm pilot is not implemented or run

Plan references: section 10 and 11.8 M11.2-M11.5.

No model arms were run. The current `pilot --compare` command requires exactly two arms,
although the plan requires:

1. current prompt;
2. short common contract;
3. common contract plus relevant profile cards.

The command records a model string but no harness/version, model version, provider,
effort, reviewer instruction revision, or run/repetition identity. Comparison does not
verify that model values match. An isolated comparison using `model-A` for one arm and
`model-B` for the other was accepted as controlled.

Fixture revision currently hashes only `docket-eval.json`. Edits to `check.sh`, subjects,
broken subjects, configuration, or oracle files can therefore change task difficulty or
expected behavior without changing the recorded revision.

Required correction:

1. Represent and compare all three required arms.
2. Record and hold constant model/provider/harness versions, effort, task revision,
   reviewer instructions, oracle revision, repetition, and relevant environment inputs.
3. Compute a deterministic revision over every file that defines a fixture, including
   scripts, inputs, correct and broken subjects, configurations, and independent oracles.
4. Refuse attribution when any controlled dimension differs or is unknown where equality
   is required.
5. Require repeated tasks and report each repetition separately before aggregation.
6. Keep provider outages separate from model failures.
7. Do not treat manually supplied pass/fail totals as proof that an arm ran. Link each
   result to captured execution and reviewer artifacts.

Required regressions:

- Different model, harness, effort, reviewer revision, or environment refuses comparison.
- Changing any fixture-defining file changes the fixture revision.
- Missing third arm or missing repetition refuses a complete-comparison claim.
- Seeded defects and straightforward controls are both represented.
- Provider outages do not count as model failures or successful trials.

After the harness is corrected, run the actual three-arm pilot. Keep only profile rules
supported by that comparison. Do not set a numeric cost target until the baseline arm has
been measured.

## Blocking finding 7: reusable pilot coverage and metrics are incomplete

Plan references: 11.8 M11.2 and M11.4.

The pilot inventory currently contains eight shell fixtures:

```text
changed-oracle
circular-parity
config-assumption
ignored-input
negative-trigger
offline-claim
one-to-one-mapping
weak-fingerprint
```

Long report truncation, provider exit without handoff, multi-root diff loss, and
straightforward controls exist only as branches inside a unit test. They are not reusable
pilot tasks with pinned metadata, held-constant reviewer criteria, and independent
oracles.

`docket metrics` currently reports some artifact counts, but it does not capture or
aggregate final correctness, seeded defects missed, review rounds, premium planning and
review turns, measured premium tokens, total model cost, or actual false blocks. It
always prints several fields as unknown and does not consume the token values stored by
pilot result records.

Required correction:

1. Package every scenario required by M11.2 as a reusable, versioned pilot task.
2. Give each task an independent oracle and held-constant reviewer criterion.
3. Capture all M11.4 measures per execution and per arm.
4. Preserve honest `unknown` only when telemetry is truly unavailable, while preventing
   a run with required unknown release measures from claiming the pilot release gate.
5. Distinguish a normal receipt from an actual duplicate notification. The current metric
   counts every `receipt` log entry as a duplicate.
6. Record premium planner, amendment, milestone-review, and final-review turns.

Exit evidence:

- Every required fixture appears in inventory and can run independently.
- Metrics trace back to immutable execution or review artifacts.
- A release report can compare premium effort and observed correctness across all arms.

## Blocking finding 8: milestone and final evidence packets are absent

Plan references: 11.0 and lines 1407-1410.

Current Docket run directories are:

```text
docket-workflow-improvements
docket-workflow-improvements-m3
docket-workflow-improvements-m4
docket-workflow-improvements-m4-bundles
```

There are no M5-M11 Docket runs, accepted task bundle digests, milestone aggregate
digests, or final pinned review packet. The implementation agent's prose summary did not
list the required milestone digests. The execution checkpoint near the top of the source
plan therefore must not be treated as verification evidence.

Do not manufacture historical baselines or recapture a baseline to make this gap look
clean. Before corrections begin, inspect whether an existing complete run baseline
legitimately predates the work it would measure. Use it only if its immutable artifacts
prove that fact. Otherwise:

1. Record that historical per-task M5-M10 bundles are unavailable.
2. Start a new audit/remediation run before making further corrections.
3. Capture complete baselines for all declared roots without committing, stashing, or
   excluding current user work.
4. Freeze every new correction and integrated qualification against that run.
5. Provide the live full repository diff separately for independent review, clearly
   distinguishing it from task-attributed evidence when attribution cannot be recovered.
6. Never relabel a new baseline as if it predated the completed implementation.

The final packet must list every available milestone and correction digest, explicitly
identify the missing historical evidence, give the full-suite and real-adapter results,
include the actual pilot measures, and state installation status and unresolved risks.

## Required execution order

Follow the dependency rules in section 11.9 of the source plan. For this correction pass:

1. Establish a valid audit/remediation baseline before editing.
2. Repair native hook roles and crash-recoverable delivery.
3. Repair positive capability qualification.
4. Run deterministic delivery tests, then disposable real-harness qualification.
5. Repair packet integrity, aggregate coverage, risk preservation, and correction packet
   content.
6. Complete reusable pilot tasks and full-fixture versioning.
7. Complete controlled-comparison metadata and enforcement.
8. Complete metrics collection.
9. Run the actual three-arm repeated pilot.
10. Remove or narrow profile rules unsupported by the comparison.
11. Run targeted tests, then `tests/test.sh` with zero failures.
12. Generate immutable correction, aggregate, pilot, and final review evidence.
13. Give the final packet to a fresh capable reviewer. Do not let verifier pass imply
    approval.

Do not install or synchronize the shared skill, commit, push, or publish unless separately
authorized. Do not weaken gates or use `--skip-verify` except for a genuinely broken
verification command as defined by the repository instructions.

## Completion checklist

The next agent may report this continuation complete only when all items below are true:

- [ ] The vulnerable native-hook boundary has a real failing-before, passing-after crash
      regression.
- [ ] Planner, orchestrator, verifier, and reviewer native hooks are role-isolated and
      functional.
- [ ] Negative or ambiguous capability text cannot enable unattended delivery.
- [ ] Real disposable delivery tests satisfy M6.4 or record an honest unavailable
      capability without claiming success.
- [ ] Reviewer packets validate every bundle, include aggregate and integration evidence,
      preserve full risks, and expose all acceptance IDs.
- [ ] Every M11.2 scenario is a reusable versioned pilot task.
- [ ] Fixture revisions cover every file that can affect task behavior or its oracle.
- [ ] The comparison enforces three arms, repetitions, and held-constant execution and
      review identities.
- [ ] Required M11.4 metrics are captured from linked artifacts.
- [ ] Actual pilot evidence demonstrates reduced premium review effort without worse
      observed correctness.
- [ ] Profile guidance retained in the skill is supported by the comparison.
- [ ] `tests/test.sh` reports zero failures.
- [ ] Static and documentation checks pass.
- [ ] Every available correction and aggregate bundle digest is listed in a pinned final
      review packet.
- [ ] Missing historical evidence is disclosed rather than reconstructed or implied.
- [ ] A fresh capable reviewer approves the exact final revisions.

## Second independent review checkpoint, 2026-09-08

The first correction pass implemented meaningful parts of findings 1-7. The full suite
now passes 151 tests, and static checks pass. Findings 1 and 2 are corrected. The
installed Herdr 0.8.2 is reported honestly as manual. Bundle validation, complete risk
preservation, aggregate packet inclusion, full fixture-directory hashing, three-arm
syntax, controlled model/harness fields, and duplicate receipt accounting are present.

Do not mark the continuation complete. The following requirements remain open. They are
refinements of findings 4-8 above, not new product requirements.

### Next task 1: bind pilot rows to real immutable evidence

Plan references: 11.8 M11.3-M11.5.

Current behavior only checks that `--evidence` is a nonempty string. It does not require
the path to exist, verify its digest, prove that it contains captured execution and
reviewer results, or freeze it. `pilot --compare` repeats the same nonempty-string check.

Observed counterexample:

```text
fixture set: ignored-input only
arms: current, contract, contract-cards
repetitions per arm: 2
evidence for every row: DOES-NOT-EXIST.json
prompt digest for every arm: sha256:SAME
result: controlled comparison accepted
```

Required correction:

1. Resolve every evidence reference inside the run and refuse missing, unreadable,
   mutable, stale, or out-of-run evidence.
2. Bind every row to content digests for the captured execution and held-constant review
   result. Store or copy the evidence into immutable run-local storage.
3. Verify the evidence describes the named fixture revision, arm prompt digest, model,
   provider, harness, effort, environment, reviewer revision, and repetition.
4. Require nonnegative measured counts and token values.
5. At comparison time, revalidate every evidence digest and refuse damaged evidence.
6. Add failing-before tests using nonexistent, edited, mismatched, and reused evidence.

### Next task 2: prove the three arms and complete fixture set actually differ as intended

Plan references: section 10 and 11.8 M11.2-M11.3.

The current comparison accepts identical prompt digests for all three arms and accepts a
single fixture as a complete controlled comparison. It does not compare the current
fixture revision with the revision recorded in each row, so later fixture edits do not
invalidate old rows.

Required correction:

1. Require the complete configured pilot fixture set for a release comparison.
2. Require each arm to use the prompt form it names. Prompt digests must be known,
   internally stable for the intended repeated input, and different where the arm
   contracts differ.
3. Bind prompt digests to immutable rendered prompt artifacts rather than accepting an
   arbitrary digest string.
4. Require compatible repetition identities across arms so the compared executions are
   paired or otherwise demonstrably equivalent.
5. Recompute the live fixture revision at comparison time or freeze the fixture content
   with each result. Refuse drift.
6. Add counterexamples for one-fixture comparisons, identical prompts, moved fixtures,
   mismatched repetitions, and fabricated prompt digests.

### Next task 3: make metrics semantic and fail closed

Plan references: 11.8 M11.4-M11.5 and the release conditions at plan lines 1357-1369.

The current release block checks only whether any pilot repetition and any numeric token
value exist. Six fabricated rows with nonexistent evidence remove the block even while
the command prints final correctness, total cost, false blocks, and manual reminders as
unknown.

The current labels also overclaim what the artifacts establish:

- approved tasks divided by decided tasks is labelled `final correctness`;
- the total user-supplied `failed` count is labelled `seeded defects missed`;
- every rendered planner or reviewer prompt is labelled a premium turn without proving a
  model invocation or premium tier;
- wall time grows from the plan file's modification time to the present instead of using
  a captured execution start and end.

Required correction:

1. Define each metric from the plan in terms of a specific immutable source artifact.
2. Keep approval, observed final correctness, and seeded-defect detection separate.
3. Capture provider/model usage, premium tier, tokens, cost, and turn type from execution
   records. Label a count as a proxy when direct telemetry is unavailable.
4. Capture bounded start/end timestamps for wall time.
5. Capture or explicitly adjudicate false blocks and manual reminders.
6. Make the release gate depend on a valid complete three-arm comparison, verified
   evidence, every required measure, no unresolved qualification failures, and the
   source plan's release conditions.
7. A required unknown must keep the release gate blocked. Printing `unknown` while
   omitting the block is a failing regression.

Required counterexample:

```text
pilot rows exist: yes
numeric tokens exist: yes
final correctness: unknown
total model cost: unknown
false blocks: unknown
manual reminders: unknown
expected release gate: BLOCKED
```

### Next task 4: perform or honestly block real disposable harness qualification

Plan reference: 11.3 M6.4, especially lines 946-961.

The test named `test_real_harness_qualification_or_honest_limitation` invokes the real
Herdr help probe, then exercises Docket files and CLI calls. It does not start, kill, or
restart a disposable recipient or hook session. Its comments claim hook kill/restart,
but no hook process is launched in that part of the test.

Required continuation:

1. Use a disposable real harness session when an available harness supports the required
   operations.
2. Exercise hook, service where supported, and recipient interruption at each delivery
   boundary, pane/session reuse, active user input, and recovery.
3. Capture the exact version, commands, process and session identities, event identity,
   lease history, outputs, and exit statuses.
4. If no available harness exposes a safe disposable mechanism, produce a durable blocked
   qualification artifact naming the unavailable capability. Do not name a help probe
   plus file simulation a real-harness qualification.
5. Keep production mode manual until a positive behavior-level qualification succeeds.

### Next task 5: complete reviewer packet integration evidence

Plan reference: 11.8 M11.1.

The corrected packet validates task bundles, includes a valid aggregate when one exists,
and preserves full risks. The section titled `Verifier findings, waivers, integration
evidence, and unresolved risks` currently lists task verifier artifacts, but it does not
extract or bind actual aggregate integration evidence. The packet test submits the
orchestrator aggregate without verification and does not assert integration evidence.

Required correction:

1. Define the immutable artifact that carries integration verification for the aggregate.
2. Require it for a final review packet and validate its command, source identity,
   outputs, result, and aggregate bundle binding.
3. Include it in the packet with a pointer to the complete frozen artifact.
4. Distinguish task or milestone packets from the final release packet so a packet that
   legitimately precedes an aggregate cannot be mistaken for final qualification.
5. Add damaged, stale, missing, failing, and source-mismatched integration evidence tests.

### Next task 6: add semantic assertions for the four new fixtures

Plan reference: 11.8 M11.2 and the repository rule requiring tests for changes.

The four new fixtures currently behave correctly when invoked manually:

```text
long-report-truncation              correct=0 broken=1
provider-exit-without-handoff       correct=0 broken=1
multi-root-diff-loss                correct=0 broken=1
straightforward-controls            correct=0 broken=1
```

The automated suite checks that these directories appear in inventory, but it does not
execute their `check.sh` scripts against both subjects. Add assertions equivalent to the
existing adversarial-fixture tests so future changes cannot make either side vacuous.

### Next task 7: run the external release work after the code gates are correct

This work remains deliberately unperformed and must not be inferred from a green unit
suite:

1. Run the actual repeated three-arm model comparison with captured evidence.
2. Measure premium effort, correctness, cost, false blocks, reminders, and delivery
   behavior.
3. Remove or narrow profile rules unsupported by the results.
4. Record the unavailable historical M5-M10 task bundles honestly. Never reconstruct or
   recapture their baselines.
5. Generate the final pinned correction and aggregate review packet.
6. Obtain fresh capable review of the exact final revisions.

### Second-review verification result

- `tests/test.sh`: 151 tests, 0 failures in 205.13 seconds.
- Python compilation: passed.
- Shell syntax for hooks and evaluation scripts: passed.
- `git diff --check`: passed.
- No em dashes in reviewed repository prose: passed.
- Installed Herdr 0.8.2 probe: manual mode, no unattended reliability claimed.
- Independent fabricated-evidence comparison: incorrectly accepted.
- Independent incomplete-metrics release gate: incorrectly not blocked.
- Final verdict: not ready for release or final capable approval.

## Third independent review checkpoint, 2026-09-08

The second correction implementation is materially stronger. Pilot rows now bind to
frozen run-local evidence, release comparisons require the complete three-arm fixture
matrix, aggregate integration verification is captured and validated, delivery
qualification exercises real processes and a disposable Herdr pane when available, and
all twelve evaluation fixtures execute against both their correct and seeded-defect
subjects.

Independent verification completed:

- `tests/test.sh`: 164 tests, 0 failures in 289.697 seconds.
- Python compilation: passed.
- Shell syntax for the hook and evaluation scripts: passed.
- `git diff --check`: passed.
- No em dashes were introduced in changed prose.
- Missing, mismatched, edited, reused, and drifted pilot evidence is covered by refusal
  tests.
- A complete three-arm comparison with paired repetitions and distinct prompt digests is
  covered by positive and negative tests.
- The approved suite exercised the disposable Herdr pane qualification successfully.

The six code tasks are not yet fully accepted. Two release blockers remain, followed by
the external pilot and human-review work already listed as Next task 7. This document is
an execution aid only; `improvements/workflow-improvement-plan.md` remains the sole
requirements source.

### Next task 1: a blocked or fabricated delivery qualification must block release

Plan references: M6.4 in section 11.3 and the release conditions in M11.5.

`cmd_metrics` currently treats both `passed` and `blocked` qualification states as
sufficient:

```python
if not quals or any(str(q.get("status", "")) not in ("passed", "blocked") for q in quals):
```

That makes a known unavailable capability indistinguishable from completed
qualification at the release boundary. The qualification reader also trusts any JSON
object named `.delivery/qualification-*.json`; it does not validate the run, role,
required checks, timestamps, captured command/process evidence, or an immutable digest.

Independent counterexample:

```text
delivery qualification orchestrator: blocked (mode manual; required disposable pane was unavailable)
release gate: READY for independent capable review (all required measures known; this is not an approval)
```

Required correction:

1. Only a complete `passed` qualification may satisfy the release gate. `blocked`,
   `failed`, unreadable, malformed, and missing artifacts must keep it `BLOCKED` and name
   the exact reason.
2. Validate that the artifact belongs to the current run and role and contains every
   required M6.4 check with captured commands, identities, exits, and a passing result.
3. Bind the qualified source and adapter/harness version. Freeze or content-address the
   qualification evidence so an edited status cannot inherit trust.
4. Add a regression that builds an otherwise valid comparison and adjudication, supplies
   a blocked qualification, and asserts that the release gate stays `BLOCKED`.
5. Add a fabricated-qualification regression. A hand-written JSON object containing only
   `{"status": "passed"}` must never satisfy release qualification.

### Next task 2: `review-packet --final` must represent the complete release gate

Plan reference: M11.5 and the final handoff requirement at the end of section 11.9.

The current `--final` gate requires a valid aggregate bundle and passing aggregate
integration verification. It does not require a controlled pilot comparison,
adjudication, delivery qualification, full-suite artifact, or a READY metrics result.
The existing positive final-packet test creates no pilot evidence at all and still
succeeds.

The rendered final packet also omits the plan's required final-handoff fields. A direct
packet probe after adding pilot and delivery artifacts produced:

```text
pilot present=False
delivery qualification present=False
real-adapter result present=False
full-suite present=False
installation state present=False
```

Milestone digests are not presented as a complete M4-M11 inventory either.

Required correction:

1. Make `review-packet --final` refuse until the valid complete comparison, required
   adjudication, complete passing delivery qualification, aggregate integration
   verification, and captured full-suite result all exist and remain bound to the exact
   reviewed revisions.
2. Freeze or content-address the release inputs. The packet must not read mutable
   qualification, adjudication, comparison, or test-result files and present them as
   pinned evidence.
3. Render every milestone digest or an explicit unavailable entry, the full-suite result,
   real-adapter result, pilot measures, unresolved risks, installation state, and every
   remaining decision. Never invent historical M5-M10 bundles or recapture a baseline
   after the measured work.
4. Add negative tests showing `--final` refuses missing, blocked, damaged, stale, or
   fabricated pilot, qualification, and suite evidence.
5. Add a positive packet-content test asserting each required final-handoff field and its
   immutable evidence digest appears.

### Next task 3: perform the external release work only after the two gates above pass

Plan references: M11.3-M11.5 and the release conditions at plan lines 1357-1369.

1. Run the actual repeated three-arm model comparison across the complete configured
   fixture set using frozen execution and held-constant reviewer evidence.
2. Adjudicate correctness, seeded defects, false blocks, reminders, tokens, wall time,
   and total model cost. Set a numeric cost target only after measuring the baseline.
3. Remove or narrow profile rules unsupported by the comparison.
4. Record the unavailable historical M5-M10 task bundles honestly. Do not reconstruct
   them or recapture their baselines.
5. Run the final full qualification suite and freeze its exact outputs against the final
   source.
6. Generate the complete final pinned packet and obtain fresh capable review of its exact
   revisions.
7. Decide whether to install the reviewed skill. No repo-local, `~/.agents`, or
   `~/.claude` Docket installation currently exists; do not install or sync before that
   decision is authorized.

### Third-review verdict

The six-task correction is not ready for final release because the mechanical release
gate accepts blocked qualification and the final packet does not bind or present the
complete release evidence required by the source plan. After those two code corrections,
the actual pilot, profile pruning, final frozen qualification, and independent capable
review remain.

## Fourth independent review checkpoint, 2026-09-09

The third-review qualification and final-packet blockers have been substantially
corrected. A blocked, malformed, foreign, or digest-damaged qualification now keeps
metrics blocked. `review-packet --final` now requires a content-addressed release freeze
and renders the milestone inventory, suite result, adapter result, pilot measures,
installation state, and decisions. The new pilot arm files are generic, versioned drafts
with resolved profile-card references.

Independent verification completed:

- `tests/test.sh`: 170 tests, 0 failures in 416.303 seconds.
- Python compilation: passed.
- Shell syntax for all evaluation scripts: passed.
- `git diff --check`: passed.
- The new pilot-arm invariant test passed.
- The blocked, fabricated, tampered, and foreign qualification regressions passed.
- The release-freeze and final-packet regressions passed as written.

Three corrections remain before approving the arm inputs or running the paid pilot. This
document remains an execution aid; `improvements/workflow-improvement-plan.md` is the
sole requirements source.

### Next task 1: make the `current` arm an actual pinned baseline

Plan reference: M11.3, especially lines 1335-1341.

The plan requires comparison against the current prompt. `eval/arms/current.md` instead
claims to present the fixture exactly as stated while adding these new directives:

```text
Solve the task using only what is in front of you.
Produce the required artifacts.
Report what you did and what remains uncertain.
```

These are substantive task instructions. In particular, “only what is in front of you”
can suppress repository discovery, while the contract arm explicitly requires discovery.
Calling the lines “procedure” does not make the baseline unchanged. The file therefore
pins neither the actual existing prompt nor a fixture-only baseline.

Required correction:

1. Define `current` from the exact prompt currently used before the experimental common
   contract, with its source and version pinned; or define a fixture-only baseline and
   remove every added task instruction. Do not mix the two definitions.
2. Hold all experiment-only procedure and telemetry instructions constant across the
   three arms outside the treatment text.
3. Add a test that reconstructs the `current` arm from its named source and proves exact
   byte identity, rather than merely checking that selected contract sentences are
   absent.
4. Keep all three files at `status: draft` until a capable reviewer approves the resolved
   baseline. No pilot row should use a draft arm.

### Next task 2: bind suite claims to the frozen suite log

Plan reference: M11.5 and the final handoff requirement at lines 1407-1410.

`docket release --freeze` accepts `--suite-tests`, `--suite-failures`, and
`--suite-exit` as unrelated caller claims. It freezes the supplied log but never checks
that the log supports those values.

Independent counterexample:

```text
frozen suite.log: 164 tests, 0 failures
supplied --suite-tests: 999
release freeze: accepted
final packet: tests: 999, failures: 0
```

Required correction:

1. Capture the suite execution and result as one artifact, or parse a supported suite
   summary and require the supplied metadata to match it exactly.
2. Refuse empty, unrelated, contradictory, or unsupported logs instead of freezing
   caller assertions as observed results.
3. Bind the suite to the exact aggregate source tree, including uncommitted content. A
   Git HEAD alone is not a source identity in this repository.
4. Add mismatched-count, stale-log, wrong-source, empty-log, and unsupported-format
   regressions.

### Next task 3: freeze every byte rendered as release evidence

Plan references: M11.1, M11.5, and the final pinned-packet requirement at lines
1407-1410.

The release bundle freezes comparison rows, adjudication, qualification JSON, and the
suite log. The final renderer still reads objective, risks, acceptance criteria,
verification files, and decision files from live run documents. Its own docstring labels
plan prose as mutable live state, which conflicts with a final pinned packet whose
approval binds exact revisions.

Independent counterexample:

```text
release frozen: yes
plan edited afterwards with AFTER-FREEZE-RISK: yes
final packet refused as stale: no
new live risk appears under the old release digest: yes
```

Required correction:

1. Copy the exact plan, task contracts, verifier findings, decisions, and other rendered
   release documents into the release directory and include every file digest in the
   release manifest.
2. Render the final packet only from those frozen bytes. Live installation state may
   remain explicitly live because it is an environmental check, not approval evidence.
3. Bind delivery qualification to the exact content tree it exercised. `repo_head` is
   insufficient when the working tree contains the implementation under review.
4. Refuse a final packet when a required frozen document is missing or damaged. Later
   live edits should either be ignored in favor of frozen bytes or require a new release
   freeze; they must never silently change a packet under the old digest.
5. Add plan-risk, task-criterion, verification, decision, dirty-source, and qualification
   source-drift regressions.

### External work after these corrections

1. Obtain reviewer approval of the exact three arm revisions and promote them from draft.
2. Run 12 fixtures across 3 arms with at least 2 paired repetitions: at least 72 model
   executions, with no cross-arm consultation.
3. Adjudicate the held-constant reviewer results and all required cost, correctness,
   false-block, reminder, token, timing, and outage measures.
4. Remove or narrow profile rules unsupported by the comparison.
5. Run and freeze the final full suite against the exact reviewed source.
6. Generate the final pinned packet and obtain fresh independent capable review.

### Fourth-review verdict

The pilot arm drafts are not approved for execution yet. The previous delivery and
packet-presence blockers are fixed, but the baseline arm must be made exact and the
release freeze must bind suite claims and every rendered approval input. The paid pilot
and final human review remain unperformed.

## Fifth independent review checkpoint, 2026-09-09

The fixture-only `current` baseline is now exact and contains no task instructions. The
common experiment procedure is shared through `_procedure.md`. Suite counts are parsed
from the frozen unittest log and must agree with the supplied totals. Plan, task,
verification, decision, and run-state documents used by the final packet are copied into
the content-addressed release and rendered from those frozen bytes. These portions of the
fourth-review corrections are accepted.

The disclosed Git incident is contained. `HEAD` is back at
`74d81691c38763267d238894149d899b8ab48ee1`; the reflog shows stray commit `44eede2`
followed by the reset, and the working-file status retains the expected uncommitted
implementation surface. Unreachable objects were left untouched. This review made no Git
state change.

Independent verification completed:

- `tests/test.sh`: 174 tests, 0 failures in 583.285 seconds.
- Python compilation: passed.
- Shell syntax for all evaluation scripts: passed.
- `git diff --check`: passed.
- Frozen final-packet documents remain byte-stable after live document edits.
- Suite count mismatch, empty log, unsupported log, and stale committed-source cases are
  covered.
- Direct counterexample: renaming an untracked file while keeping its contents unchanged
  leaves `worktree_identity()` unchanged.

Three code corrections remain before arm approval or pilot execution. This document is
an execution aid only; `improvements/workflow-improvement-plan.md` remains the sole
requirements source.

### Next task 1: make arm approval and revision binding durable

Plan reference: M11.3, especially lines 1335-1341.

`pilot --record` refuses a draft arm only when the environment variable
`DOCKET_ALLOW_DRAFT_ARMS` is absent. Setting that variable to `1` records a draft-arm row
through the production CLI. The test suite uses this path to create complete matrices,
then removes the variable; comparison, metrics, and release do not re-check arm approval.
The bypass therefore produces durable rows that outlive the alleged test seam.

Pilot rows and evidence also carry only the arm name and arbitrary prompt text/digest.
They do not carry the arm version, arm-file digest, procedure version/digest, approval
record, or selected profile-card revisions. Editing or approving an arm after rows exist
cannot invalidate them, and a prompt with unrelated text can claim any arm name.

Required correction:

1. Remove the production environment bypass. Tests must use isolated approved arm
   fixtures or an injected test-only arm root that cannot create release evidence in a
   normal run.
2. Define one deterministic composed-arm record per repetition containing the arm name,
   version, source digest, procedure digest, selected card names and versions, and final
   prompt digest.
3. Require a real reviewer approval artifact for that exact arm revision before record.
   Freeze its digest with the row.
4. At compare, metrics, and release time, revalidate arm approval and every recorded arm,
   procedure, card, and composed-prompt digest. Draft, superseded, moved, or bypassed rows
   must refuse attribution.
5. Add regressions showing an environment variable cannot record draft rows, rows created
   by a test seam cannot enter release, and arm/procedure/card edits invalidate existing
   rows.

### Next task 2: make worktree identity preserve paths and file identity

Plan references: M11.5 and the source-binding requirements throughout sections 6 and 11.

`worktree_identity()` sorts untracked paths but hashes only their contents. It does not
hash each relative path before the file bytes. This aliases distinct source trees.

Independent counterexample:

```text
untracked alpha.txt containing "same bytes": sha256:ea8413f8b982...
rename alpha.txt to beta.txt:               sha256:ea8413f8b982...
identity changed: False
```

Required correction:

1. Hash each untracked relative path, file type, mode, and content as an unambiguous
   length-delimited record.
2. Capture symlink targets as symlinks rather than following them as ordinary files.
3. Use NUL-delimited Git output so quoted, escaped, newline-containing, and rename paths
   cannot be parsed incorrectly.
4. Add rename, same-content-at-different-path, swapped-path, executable-mode, symlink,
   unusual-filename, unreadable-file, staged, and unstaged regressions.

### Next task 3: capture the suite source when the suite executes

Plan reference: M11.5 and the final pinned-packet requirement at lines 1350-1369.

The suite log now supports the reported counts, but the source binding is still assigned
later. `docket release --freeze` computes `worktree_identity()` at freeze time and records
that value as the suite source. The log carries no source identity, start/end time, cwd,
or immutable execution record from the suite invocation. Log modification time relative
to the latest commit does not bind it to uncommitted source and can be changed by copying
or touching an old log.

An old green log can therefore be presented after source edits; release labels the new
tree as the tree the old suite exercised.

Required correction:

1. Add a suite qualification command that captures exact source identity and command
   bytes before execution, runs the suite, captures bounded timestamps, cwd, exit,
   stdout/stderr, and source identity afterward, and refuses source drift during the run.
2. Freeze that structured execution artifact. `release --freeze` must consume it rather
   than associating a caller-supplied log with the current tree.
3. Require the suite, aggregate integration verification, delivery qualification, and
   live reviewed source to identify the same content tree.
4. Remove file-mtime freshness as a source-binding claim. It may remain informational.
5. Add an old-log-after-uncommitted-edit regression, plus command, cwd, start/end,
   before/after source, and output-digest tampering cases.

### External work after the fifth-review corrections

1. Obtain reviewer approval of the exact arm, procedure, and profile-card revisions.
2. Run 12 fixtures across 3 arms with at least 2 paired repetitions: at least 72 blinded
   model executions.
3. Adjudicate the held-constant results and required cost, correctness, false-block,
   reminder, token, timing, and outage measures.
4. Remove or narrow profile rules unsupported by the comparison.
5. Run the captured final qualification suite against the exact reviewed source.
6. Freeze the release, generate its final packet, and obtain fresh independent capable
   review.

### Fifth-review verdict

The exact baseline, parsed suite totals, and frozen rendered documents are accepted. Arm
approval is still bypassable and not revision-bound, the content identity aliases
different untracked paths, and suite evidence is not captured at execution time. Do not
approve the arms or begin the paid pilot until these three corrections pass independent
review.

## Sixth independent review checkpoint, 2026-09-09

The three findings from the fifth review were re-tested against the current working tree.
The production draft-arm bypass is removed, arm definitions and approvals are
revision-bound, worktree identities now preserve untracked paths and file identity, and
`docket suite --qualify` captures source identity at execution time. Those corrections
are accepted. Do not repeat them.

Independent verification completed:

- `tests/test.sh`: 184 tests, 0 failures in 1094.385 seconds.
- Python compilation: passed.
- Shell syntax for all evaluation scripts: passed.
- `git diff --check`: passed.
- `DOCKET_ALLOW_DRAFT_ARMS` has no production effect. Isolated test arm roots still
  require valid approvals, and rows bind arm, procedure, card catalog, approval, and arm
  root digests.
- Untracked renames, executable modes, symlinks, unusual names, staged changes, and
  unstaged changes move `worktree_identity()` as required.
- A structured suite artifact now freezes its command, working directory, timestamps,
  exit, separate stdout and stderr, and before/after source identities.

Three code corrections still block arm approval, pilot execution, and release. This
document remains an execution aid only. Use
`improvements/workflow-improvement-plan.md` as the sole requirements source.

### Next task 1: bind the executed prompt to the approved arm composition

Plan reference: M11.3, especially lines 1335-1341.

The row now binds arm metadata, but it does not prove that the model received the arm it
names. `pilot --record` verifies that the evidence prompt hashes to the caller-supplied
prompt digest, then validates arm, procedure, and selected-card metadata separately. It
never derives the expected prompt bytes from the fixture material, approved arm,
procedure, and selected cards or compares those bytes with `evidence.prompt`.

Independent counterexample using an isolated, validly approved test arm root:

```text
arm: current
evidence prompt: UNRELATED PROMPT THAT DOES NOT CONTAIN THE APPROVED ARM OR PROCEDURE
result: recorded successfully
```

The `contract-cards` path also accepts any nonempty valid subset of the card catalog. It
does not enforce the documented model-card match and whole-word specialty-trigger rule.

Required correction:

1. Define a deterministic prompt composer or a frozen render artifact that combines the
   exact fixture input, approved arm definition, common procedure, and mechanically
   selected cards in one specified byte order.
2. Require `evidence.prompt` to equal those composed bytes, or bind it to an execution
   artifact produced by that composer. A caller-supplied digest of arbitrary text is not
   sufficient.
3. Enforce the model-card match and specialty-card trigger rule mechanically. Record both
   selected and rejected card decisions so the composition is reproducible.
4. Revalidate the composed prompt and all of its component digests during record,
   comparison, metrics, and release.
5. Add negative tests showing unrelated text, a contract prompt labeled `current`, a
   missing required card, an extra untriggered card, reordered or altered components,
   and fixture-input drift all refuse attribution.

### Next task 2: parse the real suite summary from its actual output stream

Plan reference: M11.5 and lines 1350-1369.

`cmd_suite` freezes stdout and stderr separately but calls
`parse_suite_summary(proc.stdout)`. Python's unittest runner writes its test summary to
stderr. The repository's required `tests/test.sh` therefore produces an empty stdout and
a valid `Ran N tests` plus `OK` summary in stderr, so the documented final qualification
command refuses its real green result as unmeasurable.

Independent one-test probe:

```text
stdout bytes: 0
stderr bytes: 98
stderr summary: Ran 1 test ... OK
```

Required correction:

1. Parse the supported summary from the captured stream that contains it while preserving
   stdout and stderr as separate frozen byte artifacts.
2. Specify deterministic handling when both streams contain summaries or contain
   contradictory summaries. Ambiguity and contradiction must refuse.
3. Re-run the same parsing rule in `suite_problems`; it currently re-parses only frozen
   stdout.
4. Add a regression that qualifies a real unittest command whose summary is on stderr,
   plus both-stream, contradictory-stream, and tampered-stderr cases.
5. Before release, run `docket suite RUN --qualify --command tests/test.sh` against the
   exact settled source and prove the artifact validates.

### Next task 3: fail closed when a required source identity is unknown

Plan reference: M11.5 and the exact-source binding requirement at lines 1350-1369.

The new suite artifact permits equal strings such as `unknown (not a git checkout)` for
`source_before` and `source_after`. Release compares a suite or delivery source with the
live tree only when both values start with `sha256:`. Frozen-release validation likewise
checks staleness only for SHA identities. An unknown source can therefore skip the exact
source equality gate, even though the code comments and architecture say unknown trees
never match at binding time.

Required correction:

1. Require a `sha256:` source identity before freezing suite or delivery qualification
   used for release. An unknown identity may remain an honest diagnostic artifact, but it
   must be unqualified and release-ineligible.
2. Require the live release source, suite source, every delivery qualification source,
   and frozen packet validation source to be valid SHA identities and exactly equal.
3. Make frozen-release validation report unknown or malformed source identities even
   when the current source is also unknown.
4. Add suite, delivery, release-freeze, and final-packet regressions for unknown source
   identities. Include equal-unknown and SHA-versus-unknown cases.

### External work after the sixth-review corrections

1. Obtain reviewer approval of the exact arm, procedure, profile-card, and deterministic
   prompt-composition revisions.
2. Run 12 fixtures across 3 arms with at least 2 paired repetitions: at least 72 blinded
   model executions.
3. Adjudicate the held-constant results and required cost, correctness, false-block,
   reminder, token, timing, and outage measures.
4. Remove or narrow profile rules unsupported by the comparison.
5. Run and freeze delivery qualification and the captured full suite against the exact
   reviewed source.
6. Freeze the release, generate its final packet, and obtain fresh independent capable
   review.

### Sixth-review verdict

The fifth-review approval binding, worktree identity, and execution-time suite capture
are substantially resolved. Release is still blocked because the prompt bytes are not
bound to the approved arm composition, the real repository suite cannot be qualified
from unittest's stderr summary, and unknown source identities bypass exact-source
matching. Do not approve the arms or begin the paid pilot until these three corrections
pass independent review.

## Seventh independent review checkpoint, 2026-09-09

The three code blockers from the sixth review are resolved at their stated boundaries.
Pilot rows now reject arbitrary prompt bytes and mechanically rederive selected and
rejected cards. Suite qualification and frozen-release validation use the same separate
stdout/stderr rule, including real unittest summaries on stderr. Suite, delivery,
release-freeze, and final-packet validation now reject unknown source identities and
require exact SHA content-tree equality. Do not repeat those corrections.

Independent verification completed:

- The three new targeted regressions pass independently.
- `tests/test.sh`: 187 tests, 0 failures in 533.712 seconds.
- Python compilation, shell syntax, `git diff --check`, and the prose em-dash scan pass.
- The repository and installed copies of `skills/docket/` match byte-for-byte under
  `diff -qr`.
- Branch and reviewed HEAD remain `main` at
  `74d81691c38763267d238894149d899b8ab48ee1`; the implementation remains uncommitted.

Two corrections remain. The first invalidates the proposed pilot treatment, so arm
approval and the paid pilot must still wait. The second blocks reliable final suite
qualification. This document remains an execution aid only;
`improvements/workflow-improvement-plan.md` is the sole requirements source.

### Next task 1: make the pilot prompt a valid controlled treatment

Plan references: lines 275-310, 619-631, and M11.2-M11.3 at lines 1318-1341.

The new composer binds its output, but the output is not the baseline its own arm defines.
`current.md` promises the fixture-only baseline with no added task instructions, guidance,
or reporting requests. The actual `PILOT-PROMPT-V1` output adds protocol labels, the full
arm document, and `_procedure.md`, including a request to record telemetry. It also places
the held-constant reviewer criterion and the independent oracle directly in the
implementor-visible prompt.

Independent render of `ignored-input/current` contains:

```text
PILOT-PROMPT-V1
reviewer-criterion:check.sh passes on subject/ and fails on subject-broken/
oracle:input-a.txt holds 2 ERR lines (expect 2); input-b.txt holds 0 ERR lines (expect 0)
---arm-file---
... the current-arm definition ...
---procedure---
... Do not consult other arms or runs. Record the model ...
```

This is neither the current prompt named by the plan nor the promised fixture-only input.
For fixtures such as `changed-oracle`, exposing the independent oracle removes the very
independent-derivation failure the pilot is supposed to measure. The composer also has no
supported CLI render or prepare operation. `docket pilot --help` exposes inventory,
record, outage, and comparison actions only; tests obtain the prompt by importing a
private Python function. A blinded runner therefore has no documented command that emits
and freezes the exact bytes later required by `--record`.

Required correction:

1. Define the model-visible task input separately from reviewer-only criteria, independent
   oracle material, and experiment telemetry. Never place reviewer-only oracle answers in
   the implementor treatment.
2. Make the `current` arm reproduce the actual current baseline bytes promised by the
   plan. Shared experiment bookkeeping may be captured by the harness, but it must not
   silently add model-visible instructions to a baseline defined as instruction-free.
3. Make `contract` differ from `current` only by the short common contract, and make
   `contract-cards` differ from `contract` only by the mechanically selected cards.
   Freeze a component manifest that proves those relationships.
4. Add a supported `pilot` render or prepare operation that outputs and freezes the exact
   prompt artifact for a fixture, arm, model, and repetition. The execution record must
   bind to that artifact rather than requiring a runner to import CLI internals.
5. Add tests proving the current treatment contains no reviewer criterion, independent
   oracle, contract, cards, or reporting instructions; treatment deltas contain only the
   intended contract/cards; and the public render-to-record path preserves exact bytes.
6. Update `ARCHITECTURE.md`, `skills/docket/eval/README.md`, and the agent-facing playbook
   after the treatment format is settled. They currently describe the promised baseline,
   not the bytes the composer emits.

### Next task 2: require a complete terminal unittest result

Plan reference: M11.5 at lines 1350-1369.

The stderr-stream correction works for real unittest output, and ambiguous or
contradictory streams refuse. However, `parse_suite_summary` returns a result immediately
after `Ran N tests` even when no final `OK` or `FAILED (...)` line exists. It also accepts
an `OK` followed by later nonblank output despite documenting the status as trailing.

Independent end-to-end counterexample in a disposable Git repository:

```text
command: printf 'Ran 1 test in 0.001s\n'
exit: 0
docket suite result: qualified suite ... exit 0, 1 tests, 0 failures
```

The command writes an artifact and reports success without any unittest verdict.
`suite_problems` later rejects that artifact because the inferred failed state contradicts
the recorded green state, so the release gate remains closed. Qualification itself is
nevertheless false and leaves an unusable artifact that can also make automatic artifact
resolution ambiguous.

Required correction:

1. Make the parser return a result only for one complete `Ran N tests` plus terminal
   `OK` or `FAILED (...)` pair. A bare count is not a test verdict.
2. Define and enforce what may follow the terminal status. Unexpected later output,
   multiple incomplete summaries, and multiple complete summaries must refuse rather than
   selecting one silently.
3. Make `cmd_suite`, `suite_problems`, and frozen-release validation use that identical
   parser and diagnostic.
4. Do not publish an artifact or print `qualified suite` when the summary is incomplete.
5. Add bare-`Ran`, status-with-trailing-noise, multiple-summary, and real
   `tests/test.sh` qualification regressions.

### External work after the seventh-review corrections

1. Obtain reviewer approval of the exact arm, procedure, profile-card, task-input, and
   prompt-render revisions.
2. Run 12 fixtures across 3 arms with at least 2 paired repetitions: at least 72 blinded
   model executions.
3. Adjudicate the held-constant results and required cost, correctness, false-block,
   reminder, token, timing, and outage measures.
4. Remove or narrow profile rules unsupported by the comparison.
5. Run and freeze delivery qualification and the captured full suite against the exact
   reviewed source.
6. Freeze the release, generate its final packet, and obtain fresh independent capable
   review.

### Seventh-review verdict

The three sixth-review implementation defects are fixed, and the installed copy is now
synchronized. The pilot still cannot start because its bound prompt contaminates the
current treatment and exposes reviewer/oracle material, and there is no supported public
render path for the exact required bytes. Final qualification also needs the suite parser
to reject incomplete or nonterminal unittest summaries at capture time.

## Eighth independent review checkpoint, 2026-09-09

The public pilot render operation exists and the V1 protocol labels, arm document,
procedure text, descriptor reviewer criterion, and descriptor oracle string are removed
from the V2 treatment. Contract and card deltas are mechanically composed. The single
stream suite parser now requires one complete terminal unittest verdict and refuses bare
counts, doubled summaries, and output after the verdict. Those portions of the seventh
review are accepted.

Independent verification completed:

- Six targeted V2 render, record, treatment-delta, and suite-parser regressions pass.
- `tests/test.sh`: 191 tests, 0 failures in 667.069 seconds.
- Python compilation, shell syntax, `git diff --check`, and the prose em-dash scan pass.
- The repository and installed skill copies matched before review, and the complete suite
  used an external bytecode cache.
- Branch and reviewed HEAD remain `main` at
  `74d81691c38763267d238894149d899b8ab48ee1`; the implementation remains uncommitted.

Three corrections remain. The first two invalidate pilot inputs and attribution, so arm
approval and paid pilot execution must wait. The third blocks reliable suite
qualification. This document remains an execution aid only;
`improvements/workflow-improvement-plan.md` is the sole requirements source.

### Next task 1: separate the pilot task from its solution and evaluator

Plan references: lines 307-318, 619-631, and M11.2-M11.3 at lines 1318-1341.

`fixture_task_files()` excludes only `docket-eval.json`. Every fixture directory also
contains evaluator and reference material. Eleven fixtures contain both `subject/` and
`subject-broken/`; all twelve include `check.sh` or equivalent evaluator scripts; several
carry separate oracle files. V2 serializes all of these files into the model-visible
`current` treatment.

Independent examples:

```text
changed-oracle current files:
  check.sh
  oracle-baseline.txt
  subject/classify.sh
  subject/oracle.txt
  subject/review-note.txt
  subject-broken/classify.sh
  subject-broken/oracle.txt

circular-parity current files:
  check.sh
  circular-demo.sh
  oracle.txt
  subject/render.sh
  subject-broken/render.sh
```

The prompt therefore gives the model the correct implementation beside the seeded-defect
variant and exposes the checker and independent oracle. A model can copy or compare the
answer instead of demonstrating whether the contract or profile guidance prevents the
target failure. The current regression explicitly asserts that `check.sh` is present and
checks only that descriptor strings are absent, so it does not detect this leak.

Required correction:

1. Give every fixture an explicit model-visible task package. It must contain one
   designated starting workspace and the task inputs needed to act, never the correct
   reference implementation beside the seeded variant.
2. Keep checker scripts, independent oracles, correct/reference subjects, seeded-defect
   labels, reviewer criteria, and adjudication material in a harness-only package. Do not
   infer visibility from broad directory traversal.
3. Have the descriptor declare the task package and evaluator package explicitly, then
   include both package revisions in the render manifest while serializing only the task
   package into treatment bytes.
4. Run the hidden checker against the runner's output or resulting workspace after the
   execution. The evidence record must identify the hidden evaluator revision without
   revealing its bytes in the treatment.
5. Add a dynamic regression over every fixture proving no evaluator, oracle, correct
   reference, paired answer, or hidden filename/content enters any arm, while every
   declared task input does.
6. Re-run the seeded-defect pilot-fixture proofs after repackaging so the hidden evaluator
   still passes the correct result and rejects the defective one.

### Next task 2: bind and preserve the complete render-manifest identity

Plan references: lines 627-631 and M11.3 at lines 1335-1341.

The prompt text and treatment digest are checked, but `pilot_artifact_problems()` does not
validate the render manifest's `run`, `fixture`, `arm`, `model`, `repetition`,
`approval_digest`, `arm_root`, or complete task-input file inventory. The row binds only
the treatment digest as `prompt_artifact`, not the frozen manifest digest. For `current`
and `contract`, treatment bytes are intentionally independent of model and repetition,
so a manifest can be replayed under different controlled dimensions.

Independent end-to-end counterexample:

```text
rendered manifest: model=claude-test, repetition=rep1
copied directory:  current/ignored-input/rep1 -> rep2
record claim:      model=different-model, repetition=rep2
pilot --record:    exit 0, recorded successfully
```

The render location is also mutable: rendering the same arm, fixture, and repetition
again publishes over `prompt.txt` and `manifest.json`, and the path contains no model or
content address. This is a stale-evidence refusal later, but it is not the immutable
render artifact the documentation promises.

Required correction:

1. Validate every identity and component field in the frozen manifest against the record
   request and live approved composition: run, fixture, arm, model, repetition, format,
   approval, arm root, procedure, task package inventory and digest, contract, selected
   and rejected cards, and treatment digest.
2. Bind both the treatment digest and the frozen manifest digest in the execution
   evidence and pilot row. A treatment digest alone cannot distinguish two repetitions
   or model dimensions whose visible bytes are intentionally identical.
3. Publish render artifacts immutably under a content-addressed directory or refuse any
   attempt to overwrite an existing identity with different bytes. Publish the prompt
   and manifest as one complete unit.
4. Revalidate both artifact digests and every manifest identity during record, compare,
   metrics, release freeze, and final-packet validation.
5. Add cross-run, cross-model, cross-repetition, wrong-arm, copied-directory,
   manifest-field tampering, rerender-overwrite, and interrupted-publication regressions.

### Next task 3: refuse malformed summary tokens in either stream

Plan reference: M11.5 at lines 1350-1369.

`parse_suite_summary()` now rejects a bare count or stray verdict in one stream. However,
`parse_suite_streams()` discards that diagnostic whenever the other stream contains a
valid complete summary. A malformed or contradictory summary-shaped line can therefore
coexist with a green selected stream and still qualify.

Independent end-to-end counterexample:

```text
stdout: FAILED (failures=99)
stderr: Ran 1 test in 0.001s ... OK
command exit: 0
docket suite result: qualified suite ... exit 0, 1 tests, 0 failures
```

A bare `Ran 999 tests` or stray `OK` in stdout beside the valid stderr summary is accepted
the same way.

Required correction:

1. Distinguish ordinary non-summary output from malformed summary-shaped output.
2. If either stream contains a count or terminal-status token that does not form its one
   complete terminal summary, refuse even when the other stream has a valid summary.
3. Derive the record's `ok` from both the parsed verdict and process exit. A parsed
   `FAILED` can never become green because a wrapper returned zero.
4. Apply the identical combined-stream rule in capture, artifact validation, and frozen
   release validation.
5. Add valid-stderr plus stray-stdout count, `OK`, and `FAILED` cases, plus a complete
   `FAILED` summary whose wrapper exits zero.

### External work after the eighth-review corrections

1. Obtain reviewer approval of the exact arm, procedure, profile-card, task-package, and
   prompt-render revisions.
2. Run 12 fixtures across 3 arms with at least 2 paired repetitions: at least 72 blinded
   model executions.
3. Adjudicate the held-constant results and required cost, correctness, false-block,
   reminder, token, timing, and outage measures.
4. Remove or narrow profile rules unsupported by the comparison.
5. Run and freeze delivery qualification and the captured full suite against the exact
   reviewed source.
6. Freeze the release, generate its final packet, and obtain fresh independent capable
   review.

### Eighth-review verdict

The V2 public render path and strict single-stream terminal parser are real improvements,
but the pilot still exposes its solutions and evaluator, and its render manifest can be
replayed under a different model or repetition. Suite qualification also accepts malformed
summary tokens in the nonselected stream. Do not approve the arms or begin the paid pilot
until these three corrections pass independent review.

## Ninth implementation checkpoint, 2026-09-10

The three eighth-review blockers are corrected, independently reviewed, and corrected
again. This document remains an execution aid only;
`improvements/workflow-improvement-plan.md` is the sole requirements source.

### What changed

Blocker 1, task and evaluator separation. Every fixture now declares two packages in
`docket-eval.json`: a model-visible `task_package` (root, version, complete file
inventory) and a harness-only `evaluator_package`, plus an `evaluation` block giving the
evaluator command and the verdict pinned for the shipped task package and each evaluator
subject. The twelve fixtures were repackaged into `task/` and `harness/`, with the
seeded-defect workspace shipped unlabeled as the task package and the checker, oracles,
reference implementation, and review note moved behind `harness/`. Only declared paths
are read into a treatment; visibility is never inferred by traversal. Symlinks, escaping
paths, undeclared files, and shared package roots all refuse.

Blocker 2, render identity. `pilot --render` now freezes a manifest over the whole
execution identity and publishes prompt and manifest as one immutable unit under
`.pilot/prompts/<manifest-digest>/`. Evidence and rows bind both the treatment digest and
the frozen manifest digest, and every identity field is revalidated against the request
and the live approved composition at record, compare, metrics, release freeze, and final
packet time.

Blocker 3, combined-stream parser. A count or terminal verdict in either stream that does
not form that stream's own complete terminal summary refuses the whole capture, ordinary
output still passes, and green status now requires the parsed verdict, the failure count,
and the process exit to agree.

### Independent review and its corrections

A fresh reviewer that did not implement the change accepted blockers 2 and 3 as complete
and found three defects in blocker 1 plus two process items. All were corrected:

1. Every rewritten reviewer criterion contained `harness/check.sh`, so the
   `harness-plumbing` card triggered on all twelve fixtures. Card selection now matches
   the declared model-visible obligation alone, which also closes the deeper defect that a
   harness-only descriptor field could move model-visible treatment bytes. The
   `contract-cards` arm document states the narrowed rule and both changed arm drafts were
   version-bumped to 0.3.0-draft.
2. The hidden evaluator's verdict was not bound to any row, so correctness counts remained
   assertions. `pilot --evaluate` now freezes an immutable, content-addressed evaluation
   naming the run, fixture, all three revisions, the evaluator command, the workspace
   content identity, the verdict, and the log. A release-arm row must bind one, its counts
   must agree with the verdict, and one evaluation can never judge two repetitions.
3. Frozen pilot evidence copies are now content-addressed and published immutably.
4. The installed skill copy was stale and shipped the leaky fixture layout; it is synced
   and `diff -r` is clean.
5. The earlier full-suite run overlapped an edit, so it was rerun against a settled tree
   whose digests were pinned before and verified after.

### Verification

- `tests/test.sh`: 199 tests, 0 failures in 883.940 seconds, against
  `bin/docket` sha256 `6a92a614d32c9df95b6e5ed380e32e3bd42b1aa55a76d9379f47105edd4765e6`
  and `tests/test_docket.py` sha256
  `19a4f245273681f1871af2f4e51885e04f84d6ac426b6346a7879ff5cae25c3f`, both unchanged
  after the run.
- Every eighth-review counterexample reproduced against a neutered copy of the current
  CLI and refuses against the shipped one.
- Python compilation, shell syntax, `git diff --check`, and the prose em-dash scan pass.
- Repository and installed copies match.

### What remains unperformed

Arm approval, the 72-execution blinded pilot, adjudication, profile-rule pruning, delivery
qualification, release freeze, and the final independent review are all still outstanding.
Paid pilot execution is not authorized, and this environment has no runner context that
cannot read the harness-only packages, so no pilot row exists and none was fabricated.

## Tenth independent review checkpoint, 2026-09-10

The three eighth-review corrections are materially present. The declared task/evaluator
split keeps the current checker, oracle, and reference files out of declared task bytes;
the render manifest rejects cross-run, cross-arm, cross-model, and cross-repetition replay;
and the combined-stream parser rejects malformed summary material in either stream. Those
parts are accepted.

Independent verification completed:

- Nine targeted fixture, evaluator, render, and suite-parser regressions passed in
  28.064 seconds.
- `tests/test.sh`: 199 tests, 0 failures in 911.648 seconds.
- `bin/docket` remained at sha256
  `6a92a614d32c9df95b6e5ed380e32e3bd42b1aa55a76d9379f47105edd4765e6` and
  `test_docket.py` remained at sha256
  `19a4f245273681f1871af2f4e51885e04f84d6ac426b6346a7879ff5cae25c3f`
  across the full-suite run.
- Python compilation, shell syntax, `git diff --check`, and the prose em-dash scan passed.
- The repository and installed skill copies matched byte for byte.
- One later disposable release probe encountered an honestly reported Herdr
  `disposable-pane` qualification failure. The full suite's earlier real qualification
  passed. This transient result did not create or alter pilot evidence.

Five corrections remain. The first four are release blockers. Do not approve the arms,
start provider-backed pilot executions, adjudicate results, prune profiles, or freeze a
release until a fresh independent review accepts them.

### Next task 1: bind evaluation to the execution's produced workspace

`pilot --evaluate` freezes a verdict over a path supplied by its caller, but the resulting
artifact is not bound to the arm, repetition, render manifest, or execution evidence that
produced that workspace. The execution/review record does not carry a workspace identity,
and `pilot_evaluation_problems` never compares one with the evaluation artifact.

Two independent end-to-end counterexamples were accepted:

```text
counterexample A:
  evaluate: correct harness/reference workspace -> pass
  evidence output: "runner produced an empty workspace and no implementation"
  pilot --record: exit 0, recorded 1 passed and 0 failed

counterexample B:
  evaluate: correct workspace -> pass
  delete evaluated workspace before record
  pilot --record: exit 0, recorded 1 passed and 0 failed
```

The frozen evaluation stores only a live path and a digest. It does not carry the judged
workspace bytes, and validation neither requires the path to remain available nor hashes
it again. `workspace_identity` also hashes only regular-file paths and bytes, omitting
file type and mode. The evaluator runs before that identity is captured, with no
before/after workspace or evaluator revision check, so a mutation during evaluation can
bind a verdict to different bytes than the recorded identity.

Required correction:

1. Freeze the complete produced workspace, including paths, types, modes, symlink targets,
   and bytes, before evaluation under a content address.
2. Run the evaluator against that private frozen snapshot, not the mutable caller path.
3. Bind the evaluation to the run, fixture, arm, repetition, render-manifest digest,
   execution-evidence identity, workspace-snapshot digest, evaluator command, and all live
   fixture/package revisions.
4. Make the execution/review evidence and row bind the same workspace snapshot and
   evaluation identity. A verdict over any other workspace must refuse.
5. Revalidate the frozen snapshot and evaluator inputs at record, compare, metrics,
   release, and final-packet time.
6. Add passing-reference-versus-empty-output, deleted workspace, changed workspace,
   changed mode, symlink, cross-arm, cross-repetition, and mid-evaluation drift tests.

### Next task 2: make the frozen release carry all pilot dependencies

`cmd_release` currently copies `comparison.jsonl`, adjudication, suite files,
qualifications, and workflow documents. It does not copy the per-row execution/review
evidence, hidden-evaluation artifacts or workspace snapshots, rendered prompts and
manifests, arm approvals, profile-card bytes, or frozen task/evaluator package definitions.

`validate_frozen_release` then calls the ordinary live comparison validators, which
resolve those dependencies back through mutable `.pilot/` and the live installed eval
tree. The final packet therefore does not render solely from the frozen release even
though the architecture and evidence invariants promise that behavior.

Required correction:

1. During release freeze, stage every exact pilot dependency consumed by every validated
   row under the release directory and list every file digest in the release manifest.
2. Include execution/review evidence, evaluation records and workspace snapshots, prompt
   and render manifests, arm definitions and approvals, procedure and selected card bytes,
   task/evaluator descriptors and package bytes, and comparison/adjudication records.
3. Validate the frozen comparison using only those frozen copies. Do not resolve live
   `.pilot/`, live arm files, live cards, or live fixtures after the release exists.
4. Preserve the existing source-tree staleness check for the code being released while
   allowing unrelated later edits to live pilot documents to leave the frozen packet
   byte-identical.
5. Add tests that delete or edit every live pilot dependency after release freeze and
   prove the packet remains byte-identical, then damage each frozen counterpart and prove
   the packet refuses with `DAMAGED`.

### Next task 3: remove descriptive fixture identities from model-visible treatments

The task/evaluator file split is real, but the first line of every treatment is still
`fixture: <directory-name>`. Names such as `ignored-input`, `circular-parity`,
`negative-trigger`, and `weak-fingerprint` are seeded-defect labels. They tell the model
which failure the harness is trying to expose, contradicting the documentation that
seeded-defect labels never enter treatment bytes and the report's claim that the defective
workspace is unlabeled.

Required correction:

1. Keep the descriptive fixture identity in the harness-side render manifest only.
2. Omit it from treatment bytes, or replace it with an opaque per-task identity that has
   no mapping available inside the blinded runner context.
3. Dynamically scan every rendered arm for fixture directory names, evaluator subject
   names, and other harness-only labels.
4. Re-prove exact treatment deltas after the change.

### Next task 4: publish the render artifact as one atomic directory

`render_pilot_prompt` calls `publish_immutable` for `prompt.txt` and then separately for
`manifest.json`. It does not stage the whole directory and rename it once. Injecting a
crash at `publish:manifest.json` returned exit 70 and left the final content-addressed
directory visible with:

```text
.manifest.json.<pid>.tmp
prompt.txt
```

Record refuses the partial unit, but the repository invariant is stronger: an evidence
bundle must arrive whole through one directory rename. The current test explicitly accepts
repairing a partial final directory and therefore tests weaker behavior than the invariant.

Required correction:

1. Build prompt, manifest, and their file digests in a sibling staging directory.
2. Flush the files and staging directory, then publish the whole content-addressed
   directory with one atomic rename.
3. Treat an identical existing final directory as an idempotent no-op and refuse any
   differing content at that address.
4. Ignore or safely reconcile abandoned staging directories without treating them as
   evidence.
5. Inject faults before each file, before the rename, and after the rename; before the
   rename no final directory may exist, and after it the unit must be complete.

### Next task 5: make package inventories and revisions truly complete

`fixture_revision`, `actual_package_files`, and the top-level stray-entry check skip names
beginning with `.`. A disposable fixture containing undeclared `task/.hidden-answer` and
`harness/.hidden-oracle` returned no descriptor problem, and changing the hidden answer did
not move the whole-fixture revision. This contradicts the documented complete inventory
and every-defining-file revision.

Required correction:

1. Include every filesystem entry under each package and fixture root, including dotfiles
   and content below hidden directories, or refuse unsupported entry types explicitly.
2. Bind names, types, modes, symlink targets, and file bytes in the appropriate revision.
3. Refuse undeclared hidden files, directories, sockets, devices, and escaping links.
4. Add dotfile, hidden-directory, mode-only, symlink, and special-entry regressions.

### User authorization and pacing constraints

Provider-backed pilot execution remains unauthorized. Ask the user before starting each
batch and state the provider, exact model/version, effort, scenarios, maximum calls or
turns, and expected token or cost budget. One three-arm fixture scenario is atomic: finish
all three arms and both repetitions, six executions, before an ordinary pause. Do not
start a scenario unless its complete budget is approved.

The additional model-role sampling design lives in
`improvements/model-role-combination-pilot-plan.md`. OpenCode may use only Muse Spark 1.3,
primarily as implementor. The user expects to revise the proposed variations later, so do
not launch or finalize that model-role experiment yet.

### Tenth-review verdict

The eighth-review code blockers are substantially improved and all current tests pass,
but the pilot still permits a passing evaluation from an unrelated or vanished workspace,
the frozen release does not carry the evidence behind its comparison, the model sees
descriptive seeded-defect names, render publication violates the whole-directory atomic
invariant, and hidden files evade complete inventories and revisions. Arm approval and the
paid pilot remain blocked pending correction and fresh independent review.

### Post-checkpoint unreviewed partial edit

After the tenth review completed, the shared worktree acquired a partial CLI-only edit at
SHA-256 `073906176098950eefe1c75a5a248063603c86668a5af77133ebfe17f91348a2`.
No test-file change accompanied it, and the installed copy is stale. The edit appears to
address task 3 with an opaque model-visible task identity and task 5 with complete tree
scanning over dotfiles, types, modes, and symlink targets. The CLI compiles and four
existing fixture tests pass, but no full suite or fresh review applies to these bytes.
Tasks 1, 2, and 4 remain visibly unchanged. Preserve and audit this partial work rather
than discarding or assuming it is complete. Use
`improvements/fresh-implementor-prompt-after-tenth-review.md` for the next handoff.
