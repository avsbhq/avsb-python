"""
Structured logger backed by stdlib logging.

Provides debug/info/warn/error methods and a child() factory for scoped
sub-loggers — matching the Logger interface in types.ts §2.13.
"""

from __future__ import annotations

import logging
from typing import Any


class AvsbLogger:
    """Thin wrapper that forwards to a stdlib Logger with a prefix."""

    def __init__(self, name: str = "avsb", prefix: str = "") -> None:
        self._logger = logging.getLogger(name)
        self._prefix = prefix

    def _fmt(self, message: str) -> str:
        if self._prefix:
            return f"[{self._prefix}] {message}"
        return message

    def _extra(self, data: dict[str, Any] | None) -> dict[str, Any]:
        return {"extra": data} if data else {}

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._logger.debug(self._fmt(message), **self._extra(data))

    def info(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._logger.info(self._fmt(message), **self._extra(data))

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._logger.warning(self._fmt(message), **self._extra(data))

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        self._logger.error(self._fmt(message), **self._extra(data))

    def child(self, prefix: str) -> "AvsbLogger":
        child_prefix = f"{self._prefix}.{prefix}" if self._prefix else prefix
        inst = AvsbLogger(self._logger.name, child_prefix)
        inst._logger = self._logger
        return inst
