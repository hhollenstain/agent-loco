"""Teams belong to an organization. A personal tenant has none."""

from __future__ import annotations

import time

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.names import RANKS, clean_name, new_id


def promote_to_organization(db: Database, tenant_id: str) -> None:
    tenant = db.fetchone("SELECT id FROM tenants WHERE id = ?", (tenant_id,))
    if tenant is None:
        raise KeyError(tenant_id)
    db.execute("UPDATE tenants SET kind = 'organization' WHERE id = ?", (tenant_id,))


def create_team(db: Database, tenant_id: str, name: str) -> dict:
    tenant = db.fetchone("SELECT kind FROM tenants WHERE id = ?", (tenant_id,))
    if tenant is None:
        raise KeyError(tenant_id)
    if tenant["kind"] != "organization":
        raise ValueError("teams belong to an organization")
    clean = clean_name(name, "team")
    taken = db.fetchone(
        "SELECT id FROM teams WHERE tenant_id = ? AND name = ?",
        (tenant_id, clean),
    )
    if taken is not None:
        raise ValueError(f"{clean} is already a team")
    team_id = new_id("team")
    db.execute(
        "INSERT INTO teams (id, tenant_id, name) VALUES (?, ?, ?)",
        (team_id, tenant_id, clean),
    )
    return {"id": team_id, "name": clean}


def add_team_member(db: Database, team_id: str, identity_id: str, rank: str) -> None:
    if rank not in RANKS:
        raise ValueError("rank must be owner, admin, or member")
    team = db.fetchone("SELECT tenant_id FROM teams WHERE id = ?", (team_id,))
    if team is None:
        raise KeyError(team_id)
    member = db.fetchone(
        """
        SELECT identity_id FROM tenant_memberships
        WHERE tenant_id = ? AND identity_id = ?
        """,
        (team["tenant_id"], identity_id),
    )
    if member is None:
        raise ValueError("that person is not in this organization")
    db.execute(
        """
        INSERT INTO team_memberships (team_id, identity_id, rank, created)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (team_id, identity_id) DO UPDATE SET rank = excluded.rank
        """,
        (team_id, identity_id, rank, int(time.time())),
    )


def list_teams(db: Database, tenant_id: str) -> list[dict]:
    teams = db.fetchall(
        "SELECT id, name FROM teams WHERE tenant_id = ? ORDER BY name, id",
        (tenant_id,),
    )
    listed: list[dict] = []
    for team in teams:
        members = db.fetchall(
            """
            SELECT identities.id, identities.display_name, team_memberships.rank
            FROM team_memberships
            JOIN identities ON identities.id = team_memberships.identity_id
            WHERE team_memberships.team_id = ?
            ORDER BY team_memberships.created, identities.id
            """,
            (team["id"],),
        )
        listed.append(
            {
                "id": team["id"],
                "name": team["name"],
                "members": [
                    {"id": person["id"], "name": person["display_name"], "rank": person["rank"]}
                    for person in members
                ],
            }
        )
    return listed
