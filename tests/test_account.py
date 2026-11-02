from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_lbw.config import Settings
from agent_lbw.runtime.account import (
    account_path,
    add_user,
    load_account,
    remove_user,
    update_account,
)
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.schema import CLEAR_ORDER, clear_store
from agent_lbw.runtime.tasks import TaskManager
from agent_lbw.web_ui import create_app


def test_local_install_starts_with_one_owner(tmp_path: Path) -> None:
    account = load_account(tmp_path)
    assert account["mode"] == "local"
    assert account["org"]["name"] == "Local"
    assert len(account["users"]) == 1
    assert account["users"][0]["role"] == "owner"
    assert account["current_user_id"] == account["users"][0]["id"]
    again = load_account(tmp_path)
    assert again["org"]["id"] == account["org"]["id"]


def test_profile_renames_the_org_and_the_current_user(tmp_path: Path) -> None:
    updated = update_account(tmp_path, org_name="Acme", user_name="Ada")
    assert updated["org"]["name"] == "Acme"
    assert updated["users"][0]["name"] == "Ada"


def test_people_can_be_added_and_the_current_user_stays(tmp_path: Path) -> None:
    added = add_user(tmp_path, "Grace")
    assert [user["name"] for user in added["users"]][-1] == "Grace"
    try:
        add_user(tmp_path, "grace")
    except ValueError as exc:
        assert "already" in str(exc)
    else:
        raise AssertionError("expected a duplicate name to be refused")
    current = added["current_user_id"]
    try:
        remove_user(tmp_path, current)
    except ValueError as exc:
        assert "current user" in str(exc)
    else:
        raise AssertionError("expected the current user to stay")
    removed = remove_user(tmp_path, added["users"][-1]["id"])
    assert [user["name"] for user in removed["users"]] == [added["users"][0]["name"]]


def test_local_account_is_sqlite_and_keeps_an_older_json_file(tmp_path: Path) -> None:
    path = account_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mode": "local",
                "org": {"id": "org_fixed", "name": "Imported"},
                "users": [{"id": "user_fixed", "name": "Ada", "role": "owner"}],
                "current_user_id": "user_fixed",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_account(tmp_path)
    assert loaded["org"]["name"] == "Imported"
    assert (tmp_path / ".lbw" / "lbw.db").is_file()
    update_account(tmp_path, org_name="After")
    path.write_text(
        json.dumps(
            {
                "mode": "local",
                "org": {"id": "org_fixed", "name": "Stale"},
                "users": [{"id": "user_fixed", "name": "Ada", "role": "owner"}],
                "current_user_id": "user_fixed",
            }
        ),
        encoding="utf-8",
    )
    assert load_account(tmp_path)["org"]["name"] == "After"


def test_database_url_must_be_sqlite_or_postgres(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sqlite or postgres"):
        load_account(tmp_path, database_url="mysql://localhost/lbw")


def test_account_round_trips_through_postgres(tmp_path: Path) -> None:
    url = os.environ.get("LBW_DATABASE_URL", "").strip()
    if not url.startswith(("postgres://", "postgresql://")):
        pytest.skip("set LBW_DATABASE_URL to a postgres database")
    with database(tmp_path, url) as db:
        snapshot = _postgres_snapshot(db)
    try:
        with database(tmp_path, url) as db:
            clear_store(db)
        created = load_account(tmp_path, database_url=url)
        assert created["mode"] == "local"
        renamed = update_account(
            tmp_path,
            org_name="North",
            user_name="Ada",
            database_url=url,
        )
        added = add_user(tmp_path, "Grace", database_url=url)
        assert renamed["org"]["name"] == "North"
        assert [user["name"] for user in added["users"]] == ["Ada", "Grace"]
        again = load_account(tmp_path, database_url=url)
        assert again["org"]["id"] == renamed["org"]["id"]
        assert [user["name"] for user in again["users"]] == ["Ada", "Grace"]
    finally:
        with database(tmp_path, url) as db:
            _restore_postgres(db, snapshot)


def _postgres_snapshot(db) -> dict:
    return {name: db.fetchall(f"SELECT * FROM {name}") for name in CLEAR_ORDER}


def _restore_postgres(db, snapshot: dict) -> None:
    clear_store(db)
    for name in reversed(CLEAR_ORDER):
        for row in snapshot.get(name, []):
            columns = list(row)
            marks = ", ".join("?" for _ in columns)
            names = ", ".join(columns)
            db.execute(
                f"INSERT INTO {name} ({names}) VALUES ({marks})",
                tuple(row[column] for column in columns),
            )


def test_account_routes_save_the_org_and_its_people(settings: Settings, tmp_path: Path) -> None:
    manager = TaskManager(settings, runner=lambda task: None)
    app = create_app(manager, default_workspace=tmp_path)
    client = TestClient(app)
    try:
        created = client.get("/api/account")
        assert created.status_code == 200
        body = created.json()
        assert body["mode"] == "local"
        saved = client.put("/api/account", json={"org_name": "North", "user_name": "Ada"})
        assert saved.status_code == 200
        assert saved.json()["org"]["name"] == "North"
        person = client.post("/api/account/users", json={"name": "Grace"})
        assert person.status_code == 200
        grace = person.json()["users"][-1]
        assert grace["role"] == "member"
        gone = client.delete(f"/api/account/users/{grace['id']}")
        assert gone.status_code == 200
        assert [user["name"] for user in gone.json()["users"]] == ["Ada"]
        refused = client.put("/api/account", json={"org_name": "  "})
        assert refused.status_code == 400
        assert "error" in refused.json()
    finally:
        manager.shutdown(wait=False)
