"""
Flask extension.

Install the extra::

    pip install "avsb[flask]"

Wire it once::

    from flask import Flask
    from avsb import AvsbServer
    from avsb.middleware.flask import AvsbFlask

    app = Flask(__name__)
    server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    server.on_ready()
    AvsbFlask(app, server=server)

Then in a view::

    from flask import g
    from avsb import SingleContext

    @app.get("/checkout")
    def checkout():
        ctx = SingleContext.user(key=g.user_id)
        if g.avsb.get_bool_flag("checkout-v2", False, ctx):
            ...

Pass ``context_from`` to also get ``g.avsb_user``, a client already bound to
this request's context, so views never rebuild it.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..core.types import EvalContext
from ..user_bound import UserBoundClient

#: Derives the evaluation context for the current request. Return ``None`` to
#: skip binding (an unauthenticated route, say).
ContextFrom = Callable[[], EvalContext | None]

#: ``g.avsb``
G_KEY = "avsb"

#: ``g.avsb_user``
BOUND_G_KEY = "avsb_user"

_MISSING_FLASK = (
    "The A vs B Flask extension needs Flask, which is an optional dependency. "
    'Install it with: pip install "avsb[flask]".'
)


class SupportsForUser(Protocol):
    """The one client method this extension needs to bind a context."""

    def for_user(self, context: EvalContext) -> UserBoundClient: ...


class AvsbFlask:
    """Flask extension that puts the A vs B client on ``flask.g``."""

    def __init__(
        self,
        app: Any = None,
        server: SupportsForUser | None = None,
        *,
        context_from: ContextFrom | None = None,
    ) -> None:
        """
        :param app: The Flask app. Omit it to use the factory pattern and call
            :meth:`init_app` later.
        :param server: An :class:`~avsb.AvsbServer`. Its lifecycle is yours:
            this extension never calls ``on_ready`` or ``close``, because one
            client normally serves code outside the request path too.
        :param context_from: Optional. Builds the evaluation context for the
            current request, so ``g.avsb_user`` is already bound.
        """
        self._server = server
        self._context_from = context_from
        if app is not None:
            self.init_app(app, server, context_from=context_from)

    def init_app(
        self,
        app: Any,
        server: SupportsForUser | None = None,
        *,
        context_from: ContextFrom | None = None,
    ) -> None:
        """Register the request hook on a Flask app.

        Raises ``ImportError`` naming the extra when Flask is missing, and
        ``ValueError`` when no client was supplied, because a silently unset
        client would make every flag serve its default.
        """
        try:
            import flask
        except ImportError as exc:  # pragma: no cover - covered by a guarded test
            raise ImportError(_MISSING_FLASK) from exc

        if server is not None:
            self._server = server
        if context_from is not None:
            self._context_from = context_from
        if self._server is None:
            raise ValueError(
                "AvsbFlask needs an A vs B client: AvsbFlask(app, "
                "server=AvsbServer(sdk_key=...)). Without one, every flag would "
                "silently serve its default value."
            )

        bound_server = self._server
        derive_context = self._context_from

        @app.before_request
        def _attach_avsb() -> None:
            setattr(flask.g, G_KEY, bound_server)
            if derive_context is not None:
                context = derive_context()
                if context is not None:
                    setattr(flask.g, BOUND_G_KEY, bound_server.for_user(context))

        app.extensions[G_KEY] = self
