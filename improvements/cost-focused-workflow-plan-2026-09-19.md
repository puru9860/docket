# Docket: cost-focused workflow and prompt improvement plan

Status: proposed, 2026-09-19. Review and planning only; no product changes implemented.

This reviews the current working tree, including its uncommitted development work. It supplements the earlier `workflow-improvement-plan.md` with current findings. Per the user's subsequent direction, the current model/workflow evaluation system is scrapped from the proposed direction, and its redesign is deferred. This supersedes the earlier recommendations to reuse or extend that evaluation system.

## Recommendation

Keep the five-role design as the intended standard mode. Add a deliberately smaller quick mode, but first make the existing five-role lifecycle, notifications, and prompts agree. The evidence machinery is substantially stronger than the original design: complete dirty baselines, immutable bundles, captured verification, independent verifier artifacts, retry-safe transitions, and leased delivery are valuable foundations.

The economic target is total premium-model work per independently correct outcome. Prompt length, number of roles, and first-round approvals are useful diagnostics, but none alone establishes savings or correctness. A cheaper implementor is economical only if its discovery, coordination, correction, verification, and review costs stay controlled.

Five logical roles should not require five continuously active sessions. Start the planner for planning and amendments, use inexpensive workers for routine execution, and start the capable reviewer with a fresh context at explicit approval boundaries and final review.

In standard mode, ordinary implementation belongs to implementors; the orchestrator owns coordination and the aggregate. Keep legacy executor overrides explicitly legacy. If compatibility temporarily permits an orchestrator-owned implementation task in standard mode, it must still go through independent verification and reviewer approval.

## 1. Findings in the current version

Evidence labels: **reproduced** means exercised against the current CLI using disposable test workspaces; **inspected** means established by reading the referenced implementation or documentation. These findings concern the current working tree, not necessarily the last published release.

| Priority | Finding | Evidence and consequence |
| --- | --- | --- |
| P0 | Five-role submission retains legacy completion shortcuts. | Reproduced: an `executor=orchestrator` task under `five-role-v1` becomes `completed` on submit without a decision. `submit_locked`, around line 3892, also completes combined-topology aggregates without a workflow guard. This conflicts with reviewer-owned approval. |
| P0 | Five-role aggregate routing still targets the planner. | Reproduced: after task approval and aggregate submission, planner events contain the aggregate and reviewer events do not. See `derive_events`, around line 5161, and `reviewer_verifier_events`, around line 5051. |
| P0 | Review readiness is disconnected from verifier readiness. | Reproduced: a closed milestone batch emits a reviewer event before its verifier pass; recording the pass does not change the event. A verifier event remains derived after the pass. See `batch_ready` and `reviewer_verifier_events`. This can cause premature premium work and repeated verifier handling. |
| P0 | Dependencies can prevent the review needed to unblock them. | Reproduced: B1 contains T01 and T02, with T02 depending on T01. T01 is submitted and verifier-passed; T02 cannot dispatch until T01 is approved, while the reviewer gets no batch event until T02 is submitted. An explicit review frontier is missing. |
| P0 | Milestone packets are not scoped to a milestone. | Reproduced: a normal reviewer packet refuses when T02 has no frozen bundle, even if T01 is ready for review. `cmd_review_packet` selects all planned/assigned owners unless `--correction` is used, around line 8765. A milestone must not depend on future work having evidence. |
| P1 | New runs still default to legacy. | Inspected: `cmd_init` uses `WORKFLOW_DEFAULT`, which is `legacy`. That same constant is also the fallback for old runs without a workflow key. Changing this one constant would risk reinterpreting old runs. |
| P1 | Role documentation gives conflicting authority. | Inspected: `planner.md` opens as “Planner / reviewer”; `orchestrator.md` directs approval, waiver, and implementation; five-role contracts reserve approval for the reviewer. Implementor submission examples omit the required five-role `--as`. The orchestrator recovery text even suggests returning a report to draft, contradicting the no-manual-status invariant. |
| P1 | Generated prompts are not workflow- or stage-specific. | Reproduced/inspected: `compose_prompt`, around line 7922, does not select on workflow or phase. Planner rendering for `orch` produces task placeholders instead of the plan objective. Every role receives the same task-shaped envelope. |
| P1 | Guidance budgets can be exceeded. | Reproduced: a zero budget still selects the concurrency card; a zero-budget Claude test prompt includes 273 estimated guidance tokens. `select_cards` permits the first oversized card, and the model profile is outside the card budget. This is a guidance-budget defect; `--max-tokens` is not documented as a total-prompt cap. |
| P1 | Recovery prompts omit important correction context. | Inspected: the latest decision contributes its reason but not its numbered required changes. Reproduced: a draft handoff is labelled “ready handoff” because the renderer selects the latest file without checking readiness. |
| P1 | Review packets omit finding details and shorten waiver reasons. | Inspected: the standard packet reads verifier metadata but discards finding bodies, truncates waiver reasons to 120 characters, and substitutes generic decision instructions for report-specific questions. Correction summaries include only the first 160 characters of the first required-change line. See `cmd_review_packet`, around lines 8980-9042. This undermines the promise that a fresh reviewer receives the important risks and decisions. |
| P1 | Dispatch accounting and model policy have gaps. | Reproduced: an approved task remains in `active_dispatches`; a switch to an arbitrary model succeeds when fallback policy is absent. Inspected: initial `--model` is not checked against a configured allowlist, and `premium_budget` has no implementing reference in the CLI. Dispatch records a binding but does not itself launch a model or capture the delivered prompt. |
| P2 | Model guidance identity is misleading. | Reproduced: `muse-spark-1.3` matches no profile, while `claude-test` selects `muse.md`. That file declares Claude/Opus/Sonnet/Haiku aliases despite its Muse title. Substring matching and one-run evidence are weak grounds for model-specific defaults. |
| P2 | Generic verification instructions are overbroad. | Inspected: every verifier is told to apply all obligations, including fingerprints, zero/one/many events, and offline proof. These are valuable for matching claims, but irrelevant obligations create work and false blocks on unrelated tasks. |
| P2 | User-facing promises have drifted. | README still introduces three roles and describes exactly-once delivery; architecture describes five roles and at-least-once delivery. The 214-line `SKILL.md` duplicates extensive protocol details and a command catalog instead of providing a small role router. |

The new authority checks validate a supplied `--as` role label. This helps prevent accidental role mixing; it does not establish reviewer independence against a process with access to the same files. Registration binding and fresh review contexts should make the intended separation explicit without claiming a security boundary the system does not provide.

## 2. Standard and quick modes

Keep user-facing **mode**, protocol **workflow version**, role-to-session **topology**, and model **policy** separate. A mode selects a tested preset. It must not silently weaken evidence validation or change the meaning of an existing run.

Proposed CLI, not implemented:

```text
docket init RUN                         # standard for new runs
docket init RUN --mode standard
docket init RUN --mode quick            # three-role preset
docket init RUN --mode quick --agents 2  # restricted two-role variant
```

| Mode | Execution roles | Model use | Review separation |
| --- | --- | --- | --- |
| Standard | Planner; orchestrator; implementor; verifier; reviewer | Capable planner and reviewer; cheap orchestrator and implementor; cheap verifier initially, stronger when evidence warrants it | Planner and reviewer use separate contexts. Implementor never approves its output. |
| Quick, three roles | Coordinator combines planning/orchestration; implementor; checker combines verification/review | Brief capable planning; cheap implementation; one capable review session that also checks evidence | Checker is separate from implementation and planning. No separate verifier opinion is claimed. |
| Quick, two roles | Supervisor handles planning/coordination/verification/review; implementor | Brief capable planning and review, cheap implementation | Supervisor starts review from a fresh packet/context. No separate verifier or separate planning/review agent is claimed. |

Ship quick three-role first. Defer the two-role variant until three-role overhead is measured; another variant adds prompt and transition combinations that need testing. A fresh review context still has a cost, even if it belongs to the same supervisor role.

Quick eligibility should be based on uncertainty and impact, not file count: one clear outcome, established local verification, one writer, and no unresolved requirement or architecture decision. Start with low-impact local fixes and straightforward documentation/test changes. Concurrency protocol changes, migrations, sensitive authorization changes, cross-checkout integration, and unresolved oracle changes should use standard mode.

Discovery may invalidate the initial selection. Escalate through an explicit, recorded policy transition with the existing artifacts preserved; no silent mode downgrade and no baseline recapture. The first implementation can stop at a mode-escalation request rather than invent an unaudited migration.

All modes retain scope claims, complete baselines, immutable evidence, source-drift rejection, honest blocking, and decisions bound to exact evidence. Quick modes merge duties through a new explicit policy, not through legacy completion shortcuts, `--skip-verify`, or blanket verifier exemptions. Record that verification and review were combined.

New-run defaults must use a separate constant from legacy decoding. Runs without a workflow key must remain legacy, and existing explicit workflows must remain unchanged. Invalid explicit workflow values should refuse instead of falling through to legacy authority behavior.

### Resolve the approval-frequency tradeoff

Current policy requires approval before ordinary dependencies dispatch and holds scope until approval. Therefore “capable reviewer only at the end” is not possible for every dependency graph.

Use review frontiers: batch independent, verifier-ready submissions whose approval unlocks the next wave. Create an earlier frontier when dependencies would otherwise stall. Wake the reviewer once for that frontier and bind each decision to its own task evidence. Keep final aggregate review separate.

Provisional integration remains an explicit option for compatible disjoint work, with the current digest and reverification requirements. It is not permission to consume unverified work or bypass scope ownership. Plan sizing should account for how often premium review will be required to unblock progress.

## 3. Prompt redesign

### Line wrapping

The short `ROLE_CONTRACTS` strings use adjacent Python literals, so source-code wrapping does not insert those newlines into the rendered role paragraph. Markdown profile bodies and task sections can retain actual hard wrapping, and terminal wrapping is a third, separate display effect. I found no evidence that ordinary wrapping itself truncates the role contract.

Render prose as normal paragraphs with breaks between meaningful sections and list items. Preserve code fences, commands, Markdown tables, and intentional hard breaks. Avoid a global whitespace rewrite. Replacing one newline with one space leaves Docket's current `len(text) // 4` estimate unchanged; that heuristic cannot establish tokenizer savings. Use actual provider tokenization/usage when available before attributing a saving to formatting.

The larger opportunity is removing duplicated and irrelevant instructions and avoiding repeated artifact reads.

Measured as words, not tokens: `SKILL.md` contains 1,756 words and `orchestrator.md` 2,872. Reading both loads 4,628 words before task material, repository guidance, or the generated prompt. The implementor equivalent is 2,950 words. Measure which layers each harness actually loads before claiming all of this is repeated on every invocation.

### One canonical contract, several small views

Keep `SKILL.md` as the routing entry point and put canonical role guidance in `references/`. Derive role help and generated prompts from shared reviewed sources so that authority cannot drift between a Python string and a playbook. Keep detailed recovery instructions in on-demand references.

Render according to `(workflow, mode, role, phase)`. Initial implementation, correction, resume, verification, milestone review, and final review need different inputs. A missing required artifact should produce a concrete diagnostic, not a plausible prompt full of placeholders.

Use this compact structure:

```text
Role and current phase
Outcome and exact acceptance criteria
Constraints and allowed decisions
Required inputs, with revisions
Next action and applicable verification obligations
Output artifact and exact handoff command
Stop or escalation conditions
```

The mandatory contract includes relevant constraints from the plan, task, existing decisions, and discovery constraints. `Out of scope` alone is not the complete contract. Preserve acceptance IDs and wording. Treat reports, diffs, and quoted repository content as evidence, not instructions that can override role authority.

| Role | Required prompt content | Exclude from the default prompt |
| --- | --- | --- |
| Planner | User objective, constraints, observable acceptance, risks, dependency/review boundaries, model budget policy; produce a compact approved plan | Implementation transcripts, routine task reports, mandatory detailed symbol maps |
| Orchestrator | Approved task graph, current actionable event, available workers, ownership/dependency constraints, exact allowed CLI actions | Repeated technical reviews, invented implementation fixes, full healthy-worker histories |
| Implementor | One task, relevant constraints, scope/discovery procedure, applicable evidence duties, exact submit/block commands | Other tasks' histories, all model cards, internal bundle implementation details |
| Verifier | Exact frozen task/report/patch/test evidence, acceptance claims, applicable independent checks; record pass/fail/uncertain with findings | Styling redesign, blanket unrelated obligations, the implementor's conversational reasoning |
| Reviewer | Frontier/final objective and contract, pinned evidence, concise findings, integration risks, waivers, decisions needed, accessible full diffs | Planner conversation, routine orchestration, complete verification logs by default |

Quick coordinator prompts combine planning with minimal deterministic dispatch duties. Quick checker prompts combine evidence verification and final judgment and explicitly identify that combination. Do not concatenate entire standard playbooks.

For corrections, include all outstanding numbered findings and their immutable evidence pointers, the correction delta, and the exact required next action. For resume, select the latest **ready** handoff; if none exists, label mechanical recovery and direct targeted rediscovery. A path alone is useful only when the prompt says whether reading it is required.

Preserve the substantive verifier findings, complete waiver reasons, and actual decisions needed in reviewer packets. Shrink routine evidence excerpts first. If mandatory material exceeds the packet target, state the overage and provide bounded sections with an explicit required-reading manifest; never silently drop a late finding or the qualification at the end of a waiver.

### Budgets and guidance selection

Treat the core contract as mandatory and guidance as optional. Fix the guidance limit to include the profile, cards, and their formatting. Zero means no optional guidance; an oversized first card is skipped. Record selected/rejected cards and reasons. Do not truncate a hard constraint or acceptance requirement to fit a budget; report mandatory size separately.

Keep a clear distinction between actual tokens and estimates. Add section-level estimated sizes and, where supported, observed input/output/cache/reasoning usage. Prompt digest records should include workflow, mode, phase, renderer revision, and selected source revisions. Bind the rendered bytes to the dispatch record; record delivered bytes only when an adapter actually observes delivery.

Prefer task-relevant guidance over speculative model personalities. Remove model-specific defaults justified by the discarded evaluation work; do not carry its conclusions into the new prompts. If explicit model identities remain as execution metadata, correct the Muse/Claude mapping and avoid broad substring matches. Unknown models may receive applicable general task guidance under an explicit policy; do not describe them as receiving “common contract only” if cards are also selected.

Retain a universal obligation to prove acceptance honestly. Select specific duties for claims about offline operation, changed oracles, event aggregation, fingerprints, or configuration. Selection should explain applicability and allow an agent to identify another relevant obligation; it must not prevent discovery from revealing a previously unknown risk.

## 4. Other improvements worth making

Move repetitive work into deterministic CLI operations: task/report scaffolding, changed-file inventories, captured verification references, next-action routing, and packet assembly. Agents should author intent, judgments, and uncertainty. Do not automatically mark acceptance met from a passing command.

Generate concise report skeletons with mechanical fields filled in. Keep authored content to the outcome, evidence-to-acceptance mapping, remaining risks, and decisions needed. Distinguish requirement changes from implementation defects so an implementor does not spend its correction budget trying to satisfy incompatible instructions.

Measure repeated test execution. The implementor playbook currently asks for an explicit verification run and submission runs it again. Prefer targeted development checks and one authoritative submission run where practical. Preserve the rule that changed source, contracts, inputs, or commands require fresh verification. Verifiers should assess test adequacy and run independent checks where warranted, rather than rerun every command automatically.

Fix active worker accounting before trusting concurrency limits. Derive work readiness from lifecycle documents, distinguish execution capacity from held scope, reconcile stale dispatch records, and serialize run/provider capacity claims so concurrent owners cannot both pass the same cap. Confirm session generation before treating a retry as the same writer.

Make model policy explicit per role: approved initial model, ordered fallback list, effort policy, provider concurrency, and premium exception owner. Missing telemetry stays unknown. Enforce numeric spend limits only when usage is observable; otherwise use clearly labelled invocation/turn limits. An approved fallback policy must not be bypassable by a new initial dispatch or an empty-list interpretation.

Keep the stdlib-only CLI and fixed skill layout. Remove the discarded evaluation subsystem as scoped below. After behavior is stable, consider extracting internal modules. A 14,336-line script is a maintenance concern, but a broader reorganization should not delay the lifecycle and prompt fixes.

Separate fast development checks from final qualification. Recorded full suites take about 35 minutes; the five focused audit checks took about seven seconds. Keep the required full suite before shipping, while making focused lifecycle/prompt checks easy to run during cheap-model correction loops. Do not rerun a full suite after every prose correction or routinely duplicate it across roles.

## 5. Implementation sequence and acceptance

Each behavior fix needs a regression that fails against this reviewed build and passes after the change. Preserve legacy tests explicitly; do not rewrite their expected behavior to hide a compatibility break. Run `tests/test.sh` with zero failures, then sync and compare the installed skill as required by repository instructions.

| Phase | Deliverable | Required acceptance |
| --- | --- | --- |
| 0. Retire the current evaluation subsystem | Remove its active code, fixtures, commands, dashboard, documentation, experiment plans, and evaluation-only tests; decouple release/readiness from its results | No active workflow, prompt, help text, or release check depends on the discarded evaluation system. Core lifecycle, evidence integrity, verification capture, delivery, and regression coverage remain intact. No replacement evaluation is designed or implemented in this phase. |
| 1. Close five-role lifecycle gaps | Workflow-specific submission states, aggregate destination, verifier resolution, verifier-aware review readiness | Orchestrator-owned work cannot self-complete in standard mode; aggregate wakes reviewer only; a valid finding resolves its verifier event; a changed finding produces the correct new review eligibility; legacy behavior remains intact. |
| 2. Make dependency review progress | Review frontiers, early dependency-unblocking review, frontier-scoped packets | T01 -> T02 completes through events without manual polling; future tasks do not block a current packet; reviewed bundle/finding changes invalidate event identity; unrelated tasks remain outside the packet. |
| 3. Repair dispatch policy/accounting | Capacity release/reconciliation, serialized capacity claims, explicit fallback behavior, prompt binding | Approved/handed-off workers do not permanently consume execution slots; parallel dispatch respects the cap; allowed/disallowed model transitions and empty policy behavior are tested; retries bind session generation and cannot duplicate writers. |
| 4. Unify role prompts and docs | Canonical contracts; mode/role/phase rendering; budget fixes; ready-handoff filtering; complete review risks | Snapshots cover each supported role/phase; no contradictory authority or missing-task placeholders; all correction items, substantive findings, complete waiver reasons, and actual questions survive rendering; draft handoffs are never called ready; zero/oversized/combined guidance budgets hold; literal commands survive formatting. |
| 5. Introduce standard and quick presets | New-run standard default, quick three-role policy, explicit mode selection | Old missing-key runs remain legacy; new default is standard; invalid policy refuses; both modes preserve gate/evidence invariants; quick output records combined review duties; risk escalation cannot silently change old evidence. |

Implement phase 5 only after phases 1-4 establish a reliable standard baseline. Introduce quick two-role as a later measured extension. Each phase should be a bounded change with its own regression evidence rather than another large rewrite.

## 6. Evaluation retirement scope; replacement deferred

The current evaluation is not a baseline to repair, extend, or reuse. Remove its results and recommendations from the basis for workflow, prompt, and model-selection decisions. Defining a robust replacement is a separate later planning task; this plan specifies no replacement fixtures, scoring scheme, experiment arms, or model comparison procedure.

The planned removal covers:

- `skills/docket/eval/`, including fixture packages, hidden evaluators, arms, and pilot procedure.
- Evaluation-specific CLI composition, capture, scoring, comparison, and artifact machinery, including `docket pilot` and evaluation-dependent portions of `docket metrics`.
- The evaluation dashboard, its documentation and tests, evaluation result reports, and superseded evaluation/model-combination pilot plans.
- Evaluation-only regression cases and fixture-specific instructions or model-profile conclusions that exist solely to support the discarded approach.
- Evaluation references in `SKILL.md`, role help, README, architecture, and command listings.
- Dependencies from release qualification, final packets, and readiness checks on pilot rows, arm approvals, adjudication, or comparison scores. Preserve ordinary task/integration verification, independent review, and evidence integrity as their requirements.

Inventory shared helpers before removal so that ordinary task verification and evidence capture retain their regression coverage. Core behavioral tests are not the model/workflow evaluation being scrapped. Basic operational usage accounting remains useful for the cost objective and does not require an evaluation framework.

Existing immutable task/run evidence must not be rewritten or deleted as cleanup. Historical frozen records can remain historical without making their evaluation results an active prerequisite or endorsing their conclusions. No code or artifact deletion is performed during this planning pass.

## 7. Review validation

The architecture, README, role playbooks, model cards, prompt renderer, lifecycle/event paths, dispatch policy, and existing tests were inspected. Five disposable audit tests exercised the findings above; they assert the current observed behavior, not the desired fixed behavior. No model providers or live worker sessions were invoked. Findings retained here rely on code inspection and deterministic diagnostics, not discarded evaluation scores.

The full behavioral suite was started and then stopped when the user reiterated that this is planning only. No failures had been reported before interruption; a full-suite pass is not claimed. Product files and the installed skill were not changed by this planning pass. A comparison found the operational skill files in sync; the repository additionally contains the dashboard test and Python cache directories.
