from __future__ import annotations

import json
import re
from typing import Any

from agent_loco.llm.client import ToolCall

_FENCE = re.compile(r"```(?:json|tool|tool_call)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_XML = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_FUNCTION = re.compile(
    r"<function\s*=\s*([A-Za-z0-9_]+)>(.*?)</function>",
    re.DOTALL | re.IGNORECASE,
)
_PARAMETER = re.compile(
    r"<parameter\s*=\s*([A-Za-z0-9_]+)>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_ARG_PAIR = re.compile(
    r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>(.*?)</arg_value>",
    re.DOTALL | re.IGNORECASE,
)


def parse_tool_calls(text: str | None, known_names: set[str]) -> list[ToolCall]:
    """Recover tool calls from models that emit JSON or Qwen XML instead of native tool_calls."""
    if not text:
        return []
    calls: list[ToolCall] = []
    seen: set[tuple[str, str]] = set()

    def add(call: ToolCall | None) -> None:
        if not call:
            return
        key = (call.name, json.dumps(call.arguments, sort_keys=True))
        if key in seen:
            return
        seen.add(key)
        calls.append(call)

    for call in _xml_function_calls(text, known_names):
        add(call)
    for index, blob in enumerate(_XML.finditer(text)):
        add(_arg_key_call(blob.group(1), known_names, f"xml-args-{index}"))
    for index, blob in enumerate(_candidate_blobs(text)):
        parsed = _as_object(blob)
        if parsed is None:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            add(_to_tool_call(item, known_names, f"parsed-{index}-{len(calls)}"))
    return calls


def _xml_function_calls(text: str, known_names: set[str]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index, match in enumerate(_FUNCTION.finditer(text)):
        name = match.group(1)
        if name not in known_names:
            continue
        arguments: dict[str, Any] = {}
        for param in _PARAMETER.finditer(match.group(2)):
            arguments[param.group(1)] = _xml_text(param.group(2))
        calls.append(ToolCall(id=f"xml-{index}", name=name, arguments=arguments))
    return calls


def _arg_key_call(blob: str, known_names: set[str], fallback_id: str) -> ToolCall | None:
    lines = [line.strip() for line in blob.strip().splitlines() if line.strip()]
    if not lines or lines[0] not in known_names:
        return None
    arguments = {
        _xml_text(match.group(1)): _xml_text(match.group(2)) for match in _ARG_PAIR.finditer(blob)
    }
    if not arguments:
        return None
    return ToolCall(id=fallback_id, name=lines[0], arguments=arguments)


def _xml_text(value: str) -> str:
    if value.startswith("\n"):
        value = value[1:]
    if value.endswith("\n"):
        value = value[:-1]
    return value


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
