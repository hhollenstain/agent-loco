from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent_loco.llm.client import LLMClient
from agent_loco.progress import record_event, timed_complete

REVIEW_RAW_LIMIT = 8_000

REVIEW_SYSTEM = """You are a strict reviewer for an unattended coding agent.

Decide whether the current code change fulfills the stated goal. Passing tests
is not enough. A related, partial, planned, or hidden change is not enough.

Rules:
- If the goal says to remove something, it must actually be gone from the diff.
- If the goal asks for a user-visible control, it must be visible and wired, not
  display:none or otherwise non-functional.
- When a Rendered UI section is present, JavaScript errors mean the goal is unmet.
  Controls with 0x0 or tiny sizes are unmet. A template diff is not enough if the
  rendered page is blank, crushed, or throws.
- A summary that claims the work is done does not count unless the diff shows it.
- Use the changed-file list. A lockfile may be summarized as `package: old -> new`
  instead of a hash dump; that still counts as updating the package.
- Do not infer that a dependency was not updated just because hashes were omitted.
- Set met=true only when a careful reviewer would accept the work as complete.

Reply with ONLY a JSON object:
{"met": true or false, "reason": "one sentence"}
"""

EXISTING_REVIEW_SYSTEM = """You are a strict reviewer for an unattended coding agent.

The agent made no file changes this cycle. Decide whether the CURRENT workspace
already fulfills the stated goal.

Rules:
- Passing tests is not enough by itself.
- Use the current evidence: dependency versions, manifests, and files.
- When a Rendered UI section is present, JavaScript errors mean the goal is unmet.
  Controls with 0x0 or tiny sizes are unmet. A missing diff does not save a
  broken page.
- A missing diff does not mean the goal is unmet if the tree already has the
  requested result (for example a lockfile already on the requested version).
- Set met=true only if a careful reviewer would accept the current tree as complete.
- Set met=false if the goal still requires work.

Reply with ONLY a JSON object:
{"met": true or false, "reason": "one sentence"}
"""

REVIEW_JSON_NUDGE = (
    "Your previous reply was not valid. Reply with ONLY this JSON object and "
    'no other text:\n{"met": true or false, "reason": "one sentence"}'
)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class GoalReview:
    met: bool
    reason: str
    raw: str = ""
    parsed: bool = True


def is_test_suite_goal(goal: str) -> bool:
    first = goal.strip().splitlines()[0].lower() if goal.strip() else ""
    return first.startswith("make the project's test suite pass")


def parse_review(text: str | None) -> GoalReview:
    raw = (text or "").strip()
    if not raw:
        return GoalReview(False, "reviewer returned no verdict", "", parsed=False)
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
        return GoalReview(met, reason, raw, parsed=True)
    return GoalReview(
        False,
        'reviewer did not return a {"met": ...} verdict',
        raw,
        parsed=False,
    )


def review_goal(
    llm: LLMClient,
    goal: str,
    *,
    diff: str,
    summary: str,
    tests_passed: bool | None,
    existing: bool = False,
    upstream: str | None = None,
    ui_evidence: str | None = None,
) -> GoalReview:
    if is_test_suite_goal(goal) and tests_passed is True:
        verdict = GoalReview(True, "project tests passed after the change")
        _record_review(verdict, attempt=1)
        return verdict
    evidence_label = "Current workspace:" if existing else "Diff:"
    user_parts = [
        "Goal:",
        goal.strip(),
        "",
        "Agent summary:",
        (summary or "").strip() or "(none)",
        "",
        f"Tests passed: {tests_passed}",
    ]
    if existing:
        user_parts.extend(
            [
                "",
                "This cycle produced no file changes.",
            ]
        )
        if upstream:
            user_parts.extend(["", "Upstream:", upstream])
    user_parts.extend(["", evidence_label, diff.strip() or "(no diff)"])
    if ui_evidence and ui_evidence.strip():
        user_parts.extend(["", ui_evidence.strip()])
    user = "\n".join(user_parts)
    messages: list[dict] = [
        {
            "role": "system",
            "content": EXISTING_REVIEW_SYSTEM if existing else REVIEW_SYSTEM,
        },
        {"role": "user", "content": user},
    ]
    turn = timed_complete(llm, messages, [], purpose="review")
    verdict = parse_review(turn.text)
    _record_review(verdict, attempt=1)
    if verdict.parsed:
        return verdict
    messages.append({"role": "assistant", "content": turn.text or ""})
    messages.append({"role": "user", "content": REVIEW_JSON_NUDGE})
    turn = timed_complete(llm, messages, [], purpose="review")
    verdict = parse_review(turn.text)
    _record_review(verdict, attempt=2)
    return verdict


def review_reason(verdict: GoalReview) -> str:
    """Human-readable review outcome, including a raw snippet when JSON parse failed."""
    if verdict.parsed or not verdict.raw:
        return verdict.reason
    snippet = " ".join(verdict.raw.split())
    if len(snippet) > 240:
        snippet = snippet[:240] + "..."
    return f"{verdict.reason} ({snippet})"


def _record_review(verdict: GoalReview, *, attempt: int) -> None:
    record_event(
        kind="review",
        attempt=attempt,
        met=verdict.met,
        parsed=verdict.parsed,
        reason=verdict.reason,
        raw=_clip_raw(verdict.raw),
    )


def _clip_raw(text: str, limit: int = REVIEW_RAW_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... truncated"


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
