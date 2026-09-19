from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_loco.agent.loop import CodingAgent
from agent_loco.config import Settings
from agent_loco.llm.client import LLMClient
from agent_loco.runtime.project import (
    ProjectConfig,
    collect_context,
    load_goals,
    load_project,
    mark_goal_done,
)
from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools
from agent_loco.tools.git import (
    commit_changes,
    create_pull_request,
    current_sha,
    has_changes,
    push_changes,
)
from agent_loco.tools.tests import run_project_tests

log = logging.getLogger("loco")


def log_progress(message: str) -> None:
    log.info("%s", message)


@dataclass
class CycleResult:
    status: str
    goal: str | None
    summary: str | None
    tests_passed: bool | None
    committed: bool
    published: bool
    commit_sha: str | None
    reason: str | None



def resolve_publish(
    settings: Settings,
    project: ProjectConfig,
    cli_publish: bool | None = None,
) -> bool:
    """`--no-publish` always wins. Otherwise CLI, env, or project config can enable it."""
    if cli_publish is False:
        return False
    if cli_publish is True:
        return True
    return bool(settings.publish or project.publish_enabled)


def run_cycle(
    workspace_path: Path,
    settings: Settings,
    llm: LLMClient,
    goal: str | None = None,
    *,
    mark_checkbox: bool = True,
    cli_publish: bool | None = None,
) -> CycleResult:
    log_progress("Initializing workspace...")
    workspace = Workspace(workspace_path)
    project = load_project(workspace.root)
    allow_publish = resolve_publish(settings, project, cli_publish)
    tools = build_tools(
        workspace,
        test_command=project.test_command,
        command_timeout_seconds=settings.command_timeout_seconds,
        git_author_name=settings.git_author_name,
        git_author_email=settings.git_author_email,
        allow_publish=allow_publish,
    )

    log_progress("Running before tests...")
    tests_before = _maybe_test(workspace, project, settings)
    selected_goal = goal or _choose_goal(project, tests_before)
    if not selected_goal:
        log_progress("No pending goals and tests are green.")
        result = CycleResult(
            status="skipped",
            goal=None,
            summary=None,
            tests_passed=None if tests_before is None else tests_before.ok,
            committed=False,
            published=False,
            commit_sha=None,
            reason="no open goals and tests are green",
        )
        _write_run_log(workspace.root, result)
        return result

    log_progress(f"Selected goal: {selected_goal}")
    log_progress("Fetching current SHA...")
    sha_before = current_sha(workspace)
    log_progress("Initializing coding agent...")
    agent = CodingAgent(llm, tools, max_iterations=settings.max_iterations)
    log_progress("Running coding agent...")
    agent_result = agent.run(
        selected_goal,
        collect_context(workspace.root, project, allow_publish=allow_publish),
    )

    log_progress("Running after tests...")
    tests_after = _maybe_test(workspace, project, settings)
    if tests_after and not tests_after.ok:
        for attempt in range(project.max_repair_attempts):
            log_progress(f"Repair attempt {attempt + 1}")
            agent.run(
                "The test suite failed after the last changes. "
                "Fix the failures and nothing else.\n\n"
                + tests_after.output,
                collect_context(workspace.root, project, allow_publish=allow_publish),
            )
            tests_after = _maybe_test(workspace, project, settings)
            if tests_after.ok:
                break

    tests_passed = None if tests_after is None else tests_after.ok
    if settings.require_tests and tests_after is not None and not tests_after.ok:
        log_progress("Tests failed after repairs.")
        result = CycleResult(
            status="failed",
            goal=selected_goal,
            summary=agent_result.summary,
            tests_passed=False,
            committed=False,
            published=False,
            commit_sha=None,
            reason="tests failed; commit skipped",
        )
        _write_run_log(workspace.root, result)
        return result

    log_progress("Checking for changes...")
    committed = False
    published = False
    sha = current_sha(workspace)
    if sha_before and sha and sha != sha_before:
        committed = True
    if settings.auto_commit and has_changes(workspace):
        log_progress("Committing changes...")
        message = _commit_message(selected_goal, agent_result.summary)
        commit = commit_changes(
            workspace,
            message,
            env=_git_env(settings),
        )
        if not commit.ok:
            log_progress("Commit failed.")
            result = CycleResult(
                status="failed",
                goal=selected_goal,
                summary=agent_result.summary,
                tests_passed=tests_passed,
                committed=False,
                published=False,
                commit_sha=None,
                reason=f"commit failed: {commit.output}",
            )
            _write_run_log(workspace.root, result)
            return result
        committed = True
        log_progress(f"Committed SHA: {sha}")
        if allow_publish:
            log_progress("Pushing changes...")
            pushed = push_changes(workspace, project.publish_remote, project.publish_branch)
            published = pushed.ok
            if not pushed.ok:
                log_progress(f"Push failed: {pushed.output}")
            elif project.create_pr:
                log_progress("Creating pull request...")
                pr = create_pull_request(workspace, message, agent_result.summary)
                if not pr.ok:
                    log_progress(f"PR creation failed: {pr.output}")

    if committed and mark_checkbox:
        log_progress("Marking goal as done...")
        mark_goal_done(workspace.root, project.goals_file, selected_goal)

    log_progress("Generating cycle result...")
    result = CycleResult(
        status="success",
        goal=selected_goal,
        summary=agent_result.summary,
        tests_passed=tests_passed,
        committed=committed,
        published=published,
        commit_sha=sha,
        reason=agent_result.stopped_reason,
    )
    _write_run_log(workspace.root, result)
    return result


def _choose_goal(project: ProjectConfig, tests_before) -> str | None:
    if tests_before is not None and not tests_before.ok:
        return "Make the project's test suite pass.\n\n" + tests_before.output
    goals = load_goals(project.root, project.goals_file)
    return goals[0] if goals else None


def _maybe_test(workspace: Workspace, project: ProjectConfig, settings: Settings):
    if not project.test_command:
        return None
    result = run_project_tests(workspace, project.test_command, settings.command_timeout_seconds)
    log.info("tests ok=%s", result.ok)
    return result


def _commit_message(goal: str, summary: str) -> str:
    first_goal_line = goal.strip().splitlines()[0][:72]
    first_summary = summary.strip().splitlines()[0][:72] if summary.strip() else first_goal_line
    if first_goal_line.lower().startswith("make the project's test suite pass"):
        return first_summary
    return first_goal_line


def _git_env(settings: Settings) -> dict[str, str]:
    env: dict[str, str] = {}
    if settings.git_author_name:
        env["GIT_AUTHOR_NAME"] = settings.git_author_name
        env["GIT_COMMITTER_NAME"] = settings.git_author_name
    if settings.git_author_email:
        env["GIT_AUTHOR_EMAIL"] = settings.git_author_email
        env["GIT_COMMITTER_EMAIL"] = settings.git_author_email
    return env


def _write_run_log(root: Path, result: CycleResult) -> None:
    runs = root / ".loco" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = runs / f"{stamp}.json"
    path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")