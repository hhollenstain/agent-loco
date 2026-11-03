"""Tools that save a rule into the caller's own KiwiFS space."""

from __future__ import annotations

from pathlib import Path

from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.install import require_account
from agent_lbw.runtime.store.rules import editor_view, save_rule, update_rule
from agent_lbw.tools.base import ToolResult, ToolSpec, object_schema
from agent_lbw.tools.kiwi import KiwiError, KiwiFS, normalize_kiwifs_url


def rule_tools(
    *,
    base_url: str,
    api_key: str = "",
    store_root: Path | None = None,
    database_url: str = "",
) -> list[ToolSpec]:
    url = normalize_kiwifs_url(base_url)
    if not url or store_root is None:
        return []
    root = Path(store_root)

    def guarded(action):
        def run(**kwargs: object) -> ToolResult:
            try:
                return ToolResult(True, action(**kwargs))
            except (KiwiError, ValueError, PermissionError) as exc:
                return ToolResult(False, str(exc))
            except KeyError:
                return ToolResult(False, "not found")

        return run

    def kiwi() -> KiwiFS:
        return KiwiFS(url, api_key=api_key)

    def account() -> dict:
        load_account(root, database_url=database_url)
        with database(root, database_url) as db:
            return require_account(db)

    def write_rule(
        scope_type: str,
        scope_id: str,
        name: str,
        body: str,
        rule_id: str = "",
    ) -> str:
        person = account()
        with database(root, database_url) as db:
            if (rule_id or "").strip():
                saved = update_rule(
                    db,
                    kiwi(),
                    identity_id=person["current_user_id"],
                    tenant_id=person["org"]["id"],
                    rule_id=rule_id.strip(),
                    name=name,
                    body=body,
                )
            else:
                saved = save_rule(
                    db,
                    kiwi(),
                    identity_id=person["current_user_id"],
                    tenant_id=person["org"]["id"],
                    scope_type=scope_type,
                    scope_id=scope_id,
                    name=name,
                    body=body,
                )
        return f"Saved rule {saved['name']} ({saved['id']}) in {saved['scope_type']}."

    def list_rules(scope_type: str, scope_id: str) -> str:
        person = account()
        with database(root, database_url) as db:
            view = editor_view(
                db,
                kiwi(),
                identity_id=person["current_user_id"],
                tenant_id=person["org"]["id"],
            )
        lines = ["Rules you can read:"]
        for scope in view["scopes"]:
            if scope["scope_type"] != scope_type or scope["scope_id"] != scope_id:
                continue
            if not scope["rules"]:
                return f"No rules in {scope['name']}."
            for rule in scope["rules"]:
                lines.append(f"- {rule['name']} ({rule['id']})")
                text = str(rule.get("body") or "").strip()
                if text:
                    lines.append(text)
        if len(lines) == 1:
            return "You cannot read rules there."
        text = "\n".join(lines)
        if len(text) > 8_000:
            return text[:8_000].rstrip() + "\n…"
        return text

    return [
        ToolSpec(
            name="list_rules",
            description=(
                "List rules in one scope you can read: tenant, team, project, or identity. "
                "Each scope is a separate KiwiFS space. Do not ask for another "
                "organization, team, or person."
            ),
            parameters=object_schema(
                {
                    "scope_type": {
                        "type": "string",
                        "description": "tenant, team, project, or identity.",
                    },
                    "scope_id": {
                        "type": "string",
                        "description": "Id of that organization, team, project, or person.",
                    },
                },
                ["scope_type", "scope_id"],
            ),
            handler=guarded(list_rules),
        ),
        ToolSpec(
            name="write_rule",
            description=(
                "Save a rule into the KiwiFS space for one tenant, team, project, or "
                "person. Pass rule_id to revise a rule. The space is chosen from the "
                "scope. You cannot write another organization's, team's, or person's space."
            ),
            parameters=object_schema(
                {
                    "scope_type": {
                        "type": "string",
                        "description": "tenant, team, project, or identity.",
                    },
                    "scope_id": {
                        "type": "string",
                        "description": "Id of that organization, team, project, or person.",
                    },
                    "name": {"type": "string", "description": "Short rule name."},
                    "body": {"type": "string", "description": "The rule, as markdown."},
                    "rule_id": {
                        "type": "string",
                        "description": "Set this only when revising an existing rule.",
                    },
                },
                ["scope_type", "scope_id", "name", "body"],
            ),
            handler=guarded(write_rule),
        ),
    ]
