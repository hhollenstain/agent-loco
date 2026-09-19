from __future__ import annotations

import json
import re
from typing import Any

from agent_loco.llm.client import ToolCall

_FENCE = re.compile(r"```(?:json|tool|tool_call)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_XML = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)


def parse_tool_calls(text: str | None, known_names: set[str]) -> list[ToolCall]:
    """Recover tool calls from models that emit JSON instead of native tool_calls."""
    if not text:
        return []
    blobs = _candidate_blobs(text)
    calls: list[ToolCall] = []
    for index, blob in enumerate(blobs):
        parsed = _as_object(blob)
        if parsed is None:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            call = _to_tool_call(item, known_names, f"parsed-{index}-{len(calls)}")
            if call:
                calls.append(call)
    return calls


def _candidate_blobs(text: str) -> list[str]:
    blobs = [match.group(1) for match in _XML.finditer(text)]
    blobs.extend(match.group(1) for match in _FENCE.finditer(text))
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        blobs.append(stripped)
    return blobs


def _as_object(blob: str) -> dict[str, Any] | list[Any] | None:
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict | list):
        return parsed
    return None


def _to_tool_call(item: Any, known_names: set[str], fallback_id: str) -> ToolCall | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("tool")
    if name not in known_names:
        function = item.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            arguments = function.get("arguments", {})
        else:
            return None
    else:
        arguments = item.get("arguments", item.get("args", {}))
    if name not in known_names:
        return None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"_raw": arguments}
    if not isinstance(arguments, dict):
        return None
    return ToolCall(id=str(item.get("id") or fallback_id), name=str(name), arguments=arguments)
