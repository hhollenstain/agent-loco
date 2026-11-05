"""The local install: one current person and the tenant they belong to.

The shape returned here matches the profile the page already edits.
"""

from __future__ import annotations

import time
from pathlib import Path

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.auth import current_actor, current_tenant
from agent_lbw.runtime.store.names import RANKS, clean_name, slug_for


def connected_commit_identity(db: Database) -> tuple[str, str] | None:
    """Name and email for the signed-in person who connected GitHub."""
    account = read_account(db)
    if account is None:
        return None
    actor = current_actor() or account["current_user_id"]
    user = next((item for item in account["users"] if item["id"] == actor), None)
    if user is None:
        return None
    login = str(user.get("github_login") or "").strip()
    if not login:
        return None
    name = str(user.get("name") or "").strip() or login
    email = str(user.get("email") or "").strip()
    if "@" not in email:
        email = f"{login}@users.noreply.github.com"
    return name, email


def commit_identity_for(root: Path, database_url: str = "") -> tuple[str, str] | None:
    """The connected person, when this install has an account."""
    from agent_lbw.runtime.db import database, database_configured

    if not database_configured(root, database_url):
        return None
    try:
        with database(root, database_url) as db:
            return connected_commit_identity(db)
    except (OSError, ValueError):
        return None


def read_account(db: Database) -> dict | None:
    install = db.fetchone("SELECT tenant_id, current_identity_id FROM install WHERE id = 1")
    if install is None:
        return None
    tenant_id = _account_tenant(db, install)
    if tenant_id is None:
        return None
    tenant = db.fetchone(
        "SELECT id, name, kind, mode FROM tenants WHERE id = ?",
        (tenant_id,),
    )
    if tenant is None:
        return None
    people = db.fetchall(
        """
        SELECT identities.id, identities.display_name, identities.email,
               identities.github_login, identities.crossing, tenant_memberships.rank
        FROM tenant_memberships
        JOIN identities ON identities.id = tenant_memberships.identity_id
        WHERE tenant_memberships.tenant_id = ?
        ORDER BY tenant_memberships.created, identities.id
        """,
        (tenant["id"],),
    )
    memberships = db.fetchall(
        """
        SELECT team_memberships.identity_id, teams.id, teams.name, team_memberships.rank
        FROM team_memberships
        JOIN teams ON teams.id = team_memberships.team_id
        WHERE teams.tenant_id = ?
        ORDER BY teams.name, teams.id
        """,
        (tenant["id"],),
    )
    teams_for: dict[str, list[dict]] = {}
    for row in memberships:
        teams_for.setdefault(str(row["identity_id"]), []).append(
            {"id": row["id"], "name": row["name"], "rank": row["rank"]}
        )
    actor = current_actor()
    current = actor or install["current_identity_id"]
    if not people or current not in {person["id"] for person in people}:
        return None
    mode = tenant["mode"] if tenant["mode"] in {"local", "hosted"} else "local"
    return {
        "mode": mode,
        "org": {"id": tenant["id"], "name": tenant["name"], "kind": tenant["kind"]},
        "users": [
            {
                "id": person["id"],
                "name": person["display_name"],
                "email": person["email"] or "",
                "role": person["rank"],
                "github_login": person["github_login"] or "",
                "crossing": bool(person["crossing"]) and person["id"] == current,
                "teams": teams_for.get(person["id"], []),
            }
            for person in people
        ],
        "current_user_id": current,
    }


def save_account(db: Database, account: dict) -> None:
    """Write the profile view. An existing tenant keeps its kind."""
    org = account["org"]
    tenant_id = org["id"]
    existing = db.fetchone("SELECT kind FROM tenants WHERE id = ?", (tenant_id,))
    kind = existing["kind"] if existing is not None else "personal"
    if kind not in {"personal", "organization"}:
        kind = "personal"
    db.execute(
        """
        INSERT INTO tenants (id, kind, name, slug, mode) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET name = excluded.name, mode = excluded.mode
        """,
        (tenant_id, kind, org["name"], slug_for(org["name"], tenant_id), account["mode"]),
    )
    known = db.fetchall(
        "SELECT identity_id, created FROM tenant_memberships WHERE tenant_id = ?",
        (tenant_id,),
    )
    created_for = {row["identity_id"]: row["created"] for row in known}
    stamp = max(created_for.values(), default=int(time.time()))
    keep: list[str] = []
    for user in account["users"]:
        rank = user["role"] if user["role"] in RANKS else "member"
        created = created_for.get(user["id"])
        if created is None:
            stamp += 1
            created = stamp
        db.execute(
            """
            INSERT INTO identities (id, email, display_name, created) VALUES (?, NULL, ?, ?)
            ON CONFLICT (id) DO UPDATE SET display_name = excluded.display_name
            """,
            (user["id"], user["name"], created),
        )
        db.execute(
            """
            INSERT INTO tenant_memberships (tenant_id, identity_id, rank, created)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (tenant_id, identity_id) DO UPDATE SET rank = excluded.rank
            """,
            (tenant_id, user["id"], rank, created),
        )
        keep.append(user["id"])
    if keep:
        placeholders = ", ".join("?" for _ in keep)
        db.execute(
            f"""
            DELETE FROM tenant_memberships
            WHERE tenant_id = ? AND identity_id NOT IN ({placeholders})
            """,
            (tenant_id, *keep),
        )
    home = db.fetchone("SELECT tenant_id FROM install WHERE id = 1")
    if home is None or str(home["tenant_id"]) == tenant_id:
        db.execute(
            """
            INSERT INTO install (id, tenant_id, current_identity_id) VALUES (1, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                tenant_id = excluded.tenant_id,
                current_identity_id = excluded.current_identity_id
            """,
            (tenant_id, account["current_user_id"]),
        )


def _account_tenant(db: Database, install: dict) -> str | None:
    actor = current_actor()
    chosen = current_tenant() if actor else None
    if actor and chosen:
        member = db.fetchone(
            """
            SELECT identity_id FROM tenant_memberships
            WHERE tenant_id = ? AND identity_id = ?
            """,
            (chosen, actor),
        )
        if member is None:
            return None
        return chosen
    if actor:
        row = db.fetchone(
            """
            SELECT tenant_id FROM tenant_memberships
            WHERE identity_id = ? ORDER BY created DESC LIMIT 1
            """,
            (actor,),
        )
        if row is None:
            return None
        return str(row["tenant_id"])
    return str(install["tenant_id"])


def require_account(db: Database) -> dict:
    account = read_account(db)
    if account is None:
        raise ValueError("this install has no account")
    return account


def rename_member(account: dict, name: str) -> None:
    clean_name(name, "name")
    for user in account["users"]:
        if user["id"] == account["current_user_id"]:
            user["name"] = name
            return
    raise ValueError("current user is missing")
