"""
Logging for the SDK: a small protocol, a stdlib-backed default, and a noop.

The default used to be silent, which meant a wrong SDK key, a 404 datafile,
and a dropped event batch all looked identical to a working install. The
default is now loud in development and quiet in production:

* Development: a stderr handler at WARNING, attached once to the ``avsb``
  logger, so a broken install announces itself the first time you run it.
* Production: a :class:`logging.NullHandler`, the standard library
  convention for libraries. Your application's logging configuration
  governs, and the SDK adds no output of its own.

Development is detected from environment variables, in this order:

1. ``AVSB_LOG_LEVEL`` set to a level name forces that level and wins over
   everything. ``AVSB_LOG_LEVEL=none`` silences the SDK entirely.
2. ``AVSB_ENV``, then ``ENVIRONMENT``, ``ENV``, ``PYTHON_ENV``, ``APP_ENV``,
   ``FLASK_ENV``, ``DJANGO_ENV``: the first one set decides. ``production``
   or ``prod`` means production; anything else means development.
3. Nothing set: development, because an unconfigured process is far more
   often somebody's laptop than a production deploy, and the failure mode of
   being loud is a warning line rather than a silent outage.

Pass any object with ``debug``, ``info``, ``warn``, and ``error`` methods as
the ``logger`` option to replace all of this.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Protocol, runtime_checkable

_LOGGER_NAME = "avsb"

_ENV_VARS = (
    "AVSB_ENV",
    "ENVIRONMENT",
    "ENV",
    "PYTHON_ENV",
    "APP_ENV",
    "FLASK_ENV",
    "DJANGO_ENV",
)

_PRODUCTION_VALUES = frozenset({"production", "prod"})

_LEVEL_NAMES: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


@runtime_checkable
class Logger(Protocol):
    """The four methods the SDK calls. Implement it to route SDK logs.

    ``data`` carries structured context (a URL, an HTTP status, a flag key).
    Implementations are free to ignore it, but printing it is what makes an
    error actionable.
    """

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None: ...

    def info(self, message: str, data: dict[str, Any] | None = None) -> None: ...

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None: ...

    def error(self, message: str, data: dict[str, Any] | None = None) -> None: ...


def is_development() -> bool:
    """Is this process running outside production, per the env heuristics?"""
    for name in _ENV_VARS:
        raw = os.environ.get(name)
        if raw:
            return raw.strip().lower() not in _PRODUCTION_VALUES
    return True


def configured_level() -> int | None:
    """Resolve ``AVSB_LOG_LEVEL``, or ``None`` when it is unset or unknown.

    Returns :data:`logging.CRITICAL` plus one for ``none`` and ``off``, which
    silences every SDK log without needing a separate switch.
    """
    raw = os.environ.get("AVSB_LOG_LEVEL")
    if not raw:
        return None
    normalised = raw.strip().lower()
    if normalised in ("none", "off", "silent"):
        return logging.CRITICAL + 1
    return _LEVEL_NAMES.get(normalised)


class AvsbLogger:
    """Structured logger backed by the standard library.

    ``AvsbLogger()`` gives the documented default behaviour. Pass ``name`` to
    log under your own logger instead of ``avsb``, and ``prefix`` to tag
    messages from one subsystem::

        AvsbLogger("myapp.flags")
    """

    def __init__(
        self,
        name: str = _LOGGER_NAME,
        prefix: str = "",
        *,
        configure_default_handler: bool = True,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._prefix = prefix
        if configure_default_handler:
            _configure_once(self._logger)

    def _fmt(self, message: str) -> str:
        return f"[{self._prefix}] {message}" if self._prefix else message

    def _log(
        self, level: int, message: str, data: dict[str, Any] | None
    ) -> None:
        if data:
            self._logger.log(level, "%s %s", self._fmt(message), data)
        else:
            self._logger.log(level, "%s", self._fmt(message))

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._log(logging.DEBUG, message, data)

    def info(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._log(logging.INFO, message, data)

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._log(logging.WARNING, message, data)

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._log(logging.ERROR, message, data)

    def child(self, prefix: str) -> "AvsbLogger":
        """Return a logger that tags messages with an extra prefix."""
        child_prefix = f"{self._prefix}.{prefix}" if self._prefix else prefix
        return AvsbLogger(
            self._logger.name, child_prefix, configure_default_handler=False
        )


class NoopLogger:
    """A logger that discards everything. Useful in tests and benchmarks."""

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Discard."""

    def info(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Discard."""

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Discard."""

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Discard."""


_CONFIGURED_LOGGERS: set[str] = set()


def _configure_once(logger: logging.Logger) -> None:
    """Attach the default handler exactly once per logger name.

    Repeated ``AvsbServer(...)`` construction in a test suite or a worker
    pool must not stack handlers and print every warning N times.
    """
    if logger.name in _CONFIGURED_LOGGERS:
        return
    _CONFIGURED_LOGGERS.add(logger.name)

    forced = configured_level()
    if forced is not None:
        logger.setLevel(forced)
        if forced > logging.CRITICAL:
            logger.addHandler(logging.NullHandler())
            return
        logger.addHandler(_stderr_handler())
        return

    if is_development():
        logger.setLevel(logging.WARNING)
        logger.addHandler(_stderr_handler())
        return

    # Production: say nothing unless the host application asks for it.
    logger.addHandler(logging.NullHandler())


def _stderr_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    return handler


def default_logger() -> Logger:
    """The logger a client uses when the caller passes none."""
    return AvsbLogger()
