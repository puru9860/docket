# Fresh reviewer prompt after the tenth review

Use this only after the implementor reports that all five tenth-review tasks are fixed.
The reviewer must use a fresh context and must not be the implementing agent.

```text
Independently review the current Docket implementation in
/home/purushottam/Documents/Projects/docket. Do not rely on the implementor's completion
claim and do not begin any provider-backed pilot execution.

Authority and setup

- improvements/workflow-improvement-plan.md is the sole source of product requirements.
- Read AGENTS.md, ARCHITECTURE.md, README.md, the workflow plan, and the "Tenth independent
  review checkpoint, 2026-09-10" in
  improvements/remaining-work-after-independent-review.md.
- Read the implementor's final report and inspect the actual uncommitted diff.
- Preserve the worktree. Do not reset, stash, discard, recapture, commit, or push it.
- Do not approve pilot arms, run models, adjudicate, prune profiles, or freeze a real
  release. This is an independent code and evidence review only.

Review the five tenth-review corrections adversarially

1. Evaluation provenance and snapshot
   - Confirm evaluation runs against an immutable snapshot containing every path, type,
     mode, symlink target, and byte it claims.
   - Confirm the snapshot, evaluation, render identity, execution evidence, arm,
     repetition, and row form one exact chain.
   - Try attaching a passing reference evaluation to a failed or empty runner output.
   - Try cross-arm, cross-model, cross-repetition, and cross-run reuse.
   - Delete, mutate, chmod, and retarget the live workspace after capture.
   - Inject mutation during snapshot and evaluation. A mixed identity must never pass.
   - Damage or remove the frozen snapshot and require DAMAGED.

2. Self-contained release
   - Freeze a disposable complete release, render its final packet, then delete or edit
     all live .pilot evidence, evaluations, prompts, arms, cards, and fixture files.
   - The packet must remain byte-identical using only frozen release bytes.
   - Damage each class of frozen pilot dependency and require a precise DAMAGED refusal.
   - Confirm the release directory itself arrives atomically and contains a manifest
     digest for every file.

3. Treatment blinding
   - Render all three arms for every fixture.
   - Search the actual treatment bytes for descriptive fixture directory names,
     evaluator paths, oracle/reference content, seeded-defect labels, reviewer criteria,
     arm documents, and procedure text.
   - Confirm only the declared task package and obligation are present before controlled
     contract/card deltas, and confirm the opaque task ID cannot be resolved from runner
     inputs.

4. Atomic render publication
   - Inject faults before each staged file, before directory rename, and after rename.
   - Before rename, the final content-addressed directory must not exist. After rename,
     it must contain the complete prompt and manifest with verified digests.
   - Race concurrent identical renders and conflicting/tampered content. Identical work
     converges; differing content never overwrites evidence.

5. Complete fixture identities
   - Add undeclared dotfiles and hidden directories under both packages and at fixture
     root; they must be inventoried or refused and must move the right revision.
   - Test mode-only changes, symlink retargeting, escaping links, sockets, fifos, devices,
     unreadable entries, and empty undeclared directories.
   - Confirm all twelve shipped fixtures still prove their pinned pass/fail expectations.

Also inspect for adjacent regressions, especially mutable live-path fallback, incomplete
release manifests, silent skipping of malformed pilot rows, non-atomic append/publication,
and validation behavior that differs between record, compare, metrics, release, and final
packet paths.

Validation

- Run focused counterexamples independently rather than only invoking the implementor's
  new tests.
- Run tests/test.sh to 0 failures and record duration plus CLI/test-file SHA-256 before
  and after.
- Run Python compilation with an external PYTHONPYCACHEPREFIX, shell syntax checks,
  git diff --check, and a prose em-dash scan.
- Verify the repository and ~/.agents/skills/docket copies match byte for byte.

Output a numbered severity-ranked finding list with exact commands, artifacts, and code
locations. Explicitly accept or reject each of the five corrections. If no blocker remains,
say only that the code is ready for the user's separate arm-approval decision. Do not call
the code finally verified on the user's behalf, and do not start the pilot.
```
