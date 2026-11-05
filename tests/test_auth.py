"""Sign-in, the first admin account, and organization teams."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_lbw.config import Settings
from agent_lbw.runtime.db import database
from agent_lbw.runtime.tasks import TaskManager
from agent_lbw.web_ui import create_app

_PASSWORD = "correct-horse"


def test_first_visit_creates_the_admin_then_requires_sign_in(
    settings: Settings, tmp_path: Path
) -> None:
    client = TestClient(create_app(TaskManager(settings), default_workspace=tmp_path))
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="auth-gate"' in page.text
    assert 'id="auth-sso"' in page.text
    assert 'id="auth-github"' in page.text
    assert 'id="org-tab-teams"' in page.text
    assert 'id="team-member-picker"' in page.text
    assert 'id="team-member-search"' in page.text
    assert "Remove team" in page.text
    assert 'id="profile-email"' in page.text
    assert 'id="github-connect"' in page.text
    assert 'id="personal-tab-rules"' in page.text
    assert 'id="add-personal-rule"' in page.text
    assert (
        'id="personal-tab-github" data-personal-tab="github" aria-pressed="false" hidden'
        in page.text
    )
    status = client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json() == {
        "setup": True,
        "signed_in": False,
        "provider": "password",
        "github": False,
    }

    short = client.post(
        "/api/auth/setup",
        json={"name": "Ada", "email": "ada@example.com", "password": "short"},
    )
    assert short.status_code == 400

    created = client.post(
        "/api/auth/setup",
        json={"name": "Ada", "email": "Ada@Example.com", "password": _PASSWORD},
    )
    assert created.status_code == 200
    assert _PASSWORD not in created.text
    assert "password_hash" not in created.text
    owner = next(user for user in created.json()["users"] if user["role"] == "owner")
    assert owner["name"] == "Ada"
    assert owner["email"] == "ada@example.com"
    again = client.post(
        "/api/auth/setup",
        json={"name": "Bea", "email": "bea@example.com", "password": _PASSWORD},
    )
    assert again.status_code == 400

    guest = TestClient(create_app(TaskManager(settings), default_workspace=tmp_path))
    refused = guest.get("/api/account")
    assert refused.status_code == 401
    wrong = guest.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": "wrong-password"},
    )
    assert wrong.status_code == 400
    signed = guest.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": _PASSWORD},
    )
    assert signed.status_code == 200
    assert signed.json()["current_user_id"] == owner["id"]

    added = guest.post(
        "/api/account/users",
        json={"name": "Grace", "email": "grace@example.com"},
    )
    assert added.status_code == 200
    grace = next(user for user in added.json()["users"] if user["name"] == "Grace")
    assert grace["email"] == "grace@example.com"
    assert grace["role"] == "member"
    ranked = guest.put(f"/api/account/users/{grace['id']}", json={"role": "admin"})
    assert ranked.status_code == 200
    grace_role = next(user["role"] for user in ranked.json()["users"] if user["id"] == grace["id"])
    assert grace_role == "admin"
    kept = guest.put(f"/api/account/users/{owner['id']}", json={"role": "member"})
    assert kept.status_code == 400

    promoted = guest.post("/api/directory/promote")
    assert promoted.status_code == 200
    team = guest.post("/api/directory/teams", json={"name": "Platform"})
    assert team.status_code == 200
    assert team.json()["name"] == "Platform"
    joined = guest.post(
        f"/api/directory/teams/{team.json()['id']}/members",
        json={"identity_id": grace["id"], "rank": "admin"},
    )
    assert joined.status_code == 200
    listed = guest.get("/api/directory").json()
    platform = next(item for item in listed["teams"] if item["name"] == "Platform")
    assert platform["members"] == [{"id": grace["id"], "name": "Grace", "rank": "admin"}]
    account = guest.get("/api/account").json()
    grace_again = next(user for user in account["users"] if user["id"] == grace["id"])
    assert grace_again["teams"] == [{"id": team.json()["id"], "name": "Platform", "rank": "admin"}]
    occupied = guest.delete(f"/api/directory/teams/{team.json()['id']}")
    assert occupied.status_code == 400
    assert occupied.json()["error"] == "a team can be removed only when it is empty"
    guest.delete(f"/api/directory/teams/{team.json()['id']}/members/{grace['id']}")
    removed = guest.delete(f"/api/directory/teams/{team.json()['id']}")
    assert removed.status_code == 200
    assert removed.json()["teams"] == []
    with database(tmp_path) as db:
        row = db.fetchone(
            "SELECT password_hash FROM identities WHERE email = ?", ("ada@example.com",)
        )
    assert row is not None
    assert _PASSWORD not in row["password_hash"]
