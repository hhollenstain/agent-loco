"""Crossing guard routes. An organization admin receives 403."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from lbw_shared.directory import request_directory
from pydantic import BaseModel

from agent_lbw.runtime.db import database
from agent_lbw.runtime.store.crossing import (
    add_guard,
    board,
    save_sign_in,
    unlock_person,
    validate_sign_in,
)
from agent_lbw.runtime.store.install import require_account


class SignInBody(BaseModel):
    tenant_id: str
    provider: str
    issuer: str = ""
    metadata_url: str = ""
    client_id: str = ""
    client_secret: str = ""


class ValidateBody(BaseModel):
    tenant_id: str


class GuardBody(BaseModel):
    name: str
    email: str
    password: str


def register_crossing(app: FastAPI) -> None:
    @app.get("/api/crossingguard", response_model=None)
    def show(request: Request) -> object:
        return _act(request, lambda db, account, root: board(db, account["current_user_id"]))

    @app.post("/api/crossingguard/people/{identity_id}/unlock", response_model=None)
    def unlock(request: Request, identity_id: str) -> object:
        return _act(
            request,
            lambda db, account, root: unlock_person(db, account["current_user_id"], identity_id),
        )

    @app.post("/api/crossingguard/guards", response_model=None)
    def guards(request: Request, body: GuardBody) -> object:
        return _act(
            request,
            lambda db, account, root: add_guard(
                db,
                account["current_user_id"],
                name=body.name,
                email=body.email,
                password=body.password,
            ),
        )

    @app.put("/api/crossingguard/sign-in", response_model=None)
    def sign_in(request: Request, body: SignInBody) -> object:
        return _act(
            request,
            lambda db, account, root: save_sign_in(
                db,
                root,
                account["current_user_id"],
                tenant_id=body.tenant_id,
                provider=body.provider,
                issuer=body.issuer,
                metadata_url=body.metadata_url,
                client_id=body.client_id,
                client_secret=body.client_secret,
            ),
        )

    @app.post("/api/crossingguard/sign-in/validate", response_model=None)
    def validate(request: Request, body: ValidateBody) -> object:
        return _act(
            request,
            lambda db, account, root: validate_sign_in(
                db, root, account["current_user_id"], body.tenant_id
            ),
        )


def _act(request: Request, run) -> object:
    root, url = _ready(request)
    try:
        with database(root, url) as db:
            account = require_account(db)
            return run(db, account, root)
    except PermissionError as exc:
        return _error(str(exc), 403)
    except KeyError:
        return _error("not found", 404)
    except ValueError as exc:
        return _error(str(exc), 400)


def _ready(request: Request) -> tuple[Path, str]:
    directory = request_directory(request)
    return directory.root, directory.database_url


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)
