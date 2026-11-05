"""A GitHub App lets this install push, open pull requests, and merge."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from lbw_crossing.app import create_app as create_crossing_app

from agent_lbw.config import Settings
from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.projects import ensure_project
from agent_lbw.runtime.tasks import TaskManager
from agent_lbw.runtime.workspaces import clone_workspace
from agent_lbw.sandbox import Workspace
from agent_lbw.tools.git import create_pull_request, push_changes
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


def _empty_sync(method: str, url: str) -> _Response | None:
    if method == "POST" and url.endswith("/access_tokens"):
        return _Response(200, {"token": "ghs_secret"})
    if "/installation/repositories" in url:
        return _Response(200, {"repositories": []})
    path = url.split("?", 1)[0]
    if "/orgs/" in path and (path.endswith("/members") or path.endswith("/teams")):
        return _Response(200, [])
    return None


def test_save_keeps_the_key_when_git_is_missing(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    def fake_request(method: str, url: str, headers: dict | None = None, timeout: float = 20):
        if method == "GET" and url.endswith("/app/installations"):
            return _Response(
                200,
                [{"id": 42, "account": {"login": "acme", "type": "Organization"}}],
            )
        if method == "GET" and url.endswith("/app"):
            return _Response(200, {"slug": "lbw-local"})
        synced = _empty_sync(method, url)
        if synced is not None:
            return synced
        return _Response(404, {})

    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("agent_lbw.runtime.store.github.httpx.request", fake_request)
    monkeypatch.setattr("agent_lbw.tools.git.run_git", missing_git)
    app = create_app(TaskManager(settings), default_workspace=tmp_path)
    account = load_account(tmp_path)
    with database(tmp_path) as db:
        ensure_project(
            db,
            account["org"]["id"],
            name="box",
            kind="folder",
            locator=str(tmp_path),
        )
    client = TestClient(app)
    private_key = _pem()
    saved = client.put(
        "/api/github/app",
        json={"app_id": "123", "private_key": private_key, "client_secret": "secret-one"},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["configured"] is True
    assert body["app_id"] == "123"
    assert body["account_login"] == "acme"
    assert "private_key" not in body
    with database(tmp_path) as db:
        row = db.fetchone(
            "SELECT private_key_ciphertext, client_secret_ciphertext FROM github_apps"
        )
    assert row is not None
    assert private_key not in row["private_key_ciphertext"]
    assert "secret-one" not in (row["client_secret_ciphertext"] or "")


def test_github_app_key_stays_sealed_and_a_member_cannot_replace_it(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, headers: dict | None = None, timeout: float = 20):
        calls.append((method, url))
        assert headers is not None
        assert "BEGIN" not in headers["Authorization"]
        if method == "GET" and url.endswith("/app/installations"):
            return _Response(
                200,
                [{"id": 42, "account": {"login": "acme", "type": "Organization"}}],
            )
        if method == "GET" and url.endswith("/app"):
            return _Response(200, {"slug": "lbw-local"})
        if method == "POST" and url.endswith("/access_tokens"):
            return _Response(200, {"token": "ghs_secret"})
        synced = _empty_sync(method, url)
        if synced is not None:
            return synced
        return _Response(404, {})

    monkeypatch.setattr("agent_lbw.runtime.store.github.httpx.request", fake_request)
    app = create_app(TaskManager(settings), default_workspace=tmp_path)
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="github-app"' in page.text
    assert 'id="save-github-app"' not in page.text
    assert 'id="github-app-key"' not in page.text
    assert 'id="github-app-install"' in page.text
    assert 'id="github-app-install-row"' in page.text
    ui = app.state.ui
    guard = TestClient(create_crossing_app(Path(ui.store_root), ui.manager.settings.database_url))
    guard = guard.get("/crossingguard")
    assert 'id="crossing-save-github"' in guard.text
    assert 'id="crossing-github-key"' in guard.text
    assert ">Refresh<" in page.text

    missing = client.put("/api/github/app", json={"app_id": "123", "private_key": ""})
    assert missing.status_code == 400

    private_key = _pem()
    saved = client.put(
        "/api/github/app",
        json={"app_id": "123", "private_key": private_key},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["configured"] is True
    assert body["app_id"] == "123"
    assert body["account_login"] == "acme"
    assert body["install_url"] == "https://github.com/apps/lbw-local/installations/new"
    assert "private_key" not in body
    assert "BEGIN" not in json.dumps(body)
    kept = client.put("/api/github/app", json={"app_id": "123", "private_key": ""})
    assert kept.status_code == 200

    with database(tmp_path) as db:
        row = db.fetchone("SELECT private_key_ciphertext FROM github_apps")
    assert row is not None
    assert "PRIVATE" not in row["private_key_ciphertext"]
    assert private_key not in row["private_key_ciphertext"]

    listed = client.get("/api/github/installations")
    assert listed.status_code == 200
    assert listed.json()["installations"][0]["account_login"] == "acme"
    chosen = client.put("/api/github/installation", json={"installation_id": "42"})
    assert chosen.status_code == 200
    assert chosen.json()["account_login"] == "acme"
    refused = client.put("/api/github/installation", json={"installation_id": "99"})
    assert refused.status_code == 400

    from agent_lbw.runtime.store.github import installation_token

    with database(tmp_path) as db:
        assert installation_token(db, tmp_path) == "ghs_secret"
    assert any(url.endswith("/access_tokens") for _method, url in calls)
    assert client.get("/api/github/token").status_code == 404

    captured: dict[str, object] = {}

    def fake_clone(url, parent, *, name=None, git_config=None):
        captured["url"] = url
        captured["config"] = git_config
        dest = tmp_path / "cloned"
        dest.mkdir()
        return {"path": str(dest), "name": "cloned"}

    monkeypatch.setattr("agent_lbw.web_ui.clone_workspace", fake_clone)
    cloned = client.post(
        "/api/workspaces/clone",
        json={
            "url": "https://github.com/acme/demo.git",
            "parent": str(tmp_path),
            "name": "cloned",
        },
    )
    assert cloned.status_code == 200
    assert captured["url"] == "https://github.com/acme/demo.git"
    config = captured["config"]
    assert isinstance(config, list)
    assert "ghs_secret" not in " ".join(config)
    assert any(part.startswith("http.extraheader=") for part in config)

    added = client.post("/api/account/users", json={"name": "Grace"})
    grace = next(user for user in added.json()["users"] if user["name"] == "Grace")
    _become(tmp_path, grace["id"])
    blocked = client.put(
        "/api/github/app",
        json={"app_id": "999", "private_key": private_key},
    )
    assert blocked.status_code == 403
    visible = client.get("/api/github/app")
    assert visible.status_code == 200
    assert visible.json()["app_id"] == "123"
    assert visible.json()["install_url"] == ""


def test_save_without_an_installation_keeps_the_install_link(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    def fake_request(method: str, url: str, headers: dict | None = None, timeout: float = 20):
        if method == "GET" and url.endswith("/app/installations"):
            return _Response(200, [])
        if method == "GET" and url.endswith("/app"):
            return _Response(200, {"slug": "lbw-local"})
        return _Response(404, {})

    monkeypatch.setattr("agent_lbw.runtime.store.github.httpx.request", fake_request)
    client = TestClient(create_app(TaskManager(settings), default_workspace=tmp_path))
    saved = client.put(
        "/api/github/app",
        json={"app_id": "123", "private_key": _pem()},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["configured"] is True
    assert body["account_login"] == ""
    assert body["installation_id"] == ""
    assert body["installations"] == []
    assert body["install_url"] == "https://github.com/apps/lbw-local/installations/new"
    assert "private_key" not in body
    assert "warning" not in body


def test_saved_installations_load_without_checking_github_again(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    listings = [
        {"id": 42, "account": {"login": "acme", "type": "Organization"}},
        {"id": 43, "account": {"login": "other", "type": "User"}},
    ]
    calls: list[str] = []

    def fake_request(method: str, url: str, headers: dict | None = None, timeout: float = 20):
        calls.append(url)
        if method == "GET" and url.endswith("/app/installations"):
            return _Response(200, listings)
        if method == "GET" and url.endswith("/app"):
            return _Response(200, {"slug": "lbw-local"})
        synced = _empty_sync(method, url)
        if synced is not None:
            return synced
        return _Response(404, {})

    monkeypatch.setattr("agent_lbw.runtime.store.github.httpx.request", fake_request)
    client = TestClient(create_app(TaskManager(settings), default_workspace=tmp_path))
    saved = client.put("/api/github/app", json={"app_id": "123", "private_key": _pem()})
    assert saved.status_code == 200
    assert saved.json()["installation_id"] == ""
    assert [item["account_login"] for item in saved.json()["installations"]] == ["acme", "other"]

    calls.clear()
    loaded = client.get("/api/github/app")
    assert loaded.status_code == 200
    body = loaded.json()
    assert [item["account_login"] for item in body["installations"]] == ["acme", "other"]
    assert not any(url.endswith("/app/installations") for url in calls)

    chosen = client.put("/api/github/installation", json={"installation_id": "42"})
    assert chosen.status_code == 200
    assert chosen.json()["account_login"] == "acme"
    with database(tmp_path) as db:
        db.execute("DELETE FROM github_installations")
    remembered = client.get("/api/github/app")
    assert remembered.json()["installations"] == [
        {"id": "42", "account_login": "acme", "account_type": "Organization"}
    ]

    listings.append({"id": 44, "account": {"login": "extra", "type": "Organization"}})
    refreshed = client.get("/api/github/installations")
    assert refreshed.status_code == 200
    assert [item["account_login"] for item in refreshed.json()["installations"]] == [
        "acme",
        "extra",
        "other",
    ]
    again = client.get("/api/github/app")
    assert again.json()["installation_id"] == "42"
    assert [item["account_login"] for item in again.json()["installations"]] == [
        "acme",
        "extra",
        "other",
    ]


def test_push_and_clone_use_the_token_without_putting_it_in_the_url(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = Workspace(tmp_path)
    calls: list[list[str]] = []

    def fake_run(ws, args, env=None, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="git@github.com:acme/demo.git\n", stderr=""
            )
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return subprocess.CompletedProcess(args, 0, stdout="lbw/feature\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="pushed\n", stderr="")

    monkeypatch.setattr("agent_lbw.tools.git.run_git", fake_run)
    pushed = push_changes(workspace, "origin", "lbw/feature", token="ghs_secret")
    assert pushed.ok
    push = next(args for args in calls if "push" in args)
    assert "https://github.com/acme/demo.git" in push
    assert "ghs_secret" not in " ".join(push)
    assert any(part.startswith("http.extraheader=") for part in push)
    assert ["branch", "--set-upstream-to=origin/lbw/feature"] in calls

    seen: dict[str, object] = {}

    def fake_subprocess(args, **kwargs):
        seen["args"] = list(args)
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            args, 0, stdout="https://github.com/acme/demo/pull/4\n", stderr=""
        )

    monkeypatch.setattr("agent_lbw.tools.git.current_branch", lambda ws: "lbw/feature")
    monkeypatch.setattr("agent_lbw.tools.git.subprocess.run", fake_subprocess)
    created = create_pull_request(workspace, "Title", "Body", token="ghs_secret")
    assert created.ok
    assert "ghs_secret" not in " ".join(seen["args"])
    assert seen["env"]["GH_TOKEN"] == "ghs_secret"

    cloned_args: list[str] = []

    def fake_clone(args, **kwargs):
        cloned_args.extend(args)
        Path(args[-1]).mkdir()
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_lbw.runtime.workspaces.subprocess.run", fake_clone)
    monkeypatch.setattr(
        "agent_lbw.runtime.workspaces.write_default_project_files", lambda dest: None
    )
    clone_workspace("https://github.com/acme/demo.git", tmp_path, name="public")
    assert cloned_args[1:3] == ["clone", "https://github.com/acme/demo.git"]
    assert "ghs_secret" not in " ".join(cloned_args)


def test_saved_key_signs_a_jwt(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    from agent_lbw.runtime.store.github import _jwt

    token = _jwt(("123", pem), now=1_700_000_000)
    header, body, signature = token.split(".")
    padded = signature + "=" * (-len(signature) % 4)
    key.public_key().verify(
        base64.urlsafe_b64decode(padded),
        f"{header}.{body}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert claims["iss"] == 123
    assert claims["exp"] - claims["iat"] == 9 * 60
