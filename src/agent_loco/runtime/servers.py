from __future__ import annotations

import json
from pathlib import Path

from agent_loco.llm.client import normalize_model_base_url
from agent_loco.runtime.project import ensure_run_gitignore

MAX_SERVERS = 20
SERVERS_FILENAME = "servers.json"


def servers_path(root: Path) -> Path:
    return Path(root) / ".loco" / SERVERS_FILENAME


def _read_payload(root: Path) -> dict:
    path = servers_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {"servers": payload}


def _server_list(payload: dict) -> list[str]:
    items = payload.get("servers")
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


def load_servers(root: Path) -> list[str]:
    """Return remembered LLM server URLs, most recently used first."""
    return _server_list(_read_payload(root))


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


def load_selection(
    root: Path,
    *,
    default_url: str | None = None,
    default_model: str | None = None,
) -> dict[str, str | list[str] | None]:
    """Return remembered servers plus the last used host and model."""
    payload = _read_payload(root)
    servers = list_known_servers(root, default=default_url)
    last_url = payload.get("last_base_url")
    if isinstance(last_url, str) and last_url.strip():
        try:
            last_url = normalize_model_base_url(last_url)
        except ValueError:
            last_url = None
    else:
        last_url = None
    if not last_url:
        last_url = servers[0] if servers else default_url
    last_model = payload.get("last_model")
    if not isinstance(last_model, str) or not last_model.strip():
        last_model = default_model
    else:
        last_model = last_model.strip()
    return {
        "servers": servers,
        "last_base_url": last_url,
        "last_model": last_model,
    }


def remember_server(
    root: Path,
    url: str,
    *,
    model: str | None = None,
    default: str | None = None,
) -> list[str]:
    """Record a used LLM server (and optional model) and return the host list."""
    payload = _read_payload(root)
    resolved = normalize_model_base_url(url)
    existing = [item for item in _server_list(payload) if item != resolved]
    servers = [resolved, *existing]
    if default:
        try:
            fallback = normalize_model_base_url(default)
        except ValueError:
            fallback = None
        if fallback and fallback not in servers:
            servers.append(fallback)
    servers = servers[:MAX_SERVERS]
    saved: dict[str, object] = {
        "servers": servers,
        "last_base_url": resolved,
    }
    chosen = (model or "").strip() or (
        payload.get("last_model") if isinstance(payload.get("last_model"), str) else ""
    )
    if chosen:
        saved["last_model"] = chosen.strip()
    path = servers_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_run_gitignore(root)
    path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    return servers
