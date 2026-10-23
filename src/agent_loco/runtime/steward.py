from __future__ import annotations

import json
import re

from agent_loco.llm.client import LLMClient
from agent_loco.progress import record_event, timed_complete
from agent_loco.runtime.project import ProjectConfig, collect_context
from agent_loco.sandbox import Workspace
from agent_loco.tools.git import run_git

_NOTHING = re.compile(r"\bNOTHING_TO_DO\b", re.IGNORECASE)
_GOAL_LINE = re.compile(r"^GOAL:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_PATH = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|[\w.-]+\.(?:py|html|css|js|ts|md)")
_TOKEN = re.compile(r"[a-z0-9]{4,}")
_DEF = re.compile(r"(?m)^(?:async )?def \w+\(")
_STOP = frozenset(
    {
        "file",
        "files",
        "code",
        "improve",
        "improvement",
        "add",
        "update",
        "make",
        "this",
        "that",
        "with",
        "from",
        "into",
        "current",
        "clarity",
        "maintainability",
    }
)

PROPOSE_SYSTEM = """You review a codebase and pick the next small improvement.

Choose ONE concrete change that improves best practices or user experience.
It must be finishable in a single agent cycle. Prefer a real gap: missing
behavior, a UX hole, a broken command, or a test that does not exist.

Do not repeat a change listed under Recent commits.
Do not propose a docstring or type-hint sweep. If a file already has them,
that work is done, and rewriting List/Dict or adding another docstring will
be reverted by the linter.
Do not invent a new architecture.

Reply with:
GOAL: <one-line imperative>
then a short spec (files to touch and why).

If the repo is already in good shape, or every idea is already done, reply
with NOTHING_TO_DO and nothing else.
"""

VALIDATE_SYSTEM = """You check a proposed next step against the current code.

Accept it only when the change is not already in the files or the recent
commits, and it is one small real gap. Reject a docstring or type-hint sweep
on a file that already has them.

Reply with ONLY a JSON object:
{"accept": true or false, "reason": "one sentence", "goal": ""}

When accept is false, goal is a different one-line improvement, or empty
when nothing else is worth doing.
"""


def parse_proposed_goal(text: str | None) -> str | None:
    """Turn a steward proposal into a cycle goal, or None when there is no work."""
    raw = (text or "").strip()
    if not raw or _NOTHING.search(raw):
        return None
    match = _GOAL_LINE.search(raw)
    if match:
        headline = match.group(1).strip()
        rest = raw[match.end() :].strip()
        if rest:
            return f"{headline}\n\n{rest}"
        return headline or None
    return raw[:4000]


def repeats_recent_work(goal: str, recent_commits: str) -> bool:
    """True when the proposal is the same change as a recent commit subject."""
    tokens = _headline_tokens(goal)
    if len(tokens) < 2:
        return False
    for line in (recent_commits or "").splitlines():
        subject = line.split(" ", 1)[-1] if " " in line else line
        subject_tokens = set(_TOKEN.findall(subject.lower())) - _STOP
        if not subject_tokens:
            continue
        overlap = tokens & subject_tokens
        if len(overlap) >= 2 and len(overlap) / len(tokens) >= 0.6:
            return True
    return False


def style_sweep_already_done(goal: str, workspace: Workspace) -> bool:
    """True when the proposal only restyles a file that already has that style."""
    kind = _style_kind(goal)
    if not kind:
        return False
    paths = _existing_paths(workspace, goal)
    if not paths:
        return False
    return all(
        _file_already(kind, path.read_text(encoding="utf-8", errors="replace")) for path in paths
    )


def propose_improvement_goal(
    llm: LLMClient,
    workspace: Workspace,
    project: ProjectConfig,
    *,
    allow_publish: bool,
) -> str | None:
    """Pick one next step, then reject it when the tree already has that change."""
    context = collect_context(workspace.root, project, allow_publish=allow_publish)
    recent = _recent_commits(workspace)
    goal = _ask_for_goal(llm, context, recent, rejected="")
    goal = _drop_if_already_done(llm, workspace, context, recent, goal)
    if goal is None:
        return None
    record_event(kind="step", message="Checking the proposed step against the current code...")
    accepted, replacement = _review_step(llm, goal, workspace, recent)
    if accepted:
        return goal
    replacement = (replacement or "").strip()
    if not replacement or _already_done(replacement, workspace, recent):
        record_event(
            kind="step",
            message="That step is already done. No other improvement was proposed.",
        )
        return None
    record_event(kind="step", message=f"Next step: {replacement.splitlines()[0][:160]}")
    return replacement


def _ask_for_goal(llm: LLMClient, context: str, recent: str, rejected: str) -> str | None:
    parts = [
        context,
        "",
        "Recent commits:",
        recent or "(none)",
        "",
        "Pick the next improvement. One change only.",
    ]
    if rejected:
        parts.extend(["", f"Do not pick this again. It is already done:\n{rejected.strip()}"])
    turn = timed_complete(
        llm,
        [
            {"role": "system", "content": PROPOSE_SYSTEM},
            {"role": "user", "content": "\n".join(parts)},
        ],
        [],
        purpose="propose",
    )
    return parse_proposed_goal(turn.text)


def _drop_if_already_done(
    llm: LLMClient,
    workspace: Workspace,
    context: str,
    recent: str,
    goal: str | None,
) -> str | None:
    if goal is None or not _already_done(goal, workspace, recent):
        return goal
    record_event(
        kind="step",
        message="Proposed step repeats work already in the tree. Picking another.",
    )
    again = _ask_for_goal(llm, context, recent, rejected=goal)
    if again is None or _already_done(again, workspace, recent):
        record_event(kind="step", message="No remaining improvement that is not already done.")
        return None
    return again


def _review_step(
    llm: LLMClient,
    goal: str,
    workspace: Workspace,
    recent: str,
) -> tuple[bool, str]:
    excerpts = _excerpts(workspace, goal)
    user = "\n\n".join(
        part
        for part in (
            f"Proposed step:\n{goal.strip()}",
            f"Recent commits:\n{recent or '(none)'}",
            f"Current files:\n{excerpts or '(no file excerpt)'}",
        )
        if part
    )
    turn = timed_complete(
        llm,
        [
            {"role": "system", "content": VALIDATE_SYSTEM},
            {"role": "user", "content": user},
        ],
        [],
        purpose="propose",
    )
    return _parse_step_review(turn.text)


def _parse_step_review(text: str | None) -> tuple[bool, str]:
    raw = (text or "").strip()
    if not raw:
        return True, ""
    if _NOTHING.search(raw):
        return False, ""
    for blob in _json_blobs(raw):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "accept" not in data:
            continue
        accept = data.get("accept")
        if isinstance(accept, str):
            accept = accept.strip().lower() in {"true", "yes", "1"}
        if not isinstance(accept, bool):
            continue
        replacement = str(data.get("goal") or "").strip()
        if _NOTHING.search(replacement):
            replacement = ""
        return accept, replacement
    return True, ""


def _json_blobs(raw: str) -> list[str]:
    blobs = []
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        blobs.append(raw[start : end + 1])
    return blobs


def _already_done(goal: str, workspace: Workspace, recent: str) -> bool:
    return repeats_recent_work(goal, recent) or style_sweep_already_done(goal, workspace)


def _recent_commits(workspace: Workspace) -> str:
    if not (workspace.root / ".git").exists():
        return ""
    result = run_git(workspace, ["log", "-12", "--oneline"])
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _headline_tokens(goal: str) -> set[str]:
    headline = (goal or "").strip().splitlines()[0] if goal else ""
    return set(_TOKEN.findall(headline.lower())) - _STOP


def _style_kind(goal: str) -> str:
    lowered = (goal or "").lower()
    if "type hint" in lowered:
        return "hints"
    if "docstring" in lowered:
        return "docs"
    return ""


def _file_already(kind: str, text: str) -> bool:
    if kind == "hints":
        defs = _DEF.findall(text)
        arrows = text.count("->")
        return len(defs) >= 2 and arrows >= 2
    pairs = text.count('"""') // 2
    return pairs >= 2


def _existing_paths(workspace: Workspace, goal: str) -> list:
    found = []
    seen: set[str] = set()
    for match in _PATH.findall(goal or ""):
        rel = match.strip().lstrip("./")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        path = workspace.root / rel
        if path.is_file():
            found.append(path)
        if len(found) >= 3:
            break
    return found


def _excerpts(workspace: Workspace, goal: str) -> str:
    parts: list[str] = []
    for path in _existing_paths(workspace, goal):
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(body) > 2500:
            body = body[:2500] + "\n... truncated"
        rel = path.relative_to(workspace.root).as_posix()
        parts.append(f"{rel}:\n{body}")
    return "\n\n".join(parts)
