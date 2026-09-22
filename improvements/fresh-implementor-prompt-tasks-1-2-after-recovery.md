# Fresh implementor prompt for Tasks 1 and 2 after recovery

Copy the prompt below into a fresh, capable implementation session.

```text
Work in /home/purushottam/Documents/Projects/docket. Implement only the two remaining
tenth-review blockers, then stop after deterministic validation. Do not run a model pilot.

Authority

- improvements/workflow-improvement-plan.md is the sole source of product requirements.
- improvements/remaining-work-after-independent-review.md is a review and execution aid.
  Read "Tenth independent review checkpoint, 2026-09-10", especially Next task 1 and
  Next task 2. If it conflicts with the workflow plan, follow the workflow plan.
- Read the repository AGENTS.md, ARCHITECTURE.md, and README.md before changing behavior.
  Then read skills/docket/SKILL.md and skills/docket/eval/README.md.
- Preserve every invariant in AGENTS.md, especially immutable evidence, whole-directory
  publication, frozen bytes rather than mutable paths, and fail-closed damage reporting.

Current recovered baseline

- Branch: main
- HEAD: 74d81691c38763267d238894149d899b8ab48ee1
- skills/docket/bin/docket SHA-256:
  b3c61c4da36ba90186c00a40bcbe1c29dc7af79270d8395f7a8fb4c9a06ff745
- skills/docket/tests/test_docket.py SHA-256:
  6808c1c79f40735a84323bdf5065cb949c9c301970baf48f1fb1919ba06c3238
- The previous implementor reported tests/test.sh passing 202 tests with 0 failures in
  1097.744 seconds with those exact digests stable across the run.
- A separate check confirmed Python compilation, git diff --check, a clean prose em-dash
  scan, and byte-for-byte equality between skills/docket and
  ~/.agents/skills/docket. Four focused Tasks 3-5 tests independently passed in 11.204
  seconds.
- Verify these hashes and synchronization before editing. If they differ, stop and report
  the mismatch instead of overwriting concurrent work.

Completed work that must remain intact

- Task 3: PILOT-TREATMENT-V4 uses an opaque task identity. Every fixture and arm is
  dynamically checked for seeded-defect and harness-only labels.
- Task 4: prompt.txt and manifest.json publish as one immutable content-addressed
  directory through a single rename, with pre- and post-rename fault coverage.
- Task 5: package and fixture identities include dotfiles, hidden directories, entry
  types, modes, symlink targets, and file bytes, and unsupported entries fail closed.
- Do not redesign these corrections. Keep their tests green while implementing Tasks 1
  and 2.

Safety boundary after the recovery incident

- The worktree contains a large body of uncommitted project work. Do not reset, clean,
  stash, checkout, recapture, commit, or push it. Do not copy the installed skill over the
  repository. Do not delete or replace skills/docket, skills/docket/eval, improvements,
  docs, or any repository-root directory.
- Every destructive test operation must target a directory created by that test through
  tempfile.TemporaryDirectory or an equivalent disposable root.
- Immediately before unlink, rmtree, rename-overwrite, or recursive mutation in a test,
  resolve the target and assert mechanically that it is inside that test's disposable
  root and outside both the repository root and ~/.agents/skills/docket. A string-prefix
  check is insufficient; use resolved path ancestry.
- Never test live-dependency deletion against the repository's real skills/docket/eval or
  installed skill. Copy the required inputs into the test's disposable environment or add
  a narrowly scoped dependency-root seam that preserves production behavior.
- Do not run the full suite while source files are moving. Record CLI and test digests
  before and after every long run.

Task 1: bind evaluation to the exact execution-produced workspace

The current PILOT-EVALUATION-V1 stores a caller-supplied live workspace path and a weak
workspace_identity. It does not carry the judged bytes or bind the verdict to the exact
arm, repetition, render manifest, and execution/review evidence. A passing evaluation can
therefore be attached to evidence claiming that the runner produced an empty or failing
workspace, and deletion of the evaluated live directory does not invalidate the claim.

Implement the complete correction:

1. Freeze the produced workspace before evaluation as one content-addressed immutable
   directory. Bind every relative path, entry kind, mode, symlink target, and regular-file
   byte. Refuse unsupported entries and escaping links.
2. Capture without a mixed view. Detect source mutation during capture, or make the
   capture operate over a stable private representation. A disappearing entry must fail;
   never emit an unavailable placeholder into otherwise valid evidence.
3. Run the hidden evaluator against a new private materialization reconstructed solely
   from the frozen snapshot. Do not evaluate the caller's mutable live path.
4. Bind the evaluation record to run, fixture, arm, repetition, render-manifest digest,
   execution/review evidence identity, workspace-snapshot digest, exact evaluator argv,
   fixture revision, task-package revision, and evaluator-package revision.
5. Bind the execution/review evidence and pilot row to that same snapshot and evaluation.
   Define and enforce one canonical execution-evidence identity over the exact frozen
   evidence bytes. A verdict from another run, arm, repetition, render, evidence record,
   workspace, or evaluator must refuse.
6. Revalidate the complete chain during record, compare, metrics, release freeze, frozen
   release validation, and final-packet validation. Missing or altered frozen bytes must
   report DAMAGED precisely.
7. Keep content addresses immutable. Publish each snapshot as a whole directory through
   one rename. Identical concurrent publication may converge; conflicting content at an
   existing address must refuse.

Required Task 1 counterexamples and regressions:

- Evaluate a correct reference workspace, then submit evidence saying the runner produced
  an empty or failing workspace. Recording 1 passed and 0 failed must refuse.
- Evaluate the correct workspace, then edit or delete the live workspace before record.
  The exact frozen snapshot must remain reconstructible and bound, or validation must
  report DAMAGED. A vanished live path must never serve as proof.
- Refuse cross-run, cross-arm, cross-repetition, cross-render, cross-evidence, and
  cross-workspace reuse.
- Cover a changed regular file, mode-only change, internal symlink and retargeting,
  escaping symlink, dotfile, hidden directory, unsupported special entry, and mutation
  during capture or evaluation.
- Damage the frozen manifest and each carried file class and require DAMAGED.
- Prove the evaluator actually receives the private reconstructed path rather than the
  caller's path.

Checkpoint after Task 1:

- Add the focused regressions before beginning Task 2.
- Run every Task 1 regression plus the existing Tasks 3-5 focused tests.
- Record the CLI and test digests and test duration. If any fail, fix them before Task 2.
- Do not sync the installed copy at this checkpoint.

Task 2: make the frozen release carry every pilot dependency

The current release copies comparison.jsonl, adjudication, suite evidence,
qualifications, and workflow documents, but its validator resolves row dependencies from
mutable .pilot state and live installed evaluation material. A final packet therefore
cannot be reconstructed solely from the release directory.

Implement the complete correction:

1. Freeze every dependency consumed by every validated pilot row into the release:
   execution/review evidence; hidden-evaluation records; produced-workspace snapshots;
   rendered prompts and manifests; arm definitions and approval records; shared procedure;
   selected card bytes and catalog metadata; fixture descriptors; complete task and
   evaluator package trees; comparison rows; adjudication; and any other byte consulted
   to validate or render the result.
2. Give every frozen file a digest in the release manifest. Preserve entry kinds, modes,
   symlink targets, and content where tree semantics matter. Publish the complete release
   as one staged directory through one atomic rename.
3. Add an explicit frozen-release view or equivalent mechanism so comparison validation,
   metrics, release validation, and final packet rendering read only release-carried
   bytes. Do not silently fall back to live .pilot, live arms, live cards, live fixtures,
   the repository eval tree, or the installed skill.
4. Keep the existing released-source staleness checks for the code tree exercised by the
   suite and delivery qualifications. Editing unrelated live pilot inputs after freeze
   must not change a packet produced from the frozen release.
5. Validate all internal addresses and cross-links. Missing, reused, mismatched, or
   altered dependencies report DAMAGED. Unknown values remain unqualified.
6. Keep the manifest and rendering deterministic and token-bounded, with risks never
   shortened, as required by the workflow plan.

Required Task 2 counterexamples and regressions:

- Build a complete disposable release and render its final packet. Safely remove or alter
  every live pilot dependency inside the disposable test root: results, execution/review
  evidence, evaluations, workspaces, prompts, arms, approvals, procedure, cards, fixture
  descriptors, and task/evaluator package files. The packet must remain byte-identical.
- Prove the same after the repository or installed evaluation tree is unavailable by using
  an isolated dependency root. Never delete the real repository or installed tree.
- Damage one frozen representative of every dependency class and require a precise
  DAMAGED refusal.
- Add an undeclared frozen file, remove a manifest entry, change a mode, retarget a
  symlink, reuse one evaluation or snapshot under another row, and alter a frozen prompt.
  Each must refuse.
- Inject faults before staged files, before the release rename, and after it. Before the
  rename the final release address must be absent; after it the complete unit must exist.
- Confirm final validation performs no read from live pilot or evaluation roots. Use a
  test seam or access-denial probe rather than deleting shared files.

Checkpoint after Task 2:

- Run all new Tasks 1-2 adversarial tests and all existing Tasks 3-5 regressions.
- Run adjacent pilot record, compare, metrics, release, and final-packet tests.
- Review the diff for live-path fallback and for separate-file publication.
- Update ARCHITECTURE.md, skills/docket/eval/README.md, skills/docket/SKILL.md, and any
  affected reference playbook. Keep detailed procedures in references rather than
  duplicating them in SKILL.md. Use plain hyphens, never em dashes.

Final deterministic validation

1. Run focused regressions for all five tenth-review corrections.
2. Run tests/test.sh to 0 failures. The recovered 202-test suite took about 18 minutes;
   allow 20 to 25 minutes after adding the new tests.
3. Run Python compilation with an external PYTHONPYCACHEPREFIX.
4. Run shell syntax checks over hooks and every eval shell script.
5. Run git diff --check and scan changed prose for em dashes.
6. Confirm the CLI and test digests did not change during the full suite.
7. Only after all checks pass, sync skills/docket to ~/.agents/skills/docket and prove
   diff -r is clean. Never use that sync as a recovery or rollback mechanism.

Authorization boundaries

- Do not approve or promote pilot arms.
- Do not start provider-backed model executions or the three-arm pilot.
- Do not adjudicate pilot results, prune model profiles, choose model-role variations,
  set a cost target, or freeze a real release.
- Do not commit, push, reset, clean, or stash.
- Leave the result for a fresh independent reviewer.

Completion report

Report the exact files and behavior changed; every new or materially strengthened test;
Task 1 checkpoint results; Task 2 checkpoint results; full-suite count, failures, duration,
and before/after CLI and test digests; static checks; repository/install equality; all
remaining risks and external work; and whether anything was committed, pushed, approved,
or executed through a model provider.

Do not claim final verification. Stop after implementation and deterministic validation.
```
