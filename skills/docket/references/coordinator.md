# Coordinator

The coordinator is the quick preset session that combines planning and orchestration for work with one clear outcome, established local verification, one writer, and no unresolved requirement or architecture decision.

It owns exactly the planner intent and constraint duties plus deterministic dispatch: plan ownership, assignments, dependencies, recovery, rule-based routing, packet assembly, and explicit batch membership. It never invents technical fixes, changes acceptance, approves disputed work, or waives requirements. The merge never uses `--skip-verify`, a blanket `verifier_exempt`, or a legacy completion shortcut. Every artifact records `combined-checker` on the checker side and the quick mode on this side, and a run that outgrows quick records an escalation request without migrating or recapturing any baseline.

The generated prompt carries the mandatory contract: relevant constraints from the plan, the task, Existing decisions and Discovery constraints, not Out of scope alone. Mandatory material is never truncated to fit a guidance budget and is reported in the mandatory size. For detail, read `references/planner.md` and `references/orchestrator.md` on demand; the prompt never concatenates those playbooks.

Start a run in these commands, without `--help` discovery or template editing:

```bash
docket init R01 --title "Short name" --objective "What done means for the whole run."
docket assign R01 T01 --harness opencode --file src/x.py --verify 'tests/test.sh' --title "Task name" --goal "What must be true when done." --criterion "A mechanically checkable criterion"
docket dispatch R01 T01 --session impl-1 --agent impl-1 --register
docket set-model R01 T01 --actual <model label the harness shows>
docket arm R01 --role coordinator
```

`dispatch` runs the task-intent gate, registers the worker session, and prints the prompt file holding the exact bytes it bound; send that file to the worker.
Start the checker session too (SKILL.md shows the launch and its prompt); it acts on every submission.
Then wait with `docket watch R01 --role coordinator` as the signalling playbook describes for your harness.
Verified work wakes the checker directly, so you are woken only for these:

| Wake | What to do |
| --- | --- |
| `correction-ready` | `docket dispatch R01 T01 --session impl-1 --agent impl-1 --register`, then send the new prompt file to the implementor |
| `blocked` | answer what is yours to answer (a task decision, a sequencing change), then `docket route R01 --kind blocked --owner T01 --note TEXT`: it wakes the checker, which owns the waiver or changes |
| scope collision, handoff ready, stall | sequence the work, or `docket resume R01 T01 --session NEW --register` with the handoff |
| `escalated` | a task used its correction budget: grant rounds that are worth it with `docket escalation R01 T01 --grant 1 --as coordinator --reason TEXT` (the checker is then woken to apply or finish its refused correction), re-plan the task, or leave it for the checker to waive |
| `all:decided` | write the aggregate report, below |
| `unfinished-*` or a correction for the aggregate | run the command the wake names |
| `complete` | the checker accepted the aggregate: tell the user the outcome, record your feedback, run `docket usage R01 --archive`, then `docket disarm R01` |

The aggregate closes the run once every task is decided:

```bash
docket assign R01 orch --executor orchestrator --verify 'tests/test.sh'
docket submit R01 orch --as coordinator
```

Fill every section of `orch-report-01.mdx` between those two commands; the checker then decides the aggregate.
Its approval archives every role's harness transcript with its token usage and wakes you with `complete`.
Then record what docket cost you with `docket feedback R01 --add --role coordinator`, run `docket usage R01 --archive` so the archive includes your own final turns, and `docket disarm R01`.

```bash
docket prompt <run> <owner> --role coordinator
docket session <run> --register --session <id> --name <name> --role coordinator
```
