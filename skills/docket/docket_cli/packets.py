"""Bounded review packets and the final packet."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .common import COVERAGE_AVAILABLE, WORKFLOW_LEGACY_DECODE, die
from .frontmatter import parse, render, sections
from .paths import _ordered, owners, planned_tasks
from .publication import publish
from .policy import need_run, workflow_of
from .bundles import bundle_dir, bundle_problems, bundles_for, latest_bundle, load_bundle
from .state import (
    latest_verification, list_amendments, list_incidents, read_batch, state_of,
    verification_paths,
)
from .freeze import current_bundle
from .aggregates import aggregate_bundle_problems
from .gate import parse_evidence_table, task_criterion_ids
from .release import (
    RELEASE_MILESTONES, UNAVAILABLE_MILESTONE, find_bundle_by_digest, frozen_bundle_view,
    installation_state, packet_staleness, resolve_release, validate_frozen_release,
)


PACKET_TOKEN_TARGET = 2000


def packet_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def packet_text_block(label: str, text: str, indent: str = "  ") -> list[str]:
    """A labelled packet block whose body is preserved whole."""
    lines = [label]
    body = text.strip()
    if not body:
        lines.append(f"{indent}(none recorded)")
    else:
        lines.extend(f"{indent}{line}" if line else "" for line in body.splitlines())
    return lines


def packet_finding_lines(name: str, meta: dict[str, str], body: str) -> list[str]:
    """Verifier metadata and its substantive finding body, never truncated."""
    heading = (f"- {name}: {meta.get('result', '?')} "
               f"(round {meta.get('round', '?')}, attempt {meta.get('attempt', '?')})")
    return packet_text_block(heading + "\n  findings:",
                             sections(body).get("Findings", ""), indent="    ")


def packet_decision_lines(name: str, meta: dict[str, str], body: str) -> list[str]:
    """Decision material shared by live and frozen packet paths."""
    verdict = meta.get("verdict", "?")
    if verdict == "waived":
        return packet_text_block(f"- {name}: waived\n  complete waiver reason:",
                                 sections(body).get("Reason", ""), indent="    ")
    if verdict == "changes-requested":
        trigger = meta.get("triggered_by", "reviewer")
        return packet_text_block(
            f"- {name}: changes-requested (triggered_by {trigger})\n"
            "  outstanding required changes:",
            sections(body).get("Required changes", ""), indent="    ")
    return [f"- {name}: {verdict}"]


def packet_report_decision_lines(name: str, report_body: str) -> list[str]:
    """The report's own question, including an explicit `none`, in its own words."""
    authored = re.sub(r"<!--.*?-->", "",
                      sections(report_body).get("Decisions needed", ""),
                      flags=re.S).strip()
    return packet_text_block(f"- {name}:", authored,
                             indent="  ")


def render_bounded_packet(lines: list[str], diff_blocks: list[tuple[str, list[str]]],
                          mandatory_blocks: list[str],
                          required_reading: list[str]) -> str:
    """Shrink routine diff excerpts before declaring an honest packet overage."""
    def render(keep: int) -> str:
        rendered = []
        for line in lines:
            if line.startswith("@@diff:") and line.endswith("@@"):
                pointer, body = diff_blocks[int(line[len("@@diff:"):-len("@@")])]
                rendered.append(pointer.format(keep=min(keep, len(body))))
                rendered.extend(f"    {item}" for item in body[:keep])
            else:
                rendered.append(line)
        return "\n".join(rendered) + "\n"

    keep = 40
    packet = render(keep)
    while packet_tokens(packet) > PACKET_TOKEN_TARGET and keep > 0:
        keep //= 2
        packet = render(keep)
    if packet_tokens(packet) <= PACKET_TOKEN_TARGET:
        return packet

    mandatory_tokens = packet_tokens("\n\n".join(mandatory_blocks)) \
        if mandatory_blocks else 0
    if mandatory_tokens > PACKET_TOKEN_TARGET:
        statement = ("Mandatory material exceeds the packet target: "
                     f"{mandatory_tokens} estimated tokens of findings, complete waiver "
                     "reasons, required changes, and report decisions are retained.")
    else:
        statement = ("The packet remains above the target after routine evidence excerpts "
                     "were reduced to pointers; mandatory material is retained whole.")
    manifest = []
    for artifact in required_reading:
        if artifact and artifact not in manifest:
            manifest.append(artifact)
    packet += "\n## Packet size overage\n\n" + statement + \
        "\n\n## Required-reading manifest\n\n"
    packet += "\n".join(f"- {artifact}" for artifact in manifest) \
        if manifest else "- No separate mandatory artifact was recorded."
    return packet.rstrip() + "\n"


def task_coverage_row(d: Path, owner: str) -> dict[str, str]:
    """One task's acceptance coverage for a review packet."""
    try:
        rnd, st = state_of(d, owner)
    except (OSError, ValueError):
        return {"owner": owner, "state": "unknown"}
    row = {"owner": owner, "round": str(rnd), "state": st, "bundle": "",
           "verification": "", "decision": ""}
    if rnd:
        frozen, _ = current_bundle(d, owner, rnd)
        row["bundle"] = str((frozen or {}).get("digest", ""))
        decs = _ordered(d.glob(f"{owner}-decision-*.mdx"))
        if decs:
            row["decision"] = decs[-1].name
        _vpath, vmeta, _ = latest_verification(d, owner, rnd)
        if vmeta:
            row["verification"] = f"{vmeta.get('result', '?')} ({vmeta.get('attempt', '?')})"
    return row


def render_final_packet(a: argparse.Namespace, d: Path) -> None:
    """Render a final packet purely from one validated frozen release.

    Takes the release directory and its manifest (already resolved and fully
    revalidated by validate_frozen_release). Every rendered approval input
    comes from frozen bytes addressed by the manifest; only the installation
    state stays live, labeled as an environmental check. Never invents bundle
    digests.
    """
    rel_dir = a._release_dir
    manifest = a._release_manifest
    files = manifest.get("files", {})
    digest = str(manifest.get("digest", ""))
    frozen_plan = (rel_dir / "documents/plan.mdx").read_text(errors="replace")
    frozen_meta, frozen_body = parse(frozen_plan)
    frozen_psecs = sections(frozen_body)
    frozen_workflow = str(frozen_meta.get("workflow", "")).strip() or WORKFLOW_LEGACY_DECODE
    frozen_docs: dict[str, str] = {}
    for name in ("incidents", "escalations", "amendments"):
        try:
            frozen_docs[name] = (rel_dir / f"snapshot/{name}.json").read_text()
        except OSError:
            frozen_docs[name] = "[]"
    mandatory_blocks: list[str] = []
    required_reading: list[str] = []
    lines = [f"# Final review packet: {a.run}", "",
             f"workflow: {frozen_workflow}",
             f"packet: final",
             f"release: {digest} (frozen {manifest.get('frozen_at', '?')})", "",
             "## Objective (frozen plan)", "",
             (frozen_psecs.get("Objective", "").strip() or "(no objective recorded)"), ""]
    lines += ["", "## Release evidence", "",
              f"- release digest: {digest}",
              f"- frozen at: {manifest.get('frozen_at', '?')}",
              f"- aggregate: {manifest['aggregate']['digest']} "
              f"(orch round {manifest['aggregate']['round']})",
              f"- suite artifact digest: {files['suite/artifact.json']['sha256'][:19]}..."]
    lines += ["", "## Milestone inventory (M4-M11)", ""]
    for milestone in RELEASE_MILESTONES:
        pinned = manifest.get("milestones", {}).get(milestone, "")
        if not pinned or pinned == UNAVAILABLE_MILESTONE:
            lines.append(f"- {milestone}: {UNAVAILABLE_MILESTONE}")
        else:
            lines.append(f"- {milestone}: bundle {pinned}")
    lines += ["", "## Acceptance coverage (frozen constituents)", ""]
    for item in manifest.get("constituents", []):
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner", ""))
        lines.append(f"- {owner} round {item.get('round')}: {item.get('state', '?')} "
                     f"(bundle {item.get('bundle', 'none')}, "
                     f"verification {item.get('verification') or 'none'}, "
                     f"decision {item.get('decision') or 'none'})")
        try:
            frozen_task = (rel_dir / f"documents/tasks/{owner}-task.mdx").read_text(
                errors="replace")
        except OSError:
            frozen_task = ""
        aids = task_criterion_ids(d, owner, text=frozen_task)
        if aids:
            _, entry = find_bundle_by_digest(d, str(item.get("bundle", "")))
            ev_states: dict[str, str] = {}
            if entry is not None:
                try:
                    frozen_body = (bundle_dir(d, owner, entry) / "report.mdx"
                                   ).read_text(errors="replace")
                    table_rows, _ = parse_evidence_table(frozen_body)
                    if table_rows is not None:
                        for row in table_rows:
                            ev_states[str(row.get("id", "")).upper()] = str(row.get("state", ""))
                except OSError:
                    pass
            if ev_states:
                for aid in aids:
                    lines.append(f"  - {owner} {aid}: {ev_states.get(aid, 'not-verified')}")
            else:
                lines.append(f"  - {owner} acceptance IDs: {', '.join(aids)}")
    owner, entry = find_bundle_by_digest(d, str(manifest["aggregate"]["digest"]))
    orch_manifest = load_bundle(d, "orch", entry) if entry is not None else None
    lines += ["", "## Aggregate bundle (frozen)", ""]
    if orch_manifest is not None and entry is not None:
        lines.append(f"- orch round {manifest['aggregate']['round']} "
                     f"bundle {manifest['aggregate']['digest']}")
        for item in manifest.get("constituents", []):
            if isinstance(item, dict):
                lines.append(f"  - constituent {item.get('owner')} round "
                             f"{item.get('round')}: bundle {item.get('bundle')}")
        for root_entry in ((orch_manifest.get("patch", {}) or {}).get("roots", []) or []):
            if isinstance(root_entry, dict):
                lines.append(f"  - aggregate root {root_entry.get('alias')}: "
                             f"baseline {str(root_entry.get('baseline_tree', ''))[:12]} -> "
                             f"source {str(root_entry.get('source_tree', ''))[:12]} "
                             f"({root_entry.get('file')})")
    lines += ["", "## Integration verification (frozen)", ""]
    if orch_manifest is not None and entry is not None:
        ver = orch_manifest.get("verification", {})
        ver = ver if isinstance(ver, dict) else {}
        where = bundle_dir(d, "orch", entry)
        lines.append(f"- aggregate bundle {manifest['aggregate']['digest']} verification: "
                     f"{ver.get('status', '?')} (exit {ver.get('returncode', '?')})")
        lines.append(f"  command: {ver.get('command', '(none)')} (cwd {ver.get('cwd', '?')})")
        lines.append(f"  ran: {ver.get('started_at', '?')} to {ver.get('ended_at', '?')} "
                     f"({ver.get('duration_seconds', '?')}s)")
        for key in ("stdout", "stderr"):
            ref = ver.get(key, {})
            if isinstance(ref, dict) and ref.get("file"):
                lines.append(f"  {key}: {ref.get('file')} "
                             f"({ref.get('bytes', '?')} bytes, {ref.get('sha256', '?')}; "
                             f"full text frozen in the bundle at {where.name})")
    lines += ["", "## Evidence deltas (frozen bundles)", ""]
    diff_blocks: list[tuple[str, list[str]]] = []
    pinned: list[tuple[str, dict]] = []
    for item in manifest.get("constituents", []):
        if isinstance(item, dict) and item.get("bundle"):
            _, centry = find_bundle_by_digest(d, str(item.get("bundle", "")))
            if centry is not None:
                pinned.append((str(item.get("owner", "")), centry))
    if entry is not None:
        pinned.append(("orch", entry))
    for owner, centry in pinned:
        cmanifest = load_bundle(d, owner, centry)
        where = bundle_dir(d, owner, centry)
        lines.append(f"- {owner} bundle {centry.get('digest')}")
        patch_roots = ((cmanifest or {}).get("patch", {}) or {}).get("roots", [])
        for root_entry in patch_roots if isinstance(patch_roots, list) else []:
            name = str(root_entry.get("file", ""))
            alias = str(root_entry.get("alias", ""))
            try:
                diff_text = (where / name).read_text(errors="replace") if name else ""
            except OSError:
                diff_text = ""
            qualified = f"{alias}:{name}" if alias else name
            diff_blocks.append((f"  patch {qualified} ({len(diff_text.splitlines())} lines; "
                                "first {keep} shown; full text in the bundle)",
                                diff_text.splitlines()))
            lines.append(f"@@diff:{len(diff_blocks) - 1}@@")
    lines += ["", "## Verifier findings, waivers, and unresolved risks (frozen)", ""]
    for item in manifest.get("constituents", []):
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner", ""))
        verification_names = item.get("verifications", [])
        if not isinstance(verification_names, list):
            verification_names = []
        if not verification_names and item.get("verification"):
            verification_names = [item.get("verification")]
        for raw_name in verification_names:
            name = str(raw_name or "")
            if not name:
                continue
            try:
                meta, body = parse((rel_dir / f"documents/verifications/{name}").read_text())
            except (OSError, ValueError):
                lines.append(f"- {name}: frozen copy unreadable")
                continue
            block = packet_finding_lines(name, meta, body)
            lines.extend(block)
            mandatory_blocks.append("\n".join(block))
            required_reading.append(f"documents/verifications/{name}")
        decision_name = str(item.get("decision") or "")
        if decision_name:
            try:
                dmeta, dbody = parse(
                    (rel_dir / f"documents/decisions/{decision_name}").read_text())
            except (OSError, ValueError):
                lines.append(f"- {decision_name}: frozen copy unreadable")
            else:
                block = packet_decision_lines(decision_name, dmeta, dbody)
                lines.extend(block)
                if dmeta.get("verdict") in ("waived", "changes-requested"):
                    mandatory_blocks.append("\n".join(block))
                    required_reading.append(f"documents/decisions/{decision_name}")
    try:
        frozen_incidents = json.loads(frozen_docs["incidents"])
        frozen_escalations = json.loads(frozen_docs["escalations"])
        frozen_amendments = json.loads(frozen_docs["amendments"])
    except ValueError:
        frozen_incidents, frozen_escalations, frozen_amendments = [], [], "unreadable"
    if frozen_amendments == "unreadable":
        lines.append("- frozen run-state snapshot is unreadable")
    else:
        for incident in frozen_incidents if isinstance(frozen_incidents, list) else []:
            if isinstance(incident, dict) and incident.get("state") == "open":
                lines.append(f"- open stall: {incident.get('id')}: {incident.get('cause')}")
        for entry in frozen_escalations if isinstance(frozen_escalations, list) else []:
            if isinstance(entry, dict) and entry.get("state") == "open":
                lines.append(f"- open escalation: {entry.get('id')}")
        for entry in frozen_amendments if isinstance(frozen_amendments, list) else []:
            if isinstance(entry, dict) and entry.get("status") == "pending":
                lines.append(f"- pending amendment: {entry.get('id')}: {entry.get('need')}")
    if frozen_psecs.get("Risks", "").strip():
        lines.append("- plan risks: (complete, never shortened; frozen plan)")
        for rline in frozen_psecs.get("Risks", "").strip().splitlines():
            lines.append(f"  {rline}" if rline.strip() else "  (blank)")
    lines += ["", "## Full-suite result (frozen)", ""]
    suite = manifest.get("suite", {})
    lines.append(f"- command: {suite.get('command', '?')} (exit {suite.get('exit', '?')})")
    lines.append(f"- tests: {suite.get('tests', '?')}, failures: {suite.get('failures', '?')}, "
                 f"duration: {suite.get('duration_s', suite.get('duration', '?'))}s")
    lines.append(f"- log digest: {files['suite/stdout.txt']['sha256']}")
    lines.append(f"- ran: {suite.get('started_at', '?')} to {suite.get('ended_at', '?')} "
                 f"in {suite.get('cwd', '?')}")
    lines.append(f"- source tree at execution: {str(suite.get('source', '?'))[:19]}...")
    lines += ["", "## Real-adapter result (frozen qualifications)", ""]
    for rel in sorted(name for name in files if name.startswith("qualifications/")):
        try:
            frozen_qual = json.loads((rel_dir / rel).read_bytes().decode())
        except (OSError, ValueError):
            lines.append(f"- {rel}: frozen copy unreadable")
            continue
        if not isinstance(frozen_qual, dict):
            lines.append(f"- {rel}: frozen copy malformed")
            continue
        probe = frozen_qual.get("probe", {}) if isinstance(frozen_qual.get("probe"), dict) else {}
        lines.append(f"- {frozen_qual.get('role', '?')}: {frozen_qual.get('status', '?')} "
                     f"(mode {frozen_qual.get('mode', '?')}, boundary "
                     f"{probe.get('boundary', '?')}, adapter "
                     f"{probe.get('herdr_version', '?')})")
        lines.append(f"  event: {str((frozen_qual.get('event', {}) or {}).get('key', '?'))}; "
                     f"artifact digest: {files[rel]['sha256'][:19]}...")
    lines += ["", "## Installation state (live check)", "",
              f"- {installation_state()}", ""]
    lines += ["## Decisions needed (report-authored)", ""]
    for item in manifest.get("constituents", []):
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner", ""))
        _found_owner, centry = find_bundle_by_digest(d, str(item.get("bundle", "")))
        if centry is None:
            continue
        try:
            report_body = parse((bundle_dir(d, owner, centry) / "report.mdx").read_text())[1]
        except (OSError, ValueError):
            continue
        report_name = f"{owner}-report-{int(item.get('round', 0) or 0):02d}.mdx"
        block = packet_report_decision_lines(report_name, report_body)
        lines.extend(block)
        mandatory_blocks.append("\n".join(block))
        required_reading.append(report_name)
    if entry is not None:
        try:
            orch_report_body = parse((bundle_dir(d, "orch", entry) / "report.mdx").read_text())[1]
        except (OSError, ValueError):
            pass
        else:
            orch_name = f"orch-report-{int(manifest['aggregate']['round']):02d}.mdx"
            block = packet_report_decision_lines(orch_name, orch_report_body)
            lines.extend(block)
            mandatory_blocks.append("\n".join(block))
            required_reading.append(orch_name)
    lines.append("")
    packet = render_bounded_packet(lines, diff_blocks, mandatory_blocks,
                                   required_reading)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        publish(out, packet)
        print(f"wrote final reviewer packet for {a.run} -> {out} "
              f"({packet_tokens(packet)} estimated tokens)")
    else:
        print(packet, end="")
        print(f"---\npacket: {packet_tokens(packet)} estimated tokens "
              f"(final release packet; evidence sections never shortened)",
              file=sys.stderr)


def cmd_review_packet(a: argparse.Namespace) -> None:
    """Generate a self-contained reviewer packet with pinned revisions.

    The main packet targets roughly 1,000-2,000 estimated tokens: evidence
    bodies shrink first (with pointers to the full immutable artifacts) while
    risks, waivers, and decisions needed are never shortened away. An approval
    binds the exact revisions named here.

    Every referenced task bundle and the aggregate bundle (when one exists)
    is validated with the full bundle integrity checks before rendering. A
    missing, damaged, stale, or unbound bundle refuses the packet with a
    specific error rather than rendering reviewable-looking evidence. `--final`
    additionally requires the aggregate's integration verification to exist,
    have passed, keep its outputs, and still describe the frozen source; task
    and milestone packets legitimately precede an aggregate and must never be
    mistaken for final qualification.
    """
    d = need_run(a.run)
    if a.role != "reviewer":
        die("--role reviewer is required; packets are built for capable review")
    final = bool(a.final)
    if final and a.correction:
        die("--final with --correction is refused: a one-task correction packet "
            "is not final release qualification")
    batch_scope = str(getattr(a, "batch", "") or "").strip()
    if final and batch_scope:
        die("--final with --batch is refused: a batch packet is not final release qualification")
    if a.correction and batch_scope:
        die("--correction with --batch is refused: scope one packet at a time")
    plan = d / "plan.mdx"
    pmeta, pbody = parse(plan.read_text()) if plan.is_file() else ({}, "")
    psecs = sections(pbody)
    planned = [o for o in planned_tasks(d) if o != "orch"]
    task_owners = planned + [o for o in owners(d) if o != "orch" and o not in planned]
    if a.correction and a.correction not in task_owners:
        die(f"no task {a.correction!r} in {a.run}")
    reviewable_states = {"submitted", "blocked", "approved", "waived", "completed"}
    if a.correction:
        focus = [a.correction]
    elif batch_scope:
        batch = read_batch(d, batch_scope)
        if not batch:
            die(f"no batch {batch_scope!r} in {a.run}")
        members = [str(m) for m in (batch.get("members") or [])]
        focus = []
        for member in members:
            try:
                _, st = state_of(d, member)
            except (OSError, ValueError):
                die(f"{member}: cannot read lifecycle state; refusing a packet that "
                    "could be mistaken for reviewable evidence")
            if st in reviewable_states:
                focus.append(member)
        if not focus:
            die(f"batch {batch_scope!r} has no reviewable members; refusing a reviewable packet "
                "with no frozen evidence")
    else:
        focus = []
        for owner in task_owners:
            try:
                _, st = state_of(d, owner)
            except (OSError, ValueError):
                continue
            if st in reviewable_states:
                focus.append(owner)
        if not focus:
            die("no reviewable members; refusing a reviewable packet with no frozen evidence")
    if final:
        rel_dir, rel_manifest, rel_problems = resolve_release(
            d, (a.release or "").strip())
        if rel_problems:
            die("; ".join(rel_problems))
        assert rel_dir is not None and rel_manifest is not None
        with frozen_bundle_view(rel_dir, rel_manifest):
            frozen_problems = validate_frozen_release(d, a.run, rel_dir, rel_manifest)
            if frozen_problems:
                die("; ".join(frozen_problems))
            a._release_dir = rel_dir
            a._release_manifest = rel_manifest
            render_final_packet(a, d)
        return
    for owner in focus:
        try:
            rnd, _st = state_of(d, owner)
        except (OSError, ValueError):
            die(f"{owner}: cannot read lifecycle state; refusing a packet that "
                "could be mistaken for reviewable evidence")
        if not rnd:
            die(f"{owner} has no submitted round; refusing a reviewable packet "
                "with no frozen evidence")
        entries = bundles_for(d, owner, rnd)
        if not entries:
            die(f"{owner} round {rnd} has no frozen bundle; refusing a reviewable packet")
        entry = entries[-1]
        problems = bundle_problems(d, owner, entry)
        if problems:
            die(f"{owner} round {rnd} bundle {entry.get('digest')} is damaged: "
                + "; ".join(problems))
        manifest = load_bundle(d, owner, entry)
        stale = packet_staleness(d, owner, rnd, manifest)
        if stale:
            die(f"{owner} round {rnd} bundle {entry.get('digest')} is stale: "
                + "; ".join(stale))
    orch_entry: dict[str, object] | None = None
    orch_manifest: dict[str, object] | None = None
    orch_rnd = 0
    if not a.correction and not batch_scope:
        try:
            orch_rnd, orch_st = state_of(d, "orch")
        except (OSError, ValueError):
            orch_rnd, orch_st = 0, ""
        if orch_rnd:
            orch_entries = bundles_for(d, "orch", orch_rnd)
            if orch_entries:
                orch_entry = orch_entries[-1]
                agg_problems = aggregate_bundle_problems(d, orch_entry)
                if agg_problems:
                    die(f"aggregate bundle {orch_entry.get('digest')} is damaged or stale: "
                        + "; ".join(agg_problems))
                orch_manifest = load_bundle(d, "orch", orch_entry)
                orch_stale = packet_staleness(d, "orch", orch_rnd, orch_manifest)
                if orch_stale:
                    die(f"aggregate bundle {orch_entry.get('digest')} is stale: "
                        + "; ".join(orch_stale))
    title = f"# Review packet: {a.run}"
    mandatory_blocks: list[str] = []
    required_reading: list[str] = []
    rendered_decisions: set[str] = set()
    lines = [title, "",
             f"workflow: {workflow_of(d)}",
             "packet: standard", "",
             "## Objective", "",
             (psecs.get("Objective", "").strip() or "(no objective recorded)"), ""]
    lines += ["## Acceptance coverage", ""]
    for owner in focus:
        row = task_coverage_row(d, owner)
        lines.append(f"- {row['owner']} round {row['round']}: {row['state']} "
                     f"(bundle {row['bundle'] or 'none'}, "
                     f"verification {row['verification'] or 'none'}, "
                     f"decision {row['decision'] or 'none'})")
        aids = task_criterion_ids(d, owner)
        if aids:
            try:
                rnd = int(row.get("round") or 0)
            except ValueError:
                rnd = 0
            frozen, _ = current_bundle(d, owner, rnd) if rnd else (None, [])
            manifest = load_bundle(d, owner, frozen) if frozen else None
            where = bundle_dir(d, owner, frozen) if frozen else None
            ev_states: dict[str, str] = {}
            if manifest and where:
                try:
                    frozen_body = (where / "report.mdx").read_text(errors="replace")
                    rows, _ = parse_evidence_table(frozen_body)
                    if rows is not None:
                        for r in rows:
                            ev_states[str(r.get("id", "")).upper()] = str(r.get("state", ""))
                except OSError:
                    pass
            if ev_states:
                for aid in aids:
                    lines.append(f"  - {owner} {aid}: {ev_states.get(aid, 'not-verified')}")
            else:
                lines.append(f"  - {owner} acceptance IDs: {', '.join(aids)} "
                             "(legacy checklist; no evidence table)")
        if a.correction:
            entries = bundles_for(d, owner)
            if len(entries) >= 2:
                lines.append(f"  correction history: "
                             + " -> ".join(str(e.get('digest')) for e in entries))
                lines.append(f"  old bundle {entries[-2].get('digest')} -> "
                             f"new bundle {entries[-1].get('digest')}")
            else:
                for entry in entries:
                    if str(entry.get("round", "")) != row["round"]:
                        lines.append(f"  earlier round {entry.get('round')}: "
                                     f"bundle {entry.get('digest')}")
    if orch_entry is not None and orch_manifest is not None:
        lines += ["", "## Aggregate bundle", "",
                  f"- orch round {orch_entry.get('round')} bundle {orch_entry.get('digest')}"]
        constituents = orch_manifest.get("constituents", [])
        if isinstance(constituents, list):
            for item in constituents:
                if isinstance(item, dict):
                    lines.append(f"  - constituent {item.get('owner')} round "
                                 f"{item.get('round')}: bundle {item.get('bundle')} "
                                 f"({item.get('state')})")
        agg_patch = orch_manifest.get("patch", {}) if isinstance(
            orch_manifest.get("patch"), dict) else {}
        agg_roots = agg_patch.get("roots", []) if isinstance(agg_patch, dict) else []
        if isinstance(agg_roots, list):
            for root_entry in agg_roots:
                if isinstance(root_entry, dict):
                    lines.append(f"  - aggregate root {root_entry.get('alias')}: "
                                 f"baseline {str(root_entry.get('baseline_tree', ''))[:12]} -> "
                                 f"source {str(root_entry.get('source_tree', ''))[:12]} "
                                 f"({root_entry.get('file')})")
    lines += ["", "## Integration verification", ""]
    if orch_entry is not None and orch_manifest is not None:
        ver = orch_manifest.get("verification", {})
        ver = ver if isinstance(ver, dict) else {}
        where = bundle_dir(d, "orch", orch_entry)
        lines.append(f"- aggregate bundle {orch_entry.get('digest')} verification: "
                     f"{ver.get('status', '?')} (exit {ver.get('returncode', '?')})")
        lines.append(f"  command: {ver.get('command', '(none)')} "
                     f"(cwd {ver.get('cwd', '?')})")
        lines.append(f"  ran: {ver.get('started_at', '?')} to {ver.get('ended_at', '?')} "
                     f"({ver.get('duration_seconds', '?')}s)")
        for key in ("stdout", "stderr"):
            ref = ver.get(key, {})
            if isinstance(ref, dict) and ref.get("file"):
                lines.append(f"  {key}: {ref.get('file')} "
                             f"({ref.get('bytes', '?')} bytes, {ref.get('sha256', '?')}; "
                             f"full text frozen in the bundle at {where.name})")
        source = orch_manifest.get("source", {})
        roots = source.get("roots", []) if isinstance(source, dict) else []
        if isinstance(roots, list):
            for record in roots:
                if isinstance(record, dict):
                    lines.append(f"  - integrated source {record.get('alias')}: "
                                 f"tree {str(record.get('tree', ''))[:12]} "
                                 f"on {record.get('branch', '?')} "
                                 f"at {str(record.get('head', ''))[:12]}")
        if not roots:
            lines.append("  - integrated source: "
                         f"{source.get('reason', 'coverage unavailable') if isinstance(source, dict) else 'coverage unavailable'}")
    else:
        lines.append("- no aggregate bundle yet; this is a task/milestone packet, "
                     "not final qualification")
    lines += ["", "## Evidence deltas", ""]
    diff_blocks: list[tuple[str, list[str]]] = []
    for owner in ([*focus, "orch"] if orch_entry is not None and not a.correction else focus):
        try:
            rnd, _ = state_of(d, owner)
        except (OSError, ValueError):
            continue
        frozen, _ = current_bundle(d, owner, rnd) if rnd else (None, [])
        if not frozen:
            if owner == "orch":
                continue
            lines.append(f"- {owner}: no frozen bundle")
            continue
        manifest = load_bundle(d, owner, frozen)
        where = bundle_dir(d, owner, frozen)
        delta = (manifest or {}).get("delta", {}) if manifest else {}
        lines.append(f"- {owner} round {rnd} bundle {frozen.get('digest')}")
        if isinstance(delta, dict) and delta.get("coverage") == COVERAGE_AVAILABLE:
            lines.append(f"  correction delta from {delta.get('from')}")
            delta_roots = delta.get("roots", []) if isinstance(delta, dict) else []
            if isinstance(delta_roots, list):
                for root_entry in delta_roots:
                    if isinstance(root_entry, dict):
                        lines.append(f"  - correction delta root {root_entry.get('alias')}: "
                                     f"{root_entry.get('from_tree', '')[:12] if isinstance(root_entry.get('from_tree'), str) else ''} -> "
                                     f"{root_entry.get('to_tree', '')[:12] if isinstance(root_entry.get('to_tree'), str) else ''} "
                                     f"({root_entry.get('file')})")
        patch_roots = ((manifest or {}).get("patch", {}) or {}).get("roots", [])
        for root_entry in patch_roots if isinstance(patch_roots, list) else []:
            name = str(root_entry.get("file", ""))
            alias = str(root_entry.get("alias", ""))
            try:
                diff_text = (where / name).read_text(errors="replace") if name else ""
            except OSError:
                diff_text = ""
            qualified = f"{alias}:{name}" if alias else name
            diff_blocks.append((f"  patch {qualified} ({len(diff_text.splitlines())} lines; "
                                "first {keep} shown; full text in the bundle)",
                                diff_text.splitlines()))
            lines.append(f"@@diff:{len(diff_blocks) - 1}@@")
    if a.correction:
        owner = a.correction
        try:
            rnd, _ = state_of(d, owner)
        except (OSError, ValueError):
            rnd = 0
        lines += ["", "## Correction detail", ""]
        entries = bundles_for(d, owner)
        if len(entries) >= 2:
            lines.append(f"- addressed findings: see {owner}-decision-*.mdx Required changes "
                         "and the verifier findings below")
            for path in _ordered(d.glob(f"{owner}-decision-*.mdx")):
                try:
                    dmeta, dbody = parse(path.read_text())
                except (OSError, ValueError):
                    continue
                if dmeta.get("verdict") != "changes-requested":
                    continue
                block = packet_decision_lines(path.name, dmeta, dbody)
                lines.extend(block)
                mandatory_blocks.append("\n".join(block))
                required_reading.append(path.name)
                rendered_decisions.add(path.name)
            manifest_new, _ = current_bundle(d, owner, rnd) if rnd else (None, [])
            if manifest_new:
                ver = manifest_new.get("verification", {}) if isinstance(
                    manifest_new.get("verification"), dict) else {}
                lines.append(f"- refreshed verification: {ver.get('status', '?')} "
                             f"(exit {ver.get('returncode', '?')}, "
                             f"{ver.get('command', '?')})")
                changed = manifest_new.get("changed_paths", [])
                if isinstance(changed, list) and changed:
                    lines.append(f"- expanded change surface: {', '.join(str(c) for c in changed[:20])}")
        else:
            lines.append(f"- {owner} has a single frozen bundle; no correction delta yet")
    lines += ["", "## Verifier findings, waivers, integration evidence, and unresolved risks", ""]
    for owner in focus:
        for path in verification_paths(d, owner):
            try:
                vmeta, vbody = parse(path.read_text())
            except (OSError, ValueError):
                continue
            block = packet_finding_lines(path.name, vmeta, vbody)
            lines.extend(block)
            mandatory_blocks.append("\n".join(block))
            required_reading.append(path.name)
        for path in _ordered(d.glob(f"{owner}-decision-*.mdx")):
            try:
                dmeta, dbody = parse(path.read_text())
            except (OSError, ValueError):
                continue
            if path.name in rendered_decisions:
                continue
            block = packet_decision_lines(path.name, dmeta, dbody)
            lines.extend(block)
            if dmeta.get("verdict") in ("waived", "changes-requested"):
                mandatory_blocks.append("\n".join(block))
                required_reading.append(path.name)
    for incident in list_incidents(d):
        if incident.get("state") == "open":
            lines.append(f"- open stall: {incident.get('id')}: {incident.get('cause')}")
    escalations = d / ".escalations"
    if escalations.is_dir():
        for path in sorted(escalations.glob("*.json")):
            try:
                entry = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(entry, dict) and entry.get("state") == "open":
                lines.append(f"- open escalation: {entry.get('id')}")
    for amendment in list_amendments(d):
        if amendment.get("status") == "pending":
            lines.append(f"- pending amendment: {amendment.get('id')}: {amendment.get('need')}")
    risks_text = psecs.get("Risks", "").strip()
    if risks_text:
        lines.append("- plan risks: (complete, never shortened)")
        for rline in risks_text.splitlines():
            lines.append(f"  {rline}" if rline.strip() else "  (blank)")
    lines += ["", "## Decisions needed (report-authored)", ""]
    report_owners = [*focus]
    if orch_entry is not None and not a.correction:
        report_owners.append("orch")
    for owner in report_owners:
        try:
            rnd, _ = state_of(d, owner)
        except (OSError, ValueError):
            continue
        frozen = latest_bundle(d, owner, rnd) if rnd else None
        if not frozen:
            continue
        try:
            report_body = parse((bundle_dir(d, owner, frozen) / "report.mdx").read_text())[1]
        except (OSError, ValueError):
            continue
        report_name = f"{owner}-report-{rnd:02d}.mdx"
        block = packet_report_decision_lines(report_name, report_body)
        lines.extend(block)
        mandatory_blocks.append("\n".join(block))
        required_reading.append(report_name)
    lines.append("")
    packet = render_bounded_packet(lines, diff_blocks, mandatory_blocks,
                                   required_reading)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        publish(out, packet)
        print(f"wrote reviewer packet for {a.run} -> {out} "
              f"({packet_tokens(packet)} estimated tokens)")
    else:
        print(packet, end="")
        print(f"---\npacket: {packet_tokens(packet)} estimated tokens "
              f"(target ~1000-2000; risks never shortened)", file=sys.stderr)
