from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_RES_PATH = re.compile(r"res://([\w./-]+\.\w+)")
_PLAIN_PATH = re.compile(
    r"(?<![\w/.:-])((?:[\w-]+/)*[\w-]+\.(?:py|gd|tscn|tres|js|jsx|ts|tsx|html|css|vue|svelte|"
    r"go|rs|rb|java|kt|cs|cpp|c|h|json|yaml|yml|toml))\b"
)
_ROOT_NODE = re.compile(r"/root/([A-Za-z_]\w*)")
_SCENE_ROOT = re.compile(r'^\[node name="([^"]+)"(?![^\]]*\bparent=)', re.MULTILINE)
_SKIP_DIRS = {".git", ".lbw", ".loco", ".godot", "node_modules", ".venv", "__pycache__"}
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
    """Files the goal names, plus Godot scenes that own a named script or root node.

    A package name such as discord.py is not a source file when that path is
    not in the tree.
    """
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
    """The text of the files a goal names, for the brief and the plan.

    An update of Python or packages also includes the manifests, so a package
    name in the sentence is not planned as a missing source file.
    """
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
    sections: list[str] = []
    if parts:
        sections.append("Files named by the goal:\n" + "\n\n".join(parts))
    if _is_upgrade_goal(goal, root):
        manifests = format_dependency_manifests(root)
        if manifests:
            sections.append(manifests)
    return "\n\n".join(sections)


_UPGRADE_TOPIC = re.compile(
    r"(?i)\b(pythons?|phythons?|packages?|dependenc(?:y|ies)|requirements|librar(?:y|ies))\b"
)
_UPGRADE_VERB = re.compile(r"(?i)\b(update|upgrade|bump|outdated|out of date|newer|latest)\b")
_PYTHON_SPEC = re.compile(
    r"(?i)(?:requires-python|python_requires)\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))"
)
_NAMED_FLOOR = re.compile(
    r"(?i)(?<![.\w-])([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[^\]]*\])?\s*(>=|==|~=|\^)\s*(\d+\.\d+(?:\.\d+)?)"
)
_LOWER_BOUND = re.compile(r"(?:>=|==|~=|\^)\s*(\d+\.\d+(?:\.\d+)?)")
_UV_PACKAGE = re.compile(r'(?m)^\[\[package\]\]\s*\nname = "([^"]+)"\s*\nversion = "([^"]+)"')
_UV_SPECIFIER = re.compile(r'\{\s*name = "([^"]+)"\s*,\s*specifier = "([^"]*)"')
_UV_PYTHON = re.compile(r'(?m)^requires-python\s*=\s*"[^"]+"')
_PYTHON_NAMES = {"python", "requires-python", "python-requires"}
_MANIFEST_NAMES = ("pyproject.toml", "setup.py", "setup.cfg")


@dataclass
class VersionFloors:
    """Lowest Python and package versions the manifests already require."""

    python: tuple[int, ...] | None = None
    packages: dict[str, tuple[int, ...]] = field(default_factory=dict)


def format_dependency_manifests(root: Path) -> str:
    """Manifest text for a plan, with lockfiles reduced to package lines."""
    root = Path(root).resolve()
    blocks: list[str] = []
    for path in _dependency_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = _uv_lock_packages(text) if path.name == "uv.lock" else text.strip()
        if not body:
            continue
        if len(body) > MAX_RELATED_CHARS:
            body = body[:MAX_RELATED_CHARS] + "\n... (truncated)"
        rel = path.relative_to(root).as_posix()
        blocks.append(f"--- {rel} ---\n{body.rstrip()}")
        if len(blocks) >= 8:
            break
    if not blocks:
        return ""
    return "Dependency manifests:\n" + "\n\n".join(blocks)


def read_version_floors(root: Path) -> VersionFloors:
    """Python and package floors already required by the workspace manifests."""
    floors = VersionFloors()
    root = Path(root).resolve()
    for path in _dependency_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.name == "pyproject.toml":
            _floors_from_pyproject(text, floors)
        elif path.name == "uv.lock":
            _floors_from_uv_lock(text, floors)
        else:
            _floors_from_manifest_text(text, floors)
    return floors


def _is_upgrade_goal(goal: str | None, root: Path) -> bool:
    text = goal or ""
    if not _UPGRADE_VERB.search(text):
        return False
    if _UPGRADE_TOPIC.search(text):
        return True
    for match in _PLAIN_PATH.finditer(text):
        rel = match.group(1)
        if rel.endswith(".py") and not (root / rel).is_file():
            return True
    return False


def _dependency_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory in _manifest_dirs(root):
        for name in _MANIFEST_NAMES:
            path = directory / name
            if path.is_file():
                found.append(path)
        try:
            requirements = sorted(directory.glob("requirements*.txt"))
        except OSError:
            requirements = []
        found.extend(path for path in requirements if path.is_file())
        lock = directory / "uv.lock"
        if lock.is_file():
            found.append(lock)
    return found


def _manifest_dirs(root: Path) -> list[Path]:
    dirs = [root]
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return dirs
    for child in children:
        if not child.is_dir() or child.name in _SKIP_DIRS or child.name.startswith("."):
            continue
        dirs.append(child)
        if len(dirs) >= 12:
            break
    return dirs


def _uv_lock_packages(text: str) -> str:
    lines: list[str] = []
    python = _UV_PYTHON.search(text)
    if python:
        lines.append(python.group(0).strip())
    locked: dict[str, str] = {}
    for name, version in _UV_PACKAGE.findall(text):
        locked.setdefault(name, version)
    seen: set[tuple[str, str]] = set()
    for name, spec in _UV_SPECIFIER.findall(text):
        key = (name, spec)
        if key in seen:
            continue
        seen.add(key)
        version = locked.get(name)
        if spec and version:
            lines.append(f"- {name}: {spec} (locked {version})")
        elif spec:
            lines.append(f"- {name}: {spec}")
        elif version:
            lines.append(f"- {name}: locked {version}")
        if len(lines) >= 80:
            lines.append("...")
            break
    return "\n".join(lines)


def _floors_from_pyproject(text: str, floors: VersionFloors) -> None:
    _floors_from_manifest_text(text, floors)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    poetry = data.get("tool", {})
    if not isinstance(poetry, dict):
        return
    poetry = poetry.get("poetry", {})
    if not isinstance(poetry, dict):
        return
    dependencies = poetry.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return
    for name, spec in dependencies.items():
        raw = spec.get("version") if isinstance(spec, dict) else spec
        if not isinstance(raw, str):
            continue
        if str(name) == "python":
            _note_python(floors, raw)
        else:
            _note_package(floors, str(name), raw)


def _floors_from_uv_lock(text: str, floors: VersionFloors) -> None:
    python = _UV_PYTHON.search(text)
    if python:
        _note_python(floors, python.group(0))
    for name, spec in _UV_SPECIFIER.findall(text):
        _note_package(floors, name, spec)


def _floors_from_manifest_text(text: str, floors: VersionFloors) -> None:
    for match in _PYTHON_SPEC.finditer(text):
        _note_python(floors, next(group for group in match.groups() if group))
    for name, _operator, version in _NAMED_FLOOR.findall(text):
        _note_package(floors, name, f">={version}")


def _note_python(floors: VersionFloors, spec: str) -> None:
    for bound in _bounds(spec):
        if floors.python is None or bound > floors.python:
            floors.python = bound


def _note_package(floors: VersionFloors, name: str, spec: str) -> None:
    key = _dist_name(name)
    if key in _PYTHON_NAMES:
        _note_python(floors, spec)
        return
    for bound in _bounds(spec):
        current = floors.packages.get(key)
        if current is None or bound > current:
            floors.packages[key] = bound


def _bounds(spec: str) -> list[tuple[int, ...]]:
    found: list[tuple[int, ...]] = []
    for raw in _LOWER_BOUND.findall(spec or ""):
        parts = tuple(int(part) for part in raw.split("."))
        if len(parts) >= 2:
            found.append(parts)
    return found


def _dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip("-").lower()
