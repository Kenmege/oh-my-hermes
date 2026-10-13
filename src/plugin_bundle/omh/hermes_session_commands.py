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

`loop_timer_verdict` -- is this request a loop on a timer? "loop every 5
minutes and check CI", `/loop 5m ...`, "every 10 minutes loop over the inbox"
or "5분마다 ... 루프 돌려줘" asks for a check repeated on a cadence, which is
Hermes' native `/loop` inside the session (or a cron job when it must run
unattended), not OMH's long-horizon goal loop. Only the opening of the
person's current message is read -- after the gateway material above and a
courtesy opener, never the backfill before `[New message]`:

- the loop command and its cadence come first (`loop 5m ...`, `/loop 5m ...`,
  `loop every 5 minutes ...`, `루프 돌면서 5분마다 ...`); or
- the cadence comes first and the loop is the verb of the request or its
  trailing adverbial (`every 5 minutes loop over ...`, `every 5 minutes check
  CI in a loop`, `5분마다 루프 돌려서 ...`, `5분마다 반복해서 ...`, `5분마다
  ... 루프 돌려줘`).

The same openings describe faults ("every 5 minutes my pod restarts in a
loop", "5분마다 반복해서 에러가 나요"), so the trailing `in a loop` counts only
after a verb that asks for a check, `loop` after a cadence only when the next
word reads as its object or a conjunction, and a Korean form only when its
first clause closes on a request ending.

A loop word anywhere else -- a thing the request talks about ("the retry
loop fires every 5 seconds"), or a loop the cadence does not open ("check CI
every 5 minutes in a loop") -- leaves the verdict `none`: the turn routes as
it did before this module existed. A request that opens by asking to write
or explain a `for` loop or a music loop that runs on a timer is
`construct_loop`. Either shape is OMH's goal loop instead
(`goal_loop`) when the person uses one of OMH's own forms, names the goal
loop, or sets it a goal (`_carries_goal` is the whole rule) -- with one
exception: a typed `/loop` with its cadence is Hermes' command whatever its
prompt mentions (`typed_loop_sets_goal` says when its prompt would otherwise
have been the goal loop). Every surface that has to decide -- the chat router's early
answer, the loop guard in the scorer, the route hint and the candidate line --
takes the verdict from this one function, read on the turn's executable
routing text (`reference_regions`), so none of them keeps word lists of its
own for it.

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

# -- the cadence -------------------------------------------------------------
_TIMER_UNITS = frozenset(
    {"second", "seconds", "sec", "secs", "minute", "minutes", "min", "mins", "hour", "hours", "hr", "hrs"}
)
# `_INTERVAL_TOKEN_RE` in `hermes_cli/loops.py`: hours, then minutes, then
# seconds, at least one, digits first (`30s`, `5m`, `1h30m`).
_INTERVAL_TERM = re.compile(r"^(?=\d)(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
# A count glued to a spelled unit: `5mins`, `2hrs`.
_GLUED_UNIT_TERM = re.compile(r"^[1-9]\d*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)$")
# `5분마다`, `2시간마다`, `매분`, `매시간`, where the request starts.
_KOREAN_CADENCE = r"(?:[1-9]\d*\s?(?:초|분|시간)\s?마다|매(?:초|분|시간))"
_KOREAN_CADENCE_LEAD = re.compile(rf"{_KOREAN_CADENCE}(?=\s)")

# -- the request's opening ---------------------------------------------------
# Words a request may put before its command, each with optional punctuation.
_COURTESY_LEAD = re.compile(
    r"(?:(?:please|pls|plz|kindly|hey|hi|hello|ok|okay|hermes|(?:can|could|would|will) you)[,.!]?\s+)+"
)
# What may follow `loop` when it is the verb after a cadence ("every 5 minutes
# loop over the inbox"). A noun does not ("every 10 minutes loop detection
# kills my agent").
_LOOP_VERB_CONTINUATIONS = frozenset(
    {"and", "then", "over", "through", "to", "until", "back", "it", "them"}
    | {"the", "this", "that", "these", "those", "my", "our", "a", "an"}
)
_KOREAN_LOOP = r"(?:루프|loop)(?:를|을)?\s?"
# `5분마다 루프 돌려서 ...`, `5분마다 loop 돌면서 ...`, `5분마다 반복해서 ...`:
# the loop is what the cadence runs.
_KOREAN_LOOP_PREDICATE = re.compile(
    rf"(?:반복해서(?=\s)|{_KOREAN_LOOP}(?:돌려줘|돌려 줘|돌려주세요|돌려서|돌면서|돌리면서))"
)
# `5분마다 ... 확인하는 루프 돌려줘`: the clause closes on running the loop.
_KOREAN_LOOP_CLOSE = re.compile(rf"{_KOREAN_LOOP}(?:돌려줘|돌려 줘|돌려주세요)(?=[\s.,!~]|$)")
# `every 5 minutes check CI in a loop`: the loop as the request's adverbial.
_IN_A_LOOP = "in a loop"
# The same shape describes a fault ("every 5 minutes CI fails in a loop",
# "every few minutes my agent gets stuck in a loop"), so the adverbial counts
# only after a verb that opens a request for a repeated check. The list is
# closed and leaves out verbs that are as often a sentence's subject (build,
# test, update, deploy, restart, sync, log, report): a request it misses
# routes as it did before.
_IN_A_LOOP_VERBS = frozenset(
    {"check", "run", "rerun", "ping", "poll", "watch", "monitor", "scan", "fetch", "pull", "refresh", "review"}
    | {"summarize", "post", "send", "look", "tell", "triage", "retry", "compare", "measure", "print", "query"}
    | {"hit", "curl", "read", "show", "notify", "remind", "verify", "inspect", "collect", "sweep", "probe"}
)
# A Korean clause asks for something when it closes on a request ending; the
# same words close on a statement otherwise ("5분마다 반복해서 에러가 나요").
_KOREAN_REQUEST_ENDINGS = ("줘", "줘요", "주세요", "주십시오", "하세요", "하십시오", "해라", "하라")
_CLAUSE_END = re.compile(r"[,.!?;\n，。]")
# `루프 돌면서 5분마다 ...`: the loop first, then its cadence.
_KOREAN_LOOP_FIRST = re.compile(rf"{_KOREAN_LOOP}(?:돌면서|돌려서|돌리면서)\s?{_KOREAN_CADENCE}(?=\s)")
_SENTENCE_END = re.compile(r"(?<=[.?!;])\s|\n")

# -- OMH's own forms ---------------------------------------------------------
# The skill's name and its older `omh-` label, hyphenated or spaced, wherever
# they appear in the current message ("/ulw-loop every 5m ...", "omh loop
# every hour ...", "every 2 hours ulw-loop the flaky list"). The boundary is
# Latin only, so a Korean particle after the name keeps it.
_OMH_LOOP_NAME = re.compile(r"(?<![0-9a-z_-])(?:ulw|omh)[- ]loop(?![0-9a-z_-])")
# Leading forms, read where the person's own words start.
_OMH_LOOP_LEADS = ("./loop", "$loop", "omh 루프")
_OMH_LOOP_LEAD_SIGIL = re.compile(r"^(?:\./|\$)(?=loop(?:\s|$))")

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
_IMPROVEMENT_TOKENS = frozenset({"improve", "improving", "enhance", "enhancing"})
_IMPROVEMENT_PHRASES = ("개선", "향상", "더 좋게")
# Moving a product or star measure is a goal too: "to lower install friction",
# "to get us to 10k stars", "설치 후 ... 시간을 줄여줘".
_OUTCOME_TOKENS = frozenset(
    {"lower", "lowering", "reduce", "reduces", "reducing", "cut", "cutting", "shrink", "shrinking"}
    | {"raise", "raising", "increase", "increasing", "grow", "growing", "boost", "boosting"}
)
_OUTCOME_REACH = re.compile(r"(?<![0-9a-z_-])get (?:us )?to(?![0-9a-z_-])")
_OUTCOME_PHRASES = ("줄여", "줄이", "낮춰", "낮추", "높여", "높이", "늘려", "늘리", "올려", "올리")

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
)
# How a request to write or explain one opens.
_CONSTRUCT_ASKS = (
    "write ",
    "explain ",
    "show me ",
    "implement ",
    "how do i ",
    "how do you ",
    "how to ",
    "how would i ",
    "how can i ",
    "how should i ",
)
# The construct is named within this many words of that opening.
_CONSTRUCT_ASK_REACH = 8

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


def _bare(word: str) -> str:
    return word.rstrip(",.:;!?")


def _tokens(folded: str) -> set[str]:
    """Whole tokens, hyphenated ones kept whole: `cto-loop` is not the word `loop`."""
    return set(_TOKEN.findall(folded))


def _past_cadence(words: list[str], at: int) -> int:
    """The index after an `every <N> <unit>` cadence that starts at `words[at]`, or 0.

    The unit sits within two words of `every`: "every 5 minutes", "every
    hour", "every 5m", never "every engineer spends 30 minutes".
    """
    if len(words) <= at or words[at] not in ("every", "each"):
        return 0
    for reach in (1, 2):
        if len(words) > at + reach:
            word = _bare(words[at + reach])
            if word in _TIMER_UNITS or _interval_term(word):
                return at + reach + 1
    return 0


def _after_courtesy(text: str) -> str:
    courtesy = _COURTESY_LEAD.match(text)
    return text[courtesy.end():] if courtesy else text


def _first_sentence(text: str) -> str:
    return _SENTENCE_END.split(text, maxsplit=1)[0].strip()


def _korean_request(text: str) -> bool:
    """Whether the first clause of `text` closes on a Korean request ending."""
    return _CLAUSE_END.split(text, maxsplit=1)[0].strip().rstrip("~ ").endswith(_KOREAN_REQUEST_ENDINGS)


def _in_a_loop_request(clause: str) -> bool:
    """Whether `clause`, read after its cadence, asks for a check repeated in a loop."""
    if clause.startswith(_IN_A_LOOP):
        clause = clause[len(_IN_A_LOOP):].lstrip(" ,")
    elif not clause.endswith(f" {_IN_A_LOOP}"):
        return False
    words = clause.split(maxsplit=1)
    return bool(words) and _bare(words[0]) in _IN_A_LOOP_VERBS


def _loop_is_the_request(lead: str) -> bool:
    """Whether `lead` opens with a loop command and its cadence, in either order."""
    words = lead.split()
    if not words:
        return False
    # The native command's own syntax, `/loop [interval] <prompt>`, or the
    # cadence spelled out after it.
    if _bare(words[0]) in ("loop", "/loop") and (
        (len(words) > 1 and _interval_term(_bare(words[1]))) or _past_cadence(words, 1)
    ):
        return True
    if _KOREAN_LOOP_FIRST.match(lead):
        return _korean_request(lead)
    korean = _KOREAN_CADENCE_LEAD.match(lead)
    past = _past_cadence(words, 0)
    if korean:
        rest = lead[korean.end():].lstrip()
    elif past:
        rest = " ".join(words[past:])
    else:
        return False
    # The cadence came first; the loop has to be what it runs.
    rest = _after_courtesy(rest)
    after = rest.split(maxsplit=2)
    if after and _bare(after[0]) == "loop":
        # "loop." or "loop:" closes the clause; otherwise the next word tells
        # a verb from a noun.
        if len(after) == 1 or after[0] != "loop" or _bare(after[1]) in _LOOP_VERB_CONTINUATIONS:
            return True
    if _KOREAN_LOOP_PREDICATE.match(rest) and _korean_request(rest):
        return True
    if _in_a_loop_request(_bare(_first_sentence(rest))):
        return True
    return bool(_KOREAN_LOOP_CLOSE.search(_first_sentence(lead)))


def _asks_for_a_construct_on_a_timer(lead: str) -> bool:
    """Whether `lead` opens by asking to write or explain a code or media loop that runs on a timer."""
    sentence = _first_sentence(lead)
    if not sentence.startswith(_CONSTRUCT_ASKS):
        return False
    words = sentence.split()
    opening = f" {' '.join(_bare(word) for word in words[:_CONSTRUCT_ASK_REACH])} "
    if not any(f" {phrase} " in opening for phrase in LOOP_CONSTRUCT_PHRASES):
        return False
    return any(_past_cadence(words, at) for at in range(len(words)))


def _names_goal_loop(folded: str) -> bool:
    return bool(_GOAL_LOOP_NAME.search(folded)) or any(phrase in folded for phrase in _GOAL_LOOP_PHRASES)


def _carries_goal(folded: str) -> bool:
    """Whether the request sets the loop a goal rather than a check.

    It does when it asks to improve or enhance something; or pairs a product
    or star word with a repeat cue or an outcome verb; or pairs a star word
    with a product word.
    """
    tokens = _tokens(folded)
    if tokens & _IMPROVEMENT_TOKENS or any(phrase in folded for phrase in _IMPROVEMENT_PHRASES):
        return True
    repeated = bool(tokens & LOOP_REPEATED_IMPROVEMENT_TOKENS) or any(
        phrase in folded for phrase in LOOP_REPEATED_IMPROVEMENT_PHRASES
    )
    outcome = (
        bool(tokens & _OUTCOME_TOKENS)
        or bool(_OUTCOME_REACH.search(folded))
        or any(phrase in folded for phrase in _OUTCOME_PHRASES)
    )
    product = bool(tokens & LOOP_PRODUCT_GOAL_TOKENS) or any(phrase in folded for phrase in LOOP_PRODUCT_GOAL_PHRASES)
    north_star = bool(tokens & LOOP_NORTH_STAR_TOKENS) or any(phrase in folded for phrase in LOOP_NORTH_STAR_PHRASES)
    return ((repeated or outcome) and (product or north_star)) or (north_star and product)


@lru_cache(maxsize=1024)
def _loop_timer_reading(text: str) -> tuple[str, bool]:
    """The verdict on `text`, and whether a typed `/loop` overrode a goal-loop verdict to reach it."""
    folded = _fold(text).strip()
    # A turn Hermes wrote is no request at all, whatever its template says.
    if _host_command_turn(folded):
        return LOOP_TIMER_NONE, False
    # Only the person's current message is read: not the backfill, and not a
    # host block that never closed, which still holds whatever follows it.
    message = _after_gateway_material(folded)
    if message.startswith(_FOLDED_HOST_BLOCK_OPENERS):
        return LOOP_TIMER_NONE, False
    sender = _SENDER_PREFIX.match(message)
    lead = _after_courtesy(message[sender.end():] if sender else message)
    # OMH's names stand where the loop word would: "/ulw-loop every 5m ..." has
    # the same shape as "/loop every 5m ...", and is OMH's.
    plain = _OMH_LOOP_NAME.sub("loop", _OMH_LOOP_LEAD_SIGIL.sub("", lead, count=1))
    if _loop_is_the_request(plain):
        shape = LOOP_TIMER_NATIVE
    elif _asks_for_a_construct_on_a_timer(plain):
        shape = LOOP_TIMER_CONSTRUCT
    else:
        return LOOP_TIMER_NONE, False
    # The name is read on the whole message, so a bracketed `[ulw-loop]` that
    # the sender-prefix strip took for a display name still counts.
    goal = bool(
        _OMH_LOOP_NAME.search(message)
        or lead.startswith(_OMH_LOOP_LEADS)
        or _names_goal_loop(lead)
        or _carries_goal(lead)
    )
    # Typed out with its cadence, the native command is the native command,
    # whatever its prompt mentions.
    if lead.startswith("/loop ") and shape == LOOP_TIMER_NATIVE:
        return LOOP_TIMER_NATIVE, goal
    return (LOOP_TIMER_GOAL if goal else shape), False


def loop_timer_verdict(text: str) -> str:
    """Whether `text` opens with a loop on a timer: one of the four `LOOP_TIMER_*` verdicts.

    `text` is the turn's executable routing text, before any display-name fold.
    `goal_loop` and `none` both leave the turn to its ordinary route.
    """
    return _loop_timer_reading(text)[0]


def typed_loop_sets_goal(text: str) -> bool:
    """Whether `text` is a typed `/loop` whose prompt would otherwise make it OMH's goal loop.

    Its verdict is `native_loop` all the same; a surface that answers it can
    say how OMH's goal loop is started instead.
    """
    return _loop_timer_reading(text)[1]
