from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support import init_git_repo

from agent_loco.runtime.workspaces import (
    browse_directory,
    clone_workspace,
    create_workspace,
    last_workspace,
    load_workspaces,
    remember_workspace,
    repo_name_from_url,
)


def test_repo_name_from_url() -> None:
    assert repo_name_from_url("https://github.com/org/demo.git") == "demo"
    assert repo_name_from_url("git@github.com:org/foo-bar.git") == "foo-bar"
    assert repo_name_from_url("   ") == "workspace"


def test_browse_lists_visible_directories(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "keep" / ".git").mkdir()
    listing = browse_directory(tmp_path)
    names = [item["name"] for item in listing["entries"]]
    assert names == ["keep"]
    assert listing["entries"][0]["is_git"] is True
    assert listing["path"] == str(tmp_path.resolve())
    assert listing["parent"] == str(tmp_path.parent.resolve())
    missing = tmp_path / "nope"
    with pytest.raises(ValueError, match="not a directory"):
        browse_directory(missing)


def test_remember_and_restore_workspaces(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    saved = remember_workspace(tmp_path, first)
    assert saved[0]["path"] == str(first.resolve())
    saved = remember_workspace(tmp_path, second)
    assert [item["path"] for item in saved[:2]] == [
        str(second.resolve()),
        str(first.resolve()),
    ]
    payload = json.loads((tmp_path / ".loco" / "workspaces.json").read_text(encoding="utf-8"))
    assert payload["last"] == str(second.resolve())
    gitignore = (tmp_path / ".loco" / ".gitignore").read_text(encoding="utf-8")
    assert "workspaces.json" in gitignore
    assert last_workspace(tmp_path) == str(second.resolve())
    gone = tmp_path / "gone"
    (tmp_path / ".loco" / "workspaces.json").write_text(
        json.dumps({"last": str(gone), "workspaces": [{"path": str(gone)}]}),
        encoding="utf-8",
    )
    assert last_workspace(tmp_path, default=first) == str(first.resolve())
    loaded = load_workspaces(tmp_path, default=first)
    assert [item["path"] for item in loaded] == [str(first.resolve())]


def test_create_workspace_inits_loco_and_git(tmp_path: Path) -> None:
    target = tmp_path / "new-app"
    created = create_workspace(target)
    assert created["path"] == str(target.resolve())
    assert created["is_loco"] is True
    assert created["is_git"] is True
    assert (target / ".loco" / "config.yaml").exists()
    assert (target / ".git").exists()


def test_clone_workspace_from_local_repo(tmp_path: Path) -> None:
    source = tmp_path / "upstream"
    source.mkdir()
    (source / "readme.txt").write_text("hello\n", encoding="utf-8")
    init_git_repo(source)
    parent = tmp_path / "projects"
    cloned = clone_workspace(str(source), parent, name="checkout")
    dest = parent / "checkout"
    assert cloned["path"] == str(dest.resolve())
    assert (dest / "readme.txt").read_text(encoding="utf-8") == "hello\n"
    assert cloned["is_loco"] is True
    with pytest.raises(ValueError, match="already exists"):
        clone_workspace(str(source), parent, name="checkout")
    with pytest.raises(ValueError, match="repository URL"):
        clone_workspace("  ", parent)
