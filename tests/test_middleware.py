"""
Middleware: the client lands where the docs say it lands.

The ASGI middleware used to attach to ``scope["avsb"]`` while the docs promised
``request.state.avsb``. Starlette reads ``request.state`` from
``scope["state"]``, so the documented access path raised ``AttributeError`` on
the first request. These lock the attachment points.

No framework is needed: the ASGI and WSGI contracts are plain callables, so the
real middleware is driven directly.
"""

from __future__ import annotations

import asyncio
from typing import Any, MutableMapping

import pytest

from avsb import AvsbServer, SingleContext
from avsb.middleware.asgi import (
    BOUND_STATE_KEY,
    STATE_KEY,
    AvsbAsgiMiddleware,
)
from avsb.middleware.fastapi import AvsbFastApiMiddleware
from avsb.middleware.wsgi import (
    BOUND_ENVIRON_KEY,
    ENVIRON_KEY,
    AvsbWsgiMiddleware,
)
from avsb.test_utils import TEST_SDK_KEY, build_minimal_datafile
from avsb.user_bound import UserBoundClient


@pytest.fixture()
def server() -> Any:
    client = AvsbServer(
        TEST_SDK_KEY,
        datafile=build_minimal_datafile(),
        polling_enabled=False,
        events_enabled=False,
    )
    yield client
    client.close()


# ---------------------------------------------------------------------------
# ASGI
# ---------------------------------------------------------------------------


def _run_asgi(
    middleware: AvsbAsgiMiddleware, scope: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    seen: dict[str, Any] = {}

    async def app(inner_scope: Any, receive: Any, send: Any) -> None:
        seen.update(inner_scope)

    middleware.app = app  # type: ignore[assignment]

    async def drive() -> None:
        await middleware(scope, _noop_receive, _noop_send)

    asyncio.run(drive())
    return seen


async def _noop_receive() -> MutableMapping[str, Any]:  # pragma: no cover
    return {}


async def _noop_send(_message: MutableMapping[str, Any]) -> None:  # pragma: no cover
    return None


async def _null_app(scope: Any, receive: Any, send: Any) -> None:
    return None


def test_the_client_lands_on_scope_state_where_request_state_reads_it(
    server: AvsbServer,
) -> None:
    middleware = AvsbAsgiMiddleware(_null_app, server=server)
    scope = _run_asgi(middleware, {"type": "http", "method": "GET", "path": "/"})

    assert scope["state"][STATE_KEY] is server
    # And NOT the old wrong place, which the docs never promised.
    assert "avsb" not in {k for k in scope if k != "state"}


def test_an_existing_state_mapping_is_preserved(server: AvsbServer) -> None:
    middleware = AvsbAsgiMiddleware(_null_app, server=server)
    scope = _run_asgi(
        middleware,
        {"type": "http", "state": {"request_id": "r_1"}},
    )
    assert scope["state"]["request_id"] == "r_1"
    assert scope["state"][STATE_KEY] is server


def test_websocket_scopes_get_the_client_too(server: AvsbServer) -> None:
    middleware = AvsbAsgiMiddleware(_null_app, server=server)
    scope = _run_asgi(middleware, {"type": "websocket"})
    assert scope["state"][STATE_KEY] is server


def test_lifespan_scopes_are_left_alone(server: AvsbServer) -> None:
    """A lifespan scope has no request state to attach to."""
    middleware = AvsbAsgiMiddleware(_null_app, server=server)
    scope = _run_asgi(middleware, {"type": "lifespan"})
    assert "state" not in scope


def test_context_from_binds_a_client_for_the_request(server: AvsbServer) -> None:
    middleware = AvsbAsgiMiddleware(
        _null_app,
        server=server,
        context_from=lambda scope: SingleContext.user(key=scope["path"].strip("/")),
    )
    scope = _run_asgi(middleware, {"type": "http", "path": "/u_42"})

    bound = scope["state"][BOUND_STATE_KEY]
    assert isinstance(bound, UserBoundClient)
    assert isinstance(bound.context, SingleContext)
    assert bound.context.key == "u_42"


def test_context_from_returning_none_binds_nothing(server: AvsbServer) -> None:
    """An unauthenticated route has no context, and that is not an error."""
    middleware = AvsbAsgiMiddleware(
        _null_app, server=server, context_from=lambda _scope: None
    )
    scope = _run_asgi(middleware, {"type": "http", "path": "/health"})
    assert BOUND_STATE_KEY not in scope["state"]


def test_the_wrapped_app_is_always_called(server: AvsbServer) -> None:
    called: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        called.append(scope["type"])

    middleware = AvsbAsgiMiddleware(app, server=server)

    async def drive() -> None:
        await middleware({"type": "lifespan"}, _noop_receive, _noop_send)
        await middleware({"type": "http"}, _noop_receive, _noop_send)

    asyncio.run(drive())
    assert called == ["lifespan", "http"]


def test_the_fastapi_name_is_the_same_middleware() -> None:
    """One implementation, two discoverable names."""
    assert AvsbFastApiMiddleware is AvsbAsgiMiddleware


# ---------------------------------------------------------------------------
# WSGI
# ---------------------------------------------------------------------------


def test_the_client_lands_on_the_namespaced_environ_key(
    server: AvsbServer,
) -> None:
    seen: dict[str, Any] = {}

    def app(environ: Any, start_response: Any) -> list[bytes]:
        seen.update(environ)
        return [b""]

    middleware = AvsbWsgiMiddleware(app, server=server)
    middleware({"PATH_INFO": "/"}, lambda *_args: None)

    assert seen[ENVIRON_KEY] is server


def test_wsgi_context_from_binds_a_client(server: AvsbServer) -> None:
    seen: dict[str, Any] = {}

    def app(environ: Any, start_response: Any) -> list[bytes]:
        seen.update(environ)
        return [b""]

    middleware = AvsbWsgiMiddleware(
        app,
        server=server,
        context_from=lambda environ: SingleContext.user(
            key=environ["HTTP_X_USER_ID"]
        ),
    )
    middleware({"HTTP_X_USER_ID": "u_7"}, lambda *_args: None)

    bound = seen[BOUND_ENVIRON_KEY]
    assert isinstance(bound, UserBoundClient)
    assert isinstance(bound.context, SingleContext)
    assert bound.context.key == "u_7"


def test_the_wsgi_response_is_passed_through(server: AvsbServer) -> None:
    def app(environ: Any, start_response: Any) -> list[bytes]:
        return [b"hello"]

    middleware = AvsbWsgiMiddleware(app, server=server)
    assert list(middleware({}, lambda *_args: None)) == [b"hello"]


# ---------------------------------------------------------------------------
# Framework extensions: constructor guards
# ---------------------------------------------------------------------------


def test_the_flask_extension_refuses_to_run_without_a_client() -> None:
    """A silently unset client would make every flag serve its default."""
    flask = pytest.importorskip(
        "flask",
        reason=(
            "Flask is not installed, so its extension cannot be exercised. "
            'Install it with: pip install "avsb[flask]".'
        ),
    )
    from avsb.middleware.flask import AvsbFlask

    app = flask.Flask(__name__)
    with pytest.raises(ValueError) as excinfo:
        AvsbFlask().init_app(app)
    assert "needs an A vs B client" in str(excinfo.value)


def test_the_flask_extension_attaches_to_g(server: AvsbServer) -> None:
    flask = pytest.importorskip(
        "flask",
        reason=(
            "Flask is not installed, so its extension cannot be exercised. "
            'Install it with: pip install "avsb[flask]".'
        ),
    )
    from avsb.middleware.flask import AvsbFlask

    app = flask.Flask(__name__)
    AvsbFlask(app, server=server)

    @app.get("/")
    def index() -> str:
        return type(flask.g.avsb).__name__

    with app.test_client() as client:
        assert client.get("/").data == b"AvsbServer"
