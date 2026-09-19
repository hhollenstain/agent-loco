from __future__ import annotations

from pathlib import Path


class SandboxError(ValueError):
    """Raised when a path or action leaves the workspace."""


class Workspace:
    """Resolves paths strictly inside a project root."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        if not self.root.exists():
            raise SandboxError(f"workspace does not exist: {root}")
        if not self.root.is_dir():
            raise SandboxError(f"workspace is not a directory: {root}")

    def resolve(self, user_path: str | Path) -> Path:
        raw = Path(user_path).expanduser()
        path = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
        if not path.is_relative_to(self.root):
            raise SandboxError(f"path escapes workspace: {user_path}")
        return path

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()
