from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from agent_loco.runtime.project import load_project
from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools
from agent_loco.tools.kiwi import (
    KiwiError,
    KiwiFS,
    check_kiwifs,
    kiwi_tools,
    normalize_kiwifs_url,
    resolve_kiwifs_prefix,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://kiwi.test")


def _store(handler, prefix: str = "projects/demo/") -> KiwiFS:
    return KiwiFS("http://kiwi.test", prefix=prefix, client=_client(handler))


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_prefix_uses_the_folder_name_outside_git(tmp_path: Path) -> None:
    folder = tmp_path / "My Notes"
    folder.mkdir()
    assert resolve_kiwifs_prefix(folder, None) == "projects/my-notes/"
    assert resolve_kiwifs_prefix(folder, None, "notes/shared/") == "notes/shared/"
    assert resolve_kiwifs_prefix(folder, "") == ""
    assert resolve_kiwifs_prefix(folder, "kb/loco") == "kb/loco/"


def test_prefix_uses_the_git_remote_name(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    nested = checkout / "pkg"
    nested.mkdir(parents=True)
    _git(checkout, "init")
    _git(checkout, "remote", "add", "origin", "git@github.com:acme/Widget.API.git")
    assert resolve_kiwifs_prefix(checkout, None) == "projects/widget-api/"
    assert resolve_kiwifs_prefix(nested, None) == "projects/widget-api/"


def test_prefix_uses_the_git_root_folder_without_a_remote(tmp_path: Path) -> None:
    repo = tmp_path / "LocalRepo"
    repo.mkdir()
    _git(repo, "init")
    assert resolve_kiwifs_prefix(repo, None) == "projects/localrepo/"


def test_project_config_keeps_an_explicit_prefix(tmp_path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text("name: demo\nkiwifs_prefix: notes/demo\n", encoding="utf-8")
    assert load_project(tmp_path).kiwifs_prefix == "notes/demo"
    (loco / "config.yaml").write_text("name: demo\nkiwifs_prefix:\n", encoding="utf-8")
    assert load_project(tmp_path).kiwifs_prefix == ""
    (loco / "config.yaml").write_text("name: demo\n", encoding="utf-8")
    assert load_project(tmp_path).kiwifs_prefix is None


def test_brief_peeks_matches_and_stops_at_the_budget() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/kiwi/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "path": "projects/demo/decisions.md",
                            "snippet": "Use <mark>pytest</mark>.",
                        },
                        {"path": "guides/long.md", "snippet": "a long guide"},
                    ]
                },
            )
        if request.url.path == "/api/kiwi/peek":
            path = request.url.params["path"]
            seen.append(path)
            return httpx.Response(
                200,
                json={
                    "path": path,
                    "title": "Decisions" if "decisions" in path else "Guide",
                    "snippet": "x" * 500,
                    "word_count": 20,
                    "headings": ["Decisions", "Tests"],
                    "frontmatter": {"type": "reference", "status": "draft"},
                },
            )
        return httpx.Response(404, json={"error": "nope"})

    text = _store(handler).brief("pytest", budget_tokens=200)
    assert "projects/demo/decisions.md" in text
    assert "<mark>" not in text
    assert "Omitted: guides/long.md" in text
    assert seen == ["projects/demo/decisions.md", "guides/long.md"]


def test_writes_stay_under_the_project_prefix_and_reads_do_not() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.params["path"]
        captured["body"] = request.content.decode()
        if request.url.path == "/api/kiwi/file" and request.method == "GET":
            return httpx.Response(200, text="# Guide\n\nShared.", headers={"etag": '"abc"'})
        if request.url.path == "/api/kiwi/section":
            return httpx.Response(
                200,
                json={
                    "path": request.url.params["path"],
                    "heading": "Steps",
                    "content": "Do the thing.",
                },
            )
        return httpx.Response(200, headers={"etag": '"def"'})

    store = _store(handler)
    assert "projects/demo/decisions.md" in store.write("decisions.md", "Use pytest -q.")
    assert captured["path"] == "projects/demo/decisions.md"
    assert captured["body"] == "Use pytest -q."
    assert store.append("episodes/today.md", "the owner is review.py") == (
        "appended to projects/demo/episodes/today.md"
    )
    read = store.read("guides/setup.md")
    assert captured["path"] == "guides/setup.md"
    assert "Shared." in read
    section = store.read("guides/setup.md", heading="Steps")
    assert "Do the thing." in section
    assert "# Steps" in section


def test_path_escape_and_empty_notes_fail() -> None:
    store = _store(lambda request: httpx.Response(200, json={}))
    with pytest.raises(KiwiError, match="escapes"):
        store.write("../secrets.md", "nope")
    with pytest.raises(KiwiError, match="content is required"):
        store.write("decisions.md", "   ")
    tool = next(
        item for item in kiwi_tools(base_url="http://kiwi.test") if item.name == "kiwi_write"
    )
    result = tool.handler(path="../secrets.md", content="nope")
    assert result.ok is False
    assert "escapes" in result.output


def test_unreachable_server_stays_in_the_tool_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(KiwiError, match="not reachable"):
        _store(handler).write("decisions.md", "note")


def test_tools_are_registered_only_when_a_url_is_set(tmp_path) -> None:
    common = dict(
        test_command=None,
        command_timeout_seconds=5,
        git_author_name=None,
        git_author_email=None,
    )
    off = {tool.name for tool in build_tools(Workspace(tmp_path), **common)}
    assert "kiwi_brief" not in off
    on = {
        tool.name
        for tool in build_tools(
            Workspace(tmp_path),
            kiwifs_url="http://127.0.0.1:3333",
            kiwifs_prefix="projects/demo/",
            **common,
        )
    }
    assert {"kiwi_brief", "kiwi_peek", "kiwi_read", "kiwi_write", "kiwi_append"} <= on


def test_check_and_url_normalization() -> None:
    assert normalize_kiwifs_url("http://localhost:3333/api/kiwi/") == "http://localhost:3333"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/kiwi/context"
        return httpx.Response(200, json={"schema": "ok"})

    kiwi = KiwiFS("http://kiwi.test/api/kiwi", client=_client(handler))
    assert kiwi._json("GET", "/api/kiwi/context") == {"schema": "ok"}
    ok, detail = check_kiwifs("")
    assert ok is False
    assert detail == "off"
