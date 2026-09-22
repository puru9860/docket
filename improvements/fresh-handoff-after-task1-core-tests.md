# Fresh handoff after Task 1 core tests

Copy the prompt below into a fresh implementation session. It replaces earlier current
state summaries while retaining the tenth-review requirements.

```text
Work in /home/purushottam/Documents/Projects/docket. Finish Task 1 from its passing core,
reach a green checkpoint, then implement Task 2. Stop after deterministic validation.
Do not run any provider-backed model or pilot execution.

Authority

- improvements/workflow-improvement-plan.md is the sole source of product requirements.
- improvements/remaining-work-after-independent-review.md is a review aid. Read the
  "Tenth independent review checkpoint, 2026-09-10", especially Next tasks 1 and 2.
- Read AGENTS.md, ARCHITECTURE.md, README.md, skills/docket/SKILL.md, and
  skills/docket/eval/README.md before changing behavior.
- Read improvements/fresh-implementor-prompt-tasks-1-2-after-recovery.md for the complete
  Task 1 and Task 2 adversarial requirements and safety boundary.
- Read improvements/fresh-continuation-prompt-partial-task1-task2.md only for the history
  of the two Task 1 core defects. Both core defects have now been corrected; do not
  reintroduce them.
- If any review aid conflicts with the workflow plan, follow the workflow plan.

Current exact state

- Branch: main
- HEAD: 74d81691c38763267d238894149d899b8ab48ee1
- Repository skills/docket/bin/docket SHA-256:
  2cd22b93d33cd96092f4753fa05b9f1bc2d1a6f137f5cff6d3874b283dc564c1
- Repository skills/docket/tests/test_docket.py SHA-256:
  83600b9388b332fcdecac9c9958c915dfa9083e89feb5ae4835f1370d9d47d2a
- Installed CLI remains the last fully verified baseline:
  b3c61c4da36ba90186c00a40bcbe1c29dc7af79270d8395f7a8fb4c9a06ff745
- Installed test file remains:
  6808c1c79f40735a84323bdf5065cb949c9c301970baf48f1fb1919ba06c3238
- Repository and installed skill are intentionally out of sync. Do not sync until all
  focused tests, the full suite, documentation, and static checks pass.
- The current repository CLI and test file compile. git diff --check passes.
- reasonix.toml is an untracked file of uncertain origin. Preserve it. Do not treat it as
  a Docket requirement or delete it without separate evidence and authorization.
- Verify all hashes before editing. If the repository hashes differ, report concurrent
  movement instead of overwriting it.

Preservation and test safety

- The repository contains a large uncommitted implementation. Do not reset, clean, stash,
  checkout, recapture, commit, push, or copy the installed skill over the repository.
- Never delete or replace skills/docket, skills/docket/eval, improvements, docs, or any
  repository-root or installed-skill directory.
- Every destructive test operation must resolve its target and assert that it is inside
  that test's own TemporaryDirectory and outside both the repository root and
  ~/.agents/skills/docket. Use resolved path ancestry, not a string prefix.
- Test loss of live dependencies through disposable copies, a scoped dependency-root
  seam, or an access-denial probe. Never delete the real eval or installed skill tree.
- Keep direct Python bytecode outside the repository with PYTHONPYCACHEPREFIX.

Work completed since the previous handoff

Task 1 now has a coherent core:

1. PILOT-EVALUATION-V2 replaces the weak live-path identity.
2. snapshot_record and snapshot_address use one canonical, nonvolatile address over the
   complete workspace inventory. The record excludes timestamps and run names, so the
   same tree converges at one address.
3. capture_workspace and capture_workspace_stable include dotfiles, hidden directories,
   relative paths, entry kinds, modes, symlink targets, and regular-file bytes. They
   refuse escaping links, unsupported special entries, and changing or disappearing
   inputs.
4. publish_workspace_snapshot stages snapshot.json and tree/ together and publishes the
   whole directory with one rename. Existing evidence is revalidated and compared before
   convergence; conflicting or damaged content refuses.
5. materialize_workspace_snapshot rebuilds a private tree from frozen snapshot bytes.
   freeze_fixture_evaluation runs the hidden evaluator against that private tree and
   records the exact evaluator argv.
6. The evidence graph is now acyclic. Immutable execution/review evidence is hashed first;
   evaluation names that digest and the snapshot; --record accepts the evaluation digest
   separately and records both evaluation and snapshot without rewriting execution
   evidence.
7. pilot_evaluation_problems checks run, fixture, arm, repetition, model, prompt manifest,
   execution evidence, workspace snapshot, evaluator inputs, and fixture/package
   revisions. Frozen workspace damage is reported as DAMAGED.

Two new tests pass independently:

- test_pilot_workspace_snapshot_is_one_address_over_bytes_and_record
- test_pilot_evaluation_chain_is_acyclic_end_to_end

Observed result on the exact hashes above:

  Ran 2 tests in 1.854s
  OK

The snapshot test intentionally exercises damaged snapshots, so expected refusal messages
appear on stderr while the test remains green.

Known red compatibility point

A focused run of the two new tests plus four preserved Tasks 3-5 tests produced:

  Ran 6 tests in 10.631s
  FAILED (errors=1)

Five tests passed. The one error was
test_pilot_render_artifacts_are_immutable_and_complete. Its helper
pilot_record_attempt still invokes evaluate_workspace(fixture) using the old interface,
while evaluate_workspace now requires arm, repetition, model, and evidence. The traceback
points to skills/docket/tests/test_docket.py around the pilot_record_attempt helper.

This is only the first known stale caller. Audit every evaluate_workspace call and every
construction of execution evidence. Do not paper over failures with defaults that weaken
the exact execution binding.

Immediate checkpoint: finish Task 1 before Task 2

1. Update the shared pilot test helpers for the final acyclic public interface:
   - Freeze the execution/review evidence once before evaluation.
   - Pass exact arm, fixture, repetition, model, render manifest, evidence, and produced
     workspace to evaluation.
   - Pass the resulting evaluation digest separately to --record.
   - Never rewrite the execution/review evidence to insert evaluation_digest or
     workspace_snapshot.
2. Audit every --evaluate and --record call, documentation example, error message, parser
   help string, row validator, comparison path, metrics path, release path, and final
   packet path for the old embedded-evaluation schema.
3. Run all existing pilot tests after helper migration. Preserve custom-arm behavior if
   the plan requires it; release arms must remain fail closed.
4. Complete the Task 1 adversarial regressions required by the tenth review:
   - Passing reference evaluation attached to evidence claiming an empty or failed runner
     output must refuse.
   - Editing or deleting the live workspace after evaluation must not detach the verdict
     from its frozen snapshot. Missing frozen bytes must report DAMAGED.
   - Refuse cross-run, cross-arm, cross-repetition, cross-model, cross-render,
     cross-evidence, cross-evaluation, and cross-workspace reuse.
   - Cover regular-file changes, mode-only changes, internal symlinks and retargeting,
     escaping symlinks, dotfiles, hidden directories, unsupported special entries,
     capture-time mutation, and evaluation-time mutation.
   - Damage snapshot.json, file bytes, modes, links, and inventory and require DAMAGED.
   - Prove evaluator argv points at a private reconstruction and never the caller path.
   - Exercise concurrent identical publication and a race with conflicting or damaged
     content. Convergence is allowed only after complete validation.
5. Revalidate the full artifact chain through record, row validation, compare, metrics,
   release freeze, release validation, and final-packet validation.
6. Update Task 1 documentation only after behavior and tests settle.

Task 1 gate before starting Task 2:

- Run the two new Task 1 core tests.
- Run every added Task 1 adversarial regression.
- Run all existing pilot record, render, compare, metrics, release, and final-packet tests.
- Run the preserved Tasks 3-5 tests:
  - test_pilot_render_artifacts_are_immutable_and_complete
  - test_pilot_render_fault_after_rename_leaves_a_complete_unit
  - test_pilot_treatments_never_carry_a_seeded_defect_label
  - test_pilot_hidden_and_special_entries_cannot_evade_an_inventory
- Record CLI and test-file SHA-256 before and after the checkpoint and its duration.
- Do not start Task 2 until this checkpoint is green. Do not sync the installed copy yet.

Task 2: self-contained frozen release

Task 2 has not started. The current release copies comparison.jsonl, adjudication, suite
evidence, qualifications, and workflow documents, but ordinary validators still resolve
pilot dependencies from mutable .pilot state and live evaluation material.

Implement the complete correction:

1. Freeze every exact dependency consumed by every validated pilot row into the release:
   execution/review evidence; hidden-evaluation records; produced-workspace snapshots;
   rendered prompts and manifests; arm definitions and approvals; shared procedure;
   selected card bytes and catalog metadata; fixture descriptors; complete task and
   evaluator package trees; comparison rows; adjudication; and every other byte consulted
   by validation or final rendering.
2. Record the digest of every frozen file in the release manifest. Preserve names, entry
   kinds, modes, symlink targets, and bytes where tree semantics matter.
3. Publish the complete release directory from a sibling staging directory through one
   atomic rename. Identical retries may converge only after complete validation;
   conflicting content at an address refuses.
4. Add a frozen-release view or equivalent explicit input source. Comparison validation,
   metrics, release validation, and final packet rendering must read exclusively from
   release-carried bytes once a release is frozen. Never silently fall back to live
   .pilot, arms, cards, fixtures, repository eval files, or installed skill files.
5. Preserve source-tree staleness checks for the code exercised by the suite and delivery
   qualifications. Unrelated live pilot edits after freeze must not change the packet.
6. Validate every internal address and cross-link. Missing, reused, mismatched, altered,
   or unknown evidence must fail closed and report DAMAGED precisely.

Required Task 2 regressions:

- Freeze a complete disposable release and render its packet. Inside the disposable test
  root, remove or alter all live results, evidence, evaluations, workspaces, prompts,
  arms, approvals, procedure, cards, fixture descriptors, and task/evaluator packages.
  The packet must remain byte-identical from frozen release bytes.
- Prove the same with live dependency access denied through a scoped seam. Never delete
  repository or installed files.
- Damage one representative of every frozen dependency class and require a precise
  DAMAGED refusal.
- Add an undeclared frozen file, remove a manifest entry, alter a prompt, change a mode,
  retarget a symlink, and reuse an evaluation or snapshot under another row. Each refuses.
- Inject faults before staged release writes, before the directory rename, and after it.
  Before rename the final address is absent; after rename the unit is complete.
- Instrument or deny live reads to prove final validation uses no mutable pilot or eval
  dependency.

Documentation and final validation

- Update ARCHITECTURE.md, skills/docket/eval/README.md, skills/docket/SKILL.md, and any
  affected reference playbook. Keep detailed procedures in references. Use plain hyphens,
  never em dashes.
- Run focused tests for all five tenth-review corrections and adjacent pilot/release
  behavior.
- Run tests/test.sh to 0 failures. The recovered 202-test suite took about 18 minutes;
  allow 20 to 25 minutes for the expanded suite.
- Run Python compilation with an external PYTHONPYCACHEPREFIX, shell syntax checks over
  hooks and all eval shell scripts, git diff --check, and a changed-prose em-dash scan.
- Verify CLI and test-file digests remain unchanged across the full suite.
- Only after everything passes, sync skills/docket to ~/.agents/skills/docket and prove
  diff -r is clean. Never use installation sync as rollback or recovery.
- Leave everything uncommitted and unpushed for a fresh independent review.

Authorization boundaries

- Do not approve or promote pilot arms.
- Do not start provider-backed executions or the three-arm pilot.
- Do not adjudicate, prune profiles, select model-role variations, or set a cost target.
- Do not freeze a real release.
- Do not commit, push, reset, clean, or stash.

Completion report

Report exact behavior and files changed, every new or strengthened regression, the Task 1
checkpoint result, Task 2 focused result, full-suite count and duration, before/after CLI
and test digests, static checks, repository/install status, unresolved risks, and every
unperformed external action. Do not claim independent final verification.
```
