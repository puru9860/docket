# Recipe-only comparison results (M3, first run)

Run on 2026-11-10 on the home PC, following [the protocol](recipe-comparison-protocol.md) at pinned source `fe58b1a`.
This is the sanitized aggregate.
Raw transcripts, patches, proof and grading copies stay in private local state.

## Outcome

**Inconclusive.**
The frozen rubric requires CLI provenance `pinned` for every candidate, and all four graded `unknown`.
No candidate ran a foreign or bare Docket command, and every runtime renderer matched the pinned one.
The recognizer still counted 9 to 11 Docket commands per candidate as ambiguous.
Recipes are not promoted, and no cost or quality claim is made.

## Setup that held

- `prove` reported no problems: reference and alternative fixes passed every hidden check, each seed failed its decisive checks, existing UI tests missed both seeds, and the recipe helper caught the covered seed (`saved-list-lost-after-restart`) and passed the adjacent one.
- The wrapped milestone suite passed without models on a candidate-shaped copy in 95 s, inside the 900 s limit, leaving the tree clean.
- Four candidates ran in the generated counterbalanced order, each with one fresh visible Opus 5.5 implementor and one fresh visible GPT-6.1-Sol reviewer at high effort, both adopted by the pinned conductor.
- Requested and observed model and effort matched in all eight sessions.
- Each candidate had 2 setup interventions (one folder-trust accept per role) and no human intervention.

## Directional observations (unscored)

| Task | Coverage | Approved T / C | Rounds T / C | Adjudicated escaped defects T / C | Full-session tokens T / C | Overhead |
| --- | --- | --- | --- | --- | --- | --- |
| `added-projects-lost` | Covered | yes / yes | 1 / 1 | 0 / 1 | 2.64M / 3.01M | -12.2% |
| `removal-persists` | Adjacent | yes / yes | 1 / 1 | 1 / 1 (different defects) | 4.44M / 4.14M | +7.3% |

Every approved tree passed all hidden checks and the pinned tests.
The independent adjudicator, a fresh Sol session reading only the blind packets, reported multi-companion concurrency defects in three of the four approved changes.
On the covered task only the control had one; on the adjacent task each arm had a different one, which the frozen rule reads as a treatment regression as well as a control-only failure.
Both control arms read recipe files without executing the helper; the report lists that as contamination.
One treatment implementor left a companion server process running after its run; it is recorded in the private notes.

## Why provenance stayed unknown

The ambiguous commands were the roles' own legitimate patterns, classified conservatively by the frozen recognizer:

- The pinned launcher invoked through a shell variable, such as `D=<launcher>; $D dispatch ...`.
- The pinned launcher run after `;` instead of first or after `cd DIR &&`.
- Heredoc or `python3 -` scripts that mention `.docket/` paths.
- `python3 --version` and similar flag-only interpreter calls, treated as an interactive interpreter that could read stdin.

The tools are frozen at preparation and grading refuses changed inputs, so these runs cannot be regraded with a narrower recognizer.

## Next run

Before another trial, either narrow the recognizer for the variable and flag-only cases with red-first tests, or have the contract decision forbid launcher variables and compound Docket commands.
Then prepare a new study; never regrade this one.
Two pairs remain a directional, opt-in-only sample.
