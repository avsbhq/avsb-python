"""
Flask extension — integrates AvsbServer with a Flask application.

Requires: pip install avsb[flask]

Usage::

    from flask import Flask
    from avsb import AvsbServer
    from avsb.middleware.flask import AvsbFlask

    app = Flask(__name__)
    avsb = AvsbFlask()
    avsb.init_app(app, server=AvsbServer(sdk_key="..."))

The server is then available via ``flask.g.avsb`` in request handlers.
"""

from __future__ import annotations

from typing import Any


class AvsbFlask:
    """Flask extension for A vs B feature-flag SDK."""

    def __init__(self, app: Any = None, server: Any = None) -> None:
        self._server = server
        if app is not None:
            self.init_app(app, server)

    def init_app(self, app: Any, server: Any | None = None) -> None:
        try:
            from flask import g  # noqa: F401 — validate availability
        except ImportError as exc:
            raise ImportError(
                "flask is required.  Install it with: pip install avsb[flask]"
            ) from exc

        if server is not None:
            self._server = server

        _server = self._server

        @app.before_request
        def _attach_avsb() -> None:  # type: ignore[misc]
            from flask import g as flask_g

            flask_g.avsb = _server

        app.extensions["avsb"] = self
