"""A connected GitHub App can place repositories and copy access onto linked people."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_lbw.config import Settings
from agent_lbw.runtime.db import database
from agent_lbw.runtime.tasks import TaskManager
from agent_lbw.web_ui import create_app


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _become(root: Path, identity_id: str) -> None:
    with database(root) as db:
        db.execute("UPDATE install SET current_identity_id = ?", (identity_id,))


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_github_app_places_a_repository_and_syncs_linked_access(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    def fake_request(method: str, url: str, headers: dict | None = None, timeout: float = 20):
        assert headers is not None
        assert "BEGIN" not in headers["Authorization"]
        if method == "POST" and url.endswith("/access_tokens"):
            return _Response(200, {"token": "ghs_secret"})
        if method == "GET" and url.endswith("/app/installations"):
            return _Response(
                200,
                [{"id": 42, "account": {"login": "acme", "type": "Organization"}}],
            )
        if method == "GET" and url.endswith("/app"):
            return _Response(200, {"slug": "lbw-local"})
        if "/installation/repositories" in url:
            return _Response(200, {"repositories": [{"full_name": "acme/demo", "private": True}]})
        if "/teams/" in url and "role=maintainer" in url:
            return _Response(200, [{"login": "ada"}])
        if "/teams/" in url and "role=member" in url:
            return _Response(200, [{"login": "grace"}])
        if url.split("?", 1)[0].endswith("/teams"):
            return _Response(200, [{"name": "Platform", "slug": "platform"}])
        if "role=admin" in url:
            return _Response(200, [{"login": "ada"}, {"login": "bea"}])
        if "role=member" in url:
            return _Response(200, [{"login": "grace"}])
        if url.split("?", 1)[0].endswith("/collaborators"):
            return _Response(
                200,
                [
                    {"login": "ada", "permissions": {"admin": True, "push": True, "pull": True}},
                    {"login": "grace", "permissions": {"pull": True}},
                    {"login": "bea", "permissions": {"pull": False}},
                ],
            )
        return _Response(404, {})

    def fake_clone(url, parent, *, name=None, git_config=None):
        assert git_config is not None
        assert "ghs_secret" not in " ".join(git_config)
        dest = Path(parent) / (name or "demo")
        dest.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", url],
            cwd=dest,
            check=True,
            capture_output=True,
        )
        return {"path": str(dest), "name": dest.name}

    monkeypatch.setattr("agent_lbw.runtime.store.github.httpx.request", fake_request)
    monkeypatch.setattr("agent_lbw.github_http.clone_workspace", fake_clone)
    client = TestClient(create_app(TaskManager(settings), default_workspace=tmp_path))
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="add-github-repo"' in page.text
    assert 'id="sync-github-access"' in page.text
    assert 'id="load-github-repos"' in page.text

    saved = client.put("/api/github/app", json={"app_id": "123", "private_key": _pem()})
    assert saved.status_code == 200
    assert saved.json()["account_login"] == "acme"
    assert "ghs_secret" not in saved.text

    account = client.get("/api/account").json()
    owner = next(user for user in account["users"] if user["id"] == account["current_user_id"])
    linked = client.put(
        "/api/github/login",
        json={"user_id": owner["id"], "github_login": "ada"},
    )
    assert linked.status_code == 200
    assert linked.json()["users"][0]["github_login"] == "ada"
    for name, login in (("Bea", "bea"), ("Grace", "grace")):
        added = client.post("/api/account/users", json={"name": name}).json()
        person = next(user for user in added["users"] if user["name"] == name)
        assert (
            client.put(
                "/api/github/login",
                json={"user_id": person["id"], "github_login": login},
            ).status_code
            == 200
        )

    parent = tmp_path / "checkouts"
    placed = client.post(
        "/api/github/workspaces",
        json={
            "full_name": "acme/demo",
            "subject_type": "tenant",
            "subject_id": account["org"]["id"],
            "parent": str(parent),
        },
    )
    assert placed.status_code == 200
    body = placed.json()
    assert body["placed"] == "the organization"
    assert body["full_name"] == "acme/demo"
    assert "ghs_secret" not in placed.text
    assert (parent / "demo" / ".git").is_dir()

    synced = client.post("/api/github/sync")
    assert synced.status_code == 200
    summary = synced.json()
    assert "ghs_secret" not in synced.text
    roles = {user["name"]: user["role"] for user in summary["account"]["users"]}
    assert roles[owner["name"]] == "owner"
    assert roles["Bea"] == "admin"
    assert roles["Grace"] == "member"
    assert summary["teams"] == [{"name": "Platform", "members": ["ada", "grace"]}]

    with database(tmp_path) as db:
        grants = db.fetchall(
            """
            SELECT identities.github_login, project_grants.subject_type, project_grants.access
            FROM project_grants
            JOIN identities ON identities.id = project_grants.subject_id
            WHERE project_grants.subject_type = 'identity'
            ORDER BY identities.github_login
            """
        )
        team = db.fetchone("SELECT id FROM teams WHERE name = ?", ("Platform",))
    assert [(row["github_login"], row["access"]) for row in grants] == [
        ("ada", "manage"),
        ("grace", "use"),
    ]
    assert team is not None
    for_team = client.post(
        "/api/github/workspaces",
        json={
            "full_name": "acme/demo",
            "subject_type": "team",
            "subject_id": team["id"],
            "parent": str(parent),
        },
    )
    assert for_team.status_code == 200
    assert for_team.json()["placed"] == "team Platform"
    with database(tmp_path) as db:
        team_grant = db.fetchone(
            """
            SELECT access FROM project_grants
            WHERE subject_type = 'team' AND subject_id = ?
            """,
            (team["id"],),
        )
    assert team_grant is not None
    assert team_grant["access"] == "use"

    grace = next(user for user in summary["account"]["users"] if user["name"] == "Grace")
    _become(tmp_path, grace["id"])
    refused = client.post(
        "/api/github/workspaces",
        json={"full_name": "acme/demo", "subject_type": "tenant", "subject_id": ""},
    )
    assert refused.status_code == 403
    own = client.put(
        "/api/github/login",
        json={"user_id": grace["id"], "github_login": "grace"},
    )
    assert own.status_code == 200
    other = client.put(
        "/api/github/login",
        json={"user_id": owner["id"], "github_login": "someone"},
    )
    assert other.status_code == 403
