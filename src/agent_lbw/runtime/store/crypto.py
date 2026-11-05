"""Seal tenant keys, task goals, and job results.

The master key lives in `.lbw/master.key` on the install. The database holds
only a wrapped copy of each tenant key, and ciphertext for a goal or a job
result. A dump of the database does not include the master key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path

_KEY_BYTES = 32
_NONCE_BYTES = 16


def master_key(root: Path) -> bytes:
    """Return this install's master key, creating it the first time."""
    path = Path(root) / ".lbw" / "master.key"
    if path.is_file():
        data = path.read_bytes()
        if len(data) != _KEY_BYTES:
            raise ValueError("master key is unreadable")
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    data = secrets.token_bytes(_KEY_BYTES)
    path.write_bytes(data)
    path.chmod(0o600)
    return data


def seal(key: bytes, plaintext: str) -> str:
    """Encrypt text with key. The result is ASCII and does not contain the text."""
    body = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_BYTES)
    hidden = bytes(
        left ^ right for left, right in zip(body, _stream(key, nonce, len(body)), strict=True)
    )
    mac = hmac.new(key, b"mac" + nonce + hidden, hashlib.sha256).digest()
    return base64.b64encode(nonce + mac + hidden).decode("ascii")


def open_secret(key: bytes, token: str) -> str:
    """Decrypt a value from seal. A wrong key or a changed token fails closed."""
    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("ciphertext is unreadable") from exc
    if len(raw) < _NONCE_BYTES + 32:
        raise ValueError("ciphertext is unreadable")
    nonce, mac, hidden = (
        raw[:_NONCE_BYTES],
        raw[_NONCE_BYTES : _NONCE_BYTES + 32],
        raw[_NONCE_BYTES + 32 :],
    )
    expected = hmac.new(key, b"mac" + nonce + hidden, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("ciphertext is unreadable")
    body = bytes(
        left ^ right for left, right in zip(hidden, _stream(key, nonce, len(hidden)), strict=True)
    )
    return body.decode("utf-8")


def wrap_key(master: bytes, dek: bytes) -> str:
    return seal(master, base64.b64encode(dek).decode("ascii"))


def unwrap_key(master: bytes, wrapped: str) -> bytes:
    raw = base64.b64decode(open_secret(master, wrapped).encode("ascii"), validate=True)
    if len(raw) != _KEY_BYTES:
        raise ValueError("tenant key is unreadable")
    return raw


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    if length == 0:
        return b""
    prk = hmac.new(b"lbw-seal", key, hashlib.sha256).digest()
    return _expand(prk, nonce, length)


def _expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand from RFC 5869, using HMAC-SHA256."""
    out = b""
    block = b""
    counter = 1
    while len(out) < length:
        # The first 255 blocks stay one byte so secrets sealed before this still open.
        suffix = bytes([counter]) if counter < 256 else counter.to_bytes(4, "big")
        block = hmac.new(prk, block + info + suffix, hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]
