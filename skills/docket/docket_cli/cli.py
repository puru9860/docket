"""Docket: file-protocol coordination for a planner/orchestrator/implementor agent pipeline."""

from __future__ import annotations

import argparse
import re
import sys

from .common import (
    ACTING_ROLES, DEFAULT_REVIEWER, EVIDENCE_MODES, KNOWN_EVENT_ROLES, MODES,
    SUPPORTED_HARNESSES, TOPOLOGIES, WORKFLOWS,
)
from .profiles import PROFILE_TOKEN_BUDGET
from .sessions import cmd_usage, note_command_session
from .models import cmd_feedback, cmd_models
from .five_role import ROUTE_TABLE, cmd_escalate_mode, cmd_escalation, cmd_route
from .batches import cmd_batch
from .delivery import cmd_inbox, cmd_reconcile, cmd_session
from .signalling import cmd_arm, cmd_disarm, cmd_events, cmd_watch
from .health import cmd_health
from .submission import cmd_submit
from .amendments import cmd_amendment, cmd_propose_amendment
from .transitions import cmd_decide
from .verifier import cmd_verify
from .qualification import cmd_delivery
from .doctor import cmd_doctor
from .playbooks import HELP_TOPICS, cmd_help
from .prompts import cmd_prompt
from .improvements import cmd_improvements, cmd_retrospective
from .suite import cmd_suite
from .metrics import cmd_metrics
from .release import cmd_release
from .packets import cmd_review_packet
from .dispatch import cmd_dispatch, cmd_resume, cmd_switch_model
from .commands import (
    cmd_assign, cmd_bundle, cmd_depend, cmd_diff, cmd_handoff, cmd_init, cmd_migrate,
    cmd_preflight, cmd_roots, cmd_scope, cmd_set_model, cmd_status, cmd_validate_task,
)


def owner_argument(value: str) -> str:
    """The one parser boundary for owner IDs used in paths and lifecycle state."""
    if value == "orch" or re.fullmatch(r"T[0-9]+", value):
        return value
    raise argparse.ArgumentTypeError("owner must be T<digits> or orch")


def task_ids_argument(value: str) -> str:
    """Validate a single task ID or a comma-separated list before any command runs."""
    if not value or any(not re.fullmatch(r"T[0-9]+", item.strip())
                        for item in value.split(",")):
        raise argparse.ArgumentTypeError("task id must be T<digits>")
    return value


def task_id_argument(value: str) -> str:
    if not re.fullmatch(r"T[0-9]+", value.strip()):
        raise argparse.ArgumentTypeError("task id must be T<digits>")
    return value


def diff_target_argument(value: str) -> str:
    """`diff run` addresses the run baseline; other targets are owners."""
    return value if value == "run" else owner_argument(value)


def dependency_edge_argument(value: str) -> str:
    member, separator, dep = value.partition(":")
    if not separator or ":" in dep:
        raise argparse.ArgumentTypeError("dependency must be MEMBER:DEP with task IDs")
    task_id_argument(member)
    task_id_argument(dep)
    return value


def main() -> None:
    # A path that is not UTF-8 travels as surrogate escapes; printing one must show
    # it, never crash the command that met it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="docket", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("init", help="scaffold a run and its plan")
    q.add_argument("run")
    q.add_argument("--harness", default="claude", choices=SUPPORTED_HARNESSES)
    q.add_argument("--mode", choices=MODES,
                   help="tested run preset; bare init defaults to quick")
    q.add_argument("--agents", type=int, default=0,
                   help="assert the preset's physical session count")
    q.add_argument("--topology", default="", choices=("", *TOPOLOGIES))
    q.add_argument("--evidence-mode", choices=EVIDENCE_MODES,
                   help="review evidence for this run; defaults to what the workspace supports")
    q.add_argument("--workflow", choices=WORKFLOWS,
                   help="low-level lifecycle policy; legacy preserves current semantics")
    q.add_argument("--root", action="append", default=[], metavar="ALIAS=PATH",
                   help="declare a checkout root; repeat per checkout, default is this one")
    q.add_argument("--title", default="", help="plan title")
    q.add_argument("--objective", default="",
                   help="what done means for the whole run; fills the plan Objective")
    q.add_argument("--approach", default="",
                   help="the shape of the solution and why; fills the plan Approach")
    q.set_defaults(fn=cmd_init)

    q = sub.add_parser("assign", help="open a task and its first report")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument, help="task id such as T03, or 'orch'")
    q.add_argument("--complexity", default="low", choices=["low", "high"])
    q.add_argument("--executor", default="implementor", choices=["implementor", "orchestrator"])
    q.add_argument("--tier", choices=["small", "self"], help=argparse.SUPPRESS)
    q.add_argument("--harness", default="", choices=("", *SUPPORTED_HARNESSES),
                   help="the worker's harness; opencode for a task, the supervisor's own "
                        "harness for the aggregate")
    q.add_argument("--model", default="")
    q.add_argument("--effort", default="", help="requested reasoning effort when supported by the harness")
    q.add_argument("--file", action="append", default=[], help="allowed path; repeat for multiple paths")
    q.add_argument("--files", default="", help=argparse.SUPPRESS)
    q.add_argument("--verify", default="")
    q.add_argument("--verify-timeout", type=int, default=900)
    q.add_argument("--env", action="append", default=[], metavar="NAME=value",
                   help="declared environment input for verification; repeat per input")
    q.add_argument("--depends-on", action="append", default=[], metavar="TASK",
                   type=task_ids_argument,
                   help="task that must be approved first; repeat per dependency")
    q.add_argument("--title", default="", help="task title")
    q.add_argument("--goal", default="", help="what must be true when the task is done")
    q.add_argument("--criterion", action="append", default=[], metavar="TEXT",
                   help="mechanically checkable acceptance criterion; repeat per criterion")
    q.add_argument("--out-of-scope", action="append", default=[], metavar="TEXT",
                   help="hard constraint the task must not cross; repeat per constraint")
    q.add_argument("--decision", action="append", default=[], metavar="TEXT",
                   help="decision already made that the implementor must follow; repeat")
    q.set_defaults(fn=cmd_assign)

    q = sub.add_parser("validate-task", help="gate functional task intent before implementor discovery")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.set_defaults(fn=cmd_validate_task)

    q = sub.add_parser("scope", help="validate and claim an implementor-authored discovery scope")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--submit", action="store_true", help="validate the capsule and claim its paths")
    q.set_defaults(fn=cmd_scope)

    q = sub.add_parser("handoff", help="open or submit a resumable partial-implementation checkpoint")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--submit", action="store_true", help="validate the latest handoff and mark it ready")
    q.set_defaults(fn=cmd_handoff)

    q = sub.add_parser("set-model", help="record the active model verified in the live harness")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--actual", required=True, help="exact active model label or provider/model id")
    q.add_argument("--effort", default="", help="verified active effort when exposed by the harness")
    q.set_defaults(fn=cmd_set_model)

    q = sub.add_parser(
        "submit", help="validate a report, freeze the round, and hand it to the reviewer"
    )
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--blocked", action="store_true", help="submit as blocked and request a reviewer decision")
    q.add_argument("--skip-verify", action="store_true", help="skip the task's verify command")
    q.add_argument("--skip-verify-reason", default="", help="required audit reason when skipping verification")
    q.add_argument("--verify-timeout", type=int, help="override the task-level timeout")
    q.add_argument("--as", dest="as_role", default="",
                   choices=("", *ACTING_ROLES),
                   help="acting role; required and enforced in five-role runs")
    q.set_defaults(fn=cmd_submit)

    q = sub.add_parser("verify", help="record a numbered verifier finding for a submitted round")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--result", required=True, choices=["pass", "fail", "uncertain"],
                   help="a pass means ready for review, never approved")
    q.add_argument("--detail", default="",
                   help="numbered defects or uncertainty, linked to evidence")
    q.add_argument("--verifier", default="",
                   help="verifier identity recorded in the artifact (default: verifier)")
    q.add_argument("--open-correction", action="store_true",
                   help="open a correction round from a failure under configured policy")
    q.add_argument("--as", dest="as_role", default="",
                   choices=("", *ACTING_ROLES),
                   help="acting role; must be verifier in five-role runs")
    q.set_defaults(fn=cmd_verify)

    q = sub.add_parser("route", help="route one exception to its owning role")
    q.add_argument("run")
    q.add_argument("--kind", required=True,
                   help=f"one of: {', '.join(sorted(ROUTE_TABLE))}")
    q.add_argument("--owner", default=None, type=owner_argument,
                   help="the affected task, when there is one")
    q.add_argument("--note", default="", help="with --kind blocked: the answer you already gave")
    q.set_defaults(fn=cmd_route)

    q = sub.add_parser("migrate", help="migrate a run to five-role-v1 explicitly")
    q.add_argument("run")
    q.add_argument("--to", required=True, help="target workflow (five-role-v1)")
    q.set_defaults(fn=cmd_migrate)

    q = sub.add_parser("escalate-mode", help="record that a quick run needs standard separation")
    q.add_argument("run")
    q.add_argument("--reason", required=True,
                   help="why quick no longer fits; records a request and performs no migration")
    q.set_defaults(fn=cmd_escalate_mode)

    q = sub.add_parser("prompt", help="render a bounded, deterministic role prompt")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--role", required=True,
                   choices=ACTING_ROLES)
    q.add_argument("--model", default="", help="executing model, recorded as metadata only")
    q.add_argument("--profile", default="", help="force one reviewed model profile")
    q.add_argument("--max-tokens", type=int, default=PROFILE_TOKEN_BUDGET,
                   help="guidance budget in estimated tokens (default 600)")
    q.add_argument("--stage", default="",
                   choices=["", "initial", "correction", "resume", "verification", "review"],
                   help="inspect one stage explicitly; empty derives it from lifecycle state")
    q.set_defaults(fn=cmd_prompt)

    q = sub.add_parser("feedback", help="record, list, or digest workflow observations")
    q.add_argument("run", nargs="?", default="")
    q.add_argument("--digest", action="store_true",
                   help="summarize the user-level log across runs and projects")
    q.add_argument("--since", default="", help="digest only records at or after this date")
    q.add_argument("--top", type=int, default=10, help="recurring items to show in a digest")
    q.add_argument("--log", default="", help="read this log instead of the user-level one")
    q.add_argument("--add", action="store_true", help="record one observation")
    q.add_argument("--list", action="store_true", help="list recorded observations")
    q.add_argument("--import-ops", action="store_true",
                   help="reconcile durable transport records into observations")
    q.add_argument("--role", default="", help="author role")
    q.add_argument("--category", default="", help="observation category")
    q.add_argument("--body", default="", help="observed behavior, impact, and outcome")
    q.add_argument("--task", default=None, type=owner_argument, help="related task")
    q.add_argument("--round", default="", help="related round")
    q.add_argument("--model", default="", help="model id")
    q.add_argument("--provider", default="", help="provider name")
    q.add_argument("--harness", default="", help="harness and version")
    q.add_argument("--origin", default="agent", choices=["agent", "machine"])
    q.add_argument("--prompt-digest", default="", help="rendered prompt digest")
    q.add_argument("--profile-revisions", default="", help="selected profile revisions")
    q.add_argument("--evidence", default="", help="evidence paths")
    q.add_argument("--confidence", default="low",
                   choices=["low", "medium", "high"])
    q.set_defaults(fn=cmd_feedback)

    q = sub.add_parser("models", help="per-model outcomes, review packets, and profiles")
    q.add_argument("--review", default="", metavar="MODEL",
                   help="print the evidence packet for updating one model's profile")
    q.add_argument("--adopt", default="", metavar="FILE",
                   help="validate a reviewed profile and install it for that model")
    q.add_argument("--alias", default="", metavar="NAME=ID",
                   help="count another name (a display label, a provider prefix) as that model id")
    q.add_argument("--import", dest="import_runs", nargs="*", default=None, metavar="RUN",
                   help="backfill cases from runs' artifacts (every run here when none named)")
    q.add_argument("--since", default="", help="only cases at or after this date")
    q.add_argument("--log", default="", help="read this log instead of the user-level one")
    q.set_defaults(fn=cmd_models)

    q = sub.add_parser("escalation", help="grant an exhausted correction chain more rounds")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--grant", type=int, required=True, metavar="N",
                   help="how many more local correction rounds the chain may use")
    q.add_argument("--reason", default="", help="why more rounds are worth it")
    q.add_argument("--as", dest="as_role", default="", help="the plan owner's role")
    q.set_defaults(fn=cmd_escalation)

    q = sub.add_parser("usage", help="token usage per role from each harness session transcript")
    q.add_argument("run")
    q.add_argument("--archive", action="store_true",
                   help="copy each transcript to the user-level state directory and log its usage")
    q.add_argument("--json", action="store_true", help="print machine-readable rows")
    q.set_defaults(fn=cmd_usage)

    q = sub.add_parser("improvements", help="list and advance the improvement backlog")
    q.add_argument("--add", action="store_true", help="record a cross-run finding")
    q.add_argument("--title", default="", help="finding title, for --add")
    q.add_argument("--body", default="", help="finding detail, for --add")
    q.add_argument("--category", default="", help="filter, or category for --add")
    q.add_argument("--role", default="", help="filter by affected role")
    q.add_argument("--model", default="", help="filter by affected model")
    q.add_argument("--status", default="",
                   choices=["", "observed", "proposed", "trial", "adopted", "rejected"])
    q.add_argument("--run", default="", help="filter by run in linked evidence")
    q.add_argument("--severity", default="", help="severity, for --add")
    q.add_argument("--impact", default="", help="known impact, for --add")
    q.add_argument("--evidence", default="", help="linked evidence, for --add")
    q.add_argument("--propose", default="", metavar="ID", help="propose finding ID")
    q.add_argument("--expected", default="", help="expected benefit, for --propose")
    q.add_argument("--change", default="", help="intended change, for --propose")
    q.add_argument("--eval", default="", help="evaluation case, for --propose")
    q.add_argument("--trial", default="", metavar="ID", help="trial finding ID")
    q.add_argument("--note", default="", help="trial note, for --trial")
    q.add_argument("--adopt", default="", metavar="ID", help="adopt finding ID")
    q.add_argument("--change-rev", default="", help="actual change revision, for --adopt")
    q.add_argument("--trial-result", default="", help="trial result, for --adopt")
    q.add_argument("--reject", default="", metavar="ID", help="reject finding ID")
    q.add_argument("--rationale", default="", help="rationale, for --reject")
    q.set_defaults(fn=cmd_improvements)

    q = sub.add_parser("retrospective", help="summarize a run mechanically, without model calls")
    q.add_argument("run")
    q.set_defaults(fn=cmd_retrospective)

    q = sub.add_parser("review-packet", help="generate a pinned reviewer packet")
    q.add_argument("run")
    q.add_argument("--role", required=True, help="must be reviewer")
    q.add_argument("--correction", default=None, type=task_id_argument,
                   help="focus one task's correction packet instead of the milestone")
    q.add_argument("--batch", default="",
                   help="focus one closed batch's reviewable members instead of every task under review")
    q.add_argument("--final", action="store_true",
                   help="final release packet: requires a frozen release with the "
                        "aggregate, passing integration verification, "
                        "qualifications, and suite result")
    q.add_argument("--release", default="",
                   help="frozen release digest for --final (default: the single "
                        "frozen release, refusing zero or several)")
    q.add_argument("--out", default="", help="write the packet to a file")
    q.set_defaults(fn=cmd_review_packet)

    q = sub.add_parser("metrics", help="measure a run from artifacts")
    q.add_argument("run")
    q.set_defaults(fn=cmd_metrics)

    q = sub.add_parser("release", help="freeze the exact inputs a final packet may present")
    q.add_argument("run")
    q.add_argument("--freeze", action="store_true",
                   help="validate and freeze qualifications, "
                        "integration, suite, and milestones into one digest")
    q.add_argument("--suite-artifact", default="",
                   help="suite qualification artifact path for --freeze (default: "
                        "the single artifact under .suite/); the suite must have "
                        "been captured with docket suite RUN --qualify")
    q.add_argument("--milestone", action="append", default=[],
                   help="pin M=DIGEST for M in M4..M11; repeatable, for --freeze")
    q.set_defaults(fn=cmd_release)

    q = sub.add_parser("suite", help="run the suite once with source and outputs captured")
    q.add_argument("run")
    q.add_argument("--qualify", action="store_true",
                   help="capture source, run the suite, and freeze the execution record")
    q.add_argument("--command", default="",
                   help="the exact suite invocation to run, for --qualify")
    q.add_argument("--timeout", default="1800",
                   help="abort the suite run after this many seconds, for --qualify")
    q.set_defaults(fn=cmd_suite)

    q = sub.add_parser("dispatch", help="dispatch one task round to one session")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--session", default="", required=True,
                   help="registered worker session that will hold the round")
    q.add_argument("--agent", default="", help="human-readable agent name")
    q.add_argument("--model", default="", help="requested model, within approved policy")
    q.add_argument("--register", action="store_true",
                   help="register --session for this run and role first when it is new")
    q.set_defaults(fn=cmd_dispatch)

    q = sub.add_parser("resume", help="resume a dispatched task under a new session")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--session", default="", required=True,
                   help="registered worker session taking over")
    q.add_argument("--agent", default="", help="human-readable agent name")
    q.add_argument("--reason", default="", help="why the previous session stopped")
    q.add_argument("--model", default="",
                   help="approved model for the replacement session (default: the recorded one)")
    q.add_argument("--register", action="store_true",
                   help="register --session for this run and role first when it is new")
    q.set_defaults(fn=cmd_resume)

    q = sub.add_parser("switch-model", help="move along the approved model fallback list")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--model", required=True, help="the fallback model to use")
    q.add_argument("--session", default="",
                   help="new session when in-place switching is unsupported")
    q.set_defaults(fn=cmd_switch_model)

    q = sub.add_parser("propose-amendment", help="propose a compact contract amendment")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--need", default="", help="the decision needed")
    q.add_argument("--conflicts", default="", help="the conflicting constraints")
    q.add_argument("--evidence", default="", help="evidence pointers")
    q.add_argument("--alternative", default="", help="the recommended alternative")
    q.add_argument("--impact", default="", help="impact on acceptance and dependencies")
    q.set_defaults(fn=cmd_propose_amendment)

    q = sub.add_parser("amendment", help="accept or reject a proposed amendment")
    q.add_argument("run")
    q.add_argument("--accept", default="", metavar="ID", help="accept amendment ID")
    q.add_argument("--reject", default="", metavar="ID", help="reject amendment ID")
    q.add_argument("--by", default="planner", help="deciding role (default: planner)")
    q.add_argument("--reason", default="", help="rationale, required for --reject")
    q.set_defaults(fn=cmd_amendment)

    q = sub.add_parser("decide", help="approve a report or request changes")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--changes", action="store_true")
    g.add_argument("--waive", action="store_true", help="accept a blocked report without claiming it passed")
    g.add_argument("--reopen", action="store_true", help="reopen a waived task: preserve prior evidence, reclaim scope")
    q.add_argument("--reason", default="",
                   help="rationale recorded in the decision artifact; required for a new --waive or --reopen, "
                        "and never needed again when finishing an interrupted transition")
    q.add_argument("--change", action="append", default=[],
                   help="one required change for --changes; repeat for more. Each item is "
                        "numbered and the correction is applied in one call instead of "
                        "opening a draft first")
    q.add_argument("--reviewer", default=None,
                   help=f"reviewer recorded in the decision artifact (default: {DEFAULT_REVIEWER})")
    q.add_argument("--re-review", action="store_true",
                   help="re-verify and re-review a report body that changed after review began")
    q.add_argument("--verify-timeout", type=int, help="override the task-level re-verification timeout")
    q.add_argument("--as", dest="as_role", default="",
                   choices=("", *ACTING_ROLES),
                   help="acting role; required and enforced in five-role runs")
    q.set_defaults(fn=cmd_decide)

    q = sub.add_parser("status", help="show every owner's round and state")
    q.add_argument("run")
    q.add_argument("--role", choices=ACTING_ROLES,
                   help="planner view hides task-level traffic")
    q.set_defaults(fn=cmd_status)

    q = sub.add_parser("diff", help="show worktree paths changed since task assignment")
    q.add_argument("run")
    q.add_argument("target", type=diff_target_argument)
    q.set_defaults(fn=cmd_diff)

    q = sub.add_parser("bundle", help="show the frozen evidence bundle for a task round")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--round", type=int, help="a specific round; default is the newest")
    q.add_argument("--list", action="store_true", help="list every bundle frozen for this owner")
    q.set_defaults(fn=cmd_bundle)

    q = sub.add_parser("depend", help="record consumption of another task's frozen evidence")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--on", default=None, metavar="TASK", type=task_id_argument,
                   help="the task whose frozen evidence this one consumes; omit to list")
    q.set_defaults(fn=cmd_depend)

    q = sub.add_parser("roots", help="show or declare the run's checkout roots")
    q.add_argument("run")
    q.add_argument("--declare", nargs="*", metavar="ALIAS=PATH",
                   help="declare the roots explicitly; omit values for this checkout alone")
    q.add_argument("--redeclare", nargs="*", metavar="ALIAS=PATH",
                   help="replace the declaration; refused once a baseline references it")
    q.set_defaults(fn=cmd_roots)

    q = sub.add_parser("preflight", help="record a task's verification baseline before implementation")
    q.add_argument("run")
    q.add_argument("owner", type=owner_argument)
    q.add_argument("--verify-timeout", type=int, help="override the task-level timeout")
    q.set_defaults(fn=cmd_preflight)

    q = sub.add_parser("watch", help="block until something needs you, then exit 2")
    q.add_argument("run", nargs="?")
    q.add_argument("--role", required=True, choices=KNOWN_EVENT_ROLES,
                   help="required recipient role; prevents cross-role event consumption")
    q.add_argument("--armed", action="store_true", help="watch this role's runs in .docket/watch.conf")
    q.add_argument("--interval", type=int, default=5)
    q.add_argument("--timeout", type=int, default=28800)
    q.set_defaults(fn=cmd_watch)

    q = sub.add_parser("events", help="inspect a role's pending events without consuming them")
    q.add_argument("run")
    q.add_argument("--role", required=True, choices=KNOWN_EVENT_ROLES,
                   help="whose events to inspect; inspection never claims another role's events")
    g = q.add_mutually_exclusive_group()
    g.add_argument("--peek", action="store_true",
                   help="explicit non-consuming inspection; the default when neither --ack nor --retry is given")
    g.add_argument("--ack", default="", metavar="EVENT",
                   help="record receipt of a claimed event (requires --session); receipt is not completion")
    g.add_argument("--retry", default="", metavar="EVENT",
                   help="record why another delivery attempt is due (requires --reason)")
    q.add_argument("--session", default="",
                   help="the claiming session id, bound to its registration")
    q.add_argument("--reason", default="",
                   help="why another attempt is due, for --retry")
    q.set_defaults(fn=cmd_events)

    q = sub.add_parser("inbox", help="claim a pending event under a bounded lease")
    q.add_argument("run")
    q.add_argument("--role", required=True, choices=KNOWN_EVENT_ROLES,
                   help="whose inbox to claim from; wrong-role claims are refused")
    q.add_argument("--claim", action="store_true",
                   help="grant one bounded lease to this session")
    q.add_argument("--session", default="", required=True,
                   help="the registered session id that will hold the lease")
    q.add_argument("--lease-secs", type=int, default=0,
                   help="bounded lease length in seconds (default 300)")
    q.set_defaults(fn=cmd_inbox)

    q = sub.add_parser("reconcile", help="rebuild derived pending events and retire stale ones")
    q.add_argument("run")
    q.add_argument("--role", choices=KNOWN_EVENT_ROLES,
                   help="reconcile one role; default is orchestrator and planner")
    q.set_defaults(fn=cmd_reconcile)

    q = sub.add_parser("session", help="register a delivery session or list registrations")
    q.add_argument("run")
    q.add_argument("--register", action="store_true",
                   help="create a registration, or advance the generation when re-registering")
    q.add_argument("--list", action="store_true", help="show this run's registrations")
    q.add_argument("--session", default="", help="session id (letters, digits, '.', '_' or '-')")
    q.add_argument("--name", default="", help="human-readable agent name for this session")
    q.add_argument("--role", default="orchestrator",
                   choices=ACTING_ROLES,
                   help="role this session may claim and acknowledge")
    q.set_defaults(fn=cmd_session)

    q = sub.add_parser("batch", help="create, close, and list explicit review batches")
    q.add_argument("run")
    q.add_argument("--create", default="", metavar="ID",
                   help="open a batch with explicit membership (requires --members)")
    q.add_argument("--members", default=None, type=task_ids_argument,
                   help="comma-separated task ids, e.g. T01,T02; closed at --close")
    q.add_argument("--depends-on", action="append", default=[], metavar="MEMBER:DEP",
                   type=dependency_edge_argument,
                   help="explicit dependency; repeat per edge")
    q.add_argument("--milestone", action="store_true",
                   help="this batch is an integration milestone for capable review")
    q.add_argument("--close", default="", metavar="ID",
                   help="close batch membership before dispatch")
    q.add_argument("--list", action="store_true", help="show this run's batches")
    q.set_defaults(fn=cmd_batch)

    q = sub.add_parser("health", help="show execution health separately from report state")
    q.add_argument("run")
    q.add_argument("--owner", default=None, type=owner_argument,
                   help="one task; default is every task")
    q.add_argument("--flag-stall", default=None, metavar="OWNER", type=owner_argument,
                   help="record a stall incident (requires --cause)")
    q.add_argument("--cause", default="", help="what stalled, for --flag-stall")
    q.add_argument("--resolve-stall", default="", metavar="ID",
                   help="close a stall incident, e.g. T01-1")
    q.set_defaults(fn=cmd_health)

    q = sub.add_parser("delivery", help="pause, probe, queue, and qualifiedly deliver notifications")
    q.add_argument("run")
    q.add_argument("--role", required=True,
                   choices=KNOWN_EVENT_ROLES)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--pause", action="store_true",
                   help="queue notifications without consuming them or pausing work")
    g.add_argument("--resume", action="store_true", help="resume qualified delivery")
    g.add_argument("--queued", action="store_true", help="show the queued count and keys")
    g.add_argument("--probe", action="store_true",
                   help="capability-test the delivery boundary; never over-claims")
    g.add_argument("--send", default="", metavar="EVENT",
                   help="qualifiedly deliver one event to --session")
    g.add_argument("--service-status", action="store_true",
                   help="show whether a supervised delivery service exists")
    g.add_argument("--qualify", action="store_true",
                   help="qualify delivery against disposable real processes, or record "
                        "an honest blocked artifact")
    q.add_argument("--session", default="",
                   help="registered recipient session, for --send")
    q.add_argument("--lease-secs", type=int, default=0,
                   help="bounded lease length in seconds (default 300)")
    q.set_defaults(fn=cmd_delivery)

    q = sub.add_parser("arm", help="arm the watcher for a run and role")
    q.add_argument("run")
    q.add_argument("--role", required=True, choices=KNOWN_EVENT_ROLES)
    q.set_defaults(fn=cmd_arm)

    q = sub.add_parser("disarm", help="disarm a run/role, or everything")
    q.add_argument("run", nargs="?")
    q.add_argument("--role", choices=KNOWN_EVENT_ROLES)
    q.set_defaults(fn=cmd_disarm)

    q = sub.add_parser("doctor", help="report available dispatch and wake options")
    q.set_defaults(fn=cmd_doctor)

    q = sub.add_parser("help", help="print a role playbook")
    q.add_argument("role", choices=HELP_TOPICS)
    q.set_defaults(fn=cmd_help)

    a = p.parse_args()
    note_command_session(a)
    a.fn(a)
