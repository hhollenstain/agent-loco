"""SSO opens a session for an email that already belongs to the organization."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from lbw_crossing.app import create_app as create_crossing_app

from agent_lbw.config import Settings
from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.sso import _exclusive
from agent_lbw.runtime.tasks import TaskManager
from agent_lbw.web_ui import create_app

_PASSWORD = "correct-horse"
_SECRET = "hunter2"
_ISSUER = "https://login.example.com/tenant"
_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
_DS = "http://www.w3.org/2000/09/xmldsig#"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64int(number: int) -> str:
    length = max((number.bit_length() + 7) // 8, 1)
    return _b64(number.to_bytes(length, "big"))


def _jwt(key: rsa.RSAPrivateKey, *, kid: str, nonce: str, email: str) -> str:
    header = _b64(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    body = _b64(
        json.dumps(
            {
                "iss": _ISSUER,
                "aud": "lbw-app",
                "nonce": nonce,
                "email": email,
                "exp": int(time.time()) + 600,
            }
        ).encode()
    )
    signing = f"{header}.{body}".encode()
    signature = key.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64(signature)}"


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _certificate(key: rsa.RSAPrivateKey) -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.com")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    return base64.b64encode(der).decode("ascii")


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _signed_assertion(key: rsa.RSAPrivateKey, email: str) -> str:
    assertion = ElementTree.Element(_tag(_SAML, "Assertion"))
    subject = ElementTree.SubElement(assertion, _tag(_SAML, "Subject"))
    name = ElementTree.SubElement(subject, _tag(_SAML, "NameID"))
    name.text = email
    signature = ElementTree.SubElement(assertion, _tag(_DS, "Signature"))
    info = ElementTree.SubElement(signature, _tag(_DS, "SignedInfo"))
    method = ElementTree.SubElement(info, _tag(_DS, "SignatureMethod"))
    method.set("Algorithm", "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")
    reference = ElementTree.SubElement(info, _tag(_DS, "Reference"))
    digest_value = ElementTree.SubElement(reference, _tag(_DS, "DigestValue"))
    digest_value.text = base64.b64encode(
        hashlib.sha256(_exclusive(assertion, skip_signature=True)).digest()
    ).decode("ascii")
    value = ElementTree.SubElement(signature, _tag(_DS, "SignatureValue"))
    signed = key.sign(_exclusive(info, skip_signature=False), padding.PKCS1v15(), hashes.SHA256())
    value.text = base64.b64encode(signed).decode("ascii")
    return ElementTree.tostring(assertion, encoding="unicode")


def test_sso_callback_matches_an_existing_email(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "k1",
        "alg": "RS256",
        "n": _b64int(numbers.n),
        "e": _b64int(numbers.e),
    }
    box = {"token": ""}
    seen = {"secret": ""}

    def fake_get(url: str, timeout: float = 20):
        if url.endswith("/.well-known/openid-configuration"):
            return _Response(
                200,
                {
                    "issuer": _ISSUER,
                    "authorization_endpoint": "https://login.example.com/authorize",
                    "token_endpoint": "https://login.example.com/token",
                    "jwks_uri": "https://login.example.com/jwks",
                },
            )
        if url.endswith("/jwks"):
            return _Response(200, {"keys": [jwk]})
        return _Response(404, {})

    def fake_post(
        url: str, data: dict | None = None, headers: dict | None = None, timeout: float = 20
    ):
        assert data is not None
        seen["secret"] = str(data.get("client_secret") or "")
        assert _SECRET not in url
        return _Response(200, {"id_token": box["token"]})

    monkeypatch.setattr("agent_lbw.runtime.store.sso.httpx.get", fake_get)
    monkeypatch.setattr("agent_lbw.runtime.store.sso.httpx.post", fake_post)
    app = create_app(TaskManager(settings), default_workspace=tmp_path)
    owner = TestClient(app)
    created = owner.post(
        "/api/auth/setup",
        json={"name": "Ada", "email": "ada@example.com", "password": _PASSWORD},
    )
    assert created.status_code == 200
    tenant_id = created.json()["org"]["id"]
    added = owner.post(
        "/api/account/users",
        json={"name": "Grace", "email": "grace@example.com"},
    )
    grace = next(user for user in added.json()["users"] if user["name"] == "Grace")
    board = TestClient(
        create_crossing_app(
            Path(app.state.ui.store_root), app.state.ui.manager.settings.database_url
        )
    )
    board.cookies.update(owner.cookies)
    saved = board.put(
        "/api/crossingguard/sign-in",
        json={
            "tenant_id": tenant_id,
            "provider": "entra",
            "issuer": _ISSUER,
            "client_id": "lbw-app",
            "client_secret": _SECRET,
        },
    )
    assert saved.status_code == 200
    assert _SECRET not in saved.text

    def callback(email: str) -> object:
        guest = TestClient(app)
        started = guest.get("/api/auth/sso", follow_redirects=False)
        assert started.status_code == 303
        location = started.headers["location"]
        assert _SECRET not in location
        query = parse_qs(urlparse(location).query)
        box["token"] = _jwt(key, kid="k1", nonce=query["nonce"][0], email=email)
        return guest.get(
            "/api/auth/sso/callback",
            params={"code": "the-code", "state": query["state"][0]},
            follow_redirects=False,
        )

    done = callback("grace@example.com")
    assert done.status_code == 303
    assert done.headers["location"] == "/"
    assert _SECRET not in done.text
    assert _SECRET not in done.headers["location"]
    assert seen["secret"] == _SECRET
    assert "lbw_session" in done.cookies
    home = TestClient(app)
    home.cookies.set("lbw_session", done.cookies["lbw_session"])
    account = home.get("/api/account")
    assert account.status_code == 200
    assert account.json()["current_user_id"] == grace["id"]

    with database(tmp_path) as db:
        db.execute("UPDATE identities SET locked = 1 WHERE id = ?", (grace["id"],))
        before = db.fetchone("SELECT COUNT(*) AS n FROM identities")
    locked = callback("Grace@Example.com")
    assert locked.status_code == 400
    assert locked.json()["error"] == "this account is locked"
    assert _SECRET not in locked.text
    assert "lbw_session" not in locked.cookies

    unknown = callback("nobody@example.com")
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "email or password is wrong"
    assert "lbw_session" not in unknown.cookies
    with database(tmp_path) as db:
        after = db.fetchone("SELECT COUNT(*) AS n FROM identities")
    assert after["n"] == before["n"]

    box["token"] = _jwt(key, kid="k1", nonce="wrong", email="ada@example.com")
    guest = TestClient(app)
    started = guest.get("/api/auth/sso", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    bad = guest.get(
        "/api/auth/sso/callback",
        params={"code": "the-code", "state": state},
        follow_redirects=False,
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == "email or password is wrong"
    assert "lbw_session" not in bad.cookies

    password = TestClient(app)
    still = password.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": _PASSWORD},
    )
    assert still.status_code == 200
    assert still.json()["current_user_id"] == created.json()["current_user_id"]


def test_saml_callback_checks_the_assertion(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = _certificate(key)
    metadata = (
        "<EntityDescriptor>"
        '<SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"'
        ' Location="https://idp.example.com/sso"/>'
        f"<X509Certificate>{cert}</X509Certificate>"
        "</EntityDescriptor>"
    ).encode()

    class _Body:
        def __init__(self, status: int, content: bytes) -> None:
            self.status_code = status
            self.content = content

    def fake_get(url: str, timeout: float = 20):
        assert url == "https://idp.example.com/metadata"
        return _Body(200, metadata)

    monkeypatch.setattr("agent_lbw.runtime.store.sso.httpx.get", fake_get)
    app = create_app(TaskManager(settings), default_workspace=tmp_path)
    owner = TestClient(app)
    created = owner.post(
        "/api/auth/setup",
        json={"name": "Ada", "email": "ada@example.com", "password": _PASSWORD},
    )
    tenant_id = created.json()["org"]["id"]
    board = TestClient(
        create_crossing_app(
            Path(app.state.ui.store_root), app.state.ui.manager.settings.database_url
        )
    )
    board.cookies.update(owner.cookies)
    saved = board.put(
        "/api/crossingguard/sign-in",
        json={
            "tenant_id": tenant_id,
            "provider": "saml",
            "issuer": "https://idp.example.com",
            "metadata_url": "https://idp.example.com/metadata",
            "client_id": "lbw-app",
        },
    )
    assert saved.status_code == 200
    assert cert not in saved.text

    guest = TestClient(app)
    started = guest.get("/api/auth/sso", follow_redirects=False)
    assert started.status_code == 303
    location = started.headers["location"]
    assert location.startswith("https://idp.example.com/sso?")
    query = parse_qs(urlparse(location).query)
    assert query["SAMLRequest"]
    state = query["RelayState"][0]

    forged = guest.post(
        "/api/auth/sso/callback",
        data={"SAMLResponse": base64.b64encode(b"<Assertion/>").decode(), "RelayState": state},
        follow_redirects=False,
    )
    assert forged.status_code == 400
    assert forged.json()["error"] == "email or password is wrong"
    assert "lbw_session" not in forged.cookies

    started = guest.get("/api/auth/sso", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["RelayState"][0]
    assertion = _signed_assertion(key, "ada@example.com")
    done = guest.post(
        "/api/auth/sso/callback",
        data={
            "SAMLResponse": base64.b64encode(assertion.encode()).decode(),
            "RelayState": state,
        },
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert done.headers["location"] == "/"
    assert cert not in done.text
    assert "lbw_session" in done.cookies
    home = TestClient(app)
    home.cookies.set("lbw_session", done.cookies["lbw_session"])
    account = home.get("/api/account")
    assert account.status_code == 200
    assert account.json()["current_user_id"] == created.json()["current_user_id"]
