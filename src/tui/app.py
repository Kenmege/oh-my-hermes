"""The OMH TUI's event loop and entry point.

`main` is what `python -m omh.tui` runs once Hermes' launcher has handed the
frontend its environment. Everything that can stop the TUI from starting is
settled before the first byte is drawn, and each such failure exits with
`FALLBACK_EXIT`, so `omh` opens Hermes' own terminal instead: a terminal that
cannot run the TUI, a gateway that does not start, and a startup call this
Hermes no longer understands. An attach hand-off is refused with exit 1 and a
line naming `hermes --tui --resume <id>` (0 would make `omh` print a resume
hint for a session this TUI never opened). SIGTERM and SIGHUP leave through
`SystemExit(128 + signum)`, so the terminal is restored and the gateway reaped
as on any other exit.

`App` is the loop. One thread owns the terminal and every model: it reads
input with a short timeout, routes keys (the pager while it is open, then
Ctrl+O and Ctrl+L, an open prompt slot, the session picker, the agent list,
the mode, effort and key-list keys, the attachment keys and Esc, the
completion menu, the queue's Up, then the composer), takes everything the
gateway sent since the last step, takes the newest HUD snapshot, and
repaints when something changed or a spinner is due. Calls made from the
loop go out with `GatewayClient.request` and their answers come back through
`poll`, so a slow slash command never freezes the screen.

Protocol rules, measured against Hermes' gateway and enforced here: every
server request is answered (a slot, or an immediate decline with a notice),
because over stdio the gateway otherwise waits out its whole deadline; events
whose envelope `session_id` is not the live session are dropped, except
`gateway.*` and withdrawals, which match by request id; the stored session id
is tracked from every `session.info` and written (id only, mode 0600) to
`HERMES_TUI_ACTIVE_SESSION_FILE`; input sent while a turn runs is queued in
memory unless Hermes' config explicitly asks for `interrupt` or `steer`.
The queue is the TUI's own (the gateway has no call that withdraws a prompt):
its prompts are listed above the composer's top rule, and Up on an empty
composer takes all of them back into the composer.
Ctrl+Enter and `/steer <text>` send what the person typed into the running
turn (`session.steer`); the TUI steers nothing on its own, and text Hermes
does not take as a steer goes as a prompt instead of being dropped. A steer
answered after its session was left goes back to the composer instead: the
session now live never receives it.

Input modes: Shift+Tab cycles normal, accept and plan. Accept is Hermes'
per-session yolo, so entering and leaving it are `config.set` calls that
always name this session and an explicit value and never a `scope`; the mode
shown changes only once Hermes confirms it for this session, an answer that
confirms anything else is treated as a failure and followed by an explicit
off, and `session.info` corrects what is shown (one saying approvals are on
within `YOLO_SETTLE_SECONDS` of a confirmed on waits for the next: it may be a
snapshot Hermes took before the switch). With approvals off
Hermes sends no approval request, so the TUI still answers none. Plan is the
TUI's own flag, apart from what accept shows: a prompt typed while it is on
goes as `/plan <text>`. When Hermes has approvals off on its own (an off it
confirmed did not end them, or they were off without the TUI turning them
on), accept stays shown and Shift+Tab turns only plan on and off, sending
nothing.
Ctrl+T and `/effort <level>` set the session's reasoning effort the same way
(`config.set` with the session id); the level shown is the one Hermes
confirmed or reports in `session.info`.

Subagents: every `subagent.*` event of the live session is folded into one
`subagents.Roster`, which a new turn and a new session empty. While it holds
an agent the mode row carries a strip of them, Down on an empty composer
gives the keys to a list of them (the one thing that moves the composer: see
`frame`), and Enter or a click opens one agent's log in the pager. The log is
read-only: `subagent.tail` is asked for the open agent while it runs, at most
every `TAIL_POLL_SECONDS`, and no more once `TAIL_MISSES_BEFORE_STOP` answers
in a row had no tail, until that agent sends a frame; nothing is ever sent to
a subagent. Mouse reports are on only while a click has an agent to land on
and the row it would land on is certain: a turn running with agents named on
the strip, or the list open. A plain left click on one of those cells opens
that agent's log, or on the list's main row leaves the list; every other
report does nothing. None types, however a pause splits it across reads
(`keys`), and the one way a report can act as a key is a split right after
its first byte that lasts over `keys.REPORTING_ESCAPE_SECONDS`: that ESC is
Esc, as in tmux and vim.

Prompt text, the transcript, attachments, secrets, server-request frames and
the gateway's stderr stay in memory and on the person's own terminal; nothing
here writes them anywhere. The files the TUI writes are metadata: that
session file, and what OMH's HUD reader (`panels.HudPoller`, its fan-out
cache under `<omh_home>/runtime/`) and the model-chain picker (`/omh-model`'s
saved chains) write under the OMH home. An attached image is a path the
gateway resolves and queues itself (`image.attach`, `clipboard.paste`,
`input.detect_drop`); the TUI keeps only its name for the chip row.
"""

from __future__ import annotations

import base64
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import json
import os
import signal
import sys
import time
from types import FrameType
from typing import Any, Protocol, TextIO

from ..plugin_bundle.omh.runtime_reader import elapsed_text
from ..commands.theme_picker import (
    KEY_DOWN,
    KEY_ENTER,
    KEY_LEFT,
    KEY_NONE,
    KEY_QUIT,
    KEY_RIGHT,
    KEY_UP,
    plain_key,
)
from ..skin_pack import default_theme, skin_colors, theme_for_name, theme_for_skin_name, theme_names
from . import gateway_contract as contract
from .composer import COMPLETE, EXIT, INTERRUPT, SUBMIT, Composer, ComposerAction
from .frame import MODE_ACCEPT, MODE_ACCEPT_PLAN, MODE_NORMAL, MODE_PLAN, STATUS_SEPARATOR, build_frame, sticky_line
from .gateway import (
    Event,
    GatewayCallError,
    GatewayClient,
    GatewayExited,
    GatewayTimeout,
    GatewayUnavailable,
    Response,
    ServerRequest,
    attach_refused,
    gateway_spawn,
)
from .keys import (
    BACKSPACE,
    BUTTON_LEFT,
    CHAR,
    DOWN,
    ENTER,
    ESCAPE,
    LEFT,
    RIGHT,
    TAB,
    UNKNOWN,
    UP,
    Key,
    KeyDecoder,
    Mouse,
    Paste,
)
from .launch import FALLBACK_EXIT
from .layout import Budget, slot_cap, under_composer
from .markdown import clean_text
from .pager import ALT_SCREEN_OFF, ALT_SCREEN_ON, Pager
from .panels import SPINNER_FRAMES, HudPoller, HudSnapshot, panel_lines, status_segments
from .prompts import CHOICE_DIGITS, Answer, ImmediateAnswer, PromptSlot, open_request
from .screen import CLEAR_SCREEN_AND_SCROLLBACK, CURSOR_HOME, HIDE_CURSOR, SHOW_CURSOR, LiveRegion
from .session_picker import PICK, SessionPicker, session_rows
from .shortcuts import shortcut_lines
from .slash import (
    COMMANDS,
    DROPPED,
    HERMES,
    LOCAL,
    Completion,
    SlashInvocation,
    catalog_pairs,
    completions,
    dropped_notice,
    hermes_notice,
    parse_slash,
    plan_invocation,
)
from .style import CSI, Line, Span, Style
from .subagents import MAIN_ID, RUNNING, Hit, Roster
from .terminal import Terminal, terminal_supported
from .theme import DEFAULT_THEME, Theme, build_theme, theme_named
from .transcript import NOTICE_ERROR, NOTICE_WARN, AssistantBlock, Transcript, as_text
from .width import cell_width, line_width, truncate_left, truncate_line, wrap_line
from ..version import __version__

READ_TIMEOUT_SECONDS = 0.05
TICK_SECONDS = 0.1
# A window drag sends a burst of SIGWINCH; redraw once the size has held this long.
RESIZE_SETTLE_SECONDS = 0.15
EXIT_CONFIRM_SECONDS = 2.0
# Every key a prompt slot takes, and the composer's Enter, is ignored this long
# after the slot opens, so Enter, an arrow or a digit typed for the composer as
# the request arrives cannot answer it (an approval, a clarify, a value prompt).
SLOT_ARM_SECONDS = 0.4
STARTUP_TAIL_LINES = 10
INTERRUPTED_EXIT = 130
# Closing the gateway on exit: stdin EOF, then terminate, then kill. The live
# region is already cleared and the terminal restored before this wait starts.
CLOSE_GRACE_SECONDS = 0.5
TERMINATE_GRACE_SECONDS = 2.0
# By default these end the process without unwinding: the shell stays raw and the
# gateway is orphaned. Raised as SystemExit, they restore and reap on the way out.
EXIT_SIGNALS = ("SIGTERM", "SIGHUP")

BANNER_BRAND = "⚚ OH-MY-HERMES"
BANNER_HINT = "/ commands · @ files · ? shortcuts · ⇧⇥ modes"
PLACEHOLDER = "Ask Hermes anything"
BARE_HERMES_NOTE = "Bare `hermes` opens Hermes' own terminal."

# The mode row under the composer frame. In normal mode it carries the keys that matter now.
IDLE_KEYS_HINT = "? shortcuts · ⇧⇥ mode · ctrl+t effort"
INTERRUPT_KEY_HINT = "esc interrupt"
STEER_KEY_HINT = "ctrl+enter steer"  # only once the terminal has sent a key in a CSI u or modifyOtherKeys report
STEER_COMMAND_HINT = "/steer <text> steers"
QUEUED_KEY_HINT = "↑ edit queued"
ACCEPT_LABEL = "⏵⏵ accept all on"
ACCEPT_HINT = " · approvals off for this session (⇧⇥ to cycle)"
PLAN_LABEL = "⏸ plan mode on"
PLAN_HINT = " (⇧⇥ to cycle)"
# The mode on the status line, where the terminal is too short for the mode row.
MODE_CHIPS = {MODE_ACCEPT: "⏵⏵ accept", MODE_PLAN: "⏸ plan"}
MODE_ROW_GAP = 3
# The context figure turns warn, then bad, on the status line.
CONTEXT_WARN_PERCENT = 80
CONTEXT_BAD_PERCENT = 95

BUSY_QUEUE = "queue"
BUSY_MODES = frozenset({"queue", "steer", "interrupt"})

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_WAITING = "waiting on you"

# Accept mode is Hermes' per-session yolo. The TUI sends these words; Hermes answers "1" or "0".
YOLO_ON = "on"
YOLO_OFF = "off"
YOLO_ANSWERS = {True: "1", False: "0"}
# Hermes builds `session.info` on other threads too, reading yolo before its slow probes, so one snapshot
# taken before a confirmed on can arrive after it. For this long after a confirmed on, a `session.info`
# saying approvals are on does not take accept off the composer by itself.
YOLO_SETTLE_SECONDS = 2.0
ACCEPT_REFUSED_NOTICE = (
    "Hermes did not turn approvals off for this session alone ({}). "
    "Accept all stays off, and the OMH TUI asked Hermes to switch it back off."
)
ACCEPT_STUCK_NOTICE = "Hermes did not confirm that this session asks for approvals again ({}). Accept all may still be on."
# Said once per session, the first time Shift+Tab meets approvals Hermes turned off on its own.
HERMES_APPROVALS_NOTICE = (
    "Hermes itself has approvals off for this session: its approvals.mode is off, it was started with --yolo, "
    "or /yolo turned them off. Accept all stays shown, and Shift+Tab now turns plan mode on and off "
    "without changing Hermes' setting."
)
SWITCH_BACK_FAILED_NOTICE = "Hermes did not confirm the switch back either ({}). Check /yolo in Hermes."

# Ctrl+T steps through these; `/effort` takes any level in `contract.REASONING_LEVELS`.
EFFORT_CYCLE = ("low", "medium", "high", "xhigh", "max")
EFFORT_REFUSED_NOTICE = "Hermes did not set the reasoning effort to {} ({}); it stays as shown."

# The prompts waiting for the running turn, listed above the composer's top rule.
QUEUE_GLYPH = "⧗ "
QUEUE_ROWS = 3
QUEUE_EDIT_HINT = "↑ edit"
QUEUE_HINT_GAP = 2

STEERED_NOTICE = "↪ steered: {}"
STEER_REJECTED = "rejected"  # `session.steer`'s status when the turn ended before the text arrived
STEER_USAGE = "Usage: /steer <text to add to the running turn>"
STEER_LEFT_NOTICE = (
    "Hermes answered a steer after you left its session. "
    "Its text is back in the composer and was not sent to this one."
)

# Subagents: the strip on the mode row, the list Down opens, and one agent's log in the pager.
SUBAGENT_EVENT_PREFIX = "subagent."
STRIP_SEPARATOR = " · "
AGENT_LIST_ROWS = 7  # main and six agents; a longer roster scrolls
AGENT_LIST_HINT = "↑↓ select · ⏎ view log · esc back"
AGENT_LOG_KEYS = "←/→ agents"
TAIL_POLL_SECONDS = 2.0
# Hermes answers `available: false` while an agent's log is not there yet, as it starts. Five answers in a row
# like that (ten seconds at the pace above) stop the calls for that agent, so one whose `subagent.complete`
# never comes is not asked forever; a new frame for it asks again.
TAIL_MISSES_BEFORE_STOP = 5
# A call with no answer this long no longer holds the next one back; its answer, if it still comes, is dropped.
TAIL_ANSWER_SECONDS = 10.0
# On the way out of a run that had mouse reports on: the terminal acts on the off a moment later, so
# reports may still be on their way. A cursor position request written after the off is answered after
# all of them (a terminal handles what it is sent in order), so input is read and dropped up to that
# answer, `ESC[<row>;<col>R`, instead of landing on the shell's prompt; for at most this long, which a
# round trip over SSH fits in.
EXIT_DRAIN_SECONDS = 0.25
CURSOR_POSITION_REQUEST = f"{CSI}6n"
CURSOR_REPORT_START = b"\x1b["
CURSOR_REPORT_FINAL = b"R"

# One BEL when an approval opens, so a terminal in the background can flag the tab or bounce.
BELL = "\a"
BELL_ENV = "OMH_TUI_BELL"
BELL_OFF = "0"

EXIT_HINT = "Ctrl+C again to exit"
INTERRUPT_HINT = "esc to interrupt"
OSC52_COPY = "\x1b]52;c;{}\x07"
MENU_NAME_CELLS = 16
SESSION_LIST_LIMIT = 50
# `complete.path` goes out once the `@` token has held still this long.
PATH_COMPLETE_DEBOUNCE_SECONDS = 0.12
ATTACH_ICON = "📎"
ATTACH_HINT = "Backspace removes"
NO_SESSION_NOTICE = "No session is open yet; try again in a moment."
CONNECTION_NOTICE = (
    "Hermes is waiting for an app connection, which only Hermes' Desktop app or `hermes --tui` can complete. "
    "Esc interrupts the turn."
)
YOLO_NOTE = (
    "Hermes commands go to Hermes as you type them, /yolo included: it turns off Hermes' approval prompts, "
    "so the OMH TUI then has none to show."
)
# The keys themselves are `shortcuts.SHORTCUTS`, drawn by `?` and /keys; /help only points there.
HELP_KEYS = "Keys: ? on an empty composer, or /keys, lists every key."
# A command's long output that arrives while a view has the alternate screen; the output follows it.
VIEW_OPEN_NOTICE = "/{name}: {lines} lines arrived while a view was open, so they are here instead of in the pager."
GUIDE_KEY = "?"


class TerminalLike(Protocol):
    mouse_reporting: bool

    def __enter__(self) -> Any: ...
    def __exit__(self, *exc: object) -> Any: ...
    def size(self) -> tuple[int, int]: ...
    def resized(self) -> bool: ...
    def read(self, timeout: float) -> bytes: ...
    def write(self, text: str) -> None: ...
    def set_mouse(self, enabled: bool) -> None: ...


class PollerLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def set_session(self, stored_id: str) -> None: ...
    def take(self) -> HudSnapshot | None: ...


@dataclass(frozen=True)
class _Pending:
    """A call sent from the loop: what to do with its result, and (optionally) with its error.

    `on_error` returns True when it handled the error; otherwise the error becomes a notice.
    """

    method: str
    on_result: Callable[[dict[str, Any]], None]
    on_error: Callable[[dict[str, Any]], bool] | None = None


@dataclass(frozen=True)
class Attachment:
    """An image the gateway queued for the next message; the gateway holds the file, this is its label."""

    path: str
    name: str
    size: str  # "1280×720", or "" when the gateway could not read the dimensions


@dataclass(frozen=True)
class PathItem:
    """One `complete.path` row: `text` replaces the `@` token, `display` and `meta` are what the menu shows."""

    text: str
    display: str
    meta: str


# ── small pure helpers ────────────────────────────────────────────────────


def busy_mode(config_result: Mapping[str, Any]) -> str:
    """`display.busy_input_mode` from a `config.get {key: "full"}` answer; queue unless it says otherwise.

    The `busy` getter cannot be used: it answers `interrupt` when the key is
    unset (the server's default), which hides exactly the case the TUI must
    read as `queue`, the default Hermes' own TUI applies.
    """
    config = config_result.get("config")
    display = config.get("display") if isinstance(config, dict) else None
    raw = display.get("busy_input_mode") if isinstance(display, dict) else None
    value = raw.strip().lower() if isinstance(raw, str) else ""
    return value if value in BUSY_MODES else BUSY_QUEUE


def _compact_count(value: float) -> str:
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= size:
            return f"{value / size:.1f}".removesuffix(".0") + suffix
    return str(int(value))


def context_text(usage: object) -> str:
    """`ctx 13.9k/1M 1%` from a `session.usage` / `session.info` usage block; "" when it has no context figures."""
    if not isinstance(usage, dict):
        return ""
    used = usage.get("context_used")
    limit = usage.get("context_max")
    if not isinstance(used, int | float) or not isinstance(limit, int | float) or limit <= 0:
        return ""
    text = f"ctx {_compact_count(used)}/{_compact_count(limit)}"
    percent = usage.get("context_percent")
    if isinstance(percent, int | float) and not isinstance(percent, bool):
        text += f" {percent:.0f}%"
    return text


def context_percent(usage: object) -> float | None:
    """How full the context is, 0 to 100, from the same usage block; None without the figures."""
    if not isinstance(usage, dict) or not context_text(usage):
        return None
    percent = usage.get("context_percent")
    if isinstance(percent, int | float) and not isinstance(percent, bool):
        return float(percent)
    return usage["context_used"] / usage["context_max"] * 100


def mode_label(mode: str, theme: Theme) -> Line:
    """The mode's own words on the mode row: accept and plan have them, both when both are on, normal has none."""
    accept: Line = (Span(ACCEPT_LABEL, replace(theme.mode_accept, bold=True)),)
    plan: Line = (Span(PLAN_LABEL, replace(theme.mode_plan, bold=True)),)
    if mode == MODE_ACCEPT_PLAN:
        return (*accept, Span(STRIP_SEPARATOR, theme.hint), *plan)
    return {MODE_ACCEPT: accept, MODE_PLAN: plan}.get(mode, ())


def strip_column(mode: str, theme: Theme) -> int:
    """The mode row cell the agent strip starts on: after the mode's label and a separator, or the row's first."""
    label = mode_label(mode, theme)
    return line_width(label) + cell_width(STRIP_SEPARATOR) if label else 0


def mode_row_line(
    *,
    mode: str,
    busy: bool,
    queued: int,
    keyboard_protocol: bool,
    effort: str,
    width: int,
    theme: Theme,
    strip: Line = (),
) -> Line:
    """The row under the composer frame: the mode's label (accept, plan), the agent strip, the keys that matter now, the effort.

    `strip` is the roster's strip while it holds an agent, drawn from cell
    `strip_column(mode, theme)` on. The label always survives, and the strip
    after it: the hint is cut from its end first, and the effort on the right
    goes only when even the label and the strip beside it do not fit.
    """
    label = mode_label(mode, theme)
    if mode == MODE_ACCEPT:
        hint = ACCEPT_HINT
    elif mode in (MODE_PLAN, MODE_ACCEPT_PLAN):
        hint = PLAN_HINT
    elif busy:
        keys = [INTERRUPT_KEY_HINT, STEER_KEY_HINT if keyboard_protocol else STEER_COMMAND_HINT]
        hint = " · ".join(keys + ([QUEUED_KEY_HINT] if queued else []))
    else:
        hint = IDLE_KEYS_HINT
    if strip:
        label = (*label, Span(STRIP_SEPARATOR, theme.hint), *strip) if label else strip
        hint = hint if hint.startswith(STRIP_SEPARATOR) else STRIP_SEPARATOR + hint.strip()
    right: Line = (Span(f"effort {effort}", theme.muted),) if effort else ()
    room = width - line_width(label) - (line_width(right) + MODE_ROW_GAP if right else 0)
    if room < 0:
        right, room = (), width - line_width(label)
    left = truncate_line(label, width) + truncate_line((Span(hint, theme.hint),), max(room, 0))
    if not right:
        return left
    return (*left, Span(" " * (width - line_width(left) - line_width(right))), *right)


def yolo_answer_problem(result: Mapping[str, Any], on: bool) -> str:
    """Why a `config.set yolo` answer does not confirm the switch that was asked for; "" when it does.

    The answer must carry the value asked for ("1" on, "0" off) and
    `scope: "session"`. Any other scope means Hermes wrote something wider
    (`scope: "global"` is the permanent `approvals.mode`), so it is a failure.
    The scope proves less than it reads, though: Hermes 6c61c3e27d answers
    `scope: "session"` from its sessionless branch too, the one that sets
    `HERMES_YOLO_MODE` for the whole gateway process (`_set_yolo` in
    `tui_gateway/methods_config_set.py`), so the answer cannot say which
    branch ran. What keeps the switch on one session is settled before the
    answer: every yolo call names this session, `gateway_contract.call_problem`
    refuses to send a `config.set` without a session id, and Hermes answers a
    session id it does not hold with 4001 instead of falling back to the
    process switch (`_SESSION_SCOPED_KEYS` in the same file).
    """
    scope, value = result.get("scope"), result.get("value")
    if scope != contract.CONFIG_SET_SESSION_SCOPE:
        return f"it answered scope {json.dumps(scope)}"
    if value != YOLO_ANSWERS[on]:
        return f"it answered value {json.dumps(value)}"
    return ""


def first_line(text: str) -> str:
    """The first line of a prompt, for the rows that name it in one line."""
    return text.strip().split("\n", 1)[0]


def queue_lines(prompts: Sequence[str], width: int, max_rows: int, theme: Theme) -> list[Line]:
    """The queued prompts, next to be sent first: `⧗ <first line>` each, never more than `max_rows` rows.

    At most `QUEUE_ROWS` prompts are named and `+N more` counts the rest;
    `↑ edit`, the key that takes them back into the composer, ends the last row.
    """
    if not prompts or max_rows <= 0:
        return []
    named = min(len(prompts), QUEUE_ROWS, max_rows)
    if named < len(prompts):
        named = min(named, max_rows - 1)  # the last row counts the rest
    rows: list[Line] = [
        (Span(QUEUE_GLYPH, theme.muted), Span(clean_text(first_line(text)), theme.text)) for text in prompts[:named]
    ]
    if named < len(prompts):
        rows.append((Span(f"+{len(prompts) - named} more", theme.muted),))
    hint: Line = (Span(QUEUE_EDIT_HINT, theme.hint),)
    last = truncate_line(rows[-1], max(width - line_width(hint) - QUEUE_HINT_GAP, 0))
    rows[-1] = (*last, Span(" " * (width - line_width(last) - line_width(hint))), *hint)
    return [truncate_line(row, width) for row in rows]


def initial_theme(ready_payload: Mapping[str, Any]) -> Theme:
    """The theme for `gateway.ready`'s skin name (Hermes' `display.skin`); sky unless it names an OMH skin.

    Skin names only: a foreign skin that happens to be called `amber` is not OMH's amber.
    """
    skin = ready_payload.get("skin")
    name = as_text(skin.get("name")) if isinstance(skin, dict) else ""
    return build_theme(theme_for_skin_name(name) or default_theme())


def short_path(path: str, home: str) -> str:
    """`path` with the home directory written as `~`."""
    home = home.rstrip("/")
    if home and path == home:
        return "~"
    if home and path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


# Below this many cells the working directory says too little to be worth a column.
_BANNER_MIN_PATH_CELLS = 12


def banner_lines(*, model: str, cwd: str, resumed: str, theme: Theme, width: int) -> list[Line]:
    """The header flushed to scrollback once, before the first frame, and the hint under it.

    The header is one row at `width`: the working directory is shortened from
    the left to what is left over (and dropped when that is too little), and
    whatever still overflows is cut at the edge rather than wrapped.
    """
    facts = [f"resumed {resumed}"] if resumed else []
    facts += [model] if model else []

    def header(path: str) -> Line:
        shown = [*facts, path] if path else facts
        return (
            Span(BANNER_BRAND, theme.heading),
            Span(f" v{__version__}", theme.dim),
            *(span for fact in shown for span in (Span(" · ", theme.dim), Span(fact, theme.text))),
        )

    room = width - line_width(header("")) - len(" · ")
    path = truncate_left(cwd, room) if cwd and room >= _BANNER_MIN_PATH_CELLS else ""
    return [truncate_line(header(path), width), truncate_line((Span(BANNER_HINT, theme.dim),), width), ()]


def attachment_from(result: Mapping[str, Any]) -> Attachment | None:
    """The chip for an image the gateway queued (`image.attach`, `clipboard.paste`, an image drop); None without a path."""
    path = as_text(result.get("path"))
    if not path:
        return None
    width, height = result.get("width"), result.get("height")
    sized = all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in (width, height))
    return Attachment(path, as_text(result.get("name")) or os.path.basename(path) or path, f"{width}×{height}" if sized else "")


def attachment_line(attachments: Sequence[Attachment], width: int, theme: Theme) -> Line:
    """`📎 shot.png 1280×720 · notes.png  Backspace removes`, cut to `width` (the hint goes first)."""
    labels = " · ".join(f"{item.name} {item.size}".strip() for item in attachments)
    return truncate_line((Span(f"{ATTACH_ICON} {labels}", theme.accent), Span(f"  {ATTACH_HINT}", theme.dim)), width)


def path_items(result: Mapping[str, Any]) -> list[PathItem]:
    """The rows of a `complete.path` answer; rows without a `text` are skipped."""
    items = result.get("items")
    if not isinstance(items, list):
        return []
    return [
        PathItem(as_text(item.get("text")), as_text(item.get("display")), as_text(item.get("meta")))
        for item in items
        if isinstance(item, dict) and as_text(item.get("text"))
    ]


def at_token(text: str, caret: int) -> tuple[int, str]:
    """(start, word) of the whitespace-delimited word that ends at the caret, when it starts with `@`; else (caret, "")."""
    start = caret
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    word = text[start:caret]
    return (start, word) if word.startswith("@") else (caret, "")


def _tool_summary(message: Mapping[str, Any]) -> str:
    return f"{as_text(message.get('name')) or 'tool'}  {as_text(message.get('context'))}".strip()


def resume_command(stored_id: str) -> str:
    return f"hermes --tui --resume {stored_id}" if stored_id else "hermes --tui"


def attach_message(environ: Mapping[str, str]) -> str:
    return (
        "omh: the OMH TUI does not attach to a gateway another runtime owns; "
        f"open it in Hermes' own terminal: {resume_command(environ.get('HERMES_TUI_RESUME', ''))}"
    )


def write_active_session(path: str, stored_id: str) -> None:
    """Write `{"session_id": <stored id>}` (mode 0600) for the launcher's exit epilogue. Best effort.

    The id is all the file holds; a failure costs the launcher its resume
    hint and nothing else.
    """
    if not path or not stored_id:
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"session_id": stored_id}))
        os.chmod(path, 0o600)
    except OSError:
        return


def _matches(request_id: str, params: Mapping[str, Any], ids: set[str]) -> bool:
    """Whether a withdrawal naming `ids` covers this request (its JSON-RPC id or an approval's own id)."""
    return request_id in ids or as_text(params.get("request_id")) in ids


def _slot_owns(slot: PromptSlot, event: Key | Paste, composer_text: str) -> bool:
    """Whether a key goes to the open slot rather than the composer.

    An approval takes every key. A slot answered with text takes only what
    the composer has no use for: Esc and the arrows always, Ctrl+C on empty
    input (decline), and a digit on empty input when it can pick a clarify
    choice. Everything else types the answer.
    """
    if not slot.wants_text:
        return True
    if not isinstance(event, Key):
        return False
    if event.name in (ESCAPE, UP, DOWN):
        return True
    if composer_text or event.name != CHAR:
        return False
    if event.ctrl:
        return event.char == "c"
    return slot.method == "clarify" and not event.alt and len(event.char) == 1 and event.char in CHOICE_DIGITS


def _menu_lines(entries: list[tuple[str, str]], index: int, width: int, max_rows: int, theme: Theme) -> list[Line]:
    """The completion menu (`(label, summary)` rows), scrolled so the highlighted entry is visible."""
    if not entries or max_rows <= 0:
        return []
    start = min(max(index - max_rows + 1, 0), max(len(entries) - max_rows, 0))
    rows: list[Line] = []
    for number in range(start, min(start + max_rows, len(entries))):
        label, summary = entries[number]
        current = number == index
        style = theme.menu_current if current else theme.menu
        name = f" {label}".ljust(MENU_NAME_CELLS)
        rows.append(truncate_line((Span(name, style), Span(f" {summary} ", style)), width))
    return rows


_PICKER_KEYS = {UP: KEY_UP, DOWN: KEY_DOWN, LEFT: KEY_LEFT, RIGHT: KEY_RIGHT, ENTER: KEY_ENTER, ESCAPE: KEY_QUIT}


def picker_key(event: Key | Paste | Mouse) -> str:
    """One decoded key as an OMH picker's key token. Raw mode makes Ctrl+C a key, and it cancels like Esc."""
    if not isinstance(event, Key) or event.alt:
        return KEY_NONE
    if event.name != CHAR:
        return _PICKER_KEYS.get(event.name, KEY_NONE)
    if event.ctrl:
        return KEY_QUIT if event.char in ("c", "d") else KEY_NONE
    return plain_key(event.char)


def run_model_chain_picker(
    environ: Mapping[str, str], theme: Theme, *, read_key: Callable[[], str], write: Callable[[str], None], width: int
) -> str:
    """`omh model-chains`' picker, driven through the TUI's own terminal; returns a one-line result.

    The same rows and the same validated save the Hermes widget used
    (`plugin_bundle/omh/model_chain_picker.py`), painted by the CLI picker.
    `read_key`, `write` and `width` are the picker's seams, so the terminal
    stays in the TUI's raw mode: no terminal setting changes, and Ctrl+C
    reaches the picker as a key that cancels instead of a signal.
    """
    from ..catalogs.model_chain_table import CHAIN_SURFACE_PURPOSES, MODEL_DISPLAY_LABELS
    from ..commands.model_chain_picker import run_picker
    from ..plugin_bundle.omh.model_chain_picker import apply_picker_changes, chain_entries, picker_rows

    omh_home = environ.get("OMH_HOME") or None
    hermes_home = environ.get("HERMES_HOME") or None
    try:
        payload = picker_rows(
            omh_home, hermes_home=hermes_home, labels=MODEL_DISPLAY_LABELS, purposes=CHAIN_SURFACE_PURPOSES
        )
    except (OSError, ValueError) as error:
        return f"/omh-model could not read the model chains: {error}"
    skin = theme_for_name(theme.name) or default_theme()
    changes = run_picker(
        payload,
        read_key=read_key,
        write=write,
        use_color=not environ.get("NO_COLOR"),
        width=width,
        palette=skin_colors(skin.skin_name),
    )
    if changes is None:
        return f"Cancelled; {payload['path']} was not changed."
    if not changes:
        return "No model-chain changes."
    result = apply_picker_changes(
        omh_home, {name: chain_entries(chain) for name, chain in changes.items()}, hermes_home=hermes_home
    )
    if "error" in result:
        return f"/omh-model did not save: {result['error']}"
    count = len(result["changed"])
    return (
        f"Saved {count} categor{'y' if count == 1 else 'ies'} to {result['path']}. "
        "New delegations use this order; running children keep theirs."
    )


# ── the loop ──────────────────────────────────────────────────────────────


class App:
    """The OMH TUI after its gateway started: `startup()` opens the session, `run()` is the loop."""

    def __init__(
        self,
        terminal: TerminalLike,
        client: GatewayClient,
        environ: Mapping[str, str],
        *,
        poller: PollerLike,
        theme: Theme = DEFAULT_THEME,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        pick_model_chains: Callable[..., str] = run_model_chain_picker,
    ) -> None:
        self.terminal = terminal
        self.client = client
        self.environ = environ
        self.poller = poller
        self.theme = theme
        self.clock = clock
        self.wall_clock = wall_clock
        self.pick_model_chains = pick_model_chains
        self.transcript = Transcript(clock)
        self.composer = Composer()
        self.region = LiveRegion(terminal.write)
        self.decoder = KeyDecoder(clock)
        self.size = terminal.size()
        self.sid = ""
        self.stored_id = ""
        self.model = ""
        self.context = ""
        self.context_percent: float | None = None
        # What the composer shows (`mode`): accept while Hermes has approvals off, the TUI's own plan flag.
        self.approvals_off = False
        self.plan_mode = False
        self.yolo_by_tui = False  # the approvals off shown are the TUI's own confirmed `on`, not Hermes' own
        self.hermes_approvals_said = False  # HERMES_APPROVALS_NOTICE was shown in this session
        self.effort = ""  # the reasoning effort the mode row shows; "" shows none
        self.mode_request_open = False  # a yolo call is out: Shift+Tab waits for its answer
        self.yolo_reported: bool | None = None  # `session.info`'s yolo since the last yolo call went out
        self.yolo_confirmed_at: float | None = None  # when Hermes confirmed the TUI's on; None once one session.info was held
        self.effort_asked = ""  # the newest effort still waiting for Hermes' answer; the next Ctrl+T steps on from it
        self.busy_mode = BUSY_QUEUE
        self.catalog: list[tuple[str, str]] = []
        self.slot: PromptSlot | None = None
        self.slot_opened_at = 0.0
        self.slot_draft = ""
        self.waiting_requests: deque[ServerRequest] = deque()
        # (text sent, text shown, text typed); "" shown or typed means the text sent.
        self.queued: deque[tuple[str, str, str]] = deque()
        self.sticky = ""
        self.flushed_since_prompt = 0  # rows printed above the region since the last prompt was sent
        self.turn_pending = False  # a prompt went out and its turn has not started (or ended) yet
        self.pending: dict[int, _Pending] = {}
        self.menu_index = 0
        self.pager: Pager | None = None
        self.resized_while_paging = False
        self.hud: HudSnapshot | None = None
        self.exit_armed_at: float | None = None
        self.exit_code: int | None = None
        self.farewell = ""
        self.banner_facts: dict[str, str] | None = None  # the header, flushed above the next anchoring frame
        self.banner_record: dict[str, str] | None = None  # kept, so a full redraw can print it again
        self.redraw_at: float | None = None  # when a settled resize redraws everything
        self.anchor = True  # the next frame re-anchors the region (first frame, resize, redraw)
        self.busy_since = 0.0  # when the running turn (or the prompt still waiting for it) started
        self.attachments: list[Attachment] = []  # images the gateway queued for the next prompt
        self.picker: SessionPicker | None = None
        self.path_word = ""  # the `@` token `complete.path` was last scheduled for
        self.path_due: float | None = None  # when that call goes out (debounced)
        self.path_seq = 0  # the newest call; an answer to an older one is stale
        self.path_items: list[PathItem] = []
        self.connection_ops: set[str] = set()  # `connection.request` operations already named in a notice
        self.roster = Roster()  # the running turn's subagents
        self.list_selected: str | None = None  # the agent list's highlighted row while the list has the keys
        self.log_agent = ""  # the agent whose log the pager shows; "" for every other pager
        self.tail_asked_at: float | None = None  # when the last `subagent.tail` went out
        self.tail_open = False  # that call has no answer yet
        self.tail_seq = 0  # the last call; an answer to an earlier one is dropped
        self.tail_misses: dict[str, int] = {}  # agent id -> answers in a row without a tail
        # The cells a click opens, as last drawn: rows above the status line → the entries on that row.
        self.clickable: dict[int, tuple[Hit, ...]] = {}
        self.mouse_was_on = False  # mouse reports were on at some point in this run: the exit drains them
        self.dirty = True
        self.last_render = 0.0

    # ── startup ──

    def startup(self) -> None:
        """Read the busy mode and the command catalog, then open or resume the session.

        Raises `GatewayCallError` (drift on any call, or any error from the
        session call), `GatewayTimeout` or `GatewayUnavailable`; the caller
        turns each into `FALLBACK_EXIT`.
        """
        self.busy_mode = busy_mode(self._optional_call("config.get", {"key": "full"}))
        self.catalog = catalog_pairs(self._optional_call("commands.catalog", {}))
        resume = self.environ.get("HERMES_TUI_RESUME", "")
        if resume:
            result = self.client.call("session.resume", {"session_id": resume, "cols": self.size[0]})
        else:
            result = self.client.call("session.create", self._create_params())
        self._adopt_session(result, fallback_stored=resume)
        self.banner_record = self._banner_record(result, resumed=self.stored_id if resume else "")
        self.banner_facts = dict(self.banner_record)
        if resume:
            self._draw_history(result.get("messages"))
        self._redeliver(result.get("open_requests"))

    def _banner_record(self, result: Mapping[str, Any], resumed: str) -> dict[str, str]:
        info = result.get("info")
        cwd = (as_text(info.get("cwd")) if isinstance(info, dict) else "") or self._create_params().get("cwd", "")
        return {"model": self.model, "cwd": short_path(cwd, self.environ.get("HOME", "")), "resumed": resumed}

    def _redeliver(self, open_requests: object) -> None:
        """A resumed session's open requests, delivered as live ones."""
        for entry in open_requests if isinstance(open_requests, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("method"), str):
                params = entry.get("params")
                self._on_server_request(ServerRequest(entry["id"], entry["method"], params if isinstance(params, dict) else {}))

    def _draw_history(self, messages: object) -> None:
        """A resumed session's earlier turns as transcript blocks: text only, each tool call one dim row."""
        if not isinstance(messages, list):
            return
        tools: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "tool":
                tools.append(_tool_summary(message))
                continue
            if role not in ("user", "assistant"):
                continue
            if tools:
                self.transcript.add_notice("\n".join(tools))
                tools = []
            text = as_text(message.get("text"))
            if role == "user" and text.strip():
                self.transcript.add_user(text)
            elif role == "assistant":
                self.transcript.add_assistant(text)
        if tools:
            self.transcript.add_notice("\n".join(tools))

    def _optional_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """A startup call the TUI can do without, except when the answer is drift."""
        try:
            return self.client.call(method, params)
        except GatewayCallError as error:
            if error.drift:
                raise
            return {}

    def _create_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"cols": self.size[0]}
        cwd = self.environ.get("HERMES_TUI_CWD") or self.environ.get("HERMES_CWD")
        if cwd:
            params["cwd"] = cwd
        return params

    def _adopt_session(self, result: Mapping[str, Any], fallback_stored: str = "") -> None:
        self.sid = as_text(result.get("session_id"))
        info = result.get("info")
        self.model = as_text(info.get("model")) if isinstance(info, dict) else ""
        self.context = ""  # the next session.usage says how full this session is
        self.context_percent = None
        cwd = as_text(info.get("cwd")) if isinstance(info, dict) else ""
        self.transcript.cwd = cwd or str(self._create_params().get("cwd", ""))
        self.turn_pending = bool(result.get("running"))
        self.busy_since = self.clock()
        # Accept belongs to the session being left; this one says its own, here or in its first session.info.
        # Plan is the TUI's flag and stays as it was.
        self.mode_request_open = False
        self.yolo_reported = None
        self.approvals_off = self.yolo_by_tui = self.hermes_approvals_said = False
        self.effort_asked = ""
        self.effort = ""
        self._take_session_state(info if isinstance(info, dict) else {})
        self._adopt_stored(as_text(result.get("stored_session_id")) or fallback_stored)

    def _adopt_stored(self, stored: str) -> None:
        if not stored or stored == self.stored_id:
            return
        self.stored_id = stored
        write_active_session(self.environ.get("HERMES_TUI_ACTIVE_SESSION_FILE", ""), stored)
        self.poller.set_session(stored)

    # ── loop ──

    def run(self) -> int:
        """Run until the person quits or the gateway is gone; the exit code.

        The live region is cleared before this returns; closing the gateway is
        the caller's, after it has handed the terminal back.
        """
        self.poller.start()
        try:
            while self.exit_code is None:
                self._step()
        finally:
            self._shutdown()
        return self.exit_code

    def _step(self) -> None:
        if self.terminal.resized():
            self._on_resize()
        try:
            data = self.terminal.read(READ_TIMEOUT_SECONDS)
        except EOFError:
            self.exit_code = 0
            return
        for event in self.decoder.feed(data) if data else self.decoder.flush(self.terminal.mouse_reporting):
            self.dirty = True
            self._on_input(event)
            if self.exit_code is not None:
                return
        self._drain_gateway()
        if self.exit_code is not None:
            return
        self._track_path_token()
        self._ask_path_completion_when_due()
        self._ask_tail_when_due()
        self._take_hud()
        self._render_when_due()

    def _shutdown(self) -> None:
        self.poller.stop()
        self.terminal.set_mouse(False)
        if self.pager is not None:
            self.pager = None
            self.terminal.write(ALT_SCREEN_OFF + SHOW_CURSOR)
        cols = self.size[0]
        stable = self.transcript.take_stable_lines(cols, self.theme)
        if stable:
            self.region.flush_above(stable, cols)
        self.region.clear()
        if self.mouse_was_on:
            self._discard_late_reports()

    def _discard_late_reports(self) -> None:
        """Ask for the cursor position, then read and drop input up to the answer, for at most `EXIT_DRAIN_SECONDS`.

        Mouse reports were on in this run, and the terminal may have sent
        some before it acted on the off written above. It answers the request
        only once it has acted on everything sent before it, so every report
        comes ahead of the answer, and the drain stops the moment the answer
        is read. Keys typed in that window are discarded with the reports:
        they are bytes the closing TUI has already read, and it acts on no
        input any more. A terminal that never answers ends the drain at the
        bound, as a read that waited out the time left does; an answer that
        comes later than that reaches the shell, as a late report would.
        """
        self.terminal.write(CURSOR_POSITION_REQUEST)
        deadline = self.clock() + EXIT_DRAIN_SECONDS
        seen = b""
        while (left := deadline - self.clock()) > 0:
            try:
                data = self.terminal.read(left)
            except EOFError:
                return  # the terminal hung up: nothing more is coming
            if not data:
                return
            seen = seen[-32:] + data  # the last read's tail too: an answer can be split across two reads
            if _holds_cursor_report(seen):
                return

    def _busy(self) -> bool:
        return self.transcript.running or self.turn_pending

    # ── gateway ──

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        on_result: Callable[[dict[str, Any]], None],
        on_error: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        try:
            request_id = self.client.request(method, params)
        except GatewayUnavailable as error:
            self._lose_gateway(str(error))
            return
        self.pending[request_id] = _Pending(method, on_result, on_error)

    def _respond(self, answer: Answer) -> None:
        try:
            self.client.respond(answer.request_id, answer.frame)
        except GatewayUnavailable as error:
            self._lose_gateway(str(error))

    def _lose_gateway(self, reason: str) -> None:
        if self.exit_code is not None:
            return
        self.transcript.add_notice(f"{reason}.", NOTICE_ERROR)
        self.farewell = f"omh: {reason}. Resume the session in Hermes' own terminal: {resume_command(self.stored_id)}"
        self.exit_code = 1

    def _drain_gateway(self) -> None:
        for item in self.client.poll():
            self.dirty = True
            if isinstance(item, Event):
                self._on_event(item)
            elif isinstance(item, ServerRequest):
                self._on_server_request(item)
            elif isinstance(item, Response):
                self._on_response(item)
            elif isinstance(item, GatewayExited):
                self._lose_gateway(f"the Hermes gateway exited (code {item.returncode})")
            if self.exit_code is not None:
                return

    def _on_response(self, response: Response) -> None:
        pending = self.pending.pop(response.id, None)
        if pending is None:
            return
        if response.error is None:
            pending.on_result(response.result or {})
            return
        error = response.error
        if contract.is_drift_error(error):
            self._lose_gateway(
                f"this Hermes refused {pending.method} ({error.get('code')}); "
                "it no longer speaks the protocol the OMH TUI was built for"
            )
            return
        if pending.on_error is not None and pending.on_error(error):
            return
        self.transcript.add_notice(
            f"{pending.method} failed ({error.get('code')}): {as_text(error.get('message'))}", NOTICE_ERROR
        )

    def _on_event(self, event: Event) -> None:
        payload = event.payload
        if event.type in ("request.cancel", "approval.cancelled"):
            # Matched by request id whatever the envelope says: a request from a session this TUI is
            # leaving (a switch, `/new`) stays open here until its withdrawal arrives.
            self._on_withdrawn(payload)
            return
        if not event.type.startswith(contract.GATEWAY_SCOPED_EVENT_PREFIX) and event.session_id != self.sid:
            return
        if event.type == "session.info":
            self._on_session_info(payload)
        elif event.type == "session.usage":
            self._take_usage(payload.get("usage"))
        elif event.type == "connection.request":
            self._on_connection_request(payload)
        elif event.type == "error":
            self._on_error_event(as_text(payload.get("message")) or "The gateway reported an error.")
        elif event.type.startswith(SUBAGENT_EVENT_PREFIX):
            self.roster.apply(event.type, payload, self.clock())  # folds what it knows; raises on nothing
            if isinstance(payload, Mapping):  # a new frame for an agent: its tail is asked for again
                self.tail_misses.pop(as_text(payload.get("subagent_id")), None)
        else:
            self.transcript.apply(event)
            if event.type == "message.start":
                if not self.turn_pending:  # a turn this TUI did not start (a resumed one, a server queue)
                    self.busy_since = self.clock()
                self.turn_pending = False
            elif event.type == "message.complete":
                self.turn_pending = False
                self._drain_queue()

    def _on_error_event(self, message: str) -> None:
        """A bare `error` event; during a turn it ends that turn.

        Hermes sends no `message.complete` after it refuses a turn (another
        process owns the session) or cancels one before the agent was ready, so
        the turn closes here and the queue moves on, as Ink's `recordError` does.
        """
        if not self._busy():
            self.transcript.add_notice(message, NOTICE_ERROR)
            return
        self.transcript.fail_turn(message)
        self.turn_pending = False
        self._drain_queue()

    def _on_connection_request(self, payload: Mapping[str, Any]) -> None:
        """Hermes waits on an app connection the TUI cannot complete; say so once per operation."""
        operation = as_text(payload.get("op_id"))
        if operation in self.connection_ops:
            return
        self.connection_ops.add(operation)
        self.transcript.add_notice(CONNECTION_NOTICE, NOTICE_WARN)

    def _on_session_info(self, payload: Mapping[str, Any]) -> None:
        self.model = as_text(payload.get("model")) or self.model
        self._take_usage(payload.get("usage"))
        self._take_session_state(payload)
        # `running` is not read: the previous turn's closing `running: false` arrives after
        # `message.complete`, by which time a queued prompt may already be pending.
        self._adopt_stored(as_text(payload.get("stored_session_id")))

    def _take_session_state(self, info: Mapping[str, Any]) -> None:
        """Hermes' own word on the session: its reasoning effort, and whether its approvals are off.

        Approvals off means accept is shown, on means it is not; the plan
        flag is the TUI's and stays as it is. Hermes reports yolo on for its
        own reasons too (`approvals.mode` off, `--yolo`, a `/yolo` typed to
        it), and then the composer says so as well.

        One `session.info` saying approvals are on, within
        `YOLO_SETTLE_SECONDS` of the TUI's confirmed on, is held instead: it
        may be a snapshot Hermes took before the switch and wrote after its
        answer. Hermes 6c61c3e27d has no per-session read to settle it with
        (`config.get` reads `approvals.mode`, the global setting, and nothing
        reads the session switch), so accept stays shown and the next
        `session.info` decides. The other way round needs no hold: approvals
        off is shown at once, since in doubt the composer shows accept.
        """
        effort = info.get("reasoning_effort")
        if isinstance(effort, str):
            self.effort = effort
        yolo = info.get("yolo")
        if not isinstance(yolo, bool):
            return
        confirmed_at = self.yolo_confirmed_at
        settling = confirmed_at is not None and self.clock() - confirmed_at < YOLO_SETTLE_SECONDS
        if not yolo and self.yolo_by_tui and settling:
            self.yolo_confirmed_at = None  # held once: the next session.info decides
            return
        self.yolo_reported = self.approvals_off = yolo
        if not yolo:
            self.yolo_by_tui = False  # nothing has approvals off now, the TUI's own switch included

    def _take_usage(self, usage: object) -> None:
        """A usage block with context figures replaces the shown ones; one without leaves them."""
        if context_text(usage):
            self.context, self.context_percent = context_text(usage), context_percent(usage)

    def _on_withdrawn(self, payload: Mapping[str, Any]) -> None:
        ids = {as_text(payload.get("id"))} - {""}
        request_ids = payload.get("request_ids")
        if isinstance(request_ids, list):
            ids.update(value for value in request_ids if isinstance(value, str) and value)
        self.waiting_requests = deque(
            request for request in self.waiting_requests if not _matches(request.id, request.params, ids)
        )
        slot = self.slot
        if slot is not None and _matches(slot.request_id, slot.params, ids):
            self._close_slot()
            reason = as_text(payload.get("reason")) or "withdrawn"
            self.transcript.add_notice(f"Hermes withdrew its {slot.method} request ({reason}).", NOTICE_WARN)

    # ── server requests and the prompt slot ──

    def _on_server_request(self, request: ServerRequest) -> None:
        if self.slot is not None:
            self.waiting_requests.append(request)
            return
        if self.pager is not None:
            self._leave_pager()
        opened = open_request(request.id, request.method, request.params)
        if isinstance(opened, ImmediateAnswer):
            self._respond(opened.answer)
            self.transcript.add_notice(opened.notice, NOTICE_WARN)
            return
        self.slot = opened
        self.slot_opened_at = self.clock()
        self._settle_list()  # a question has the keys now: the agent list gives them up
        if opened.method == "approval" and self.environ.get(BELL_ENV) != BELL_OFF:
            self.terminal.write(BELL)  # once per approval, as its slot opens; the wait is on the person
        if opened.wants_text:
            self.slot_draft = self.composer.text
            self._set_composer("")
            self.composer.masked = opened.masked
            self.composer.records_history = False  # an answer to Hermes is not a prompt to recall

    def _answer(self, answer: Answer) -> None:
        self._respond(answer)
        self._close_slot()

    def _close_slot(self) -> None:
        slot, self.slot = self.slot, None
        if slot is not None and slot.wants_text:
            self.composer.masked = False
            self.composer.records_history = True
            self._set_composer(self.slot_draft)
            self.slot_draft = ""
        while self.slot is None and self.waiting_requests:
            self._on_server_request(self.waiting_requests.popleft())

    def _decline_all_requests(self) -> int:
        """Answer every open and waiting request with its decline frame (the session is going away); how many."""
        declined = 0
        # The waiting ones first: closing the open slot would pull the next one in, and one the
        # TUI cannot show would then be declined into the transcript being dropped, uncounted.
        while self.waiting_requests:
            request = self.waiting_requests.popleft()
            opened = open_request(request.id, request.method, request.params)
            self._respond(opened.answer if isinstance(opened, ImmediateAnswer) else opened.decline())
            declined += 1
        if self.slot is not None:
            self._answer(self.slot.decline())
            declined += 1
        return declined

    # ── input ──

    def _set_composer(self, text: str) -> None:
        self.composer.replace(0, len(self.composer.text), text)

    def _on_input(self, event: Key | Paste | Mouse) -> None:
        if isinstance(event, Mouse):
            self._on_mouse(event)  # never a key: whatever it is, it types nothing
            return
        if self.pager is not None:
            if self.log_agent and isinstance(event, Key) and event.name in (LEFT, RIGHT):
                self._step_agent_log(-1 if event.name == LEFT else 1)
            elif not self.pager.handle_key(event, self.size[1]):
                self._leave_pager()
            return
        if self._display_key(event):
            return
        if self.slot is not None and _slot_owns(self.slot, event, self.composer.text):
            if self._slot_arming():
                return
            answer = self.slot.handle_key(event)
            if answer is not None:
                self._answer(answer)
            return
        if self.slot is None and self.picker is not None:
            self._picker_key(event)
            return
        if self.slot is None and self._agent_list_key(event):
            return
        if self.slot is None and (self._mode_key(event) or self._effort_key(event) or self._guide_key(event)):
            return
        if self.slot is None and (self._attachment_input(event) or self._escape(event)):
            return
        if self.slot is None and isinstance(event, Key) and self._menu_moves(event):
            return
        if self.slot is None and self._recall_queue(event):
            return
        before = self.composer.text
        action = self.composer.handle(event)
        if self.composer.text != before:
            self.menu_index = 0
        self._on_composer(action, steer=isinstance(event, Key) and event.name == ENTER and event.ctrl)

    def _slot_arming(self) -> bool:
        """Whether the open slot opened too recently to take a key or a submit (`SLOT_ARM_SECONDS`)."""
        return self.slot is not None and self.clock() - self.slot_opened_at < SLOT_ARM_SECONDS

    def _on_composer(self, action: ComposerAction, steer: bool = False) -> None:
        """Act on what the composer made of a key; `steer` says the key was Ctrl+Enter."""
        if action.kind == SUBMIT:
            if self.slot is not None:
                if self._slot_arming():
                    return  # an Enter meant for something else answers nothing, not the highlighted choice
                answer = self.slot.submit_text(action.text)
                if answer is not None:
                    self._answer(answer)
            elif steer:
                self._steer_or_submit(action.text)
            else:
                self._submit(action.text)
        elif action.kind == INTERRUPT:
            self._interrupt_or_exit()
        elif action.kind == EXIT:
            self.exit_code = 0
        elif action.kind == COMPLETE and self.slot is None:
            self._accept_completion()

    def _display_key(self, event: Key | Paste) -> bool:
        """Ctrl+O (details) and Ctrl+L (redraw) work whatever has the keys; True when it was one."""
        if not isinstance(event, Key) or event.name != CHAR or not event.ctrl or event.alt:
            return False
        if event.char == "o":
            self._details("")
        elif event.char == "l":
            self._redraw("")
        else:
            return False
        return True

    def _attachment_input(self, event: Key | Paste) -> bool:
        """Ctrl+V, Backspace on an empty composer with images queued, and a one-line paste (a drop); True when handled."""
        if isinstance(event, Paste):
            if "\n" in event.text or not event.text.strip() or not self.sid:
                return False
            self._request(
                "input.detect_drop",
                {"session_id": self.sid, "text": event.text},
                lambda result: self._on_drop(event.text, result),
                lambda error: self._on_drop_refused(event.text),
            )
            return True
        if event.name == CHAR and event.ctrl and not event.alt and event.char == "v":
            self._paste("")
            return True
        if event.name == BACKSPACE and not self.composer.text and self.attachments:
            detached = self.attachments.pop()
            self._request("image.detach", {"session_id": self.sid, "path": detached.path}, lambda result: None)
            return True
        return False

    def _escape(self, event: Key | Paste) -> bool:
        """Esc closes the `@` path menu, or interrupts a running turn when the composer is empty."""
        if not isinstance(event, Key) or event.name != ESCAPE:
            return False
        if self.path_items:
            self.path_items = []
            return True
        if not self.composer.text and self._busy():
            self._interrupt()
            return True
        return False

    def _interrupt(self) -> None:
        if self.sid:
            self._request("session.interrupt", {"session_id": self.sid}, lambda result: None)

    def _interrupt_or_exit(self) -> None:
        """Ctrl+C on empty input: interrupt a running turn, else exit on a second press within 2 s."""
        if self._busy():
            self._interrupt()
            return
        now = self.clock()
        if self.exit_armed_at is not None and now - self.exit_armed_at <= EXIT_CONFIRM_SECONDS:
            self.exit_code = 0
            return
        self.exit_armed_at = now
        self.transcript.add_notice(EXIT_HINT)

    def _menu_entries(self) -> list[Completion]:
        """Completions while the composer holds a leading `/word` and nothing else."""
        text = self.composer.text
        if self.slot is not None or not text.startswith("/") or any(ch.isspace() for ch in text):
            return []
        prefix = text[1:]
        if "/" in prefix:
            return []
        return completions(prefix, self.catalog)

    def _menu_rows(self) -> list[tuple[str, str]]:
        """The open completion menu as `(label, summary)` rows: slash commands, else `@` paths."""
        entries = self._menu_entries()
        if entries:
            return [(f"/{entry.name}", entry.summary) for entry in entries]
        return [(item.display or item.text, item.meta) for item in self.path_items]

    def _menu_moves(self, key: Key) -> bool:
        if key.name not in (UP, DOWN):
            return False
        rows = self._menu_rows()
        if not rows:
            return False
        self.menu_index = (self.menu_index + (-1 if key.name == UP else 1)) % len(rows)
        return True

    def _accept_completion(self) -> None:
        entries = self._menu_entries()
        if entries:
            self._set_composer(f"/{entries[min(self.menu_index, len(entries) - 1)].name} ")
            return
        start, word = at_token(self.composer.text, self.composer.caret)
        if not self.path_items or not word:
            return
        item = self.path_items[min(self.menu_index, len(self.path_items) - 1)]
        self.composer.replace(start, self.composer.caret, item.text)
        self.path_items = []
        if not item.text.endswith("/"):
            self.path_word = item.text  # an accepted file closes the menu; a folder lists its contents next

    def _track_path_token(self) -> None:
        """Schedule `complete.path` for the `@` token at the caret once it holds still; drop the menu without one."""
        open_composer = self.slot is None and self.picker is None
        word = at_token(self.composer.text, self.composer.caret)[1] if open_composer else ""
        if word == self.path_word:
            return
        self.path_word = word
        if word:
            self.path_due = self.clock() + PATH_COMPLETE_DEBOUNCE_SECONDS
        else:
            self.path_due = None
            self.path_items = []

    def _ask_path_completion_when_due(self) -> None:
        if self.path_due is None or self.clock() < self.path_due or not self.sid:
            return
        self.path_due = None
        self.path_seq += 1
        seq, word = self.path_seq, self.path_word
        self._request(
            "complete.path",
            {"word": word, "session_id": self.sid},
            lambda result: self._on_path_items(seq, word, result),
            lambda error: True,  # completion is advisory; a failed lookup offers nothing
        )

    def _on_path_items(self, seq: int, word: str, result: dict[str, Any]) -> None:
        if seq != self.path_seq or word != self.path_word:
            return  # the token moved on while this answer was on its way
        self.path_items = path_items(result)
        self.menu_index = 0

    # ── prompts ──

    def _submit(self, text: str) -> None:
        if not text.strip():
            return
        invocation = parse_slash(text)
        if invocation is not None:
            self._run_slash(invocation)
        else:
            self._send_typed(text)

    def _send_typed(self, text: str) -> None:
        """A prompt the person typed and sent: in plan mode as `/plan <text>`, the way a typed `/plan` goes."""
        if self.plan_mode:
            self._run_slash(plan_invocation(text))
        else:
            self._send_prompt(text)

    def _send_prompt(self, text: str, shown: str = "", typed: str = "") -> None:
        """Send `text`, or queue it behind the running turn; `typed` is what Up puts back in the composer."""
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        if self._busy() and self.busy_mode == BUSY_QUEUE:
            self.queued.append((text, shown, typed))
            return
        self._submit_prompt(text, shown)

    def _submit_prompt(self, text: str, shown: str = "", queued: bool = False) -> None:
        self.transcript.add_user(shown or text)
        self.sticky = shown or text
        self.flushed_since_prompt = 0
        self.roster.clear()  # the agents shown were the last turn's
        self.turn_pending = True
        self.busy_since = self.clock()
        self.attachments.clear()  # the gateway sends its queued images with this turn
        params: dict[str, Any] = {"session_id": self.sid, "text": text}
        if queued:
            params["queued"] = True  # the busy path holds a drained prompt; it never redirects or steers it
        self._request("prompt.submit", params, lambda result: None, self._on_submit_error)

    def _on_submit_error(self, error: dict[str, Any]) -> bool:
        """A refused prompt.submit: no turn will start, so the next queued prompt goes now, in order."""
        self.turn_pending = False
        self._drain_queue()
        return False

    def _drain_queue(self) -> None:
        if self.queued and self.sid:
            text, shown, _typed = self.queued.popleft()
            self._submit_prompt(text, shown, queued=True)

    def _recall_queue(self, event: Key | Paste) -> bool:
        """Up on an empty composer takes every queued prompt back into it, one per line, and empties the queue.

        Each comes back as what was typed for it: a prompt a slash command
        built as that command line, a prompt plan mode wrapped as the text
        typed in plan mode. Enter then submits the composer as one text, as it
        submits anything typed: if the text starts with a slash command it
        goes as that command, whose text is everything after the slash, later
        lines included; otherwise it goes as one prompt (in plan mode as
        `/plan <text>`), with a command on any later line as plain text in it.
        With nothing queued Up is the composer's: it walks the input history.
        """
        if not isinstance(event, Key) or event.name != UP or self.composer.text or not self.queued:
            return False
        self._set_composer("\n".join(typed or text for text, _shown, typed in self.queued))
        self.queued.clear()
        return True

    # ── steering ──

    def _steer_or_submit(self, text: str) -> None:
        """Ctrl+Enter: steer the running turn with the typed text. Idle, or on a slash command, it is Enter."""
        if self._busy() and text.strip() and parse_slash(text) is None:
            self._steer(text)
        else:
            self._submit(text)

    def _steer_command(self, arg: str) -> None:
        """`/steer <text>`: what Ctrl+Enter does, on any terminal. Idle, the text goes as a prompt."""
        if not arg:
            self.transcript.add_notice(STEER_USAGE, NOTICE_WARN)
        elif self._busy():
            self._steer(arg)
        else:
            self._send_typed(arg)

    def _steer(self, text: str) -> None:
        """Send `text` into the running turn. Its only callers pass what the person typed and sent."""
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        sid = self.sid
        self._request(
            "session.steer",
            {"session_id": sid, "text": text},
            lambda result: self._on_steered(sid, text, result),
            lambda error: self._on_steer_refused(sid, text, error),
        )

    def _on_steered(self, sid: str, text: str, result: dict[str, Any]) -> None:
        if sid != self.sid:
            self._steer_back(text)
        elif as_text(result.get("status")) == STEER_REJECTED:
            self._send_typed(text)  # the turn ended before the text arrived: it is the next prompt
        else:
            self.transcript.add_notice(STEERED_NOTICE.format(first_line(text)))

    def _on_steer_refused(self, sid: str, text: str, error: dict[str, Any]) -> bool:
        """Hermes could not steer (an agent still starting, or one that takes no steer): the text goes as a prompt."""
        if sid != self.sid:
            self._steer_back(text)
            return True
        message = as_text(error.get("message")) or "refused"
        self.transcript.add_notice(
            f"Hermes did not take the steer ({error.get('code')}): {message}. Sent as a prompt instead.", NOTICE_WARN
        )
        self._send_typed(text)
        return True

    def _steer_back(self, text: str) -> None:
        """A steer answered after its session was left: the text returns to the person, never to the live session.

        It goes after whatever the composer holds; while a question has the
        composer, after the draft the composer gets back when it closes, so it
        never becomes part of an answer to Hermes.
        """
        if self.slot is not None and self.slot.wants_text:
            self.slot_draft = "\n".join(part for part in (self.slot_draft, text) if part)
        else:
            self._set_composer("\n".join(part for part in (self.composer.text, text) if part))
        self.transcript.add_notice(STEER_LEFT_NOTICE, NOTICE_WARN)

    # ── input modes ──

    def _mode_key(self, event: Key | Paste) -> bool:
        """Shift+Tab cycles the input mode; with a completion menu open it stays the no-op it was.

        Shift+Tab alone: a key report that adds Ctrl or Alt to it is another
        chord, and a chord that may turn approvals off is never a guess.
        """
        if event != Key(TAB, shift=True) or self._menu_rows():
            return False
        self._cycle_mode()
        return True

    def _cycle_mode(self) -> None:
        """normal → accept → plan → normal. Entering and leaving accept wait for Hermes; plan is the TUI's own flag.

        Approvals Hermes turned off on its own are not the TUI's to turn back
        on: then accept stays shown and the key turns plan on and off, with
        nothing sent.
        """
        if self.mode_request_open:
            return  # one change at a time: the mode shown is the last one Hermes confirmed
        if self.approvals_off and not self.yolo_by_tui:
            self.plan_mode = not self.plan_mode
            self._say_hermes_approvals()
        elif self.approvals_off:
            self._set_yolo(False)
        elif self.plan_mode:
            self.plan_mode = False
        else:
            self._set_yolo(True)

    def _say_hermes_approvals(self) -> None:
        if not self.hermes_approvals_said:
            self.hermes_approvals_said = True
            self.transcript.add_notice(HERMES_APPROVALS_NOTICE, NOTICE_WARN)

    def _set_yolo(self, on: bool) -> None:
        """Ask Hermes to turn this session's approvals off (`on`) or back on; accept, or plan, once it confirms.

        The only caller is Shift+Tab. The call always names the session and
        an explicit value: without a session Hermes would set the switch for
        the whole gateway process, and a blank value toggles whatever it finds.
        """
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        sid = self.sid
        self.mode_request_open = True
        self.yolo_reported = self.yolo_confirmed_at = None
        self._request(
            "config.set",
            {"key": "yolo", "session_id": sid, "value": YOLO_ON if on else YOLO_OFF},
            lambda result: self._on_yolo_answer(sid, on, yolo_answer_problem(result, on)),
            lambda error: self._on_yolo_answer(
                sid, on, f"{error.get('code')}: {as_text(error.get('message')) or 'refused'}"
            ),
        )

    def _on_yolo_answer(self, sid: str, on: bool, problem: str) -> bool:
        """Hermes answered a yolo call; `problem` says why the answer confirms nothing ("" when it does).

        A confirmed off moves on to plan. When Hermes still reports approvals
        off after it, they are Hermes' own: accept stays shown beside plan.
        """
        if sid != self.sid:
            return True  # that session was left meanwhile, and its switch with it
        self.mode_request_open = False
        if problem and on:
            # Whatever Hermes did, it was not "this session only": show accept off and ask for it explicitly.
            self.approvals_off = False
            self.transcript.add_notice(ACCEPT_REFUSED_NOTICE.format(problem), NOTICE_ERROR)
            self._request(
                "config.set",
                {"key": "yolo", "session_id": sid, "value": YOLO_OFF},
                self._on_switched_back,
            )
        elif problem:
            self.transcript.add_notice(ACCEPT_STUCK_NOTICE.format(problem), NOTICE_ERROR)  # accept stays shown
        elif on:
            self.approvals_off = self.yolo_by_tui = True
            self.yolo_confirmed_at = self.clock()
        else:
            self.yolo_by_tui, self.plan_mode, self.approvals_off = False, True, bool(self.yolo_reported)
            if self.approvals_off:
                self._say_hermes_approvals()
        return True

    def _on_switched_back(self, result: dict[str, Any]) -> None:
        problem = yolo_answer_problem(result, False)
        if problem:
            self.transcript.add_notice(SWITCH_BACK_FAILED_NOTICE.format(problem), NOTICE_ERROR)

    # ── reasoning effort ──

    def _effort_key(self, event: Key | Paste) -> bool:
        """Ctrl+T steps the reasoning effort through `EFFORT_CYCLE`, from low when it is at none of them."""
        if not isinstance(event, Key) or event.name != CHAR or not event.ctrl or event.alt or event.char != "t":
            return False
        current = self.effort_asked or self.effort
        step = EFFORT_CYCLE.index(current) + 1 if current in EFFORT_CYCLE else 0
        self._set_effort(EFFORT_CYCLE[step % len(EFFORT_CYCLE)])
        return True

    def _effort_command(self, arg: str) -> None:
        """`/effort <level>`: any level Hermes takes; alone, it names the level shown and the choices."""
        level = arg.strip().lower()
        levels = ", ".join(contract.REASONING_LEVELS)
        if level in contract.REASONING_LEVELS:
            self._set_effort(level)
        elif level:
            self.transcript.add_notice(f"No reasoning effort named {arg}. Levels: {levels}.", NOTICE_WARN)
        else:
            now = f"Reasoning effort {self.effort}." if self.effort else "Hermes reports no reasoning effort."
            self.transcript.add_notice(f"{now} /effort <level> sets it for this session. Levels: {levels}.")

    def _set_effort(self, level: str) -> None:
        """Ask Hermes for `level` in this session; the mode row changes once Hermes confirms it."""
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        sid = self.sid
        self.effort_asked = level
        self._request(
            "config.set",
            {"key": "reasoning", "session_id": sid, "value": level},
            lambda result: self._on_effort_answer(sid, level, result.get("value")),
            lambda error: self._on_effort_answer(
                sid, level, None, f"{error.get('code')}: {as_text(error.get('message')) or 'refused'}"
            ),
        )

    def _on_effort_answer(self, sid: str, level: str, value: object, refusal: str = "") -> bool:
        """Hermes answered an effort call: its `value` is the level it set, or `refusal` says why it set none."""
        if sid != self.sid:
            return True  # that session was left meanwhile
        if self.effort_asked == level:
            self.effort_asked = ""
        if not refusal and value != level:
            refusal = f"it answered {json.dumps(value)}"
        if refusal:
            self.transcript.add_notice(EFFORT_REFUSED_NOTICE.format(level, refusal), NOTICE_WARN)
        else:
            self.effort = level
        return True

    # ── slash commands ──

    def _run_slash(self, invocation: SlashInvocation, depth: int = 0) -> None:
        command = invocation.command
        if command is not None and command.route == LOCAL:
            self._local_handlers()[command.name](invocation.arg)
        elif command is not None and command.route == HERMES:
            self.transcript.add_notice(hermes_notice(command))
        elif command is not None and command.route == DROPPED:
            self.transcript.add_notice(dropped_notice(command))
        elif not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
        else:
            self._request(
                "slash.exec",
                {"command": invocation.text, "session_id": self.sid},
                lambda result: self._on_slash_result(invocation, result, depth),
                lambda error: self._on_slash_refused(invocation, error, depth),
            )

    def _on_slash_refused(self, invocation: SlashInvocation, error: dict[str, Any], depth: int) -> bool:
        """`slash.exec` refusing a command `command.dispatch` owns (4018) falls back to it, as Ink does."""
        if error.get("code") != contract.NOT_MINE:
            return False
        self._request(
            "command.dispatch",
            {"name": invocation.name, "arg": invocation.arg, "session_id": self.sid},
            lambda result: self._on_dispatch(invocation, result, depth),
        )
        return True

    def _on_slash_result(self, invocation: SlashInvocation, result: dict[str, Any], depth: int) -> None:
        if result.get("type"):
            self._on_dispatch(invocation, result, depth)
            return
        body = as_text(result.get("output")) or f"/{invocation.name}: no output"
        warning = as_text(result.get("warning"))
        self._show_output(invocation.name, f"warning: {warning}\n{body}" if warning else body)

    def _on_dispatch(self, invocation: SlashInvocation, result: dict[str, Any], depth: int) -> None:
        kind = as_text(result.get("type"))
        if kind in ("exec", "plugin"):
            self._show_output(invocation.name, as_text(result.get("output")) or "(no output)")
            return
        if kind == "alias":
            target = as_text(result.get("target")).lstrip("/")
            aliased = parse_slash(f"/{target} {invocation.arg}") if target and depth == 0 else None
            if aliased is not None:
                self._run_slash(aliased, depth + 1)
            else:
                self.transcript.add_notice(f"/{invocation.name}: the alias did not resolve.", NOTICE_WARN)
            return
        notice = as_text(result.get("notice")).strip()
        if notice:
            self.transcript.add_notice(notice)
        message = as_text(result.get("message"))
        if kind in ("send", "skill"):
            if message.strip():
                shown = as_text(result.get("display")).strip() or invocation.shown
                self._send_prompt(message, shown, typed=invocation.typed)
            else:
                self.transcript.add_notice(f"/{invocation.name}: empty message", NOTICE_WARN)
        elif kind == "prefill":
            if message:
                self._set_composer(message)
        else:
            other = as_text(result.get("output")) or as_text(result.get("display"))
            if other:
                self._show_output(invocation.name, other)

    def _show_output(self, name: str, text: str) -> None:
        """A command's output as a notice, or in the pager when it is longer than half the screen.

        Output that arrives while a view already has the alternate screen (an
        agent's log, the key list, another command's output) does not replace
        it: it goes to the transcript after `VIEW_OPEN_NOTICE`, and shows there
        once the person leaves the view.
        """
        cols, rows = self.size
        source = text.split("\n")
        if len(source) <= max(rows // 2, 3):
            self.transcript.add_notice(text)
            return
        if self.pager is not None:
            self.transcript.add_notice(VIEW_OPEN_NOTICE.format(name=name, lines=len(source)))
            self.transcript.add_notice(text)
            return
        lines = [wrapped for line in source for wrapped in (wrap_line((Span(line),), cols) or [()])]
        self._open_pager(f"/{name}", lines)
        self.transcript.add_notice(f"/{name}: {len(source)} lines, shown in the pager.")

    def _local_handlers(self) -> dict[str, Callable[[str], None]]:
        return {
            "help": self._help,
            "keys": self._keys,
            "quit": self._quit,
            "new": self._new_session,
            "redraw": self._redraw,
            "history": self._history,
            "details": self._details,
            "queue": self._show_queue,
            "steer": self._steer_command,
            "effort": self._effort_command,
            "logs": self._logs,
            "copy": self._copy,
            "theme": self._theme,
            "omh-model": self._omh_model,
            "sessions": self._sessions,
            "image": self._image,
            "paste": self._paste,
        }

    def _help(self, arg: str) -> None:
        rows = ["OMH TUI commands:"]
        rows += [f"/{command.name:<10} {command.summary}" for command in COMMANDS if command.route == LOCAL]
        names = " ".join(name for name, _summary in self.catalog)
        rows.append(f"Hermes commands ({len(self.catalog)}): {names}" if names else "Hermes commands: none listed.")
        rows.append(YOLO_NOTE)
        rows.append(HELP_KEYS)
        rows.append(BARE_HERMES_NOTE)
        self.transcript.add_notice("\n".join(rows))

    def _guide_key(self, event: Key | Paste) -> bool:
        """`?` on an empty composer opens the key list; with anything typed it is a character."""
        plain = isinstance(event, Key) and event.name == CHAR and not event.ctrl and not event.alt
        if not plain or event.char != GUIDE_KEY or self.composer.text:
            return False
        self._keys("")
        return True

    def _keys(self, arg: str) -> None:
        """The key list on the alternate screen; q or Esc leaves, as in every pager."""
        self._open_pager("keys", shortcut_lines(self.size[0], self.theme))

    def _quit(self, arg: str) -> None:
        """Close the session first, so Hermes ends its runtime before the gateway reads end of input."""
        if self.sid:
            self._request("session.close", {"session_id": self.sid}, lambda result: None, lambda error: True)
        self.exit_code = 0

    def _new_session(self, arg: str) -> None:
        """Create the new session first; the current one closes only once that worked."""
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        self._request("session.create", self._create_params(), self._on_new_session, self._on_create_refused)

    def _on_new_session(self, result: dict[str, Any]) -> None:
        declined = self._enter_session(result)
        self.transcript.add_notice(f"New session {self.stored_id}.")
        self._notice_declined(declined)

    def _on_create_refused(self, error: dict[str, Any]) -> bool:
        message = as_text(error.get("message")) or "refused"
        self.transcript.add_notice(
            f"Could not start a new session ({error.get('code')}): {message}. Still in {self.stored_id}.", NOTICE_WARN
        )
        return True

    def _enter_session(self, result: Mapping[str, Any], fallback_stored: str = "") -> int:
        """Leave the current session for the one `result` opened; how many of its requests were declined.

        The old session's open requests are declined and its runtime closed;
        everything that belonged to it (transcript, queue, sticky prompt,
        attachments, HUD snapshot, subagents) is dropped before the new one
        is adopted.
        The caller says so in the new transcript, after its own notice.
        """
        old = self.sid
        declined = self._decline_all_requests()
        if old and old != as_text(result.get("session_id")):
            self._request("session.close", {"session_id": old}, lambda result: None, lambda error: True)
        self.transcript = Transcript(self.clock)
        self.queued.clear()
        self.sticky = ""
        self.flushed_since_prompt = 0  # rows printed above the region since the last prompt was sent
        self.attachments.clear()  # the gateway queued them for the session being left
        self.hud = None
        self.roster.clear()
        self._adopt_session(result, fallback_stored)
        return declined

    def _notice_declined(self, declined: int) -> None:
        if declined:
            noun = "request" if declined == 1 else "requests"
            self.transcript.add_notice(f"Declined {declined} open {noun} from the session you left.", NOTICE_WARN)

    # ── switching sessions ──

    def _sessions(self, arg: str) -> None:
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
        elif arg:
            self._switch_session(arg)
        else:
            self._request("session.list", {"limit": SESSION_LIST_LIMIT}, self._open_session_picker)

    def _open_session_picker(self, result: dict[str, Any]) -> None:
        rows = session_rows(result)
        if rows:
            self.picker = SessionPicker(rows, current=self.stored_id)
            self._settle_list()
        else:
            self.transcript.add_notice("Hermes has no stored sessions to switch to.")

    def _picker_key(self, event: Key | Paste) -> None:
        if self.picker is None:
            return
        action = self.picker.handle_key(event)
        if action is None:
            return
        self.picker = None
        if action.kind == PICK:
            self._switch_session(action.session_id)

    def _switch_session(self, target: str) -> None:
        """Resume `target` (a stored id or an exact title); the current session closes once that worked."""
        if target == self.stored_id:
            self.transcript.add_notice(f"Already in session {target}.")
            return
        self._request(
            "session.resume",
            {"session_id": target, "cols": self.size[0]},
            lambda result: self._on_session_resumed(target, result),
            lambda error: self._on_resume_refused(target, error),
        )

    def _on_session_resumed(self, target: str, result: dict[str, Any]) -> None:
        """Leave the old session and redraw the screen as the resumed one."""
        declined = self._enter_session(result, fallback_stored=target)
        self._draw_history(result.get("messages"))
        self._notice_declined(declined)
        self.banner_record = self._banner_record(result, resumed=self.stored_id)
        self._full_redraw()
        self._redeliver(result.get("open_requests"))

    def _on_resume_refused(self, target: str, error: dict[str, Any]) -> bool:
        message = as_text(error.get("message")) or "refused"
        self.transcript.add_notice(
            f"Could not switch to {target} ({error.get('code')}): {message}. Still in {self.stored_id}.", NOTICE_WARN
        )
        return True

    # ── attachments ──

    def _image(self, arg: str) -> None:
        if not arg:
            self.transcript.add_notice("Usage: /image <path to an image file>", NOTICE_WARN)
        elif not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
        else:
            self._request(
                "image.attach",
                {"session_id": self.sid, "path": arg},
                self._on_image_attached,
                lambda error: self._attach_failed("/image", error),
            )

    def _paste(self, arg: str) -> None:
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        self._request(
            "clipboard.paste",
            {"session_id": self.sid},
            self._on_clipboard_pasted,
            lambda error: self._attach_failed("/paste", error),
        )

    def _on_image_attached(self, result: dict[str, Any]) -> None:
        attachment = attachment_from(result) if result.get("attached") is True else None
        if attachment is None:
            self.transcript.add_notice("/image: Hermes did not attach the file.", NOTICE_WARN)
            return
        self.attachments.append(attachment)
        remainder = as_text(result.get("remainder")).strip()
        if remainder:  # `/image shot.png what is this?` keeps the question for the person to send
            self.composer.handle(Paste(remainder))

    def _on_clipboard_pasted(self, result: dict[str, Any]) -> None:
        attachment = attachment_from(result) if result.get("attached") is True else None
        if attachment is None:
            self.transcript.add_notice(as_text(result.get("message")) or "No image found in the clipboard.")
        else:
            self.attachments.append(attachment)

    def _attach_failed(self, command: str, error: dict[str, Any]) -> bool:
        message = as_text(error.get("message")) or "not attached"
        self.transcript.add_notice(f"{command}: {message} ({error.get('code')}).", NOTICE_WARN)
        return True

    def _on_drop(self, text: str, result: dict[str, Any]) -> None:
        """An image drop is already queued (a chip, nothing typed); a file drop types its reference; else the paste."""
        if result.get("matched") is True and result.get("is_image") is True:
            attachment = attachment_from(result)
            if attachment is not None:
                self.attachments.append(attachment)
            return
        if result.get("matched") is True and as_text(result.get("text")):
            text = as_text(result.get("text"))
        self.composer.handle(Paste(text))

    def _on_drop_refused(self, text: str) -> bool:
        self.composer.handle(Paste(text))
        return True

    def _redraw(self, arg: str) -> None:
        self._full_redraw()

    def _history(self, arg: str) -> None:
        lines = self.transcript.history_lines(self.size[0], self.theme, details=True)
        if lines:
            self._open_pager("history", lines)
        else:
            self.transcript.add_notice("Nothing in this session's transcript yet.")

    def _details(self, arg: str) -> None:
        # No notice: a new block would close the live one, the only block the toggle changes.
        self.transcript.toggle_details()

    def _show_queue(self, arg: str) -> None:
        if not self.queued:
            self.transcript.add_notice("Nothing is queued.")
            return
        rows = ["Queued (sent one at a time as each turn ends):"]
        rows += [f"{number}. {shown or text}" for number, (text, shown, _typed) in enumerate(self.queued, start=1)]
        self.transcript.add_notice("\n".join(rows))

    def _logs(self, arg: str) -> None:
        tail = self.client.stderr_tail()
        if tail:
            self._open_pager("gateway stderr (memory only)", [(Span(line),) for line in tail])
        else:
            self.transcript.add_notice("The gateway has written nothing to stderr.")

    def _copy(self, arg: str) -> None:
        last = next((block for block in reversed(self.transcript.blocks) if isinstance(block, AssistantBlock)), None)
        if last is None or not last.text.strip():
            self.transcript.add_notice("No reply to copy yet.")
            return
        encoded = base64.b64encode(last.text.strip().encode("utf-8")).decode("ascii")
        self.terminal.write(OSC52_COPY.format(encoded))
        self.transcript.add_notice("Copied the last reply to the clipboard (OSC 52; the terminal must allow it).")

    def _theme(self, arg: str) -> None:
        if not arg:
            self.transcript.add_notice(f"Theme {self.theme.name}. Available: {', '.join(theme_names())}.")
        elif theme_for_name(arg) or theme_for_skin_name(arg):
            self.theme = theme_named(arg)
            self.transcript.add_notice(f"Theme {self.theme.name} applies to this session; `omh theme` keeps it.")
        else:
            self.transcript.add_notice(f"No theme named {arg}. Available: {', '.join(theme_names())}.", NOTICE_WARN)

    def _omh_model(self, arg: str) -> None:
        """Run the model-chain picker on the alternate screen, over the TUI's own raw terminal.

        The main screen (scrollback and the live region) stays untouched underneath
        and comes back as it was, so the picker leaves no frame behind; only a resize
        while it was open needs the full redraw. Gateway events wait while it is open: a
        new approval shows once the picker closes, and Hermes may withdraw a request on its
        own timeout meanwhile. Nothing is answered for the person either way.
        """
        before = self.size
        self.terminal.set_mouse(False)  # the strip is not on the picker's screen; the next frame asks again
        self.terminal.write(ALT_SCREEN_ON + CURSOR_HOME + HIDE_CURSOR)
        try:
            message = self.pick_model_chains(
                self.environ,
                self.theme,
                read_key=self._picker_reader(),
                # Raw mode writes "\n" as a bare line feed; the picker's lines need a carriage return too.
                write=lambda text: self.terminal.write(text.replace("\n", "\r\n")),
                width=self.size[0],
            )
        finally:
            self.terminal.write(ALT_SCREEN_OFF + SHOW_CURSOR)
        self.size = self.terminal.size()
        if self.size != before:
            self._full_redraw()
        self.dirty = True
        self.transcript.add_notice(message)

    def _picker_reader(self) -> Callable[[], str]:
        """A picker's `read_key` over the TUI's terminal and key decoder; a hung-up terminal cancels."""
        pending: deque[Key | Paste | Mouse] = deque()

        def read_key() -> str:
            while not pending:
                try:
                    data = self.terminal.read(READ_TIMEOUT_SECONDS)
                except EOFError:
                    return KEY_QUIT
                pending.extend(self.decoder.feed(data) if data else self.decoder.flush())
            return picker_key(pending.popleft())

        return read_key

    # ── pager ──

    def _open_pager(self, title: str, lines: list[Line]) -> None:
        """`lines` on the alternate screen; a pager already open is replaced without switching to that screen again.

        A second `ALT_SCREEN_ON` would save the main screen's cursor over the
        one saved on the way in. It is no agent's log: `_open_agent_log` names
        the agent once this returns.
        """
        if self.pager is None:
            self.terminal.write(ALT_SCREEN_ON)
        self.pager = Pager(title, lines)
        self.log_agent = ""

    def _leave_pager(self) -> None:
        self.pager = None
        self.log_agent = ""
        self.terminal.write(ALT_SCREEN_OFF + SHOW_CURSOR)
        if self.resized_while_paging:
            self.resized_while_paging = False
            self._full_redraw()

    # ── drawing ──

    def _on_resize(self) -> None:
        """Note the new size; the redraw waits until the size settles (`_redraw_when_settled`).

        A terminal reflows its own rows when its width changes, so the live
        region's relative bookkeeping no longer matches the screen and any
        in-place repaint leaves the old frame behind in scrollback. The only
        clean answer is a full redraw from the transcript kept in memory.
        Mouse reports go off at once, since what is on screen until then is
        the old layout reflowed; the settled redraw's frame turns them back
        on if it still has an agent to click.
        """
        self.terminal.set_mouse(False)
        self.size = self.terminal.size()
        self.dirty = True
        if self.pager is not None:
            self.resized_while_paging = True
            return
        self.redraw_at = self.clock() + RESIZE_SETTLE_SECONDS

    def _redraw_when_settled(self) -> None:
        if self.redraw_at is not None and self.clock() >= self.redraw_at:
            self.redraw_at = None
            self._full_redraw()

    def _full_redraw(self) -> None:
        """Erase screen and scrollback, then print the session again at the current width.

        The banner and every transcript line are rendered anew (`Transcript.retake`),
        so nothing wrapped at the old width survives and no earlier frame is left
        in scrollback. Scrollback from before the TUI started is erased with it.
        """
        self.terminal.write(CLEAR_SCREEN_AND_SCROLLBACK)
        # The clear homes the cursor, so the next first frame starts on the screen's top row: unless a
        # pager is open, when it cleared the alternate screen and the main screen's cursor is where it was.
        self.region = LiveRegion(self.terminal.write, at_top=self.pager is None)
        self.transcript.retake()
        if self.banner_record is not None:
            self.banner_facts = dict(self.banner_record)
        self.anchor = True
        self.dirty = True

    def _take_hud(self) -> None:
        snapshot = self.poller.take()
        if snapshot is not None and snapshot.session_id == self.stored_id:
            self.hud = snapshot
            self.dirty = True

    def _panel_live(self) -> bool:
        payload = self.hud.payload if self.hud is not None else None
        return isinstance(payload, Mapping) and bool(payload.get("active"))

    def _render_when_due(self) -> None:
        self._redraw_when_settled()
        if self.redraw_at is not None:
            return  # the screen is mid-reflow; the settled redraw paints it
        now = self.clock()
        due = (self._busy() or self._panel_live()) and now - self.last_render >= TICK_SECONDS
        if not (self.dirty or due):
            return
        self.dirty = False
        self.last_render = now
        self._render()

    def _render(self) -> None:
        cols, rows = self.size
        self._settle_list()
        if self.pager is not None:
            self.clickable = {}
            self.terminal.set_mouse(False)
            self._refresh_agent_log()
            self.terminal.write(self.pager.frame(cols, rows, self.theme))
            return
        stable = self.transcript.take_stable_lines(cols, self.theme)
        if self.banner_facts is not None:
            stable = banner_lines(**self.banner_facts, theme=self.theme, width=cols) + stable
            self.banner_facts = None
        anchoring = self.anchor
        if stable and not anchoring:
            self.region.flush_above(stable, cols)
        self.flushed_since_prompt += len(stable)
        previous = 0 if anchoring else self.region.height
        self.anchor = False
        strip, hits = self._agent_strip(cols)
        listed: tuple[str, ...] = ()

        def agent_list(room: int) -> list[Line]:
            nonlocal listed
            lines, listed = self._agent_list(cols, room)
            return lines

        frame = build_frame(
            cols=cols,
            rows=rows,
            composer=self.composer,
            status=self._status_line(cols),
            previous_height=previous,
            sticky=sticky_line(self.sticky, cols, self.theme) if self._sticky_needed(rows) else None,
            tail=self._tail(cols),
            slot=self._slot_lines(cols, rows),
            theme=self.theme,
            attachments=attachment_line(self.attachments, cols, self.theme)
            if self.attachments and self.slot is None
            else None,
            mode=self.mode,
            mode_row=mode_row_line(
                mode=self.mode,
                busy=self._busy(),
                queued=len(self.queued),
                keyboard_protocol=self.keyboard_protocol,
                effort=self.effort,
                width=cols,
                theme=self.theme,
                strip=strip,
            ),
            placeholder=PLACEHOLDER if self.slot is None and self.picker is None else "",
            agent_list=agent_list if self.list_selected is not None else None,
        )
        self.region.render(frame.lines, frame.cursor, cols, pad=frame.pad)
        if stable and anchoring:
            # An anchoring frame fills the screen with pad rows. Lines flushed before it
            # (the banner, a resumed history) would be pushed off the top before anyone
            # saw them; flushed after it, they land in those pad rows instead.
            self.region.flush_above(stable, cols)
        self._take_clickable(frame.budget, hits, listed)

    @property
    def mode(self) -> str:
        """What the composer's rules and mode row show: accept while approvals are off, plan while its flag is on."""
        if self.approvals_off:
            return MODE_ACCEPT_PLAN if self.plan_mode else MODE_ACCEPT
        return MODE_PLAN if self.plan_mode else MODE_NORMAL

    # ── subagents ──

    def _spinner_frame(self) -> int:
        return int(self.clock() / TICK_SECONDS)

    def _agent_strip(self, cols: int) -> tuple[Line, tuple[Hit, ...]]:
        """The roster's strip for the mode row, and the cells each entry covers as 0-based screen columns."""
        start = strip_column(self.mode, self.theme)
        line, hits = self.roster.strip_line(cols - start, self.theme, self._spinner_frame())
        return line, tuple(Hit(hit.start + start, hit.end + start, hit.subagent_id) for hit in hits)

    def _agent_list(self, cols: int, room: int) -> tuple[list[Line], tuple[str, ...]]:
        """The focused agent list for `room` rows, and the id of each agent row; nothing while it has no focus.

        Its agent rows, scrolled to the highlight, then the hint row; where
        `room` is a single row the frame keeps the first, the highlight's.
        """
        if self.list_selected is None:
            return [], ()
        shown = min(AGENT_LIST_ROWS, max(room - 1, 1))
        lines, listed = self.roster.list_lines(
            cols, self.theme, self.list_selected, self._spinner_frame(), shown, self.clock()
        )
        return [*lines, truncate_line((Span(AGENT_LIST_HINT, self.theme.hint),), cols)], listed

    def _take_clickable(self, budget: Budget, hits: Sequence[Hit], listed: Sequence[str]) -> None:
        """Note the cells of the frame just drawn that a click opens, and ask for mouse reports only while there are some.

        A list row is clickable from edge to edge. The strip's agents are
        clickable while a turn runs and no question is open; its `main` entry
        opens nothing. Reports stay off while the region's place on the screen
        is not certain, since no click could be read then.
        """
        cols, rows = self.size
        under = budget.status + budget.mode - 1  # rows above the status line of the mode block's first row
        self.clickable = {}
        if listed:
            self.clickable = {under - index: (Hit(0, cols, row_id),) for index, row_id in enumerate(listed[: budget.mode])}
        elif budget.mode and self._busy() and self.slot is None:
            agents = tuple(hit for hit in hits if hit.subagent_id != MAIN_ID)
            self.clickable = {under: agents} if agents else {}
        self.terminal.set_mouse(bool(self.clickable) and self.region.last_row(rows) is not None)
        self.mouse_was_on = self.mouse_was_on or self.terminal.mouse_reporting

    def _list_available(self) -> bool:
        """Whether the agent list can be shown: agents to list, the mode row it replaces, and nothing else holding the keys."""
        cols, rows = self.size
        return bool(self.roster.agents and under_composer(rows, cols)[1]) and self.slot is None and self.picker is None

    def _settle_list(self) -> None:
        """Take the keys back from a list that can no longer be shown; keep its highlight on a row it still has."""
        if self.list_selected is None:
            return
        if not self._list_available():
            self.list_selected = None
        elif self.list_selected not in self.roster.agents:
            self.list_selected = MAIN_ID

    def _agent_list_key(self, event: Key | Paste) -> bool:
        """The agent list's keys; True when the list took the key.

        Down on an empty composer gives the list the keys, on the first
        agent, while there is one to list and no completion menu is open.
        Then Up and Down move, Enter opens the highlighted agent's log (on
        main it only leaves) and Esc or Ctrl+C leaves. Anything else leaves
        too and is then read as it would have been, so typing types. The
        exception is a sequence the decoder has no name for, which does
        nothing: with mouse reports on, a terminal that answers in the legacy
        encoding sends one for every click.
        """
        self._settle_list()
        name = event.name if isinstance(event, Key) else ""  # a paste is no key of the list's
        if self.list_selected is None:
            if name != DOWN or self.composer.text or self._menu_rows() or not self._list_available():
                return False
            self.list_selected = next(iter(self.roster.agents))
            return True
        if name in (UP, DOWN):
            ids = self.roster.ids()
            row = ids.index(self.list_selected) + (-1 if name == UP else 1)
            self.list_selected = ids[min(max(row, 0), len(ids) - 1)]
            return True
        if name == ENTER and self.list_selected != MAIN_ID:
            self._open_agent_log(self.list_selected)  # over the list: leaving the log comes back to it
            return True
        if name == UNKNOWN:
            return True
        self.list_selected = None
        return name in (ENTER, ESCAPE) or event == Key(CHAR, "c", ctrl=True)

    def _on_mouse(self, event: Mouse) -> None:
        """A plain left press on an agent, on the strip or in the list, opens that agent's log; nothing else does anything.

        The one other thing a click does is leave the list, from its main
        row, which has no log. `clickable` holds the cells of the last frame
        a click opens and is empty whenever the TUI has not asked for
        reports, so a report another program left on opens nothing. The
        report's row is read against the region's last row, and dropped while
        that row is not certain, while a question Hermes asked has the keys,
        or while the screen is being redrawn for a new size.
        """
        plain = event.button == BUTTON_LEFT and event.pressed and not (event.motion or event.shift or event.alt or event.ctrl)
        last = self.region.last_row(self.size[1])
        if not plain or last is None or self.pager is not None or self.slot is not None or self.redraw_at is not None:
            return
        above, column = last - (event.y - 1), event.x - 1  # a report counts rows and columns from 1
        target = next((hit.subagent_id for hit in self.clickable.get(above, ()) if hit.start <= column < hit.end), None)
        if target is None:
            return
        if self.list_selected is not None:
            self.list_selected = target if target != MAIN_ID else None  # main has no log: the click leaves the list
        if target != MAIN_ID:
            self._open_agent_log(target)

    def _open_agent_log(self, subagent_id: str) -> None:
        """One agent's log on the alternate screen, opened at its newest rows; nothing for an agent no longer held."""
        if subagent_id not in self.roster.agents:
            return
        self._open_pager("", [])
        self.log_agent = subagent_id
        self._refresh_agent_log(to_end=True)

    def _step_agent_log(self, step: int) -> None:
        """Left and Right in the log view: the agent before or after this one, stopping at either end."""
        ids = tuple(self.roster.agents)
        if self.log_agent not in ids:
            return
        self.log_agent = ids[min(max(ids.index(self.log_agent) + step, 0), len(ids) - 1)]
        if self.list_selected is not None:
            self.list_selected = self.log_agent  # leaving the log comes back to the row it shows
        self._refresh_agent_log(to_end=True)

    def _refresh_agent_log(self, to_end: bool = False) -> None:
        """Rebuild the open log from the roster; a view on its last page stays on it as rows arrive.

        An agent the roster no longer holds (a new turn began) keeps the rows
        last drawn.
        """
        agent = self.roster.agents.get(self.log_agent)
        if self.pager is None or agent is None:
            return
        cols, rows = self.size
        at_end = self.pager.top >= len(self.pager.lines) - self.pager.body_rows(rows)
        self.pager.title = f"{agent.name} · {AGENT_LOG_KEYS}"
        self.pager.lines = self.roster.log_lines(agent.subagent_id, cols, self.theme, self.clock())
        if to_end or at_end:
            self.pager.top = len(self.pager.lines)  # past the end: the pager shows the last full page

    def _ask_tail_when_due(self) -> None:
        """`subagent.tail` for the agent whose log is open, while it runs: at most one call every `TAIL_POLL_SECONDS`.

        The pace is the view's, not each agent's: the first call goes out as
        a log opens unless one went out less than `TAIL_POLL_SECONDS` before,
        and moving to another agent does not bring the next call forward.
        None goes out while the last has had no answer for less than
        `TAIL_ANSWER_SECONDS`, for an agent that is not running, for one whose
        last `TAIL_MISSES_BEFORE_STOP` answers had no tail and that sent no
        frame since, or without a session.
        """
        agent = self.roster.agents.get(self.log_agent) if self.pager is not None else None
        if agent is None or agent.status != RUNNING or not self.sid:
            return
        if self.tail_misses.get(agent.subagent_id, 0) >= TAIL_MISSES_BEFORE_STOP:
            return
        now = self.clock()
        if self.tail_asked_at is not None:
            since = now - self.tail_asked_at
            if since < TAIL_POLL_SECONDS or (self.tail_open and since < TAIL_ANSWER_SECONDS):
                return
        self.tail_seq += 1
        self.tail_asked_at, self.tail_open = now, True
        seq, sid, subagent_id = self.tail_seq, self.sid, agent.subagent_id
        self._request(
            "subagent.tail",
            {"session_id": sid, "subagent_id": subagent_id},
            lambda result: self._on_tail(seq, sid, subagent_id, result),
            lambda error: self._on_tail(seq, sid, subagent_id, {}),  # the log keeps what the events gave it
        )

    def _on_tail(self, seq: int, sid: str, subagent_id: str, result: Mapping[str, Any]) -> bool:
        """A `subagent.tail` answer: read only when Hermes has a tail, for an agent still running in this session.

        A tail that arrives after its agent finished would replace the reply
        its `subagent.complete` put in the log, so it is dropped. An answer
        without a tail (`available` anything but true, or an error) counts
        towards `TAIL_MISSES_BEFORE_STOP`. An answer to a call that timed out
        (`TAIL_ANSWER_SECONDS`) is dropped: the call after it is the one waited for.
        """
        if seq != self.tail_seq:
            return True
        self.tail_open = False
        if result.get("available") is not True:
            self.tail_misses[subagent_id] = self.tail_misses.get(subagent_id, 0) + 1
            return True
        self.tail_misses.pop(subagent_id, None)
        agent = self.roster.agents.get(subagent_id)
        if sid == self.sid and agent is not None and agent.status == RUNNING:
            self.roster.ingest_tail(subagent_id, as_text(result.get("text")), result.get("truncated") is True)
        return True

    @property
    def keyboard_protocol(self) -> bool:
        """Whether Ctrl+Enter reaches the TUI apart from Enter: a key has arrived as a CSI u or modifyOtherKeys report.

        Asking the terminal for those reports is not enough, since a terminal
        on the list can ignore the request and send Ctrl+Enter as Enter. So
        the mode row names `/steer` until the first such key, and Ctrl+Enter
        from then on.
        """
        return self.decoder.protocol_reported

    def _sticky_needed(self, rows: int) -> bool:
        """Whether the sent prompt needs echoing above the composer: only once it has scrolled off.

        Everything above the live region is transcript, so while the rows printed
        since the prompt (the prompt's own rows included) still fit there, the
        prompt itself is on screen and an echo would only repeat it.
        """
        if not (self._busy() and self.sticky.strip()):
            return False
        return self.flushed_since_prompt > rows - self.region.height

    def _tail(self, cols: int) -> list[Line]:
        lines = list(self.transcript.live_lines(cols, self.theme))
        if self._busy():
            text = self.transcript.activity or self.transcript.status or "working…"
            now = self.clock()
            spinner = SPINNER_FRAMES[int(now / TICK_SECONDS) % len(SPINNER_FRAMES)]
            elapsed = elapsed_text(now - (self.busy_since or now))
            row = f"{spinner} {text} ({elapsed} · {INTERRUPT_HINT})"
            lines.append(truncate_line((Span(row, self.theme.dim),), cols))
        return lines

    def _slot_lines(self, cols: int, rows: int) -> list[Line]:
        cap = slot_cap(rows, cols)
        if cap <= 0:
            return []
        if self.slot is not None:
            return self.slot.lines(cols, self.theme, cap)
        if self.picker is not None:
            return self.picker.lines(cols, self.theme, cap, self.wall_clock())
        # Nothing has the keys: the queued prompts take the slot's last rows, right above the composer's
        # top rule, and the menu or the panel gets the rows left. A question or the picker hides them,
        # since Up then does not edit the queue.
        queue = queue_lines([shown or text for text, shown, _typed in self.queued], cols, cap, self.theme)
        return self._menu_or_panel_lines(cols, cap - len(queue)) + queue

    def _menu_or_panel_lines(self, cols: int, cap: int) -> list[Line]:
        if cap <= 0:
            return []
        rows = self._menu_rows()
        if rows:
            return _menu_lines(rows, min(self.menu_index, len(rows) - 1), cols, cap, self.theme)
        payload = self.hud.payload if self.hud is not None else None
        if payload is None:
            return []
        return panel_lines(payload, cols, cap, self.theme, self.wall_clock(), received_at=self.hud.received_at)

    def _state_text(self) -> str:
        if self.slot is not None:
            state = STATE_WAITING
        elif self._busy():
            state = STATE_RUNNING
        else:
            state = STATE_IDLE
        return f"{state} · queued {len(self.queued)}" if self.queued else state

    def _context_style(self) -> Style:
        percent = self.context_percent if self.context_percent is not None else 0.0
        if percent >= CONTEXT_BAD_PERCENT:
            return self.theme.status_bad
        return self.theme.status_warn if percent >= CONTEXT_WARN_PERCENT else self.theme.status

    def _mode_chips(self) -> tuple[tuple[str, Style], ...]:
        """The mode in short for the status line, accept then plan: only where the mode row is not drawn."""
        bar = self.theme.status
        cols, rows = self.size
        if under_composer(rows, cols)[1]:
            return ()
        shown = (
            (MODE_ACCEPT, self.theme.mode_accept, self.approvals_off),
            (MODE_PLAN, self.theme.mode_plan, self.plan_mode),
        )
        return tuple((MODE_CHIPS[mode], replace(colour, bg=bar.bg, bold=True)) for mode, colour, on in shown if on)

    def _status_line(self, cols: int) -> Line:
        """OMH's segments, then model, context and state, on the status bar's background.

        The context turns warn from `CONTEXT_WARN_PERCENT` full and bad from
        `CONTEXT_BAD_PERCENT`. A terminal too short for the mode row carries
        the mode here, before the state, so accept and plan are never
        invisible. When the line does not fit, the context goes first and the
        model next; then OMH's segments are cut from their right, so the mode
        and the state (idle / running / waiting on you) are the last things cut.
        """
        bar = self.theme.status
        payload = self.hud.payload if self.hud is not None else None
        head: Line = tuple(
            Span(span.text, replace(span.style, bg=bar.bg)) for span in status_segments(payload, self.theme)
        )
        model, context, state = (self.model, bar), (self.context, self._context_style()), (self._state_text(), bar)
        chips = self._mode_chips()
        tail: Line = ()
        for fields in ((model, context, *chips, state), (model, *chips, state), (*chips, state)):
            tail = tuple(
                span
                for text, style in fields
                if text
                for span in (Span(STATUS_SEPARATOR, self.theme.status_dim), Span(text, style))
            )
            if line_width(head) + line_width(tail) <= cols:
                break
        line = truncate_line(truncate_line(head, max(cols - line_width(tail), 0)) + tail, cols)
        filler = cols - line_width(line)
        return line + ((Span(" " * filler, bar),) if filler > 0 else ())


# ── entry point ───────────────────────────────────────────────────────────


def _holds_cursor_report(data: bytes) -> bool:
    """Whether `data` holds a cursor position report: `ESC[`, a row, `;`, a column and `R`."""
    start = data.find(CURSOR_REPORT_START)
    while start >= 0:
        end = data.find(CURSOR_REPORT_FINAL, start)
        if end < 0:
            return False
        row, separator, column = data[start + len(CURSOR_REPORT_START) : end].partition(b";")
        if separator and row.isdigit() and column.isdigit():
            return True
        start = data.find(CURSOR_REPORT_START, start + 1)
    return False


def _exit_on_signal(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def _install_exit_signals() -> dict[int, Any]:
    """Route `EXIT_SIGNALS` through SystemExit; the handlers they replace."""
    previous: dict[int, Any] = {}
    for name in EXIT_SIGNALS:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.signal(signum, _exit_on_signal)
        except ValueError:
            break  # handlers can only be installed from the main thread
    return previous


def _restore_signals(previous: Mapping[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


def _say(stream: TextIO, text: str) -> None:
    """One line to the shell, after the terminal is restored; gateway text in it is cleaned of escapes and controls."""
    stream.write(clean_text(text) + "\n")
    stream.flush()


def _start_gateway(client: GatewayClient, stderr: TextIO) -> Event | None:
    """The gateway's ready event, or None after saying why it did not start."""
    try:
        return client.start()
    except GatewayUnavailable as error:
        _say(stderr, f"omh: the Hermes gateway did not start: {error}")
        for line in error.stderr_tail[-STARTUP_TAIL_LINES:]:
            _say(stderr, f"  {clean_text(line)}")  # the gateway's own text: no escape reaches the shell
    except GatewayCallError as error:
        _say(stderr, f"omh: the Hermes gateway refused the OMH TUI: {error}")
    return None


def _run(
    client: GatewayClient,
    environ: Mapping[str, str],
    stderr: TextIO,
    open_terminal: Callable[[], TerminalLike],
    make_poller: Callable[[str | None, str | None], PollerLike],
) -> int:
    """Start the gateway, open the session, then run the loop on the raw terminal; the exit code."""
    ready = _start_gateway(client, stderr)
    if ready is None:
        return FALLBACK_EXIT
    terminal = open_terminal()
    app = App(
        terminal,
        client,
        environ,
        poller=make_poller(environ.get("OMH_HOME") or None, environ.get("HERMES_HOME") or None),
        theme=initial_theme(ready.payload),
    )
    try:
        app.startup()
    except (GatewayCallError, GatewayTimeout, GatewayUnavailable) as error:
        _say(stderr, f"omh: the OMH TUI could not open a Hermes session: {error}")
        return FALLBACK_EXIT
    with terminal:
        code = app.run()
    if app.farewell:
        _say(stderr, app.farewell)
    return code


def main(
    environ: Mapping[str, str],
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    open_terminal: Callable[[], TerminalLike] | None = None,
    make_client: Callable[..., GatewayClient] = GatewayClient,
    make_poller: Callable[[str | None, str | None], PollerLike] = HudPoller,
) -> int:
    """Start the OMH TUI and return its exit code (`FALLBACK_EXIT` when it could not start)."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if not terminal_supported(stdin, stdout, environ):
        _say(stderr, "omh: this terminal cannot run the OMH TUI; opening Hermes' own terminal instead.")
        return FALLBACK_EXIT
    if attach_refused(environ):
        _say(stderr, attach_message(environ))
        return 1
    try:
        client = make_client(gateway_spawn(environ))
    except GatewayUnavailable as error:
        _say(stderr, f"omh: the Hermes gateway did not start: {error}")
        return FALLBACK_EXIT
    previous_handlers = _install_exit_signals()
    try:
        # The terminal picks its key reports from the environment the TUI was given (TERM_PROGRAM and kin).
        return _run(client, environ, stderr, open_terminal or (lambda: Terminal(environ=environ)), make_poller)
    except KeyboardInterrupt:
        # Ctrl+C is a signal, not a key, until the terminal goes raw: while the gateway starts
        # and the session opens. The child is reaped below either way.
        _say(stderr, "omh: the OMH TUI was interrupted.")
        return INTERRUPTED_EXIT
    finally:
        # The screen has settled and the terminal is the shell's again; only now wait on the child.
        try:
            client.close(grace=CLOSE_GRACE_SECONDS, terminate_grace=TERMINATE_GRACE_SECONDS)
        finally:
            _restore_signals(previous_handlers)
