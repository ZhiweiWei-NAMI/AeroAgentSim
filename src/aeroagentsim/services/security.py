"""HTTP boundaries for the locally hosted research console."""

from __future__ import annotations

import os
import re
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_ORIGIN = r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]+)?"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class RequestBoundary:
    """Check writes before parsing submissions; optional bearer auth covers APIs."""

    def __init__(self, app: ASGIApp, *, console_origin: str | None) -> None:
        self.app = app
        self.origin = None
        self.hosts = set(LOCAL_HOSTS)
        if console_origin:
            parts = urlsplit(console_origin)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
            ):
                raise ValueError("AEROAGENTSIM_CONSOLE_URL must be an HTTP(S) origin")
            self.origin = console_origin.rstrip("/")
            self.hosts.add(parts.hostname.lower())
        self.token = os.environ.get("AEROAGENTSIM_API_TOKEN")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        status, detail = 0, ""
        if (
            self.token
            and scope["path"].startswith("/v1/")
            and scope["method"] != "OPTIONS"
            and not secrets.compare_digest(
                headers.get("authorization", "").encode(),
                ("Bearer " + self.token).encode(),
            )
        ):
            status, detail = 401, "Bearer token required"
        if not status and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            host = headers.get("host", "")
            try:
                parts = urlsplit("//" + host)
                allowed_host = (
                    parts.hostname in self.hosts
                    and parts.username is None
                    and parts.password is None
                    and not parts.path
                    and not parts.query
                    and not parts.fragment
                    and (parts.port is None or 0 < parts.port <= 65535)
                )
            except ValueError:
                allowed_host = False
            origin = headers.get("origin")
            if not allowed_host or (
                origin is not None
                and origin != self.origin
                and re.fullmatch(LOCAL_ORIGIN, origin) is None
            ):
                status, detail = 403, "Untrusted request Host or Origin"
            elif (
                scope["method"] == "POST"
                and headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                status, detail = 415, "POST requires application/json"
        if status:
            response = JSONResponse({"detail": detail}, status_code=status)
            if status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
