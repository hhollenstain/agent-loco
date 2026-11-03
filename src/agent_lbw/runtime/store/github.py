"""A tenant's GitHub App. The private key is sealed. Tokens are minted when used."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.crypto import open_secret, seal
from agent_lbw.runtime.store.install import require_account
from agent_lbw.runtime.store.tasks import tenant_dek

_API = "https://api.github.com"
_TOKEN_TTL = 45 * 60
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98})$")
_tokens: dict[tuple[str, str, str], tuple[float, str]] = {}


class GitHubAppError(Exception):
    """GitHub refused the app, or the installation token could not be minted."""


def github_app_view(db: Database) -> dict:
    account = require_account(db)
    row = _row(db, account["org"]["id"])
    if row is None:
        view = _view("", "", "")
    else:
        view = _view(row["app_id"], row["installation_id"] or "", row["account_login"] or "")
    view["installations"] = saved_installations(db)
    return view


def saved_installations(db: Database) -> list[dict]:
    """Installations last seen for this app. This does not call GitHub."""
    account = require_account(db)
    tenant_id = account["org"]["id"]
    rows = db.fetchall(
        """
        SELECT installation_id, account_login, account_type
        FROM github_installations
        WHERE tenant_id = ?
        ORDER BY account_login, installation_id
        """,
        (tenant_id,),
    )
    found = [_installation(row) for row in rows]
    row = _row(db, tenant_id)
    chosen = "" if row is None else str(row["installation_id"] or "")
    if chosen and not any(item["id"] == chosen for item in found):
        found.insert(
            0,
            {
                "id": chosen,
                "account_login": str(row["account_login"] or ""),
                "account_type": str(row["account_type"] or ""),
            },
        )
    return found


def github_app_state(db: Database, root: Path) -> dict:
    """The saved app plus the install link. Members do not call GitHub."""
    view = github_app_view(db)
    view["install_url"] = ""
    if view["configured"] and _is_manager(db):
        view["install_url"] = install_url(db, root)
    return view


def install_url(db: Database, root: Path) -> str:
    """Where an owner installs this app on a GitHub account or organization."""
    try:
        creds = _credentials(db, root, require_account(db)["org"]["id"])
        payload = _github_json("GET", "/app", jwt=_jwt(creds))
    except (GitHubAppError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    slug = str(payload.get("slug") or "")
    if _SLUG.fullmatch(slug) is None:
        return ""
    return f"https://github.com/apps/{slug}/installations/new"


def save_github_app(db: Database, root: Path, *, app_id: str, private_key: str) -> dict:
    account = _require_manager(db)
    clean_id = app_id.strip()
    if not clean_id.isdigit() or len(clean_id) > 20:
        raise ValueError("app id is the number GitHub shows for the app")
    tenant_id = account["org"]["id"]
    existing = _row(db, tenant_id)
    key = private_key.strip()
    if key:
        _load_key(key)
        ciphertext = seal(tenant_dek(db, root, tenant_id), key)
    elif existing is not None and existing["app_id"] == clean_id:
        ciphertext = existing["private_key_ciphertext"]
    else:
        raise ValueError("paste the app's private key")
    same_app = existing is not None and existing["app_id"] == clean_id
    installation_id = existing["installation_id"] if same_app else None
    account_login = existing["account_login"] if same_app else None
    account_type = existing["account_type"] if same_app else None
    if not same_app:
        db.execute("DELETE FROM github_installations WHERE tenant_id = ?", (tenant_id,))
    db.execute(
        """
            INSERT INTO github_apps (
                tenant_id, app_id, private_key_ciphertext, installation_id,
                account_login, account_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id) DO UPDATE SET
                app_id = excluded.app_id,
                private_key_ciphertext = excluded.private_key_ciphertext,
                installation_id = excluded.installation_id,
                account_login = excluded.account_login,
                account_type = excluded.account_type
            """,
        (tenant_id, clean_id, ciphertext, installation_id, account_login, account_type),
    )
    found: list[dict] = []
    warning = ""
    try:
        found = list_installations(db, root)
    except GitHubAppError as exc:
        warning = str(exc)
    else:
        current = _row(db, tenant_id)
        if current is not None and not current["installation_id"] and len(found) == 1:
            select_installation(db, root, found[0]["id"])
    view = github_app_view(db)
    view["install_url"] = "" if warning else install_url(db, root)
    if warning:
        view["warning"] = warning
    return view


def list_installations(db: Database, root: Path) -> list[dict]:
    account = _require_manager(db)
    creds = _credentials(db, root, account["org"]["id"])
    payload = _github_json("GET", "/app/installations", jwt=_jwt(creds))
    if not isinstance(payload, list):
        raise GitHubAppError("GitHub did not list installations")
    found: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        owner = item.get("account") if isinstance(item.get("account"), dict) else {}
        installation_id = str(item.get("id") or "")
        if not installation_id:
            continue
        found.append(
            {
                "id": installation_id,
                "account_login": str(owner.get("login") or ""),
                "account_type": str(owner.get("type") or ""),
            }
        )
    found.sort(key=lambda item: (item["account_login"].casefold(), item["id"]))
    _replace_installations(db, account["org"]["id"], found)
    chosen = {item["id"] for item in found}
    current = _row(db, account["org"]["id"])
    if (
        current is not None
        and current["installation_id"]
        and current["installation_id"] not in chosen
    ):
        db.execute(
            """
            UPDATE github_apps
            SET installation_id = NULL, account_login = NULL, account_type = NULL
            WHERE tenant_id = ?
            """,
            (account["org"]["id"],),
        )
    return found


def select_installation(db: Database, root: Path, installation_id: str) -> dict:
    account = _require_manager(db)
    chosen_id = installation_id.strip()
    if not chosen_id.isdigit():
        raise ValueError("choose an installation")
    choices = {item["id"]: item for item in list_installations(db, root)}
    chosen = choices.get(chosen_id)
    if chosen is None:
        raise ValueError("that installation is not on this app")
    db.execute(
        """
        UPDATE github_apps
        SET installation_id = ?, account_login = ?, account_type = ?
        WHERE tenant_id = ?
        """,
        (
            chosen_id,
            chosen["account_login"] or None,
            chosen["account_type"] or None,
            account["org"]["id"],
        ),
    )
    return github_app_view(db)


def installation_token(db: Database, root: Path) -> str | None:
    """Mint a short-lived installation token. Nothing stores it."""
    from agent_lbw.runtime.store.install import read_account

    account = read_account(db)
    if account is None:
        return None
    row = _row(db, account["org"]["id"])
    if row is None or not row["installation_id"]:
        return None
    creds = _credentials(db, root, account["org"]["id"])
    cache_key = (
        str(Path(root).resolve()),
        str(row["installation_id"]),
        str(row["private_key_ciphertext"])[:24],
    )
    cached = _tokens.get(cache_key)
    now = time.time()
    if cached is not None and cached[0] > now:
        return cached[1]
    payload = _github_json(
        "POST",
        f"/app/installations/{row['installation_id']}/access_tokens",
        jwt=_jwt(creds),
    )
    if not isinstance(payload, dict) or not payload.get("token"):
        raise GitHubAppError("GitHub did not return an installation token")
    token = str(payload["token"])
    _tokens[cache_key] = (now + _TOKEN_TTL, token)
    return token


def current_token(root: Path, database_url: str = "") -> str | None:
    from agent_lbw.runtime.db import database

    with database(root, database_url) as db:
        return installation_token(db, root)


def _view(app_id: str, installation_id: str, account_login: str) -> dict:
    return {
        "configured": bool(app_id),
        "app_id": app_id,
        "installation_id": installation_id,
        "account_login": account_login,
    }


def _installation(row: dict) -> dict:
    return {
        "id": str(row["installation_id"]),
        "account_login": str(row["account_login"] or ""),
        "account_type": str(row["account_type"] or ""),
    }


def _replace_installations(db: Database, tenant_id: str, found: list[dict]) -> None:
    db.execute("DELETE FROM github_installations WHERE tenant_id = ?", (tenant_id,))
    for item in found:
        db.execute(
            """
            INSERT INTO github_installations (
                tenant_id, installation_id, account_login, account_type
            ) VALUES (?, ?, ?, ?)
            """,
            (tenant_id, item["id"], item["account_login"], item["account_type"]),
        )


def require_github_manager(db: Database) -> dict:
    return _require_manager(db)


def github_manager(db: Database) -> bool:
    return _is_manager(db)


def github_installation(db: Database, tenant_id: str):
    return _row(db, tenant_id)


def _row(db: Database, tenant_id: str):
    return db.fetchone(
        """
        SELECT app_id, private_key_ciphertext, installation_id, account_login, account_type
        FROM github_apps WHERE tenant_id = ?
        """,
        (tenant_id,),
    )


def _is_manager(db: Database) -> bool:
    account = require_account(db)
    current = next(
        (user for user in account["users"] if user["id"] == account["current_user_id"]),
        None,
    )
    return current is not None and current["role"] in {"owner", "admin"}


def _require_manager(db: Database) -> dict:
    account = require_account(db)
    if not _is_manager(db):
        raise PermissionError("an owner or admin connects the GitHub App")
    return account


def _credentials(db: Database, root: Path, tenant_id: str) -> tuple[str, str]:
    row = _row(db, tenant_id)
    if row is None:
        raise ValueError("save the GitHub App id and private key first")
    return row["app_id"], open_secret(
        tenant_dek(db, root, tenant_id), row["private_key_ciphertext"]
    )


def _jwt(creds: tuple[str, str], *, now: int | None = None) -> str:
    app_id, private_key = creds
    issued = int(time.time()) if now is None else now
    header = _b64({"alg": "RS256", "typ": "JWT"})
    body = _b64({"iat": issued - 60, "exp": issued + 8 * 60, "iss": int(app_id)})
    signing = f"{header}.{body}".encode()
    signature = _load_key(private_key).sign(signing, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64_bytes(signature)}"


def _load_key(private_key: str):
    try:
        return serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError("private key could not be read") from exc


def github_api(method: str, path: str, *, token: str) -> object:
    """Call GitHub with an installation token. The token is not returned."""
    return _request(method, path, auth=f"Bearer {token}")


def _github_json(method: str, path: str, *, jwt: str) -> object:
    return _request(method, path, auth=f"Bearer {jwt}")


def _request(method: str, path: str, *, auth: str) -> object:
    try:
        response = httpx.request(
            method,
            f"{_API}{path}",
            headers={
                "Authorization": auth,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise GitHubAppError("GitHub could not be reached") from exc
    if response.status_code == 401:
        raise GitHubAppError("GitHub refused the app credentials")
    if response.status_code == 403:
        raise GitHubAppError(
            "GitHub refused that request. Grant the app Contents and Members read."
        )
    if response.status_code >= 400:
        raise GitHubAppError(f"GitHub returned {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise GitHubAppError("GitHub returned an unreadable response") from exc


def _b64(payload: dict) -> str:
    return _b64_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _b64_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
