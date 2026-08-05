"""
WSGI middleware: puts the A vs B client in every request environ.

Works with any WSGI app (Flask, Django, Pyramid, Bottle)::

    from avsb import AvsbServer
    from avsb.middleware.wsgi import AvsbWsgiMiddleware

    server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    server.on_ready()
    application = AvsbWsgiMiddleware(application, server=server)

Then in a view::

    server = environ["avsb.server"]
    bound = environ.get("avsb.user")   # only when context_from was passed

The key is namespaced ``avsb.server``, per the WSGI convention for third-party
environ keys.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, MutableMapping, Protocol

from ..core.types import EvalContext
from ..user_bound import UserBoundClient

Environ = MutableMapping[str, Any]
StartResponse = Callable[..., Any]
WsgiApp = Callable[[Environ, StartResponse], Iterable[bytes]]

#: Derives the evaluation context from the WSGI environ. Return ``None`` to
#: skip binding for that request (an unauthenticated route, say).
ContextFrom = Callable[[Environ], EvalContext | None]

#: ``environ["avsb.server"]``
ENVIRON_KEY = "avsb.server"

#: ``environ["avsb.user"]``
BOUND_ENVIRON_KEY = "avsb.user"


class SupportsForUser(Protocol):
    """The one client method this middleware needs to bind a context."""

    def for_user(self, context: EvalContext) -> UserBoundClient: ...


class AvsbWsgiMiddleware:
    """Attaches an A vs B client to the WSGI environ on every request."""

    def __init__(
        self,
        app: WsgiApp,
        server: SupportsForUser,
        *,
        context_from: ContextFrom | None = None,
    ) -> None:
        """
        :param app: The WSGI app to wrap.
        :param server: An :class:`~avsb.AvsbServer`. Its lifecycle is yours:
            this middleware never calls ``on_ready`` or ``close``, because one
            client normally serves code outside the request path too.
        :param context_from: Optional. Builds the evaluation context from the
            environ, so ``environ["avsb.user"]`` is already bound.
        """
        self.app = app
        self.server = server
        self.context_from = context_from

    def __call__(
        self, environ: Environ, start_response: StartResponse
    ) -> Iterable[bytes]:
        """Attach the client, then hand off to the wrapped app."""
        environ[ENVIRON_KEY] = self.server
        if self.context_from is not None:
            context = self.context_from(environ)
            if context is not None:
                environ[BOUND_ENVIRON_KEY] = self.server.for_user(context)
        return self.app(environ, start_response)
