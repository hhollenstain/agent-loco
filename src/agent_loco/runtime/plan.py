from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent_loco.llm.client import LLMClient
from agent_loco.progress import timed_complete

PLAN_SYSTEM = """You break one coding goal into ordered steps before anyone edits files.

First name the cause. Compare what the failing code expects with what the
files you are given actually contain: node paths and their parents, file
paths, keys, routes, and fields. Say exactly which part differs. If the
evidence lists parts that draw nothing, the cause includes them.

Then list the steps that fix it. Every step changes one file and says what
to change, except the last step, which re-runs the check that proves the
whole goal (run_tests, or review_ui for a screen). Do not add a step that only
reads, checks, or verifies something the files below already show. Each step
has a check that proves it: a test that must pass, an error that must stop
printing, or what the screen must show. Cover every part of the goal. Use 2 to
6 steps. A null check, print, or log that hides an error is never a step.

Reply with ONLY this JSON:
{"cause": "...", "steps": [{"do": "...", "file": "...", "check": "..."}]}
"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_GUARD_STEP = re.compile(
    r"null check|get_node_or_null|\bprint\b|\blogging\b|log the error|hide the error",
    re.IGNORECASE,
)
_MISSING_NODE = re.compile(r'(?i)not found:?\s*"([^"]+)"')
_SCENE_SUFFIXES = {".tscn", ".tres", ".scn"}
MAX_PLAN_STEPS = 6


@dataclass
class PlanStep:
    do: str
    file: str = ""
    check: str = ""


@dataclass
class GoalPlan:
    steps: list[PlanStep] = field(default_factory=list)
    cause: str = ""


def parse_plan(text: str | None) -> GoalPlan | None:
    raw = (text or "").strip()
    blobs = [match.group(1).strip() for match in _FENCE.finditer(raw)]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        blobs.append(raw[start : end + 1])
    for blob in blobs:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        items = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        steps: list[PlanStep] = []
        for item in items[:MAX_PLAN_STEPS]:
            if not isinstance(item, dict):
                continue
            action = str(item.get("do") or item.get("step") or "").strip()
            if not action:
                continue
            steps.append(
                PlanStep(
                    do=action[:300],
                    file=str(item.get("file") or "").strip()[:200],
                    check=str(item.get("check") or "").strip()[:300],
                )
            )
        if steps:
            return GoalPlan(steps, cause=str(data.get("cause") or "").strip()[:500])
    return None


def plan_goal(
    llm: LLMClient,
    goal: str,
    *,
    context: str = "",
    files: str = "",
    evidence: str = "",
    failure: str = "",
    previous: GoalPlan | None = None,
    diff: str = "",
) -> GoalPlan | None:
    """Ask the model for ordered steps, or a new plan after the last one failed."""
    parts = ["Goal:", goal.strip()]
    if files:
        parts.extend(["", files])
    if evidence:
        parts.extend(["", "Before any edit:", evidence.strip()[:4000]])
    if context:
        parts.extend(["", "Workspace context:", context.strip()[:6000]])
    if previous is not None and failure:
        parts.extend(
            [
                "",
                "The last plan was carried out and the goal is still not met.",
                format_plan(previous),
                "",
                "Why it is not met:",
                failure.strip(),
                "",
                "Current diff:",
                (diff or "(no diff)")[:6000],
                "",
                "Make a new plan that fixes the cause this review names. Do not repeat "
                "a step that already ran and did not help.",
            ]
        )
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]
    try:
        turn = timed_complete(llm, messages, [], purpose="plan")
    except Exception:  # noqa: BLE001 - a failed plan call must not stop the cycle
        return None
    return parse_plan(turn.text)


def ground_plan(plan: GoalPlan | None, goal: str, root: Path) -> GoalPlan | None:
    """Drop steps that only hide an error, and start from the file that owns a missing node."""
    steps = []
    if plan is not None:
        steps = [step for step in plan.steps if not _GUARD_STEP.search(step.do)]
    names = _MISSING_NODE.findall(goal or "")
    scene_steps = [step for step in steps if Path(step.file).suffix.lower() in _SCENE_SUFFIXES]
    if names and not scene_steps:
        from agent_loco.runtime.related import goal_files

        scene = ""
        workspace = Path(root).resolve()
        for path in goal_files(workspace, goal):
            if path.suffix == ".tscn":
                scene = path.relative_to(workspace).as_posix()
                break
        name = names[0]
        where = scene or "the scene file"
        steps.insert(
            0,
            PlanStep(
                do=(
                    f'The error says "{name}" is missing. Change {where} so that node '
                    "exists where the script looks it up, or change the lookup to the "
                    "path the scene already uses. Do not add a null check."
                ),
                file=scene,
                check=f"The Node not found error for {name} no longer prints",
            ),
        )
    if not steps:
        return None
    cause = plan.cause if plan is not None else ""
    return GoalPlan(steps[:MAX_PLAN_STEPS], cause=cause)


def format_plan(plan: GoalPlan | None) -> str:
    if plan is None or not plan.steps:
        return ""
    lines = ["Plan:"]
    if plan.cause:
        lines.append(f"Cause: {plan.cause}")
    for index, step in enumerate(plan.steps, start=1):
        line = f"{index}. {step.do}"
        if step.file:
            line += f" [{step.file}]"
        if step.check:
            line += f" Check: {step.check}"
        lines.append(line)
    return "\n".join(lines)
