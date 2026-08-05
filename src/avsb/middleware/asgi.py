"""
ASGI middleware: puts the A vs B client on every request.

Works with FastAPI, Starlette, Litestar, Quart, and any other ASGI framework::

    from avsb import AvsbServer
    from avsb.middleware.asgi import AvsbAsgiMiddleware

    server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    server.on_ready()
    app = AvsbAsgiMiddleware(app, server=server)

Then in a handler::

    @app.get("/checkout")
    async def checkout(request: Request):
        avsb = request.state.avsb              # the client
        bound = request.state.avsb_user        # bound to this request's context

The client is attached to ``scope["state"]``, which is the ASGI extension
Starlette reads ``request.state`` from. It used to be attached to
``scope["avsb"]`` while the docs promised ``request.state.avsb``, so the
documented access path raised ``AttributeError`` on the first request.

Pass ``context_from`` to also get a request-scoped bound client, so handlers
never rebuild the context::

    AvsbAsgiMiddleware(
        app,
        server=server,
        context_from=lambda scope: SingleContext.user(
            key=dict(scope["headers"]).get(b"x-user-id", b"").decode()
        ),
    )
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, MutableMapping, Protocol

from ..core.types import EvalContext
from ..user_bound import UserBoundClient

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: Derives the evaluation context from the raw ASGI scope. Return ``None`` to
#: skip binding for that request (an unauthenticated route, say).
ContextFrom = Callable[[Scope], EvalContext | None]

#: ``request.state.avsb``
STATE_KEY = "avsb"

#: ``request.state.avsb_user``
BOUND_STATE_KEY = "avsb_user"


class SupportsForUser(Protocol):
    """The one client method this middleware needs to bind a context."""

    def for_user(self, context: EvalContext) -> UserBoundClient: ...


class AvsbAsgiMiddleware:
    """Attaches an A vs B client to ``scope["state"]`` on every request."""

    def __init__(
        self,
        app: AsgiApp,
        server: SupportsForUser,
        *,
        context_from: ContextFrom | None = None,
    ) -> None:
        """
        :param app: The ASGI app to wrap.
        :param server: An :class:`~avsb.AvsbServer` or
            :class:`~avsb.async_client.AsyncAvsbServer`. Its lifecycle is
            yours: this middleware never calls ``on_ready`` or ``close``,
            because one client normally serves code outside the request path
            too.
        :param context_from: Optional. Builds the evaluation context from the
            ASGI scope, so ``request.state.avsb_user`` is a client already bound
            to this request.
        """
        self.app = app
        self.server = server
        self.context_from = context_from

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Attach the client, then hand off to the wrapped app."""
        if scope.get("type") in ("http", "websocket"):
            state = scope.setdefault("state", {})
            if isinstance(state, dict):
                state[STATE_KEY] = self.server
                if self.context_from is not None:
                    context = self.context_from(scope)
                    if context is not None:
                        state[BOUND_STATE_KEY] = self.server.for_user(context)
        await self.app(scope, receive, send)
