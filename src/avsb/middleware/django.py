"""
Django middleware — attaches AvsbServer to each request object.

Requires: pip install avsb[django]

Usage in settings.py::

    MIDDLEWARE = [
        ...
        "avsb.middleware.django.AvsbDjangoMiddleware",
    ]

    AVSB_SERVER = AvsbServer(sdk_key="...")

The server is available as ``request.avsb`` in views.
"""

from __future__ import annotations

from typing import Any, Callable


class AvsbDjangoMiddleware:
    """
    Django middleware that attaches AvsbServer to ``request.avsb``.

    Configure the server via the ``AVSB_SERVER`` Django setting.
    """

    def __init__(self, get_response: Callable) -> None:
        try:
            from django.conf import settings  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "django is required.  Install it with: pip install avsb[django]"
            ) from exc
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        from django.conf import settings

        server = getattr(settings, "AVSB_SERVER", None)
        request.avsb = server
        return self.get_response(request)
