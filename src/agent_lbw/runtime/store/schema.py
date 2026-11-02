"""Create the directory tables, and fold an older account database into them."""

from __future__ import annotations

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.names import slug_for

_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS identities (
        id TEXT PRIMARY KEY,
        email TEXT,
        display_name TEXT NOT NULL,
        created INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenants (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('personal', 'organization')),
        name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE,
        mode TEXT NOT NULL CHECK (mode IN ('local', 'hosted'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_memberships (
        tenant_id TEXT NOT NULL REFERENCES tenants (id),
        identity_id TEXT NOT NULL REFERENCES identities (id),
        rank TEXT NOT NULL CHECK (rank IN ('owner', 'admin', 'member')),
        created INTEGER NOT NULL,
        PRIMARY KEY (tenant_id, identity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS teams (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants (id),
        name TEXT NOT NULL,
        UNIQUE (tenant_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_memberships (
        team_id TEXT NOT NULL REFERENCES teams (id),
        identity_id TEXT NOT NULL REFERENCES identities (id),
        rank TEXT NOT NULL CHECK (rank IN ('owner', 'admin', 'member')),
        created INTEGER NOT NULL,
        PRIMARY KEY (team_id, identity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duty_grants (
        id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('tenant', 'team')),
        scope_id TEXT NOT NULL,
        identity_id TEXT NOT NULL REFERENCES identities (id),
        duty TEXT NOT NULL CHECK (duty IN ('billing', 'security')),
        UNIQUE (scope_type, scope_id, identity_id, duty)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants (id),
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('github', 'git', 'folder')),
        locator TEXT NOT NULL,
        UNIQUE (tenant_id, locator)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_grants (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects (id),
        subject_type TEXT NOT NULL CHECK (subject_type IN ('team', 'identity')),
        subject_id TEXT NOT NULL,
        access TEXT NOT NULL CHECK (access IN ('use', 'manage')),
        UNIQUE (project_id, subject_type, subject_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_keys (
        tenant_id TEXT PRIMARY KEY REFERENCES tenants (id),
        wrapped_key TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants (id),
        project_id TEXT NOT NULL REFERENCES projects (id),
        requester_id TEXT NOT NULL REFERENCES identities (id),
        status TEXT NOT NULL,
        branch TEXT,
        pr_url TEXT,
        run_ref TEXT,
        goal_ciphertext TEXT,
        created INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedules (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants (id),
        project_id TEXT NOT NULL REFERENCES projects (id),
        kind TEXT NOT NULL CHECK (kind IN ('change', 'scan')),
        cadence TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        created_by TEXT NOT NULL REFERENCES identities (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS install (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        tenant_id TEXT NOT NULL REFERENCES tenants (id),
        current_identity_id TEXT NOT NULL REFERENCES identities (id)
    )
    """,
)

# Child rows go before the identity and tenant they point at.
CLEAR_ORDER = (
    "schedules",
    "tasks",
    "project_grants",
    "projects",
    "duty_grants",
    "team_memberships",
    "teams",
    "tenant_memberships",
    "tenant_keys",
    "install",
    "identities",
    "tenants",
)

TASK_COLUMNS = frozenset(
    {
        "id",
        "tenant_id",
        "project_id",
        "requester_id",
        "status",
        "branch",
        "pr_url",
        "run_ref",
        "goal_ciphertext",
        "created",
    }
)


def apply_schema(db: Database) -> None:
    """Create tables. An older organizations/users database is copied in once."""
    legacy = None
    if _has_table(db, "organizations") and not _has_table(db, "tenants"):
        legacy = _legacy_account(db)
    if legacy is not None:
        for name in ("install", "users", "organizations"):
            db.execute(f"DROP TABLE {name}")
    for statement in _STATEMENTS:
        db.execute(statement)
    if legacy is not None:
        _write_legacy(db, legacy)


def clear_store(db: Database) -> None:
    """Remove directory rows. Used when a shared Postgres database is reset."""
    for name in CLEAR_ORDER:
        if _has_table(db, name):
            db.execute(f"DELETE FROM {name}")


def table_columns(db: Database, table: str) -> set[str]:
    if db.kind == "sqlite":
        rows = db.fetchall(f"PRAGMA table_info({table})")
        return {str(row["name"]) for row in rows}
    rows = db.fetchall(
        """
        SELECT column_name AS name
        FROM information_schema.columns
        WHERE table_name = ?
        """,
        (table,),
    )
    return {str(row["name"]) for row in rows}


def _has_table(db: Database, name: str) -> bool:
    if db.kind == "sqlite":
        row = db.fetchone(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        )
    else:
        row = db.fetchone(
            "SELECT tablename AS name FROM pg_tables WHERE schemaname = 'public' AND tablename = ?",
            (name,),
        )
    return row is not None


def _legacy_account(db: Database) -> dict | None:
    if not _has_table(db, "users") or not _has_table(db, "install"):
        return None
    install = db.fetchone("SELECT org_id, current_user_id FROM install WHERE id = 1")
    orgs = db.fetchall("SELECT id, name, mode FROM organizations")
    users = db.fetchall("SELECT id, org_id, name, role, created FROM users")
    if install is None or not orgs or not users:
        return None
    return {"install": install, "orgs": orgs, "users": users}


def _write_legacy(db: Database, legacy: dict) -> None:
    for org in legacy["orgs"]:
        slug = slug_for(str(org["name"]), str(org["id"]))
        mode = org["mode"] if org["mode"] in {"local", "hosted"} else "local"
        db.execute(
            "INSERT INTO tenants (id, kind, name, slug, mode) VALUES (?, 'organization', ?, ?, ?)",
            (org["id"], org["name"], slug, mode),
        )
    for user in legacy["users"]:
        rank = user["role"] if user["role"] in {"owner", "admin", "member"} else "member"
        db.execute(
            "INSERT INTO identities (id, email, display_name, created) VALUES (?, NULL, ?, ?)",
            (user["id"], user["name"], user["created"]),
        )
        db.execute(
            """
            INSERT INTO tenant_memberships (tenant_id, identity_id, rank, created)
            VALUES (?, ?, ?, ?)
            """,
            (user["org_id"], user["id"], rank, user["created"]),
        )
    install = legacy["install"]
    db.execute(
        "INSERT INTO install (id, tenant_id, current_identity_id) VALUES (1, ?, ?)",
        (install["org_id"], install["current_user_id"]),
    )
