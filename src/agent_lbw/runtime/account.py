"""The organization and people for this LBW install.

A local install stores them in SQLite under `.lbw/lbw.db`. A database URL
stores the same records in Postgres. The current user is the person this
process runs as.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.install import read_account, rename_member, save_account

ACCOUNT_FILENAME = "account.json"
NAME_LIMIT = 80
_ROLES = frozenset({"owner", "member"})


def account_path(root: Path) -> Path:
    return Path(root) / ".lbw" / ACCOUNT_FILENAME


def load_account(root: Path, *, database_url: str = "") -> dict:
    """Return this install's organization and people, creating them if needed."""
    with database(root, database_url) as db:
        found = read_account(db)
        if found is not None:
            return found
        account = _read_legacy(root) or _default_account()
        save_account(db, account)
        return read_account(db) or account


def update_account(
    root: Path,
    *,
    org_name: str | None = None,
    user_name: str | None = None,
    database_url: str = "",
) -> dict:
    """Rename the organization, the current person, or both."""
    with database(root, database_url) as db:
        account = _require(db, root)
        if org_name is not None:
            account["org"]["name"] = _clean_name(org_name, "organization")
        if user_name is not None:
            rename_member(account, _clean_name(user_name, "name"))
        save_account(db, account)
        return account


def add_user(root: Path, name: str, *, database_url: str = "") -> dict:
    """Add a person to the organization."""
    with database(root, database_url) as db:
        account = _require(db, root)
        clean = _clean_name(name, "name")
        if _name_taken(account, clean):
            raise ValueError(f"{clean} is already in this organization")
        account["users"].append({"id": _new_id("user"), "name": clean, "role": "member"})
        save_account(db, account)
        return read_account(db) or account


def remove_user(root: Path, user_id: str, *, database_url: str = "") -> dict:
    """Remove a person who is not the current user and not the last owner."""
    with database(root, database_url) as db:
        account = _require(db, root)
        target = next((user for user in account["users"] if user["id"] == user_id), None)
        if target is None:
            raise KeyError(user_id)
        if target["id"] == account["current_user_id"]:
            raise ValueError("the current user stays on this install")
        owners = [user for user in account["users"] if user["role"] == "owner"]
        if target["role"] == "owner" and len(owners) == 1:
            raise ValueError("the organization keeps one owner")
        account["users"] = [user for user in account["users"] if user["id"] != user_id]
        save_account(db, account)
        return read_account(db) or account


def _require(db, root: Path) -> dict:
    found = read_account(db)
    if found is not None:
        return found
    account = _read_legacy(root) or _default_account()
    save_account(db, account)
    return read_account(db) or account


def _read_legacy(root: Path) -> dict | None:
    path = account_path(root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _valid_account(payload)


def _default_account() -> dict:
    user_id = _new_id("user")
    return {
        "mode": "local",
        "org": {"id": _new_id("org"), "name": "Local"},
        "users": [{"id": user_id, "name": _machine_name(), "role": "owner"}],
        "current_user_id": user_id,
    }


def _machine_name() -> str:
    try:
        import getpass

        name = getpass.getuser().strip()
    except (ImportError, OSError):
        name = ""
    return name or "You"


def _new_id(kind: str) -> str:
    return f"{kind}_{uuid.uuid4().hex[:12]}"


def _clean_name(value: str, label: str) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > NAME_LIMIT:
        raise ValueError(f"{label} must be {NAME_LIMIT} characters or fewer")
    return text


def _name_taken(account: dict, name: str) -> bool:
    folded = name.casefold()
    return any(user["name"].casefold() == folded for user in account["users"])


def _valid_account(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    org = payload.get("org")
    users = payload.get("users")
    current = payload.get("current_user_id")
    if not isinstance(org, dict) or not isinstance(users, list) or not isinstance(current, str):
        return None
    org_id = org.get("id")
    org_name = org.get("name")
    if not isinstance(org_id, str) or not org_id or not isinstance(org_name, str) or not org_name:
        return None
    if len(org_name) > NAME_LIMIT:
        return None
    clean_users: list[dict[str, str]] = []
    seen: set[str] = set()
    for user in users:
        if not isinstance(user, dict):
            return None
        user_id = user.get("id")
        name = user.get("name")
        role = user.get("role")
        if (
            not isinstance(user_id, str)
            or not user_id
            or user_id in seen
            or not isinstance(name, str)
            or not name
            or len(name) > NAME_LIMIT
            or role not in _ROLES
        ):
            return None
        seen.add(user_id)
        clean_users.append({"id": user_id, "name": name, "role": role})
    if current not in seen or not clean_users:
        return None
    mode = payload.get("mode")
    if mode not in {"local", "hosted"}:
        mode = "local"
    return {
        "mode": mode,
        "org": {"id": org_id, "name": org_name},
        "users": clean_users,
        "current_user_id": current,
    }
