"""Crossing guard is for LBW engineers, not an organization admin."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from lbw_crossing.app import create_app as create_crossing_app

from agent_lbw.config import Settings
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.auth import hash_password
from agent_lbw.runtime.store.projects import ensure_project
from agent_lbw.runtime.store.tasks import fault_for
from agent_lbw.runtime.tasks import TaskManager
from agent_lbw.web_ui import create_app

_PASSWORD = "correct-horse"


def test_fault_for_keeps_code_failures_off_the_board() -> None:
    assert fault_for(status="failed", reason="tests failed; commit skipped") == ""
    assert fault_for(status="failed", reason="QA engineer send back: the change is wrong") == ""
    assert fault_for(status="error", error="database is closed") == "database is closed"
    prepared = fault_for(status="failed", reason="could not leave main: refused")
    assert prepared == "could not leave main: LBW could not finish this job"


def test_crossing_guard_restores_access_without_opening_a_change(
    settings: Settings, tmp_path: Path
) -> None:
    app = create_app(TaskManager(settings), default_workspace=tmp_path)
    owner = TestClient(app)
    admin = TestClient(app)
    ui = app.state.ui
    board = TestClient(create_crossing_app(Path(ui.store_root), ui.manager.settings.database_url))

    def use(source: TestClient) -> TestClient:
        board.cookies.clear()
        for name, value in source.cookies.items():
            board.cookies.set(name, value)
        return board

    page = use(owner).get("/crossingguard")
    assert page.status_code == 200
    assert 'id="crossing-app"' in page.text
    assert "does not open a repository" in page.text
    assert "Add crossing guard" in page.text
    assert "Remove team" not in page.text
    home = owner.get("/")
    assert 'id="open-crossing"' in home.text
    assert 'data-crossing-url="/crossingguard"' in home.text

    org_host = {"host": "www-local.lookbothways.dev"}
    org_page = owner.get("/", headers=org_host)
    assert 'id="goal"' in org_page.text or 'id="auth-gate"' in org_page.text
    assert 'data-crossing-url="http://crossing-local.lookbothways.dev/"' in org_page.text
    jumped = owner.get("/crossingguard", headers=org_host, follow_redirects=False)
    assert jumped.status_code == 303
    assert jumped.headers["location"] == "http://crossing-local.lookbothways.dev/"
    engineer = board.get("/", headers={"host": "crossing-local.lookbothways.dev"})
    assert 'id="crossing-app"' in engineer.text
    assert 'id="open-integrations"' in engineer.text
    assert 'id="crossing-integrations"' in engineer.text
    assert 'id="crossing-save-github"' in engineer.text
    assert "http://www-local.lookbothways.dev/api/github/callback" in engineer.text
    assert 'id="goal"' not in engineer.text
    again = board.get(
        "/crossingguard",
        headers={"host": "crossing-local.lookbothways.dev"},
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert again.headers["location"] == "/"

    created = owner.post(
        "/api/auth/setup",
        json={"name": "Ada", "email": "ada@example.com", "password": _PASSWORD},
    )
    assert created.status_code == 200
    ada = next(user for user in created.json()["users"] if user["role"] == "owner")
    assert ada["crossing"] is True
    added = owner.post(
        "/api/account/users",
        json={"name": "Grace", "email": "grace@example.com"},
    )
    grace = next(user for user in added.json()["users"] if user["name"] == "Grace")
    ranked = owner.put(f"/api/account/users/{grace['id']}", json={"role": "admin"})
    assert ranked.status_code == 200
    with database(tmp_path) as db:
        db.execute(
            "UPDATE identities SET password_hash = ? WHERE id = ?",
            (hash_password(_PASSWORD), grace["id"]),
        )
    signed = admin.post(
        "/api/auth/login",
        json={"email": "grace@example.com", "password": _PASSWORD},
    )
    assert signed.status_code == 200
    refused = use(admin).get("/api/crossingguard")
    assert refused.status_code == 403
    admin.post("/api/auth/logout")
    for _ in range(5):
        wrong = admin.post(
            "/api/auth/login",
            json={"email": "grace@example.com", "password": "wrong-password"},
        )
        assert wrong.status_code == 400
    locked = admin.post(
        "/api/auth/login",
        json={"email": "grace@example.com", "password": _PASSWORD},
    )
    assert locked.status_code == 400
    assert locked.json()["error"] == "this account is locked"
    opened = use(owner).post(f"/api/crossingguard/people/{grace['id']}/unlock")
    assert opened.status_code == 200
    people = [person for org in opened.json()["orgs"] for person in org["people"]]
    assert next(person for person in people if person["id"] == grace["id"])["locked"] is False
    again = admin.post(
        "/api/auth/login",
        json={"email": "grace@example.com", "password": _PASSWORD},
    )
    assert again.status_code == 200

    with database(tmp_path) as db:
        project = ensure_project(
            db,
            created.json()["org"]["id"],
            name="box",
            kind="folder",
            locator=str(tmp_path),
        )
        db.execute(
            """
            INSERT INTO tasks (
                id, tenant_id, project_id, requester_id, status, branch, pr_url,
                run_ref, goal_ciphertext, fault, created
            ) VALUES (?, ?, ?, ?, 'error', 'feat', 'https://github.com/acme/app/pull/9',
                      '.lbw/runs/task_fault', 'sealed-goal', ?, 1)
            """,
            (
                "task_fault",
                created.json()["org"]["id"],
                project["id"],
                ada["id"],
                "LBW crashed while running this job",
            ),
        )
        db.execute(
            """
            INSERT INTO tasks (
                id, tenant_id, project_id, requester_id, status, branch, pr_url,
                run_ref, goal_ciphertext, fault, created
            ) VALUES (?, ?, ?, ?, 'failed', 'feat', 'https://github.com/acme/app/pull/10',
                      '.lbw/runs/task_code', 'sealed-goal', NULL, 2)
            """,
            (
                "task_code",
                created.json()["org"]["id"],
                project["id"],
                ada["id"],
            ),
        )
    listed = use(owner).get("/api/crossingguard")
    assert listed.status_code == 200
    body = listed.json()
    home = next(org for org in body["orgs"] if org["id"] == created.json()["org"]["id"])
    assert home["faults"] == [
        {
            "id": "task_fault",
            "status": "error",
            "fault": "LBW crashed while running this job",
            "created": 1,
        }
    ]
    assert "pull/" not in listed.text
    assert "sealed-goal" not in listed.text
    assert "feat" not in listed.text

    signed_up = TestClient(app).post(
        "/api/auth/signup",
        json={
            "name": "Mina",
            "email": "mina@example.com",
            "password": _PASSWORD,
            "organization": "Naval",
        },
    )
    assert signed_up.status_code == 200
    assert signed_up.json()["org"]["name"] == "Naval"
    assert signed_up.json()["org"]["kind"] == "organization"
    mina = next(user for user in signed_up.json()["users"] if user["role"] == "owner")
    assert mina["crossing"] is False
    assert (
        TestClient(app)
        .post(
            "/api/auth/login",
            json={"email": "mina@example.com", "password": _PASSWORD},
        )
        .status_code
        == 200
    )
    naval = TestClient(app)
    naval.post("/api/auth/login", json={"email": "mina@example.com", "password": _PASSWORD})
    assert use(naval).get("/api/crossingguard").status_code == 403

    listed = use(owner).get("/api/crossingguard")
    names = {org["name"] for org in listed.json()["orgs"]}
    assert "Naval" in names
    assert created.json()["org"]["name"] in names
    gone = use(owner).delete("/api/crossingguard/teams/team_missing")
    assert gone.status_code == 404

    tenant_id = created.json()["org"]["id"]
    missing = use(owner).put(
        "/api/crossingguard/sign-in",
        json={"tenant_id": tenant_id, "provider": "entra", "issuer": ""},
    )
    assert missing.status_code == 400
    saved = use(owner).put(
        "/api/crossingguard/sign-in",
        json={
            "tenant_id": tenant_id,
            "provider": "entra",
            "issuer": "https://login.example.com/tenant",
            "client_id": "lbw-app",
            "client_secret": "hunter2",
        },
    )
    assert saved.status_code == 200
    saved_org = next(org for org in saved.json()["orgs"] if org["id"] == tenant_id)
    assert saved_org["sign_in"]["provider"] == "entra"
    assert saved_org["sign_in"]["has_secret"] is True
    assert "hunter2" not in saved.text
    assert "client_secret_ciphertext" not in saved.text
    checked = use(owner).post(
        "/api/crossingguard/sign-in/validate",
        json={"tenant_id": tenant_id},
    )
    assert checked.status_code == 200
    assert checked.json()["notice"] == "the sign-in settings check out"
    added = use(owner).post(
        "/api/crossingguard/guards",
        json={"name": "Edith", "email": "edith@example.com", "password": _PASSWORD},
    )
    assert added.status_code == 200
    assert any(guard["email"] == "edith@example.com" for guard in added.json()["guards"])
    edith = TestClient(app)
    signed = edith.post(
        "/api/auth/login",
        json={"email": "edith@example.com", "password": _PASSWORD},
    )
    assert signed.status_code == 200
    edith_user = next(
        user for user in signed.json()["users"] if user["email"] == "edith@example.com"
    )
    assert edith_user["role"] == "member"
    assert edith_user["crossing"] is True
    assert use(edith).get("/api/crossingguard").status_code == 200
