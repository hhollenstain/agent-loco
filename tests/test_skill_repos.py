"""Skill repositories stay in a KiwiFS space named from ids."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from tests.support import init_git_repo
from tests.test_rules import MemoryKiwi

from agent_lbw.config import Settings
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.skill_repos import skill_space
from agent_lbw.runtime.tasks import TaskManager
from agent_lbw.web_ui import create_app


def test_skill_space_uses_ids_only() -> None:
    org = "org_0123456789ab"
    team = "team_abcdefabcdef"
    project = "proj_1234567890ab"
    assert skill_space(org) == "sk-org-0123456789ab"
    assert skill_space(org, team_id=team) == "sk-org-0123456789ab-team-abcdefabcdef"
    assert skill_space(org, project_id=project) == "sk-org-0123456789ab-proj-1234567890ab"
    full = skill_space(org, team_id=team, project_id=project)
    assert full == "sk-org-0123456789ab-team-abcdefabcdef-proj-1234567890ab"
    assert "acme" not in full
    assert "northwind" not in full


def test_repo_skills_stay_in_the_org_team_and_project_space(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    memory = MemoryKiwi()
    monkeypatch.setattr("agent_lbw.rules_http.open_kiwi", lambda *_args, **_kwargs: memory)
    alpha = tmp_path / "alpha-app"
    beta = tmp_path / "beta-app"
    alpha.mkdir()
    beta.mkdir()
    remote = _skill_repo(tmp_path / "origin", body="Review the public API.")
    app = create_app(TaskManager(settings), default_workspace=alpha)
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    script = client.get("/static/app/app.js").text
    assert 'id:"add-org-skill-repo"' in script
    assert 'id:"org-skill-repos"' in script

    client.get("/api/directory")
    client.post("/api/directory/promote")
    renamed = client.put("/api/account", json={"org_name": "Acme Secrets"})
    assert renamed.status_code == 200
    team = client.post("/api/directory/teams", json={"name": "Northwind"})
    assert team.status_code == 200
    team_id = team.json()["id"]
    other = client.post("/api/directory/teams", json={"name": "Other Team"})
    other_id = other.json()["id"]
    first = client.post(
        "/api/directory/projects",
        json={"name": "Alpha Secrets", "kind": "folder", "locator": str(alpha)},
    )
    second = client.post(
        "/api/directory/projects",
        json={"name": "Beta Secrets", "kind": "folder", "locator": str(beta)},
    )
    assert first.status_code == 200
    alpha_id = first.json()["id"]
    beta_id = second.json()["id"]
    assert (
        client.post(
            f"/api/directory/projects/{alpha_id}/grants",
            json={"subject_type": "team", "subject_id": team_id, "access": "use"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/directory/projects/{beta_id}/grants",
            json={"subject_type": "team", "subject_id": other_id, "access": "use"},
        ).status_code
        == 200
    )

    added = client.post(
        "/api/org/skill-repos",
        json={"url": str(remote), "scope_type": "team", "scope_id": team_id},
    )
    assert added.status_code == 200
    repo = next(item for item in added.json()["skill_repos"] if item["scope_id"] == team_id)
    assert repo["sha"]
    assert "code-review" in repo["skills"]
    assert repo["space"] == skill_space(added.json()["org_id"], team_id=team_id)
    assert "Acme" not in repo["space"]
    assert "Northwind" not in repo["space"]
    assert "Secrets" not in repo["space"]
    stored = next(iter(memory.files.values()))
    assert "Review the public API." in stored

    seen = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    assert seen.status_code == 200
    assert "code-review" in [item["name"] for item in seen.json()["skills"]]
    detail = client.get(f"/api/org/skill-repos/{repo['id']}/skills/code-review")
    assert detail.status_code == 200
    assert detail.json()["description"] == "Review the public API."
    assert "Review the public API." in detail.json()["html"]
    off = client.put(
        f"/api/org/skill-repos/{repo['id']}/skills/code-review",
        json={"enabled": False},
    )
    assert off.status_code == 200
    paused = next(item for item in off.json()["skill_repos"] if item["id"] == repo["id"])
    assert (
        next(item["enabled"] for item in paused["entries"] if item["name"] == "code-review")
        is False
    )
    hidden_skill = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    assert "code-review" not in [item["name"] for item in hidden_skill.json()["skills"]]
    repo_off = client.put(f"/api/org/skill-repos/{repo['id']}", json={"enabled": False})
    assert repo_off.status_code == 200
    on = client.put(f"/api/org/skill-repos/{repo['id']}", json={"enabled": True})
    assert on.status_code == 200
    restored = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    assert "code-review" in [item["name"] for item in restored.json()["skills"]]
    hidden = client.get("/api/workspaces/skills", params={"path": str(beta)})
    assert "code-review" not in [item["name"] for item in hidden.json()["skills"]]

    org_remote = _skill_repo(tmp_path / "org-origin", name="shared-notes", body="Shared notes.")
    org_added = client.post(
        "/api/org/skill-repos",
        json={
            "url": str(org_remote),
            "scope_type": "tenant",
            "scope_id": added.json()["org_id"],
        },
    )
    assert org_added.status_code == 200
    for place in (alpha, beta):
        listed = client.get("/api/workspaces/skills", params={"path": str(place)})
        assert "shared-notes" in [item["name"] for item in listed.json()["skills"]]
    org_repo = next(
        item for item in org_added.json()["skill_repos"] if item["scope_type"] == "tenant"
    )
    dropped = client.delete(f"/api/org/skill-repos/{org_repo['id']}")
    assert dropped.status_code == 200
    assert all(item["id"] != org_repo["id"] for item in dropped.json()["skill_repos"])
    after_drop = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    assert "shared-notes" not in [item["name"] for item in after_drop.json()["skills"]]
    assert "code-review" in [item["name"] for item in after_drop.json()["skills"]]

    project_remote = _skill_repo(tmp_path / "project-origin", name="alpha-only", body="Alpha only.")
    project_added = client.post(
        "/api/workspaces/skills/sources",
        json={"path": str(alpha), "url": str(project_remote)},
    )
    assert project_added.status_code == 200
    project_repo = project_added.json()["added"]
    assert project_repo["space"] == skill_space(
        added.json()["org_id"], team_id=team_id, project_id=alpha_id
    )
    assert "Alpha" not in project_repo["space"]
    only_alpha = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    only_beta = client.get("/api/workspaces/skills", params={"path": str(beta)})
    assert "alpha-only" in [item["name"] for item in only_alpha.json()["skills"]]
    assert "alpha-only" not in [item["name"] for item in only_beta.json()["skills"]]
    assert "data-skill-source-remove" in script
    assert "data-org-skill-remove" in script
    assert "data-org-skill-info" in script
    assert "data-org-skill-enable" in script

    kept = client.post(
        "/api/workspaces/skills/sources/remove",
        json={"path": str(alpha), "slug": repo["id"]},
    )
    assert kept.status_code == 403
    removed = client.post(
        "/api/workspaces/skills/sources/remove",
        json={"path": str(alpha), "slug": project_repo["id"]},
    )
    assert removed.status_code == 200
    assert "alpha-only" not in [item["name"] for item in removed.json()["skills"]]
    assert "code-review" in [item["name"] for item in removed.json()["skills"]]

    extra = remote / "skills" / "follow-up"
    extra.mkdir()
    (extra / "SKILL.md").write_text(
        "---\nname: follow-up\ndescription: Follow up.\n---\n\nWrite the follow-up.\n",
        encoding="utf-8",
    )
    init_git_repo(remote)
    synced = client.post(f"/api/org/skill-repos/{repo['id']}/sync")
    assert synced.status_code == 200
    fresh = next(item for item in synced.json()["skill_repos"] if item["id"] == repo["id"])
    assert fresh["sha"] != repo["sha"]
    again = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    assert "follow-up" in [item["name"] for item in again.json()["skills"]]
    client.put(
        f"/api/org/skill-repos/{repo['id']}/skills/code-review",
        json={"enabled": False},
    )
    kept_off = client.post(f"/api/org/skill-repos/{repo['id']}/sync")
    assert kept_off.status_code == 200
    kept_repo = next(item for item in kept_off.json()["skill_repos"] if item["id"] == repo["id"])
    flags = {item["name"]: item["enabled"] for item in kept_repo["entries"]}
    assert flags["code-review"] is False
    assert flags["follow-up"] is True
    selective = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    names = [item["name"] for item in selective.json()["skills"]]
    assert "follow-up" in names
    assert "code-review" not in names

    with database(alpha) as db:
        db.execute(
            "UPDATE skill_repos SET space_name = ? WHERE id = ?",
            ("sk-org-deadbeefdead", repo["id"]),
        )
    leaked = client.get("/api/workspaces/skills", params={"path": str(alpha)})
    assert "follow-up" not in [item["name"] for item in leaked.json()["skills"]]
    assert "code-review" not in [item["name"] for item in leaked.json()["skills"]]


def _skill_repo(root: Path, *, name: str = "code-review", body: str = "Check the API.") -> Path:
    skill = root / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {body}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    init_git_repo(root)
    return root
