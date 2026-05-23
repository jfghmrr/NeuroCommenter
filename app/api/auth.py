from __future__ import annotations

import secrets

from fastapi import Cookie, HTTPException, Request, status

from ..config import get_settings

_TOKENS: set[str] = set()


def issue_token() -> str:
    tok = secrets.token_urlsafe(24)
    _TOKENS.add(tok)
    return tok


def revoke_token(tok: str) -> None:
    _TOKENS.discard(tok)


def require_auth(request: Request, nc_token: str | None = Cookie(default=None)) -> None:
    # для статики/favicon
    if request.url.path.startswith("/static") or request.url.path in {"/favicon.ico", "/login", "/api/login"}:
        return
    if not nc_token or nc_token not in _TOKENS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth required")
