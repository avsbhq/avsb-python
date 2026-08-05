"""
SDK key shape validation, and the message it produces.

A bad key used to be invisible: the CDN 404s, the fetch returns nothing, and
every flag silently serves its default. The value of this check is entirely in
the message, so that is what is asserted.
"""

from __future__ import annotations

import pytest

from avsb.sdk_key import describe_sdk_key, validate_sdk_key_shape


@pytest.mark.parametrize(
    "key",
    [
        "sdk_production_ttqm0eaj4vth1krcb2xn",
        "sdk_development_abc12345",
        # Environment keys may themselves contain underscores.
        "sdk_staging_eu_ttqm0eaj4vth1krcb2xn",
    ],
)
def test_a_real_shaped_key_is_accepted_silently(key: str) -> None:
    result = validate_sdk_key_shape(key)
    assert result.valid is True
    assert result.message is None


@pytest.mark.parametrize(
    ("key", "needle"),
    [
        ("", "no SDK key was passed at all"),
        (
            " sdk_production_ttqm0eaj4vth1krcb2xn ",
            "leading or trailing whitespace",
        ),
        ("https://app.avsb.cloud/projects/p_1/settings", "looks like a URL"),
        ("avsb_pat_abcdefghijklmnop", "looks like an API token"),
        ("avsb_svc_abcdefghijklmnop", "looks like an API token"),
        ("sdk-abcdefghijklmnop", "never existed"),
        ("pub_abcdefghijklmnop", "never existed"),
        ("srv_abcdefghijklmnop", "never existed"),
        ("nonsense", 'does not start with "sdk_"'),
        ("sdk_production", "missing the environment segment"),
        ("sdk_production_abc", "too short"),
    ],
)
def test_a_bad_key_is_diagnosed_specifically(key: str, needle: str) -> None:
    result = validate_sdk_key_shape(key)
    assert result.valid is False
    assert result.message is not None
    assert needle in result.message


def test_every_failure_message_says_where_the_real_key_lives() -> None:
    for key in ("", "nonsense", "sdk-oops", "https://app.avsb.cloud"):
        message = validate_sdk_key_shape(key).message
        assert message is not None
        assert "Environments" in message
        assert "sdk_<environment>_<id>" in message
        # And that the SDK keeps going, so nobody expects a crash.
        assert "keep going" in message


def test_validation_never_raises() -> None:
    """A key this module cannot recognise may still be valid."""
    for key in ("", "\n", "sdk_" * 100, "sdk_prod_" + "z" * 500):
        assert validate_sdk_key_shape(key).valid in (True, False)


def test_the_message_redacts_the_generated_tail() -> None:
    """The message is meant to be pasteable into a support thread."""
    key = "sdk_production_secrettailvalue00"
    message = validate_sdk_key_shape(key + "!").message
    assert message is not None
    assert "secrettailvalue" not in message
    assert "sdk_producti" in message


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("", "(empty)"),
        ("short", "short"),
        ("sdk_production_ttqm0eaj4vth1krcb2xn", "sdk_producti...b2xn"),
    ],
)
def test_describe_sdk_key(key: str, expected: str) -> None:
    assert describe_sdk_key(key) == expected
