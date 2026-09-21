"""Load goals from GitHub issues."""

import httpx
from pydantic import BaseModel

from agent_loco.config import Settings


def _extract_goal_body(body: str | None) -> str:
    """Clean up issue body text for use as a goal."""
    if not body:
        return ""
    # Remove markdown headers and excess whitespace
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

    Returns:
        dict with 'issues' (list), 'owner', 'repo', 'state'
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    params = {"state": state, "per_page": per_page}

    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # Check for API key in environment
        api_key = __import__("os").environ.get("GITHUB_TOKEN")
        if api_key:
            headers["Authorization"] = f"token {api_key}"

        response = httpx.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        issues = []
        for item in data:
            issues.append(
                GitHubIssue(
                    id=item["id"],
                    number=item["number"],
                    title=item["title"],
                    body=_extract_goal_body(item.get("body")),
                    url=item["html_url"],
                    state=item["state"],
                ).model_dump()
            )

        return {
            "issues": issues,
            "owner": owner,
            "repo": repo,
            "state": state,
        }

    except httpx.HTTPError as e:
        return {"issues": [], "error": str(e), "owner": owner, "repo": repo, "state": state}
    except Exception as e:
        return {"issues": [], "error": str(e), "owner": owner, "repo": repo, "state": state}
