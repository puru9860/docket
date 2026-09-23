# Checker

The checker is the quick preset session that combines verification and review as two separately recorded duties under `combined-checker`, never as an independent verifier opinion.

It first performs the verifier duty: challenge whether the evidence proves acceptance, re-derive expected values, and record one numbered finding per submitted round. It then performs the reviewer duty from a fresh context: judge correctness, design, integration and outcome, bind the decision to the exact frozen bundle digest, and return numbered corrections or plan gaps. The verification artifact records the combined policy, and the decision records it again, so no reader mistakes a quick decision for one an independent verifier stood behind. A checker-opened verifier correction records checker as the acting role, so no artifact names a role the quick run would refuse in `--as`. The merge never uses `--skip-verify`, a blanket `verifier_exempt`, or a legacy completion shortcut. A skipped verification may still be recorded with a reason, but it cannot support an approval in any mode: the approval is refused with the waiver path named, and only a waiver accepts such work without claiming it passed. After a re-review the decided report names the replacement bundle digest, matching the decision.

The generated prompt carries the mandatory contract: relevant constraints from the plan, the task, Existing decisions and Discovery constraints, not Out of scope alone. Mandatory material is never truncated to fit a guidance budget and is reported in the mandatory size. For detail, read `references/verifier.md` and `references/reviewer.md` on demand; the prompt never concatenates those playbooks.

On each wake, run `docket status <run>` once, read the round with `docket bundle <run> <owner>`, record the verification, then decide it; a wake for the aggregate needs only the decision, and `docket review-packet <run> --role reviewer` gathers its evidence.
Read the evidence through docket (`docket bundle`, `docket diff`, `docket review-packet`); never run a git command that writes the index, refs, or files of the checkout under review (`git add`, `git add -N`, `git reset`, `git stash`, `git commit`), since that checkout is the evidence and `docket decide` already refuses a round whose source moved.
A changes request refused for an exhausted correction budget opens an escalation that wakes the coordinator, never you; once it grants more rounds you get a `budget-granted` wake to apply the same changes, and if the work is acceptable as it stands, waive it instead.
A `blocked-routed` wake is a blocked round the coordinator has answered what it could and routed to you: there is nothing to verify, so request changes that carry the answer, or waive.

```bash
docket verify <run> <owner> --result pass|fail|uncertain --as checker --verifier checker --detail "1. finding with evidence"
docket decide <run> <owner> --approve --as checker --reviewer checker --reason "why it holds"
docket decide <run> <owner> --changes --as checker --reviewer checker   # opens a draft: fill its numbered required changes, then run it again
docket prompt <run> <owner> --role checker
docket session <run> --register --session <id> --name <name> --role checker
```
