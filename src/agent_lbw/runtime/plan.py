from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent_lbw.llm.client import LLMClient
from agent_lbw.progress import timed_complete
from agent_lbw.runtime.related import VersionFloors, read_version_floors

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
_PY_MENTION = re.compile(r"(?i)(?<![.\w-])(?:requires-python|python_requires|python)(?![\w-])")
_STEP_FLOOR = re.compile(
    r"(?i)(?<![.\w-])([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[^\]]*\])?(?:\s*=\s*[\"']?\s*|\s*)"
    r"(>=|==|~=|\^)\s*(\d+\.\d+(?:\.\d+)?)"
)
_PKG_TO = re.compile(
    r"(?i)(?<![.\w-])([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[^\]]*\])?\s*(?:=|:)?\s*[\"']?\s*"
    r"(?:from\s+[0-9.^<>=~]+\s+)?"
    r"to\s+(?:>=|==|~=|\^)?\s*(\d+\.\d+(?:\.\d+)?)"
)
_LOWER_BOUND = re.compile(r"(?:>=|==|~=|\^)\s*(\d+\.\d+(?:\.\d+)?)")
_TO_FLOOR = re.compile(r"(?i)\bto\s+(?:>=|==|~=|\^)?\s*(\d+\.\d+(?:\.\d+)?)")
_PYTHON_NAMES = {"python", "requires-python", "python-requires"}
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
    opinions: str = "",
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
    system = PLAN_SYSTEM
    extra = (opinions or "").strip()
    if extra:
        system = f"{PLAN_SYSTEM}\n\nOpinions for this plan:\n{extra[:4000]}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(parts)},
    ]
    try:
        turn = timed_complete(llm, messages, [], purpose="plan")
    except Exception:  # noqa: BLE001 - a failed plan call must not stop the cycle
        return None
    return parse_plan(turn.text)


def ground_plan(plan: GoalPlan | None, goal: str, root: Path) -> GoalPlan | None:
    """Drop a downgrade or a step that only hides an error, and start from the missing node."""
    steps = []
    if plan is not None:
        steps = [step for step in plan.steps if not _GUARD_STEP.search(step.do)]
    names = _MISSING_NODE.findall(goal or "")
    scene_steps = [step for step in steps if Path(step.file).suffix.lower() in _SCENE_SUFFIXES]
    if names and not scene_steps:
        from agent_lbw.runtime.related import goal_files

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
    floors = read_version_floors(root)
    steps = [step for step in steps if not _lowers_required_floor(step, floors)]
    if not steps:
        return None
    cause = plan.cause if plan is not None else ""
    return GoalPlan(steps[:MAX_PLAN_STEPS], cause=cause)


def _lowers_required_floor(step: PlanStep, floors: VersionFloors) -> bool:
    """True when the version this step would set is older than the manifests."""
    text = f"{step.do}\n{step.check}"
    python_targets = _python_targets(text)
    if floors.python is not None and python_targets and max(python_targets) < floors.python:
        return True
    for name, versions in _package_targets(text).items():
        current = floors.packages.get(name)
        if current is not None and versions and max(versions) < current:
            return True
    return False


def _python_targets(text: str) -> list[tuple[int, ...]]:
    found: list[tuple[int, ...]] = []
    for match in _PY_MENTION.finditer(text):
        target = _chosen_target(text[match.end() : match.end() + 80])
        if target is not None:
            found.append(target)
    return found


def _package_targets(text: str) -> dict[str, list[tuple[int, ...]]]:
    found: dict[str, list[tuple[int, ...]]] = {}
    directed: set[str] = set()
    for match in _PKG_TO.finditer(text):
        key = re.sub(r"[-_.]+", "-", match.group(1)).strip("-").lower()
        if key in _PYTHON_NAMES:
            continue
        parsed = _bounds(match.group(2))
        if not parsed:
            continue
        found[key] = [parsed[0]]
        directed.add(key)
    for match in _STEP_FLOOR.finditer(text):
        key = re.sub(r"[-_.]+", "-", match.group(1)).strip("-").lower()
        if key in _PYTHON_NAMES or key in directed:
            continue
        target = _chosen_target(text[match.start() : match.end() + 48])
        if target is not None:
            found.setdefault(key, []).append(target)
    return found


def _chosen_target(window: str) -> tuple[int, ...] | None:
    """The version a step sets. A 'to' version beats other numbers in the same phrase."""
    to_version = _TO_FLOOR.search(window)
    if to_version:
        parsed = _bounds(to_version.group(1))
        return parsed[0] if parsed else None
    bounds = _bounds(window)
    return max(bounds) if bounds else None


def _bounds(spec: str) -> list[tuple[int, ...]]:
    found: list[tuple[int, ...]] = []
    for raw in _LOWER_BOUND.findall(spec or ""):
        parts = tuple(int(part) for part in raw.split("."))
        if len(parts) >= 2:
            found.append(parts)
    bare = re.fullmatch(r"\d+\.\d+(?:\.\d+)?", (spec or "").strip())
    if bare:
        parts = tuple(int(part) for part in bare.group(0).split("."))
        if len(parts) >= 2:
            found.append(parts)
    return found


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
