from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest

from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools, execute_tool
from agent_loco.tools.web import (
    check_public_url,
    fetch_url,
    html_to_text,
    parse_search_html,
    web_search,
    web_tools,
)

DDG_HTML = """
<html><body>
  <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html">
    asyncio — Asynchronous I/O
  </a>
  <a class="result__snippet">High-level concurrency APIs.</a>
  <a class="result__a" href="https://docs.python.org/3/library/asyncio-queue.html">asyncio.Queue</a>
  <div class="result__snippet">A first-in, first-out queue.</div>
</body></html>
"""

DOCS_HTML = """
<!doctype html>
<html>
  <head><title>asyncio — Python 3 docs</title></head>
  <body>
    <nav>Skip this</nav>
    <main>
      <h1>asyncio</h1>
      <p>This module provides infrastructure for writing concurrent code.</p>
      <h2>Queues</h2>
      <ul><li>asyncio.Queue</li></ul>
    </main>
    <footer>Also skip</footer>
  </body>
</html>
"""


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        content: str | bytes,
        content_type: str = "text/html; charset=utf-8",
        status: int = 200,
        location: str = "",
        payload: object | None = None,
    ) -> None:
        self.url = url
        if isinstance(content, bytes):
            self.content = content
        else:
            self.content = content.encode("utf-8")
        self.encoding = "utf-8"
        self.status_code = status
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location
        self._payload = payload

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")


def _public_addrinfo(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0))]


def _tool(workspace: Workspace, name: str):
    return next(tool for tool in web_tools(workspace) if tool.name == name)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_loco.tools.web.socket.getaddrinfo", _public_addrinfo)


def test_web_tools_are_in_build_tools(tmp_path: Path) -> None:
    tools = build_tools(
        Workspace(tmp_path),
        test_command=None,
        command_timeout_seconds=5,
        git_author_name=None,
        git_author_email=None,
    )
    names = {tool.name for tool in tools}
    assert "web_search" in names
    assert "fetch_url" in names


def test_parse_search_html_unwraps_ddg_redirects() -> None:
    results = parse_search_html(DDG_HTML)
    assert results[0]["url"] == "https://docs.python.org/3/library/asyncio.html"
    assert "asyncio" in results[0]["title"]
    assert "concurrency" in results[0]["snippet"]
    assert results[1]["url"] == "https://docs.python.org/3/library/asyncio-queue.html"
    assert "queue" in results[1]["snippet"].lower()


def test_html_to_text_prefers_main_and_drops_chrome() -> None:
    text = html_to_text(DOCS_HTML)
    assert "asyncio — Python 3 docs" in text
    assert "# asyncio" in text
    assert "## Queues" in text
    assert "concurrent code" in text
    assert "- asyncio.Queue" in text
    assert "Skip this" not in text
    assert "Also skip" not in text


def test_web_search_merges_instant_answer_and_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url, *args, **kwargs):
        assert "api.duckduckgo.com" in str(url)
        return FakeResponse(
            url=str(url),
            content="{}",
            content_type="application/json",
            payload={
                "Heading": "asyncio",
                "AbstractURL": "https://docs.python.org/3/library/asyncio.html",
                "AbstractText": "Asynchronous I/O.",
                "AbstractSource": "Python",
            },
        )

    def fake_post(url, *args, **kwargs):
        assert "html.duckduckgo.com" in str(url)
        return FakeResponse(url=str(url), content=DDG_HTML)

    monkeypatch.setattr("agent_loco.tools.web.httpx.get", fake_get)
    monkeypatch.setattr("agent_loco.tools.web.httpx.post", fake_post)
    result = _tool(Workspace(tmp_path), "web_search").handler(
        query="python asyncio official docs",
        max_results=5,
    )
    assert result.ok
    assert "docs.python.org/3/library/asyncio.html" in result.output
    assert result.output.count("docs.python.org/3/library/asyncio.html") == 1
    assert "asyncio.Queue" in result.output
    assert "Asynchronous I/O." in result.output


def test_web_search_rejects_empty_query(tmp_path: Path) -> None:
    result = _tool(Workspace(tmp_path), "web_search").handler(query="  ")
    assert not result.ok
    assert "query is required" in result.output


def test_web_search_reports_empty_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_loco.tools.web.httpx.get",
        lambda *args, **kwargs: FakeResponse(
            url="https://api.duckduckgo.com/",
            content="{}",
            content_type="application/json",
            payload={},
        ),
    )
    monkeypatch.setattr(
        "agent_loco.tools.web.httpx.post",
        lambda *args, **kwargs: FakeResponse(
            url="https://html.duckduckgo.com/html/",
            content="<html><body>no results</body></html>",
        ),
    )
    result = web_search("zzzxnotareallibrary")
    assert not result.ok
    assert "No web results" in result.output


def test_fetch_url_extracts_docs_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_loco.tools.web.httpx.get",
        lambda url, *args, **kwargs: FakeResponse(url=url, content=DOCS_HTML),
    )
    result = execute_tool(
        web_tools(Workspace(tmp_path)),
        "fetch_url",
        {"url": "https://docs.python.org/3/library/asyncio.html"},
    )
    assert result.ok
    assert result.output.startswith("URL: https://docs.python.org/3/library/asyncio.html")
    assert "# asyncio" in result.output
    assert "concurrent code" in result.output
    assert "Skip this" not in result.output


def test_fetch_url_pretty_prints_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_loco.tools.web.httpx.get",
        lambda url, *args, **kwargs: FakeResponse(
            url=url,
            content='{"ok":true}',
            content_type="application/json",
        ),
    )
    result = _tool(Workspace(tmp_path), "fetch_url").handler(
        url="https://example.com/api.json",
    )
    assert result.ok
    assert '"ok": true' in result.output


def test_fetch_url_rewrites_github_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_get(url, *args, **kwargs):
        seen.append(url)
        return FakeResponse(
            url=url,
            content="# Usage\nCall web_search.\n",
            content_type="text/plain",
        )

    monkeypatch.setattr("agent_loco.tools.web.httpx.get", fake_get)
    result = fetch_url("https://github.com/acme/demo/blob/main/README.md")
    assert result.ok
    assert seen == ["https://raw.githubusercontent.com/acme/demo/main/README.md"]
    assert "Call web_search." in result.output


def test_fetch_url_follows_public_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        if url.endswith("/old"):
            return FakeResponse(
                url=url,
                content="",
                status=302,
                location="https://docs.example.com/new",
            )
        return FakeResponse(url=url, content=DOCS_HTML)

    monkeypatch.setattr("agent_loco.tools.web.httpx.get", fake_get)
    result = _tool(Workspace(tmp_path), "fetch_url").handler(
        url="https://docs.example.com/old",
    )
    assert result.ok
    assert calls == ["https://docs.example.com/old", "https://docs.example.com/new"]
    assert "URL: https://docs.example.com/new" in result.output


def test_fetch_url_rejects_private_and_local_targets() -> None:
    assert check_public_url("file:///etc/passwd") == "only http and https URLs are allowed"
    assert check_public_url("http://localhost/docs") == "refusing local or private URL"
    assert check_public_url("http://127.0.0.1/docs") == "refusing local or private URL"
    assert check_public_url("http://10.0.0.8/docs") == "refusing local or private URL"
    assert check_public_url("http://192.168.1.9/docs") == "refusing local or private URL"
    assert check_public_url("http://169.254.169.254/latest") == "refusing local or private URL"
    result = fetch_url("http://127.0.0.1:8080/secret")
    assert not result.ok
    assert "private" in result.output


def test_fetch_url_rejects_resolved_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def lan_addrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.20", 0))]

    monkeypatch.setattr("agent_loco.tools.web.socket.getaddrinfo", lan_addrinfo)
    result = fetch_url("https://docs.internal.example/guide")
    assert not result.ok
    assert "private" in result.output


def test_fetch_url_refuses_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_loco.tools.web.httpx.get",
        lambda url, *args, **kwargs: FakeResponse(
            url=url,
            content=b"%PDF-1.4",
            content_type="application/pdf",
        ),
    )
    result = fetch_url("https://example.com/spec.pdf")
    assert not result.ok
    assert "non-text" in result.output
