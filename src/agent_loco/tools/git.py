from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agent_loco.sandbox import Workspace
from agent_loco.tools.base import ToolResult, ToolSpec, object_schema
from agent_loco.tools.files import is_probably_secret_path

SECRET_REFUSAL = "refusing to commit likely secrets: {paths}"
RUN_LOG_PREFIX = ".loco/runs"
PROTECTED_BRANCHES = frozenset({"main", "master", "trunk"})


def git_tools(
    workspace: Workspace,
    author_name: str | None,
    author_email: str | None,
    *,
    allow_publish: bool = False,
) -> list[ToolSpec]:
    env_extras = _git_identity_env(author_name, author_email)
    tools = [
        ToolSpec(
            name="git_status",
            description="Show git status and the current branch for the workspace.",
            parameters=object_schema({}),
            handler=lambda: _git_status(workspace),
        ),
        ToolSpec(
            name="git_diff",
            description="Show the unstaged and staged diff. Optionally limit to one path.",
            parameters=object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Optional workspace-relative path to diff.",
                    }
                }
            ),
            handler=lambda path=None: _git_diff(workspace, path),
        ),
        ToolSpec(
            name="git_log",
            description="Show recent commit subjects.",
            parameters=object_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Number of commits to show. Default 8.",
                    }
                }
            ),
            handler=lambda limit=8: _git_log(workspace, int(limit)),
        ),
        ToolSpec(
            name="git_commit",
            description=(
                "Stage workspace changes and create a commit. Refuses secrets and empty commits. "
                "Do not use this until tests have passed if the project has a test command."
            ),
            parameters=object_schema(
                {
                    "message": {
                        "type": "string",
                        "description": "Concise commit message explaining why the change exists.",
                    }
                },
                ["message"],
            ),
            handler=lambda message: commit_changes(workspace, message, env_extras),
        ),
    ]
    if allow_publish:
        tools.append(
            ToolSpec(
                name="git_push",
                description="Push the current feature branch. Refuses main/master.",
                parameters=object_schema(
                    {
                        "remote": {
                            "type": "string",
                            "description": "Remote name. Default origin.",
                        },
                        "branch": {
                            "type": "string",
                            "description": "Optional remote branch. Default: current branch.",
                        },
                    }
                ),
                handler=lambda remote="origin", branch=None: push_changes(
                    workspace, remote, branch
                ),
            )
        )
    return tools


def _git_identity_env(name: str | None, email: str | None) -> dict[str, str]:
    env: dict[str, str] = {}
    if name:
        env["GIT_AUTHOR_NAME"] = name
        env["GIT_COMMITTER_NAME"] = name
    if email:
        env["GIT_AUTHOR_EMAIL"] = email
        env["GIT_COMMITTER_EMAIL"] = email
    return env


def run_git(
    workspace: Workspace,
    args: list[str],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=workspace.root,
        check=False,
        capture_output=True,
        text=True,
        env=_merged_env(env),
    )


def _merged_env(extra: dict[str, str] | None) -> dict[str, str] | None:
    if not extra:
        return None
    import os

    merged = os.environ.copy()
    merged.update(extra)
    return merged


def _output(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        return err or text or f"git exited {result.returncode}"
    return text or "(clean)"


def _git_status(workspace: Workspace) -> ToolResult:
    if not (workspace.root / ".git").exists():
        return ToolResult(False, "not a git repository")
    result = run_git(workspace, ["status", "--short", "--branch"])
    return ToolResult(result.returncode == 0, _output(result))


def _git_diff(workspace: Workspace, path: str | None) -> ToolResult:
    args = ["diff", "HEAD"]
    if path:
        args.extend(["--", str(workspace.resolve(path))])
    result = run_git(workspace, args)
    return ToolResult(result.returncode == 0, _output(result) or "(no diff)")


def _git_log(workspace: Workspace, limit: int) -> ToolResult:
    result = run_git(workspace, ["log", f"-{max(1, min(limit, 30))}", "--oneline"])
    return ToolResult(result.returncode == 0, _output(result))


def is_runtime_artifact(path: Path | str) -> bool:
    """Cycle logs and local UI state are telemetry, not project work."""
    posix = _posix_rel(path)
    if posix == RUN_LOG_PREFIX or posix.startswith(f"{RUN_LOG_PREFIX}/"):
        return True
    return posix == ".loco/servers.json"


def has_changes(workspace: Workspace) -> bool:
    return any(_is_project_change(path) for path in _changed_paths(workspace))


def _posix_rel(path: Path | str) -> str:
    posix = Path(str(path).strip().strip('"')).as_posix()
    while posix.startswith("./"):
        posix = posix[2:]
    return posix


def _is_project_change(path: Path | str) -> bool:
    posix = _posix_rel(path)
    if posix == ".loco/.gitignore":
        return False
    return not is_runtime_artifact(posix)


def current_branch(workspace: Workspace) -> str | None:
    result = run_git(workspace, ["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def is_protected_branch(name: str | None) -> bool:
    if not name:
        return False
    return name.lower() in PROTECTED_BRANCHES


def default_base_branch(workspace: Workspace, configured: str | None = None) -> str:
    if configured and configured.strip():
        return configured.strip()
    for candidate in ("main", "master"):
        exists = run_git(workspace, ["show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"])
        if exists.returncode == 0:
            return candidate
    return current_branch(workspace) or "main"


def _branch_slug(goal: str) -> str:
    first = goal.strip().splitlines()[0] if goal.strip() else "change"
    slug = re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")
    return (slug[:40].strip("-") or "change")


def ensure_pr_branch(
    workspace: Workspace,
    goal: str,
    sha_before: str | None,
) -> ToolResult:
    """Put this cycle's work on a loco/* branch. Does not leave new commits on main."""
    current = current_branch(workspace)
    if current and current != "HEAD" and not is_protected_branch(current):
        return ToolResult(True, current)
    name = f"loco/{_branch_slug(goal)}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    created = run_git(workspace, ["checkout", "-b", name])
    if created.returncode != 0:
        return ToolResult(False, _output(created))
    if current and sha_before and is_protected_branch(current):
        moved = run_git(workspace, ["branch", "-f", current, sha_before])
        if moved.returncode != 0:
            return ToolResult(False, _output(moved))
    return ToolResult(True, name)


def current_sha(workspace: Workspace) -> str | None:
    result = run_git(workspace, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def commit_changes(
    workspace: Workspace,
    message: str,
    env: dict[str, str] | None = None,
) -> ToolResult:
    message = " ".join(message.split()).strip()
    if not message:
        return ToolResult(False, "commit message is required")
    if not (workspace.root / ".git").exists():
        return ToolResult(False, "not a git repository")

    secrets = _secret_changes(workspace)
    if secrets:
        return ToolResult(False, SECRET_REFUSAL.format(paths=", ".join(secrets)))

    add = run_git(workspace, ["add", "-A"])
    if add.returncode != 0:
        return ToolResult(False, _output(add))
    _unstage_runtime_artifacts(workspace)

    leftover_secrets = _staged_secrets(workspace)
    if leftover_secrets:
        run_git(workspace, ["reset", "HEAD", "--", *leftover_secrets])
        return ToolResult(False, SECRET_REFUSAL.format(paths=", ".join(leftover_secrets)))

    commit = run_git(workspace, ["commit", "-m", message], env=env)
    if commit.returncode != 0:
        return ToolResult(False, _output(commit))
    sha = current_sha(workspace) or ""
    return ToolResult(True, f"committed {sha}: {message}")


def push_changes(
    workspace: Workspace,
    remote: str = "origin",
    branch: str | None = None,
) -> ToolResult:
    if not remote:
        remote = "origin"
    target = (branch or current_branch(workspace) or "").strip()
    if not target or target == "HEAD":
        return ToolResult(False, "no branch to push")
    if is_protected_branch(target):
        return ToolResult(False, f"refusing to push directly to {target}")
    result = run_git(workspace, ["push", "-u", remote, f"HEAD:{target}"])
    return ToolResult(result.returncode == 0, _output(result))


def create_pull_request(
    workspace: Workspace,
    title: str,
    body: str,
    *,
    base: str | None = None,
) -> ToolResult:
    title = " ".join(title.split()).strip() or "loco changes"
    head = current_branch(workspace)
    if is_protected_branch(head):
        return ToolResult(False, f"refusing to open a PR from {head}")
    args = ["gh", "pr", "create", "--title", title, "--body", body or title]
    if base:
        args.extend(["--base", base])
    result = subprocess.run(
        args,
        cwd=workspace.root,
        check=False,
        capture_output=True,
        text=True,
    )
    ok = result.returncode == 0
    return ToolResult(ok, (result.stdout or result.stderr).strip())


def _unstage_runtime_artifacts(workspace: Workspace) -> None:
    result = run_git(workspace, ["diff", "--cached", "--name-only", "-z"])
    if result.returncode != 0 or not result.stdout:
        return
    runtime = [
        path for path in result.stdout.split("\0") if path and is_runtime_artifact(path)
    ]
    if runtime:
        run_git(workspace, ["reset", "HEAD", "--", *runtime])


def _changed_paths(workspace: Workspace) -> list[Path]:
    result = run_git(workspace, ["status", "--porcelain", "-uall"])
    if result.returncode != 0:
        return []
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        if raw:
            paths.append(Path(raw))
    return paths


def _secret_changes(workspace: Workspace) -> list[str]:
    return [path.as_posix() for path in _changed_paths(workspace) if is_probably_secret_path(path)]


def _staged_secrets(workspace: Workspace) -> list[str]:
    result = run_git(workspace, ["diff", "--cached", "--name-only"])
    if result.returncode != 0:
        return []
    secrets: list[str] = []
    for line in result.stdout.splitlines():
        if is_probably_secret_path(Path(line.strip())):
            secrets.append(line.strip())
    return secrets
