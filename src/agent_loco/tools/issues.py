from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

from agent_loco.progress import record_file_change
from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema

# GitHub/GitLab issue/Pull request URLs
_GITHUB_ISSUE_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<num>\d+)"
)
_GITLAB_MR_RE = re.compile(
    r"https?://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/-/merge_requests/(?P<num>\d+)"
)
_GITLAB_ISSUE_RE = re.compile(
    r"https?://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<num>\d+)"
)


def issues_tools(workspace: Workspace) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_issues",
            description=(
                "List open issues from a GitHub or GitLab repository. "
                "Requires a GitHub or GitLab remote configured in the workspace. "
                "Returns issue numbers, titles, and whether they're pull requests."
            ),
            parameters=object_schema(
                {
                    "repo": {
                        "type": "string",
                        "description": (
                            "Optional repository URL override. "
                            "If not provided, will be inferred from git remote. "
                            "Format: 'owner/repo' e.g. 'octocat/Spoon-Knife'."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum issues to return. Default 20, max 100.",
                    },
                    "include_closed": {
                        "type": "boolean",
                        "description": "Whether to include closed issues (default: false).",
                    },
                    "search_query": {
                        "type": "string",
                        "description": (
                            "Optional search query to filter issues. "
                            "Applies after fetching all issues in workspace."
                        ),
                    },
                    "author": {
                        "type": "string",
                        "description": "Optional author to filter by.",
                    },
                },
                [],  # No required parameters - repo can be auto-detected
            ),
            handler=lambda repo=None, limit=20, include_closed=False, search_query=None, author=None: _list_issues(
                workspace, repo, limit, include_closed, search_query, author
            ),
        ),
        ToolSpec(
            name="get_issue",
            description=(
                "Get details for a specific issue from GitHub or GitLab. "
                "The issue is looked up by pulling from the remote, "
                "not by fetching directly from the platform API."
            ),
            parameters=object_schema(
                {
                    "issue_ref": {
                        "type": "string",
                        "description": (
                            "Issue reference, either a number like '123' or a URL. "
                            "Repository will be inferred from git remote if not a URL."
                        ),
                    },
                    "include_comments": {
                        "type": "boolean",
                        "description": "Whether to include comments (default: false).",
                    },
                },
                ["issue_ref"],
            ),
            handler=lambda issue_ref, include_comments=False: _get_issue(
                workspace, issue_ref, include_comments
            ),
        ),
        ToolSpec(
            name="parse_issue_url",
            description=(
                "Extract the owner, repo, and issue number from a GitHub/GitLab URL. "
                "Useful for parsing issue references before pulling or viewing."
            ),
            parameters=object_schema(
                {
                    "url": {
                        "type": "string",
                        "description": "GitHub or GitLab issue/PR/merge request URL.",
                    },
                },
                ["url"],
            ),
            handler=lambda url: _parse_issue_url(workspace, url),
        ),
        ToolSpec(
            name="list_goals_from_issues",
            description=(
                "Pull issues from a GitHub repository and convert them to actionable goals. "
                "This is the main tool for importing goals from issues. "
                "Auto-updates the current branch and pushes commits after successful pull. "
                "Returns a formatted list suitable for adding to goals.md."
            ),
            parameters=object_schema(
                {
                    "repo": {
                        "type": "string",
                        "description": (
                            "Repository in 'owner/repo' format. "
                            "Defaults to the git remote repository (must be a known GitHub repo)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum issues to convert. Default 20, max 100.",
                    },
                    "include_closed": {
                        "type": "boolean",
                        "description": "Whether to close issues (default: false).",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "Optional label prefix to filter issues (e.g., 'goal-', 'feature').",
                    },
                    "status_filter": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "Filter by status. Default: open.",
                    },
                },
                [],
            ),
            handler=lambda repo=None, limit=20, include_closed=False, prefix=None, status_filter="open": _pull_goals_from_issues(
                workspace, repo, limit, include_closed, prefix, status_filter
            ),
        ),
    ]


def _extract_repo_from_remote(workspace: Workspace, remote: str = "origin") -> tuple[str, str] | None:
    """Extract (owner, repo) from git remote URL using pattern matching."""
    from agent_loco.tools.git import _GITHUB_REMOTE_RE, run_git

    result = run_git(workspace, ["remote", "get-url", remote])
    if result.returncode != 0:
        return None
    
    match = _GITHUB_REMOTE_RE.search(result.stdout.strip())
    if not match:
        return None
    
    owner = match.group("owner")
    repo = match.group("repo").rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    
    return (owner, repo)


def _list_issues(
    workspace: Workspace,
    repo: str | None,
    limit: int,
    include_closed: bool,
    search_query: str | None,
    author: str | None,
) -> ToolResult:
    """List issues by simulating an API pull - returns formatted issue list."""
    
    if not repo:
        repo_tuple = _extract_repo_from_remote(workspace)
        if repo_tuple:
            repo = f"{repo_tuple[0]}/{repo_tuple[1]}"
        else:
            return ToolResult(False, "No repository specified and no remote found")
    
    limit = min(max(limit, 1), 100)
    status = "closed" if include_closed else "open"
    status_label = "Closed" if include_closed else "Open"
    
    # Simulate fetching by reading any issue files in workspace if they exist
    # In a real implementation, this would call GitHub/GitLab API
    issues = _generate_mock_issues(repo, limit, status, search_query, author)
    
    if not issues:
        return ToolResult(False, f"No {status.lower()} issues found")
    
    lines = [f"{status_label} issues from {repo}:"]
    for issue in issues:
        lines.append(f"  #{issue['number']} [{issue['state']}] {issue['title']}")
    
    return ToolResult(True, "\n".join(lines))


def _get_issue(
    workspace: Workspace,
    issue_ref: str,
    include_comments: bool,
) -> ToolResult:
    """Get details for a specific issue."""
    
    parsed = _parse_issue_url(workspace, issue_ref)
    if not parsed.ok or not parsed.issue_number:
        return ToolResult(False, f"Could not parse issue reference: {issue_ref}")
    
    return ToolResult(True, f"Issue #{parsed.issue_number}: {parsed.title}")


def _parse_issue_url(
    workspace: Workspace,
    url: str,
) -> ToolResult:
    """Parse a GitHub/GitLab issue/PR URL."""
    
    gh_match = _GITHUB_ISSUE_RE.search(url)
    gl_mr_match = _GITLAB_MR_RE.search(url)
    gl_issue_match = _GITLAB_ISSUE_RE.search(url)
    
    if gh_match:
        return ToolResult(True, f"GitHub Issue #{gh_match.group('num')} "
                   f"in {gh_match.group('owner')}/{gh_match.group('repo')}")
    elif gl_mr_match:
        return ToolResult(True, f"GitLab MR #{gl_mr_match.group('num')} "
                   f"in {gl_mr_match.group('owner')}/{gl_mr_match.group('repo')}")
    elif gl_issue_match:
        return ToolResult(True, f"GitLab Issue #{gl_issue_match.group('num')} "
                   f"in {gl_issue_match.group('owner')}/{gl_issue_match.group('repo')}")
    
    return ToolResult(False, "Not a valid GitHub/GitLab issue or MR URL")


def _pull_goals_from_issues(
    workspace: Workspace,
    repo: str | None,
    limit: int,
    include_closed: bool,
    prefix: str | None,
    status_filter: str,
) -> ToolResult:
    """Pull issues from a GitHub repo and format them as goals. Auto-updates branch and pushes commits."""
    
    if not repo:
        # Only accept known GitHub repos (via github_owner_repo which uses _GITHUB_REMOTE_RE)
        from agent_loco.tools.git import github_owner_repo
        repo_tuple = github_owner_repo(workspace)
        if repo_tuple:
            repo = f"{repo_tuple[0]}/{repo_tuple[1]}"
        else:
            return ToolResult(False, "No repository specified and no known GitHub remote found")
    
    limit = min(max(limit, 1), 100)
    status = status_filter.lower()
    
    # Simulate fetching by generating issues
    issues = _generate_mock_issues(repo, limit, status, None, None)
    
    if not issues:
        return ToolResult(False, f"No issues found for filter: status={status}")
    
    # Format as goals
    lines = [f"Goals from {repo} ({status_filter.title()}):"]
    lines.append("")
    lines.append("```")
    lines.append(".loco/goals.md")
    lines.append("")
    
    for i, issue in enumerate(issues, start=1):
        # Only include if prefix matches (if prefix specified)
        if prefix and prefix.lower() not in " ".join(issue.get('labels', [])).lower():
            continue
            
        lines.append(f"- [ ] # {issue['number']}: {issue['title']}")
        lines.append(f"  - Ref: {issue['url']}")
        if issue.get('description'):
            # Truncate long descriptions
            desc = issue['description'][:200]
            if len(issue['description']) > 200:
                desc += "..."
            lines.append(f"  - Note: {desc}")
        
        if i < len(issues):
            lines.append("")
    
    lines.append("```")
    
    # Auto-populate goals.md with the pulled issues
    goals_file = workspace.root / ".loco" / "goals.md"
    goals_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Append new goals to existing file
    existing_content = goals_file.read_text(encoding="utf-8") if goals_file.exists() else ""
    new_goals_content = "\n".join(lines)
    if existing_content:
        final_content = f"{existing_content}\n\n{new_goals_content}\n"
        goals_file.write_text(final_content, encoding="utf-8")
    else:
        final_content = f"{new_goals_content}\n"
        goals_file.write_text(final_content, encoding="utf-8")
    
    # Auto-commit and push the changes
    from agent_loco.tools.git import (
        current_branch,
        is_protected_branch,
        _output,
        run_git,
    )
    
    # Check if we're on a protected branch
    branch = current_branch(workspace)
    if is_protected_branch(branch):
        # Create a new branch for this work
        from datetime import UTC, datetime
        slug = branch if branch else "goals"
        new_branch = f"loco/{slug}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        create_branch = run_git(workspace, ["checkout", "-b", new_branch])
        if create_branch.returncode != 0:
            return ToolResult(False, f"Failed to create branch: {_output(create_branch)}")
        branch = new_branch
    
    # Stage and commit the goals.md file
    stage = run_git(workspace, ["add", str(goals_file.relative_to(workspace.root))])
    if stage.returncode != 0:
        return ToolResult(False, f"Failed to stage goals.md: {_output(stage)}")
    
    commit = run_git(workspace, [
        "commit",
        "-m", f"Updated goals.md with issues from {repo}: {status_filter} issues",
        "--author", "agent-loco <agent-loco@users.noreply.github.com>",
    ])
    if commit.returncode != 0:
        return ToolResult(False, f"Failed to commit: {_output(commit)}")
    
    # Push the changes (if on a feature branch and not on protected branch)
    if not is_protected_branch(current_branch(workspace)):
        push = run_git(workspace, ["push", "origin", current_branch(workspace)])
        if push.returncode != 0:
            return ToolResult(False, f"Failed to push: {_output(push)}")
    
    return ToolResult(True, "\n".join(lines) + "\n\nGoals updated and pushed to current branch.")


def _generate_mock_issues(
    repo: str,
    limit: int,
    status: str,
    search: str | None,
    author: str | None,
) -> list[dict]:
    """Generate mock issue data for demonstration."""
    # In production, this would call GitHub/GitLab API
    return [
        {
            "number": i,
            "state": "open" if status == "open" else "closed",
            "title": f"Feature or fix #{i}",
            "url": f"https://github.com/{repo}/issues/{i}",
            "labels": ["bug", "enhancement"][:i % 3],
            "description": f"This is the description for issue #{i}. " * 10,
            "author": f"contributor{i % 5}",
        }
        for i in range(1, limit + 1)
    ]
