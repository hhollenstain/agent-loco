"""KiwiFS tools. A local model keeps memory in the knowledge base, not the prompt."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from agent_loco.runtime.workspaces import repo_name_from_url
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema

DEFAULT_KIWIFS_URL = "http://127.0.0.1:3333"
DEFAULT_BUDGET_TOKENS = 800
MAX_BUDGET_TOKENS = 2000
DEFAULT_READ_CHARS = 4_000
MAX_READ_CHARS = 8_000
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 8
_CHARS_PER_TOKEN = 4
_MARK = re.compile(r"</?mark>")
_SLUG = re.compile(r"[^a-z0-9]+")


class KiwiError(Exception):
    """The KiwiFS request failed. Callers turn this into a tool result."""


def normalize_kiwifs_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    for suffix in ("/api/kiwi", "/api"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned


def project_slug(name: str) -> str:
    slug = _SLUG.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "project"


def kiwifs_namespace(root: Path) -> str:
    """Git remote repo name, or the workspace folder name when it is not a repo."""
    directory = Path(root)
    remote = _git_output(directory, ["remote", "get-url", "origin"])
    if remote:
        return project_slug(repo_name_from_url(remote))
    if _git_output(directory, ["rev-parse", "--is-inside-work-tree"]) == "true":
        toplevel = _git_output(directory, ["rev-parse", "--show-toplevel"])
        folder = Path(toplevel).name if toplevel else directory.name
        return project_slug(folder)
    return project_slug(directory.name)


def resolve_kiwifs_prefix(root: Path, configured: str | None, env_prefix: str = "") -> str:
    """Folder loco may write. An explicit empty prefix writes anywhere in the base."""
    if configured is not None:
        return _clean_prefix(configured)
    if (env_prefix or "").strip():
        return _clean_prefix(env_prefix)
    return f"projects/{kiwifs_namespace(root)}/"


def _git_output(root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _clean_prefix(value: str) -> str:
    cleaned = (value or "").strip().strip("/")
    if not cleaned:
        return ""
    rel = _relative_path(cleaned)
    return f"{rel}/"


def _relative_path(path: str) -> str:
    rel = (path or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        raise KiwiError("path is required")
    parts = [part for part in rel.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise KiwiError(f"path escapes the knowledge base: {path}")
    if not parts:
        raise KiwiError("path is required")
    return "/".join(parts)


def _clamp(value: object, default: int, high: int, low: int = 1) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def _strip_marks(text: str) -> str:
    return _MARK.sub("", text or "").strip()


class KiwiFS:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        actor: str = "loco",
        prefix: str = "",
        client: httpx.Client | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = normalize_kiwifs_url(base_url)
        self.api_key = (api_key or "").strip()
        self.actor = (actor or "loco").strip() or "loco"
        self.prefix = _clean_prefix(prefix) if (prefix or "").strip() else ""
        self._client = client
        self.timeout = timeout

    def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[dict[str, Any]]:
        cleaned = (query or "").strip()
        if not cleaned:
            raise KiwiError("query is required")
        payload = self._json(
            "GET",
            "/api/kiwi/search",
            params={"q": cleaned, "limit": _clamp(limit, DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT)},
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return []
        return [item for item in results if isinstance(item, dict)]

    def peek(self, path: str) -> dict[str, Any]:
        payload = self._json("GET", "/api/kiwi/peek", params={"path": _relative_path(path)})
        return payload if isinstance(payload, dict) else {}

    def read(self, path: str, *, heading: str = "", max_chars: int = DEFAULT_READ_CHARS) -> str:
        rel = _relative_path(path)
        limit = _clamp(max_chars, DEFAULT_READ_CHARS, MAX_READ_CHARS, low=200)
        if (heading or "").strip():
            payload = self._json(
                "GET",
                "/api/kiwi/section",
                params={"path": rel, "heading": heading.strip()},
            )
            content = ""
            found = heading.strip()
            if isinstance(payload, dict):
                content = str(payload.get("content") or "")
                found = str(payload.get("heading") or found)
            body = content.strip() or "(empty section)"
            text = f"{rel} # {found}\n\n{body}"
        else:
            response = self._send("GET", "/api/kiwi/file", params={"path": rel})
            etag = response.headers.get("etag", "").strip()
            tag = f" etag {etag}" if etag else ""
            text = f"{rel}{tag}\n\n{response.text.strip()}"
        if len(text) > limit:
            return text[:limit].rstrip() + "\n… truncated. Pass a heading to read one section."
        return text

    def write(self, path: str, content: str) -> str:
        rel = self._write_path(path)
        body = content if isinstance(content, str) else str(content or "")
        if not body.strip():
            raise KiwiError("content is required")
        response = self._send(
            "PUT",
            "/api/kiwi/file",
            params={"path": rel},
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/markdown; charset=utf-8"},
        )
        etag = response.headers.get("etag", "").strip()
        tag = f" ({etag})" if etag else ""
        return f"wrote {rel}{tag}"

    def append(self, path: str, content: str) -> str:
        rel = self._write_path(path)
        body = content if isinstance(content, str) else str(content or "")
        if not body.strip():
            raise KiwiError("content is required")
        self._send(
            "POST",
            "/api/kiwi/file/append",
            params={"path": rel, "separator": "\n"},
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/markdown; charset=utf-8"},
        )
        return f"appended to {rel}"

    def brief(self, query: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> str:
        budget = _clamp(budget_tokens, DEFAULT_BUDGET_TOKENS, MAX_BUDGET_TOKENS, low=200)
        char_budget = budget * _CHARS_PER_TOKEN
        hits = self.search(query, limit=min(MAX_SEARCH_LIMIT, max(3, budget // 250)))
        if not hits:
            return f'No KiwiFS pages match "{query.strip()}".'
        blocks: list[str] = []
        used = 0
        omitted: list[str] = []
        for hit in hits:
            path = str(hit.get("path") or "").strip()
            if not path:
                continue
            try:
                block = _peek_block(self.peek(path))
            except KiwiError:
                block = _search_block(hit)
            if blocks and used + len(block) > char_budget:
                omitted.append(path)
                continue
            blocks.append(block)
            used += len(block)
        if not blocks:
            return f'No KiwiFS pages match "{query.strip()}".'
        header = (
            f'KiwiFS brief for "{query.strip()}" '
            f"({len(blocks)} page{'s' if len(blocks) != 1 else ''}, "
            f"about {used // _CHARS_PER_TOKEN} tokens)."
        )
        lines = [header, *blocks]
        if omitted:
            lines.append("Omitted: " + ", ".join(omitted) + ". Peek one path if you still need it.")
        else:
            lines.append("Peek or read one heading if a single page is the answer.")
        return "\n".join(lines)

    def _write_path(self, path: str) -> str:
        rel = _relative_path(path)
        if not self.prefix:
            return rel
        root = self.prefix.strip("/")
        if rel == root or rel.startswith(root + "/"):
            return rel
        return f"{root}/{rel}"

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise KiwiError(f"KiwiFS returned a non-JSON body for {path}") from exc

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-Actor", self.actor)
        if self.api_key:
            token = self.api_key
            if not token.lower().startswith("bearer "):
                token = f"Bearer {token}"
            headers.setdefault("Authorization", token)
        try:
            if self._client is not None:
                response = self._client.request(method, path, headers=headers, **kwargs)
            else:
                with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                    response = client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise KiwiError(
                f"KiwiFS at {self.base_url or 'the configured URL'} is not reachable"
            ) from exc
        if response.status_code >= 400:
            detail = " ".join(response.text.split())[:240]
            raise KiwiError(
                f"KiwiFS {response.status_code} for {path}: {detail or response.reason_phrase}"
            )
        return response


def _search_block(hit: dict[str, Any]) -> str:
    path = str(hit.get("path") or "page")
    snippet = _strip_marks(str(hit.get("snippet") or ""))
    return f"- {path}\n  {snippet}" if snippet else f"- {path}"


def _peek_block(payload: dict[str, Any]) -> str:
    path = str(payload.get("path") or "page")
    front = payload.get("frontmatter") if isinstance(payload.get("frontmatter"), dict) else {}
    title = str(payload.get("title") or front.get("title") or "").strip()
    kind = str(front.get("type") or "").strip()
    status = str(front.get("status") or "").strip()
    meta = " ".join(part for part in (kind, status) if part)
    snippet = _strip_marks(str(payload.get("snippet") or ""))
    headings = payload.get("headings") if isinstance(payload.get("headings"), list) else []
    names = [
        str(item).strip() for item in headings if str(item).strip() and str(item).strip() != title
    ]
    words = payload.get("word_count")
    lines = [f"- {path}" + (f" — {title}" if title else "")]
    if meta or isinstance(words, int):
        size = f"{words} words" if isinstance(words, int) else ""
        lines.append("  " + " · ".join(part for part in (meta, size) if part))
    if snippet:
        lines.append(f"  {snippet}")
    if names:
        lines.append("  headings: " + ", ".join(names[:8]))
    if isinstance(words, int) and words > 400:
        lines.append("  long page: kiwi_read it with a heading, not the whole file")
    return "\n".join(lines)


def check_kiwifs(base_url: str, api_key: str = "", timeout: float = 3.0) -> tuple[bool, str]:
    url = normalize_kiwifs_url(base_url)
    if not url:
        return False, "off"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, f"not a URL: {base_url}"
    try:
        kiwi = KiwiFS(url, api_key=api_key, timeout=timeout)
        kiwi._json("GET", "/api/kiwi/context")
    except KiwiError as exc:
        return False, str(exc)
    return True, f"UP at {url}"


def kiwi_tools(
    *,
    base_url: str,
    api_key: str = "",
    prefix: str = "",
) -> list[ToolSpec]:
    url = normalize_kiwifs_url(base_url)
    if not url:
        return []
    fence = prefix.strip() or "the path you pass"
    store = KiwiFS(url, api_key=api_key, prefix=prefix)

    def guarded(action):
        def run(**kwargs: Any) -> ToolResult:
            try:
                return ToolResult(True, action(**kwargs))
            except KiwiError as exc:
                return ToolResult(False, str(exc))

        return run

    return [
        ToolSpec(
            name="kiwi_brief",
            description=(
                "Search the KiwiFS knowledge base and return a short brief of the top "
                "pages. Call this before reading the repo widely or opening a long page. "
                f"The default budget is {DEFAULT_BUDGET_TOKENS} tokens. Do not raise it "
                "to pull the whole base into a local model."
            ),
            parameters=object_schema(
                {
                    "query": {
                        "type": "string",
                        "description": "Keywords: file, error, library, or decision to find.",
                    },
                    "budget_tokens": {
                        "type": "integer",
                        "description": (
                            f"Token cap for the brief. Default {DEFAULT_BUDGET_TOKENS}, "
                            f"max {MAX_BUDGET_TOKENS}."
                        ),
                    },
                },
                ["query"],
            ),
            handler=guarded(
                lambda query, budget_tokens=DEFAULT_BUDGET_TOKENS: store.brief(query, budget_tokens)
            ),
        ),
        ToolSpec(
            name="kiwi_peek",
            description=(
                "Skim one KiwiFS page: title, first lines, and headings. Use this after "
                "kiwi_brief when one path looks relevant. It is much smaller than a full read."
            ),
            parameters=object_schema(
                {"path": {"type": "string", "description": "Page path from a brief or search."}},
                ["path"],
            ),
            handler=guarded(lambda path: _peek_block(store.peek(path))),
        ),
        ToolSpec(
            name="kiwi_read",
            description=(
                "Read one KiwiFS page, or one heading inside it. Prefer kiwi_peek. Pass "
                "heading when the page is long. Do not read several full pages in one turn."
            ),
            parameters=object_schema(
                {
                    "path": {"type": "string", "description": "Page path."},
                    "heading": {
                        "type": "string",
                        "description": "Heading to read. Omit only for a short page.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": f"Cap on returned text. Default {DEFAULT_READ_CHARS}.",
                    },
                },
                ["path"],
            ),
            handler=guarded(
                lambda path, heading="", max_chars=DEFAULT_READ_CHARS: store.read(
                    path, heading=heading, max_chars=max_chars
                )
            ),
        ),
        ToolSpec(
            name="kiwi_write",
            description=(
                "Save a short durable note (a decision, a test command, an approach that "
                f"failed). Stored under {fence}. A few sentences. Do not store diffs, logs, "
                "or source files."
            ),
            parameters=object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Note path, such as decisions.md or conventions.md.",
                    },
                    "content": {"type": "string", "description": "Markdown to store."},
                },
                ["path", "content"],
            ),
            handler=guarded(lambda path, content: store.write(path, content)),
        ),
        ToolSpec(
            name="kiwi_append",
            description=(
                "Append a few lines to a project episode without rereading the page. "
                f"Stored under {fence}. Use episodes/<date>.md for one run's notes."
            ),
            parameters=object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Page to append to, such as episodes/2026-10-20.md.",
                    },
                    "content": {"type": "string", "description": "Lines to add."},
                },
                ["path", "content"],
            ),
            handler=guarded(lambda path, content: store.append(path, content)),
        ),
    ]
