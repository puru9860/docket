---
profile: harness-plumbing
version: 1
card: true
triggers: harness, pane, session, hook, wake, herdr, tmux, terminal, input
confidence: observed-pattern
---

# Harness and input plumbing

- Confirm the live model label instead of trusting launch flags; record what
  was observed, requested, or is unknown.
- Never type into an uncertain input buffer; queue for a safe boundary or
  explicit pickup instead.
- Re-register the delivery session whenever it restarts; a reused pane with
  an old generation inherits nothing.
- Read restrictions in a scope document request a boundary; only harness or
  OS enforcement provides one.
