# Fresh continuation prompt for partial Task 1 and remaining Task 2

Copy this prompt into a fresh implementation session. It supersedes the current-state
section of `fresh-implementor-prompt-tasks-1-2-after-recovery.md`.

```text
Work in /home/purushottam/Documents/Projects/docket. Continue the partial Task 1
implementation, prove it, then implement Task 2. Stop after deterministic validation.
Do not run a model pilot.

Authority and required reading

- improvements/workflow-improvement-plan.md is the sole source of product requirements.
- improvements/remaining-work-after-independent-review.md is a review aid. Read the
  "Tenth independent review checkpoint, 2026-09-10", especially Next tasks 1 and 2.
- Read AGENTS.md, ARCHITECTURE.md, README.md, skills/docket/SKILL.md, and
  skills/docket/eval/README.md before changing behavior.
- Also read improvements/fresh-implementor-prompt-tasks-1-2-after-recovery.md for the
  full counterexample lists, checkpoints, safety boundary, and validation requirements.
  This prompt updates its baseline and identifies two defects in the partial Task 1 code.

Current state

- Branch: main
- HEAD: 74d81691c38763267d238894149d899b8ab48ee1
- Repository CLI SHA-256:
  7f1af7b4e775ba6d07a65fd44b7d6ef3be3a446b5a383df55352861ae951399a
- Test file SHA-256:
  6808c1c79f40735a84323bdf5065cb949c9c301970baf48f1fb1919ba06c3238
- Installed CLI remains the last verified baseline at:
  b3c61c4da36ba90186c00a40bcbe1c29dc7af79270d8395f7a8fb4c9a06ff745
- Repository and installed copies are intentionally out of sync. Do not sync until all
  implementation, focused tests, the full suite, and static checks pass.
- The partial repository CLI and unchanged tests compile, and git diff --check passes.
  No behavioral test validates the partial Task 1 implementation. Existing pilot test
  helpers still call the old --evaluate interface and are expected to fail.
- Task 2 has not started. Tasks 3, 4, and 5 were verified before this partial edit and
  must remain intact.

Preservation and test safety

- Preserve all uncommitted work. Do not reset, clean, stash, checkout, recapture, commit,
  push, or copy the installed skill over the repository.
- Do not delete or replace any repository or installed-skill directory.
- Every destructive test operation must resolve its target and assert that the target is
  inside that test's own TemporaryDirectory and outside both the repository and
  ~/.agents/skills/docket. Never run a deletion regression against live eval files.
- Recheck the hashes above before editing. If they differ, stop and report concurrent
  movement instead of overwriting it.

Partial Task 1 code to audit, not trust

The repository CLI added PILOT-EVALUATION-V2 and these helpers:

- workspace_tree_revision
- snapshot_address
- execution_evidence_identity
- capture_workspace and capture_workspace_stable
- write_workspace_tree
- workspace_snapshot_path and read_workspace_snapshot
- materialize_workspace_snapshot and publish_workspace_snapshot
- pilot_evidence_binding_problems and frozen_evaluation_snapshot

It also changed freeze_fixture_evaluation, pilot_evaluation_problems,
pilot_row_evidence_problems, cmd_pilot, and the --evaluate contract. Preserve useful code,
but treat all of it as unreviewed. Fix the two confirmed first-use defects before updating
the broader test helpers.

Confirmed defect 1: snapshot publication can never succeed

snapshot_address(entries) is the domain-separated digest of the workspace tree revision.
publish_workspace_snapshot then creates freeze_record({...}) containing fields including
run and captured_at and requires record["digest"] == snapshot_address(entries). These are
different payloads and therefore different digests.

A disposable direct probe produced:

  tree address:
  sha256:d59c2e39932e33aaabde3db266d9b9f91d91296ed1305094cd596c040e1d2d66

  snapshot record address:
  sha256:8f0530d37444fafbbf31628080e112a81996c66225ded5b419de6f6b139dd118

and publish_workspace_snapshot exited 1 with "refusing to publish evidence under an
address it does not own".

Use one canonical content-address definition. Volatile metadata such as captured_at must
not make identical workspace bytes publish differently. The final directory name, frozen
manifest address, tree inventory, and carried bytes must agree under an explicitly
documented scheme. Identical concurrent publication must compare the complete staged and
published units before converging; it must never accept an arbitrary directory merely
because the address path exists.

Add a minimal first-use test immediately:

1. Capture and publish a disposable one-file workspace.
2. Read and reconstruct it from frozen bytes.
3. Publish the identical workspace again and prove idempotence.
4. Attempt conflicting content at the same claimed address and prove refusal.
5. Cover an empty workspace as a valid, real content address if the plan permits it.

Run that test before continuing.

Confirmed defect 2: the execution-evidence binding is circular

The new --evaluate command hashes the exact pre-evaluation evidence JSON and stores that
identity in the evaluation. The later --record path reads evaluation_digest and
workspace_snapshot from the evidence JSON. Adding those post-evaluation identifiers to
the JSON changes its exact-byte identity, so the evaluation no longer matches the record.
Leaving them absent gives --record no evaluation to resolve. Under the current schema, a
successful evaluation cannot become a valid recorded row.

Redesign the chain so every artifact is immutable and the graph is acyclic. One acceptable
shape, if consistent with the workflow plan, is:

1. Freeze immutable execution/review evidence first and give it a content digest.
2. Freeze the workspace snapshot independently.
3. Freeze an evaluation that names the execution-evidence digest, snapshot digest,
   render-manifest digest, and exact execution dimensions.
4. Record a row or immutable link record that separately names the already-frozen
   execution evidence and evaluation. Do not edit the execution evidence to insert a
   digest that did not exist when its identity was computed.

The CLI may accept a separate evaluation identifier, discover exactly one evaluation by
the complete execution key, or freeze a separate link artifact. Choose a deterministic,
unambiguous interface supported by the plan. Never compute an "exact bytes" identity by
silently excluding mutable result fields from the same document.

Add a minimal end-to-end test immediately:

1. Render one release-arm execution.
2. Author and freeze its execution/review evidence once.
3. Evaluate the exact produced workspace.
4. Record the row without modifying the frozen execution evidence.
5. Revalidate it through the ordinary row validator.
6. Prove that changing any one artifact or execution dimension refuses.

Run this test plus the snapshot first-use test before expanding Task 1 coverage.

Finish Task 1

- Complete every Task 1 requirement and adversarial regression in
  fresh-implementor-prompt-tasks-1-2-after-recovery.md.
- Update all stale pilot test helpers to the final non-circular public interface.
- Test passing-reference versus empty runner output, deleted and changed live workspace,
  mode and symlink changes, special entries, capture/evaluation drift, frozen damage, and
  all cross-run/arm/repetition/render/evidence/workspace reuse cases.
- Prove the evaluator receives a private reconstruction rather than the caller's path.
- Revalidate the chain in record, compare, metrics, release, and final packet paths.
- Run the Task 1 checkpoint, including the existing Tasks 3-5 focused regressions, and
  record source digests and duration before starting Task 2.

Implement Task 2

- Follow the complete Task 2 requirements and counterexamples in
  fresh-implementor-prompt-tasks-1-2-after-recovery.md.
- Freeze every pilot dependency into one content-addressed release directory and validate
  and render exclusively from those frozen bytes.
- Do not fall back to live .pilot state, arms, cards, fixtures, repository eval files, or
  installed skill files after release freeze.
- Preserve source-tree staleness checks for released code while making unrelated live
  pilot edits irrelevant to the frozen packet.
- Use isolated roots or access-denial seams for live-dependency tests. Never delete shared
  source directories.
- Add representative damage tests for every dependency class and atomic release fault
  tests before and after the one directory rename.

Validation and handoff

1. Run focused Tasks 1-2 tests and all Tasks 3-5 regression tests.
2. Run adjacent record, compare, metrics, release, and final-packet tests.
3. Update ARCHITECTURE.md, skills/docket/eval/README.md, skills/docket/SKILL.md, and any
   affected reference playbook. Use plain hyphens, never em dashes.
4. Run tests/test.sh to 0 failures. Allow 20 to 25 minutes and verify CLI/test hashes are
   unchanged across the run.
5. Run Python compilation with an external PYTHONPYCACHEPREFIX, shell syntax checks, git
   diff --check, and a changed-prose em-dash scan.
6. Only after everything passes, sync skills/docket to ~/.agents/skills/docket and prove
   diff -r is clean.
7. Leave all work uncommitted and unpushed and hand it to a fresh independent reviewer.

Do not approve arms, run provider-backed models, execute the three-arm pilot, adjudicate,
prune profiles, choose model-role variations, set a cost target, or freeze a real release.
Report exact changes, tests, durations, before/after digests, install synchronization, and
remaining risks. Do not claim final verification.
```
