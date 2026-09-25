"""Role contracts and the playbooks `docket help` prints."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .common import ACTING_ROLES, SKILL_DIR, die
from .frontmatter import unwrap_markdown


HELP_TOPICS = (*ACTING_ROLES, "signalling")


def contracts_dir() -> Path:
    """Canonical role-contract tree: the installed references, or a test overlay.

    Like the profile root, this override only selects which reviewed bytes are
    read; the digest still binds the exact content.
    """
    override = os.environ.get("DOCKET_CONTRACTS_ROOT", "").strip()
    if override:
        return Path(override)
    return SKILL_DIR / "references" / "contracts"


def contract_path(role: str) -> Path:
    return contracts_dir() / f"{role}.md"


def load_contract(role: str) -> tuple[str, str]:
    """Read one canonical role contract. Returns (text, diagnostic)."""
    path = contract_path(role)
    try:
        text = path.read_text().strip()
    except OSError as exc:
        return "", f"missing role contract: {path} ({exc})"
    if not text:
        return "", f"missing role contract: {path} is empty"
    return text, ""


def read_contract(role: str) -> str:
    """The one canonical contract for a role. Both help and prompts call this."""
    text, problem = load_contract(role)
    if problem:
        die(f"{problem}; add the canonical contract before rendering this role")
    return text


def cmd_help(a: argparse.Namespace) -> None:
    """Read the canonical role reference rather than duplicating playbooks in code."""
    if a.role != "signalling":
        contract = read_contract(a.role)
    else:
        contract = ""
    reference = SKILL_DIR / "references" / f"{a.role}.md"
    if not reference.is_file():
        die(f"missing playbook: {reference}")
    body = unwrap_markdown(reference.read_text().strip())
    if contract:
        print(f"# Role: {a.role}\n\n{contract}\n\n{body}")
    else:
        print(body)
