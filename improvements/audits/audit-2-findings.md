# Adversarial audit findings, round two

Audit target: `/home/purushottam/Documents/Projects/docket`
Binary audited: `skills/docket/bin/docket`
`sha256:b7509e7b0782a5480ee1d422478cedf0eaf2ed856fbb2e9818c5c8b070603935`

All mutations and CLI runs were under `/tmp/dka2`; the repository and its
`.docket/` were read only. No file in the repository was edited.

## Round-one fix confirmations (all six hold)

1. Attempt-100 numeric ordering: executed `test_t82_ordering_past_ninety_nine_resolves_to_failure` - OK.
2. Quick skip-verify approval refusal: reproduced in `/tmp/dka2/s1` (quick init,
   `--verify false`, `--skip-verify`, checker pass, `decide --approve` exits 1,
   `cannot be approved: its frozen verification was skipped`, no decision file).
3. Dependency cycle ingress refusal: reproduced in `/tmp/dka2/s3` (assign
   self-edge and 2-cycle refused with `nothing published`; batch 2-cycle
   refused, no batch file).
4. Dispatch task-intent gate: reproduced in `/tmp/dka2/s4` (validate-task and
   dispatch both refuse untouched task, no `.dispatch/` record, prompt refuses).
5. Declared-env override + effective capture: reproduced in `/tmp/dka2/s2`
   (ambient `FOO=actual` overridden by declared `FOO=declared`, submit fails
   as declared dictates; passing case freezes `declared_env` and
   `effective_env` as `FOO=declared`, no `UNRELATED` leakage).
6. Re-review report pointer: executed `test_t81_rereview_report_names_replacement_bundle`
   and `test_t81_interrupted_rereview_resumes_without_second_round` - OK.

Additional guards spot-checked by execution: failure routing (`/tmp/dka2/f1`:
fail blocks approval, derives `verification-failed` reviewer event); contract
edit after freeze blocks approval (`/tmp/dka2/c1`); quick end-to-end
approve binds exact bundle with `combined-checker` (`/tmp/dka2/q1`).

## Reproduced findings

### 1. Quick verifier-triggered correction records `reviewer: verifier`, a role that cannot act in a quick run

**Claim:** In `--mode quick`, a checker-opened verifier correction writes a
decision with `reviewer: verifier`, even though `--as verifier` is refused in
quick runs and no verifier session exists there.

**Severity:** Low. Evidence misattribution only; the verdict is non-accepting
(`changes-requested`) and `review_policy: combined-checker` is still recorded.
No accepting verdict is reachable through it.

**Commands** (in `/tmp/dka2/v1`):

```bash
D=/home/purushottam/Documents/Projects/docket/skills/docket/bin/docket
python3 "$D" init demo --mode quick --evidence-mode documents-only
python3 "$D" assign demo T01 --complexity high --executor implementor \
  --harness opencode --file src/t01.py --verify 'printf "T01 ok\n"'
# allow verifier corrections in plan.mdx, fill task/scope/report, scope --submit
python3 "$D" submit demo T01 --as implementor
python3 "$D" verify demo T01 --result fail --as checker --verifier checker \
  --detail 'guard missing at src/t01.py:1' --open-correction
grep -E '^(reviewer|triggered_by):' .docket/runs/demo/T01-decision-01.mdx
python3 "$D" verify demo T01 --result pass --as verifier  # refused
```

**Observed:** Correction opens (`T01 round 1 needs changes (verifier-triggered)`),
decision frontmatter reads `reviewer: verifier`, `triggered_by: verifier`.
The `--as verifier` probe is refused:
`verify-record in a five-role run requires --as checker; got verifier.`
The verification artifact itself correctly records `verifier: checker`.

**Expected:** The decision should name the acting role (`checker`) or the
logical trigger without impersonating a session role that the preset rejects.
`open_verifier_correction` hardcodes `"reviewer": "verifier"`, which is only
true in standard runs.

**File and symbol:** `skills/docket/bin/docket` - `open_verifier_correction`
(`decision_meta` hardcodes `"reviewer": "verifier"`).

**Evidence status:** Reproduced by execution.

## Coverage note

All seven follow-up areas below are now covered; nothing remains pending.

## Follow-up areas, all cleared

### 2. `docket batch --create` accepts dependency edges from non-members and silently drops their enforcement

**Claim:** The member side of `--depends-on MEMBER:DEP` is not validated.
An edge whose source is not a batch member (even an unassigned id) is
accepted, stored, and confirmed, but it is permanently inoperative:
`dispatch_dependencies_unmet` and `batch_ready` only consult edges keyed by
members. A typo on the member side therefore voids the requested ordering
while the CLI reports success. The dep-target side IS validated (must be
planned or assigned), so the two sides disagree.

**Severity:** Medium. No terminal-state bypass (task/batch edges gate
dispatch and readiness events, never verdicts, per ARCHITECTURE.md -
deliberately not reported as a defect). The harm is silent loss of the
dispatch serialization batches exist to provide: dependent work dispatches
against a draft dependency with the batch file claiming the edge exists.

**Commands:** contrast in `/tmp/dka2/b1` (void edge) vs `/tmp/dka2/b2`
(real edge). Both: standard five-role init, assign T01 and T02, create a
batch, fill T02 task/scope, `scope --submit`, register an implementor
session, `dispatch demo T02 --session w2`:

```bash
D=/home/purushottam/Documents/Projects/docket/skills/docket/bin/docket
# b1: non-member source, never assigned
python3 "$D" batch demo --create B1 --members T01,T02 --depends-on T99:T01
# b2: correct edge
python3 "$D" batch demo --create B1 --members T01,T02 --depends-on T02:T01
python3 "$D" dispatch demo T02 --session w2
```

**Observed:** b1: `created batch B1 with members T01,T02`, exit 0;
`.batches/B1.json` stores `"depends_on": {"T99": ["T01"]}`; T02 dispatches
freely (`dispatched T02 round 1`). b2: identical setup with `T02:T01`
refuses dispatch: `T02 cannot dispatch with unmet dependencies: -
T01 (draft) via batch B1`, exit 1.

**Expected:** The edge source should be validated as a batch member the way
the dep target is validated as planned/assigned: refuse with a diagnostic
naming the non-member and publish nothing, matching the fail-closed posture
of the cycle refusal two lines below it.

**File and symbol:** `skills/docket/bin/docket` - `cmd_batch` (depends
parsing; validates `members` assigned and dep targets known, but never that
each edge source is in `members`); contrast `dispatch_dependencies_unmet`
(`if owner in members`) and `batch_ready` (`for member in members`), which
both ignore non-member keys.

**Evidence status:** Reproduced by execution (both sides of the contrast).

## Clean areas (reproduced, no finding)

- **`docket depend --on` vs submitted/decided consumer** (`/tmp/dka2/d1`):
  draft recording succeeds; after submit, re-recording is refused naming
  `--changes` then `depend` then `submit` as recovery; after approval,
  refused with the frozen digest cited unchanged and the bundle untouched.
  No over-refusal (draft path works) and no frozen-evidence mutation.
- **Quick waive over skipped verification** (`/tmp/dka2/s1`, continuing the
  round-one repro state): `decide --waive --reason ... --as checker
  --reviewer checker` exits 0, report `waived`. The skipped-approval refusal
  does not over-block the honest waiver path.
- **Out-of-policy `--model` dispatch** (`/tmp/dka2/n1`,
  `primary_model: m1`, `fallback_models: m2, m3`): `--model rogue` exits 1
  naming the approved list, publishes no `.dispatch/` record, and opens the
  `model-fallback-exhausted` exception; `--model m2` then dispatches
  normally. Refusal happens before any record write.
- **Milestone batch and frontier interplay** (`/tmp/dka2/m1` plus
  `test_p2_frontier_wakes_reviewer_for_stalled_dependency` and
  `test_p2_milestone_and_frontier_agree_on_verification`, both OK):
  milestone batch with both members submitted and passed derives exactly
  one `batch:M1:ready:1` reviewer event with no frontier (suppression for
  the same evidence holds); fail/unverified cases derive no frontier.
- **Suite ANSI edges** (`/tmp/dka2/g1`, `/tmp/dka2/g2`): doubled colorized
  green summary on stderr refuses (no silent pick-one); green stderr plus a
  stray colorized `FAILED (failures=1)` fragment on stdout refuses the whole
  capture; honest colorized red qualifies as red (`exit 1, 2 tests,
  1 failures`), never green. Normalization does not weaken any refusal.
- **Renderer revision staleness** (`/tmp/dka2/r1`): dispatch `prompt_digest`
  equals a fresh `docket prompt` digest for the same role (recomputable);
  `renderer: derived-a679fc20457d` is recorded; editing the task moves the
  digest (stale bindings are detectable) and `submit` re-gates against the
  current task rather than trusting the dispatch digest. Verdicts bind to
  frozen bundles, never to the dispatch digest, so no staleness path reaches
  a decision.
- **Verifier-exempt approval path** (`/tmp/dka2/x1`,
  `verifier_exempt: T01`): approval with a passed submission verification
  but no verifier artifact exits 0. The new verification gates do not
  over-block the explicit-policy exemption.

## Deliberately not reported

- A batch/task dependency does not block `decide --approve` (verified in
  `/tmp/dka2/b2`: T02 approved while T01 draft despite edge `T02:T01`).
  ARCHITECTURE.md promises dependency enforcement at dispatch ("enforced
  before launch") and through milestone/frontier readiness events, never at
  the verdict; the reviewer playbook claims no dependency gate. Consistent
  with the documented design, so not a defect.
- Quick verifier-correction decisions record `reviewer: verifier` while
  `--as verifier` is refused in quick runs (finding 1 above) - kept as the
  single low-severity evidence-label finding from this round.

## Inference-only observations

None. Every claim above was executed in a disposable run.
