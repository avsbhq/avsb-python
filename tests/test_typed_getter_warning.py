"""
The wrong-getter warning names real methods (N1-18).

Reading a string flag with ``get_bool_flag`` used to warn that it was read with
``get_boolean_flag``, a method that does not exist, so the one line meant to
point at the call site pointed nowhere.
"""

from __future__ import annotations

from typing import Any

import pytest

from avsb import AvsbServer, SingleContext
from avsb.test_utils import TEST_SDK_KEY, build_minimal_datafile


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None:
        pass

    def info(self, message: str, data: dict[str, Any] | None = None) -> None:
        pass

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.warnings.append(message)

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        pass


def _flag(key: str, flag_type: str, value: Any) -> dict[str, Any]:
    return {
        "id": f"flag_{key}",
        "key": key,
        "type": flag_type,
        "enabled": True,
        "defaultVariationId": "var_a",
        "variations": [{"id": "var_a", "key": "a", "value": value}],
        "overrides": [],
        "rules": [],
    }


def _server(logger: _Logger) -> AvsbServer:
    return AvsbServer(
        TEST_SDK_KEY,
        datafile=build_minimal_datafile(
            flags=[
                _flag("theme", "string", "dark"),
                _flag("dark_mode", "boolean", True),
                _flag("limit", "number", 5),
                _flag("layout", "json", {"columns": 2}),
            ]
        ),
        polling_enabled=False,
        events_enabled=False,
        logger=logger,
    )


@pytest.mark.parametrize(
    ("getter", "flag_key", "default", "declared", "correct"),
    [
        ("get_bool_flag", "theme", False, "string", "get_string_flag"),
        ("get_string_flag", "dark_mode", "x", "boolean", "get_bool_flag"),
        ("get_number_flag", "layout", 0, "json", "get_json_flag"),
        ("get_json_flag", "limit", {}, "number", "get_number_flag"),
    ],
)
def test_the_warning_names_the_getter_that_was_called_and_the_right_one(
    getter: str, flag_key: str, default: Any, declared: str, correct: str
) -> None:
    logger = _Logger()
    server = _server(logger)

    value = getattr(server, getter)(flag_key, default, SingleContext.user(key="u"))

    assert value == default
    assert len(logger.warnings) == 1
    warning = logger.warnings[0]
    assert f"declared as a {declared} flag" in warning
    assert f"read with {getter}," in warning
    assert f"Call {correct}" in warning
    server.close()


def test_no_warning_names_a_getter_the_client_does_not_have() -> None:
    logger = _Logger()
    server = _server(logger)
    ctx = SingleContext.user(key="u")
    server.get_bool_flag("theme", False, ctx)
    server.get_string_flag("dark_mode", "x", ctx)
    server.get_number_flag("theme", 0, ctx)
    server.get_json_flag("theme", {}, ctx)

    for warning in logger.warnings:
        for word in warning.replace(",", " ").split():
            if word.startswith("get_") and word.endswith("_flag"):
                assert hasattr(server, word), f"{word} is not a method: {warning}"
    assert not any("get_boolean_flag" in warning for warning in logger.warnings)
    server.close()


def test_the_right_getter_logs_nothing() -> None:
    logger = _Logger()
    server = _server(logger)
    assert server.get_bool_flag("dark_mode", False, SingleContext.user(key="u")) is True
    assert logger.warnings == []
    server.close()
