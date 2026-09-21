"""Load goals from GitHub issues."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from agent_loco.sandbox import SandboxError, Workspace
from agent_loco.tools.git import github_owner_repo

ALLOWED_ISSUE_STATES = frozenset({"open", "closed", "all"})
_GITHUB_ISSUE_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<num>\d+)",
    re.IGNORECASE,
)
_GITLAB_ISSUE_RE = re.compile(
    r"https?://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<num>\d+)",
    re.IGNORECASE,
)
_GITLAB_MR_RE = re.compile(
    r"https?://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/-/merge_requests/(?P<num>\d+)",
    re.IGNORECASE,
)
_BARE_ISSUE_RE = re.compile(r"^#?(?P<num>\d+)$")


@dataclass(frozen=True)
class IssueRef:
    number: int
    owner: str | None = None
    repo: str | None = None


def parse_issue_ref(text: str) -> IssueRef | None:
    """Parse a GitHub/GitLab issue URL or a bare issue number."""
    raw = (text or "").strip()
    if not raw:
        return None
    for pattern in (_GITHUB_ISSUE_RE, _GITLAB_ISSUE_RE, _GITLAB_MR_RE):
        match = pattern.search(raw)
        if match:
            return IssueRef(
                number=int(match.group("num")),
                owner=match.group("owner"),
                repo=match.group("repo"),
            )
    match = _BARE_ISSUE_RE.fullmatch(raw)
    if match:
        return IssueRef(number=int(match.group("num")))
    return None


def format_issue_goal(
    *,
    number: int,
    title: str,
    url: str = "",
    body: str = "",
    comments: list[dict] | None = None,
) -> str:
    """Turn an issue into the goal text the cycle should implement."""
    lines = [f"#{number} {title}".strip()]
    if url:
        lines.append(url)
    lines.extend(
        [
            "",
            "Implement this GitHub issue. Do the work it describes; do not only summarize it.",
        ]
    )
    text = (body or "").strip()
    if text:
        lines.extend(["", text])
    comment_blocks = _format_comments(comments)
    if comment_blocks:
        lines.extend(["", "Comments:", *comment_blocks])
    return "\n".join(lines).strip() + "\n"


def goal_headline(goal: str) -> str:
    """Short title for logs, branch slugs, and commit subjects."""
    lines = [line.strip() for line in (goal or "").splitlines() if line.strip()]
    if not lines:
        return "change"
    for line in lines:
        if re.match(r"^#\d+\b", line):
            return line
    return lines[0]


def _format_comments(comments: list[dict] | None) -> list[str]:
    blocks: list[str] = []
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        text = (comment.get("body") or "").strip()
        if not text:
            continue
        who = f"@{login}" if login else "comment"
        blocks.append(f"{who}:\n{text}")
    return blocks


class GitHubIssue(BaseModel):
    id: int
    number: int
    title: str
    body: str
    url: str
    state: str
    comments: int = 0
    comments_data: list[dict] | None = None

    def as_goal(self) -> str:
        return format_issue_goal(
            number=self.number,
            title=self.title,
            url=self.url,
            body=self.body,
            comments=self.comments_data,
        )

    def short_label(self) -> str:
        return f"#{self.number} {self.title}".strip()


def github_repo_for_workspace(root: Path | str) -> tuple[str, str] | None:
    """Return (owner, repo) from the workspace origin remote, if it is GitHub."""
    try:
        workspace = Workspace(Path(root))
    except (SandboxError, OSError):
        return None
    return github_owner_repo(workspace)


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_key = os.environ.get("GITHUB_TOKEN")
    if api_key:
        headers["Authorization"] = f"token {api_key}"
    return headers


def _fetch_issue_comments(
    owner: str,
    repo: str,
    number: int,
    headers: dict[str, str],
) -> list[dict]:
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"
    try:
        response = httpx.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError:
        return []
    if not isinstance(data, list):
        return []
    comments: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        comments.append(
            {
                "user": {"login": user.get("login")},
                "body": item.get("body") or "",
            }
        )
    return comments


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
    headers = _github_headers()

    try:
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
            number = int(item["number"])
            comments = []
            if int(item.get("comments") or 0) > 0:
                comments = _fetch_issue_comments(owner, repo, number, headers)
            issue = GitHubIssue(
                id=item["id"],
                number=number,
                title=item["title"],
                body=(item.get("body") or "").strip(),
                url=item["html_url"],
                state=item["state"],
                comments=len(comments) if comments else int(item.get("comments") or 0),
                comments_data=comments or None,
            )
            payload = issue.model_dump()
            payload["goal"] = issue.as_goal()
            payload["label"] = issue.short_label()
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
