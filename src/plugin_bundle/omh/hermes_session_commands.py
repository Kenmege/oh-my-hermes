"""Hermes' own session commands, where OMH would otherwise route over them.

Read by the plugin's per-turn hook and by `omh.routing.policy` alike, so the
TUI hint and the router cannot disagree; standalone, because the bundle may
not import `omh`.

Host command turns. Hermes' session commands reach the model as an ordinary
user-role turn whose text the host built from a fixed template. The CLI and
the TUI write none of these rows with a `display_kind`. The messaging gateway
stamps its `/loop` wakeups `internal_notification` (in the floor release and
on main) and, since 0.21.4, its heartbeats too, which
`turn_authorship.host_synthesized_turn` already reads; its `/plan` and
`/learn` turns (the command's own event text rewritten) and `/goal`
continuations (a synthetic event that is not internal) stay unstamped. Where
no stamp exists, the template's own opening is the only record of who wrote
the turn. Each frame below is verbatim from upstream, all present in the floor
release (`v2026.9.7`) except `/initiate-setup`, which only newer hosts send (a
frame match needs no version check: an older host never writes it):

- `/plan` -- `build_plan_prompt`, `agent/plan_prompt.py`;
- a `/loop` wakeup -- `WAKEUP_PROMPT_TEMPLATE`, `hermes_cli/loops.py`;
- a `/goal` continuation, with or without a completion contract or `/subgoal`
  criteria, or after a failed quality gate -- the `CONTINUATION_PROMPT_*`
  templates, `hermes_cli/goals.py`;
- the `/goal` kick that stands in for a goal the person just sent --
  `GOAL_ALREADY_SEEN_KICK`, same file;
- a goal-mode kanban card's continuation and finalize nudges --
  `KANBAN_GOAL_*_TEMPLATE`, same file;
- a `/heartbeat` firing -- `HEARTBEAT_PROMPT_TEMPLATE`, `hermes_cli/heartbeat.py`;
- `/learn` -- `build_learn_prompt`, `agent/learn_prompt.py`;
- `/initiate-setup` -- `HEADER`, `agent/initiate_setup_prompt.py`.

Such a turn already carries the host's instruction for itself -- plan only,
re-check the watched state, take the goal's next step, author the skill -- so
it is not a request for OMH to route. Read as one, the plan-mode rules scored
ops, connector, and memory skills; a goal continuation dispatched OMH's own
goal loop into a permission-profile question; the kanban nudges dispatched the
image-card skill; the heartbeat, `/learn`, and setup turns dispatched
automation, memory, and toolbelt skills -- all at high confidence. Only the
frame at the very start is read; the person's words inside the turn are never
interpreted here.

A frame counts only as the template writes it: the turn's first line is the
frame line (or, where the template continues it with a tick or an interval,
starts with it and closes with `]`), and the next non-blank line opens with
the template's next fixed text. `[/initiate-setup] is not working` and
`[/loop wakeup #3] what does this mean?` are a person writing. On a messaging
gateway Hermes may put its own material before the frame
(`gateway/run_inbound.py`, modelled in `jev_consent`): a Discord trigger note
or a reply pointer, each closed by `]` and a blank line, channel-history
backfill ending in `[New message]`, and a `[<name>] ` sender prefix in a
shared session. Those are removed before the frame is read.

A loop on a timer. "every 5 minutes", "5분마다", or `/loop 5m ...` asks for a
check repeated on a cadence, which is Hermes' native `/loop` inside the
session (or a cron job when it must run unattended), not OMH's long-horizon
goal loop. OMH's own invocation forms (`./loop`, `$loop`, `omh loop`, and
the skill's own name `ulw-loop`, or `omh-loop`, anywhere in the text) keep their meaning
whatever follows them. An interval term has the shape `/loop` accepts
(`30s`, `5m`, `2h`, `1h30m`).

Text is folded with the same NFKC and case fold the router's
`normalized_phrase` applies.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .jev_consent import _BACKFILL_SEPARATOR, _HOST_BLOCK_END, _HOST_BLOCK_OPENERS, _SENDER_PREFIX


@dataclass(frozen=True)
class HostFrame:
    """A host template's first line and the fixed text its next line opens with.

    `opening` is the whole first line, or its fixed start when `open_ended`
    (the template continues the line with a tick or an interval and closes it
    with `]`). `next_line` is "" where the template's next line is the
    person's own words or a file the host attaches.
    """

    opening: str
    next_line: str = ""
    open_ended: bool = False


# Verbatim from upstream; see the module docstring for each template's source.
HOST_COMMAND_TURN_FRAMES = (
    HostFrame("[/plan — plan mode]", "For this turn, you are in PLAN MODE"),
    HostFrame("[/loop wakeup #", "Recurring task: ", open_ended=True),
    HostFrame("[Continuing toward your standing goal]", "Goal: "),
    HostFrame("[Continuing toward your standing goal — a quality gate failed]", "Goal: "),
    HostFrame("[Goal set] Continue with the goal you were just given; there is no need to re-read it."),
    HostFrame("[Continuing toward this kanban task — judge says it is not done yet]", "Reason: "),
    HostFrame("[The work looks complete, but the task is still open]", "Reason: "),
    HostFrame("[Heartbeat — recurring instruction, fires every ", open_ended=True),
    HostFrame(
        "[/learn] The user wants you to learn a reusable skill from the request below, and save it.",
        "THE REQUEST:",
    ),
    HostFrame("[/initiate-setup]"),
)

# Set on the route hint payload when the hint table read the message as a
# loop on a timer with no goal beside it, so the candidate line, which ranks
# the catalog on its own, leaves the goal loop off on the same verdict.
NATIVE_LOOP_CADENCE_HINT_KEY = "native_loop_cadence"

_LOOP_CADENCE_UNITS = frozenset({"second", "seconds", "secs", "minute", "minutes", "mins", "hour", "hours"})
_LOOP_CADENCE_PHRASES = ("초마다", "분마다", "시간마다", "매분", "매시간", "주기적으로")
_OMH_LOOP_INVOCATION_PREFIXES = ("./loop", "$loop", "omh loop", "omh 루프")
# The skill's own name and its older `omh-` label, word-bounded, wherever
# they appear ("use the ulw-loop skill every 2 hours", "/ulw-loop ...").
_OMH_LOOP_SKILL_NAME = re.compile(r"(?<![\w-])(?:ulw|omh)-loop(?![\w-])")
_WORD = re.compile(r"[0-9a-z]+")
# `_INTERVAL_TOKEN_RE` in `hermes_cli/loops.py`: hours, then minutes, then
# seconds, at least one, digits first.
_INTERVAL_TERM = re.compile(r"^(?=\d)(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


_FOLDED_FRAMES = tuple(
    HostFrame(_fold(frame.opening), _fold(frame.next_line), frame.open_ended) for frame in HOST_COMMAND_TURN_FRAMES
)
_FOLDED_HOST_BLOCK_OPENERS = tuple(_fold(opener) for opener in _HOST_BLOCK_OPENERS)
_FOLDED_BACKFILL_SEPARATOR = _fold(_BACKFILL_SEPARATOR)


def _opens_with_frame(text: str) -> bool:
    first, _, rest = text.partition("\n")
    first = first.rstrip()
    rest = rest.lstrip("\r\n")
    for frame in _FOLDED_FRAMES:
        if frame.open_ended:
            opens = first.startswith(frame.opening) and first.endswith("]")
        else:
            opens = first == frame.opening
        if opens and rest.startswith(frame.next_line):
            return True
    return False


def host_command_turn(message: object) -> bool:
    """Whether Hermes opened this turn from one of its own session-command templates."""
    if not isinstance(message, str):
        return False
    text = _fold(message)
    if _FOLDED_BACKFILL_SEPARATOR in text:
        text = text.rsplit(_FOLDED_BACKFILL_SEPARATOR, 1)[1]
    text = text.lstrip()
    while text.startswith(_FOLDED_HOST_BLOCK_OPENERS):
        end = text.find(_HOST_BLOCK_END)
        if end == -1:
            return False
        text = text[end + len(_HOST_BLOCK_END):].lstrip()
    if _opens_with_frame(text):
        return True
    sender = _SENDER_PREFIX.match(text)
    return bool(sender) and _opens_with_frame(text[sender.end():])


def _interval_term(term: str) -> bool:
    """An interval written as one term, the way `/loop` takes it: `30s`, `5m`, `2h`, `1h30m`."""
    match = _INTERVAL_TERM.match(term)
    return bool(match) and any(int(part or 0) for part in match.groups())


def mentions_loop(text: str) -> bool:
    """Whether the text says "loop" as a word, or `루프` / `반복해서`."""
    folded = _fold(text)
    return "loop" in _WORD.findall(folded) or "루프" in folded or "반복해서" in folded


def native_loop_cadence(text: str) -> bool:
    """Whether the text puts a loop on a timer, outside OMH's own invocation forms.

    Says nothing about whether a loop is asked for at all; each caller pairs it
    with its own loop and goal signals.
    """
    folded = _fold(text).strip()
    if folded.startswith(_OMH_LOOP_INVOCATION_PREFIXES) or _OMH_LOOP_SKILL_NAME.search(folded):
        return False
    leading = folded.split(maxsplit=2)
    # The native command's own syntax: `/loop [interval] <prompt>`.
    if len(leading) > 1 and leading[0] in ("loop", "/loop") and _interval_term(leading[1]):
        return True
    words = set(_WORD.findall(folded))
    if "every" in words and (words & _LOOP_CADENCE_UNITS or any(_interval_term(word) for word in words)):
        return True
    return any(phrase in folded for phrase in _LOOP_CADENCE_PHRASES)
