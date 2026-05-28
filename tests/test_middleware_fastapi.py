"""
FastAPI middleware mount test.

Skipped when fastapi is not installed.  Verifies the middleware correctly
attaches the server to request scope without crashing the ASGI lifecycle.
"""

import pytest

try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="fastapi not installed")
class TestAvsbFastApiMiddleware:
    def test_middleware_attaches_server_to_scope(self):
        """
        The middleware should attach the server to scope['state']['avsb']
        on each HTTP request.
        """
        from avsb.middleware.asgi import AvsbAsgiMiddleware

        server_sentinel = object()
        captured_scope: dict = {}

        async def dummy_app(scope, receive, send):
            captured_scope.update(scope)

        middleware = AvsbAsgiMiddleware(dummy_app, server=server_sentinel)

        import asyncio

        async def run():
            scope = {"type": "http", "method": "GET", "path": "/"}
            await middleware(scope, None, None)  # type: ignore[arg-type]

        asyncio.run(run())
        assert captured_scope.get("avsb") is server_sentinel

    def test_non_http_scope_unchanged(self):
        """Lifespan scopes should pass through without avsb key."""
        from avsb.middleware.asgi import AvsbAsgiMiddleware

        server_sentinel = object()
        captured_scope: dict = {}

        async def dummy_app(scope, receive, send):
            captured_scope.update(scope)

        middleware = AvsbAsgiMiddleware(dummy_app, server=server_sentinel)

        import asyncio

        async def run():
            scope = {"type": "lifespan"}
            await middleware(scope, None, None)  # type: ignore[arg-type]

        asyncio.run(run())
        assert "avsb" not in captured_scope
