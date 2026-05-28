"""
FastAPI / Starlette middleware — attaches AvsbServer to each request state.

Requires: pip install avsb[fastapi]

Usage::

    from fastapi import FastAPI, Request
    from avsb import AvsbServer
    from avsb.middleware.fastapi import AvsbFastApiMiddleware

    app = FastAPI()
    server = AvsbServer(sdk_key="...")
    app.add_middleware(AvsbFastApiMiddleware, server=server)

The server is available as ``request.state.avsb`` inside route handlers.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable


class AvsbFastApiMiddleware:
    """
    Starlette-compatible middleware for FastAPI.

    Attaches the AvsbServer to ``request.state.avsb`` on every HTTP request.
    """

    def __init__(self, app: Any, server: Any) -> None:
        try:
            from starlette.middleware.base import BaseHTTPMiddleware  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "fastapi (and starlette) are required.  Install with: pip install avsb[fastapi]"
            ) from exc
        self.app = app
        self.server = server

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable,
        send: Callable,
    ) -> None:
        if scope.get("type") == "http":
            scope.setdefault("state", {})  # type: ignore[call-overload]
            if isinstance(scope.get("state"), dict):
                scope["state"]["avsb"] = self.server
            else:
                try:
                    scope["state"].avsb = self.server  # type: ignore[attr-defined]
                except AttributeError:
                    pass
        await self.app(scope, receive, send)
