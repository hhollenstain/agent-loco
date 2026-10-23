from __future__ import annotations

import hashlib
from pathlib import Path

from agent_loco.tools.base import ToolResult
from agent_loco.tools.files import SKIP_DIR_NAMES
from agent_loco.tools.git import is_runtime_artifact

# Generated or binary files. A change to one of these is not a reason to re-run
# lint, tests, or review. Source in any nested project still counts.
_NON_CODE_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".mp3",
        ".mp4",
        ".wav",
        ".webm",
        ".ogg",
        ".zip",
        ".gz",
        ".tgz",
        ".tar",
        ".bz2",
        ".7z",
        ".rar",
        ".pdf",
        ".log",
        ".map",
        ".lock",
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".o",
        ".a",
        ".class",
        ".jar",
        ".wasm",
    }
)
_CODE_NAMES = frozenset(
    {
        "dockerfile",
        "containerfile",
        "makefile",
        "justfile",
        "jenkinsfile",
        "go.mod",
        "cargo.toml",
        "package.json",
        "pyproject.toml",
        "composer.json",
        "gemfile",
        "requirements.txt",
        "cmakelists.txt",
    }
)
_EXTRA_SKIP_DIRS = frozenset(
    {
        "htmlcov",
        "coverage",
        ".next",
        "target",
        "vendor",
        ".tox",
        ".eggs",
        ".cache",
        "site-packages",
        ".gradle",
    }
)
_MAX_HASH_BYTES = 512_000


def reused_check_result(tool: str, previous: ToolResult) -> ToolResult:
    """Tell the agent the check already covers the current code."""
    status = "passed" if previous.ok else "failed"
    note = (
        f"No code has changed since the last {tool}, including other projects "
        f"in this workspace. Not running again. Previous result: {status}. "
        "Do not call this again until you edit code."
    )
    if not previous.ok and previous.output:
        note = f"{note}\n{previous.output}"
    return ToolResult(previous.ok, note)


def is_code_path(path: Path) -> bool:
    """True for source, tests, templates, and project manifests. Not for caches or media."""
    name = path.name.lower()
    if name.startswith(".") and name not in _CODE_NAMES:
        return False
    if name in _CODE_NAMES:
        return True
    suffix = path.suffix.lower()
    if suffix in _NON_CODE_SUFFIXES:
        return False
    if name.endswith((".lock", "-lock.json", "-lock.yaml", "-lock.yml")):
        return False
    return bool(suffix) or name in _CODE_NAMES


def workspace_fingerprint(root: Path) -> str:
    """Code fingerprint plus project config, so a new test or preview command counts."""
    digest = hashlib.sha256()
    digest.update(code_fingerprint(root).encode("utf-8"))
    config = Path(root) / ".loco" / "config.yaml"
    digest.update(b"\0config\0")
    if config.is_file():
        try:
            digest.update(config.read_bytes())
        except OSError:
            pass
    return digest.hexdigest()


def code_fingerprint(root: Path) -> str:
    """Content hash of code files under root, including nested projects."""
    digest = hashlib.sha256()
    base = Path(root)
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if _skipped(relative):
            continue
        if is_runtime_artifact(relative):
            continue
        if not is_code_path(path):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_HASH_BYTES:
            digest.update(str(size).encode("utf-8"))
            digest.update(b"\0")
            continue
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return digest.hexdigest()


def _skipped(relative: Path) -> bool:
    return any(part in SKIP_DIR_NAMES or part in _EXTRA_SKIP_DIRS for part in relative.parts)
