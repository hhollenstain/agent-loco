"""Break-glass for LBW engineers.

An organization admin does not open this. A person is a crossing guard only
when identities.crossing is set. The page lists every organization, can restore
a locked sign-in, and stores the SSO settings. It does not read a goal, a diff,
a branch, or a pull request, and it does not remove a team.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.auth import add_crossing_guard
from agent_lbw.runtime.store.crypto import open_secret, seal
from agent_lbw.runtime.store.tasks import tenant_dek
from agent_lbw.runtime.store.teams import list_teams

PROVIDERS = frozenset({"password", "entra", "auth0", "saml"})
_HTTPS = re.compile(r"^https://\S{1,300}$")
_CLIENT = re.compile(r"^[A-Za-z0-9._:@/-]{1,200}$")


def board(db: Database, identity_id: str) -> dict:
    _require(db, identity_id)
    tenants = db.fetchall("SELECT id, name, kind FROM tenants ORDER BY name, id")
    guards = db.fetchall(
        """
        SELECT id, display_name, email
        FROM identities WHERE crossing = 1
        ORDER BY created, id
        """
    )
    return {
        "orgs": [_org(db, tenant) for tenant in tenants],
        "guards": [
            {"id": row["id"], "name": row["display_name"], "email": row["email"] or ""}
            for row in guards
        ],
    }


def unlock_person(db: Database, actor_id: str, identity_id: str) -> dict:
    _require(db, actor_id)
    row = db.fetchone("SELECT id FROM identities WHERE id = ?", (identity_id,))
    if row is None:
        raise KeyError(identity_id)
    db.execute(
        "UPDATE identities SET locked = 0, failed_attempts = 0 WHERE id = ?",
        (identity_id,),
    )
    return board(db, actor_id)


def add_guard(db: Database, actor_id: str, *, name: str, email: str, password: str) -> dict:
    _require(db, actor_id)
    add_crossing_guard(db, name=name, email=email, password=password)
    return board(db, actor_id)


def save_sign_in(
    db: Database,
    root: Path,
    actor_id: str,
    *,
    tenant_id: str,
    provider: str,
    issuer: str = "",
    metadata_url: str = "",
    client_id: str = "",
    client_secret: str = "",
) -> dict:
    _require(db, actor_id)
    _tenant(db, tenant_id)
    clean = _sign_in_fields(provider, issuer, metadata_url, client_id)
    existing = _sign_in_row(db, tenant_id)
    secret = existing["client_secret_ciphertext"] if existing else ""
    if client_secret.strip():
        if len(client_secret) > 500:
            raise ValueError("client secret must be 500 characters or fewer")
        secret = seal(tenant_dek(db, root, tenant_id), client_secret)
    db.execute(
        """
        INSERT INTO sign_in (
            tenant_id, provider, issuer, metadata_url, client_id, client_secret_ciphertext
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (tenant_id) DO UPDATE SET
            provider = excluded.provider,
            issuer = excluded.issuer,
            metadata_url = excluded.metadata_url,
            client_id = excluded.client_id,
            client_secret_ciphertext = excluded.client_secret_ciphertext
        """,
        (
            tenant_id,
            clean["provider"],
            clean["issuer"],
            clean["metadata_url"],
            clean["client_id"],
            secret,
        ),
    )
    return board(db, actor_id)


def validate_sign_in(db: Database, root: Path, actor_id: str, tenant_id: str) -> dict:
    """Check the saved provider. A metadata URL is fetched. The secret stays sealed."""
    _require(db, actor_id)
    _tenant(db, tenant_id)
    row = _sign_in_row(db, tenant_id)
    provider = row["provider"] if row else "password"
    issuer = row["issuer"] if row else ""
    metadata_url = row["metadata_url"] if row else ""
    client_id = row["client_id"] if row else ""
    _sign_in_fields(provider, issuer, metadata_url, client_id)
    if metadata_url:
        _fetch_metadata(metadata_url)
    if row and row["client_secret_ciphertext"]:
        open_secret(tenant_dek(db, root, tenant_id), row["client_secret_ciphertext"])
    shown = board(db, actor_id)
    shown["notice"] = "the sign-in settings check out"
    return shown


def _org(db: Database, tenant: dict) -> dict:
    tenant_id = str(tenant["id"])
    people = db.fetchall(
        """
        SELECT identities.id, identities.display_name, identities.email,
               identities.locked, tenant_memberships.rank
        FROM tenant_memberships
        JOIN identities ON identities.id = tenant_memberships.identity_id
        WHERE tenant_memberships.tenant_id = ?
        ORDER BY tenant_memberships.created, identities.id
        """,
        (tenant_id,),
    )
    faults = db.fetchall(
        """
        SELECT id, status, fault, created
        FROM tasks
        WHERE tenant_id = ? AND fault IS NOT NULL AND fault <> ''
        ORDER BY created DESC, id
        """,
        (tenant_id,),
    )
    return {
        "id": tenant_id,
        "name": tenant["name"],
        "kind": tenant["kind"],
        "people": [
            {
                "id": person["id"],
                "name": person["display_name"],
                "email": person["email"] or "",
                "role": person["rank"],
                "locked": bool(person["locked"]),
            }
            for person in people
        ],
        "teams": list_teams(db, tenant_id),
        "sign_in": _public_sign_in(db, tenant_id),
        "faults": [
            {
                "id": row["id"],
                "status": row["status"],
                "fault": row["fault"],
                "created": row["created"],
            }
            for row in faults
        ],
    }


def _require(db: Database, identity_id: str) -> dict:
    from agent_lbw.runtime.store.install import require_account

    row = db.fetchone("SELECT crossing FROM identities WHERE id = ?", (identity_id,))
    if row is None or not int(row["crossing"] or 0):
        raise PermissionError("crossing guard is for LBW engineers")
    return require_account(db)


def _tenant(db: Database, tenant_id: str) -> None:
    row = db.fetchone("SELECT id FROM tenants WHERE id = ?", (tenant_id,))
    if row is None:
        raise KeyError(tenant_id)


def _sign_in_row(db: Database, tenant_id: str) -> dict | None:
    return db.fetchone(
        """
        SELECT provider, issuer, metadata_url, client_id, client_secret_ciphertext
        FROM sign_in WHERE tenant_id = ?
        """,
        (tenant_id,),
    )


def _public_sign_in(db: Database, tenant_id: str) -> dict:
    row = _sign_in_row(db, tenant_id)
    if row is None:
        return {
            "provider": "password",
            "issuer": "",
            "metadata_url": "",
            "client_id": "",
            "has_secret": False,
        }
    return {
        "provider": row["provider"],
        "issuer": row["issuer"],
        "metadata_url": row["metadata_url"],
        "client_id": row["client_id"],
        "has_secret": bool(row["client_secret_ciphertext"]),
    }


def _sign_in_fields(provider: str, issuer: str, metadata_url: str, client_id: str) -> dict:
    chosen = provider.strip().casefold()
    if chosen not in PROVIDERS:
        raise ValueError("provider must be password, entra, auth0, or saml")
    issuer_text = issuer.strip()
    metadata_text = metadata_url.strip()
    client_text = client_id.strip()
    if chosen != "password" and not issuer_text:
        raise ValueError("issuer is required")
    if issuer_text and _HTTPS.fullmatch(issuer_text) is None:
        raise ValueError("issuer must be an https URL")
    if metadata_text and _HTTPS.fullmatch(metadata_text) is None:
        raise ValueError("metadata URL must be an https URL")
    if client_text and _CLIENT.fullmatch(client_text) is None:
        raise ValueError("client id is not usable")
    return {
        "provider": chosen,
        "issuer": issuer_text,
        "metadata_url": metadata_text,
        "client_id": client_text,
    }


def _fetch_metadata(url: str) -> None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            code = int(getattr(response, "status", 200))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ValueError("the metadata URL could not be checked") from exc
    if code >= 400:
        raise ValueError("the metadata URL could not be checked")
