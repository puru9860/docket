# Verification obligations

The renderer selects these duties by the claims in the submitted contract and report.
The `honest-acceptance` duty is universal.
The other duties apply only when a submission makes the claim they bear on.
Selection narrows the expected checks, never what a verifier may discover or report.
Mechanically enforceable parts remain in `docket submit`.

## honest-acceptance

Build a criterion-to-observation map from independently derived expectations; inspect changed tests for false positives.
Run one or two decisive negative controls for highest-risk claims in an isolated copy; do not require a mutation for every branch.
Budget 2-3 minutes of model work plus declared check runtime; record `uncertain` (verifier) or request changes/qualified waiver (quick reviewer) for material gaps.
Do not rerun an identical captured suite.
Keep actual-output provenance separate from expected values; vary inputs when behavior depends on input and state unverified conditions.
For failure/concurrency claims, check each test injects that failure into the component the criterion names and every case starts from the state it claims.
Check sibling sites, preservation of existing behavior with focused regression assertions, and side effects on pre-existing files, records, sessions, and user data.
A happy path does not prove preservation.
Challenge green-but-wrong results with selective boundary and negative checks; use conditional baseline-red checks to establish that a test distinguishes the fix.

## offline-operation

A claim that behavior holds without network access needs a controlled network-disabled execution or an explicit unverified gap in the evidence table.
Logs alone do not establish absence of network use.

## changed-oracle

Every changed fixture, oracle, reference output, or expected value needs a per-value independent derivation and an explicit review of the expectation change.
Never supply asserted output from the same fixture and present it as an independent system comparison.
An oracle edited to match the implementation is a new claim, not a passing test.

## event-aggregation

Exercise zero, one, and multiple callbacks or events per aggregation boundary.
A single happy-path case cannot distinguish real aggregation from a coincidental one-to-one pairing.

## content-fingerprint

Change bytes without changing the path or status shape; the fingerprint must change.
A hash over filenames and status alone cannot see a content edit and must never be presented as change evidence.

## configuration

A configuration claim needs evidence for the declared source and selected value, including a materially different value when selection behavior matters.
Record the configuration input the verification used, and do not infer deployment behavior from an undeclared local default.

## Scope limits

A digest proves identity only; it is not semantic evidence.
Read restrictions in a scope document request a discovery boundary; only harness or OS enforcement provides one, and the report states which one held.
Discovery may reveal another relevant obligation or risk, and the verifier must raise it even when the deterministic selector did not predict it.
