from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from agent_loco.progress import record_lint_run
from agent_loco.runtime.project import load_project
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema
from agent_loco.tools.files import SKIP_DIR_NAMES
from agent_loco.tools.git import is_runtime_artifact
from agent_loco.tools.shell import run_command

_RUFF_ARROW_PATH = re.compile(r"-->\s+([^:\s]+):\d+")
_COLON_PATH = re.compile(r"^([^:\s]+?\.\w+):\d+(?::\d+)?", re.MULTILINE)


@dataclass
class _CachedRun:
    fingerprint: str
    result: ToolResult


_CACHE: dict[str, _CachedRun] = {}


def lint_targets_changed_files(output: str, changed_files: list[str]) -> bool:
    """True when lint output is about this cycle's files, or cannot be attributed."""
    mentioned = _lint_mentioned_paths(output)
    if not mentioned:
        return True
    changed = {_normalize_lint_path(path) for path in changed_files if path}
    if not changed:
        return False
    return any(_lint_path_matches(path, changed) for path in mentioned)


def _lint_mentioned_paths(output: str) -> set[str]:
    found: set[str] = set()
    for match in _RUFF_ARROW_PATH.finditer(output or ""):
        found.add(_normalize_lint_path(match.group(1)))
    for match in _COLON_PATH.finditer(output or ""):
        found.add(_normalize_lint_path(match.group(1)))
    found.discard("")
    return found


def _lint_path_matches(mentioned: str, changed: set[str]) -> bool:
    if mentioned in changed:
        return True
    return any(mentioned.endswith(f"/{path}") or path.endswith(f"/{mentioned}") for path in changed)


def _normalize_lint_path(path: str) -> str:
    posix = Path(str(path).strip().strip("\"'`")).as_posix()
    while posix.startswith("./"):
        posix = posix[2:]
    return posix


def apply_ruff_autofix(
    workspace: Workspace,
    lint_command: str | None,
    timeout_seconds: int,
) -> bool:
    """Run `ruff check --fix` when the project lint command uses ruff check."""
    if not lint_command or "ruff check" not in lint_command:
        return False
    if "ruff check --fix" in lint_command:
        fix_command = lint_command
    else:
        fix_command = lint_command.replace("ruff check", "ruff check --fix")
    started = time.perf_counter()
    result = run_command(workspace, fix_command, timeout_seconds)
    record_lint_run(
        command=fix_command,
        ok=result.ok,
        output=result.output,
        phase="autofix",
        elapsed_ms=int(round((time.perf_counter() - started) * 1000)),
    )
    return True


def lint_tools(
    workspace: Workspace,
    lint_command: str | None,
    timeout_seconds: int,
) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="run_lint",
            description=(
                "Run the project's configured linter (for example ruff format "
                "then ruff check). Call this after edits and before considering "
                "a pull request done. If the workspace tree has not changed "
                "since the last run, returns that result without running the "
                "linter again. If no command is configured, write lint_command "
                "in .loco/config.yaml and call this again."
            ),
            parameters=object_schema({}),
            handler=lambda: run_project_lint(
                workspace, lint_command, timeout_seconds, phase="agent"
            ),
        )
    ]


def run_project_lint(
    workspace: Workspace,
    lint_command: str | None,
    timeout_seconds: int,
    *,
    phase: str = "lint",
) -> ToolResult:
    command = _active_lint_command(workspace, lint_command)
    if not command:
        result = ToolResult(
            False,
            "no lint command configured for this project. "
            "Write lint_command in .loco/config.yaml to the command that "
            "lints this project, then call run_lint again.",
        )
        record_lint_run(command="", ok=False, output=result.output, phase=phase)
        return result
    fingerprint = _tree_fingerprint(workspace, command)
    cached = _CACHE.get(str(workspace.root))
    if cached is not None and cached.fingerprint == fingerprint:
        record_lint_run(
            command=command,
            ok=cached.result.ok,
            output=cached.result.output,
            phase=phase,
            elapsed_ms=0,
            reused=True,
        )
        return cached.result
    started = time.perf_counter()
    result = run_command(workspace, command, timeout_seconds)
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    record_lint_run(
        command=command,
        ok=result.ok,
        output=result.output,
        phase=phase,
        elapsed_ms=elapsed_ms,
    )
    _CACHE[str(workspace.root)] = _CachedRun(fingerprint=fingerprint, result=result)
    return result


def _active_lint_command(workspace: Workspace, lint_command: str | None) -> str | None:
    if lint_command:
        return lint_command
    return load_project(workspace.root).lint_command


def _tree_fingerprint(workspace: Workspace, lint_command: str) -> str:
    digest = hashlib.sha256()
    digest.update(lint_command.encode("utf-8"))
    root = workspace.root
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root).as_posix()
        if is_runtime_artifact(rel):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
    return digest.hexdigest()
