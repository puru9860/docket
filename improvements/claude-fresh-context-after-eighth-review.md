# Fresh Claude context: finish Docket workflow improvements

Status: superseded after the tenth independent review on 2026-09-10.
Do not implement the three eighth-review blockers from this file again: that work landed
and was reviewed. Continue from `Tenth independent review checkpoint, 2026-09-10` in
`improvements/remaining-work-after-independent-review.md`. The pilot authorization,
scenario pacing, and model-provider constraints below remain active.

You are taking over the unfinished Docket workflow improvement work in:

`/home/purushottam/Documents/Projects/docket`

## Authority and source of truth

`improvements/workflow-improvement-plan.md` is the sole source of product requirements.
Use `improvements/remaining-work-after-independent-review.md`, especially the eighth
checkpoint, only as a review and execution aid. If they differ, follow the workflow plan.

Before changing behavior, read:

1. `AGENTS.md`
2. `ARCHITECTURE.md`, including invariants and known gaps
3. `README.md`
4. `improvements/workflow-improvement-plan.md`
5. `improvements/remaining-work-after-independent-review.md`, starting at
   `Eighth independent review checkpoint, 2026-09-09`
6. `skills/docket/SKILL.md`
7. `skills/docket/eval/README.md`

Do not reset, stash, discard, or recapture the existing worktree. Do not invent historical
milestone evidence. Do not commit or push unless the user separately authorizes it.

## Current state

- Branch: `main`
- Reviewed HEAD: `74d81691c38763267d238894149d899b8ab48ee1`
- The milestone implementation is intentionally uncommitted across the current worktree.
- CLI: `skills/docket/bin/docket`, stdlib-only.
- Installed copy: `/home/purushottam/.agents/skills/docket/`
- Repository and installed skill copies matched after the eighth review.
- Last complete independent run: `tests/test.sh`, 191 tests, 0 failures in 667.069 seconds.
- Static compilation, shell syntax, `git diff --check`, and prose em-dash checks passed.
- Pilot arms remain drafts. No real three-arm pilot has run.

The implementation is substantial and most milestones are complete. Do not rewrite it or
repeat historical fixes. Finish the three blockers below, independently review them, then
continue through the real pilot and release sequence.

## Blocking task 1: separate model-visible tasks from solutions and evaluators

Plan references: lines 307-318, 619-631, and M11.2-M11.3 at lines 1318-1341.

`fixture_task_files()` excludes only `docket-eval.json`. It currently serializes the rest
of each fixture directory into the model-visible treatment. That exposes:

- `check.sh` and other evaluator scripts;
- separate oracle files;
- the correct `subject/` implementation;
- the seeded `subject-broken/` implementation beside it;
- filenames and notes that reveal which version is defective.

Independent examples from the current composer:

```text
changed-oracle:
  check.sh
  oracle-baseline.txt
  subject/classify.sh
  subject/oracle.txt
  subject/review-note.txt
  subject-broken/classify.sh
  subject-broken/oracle.txt

circular-parity:
  check.sh
  circular-demo.sh
  oracle.txt
  subject/render.sh
  subject-broken/render.sh
```

This invalidates the experiment because the model receives the solution and hidden
evaluator. Fix the fixture format so every fixture explicitly declares:

- one model-visible starting task package;
- one harness-only evaluator/reference package;
- the revision of each package;
- how the hidden evaluator runs against the produced result.

Never infer model visibility by traversing the entire fixture directory. The treatment
must contain only the declared task package. It must not expose the correct/reference
implementation, checker, oracle, reviewer criterion, seeded-defect label, or adjudication
material. Preserve the intended arm relationship:

```text
current        = exact model-visible task input
contract       = current + short common contract only
contract-cards = contract + mechanically selected cards only
```

Add a dynamic regression over all fixtures proving every declared visible input is present
and every hidden evaluator/reference asset is absent from all three treatments. Re-run the
fixture oracle proofs after repackaging.

## Blocking task 2: bind and preserve the complete render manifest

Plan references: lines 627-631 and M11.3 at lines 1335-1341.

`pilot_artifact_problems()` does not validate all frozen manifest identity fields. A
manifest rendered as `model=claude-test, repetition=rep1` can be copied to the `rep2` path
and recorded successfully as `model=different-model, repetition=rep2` because current and
contract treatment bytes do not depend on those dimensions.

The independent counterexample returned:

```text
rendered manifest: model=claude-test, repetition=rep1
record claim: model=different-model, repetition=rep2
pilot --record: exit 0, recorded successfully
```

Fix this by:

1. Validating every manifest field against the request and live approved composition:
   run, fixture, arm, model, repetition, format, approval digest, arm root, procedure,
   task package inventory and digest, contract, selected and rejected cards, and treatment
   digest.
2. Binding both treatment digest and frozen manifest digest in execution evidence and the
   pilot row.
3. Publishing prompt plus manifest as one immutable unit under a content address, or
   refusing an overwrite that would change an existing identity.
4. Revalidating both artifacts during record, compare, metrics, release freeze, and final
   packet validation.
5. Adding cross-run, cross-model, cross-repetition, wrong-arm, copied-directory,
   manifest-field tampering, rerender-overwrite, and interrupted-publication tests.

## Blocking task 3: reject malformed summary tokens in either output stream

Plan reference: M11.5 at lines 1350-1369.

The single-stream parser now correctly requires one terminal `Ran N tests` plus
`OK`/`FAILED` pair. `parse_suite_streams()` still ignores a malformed summary in one
stream when the other stream has a valid summary.

This command currently qualifies as green:

```text
stdout: FAILED (failures=99)
stderr: Ran 1 test in 0.001s ... OK
process exit: 0
```

A bare `Ran 999 tests` or stray `OK` in stdout beside a valid stderr summary is also
accepted.

Fix the combined parser so ordinary output remains allowed, but any summary-shaped count
or terminal token in either stream must form that stream's one complete terminal summary.
Malformed or partial summary material in either stream refuses the whole capture. Derive
the record's green status from both the parsed verdict and process exit. Use the identical
rule in capture, artifact validation, release freeze, and final validation.

Add valid-stderr plus stray-stdout `Ran`, `OK`, and `FAILED` tests, and a complete
`FAILED` summary whose wrapper exits zero.

## Required implementation discipline

- Keep `skills/docket/bin/docket` stdlib-only.
- Add meaningful regression tests for every correction. Each counterexample above must
  fail before the fix and pass after it.
- Preserve flat frontmatter and existing evidence, locking, lifecycle, and authority
  invariants.
- Publish evidence atomically and never overwrite immutable evidence.
- Use plain hyphens in prose, never em dashes.
- Update `ARCHITECTURE.md`, `skills/docket/eval/README.md`, `skills/docket/SKILL.md`, and
  relevant playbooks after the behavior is settled.
- Run `tests/test.sh`, Python compilation, shell syntax checks, `git diff --check`, and a
  prose em-dash scan.
- Sync the checked skill into `/home/purushottam/.agents/skills/docket/` and prove
  `diff -r` is clean, following repository instructions and filesystem permissions.

## Review and continuation sequence

Do not approve arms or start paid pilot executions merely because the implementor tests
pass.

## Pilot authorization and pacing

The user has not authorized paid or provider-backed pilot execution. Before starting any
real pilot model invocation, ask the user for explicit approval and state the provider,
model, effort, number of executions in the proposed batch, and expected token or cost
budget. Approval to implement, review, test the CLI, approve arm artifacts, or prepare the
pilot does not authorize real pilot executions. Never infer authorization from the fact
that credentials or providers are available.

The pilot must support incremental execution, but a started fixture scenario is atomic.
For one scenario, render, execute, evaluate, and record that fixture across all three arms
and both paired repetitions, six model executions, before pausing. Do not start a scenario
unless the approved budget can finish all six cells. After the scenario is complete, the
run may stop and resume with another complete scenario after provider limits reset. Each
row remains independently immutable for crash recovery, but an ordinary budget pause must
not deliberately leave a scenario half-run. Do not run ahead of the exact complete-scenario
batch the user approved.

Pausing must not weaken the controlled comparison. On resume, preserve the pinned model
and model version, provider, harness and version, effort, environment digest, reviewer
revision and criterion, fixture revisions, arm approvals, and paired repetition IDs. If a
provider or model version changes while paused, record the interruption and obtain the
user's approval for a new compatible batch or restart the affected comparison cells. Do
not merge mismatched rows into the release comparison.

## Optional model-role screening requested by the user

The user also wants a separate sampled experiment to learn which model and effort
combination works best across Docket's five roles. This is additional evaluation scope,
not a change to the product requirements in the workflow plan. Do not mix it into the
three-arm prompt comparison: first select a prompt treatment with model and effort held
constant, then test role stacks using that treatment so prompt effects and model effects
remain distinguishable.

Follow the detailed proposed design in `improvements/model-role-combination-pilot-plan.md`.
It is an execution aid and user-requested experiment, not a product requirements source.

Provider constraint: OpenCode may use only Muse Spark 1.3, and Muse should be used mainly
as the implementor. Do not spend pilot calls on other free OpenCode models. Pin the exact
Muse provider model ID and version in evidence before launch. Use capable Claude models for
planner and reviewer, with separate contexts, and compare cheaper versus capable Claude
assignments for orchestrator and verifier. Pin their exact provider IDs and versions too.

Use a small sequential screening matrix rather than a full factorial:

1. Hold planner and reviewer at Claude Opus 5, high effort. Hold orchestrator and verifier
   at the selected Claude baseline. Compare Muse Spark 1.3 implementor at medium versus
   high effort.
2. Keep the winning Muse effort fixed. Compare the Claude orchestrator baseline against
   Claude Opus 5 at high effort.
3. Keep the winning orchestrator fixed. Compare the Claude verifier baseline against
   Claude Opus 5 at high effort.
4. Run the two best stacks on additional scenarios before selecting a default. Preserve
   independent reviewer context in every stack.

For screening, select at least one straightforward scenario, one adversarial correctness
scenario, and one recovery/integration scenario. A model-role scenario is complete only
after every stack approved for that stage has run the same task and received the same
hidden evaluation and reviewer criterion. Pause only after that complete scenario. Ask for
separate user approval before each screening stage and state the scenarios, stack matrix,
role/model/effort assignments, maximum model invocations, and expected token or cost
budget. The best stack is chosen from measured final correctness first, then escaped
defects, premium tokens per accepted outcome, review rounds, wall time, and provider
outages. Do not declare a winner from anecdotal runs or incomparable configurations.

## Required continuation order

1. Finish all three blockers and provide exact counterexample results plus full-suite
   results.
2. Give the resulting change to a fresh capable reviewer that did not implement it.
3. If review finds defects, correct and re-review them before arm approval.
4. Have a capable reviewer approve the exact task packages, arm documents, procedure,
   profile cards, approval artifacts, and render format revisions.
5. Use blinded runner contexts that cannot inspect harness-only evaluator/reference
   packages. The orchestrator and implementor that read the oracles are disqualified as
   runners.
6. Run all 12 fixtures across all 3 arms with at least 2 paired repetitions: at least 72
   real model executions, holding model, provider, harness, effort, environment, task
   difficulty, and reviewer instructions constant.
7. Record provider outages separately. Freeze every execution, review, prompt, manifest,
   task-package, and evaluator revision through Docket.
8. Adjudicate correctness, seeded defects, review rounds, prompt size, tokens, cost, wall
   time, false blocks, reminders, and duplicate/delayed notifications. Do not invent
   unknown values or set a cost target before measuring the baseline.
9. Remove or narrow profile rules unsupported by the comparison.
10. Run real delivery qualification and the captured full suite against the exact settled
    source.
11. Freeze the release, generate the final pinned review packet, and obtain a fresh
    independent capable review.

If the environment cannot perform a real model run, hidden evaluation, disposable harness
qualification, or fresh independent review, stop at that exact boundary and report it as
unperformed. Never fabricate pilot rows, approvals, qualification, or release evidence.

## Completion report

Report:

- files and behavior changed;
- the exact task/evaluator package contract;
- the immutable render identity and replay protections;
- combined-stream parser behavior;
- new regression names and full test result;
- repository/installed-copy diff result;
- real pilot matrix and adjudicated measures, if actually executed;
- remaining unknowns, risks, blocked capabilities, and decisions;
- whether anything was committed, installed, or pushed.
