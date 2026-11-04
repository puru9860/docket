# Focused task methods

The planner selects a method only when the task warrants it, through an existing
`--decision` / JSON task `decision` entry.
Read the selected section through `docket help task-methods` on demand.
Selection stays within the objective and approved contract: it does not change
acceptance, authorize behavior changes, create report fields or add model turns.
Ordinary decision text remains unrestricted and has no automatic lifecycle meaning.

## refactor-equivalence

Implementor: before structural edits, pin the approved observable behavior and state transitions at the material boundary; state the intended shape and scope.
For Git evidence, export the captured task baseline with `docket bundle <run> <owner> --baseline --checkout`; no first frozen bundle is needed. A documents-only run cannot claim a reconstructed Git baseline.
Run the same characterization checks against baseline and patch, including relevant preservation, error and negative cases; green on both supports only the behavior those checks distinguish.
When green checks could miss the material outcome, use a discriminating control in a disposable copy or private state; never mutate the measured checkout for that control.
Change structure in small, staged steps without speculative indirection; preserve deliberate historical compatibility.
Newly discovered defects or desired behavior changes need a separate planner intent decision, not a silent repair or an oracle edited to fit the patch.
Record actual commands, baseline/patch observations, conditions and gaps in the existing evidence/report structures.

Reviewer: verify that the claimed baseline and patch checks actually ran at the promised boundary; inspect integration, preservation and state transitions.
Independently derive expectations and scrutinize every changed characterization value as a changed oracle.
Assess readability concretely through structure, duplication, naming and justified abstractions; a cleaner diff alone does not prove behavioral equivalence.
Require changes or an explicit qualified waiver for material missing evidence; preserve final review independence and the existing contract.

## performance

Implementor: state the metric, units, user-visible target, claimed surface and execution conditions before optimizing.
Measure an actual baseline and patch on the same machine with comparable realistic data, configuration, cache and concurrency; prove that timed work really executed.
Check equal work and output correctness, including relevant errors and work counts, so skipped or changed work cannot masquerade as a speedup.
Profile outside reported timings to locate the bottleneck; distinguish a local measurement from end-to-end impact.
For adoption claims, warm up and interleave repeated baseline/patch measurements, for example ABAB, with enough observations to assess variability; report the actual run count.
Save commands and raw timings under existing run evidence; report units, median, range, conditions and confounders without inventing precision.
A requested one-run ballpark may be explicitly qualified as such; it does not support an adoption claim or replace correctness evidence.
Overlapping ranges alone prove neither equality nor improvement. If the effect is indistinguishable from noise, equal work/conditions are unverified, or prerequisites are missing, state inconclusive with the actual evidence.
Do not infer provider dollars from tokens or latency without metered data, or adopt a strict global numeric confidence threshold.

Reviewer: independently check equal work, output correctness, baseline provenance, confounders and whether the claimed surface matches the measured one.
Check end-to-end plausibility and rerun an interleaved pair when cheap and material; preserve uncertainty rather than inventing a benefit.
