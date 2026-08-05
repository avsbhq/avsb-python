"""
JavaScript value semantics, ported so every A vs B SDK agrees.

The reference evaluator is TypeScript, so JavaScript's rules are the
contract (conformance ``fixtures/semantics/manifest.json``). Python's own
rules differ in two places that matter, and both were shipping wrong
answers before this module existed:

1. Truthiness. Python calls ``[]`` and ``{}`` falsy; JavaScript calls them
   truthy. A JSON flag serving ``{}`` is ENABLED, and
   :func:`js_truthy` is what makes ``Flag.is_enabled()`` say so.
2. Number formatting. ``str(3330.0)`` is ``"3330.0"`` in Python and
   ``"3330"`` in JavaScript. Reason strings embed bucket thresholds, and
   the conformance corpus compares reasons character for character, so
   :func:`js_number_str` is required for the Python evaluator to pass.

Nothing here has a Python-flavoured variant. If a value crosses a
language boundary, it goes through these functions.
"""

from __future__ import annotations

import math

#: JavaScript switches from decimal to exponential notation at 1e21.
_EXPONENTIAL_THRESHOLD = 1e21


def js_truthy(value: object) -> bool:
    """Is ``value`` truthy under JavaScript's rules?

    Falsy: ``False``, ``0``, ``-0``, ``NaN``, ``""``, ``None``.
    Truthy: everything else, including ``[]``, ``{}``, ``"0"`` and
    ``"false"``.

    ``None`` covers both of JavaScript's empty values: a JSON ``null``
    variation value deserialises to ``None``, and so does an absent one.
    """
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return False
        return value != 0
    if isinstance(value, str):
        return value != ""
    # Lists, dicts, tuples, objects: truthy however empty they are.
    return True


def js_number_str(value: float | int) -> str:
    """Format a number the way JavaScript's ``String(number)`` does.

    Integral floats lose the trailing ``.0`` (``3330.0`` becomes
    ``"3330"``), fractional ones keep the shortest round-tripping form
    (``1234.5`` stays ``"1234.5"``), and ``-0.0`` renders as ``"0"``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if value == math.inf:
        return "Infinity"
    if value == -math.inf:
        return "-Infinity"
    if value == 0:
        # Python distinguishes -0.0; JavaScript's String(-0) is "0".
        return "0"
    if value.is_integer() and abs(value) < _EXPONENTIAL_THRESHOLD:
        return str(int(value))
    return repr(value)


def js_optional_str(value: str | None) -> str:
    """Render an optional string the way a JS template literal would.

    ``${maybe?.key}`` prints ``undefined`` when the object is absent. The
    conformance corpus records reason strings produced that way, so a
    missing variation key has to read ``undefined``, not ``None``.
    """
    return "undefined" if value is None else value


def js_string(value: object) -> str:
    """Coerce a value to string the way JavaScript's ``String(x)`` does.

    Audience operators compare stringified values, so the coercion has to
    match the reference engine or the same audience matches different people
    per language. The notable rules: ``True`` is ``"true"`` (not
    ``"True"``), an absent value is ``"undefined"``, a list joins on commas,
    and a dict is ``"[object Object]"``.

    One collapse is unavoidable: JavaScript renders a missing attribute as
    ``"undefined"`` and an explicit JSON ``null`` as ``"null"``, while Python
    has only ``None`` for both. ``None`` renders as ``"undefined"``, because
    a missing attribute is the case that actually occurs.
    """
    if value is None:
        return "undefined"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return js_number_str(value)
    if isinstance(value, (list, tuple)):
        # Array.prototype.toString: comma-joined, empty for null/undefined.
        return ",".join("" if item is None else js_string(item) for item in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def js_number(value: object) -> float:
    """Coerce a value to a number the way JavaScript's ``Number(x)`` does.

    Returns ``NaN`` for anything uncoercible, and every comparison against
    ``NaN`` is false, which is how numeric audience operators fail closed.

    The rules that bite: an empty or whitespace-only string is ``0``, not an
    error; an absent value is ``NaN``; ``True`` is ``1``. Python's ``float()``
    raises on the first of those and rejects the hex, octal, and binary
    literal forms JavaScript accepts, so this cannot be ``float()``.
    """
    if value is None:
        return math.nan
    if value is True:
        return 1.0
    if value is False:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _js_number_from_string(value)
    if isinstance(value, (list, tuple)):
        # Number([]) is 0 and Number([n]) is n, via the string coercion.
        return _js_number_from_string(js_string(value))
    return math.nan


def _js_number_from_string(raw: str) -> float:
    trimmed = raw.strip()
    if trimmed == "":
        return 0.0
    if trimmed in ("Infinity", "+Infinity"):
        return math.inf
    if trimmed == "-Infinity":
        return -math.inf
    lowered = trimmed.lower()
    for prefix, base in (("0x", 16), ("0o", 8), ("0b", 2)):
        if lowered.startswith(prefix):
            try:
                return float(int(trimmed[2:], base))
            except ValueError:
                return math.nan
    # Python's float() accepts spellings JavaScript rejects: "nan", "inf",
    # and underscore separators such as "1_000".
    if lowered in ("nan", "-nan", "+nan", "inf", "-inf", "+inf"):
        return math.nan
    if "_" in trimmed:
        return math.nan
    try:
        return float(trimmed)
    except ValueError:
        return math.nan
