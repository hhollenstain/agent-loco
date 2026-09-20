from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent_loco.llm.client import LLMClient
from agent_loco.sandbox import Workspace
from agent_loco.tools.git import is_runtime_artifact, run_git

DIFF_LIMIT = 16_000

REVIEW_SYSTEM = """You are a strict reviewer for an unattended coding agent.

Decide whether the current code change fulfills the stated goal. Passing tests
is not enough. A related, partial, planned, or hidden change is not enough.

Rules:
- If the goal says to remove something, it must actually be gone from the diff.
- If the goal asks for a user-visible control, it must be visible and wired, not
  display:none or otherwise non-functional.
- A summary that claims the work is done does not count unless the diff shows it.
- Set met=true only when a careful reviewer would accept the work as complete.

Reply with ONLY a JSON object:
{"met": true or false, "reason": "one sentence"}
"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class GoalReview:
    met: bool
    reason: str
    raw: str = ""


def is_test_suite_goal(goal: str) -> bool:
    first = goal.strip().splitlines()[0].lower() if goal.strip() else ""
    return first.startswith("make the project's test suite pass")


def collect_work_diff(workspace: Workspace, sha_before: str | None) -> str:
    """Diff of this cycle's work: commits after sha_before plus the working tree."""
    parts: list[str] = []
    status = run_git(workspace, ["status", "--short", "--branch"])
    if status.stdout.strip():
        parts.append(status.stdout.strip())
    diff_args = ["diff"]
    if sha_before:
        diff_args.append(sha_before)
    else:
        diff_args.append("HEAD")
    diff = run_git(workspace, diff_args)
    if diff.stdout.strip():
        parts.append(diff.stdout.strip())
    untracked = run_git(workspace, ["ls-files", "--others", "--exclude-standard"])
    for rel in untracked.stdout.splitlines():
        path = rel.strip()
        if not path or is_runtime_artifact(path):
            continue
        full = workspace.root / path
        if not full.is_file():
            parts.append(f"untracked: {path}")
            continue
        try:
            body = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            parts.append(f"untracked binary: {path}")
            continue
        parts.append(f"--- /dev/null\n+++ b/{path}\n{body}")
    text = "\n\n".join(parts).strip() or "(no diff)"
    if len(text) > DIFF_LIMIT:
        return text[:DIFF_LIMIT] + "\n... truncated"
    return text


def parse_review(text: str | None) -> GoalReview:
    raw = (text or "").strip()
    if not raw:
        return GoalReview(False, "reviewer returned no verdict", "")
    for blob in _candidate_blobs(raw):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "met" not in data:
            continue
        met = _as_bool(data.get("met"))
        if met is None:
            continue
        reason = str(data.get("reason") or data.get("why") or "").strip()
        if not reason:
            reason = "goal met" if met else "goal not met"
        return GoalReview(met, reason, raw)
    return GoalReview(False, 'reviewer did not return a {"met": ...} verdict', raw)


def review_goal(
    llm: LLMClient,
    goal: str,
    *,
    diff: str,
    summary: str,
    tests_passed: bool | None,
) -> GoalReview:
    if is_test_suite_goal(goal) and tests_passed is True:
        return GoalReview(True, "project tests passed after the change")
    user = "\n".join(
        [
            "Goal:",
            goal.strip(),
            "",
            "Agent summary:",
            (summary or "").strip() or "(none)",
            "",
            f"Tests passed: {tests_passed}",
            "",
            "Diff:",
            diff.strip() or "(no diff)",
        ]
    )
    turn = llm.complete(
        [
            {"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": user},
        ],
        [],
    )
    return parse_review(turn.text)


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "met", "done"}:
            return True
        if lowered in {"false", "no", "unmet", "not met"}:
            return False
    return None


def _candidate_blobs(text: str) -> list[str]:
    blobs = [match.group(1).strip() for match in _FENCE.finditer(text)]
    blobs.extend(_embedded_json_objects(text))
    stripped = text.strip()
    if stripped.startswith("{"):
        blobs.append(stripped)
    return blobs


def _embedded_json_objects(text: str) -> list[str]:
    blobs: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "{":
            index += 1
            continue
        end = _match_json_object(text, index)
        if end is None:
            index += 1
            continue
        blobs.append(text[index:end])
        index = end
    return blobs


def _match_json_object(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None
