# Remaining work after independent Tasks 1 and 2 review

This is a review and execution aid, not a product requirements source.
`improvements/workflow-improvement-plan.md` remains the sole source of product
requirements.

Reviewed source:

- `skills/docket/bin/docket` SHA-256
  `9da2afc2e9859d208458277e60d9e476805c954447e5af383b132d03e2b53e64`
- `skills/docket/tests/test_docket.py` SHA-256
  `0f391efcf3b784134bb8850e60b34443eea658b02cf2be99184cbc22d43af355`
- branch `main`, HEAD `74d81691c38763267d238894149d899b8ab48ee1`

The repository and installed skill matched at review start. Python compilation and
`git diff --check` passed. The implementor reported `tests/test.sh` passing 215 tests
with zero failures in 1801.795 seconds on these exact source digests. That full run was
not repeated during this review.

## Blocking finding 1: the original produced-workspace counterexample still records

Task 1 now freezes complete workspace snapshots, evaluates private reconstructions, and
binds the evaluation to immutable execution/review evidence bytes. Those are meaningful
improvements. The link still proves only that a caller supplied the evidence and workspace
to the same evaluation command. It does not prove that the workspace was produced by the
execution represented by that evidence.

The new regression `test_pilot_evaluation_refuses_evidence_claiming_an_empty_runner`
does not reproduce the accepted counterexample. It evaluates a passing workspace against
an ordinary evidence record, then changes the record to claim an empty workspace. That
proves post-evaluation evidence substitution is refused.

The original counterexample starts with the contradictory evidence already present:

1. Author execution/review evidence whose output says `runner produced an empty workspace
   and no implementation` and whose review says there is nothing to accept.
2. Supply an unrelated passing reference workspace to `pilot --evaluate` with that same
   evidence.
3. Record 1 passed and 0 failed using the resulting evaluation digest.

On the reviewed source, this produced:

```text
evaluation_verdict pass
record_exit 0
record_stdout recorded arm current on ignored-input rep1: 1 passed, 0 failed
row_written True
```

The disposable reproduction used the public render, evaluate, and record paths and wrote
only under a `TemporaryDirectory`.

Required correction:

1. Add a structured, mechanically checked produced-workspace commitment to the execution
   evidence. Do not infer it by policing free-form output prose.
2. Keep the graph acyclic. A safe sequence is: produce workspace, capture and publish the
   immutable snapshot, author immutable execution/review evidence naming that snapshot,
   evaluate that named snapshot, then record a row naming both frozen artifacts.
3. `pilot --evaluate` must evaluate the snapshot committed by the evidence, not an
   arbitrary additional `--workspace` path that can name unrelated passing bytes.
4. If a capture command accepts a live workspace, it must return a frozen snapshot digest
   before the execution/review evidence is authored. Evaluation should consume that digest
   or re-capture and require exact equality with the evidence commitment.
5. Replace the current regression with the original pre-existing contradiction and prove
   refusal. Retain the post-evaluation substitution test as a separate case.
6. Revalidate the workspace commitment through row, comparison, metrics, release, and
   final-packet validation.

## Blocking finding 2: final release validation and rendering still need live bundles

The release now carries pilot rows, execution/review evidence, evaluations, workspace
snapshots, prompts, arms, approvals, procedure, cards, fixtures, packages, comparison,
and adjudication. Frozen pilot validation correctly accepts explicit release-local roots.

However, `validate_frozen_release` still resolves the aggregate and milestone digests with
live-run functions including `find_bundle_by_digest`, `load_bundle`, `bundle_problems`,
`aggregate_bundle_problems`, and `integration_verification_problems`.
`render_final_packet` likewise reads live aggregate and constituent bundle manifests,
reports, patches, and verification output through `find_bundle_by_digest`, `bundle_dir`,
and `load_bundle`. It also derives workflow metadata from the live run.

This conflicts with the implementation handoff requirement to freeze every byte consulted
to validate or render the final packet and to render exclusively from release-carried
bytes. The function documentation currently says the final packet renders purely from the
release, which is not true for bundles.

Required correction:

1. Copy the pinned aggregate bundle, every pinned constituent bundle, milestone bundles,
   their manifests, reports, patches, baselines, reconstruction artifacts, and integration
   verification evidence into the release, preserving complete content and tree identity.
2. Record every carried bundle entry in the release inventory and regular-file digest map.
3. Resolve bundle and integration validation from a release-local bundle store. Never
   fall back to live `.bundles` after freeze.
4. Render aggregate data, acceptance evidence states, full diff pointers, and integration
   output from those release-local bundle bytes.
5. Preserve the live released-source staleness check required by the plan, while making
   deletion or damage of live `.bundles` irrelevant after a successful release freeze.
6. Add a disposable test that freezes a release, removes the entire live `.bundles` tree,
   denies access to it, and proves the final packet is byte-identical. Damage each frozen
   bundle class and require `DAMAGED`.

## Independent verification limitation: Task 2 tests did not reach release assertions

All six new Task 1 regressions passed in an independent focused run.

The five new Task 2 tests were run together and every one stopped during shared setup at
real `delivery --qualify`, before release construction. The same first Task 2 test failed
when rerun alone. In both cases Herdr reported:

```text
qualification orchestrator: failed
docket: qualification orchestrator failed: disposable-pane
```

Observed results:

```text
Ran 11 tests in 453.697s
FAILED (failures=5)

Ran 1 test in 88.175s
FAILED (failures=1)
```

The shell had `HERDR_ENV=1`, a valid pane ID, and `herdr pane layout` succeeded. This is
the known disposable-pane qualification limitation, not a failure assertion from the new
release implementation. It leaves the five Task 2 properties independently unexercised in
this environment.

Required follow-up:

1. Re-run the Task 2 regressions in an environment where real disposable-pane
   qualification passes, or separate deterministic release-property setup from the one
   dedicated real-adapter qualification test while keeping the release gate itself strict.
2. Do not fabricate a passing qualification or weaken `delivery --qualify`.
3. Capture the exact qualification artifact and focused test output for independent
   review.

## Current verdict

Tasks 1 and 2 are not accepted. The snapshot, acyclic evidence, and frozen pilot work is
substantial and should be preserved, but the original unrelated-workspace counterexample
still succeeds and the final packet still consumes live bundle bytes. Arm approval,
provider-backed pilot execution, adjudication, profile pruning, cost targeting, and real
release freeze remain blocked pending correction and fresh independent review.
