from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".loco",
}

MAX_READ_CHARS = 200_000
MAX_WRITE_BYTES = 1_000_000
MAX_LIST_ENTRIES = 200
MAX_SEARCH_HITS = 50


def file_tools(workspace: Workspace) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_dir",
            description="List files and directories under a workspace-relative path.",
            parameters=object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Directory to list. Use '.' for the workspace root.",
                    }
                },
                ["path"],
            ),
            handler=lambda path: _list_dir(workspace, path),
        ),
        ToolSpec(
            name="read_file",
            description="Read a text file. Optionally slice by 1-based start_line and end_line.",
            parameters=object_schema(
                {
                    "path": {"type": "string", "description": "File path inside the workspace."},
                    "start_line": {
                        "type": "integer",
                        "description": "Optional 1-based first line to include.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional 1-based last line to include.",
                    },
                },
                ["path"],
            ),
            handler=lambda path, start_line=None, end_line=None: _read_file(
                workspace, path, start_line, end_line
            ),
        ),
        ToolSpec(
            name="write_file",
            description=(
                "Create or overwrite a text file inside the workspace. "
                "Creates parent directories."
            ),
            parameters=object_schema(
                {
                    "path": {"type": "string", "description": "File path inside the workspace."},
                    "content": {"type": "string", "description": "Full file contents to write."},
                },
                ["path", "content"],
            ),
            handler=lambda path, content: _write_file(workspace, path, content),
        ),
        ToolSpec(
            name="search_text",
            description=(
                "Search workspace files for a regex or literal string. "
                "Skips VCS and dependency dirs."
            ),
            parameters=object_schema(
                {
                    "query": {"type": "string", "description": "Text or regex to search for."},
                    "glob": {
                        "type": "string",
                        "description": "Optional glob such as '*.py' or 'src/**/*.ts'.",
                    },
                },
                ["query"],
            ),
            handler=lambda query, glob=None: _search_text(workspace, query, glob),
        ),
    ]


def _list_dir(workspace: Workspace, path: str) -> ToolResult:
    directory = workspace.resolve(path)
    if not directory.exists():
        return ToolResult(False, f"not found: {path}")
    if not directory.is_dir():
        return ToolResult(False, f"not a directory: {path}")

    entries: list[str] = []
    for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in SKIP_DIR_NAMES:
            continue
        suffix = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{suffix}")
        if len(entries) >= MAX_LIST_ENTRIES:
            entries.append(f"... truncated after {MAX_LIST_ENTRIES} entries")
            break
    return ToolResult(True, "\n".join(entries) if entries else "(empty)")


def _read_file(
    workspace: Workspace,
    path: str,
    start_line: int | None,
    end_line: int | None,
) -> ToolResult:
    file_path = workspace.resolve(path)
    if not file_path.exists():
        return ToolResult(False, f"not found: {path}")
    if not file_path.is_file():
        return ToolResult(False, f"not a file: {path}")
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(False, f"not a utf-8 text file: {path}")

    lines = text.splitlines()
    start = 1 if start_line is None else max(1, start_line)
    end = len(lines) if end_line is None else min(len(lines), end_line)
    if start > end:
        return ToolResult(False, "start_line must be <= end_line")
    sliced = lines[start - 1 : end]
    numbered = [f"{idx + start:>4}|{line}" for idx, line in enumerate(sliced)]
    body = "\n".join(numbered)
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n... truncated"
    return ToolResult(True, body or "(empty file)")


def _write_file(workspace: Workspace, path: str, content: str) -> ToolResult:
    file_path = workspace.resolve(path)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        return ToolResult(False, f"refusing to write more than {MAX_WRITE_BYTES} bytes")
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolResult(False, f"write failed: {exc}")
    return ToolResult(True, f"wrote {workspace.relative(file_path)} ({len(encoded)} bytes)")


def _search_text(workspace: Workspace, query: str, glob: str | None) -> ToolResult:
    if not query:
        return ToolResult(False, "query is required")

    rg = shutil.which("rg")
    if rg:
        return _search_with_rg(workspace, rg, query, glob)
    return _search_walk(workspace, query, glob)


def _search_with_rg(workspace: Workspace, rg: str, query: str, glob: str | None) -> ToolResult:
    cmd = [rg, "--line-number", "--no-heading", "--color", "never", "--max-count", "5", query]
    if glob:
        cmd.extend(["--glob", glob])
    for name in SKIP_DIR_NAMES:
        cmd.extend(["--glob", f"!{name}"])
    result = subprocess.run(
        cmd,
        cwd=workspace.root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in {0, 1}:
        return ToolResult(False, result.stderr.strip() or "search failed")
    hits = result.stdout.splitlines()[:MAX_SEARCH_HITS]
    return ToolResult(True, "\n".join(hits) if hits else "no matches")


def _search_walk(workspace: Workspace, query: str, glob: str | None) -> ToolResult:
    hits: list[str] = []
    for path in workspace.root.rglob(glob or "*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        try:
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if query in line:
                    rel = workspace.relative(path)
                    hits.append(f"{rel}:{lineno}:{line.strip()}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return ToolResult(True, "\n".join(hits))
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return ToolResult(True, "\n".join(hits) if hits else "no matches")


def is_probably_secret_path(path: Path) -> bool:
    name = path.name.lower()
    if name in {".env", "credentials.json", "secrets.yaml", "secrets.yml", "id_rsa", "id_ed25519"}:
        return True
    if name.startswith(".env."):
        return True
    return path.suffix.lower() in {".pem", ".p12", ".key"}
