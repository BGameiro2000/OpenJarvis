"""Login (basic or Tailscale identity) and the Content Security Policy."""

from __future__ import annotations

import base64
import binascii
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .settings import Settings

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)

OPEN_PATHS = {"/healthz"}


def check(settings: Settings, request: Request) -> str | None:
    """Return the identity if the request may pass, else None."""
    if settings.auth == "off":
        return "lan"
    if settings.auth == "tailscale":
        who = request.headers.get("tailscale-user-login", "")
        return who or None
    if settings.auth == "basic":
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return None
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return None
        ok_user = secrets.compare_digest(user, settings.user)
        ok_pw = bool(settings.password) and secrets.compare_digest(pw, settings.password)
        return user if ok_user and ok_pw else None
    return None  # unknown mode: fail closed


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:  # noqa: ANN001
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        if request.url.path not in OPEN_PATHS:
            who = check(self.settings, request)
            if who is None:
                headers = {}
                if self.settings.auth == "basic":
                    headers["WWW-Authenticate"] = 'Basic realm="The Machine"'
                return PlainTextResponse("login required", status_code=401, headers=headers)
            request.state.identity = who
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response
