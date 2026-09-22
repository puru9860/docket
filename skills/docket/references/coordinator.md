# Coordinator

The coordinator is the quick preset session that combines planning and orchestration for work with one clear outcome, established local verification, one writer, and no unresolved requirement or architecture decision.

It owns exactly the planner intent and constraint duties plus deterministic dispatch: plan ownership, assignments, dependencies, recovery, rule-based routing, packet assembly, and explicit batch membership. It never invents technical fixes, changes acceptance, approves disputed work, or waives requirements. The merge never uses `--skip-verify`, a blanket `verifier_exempt`, or a legacy completion shortcut. Every artifact records `combined-checker` on the checker side and the quick mode on this side, and a run that outgrows quick records an escalation request without migrating or recapturing any baseline.

The generated prompt carries the mandatory contract: relevant constraints from the plan, the task, Existing decisions and Discovery constraints, not Out of scope alone. Mandatory material is never truncated to fit a guidance budget and is reported in the mandatory size. For detail, read `references/planner.md` and `references/orchestrator.md` on demand; the prompt never concatenates those playbooks.

```bash
docket prompt <run> <owner> --role coordinator
docket session <run> --register --session <id> --name <name> --role coordinator
```
