"""Rules indexed in the directory. The text lives in a KiwiFS space.

One space belongs to one tenant, one team, one project, or one person. The
space name is derived from that id. A request cannot choose a space, and a
stored path cannot point outside `rules/<id>.md`.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.names import clean_name, new_id
from agent_lbw.runtime.store.projects import can_access, ensure_project, require_project
from agent_lbw.tools.kiwi import KiwiError

SCOPES = frozenset({"tenant", "team", "project", "identity"})
BODY_LIMIT = 20_000
_SCOPE_PREFIX = {
    "tenant": "org",
    "team": "team",
    "project": "proj",
    "identity": "user",
}
_ID_PREFIX = {
    "tenant": "org_",
    "team": "team_",
    "project": "proj_",
    "identity": "user_",
}
_BODY = re.compile(r"^[a-z0-9]{8,64}$")
_RULE_PATH = re.compile(r"^rules/rule_[a-f0-9]{12}\.md$")


def space_name(scope_type: str, scope_id: str) -> str:
    """The only space this scope may use. Anything else is refused."""
    prefix = _SCOPE_PREFIX.get(scope_type)
    id_prefix = _ID_PREFIX.get(scope_type)
    if prefix is None or id_prefix is None or not str(scope_id).startswith(id_prefix):
        raise ValueError("unknown scope")
    body = str(scope_id)[len(id_prefix) :]
    if not _BODY.fullmatch(body):
        raise ValueError("unknown scope")
    return f"{prefix}-{body}"


def can_read_scope(
    db: Database,
    *,
    identity_id: str,
    tenant_id: str,
    scope_type: str,
    scope_id: str,
) -> bool:
    if not _in_tenant(db, tenant_id, scope_type, scope_id):
        return False
    if scope_type == "identity":
        return scope_id == identity_id
    rank = _tenant_rank(db, tenant_id, identity_id)
    if rank is None:
        return False
    if scope_type == "tenant":
        return True
    if scope_type == "team":
        if rank in {"owner", "admin"} or _has_duty(db, "tenant", tenant_id, identity_id):
            return True
        return _team_rank(db, scope_id, identity_id) is not None
    if scope_type == "project":
        if can_write_scope(
            db,
            identity_id=identity_id,
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
        ):
            return True
        return can_access(db, identity_id, scope_id, need="use")
    return False


def can_write_scope(
    db: Database,
    *,
    identity_id: str,
    tenant_id: str,
    scope_type: str,
    scope_id: str,
) -> bool:
    if not _in_tenant(db, tenant_id, scope_type, scope_id):
        return False
    if scope_type == "identity":
        return scope_id == identity_id and _tenant_rank(db, tenant_id, identity_id) is not None
    rank = _tenant_rank(db, tenant_id, identity_id)
    if rank is None:
        return False
    if scope_type == "tenant":
        return rank in {"owner", "admin"} or _has_duty(db, "tenant", tenant_id, identity_id)
    if scope_type == "team":
        if rank in {"owner", "admin"} or _has_duty(db, "tenant", tenant_id, identity_id):
            return True
        team_rank = _team_rank(db, scope_id, identity_id)
        if team_rank in {"owner", "admin"}:
            return True
        return _has_duty(db, "team", scope_id, identity_id)
    if scope_type == "project":
        if rank in {"owner", "admin"} or _has_duty(db, "tenant", tenant_id, identity_id):
            return True
        return can_access(db, identity_id, scope_id, need="manage")
    return False


def project_for_workspace(db: Database, tenant_id: str, workspace: Path) -> dict | None:
    places: list[str] = []
    resolved = str(workspace.expanduser().resolve())
    raw = str(workspace)
    for place in (resolved, raw):
        if place not in places:
            places.append(place)
    for place in places:
        row = db.fetchone(
            """
            SELECT id, name, kind, locator FROM projects
            WHERE tenant_id = ? AND locator = ?
            """,
            (tenant_id, place),
        )
        if row is not None:
            return {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "locator": row["locator"],
            }
    return None


def save_rule(
    db: Database,
    kiwi: Any,
    *,
    identity_id: str,
    tenant_id: str,
    scope_type: str,
    scope_id: str,
    name: str,
    body: str,
) -> dict:
    _require_scope(scope_type)
    _require_write(db, identity_id, tenant_id, scope_type, scope_id)
    clean = clean_name(name, "rule")
    text = _body(body)
    taken = db.fetchone(
        """
        SELECT id FROM rules
        WHERE tenant_id = ? AND scope_type = ? AND scope_id = ? AND name = ?
        """,
        (tenant_id, scope_type, scope_id, clean),
    )
    if taken is not None:
        raise ValueError(f"{clean} is already a rule here")
    rule_id = new_id("rule")
    path = f"rules/{rule_id}.md"
    space = space_name(scope_type, scope_id)
    _write_space(kiwi, space, path, text)
    db.execute(
        """
        INSERT INTO rules (
            id, tenant_id, scope_type, scope_id, name, space_name, path, created_by, created
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule_id,
            tenant_id,
            scope_type,
            scope_id,
            clean,
            space,
            path,
            identity_id,
            int(time.time()),
        ),
    )
    return _public(db, _row(db, rule_id), text, identity_id=identity_id, tenant_id=tenant_id)


def update_rule(
    db: Database,
    kiwi: Any,
    *,
    identity_id: str,
    tenant_id: str,
    rule_id: str,
    name: str,
    body: str,
) -> dict:
    row = _owned(db, tenant_id, rule_id)
    _require_write(db, identity_id, tenant_id, row["scope_type"], row["scope_id"])
    clean = clean_name(name, "rule")
    text = _body(body)
    taken = db.fetchone(
        """
        SELECT id FROM rules
        WHERE tenant_id = ? AND scope_type = ? AND scope_id = ? AND name = ? AND id <> ?
        """,
        (tenant_id, row["scope_type"], row["scope_id"], clean, rule_id),
    )
    if taken is not None:
        raise ValueError(f"{clean} is already a rule here")
    space, path = _checked_place(row)
    _write_space(kiwi, space, path, text)
    db.execute("UPDATE rules SET name = ? WHERE id = ?", (clean, rule_id))
    fresh = _row(db, rule_id)
    return _public(db, fresh, text, identity_id=identity_id, tenant_id=tenant_id)


def delete_rule(
    db: Database,
    kiwi: Any,
    *,
    identity_id: str,
    tenant_id: str,
    rule_id: str,
) -> dict:
    row = _owned(db, tenant_id, rule_id)
    _require_write(db, identity_id, tenant_id, row["scope_type"], row["scope_id"])
    space, path = _checked_place(row)
    _delete_space(kiwi, space, path)
    db.execute("DELETE FROM rule_requirements WHERE rule_id = ?", (rule_id,))
    db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return {"deleted": rule_id}


def set_required(
    db: Database,
    *,
    identity_id: str,
    tenant_id: str,
    rule_id: str,
    project_id: str,
    required: bool,
) -> dict:
    row = _owned(db, tenant_id, rule_id)
    if row["scope_type"] != "tenant":
        raise ValueError("only an organization rule can be required on a project")
    if not can_write_scope(
        db,
        identity_id=identity_id,
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    ):
        raise PermissionError("you cannot change which projects require this rule")
    require_project(db, tenant_id, project_id)
    if required:
        existing = db.fetchone(
            "SELECT rule_id FROM rule_requirements WHERE rule_id = ? AND project_id = ?",
            (rule_id, project_id),
        )
        if existing is None:
            db.execute(
                "INSERT INTO rule_requirements (rule_id, project_id) VALUES (?, ?)",
                (rule_id, project_id),
            )
    else:
        db.execute(
            "DELETE FROM rule_requirements WHERE rule_id = ? AND project_id = ?",
            (rule_id, project_id),
        )
    return _public(db, _row(db, rule_id), "", identity_id=identity_id, tenant_id=tenant_id)


def editor_view(
    db: Database,
    kiwi: Any,
    *,
    identity_id: str,
    tenant_id: str,
) -> dict:
    scopes: list[dict] = []
    if can_read_scope(
        db,
        identity_id=identity_id,
        tenant_id=tenant_id,
        scope_type="identity",
        scope_id=identity_id,
    ):
        scopes.append(
            _scope_block(
                db,
                kiwi,
                identity_id=identity_id,
                tenant_id=tenant_id,
                scope_type="identity",
                scope_id=identity_id,
                name="Your rules",
            )
        )
    tenant = db.fetchone("SELECT name FROM tenants WHERE id = ?", (tenant_id,))
    if tenant is not None and can_read_scope(
        db,
        identity_id=identity_id,
        tenant_id=tenant_id,
        scope_type="tenant",
        scope_id=tenant_id,
    ):
        scopes.append(
            _scope_block(
                db,
                kiwi,
                identity_id=identity_id,
                tenant_id=tenant_id,
                scope_type="tenant",
                scope_id=tenant_id,
                name=str(tenant["name"]),
            )
        )
    teams = db.fetchall(
        "SELECT id, name FROM teams WHERE tenant_id = ? ORDER BY name, id",
        (tenant_id,),
    )
    for team in teams:
        if not can_read_scope(
            db,
            identity_id=identity_id,
            tenant_id=tenant_id,
            scope_type="team",
            scope_id=team["id"],
        ):
            continue
        scopes.append(
            _scope_block(
                db,
                kiwi,
                identity_id=identity_id,
                tenant_id=tenant_id,
                scope_type="team",
                scope_id=team["id"],
                name=f"Team {team['name']}",
            )
        )
    projects = [
        {"id": row["id"], "name": row["name"]}
        for row in db.fetchall(
            "SELECT id, name FROM projects WHERE tenant_id = ? ORDER BY name, id",
            (tenant_id,),
        )
    ]
    return {"scopes": scopes, "projects": projects}


def applied_view(
    db: Database,
    kiwi: Any,
    *,
    identity_id: str,
    tenant_id: str,
    project_id: str,
) -> dict:
    project = require_project(db, tenant_id, project_id)
    if not can_access(db, identity_id, project_id, need="use"):
        raise PermissionError("you cannot read rules for this project")
    rules: list[dict] = []
    required = db.fetchall(
        """
        SELECT rules.id, rules.tenant_id, rules.scope_type, rules.scope_id, rules.name,
               rules.space_name, rules.path
        FROM rules
        JOIN rule_requirements ON rule_requirements.rule_id = rules.id
        WHERE rule_requirements.project_id = ?
          AND rules.tenant_id = ?
          AND rules.scope_type = 'tenant'
        ORDER BY rules.name, rules.id
        """,
        (project_id, tenant_id),
    )
    for row in required:
        rules.append(
            _applied(db, kiwi, row, source="organization", required=True, identity_id=identity_id)
        )
    teams = db.fetchall(
        """
        SELECT teams.id, teams.name
        FROM project_grants
        JOIN teams ON teams.id = project_grants.subject_id
        WHERE project_grants.project_id = ?
          AND project_grants.subject_type = 'team'
          AND teams.tenant_id = ?
        ORDER BY teams.name, teams.id
        """,
        (project_id, tenant_id),
    )
    for team in teams:
        rows = _scope_rows(db, tenant_id, "team", team["id"])
        for row in rows:
            item = _applied(
                db,
                kiwi,
                row,
                source="team",
                required=False,
                identity_id=identity_id,
                scope_name=str(team["name"]),
            )
            rules.append(item)
    for row in _scope_rows(db, tenant_id, "project", project_id):
        rules.append(
            _applied(db, kiwi, row, source="project", required=False, identity_id=identity_id)
        )
    return {
        "project": {"id": project["id"], "name": project["name"]},
        "can_write": can_write_scope(
            db,
            identity_id=identity_id,
            tenant_id=tenant_id,
            scope_type="project",
            scope_id=project_id,
        ),
        "rules": rules,
    }


def ensure_workspace_project(db: Database, tenant_id: str, workspace: Path) -> dict:
    found = project_for_workspace(db, tenant_id, workspace)
    if found is not None:
        return found
    place = str(workspace.expanduser().resolve())
    kind = "git" if (workspace / ".git").exists() else "folder"
    return ensure_project(
        db,
        tenant_id,
        name=workspace.name or "project",
        kind=kind,
        locator=place,
    )


def _scope_block(
    db: Database,
    kiwi: Any,
    *,
    identity_id: str,
    tenant_id: str,
    scope_type: str,
    scope_id: str,
    name: str,
) -> dict:
    writable = can_write_scope(
        db,
        identity_id=identity_id,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    rules = [
        _public(db, row, _open_rule(kiwi, row), identity_id=identity_id, tenant_id=tenant_id)
        for row in _scope_rows(db, tenant_id, scope_type, scope_id)
    ]
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "name": name,
        "can_write": writable,
        "rules": rules,
    }


def _applied(
    db: Database,
    kiwi: Any,
    row: dict,
    *,
    source: str,
    required: bool,
    identity_id: str,
    scope_name: str = "",
) -> dict:
    label = scope_name or _scope_label(db, row["scope_type"], row["scope_id"])
    writable = can_write_scope(
        db,
        identity_id=identity_id,
        tenant_id=row["tenant_id"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "body": _open_rule(kiwi, row),
        "source": source,
        "scope_name": label,
        "required": required,
        "can_write": writable and source == "project",
    }


def _public(
    db: Database,
    row: dict,
    body: str,
    *,
    identity_id: str,
    tenant_id: str,
) -> dict:
    item = {
        "id": row["id"],
        "name": row["name"],
        "body": body,
        "scope_type": row["scope_type"],
        "scope_id": row["scope_id"],
        "can_write": can_write_scope(
            db,
            identity_id=identity_id,
            tenant_id=tenant_id,
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
        ),
    }
    if row["scope_type"] == "tenant":
        links = db.fetchall(
            "SELECT project_id FROM rule_requirements WHERE rule_id = ? ORDER BY project_id",
            (row["id"],),
        )
        item["required_project_ids"] = [link["project_id"] for link in links]
    return item


def _scope_rows(db: Database, tenant_id: str, scope_type: str, scope_id: str) -> list[dict]:
    return db.fetchall(
        """
        SELECT id, tenant_id, scope_type, scope_id, name, space_name, path
        FROM rules
        WHERE tenant_id = ? AND scope_type = ? AND scope_id = ?
        ORDER BY name, id
        """,
        (tenant_id, scope_type, scope_id),
    )


def _row(db: Database, rule_id: str) -> dict:
    row = db.fetchone(
        """
        SELECT id, tenant_id, scope_type, scope_id, name, space_name, path
        FROM rules WHERE id = ?
        """,
        (rule_id,),
    )
    if row is None:
        raise KeyError(rule_id)
    return row


def _owned(db: Database, tenant_id: str, rule_id: str) -> dict:
    row = db.fetchone(
        """
        SELECT id, tenant_id, scope_type, scope_id, name, space_name, path
        FROM rules WHERE id = ?
        """,
        (rule_id,),
    )
    if row is None or row["tenant_id"] != tenant_id:
        raise KeyError(rule_id)
    return row


def _open_rule(kiwi: Any, row: dict) -> str:
    try:
        space, path = _checked_place(row)
    except ValueError:
        return ""
    try:
        return kiwi.in_space(space).raw(path)
    except KiwiError:
        return ""


def _write_space(kiwi: Any, space: str, path: str, body: str) -> None:
    if not _RULE_PATH.fullmatch(path):
        raise ValueError("rule path is not in this scope")
    kiwi.ensure_space(space)
    kiwi.in_space(space).write(path, body)


def _delete_space(kiwi: Any, space: str, path: str) -> None:
    if not _RULE_PATH.fullmatch(path):
        raise ValueError("rule path is not in this scope")
    try:
        kiwi.in_space(space).delete(path)
    except KiwiError as exc:
        if "404" not in str(exc):
            raise


def _checked_place(row: dict) -> tuple[str, str]:
    space = space_name(row["scope_type"], row["scope_id"])
    path = str(row["path"] or "")
    if row["space_name"] != space or not _RULE_PATH.fullmatch(path):
        raise ValueError("rule space does not match its scope")
    return space, path


def _scope_label(db: Database, scope_type: str, scope_id: str) -> str:
    if scope_type == "tenant":
        row = db.fetchone("SELECT name FROM tenants WHERE id = ?", (scope_id,))
        return str(row["name"]) if row else ""
    if scope_type == "team":
        row = db.fetchone("SELECT name FROM teams WHERE id = ?", (scope_id,))
        return str(row["name"]) if row else ""
    if scope_type == "project":
        row = db.fetchone("SELECT name FROM projects WHERE id = ?", (scope_id,))
        return str(row["name"]) if row else ""
    return ""


def _require_scope(scope_type: str) -> None:
    if scope_type not in SCOPES:
        raise ValueError("scope must be tenant, team, project, or identity")


def _require_write(
    db: Database,
    identity_id: str,
    tenant_id: str,
    scope_type: str,
    scope_id: str,
) -> None:
    _require_scope(scope_type)
    if not can_write_scope(
        db,
        identity_id=identity_id,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
    ):
        raise PermissionError("you cannot change rules here")


def _body(body: str) -> str:
    text = body if isinstance(body, str) else str(body or "")
    if not text.strip():
        raise ValueError("rule text is required")
    if len(text) > BODY_LIMIT:
        raise ValueError(f"rule text must be {BODY_LIMIT} characters or fewer")
    return text


def _in_tenant(db: Database, tenant_id: str, scope_type: str, scope_id: str) -> bool:
    if scope_type not in SCOPES:
        return False
    try:
        space_name(scope_type, scope_id)
    except ValueError:
        return False
    if scope_type == "tenant":
        return scope_id == tenant_id
    if scope_type == "team":
        row = db.fetchone("SELECT tenant_id FROM teams WHERE id = ?", (scope_id,))
    elif scope_type == "project":
        row = db.fetchone("SELECT tenant_id FROM projects WHERE id = ?", (scope_id,))
    else:
        row = db.fetchone(
            """
            SELECT tenant_id FROM tenant_memberships
            WHERE tenant_id = ? AND identity_id = ?
            """,
            (tenant_id, scope_id),
        )
        return row is not None
    return row is not None and row["tenant_id"] == tenant_id


def _tenant_rank(db: Database, tenant_id: str, identity_id: str) -> str | None:
    row = db.fetchone(
        "SELECT rank FROM tenant_memberships WHERE tenant_id = ? AND identity_id = ?",
        (tenant_id, identity_id),
    )
    return None if row is None else str(row["rank"])


def _team_rank(db: Database, team_id: str, identity_id: str) -> str | None:
    row = db.fetchone(
        "SELECT rank FROM team_memberships WHERE team_id = ? AND identity_id = ?",
        (team_id, identity_id),
    )
    return None if row is None else str(row["rank"])


def _has_duty(db: Database, scope_type: str, scope_id: str, identity_id: str) -> bool:
    row = db.fetchone(
        """
        SELECT id FROM duty_grants
        WHERE scope_type = ? AND scope_id = ? AND identity_id = ? AND duty = 'rules'
        """,
        (scope_type, scope_id, identity_id),
    )
    return row is not None
