"""Hermes' own session commands, where OMH would otherwise route over them.

Two questions, each answered once, here, for the plugin's per-turn hook and
for `omh.routing` alike. Standalone, because the bundle may not import `omh`.

`host_command_turn` -- did Hermes write this turn? Hermes' session commands
reach the model as an ordinary user-role turn whose text the host built from
a fixed template. The CLI and the TUI write none of these rows with a
`display_kind`. The messaging gateway stamps its `/loop` wakeups
`internal_notification` (in the floor release and on main) and, since 0.21.4,
its heartbeats too, which `turn_authorship.host_synthesized_turn` already
reads; its `/plan` and `/learn` turns (the command's own event text rewritten)
and `/goal` continuations (a synthetic event that is not internal) stay
unstamped. Where no stamp exists, the template's own opening is the only
record of who wrote the turn. Each frame is verbatim from upstream, all
present in the floor release (`v2026.9.7`) except `/initiate-setup`, which
only newer hosts send (a frame match needs no version check: an older host
never writes it):

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

Such a turn already carries the host's instruction for itself, so it is not a
request for OMH to route. A frame counts only as the template writes it: the
turn's first line is the frame line (or, where the template continues it with
a tick or an interval, starts with it and closes with `]`), and the next
non-blank line opens with the template's next fixed text. A heartbeat's next
line is the person's own recurring instruction, so a person who types under a
pasted heartbeat frame cannot be told from the host there. On a messaging
gateway Hermes may put its own material before the frame
(`gateway/run_inbound.py`, modelled in `jev_consent`): a Discord trigger note
or a reply pointer, each closed by `]` and a blank line, channel-history
backfill ending in `[New message]`, and a `[<name>] ` sender prefix in a
shared session. Those are removed first. A host block that never closes --
including a reply pointer whose quoted text itself holds `]` and a blank line,
which is cut there -- leaves the turn a person's, which routes as it did
before this module existed.

`loop_timer_verdict` -- what is a loop on a timer? "loop every 5 minutes and
check CI", "5분마다 ... 루프 돌려줘", or `/loop 5m ...` asks for a check repeated
on a cadence, which is Hermes' native `/loop` inside the session (or a cron
job when it must run unattended), not OMH's long-horizon goal loop. The same
words name OMH's goal loop when the person uses one of its own forms, names
it, or sets it a goal, and name neither when the only loop is a `for` loop or
a music loop. The verdict is one of four, and every surface that has to
decide -- the chat router's early answer, the loop guard in the scorer, the
route hint and the candidate line -- takes it from this one function, read on
the turn's executable routing text (`reference_regions`), so none of them
keeps word lists of its own for it.

Text is folded with NFKC and a case fold, as the router's `normalized_phrase`
does, with the Unicode hyphens folded to `-`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

from .jev_consent import _BACKFILL_SEPARATOR, _HOST_BLOCK_END, _HOST_BLOCK_OPENERS, _SENDER_PREFIX


@dataclass(frozen=True)
class HostFrame:
    """A host template's first line and the fixed text its next line opens with.

    `opening` is the whole first line, or its fixed start when `open_ended`
    (the template continues the line with a tick or an interval and closes it
    with `]`). `next_line` is "" where the template's next line is the
    person's own words.
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
    # The template's body is a SKILL.md file, whose first line is the
    # frontmatter fence and nothing else.
    HostFrame("[/initiate-setup]", "---\n"),
)

# The four verdicts on a loop beside a timer.
LOOP_TIMER_GOAL = "goal_loop"  # OMH's goal loop, whatever timer sits beside it
LOOP_TIMER_NATIVE = "native_loop"  # a check repeated on a timer: Hermes' `/loop`
LOOP_TIMER_CONSTRUCT = "construct_loop"  # a code or media loop that runs on a timer
LOOP_TIMER_NONE = "none"  # no loop on a timer; nothing here decides the turn
# The two a surface answers in chat instead of offering the goal loop.
LOOP_TIMERS_ANSWERED_DIRECTLY = frozenset({LOOP_TIMER_NATIVE, LOOP_TIMER_CONSTRUCT})

# Set on the route hint payload when the verdict is one of those two, so the
# candidate line, which ranks the catalog on its own, leaves the goal loop off.
NATIVE_LOOP_CADENCE_HINT_KEY = "native_loop_cadence"

# -- the timer ---------------------------------------------------------------
_TIMER_UNITS = frozenset(
    {"second", "seconds", "sec", "secs", "minute", "minutes", "min", "mins", "hour", "hours", "hr", "hrs"}
)
_TIMER_PHRASES = ("초마다", "분마다", "시간마다", "매분", "매시간", "주기적으로")
# `_INTERVAL_TOKEN_RE` in `hermes_cli/loops.py`: hours, then minutes, then
# seconds, at least one, digits first (`30s`, `5m`, `1h30m`).
_INTERVAL_TERM = re.compile(r"^(?=\d)(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
# A count glued to a spelled unit: `5mins`, `2hrs`.
_GLUED_UNIT_TERM = re.compile(r"^[1-9]\d*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)$")

# -- OMH's own forms ---------------------------------------------------------
# The skill's name and its older `omh-` label, hyphenated or spaced, wherever
# they appear ("use the ulw-loop skill every 2 hours", "/ulw-loop ...", "omh
# loop every hour", "ulw-loop로"). The boundary is Latin only, so a Korean
# particle after the name keeps it.
_OMH_LOOP_NAME = re.compile(r"(?<![0-9a-z_-])(?:ulw|omh)[- ]loop(?![0-9a-z_-])")
# Leading forms, read where the person's own words start.
_OMH_LOOP_LEADS = ("./loop", "$loop", "omh 루프")

# -- a name for the goal loop ------------------------------------------------
_GOAL_LOOP_NAME = re.compile(
    r"(?<![0-9a-z_-])(?:goal loop|loop engineering|loopable|loopability|loop skill)(?![0-9a-z_-])"
)
_GOAL_LOOP_PHRASES = (
    "목표 루프",
    "loop 스킬",
    "루프 스킬",
    "loop로 돌릴만",
    "loop로 해야",
    "루프로 돌릴만",
    "루프로 해야",
)

# -- a goal beside the loop --------------------------------------------------
# The loop guard (`omh.routing.policy._loop_goal_guard_applies`) reads these
# same three vocabularies for requests that say no loop word at all.
LOOP_REPEATED_IMPROVEMENT_TOKENS = frozenset({"repeatedly", "iteratively", "iterate", "until"})
LOOP_REPEATED_IMPROVEMENT_PHRASES = (
    "keep improving",
    "reduce friction",
    "reducing friction",
    "until first success",
    "until first value",
    "improve until first value",
    "반복 개선",
    "계속 개선",
    "계속해서 개선",
    "줄여가며 개선",
    "줄이면서 개선",
    "막히는 부분을 계속 개선",
)
LOOP_PRODUCT_GOAL_TOKENS = frozenset({"oss", "repo", "repository", "install", "first-run", "friction", "product"})
LOOP_PRODUCT_GOAL_PHRASES = (
    "first run",
    "first-run",
    "first run experience",
    "first successful value",
    "install to first value",
    "why users do not feel value",
    "스타급 oss",
    "star-worthy oss",
    "설치 후",
    "설치부터 첫 성공",
    "10분 안에 가치",
    "가치 못 느끼는",
    "첫 성공",
    "첫 성공까지",
)
LOOP_NORTH_STAR_TOKENS = frozenset({"star", "stars", "star-worthy", "starworthy", "10k-star", "100k-star", "adoption"})
LOOP_NORTH_STAR_PHRASES = (
    "star worthy",
    "star-worthy",
    "first-run friction",
    "10k star",
    "10k-star",
    "100k star",
    "100k-star",
)
# Asking the loop to make something better is a goal on its own; a check is not.
_IMPROVEMENT_TOKENS = frozenset({"improve", "improving"})
_IMPROVEMENT_PHRASES = ("개선",)

# -- a loop that is a thing, not a request -----------------------------------
LOOP_CONSTRUCT_PHRASES = (
    "for loop",
    "while loop",
    "event loop",
    "bash loop",
    "shell loop",
    "loop in python",
    "loop in bash",
    "music loop",
    "audio loop",
    "video loop",
    "game loop",
    "for 루프",
    "while 루프",
    "루프문",
)

_TOKEN = re.compile(r"[0-9a-z][0-9a-z_-]*")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("‐", "-")


_FOLDED_FRAMES = tuple(
    HostFrame(_fold(frame.opening), _fold(frame.next_line), frame.open_ended) for frame in HOST_COMMAND_TURN_FRAMES
)
_FOLDED_HOST_BLOCK_OPENERS = tuple(_fold(opener) for opener in _HOST_BLOCK_OPENERS)
_FOLDED_BACKFILL_SEPARATOR = _fold(_BACKFILL_SEPARATOR)


def _opens_with_frame(text: str) -> bool:
    first, _, rest = text.replace("\r\n", "\n").partition("\n")
    first = first.rstrip()
    rest = rest.lstrip("\n")
    for frame in _FOLDED_FRAMES:
        if frame.open_ended:
            opens = first.startswith(frame.opening) and first.endswith("]")
        else:
            opens = first == frame.opening
        if opens and rest.startswith(frame.next_line):
            return True
    return False


def _after_gateway_material(text: str) -> str:
    """The folded text after the backfill and host blocks a gateway prepends; a block
    that never closes is left in place."""
    if _FOLDED_BACKFILL_SEPARATOR in text:
        text = text.rsplit(_FOLDED_BACKFILL_SEPARATOR, 1)[1]
    text = text.lstrip()
    while text.startswith(_FOLDED_HOST_BLOCK_OPENERS):
        end = text.find(_HOST_BLOCK_END)
        if end == -1:
            break
        text = text[end + len(_HOST_BLOCK_END):].lstrip()
    return text


def _host_command_turn(folded: str) -> bool:
    text = _after_gateway_material(folded)
    # A host block still open here never closed; what follows it is inside it.
    if text.startswith(_FOLDED_HOST_BLOCK_OPENERS):
        return False
    if _opens_with_frame(text):
        return True
    sender = _SENDER_PREFIX.match(text)
    return bool(sender) and _opens_with_frame(text[sender.end():])


def host_command_turn(message: object) -> bool:
    """Whether Hermes opened this turn from one of its own session-command templates."""
    return isinstance(message, str) and _host_command_turn(_fold(message))


def _interval_term(term: str) -> bool:
    match = _INTERVAL_TERM.match(term)
    if match:
        return any(int(part or 0) for part in match.groups())
    return bool(_GLUED_UNIT_TERM.match(term))


def _tokens(folded: str) -> set[str]:
    """Whole tokens, hyphenated ones kept whole: `cto-loop` is not the word `loop`."""
    return set(_TOKEN.findall(folded))


def _on_a_timer(folded: str, lead: str) -> bool:
    leading = lead.split(maxsplit=2)
    # The native command's own syntax: `/loop [interval] <prompt>`.
    if len(leading) > 1 and leading[0] in ("loop", "/loop") and _interval_term(leading[1]):
        return True
    tokens = _tokens(folded)
    if "every" in tokens and (tokens & _TIMER_UNITS or any(_interval_term(token) for token in tokens)):
        return True
    return any(phrase in folded for phrase in _TIMER_PHRASES)


def _says_loop(folded: str) -> bool:
    return "loop" in _tokens(folded) or "루프" in folded or "반복해서" in folded


def _names_goal_loop(folded: str) -> bool:
    return bool(_GOAL_LOOP_NAME.search(folded)) or any(phrase in folded for phrase in _GOAL_LOOP_PHRASES)


def _carries_goal(folded: str) -> bool:
    tokens = _tokens(folded)
    if tokens & _IMPROVEMENT_TOKENS or any(phrase in folded for phrase in _IMPROVEMENT_PHRASES):
        return True
    repeated = bool(tokens & LOOP_REPEATED_IMPROVEMENT_TOKENS) or any(
        phrase in folded for phrase in LOOP_REPEATED_IMPROVEMENT_PHRASES
    )
    product = bool(tokens & LOOP_PRODUCT_GOAL_TOKENS) or any(phrase in folded for phrase in LOOP_PRODUCT_GOAL_PHRASES)
    north_star = bool(tokens & LOOP_NORTH_STAR_TOKENS) or any(phrase in folded for phrase in LOOP_NORTH_STAR_PHRASES)
    return (repeated and (product or north_star)) or (north_star and product)


@lru_cache(maxsize=1024)
def loop_timer_verdict(text: str) -> str:
    """What a loop on a timer in `text` is: one of the four `LOOP_TIMER_*` verdicts.

    `text` is the turn's executable routing text, before any display-name fold.
    `goal_loop` and `none` both leave the turn to its ordinary route.
    """
    folded = _fold(text).strip()
    # A turn Hermes wrote is no request at all, whatever its template says.
    if _host_command_turn(folded):
        return LOOP_TIMER_NONE
    # The leading forms are read where the person's own words start.
    lead = _after_gateway_material(folded)
    sender = _SENDER_PREFIX.match(lead)
    if sender:
        lead = lead[sender.end():]
    if not _on_a_timer(folded, lead):
        return LOOP_TIMER_NONE
    # The name is read on the whole text, so a bracketed `[ulw-loop]` that the
    # sender-prefix strip took for a display name still counts.
    if _OMH_LOOP_NAME.search(folded) or lead.startswith(_OMH_LOOP_LEADS):
        return LOOP_TIMER_GOAL
    # Typed out, the native command is the native command, whatever its prompt says.
    if lead.startswith("/loop "):
        return LOOP_TIMER_NATIVE
    beyond_constructs = folded
    for phrase in LOOP_CONSTRUCT_PHRASES:
        beyond_constructs = beyond_constructs.replace(phrase, " ")
    if _says_loop(beyond_constructs):
        if _names_goal_loop(beyond_constructs) or _carries_goal(folded):
            return LOOP_TIMER_GOAL
        return LOOP_TIMER_NATIVE
    if not _says_loop(folded):
        return LOOP_TIMER_NONE
    # The only loop is a construct: a question about it, unless a goal rides along.
    return LOOP_TIMER_GOAL if _carries_goal(folded) else LOOP_TIMER_CONSTRUCT
