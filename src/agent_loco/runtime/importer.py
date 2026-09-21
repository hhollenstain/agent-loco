"""Load goals from GitHub issues."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from agent_loco.sandbox import SandboxError, Workspace
from agent_loco.tools.git import github_owner_repo

ALLOWED_ISSUE_STATES = frozenset({"open", "closed", "all"})


def _extract_goal_body(body: str | None) -> str:
    """Clean up issue body text for use as a goal."""
    if not body:
        return ""
    lines = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped:
            lines.append(stripped)
    return " ".join(lines)


class GitHubIssue(BaseModel):
    id: int
    number: int
    title: str
    body: str
    url: str
    state: str
    comments: int
    comments_data: list[dict] | None = None

    def as_goal(self) -> str:
        return f"#{self.number} {self.title}".strip()


def github_repo_for_workspace(root: Path | str) -> tuple[str, str] | None:
    """Return (owner, repo) from the workspace origin remote, if it is GitHub."""
    try:
        workspace = Workspace(Path(root))
    except (SandboxError, OSError):
        return None
    return github_owner_repo(workspace)


def load_goals_from_issues(
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 100,
) -> dict[str, Any]:
    """
    Load GitHub issues as selectable goals.

    Args:
        owner: GitHub repository owner (e.g., 'octocat')
        repo: GitHub repository name (e.g., 'Spoon-Knife')
        state: Filter by issue state ('open', 'closed', or 'all')
        per_page: Max issues to fetch (max 100)
    """
    status = state if state in ALLOWED_ISSUE_STATES else "open"
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    params = {"state": status, "per_page": min(max(per_page, 1), 100)}

    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        api_key = os.environ.get("GITHUB_TOKEN")
        if api_key:
            headers["Authorization"] = f"token {api_key}"

        response = httpx.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            return {
                "issues": [],
                "error": "GitHub did not return a list of issues",
                "owner": owner,
                "repo": repo,
                "state": status,
            }

        issues = []
        for item in data:
            if not isinstance(item, dict) or item.get("pull_request"):
                continue
            
            # Fetch full issue details including comments for complete context
            issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{item['number']}"
            issue_response = httpx.get(issue_url, headers=headers, timeout=30)
            issue_data = issue_response.json() if issue_response.status_code == 200 else item
            
            # Fetch comments if issue has comments
            comments = []
            if item.get("comments", 0) > 0:
                comments_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{item['number']}/comments"
                comments_response = httpx.get(comments_url, headers=headers, timeout=30)
                if comments_response.status_code == 200:
                    comments = comments_response.json()
            
            issue = GitHubIssue(
                id=item["id"],
                number=item["number"],
                title=item["title"],
                body=_extract_goal_body(item.get("body")),
                url=item["html_url"],
                state=item["state"],
                comments=item.get("comments", 0),
                comments_data=comments if comments else None,
            )
            payload = issue.model_dump()
            payload["goal"] = issue.as_goal()
            issues.append(payload)

        return {
            "issues": issues,
            "owner": owner,
            "repo": repo,
            "state": status,
        }

    except httpx.HTTPError as e:
        return {"issues": [], "error": str(e), "owner": owner, "repo": repo, "state": status}
    except Exception as e:
        return {"issues": [], "error": str(e), "owner": owner, "repo": repo, "state": status}


def load_goals_from_workspace(
    root: Path | str,
    state: str = "open",
) -> dict[str, Any]:
    """Load issues for the GitHub remote attached to this workspace."""
    parsed = github_repo_for_workspace(root)
    if not parsed:
        return {
            "issues": [],
            "error": "workspace is not a GitHub repository",
            "owner": None,
            "repo": None,
            "state": state if state in ALLOWED_ISSUE_STATES else "open",
        }
    owner, repo = parsed
    return load_goals_from_issues(owner, repo, state=state)
