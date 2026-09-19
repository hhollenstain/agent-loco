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
    seen: set[tuple[str, str]] = set()
    for index, blob in enumerate(blobs):
        parsed = _as_object(blob)
        if parsed is None:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            call = _to_tool_call(item, known_names, f"parsed-{index}-{len(calls)}")
            if not call:
                continue
            key = (call.name, json.dumps(call.arguments, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            calls.append(call)
    return calls


def _candidate_blobs(text: str) -> list[str]:
    blobs = [match.group(1) for match in _XML.finditer(text)]
    blobs.extend(match.group(1) for match in _FENCE.finditer(text))
    blobs.extend(_embedded_json_objects(text))
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        blobs.append(stripped)
    return blobs


def _embedded_json_objects(text: str) -> list[str]:
    """Pull `{...}` objects out of prose so planned tool calls still execute."""
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
