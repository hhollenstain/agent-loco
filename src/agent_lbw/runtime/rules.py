"""Rules text for a cycle. The agent sees the rules that apply, not other spaces."""

from __future__ import annotations

from pathlib import Path

from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.install import read_account
from agent_lbw.runtime.store.projects import can_access
from agent_lbw.runtime.store.rules import applied_view, project_for_workspace
from agent_lbw.tools.kiwi import KiwiFS, normalize_kiwifs_url

_PROMPT_LIMIT = 6_000


def rules_for_prompt(
    store_root: Path,
    workspace: Path,
    *,
    database_url: str = "",
    kiwifs_url: str = "",
    kiwifs_api_key: str = "",
) -> str:
    """Organization, team, and project rules that apply to this workspace."""
    url = normalize_kiwifs_url(kiwifs_url)
    if not url:
        return ""
    with database(store_root, database_url) as db:
        account = read_account(db)
        if account is None:
            return ""
        tenant_id = account["org"]["id"]
        identity_id = account["current_user_id"]
        project = project_for_workspace(db, tenant_id, workspace)
        if project is None or not can_access(db, identity_id, project["id"], need="use"):
            return ""
        view = applied_view(
            db,
            KiwiFS(url, api_key=kiwifs_api_key),
            identity_id=identity_id,
            tenant_id=tenant_id,
            project_id=project["id"],
        )
    return format_rules(view)


def format_rules(view: dict) -> str:
    rules = view.get("rules") or []
    if not rules:
        return ""
    lines = ["Rules in force for this project:"]
    for rule in rules:
        source = rule.get("source")
        if source == "organization":
            label = "Organization, required"
        elif source == "team":
            label = f"Team {rule.get('scope_name') or ''}".strip()
        else:
            label = "Project"
        lines.append(f"### {rule.get('name') or 'Rule'} ({label})")
        body = str(rule.get("body") or "").strip()
        if body:
            lines.append(body)
    lines.append(
        "Follow these rules. Do not read or write another organization's, "
        "team's, or person's rules."
    )
    text = "\n".join(lines)
    if len(text) <= _PROMPT_LIMIT:
        return text
    return text[:_PROMPT_LIMIT].rstrip() + "\n…"
