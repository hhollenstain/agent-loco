from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from agent_loco.runtime.project import (
    ensure_run_gitignore,
    guidelines_are_custom,
    load_guidelines,
    write_default_project_files,
)
from agent_loco.tools.files import SKIP_DIR_NAMES

MAX_WORKSPACES = 20
MAX_BROWSE_ENTRIES = 200
WORKSPACES_FILENAME = "workspaces.json"
CLONE_TIMEOUT_SECONDS = 180

_SKIP_BROWSE = SKIP_DIR_NAMES | {".DS_Store"}


def workspaces_path(root: Path) -> Path:
    return Path(root) / ".loco" / WORKSPACES_FILENAME


def _read_payload(root: Path) -> dict:
    path = workspaces_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {"workspaces": payload}


def _normalize_dir(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"workspace is not a directory: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"workspace is not a directory: {resolved}")
    return resolved


def _entry(path: Path) -> dict[str, str | bool]:
    return {
        "path": str(path),
        "name": path.name or str(path),
        "is_git": (path / ".git").exists(),
        "is_loco": (path / ".loco" / "config.yaml").exists(),
        "guidelines": load_guidelines(path),
        "custom_guidelines": guidelines_are_custom(path),
    }


def load_workspaces(root: Path, *, default: str | None = None) -> list[dict[str, str | bool]]:
    payload = _read_payload(root)
    items = payload.get("workspaces")
    if not isinstance(items, list):
        items = []
    result: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("path") if isinstance(item, dict) else item
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if not path.is_dir() or str(path) in seen:
            continue
        seen.add(str(path))
        result.append(_entry(path))
    if default:
        try:
            fallback = _normalize_dir(default)
        except ValueError:
            fallback = None
        if fallback and str(fallback) not in seen:
            result.append(_entry(fallback))
    return result[:MAX_WORKSPACES]


def last_workspace(root: Path, *, default: str | Path | None = None) -> str:
    payload = _read_payload(root)
    last = payload.get("last")
    if isinstance(last, str) and last.strip():
        try:
            return str(_normalize_dir(last))
        except ValueError:
            pass
    if default is not None:
        try:
            return str(_normalize_dir(default))
        except ValueError:
            pass
    return str(Path(root).expanduser().resolve())


def remember_workspace(store_root: Path, path: str | Path) -> list[dict[str, str | bool]]:
    resolved = _normalize_dir(path)
    existing = load_workspaces(store_root)
    if any(str(item["path"]) == str(resolved) for item in existing):
        saved = existing
    else:
        saved = [*existing, _entry(resolved)][-MAX_WORKSPACES:]
    payload = {
        "workspaces": [{"path": item["path"]} for item in saved],
        "last": str(resolved),
    }
    dest = workspaces_path(store_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ensure_run_gitignore(store_root)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return saved


def browse_directory(path: str | Path | None = None) -> dict[str, object]:
    target = Path(path).expanduser() if path else Path.home()
    target = target.resolve()
    if not target.exists() or not target.is_dir():
        raise ValueError(f"not a directory: {target}")
    entries: list[dict[str, str | bool]] = []
    try:
        children = sorted(target.iterdir(), key=lambda child: child.name.lower())
    except OSError as exc:
        raise ValueError(f"cannot list {target}: {exc}") from exc
    for child in children:
        if len(entries) >= MAX_BROWSE_ENTRIES:
            break
        if not child.is_dir():
            continue
        if child.name in _SKIP_BROWSE or child.name.startswith("."):
            continue
        entries.append(_entry(child))
    parent = target.parent
    return {
        "path": str(target),
        "name": target.name or str(target),
        "parent": str(parent) if parent != target else None,
        "home": str(Path.home()),
        "is_git": (target / ".git").exists(),
        "is_loco": (target / ".loco" / "config.yaml").exists(),
        "entries": entries,
    }


def repo_name_from_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if ":" in cleaned and "://" not in cleaned:
        cleaned = cleaned.rsplit(":", 1)[-1]
    name = cleaned.rsplit("/", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return slug or "workspace"


def create_workspace(path: str | Path) -> dict[str, str | bool]:
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise ValueError(f"workspace is not a directory: {target}")
    write_default_project_files(target)
    if not (target / ".git").exists():
        init = subprocess.run(
            ["git", "init"],
            cwd=target,
            check=False,
            capture_output=True,
            text=True,
        )
        if init.returncode != 0:
            raise ValueError((init.stderr or init.stdout or "git init failed").strip())
    return _entry(target)


def clone_workspace(
    url: str,
    parent: str | Path,
    *,
    name: str | None = None,
) -> dict[str, str | bool]:
    repo = url.strip()
    if not repo:
        raise ValueError("repository URL is required")
    dest_parent = Path(parent).expanduser().resolve()
    dest_parent.mkdir(parents=True, exist_ok=True)
    folder = (name or "").strip() or repo_name_from_url(repo)
    dest = dest_parent / folder
    if dest.exists():
        raise ValueError(f"already exists: {dest}")
    try:
        cloned = subprocess.run(
            ["git", "clone", repo, str(dest)],
            check=False,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("git clone timed out") from exc
    if cloned.returncode != 0:
        raise ValueError((cloned.stderr or cloned.stdout or "git clone failed").strip())
    write_default_project_files(dest)
    return _entry(dest)
