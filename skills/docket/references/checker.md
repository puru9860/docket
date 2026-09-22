# Checker

The checker is the quick preset session that combines verification and review as two separately recorded duties under `combined-checker`, never as an independent verifier opinion.

It first performs the verifier duty: challenge whether the evidence proves acceptance, re-derive expected values, and record one numbered finding per submitted round. It then performs the reviewer duty from a fresh context: judge correctness, design, integration and outcome, bind the decision to the exact frozen bundle digest, and return numbered corrections or plan gaps. The verification artifact records the combined policy, and the decision records it again, so no reader mistakes a quick decision for one an independent verifier stood behind. A checker-opened verifier correction records checker as the acting role, so no artifact names a role the quick run would refuse in `--as`. The merge never uses `--skip-verify`, a blanket `verifier_exempt`, or a legacy completion shortcut. A skipped verification may still be recorded with a reason, but it cannot support an approval in any mode: the approval is refused with the waiver path named, and only a waiver accepts such work without claiming it passed. After a re-review the decided report names the replacement bundle digest, matching the decision.

The generated prompt carries the mandatory contract: relevant constraints from the plan, the task, Existing decisions and Discovery constraints, not Out of scope alone. Mandatory material is never truncated to fit a guidance budget and is reported in the mandatory size. For detail, read `references/verifier.md` and `references/reviewer.md` on demand; the prompt never concatenates those playbooks.

```bash
docket verify <run> <owner> --result pass|fail|uncertain --as checker --verifier checker
docket decide <run> <owner> --approve --as checker --reviewer checker
docket prompt <run> <owner> --role checker
docket session <run> --register --session <id> --name <name> --role checker
```
