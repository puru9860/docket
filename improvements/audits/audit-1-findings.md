# Adversarial audit findings

Audit target: `/home/purushottam/Documents/Projects/docket`

Snapshot audited:

- `skills/docket/bin/docket`: `sha256:fb7fa7f494f3d9747dbe0e739fa192db049d17b4ccf4d622f4c1dc0a6949d154`
- `skills/docket/tests/test_docket.py`: `sha256:aac1179be11b8bb0fa08dc368ff79c6cd56a821371890f5dfd4ad7dab173786b`

The repository copies still had those hashes after reproduction. All mutations and all
CLI runs were under `/tmp/dk-scratch`; the repository and its `.docket` directory were
read only.

## Reproduction convention

The findings that need a structurally valid task used generated task, scope, and report
files with every `<!-- TODO ... -->` replaced by concrete text, one matching acceptance
criterion (`Audit fixture acceptance.`), `Files changed: none`, and a nonempty
verification section. Those exact fixture files remain under the scratch directories
named below. This helper expresses the deterministic edit used by the reproductions:

```bash
fill_valid() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

d = Path(sys.argv[1])
owner = "T01"

p = d / f"{owner}-task.mdx"
s = p.read_text()
s = s.replace(f"# Task {owner}: <title>", f"# Task {owner}: audit fixture")
s = s.replace("<!-- TODO: one paragraph. What must be true when this is done. -->",
              "Audit fixture goal.")
s = s.replace("<!-- Each line becomes a checkbox in the report. Make them mechanically checkable. -->\n\n- [ ] <!-- TODO -->",
              "- [ ] Audit fixture acceptance.")
p.write_text(s)

p = d / f"{owner}-scope.mdx"
s = p.read_text()
replacements = {
  "<!-- TODO: what you inspected and why this is enough context to implement. -->": "Inspected the audit fixture.",
  "<!-- TODO: explain every proposed path in frontmatter `files:`. -->": "The generated path is the boundary.",
  "<!-- TODO: useful paths and symbols for implementation and later recovery. -->": "src/a.py",
  "<!-- TODO: explain the exact frontmatter `verify:` command and what it proves. -->": "The registered command is the audit oracle.",
  "<!-- TODO: unresolved discovery uncertainty, or \"none\". -->": "none",
}
for old, new in replacements.items():
    s = s.replace(old, new)
p.write_text(s)

p = d / f"{owner}-report-01.mdx"
s = p.read_text()
replacements = {
  "<!-- TODO: what you actually did, 2-4 sentences. No plans, only past tense. -->": "Prepared the audit fixture.",
  "<!-- TODO: one bullet per change as `path/to/file.py:120` plus a short note. Write \"none\" if nothing changed. -->": "none",
  "<!-- Copy every criterion from the task. Check a box only when it genuinely passes. -->\n\n- [ ] <!-- TODO -->": "- [x] Audit fixture acceptance.",
  "<!-- TODO: the exact command you ran and its real output. Never claim a result you did not see. -->": "The registered command was exercised by the submission gate.",
}
for old, new in replacements.items():
    s = s.replace(old, new)
p.write_text(s)
PY
}
```

## Reproduced findings

### 1. Attempt 100 is sorted behind attempt 99, so a final verifier failure can be approved

**Claim:** Once a round reaches 100 verifier attempts, Docket treats attempt 99 as the
latest. A pass at 99 followed by a fail at 100 produces no failure event and can be
approved.

**Severity:** Critical. This is an accepting-verdict bypass: the newest verifier verdict
is a failure, but the task becomes approved.

**Commands:** Reproduced in `/tmp/dk-scratch/env-mismatch`; the minimal clean sequence is:

```bash
D=/tmp/dk-scratch/snapshot/docket/bin/docket
W=/tmp/dk-scratch/attempt-100
mkdir -p "$W" && cd "$W"
python3 "$D" init R --mode standard --evidence-mode documents-only
python3 "$D" assign R T01 --file src/a.py --verify true
fill_valid "$W/.docket/runs/R"
python3 "$D" scope R T01 --submit
python3 "$D" submit R T01 --as implementor
for i in $(seq 1 99); do
  python3 "$D" verify R T01 --result pass --as verifier --detail "pass $i"
done
python3 "$D" verify R T01 --result fail --as verifier \
  --detail 'final attempt found a release-blocking defect'
python3 "$D" events R --role reviewer --peek
python3 "$D" decide R T01 --approve --as reviewer
rg '^(status|verdict|verification):' \
  .docket/runs/R/T01-{report-01,decision-01}.mdx
```

**Observed:** The final write said `recorded T01-verification-100.mdx: fail`. Reviewer
events then said `no derived events for this role`. Approval succeeded:

```text
T01 round 1 approved -> T01-decision-01.mdx
T01-decision-01.mdx: verdict: approved
T01-decision-01.mdx: verification: T01-verification-99.mdx
T01-report-01.mdx: status: approved
```

Files 98, 99, and 100 all carried the same bundle digest; 98 and 99 were `pass`, while
100 was `fail`.

**Expected:** Attempt 100 must be the latest attempt. The failure must derive one
`verification-failed` reviewer event, keep milestone/frontier readiness false, and block
approval. `ARCHITECTURE.md` says a changed verdict moves readiness, a fail is excluded
from review readiness, and an accepting decision requires a passing verification for the
exact evidence. Invariants 14 and 24 make that binding explicit.

**File and symbols:** `skills/docket/bin/docket` - `verification_paths` lexically sorts
filenames, while `latest_verification`, `matching_verifications`,
`round_failed_verification`, `verification_fragment`, and `decide_locked` all rely on the
last lexical entry.

**Evidence status:** Reproduced by execution. No inference is needed.

### 2. Quick mode can approve a bundle whose registered verification was deliberately skipped

**Claim:** Quick mode accepts `--skip-verify`, lets the combined checker record a pass,
and approves the task even when the registered command is `false`.

**Severity:** High. This defeats an explicit defining promise of the quick preset and
allows the preset to merge duties through the forbidden shortcut.

**Commands:** Reproduced in `/tmp/dk-scratch/quick-skip`:

```bash
D=/tmp/dk-scratch/snapshot/docket/bin/docket
W=/tmp/dk-scratch/quick-skip-repro
mkdir -p "$W" && cd "$W"
python3 "$D" init R --mode quick --evidence-mode documents-only
python3 "$D" assign R T01 --file src/a.py --verify false
fill_valid "$W/.docket/runs/R"
python3 "$D" scope R T01 --submit
python3 "$D" submit R T01 --as implementor --skip-verify \
  --skip-verify-reason 'registered command is broken'
python3 "$D" verify R T01 --result pass --as checker \
  --verifier checker --detail 'accepted by inspection'
python3 "$D" decide R T01 --approve --as checker --reviewer checker
python3 "$D" bundle R T01
```

**Observed:** All three lifecycle commands exited zero. `docket bundle` reported:

```text
verification skipped exit None  false
```

The report ended `status: approved`; the decision recorded `verdict: approved` and
`review_policy: combined-checker`.

**Expected:** Quick mode must refuse `--skip-verify`, or at minimum refuse an accepting
decision over a bundle whose captured submission verification is `skipped`. The plan,
README, SKILL.md, coordinator playbook, and `ARCHITECTURE.md` all state that quick never
uses `--skip-verify` and never merges duties through that shortcut. Phase 5 acceptance
also requires quick to preserve the gate and evidence invariants.

**File and symbols:** `skills/docket/bin/docket` - `submit_locked` permits skipped
verification without checking `mode_of(d)`, and `decide_locked` checks only the separately
authored checker/verifier artifact, not the bundle's captured verification status.

**Evidence status:** Reproduced by execution.

### 3. Dependency cycles are accepted and leave every role with no actionable event

**Claim:** Both assignment dependencies and closed-batch dependency edges accept cycles.
Every member is then undispatchable, but planner, orchestrator, verifier, and reviewer all
derive no event.

**Severity:** High. The run enters a durable deadlock through supported commands. A closed
batch has no command to remove or repair its edges.

**Commands:** Task-frontmatter cycle reproduced in `/tmp/dk-scratch/task-cycle`:

```bash
D=/tmp/dk-scratch/snapshot/docket/bin/docket
W=/tmp/dk-scratch/task-cycle-repro
mkdir -p "$W" && cd "$W"
python3 "$D" init R --mode standard --evidence-mode documents-only
python3 "$D" assign R T01 --file src/a.py --verify true --depends-on T02
python3 "$D" assign R T02 --file src/b.py --verify true --depends-on T01
python3 "$D" session R --register --session w1 --name worker --role implementor
python3 "$D" dispatch R T01 --session w1
python3 "$D" dispatch R T02 --session w1
for role in planner orchestrator verifier reviewer; do
  python3 "$D" events R --role "$role" --peek
done
```

The batch form was also reproduced in `/tmp/dk-scratch/batch-cycle` using:

```bash
python3 "$D" batch R --create B1 --members T01,T02 \
  --depends-on T01:T02 --depends-on T02:T01
python3 "$D" batch R --close B1
```

**Observed:** Both dispatches were refused:

```text
docket: T01 cannot dispatch with unmet dependencies:
      - T02 (draft)
docket: T02 cannot dispatch with unmet dependencies:
      - T01 (draft)
```

Every role printed `no derived events for this role`.

**Expected:** The dependency ingress must reject a self-edge or cycle before publishing
it, or derive an actionable coordination/plan event that identifies the cycle and has a
supported recovery. The architecture promises deterministic dependency routing and
frontiers that keep dependency graphs progressing; the audit brief specifically calls
out reachable states where nobody receives an actionable event.

**File and symbols:** `skills/docket/bin/docket` - `cmd_assign` records `depends_on`
without graph validation; `cmd_batch` validates targets but not edge sources or cycles;
`dispatch_dependencies_unmet` refuses every cycle member; `derive_events` and
`frontier_ready` emit nothing until one member has submitted verified work, which a cycle
prevents.

**Evidence status:** Reproduced through both supported dependency surfaces.

### 4. Dispatch bypasses the advertised task-intent gate and binds a prompt containing TODOs

**Claim:** `docket validate-task` rejects an untouched generated task, but
`docket dispatch` accepts the same task and stores a prompt digest for a prompt containing
the unresolved goal and acceptance placeholders.

**Severity:** High. The task-intent "gate" is advisory at the exact command boundary that
starts work, so implementors can be dispatched without an executable contract.

**Commands:** Reproduced in `/tmp/dk-scratch/invalid-dispatch`:

```bash
D=/tmp/dk-scratch/snapshot/docket/bin/docket
W=/tmp/dk-scratch/invalid-dispatch-repro
mkdir -p "$W" && cd "$W"
python3 "$D" init R --mode standard --evidence-mode documents-only
python3 "$D" assign R T01 --file src/a.py --verify true
python3 "$D" validate-task R T01
python3 "$D" session R --register --session w1 --name worker --role implementor
python3 "$D" dispatch R T01 --session w1
python3 "$D" prompt R T01 --role implementor --max-tokens 0
```

**Observed:** `validate-task` exited 1 with:

```text
INVALID TASK: T01-task.mdx
  - section '## Goal' is empty
  - unresolved placeholder ... <!-- TODO ... -->
```

Dispatch then exited zero with `dispatched T01 round 1` and a nonempty prompt digest. The
rendered prompt contained:

```text
goal: <!-- TODO: one paragraph. What must be true when this is done. -->
acceptance:
- <!-- TODO -->
```

**Expected:** Dispatch must run the same task-intent validation and refuse before writing
a dispatch record. `ARCHITECTURE.md` says mechanical gates validate task intent. The
orchestrator playbook says to validate before dispatch and "Do not dispatch a task that
fails this gate." Phase 4 acceptance requires prompts without missing-task placeholders.

**File and symbols:** `skills/docket/bin/docket` - `cmd_validate_task` owns the validation,
but neither `cmd_dispatch` nor `rendering_problem` invokes it; `compose_prompt` consequently
renders comment placeholders as mandatory contract text.

**Evidence status:** Reproduced by execution.

### 5. Frozen verification records a declared environment value but executes a different ambient value

**Claim:** A task can declare `FOO=declared`, while submission runs and passes against
ambient `FOO=actual`; the frozen bundle records only the false declared value and not the
value that satisfied the command.

**Severity:** High. The gate can be satisfied by unrecorded ambient state while the bundle
claims a different environment, so the captured verification is not reproducible evidence
of what ran.

**Commands:** Reproduced in `/tmp/dk-scratch/env-mismatch` before the later attempt-count
experiment:

```bash
D=/tmp/dk-scratch/snapshot/docket/bin/docket
W=/tmp/dk-scratch/env-mismatch-repro
mkdir -p "$W" && cd "$W"
python3 "$D" init R --mode standard --evidence-mode documents-only
python3 "$D" assign R T01 --file src/a.py \
  --verify 'test "$FOO" = actual' --env FOO=declared
fill_valid "$W/.docket/runs/R"
python3 "$D" scope R T01 --submit
FOO=actual python3 "$D" submit R T01 --as implementor
python3 - <<'PY'
import json
from pathlib import Path
p = next(Path('.docket/runs/R/.bundles/T01/01').glob('*/bundle.json'))
v = json.loads(p.read_text())['verification']
print(v['status'], v['command'], v['declared_env'])
PY
```

**Observed:** Submission printed `verify passed`. The frozen record printed:

```text
passed test "$FOO" = actual ['FOO=declared']
```

No actual `FOO=actual` value, environment digest, or ambient `PATH` was frozen.

**Expected:** The verifier must either execute with the declared environment or capture
the actual relevant environment values used by the child. It must not label an unobserved
declaration as the command's environment. `ARCHITECTURE.md` says captured verification
describes its environment, and invariant 24 says verification is captured whole. Invariant
3 also excludes personal `PATH` additions, but `subprocess.run` inherits the caller's
ambient `PATH` here.

**File and symbols:** `skills/docket/bin/docket` - `task_env` reads declarations;
`run_verification` stores them in `declared_env` but passes no controlled `env` to
`subprocess.run`; `cmd_preflight` has the same ambient-execution behavior.

**Evidence status:** Reproduced by execution. The additional `PATH` consequence is inferred
from the same executed code path and the direct `subprocess.run` call; it is not a separate
finding.

### 6. A successful re-review decision leaves the report pointing at the superseded bundle

**Claim:** When `--re-review` freezes a replacement bundle and pauses for a new independent
verifier pass, the later successful approval binds the new bundle in the decision but
leaves the approved report's `bundle_digest` on the original bundle.

**Severity:** Medium. The gate itself selects the new ledger entry, but the lifecycle
document exposes a false evidence pointer after approval. Consumers that trust report
frontmatter read the wrong immutable evidence.

**Commands:** Reproduced in `/tmp/dk-scratch/rereview-binding`:

```bash
D=/tmp/dk-scratch/snapshot/docket/bin/docket
W=/tmp/dk-scratch/rereview-binding-repro
mkdir -p "$W" && cd "$W"
python3 "$D" init R --mode standard --evidence-mode documents-only
python3 "$D" assign R T01 --file src/a.py --verify true
fill_valid "$W/.docket/runs/R"
python3 "$D" scope R T01 --submit
python3 "$D" submit R T01 --as implementor
python3 "$D" verify R T01 --result pass --as verifier
python3 - <<'PY'
from pathlib import Path
p = Path('.docket/runs/R/T01-report-01.mdx')
p.write_text(p.read_text().replace('Prepared the audit fixture.',
                                   'Prepared the edited audit fixture.'))
PY
python3 "$D" decide R T01 --approve --as reviewer --re-review
python3 "$D" verify R T01 --result pass --as verifier
python3 "$D" decide R T01 --approve --as reviewer
rg '^(status|verdict|bundle_digest|verification):' \
  .docket/runs/R/T01-{report-01,decision-01}.mdx
python3 "$D" bundle R T01 --list
```

The first decision command is expected to exit 1 after freezing the replacement and
requesting an independent pass; the remaining commands continue.

**Observed:** The first command froze
`sha256:67ba2df...` and then refused because verification 01 bound the old
`sha256:d64adb...`. After verification 02, approval succeeded. Final artifacts said:

```text
T01-decision-01.mdx: verdict: approved
T01-decision-01.mdx: bundle_digest: sha256:67ba2df...
T01-decision-01.mdx: verification: T01-verification-02.mdx
T01-report-01.mdx: status: approved
T01-report-01.mdx: bundle_digest: sha256:d64adb...
```

`docket bundle --list` confirmed that `d64adb...` was the original submission and
`67ba2df...` was the re-review bundle.

**Expected:** The report's lifecycle update must publish the accepted replacement digest,
matching the decision. `ARCHITECTURE.md` states that `docket submit` records the frozen
address in the report and that a re-review freezes the changed body as a new bundle to
which the decision binds. The code's own re-review branch assigns
`report_meta["bundle_digest"]` to the replacement, showing that this is intended state.

**File and symbols:** `skills/docket/bin/docket` - `decide_locked`. The failed re-review
keeps the new bundle but does not publish its updated `report_meta`; the next invocation
sees a current matching bundle, skips the re-review branch, and `commit_transition`
publishes the old report frontmatter with the new decision.

**Evidence status:** Reproduced by execution.

## Inspected or exercised areas with no additional finding

- Baseline reconstruction, multi-root qualification, task and aggregate bundle integrity,
  provisional dependency pinning, transition recovery, delivery leases, release freezing,
  and strict unittest-summary parsing were exercised by the copied behavioral suite. The
  suite ran 244 tests. The first run had 237 passes and seven document-location checks
  failing only because the audit snapshot was one directory shallower than the repository
  layout those tests derive from `DOCKET_BIN`; after mirroring `ARCHITECTURE.md`,
  `README.md`, and `docs/` into the expected disposable layout, all seven reran green.
- Root declaration and baseline code was inspected against invariants 17-21. I did not
  independently reproduce a new defect there.
- Release inventory and frozen-bundle validation were inspected, including symlink, mode,
  added-file, and source-staleness paths. I did not reproduce a new defect there.
- Qualified delivery and lease reconciliation were inspected and exercised by the suite.
  I did not reproduce a new defect there.

## Inference-only observations

None reported as findings. Every numbered finding above was executed in a disposable run.
