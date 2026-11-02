from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_up_local_builds_the_agent_image_before_postgres() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("up-local:\n", 1)[1].split("\nup-", 1)[0]
    build_at = recipe.find("build agent")
    postgres_at = recipe.find("up -d --wait postgres")
    built = "docker compose -f docker-compose.yml -f docker-compose.postgres.yml build agent"
    assert built in recipe
    assert build_at != -1 and postgres_at != -1
    assert build_at < postgres_at


def test_compose_mounts_workspace_not_app_lbw() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${LBW_WORKSPACE:-./workspaces}:/workspaces" in compose
    assert "./.lbw:/workspaces/.lbw" not in compose
    assert ".lbw:/workspaces/.lbw" not in compose
    assert '"${LBW_UI_PORT:-8080}:8080"' in compose
    assert '"ui"' in compose
    assert "0.0.0.0" in compose


def test_readme_documents_lbw_clone_not_docker_clone() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docker clone" not in readme
    assert "lbw clone" in readme
    assert "clone first" in readme.lower()
    assert "init /workspaces" in readme
    assert "docker compose run --rm agent clone" in readme
