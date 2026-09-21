from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_mounts_workspace_not_app_loco() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${LOCO_WORKSPACE:-./workspaces}:/workspaces" in compose
    assert "./.loco:/workspaces/.loco" not in compose
    assert ".loco:/workspaces/.loco" not in compose
    assert '"${LOCO_UI_PORT:-8080}:8080"' in compose
    assert '"ui"' in compose
    assert "0.0.0.0" in compose


def test_readme_documents_loco_clone_not_docker_clone() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docker clone" not in readme
    assert "loco clone" in readme
    assert "clone first" in readme.lower()
    assert "init /workspaces" in readme
    assert "docker compose run --rm agent clone" in readme
