---
profile: concurrency
version: 1
card: true
triggers: concurren, parallel, race, lock, thread, async, deadlock
confidence: observed-pattern
---

# Concurrency and parallelism

- Exercise zero, one, and multiple callbacks or events per aggregation
  boundary; a single happy path cannot distinguish real aggregation from
  coincidence.
- Name the lock or generation that serializes each shared mutation; two
  writers without one is a race, not a retry path.
- A retry that can run twice must be idempotent; prove the second run is
  harmless, not just unlikely.
