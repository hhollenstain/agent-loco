from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from agent_loco.progress import record_test_run
from agent_loco.runtime.project import load_project
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema
from agent_loco.tools.files import SKIP_DIR_NAMES
from agent_loco.tools.git import is_runtime_artifact
from agent_loco.tools.shell import run_command


@dataclass
class _CachedRun:
    fingerprint: str
    result: ToolResult


_CACHE: dict[str, _CachedRun] = {}


def test_tools(
    workspace: Workspace,
    test_command: str | None,
    timeout_seconds: int,
) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="run_tests",
            description=(
                "Run the project's configured test command. "
                "If the workspace tree has not changed since the last run, "
                "returns that result without running the suite again. "
                "If no command is configured, write test_command in "
                ".loco/config.yaml and call this again."
            ),
            parameters=object_schema({}),
            handler=lambda: run_project_tests(
                workspace, test_command, timeout_seconds, phase="agent"
            ),
        )
    ]


def run_project_tests(
    workspace: Workspace,
    test_command: str | None,
    timeout_seconds: int,
    *,
    phase: str = "tests",
) -> ToolResult:
    command = _active_test_command(workspace, test_command)
    if not command:
        result = ToolResult(
            False,
            "no test command configured for this project. "
            "Write test_command in .loco/config.yaml to the command that runs "
            "these tests, then call run_tests again.",
        )
        record_test_run(command="", ok=False, output=result.output, phase=phase)
        return result
    fingerprint = _tree_fingerprint(workspace, command)
    cached = _CACHE.get(str(workspace.root))
    if cached is not None and cached.fingerprint == fingerprint:
        record_test_run(
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
    record_test_run(
        command=command,
        ok=result.ok,
        output=result.output,
        phase=phase,
        elapsed_ms=elapsed_ms,
    )
    _CACHE[str(workspace.root)] = _CachedRun(fingerprint=fingerprint, result=result)
    return result


def _active_test_command(workspace: Workspace, test_command: str | None) -> str | None:
    if test_command:
        return test_command
    return load_project(workspace.root).test_command


def _tree_fingerprint(workspace: Workspace, test_command: str) -> str:
    digest = hashlib.sha256()
    digest.update(test_command.encode("utf-8"))
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
