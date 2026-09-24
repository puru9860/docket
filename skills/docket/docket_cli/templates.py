"""Document templates, honouring per-project overrides."""

from __future__ import annotations

from .common import STATE_DIR
from .paths import root


def template(name: str) -> str:
    override = root() / STATE_DIR / "templates" / f"{name}.mdx"
    if override.is_file():
        return override.read_text()
    return TEMPLATES[name]


TEMPLATES = {
    "plan": """---
run: {run}
status: draft
mode: {mode}
topology: {topology}
role_sessions: {role_sessions}
review_policy: {review_policy}
model_policy: run-declared
planner_harness: {harness}
progress_updates: quiet
evidence_mode: {evidence_mode}
provisional_integration: forbidden
workflow: {workflow}
correction_limit: 2
verifier_correction: forbidden
---

# Plan: <title>

## Objective

<!-- TODO: what "done" means for this whole run, in 2-4 sentences -->

## Approach

<!-- TODO: the shape of the solution, and why this shape -->

## Tasks

<!-- Complexity controls capability and review depth. Executor controls ownership.
     A high-complexity task may still use an implementor when the user requests it. -->

| id  | title | complexity | executor | constraints |
| --- | ----- | ---------- | -------- | ----------- |
| T01 |       | low        | implementor |             |

## Risks

## Out of scope
""",
    "task": """---
run: {run}
task: {owner}
owner: {owner}
complexity: {complexity}
executor: {executor}
mode: {mode}
review_policy: {review_policy}
harness: {harness}
requested_model: {model}
actual_model:
model_history:
requested_effort: {effort}
actual_effort:
effort_history:
scope_status: {scope_status}
file_hints: {file_hints}
verify_hint: {verify_hint}
files: {files}
verify: {verify}
verify_timeout: {verify_timeout}
env: {env}
depends_on: {depends_on}
---

# Task {owner}: <title>

## Goal

<!-- TODO: one paragraph. What must be true when this is done. -->

## Acceptance criteria

<!-- Each line becomes a checkbox in the report. Make them mechanically checkable. -->

- [ ] <!-- TODO -->

## Scope

The implementor owns discovery. Before editing, create and submit the discovery
capsule with `docket scope ...`; its accepted `files:` become the change boundary.
`file_hints:` is an optional clue, not supervisor-approved scope. `verify_hint:` is the
assigned verification: the gate always runs it, alongside any command the capsule adds.

## Out of scope

## Starting hints

none

## Existing decisions

none

## Discovery constraints

none
""",
    "scope": """---
run: {run}
task: {owner}
owner: {owner}
status: draft
mode: {mode}
review_policy: {review_policy}
files: {files}
verify: {verify}
---

# Discovery capsule: {owner}

## Discovery summary

<!-- TODO: what you inspected and why this is enough context to implement. -->

## Change surface

<!-- TODO: explain every proposed path in frontmatter `files:`. -->

## Relevant symbols

<!-- TODO: useful paths and symbols for implementation and later recovery. -->

## Verification plan

<!-- TODO: explain the exact frontmatter `verify:` command and what it proves. -->

## Risks and assumptions

<!-- TODO: unresolved discovery uncertainty, or "none". -->
""",
    "report": """---
run: {run}
task: {owner}
owner: {owner}
round: {round}
status: draft
mode: {mode}
review_policy: {review_policy}
harness: {harness}
requested_model: {model}
actual_model:
model_history:
requested_effort: {effort}
actual_effort:
effort_history:
---

# Report: {owner} round {round}

## Summary

<!-- TODO: what you actually did, 2-4 sentences. No plans, only past tense. -->

## Files changed

<!-- TODO: one bullet per change as `path/to/file.py:120` plus a short note. Write "none" if nothing changed. -->

## Acceptance

<!-- Keep every criterion exactly as the task states it. Check a box only when it genuinely passes. -->

- [ ] <!-- TODO -->

## Verification

<!-- TODO: the exact command you ran and its real output. Never claim a result you did not see. -->

## Decisions needed

<!-- Something only the reviewer can decide? List it here and submit with
     `docket submit ... --blocked`. Otherwise write "none". Do not edit status. -->

none

## Notes
""",
    "orchestrator_report": """---
run: {run}
owner: orch
round: {round}
status: draft
mode: {mode}
review_policy: {review_policy}
harness: {harness}
requested_model: {model}
actual_model:
model_history:
requested_effort: {effort}
actual_effort:
effort_history:
verify: {verify}
verify_timeout: {verify_timeout}
---

# Orchestrator report: round {round}

## Executive summary

<!-- TODO: summarize the delivered outcome for the planner. -->

## Task outcomes

<!-- TODO: one row per planned task, including owner, terminal state, and verification. -->

## Changes delivered

<!-- TODO: summarize the task-local changes the planner should review. -->

## Integrated verification

<!-- TODO: exact integrated command and real output. -->

## Exceptions and waivers

none

## Decisions needed

none

## Notes
""",
    "handoff": """---
run: {run}
task: {owner}
owner: {owner}
handoff: {handoff}
report_round: {round}
status: draft
mode: {mode}
review_policy: {review_policy}
harness: {harness}
actual_model: {model}
actual_effort: {effort}
---

# Handoff: {owner} checkpoint {handoff}

## Resume summary

<!-- TODO: current implementation state in 2-4 sentences. -->

## Completed

<!-- TODO: finished behavior and decisions; cite `path:symbol` where useful. -->

## Remaining

<!-- TODO: concrete unfinished items, in dependency order. -->

## Files and symbols

<!-- TODO: every touched or relevant `path:symbol`, plus what changed or remains. -->

## Verification state

<!-- TODO: exact commands already run, real results, and tests not yet run. -->

## Decisions and risks

<!-- TODO: unresolved issues and fragile assumptions, or "none". -->

## Exact next action

<!-- TODO: one precise first action for the replacement implementor. -->
""",
    "decision": """---
run: {run}
task: {owner}
owner: {owner}
round: {round}
verdict: {verdict}
reviewer: {reviewer}
mode: {mode}
review_policy: {review_policy}
evidence_digest:
applied: no
---

# Decision: {owner} round {round}

## Verdict

{verdict}

## Reason

<!-- Why this verdict. Required for a waiver, recommended for an approval. -->

## Required changes

<!-- Numbered, specific, each one independently actionable. Cite `file:line`. -->

## Answers to decisions needed

<!-- Answer everything the report raised under "Decisions needed". -->

## Notes
""",
}
