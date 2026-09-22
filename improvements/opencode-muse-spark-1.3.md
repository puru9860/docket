# OpenCode + Muse Spark 1.3 Free

Feedback on this harness and model as a Docket implementor.

| field | value |
| --- | --- |
| harness | OpenCode, launched as `opencode --auto` |
| model | Muse Spark 1.3 Free (OpenCode Zen), default/unpinned |
| effort | xhigh (harness default) |
| role | Docket implementor under a Claude Opus 5 orchestrator |

## Verdict

Strong value as an implementor inside a structure that reviews it.
Not safe to run unreviewed on correctness-critical code.

The trust property that matters most holds without exception: it reports test
results honestly.
Review effort therefore goes into judgment rather than fact-checking.

Its one consistent weakness is that it does not attack its own claims.
Most defects it produces are confident assertions it never verified, not mechanical
errors in the code it writes.

That weakness is almost entirely correctable from the task file.
When verification obligations are written in as explicit, checkable criteria, it
satisfies them rigorously and the review rounds collapse.
Left implicit, the same obligations are skipped and the gap is papered over with
confident prose.
Treat the quality of its output as a function of how explicitly the task states
what must be proven.

## What works well

- **Honest verification numbers.** Every figure it reports reproduces exactly when
  re-run. It does not inflate results or claim a suite passed that did not.
- **Discipline around destructive operations.** In a tree full of stashes, dirty
  files, and shared worktrees it stays on read-only inspection and selective
  application. It does not reach for the shortcut that would destroy user work.
- **Accurate scope capsules.** What it proposes to change matches what it actually
  changes, and claims stay disjoint.
- **Precise correction handling.** Numbered items come back applied exactly, with no
  collateral edits and no scope creep.
- **Real implementation competence.** It handles circular imports, deterministic
  time control in tests, lock and concurrency reasoning, and lazy-import
  workarounds without being told.
- **Substantive rather than minimal fixes.** Asked to remove duplicated policy, it
  deletes it and rebuilds the proofs on observable effects instead of patching the
  duplicate. Given a direction, it builds the right mechanism.
- **Good reporting hygiene.** File-and-line lists are precise enough to separate its
  work from pre-existing changes in an already-dirty checkout.
- **Rigorous when given an explicit verification obligation.** Told to prove its
  negative tests are adversarial, it will neutralize each guard, observe the test
  go red, restore the file byte-identical, re-run green, and document the whole
  procedure. It invents sound method; it just does not invent the obligation.
- **Draws honest boundaries when told not to overclaim.** Given permission to say
  "not verified, and here is what it depends on", it will mark the genuinely
  ambiguous case as unknown rather than fabricate a clean answer. It also scopes
  its guarantees to what the repository actually shows.
- **Keeps new machinery inert when the feature is off.** It gates added state and
  emission behind the existing disarm checks so the default path stays a no-op,
  without being asked.
- **Produces auditable per-value evidence when asked for it.** Told that every
  changed fixture value needs a derivation, it returns a value-by-value table
  citing the file and line in the reference implementation that establishes each
  one. That table made a whole class of review question answerable in minutes.
- **Classifies what it cannot derive, instead of inventing a derivation.** Where a
  value is an opaque model output with no code to trace, it says so plainly and
  explains why the value cannot be tuned to force a pass, rather than manufacturing
  a citation.
- **Reports honestly when something was not run.** A runner it built reports
  `NOT_RUN` with a pointer for stages it did not execute, rather than a green it
  did not earn.
- **Overturns a reviewer's factual premise when the code disagrees.** Handed a
  correction whose stated cause was wrong, it traced both call sites, reported the
  premise false with file and line, declined the code change, and delivered the
  regression test that the correction actually needed. It distinguishes the request
  from the reasoning behind it.
- **Widens correctly when told a constraint was a default.** Given the citations
  showing its narrowing premise was false, it extended the work to the full range,
  kept the previously correct exclusions with per-item reasons rather than dropping
  them wholesale, and re-derived every new value in the same audited format. It
  distinguishes "this was wrong" from "this was right for a different reason".
- **Tracks where a constraint came from.** It will say a limitation exists because
  the task file directed it, which is accurate and makes the constraint reviewable
  rather than silently owned or silently blamed.
- **Escalates its own verification method when the easy route fails.** If a
  monkeypatch cannot reach the code under test, it will neutralize the real site
  instead, hash the file, and prove the restore was byte-identical. Given an
  obligation it cannot satisfy cheaply, it finds a harder way rather than quietly
  weakening the claim.
- **Discloses its own limitations in prose.** The caveats are usually there, written
  plainly, even when the acceptance boxes above them are all ticked. Read the notes
  and docstrings before the checkboxes; that is where the truth is.
- **Pushes back on a wrong instruction, with evidence.** Handed a prescription that
  does not hold, it will test the prescription, report what actually happened, and
  implement something sounder rather than complying and letting the claim fail
  quietly. It has caught API-signature and mechanism errors in review direction
  this way. Do not assume a supervisor correction is the end of the analysis.

## What needs improvement

- **Invents guarantees.** It writes absolute claims ("impossible", "no path can",
  "never") about code it has not traced, and attaches them to mechanisms it just
  built. This is the single highest-risk behavior, because the claim reads as
  verification and is not.
- **Verifies against its mental model, not the repository.** When it needs to know
  how the system is configured or deployed, it assumes rather than reads the
  config, manifests, or env files sitting in the same tree. Its tests then pass
  because they encode the same assumption.
- **Writes tests that pass for the wrong reason.** Negative tests often omit the
  condition under test, so they would still pass if the behavior regressed.
- **Feeds the expected value into its own assertion.** Where a system does not
  expose something the comparison needs, it will supply that value from the test
  and then compare it against the reference, producing a tautology that looks like
  a passing parity check. This is the most dangerous shape its work takes, because
  the test name, the report, and the green run all read as proof.
- **Lets long reports get truncated.** Past a certain length its submitted report
  is cut off mid-sentence, sometimes with the truncation marker written into the
  file, and it does not notice. The section lost is usually the caveats at the end.
- **Explains away its own measurement artifacts.** When two numbers disagree
  because it measured them differently, it will narrate a plausible cause rather
  than re-measure. Check that any before/after comparison it reports used one
  method for both sides.
- **Stops silently when it runs out of capacity.** On a rate or context limit it
  goes idle with the report still in draft rather than filing a checkpoint, so the
  work looks abandoned. Instruct it up front to file a `docket handoff` before
  stopping, and watch for the limit directly rather than for the absence of a
  submission.
- **Optimistic self-grading.** First rounds mark every acceptance criterion
  satisfied, including ones that do not hold, even when its own prose describes the
  gap. The prose is more reliable than the checkboxes.
- **Duplicates policy instead of reusing it.** It re-implements logic that already
  exists nearby, and the copy drifts from the original almost immediately.
- **Treats task-file direction as settled fact.** It will test and reject an
  explicit technical prescription, but it does not test a constraint phrased as
  scope or direction, even when the repository plainly contradicts it. In one case
  it limited a fixture to a single event category because the task said so, while
  the reference implementation emitted four such categories and captured all of
  them. The narrowing was reported honestly and attributed correctly; it simply
  never checked whether the constraint was true.
- **Repeats a failure mode one level deeper.** Corrected on an unverified claim, it
  will build a real fix and then vouch for the fix with a new unverified claim.
  Correcting the instance does not correct the habit.

## How to drive it for better results

Each item below targets a specific weakness above.
Applied together in a task file, they have taken a first-round submission from
"needs three rounds" to "approvable as submitted", so the effort spent writing the
criteria is recovered immediately in review.

- **Put the verification burden in the task file.** Do not write "make X safe";
  write "prove X by citing the file and line that establishes it". It reliably does
  what is asked and reliably skips what is merely implied.
- **Name the artifacts it must read.** For anything environment-, deployment-, or
  configuration-dependent, list the concrete files (manifests, env files,
  Dockerfiles, settings modules) and require the report to quote what it found.
  This closes the assume-instead-of-read gap directly.
- **Ban unverified absolutes in acceptance criteria.** Phrase criteria as "the
  report explains concretely what would have to be true for X" rather than "X is
  impossible". It answers the concrete question well and fills the absolute one
  with confident prose.
- **Require negative tests to be adversarial.** Ask explicitly that each negative
  test would fail if the behavior regressed, and that the report say how it was
  confirmed to fail. Otherwise expect tests that assert the absence of something
  under conditions where it was never going to happen.
- **Make acceptance criteria mechanically checkable.** Criteria phrased as
  observable effects (a counter value, a task count, a call count, an exact output)
  get honest answers. Criteria phrased as qualities ("robust", "safe", "correct")
  get checkmarks.
- **Point it at the existing implementation before it writes policy.** Say which
  function already enforces the rule and require reuse. Left alone it will mirror
  the logic rather than call it.
- **Prefer one prescriptive constraint over a specification.** Stating the boundary
  ("the second factor must not be satisfiable from any dotenv-loaded source") and
  leaving the mechanism to it produces better designs than dictating the mechanism.
- **Re-run every registered verify and read the diff each round.** Cheap, because
  its numbers are honest; necessary, because its claims are not.
- **Re-check the new guard, not just the old gap.** After it closes a safety or
  security hole, validate the replacement against real configuration. Expect the
  second attempt to be a genuine mechanism carrying an invented guarantee.
- **Mark each direction as a hard constraint or a starting default.** It optimizes
  to the letter of the task file, so an incidental phrasing becomes a ceiling. Where
  you want it to widen if the code allows, say so explicitly and tell it to check
  the reference implementation first.
- **Ask for a per-value derivation table whenever it changes test data.** Requiring
  a file and line per changed value is the single cheapest way to separate a
  corrected oracle from an oracle moved to fit the implementation.
- **Require every asserted value to come from the system, not the test.** State it
  as a criterion: a box may be checked only when the compared value is produced by
  the code under test. Where the system genuinely cannot expose it, require the
  criterion to be reported partial with the gap named. This is the single highest
  -value check to make explicit, because a tautological comparison is invisible in
  a green run.
- **Cap report length and ask for the caveats first.** Tell it to keep the report
  short and to put limitations near the top rather than in a closing notes section,
  so the part most likely to be truncated is not the part you most need.
- **Require a handoff before it runs dry.** Put it in the dispatch prompt: on a
  context or rate limit, file and submit a `docket handoff` checkpoint rather than
  stopping. Otherwise a limit costs the whole round's continuity.
- **Start a fresh process for every dispatch.** One process per task and per
  changes round, not one long-lived session. A fresh context avoids the truncation
  failure above, and the task file, scope capsule, latest decision, and task-local
  diff carry everything a replacement needs. Do not restart mid-round with work in
  flight unless a ready checkpoint exists.
- **Read its prose against its checkboxes.** When the two disagree, the prose is
  right. Treat any hedge in a docstring or a notes section as a criterion that is
  not actually met.
- **Decline a blocker when the capability exists, but verify your own prescription
  first.** It will sometimes submit blocked on a limitation that a library it is
  already using can solve, so checking is worth it. Check the installed API, not
  the documented one, and expect to be corrected on the details: prescribe the
  outcome and the constraint, and leave the mechanism to it.
- **Keep the session on model change.** `Ctrl+X` then `M`, or
  `herdr agent send-keys <name> ctrl+x m`, rather than restarting and losing
  context.

## Closing verdict after a full run

One completed run as sole implementor: `pybot-live-temporal-parity-v2`, twelve
tasks, ten approved and two waived, roughly 12,700 insertions across 64 files on
the Live Interview parity branch plus the Pybot compatibility and capture work,
supervised by a Claude Opus 5 orchestrator in split topology under a separate
planner.

**Worth using again, with the split drawn differently.**

What made it cheap to supervise was not its coding so much as two habits. Its test
numbers were honest without exception: every figure it reported reproduced exactly
when re-run, across the whole run. And it never damaged a working tree that had
twenty stashes and two dirty checkouts sitting in it, on a task whose entire premise
was selective recovery. Those two properties are what let a supervisor spend review
effort on judgment instead of fact-checking.

Its cost landed almost entirely on one class of defect: whether evidence proves what
it claims. Invented guarantees, a comparison that supplied its own expected value, a
measurement artifact rationalised as someone else's activity, acceptance boxes
ticked directly above a limitations section contradicting them. Each of those cost
review rounds.

The round count fell sharply once the task files stopped asking for outcomes and
started stating verification obligations as checkable criteria. Six of the later
tasks approved on the first round. That is the single highest-leverage lever
available, and it is free.

**The finding that is not about the model at all.**

The three most serious defects of the run were caught by neither the implementor nor
the orchestrator: a gate runner that never passed its `--dataset` argument, so three
of five gates reported synthetic results for any input; a dirty fingerprint hashing
`git status` output rather than content; and a positional callback-to-flush mapping
that only worked because the committed fixture happened to be one callback per
flush. The orchestrator approved all three. A third reader with fresh eyes on the
aggregate report caught them.

So the layered topology earned its keep more than the model choice did, and
orchestrator overconfidence was the weakest link rather than implementor capability.
Treat that as the structural lesson: a cheap implementor plus a diligent reviewer
still misses "this evidence is hollow" defects, because both parties are reading the
same artifact with the same expectations.

**How to divide work next time**

- Delegate bulk, verifiable, mechanically-checkable construction: tapes, fixtures,
  tests, adapters, capture plumbing. This model is good value at zero cost there.
- Do not delegate the seam where a harness is wired to its own input, or where an
  oracle or fixture value changes. Those are simultaneously its weakest area and the
  reviewer's blind spot. Wire it yourself, or demand an adversarial mutant input
  from the first round rather than after a reviewer pushes.
- Budget a paid model for the tail of a long run. Rate-limit thrash, three silent
  stalls without checkpoints, a model switch that produced zero tokens, and finally
  the orchestrator taking over the last task cost more wall-clock than paying would
  have.
