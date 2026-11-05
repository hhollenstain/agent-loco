"""The engineer dashboard is a sibling host. The organization site stays put."""

from __future__ import annotations

_LOCAL = {"localhost", "127.0.0.1", "testserver", "::1"}


def host_name(value: str) -> tuple[str, str]:
    """Return the hostname and port from a Host header."""
    text = value.strip().lower()
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            rest = text[end + 1 :]
            port = rest[1:] if rest.startswith(":") and rest[1:].isdigit() else ""
            return text[1:end], port
    name, sep, port = text.rpartition(":")
    if sep and port.isdigit():
        return name, port
    return text, ""


def is_crossing_host(value: str) -> bool:
    name, _port = host_name(value)
    first = name.split(".", 1)[0]
    return first == "crossing" or first.startswith("crossing-")


def organization_name(value: str) -> str:
    """The organization site for an engineer host. Other hosts stay as they are."""
    name, _port = host_name(value)
    first, _, rest = name.partition(".")
    if first == "crossing" and rest:
        return f"www.{rest}"
    if first.startswith("crossing-") and len(first) > len("crossing-"):
        bare = first[len("crossing-") :]
        label = f"www-{bare}"
        return f"{label}.{rest}" if rest else label
    return name


def crossing_url(value: str, *, scheme: str) -> str:
    """Where an LBW engineer opens the dashboard. A local host stays on this origin."""
    name, port = host_name(value)
    if not name or name in _LOCAL or _is_ip(name):
        return "/crossingguard"
    if is_crossing_host(name):
        return "/"
    labels = name.split(".")
    first = labels[0]
    if first == "www":
        labels[0] = "crossing"
    elif first.startswith("www-") and len(first) > len("www-"):
        labels[0] = "crossing-" + first[len("www-") :]
    else:
        labels[0] = f"crossing-{first}"
    host = ".".join(labels)
    if port:
        host = f"{host}:{port}"
    return f"{scheme}://{host}/"


def github_callback_url(value: str, *, scheme: str) -> str:
    """OAuth returns to the organization site. The engineer host is not the callback."""
    name, port = host_name(value)
    org = organization_name(name) if name else "localhost"
    if port:
        org = f"{org}:{port}"
    return f"{scheme}://{org}/api/github/callback"


def _is_ip(name: str) -> bool:
    parts = name.split(".")
    return len(parts) == 4 and all(part.isdigit() for part in parts)
