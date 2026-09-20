from __future__ import annotations

import html
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from agent_loco.progress import record_event
from agent_loco.runtime.project import ProjectConfig
from agent_loco.sandbox import Workspace

log = logging.getLogger("loco")

UI_FILE_RE = re.compile(
    r"(?:^|[/\s])(?:[\w./-]+\.(?:html?|css|js|jsx|tsx|vue|svelte)|templates/)",
    re.IGNORECASE,
)
UI_GOAL_RE = re.compile(
    r"\b(ui|ux|css|html|layout|sidebar|progress bar|template|frontend|"
    r"web page|web-ui|web ui|button|panel|dark mode|responsive)\b",
    re.IGNORECASE,
)
MAX_SNAPSHOT_CHARS = 6_000
PAGE_EVAL = """() => {
  const nodes = [...document.querySelectorAll(
    'button, a, [role="tab"], [role="button"], h1, h2, h3, label, input,'
    + ' select, textarea, .progress-stage, .task, .task-card'
  )];
  return {
    title: document.title || "",
    url: location.href,
    text: (document.body && document.body.innerText || "").slice(0, 8000),
    errors: window.__locoErrors || [],
    elements: nodes.slice(0, 80).map((el) => {
      const box = el.getBoundingClientRect();
      const name = (
        el.getAttribute("aria-label")
        || el.innerText
        || el.getAttribute("title")
        || el.id
        || ""
      ).trim().slice(0, 80);
      return {
        tag: el.tagName.toLowerCase(),
        name,
        w: Math.round(box.width),
        h: Math.round(box.height),
        x: Math.round(box.x),
        y: Math.round(box.y),
      };
    }),
  };
}"""
INIT_SCRIPT = """
window.__locoErrors = [];
window.addEventListener("error", (event) => {
  window.__locoErrors.push(String(event.message || event.error || "error"));
});
window.addEventListener("unhandledrejection", (event) => {
  window.__locoErrors.push(String(event.reason || "unhandledrejection"));
});
"""


@dataclass
class UiEvidence:
    ok: bool
    url: str = ""
    title: str = ""
    page_errors: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    smashed: list[str] = field(default_factory=list)
    snapshot: str = ""
    screenshot: str | None = None
    notes: str = ""

    @property
    def summary(self) -> str:
        if not self.ok and self.notes:
            return f"UI review skipped: {self.notes}"
        errors = self.page_errors + self.console_errors
        bits = ["Rendered UI"]
        if self.title:
            bits.append(self.title)
        if errors:
            bits.append(f"{len(errors)} JS error(s)")
        if self.smashed:
            bits.append(f"{len(self.smashed)} unreadable control(s)")
        if not errors and not self.smashed:
            bits.append("no console errors")
        return " · ".join(bits)


def ui_review_needed(goal: str, diff: str = "", workspace: Path | None = None) -> bool:
    goal_is_ui = bool(UI_GOAL_RE.search(goal or ""))
    diff_is_ui = bool(UI_FILE_RE.search(diff or ""))
    if diff_is_ui:
        return True
    if goal_is_ui and workspace and _has_web_preview(workspace):
        return True
    return False


def format_ui_evidence(evidence: UiEvidence | None) -> str:
    if evidence is None:
        return ""
    lines = ["Rendered UI:"]
    if evidence.url:
        lines.append(f"URL: {evidence.url}")
    if evidence.title:
        lines.append(f"Title: {evidence.title}")
    if evidence.notes:
        lines.append(evidence.notes)
    errors = evidence.page_errors + evidence.console_errors
    if errors:
        lines.append("JavaScript errors:")
        lines.extend(f"- {item}" for item in errors[:12])
    if evidence.smashed:
        lines.append("Controls with no usable size (hidden, crushed, or off-screen):")
        lines.extend(f"- {item}" for item in evidence.smashed[:12])
    if evidence.snapshot:
        lines.append("Visible page:")
        lines.append(evidence.snapshot.strip())
    if evidence.screenshot:
        lines.append(f"Screenshot: {evidence.screenshot}")
    return "\n".join(lines)


def collect_ui_evidence(
    workspace: Workspace,
    project: ProjectConfig,
    goal: str,
    *,
    url: str | None = None,
    path: str | None = None,
    click: str | None = None,
    wait_ms: int = 4000,
) -> UiEvidence:
    screenshot = workspace.root / ".loco" / "ui-review.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    preview: _Preview | None = None
    target = (url or "").strip()
    try:
        if not target:
            preview = start_preview(workspace, project, path=path)
            if preview is None:
                return UiEvidence(
                    ok=False,
                    notes=(
                        "No preview server for this workspace. "
                        "Set preview_command in .loco/config.yaml."
                    ),
                )
            target = preview.url
        page = capture_page(
            target,
            click=click or _default_click(workspace.root),
            screenshot=screenshot,
            wait_ms=wait_ms,
        )
        record_event(
            kind="ui",
            ok=page.ok,
            url=page.url or target,
            message=page.summary,
            snapshot=page.snapshot,
            screenshot=page.screenshot,
            errors=page.page_errors + page.console_errors,
        )
        return page
    except Exception as exc:  # noqa: BLE001 - capture failures become evidence
        return UiEvidence(ok=False, notes=f"could not render UI: {exc}")
    finally:
        if preview is not None:
            preview.close()


def start_preview(
    workspace: Workspace,
    project: ProjectConfig,
    *,
    path: str | None = None,
    port: int | None = None,
) -> _Preview | None:
    host_port = port or _free_port()
    command = getattr(project, "preview_command", None)
    if command:
        return _start_command_preview(workspace, str(command), host_port)
    if path:
        html_path = workspace.resolve(path)
        if html_path.is_file():
            return _start_static_preview(html_path.parent, host_port, html_path.name)
    if _is_loco_project(workspace.root):
        return _start_loco_preview(workspace, host_port)
    html = _first_html_file(workspace.root)
    if html is not None:
        return _start_static_preview(html.parent, host_port, html.name)
    return None


def capture_page(
    url: str,
    *,
    click: str | None = None,
    screenshot: Path | None = None,
    wait_ms: int = 4000,
) -> UiEvidence:
    try:
        return _playwright_capture(url, click=click, screenshot=screenshot, wait_ms=wait_ms)
    except Exception as exc:
        log.info("playwright UI capture unavailable: %s", exc)
    try:
        return _chrome_capture(url, screenshot=screenshot, wait_ms=wait_ms)
    except Exception as exc:
        return UiEvidence(ok=False, url=url, notes=f"could not render UI: {exc}")


def _playwright_capture(
    url: str,
    *,
    click: str | None,
    screenshot: Path | None,
    wait_ms: int,
) -> UiEvidence:
    from playwright.sync_api import sync_playwright

    console: list[str] = []
    page_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = _launch_playwright(playwright)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.add_init_script(INIT_SCRIPT)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on(
            "console",
            lambda msg: console.append(msg.text) if msg.type == "error" else None,
        )
        page.goto(url, wait_until="domcontentloaded", timeout=max(wait_ms, 8000))
        page.wait_for_timeout(min(wait_ms, 2500))
        if click:
            _playwright_click(page, click, wait_ms)
            page.wait_for_timeout(400)
        raw = page.evaluate(PAGE_EVAL)
        shot = None
        if screenshot is not None:
            page.screenshot(path=str(screenshot), full_page=False)
            shot = str(screenshot)
        browser.close()
    return _from_eval(url, raw, page_errors=page_errors, console_errors=console, screenshot=shot)


def _launch_playwright(playwright: Any) -> Any:
    errors: list[str] = []
    for kwargs in ({"channel": "chrome"}, {"channel": "chromium"}, {}):
        try:
            return playwright.chromium.launch(headless=True, **kwargs)
        except Exception as exc:  # noqa: BLE001 - try the next browser backend
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors) or "playwright could not launch chromium")


def _playwright_click(page: Any, click: str, wait_ms: int) -> None:
    timeout = min(max(wait_ms, 500), 8000)
    locators = [page.locator(click), page.get_by_role("button", name=click)]
    for locator in locators:
        try:
            if locator.count() > 0:
                locator.first.click(timeout=timeout)
                return
        except Exception:  # noqa: BLE001 - try the next locator
            continue


def _chrome_capture(url: str, *, screenshot: Path | None, wait_ms: int) -> UiEvidence:
    chrome = _chrome_executable()
    if not chrome:
        raise RuntimeError("Chrome/Chromium not found; pip install playwright")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--disable-dev-shm-usage",
        "--window-size=1280,800",
        f"--virtual-time-budget={max(wait_ms, 1000)}",
        "--dump-dom",
        url,
    ]
    if screenshot is not None:
        cmd.insert(-1, f"--screenshot={screenshot}")
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(wait_ms / 1000 + 8, 12),
    )
    if result.returncode not in {0, None}:
        raise RuntimeError(result.stderr.strip() or f"chrome exited {result.returncode}")
    text = _visible_text(result.stdout)
    errors = _chrome_logged_errors(result.stderr)
    smashed: list[str] = []
    if not text.strip():
        smashed.append("page body was empty after render")
    return UiEvidence(
        ok=not errors,
        url=url,
        page_errors=errors,
        snapshot=text[:MAX_SNAPSHOT_CHARS],
        screenshot=str(screenshot) if screenshot and screenshot.exists() else None,
        notes="Captured with Chrome --dump-dom (console coverage is limited).",
        smashed=smashed,
    )


def _from_eval(
    url: str,
    raw: Any,
    *,
    page_errors: list[str],
    console_errors: list[str],
    screenshot: str | None,
) -> UiEvidence:
    data = raw if isinstance(raw, dict) else {}
    elements = data.get("elements") if isinstance(data.get("elements"), list) else []
    smashed = []
    for item in elements:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("tag") or "control").strip()
        width = int(item.get("w") or 0)
        height = int(item.get("h") or 0)
        if name and width < 8 and height < 8:
            smashed.append(f"{name} ({width}x{height})")
    injected = data.get("errors") if isinstance(data.get("errors"), list) else []
    errors = [str(item) for item in [*page_errors, *injected] if str(item).strip()]
    text = str(data.get("text") or "")
    snapshot = _format_snapshot(text, elements)
    return UiEvidence(
        ok=not errors and not smashed,
        url=str(data.get("url") or url),
        title=str(data.get("title") or ""),
        page_errors=errors,
        console_errors=[item for item in console_errors if item],
        smashed=smashed,
        snapshot=snapshot,
        screenshot=screenshot,
    )


def _format_snapshot(text: str, elements: list[Any]) -> str:
    lines: list[str] = []
    for item in elements:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        tag = item.get("tag") or "node"
        lines.append(f"{tag}: {name} ({item.get('w')}x{item.get('h')})")
        if len(lines) >= 40:
            break
    body = text.strip()
    if body:
        lines.extend(["", body[:4000]])
    snapshot = "\n".join(lines)
    if len(snapshot) > MAX_SNAPSHOT_CHARS:
        return snapshot[:MAX_SNAPSHOT_CHARS] + "\n... truncated"
    return snapshot


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._skip = tag in {"script", "style", "noscript"}

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def _visible_text(markup: str) -> str:
    parser = _TextParser()
    parser.feed(markup or "")
    return html.unescape("\n".join(parser.parts))


def _chrome_logged_errors(stderr: str) -> list[str]:
    errors: list[str] = []
    for line in (stderr or "").splitlines():
        if re.search(r"Uncaught|TypeError|ReferenceError|SyntaxError", line):
            errors.append(line.strip())
    return errors[:12]


def _chrome_executable() -> str | None:
    named = os.environ.get("CHROME_PATH")
    candidates = [
        named,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("microsoft-edge"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def _has_web_preview(root: Path) -> bool:
    if _is_loco_project(root):
        return True
    config = root / ".loco" / "config.yaml"
    if config.exists() and "preview_command" in config.read_text(encoding="utf-8"):
        return True
    return _first_html_file(root) is not None


def _is_loco_project(root: Path) -> bool:
    return (root / "src" / "agent_loco" / "web_ui.py").is_file() or (
        root / "src" / "agent_loco" / "templates" / "index.html"
    ).is_file()


def _first_html_file(root: Path) -> Path | None:
    for candidate in (
        root / "index.html",
        root / "src" / "agent_loco" / "templates" / "index.html",
        *sorted(root.glob("*.html")),
    ):
        if candidate.is_file():
            return candidate
    return None


def _default_click(root: Path) -> str | None:
    if _is_loco_project(root):
        return "#history-list button.task"
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class _Preview:
    url: str
    close: Callable[[], None]


def _wait_for_port(port: int, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"preview server did not start on port {port}")


def _start_command_preview(workspace: Workspace, command: str, port: int) -> _Preview:
    rendered = command.replace("{port}", str(port))
    proc = subprocess.Popen(  # noqa: S603 - project-configured preview
        ["/bin/bash", "-lc", rendered],
        cwd=workspace.root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(port)
    return _Preview(
        url=f"http://127.0.0.1:{port}/",
        close=lambda: proc.terminate(),
    )


def _start_static_preview(directory: Path, port: int, filename: str) -> _Preview:
    proc = subprocess.Popen(
        ["python3", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(port)
    return _Preview(
        url=urljoin(f"http://127.0.0.1:{port}/", filename),
        close=lambda: proc.terminate(),
    )


def _start_loco_preview(workspace: Workspace, port: int) -> _Preview:
    import uvicorn

    from agent_loco.config import Settings
    from agent_loco.runtime.improve import CycleResult
    from agent_loco.runtime.tasks import TaskManager
    from agent_loco.web_ui import create_app

    def runner(task: Any) -> CycleResult:
        return CycleResult(
            status="skipped",
            goal=task.goal,
            summary="ui review preview",
            tests_passed=True,
            committed=False,
            published=False,
            commit_sha=None,
            reason="preview",
        )

    manager = TaskManager(Settings(), runner=runner, max_concurrent=1)
    app = create_app(manager, default_workspace=workspace.root)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_port(port)
    return _Preview(
        url=f"http://127.0.0.1:{port}/",
        close=lambda: setattr(server, "should_exit", True),
    )
