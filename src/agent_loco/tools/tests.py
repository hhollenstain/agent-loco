from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

from agent_loco.progress import record_test_run
from agent_loco.runtime.project import configured_value, load_project
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema
from agent_loco.tools.codechange import code_fingerprint, reused_check_result
from agent_loco.tools.shell import run_command


@dataclass
class _CachedRun:
    fingerprint: str
    result: ToolResult


_CACHE: dict[str, _CachedRun] = {}
_VACUOUS_TEST_RE = re.compile(
    r"would be executed|without actually running|tests were not run",
    re.IGNORECASE,
)


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
                "Call it once after code changes, including changes in another "
                "project in this workspace. If no code file has changed since "
                "the last run, this returns that result and does not run the "
                "suite again. Do not call it again until you edit code. "
                "If no command is configured, look up the official runner and call "
                "configure_project, then call this again."
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
            "Look up the official test runner, then call configure_project "
            "with test_command and run_tests again.",
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
        return reused_check_result("run_tests", cached.result)
    started = time.perf_counter()
    result = run_command(workspace, command, timeout_seconds)
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    result = _reject_vacuous_pass(result)
    record_test_run(
        command=command,
        ok=result.ok,
        output=result.output,
        phase=phase,
        elapsed_ms=elapsed_ms,
    )
    _CACHE[str(workspace.root)] = _CachedRun(fingerprint=fingerprint, result=result)
    return result


def _reject_vacuous_pass(result: ToolResult) -> ToolResult:
    """A command that exits 0 without running checks is still a failed test."""
    if not result.ok or not _VACUOUS_TEST_RE.search(result.output or ""):
        return result
    note = (
        "The test command exited 0 without running tests. "
        "Make it execute the suite and exit non-zero when a check fails, "
        "then call run_tests again."
    )
    output = f"{(result.output or '').strip()}\n{note}".strip()
    return ToolResult(False, output)


def current_test_result(
    workspace: Workspace,
    test_command: str | None,
) -> ToolResult | None:
    """The last test result when it already covers the current code. Does not run tests."""
    command = _active_test_command(workspace, test_command)
    if not command:
        return None
    cached = _CACHE.get(str(workspace.root))
    if cached is None or cached.fingerprint != _tree_fingerprint(workspace, command):
        return None
    return cached.result


def _active_test_command(workspace: Workspace, test_command: str | None) -> str | None:
    present, value = configured_value(workspace.root, "test_command")
    if present:
        return value
    if test_command:
        return test_command
    return load_project(workspace.root).test_command


def _tree_fingerprint(workspace: Workspace, test_command: str) -> str:
    digest = hashlib.sha256()
    digest.update(test_command.encode("utf-8"))
    digest.update(b"\0")
    digest.update(code_fingerprint(workspace.root).encode("utf-8"))
    return digest.hexdigest()
