"""Load goals from GitHub issues."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from agent_lbw.sandbox import SandboxError, Workspace
from agent_lbw.tools.git import github_owner_repo

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


def _github_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    secret = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
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
    token: str | None = None,
) -> dict[str, Any]:
    """Load GitHub issues as selectable goals. Pull requests are counted apart."""
    status = state if state in ALLOWED_ISSUE_STATES else "open"
    try:
        return _read_issue_pages(owner, repo, status, per_page, token)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 403 and token:
            public = load_goals_from_issues(owner, repo, state=status, per_page=per_page)
            if "error" not in public:
                return public
        return _issue_failure(owner, repo, status, _issue_http_message(code, exc))
    except httpx.HTTPError as exc:
        return _issue_failure(owner, repo, status, str(exc))
    except Exception as exc:
        return _issue_failure(owner, repo, status, str(exc))


def _issue_failure(owner: str, repo: str, state: str, message: str) -> dict[str, Any]:
    return {
        "issues": [],
        "pull_requests": 0,
        "error": message,
        "owner": owner,
        "repo": repo,
        "state": state,
    }


def _issue_http_message(code: int, exc: httpx.HTTPStatusError) -> str:
    if code in {401, 403}:
        return "GitHub refused to list issues. Grant the app Issues read."
    if code == 404:
        return "GitHub could not find that repository."
    return str(exc)


def _read_issue_pages(
    owner: str,
    repo: str,
    state: str,
    per_page: int,
    token: str | None,
) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    page_size = min(max(per_page, 1), 100)
    headers = _github_headers(token)
    issues: list[dict] = []
    pull_requests = 0
    for page in range(1, 6):
        response = httpx.get(
            url,
            params={"state": state, "per_page": page_size, "page": page},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            return _issue_failure(owner, repo, state, "GitHub did not return a list of issues")
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("pull_request"):
                pull_requests += 1
                continue
            number = int(item["number"])
            comments: list[dict] = []
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
        if len(data) < page_size:
            break
    return {
        "issues": issues,
        "pull_requests": pull_requests,
        "owner": owner,
        "repo": repo,
        "state": state,
    }


def load_goals_from_workspace(
    root: Path | str,
    state: str = "open",
    token: str | None = None,
) -> dict[str, Any]:
    """Load issues for the GitHub remote attached to this workspace."""
    parsed = github_repo_for_workspace(root)
    status = state if state in ALLOWED_ISSUE_STATES else "open"
    if not parsed:
        return {
            "issues": [],
            "pull_requests": 0,
            "error": "workspace is not a GitHub repository",
            "owner": None,
            "repo": None,
            "state": status,
        }
    owner, repo = parsed
    return load_goals_from_issues(owner, repo, state=status, token=token)
