"""
Django middleware.

Install the extra::

    pip install "avsb[django]"

In ``settings.py``::

    from avsb import AvsbServer

    AVSB_SERVER = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    AVSB_SERVER.on_ready()

    MIDDLEWARE = [
        ...,
        "avsb.middleware.django.AvsbDjangoMiddleware",
    ]

Then in a view::

    from avsb import SingleContext

    def checkout(request):
        ctx = SingleContext.user(key=str(request.user.pk))
        if request.avsb.get_bool_flag("checkout-v2", False, ctx):
            ...

Optionally set ``AVSB_CONTEXT_FROM`` to a callable taking the request and
returning an :data:`~avsb.EvalContext`, and ``request.avsb_user`` becomes a
client already bound to it.

Django builds middleware once per process, so the client is resolved from
settings at startup rather than on every request.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..core.types import EvalContext
from ..user_bound import UserBoundClient

#: ``request.avsb``
REQUEST_ATTR = "avsb"

#: ``request.avsb_user``
BOUND_REQUEST_ATTR = "avsb_user"

#: The settings name holding the client.
SETTING_NAME = "AVSB_SERVER"

#: The settings name holding an optional context factory.
CONTEXT_SETTING_NAME = "AVSB_CONTEXT_FROM"

_MISSING_DJANGO = (
    "The A vs B Django middleware needs Django, which is an optional "
    'dependency. Install it with: pip install "avsb[django]".'
)


class SupportsForUser(Protocol):
    """The one client method this middleware needs to bind a context."""

    def for_user(self, context: EvalContext) -> UserBoundClient: ...


class AvsbDjangoMiddleware:
    """Attaches the A vs B client to every request as ``request.avsb``."""

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        """Resolve the client from settings once, at startup.

        Raises ``ImportError`` naming the extra when Django is missing, and
        ``ValueError`` when ``AVSB_SERVER`` is not set, because a silently unset
        client would make every flag serve its default for the life of the
        process.
        """
        try:
            from django.conf import settings
        except ImportError as exc:  # pragma: no cover - covered by a guarded test
            raise ImportError(_MISSING_DJANGO) from exc

        self.get_response = get_response
        self.server: SupportsForUser | None = getattr(settings, SETTING_NAME, None)
        self.context_from: Callable[[Any], EvalContext | None] | None = getattr(
            settings, CONTEXT_SETTING_NAME, None
        )

        if self.server is None:
            raise ValueError(
                f"AvsbDjangoMiddleware needs {SETTING_NAME} in your Django "
                "settings: AVSB_SERVER = AvsbServer(sdk_key=...). Without it, "
                "every flag would silently serve its default value. Remove the "
                "middleware if you did not mean to enable A vs B."
            )

    def __call__(self, request: Any) -> Any:
        """Attach the client, then hand off to the next middleware."""
        setattr(request, REQUEST_ATTR, self.server)
        if self.context_from is not None and self.server is not None:
            context = self.context_from(request)
            if context is not None:
                setattr(request, BOUND_REQUEST_ATTR, self.server.for_user(context))
        return self.get_response(request)
