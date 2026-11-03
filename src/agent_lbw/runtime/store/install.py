"""The local install: one current person and the tenant they belong to.

The shape returned here matches the profile the page already edits.
"""

from __future__ import annotations

import time

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.names import RANKS, clean_name, slug_for


def read_account(db: Database) -> dict | None:
    install = db.fetchone("SELECT tenant_id, current_identity_id FROM install WHERE id = 1")
    if install is None:
        return None
    tenant = db.fetchone(
        "SELECT id, name, kind, mode FROM tenants WHERE id = ?",
        (install["tenant_id"],),
    )
    if tenant is None:
        return None
    people = db.fetchall(
        """
        SELECT identities.id, identities.display_name, identities.github_login,
               tenant_memberships.rank
        FROM tenant_memberships
        JOIN identities ON identities.id = tenant_memberships.identity_id
        WHERE tenant_memberships.tenant_id = ?
        ORDER BY tenant_memberships.created, identities.id
        """,
        (tenant["id"],),
    )
    current = install["current_identity_id"]
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
                "role": person["rank"],
                "github_login": person["github_login"] or "",
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
    db.execute(
        """
        INSERT INTO install (id, tenant_id, current_identity_id) VALUES (1, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            tenant_id = excluded.tenant_id,
            current_identity_id = excluded.current_identity_id
        """,
        (tenant_id, account["current_user_id"]),
    )


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
