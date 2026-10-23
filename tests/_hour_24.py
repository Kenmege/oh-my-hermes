"""Run one timestamp site under both readings of hour 24, on any interpreter.

CPython 3.14 parses ``2026-10-24T24:00:00`` as midnight of the next day;
3.11 to 3.13 raise ``hour must be in 0..23``. CI runs 3.11 and 3.13 only, so a
guard against the 3.14 reading would never meet it there. ``Strict`` and
``Lenient`` give ``datetime.fromisoformat`` each reading regardless of the
interpreter running the test, and ``assert_hour_24_refused`` swaps them in for
the ``datetime`` that a site's module and the shared helper resolve.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any
from unittest import TestCase, mock

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh import iso_datetime  # noqa: E402

HOUR_24 = "2020-01-01T24:00:00Z"


def _names_hour_24(value: object) -> bool:
    return isinstance(value, str) and value[10:11] in ("T", " ") and value[11:13] == "24"


def _as(cls: type[datetime], parsed: datetime) -> datetime:
    # An instance of the patched class, so a site's own isinstance(value, datetime)
    # still holds while its module resolves `datetime` to the reading.
    return cls.combine(parsed.date(), parsed.timetz())


class Strict(datetime):
    """The 3.11-3.13 reading: hour 24 is refused."""

    @classmethod
    def fromisoformat(cls, value: str) -> datetime:  # type: ignore[override]
        if _names_hour_24(value):
            raise ValueError("hour must be in 0..23")
        return _as(cls, datetime.fromisoformat(value))


class Lenient(datetime):
    """The 3.14 reading: ``T24:00:00`` is the next day's midnight."""

    @classmethod
    def fromisoformat(cls, value: str) -> datetime:  # type: ignore[override]
        if _names_hour_24(value):
            return _as(cls, datetime.fromisoformat(value[:11] + "00" + value[13:]) + timedelta(days=1))
        return _as(cls, datetime.fromisoformat(value))


def _outcome(call: Callable[[str], Any], stamp: str) -> tuple[object, ...]:
    try:
        return ("returned", call(stamp))
    except Exception as exc:  # the comparison is the point: a raise is an outcome
        return ("raised", type(exc), str(exc))


def outcome_under(module: ModuleType, call: Callable[[str], Any], stamp: str,
                  reading: type[datetime], *, guarded: bool = True) -> tuple[object, ...]:
    """``call(stamp)`` with ``reading`` behind every ``fromisoformat`` the site can reach."""
    with ExitStack() as stack:
        for target in (module, iso_datetime):
            if isinstance(getattr(target, "datetime", None), type):
                stack.enter_context(mock.patch.object(target, "datetime", reading))
        if not guarded:
            stack.enter_context(mock.patch.object(module, "strict_fromisoformat", reading.fromisoformat))
        return _outcome(call, stamp)


def assert_hour_24_refused(case: TestCase, module: ModuleType, call: Callable[[str], Any],
                           stamp: str = HOUR_24) -> None:
    """The 3.14 reading of ``stamp`` must leave the site where 3.11 leaves it.

    The control runs the same call with the site's guard removed: if the 3.14
    reading did not change the result there either, this site never depended
    on the refusal and the case would prove nothing.
    """
    strict = outcome_under(module, call, stamp, Strict)
    case.assertEqual(outcome_under(module, call, stamp, Lenient), strict,
                     "under the 3.14 reading hour 24 must come out the way 3.11 refuses it")
    case.assertNotEqual(outcome_under(module, call, stamp, Lenient, guarded=False), strict,
                        "control: without the guard the 3.14 reading must change this result")
