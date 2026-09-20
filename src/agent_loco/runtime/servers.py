from __future__ import annotations

import json
from pathlib import Path

from agent_loco.llm.client import normalize_model_base_url
from agent_loco.runtime.project import ensure_run_gitignore

MAX_SERVERS = 20
SERVERS_FILENAME = "servers.json"


def servers_path(root: Path) -> Path:
    return Path(root) / ".loco" / SERVERS_FILENAME


def load_servers(root: Path) -> list[str]:
    """Return remembered LLM server URLs, most recently used first."""
    path = servers_path(root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        items = payload.get("servers")
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    if not isinstance(items, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        try:
            url = normalize_model_base_url(item)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def list_known_servers(root: Path, *, default: str | None = None) -> list[str]:
    servers = load_servers(root)
    if not default:
        return servers
    try:
        fallback = normalize_model_base_url(default)
    except ValueError:
        return servers
    if fallback not in servers:
        servers.append(fallback)
    return servers


def remember_server(
    root: Path,
    url: str,
    *,
    default: str | None = None,
) -> list[str]:
    """Record a used LLM server and return the updated newest-first list."""
    resolved = normalize_model_base_url(url)
    existing = [item for item in load_servers(root) if item != resolved]
    servers = [resolved, *existing]
    if default:
        try:
            fallback = normalize_model_base_url(default)
        except ValueError:
            fallback = None
        if fallback and fallback not in servers:
            servers.append(fallback)
    servers = servers[:MAX_SERVERS]
    path = servers_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_run_gitignore(root)
    path.write_text(
        json.dumps({"servers": servers}, indent=2) + "\n",
        encoding="utf-8",
    )
    return servers
