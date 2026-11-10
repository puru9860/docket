# Recipe-only comparison results (M3)

Runs on 2026-11-10 on the home PC, following [the protocol](recipe-comparison-protocol.md) at pinned source `fe58b1a`, with Opus 5.5 implementors and GPT-6.1-Sol reviewers at high effort.
This is the sanitized aggregate.
Raw transcripts, patches, proof and grading copies stay in private local state.

## Outcome

**Not supported.**
The fifth study is the first whose every candidate proved CLI provenance `pinned`, so its report reached a verdict.
The frozen rubric rejects promotion on an observed treatment regression: on the adjacent task, the blind adjudicator named two escaped defects in the treatment and one different defect in the control.
Recipes stay opt-in, and no default changes.

The same report also shows measured savings that the regression rule outranks: the control alone had an adjudicated defect in both pairs, the adjacent treatment needed one correction round fewer, and the treatment used fewer full-session tokens in both pairs.
Two pairs remain a directional sample, not statistical assurance.

## Fifth study (the reported one)

| Task | Coverage | Approved T / C | Rounds T / C | Adjudicated escaped defects T / C | Routine turns T / C | Full-session tokens T / C | Overhead |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `added-projects-lost` | Covered | yes / yes | 1 / 1 | 0 / 1 | 2 / 2 | 3.49M / 4.58M | -23.9% |
| `removal-persists` | Adjacent | yes / yes | 1 / 2 | 2 / 1 (all different) | 2 / 4 | 5.55M / 11.23M | -50.6% |

- `prove` reported no problems, and the wrapped milestone suite passed without models in 97 s, inside the 900 s limit.
- Every approved tree passed every hidden check and the pinned production tests.
- All four candidates graded CLI provenance `pinned`: matching runtime renderers, 14 to 30 pinned launcher executions each, and no foreign, bare, ambiguous or opaque command.
- Requested and observed model and effort matched in all eight sessions.
- Each candidate had 2 setup interventions (one folder-trust accept per role) and no human intervention.
- Both control arms read recipe files without executing the helper; the report lists that as contamination.

Every adjudicated defect in both arms is a multi-instance race: two companion processes saving the same list concurrently.
The rubric compares the adjudicator's defect statements as a set, so any differently described defect in the treatment counts as a regression even when the control shows the same class of problem.
That rule is frozen and was applied unchanged; a future rubric could adjudicate defect classes instead of statements, decided before candidates run.

## Earlier studies

| Study | Result | Why |
| --- | --- | --- |
| 1 | Inconclusive, fully run | The frozen recognizer graded every candidate `unknown` although no foreign or bare Docket command ran. |
| 2 | Abandoned after one candidate | A report-writing Python here-document naming the launcher stayed ambiguous. |
| 3 | Abandoned after one candidate | A `sed` or `node` call anywhere in a script tainted provably placed pinned commands. |
| 4 | Abandoned after one candidate | Brace groups, backticks inside single quotes, and pinned commands after `&&` stayed ambiguous. |

Every abandoned study was stopped once its frozen recognizer could only end inconclusive, and none was graded or reported.
Each graded candidate in them was approved with all hidden checks passing.
Between studies the recognizer was narrowed with red-first and mutation-checked tests, reviewed adversarially twice, and calibrated against every collected role transcript.
Its threat model is a cooperative agent accidentally reaching another Docket CLI; deliberately disguised execution is out of scope, as the protocol states.
The contract decision also gained one sentence asking roles to run each Docket command on its own.

Study 1's directional figures, unscored: on the covered task 0 / 1 adjudicated defects and -12.2% tokens; on the adjacent task one different defect in each arm and +7.3% tokens.
