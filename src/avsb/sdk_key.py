"""
SDK key shape validation at construction.

Real keys are minted as ``sdk_<environment_key>_<id>``, where the
environment key matches ``[a-z][a-z0-9_]*`` (it may itself contain
underscores, which is why this validates with one anchored pattern instead
of splitting on ``_``) and the id is 24 lowercase alphanumerics.

A bad key used to be invisible: the CDN 404s, the fetch returns nothing, and
every flag silently serves its default. The four commonest bad values are a
pasted dashboard URL, a truncated copy, an API token in the wrong field, and
a prefix that never existed, and all four are recognisable on sight. So the
check names what it got, what the shape is, and where the real key lives.

Validation NEVER raises. A key this module cannot recognise may still be
valid (the mint format can grow), so the client logs and proceeds with the
value it was given. The datafile fetch is the real arbiter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Matches ``sdk_<env>_<id>``; the trailing id segment carries no underscore.
_SDK_KEY_PATTERN = re.compile(r"^sdk_[a-z][a-z0-9_]*_[a-z0-9]{8,}$")

#: A real-shaped example, so the message is comparable by eye.
_EXAMPLE_KEY = "sdk_production_ttqm0eaj4vth1krcb2xn"


@dataclass(frozen=True)
class SdkKeyShapeResult:
    """The outcome of :func:`validate_sdk_key_shape`."""

    valid: bool
    #: An actionable message safe to paste into a support thread, or
    #: ``None`` when the key looks right.
    message: str | None = None


def describe_sdk_key(sdk_key: str) -> str:
    """Redact all but the recognisable head and tail of a key.

    The environment name is the useful half ("you used the development
    key"); the generated tail is not, so it is truncated.
    """
    if not sdk_key:
        return "(empty)"
    if len(sdk_key) <= 12:
        return sdk_key
    return f"{sdk_key[:12]}...{sdk_key[-4:]}"


def _diagnose(sdk_key: str) -> str:
    trimmed = sdk_key.strip()

    if not trimmed:
        return "no SDK key was passed at all"
    if trimmed != sdk_key:
        return (
            "it has leading or trailing whitespace, which usually means a "
            "copy-paste picked up a line break"
        )
    if re.match(r"^https?://", trimmed, re.IGNORECASE):
        return (
            "it looks like a URL, not a key. Copy the key itself, not the "
            "address of the page it is on"
        )
    if trimmed.startswith("avsb_pat_") or trimmed.startswith("avsb_svc_"):
        return (
            "it looks like an API token. API tokens talk to the management "
            "API; SDKs need the environment SDK key"
        )
    if (
        trimmed.startswith("sdk-")
        or trimmed.startswith("pub_")
        or trimmed.startswith("srv_")
    ):
        return (
            "that key prefix has never existed in A vs B. There is one key "
            "format for SDKs"
        )
    if not trimmed.startswith("sdk_"):
        return 'it does not start with "sdk_"'
    if len(trimmed.split("_")) < 3:
        return "it is missing the environment segment, so it may have been truncated"
    return (
        "the id after the environment name is too short or has characters a "
        "generated id never contains, so it may have been truncated or edited"
    )


def validate_sdk_key_shape(sdk_key: str) -> SdkKeyShapeResult:
    """Check an SDK key's shape and return the message to log.

    Returns the message rather than logging it, so the caller controls the
    transport.
    """
    if _SDK_KEY_PATTERN.match(sdk_key):
        return SdkKeyShapeResult(valid=True, message=None)

    return SdkKeyShapeResult(
        valid=False,
        message=(
            f'AvsB SDK key "{describe_sdk_key(sdk_key)}" does not look like an '
            f"SDK key: {_diagnose(sdk_key)}. Keys look like {_EXAMPLE_KEY} "
            "(sdk_<environment>_<id>). Copy the key for the environment you "
            "want from app.avsb.cloud, then Settings, then Environments. The "
            "SDK will keep going with the value you passed, so if the datafile "
            "request 404s next, this is why."
        ),
    )
