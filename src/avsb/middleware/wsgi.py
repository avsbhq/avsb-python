"""
Generic WSGI middleware: attaches an AvsbServer to each environ dict.

Usage::

    from avsb import AvsbServer
    from avsb.middleware.wsgi import AvsbWsgiMiddleware

    app = AvsbWsgiMiddleware(your_wsgi_app, server=AvsbServer(...))
"""

from __future__ import annotations

from typing import Any, Callable, Iterable


class AvsbWsgiMiddleware:
    """
    Wraps any WSGI app and makes the AvsbServer available via
    ``environ["avsb.server"]``.
    """

    def __init__(self, app: Callable, server: Any) -> None:
        self.app = app
        self.server = server

    def __call__(self, environ: dict[str, Any], start_response: Callable) -> Iterable:
        environ["avsb.server"] = self.server
        return self.app(environ, start_response)
