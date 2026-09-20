from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agent_loco.agent.loop import CodingAgent
from agent_loco.config import Settings
from agent_loco.llm.client import LLMClient
from agent_loco.progress import bind_progress, current_events, record_event, reset_progress
from agent_loco.runtime.project import (
    ProjectConfig,
    collect_context,
    ensure_run_gitignore,
    load_goals,
    load_guidelines,
    load_project,
    mark_goal_done,
)
from agent_loco.runtime.review import GoalReview, review_goal, review_reason
from agent_loco.runtime.workdiff import collect_current_evidence, collect_work_diff
from agent_loco.sandbox import Workspace
from agent_loco.tools import build_tools
from agent_loco.tools.git import (
    commit_changes,
    create_pull_request,
    current_branch,
    current_sha,
    default_base_branch,
    ensure_pr_branch,
    has_changes,
    is_protected_branch,
    push_changes,
    upstream_state,
)
from agent_loco.tools.tests import run_project_tests

log = logging.getLogger("loco")


def log_progress(message: str) -> None:
    record_event(kind="step", message=message)
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
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    events: list[dict] = field(default_factory=list)


def resolve_create_pr(
    settings: Settings,
    project: ProjectConfig,
    cli_create_pr: bool | None = None,
) -> bool:
    """`--no-create-pr` always wins. Otherwise CLI, env, or project config can enable it."""
    if cli_create_pr is False:
        return False
    if cli_create_pr is True:
        return True
    return bool(settings.create_pr or project.publish_enabled)


def run_cycle(
    workspace_path: Path,
    settings: Settings,
    llm: LLMClient,
    goal: str | None = None,
    *,
    mark_checkbox: bool = True,
    cli_create_pr: bool | None = None,
) -> CycleResult:
    progress = bind_progress()
    try:
        return _run_cycle(
            workspace_path,
            settings,
            llm,
            goal,
            mark_checkbox=mark_checkbox,
            cli_create_pr=cli_create_pr,
        )
    finally:
        reset_progress(progress)


def _run_cycle(
    workspace_path: Path,
    settings: Settings,
    llm: LLMClient,
    goal: str | None = None,
    *,
    mark_checkbox: bool = True,
    cli_create_pr: bool | None = None,
) -> CycleResult:
    log_progress("Initializing workspace...")
    workspace = Workspace(workspace_path)
    ensure_run_gitignore(workspace.root)
    project = load_project(workspace.root)
    allow_create_pr = resolve_create_pr(settings, project, cli_create_pr)
    tools = build_tools(
        workspace,
        test_command=project.test_command,
        command_timeout_seconds=settings.command_timeout_seconds,
        git_author_name=settings.git_author_name,
        git_author_email=settings.git_author_email,
        allow_publish=False,
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
        _append_to_history(workspace.root, result)
        return result

    log_progress(f"Selected goal: {selected_goal}")
    log_progress("Fetching current SHA...")
    sha_before = current_sha(workspace)
    log_progress("Initializing coding agent...")
    agent = CodingAgent(
        llm,
        tools,
        max_iterations=settings.max_iterations,
        system_prompt=load_guidelines(workspace.root),
    )
    log_progress("Running coding agent...")
    agent_result = agent.run(
        selected_goal,
        collect_context(workspace.root, project, allow_publish=allow_create_pr),
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
                collect_context(workspace.root, project, allow_publish=allow_create_pr),
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
        _append_to_history(workspace.root, result)
        return result

    log_progress("Checking for changes...")
    sha = current_sha(workspace)
    has_work = has_changes(workspace) or bool(sha_before and sha and sha != sha_before)
    if not has_work:
        return _finish_without_changes(
            workspace,
            project,
            llm,
            selected_goal,
            agent_result.summary,
            tests_passed,
            sha,
            mark_checkbox=mark_checkbox,
        )

    review = _ensure_goal_met(
        workspace,
        settings,
        project,
        llm,
        agent,
        selected_goal,
        agent_result.summary,
        sha_before,
        tests_passed,
        allow_create_pr,
    )
    tests_passed = review["tests_passed"]
    agent_result_summary = review["summary"]
    if not review["met"]:
        log_progress(f"Goal not met: {review['reason']}")
        reason = (
            "tests failed; commit skipped"
            if review.get("tests_failed")
            else f"goal not met; PR skipped: {review['reason']}"
        )
        result = CycleResult(
            status="failed",
            goal=selected_goal,
            summary=agent_result_summary,
            tests_passed=tests_passed,
            committed=False,
            published=False,
            commit_sha=current_sha(workspace),
            reason=reason,
        )
        _write_run_log(workspace.root, result)
        _append_to_history(workspace.root, result)
        return result
    agent_result.summary = agent_result_summary

    log_progress("Goal confirmed; checking for publishable changes...")
    committed = False
    published = False
    sha = current_sha(workspace)
    if sha_before and sha and sha != sha_before:
        committed = True

    will_commit = settings.auto_commit and has_changes(workspace)
    if allow_create_pr and (committed or will_commit):
        log_progress("Moving work onto a pull-request branch...")
        branched = ensure_pr_branch(workspace, selected_goal, sha_before)
        if not branched.ok:
            log_progress(f"Could not create PR branch: {branched.output}")
            result = CycleResult(
                status="failed",
                goal=selected_goal,
                summary=agent_result.summary,
                tests_passed=tests_passed,
                committed=False,
                published=False,
                commit_sha=sha,
                reason=f"could not leave main: {branched.output}",
            )
            _write_run_log(workspace.root, result)
            _append_to_history(workspace.root, result)
            return result
        log_progress(f"On branch {branched.output}")

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
            _append_to_history(workspace.root, result)
            return result
        committed = True
        sha = current_sha(workspace)
        log_progress(f"Committed SHA: {sha}")

    if committed and allow_create_pr:
        branch = current_branch(workspace)
        if is_protected_branch(branch):
            log_progress(f"Refusing to publish {branch} directly.")
            result = CycleResult(
                status="failed",
                goal=selected_goal,
                summary=agent_result.summary,
                tests_passed=tests_passed,
                committed=committed,
                published=False,
                commit_sha=sha,
                reason=f"refusing to push commits on {branch}",
            )
            _write_run_log(workspace.root, result)
            _append_to_history(workspace.root, result)
            return result
        log_progress(f"Pushing branch {branch}...")
        pushed = push_changes(workspace, project.publish_remote, branch)
        if not pushed.ok:
            log_progress(f"Push failed: {pushed.output}")
            result = CycleResult(
                status="failed",
                goal=selected_goal,
                summary=agent_result.summary,
                tests_passed=tests_passed,
                committed=committed,
                published=False,
                commit_sha=sha,
                reason=f"push failed: {pushed.output}",
            )
            _write_run_log(workspace.root, result)
            _append_to_history(workspace.root, result)
            return result
        base = default_base_branch(workspace, project.publish_branch)
        log_progress("Opening pull request...")
        pr = create_pull_request(
            workspace,
            _commit_message(selected_goal, agent_result.summary),
            _pr_body(
                selected_goal,
                agent_result.summary,
                tests_passed=tests_passed,
                commit_sha=sha,
                branch=branch or "",
            ),
            base=base,
        )
        published = pr.ok
        if not pr.ok:
            log_progress(f"PR creation failed: {pr.output}")
            result = CycleResult(
                status="failed",
                goal=selected_goal,
                summary=agent_result.summary,
                tests_passed=tests_passed,
                committed=committed,
                published=False,
                commit_sha=sha,
                reason=f"PR failed: {pr.output}",
            )
            _write_run_log(workspace.root, result)
            _append_to_history(workspace.root, result)
            return result
        log_progress(pr.output)

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
    _append_to_history(workspace.root, result)
    return result


def _finish_without_changes(
    workspace: Workspace,
    project: ProjectConfig,
    llm: LLMClient,
    goal: str,
    summary: str,
    tests_passed: bool | None,
    sha: str | None,
    *,
    mark_checkbox: bool,
) -> CycleResult:
    log_progress("No project files changed; checking whether the goal is already met...")
    upstream = upstream_state(workspace, project.publish_remote)
    log_progress(f"Upstream: {upstream.detail}")
    evidence = collect_current_evidence(workspace, goal)
    verdict = review_goal(
        llm,
        goal,
        diff=evidence,
        summary=summary,
        tests_passed=tests_passed,
        existing=True,
        upstream=upstream.detail,
    )
    log_progress(f"Goal review: met={verdict.met} ({review_reason(verdict)})")
    combined = _no_work_summary(summary, verdict, upstream.detail)
    if verdict.met:
        log_progress(f"No work needed: {verdict.reason}")
        if mark_checkbox:
            log_progress("Marking goal as done...")
            mark_goal_done(workspace.root, project.goals_file, goal)
        result = CycleResult(
            status="success",
            goal=goal,
            summary=combined,
            tests_passed=tests_passed,
            committed=False,
            published=False,
            commit_sha=sha,
            reason=f"no changes needed: {verdict.reason} {upstream.detail}",
        )
    else:
        log_progress("Goal is not already met; no files were changed.")
        result = CycleResult(
            status="skipped",
            goal=goal,
            summary=combined,
            tests_passed=tests_passed,
            committed=False,
            published=False,
            commit_sha=sha,
            reason=(
                "no files changed and the goal is not already met: "
                f"{review_reason(verdict)} {upstream.detail}"
            ),
        )
    _write_run_log(workspace.root, result)
    _append_to_history(workspace.root, result)
    return result


def _no_work_summary(agent_summary: str, verdict: GoalReview, upstream_detail: str) -> str:
    if verdict.met:
        lines = [
            f"No work to do: {verdict.reason}",
            upstream_detail,
        ]
    else:
        lines = [
            "No files changed this cycle, and the current tree does not already "
            f"meet the goal: {review_reason(verdict)}",
            upstream_detail,
        ]
    notes = (agent_summary or "").strip()
    skip_notes = {
        "stopped after reaching the iteration limit.",
        "agent finished without a summary.",
    }
    if notes and notes.lower() not in skip_notes:
        lines.extend(["", "Agent notes:", notes])
    return "\n".join(lines)


def _ensure_goal_met(
    workspace: Workspace,
    settings: Settings,
    project: ProjectConfig,
    llm: LLMClient,
    agent: CodingAgent,
    goal: str,
    summary: str,
    sha_before: str | None,
    tests_passed: bool | None,
    allow_create_pr: bool,
) -> dict[str, object]:
    context = collect_context(workspace.root, project, allow_publish=allow_create_pr)
    work_diff = collect_work_diff(workspace, sha_before, goal=goal)
    verdict = review_goal(
        llm,
        goal,
        diff=work_diff,
        summary=summary,
        tests_passed=tests_passed,
    )
    log_progress(f"Goal review: met={verdict.met} ({review_reason(verdict)})")
    attempts = 0
    while (
        not verdict.met
        and verdict.parsed
        and attempts < project.max_repair_attempts
    ):
        attempts += 1
        log_progress(f"Goal retry {attempts}/{project.max_repair_attempts}: {verdict.reason}")
        work_diff = collect_work_diff(workspace, sha_before, goal=goal)
        follow = agent.run(
            _goal_retry_prompt(goal, verdict.reason, work_diff),
            context,
        )
        if follow.summary:
            summary = follow.summary
        tests_after = _maybe_test(workspace, project, settings)
        tests_passed = None if tests_after is None else tests_after.ok
        if settings.require_tests and tests_after is not None and not tests_after.ok:
            return {
                "met": False,
                "reason": "tests failed after goal retry",
                "summary": summary,
                "tests_passed": False,
                "tests_failed": True,
            }
        work_diff = collect_work_diff(workspace, sha_before, goal=goal)
        verdict = review_goal(
            llm,
            goal,
            diff=work_diff,
            summary=summary,
            tests_passed=tests_passed,
        )
        log_progress(f"Goal review: met={verdict.met} ({review_reason(verdict)})")
    return {
        "met": verdict.met,
        "reason": review_reason(verdict),
        "summary": summary,
        "tests_passed": tests_passed,
        "tests_failed": False,
    }


def _goal_retry_prompt(goal: str, reason: str, diff: str) -> str:
    return (
        "The stated goal is not done. Do not summarize. Finish the goal.\n\n"
        f"Goal:\n{goal.strip()}\n\n"
        f"Why it is not done:\n{reason.strip()}\n\n"
        f"Current diff:\n{diff}"
    )


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


def _pr_body(
    goal: str,
    summary: str,
    *,
    tests_passed: bool | None,
    commit_sha: str | None,
    branch: str,
) -> str:
    detail = (summary or "").strip() or goal.strip()
    if tests_passed is True:
        tests_line = "- [x] Project tests passed locally"
    elif tests_passed is False:
        tests_line = "- [ ] Project tests failed locally — do not merge until green"
    else:
        tests_line = "- [ ] No project test command; verify manually"
    lines = [
        "## Summary",
        "",
        detail,
        "",
        "## Goal",
        "",
        goal.strip(),
        "",
        "## Test plan",
        "",
        tests_line,
        "- [x] Goal review confirmed the requested outcome",
        "- [ ] Review this feature branch; do not merge unreviewed commits to main",
    ]
    if commit_sha:
        lines.extend(["", f"Commit: `{commit_sha}`"])
    if branch:
        lines.append(f"Branch: `{branch}`")
    return "\n".join(lines)


def _git_env(settings: Settings) -> dict[str, str]:
    env: dict[str, str] = {}
    if settings.git_author_name:
        env["GIT_AUTHOR_NAME"] = settings.git_author_name
        env["GIT_COMMITTER_NAME"] = settings.git_author_name
    if settings.git_author_email:
        env["GIT_AUTHOR_EMAIL"] = settings.git_author_email
        env["GIT_COMMITTER_EMAIL"] = settings.git_author_email
    return env


def _attach_events(result: CycleResult) -> None:
    if not result.events:
        result.events = current_events()


def _write_run_log(root: Path, result: CycleResult) -> None:
    _attach_events(result)
    ensure_run_gitignore(root)
    runs = root / ".loco" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = runs / f"{stamp}.json"
    payload = asdict(result)
    payload.setdefault("id", stamp)
    payload.setdefault("created_at", result.created_at)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _append_to_history(root: Path, result: CycleResult) -> None:
    """Append cycle result to history.json for persistent storage across restarts."""
    history_file = root / "history.json"
    # Read existing history
    try:
        if history_file.exists():
            with open(history_file, encoding="utf-8") as f:
                history = json.load(f)
        else:
            history = []
    except (OSError, json.JSONDecodeError):
        # If we can't read the file for any reason, start fresh
        history = []
    
    # Add the new result to the beginning of the list 
    # (most recent at the beginning) and keep only the last 100 entries
    _attach_events(result)
    history.insert(0, asdict(result))
    
    # Limit history size to avoid file becoming too large
    if len(history) > 100:
        history = history[:100]
    
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except OSError:
        # If we can't write the file, silently ignore (not critical for functionality)
        pass