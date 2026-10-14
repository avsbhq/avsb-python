"""
What a flag key may look like, and what the SDK says when a read asks for one
the datafile does not carry.

A port of ``packages/avsb-core/src/flagKeyRules.ts``. The platform creates flag
keys made of lowercase letters, digits and underscores, starting with a letter,
at most 100 characters. The SDK keeps accepting any string (a lookup is a
lookup), but a miss on a key that breaks the rule says so and suggests the
spelling the platform would accept. The message text is the TypeScript text
word for word, so every SDK logs the same line for the same mistake.
"""

from __future__ import annotations

import re
from typing import Literal

#: The platform's flag key rule. Mirrors app/_lib/validators/flag.ts.
FLAG_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

#: The platform's flag key length limit. Mirrors app/_lib/validators/flag.ts.
FLAG_KEY_MAX_LENGTH = 100

FlagLookupSource = Literal["datafile", "snapshot"]

_NOT_KEY_CHARACTERS = re.compile(r"[^a-z0-9]+")


def is_platform_flag_key(key: str) -> bool:
    """True when the platform would accept ``key`` as a flag key."""
    return len(key) <= FLAG_KEY_MAX_LENGTH and FLAG_KEY_PATTERN.fullmatch(key) is not None


def suggest_flag_key(key: str) -> str | None:
    """The spelling the platform would accept for a key it would refuse.

    ``None`` when the key is already valid or there is no obvious fix (a key
    that does not start with a letter)::

        suggest_flag_key("new-checkout-flow")  # "new_checkout_flow"
        suggest_flag_key("Dark Mode")          # "dark_mode"
        suggest_flag_key("checkout_v2")        # None, already valid
    """
    if is_platform_flag_key(key):
        return None
    candidate = _NOT_KEY_CHARACTERS.sub("_", key.strip().lower()).strip("_")
    return candidate if is_platform_flag_key(candidate) else None


def flag_not_found_message(flag_key: str, where: FlagLookupSource = "datafile") -> str:
    """The line every SDK logs, once per key, when a read asks for a missing key.

    Distinct from a type mismatch on purpose: a missing key is not a type
    problem, and saying so sent people looking for the wrong thing.
    """
    if where == "snapshot":
        missing = (
            f'AvsB flag "{flag_key}" is not in the server bootstrap snapshot, so reads '
            'return the default you passed (source "not_found") until a datafile '
            "loads. The server evaluated every flag in its datafile."
        )
    else:
        missing = (
            f'AvsB flag "{flag_key}" is not in this environment\'s datafile, so reads '
            'return the default you passed (source "not_found").'
        )

    if is_platform_flag_key(flag_key):
        return (
            f"{missing} Check the key against the Flags page in the A vs B dashboard; "
            "archived flags are left out of the datafile."
        )
    suggestion = suggest_flag_key(flag_key)
    rule = "A vs B flag keys use lowercase letters, digits and underscores, starting with a letter"
    if suggestion:
        return f'{missing} {rule}: did you mean "{suggestion}"?'
    return f"{missing} {rule}, so check the key against the Flags page in the A vs B dashboard."
