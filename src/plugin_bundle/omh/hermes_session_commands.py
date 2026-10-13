"""Hermes' own session commands, where OMH would otherwise route over them.

Read by the plugin's per-turn hook and by `omh.routing.policy` alike, so the
TUI hint and the router cannot disagree; standalone, because the bundle may
not import `omh`.

Host command turns. Two of Hermes' session commands reach the model as an
ordinary user-role turn whose text the host built from a fixed template:
`/plan` (`build_plan_prompt` in `agent/plan_prompt.py`) and every `/loop`
wakeup (`WAKEUP_PROMPT_TEMPLATE` in `hermes_cli/loops.py`), both in the floor
release (`v2026.9.7`). The host stamps neither row with a `display_kind`, so
`turn_authorship.host_synthesized_turn` cannot see them, and the template's
own opening frame is the only record of who wrote the turn. Such a turn
already carries the host's instruction for itself -- plan only and save the
plan, or re-check the watched state and end with `LOOP_COMPLETE` -- so it is
not a request for OMH to route. Read as one, the plan-mode rules (commit,
push, external actions) and the wakeup text (recurring task, stop condition)
scored ops, connector, memory, and loop skills at high confidence. Only the
frame at the very start is read; the person's words inside the turn are never
interpreted here.

A loop on a timer. "every 5 minutes", "5분마다", or `/loop 5m ...` asks for a
check repeated on a cadence, which is Hermes' native `/loop` inside the
session (or a cron job when it must run unattended), not OMH's long-horizon
goal loop. OMH's own invocation forms (`./loop`, `$loop`, `omh loop`) keep
their meaning whatever follows them.

Text is folded with the same NFKC and case fold the router's
`normalized_phrase` applies.
"""

from __future__ import annotations

import re
import unicodedata


# The opening frame of each host template, verbatim from upstream. A wakeup
# continues with its tick number and cadence (`[/loop wakeup #3, every 5m]`).
HOST_COMMAND_TURN_FRAMES = (
    "[/plan — plan mode]",
    "[/loop wakeup #",
)

_LOOP_CADENCE_UNITS = frozenset({"second", "seconds", "secs", "minute", "minutes", "mins", "hour", "hours"})
_LOOP_CADENCE_PHRASES = ("초마다", "분마다", "시간마다", "매분", "매시간", "주기적으로")
_OMH_LOOP_INVOCATION_PREFIXES = ("./loop", "$loop", "omh loop", "omh 루프")
_WORD = re.compile(r"[0-9a-z]+")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


_FOLDED_FRAMES = tuple(_fold(frame) for frame in HOST_COMMAND_TURN_FRAMES)


def host_command_turn(message: object) -> bool:
    """Whether Hermes opened this turn from its own `/plan` or `/loop` template."""
    if not isinstance(message, str):
        return False
    return _fold(message).lstrip().startswith(_FOLDED_FRAMES)


def _interval_term(term: str) -> bool:
    """An interval written as one term, the way `/loop` takes it: `30s`, `5m`, `2h`."""
    digits = term.rstrip("smh")
    return digits != term and digits.isdigit()


def native_loop_cadence(text: str) -> bool:
    """Whether the text puts a loop on a timer, outside OMH's own invocation forms.

    Says nothing about whether a loop is asked for at all; each caller pairs it
    with its own loop and goal signals.
    """
    folded = _fold(text).strip()
    if folded.startswith(_OMH_LOOP_INVOCATION_PREFIXES):
        return False
    leading = folded.split(maxsplit=2)
    # The native command's own syntax: `/loop [interval] <prompt>`.
    if len(leading) > 1 and leading[0] in ("loop", "/loop") and _interval_term(leading[1]):
        return True
    words = set(_WORD.findall(folded))
    if "every" in words and (words & _LOOP_CADENCE_UNITS or any(_interval_term(word) for word in words)):
        return True
    return any(phrase in folded for phrase in _LOOP_CADENCE_PHRASES)
