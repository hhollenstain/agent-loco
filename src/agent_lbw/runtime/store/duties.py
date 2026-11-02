"""Additive duties. An owner already holds them, so an owner gets no row."""

from __future__ import annotations

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.names import DUTIES, new_id


def grant_duty(
    db: Database,
    *,
    scope_type: str,
    scope_id: str,
    identity_id: str,
    duty: str,
) -> dict:
    if scope_type not in {"tenant", "team"}:
        raise ValueError("scope must be tenant or team")
    if duty not in DUTIES:
        raise ValueError("duty must be billing or security")
    tenant_id = _tenant_for_scope(db, scope_type, scope_id)
    membership = db.fetchone(
        "SELECT rank FROM tenant_memberships WHERE tenant_id = ? AND identity_id = ?",
        (tenant_id, identity_id),
    )
    if membership is None:
        raise ValueError("that person is not in this organization")
    if membership["rank"] == "owner":
        raise ValueError("an owner already holds every duty")
    if scope_type == "team":
        on_team = db.fetchone(
            "SELECT identity_id FROM team_memberships WHERE team_id = ? AND identity_id = ?",
            (scope_id, identity_id),
        )
        if on_team is None:
            raise ValueError("that person is not on this team")
    existing = db.fetchone(
        """
        SELECT id FROM duty_grants
        WHERE scope_type = ? AND scope_id = ? AND identity_id = ? AND duty = ?
        """,
        (scope_type, scope_id, identity_id, duty),
    )
    if existing is not None:
        return {
            "id": existing["id"],
            "scope_type": scope_type,
            "scope_id": scope_id,
            "duty": duty,
        }
    duty_id = new_id("duty")
    db.execute(
        """
        INSERT INTO duty_grants (id, scope_type, scope_id, identity_id, duty)
        VALUES (?, ?, ?, ?, ?)
        """,
        (duty_id, scope_type, scope_id, identity_id, duty),
    )
    return {"id": duty_id, "scope_type": scope_type, "scope_id": scope_id, "duty": duty}


def list_duties(db: Database, tenant_id: str) -> list[dict]:
    team_ids = [
        row["id"] for row in db.fetchall("SELECT id FROM teams WHERE tenant_id = ?", (tenant_id,))
    ]
    rows = db.fetchall(
        """
        SELECT scope_type, scope_id, identity_id, duty
        FROM duty_grants
        WHERE scope_type = 'tenant' AND scope_id = ?
        ORDER BY duty, identity_id
        """,
        (tenant_id,),
    )
    if team_ids:
        marks = ", ".join("?" for _ in team_ids)
        rows.extend(
            db.fetchall(
                f"""
                SELECT scope_type, scope_id, identity_id, duty
                FROM duty_grants
                WHERE scope_type = 'team' AND scope_id IN ({marks})
                ORDER BY duty, identity_id
                """,
                tuple(team_ids),
            )
        )
    return [
        {
            "scope_type": row["scope_type"],
            "scope_id": row["scope_id"],
            "identity_id": row["identity_id"],
            "duty": row["duty"],
        }
        for row in rows
    ]


def _tenant_for_scope(db: Database, scope_type: str, scope_id: str) -> str:
    if scope_type == "tenant":
        tenant = db.fetchone("SELECT id FROM tenants WHERE id = ?", (scope_id,))
        if tenant is None:
            raise KeyError(scope_id)
        return str(tenant["id"])
    team = db.fetchone("SELECT tenant_id FROM teams WHERE id = ?", (scope_id,))
    if team is None:
        raise KeyError(scope_id)
    return str(team["tenant_id"])
