"""
The flag key rule and the missing-key message, ported from
``packages/avsb-core/src/flagKeyRules.ts``.

Every case below is a case in ``flagKeyRules.test.ts``: the two must agree so a
Python service and a Node service log the same line for the same mistake.
"""

from __future__ import annotations

from avsb.core.flag_key_rules import (
    FLAG_KEY_MAX_LENGTH,
    flag_not_found_message,
    is_platform_flag_key,
    suggest_flag_key,
)


def test_accepts_the_keys_the_platform_creates() -> None:
    assert is_platform_flag_key("checkout_v2") is True
    assert is_platform_flag_key("a") is True


def test_refuses_hyphens_capitals_spaces_and_a_leading_digit() -> None:
    assert is_platform_flag_key("checkout-v2") is False
    assert is_platform_flag_key("Checkout") is False
    assert is_platform_flag_key("dark mode") is False
    assert is_platform_flag_key("2fa") is False
    assert is_platform_flag_key("") is False


def test_refuses_a_trailing_newline_that_a_dollar_anchor_would_let_through() -> None:
    assert is_platform_flag_key("checkout_v2\n") is False


def test_refuses_a_key_over_the_length_limit() -> None:
    assert is_platform_flag_key("a" * FLAG_KEY_MAX_LENGTH) is True
    assert is_platform_flag_key("a" * (FLAG_KEY_MAX_LENGTH + 1)) is False


def test_suggests_the_underscore_spelling_for_the_docs_examples() -> None:
    assert suggest_flag_key("new-checkout-flow") == "new_checkout_flow"
    assert suggest_flag_key("dark-mode") == "dark_mode"
    assert suggest_flag_key("Dark Mode") == "dark_mode"
    assert suggest_flag_key("checkout.v2") == "checkout_v2"


def test_suggests_nothing_for_a_valid_key_or_one_with_no_obvious_fix() -> None:
    assert suggest_flag_key("checkout_v2") is None
    assert suggest_flag_key("2fa-enabled") is None
    assert suggest_flag_key("---") is None


def test_the_message_says_the_key_is_missing_not_that_its_type_is_wrong() -> None:
    message = flag_not_found_message("checkout_v3")
    assert "\"checkout_v3\" is not in this environment's datafile" in message
    assert 'source "not_found"' in message
    assert "getter" not in message
    assert "declared as" not in message


def test_the_message_suggests_the_platform_spelling_for_a_hyphenated_key() -> None:
    assert 'did you mean "new_checkout_flow"?' in flag_not_found_message("new-checkout-flow")


def test_the_message_names_the_snapshot_when_that_is_where_the_read_looked() -> None:
    assert "is not in the server bootstrap snapshot" in flag_not_found_message(
        "checkout_v3", "snapshot"
    )


def test_the_message_matches_the_typescript_text_word_for_word() -> None:
    assert flag_not_found_message("checkout_v3") == (
        'AvsB flag "checkout_v3" is not in this environment\'s datafile, so reads '
        'return the default you passed (source "not_found"). Check the key against '
        "the Flags page in the A vs B dashboard; archived flags are left out of the "
        "datafile."
    )
    assert flag_not_found_message("2-b") == (
        'AvsB flag "2-b" is not in this environment\'s datafile, so reads return the '
        'default you passed (source "not_found"). A vs B flag keys use lowercase '
        "letters, digits and underscores, starting with a letter, so check the key "
        "against the Flags page in the A vs B dashboard."
    )


def test_the_message_uses_no_em_dash() -> None:
    for message in (
        flag_not_found_message("a_b"),
        flag_not_found_message("a-b"),
        flag_not_found_message("2-b"),
        flag_not_found_message("a_b", "snapshot"),
    ):
        assert "—" not in message
