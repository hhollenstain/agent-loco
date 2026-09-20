from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from agent_loco.tools.files import SKIP_DIR_NAMES


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    root: Path
    test_command: str | None
    setup_command: str | None
    publish_enabled: bool
    publish_remote: str
    publish_branch: str | None
    create_pr: bool
    goals_file: str
    max_repair_attempts: int


def load_project(root: Path) -> ProjectConfig:
    root = root.resolve()
    raw: dict = {}
    config_path = root / ".loco" / "config.yaml"
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"invalid project config: {config_path}")
        raw = loaded

    publish = raw.get("publish") or {}
    if not isinstance(publish, dict):
        publish = {}

    configured = raw.get("test_command")
    test_command = str(configured) if configured else infer_test_command(root)
    return ProjectConfig(
        name=str(raw.get("name") or root.name),
        root=root,
        test_command=test_command,
        setup_command=raw.get("setup_command"),
        publish_enabled=bool(publish.get("enabled", False)),
        publish_remote=str(publish.get("remote") or "origin"),
        publish_branch=publish.get("branch"),
        create_pr=bool(publish.get("create_pr", False)),
        goals_file=str(raw.get("goals_file") or "goals.md"),
        max_repair_attempts=int(raw.get("max_repair_attempts") or 1),
    )


def infer_test_command(root: Path) -> str | None:
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        return "pytest -q"
    package_json = root / "package.json"
    if package_json.exists() and '"test"' in package_json.read_text(encoding="utf-8"):
        return "npm test"
    makefile = root / "Makefile"
    if makefile.exists() and _makefile_has_target(makefile, "test"):
        return "make test"
    if (root / "cargo.toml").exists() or (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    return None


def load_goals(root: Path, goals_file: str) -> list[str]:
    path = root / ".loco" / goals_file
    if not path.exists():
        path = root / goals_file
    if not path.exists():
        return []

    goals: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            goal = stripped[5:].strip()
            if goal:
                goals.append(goal)
    return goals


def mark_goal_done(root: Path, goals_file: str, goal: str) -> bool:
    for candidate in (root / ".loco" / goals_file, root / goals_file):
        if not candidate.exists():
            continue
        lines = candidate.read_text(encoding="utf-8").splitlines()
        changed = False
        updated: list[str] = []
        for line in lines:
            if line.strip() == f"- [ ] {goal}":
                updated.append(line.replace("- [ ]", "- [x]", 1))
                changed = True
            else:
                updated.append(line)
        if changed:
            candidate.write_text("\n".join(updated) + "\n", encoding="utf-8")
            return True
    return False


def write_default_project_files(root: Path) -> list[Path]:
    loco = root / ".loco"
    loco.mkdir(exist_ok=True)
    created: list[Path] = []

    config_path = loco / "config.yaml"
    if not config_path.exists():
        test_command = infer_test_command(root) or "pytest -q"
        config_path.write_text(
            (
                f"name: {root.name}\n"
                f"test_command: {test_command}\n"
                "max_repair_attempts: 1\n"
                "publish:\n"
                "  enabled: false\n"
                "  remote: origin\n"
                "  create_pr: false\n"
                "goals_file: goals.md\n"
            ),
            encoding="utf-8",
        )
        created.append(config_path)

    goals_path = loco / "goals.md"
    if not goals_path.exists():
        goals_path.write_text(
            "# Goals\n\n- [ ] Add a regression test for the next change\n",
            encoding="utf-8",
        )
        created.append(goals_path)
    gitignore = ensure_run_gitignore(root)
    if gitignore is not None:
        created.append(gitignore)
    return created


def ensure_run_gitignore(root: Path) -> Path | None:
    """Keep local loco runtime files out of git. Returns the path only when created."""
    markers = ("runs/", "servers.json", "workspaces.json")
    loco = root / ".loco"
    loco.mkdir(parents=True, exist_ok=True)
    path = loco / ".gitignore"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        lines = {line.strip() for line in existing.splitlines()}
        missing = [marker for marker in markers if marker not in lines]
        if not missing:
            return None
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        path.write_text(f"{existing}{suffix}" + "".join(f"{m}\n" for m in missing), encoding="utf-8")
        return None
    path.write_text("".join(f"{marker}\n" for marker in markers), encoding="utf-8")
    return path


def collect_context(
    root: Path,
    project: ProjectConfig,
    *,
    allow_publish: bool | None = None,
) -> str:
    publish_on = project.publish_enabled if allow_publish is None else allow_publish
    parts = [
        f"Project: {project.name}",
        f"Root: {root}",
        f"Test command: {project.test_command or '(none)'}",
        f"Create PR: {'on' if publish_on else 'off'} via {project.publish_remote} (never pushes to main)",
    ]
    goals = load_goals(root, project.goals_file)
    if goals:
        parts.append("Open goals:")
        parts.extend(f"- {goal}" for goal in goals[:8])
    tree = render_tree(root)
    if tree:
        parts.append("Workspace files:")
        parts.append(tree)
    return "\n".join(parts)


def render_tree(root: Path, *, max_entries: int = 80, max_depth: int = 3) -> str:
    lines: list[str] = []

    def walk(directory: Path, prefix: str, depth: int) -> None:
        if len(lines) >= max_entries or depth > max_depth:
            return
        children = sorted(
            (child for child in directory.iterdir() if child.name not in SKIP_DIR_NAMES),
            key=lambda path: (not path.is_dir(), path.name.lower()),
        )
        for child in children:
            if len(lines) >= max_entries:
                lines.append(f"{prefix}...")
                return
            lines.append(f"{prefix}{child.name}{'/' if child.is_dir() else ''}")
            if child.is_dir():
                walk(child, prefix + "  ", depth + 1)

    walk(root, "", 1)
    return "\n".join(lines)


def _makefile_has_target(path: Path, target: str) -> bool:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{target}:") or line.startswith(f"{target} :"):
            return True
    return False
