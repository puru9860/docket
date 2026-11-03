# Paused pstack work: resume handoff

Paused at the user's explicit request on 2026-11-03, around 16:55 Nepal time.
This pause supersedes the earlier instruction to work continuously. Do not
resume implementation or re-arm model timers until the user asks to continue.

All three worker sessions were stopped. Both Opus automatic quota-reset
continuations were cancelled, and Sol's active turn was interrupted. The
coordinator's in-flight M1 full suite received SIGINT and exited 130 through its
worker-cleanup path. The interruption is not a passing test result.

## Saved Git checkpoints

| Branch / checkpoint | State |
| --- | --- |
| `audit-3-fixes`, `e4119fd` | Portable adoption/continuation plan |
| `audit-3-fixes`, `fe58b1a` | Last fully verified milestone: recipe infrastructure and real companion recipe |
| `audit-3-fixes`, this handoff checkpoint | M1 reasoning guidance, independently reviewed and focused-tested; full regression suite still pending |
| `pstack-group-discussions`, `e23dda0` | WIP native group-discussion design and partial implementation; unfinished and unvalidated |
| `pstack-recipe-comparison`, `9f66fbe` | WIP hidden checker and two task definitions; preparation/runner/protocol/tests unfinished |

The WIP branches are separate from the continuation branch. They are preservation
checkpoints, not approved implementations. No comparison candidates ran and no
experimental results or default promotion exist.

## Completed and checked

The recipe milestone adds explicit `--recipe` / plan-task selection, strict
manifest/path/root validation, fresh private proof for preflight/submission/
re-review, exact input and output freezing, drift and missing-proof refusals,
lightweight honest blocks, immutable review pointers and changed-oracle duties.
The companion recipe exercises actual browser project persistence across a real
server restart, with negative/prerequisite controls and owned-instance cleanup.

Verified recipe checkpoint: 789 CLI tests, 43 UI Python tests, 104 UI Node tests,
and 8 explicit browser recipe tests passed. Real preflight and submission used
distinct proof directories and froze an intact doctor/result/screenshot bundle.
That integration fixture used documents-only evidence; Git freezing, multi-root,
drift and tamper cases are exercised by the CLI suite.

M1 adds conditional discovery safety facts/probes, runtime proof type/conditions/
observed boundary alongside existing semantic classifications, and judgment-based
shared-premise inspection during correction. It adds no role, required report
field, gate, automatic budget grant or routine model turn. Renderer fingerprints
include the new guidance helpers. Seven new tests and 34 related existing tests
passed (41 focused tests). Baseline assertions failed on the old implementation
as expected. The combined full suite was interrupted by the user's pause.

## Work still in progress

### Group discussions: heavy Opus lane

Read `docs/group-discussion-design.md` on `pstack-group-discussions`.
The opt-in protocol must preserve `discussion-v1`, distinct identities/generations,
serialized turns, append-only chained messages, per-recipient delivery, explicit
proposal-bound responses, named dissent, whole-group user pause, honest caps and
recovery. Silence and timeouts never mean agreement. No harness launch, coding
mode change or automatic external communication is authorized by this feature.

The draft introduces `discussion_groups.py` and starts routing the existing I/O
layer to it. It is incomplete. A review finding must be resolved before use:
participant replacement writes registration and a generation event separately.
An interruption must not leave old-generation acceptance valid, and retry must
recover one replacement without duplicate effects. Verify actual generation
identity as well as the event. Complete the handlers/CLI integration and add
real interruption, stale proposal, identity, delivery, dissent and convergence
tests. Run all existing bilateral tests unchanged.

### Comparison preparation: heavy Opus lane

The WIP branch contains only task JSON and `tools/recipe-comparison/hidden/check.mjs`.
Finish the narrow portable procedure described in M3 of the adoption plan.
Use the pinned recipe-only source `fe58b1a`, fixed Opus implementor and Sol
reviewer assignments, real Docket contracts/registered verification/submission/
review packets, and separate ordinary-looking task directories. Keep hidden
checks and variant mapping outside candidate directories. Exclude the adoption
plan itself from both snapshots because it exposes the rubric; record exclusions
and resulting hashes honestly. Preserve operational instructions and real tests.

Prove seeded defects fail independent checks and correct behavior passes before
running candidates. Do not restore the retired Docket evaluation subsystem or
claim quality/cost gains from setup. Two pairs permit directional opt-in findings
only. Raw proof and transcripts stay private; publish sanitized summaries.

### Recipe-path round-trip bug: light Sol follow-up

Reproduced before the pause: selecting
`recipes/project #1/recipe.json` records `recipes/project` after flat frontmatter
comment parsing. The new worktree for this follow-up had no source edits when
stopped, so no additional draft branch is needed to recover it.

Fix this field's encode/decode narrowly, retaining ordinary unquoted paths and
the global flat-frontmatter convention. Cover CLI assign, JSON plan, submission,
and a qualified-root path with red-before/green-after tests. Source ownership was
`recipes.py`, `commands.py`, and new `test_recipe_paths.py`; coordinate the small
renderer decoding change in `prompts.py` with M1 rather than overwriting it.

## Resume from the home PC

Fetch origin, switch to `audit-3-fixes`, and fast-forward only. Read this handoff,
`docs/pstack-adoption-plan.md`, `AGENTS.md`, and `ARCHITECTURE.md` in a fresh
session. Resume only on the user's instruction. Opus 5.5 owns heavy work and Sol
owns light work; use the existing `codex` alias launch settings without adding
competing approval/sandbox flags.

Finish the M1 regression run before calling it complete. Recreate isolated
worktrees from the continuation branch and inspect/cherry-pick the corresponding
WIP commit for each heavy lane. Keep their ownership disjoint. All needed source
and intent are in Git; no first-machine `/tmp` directory, pane, transcript or
private `.docket` report is required.

Relevant checks:

```sh
tests/test.sh
(cd ui && npm test)
node --test ui/verification/companion-projects/verify.test.mjs
git diff --check
```

For focused M1 work, set `PYTHONPYCACHEPREFIX` outside the skill and run
`python3 -m unittest discover -s skills/docket/tests -p test_reasoning_guidance.py`.
Install the locked UI dependencies and Playwright Chromium/system libraries only
when running the explicit browser suite. The `no-mistakes` CLI was unavailable;
it was not run. Use repository checks and independent review.

The installed skill was not synced. Do not install the WIP group/comparison
drafts. Sync only approved source according to `AGENTS.md`, excluding bytecode
and avoiding changes underneath active Docket runs. The pre-existing unrelated
`reasonix.toml` remains local, untracked and untouched.
