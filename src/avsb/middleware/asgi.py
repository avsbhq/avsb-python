"""
Generic ASGI middleware — attaches an AvsbServer to each request scope.

Usage::

    from avsb import AvsbServer
    from avsb.middleware.asgi import AvsbAsgiMiddleware

    app = AvsbAsgiMiddleware(your_asgi_app, server=AvsbServer(...))
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable


class AvsbAsgiMiddleware:
    """
    Wraps any ASGI app and makes the AvsbServer available via
    ``scope["avsb"]``.
    """

    def __init__(self, app: Any, server: Any) -> None:
        self.app = app
        self.server = server

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope.get("type") in ("http", "websocket"):
            scope["avsb"] = self.server
        await self.app(scope, receive, send)
