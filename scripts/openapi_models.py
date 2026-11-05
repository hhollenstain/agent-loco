"""Generate Pydantic models from openapi/lbw.yaml.

The spec is the source. This writes src/agent_lbw/openapi_models.py.
`make openapi` refreshes it. `make openapi-check` fails when the file is stale.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "openapi" / "lbw.yaml"
TARGET = ROOT / "src" / "agent_lbw" / "openapi_models.py"

_TYPES = {"string": "str", "integer": "int", "boolean": "bool", "number": "float"}


def generate(spec: dict) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    if not isinstance(schemas, dict) or not schemas:
        raise SystemExit("openapi/lbw.yaml has no component schemas")
    lines = [
        "from __future__ import annotations",
        "",
        "from pydantic import BaseModel, Field",
        "",
        "# Generated from openapi/lbw.yaml. Run `make openapi` after a spec change.",
        "",
        "",
    ]
    for name, schema in schemas.items():
        lines.extend(_class(name, schema, schemas))
        lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _class(name: str, schema: dict, schemas: dict) -> list[str]:
    if schema.get("type") != "object":
        raise SystemExit(f"{name} is not an object schema")
    required = set(schema.get("required") or [])
    properties = schema.get("properties") or {}
    body = [f"class {name}(BaseModel):"]
    if not properties:
        body.append("    pass")
        return body
    fields: list[str] = []
    for prop, raw in properties.items():
        annotation, default = _field(prop, raw, schemas, prop in required)
        if default is None:
            fields.append(f"    {prop}: {annotation}")
        else:
            fields.append(f"    {prop}: {annotation} = {default}")
    required_lines = [line for line in fields if " = " not in line]
    optional_lines = [line for line in fields if " = " in line]
    body.extend(required_lines + optional_lines)
    return body


def _field(name: str, raw: dict, schemas: dict, required: bool) -> tuple[str, str | None]:
    if "$ref" in raw:
        ref = str(raw["$ref"]).rsplit("/", 1)[-1]
        if ref not in schemas:
            raise SystemExit(f"{name} references unknown schema {ref}")
        return ref, None if required else "None"
    kind = raw.get("type")
    if kind == "array":
        items = raw.get("items") or {}
        if "$ref" in items:
            inner = str(items["$ref"]).rsplit("/", 1)[-1]
        else:
            inner = _TYPES.get(str(items.get("type") or ""), "object")
        annotation = f"list[{inner}]"
        if required and "default" not in raw:
            return annotation, "Field(default_factory=list)"
        return annotation, "Field(default_factory=list)"
    annotation = _TYPES.get(str(kind or ""), "object")
    if required and "default" not in raw:
        return annotation, None
    default = raw.get("default", "" if annotation == "str" else None)
    return annotation, _literal(default)


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if value is None:
        return "None"
    return repr(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    text = generate(spec)
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print("openapi models are stale. Run make openapi.", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
