# Role and milestone assignments

## Configure defaults

Assignments cover `planner`, `implementor`, `reviewer`, and `test_author`.
They are requests, never proof that a session launched or a requested model executed.
Role assignment configuration records requests; `docket conduct` performs worker launches.

User defaults live at `${XDG_CONFIG_HOME:-~/.config}/docket/config.json`.
Project overrides live at `.docket/config.json` in the project that holds the run.
Both files are flat JSON objects, parsed by the Python standard library on the CLI's supported Python versions.
A missing file is valid; unreadable files, dangling symlinks, invalid JSON, duplicate keys, nested objects, unsupported versions, and unknown keys refuse with the source path.
`version` may be omitted or must equal integer `1`.
Use exact model identifiers rather than aliases that imply a version the harness has not confirmed.

A user config can contain:

```json
{
  "version": 1,
  "planner_harness": "claude",
  "implementor_harness": "claude",
  "implementor_model": "balanced-model",
  "implementor_allowed_models": ["balanced-model", "strong-model"],
  "reviewer_harness": "codex",
  "reviewer_model": "review-model",
  "reviewer_allowed_models": ["review-model"],
  "reviewer_pinned": true,
  "test_author_harness": "codex",
  "test_author_model": "review-model"
}
```

Each role accepts `<role>_harness`, `<role>_model`, `<role>_allowed_models`, and `<role>_pinned` in the user file.
Harnesses are `claude`, `codex`, or explicit opt-in `opencode`.
Models must be nonempty strings on one line; allowed models must be a list of such strings and pins must be JSON booleans.
An omitted allowed list adds no restriction; an empty allowed list permits no model launch.
An omitted or false pin permits planner overrides within the allowed list.
A true pin protects the user's resolved assignment and refuses any project or milestone override, even one requesting the same value.
A model omitted from a pinned role still means the session or harness default; it is not a named model pin.
A project file may override harness and model fields only; it cannot widen allowed lists or remove user pins.
Only the user file accepts `claude_launch_args`, `codex_launch_args`, and `opencode_launch_args`, each a JSON array of nonempty single-line argument strings.
The conductor reads these launch arguments at launch time and appends the milestone's recorded model flag.
Project configuration cannot supply launch arguments, including permission bypass flags.
With no user launch arguments, only the recorded model flag is passed and permission dialogs remain visible for the user.

When nothing is configured, planner and implementor use `claude`, reviewer and test author use `codex`.
No model version is built in and no role is pinned.
An empty recorded model means keep the planner's own session model, or use the worker harness's default until a concrete milestone model is selected.
An explicit legacy `init --harness` supplies the planner harness default before config is applied.
A requested default is never presented as an observed model.

## Resolve and record

Resolution is per field: built-in defaults, then user defaults, then project overrides, then explicit milestone flags.
`docket init` records the base assignment, source labels and paths, allowed lists, and pins in flat `plan.mdx` frontmatter.
Milestone creation copies that recorded base into flat fields on its batch JSON and applies only the requested milestone overrides.
Later config edits cannot change an existing run's recorded assignments, including milestones created later in that run.
User launch arguments are live launch policy, separate from those frozen role assignments.
Runs predating recorded assignments retain their original run-wide and per-role model policy.
The existing approved-model policy is still enforced by dispatch, resume, and model switching, narrowed by the recorded role allow-list and any named model pin.
An allowed list does not bypass a configured run-wide approval policy.

```bash
docket config
docket config R01
docket config R01 --json
docket batch R01 --create M1 --members T01,T02 --milestone --command 'tests/test.sh' --as planner \
  --implementor-harness claude --implementor-model strong-model \
  --reviewer-context continue --continuity-reason 'Builds on the earlier milestone.' \
  --assignment-reason 'Shared persistence invariant needs the stronger approved implementor.'
docket batch R01 --close M1 --as planner
```

Use matching `--planner-harness`, `--planner-model`, `--reviewer-harness`, `--reviewer-model`, `--test-author-harness`, or `--test-author-model` flags when needed.
Separate harness and model flags preserve provider/model identifiers containing `/` without ambiguous splitting.
Every explicit milestone assignment requires planner authority and a recorded `--assignment-reason`.
A pinned role refuses with the pin and user source file; ask the user to change that config for a new run rather than bypassing it.
Assignment and continuity flags apply only to milestone creation, never to verification or approval.

## Keep one implementor assignment per quick milestone

Assign every member before creating its milestone, normally omitting task-level harness/model flags.
A conflicting explicit task choice refuses batch creation before publication; an assignment made against an existing milestone also refuses conflicting flags.
Inherited task and draft-report requests are synchronized before the batch closes, while no member can dispatch from it.
They never overwrite a started task or frozen evidence.
If a draft task accidentally recorded a conflicting choice:

```bash
docket assign R01 T01 --clear-assignment --as planner
```

This clears explicit task choices and inherits the milestone, or the recorded run defaults if no milestone exists yet.
Retry a refused batch creation after clearing the conflicts.
The recovery refuses once dispatch, accepted scope, an observed model, or a non-draft report shows that work started.
It changes no task intent, accepted scope, baseline, or lifecycle state.

## Record reviewer continuity

Use `--reviewer-context continue` for a milestone that depends on an earlier one or carries deliberately deferred work.
Use `fresh` for independent milestones.
An explicit choice requires `--continuity-reason TEXT`; omission records `continue` with a default rationale.
The conductor consumes continuity without changing an existing run's `round_context`.
A live continued reviewer must have the same requested harness and model; a different assignment requires `fresh` with a reason.

`docket config RUN` shows each role and milestone with per-field source, allowed list, and pin.
`docket status RUN` shows milestone implementor and reviewer requests alongside observed member models.
An unobserved model stays labelled unobserved; the reviewer request is never substituted for missing reviewer telemetry.
