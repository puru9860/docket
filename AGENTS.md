# Working on docket

Instructions for an agent modifying this repository. If you want to *use* docket in a
project, that is a different thing: read `skills/docket/SKILL.md` and run
`docket help <role>`.

## Read first

1. **`ARCHITECTURE.md`** - the data model, the lifecycle, why signalling works the way it
   does, the invariants, and the known gaps. Do not change behaviour before reading the
   invariants section; several of them exist because the alternative shipped and broke.
2. `README.md` - what users are promised.

## Where things live

Everything is one stdlib-only Python script: `skills/docket/bin/docket`.
`ARCHITECTURE.md` has a code map of its regions.

The repo layout is fixed by the `skills` CLI convention - `skills/<name>/SKILL.md` is how
installers find it. Do not move it.

## Before you finish

```bash
tests/test.sh          # must be 0 failures
```

**Add a test for whatever you changed.** This is not a formality: the one significant bug
this project has shipped - the planner never being woken - existed because that code path
had never been exercised, while the neighbouring path had. A change with no new assertion
is a change nobody can verify later.

If you fix a bug, the test should fail before your fix and pass after. Check that.

## Two copies exist

The repo copy and the installed copy at `~/.agents/skills/docket/` are separate
directories. After editing, keep them in sync or you will test one and ship the other:

```bash
cp -r ~/Documents/Projects/docket/skills/docket/. ~/.agents/skills/docket/
diff -r ~/.agents/skills/docket ~/Documents/Projects/docket/skills/docket
```

## House rules

- No dependencies. `bin/docket` is stdlib-only and must stay runnable as a plain
  `python3` script if someone swaps the shebang.
- No em dashes in prose, use a plain dash.
- Playbooks live in the `PLAYBOOKS` dict in the CLI, never in `SKILL.md`. That is what
  keeps installed copies from going stale.
- Do not weaken the gate. If a report is getting rejected, the report is wrong, not the
  gate. `--skip-verify` exists for a broken verify command, not for a hurried report.
- Do not make `status: blocked` harder to use than submitting. An honest block is the
  behaviour the whole design is trying to buy.
- Keep frontmatter flat. No YAML library, no nested structures, no parallel state file.

## Things that look like bugs but are not

- The watcher exits 0 silently when nothing is actionable. That is the common case.
- An unarmed role is never woken. That is by design; `docket doctor` warns about it.
- `verify:` runs after the structural checks, not before. Ordering is deliberate.
- `docket submit` refuses a report that is already `submitted`. Also deliberate.
