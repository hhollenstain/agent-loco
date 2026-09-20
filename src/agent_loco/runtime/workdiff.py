from __future__ import annotations

import re
from pathlib import Path

from agent_loco.sandbox import Workspace
from agent_loco.tools.git import is_runtime_artifact, run_git

DIFF_LIMIT = 16_000
MAX_LOCKFILE_ROWS = 80
UNTRACKED_FILE_LIMIT = 4_000

LOCKFILE_BASENAMES = frozenset(
    {
        "pipfile.lock",
        "poetry.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pnpm-lock.yml",
        "bun.lock",
        "bun.lockb",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "uv.lock",
        "pdm.lock",
        "flake.lock",
        "go.sum",
    }
)

_META_KEYS = frozenset(
    {
        "checksum",
        "content-hash",
        "default",
        "dependencies",
        "develop",
        "dev-dependencies",
        "editable",
        "extras",
        "files",
        "git",
        "groups",
        "hashes",
        "index",
        "integrity",
        "lock-version",
        "markers",
        "meta",
        "metadata",
        "package",
        "packages",
        "path",
        "pipfile-spec",
        "python-versions",
        "python_version",
        "ref",
        "requires",
        "requires_python",
        "resolved",
        "source",
        "subdir",
        "version",
    }
)

_GIT_DIFF_SPLIT = re.compile(r"(?=^diff --git )", re.MULTILINE)
_DIFF_PATH = re.compile(r"^diff --git a/.+ b/(.+)$", re.MULTILINE)
_PLUS_PATH = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_PACKAGE_KEY = re.compile(r'^"([^"]+)"\s*:\s*\{')
_JSON_VERSION = re.compile(r'^"version"\s*:\s*"([^"]+)"')
_TOML_NAME = re.compile(r'^name\s*=\s*"([^"]+)"')
_TOML_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"')
_YARN_HEADER = re.compile(r'^"?([^"@\s]+)@')
_YARN_VERSION = re.compile(r'^version\s+"([^"]+)"')
_GOAL_TOKEN = re.compile(r"[A-Za-z][\w.\-]{2,}")


def is_lockfile(path: str) -> bool:
    name = Path(path).name.lower()
    if name in LOCKFILE_BASENAMES:
        return True
    if name.endswith(("-lock.json", "-lock.yaml", "-lock.yml")):
        return True
    return name.endswith(".lock")


def collect_work_diff(
    workspace: Workspace,
    sha_before: str | None,
    *,
    goal: str | None = None,
) -> str:
    """Diff of this cycle's work: commits after sha_before plus the working tree."""
    status = run_git(workspace, ["status", "--short", "--branch"])
    range_args = [sha_before] if sha_before else ["HEAD"]
    names = run_git(workspace, ["diff", "--name-status", *range_args])
    diff = run_git(workspace, ["diff", *range_args])
    lock_summaries = lockfile_summaries_from_status(
        workspace,
        names.stdout,
        sha_before,
        goal=goal,
    )
    untracked_parts: list[str] = []
    listed = run_git(workspace, ["ls-files", "--others", "--exclude-standard"])
    for rel in listed.stdout.splitlines():
        path = rel.strip()
        if not path or is_runtime_artifact(path):
            continue
        full = workspace.root / path
        if not full.is_file():
            untracked_parts.append(f"untracked: {path}")
            continue
        try:
            body = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            untracked_parts.append(f"untracked binary: {path}")
            continue
        untracked_parts.append(_format_untracked(path, body, goal=goal))
    return format_work_diff(
        status=status.stdout,
        name_status=names.stdout,
        unified=diff.stdout,
        extra=untracked_parts,
        lock_summaries=lock_summaries,
        goal=goal,
    )


def format_work_diff(
    *,
    status: str = "",
    name_status: str = "",
    unified: str = "",
    extra: list[str] | None = None,
    lock_summaries: list[str] | None = None,
    goal: str | None = None,
    limit: int = DIFF_LIMIT,
) -> str:
    """Build a reviewer-sized diff: file list, source hunks, lockfile versions."""
    header_parts: list[str] = []
    if status.strip():
        header_parts.append(status.strip())
    if name_status.strip():
        header_parts.append("Changed files:\n" + name_status.strip())
    source_parts, lock_from_diff = partition_unified(unified, goal=goal)
    lock_parts = list(lock_summaries) if lock_summaries is not None else lock_from_diff
    body = source_parts + lock_parts + [part for part in extra or [] if part.strip()]
    header = "\n\n".join(header_parts)
    if not header and not body:
        return "(no diff)"
    remaining = limit - len(header) - (2 if header and body else 0)
    packed = _join_budget(body, max(remaining, 0))
    if header and packed:
        return f"{header}\n\n{packed}"
    return header or packed or "(no diff)"


def summarize_unified_diff(
    text: str,
    *,
    goal: str | None = None,
    limit: int = DIFF_LIMIT,
) -> str:
    """Rewrite lockfile hunks as version changes; leave other files intact."""
    raw = (text or "").strip()
    if not raw:
        return raw
    source_parts, lock_parts = partition_unified(raw, goal=goal)
    if not lock_parts and len(raw) <= limit:
        return raw
    return _join_budget(source_parts + lock_parts, limit) or raw[:limit]


def lockfile_summaries_from_status(
    workspace: Workspace,
    name_status: str,
    sha_before: str | None,
    *,
    goal: str | None = None,
) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    for line in (name_status or "").splitlines():
        path = _name_status_path(line)
        if not path or path in seen or not is_lockfile(path):
            continue
        seen.add(path)
        old = _file_at_revision(workspace, sha_before, path)
        new = _working_tree_text(workspace, path)
        summaries.append(summarize_lockfile_pair(path, old, new, goal=goal))
    return summaries


def summarize_lockfile_pair(
    path: str,
    old_text: str,
    new_text: str,
    *,
    goal: str | None = None,
) -> str:
    rows = version_change_rows(
        extract_versions_from_lock_text(old_text),
        extract_versions_from_lock_text(new_text),
        goal=goal,
    )
    return _format_lockfile_rows(path, rows)


def partition_unified(text: str, *, goal: str | None = None) -> tuple[list[str], list[str]]:
    source: list[str] = []
    lock: list[str] = []
    for chunk in _split_file_diffs(text):
        path = _diff_path(chunk)
        if path and is_lockfile(path):
            lock.append(summarize_lockfile_diff(path, chunk, goal=goal))
        else:
            source.append(chunk)
    return source, lock


def summarize_lockfile_diff(path: str, chunk: str, *, goal: str | None = None) -> str:
    return _format_lockfile_rows(path, extract_version_changes(chunk, goal=goal))


def extract_versions_from_lock_text(text: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    name: str | None = None
    for raw_line in (text or "").splitlines():
        stripped = raw_line.strip().rstrip(",")
        package = _package_key(stripped)
        if package:
            name = package
            continue
        version = _version_value(stripped)
        if version and name:
            versions[name] = version
    return versions


def extract_version_changes(chunk: str, *, goal: str | None = None) -> list[str]:
    old: dict[str, str] = {}
    new: dict[str, str] = {}
    name: str | None = None
    for raw_line in chunk.splitlines():
        if not raw_line or raw_line.startswith(("diff ", "index ", "+++", "---", "@@", "\\")):
            continue
        prefix = raw_line[0] if raw_line[0] in "+-" else " "
        line = raw_line[1:] if raw_line[0] in "+- " else raw_line
        stripped = line.strip().rstrip(",")
        package = _package_key(stripped)
        if package:
            name = package
            continue
        version = _version_value(stripped)
        if not version or not name:
            continue
        if prefix == "-":
            old[name] = version
        elif prefix == "+":
            new[name] = version
    return version_change_rows(old, new, goal=goal)


def version_change_rows(
    old: dict[str, str],
    new: dict[str, str],
    *,
    goal: str | None = None,
) -> list[str]:
    pairs: list[tuple[str, str, str]] = []
    for package in set(old) | set(new):
        before = old.get(package)
        after = new.get(package)
        if before == after:
            continue
        pairs.append((package, before or "(none)", after or "(removed)"))
    tokens = _goal_tokens(goal)
    pairs.sort(
        key=lambda item: (
            0 if _matches_goal(item[0], tokens) else 1,
            item[0].lower(),
        )
    )
    return [f"- {package}: {before} -> {after}" for package, before, after in pairs]


def _format_lockfile_rows(path: str, rows: list[str]) -> str:
    if not rows:
        return (
            f"{path} (lockfile): no package version changes in diff; "
            "hashes or metadata may have changed"
        )
    shown = rows[:MAX_LOCKFILE_ROWS]
    extra = len(rows) - len(shown)
    lines = [f"{path} (lockfile version changes):", *shown]
    if extra > 0:
        lines.append(f"... and {extra} more package version changes")
    return "\n".join(lines)


def _name_status_path(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    path = parts[1]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip() or None


def _file_at_revision(workspace: Workspace, sha: str | None, path: str) -> str:
    rev = sha or "HEAD"
    result = run_git(workspace, ["show", f"{rev}:{path}"])
    if result.returncode != 0:
        return ""
    return result.stdout


def _working_tree_text(workspace: Workspace, path: str) -> str:
    full = workspace.root / path
    if not full.is_file():
        return ""
    try:
        return full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _format_untracked(path: str, body: str, *, goal: str | None) -> str:
    if is_lockfile(path):
        return summarize_lockfile_pair(path, "", body, goal=goal)
    if len(body) > UNTRACKED_FILE_LIMIT:
        body = body[:UNTRACKED_FILE_LIMIT] + "\n... truncated"
    return f"--- /dev/null\n+++ b/{path}\n{body}"


def _split_file_diffs(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if raw.startswith("diff --git "):
        return [chunk.strip() for chunk in _GIT_DIFF_SPLIT.split(raw) if chunk.strip()]
    return [raw]


def _diff_path(chunk: str) -> str | None:
    match = _DIFF_PATH.search(chunk)
    if match:
        return match.group(1).strip()
    match = _PLUS_PATH.search(chunk)
    if match:
        return match.group(1).strip()
    return None


def _package_key(stripped: str) -> str | None:
    match = _PACKAGE_KEY.match(stripped)
    if match:
        name = match.group(1)
        return None if name.lower() in _META_KEYS else name
    match = _TOML_NAME.match(stripped)
    if match:
        return match.group(1)
    match = _YARN_HEADER.match(stripped)
    if match and stripped.endswith(":"):
        return match.group(1)
    return None


def _version_value(stripped: str) -> str | None:
    for pattern in (_JSON_VERSION, _TOML_VERSION, _YARN_VERSION):
        match = pattern.match(stripped)
        if match:
            value = match.group(1).strip()
            if value.startswith("=="):
                return value[2:]
            return value
    return None


def _goal_tokens(goal: str | None) -> set[str]:
    if not goal:
        return set()
    return {match.group(0).lower() for match in _GOAL_TOKEN.finditer(goal)}


def _matches_goal(name: str, tokens: set[str]) -> bool:
    lowered = name.lower()
    return any(token in lowered or lowered in token for token in tokens)


def _join_budget(parts: list[str], limit: int) -> str:
    if limit <= 0:
        return "... truncated"
    kept: list[str] = []
    used = 0
    for part in parts:
        extra = 2 if kept else 0
        size = extra + len(part)
        if used + size <= limit:
            kept.append(part)
            used += size
            continue
        remain = limit - used - extra
        if remain > 80:
            kept.append(part[:remain] + "\n... truncated")
        elif kept:
            kept.append("... truncated")
        elif limit > 15:
            kept.append(part[: limit - 15] + "\n... truncated")
        break
    return "\n\n".join(kept)
