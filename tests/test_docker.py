from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _recipe(name: str) -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    marker = f"{name}:"
    start = makefile.find(marker)
    assert start != -1
    lines = makefile[start + len(marker) :].splitlines()
    kept: list[str] = []
    for index, line in enumerate(lines):
        if index and line and not line.startswith(("\t", " ", "#")):
            break
        kept.append(line)
    return "\n".join(kept)


def test_up_local_builds_the_agent_image_before_postgres() -> None:
    recipe = _recipe("up-local")
    build_at = recipe.find("build agent crossing")
    postgres_at = recipe.find("up -d --wait postgres")
    assert "docker compose -f docker-compose.yml -f docker-compose.postgres.yml" in (
        ROOT / "Makefile"
    ).read_text(encoding="utf-8")
    assert build_at != -1 and postgres_at != -1
    assert build_at < postgres_at


def test_up_local_starts_kiwifs_and_the_agent() -> None:
    recipe = _recipe("up-local")
    assert "up -d kiwifs" in recipe
    assert "up -d --no-deps agent crossing ingress" in recipe
    assert "stop postgres kiwifs" not in recipe
    assert "nohup" not in recipe
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: ameliaanhlam/kiwifs:latest" in compose
    assert "lbw-kiwifs:/data" in compose
    assert "http://kiwifs:3333" in compose
    mac = (ROOT / "docker-compose.mac.yml").read_text(encoding="utf-8")
    assert "http://kiwifs:3333" in mac


def test_wipe_removes_compose_volumes() -> None:
    recipe = _recipe("wipe")
    assert "$(COMPOSE) down --volumes --remove-orphans" in recipe
    assert "docker-compose.nvidia.yml down --volumes --remove-orphans" in recipe
    assert "workspaces" not in recipe


def test_restart_local_rebuilds_both_services() -> None:
    recipe = _recipe("restart-local")
    assert "up -d --build --no-deps --force-recreate agent crossing" in recipe
    assert "up -d --no-deps ingress" in recipe
    assert "postgres" not in recipe
    assert "kiwifs" not in recipe
    agent = _recipe("restart-agent")
    crossing = _recipe("restart-crossing")
    assert "force-recreate agent" in agent
    assert "crossing" not in agent
    assert "force-recreate crossing" in crossing
    assert "up -d --no-deps ingress" in agent
    assert "up -d --no-deps ingress" in crossing
    assert "postgres" not in agent
    assert "postgres" not in crossing


def test_agent_image_installs_the_github_cli() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "git gh ca-certificates" in dockerfile


def test_compose_mounts_workspace_not_app_lbw() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${LBW_WORKSPACE:-./workspaces}:/workspaces" in compose
    assert "./.lbw:/workspaces/.lbw" not in compose
    assert ".lbw:/workspaces/.lbw" not in compose
    assert "lbw-crossing:local" in compose
    assert "deploy/nginx.conf" in compose
    assert "nginx:1.27-alpine" in compose
    assert '"443:443"' in compose
    assert '"ui"' in compose
    assert "0.0.0.0" in compose


def test_certs_uses_certutil_and_reloads_ingress() -> None:
    recipe = _recipe("certs")
    assert "scripts/local-certs.sh" in recipe
    script = (ROOT / "scripts/local-certs.sh").read_text(encoding="utf-8")
    assert "certutil" in script
    assert "brew install nss" in script
    assert "--force-recreate" in script
    assert "ingress" in script
    nginx = (ROOT / "deploy/nginx.conf").read_text(encoding="utf-8")
    assert "server_name www-local.lookbothways.dev" in nginx
    assert "server_name crossing-local.lookbothways.dev" in nginx
    assert "set $lbw_agent http://agent:8080" in nginx
    assert "set $lbw_crossing http://crossing:8080" in nginx


def test_readme_documents_lbw_clone_not_docker_clone() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docker clone" not in readme
    assert "lbw clone" in readme
    assert "clone first" in readme.lower()
    assert "init /workspaces" in readme
    assert "docker compose run --rm agent clone" in readme
