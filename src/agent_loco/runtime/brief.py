from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agent_loco.runtime.project import render_tree
from agent_loco.tools.files import is_probably_secret_path

STOPWORDS = frozenset({"the", "and", "for", "add", "make", "is", "to", "of", "a", "in", "on"})
LAST_FAILURE_FILE = ".loco/last-failure.json"


def _compute_goal_key(goal: str) -> str:
    """Compute a 16-char hex prefix of sha256 of stripped goal."""
    stripped = goal.strip()
    digest = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    return digest[:16]


def _read_last_failure(root: Path, goal: str) -> dict | None:
    """Read last-failure.json and return if goal_key matches current goal."""
    try:
        failure_path = root / LAST_FAILURE_FILE
        if not failure_path.exists():
            return None
        data = json.loads(failure_path.read_text(encoding="utf-8"))
        if _compute_goal_key(goal) != data.get("goal_key"):
            return None
        return data
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def build_task_brief(root: Path, goal: str, project) -> str:
    """Build a task brief that injects AGENTS.md context and relevant paths.

    Args:
        root: Workspace root path
        goal: The goal/issue description
        project: ProjectConfig with workspace settings

    Returns:
        Compact task brief including:
        - Project/test/PR info (from collect_context)
        - AGENTS.md excerpt (first 80 lines)
        - Relevant paths (up to 12 files matching tokens from goal)
        - Enabled skills list
        - Workspace files tree
    """
    from agent_loco.runtime.skills import enabled_skill_names

    root = root.resolve()
    parts = [
        f"Project: {project.name}",
        f"Root: {root}",
        f"Test command: {project.test_command or '(none)'}",
        f"Lint command: {project.lint_command or '(none)'}",
        f"Create PR: {'on' if project.publish_enabled else 'off'} via {project.publish_remote}",
        " (never pushes to main)",
    ]

    # AGENTS.md excerpt: first 80 lines if exists
    agents_path = root / "AGENTS.md"
    if agents_path.exists():
        lines = agents_path.read_text(encoding="utf-8").splitlines()
        excerpt = lines[:80]
        parts.append("AGENTS.md excerpt:")
        parts.extend(excerpt)

    # Relevant paths: up to 12 files matching tokens from goal
    goal_tokens = _extract_goal_tokens(goal)
    relevant = _find_relevant_files(root, goal_tokens, max_files=12)
    if relevant:
        parts.append("Relevant paths:")
        for path in relevant:
            rel = root / path
            parts.append(f"- {root.name}/{rel.relative_to(root)}")

    # Enabled skills
    enabled = enabled_skill_names(root)
    if enabled:
        parts.append("Enabled skills: " + ", ".join(enabled))

    # Workspace files tree
    tree = render_tree(root)
    if tree:
        parts.append("Workspace files:")
        parts.append(tree)

    # Last failure for this goal
    failure = _read_last_failure(root, goal)
    if failure:
        parts.append("")
        parts.append("Last attempt of this goal failed:")
        parts.append(f"status: {failure.get('status', 'unknown')}")
        parts.append(f"reason: {failure.get('reason', 'unknown')}")

    return "\n".join(parts)


def _extract_goal_tokens(goal: str) -> list[str]:
    """Extract significant tokens from the goal.

    Split on non-alphanumeric, drop tokens shorter than 3 chars
    and stopwords.
    """
    tokens = re.split(r"[^a-z0-9]+", goal.lower())
    return [t for t in tokens if len(t) >= 3 and t not in STOPWORDS]


def _find_relevant_files(root: Path, goal_tokens: list[str], max_files: int) -> list[str]:
    """Find up to max_files files whose paths share tokens with goal_tokens.

    Similar tree traversal as render_tree, skipping .loco directories.
    """
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
    skip_root = root / ".loco"
    found: list[str] = []

    def walk(directory: Path, rel_prefix: str) -> None:
        if len(found) >= max_files:
            return
        for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if len(found) >= max_files:
                break
            if child.name in skip_dirs or child == skip_root:
                continue
            if child.name == ".loco":
                continue
            rel_path = rel_prefix + child.name if rel_prefix else child.name
            rel_upper = rel_path.lower()
            # Skip secret paths entirely
            full_path = child.resolve() if child.is_file() else child
            if is_probably_secret_path(full_path):
                continue
            # Check if any goal token appears in the path
            matched = any(token in rel_upper for token in goal_tokens)
            if matched and child.is_file():
                found.append(rel_path)
            elif child.is_dir():
                walk(child, rel_path + "/")

    walk(root, "")
    return found[:max_files]
