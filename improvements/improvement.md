
• The biggest improvement is to make the expensive-model boundary enforceable, not merely advisory. In this run, Docket preserved implementation
  context reasonably well, but the planner still spent too many tokens supervising task lifecycle, reconstructing state after restarts, reviewing noisy
  reports, and managing model sessions.

  My prioritized improvements would be:

1. Add a “budgeted combined” topology

   The current choices are:

   - split: separate planner and orchestrator
   - combined: expensive planner also performs orchestration

   We need a third practical mode:

   - Expensive model plans, defines acceptance criteria, reviews exceptions, and performs final review.
   - Cheap model handles dispatch, progress, focused test retries, report preparation, and ordinary diff review.
   - Deterministic Docket commands handle lifecycle and validation.
   - The expensive model wakes only for plan approval, material scope changes, failed acceptance, or the final aggregate report.

   This most closely matches your intended design.
2. Reject oversized tasks during validate-task

   T04 has roughly 20 files and 13 acceptance criteria spanning four systems:

   - Boundary evaluation
   - Question refresh
   - Ending phase
   - Prewarm behavior

   That is too large for a single cheap implementor context, especially after interruptions. Docket should calculate a task-complexity score from
   file count, subsystems, criteria, and verification breadth, then warn or reject it.

   T04 should probably have been split into sequential tasks such as boundary/control replay, refresh, ending phase, and reporting/prewarm
   diagnostics.
3. Make lifecycle state unambiguous

   Docket currently reports T04 as draft, even though a Terra implementor is actively working on it. The status model should distinguish:

   - assigned
   - dispatched
   - implementing
   - awaiting report
   - submitted
   - changes requested
   - approved
   - blocked

   It should also show the active agent, actual model, effort, session ID, and last checkpoint. The planner should not need Herdr inspection to
   understand status.
4. Provide first-class dispatch and resume commands

   Too much manual session management was required. Docket should support something like:

   docket dispatch RUN T04
   docket switch-model RUN T04 --model gpt-5.6-terra --effort medium
   docket resume RUN T04

   These commands should:

   - Create consistently named Herdr agents, such as impl-T04.
   - Send only the implementor playbook, task, latest decision, and checkpoint.
   - Preserve the existing session during model changes.
   - Verify and record the actual active model.
   - Prevent an agent named impl-t03-sol from silently becoming the T04 Terra implementor.
5. Encode provider limits and fallback policies

   We learned that free Minimax may not support concurrent sessions and Sol can exhaust usage. This should be captured in run configuration:

   model_policy:
   primary: gpt-5.6-sol
   fallback: gpt-5.6-terra
   provider_concurrency:
   openrouter/minimax: 1

   The scheduler could then avoid conflicting dispatches and switch models without involving the planner unless all allowed choices fail.
6. Enforce workspace read boundaries

   The attempt to inspect / should have been rejected automatically. A task should carry both:

   - Writable file scope
   - Readable/discovery scope rooted in the repository

   Commands attempting broad reads outside the workspace should stop with a clear violation. This is both safer and much better for token
   preservation because it prevents implementors from surveying irrelevant files.
7. Generate objective report evidence automatically

   Implementors should not manually state facts that Docket can calculate. The CLI should populate:

   - Files changed
   - Out-of-scope changes
   - Exact test command, exit code, duration, and test counts
   - Baseline comparison
   - Actual model history
   - Skips and xfails
   - Network activity, if detectable

   The implementor would only explain design decisions, residual risks, and failures.
8. Validate acceptance claims against report evidence

   T01 checked “No network access is required,” but its own notes identified a LiteLLM network request as the likely timeout source. docket submit
   accepted this contradiction.

   Each acceptance criterion should require linked evidence:

   - [X] No network access is required.
     Evidence: verification-network-log.txt

   At minimum, submission should flag affirmative claims contradicted by the notes or verification output. The expensive reviewer should receive only
   those exceptions, not reread the whole report to discover them.
9. Introduce cheap preliminary review

   Before waking the expensive planner, a cheap reviewer should check:

   - Scope compliance
   - Acceptance-to-evidence mapping
   - Suspicious test weakening
   - Missing tests
   - Diff quality
   - Report contradictions
   - Whether the task actually solved its stated goal

   The expensive planner would then review a compact decision packet containing the relevant diff, risks, failures, and preliminary findings.
10. Make checkpoints automatic and restart-safe

  Session restarts exposed too much operational state. Docket should automatically produce a checkpoint:

- After a configurable time or token threshold
- Before model switching
- When a harness exits
- When a user pauses the run

  The checkpoint should be machine-populated from the task diff and latest verification, with the implementor adding only the remaining reasoning.

11. Support planned task amendments

  When an implementor discovers that the task is wrong or off-road—as happened around T02—the flow should be:

  docket propose-amendment RUN T02

  The implementor records the discovery and proposed scope change, then stops. The planner sees a short amendment request instead of having to inspect
  terminal history and reconstruct why the task drifted.

12. Improve baseline classification

  T04’s preflight fails because one of the tests is intentionally not created yet. That is different from a regression. Baselines should classify:

- passing
- existing failure
- expected missing artifact
- infrastructure failure
- timeout
- nondeterministic/network dependency

  This would make later verification comparisons more meaningful.

13. Create a compact planner dashboard

  One command should provide only decision-relevant information:

  T01  review required — acceptance/report contradiction
  T02  approved
  T03  approved
  T04  implementing — Terra medium — checkpoint age 12m
  T05  waiting on T04

  No terminal scrollback, full reports, or lifecycle investigation should be necessary.

  Overall, the Docket instructions have the right conceptual separation, especially the task files, checkpoints, task-local diffs, and aggregate
  report. The weakness is that enforcement and routine orchestration still depend on the expensive model. I would shift those responsibilities into
  Docket commands plus a cheap supervisory agent, leaving the expensive model with four responsibilities: plan, approve scope changes, resolve genuine
  exceptions, and perform final outcome review.
