from __future__ import annotations

import re
from pathlib import Path

_RES_PATH = re.compile(r"res://([\w./-]+\.\w+)")
_PLAIN_PATH = re.compile(
    r"(?<![\w/.:-])((?:[\w-]+/)*[\w-]+\.(?:py|gd|tscn|tres|js|jsx|ts|tsx|html|css|vue|svelte|"
    r"go|rs|rb|java|kt|cs|cpp|c|h|json|yaml|yml|toml))\b"
)
_ROOT_NODE = re.compile(r"/root/([A-Za-z_]\w*)")
_SCENE_ROOT = re.compile(r'^\[node name="([^"]+)"(?![^\]]*\bparent=)', re.MULTILINE)
_SKIP_DIRS = {".git", ".loco", ".godot", "node_modules", ".venv", "__pycache__"}
MAX_RELATED_FILES = 4
MAX_RELATED_CHARS = 6000


def _scene_files(root: Path) -> list[Path]:
    scenes: list[Path] = []
    for path in sorted(root.rglob("*.tscn")):
        if _SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        scenes.append(path)
        if len(scenes) >= 200:
            break
    return scenes


def _scene_root_name(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _SCENE_ROOT.search(text)
    return match.group(1) if match else None


def goal_files(root: Path, goal: str | None) -> list[Path]:
    """Files the goal names, plus Godot scenes that own a named script or root node."""
    root = Path(root).resolve()
    text = goal or ""
    found: list[Path] = []

    def add(path: Path) -> None:
        if path.is_file() and path not in found and len(found) < MAX_RELATED_FILES:
            found.append(path)

    named = [*_RES_PATH.findall(text), *_PLAIN_PATH.findall(text)]
    for rel in named:
        candidate = (root / rel.lstrip("/")).resolve()
        if root in candidate.parents:
            add(candidate)
    roots = set(_ROOT_NODE.findall(text))
    scripts = [path for path in found if path.suffix == ".gd"]
    if roots or scripts:
        for scene in _scene_files(root):
            if _scene_root_name(scene) in roots:
                add(scene)
                continue
            try:
                body = scene.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for script in scripts:
                if f'path="res://{script.relative_to(root).as_posix()}"' in body:
                    add(scene)
                    break
    return found


def goal_scene(root: Path, goal: str | None) -> str | None:
    """The `res://` scene a goal is about, or None for the project's main scene."""
    root = Path(root).resolve()
    for path in goal_files(root, goal):
        if path.suffix == ".tscn":
            return f"res://{path.relative_to(root).as_posix()}"
    return None


_PLAY_GOAL = re.compile(
    r"\b(player|pipes?|gameplay|after (?:you )?start|main level|blank screen)\b",
    re.IGNORECASE,
)
_CHANGE_SCENE = re.compile(r'change_scene_to_file\(\s*"res://([^"]+\.tscn)"\s*\)')
_SCENE_SCRIPT = re.compile(r'\[ext_resource type="Script" path="res://([^"]+)"')
_SKIP_SCENE = re.compile(r"menu|settings|title|pause|game_over", re.IGNORECASE)
_PLAYER_NODE = re.compile(r'^\[node name="Player"', re.MULTILINE)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _main_scene_rel(root: Path) -> str:
    match = re.search(
        r'^(?:run/)?main_scene="res://([^"]+)"',
        _read(root / "project.godot"),
        re.MULTILINE,
    )
    return match.group(1) if match else ""


def _scene_script(root: Path, rel: str) -> str:
    match = _SCENE_SCRIPT.search(_read(root / rel))
    if not match:
        return ""
    return _read(root / match.group(1))


def gameplay_scene(root: Path, goal: str | None) -> str | None:
    """The level a Start button opens, when the goal is about play rather than the menu."""
    if not _PLAY_GOAL.search(goal or ""):
        return None
    root = Path(root).resolve()
    if not (root / "project.godot").is_file():
        return None
    main = _main_scene_rel(root)
    if main:
        for target in _CHANGE_SCENE.findall(_scene_script(root, main)):
            if not _SKIP_SCENE.search(target):
                return f"res://{target}"
    for scene in _scene_files(root):
        rel = scene.relative_to(root).as_posix()
        if _SKIP_SCENE.search(rel):
            continue
        if _PLAYER_NODE.search(_read(scene)):
            return f"res://{rel}"
    return None


def format_goal_files(root: Path, goal: str | None) -> str:
    """The text of the files a goal names, for the brief and the plan."""
    root = Path(root).resolve()
    parts: list[str] = []
    for path in goal_files(root, goal):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(body) > MAX_RELATED_CHARS:
            body = body[:MAX_RELATED_CHARS] + "\n... (truncated)"
        parts.append(f"--- {path.relative_to(root).as_posix()} ---\n{body.rstrip()}")
    if not parts:
        return ""
    return "Files named by the goal:\n" + "\n\n".join(parts)
