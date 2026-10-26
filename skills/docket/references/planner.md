# Planner

Own objective, constraints, tasks, milestones, and intent amendments.
The implementor discovers and implements; only the independent reviewer approves or waives.
Use a fresh planner session for each run.
Before writing the plan, read the code the request touches and follow the repository's own context instructions (AGENTS.md, CLAUDE.md, or the project-context tools they name).
Carry the standing constraints they give into risks and out of scope.

## Size the work

Default to one milestone for the whole change, delivering one demonstrable capability.
Split only for a real intermediate approval checkpoint: later work builds on a risky foundation that must be approved first, parts are independently shippable, or the work exceeds one implementor's working context.
Each task is one checkable behavior with meaningful tests that fits one working context.
Prefer fewer, larger tasks with shared discovery or inseparable invariants.
State the failure model and excluded failure classes without contradicting acceptance.
Assign focused per-task verification; reserve the full suite for the milestone command.
Name real-system boundary observations when mocks cannot prove a criterion.

## Apply the complete plan

Write one stdlib JSON file, for example `plan.json`:

```json
{
  "run": {
    "title": "Validate saved settings",
    "objective": "Saved settings reject invalid input and preserve valid values.",
    "approach": "Validate at the storage boundary and test read-back.",
    "evidence_mode": "git"
  },
  "roots": ["app=."],
  "tasks": [
    {
      "owner": "T01",
      "title": "Validate and preserve settings",
      "goal": "Reject invalid settings before writing and preserve existing valid data.",
      "criterion": [
        "Invalid input returns an error and leaves pre-existing saved bytes unchanged.",
        "Valid settings survive save and read-back through the real storage API."
      ],
      "file": ["src/settings.py", "tests/test_settings.py"],
      "verify": "python3 -m unittest tests.test_settings",
      "verify_timeout": 900,
      "risk": "low",
      "complexity": "low",
      "executor": "implementor",
      "env": [],
      "depends_on": [],
      "out_of_scope": ["No migration or automatic repair of existing invalid data."],
      "decision": ["Durability after a machine power loss is excluded."],
      "no_default_criteria": false
    }
  ],
  "milestones": [
    {
      "id": "M1",
      "members": ["T01"],
      "command": "tests/test.sh",
      "reviewer_context": "continue",
      "continuity_reason": "Keep milestone knowledge through corrections."
    }
  ]
}
```

```bash
docket plan apply R01 --file plan.json
docket launch R01
```

Apply validates through init, roots, assign, batch creation, and close before publishing the complete run.
Any refusal leaves no run; an existing run refuses instead of duplicating work.
Launch opens the plain conductor in its own Herdr pane, arms your planner hook, and prints one status.
Start after the user has approved the plan; existing task authorization applies.
Quick has no plan-status transition; do not edit lifecycle status to record plan approval.
New quick runs use shared context; existing policies stay recorded, as described in [historical-runs.md](historical-runs.md).
Then end your turn.
The conductor launches workers, runs ready milestone suites, and delivers worker wakes.
Do not monitor panes, poll status, or relay healthy progress.

Run fields accept init options with underscores replacing hyphens, plus `risks` and `out_of_scope` (a string or a list of strings), and flat `policy` settings such as `premium_models`, `token_budget`, `review_reserve`, and `correction_limit`.
Task `risk` and `complexity` are `low` or `high`; a refusal names the plan path, such as `tasks[0].risk`.
Roots are explicit `alias=path` strings; a checkout uses `evidence_mode: git`.
Omitting roots declares only the enclosing checkout; name nested repositories and linked worktrees explicitly.
Never commit, stash, or recapture user work to clean a baseline.
Default review criteria cover sibling sites, regressions, and side effects; `no_default_criteria` corresponds to `--no-default-criteria` and is appropriate only when those checks do not apply.
Tasks accept assign fields: `harness`, `model`, and `effort` are optional requests in addition to the fields shown.
Milestones also accept `depends_on` edges such as `["T02:T01"]`, `implementor_harness`, `implementor_model`, and corresponding `reviewer`, `planner`, and `test_author` fields.
Each assignment override needs `assignment_reason` and must respect user allow-lists and pins.
All tasks inherit their milestone's implementor assignment; omit task model/harness requests unless they agree.
Choose a strong, lower-cost implementor and approved model identifiers from config; Claude is the default worker harness, Codex is first-class, OpenCode is opt-in.
Inspect assignment sources with `docket config R01` only when needed.
Reviewer continuity defaults to `continue`; choose `fresh` with a `continuity_reason` only for independent checkpoints.
Cross-milestone dependencies require intact approval; within a milestone verified settled work can satisfy dependencies.

## Answer decisions and finish

A decision wake names the issue and evidence.
Use `docket pickup R01 --role planner` before acting on a decision wake.
Accept or reject intent amendments with `docket amendment R01 --accept ID` or `--reject ID --reason TEXT`.
Edit the contract first when accepting; the reviewer opens any required reverification round.
Grant an exhausted correction budget only after judging the policy question: `docket escalation R01 T01 --grant N --as planner --reason TEXT`.
Send a new intent decision with `docket instruct R01 --role implementor --message TEXT`.
Informational provider warnings appear in status and conductor output; only an actionable exhausted window wakes you.
On a status request, read `docket status R01` once and answer.
For interrupted work or worker limits, read [recovery.md](recovery.md); transport diagnostics live in [conductor.md](conductor.md), manual launch in [launch.md](launch.md).
Keep granular init, roots, assign, and `docket batch` commands for amendments and recovery.

The completion wake carries milestone verdicts, member verdicts, suite commands, results, and evidence digests.
Report the outcome and run one command:

```bash
docket finish R01 --feedback none
```

Replace `none` with what Docket cost you when there was friction.
Finish acknowledges completion, records feedback, archives usage, and disarms the run.
It refuses incomplete or damaged approval evidence.
