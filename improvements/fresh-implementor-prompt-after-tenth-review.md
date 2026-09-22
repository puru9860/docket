# Fresh implementor prompt after the tenth review

Copy the prompt below into a new capable implementation agent.

```text
Work in /home/purushottam/Documents/Projects/docket and finish the remaining Docket
workflow-improvement code through independent-review readiness.

Authority and boundaries

- improvements/workflow-improvement-plan.md is the sole source of product requirements.
- improvements/remaining-work-after-independent-review.md is an execution and review aid.
  Start at "Tenth independent review checkpoint, 2026-09-10". If it conflicts with the
  workflow plan, follow the workflow plan.
- Read AGENTS.md, ARCHITECTURE.md, and README.md before changing behavior. Then read
  skills/docket/SKILL.md and skills/docket/eval/README.md.
- Preserve the existing large uncommitted worktree. Do not reset, stash, discard,
  recapture, commit, or push it. Do not rewrite completed milestone work.
- Do not approve pilot arms, start provider-backed model executions, adjudicate pilot
  results, prune model profiles, freeze a release, or claim final verification.
- The user will revise the separate model-role variation design later. Do not implement
  or launch it now. OpenCode may eventually use only Muse Spark 1.3, primarily as
  implementor, but no model-provider pilot is authorized.

Current observed state

- Branch: main
- HEAD: 74d81691c38763267d238894149d899b8ab48ee1
- Current CLI SHA-256 at handoff:
  073906176098950eefe1c75a5a248063603c86668a5af77133ebfe17f91348a2
- Current test file SHA-256:
  19a4f245273681f1871af2f4e51885e04f84d6ac426b6346a7879ff5cae25c3f
- The last full run, 199 tests and 0 failures in 911.648 seconds, exercised the previous
  CLI digest 6a92a614... and does not qualify the current partial edit.
- The current CLI compiles and four existing fixture-boundary tests pass in 7.820 seconds.
- The repository and installed skill are currently out of sync. The installed copy still
  has the pre-partial-edit CLI. Do not sync until the implementation and tests pass.
- No current process appears to be editing the repository, but recheck status and file
  hashes before writing.

Unreviewed partial work already present

Someone edited skills/docket/bin/docket after the tenth review without changing tests.
Preserve and audit it rather than redoing or deleting it:

1. scan_content_tree and tree_revision now include dotfiles, entry types, modes, and
   symlink targets, and package validation appears to refuse unsupported or undeclared
   entries. This is intended to address tenth-review task 5.
2. compose_task_input now emits an opaque task identity instead of the descriptive
   fixture directory name, with TREATMENT_FORMAT bumped to V4. This is intended to
   address tenth-review task 3.

Neither partial correction has new regression coverage yet, neither has received a full
suite run, and neither is synced to the installed copy. Verify their semantics against
the plan and repository invariants, finish them, add tests, and update documentation.

Required implementation work

1. Finish tenth-review task 1: bind evaluation to the workspace produced by that exact
   execution.
   - Freeze the complete produced workspace before evaluation under a content address,
     including paths, entry types, modes, symlink targets, and file bytes.
   - Publish the workspace snapshot as one immutable directory and evaluate the private
     frozen snapshot rather than the caller's mutable path.
   - Bind the evaluation to run, fixture, arm, repetition, render-manifest digest,
     execution-evidence identity, workspace-snapshot digest, evaluator command, and all
     fixture/package revisions.
   - Bind execution/review evidence and the pilot row to the same snapshot and evaluation.
   - Revalidate the complete chain during record, compare, metrics, release, and final
     packet validation.
   - Capture before/after identities or otherwise make mutation during capture or
     evaluation impossible. A moved or missing dependency reports DAMAGED.

   Reproduce and close both accepted counterexamples:
   a. Evaluate a correct reference workspace, claim the runner produced an empty or
      failing workspace, and attempt to record 1 passed / 0 failed. It must refuse.
   b. Evaluate a correct workspace, delete or change the evaluated workspace before
      record, and attempt to record success. The exact frozen snapshot must remain
      reconstructible and bound, or validation must refuse as DAMAGED. It must never
      accept an unavailable live path as proof.

2. Finish tenth-review task 2: make the frozen release self-contained.
   - Freeze every dependency consumed by every validated pilot row: execution/review
     evidence, evaluation records and workspace snapshots, prompts and render manifests,
     arm definitions and approvals, procedure and selected card bytes, fixture
     descriptors, task/evaluator package bytes, comparison rows, and adjudication.
   - List every frozen file and digest in the release manifest.
   - Validate a frozen comparison and final packet only from frozen release bytes. Do not
     resolve mutable .pilot files, live arms/cards, or live fixture definitions after
     release freeze.
   - Retain the existing released-source staleness checks.
   - Prove that deleting or editing every live pilot dependency after freeze leaves the
     final packet byte-identical, while damaging any frozen counterpart reports DAMAGED.

3. Finish and test the existing partial correction for tenth-review task 3.
   - Descriptive fixture names and other seeded-defect labels must not appear anywhere in
     model-visible treatment bytes for any arm.
   - Keep the descriptive mapping harness-side only. Confirm the opaque ID cannot be
     resolved by anything available to the blinded runner.
   - Add a dynamic test over every fixture and arm, and re-prove exact arm deltas.

4. Finish tenth-review task 4: publish render artifacts as one atomic directory.
   - Build prompt.txt, manifest.json, and their digests in a sibling staging directory.
   - Flush them and publish the complete content-addressed directory with one rename.
   - An identical final directory is an idempotent no-op; differing content at the same
     address refuses.
   - Abandoned staging directories are never evidence.
   - Fault tests before each staged write, before rename, and after rename must prove the
     final directory is either absent or complete. Replace the current test that accepts
     repairing a partially visible final directory.

5. Finish and test the existing partial correction for tenth-review task 5.
   - Complete inventories and revisions include dotfiles and hidden-directory contents.
   - Names, types, modes, symlink targets, and file bytes are bound.
   - Undeclared hidden files/directories, sockets, devices, fifos, and escaping links
     refuse explicitly.
   - Add dotfile, hidden-directory, mode-only, symlink, special-entry, and unreadable-entry
     regressions. Confirm every actual fixture remains valid.

Implementation discipline

- Keep skills/docket/bin/docket stdlib-only and runnable as python3.
- Preserve flat frontmatter and every evidence, authority, locking, and lifecycle
  invariant in AGENTS.md.
- Evidence bundles publish the whole directory in one rename and carry the evidence they
  depend on. Never substitute a live path for frozen bytes.
- Add a meaningful regression for every behavior change. Each tenth-review
  counterexample must fail against the earlier implementation and pass after the fix.
- Use an external PYTHONPYCACHEPREFIX for direct Python commands.
- Update ARCHITECTURE.md, skills/docket/eval/README.md, skills/docket/SKILL.md, and any
  affected playbooks after behavior settles. Use plain hyphens, never em dashes.

Verification before handoff

1. Run focused adversarial tests for all five tasks.
2. Run tests/test.sh to 0 failures. Allow approximately 15 to 20 minutes.
3. Run Python compilation with an external bytecode cache, shell syntax checks over hooks
   and every eval script, git diff --check, and a prose em-dash scan.
4. Verify the CLI and test-file digests did not move during the full suite.
5. Only after everything passes, sync skills/docket to ~/.agents/skills/docket and prove
   diff -r is clean.
6. Leave all work uncommitted and unpushed unless the user separately authorizes it.

Completion report

Report exact files and behavior changed; every new regression name; targeted and full
test results with duration and source digests; repository/install diff status; exact
remaining risks and unperformed external work; and whether anything was committed,
installed, pushed, approved, or executed through a model provider.

Stop after implementation and deterministic validation. Hand the result to a fresh
reviewer using improvements/fresh-reviewer-prompt-after-tenth-review.md. Do not perform
the paid pilot.
```
