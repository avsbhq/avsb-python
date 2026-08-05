"""
Sticky bucketing, wired at the client layer.

Step 2 of the decision pipeline. The evaluator stays a pure function of
datafile plus context; remembering an assignment is a client concern, exactly
as the reference Node SDK arranges it, and the conformance semantics fixture
locks that arrangement.

The rules, all four conformance-locked:

* **Lookup before the evaluator.** A hit returns immediately with source
  ``sticky`` and the stored rule id and rule type. The evaluator is not
  consulted.
* **A miss is normal.** Fall through and let the evaluator decide.
* **A deleted variation is a miss.** When the stored variation id is not among
  the flag's current variations, serving it is impossible, so the evaluator
  decides instead.
* **Save on ab_test only.** A ``targeted_delivery`` rule is a rollout, not an
  assignment. Holdouts, overrides, and fallbacks are never saved. And the
  saved field is the variation **id**, never its key: lookup resolves by id,
  so a stored key can never match again and the store silently rots.

Store failures never break an evaluation. A Redis outage degrades bucketing
stability; it must not take the application down with it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .core.datafile_types import FlagDatafile, FlagDatafileEntry, FlagDatafileVariation
from .core.types import StickyAssignment, StickyBucketService
from .logger import Logger, NoopLogger


@dataclass(frozen=True)
class StickyHit:
    """A replayed assignment: the variation to serve and where it came from."""

    variation: FlagDatafileVariation
    assignment: StickyAssignment


class StickyPipeline:
    """Wraps a :class:`~avsb.StickyBucketService` with the client-layer rules."""

    def __init__(
        self,
        service: StickyBucketService | None,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._service = service
        self._logger = logger or NoopLogger()

    @property
    def enabled(self) -> bool:
        """Is a sticky-bucket service configured?"""
        return self._service is not None

    def lookup(
        self, datafile: FlagDatafile, flag_key: str, visitor_id: str
    ) -> StickyHit | None:
        """Look up a stored assignment. ``None`` means "let the evaluator decide"."""
        service = self._service
        if service is None:
            return None

        try:
            assignment = service.lookup(visitor_id, flag_key)
        except Exception as exc:  # noqa: BLE001 - a store outage is not fatal
            self._logger.warn(
                "A vs B sticky-bucket lookup failed, so this evaluation ran "
                "without it and may assign a different variation than the one "
                "stored. Flag evaluation itself is unaffected.",
                {"flag_key": flag_key, "error": str(exc)},
            )
            return None

        if assignment is None:
            return None

        flag = _find_flag(datafile, flag_key)
        if flag is None:
            return None

        for variation in flag.variations:
            if variation.id == assignment.variation_id:
                return StickyHit(variation=variation, assignment=assignment)

        self._logger.debug(
            f'Sticky assignment for flag "{flag_key}" names variation '
            f'"{assignment.variation_id}", which is no longer on the flag, so '
            "the evaluator decided instead."
        )
        return None

    def save_ab_test(
        self,
        *,
        visitor_id: str,
        flag_key: str,
        variation: FlagDatafileVariation,
        rule_id: str,
    ) -> None:
        """Persist an ab_test assignment. Fire and forget."""
        service = self._service
        if service is None:
            return
        assignment = StickyAssignment(
            variation_id=variation.id,
            rule_id=rule_id,
            rule_type="ab_test",
            assigned_at=int(time.time() * 1000),
        )
        try:
            service.save(visitor_id, flag_key, assignment)
        except Exception as exc:  # noqa: BLE001 - fire and forget by contract
            self._logger.warn(
                "A vs B could not save a sticky-bucket assignment, so this "
                "visitor may be re-bucketed on a later request.",
                {"flag_key": flag_key, "error": str(exc)},
            )

    def attributable_rule_id(
        self, datafile: FlagDatafile, flag_key: str, hit: StickyHit, visitor_id: str
    ) -> str | None:
        """The rule id a replayed exposure can be attributed to, or ``None``.

        When the rule the assignment belongs to is gone from the datafile, the
        stored VALUE is still served (flipping a returning visitor's experience
        is worse than a gap in the data) but no exposure is recorded, because
        there is no rule to attribute it to. That case is logged loudly rather
        than dropped.
        """
        flag = _find_flag(datafile, flag_key)
        if flag is None:
            return None
        rule = next(
            (r for r in flag.rules if r.id == hit.assignment.rule_id), None
        )
        if rule is not None and any(
            allocation.variation_id == hit.variation.id
            for allocation in rule.variations
        ):
            return rule.id

        self._logger.warn(
            f'Sticky assignment for flag "{flag_key}" points at rule '
            f'"{hit.assignment.rule_id}", which no longer matches the '
            "datafile. The stored variation is still being served, but no "
            "exposure was recorded, because there is no rule left to attribute "
            "it to. Expect a gap in this experiment's exposure counts for "
            "returning visitors.",
            {"visitor_id": visitor_id, "variation_id": hit.variation.id},
        )
        return None


def _find_flag(
    datafile: FlagDatafile, flag_key: str
) -> FlagDatafileEntry | None:
    for entry in datafile.flags:
        if entry.key == flag_key:
            return entry
    return None
