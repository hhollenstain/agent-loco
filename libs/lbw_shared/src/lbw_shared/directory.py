"""The directory both services share: one install root and one database."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request


@dataclass(frozen=True)
class Directory:
    root: Path
    database_url: str


def bind_directory(app: Any, root: Path, database_url: str = "") -> None:
    app.state.directory = Directory(Path(root), database_url)


def request_directory(request: Request) -> Directory:
    found = getattr(request.app.state, "directory", None)
    if isinstance(found, Directory):
        return found
    ui = getattr(request.app.state, "ui", None)
    if ui is None:
        raise RuntimeError("this service has no directory")
    return Directory(Path(ui.store_root), ui.manager.settings.database_url)
