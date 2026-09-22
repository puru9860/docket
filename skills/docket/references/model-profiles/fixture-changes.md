---
profile: fixture-changes
version: 1
card: true
triggers: fixture, oracle, snapshot, golden, expected
confidence: observed-pattern
---

# Fixture and oracle changes

- Every changed expected value needs a per-value independent derivation and
  an explicit review note; an oracle edited to match the implementation is a
  new claim, not a passing test.
- Never copy actual output into the expectation file and call that a system
  comparison.
- For a negative test, remove or mutate the relevant behavior in an isolated
  copy and prove the test fails without it.
