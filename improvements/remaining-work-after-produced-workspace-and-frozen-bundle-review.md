# Remaining work after produced-workspace and frozen-bundle review

This is a review and execution aid, not a product requirements source.
`improvements/workflow-improvement-plan.md` remains the sole source of product
requirements.

Reviewed source:

- `skills/docket/bin/docket` SHA-256
  `f10d2e3e6071b1e4d8316fe890d447db3b37f66a90a66e6476c3a4671944c3d5`
- `skills/docket/tests/test_docket.py` SHA-256
  `e63ad771898dcba2d6c6d1f7389bc578c0733d8ad0cb4d20a2bc4bfdb9b55a5d`
- branch `main`, HEAD `74d81691c38763267d238894149d899b8ab48ee1`

The repository and installed skill matched at review start.
Python compilation, shell syntax, and `git diff --check` passed.
The implementor's saved full-suite log reports 216 tests with zero failures in
2131.887 seconds on the reviewed source digests.

## Resolved: produced-workspace commitment

The original unrelated-workspace counterexample is closed.

`docket pilot RUN --capture --workspace DIR` now publishes a complete
content-addressed snapshot before the execution/review record is authored.
The record must name that snapshot.
`pilot --evaluate` reconstructs and judges the committed snapshot, and an optional
live `--workspace` argument is accepted only when its fresh capture equals the
commitment.
The evaluation, result row, comparison, metrics, release, and final packet revalidate
the same commitment.

Independent focused verification passed both relevant regressions:

```text
Ran 2 tests in 2.549s
OK
```

The tests cover the pre-existing contradictory record with an unrelated passing
workspace and post-evaluation evidence substitution as separate cases.

## Resolved in source: release-local bundle store

Release freeze now copies the pinned aggregate, constituent, and milestone bundle
directories, filtered ledgers, and the run-private Git object store.
The manifest inventory and regular-file digest map cover the carried bundle bytes.
Final validation, final packet rendering, and frozen metrics enter
`frozen_bundle_view`, which redirects bundle resolution and Git reconstruction to the
release-local store.
The regression removes the disposable run's entire live `.pilot` and `.bundles`
trees, denies the repository roots through scoped seams, and requires byte-identical
packet output.

The implementor's saved focused result reports:

```text
Ran 5 tests in 630.470s
OK
```

Its saved real Herdr 0.8.2 qualification artifact records all ten checks as passed,
including the disposable pane lifecycle.

Two independent attempts to rerun the release-local test on 2026-09-11 stopped in
shared setup before release construction because real delivery qualification reported
`disposable-pane` failed.
A direct `herdr pane split` and close succeeded between those attempts.
This is a transient adapter qualification limitation, not a failure of a release
assertion, but the release property was not independently re-executed in this review.

## Resolved: empty workspace snapshots validate their complete tree

The prior implementation scanned and compared the frozen `tree/` only when the
declared inventory was nonempty.
The correction now requires `tree/` to exist as a real directory, always scans it, and
always compares the scan with the declared inventory.
It also fixes the latent undefined variable in the scan-error diagnostic.

The strengthened regression covers an injected file, a removed `tree/`, and a
symlinked `tree/`, and then proves the restored empty snapshot reads and reconstructs
normally.
Independent focused verification passed that regression together with the original
unrelated-workspace counterexample:

```text
Ran 2 tests in 1.640s
OK
```

The implementor's broader focused run reports seven tests with zero failures in
14.293 seconds.

## Remaining external work

Do not begin provider-backed pilot execution without the user's explicit approval for
that batch.
No deterministic code blocker found in this review remains open.
The remaining work is arm approval and promotion, the scenario-by-scenario three-arm
pilot, human adjudication, profile pruning, a measured cost target, a real frozen
release, and final reviewer approval.

The acknowledged release-size risk remains: a release currently copies the complete
run-private Git object store, so freeze cost scales with that store rather than only
with the trees pinned by the selected bundles.
