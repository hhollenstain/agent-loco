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


def _server_list_for_display(payload: dict) -> list[dict[str, str]]:
    """Return list of server dicts with url and optional alias.

    Each item has:
      - url: normalized URL
      - alias: user-provided alias (string or null)
    """
    items = payload.get("servers")
    if not isinstance(items, list):
        return []
    result: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in items:
        if isinstance(item, str):
            # Legacy format: just a URL string
            try:
                url = normalize_model_base_url(item)
            except ValueError:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            result.append({"url": url, "alias": None})
        elif isinstance(item, dict) and "url" in item:
            # New format: dict with url and optional alias
            url = item.get("url", "")
            alias = item.get("alias")
            if isinstance(url, str) and url.strip():
                try:
                    url = normalize_model_base_url(url)
                except ValueError:
                    continue
                if not isinstance(alias, str):
                    alias = None
                if alias is not None and not alias.strip():
                    alias = None
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                result.append(
                    {
                        "url": url,
                        "alias": alias.strip() if isinstance(alias, str) else None,
                    }
                )
    return result


def load_servers(root: Path) -> list[dict[str, str]]:
    """Return remembered LLM server info (url and optional alias), most recently used first."""
    return _server_list_for_display(_read_payload(root))


def list_known_servers(root: Path, *, default: str | None = None) -> list[dict[str, str]]:
    """Return servers with aliases, including default if not present."""
    servers = load_servers(root)
    if not default:
        return servers
    try:
        fallback = normalize_model_base_url(default)
    except ValueError:
        return servers
    # Check if default URL already in servers
    fallback_url = fallback.strip()
    for server in servers:
        if server.get("url", "").strip() == fallback_url:
            return servers
    # Append default with no alias
    servers.append({"url": fallback, "alias": None})
    return servers


def server_url_to_display(server_url: str) -> str:
    """Convert server info to display string."""
    if isinstance(server_url, dict):
        url = server_url.get("url", "")
        alias = server_url.get("alias")
        if alias:
            return f"{alias} ({url})"
        return url
    return str(server_url)


def load_selection(
    root: Path,
    *,
    default_url: str | None = None,
    default_model: str | None = None,
) -> dict[str, str | list[dict[str, str]] | None]:
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
        last_url = servers[0]["url"] if servers else default_url
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
    alias: str | None = None,
) -> list[dict[str, str]]:
    """Record a used LLM server (and optional model/alias) and return the host list."""
    payload = _read_payload(root)
    resolved = normalize_model_base_url(url)

    # Existing server records
    servers = _server_list_for_display(payload)

    # Remove existing entry with same URL
    existing = [s for s in servers if s.get("url", "").strip() != resolved]

    # Use provided alias or existing alias
    used_alias = alias if alias and alias.strip() else None
    for s in servers:
        if s.get("url", "").strip() == resolved:
            used_alias = s.get("alias") if not used_alias else used_alias
            break
    else:
        used_alias = None

    # Add new entry at the front
    servers = [{"url": resolved, "alias": used_alias}, *existing]

    if default:
        try:
            fallback = normalize_model_base_url(default)
        except ValueError:
            fallback = None
        if fallback and fallback not in [s.get("url", "").strip() for s in servers]:
            servers.append({"url": fallback, "alias": None})

    servers = servers[:MAX_SERVERS]

    saved: dict[str, object] = {"servers": servers}

    if alias and alias.strip():
        saved["last_alias"] = alias.strip()

    saved["last_base_url"] = resolved

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


def update_server_alias(root: Path, url: str, alias: str | None) -> list[dict[str, str]]:
    """Update or add an alias for a server URL."""
    payload = _read_payload(root)
    resolved = normalize_model_base_url(url)
    servers = _server_list_for_display(payload)
    found = False
    for s in servers:
        if s.get("url", "").strip() == resolved.strip():
            s["alias"] = alias.strip() if alias and alias.strip() else None
            found = True
            break
    if not found and alias and alias.strip():
        servers.insert(0, {"url": resolved, "alias": alias.strip()})
    elif not found:
        servers.insert(0, {"url": resolved, "alias": None})

    servers = servers[:MAX_SERVERS]
    saved: dict[str, object] = {"servers": servers}
    saved["last_base_url"] = resolved
    path = servers_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_run_gitignore(root)
    path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    return servers


def create_or_update_server(root: Path, url: str, alias: str) -> list[dict[str, str]]:
    """Create a new server entry with alias only (doesn't update on URL reuse)."""
    payload = _read_payload(root)
    resolved = normalize_model_base_url(url)
    servers = _server_list_for_display(payload)

    # Only add if URL doesn't exist
    for s in servers:
        if s.get("url", "").strip() == resolved.strip():
            return servers

    # Add new server
    servers = [
        {"url": resolved, "alias": alias.strip() if alias and alias.strip() else None},
        *servers,
    ]
    servers = servers[:MAX_SERVERS]

    saved: dict[str, object] = {"servers": servers}
    saved["last_base_url"] = resolved

    path = servers_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_run_gitignore(root)
    path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    return servers
