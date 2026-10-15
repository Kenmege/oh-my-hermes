"""The OMH TUI's event loop and entry point.

`main` is what `python -m omh.tui` runs once Hermes' launcher has handed the
frontend its environment. Everything that can stop the TUI from starting is
settled before the first byte is drawn, and each such failure exits through
`request_fallback` (the file `omh` named, then `FALLBACK_EXIT`), so `omh`
opens Hermes' own terminal instead: a terminal that cannot run the TUI, a
gateway that does not start, and a session call (create or resume) this
Hermes refuses or no longer understands. The busy mode and the command
catalog are asked for without waiting; drift in either is met in the loop,
as drift in any call is. An attach hand-off is refused with exit 1 and a
line naming `hermes --tui --resume <id>` (0 would make `omh` print a resume
hint for a session this TUI never opened). SIGTERM and SIGHUP leave through
`SystemExit(128 + signum)`, so the terminal is restored and the gateway reaped
as on any other exit.

`App` is the loop. One thread owns the terminal and every model: it waits
for input, a wake-up from the gateway client or a resize (`_read_timeout`
says until when), routes keys (the pager while it is open, then
Ctrl+O and Ctrl+L, an open prompt slot, the session picker, the agent list,
the mode, effort and key-list keys, the attachment keys and Esc, the
completion menu, the queue's Up, then the composer), takes everything the
gateway sent since the last step, takes the newest HUD snapshot, and
repaints when something changed or a spinner is due. For `MIN_FRAME_SECONDS`
after a frame it does not wait on the gateway, so a burst of events shares
the next frame while a key still draws at once. Calls made from the
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
composer takes them back into the composer (a `!` line on its own, never
joined to a prompt: `_recall_queue`).
Ctrl+Enter and `/steer <text>` send what the person typed into the running
turn (`session.steer`); the TUI steers nothing on its own. Text Hermes does
not take as a steer joins the TUI's queue while the turn runs, whatever the
busy mode, and goes as a prompt once it ends; sent at once, it would
interrupt or redirect the turn it was meant to steer. A steer answered after
its session was left goes back to the composer instead: the session now live
never receives it.

The input modes and the reasoning effort are `modes.ModesMixin`'s, and the
subagent strip, list and log are `agent_view.AgentViewMixin`'s; each
module's docstring holds their rules. `App` mixes both in.

The transcript, attachments, secrets, server-request frames and the
gateway's stderr stay in memory and on the person's own terminal; nothing
here writes them anywhere. Prompt text goes to one file only: each line the
person sends from the composer is appended to Hermes' own input history
(`history.py`), never an answer to a question Hermes asked. The other files
the TUI writes are metadata: that session file, what OMH's HUD reader (`panels.HudPoller`, its fan-out cache
under `<omh_home>/runtime/`) and the model-chain picker (`/omh-model`'s saved
chains) write under the OMH home, and an empty fallback marker at the path
`omh` named, which `omh` removes. An attached image is a path the
gateway resolves and queues itself (`image.attach`, `clipboard.paste`,
`input.detect_drop`); the TUI keeps only its name for the chip row.
"""

from __future__ import annotations

import base64
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
import errno
from functools import partial
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from types import FrameType
import unicodedata
from typing import Any, NamedTuple, Protocol, TextIO

from ..plugin_bundle.omh.hud_text import elapsed_text
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
from ..skin_pack import (
    SkinTheme,
    default_theme,
    effective_theme,
    skin_colors,
    theme_for_name,
    theme_for_skin_name,
    theme_names,
)
from . import gateway_contract as contract
from .composer import COMPLETE, EXIT, INTERRUPT, SUBMIT, Composer, ComposerAction
from .frame import MODE_ACCEPT, MODE_PLAN, MODE_SHELL, STATUS_SEPARATOR, build_frame, sticky_line
from .history import SEARCH_ACCEPT, SEARCH_CANCEL, HistorySearch, append_entry, history_path, load_history
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
    CHAR,
    DOWN,
    END,
    ENTER,
    ESCAPE,
    LEFT,
    MOUSE_REPORT_SECONDS,
    RIGHT,
    UP,
    Key,
    KeyDecoder,
    Mouse,
    Paste,
)
from .launch import request_fallback
from .agent_view import AGENT_STEER_LEFT_UNANSWERED_NOTICE, STEER_SAY, TAIL_ANSWER_SECONDS, AgentViewMixin
from .layout import choice_cap, slot_cap, under_composer
from .markdown import clean_text
from .modes import MODE_ROW_GAP, NO_SESSION_NOTICE, STRIP_SEPARATOR, ModesMixin, mode_row_line
from .pager import ALT_SCREEN_OFF, ALT_SCREEN_ON, HistoryPager, Pager
from .model_picker import KEYS as MODEL_PICKER_KEYS
from .model_picker import PICK as MODEL_PICK
from .model_picker import ModelPicker, model_rows
from .panels import SPINNER_FRAMES, HudPoller, HudSnapshot, panel_lines, plan_text, status_segments
from .prompts import Answer, ImmediateAnswer, PromptSlot, TypeIt, open_request
from .screen import CLEAR_SCREEN_AND_SCROLLBACK, CURSOR_HOME, HIDE_CURSOR, SHOW_CURSOR, LiveRegion
from .session_picker import KEYS as SESSION_PICKER_KEYS
from .session_picker import PICK, SessionPicker, session_rows
from .rewind_picker import KEYS as REWIND_PICKER_KEYS
from .rewind_picker import PICK as REWIND_PICK
from .rewind_picker import RewindPicker, rewind_rows
from .shortcuts import shortcut_lines
from .slash import (
    COMMANDS,
    DROPPED,
    HERMES,
    LOCAL,
    RENAMED,
    Completion,
    SlashInvocation,
    catalog_categories,
    catalog_pairs,
    catalog_skill_count,
    completions,
    dropped_notice,
    hermes_notice,
    parse_slash,
    plan_invocation,
)
from .style import CSI, Line, Span, Style
from .subagents import Hit, Roster
from .terminal import Terminal, background_from_colorfgbg, colorfgbg_names_dark, terminal_name, terminal_supported
from .theme import (
    DEFAULT_THEME,
    LIGHT_STAND_IN,
    Theme,
    build_theme,
    is_light,
    logo_lines,
    on_status_bar,
    surface_for,
    theme_named,
)
from .transcript import (
    NOTICE_ERROR,
    NOTICE_INFO,
    NOTICE_OK,
    NOTICE_WARN,
    OUTPUT_FAILED,
    STEER_QUEUED,
    STEER_REFUSED,
    AssistantBlock,
    CommandBlock,
    NoticeBlock,
    SteerBlock,
    Transcript,
    as_text,
    block_rows,
    compact_count,
)
from .width import cell_width, clusters, line_width, truncate_left, truncate_line
from ..version import __version__

# While the key decoder holds bytes (a lone ESC, half a sequence, an open paste), the loop reads with this
# timeout, and an empty read settles what is held only once input has been quiet for `FLUSH_SILENCE_SECONDS`:
# a wake-up for the gateway right after an ESC must not decide it before the rest of its sequence arrives.
READ_TIMEOUT_SECONDS = 0.05
FLUSH_SILENCE_SECONDS = 0.04
TICK_SECONDS = 0.1
# With reduced motion nothing on screen moves but the elapsed time, which changes once a second.
REDUCED_TICK_SECONDS = 1.0
# The highlight that sweeps across what Hermes is doing on the working row, in clusters, one cluster a tick.
SWEEP_CLUSTERS = 3
# Nothing due, nothing held: the loop still wakes this often. Gateway news and a new HUD snapshot wake it sooner.
IDLE_READ_SECONDS = 1.0
# For this long after a frame the loop does not wake for the gateway: what arrives meanwhile shares the next
# frame. Input still wakes it at once.
MIN_FRAME_SECONDS = 0.016
# A window drag sends a burst of SIGWINCH; redraw once the size has held this long.
RESIZE_SETTLE_SECONDS = 0.15
# A full redraw (a resize, Ctrl+L, /redraw, a switch, a resumed session) prints the transcript's last this many
# rows; the blocks above them are counted on one row instead (`Transcript.retake_tail`) and /history shows them.
REDRAW_TAIL_ROWS = 3000
EXIT_CONFIRM_SECONDS = 2.0
# Esc pressed twice within this long: with text, clears it into this session's history; on an empty composer
# between turns, opens the rewind picker. The mode row says what the second press will do.
DOUBLE_ESCAPE_SECONDS = 0.8
ESCAPE_CLEAR = "clear"
ESCAPE_REWIND = "rewind"
ESCAPE_AGAIN = "esc again to {}"
# What the mode row says while a picked rewind waits to be sent, longest first: a narrow row takes the first that fits,
# so what sending it replaces and how to cancel it survive.
REWIND_SAY = (
    "⏪ rewind: sending replaces prompt #{ordinal} and everything after it · esc cancels",
    "⏪ sending replaces #{ordinal} and everything after it · esc cancels",
    "⏪ replaces #{ordinal} and later · esc cancels",
    "⏪ replaces #{ordinal}+ · esc",
)
REWIND_BUSY_NOTICE = "Rewind works between turns: let this one end, or interrupt it with Esc."
# A composer line that starts with this is the person's own shell command (`shell.exec`), never a prompt.
SHELL_PREFIX = "!"
SHELL_USAGE = "Usage: !<command> runs it in Hermes' shell (30 s at most); its output is not sent to the model."
SHELL_REFUSED = 4005  # Hermes' answer to a dangerous or hardline command
REWIND_NONE_NOTICE = "No prompt to rewind to in this session yet."
REWIND_REFUSED_NOTICE = "Hermes did not rewind: {reason}. The screen shows the session as Hermes still has it."
# Said as Hermes refuses, over the screen from before the send, until Hermes' history redraws it.
REWIND_KEPT_NOTICE = "Hermes did not rewind: {reason}. The screen is back to how it was before you sent."
REWIND_UNREAD_NOTICE = "Hermes did not send the session's history ({reason}), so the screen stays as it was before you sent."
MODEL_BUSY_NOTICE = "Hermes switches the model between turns: let this one end, or interrupt it with Esc."
TODOS_NONE_NOTICE = "No plan in this session yet: Hermes' todo tool writes one."
# Claude Code's /export saves the conversation; Hermes' writes a profile archive. Said once, then it runs.
EXPORT_NOTE = (
    "Hermes' /export writes a profile archive (config, skills, theme), not this transcript; /export again runs it. "
    "/history pages the transcript and /copy copies the last reply."
)
# Every key a prompt slot takes, and the composer's Enter, is ignored when its
# first byte arrived before the slot opened or less than this long after, so
# Enter, Esc, an arrow or a digit typed for the composer as the request arrives
# cannot answer it (an approval, a clarify, a value prompt), however late the
# decoder decides the key.
SLOT_ARM_SECONDS = 0.4
# The delay instead, counted from the slot's opening, when reports were on as it
# opened or went off less than `keys.MOUSE_REPORT_SECONDS` before: a report sent
# before the off can start arriving that long after it, and its ESC, stalled
# past the decoder's wait, is read as Esc.
REPORTING_SLOT_ARM_SECONDS = max(SLOT_ARM_SECONDS, MOUSE_REPORT_SECONDS)
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
# The block logo goes above the banner from this many rows (and as many columns as it is wide): its six rows and
# the blank under it leave the composer region its room there.
LOGO_MIN_ROWS = 24
# A fresh session says where the last one is (`session.list`), while nothing has been sent yet.
LAST_SESSION_LIMIT = 3
LAST_SESSION_NOTICE = "last session: {label} · /resume {id} reopens it"
PLACEHOLDER = "Ask Hermes anything"
BARE_HERMES_NOTE = "Bare `hermes` opens Hermes' own terminal."

# The mode on the status line, where the terminal is too short for the mode row.
MODE_CHIPS = {MODE_ACCEPT: "⏵⏵ accept", MODE_PLAN: "⏸ plan"}
# The context figure turns warn, then bad, on the status line.
CONTEXT_WARN_PERCENT = 80
CONTEXT_BAD_PERCENT = 95

# What `/theme auto|light|dark` measures the roles against: the background the terminal reported (auto), or a
# light or a dark one whatever it reported.
BACKGROUND_AUTO = "auto"
BACKGROUND_LIGHT = "light"
BACKGROUND_DARK = "dark"
BACKGROUND_MODES = (BACKGROUND_AUTO, BACKGROUND_LIGHT, BACKGROUND_DARK)

BUSY_QUEUE = "queue"
BUSY_MODES = frozenset({"queue", "steer", "interrupt"})

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_WAITING = "waiting on you"

# The prompts waiting for the running turn, listed above the composer's top rule.
QUEUE_GLYPH = "⧗ "
QUEUE_ROWS = 3
QUEUE_EDIT_HINT = "↑ edit"
QUEUE_HINT_GAP = 2

# Hermes answers `queued` both when the running turn takes the steer (at its next tool call) and when it
# queues the text as the next prompt because it is compressing the context; the transcript's
# `↪ steer queued by Hermes` is true of both. A steer it refuses says what became of the text instead.
STEER_REFUSED_OUTCOME = "{fallback} — Hermes refused the steer ({reason})"
STEER_FALLBACK_QUEUED = "queued"  # a turn still runs: the text waits in the TUI's queue for it to end
STEER_FALLBACK_SENT = "sent as a prompt"
STEER_REJECTED_REASON = "its turn had ended"  # Hermes' `rejected`: the agent was idle
# A steer with no answer is no refusal: Hermes may have taken it, so its text is never sent again but given back.
STEER_UNANSWERED_OUTCOME = "back in the composer — Hermes did not answer the steer in {seconds:g} s"
# What an approval's answer reads as in the transcript, and its colour: allowed in green, denied in red.
APPROVAL_OUTCOMES = {
    "once": ("allowed once", NOTICE_OK),
    "session": ("allowed for this session", NOTICE_OK),
    "deny": ("denied", NOTICE_ERROR),
}
STEER_USAGE = "Usage: /steer <text to add to the running turn>"
STEER_LEFT_NOTICE = (
    "Hermes answered a steer after you left its session. "
    "Its text is back in the composer and was not sent to this one."
)
STEER_LEFT_UNANSWERED_NOTICE = (
    "Hermes did not answer a steer in {seconds:g} s, and you had left its session. "
    "Its text is back in the composer and was not sent to this one."
)

# On the way out while mouse reports may still be on their way (reporting on, or off for less than
# `keys.MOUSE_REPORT_SECONDS`): a cursor position request written after the off is answered after all of
# them (a terminal handles what it is sent in order), so input is read and dropped up to that answer,
# `ESC[<row>;<col>R`, instead of landing on the shell's prompt; for at most this long. The drain stops at
# the answer, so the bound costs nothing on a fast link and covers a round trip of up to a second.
EXIT_DRAIN_SECONDS = 1.0
CURSOR_POSITION_REQUEST = f"{CSI}6n"
# How long a call made from the loop is waited for: Hermes' own TUI gives every request 120 s
# (`HERMES_TUI_RPC_TIMEOUT_MS`), and a `subagent.tail` is asked again after `TAIL_ANSWER_SECONDS` anyway. Past
# it the call is forgotten (`App._expire_calls`) and an answer that still comes is dropped. Only the calls in
# QUIET_EXPIRY_METHODS, which a person never waits on, are forgotten without a notice.
CALL_ANSWER_SECONDS = 120.0
CALL_ANSWER_SECONDS_BY_METHOD = {"subagent.tail": TAIL_ANSWER_SECONDS}
QUIET_EXPIRY_METHODS = frozenset({"subagent.tail", "complete.path", "config.get"})
CALL_EXPIRED_NOTICE = "Hermes has not answered {method} in {seconds:g} s; the OMH TUI stopped waiting for it."
CURSOR_REPORT_START = b"\x1b["
CURSOR_REPORT_FINAL = b"R"

# One BEL when an approval opens, so a terminal in the background can flag the tab or bounce.
BELL = "\a"
BELL_ENV = "OMH_TUI_BELL"
BELL_OFF = "0"
# A turn that ran this long ends with the terminal's attention signal (`attention_sequence`).
TURN_ALERT_SECONDS = 10.0
TURN_ALERT_TEXT = "Hermes finished a turn ({elapsed})"
# Terminals that show OSC 9 as a notification, and the TERM names whose form is OSC 777; elsewhere the bell.
OSC9_TERMINALS = frozenset({"iTerm.app", "WezTerm", "ghostty"})
OSC777_TERMS = ("foot", "rxvt")
# The terminal title (OSC 2), pushed onto xterm's title stack before the first one and popped on the way out.
TITLE_PUSH = f"{CSI}22;0t"
TITLE_POP = f"{CSI}23;0t"
# Every OSC string written here ends with ST, not BEL, so a BEL on the wire is only ever the bell itself.
STRING_TERMINATOR = "\x1b\\"
TITLE_SET = "\x1b]2;{}" + STRING_TERMINATOR
TITLE_DEFAULT = "Hermes"
TITLE_CHARS = 80

EXIT_HINT = "Ctrl+C again to exit"
INTERRUPT_HINT = "esc to interrupt"
OSC52_COPY = "\x1b]52;c;{}\x07"
# The completion menu's name column: at least this wide, wider for the longest name it lists (OMH skills are
# `omh-*` and `ulw-*` slugs) up to half the terminal; a name longer than that pushes only its own summary.
MENU_NAME_CELLS = 16
SESSION_LIST_LIMIT = 50
# `complete.path` goes out once the `@` token has held still this long.
PATH_COMPLETE_DEBOUNCE_SECONDS = 0.12
ATTACH_ICON = "📎"
ATTACH_HINT = "Backspace removes"
# An image Hermes queued for a session the TUI has since left: the gateway holds it there, so no chip shows here.
ATTACH_LEFT_NOTICE = "Hermes queued {name} in the session you left, so it is not attached here."
CONNECTION_NOTICE = (
    "Hermes is waiting for an app connection, which only Hermes' Desktop app or `hermes --tui` can complete. "
    "Esc interrupts the turn."
)
# The events that show a submitted turn is one the TUI can follow: the first of them ends the drift watch
# (`App._watch_turn`). `session.info` and `session.usage` are left out: Hermes sends `session.info` after each
# `message.complete`, and on a session's first turn one also arrives after the prompt and before
# `message.start`, so it says nothing about whether this Hermes still sends the turn events the TUI reads.
TURN_PROGRESS_EVENTS = frozenset((*contract.TRANSCRIPT_EVENTS, *contract.SUBAGENT_EVENTS, "error"))
# Events of types the gateway contract does not declare that a submitted turn may bring, before any of
# TURN_PROGRESS_EVENTS, without a word; the next one shows UNKNOWN_EVENTS_NOTICE, unless every type the turn
# brought was named in an earlier notice of this run. Two, so one or two events of a new type that Hermes sends
# ahead of the usual ones say nothing.
UNKNOWN_EVENTS_BEFORE_NOTICE = 2
UNKNOWN_EVENT_NAME_CHARS = 40
UNKNOWN_EVENTS_NOTICE = (
    "This turn has brought no event the OMH TUI shows, only types it does not know ({types}), so Hermes may have "
    "changed its protocol; `omh doctor` shows the installed Hermes next to the one the TUI was built against. "
    "Esc interrupts the turn."
)
YOLO_NOTE = (
    "Hermes commands go to Hermes as you type them, /yolo included: it turns off Hermes' approval prompts, "
    "so the OMH TUI then has none to show."
)
# The keys themselves are `shortcuts.SHORTCUTS`, drawn by `?` and /keys; /help only points there.
HELP_KEYS = "Keys: ? on an empty composer, or /keys, lists every key."
SKILL_COMMANDS_NOTE = "{count} skill commands available: type / and part of a skill's name to find one."
RELOAD_REFUSED_NOTICE = (
    "Hermes did not rescan its skills ({code}: {message}); a skill installed since the OMH TUI started runs as "
    "/<name> once it is restarted."
)
SETTINGS_REFUSED_NOTICE = (
    "Hermes did not send its settings ({code}: {message}), so a prompt sent during a turn queues until the turn "
    "ends (busy_input_mode queue, Hermes' default)."
)
CATALOG_REFUSED_NOTICE = (
    "Hermes did not send its command list ({code}: {message}), so completion and /help show only what the "
    "OMH TUI already knew; Hermes commands still run when typed."
)
GUIDE_KEY = "?"
# A notification's glyph by level, unless its text opens with a glyph of its own (Hermes writes most of them so).
NOTIFICATION_GLYPHS = {"info": "ℹ ", "warn": "⚠ ", "error": "✗ ", "success": "✓ "}
# What a refused call outside a command failed to do, in the words a person reads; the method and the JSON-RPC
# code stay out of the transcript. A refusal inside a command is Hermes' own words under the command's echo.
CALL_FAILED_PHRASES = {
    "prompt.submit": "take the prompt",
    "session.interrupt": "interrupt the turn",
    "image.detach": "remove the image",
    "slash.exec": "run the command",
    "command.dispatch": "run the command",
    "session.list": "list the stored sessions",
}


class TerminalLike(Protocol):
    mouse_reporting: bool

    def __enter__(self) -> Any: ...
    def __exit__(self, *exc: object) -> Any: ...
    def size(self) -> tuple[int, int]: ...
    def query_background(self) -> tuple[str | None, bytes]: ...
    def resized(self) -> bool: ...
    def read(self, timeout: float, wake: int | None = None) -> bytes: ...
    def write(self, text: str) -> None: ...
    def set_mouse(self, enabled: bool) -> None: ...


class PollerLike(Protocol):
    def start(self, wake: Callable[[], None] | None = None) -> None: ...
    def stop(self) -> None: ...
    def set_session(self, stored_id: str) -> None: ...
    def take(self) -> HudSnapshot | None: ...
    def refresh(self) -> None: ...


@dataclass(frozen=True)
class _Pending:
    """A call sent from the loop: what to do with its result, and (optionally) with its error.

    `on_error` returns True when it handled the error; otherwise the error becomes a notice.
    """

    method: str
    on_result: Callable[[dict[str, Any]], None]
    on_error: Callable[[dict[str, Any]], bool] | None = None
    expires_at: float = math.inf  # on the app's clock: `_expire_calls` forgets the call then
    # The command it was made for (its handler, or the answer to one of its calls, was running): what its answer
    # says lands under that command's echo, which stays open until every call made for it is answered.
    command: CommandBlock | None = None
    on_expiry: Callable[[], None] | None = None  # what an answer that never came settles; None: a notice says so


class QueuedLine(NamedTuple):
    """A line waiting for the running turn: what is sent, what the queue rows show, what Up puts back.

    `shell` says it is a `!` line the person sent, which runs with `shell.exec`; only `_shell_line` sets it, so
    a prompt whose text starts with `!` (a refused steer, a message Hermes composed) stays a prompt.
    """

    text: str
    shown: str = ""  # "" shows `text`
    typed: str = ""  # "" puts `text` back
    shell: bool = False
    chips: tuple[tuple[str, str], ...] = ()  # the paste chips in `shown` (label, text), for Up to put back as chips


@dataclass(frozen=True)
class Rewind:
    """A prompt picked to go back to: sending the composer truncates the session before `row_id`."""

    row_id: int
    ordinal: int
    before: tuple[dict[str, Any], ...]  # the history's messages before it: what the redraw shows above the new prompt


@dataclass(frozen=True)
class Notification:
    """One `notification.show` as the row above the composer shows it, until `expires_at` on the app's clock."""

    text: str
    level: str
    key: str
    expires_at: float = math.inf

    def style(self, theme: Theme) -> Style:
        return {"warn": theme.warn, "error": theme.error, "success": theme.ok}.get(self.level, theme.muted)

    def line(self, width: int, theme: Theme) -> Line:
        """The glyph (Hermes' own when the text opens with one) and the text, cut to `width`."""
        first = self.text[:1]
        own_glyph = bool(first) and not first.isalnum() and unicodedata.category(first)[0] in "SP"
        glyph = "" if own_glyph else NOTIFICATION_GLYPHS.get(self.level, NOTIFICATION_GLYPHS["info"])
        return truncate_line((Span(glyph + self.text, self.style(theme)),), width)


def notification_from(payload: Mapping[str, Any], now: float) -> Notification | None:
    """The row a `notification.show` payload shows; None without text. Only kind ttl expires, after `ttl_ms`."""
    text = " ".join(clean_text(as_text(payload.get(contract.NOTIFICATION_TEXT))).split())
    if not text:
        return None
    level = as_text(payload.get(contract.NOTIFICATION_LEVEL))
    ttl = payload.get(contract.NOTIFICATION_TTL_MS)
    expires_at = math.inf
    if as_text(payload.get(contract.NOTIFICATION_KIND)) == contract.NOTIFICATION_TTL_KIND and isinstance(ttl, int) \
            and not isinstance(ttl, bool) and ttl > 0:
        expires_at = now + ttl / 1000
    level = level if level in contract.NOTIFICATION_LEVELS else "info"
    return Notification(text, level, as_text(payload.get(contract.NOTIFICATION_KEY)), expires_at)


def attention_sequence(environ: Mapping[str, str], message: str) -> str:
    """What asks for the person's attention on this terminal: OSC 9, OSC 777, or the bell, by its name."""
    text = clean_text(message)
    if terminal_name(environ) in OSC9_TERMINALS:
        return f"\x1b]9;{text}{STRING_TERMINATOR}"
    if environ.get("TERM", "").startswith(OSC777_TERMS):
        return f"\x1b]777;notify;OMH;{text}{STRING_TERMINATOR}"
    return BELL


def plain_sentence(message: str) -> str:
    """Hermes' message as a sentence: its first letter capitalised and a full stop when it ends without one."""
    text = message.strip() or "refused"
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


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


def context_text(usage: object) -> str:
    """`ctx 13.9k/1M 1%` from a `session.usage` / `session.info` usage block; "" when it has no context figures."""
    if not isinstance(usage, dict):
        return ""
    used = usage.get("context_used")
    limit = usage.get("context_max")
    if not isinstance(used, int | float) or not isinstance(limit, int | float) or limit <= 0:
        return ""
    text = f"ctx {compact_count(used)}/{compact_count(limit)}"
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


def initial_theme(ready_payload: Mapping[str, Any], omh_home: str | None = None) -> Theme:
    """The theme `effective_theme` picks: saved under `omh_home`, else `gateway.ready`'s skin name, else sky."""
    skin = ready_payload.get("skin")
    name = as_text(skin.get("name")) if isinstance(skin, dict) else ""
    return build_theme(effective_theme(Path(omh_home) if omh_home else None, name)[0])


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


def banner_lines(*, cwd: str, resumed: str, theme: Theme, width: int) -> list[Line]:
    """The header flushed to scrollback once, before the first frame, and a blank row under it.

    The header is one row at `width`: the brand, the version, the session it
    resumed and the working directory, shortened from the left to what is
    left over (and dropped when that is too little); whatever still
    overflows is cut at the edge rather than wrapped. The model is the status
    line's, and the keys the mode row's.
    """
    facts = [f"resumed {resumed}"] if resumed else []

    def header(path: str) -> Line:
        shown = [*facts, path] if path else facts
        return (
            Span(BANNER_BRAND, theme.heading),
            Span(f" v{__version__}", theme.muted),
            *(span for fact in shown for span in (Span(" · ", theme.dim), Span(fact, theme.text))),
        )

    room = width - line_width(header("")) - len(" · ")
    path = truncate_left(cwd, room) if cwd and room >= _BANNER_MIN_PATH_CELLS else ""
    return [truncate_line(header(path), width), ()]


def banner_logo(skin: SkinTheme, background: str | None, width: int, rows: int) -> list[Line]:
    """The skin's block logo and a blank row, for above the banner; nothing narrower than the logo or under LOGO_MIN_ROWS."""
    logo = logo_lines(skin, background)
    if not logo or rows < LOGO_MIN_ROWS or width < max(line_width(row) for row in logo):
        return []
    return [*logo, ()]


def reduced_motion(environ: Mapping[str, str]) -> bool:
    """`OMH_TUI_REDUCED_MOTION` set to anything but 0, or `NO_COLOR` set: nothing pulses, sweeps or turns."""
    return environ.get("OMH_TUI_REDUCED_MOTION", "") not in ("", "0") or bool(environ.get("NO_COLOR"))


def working_spans(text: str, frame: int, theme: Theme) -> list[Span]:
    """What Hermes is doing, muted, with a bright highlight `SWEEP_CLUSTERS` wide at `frame`'s place; it sweeps
    across and starts over after a pause as long as itself. Frame 0 falls in that pause, so the first frame,
    the one reduced motion holds, shows no highlight."""
    units = clusters(text)
    head = frame % (len(units) + SWEEP_CLUSTERS * 2) - SWEEP_CLUSTERS
    start, end = max(head, 0), max(head + SWEEP_CLUSTERS, 0)
    parts = (("".join(units[:start]), theme.muted), ("".join(units[start:end]), replace(theme.text, bold=True)),
             ("".join(units[end:]), theme.muted))
    return [Span(part, style) for part, style in parts if part]


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
    return truncate_line((Span(f"{ATTACH_ICON} {labels}", theme.accent), Span(f"  {ATTACH_HINT}", theme.hint)), width)


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

    A slot answered by picking (an approval, a clarify question's list) takes
    every key; the composer is hidden then. A slot answered with text takes
    only what the composer has no use for: Esc and Up and Down always (Up and
    Down take a clarify question's text field back to its list), and Ctrl+C
    on empty input (decline). Everything else types the answer.
    """
    if slot.picking:
        return True
    if not isinstance(event, Key):
        return False
    if event.name in (ESCAPE, UP, DOWN):
        return True
    return not composer_text and event.name == CHAR and event.ctrl and event.char == "c"


def _menu_lines(entries: list[tuple[str, str]], index: int, width: int, max_rows: int, theme: Theme) -> list[Line]:
    """The completion menu (`(label, summary)` rows), scrolled so the highlighted entry is visible."""
    if not entries or max_rows <= 0:
        return []
    start = min(max(index - max_rows + 1, 0), max(len(entries) - max_rows, 0))
    # Measured over every entry, not only the rows shown, so scrolling never moves the summary column.
    longest = max(cell_width(label) for label, _summary in entries) + 2
    name_cells = max(MENU_NAME_CELLS, min(longest, width // 2))
    rows: list[Line] = []
    for number in range(start, min(start + max_rows, len(entries))):
        label, summary = entries[number]
        current = number == index
        style = theme.menu_current if current else theme.menu
        name = f" {label}" + " " * max(name_cells - 1 - cell_width(label), 0)
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

    The rows and the validated save of `plugin_bundle/omh/model_chain_picker.py`,
    the same ones `omh model-chains` uses, painted by the CLI picker.
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


class App(ModesMixin, AgentViewMixin):
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
        self.motion = not reduced_motion(environ)
        self.poller = poller
        self.theme = theme
        self.clock = clock
        self.wall_clock = wall_clock
        self.pick_model_chains = pick_model_chains
        self.transcript = Transcript(clock)
        self.composer = Composer()
        # Hermes' own input history: read once here, and every line the person sends appended (`history.py`).
        self.history_file = history_path(environ)
        if self.history_file is not None:
            self.composer.history = load_history(self.history_file)
            self.composer.on_record = self._remember
        self.search: HistorySearch | None = None  # Ctrl+R's search, while it has the keys
        self.escape_armed: tuple[str, float] | None = None  # (what a second Esc does, when the first came)
        self.rewind_picker: RewindPicker | None = None
        self.rewind_messages: list[dict[str, Any]] = []  # the history the picker was opened over
        self.rewind: Rewind | None = None  # a picked prompt waiting to be sent again
        self.model_picker: ModelPicker | None = None  # bare /model's list of what Hermes offers
        self.export_said = False  # EXPORT_NOTE has been said: the next /export runs
        self.interrupt_armed: str | None = None  # the agent a first `x` in the list asked about
        self.agent_steer: str | None = None  # the agent `s` picked: the next line sent is its steer
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
        # What `commands.catalog` last said, bound to the live session: completion and `/help` read it.
        self.catalog: list[tuple[str, str]] = []
        self.catalog_categories: list[tuple[str, list[str]]] = []
        self.skill_count = 0
        self.catalog_warning = ""  # the last warning shown, so a re-read repeats none
        self.slot: PromptSlot | None = None
        self.slot_opened_at = 0.0
        self.slot_arm_seconds = SLOT_ARM_SECONDS  # the open slot's arm delay, fixed as it opened
        # The person's draft, set aside whole (text, caret, paste chips) while a slot answered with text has a
        # composer of its own; it is the composer again when that slot closes.
        self.slot_draft: Composer | None = None
        self.waiting_requests: deque[ServerRequest] = deque()
        self.queued: deque[QueuedLine] = deque()
        self.sticky = ""
        self.flushed_since_prompt = 0  # rows printed above the region from the last prompt's first row on
        self.turn_pending = False  # a prompt went out and its turn has not started (or ended) yet
        # The undeclared event types the submitted turn has brought before any of TURN_PROGRESS_EVENTS; None
        # before the first prompt, once the watch ends, and after the notice (`_watch_turn`).
        self.turn_unknown: list[str] | None = None
        self.turn_unknown_count = 0
        # Every undeclared type a notice has named, for the life of this TUI and its gateway, whatever the session.
        self.unknown_named: set[str] = set()
        # While a slash command the person typed runs (its handler, or the answer to a call made for it), a call made
        # is that command's (`Transcript.command` says which), and its echo stays open until every such call is
        # answered (`_run_slash`).
        self.pending: dict[int, _Pending] = {}
        self.menu_index = 0
        self.pager: Pager | None = None
        self.resized_while_paging = False
        self.hud: HudSnapshot | None = None
        self.notification: Notification | None = None  # Hermes' out-of-band notice, shown above the composer
        self.exit_armed_at: float | None = None
        self.exit_code: int | None = None
        self.farewell = ""
        self.banner_facts: dict[str, str] | None = None  # the header, flushed above the next anchoring frame
        self.banner_record: dict[str, str] | None = None  # kept, so a full redraw can print it again
        self.logo_due = False  # the block logo goes above the banner flushed next
        self.in_launch_session = True  # the logo is the start's: it is not repeated for /new or a switch
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
        self.tail_misses: dict[str, int] = {}  # agent id -> answers in a row without a tail
        # The cells a click opens, as last drawn: rows above the status line → the entries on that row.
        self.clickable: dict[int, tuple[Hit, ...]] = {}
        self.mouse_off_at: float | None = None  # when `_set_mouse` last turned reports off; None before that
        self.wake: int | None = None  # the gateway client's wake descriptor, which `run` selects on with the input
        self.input_at = 0.0  # when input last arrived, on `clock`
        self.background: str | None = None  # the terminal's background as it reported it; None when it did not
        self.background_named_dark = False  # no colour reported, but `COLORFGBG` named the background dark
        self.usage_output: int | None = None  # the session's `output` counter, as the last usage block said
        self.turn_output_base: int | None = None  # that counter as the running turn began
        self.session_title = ""  # Hermes' auto-title for the session (`session.title`)
        self.shown_title: str | None = None  # the terminal title last written
        self.title_words = ""  # what that title was cut from: the same words need no new cut
        # The status line last composed, with what it showed and the theme and HUD snapshot it was composed with.
        self.status_held: tuple[tuple[object, ...], Theme, HudSnapshot | None, Line] | None = None
        self.title_pushed = False
        self.background_mode = BACKGROUND_AUTO
        self.dirty = True
        self.last_render = -math.inf

    # ── startup ──

    def startup(self) -> None:
        """Open or resume the session, then ask for the busy mode and for the session's command catalog.

        Only the session call is awaited: the first frame needs nothing else.
        The busy mode (`config.get`) and the catalog (asked for after the
        session and naming it, because Hermes discovers project-local skills
        for the session's workspace) are answered in the loop like any call
        made during the session; until the busy mode arrives it is
        `BUSY_QUEUE`, Hermes' own default. Raises `GatewayCallError`,
        `GatewayTimeout` or `GatewayUnavailable` from the session call; the
        caller turns each into `FALLBACK_EXIT`.
        """
        resume = self.environ.get("HERMES_TUI_RESUME", "")
        if resume:
            result = self.client.call("session.resume", {"session_id": resume, "cols": self.size[0]})
        else:
            result = self.client.call("session.create", self._create_params())
        self._adopt_session(result, fallback_stored=resume)
        self._request("config.get", {"key": "full"}, self._take_busy_mode, self._on_settings_refused)
        self._refresh_catalog()
        if not resume:
            self._ask_last_session()
        self.banner_record = self._banner_record(result, resumed=self.stored_id if resume else "")
        self.banner_facts = dict(self.banner_record)
        self.logo_due = True
        if resume:
            self._draw_history(result.get("messages"))
        self._redeliver(result.get("open_requests"))

    def _ask_last_session(self) -> None:
        """Ask for the stored sessions, to say under the banner where the last one is; a refusal says nothing."""
        self._request("session.list", {"limit": LAST_SESSION_LIMIT}, self._on_last_session, lambda error: True)

    def _on_last_session(self, result: Mapping[str, Any]) -> None:
        """Name the newest other session that has messages, unless the person has started already."""
        started = any(not isinstance(block, NoticeBlock) for block in self.transcript.blocks)
        row = next((row for row in session_rows(result) if row.id != self.stored_id and row.message_count > 0), None)
        if row is not None and not started:
            self.transcript.add_notice(LAST_SESSION_NOTICE.format(label=row.label, id=row.id))

    def _banner_record(self, result: Mapping[str, Any], resumed: str) -> dict[str, str]:
        info = result.get("info")
        cwd = (as_text(info.get("cwd")) if isinstance(info, dict) else "") or self._create_params().get("cwd", "")
        return {"cwd": short_path(cwd, self.environ.get("HOME", "")), "resumed": resumed}

    def _redeliver(self, open_requests: object) -> None:
        """A resumed session's open requests, delivered as live ones."""
        for entry in open_requests if isinstance(open_requests, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("method"), str):
                params = entry.get("params")
                self._on_server_request(ServerRequest(entry["id"], entry["method"], params if isinstance(params, dict) else {}))

    def _draw_history(self, messages: object) -> None:
        """A resumed session's earlier turns as transcript blocks: text only, each tool call one dim row.

        Only the last `REDRAW_TAIL_ROWS` rows of them are printed; /history shows the rest.
        """
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
        self.transcript.retake_tail(self.size[0], self.theme, REDRAW_TAIL_ROWS)

    def _take_busy_mode(self, result: dict[str, Any]) -> None:
        """`config.get`'s answer: the busy mode Hermes is set to."""
        self.busy_mode = busy_mode(result)

    def _on_settings_refused(self, error: dict[str, Any]) -> bool:
        """A refused `config.get`, drift included (`contract.OPTIONAL_METHODS`): `BUSY_QUEUE` stays, and a notice says so."""
        message = as_text(error.get("message")) or "refused"
        self.transcript.add_notice(SETTINGS_REFUSED_NOTICE.format(code=error.get("code"), message=message), NOTICE_WARN)
        return True

    def _catalog_params(self) -> dict[str, Any]:
        return {"session_id": self.sid} if self.sid else {}

    def _take_catalog(self, result: Mapping[str, Any]) -> None:
        """Keep what completion and `/help` read from a `commands.catalog` result; a new warning is a notice.

        Hermes' warning says skill, quick-command or plugin discovery failed,
        or names skills that share a built-in command's name and run only as
        `/skill <name>`.
        """
        self.catalog = catalog_pairs(result)
        self.catalog_categories = catalog_categories(result)
        self.skill_count = catalog_skill_count(result)
        warning = as_text(result.get("warning")).strip()
        if warning and warning != self.catalog_warning:
            self.transcript.add_notice(f"Hermes' command catalog: {warning}", NOTICE_WARN)
        self.catalog_warning = warning

    def _refresh_catalog(self) -> None:
        """Ask for the command catalog of the live session; an answer for a session since left is dropped.

        A refusal, drift included (`contract.OPTIONAL_METHODS`), keeps the
        catalog the TUI has and says so: completion and `/help` then list
        only what it already knew, and the session goes on.
        """
        sid = self.sid
        self._request(
            "commands.catalog",
            self._catalog_params(),
            lambda result: self._take_catalog(result) if sid == self.sid else None,
            lambda error: self._on_catalog_refused(sid, error),
        )

    def _on_catalog_refused(self, sid: str, error: dict[str, Any]) -> bool:
        if sid == self.sid:
            message = as_text(error.get("message")) or "refused"
            self.transcript.add_notice(CATALOG_REFUSED_NOTICE.format(code=error.get("code"), message=message), NOTICE_WARN)
        return True

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
        self.usage_output = self.turn_output_base = None  # the token counter is the session's own
        cwd = as_text(info.get("cwd")) if isinstance(info, dict) else ""
        self.transcript.cwd = cwd or str(self._create_params().get("cwd", ""))
        self.turn_pending = bool(result.get("running"))
        self.turn_unknown = None  # the drift watch was for a turn of the session being left
        self.busy_since = self.clock()
        # Accept belongs to the session being left; this one says its own, here or in its first session.info.
        # Plan is the TUI's flag and stays as it was.
        self.mode_request_open = False
        self.yolo_reported = None
        self.notification = None  # Hermes' notices are the session's
        self.session_title = ""
        self.rewind = self.rewind_picker = None  # a rewind names a row of the session being left
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

    def adopt_background(self, answered: str | None, environ: Mapping[str, str] | None = None) -> None:
        """The background the terminal answered, else what `COLORFGBG` in `environ` names: the theme is measured
        against it from now on (`_surface`). None of either is no background reported."""
        named = environ or {}
        self.background = answered or background_from_colorfgbg(named)
        self.background_named_dark = self.background is None and colorfgbg_names_dark(named)
        self.theme = build_theme(self._skin(), self._surface())

    def _skin(self) -> SkinTheme:
        return theme_for_name(self.theme.name) or default_theme()

    def _surface(self) -> str | None:
        """What the theme is measured against: the reported background, a dark one `COLORFGBG` named, or a light or
        dark one `/theme` forced."""
        reported = self.background
        if self.background_mode == BACKGROUND_LIGHT:
            return reported if reported and is_light(reported) else LIGHT_STAND_IN
        if self.background_mode == BACKGROUND_DARK or self.background_named_dark:
            # The palette's status-bar background stands in for a dark one the person or `COLORFGBG` named: named, it
            # is no guess, so the bands that need a known background are painted (None would read as nothing reported).
            return reported if reported and not is_light(reported) else surface_for(self._skin(), None)
        return reported

    def run(self, typed: bytes = b"") -> int:
        """Run until the person quits or the gateway is gone; the exit code.

        `typed` is input that arrived before the loop started (while the
        background query waited); it is read before the first frame. The live
        region is cleared before this returns; closing the gateway is the
        caller's, after it has handed the terminal back.
        """
        self.wake = self.client.wake_fd()
        self.poller.start(self.client.wake)  # a new HUD snapshot wakes the loop as gateway news does
        try:
            if typed:
                self._take_input(typed)
            self._render_when_due()  # the first frame does not wait for a first read
            while self.exit_code is None:
                self._step()
        finally:
            self._shutdown()
        return self.exit_code

    def _step(self) -> None:
        if self.terminal.resized():
            self._on_resize()
        timeout, pace = self._read_timeout(), self.last_render + MIN_FRAME_SECONDS - self.clock()
        try:
            if pace > 0:  # a frame was just drawn: gateway news waits out the interval, input does not
                data = self.terminal.read(min(timeout, pace))
            else:
                data = self.terminal.read(timeout, self.wake)
        except EOFError:
            self.exit_code = 0
            return
        self._take_input(data)
        if self.exit_code is not None:
            return
        self._drain_gateway()
        if self.exit_code is not None:
            return
        self._expire_calls()
        self._expire_notification()
        self._expire_escape()
        self._track_path_token()
        self._ask_path_completion_when_due()
        self._ask_tail_when_due()
        self._take_hud()
        self._render_when_due()

    def _take_input(self, data: bytes) -> None:
        """Decode `data` and act on each key; an empty read settles held bytes once input has been quiet long enough."""
        if data:
            self.input_at = self.clock()
            events = self.decoder.feed(data)
        elif self.clock() - self.input_at >= FLUSH_SILENCE_SECONDS:
            # A report the terminal sent before an off may still be on its way: the decoder waits for it.
            events = self.decoder.flush(self.terminal.mouse_reporting, self.mouse_off_at)
        else:
            events = []
        for event in events:
            self.dirty = True
            self._on_input(event)
            if self.exit_code is not None:
                return

    def _read_timeout(self) -> float:
        """How long the next read may wait: until the soonest thing the loop has to do without new input.

        Input, a wake-up from the gateway client and a resize end the wait
        sooner. Held key bytes, a spinner tick while a turn runs or the panel
        is live, the settled resize, the debounced path completion and the
        next agent tail call each set a deadline; without one it is
        `IDLE_READ_SECONDS`.
        """
        now = self.clock()
        due = [now + IDLE_READ_SECONDS]
        if self.decoder.holding:
            due.append(now + READ_TIMEOUT_SECONDS)
        if self.redraw_at is not None:
            due.append(self.redraw_at)
        elif self._busy() or self._panel_live():
            due.append(self.last_render + self._tick_seconds())
        if self.path_due is not None and self.sid:
            due.append(self.path_due)
        tail = self._tail_due_at()
        if tail is not None:
            due.append(tail)
        if self.notification is not None and self.notification.expires_at < math.inf:
            due.append(self.notification.expires_at)
        if self.escape_armed is not None:
            due.append(self.escape_armed[1] + DOUBLE_ESCAPE_SECONDS)
        return max(min(due) - now, 0.0)

    def _expire_notification(self) -> None:
        """Take a ttl notification off the screen once its time is up."""
        if self.notification is not None and self.clock() >= self.notification.expires_at:
            self.notification = None
            self.dirty = True

    def _shutdown(self) -> None:
        """Hand the screen back to the shell, then stop the HUD poller and turn off yolo the TUI set.

        The terminal writes come first (reports off, the alternate screen
        left, the live region erased whatever the last lines' rendering
        raises), and the other steps run whatever those raise, so a failing
        step never leaves the shell on the alternate screen or with reports
        on. A hung-up terminal (SIGHUP, a closed window) refuses every write
        with EIO and has no screen to hand back: that ends the writes quietly,
        as in `Terminal._restore`, and the exit already under way (the
        signal's SystemExit) is the one that propagates.
        """
        reports_may_arrive = self._reports_may_arrive()  # read before the off below starts a new grace period
        try:
            self._set_mouse(False)
            if self.pager is not None:
                self.pager = None
                self.terminal.write(ALT_SCREEN_OFF + SHOW_CURSOR)
            cols = self.size[0]
            try:
                stable = self.transcript.take_stable_lines(cols, self.theme)
                if stable:
                    self.region.flush_above(stable, cols)
            finally:
                self.region.clear()
            if self.title_pushed:
                self.terminal.write(TITLE_POP)  # the title the shell had
                self.title_pushed = False
            if reports_may_arrive:
                self._discard_late_reports()
        except OSError as error:
            if error.errno != errno.EIO:
                raise
        finally:
            try:
                self.poller.stop()
            finally:
                if self.sid:  # the gateway may outlive the TUI by its close grace, or longer
                    self._turn_off_yolo_set_here(self.sid)

    def _set_mouse(self, enabled: bool) -> None:
        """Turn mouse reports on or off, noting when they went off: reports sent before the off still arrive after it."""
        was_on = self.terminal.mouse_reporting
        self.terminal.set_mouse(enabled)
        if was_on and not self.terminal.mouse_reporting:
            self.mouse_off_at = self.clock()

    def _reports_may_arrive(self) -> bool:
        """Whether a mouse report may still arrive: reporting is on, or went off less than `MOUSE_REPORT_SECONDS` ago."""
        off_at = self.mouse_off_at
        return self.terminal.mouse_reporting or (off_at is not None and self.clock() - off_at < MOUSE_REPORT_SECONDS)

    def _discard_late_reports(self) -> None:
        """Ask for the cursor position, then read and drop input up to the answer, for at most `EXIT_DRAIN_SECONDS`.

        Runs only when a report may still be on its way as the TUI leaves:
        reporting was on as the shutdown began, or went off less than
        `MOUSE_REPORT_SECONDS` before it. Otherwise nothing is asked, so no
        answer can reach the shell. The terminal answers the request only
        once it has acted on everything sent before it, so every report comes
        ahead of the answer, and the drain stops the moment the answer is
        read. Keys typed in that window are discarded with the reports: they
        are bytes the closing TUI has already read, and it acts on no input
        any more. A terminal that does not answer within the bound ends the
        drain there, as a read that waited out the time left does, and its
        answer, if it comes later, reaches the shell as `ESC[<row>;<col>R`. A
        read a resize signal ends early with nothing read does not end it.
        That takes a round trip over a second just after a click, on a link
        where the reports the drain exists for would reach the shell too: one
        late answer is the price of dropping every report ahead of it.
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
                continue  # waited out (the loop ends) or ended early by a resize signal (it reads on)
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
        on_expiry: Callable[[], None] | None = None,
    ) -> None:
        try:
            request_id = self.client.request(method, params)
        except GatewayUnavailable as error:
            self._lose_gateway(str(error))
            return
        except ValueError as error:  # the gateway contract refused the call (`contract.call_problem`): nothing went out
            self.transcript.add_notice(f"The OMH TUI did not send {method}: {error}.", NOTICE_ERROR)
            return
        expires_at = self.clock() + CALL_ANSWER_SECONDS_BY_METHOD.get(method, CALL_ANSWER_SECONDS)
        self.pending[request_id] = _Pending(method, on_result, on_error, expires_at, self.transcript.command, on_expiry)

    def _expire_calls(self) -> None:
        """Forget every call unanswered past its `expires_at`, here and in the client, saying so for most.

        Neither of its handlers runs: an answer that never came is not a
        refusal, and state that waits on one (a yolo call still unanswered)
        stays as it was. A call with an `on_expiry` (a steer, whose row and
        text wait on the answer) is settled by it instead, and says so itself.
        """
        now = self.clock()
        for request_id, call in [(rid, call) for rid, call in self.pending.items() if now >= call.expires_at]:
            del self.pending[request_id]
            self.client.forget(request_id)
            if call.on_expiry is not None:
                call.on_expiry()
            elif call.method not in QUIET_EXPIRY_METHODS:
                seconds = CALL_ANSWER_SECONDS_BY_METHOD.get(call.method, CALL_ANSWER_SECONDS)
                notice = CALL_EXPIRED_NOTICE.format(method=call.method, seconds=seconds)
                if call.command is None:
                    self.transcript.add_aside(NoticeBlock(notice, NOTICE_WARN))  # never closes what runs
                else:
                    with self._for_command(call.command):  # under the echo of the command it was made for
                        self.transcript.add_notice(notice, NOTICE_WARN)
            self._command_call_done(call)

    @contextmanager
    def _for_command(self, command: CommandBlock | None) -> Iterator[None]:
        """While inside, what is said lands under `command`'s echo while it is open, and a call made is its call."""
        transcript = self.transcript
        outer, transcript.command = transcript.command, command
        try:
            yield
        finally:
            transcript.command = outer

    def _command_call_done(self, call: _Pending) -> None:
        """A command's call is answered or forgotten: once none made for it is left, the command's echo closes."""
        command = call.command
        if command is not None and not any(pending.command is command for pending in self.pending.values()):
            self.transcript.close_command(command)

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
        try:
            with self._for_command(pending.command):  # a call made from here is the command's too
                self._answer_call(pending, response)
        finally:
            self._command_call_done(pending)

    def _answer_call(self, pending: _Pending, response: Response) -> None:
        if response.error is None:
            pending.on_result(response.result or {})
            return
        error = response.error
        if contract.is_drift_error(error) and pending.method not in contract.OPTIONAL_METHODS:
            self._lose_gateway(
                f"this Hermes refused {pending.method} ({error.get('code')}); "
                "it no longer speaks the protocol the OMH TUI was built for"
            )
            return
        if pending.on_error is not None and pending.on_error(error):
            return
        message = as_text(error.get("message")) or "refused"
        if pending.command is not None:
            self.transcript.add_notice(plain_sentence(message), NOTICE_ERROR)  # under the command's echo
            return
        phrase = CALL_FAILED_PHRASES.get(pending.method, f"complete {pending.method}")
        reason = message.strip().rstrip(".")
        self.transcript.add_notice(f"Hermes did not {phrase}: {reason}.", NOTICE_ERROR)

    def _on_event(self, event: Event) -> None:
        payload = event.payload
        if event.type in ("request.cancel", "approval.cancelled"):
            # Matched by request id whatever the envelope says: a request from a session this TUI is
            # leaving (a switch, `/new`) stays open here until its withdrawal arrives.
            self._on_withdrawn(payload)
            return
        if not event.type.startswith(contract.GATEWAY_SCOPED_EVENT_PREFIX) and event.session_id != self.sid:
            return
        self._watch_turn(event.type)
        if event.type == "session.info":
            self._on_session_info(payload)
        elif event.type == "session.usage":
            self._take_usage(payload.get("usage"))
        elif event.type == "connection.request":
            self._on_connection_request(payload)
        elif event.type == "error":
            self._on_error_event(as_text(payload.get("message")) or "The gateway reported an error.")
        elif event.type == "notification.show":
            self.notification = notification_from(payload, self.clock()) or self.notification
        elif event.type == "todo.updated":
            # Hermes' plan changed: the OMH plugin projected it into the session's record as the tool finished
            # (post_tool_call runs before Hermes emits this), so the HUD is read now instead of at its next poll.
            self.poller.refresh()
        elif event.type == "session.title":
            self.session_title = " ".join(clean_text(as_text(payload.get(contract.SESSION_TITLE))).split())
        elif event.type == "notification.clear":
            key = as_text(payload.get(contract.NOTIFICATION_KEY))
            if self.notification is not None and key and self.notification.key == key:
                self.notification = None  # a clear for another key, or a late one, leaves a newer notice
        elif event.type.startswith(contract.SUBAGENT_EVENT_PREFIX):
            self.roster.apply(event.type, payload, self.clock())  # folds what it knows; raises on nothing
            if isinstance(payload, Mapping):  # a new frame for an agent: its tail is asked for again
                self.tail_misses.pop(as_text(payload.get("subagent_id")), None)
        else:
            self.transcript.apply(event)
            if event.type == "message.start":
                if not self.turn_pending:  # a turn this TUI did not start (a resumed one, a server queue)
                    self.busy_since = self.clock()
                    self.turn_output_base = self.usage_output
                self.turn_pending = False
            elif event.type == "message.complete":
                self.turn_pending = False
                self._take_usage(payload.get("usage"))
                seconds = self.clock() - self.busy_since
                self.transcript.add_turn_summary(seconds, self._turn_tokens())
                self._alert_turn_end(seconds)
                self._drain_queue()

    def _alert_turn_end(self, seconds: float) -> None:
        """A turn of `TURN_ALERT_SECONDS` or more ends with the terminal's attention signal, unless OMH_TUI_BELL=0."""
        if seconds < TURN_ALERT_SECONDS or self.environ.get(BELL_ENV) == BELL_OFF:
            return
        self.terminal.write(attention_sequence(self.environ, TURN_ALERT_TEXT.format(elapsed=elapsed_text(seconds))))

    def _write_title(self) -> None:
        """The terminal title, `<session title> · <state>`, written only when it changed (the first one pushes the old)."""
        state = STATE_WAITING if self.slot is not None else STATE_RUNNING if self._busy() else STATE_IDLE
        words = f"{self.session_title or TITLE_DEFAULT} · {state}"
        if words == self.title_words:
            return  # every frame asks; most change nothing here
        self.title_words = words
        title = truncate_left(words, TITLE_CHARS)
        if title == self.shown_title:
            return
        self.shown_title = title
        push = "" if self.title_pushed else TITLE_PUSH
        self.title_pushed = True
        self.terminal.write(push + TITLE_SET.format(title))  # the session title was cleaned as it arrived

    def _watch_turn(self, event_type: str) -> None:
        """Say once when a submitted turn has brought no event it shows, only types the contract does not declare.

        The first of TURN_PROGRESS_EVENTS ends the watch for the turn, and so
        does a turn that is no longer pending (refused) and a session change
        (`_adopt_session`); any other declared type, and a gateway-scoped one,
        neither counts nor ends it. The count starts again with every turn,
        but a turn whose undeclared types were all named before says nothing:
        a Hermes that streams a new harmless type ahead of every turn would
        otherwise repeat the notice on each one. A turn whose events Hermes
        renamed would otherwise only look like one that never finishes.
        """
        unknown = self.turn_unknown
        if unknown is None or event_type.startswith(contract.GATEWAY_SCOPED_EVENT_PREFIX):
            return
        if event_type in TURN_PROGRESS_EVENTS or not self.turn_pending:
            self.turn_unknown = None
            return
        if event_type in contract.HANDLED_EVENTS or event_type in contract.UNREAD_EVENTS:
            return
        if event_type not in unknown:
            unknown.append(event_type)
        self.turn_unknown_count += 1
        if self.turn_unknown_count > UNKNOWN_EVENTS_BEFORE_NOTICE:
            if not self.unknown_named.issuperset(unknown):
                types = ", ".join(clean_text(name)[:UNKNOWN_EVENT_NAME_CHARS] for name in unknown)
                self.transcript.add_notice(UNKNOWN_EVENTS_NOTICE.format(types=types), NOTICE_WARN)
                self.unknown_named.update(unknown)
            self.turn_unknown = None

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

    def _take_usage(self, usage: object) -> None:
        """A usage block with context figures replaces the shown ones; one without leaves them. Its `output` counts tokens.

        A turn that began before any counter was known (a fresh session's
        first: Hermes sends its first usage block after `prompt.submit`) counts
        from the first one it brings.
        """
        if context_text(usage):
            self.context, self.context_percent = context_text(usage), context_percent(usage)
        output = usage.get("output") if isinstance(usage, dict) else None
        if isinstance(output, int) and not isinstance(output, bool):
            if self.turn_output_base is None and self._busy():
                self.turn_output_base = output
            self.usage_output = output

    def _turn_tokens(self) -> int | None:
        """What the running (or just ended) turn has written, by the usage counter; None before it has written any."""
        if self.usage_output is None or self.turn_output_base is None:
            return None
        written = self.usage_output - self.turn_output_base
        return written if written > 0 else None

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
        self.slot_arm_seconds = REPORTING_SLOT_ARM_SECONDS if self._reports_may_arrive() else SLOT_ARM_SECONDS
        self._settle_list()  # a question has the keys now: the agent list gives them up
        if opened.method == "approval" and self.environ.get(BELL_ENV) != BELL_OFF:
            self.terminal.write(BELL)  # once per approval, as its slot opens; the wait is on the person
        if opened.wants_text:
            # An answer to Hermes is typed into a composer of its own, which records no history.
            self.slot_draft, self.composer = self.composer, Composer(masked=opened.masked, records_history=False)

    def _answer(self, answer: Answer) -> None:
        self._respond(answer)
        self._note_approval(answer)
        self._close_slot()

    def _note_approval(self, answer: Answer) -> None:
        """An approval's answer, in green or red, ahead of the tool call it was about: what the person decided."""
        slot = self.slot
        result = answer.frame.get("result")
        choice = result.get("choice") if slot is not None and slot.method == "approval" and isinstance(result, dict) else None
        if choice in APPROVAL_OUTCOMES:
            command = first_line(as_text(slot.params.get("command"))) if slot is not None else ""
            outcome, level = APPROVAL_OUTCOMES[choice]
            self.transcript.add_answer(f"{outcome} · {command}" if command else outcome, level)

    def _close_slot(self) -> None:
        """The slot goes and the draft comes back as it was; a request that waited behind it opens at once.

        Nothing draws a frame in between, so a waiting question that hides the
        composer opens with it still hidden.
        """
        slot, self.slot = self.slot, None
        if slot is not None and self.slot_draft is not None:
            self.composer, self.slot_draft = self.slot_draft, None
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
            if self._slot_arming(event):
                return
            answer = self.slot.handle_key(event)
            if isinstance(answer, TypeIt):
                self.composer.handle(answer.key)  # the key that opened the clarify's text field is its first text
            elif answer is not None:
                self._answer(answer)
            return
        if self.slot is None and self.picker is not None:
            self._picker_key(event)
            return
        if self.slot is None and self.search is not None:
            self._search_key(event)
            return
        if self.slot is None and self.rewind_picker is not None:
            self._rewind_picker_key(event)
            return
        if self.slot is None and self.model_picker is not None:
            self._model_picker_key(event)
            return
        if self.slot is None and self._agent_list_key(event):
            return
        if self.slot is None and (
            self._mode_key(event) or self._effort_key(event) or self._guide_key(event) or self._search_open_key(event)
        ):
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
            self.escape_armed = None  # an edit takes back what a second Esc would have done
        self._on_composer(action, event, steer=isinstance(event, Key) and event.name == ENTER and event.ctrl)

    def _remember(self, text: str) -> None:
        """A line the composer recorded goes to the end of Hermes' history file too (best effort)."""
        if self.history_file is not None:
            append_entry(self.history_file, text)

    def _search_open_key(self, event: Key | Paste) -> bool:
        """Ctrl+R opens the search over the composer's history in the slot."""
        if not isinstance(event, Key) or event != Key(CHAR, "r", ctrl=True):
            return False
        self.search = HistorySearch(list(self.composer.history), self.composer.text)
        return True

    def _search_key(self, event: Key | Paste | Mouse) -> None:
        """A key for the open search: Enter puts the match in the composer, Esc leaves it as it was."""
        search = self.search
        if search is None or isinstance(event, Mouse):
            return
        action = search.handle_key(event)
        if action == SEARCH_ACCEPT:
            self._set_composer(search.current() or search.original)
        if action in (SEARCH_ACCEPT, SEARCH_CANCEL):
            self.search = None

    def _slot_arming(self, event: Key | Paste) -> bool:
        """Whether `event` came too early to answer the open slot (`SLOT_ARM_SECONDS`).

        Timed from when the key's first byte arrived, not from when it was
        decided: an Esc waits up to `keys.REPORTING_ESCAPE_SECONDS` for a
        report's `[` after the question turned reports off, and an Esc pressed
        for the composer must not answer the question it then meets. A key
        that arrived before the slot opened is early too. A key the decoder
        did not make carries no arrival and is timed from now. A slot that
        opened while a report could still arrive waits
        `REPORTING_SLOT_ARM_SECONDS` instead.
        """
        if self.slot is None:
            return False
        arrived_at = event.arrived_at if isinstance(event, Key) else None
        return (self.clock() if arrived_at is None else arrived_at) - self.slot_opened_at < self.slot_arm_seconds

    def _on_composer(self, action: ComposerAction, event: Key | Paste, steer: bool = False) -> None:
        """Act on what the composer made of `event`; `steer` says the key was Ctrl+Enter."""
        if action.kind == SUBMIT:
            if self.slot is not None:
                if self._slot_arming(event):
                    return  # an Enter meant for something else answers nothing, not the highlighted choice
                answer = self.slot.submit_text(action.text)
                if answer is not None:
                    self._answer(answer)
            elif steer:
                self._steer_or_submit(action.text, action.shown, action.chips)
            else:
                self._submit(action.text, action.shown, action.chips)
        elif action.kind == INTERRUPT:
            self._interrupt_or_exit()
        elif action.kind == EXIT:
            self.exit_code = 0
        elif action.kind == COMPLETE and self.slot is None:
            self._accept_completion()

    def _display_key(self, event: Key | Paste) -> bool:
        """Ctrl+O (the details view) and Ctrl+L (redraw) work whatever has the keys; True when it was one.

        Ctrl+O while Hermes asks something leaves the question on screen.
        """
        if not isinstance(event, Key) or event.name != CHAR or not event.ctrl or event.alt:
            return False
        if event.char == "o":
            if self.slot is None:
                self._details_view()
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
            sid = self.sid
            self._request(
                "input.detect_drop",
                {"session_id": sid, "text": event.text},
                lambda result: self._on_drop(sid, event.text, result),
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
        """Esc: closes the `@` path menu, cancels a picked agent steer (what was typed stays, a draft like any other),
        cancels a rewind, interrupts a running turn on an empty composer.

        Otherwise it arms a second press within `DOUBLE_ESCAPE_SECONDS`: with
        text, the second clears it into this session's history (a draft is no
        line sent, so it stays out of Hermes' history file); on an empty
        composer between turns, the second opens the rewind picker.
        """
        if not isinstance(event, Key) or event.name != ESCAPE:
            return False
        if self.path_items:
            self.path_items = []
            return True
        if self.agent_steer is not None:
            self.agent_steer = None
            return True
        if self.rewind is not None:
            self.rewind = None
            self._set_composer("")
            return True
        if not self.composer.text and self._busy():
            self._interrupt()
            return True
        kind = ESCAPE_CLEAR if self.composer.text else ESCAPE_REWIND
        armed, now = self.escape_armed, self.clock()
        if armed is None or armed[0] != kind or now - armed[1] > DOUBLE_ESCAPE_SECONDS:
            self.escape_armed = (kind, now)
            return True
        self.escape_armed = None
        if kind == ESCAPE_CLEAR:
            self.composer.record(self.composer.expanded(), sent=False)  # a chip's text, not its label
            self._set_composer("")
        else:
            self._rewind("")
        return True

    def _expire_escape(self) -> None:
        """Forget a first Esc once the second can no longer come, so the mode row stops naming it."""
        if self.escape_armed is not None and self.clock() - self.escape_armed[1] > DOUBLE_ESCAPE_SECONDS:
            self.escape_armed = None
            self.dirty = True

    # ── rewind ──

    def _rewind(self, arg: str) -> None:
        """`/rewind` (and Esc Esc): between turns, list this session's prompts to go back to."""
        if self._busy():
            self.transcript.add_notice(REWIND_BUSY_NOTICE, NOTICE_WARN)
        elif not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
        else:
            sid = self.sid
            self._request("session.history", {"session_id": sid}, lambda result: self._open_rewind_picker(sid, result))

    def _open_rewind_picker(self, sid: str, result: dict[str, Any]) -> None:
        if sid != self.sid:
            return
        raw = result.get("messages")
        messages = [message for message in raw if isinstance(message, dict)] if isinstance(raw, list) else []
        rows = rewind_rows({"messages": messages})  # indexed over the very list a cut slices
        if not rows:
            self.transcript.add_notice(REWIND_NONE_NOTICE)
            return
        self.rewind_messages = messages
        self.rewind_picker = RewindPicker(rows)
        self._settle_list()

    def _rewind_picker_key(self, event: Key | Paste | Mouse) -> None:
        """A key for the open rewind picker: the prompt picked comes back into the composer, to edit and send."""
        picker = self.rewind_picker
        if picker is None or isinstance(event, Mouse):
            return
        action = picker.handle_key(event)
        if action is None:
            return
        self.rewind_picker = None
        if action.kind == REWIND_PICK and action.row is not None:
            row = action.row
            before = tuple(self.rewind_messages[: row.index]) if row.index <= len(self.rewind_messages) else ()
            self.rewind = Rewind(row.row_id, row.ordinal, before)
            self._set_composer(row.text)

    # ── Claude Code's commands: /todos, /model, /export ──

    def _todos(self, arg: str) -> None:
        """The whole plan the panel shows a window of, under the echo."""
        text = plan_text(self.hud.payload if self.hud is not None else None)
        if text:
            self._show_output("/todos", text, whole=True)
        else:
            self.transcript.add_notice(TODOS_NONE_NOTICE)

    def _model(self, arg: str) -> None:
        """`/model <name> ...` is Hermes' as typed; bare, between turns, a picker of what Hermes offers this session."""
        if arg:
            self._route_slash(SlashInvocation("model", arg, None), 0)
        elif self._busy():
            # Bare /model has no echo to say this under, so it waits under what runs rather than close it.
            self.transcript.add_aside(NoticeBlock(MODEL_BUSY_NOTICE, NOTICE_WARN))
        elif not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
        else:
            sid = self.sid
            self._request("model.options", {"session_id": sid}, lambda result: self._open_model_picker(sid, result))

    def _open_model_picker(self, sid: str, result: dict[str, Any]) -> None:
        if sid != self.sid:
            return
        self.model_picker = ModelPicker(model_rows(result), current=as_text(result.get("model")) or self.model)
        self._settle_list()

    def _model_picker_key(self, event: Key | Paste | Mouse) -> None:
        """A key for the open model picker: the model picked becomes this session's, through Hermes' own /model."""
        picker = self.model_picker
        if picker is None or isinstance(event, Mouse):
            return
        action = picker.handle_key(event)
        if action is None:
            return
        self.model_picker = None
        if action.kind == MODEL_PICK and action.row is not None:
            self._run_slash(SlashInvocation("model", action.row.arg, None))  # Hermes' own command, echoed

    def _export(self, arg: str) -> None:
        """Hermes' /export, once the person has been told it is a profile archive and not the transcript."""
        if not self.export_said:
            self.export_said = True
            self.transcript.add_notice(EXPORT_NOTE)
            return
        self._route_slash(SlashInvocation("export", arg, None), 0)

    def _send_rewind(self, text: str, shown: str = "") -> None:
        """Send `text` in place of the picked prompt: Hermes drops it and what came after, and the screen restarts there.

        The transcript is rebuilt from the history above the cut (as a resumed
        session's is drawn), the new prompt goes as `prompt.submit` with
        `truncate_before_row_id` and `confirm_truncate`, and the screen is
        redrawn. Plan mode does not wrap it: a truncation is a `prompt.submit`.
        """
        rewind, self.rewind = self.rewind, None
        if rewind is None:
            return
        if self._busy():  # a turn started meanwhile: the row it names may be gone by its end
            self.transcript.add_notice(REWIND_BUSY_NOTICE, NOTICE_WARN)
            self._set_composer(text)
            return
        kept = self.transcript  # the screen before the cut, put back if Hermes refuses it
        self._restart_transcript(list(rewind.before))
        self._submit_prompt(text, shown, truncate=rewind.row_id, before_cut=kept)
        self._full_redraw()

    def _restart_transcript(self, messages: list[dict[str, Any]]) -> None:
        """A fresh transcript holding `messages` as a resumed session's history is drawn."""
        cwd = self.transcript.cwd
        self.transcript = Transcript(self.clock)
        self.transcript.cwd = cwd
        self._draw_history(messages)

    def _on_rewind_refused(self, sid: str, kept: Transcript, error: dict[str, Any]) -> bool:
        """Hermes kept its history: the turn never starts, and the screen goes back to `kept`, as it was before the send.

        Then it is redrawn from the history Hermes sends; if that read fails,
        or is not answered, the screen from before the send stays, and the
        notices say which. A prompt queued behind the rewind goes only once
        that is settled, so the redraw does not wipe its echo.
        """
        if sid != self.sid:
            self._on_submit_error(error)
            return True
        reason = (as_text(error.get("message")) or "refused").strip().rstrip(".")
        self.transcript = kept
        self._full_redraw()
        self.transcript.add_notice(REWIND_KEPT_NOTICE.format(reason=reason), NOTICE_WARN)
        self.turn_pending = False  # no turn starts; the queue waits for the history read
        self._request(
            "session.history",
            {"session_id": sid},
            lambda result: self._redraw_from_history(sid, result, REWIND_REFUSED_NOTICE.format(reason=reason)),
            lambda history_error: self._history_unread(sid, history_error),
            lambda: self._history_unread(sid, {"message": f"no answer in {CALL_ANSWER_SECONDS:g} s"}),
        )
        return True

    def _redraw_from_history(self, sid: str, result: Mapping[str, Any], notice: str) -> None:
        """The screen redrawn from the history Hermes sent, under `notice` (one without messages changes nothing);
        then the next queued line goes, unless a turn started meanwhile."""
        messages = result.get("messages")
        if sid != self.sid:
            return
        if isinstance(messages, list):
            self._restart_transcript([message for message in messages if isinstance(message, dict)])
            self._full_redraw()
            self.transcript.add_notice(notice, NOTICE_WARN)
        self._drain_unless_busy()

    def _history_unread(self, sid: str, error: Mapping[str, Any]) -> bool:
        """No history to redraw from (`error` says why): the screen stays, says so, and the next queued line goes."""
        if sid == self.sid:
            reason = (as_text(error.get("message")) or "refused").strip().rstrip(".")
            self.transcript.add_notice(REWIND_UNREAD_NOTICE.format(reason=reason), NOTICE_WARN)
            self._drain_unless_busy()
        return True

    def _drain_unless_busy(self) -> None:
        """Send the next queued line now, unless a turn runs: then it goes when that turn ends, as any queued line."""
        if not self._busy():
            self._drain_queue()

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
        word = at_token(self.composer.text, self.composer.caret)[1] if self._composer_open() else ""
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

    def _submit(self, text: str, shown: str = "", chips: tuple[tuple[str, str], ...] = ()) -> None:
        """Send what the composer submitted; `shown` is its compact form (paste chips), which the transcript shows.

        `chips` are the chips in `shown` (label, text): a prompt queued behind the turn keeps them for Up.

        Whether the line is a `!` shell line or a slash command is read from
        what the composer showed, never from a chip's text: a paste that
        starts with `!` or `/` is a prompt. Chips are expanded only in what is
        sent: a shell line's command, a command's argument. A chip glued to
        the command's name leaves the name as shown.
        """
        if not text.strip():
            return
        seen = shown or text
        if seen.startswith(SHELL_PREFIX):
            self._shell_line(text, seen)  # a chip holds no `!` of its own, so the sent text starts with it too
            return
        invocation = parse_slash(seen)
        if invocation is not None:
            sent = parse_slash(text)
            if sent is None or sent.name != invocation.name:
                sent = invocation
            self._run_slash(sent, echo=f"/{invocation.text}")  # a picked rewind or agent stays picked under a command
        elif self.agent_steer is not None:
            self._steer_agent(text)
        elif self.rewind is not None:
            self._send_rewind(text, shown)
        else:
            self._send_typed(text, shown=shown, chips=chips)

    def _send_typed(self, text: str, hold: bool = False, shown: str = "", chips: tuple[tuple[str, str], ...] = ()) -> None:
        """A prompt the person typed and sent: in plan mode as `/plan <text>`, the way a typed `/plan` goes.

        `hold` keeps it behind a running turn whatever the busy mode (`_send_prompt`).
        """
        if self.plan_mode:
            self._run_slash(replace(plan_invocation(text), held=hold))
        else:
            self._send_prompt(text, shown=shown, hold=hold, chips=chips)

    def _send_prompt(
        self, text: str, shown: str = "", typed: str = "", hold: bool = False, chips: tuple[tuple[str, str], ...] = ()
    ) -> None:
        """Send `text`, or queue it behind the running turn; `typed` is what Up puts back in the composer.

        `hold` queues it behind a running turn whatever the busy mode.
        """
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        if self._busy() and (hold or self.busy_mode == BUSY_QUEUE):
            self.queued.append(QueuedLine(text, shown, typed, chips=chips))
            return
        self._submit_prompt(text, shown)

    def _submit_prompt(
        self, text: str, shown: str = "", queued: bool = False, truncate: int | None = None,
        before_cut: Transcript | None = None,
    ) -> None:
        """Send one prompt; `queued` says the queue sent it.

        `truncate` names the row a rewind cuts the session before, and
        `before_cut` the transcript from before that cut, which a refusal puts back.
        """
        self.transcript.add_user(shown or text, from_queue=queued)
        self.sticky = shown or text
        self.flushed_since_prompt = 0
        self._clear_roster()  # the agents shown were the last turn's
        self.turn_pending = True
        self.turn_unknown, self.turn_unknown_count = [], 0
        self.busy_since = self.clock()
        self.turn_output_base = self.usage_output
        self.attachments.clear()  # the gateway sends its queued images with this turn
        params: dict[str, Any] = {"session_id": self.sid, "text": text}
        if queued:
            params["queued"] = True  # the busy path holds a drained prompt; it never redirects or steers it
        if truncate is not None:
            params.update(truncate_before_row_id=truncate, confirm_truncate=True)  # the person picked this row
            sid, kept = self.sid, before_cut or self.transcript
            self._request("prompt.submit", params, lambda result: None, lambda error: self._on_rewind_refused(sid, kept, error))
            return
        self._request("prompt.submit", params, lambda result: None, self._on_submit_error)

    def _on_submit_error(self, error: dict[str, Any]) -> bool:
        """A refused prompt.submit: no turn will start, so the next queued prompt goes now, in order."""
        self.turn_pending = False
        self._drain_queue()
        return False

    def _drain_queue(self) -> None:
        """Send the next queued line: a prompt starts the next turn; a `!` line runs, and the one after waits for it.

        Which it is comes from the entry's `shell`, never from its text.
        """
        if self.queued and self.sid:
            line = self.queued.popleft()
            if line.shell:
                self._run_shell(line.text[len(SHELL_PREFIX) :].strip(), drain_after=True, echo=line.shown)
            else:
                self._submit_prompt(line.text, line.shown, queued=True)

    # ── `!` shell lines ──

    def _shell_line(self, text: str, shown: str = "") -> None:
        """A `!command` the person sent: run it now, or after the running turn, as a command waits in Claude Code.

        `shown` is the line as the composer showed it (paste chips), which the echo shows.
        """
        command = text[len(SHELL_PREFIX) :].strip()
        echo = SHELL_PREFIX + shown[len(SHELL_PREFIX) :].strip() if shown and shown != text else ""
        if not command:
            self.transcript.add_notice(SHELL_USAGE, NOTICE_WARN)
        elif self._busy():
            self.queued.append(QueuedLine(text, echo, shell=True))
        else:
            self._run_shell(command, echo=echo)

    def _run_shell(self, command: str, drain_after: bool = False, echo: str = "") -> None:
        """`shell.exec` with the command the person typed, echoed as `› !command`; its output lands under the echo.

        `echo` is the line as the composer showed it, when paste chips made it
        differ. `drain_after` sends the next queued line once Hermes answered,
        so a prompt queued behind it does not start a turn under its output.
        """
        echo = echo or f"{SHELL_PREFIX}{command}"

        def run() -> None:
            self._request(
                "shell.exec",
                {"command": command},
                lambda result: self._on_shell_result(echo, result, drain_after),
                lambda error: self._on_shell_refused(echo, error, drain_after),
            )

        self._as_command(echo, run)

    def _on_shell_result(self, echo: str, result: Mapping[str, Any], drain_after: bool) -> None:
        """What it printed (stdout, then stderr), then its exit code when it failed or printed nothing.

        A failure's printed lines are on the red band with its exit line, as a failed tool's output is.
        """
        printed = [text for text in (as_text(result.get("stdout")), as_text(result.get("stderr"))) if text.strip()]
        code = result.get("code")
        code = code if isinstance(code, int) and not isinstance(code, bool) else None
        for text in printed:
            self.transcript.add_output(text.rstrip("\n"), OUTPUT_FAILED if code != 0 else NOTICE_INFO, command=echo)
        if code != 0:
            self.transcript.add_output(f"exit {code if code is not None else '?'}", NOTICE_ERROR, command=echo)
        elif not printed:
            self.transcript.add_output("exit 0", command=echo)
        if drain_after:
            self._drain_queue()

    def _on_shell_refused(self, echo: str, error: Mapping[str, Any], drain_after: bool) -> bool:
        """Hermes' own words: a refusal of a dangerous command (4005) is a warning, a timeout or failure an error."""
        level = NOTICE_WARN if error.get("code") == SHELL_REFUSED else NOTICE_ERROR
        self.transcript.add_output(plain_sentence(as_text(error.get("message"))), level, command=echo)
        if drain_after:
            self._drain_queue()
        return True

    def _recall_queue(self, event: Key | Paste) -> bool:
        """Up on an empty composer takes the queued prompts back into it, one per line, up to a queued `!` line.

        Each comes back as what was typed for it: a prompt a slash command
        built as that command line, a prompt plan mode wrapped as the text
        typed in plan mode. Enter then submits the composer as one text, as it
        submits anything typed: if the text starts with a slash command it
        goes as that command, whose text is everything after the slash, later
        lines included; otherwise it goes as one prompt (in plan mode as
        `/plan <text>`), with a command on any later line as plain text in it.
        A `!` line is never merged with another line, since sent as one text
        the shell would run them all: when it is next in the queue it comes
        back alone, and otherwise the prompts before it come back and it stays
        queued, with everything after it. A line sent again while the turn
        still runs joins the queue at its end.
        The composer decides again what the text it gets back shows, as for
        anything typed. So a prompt that comes back first and was a paste chip
        whose text starts with `!` comes back as it was shown, its chips chips
        again (`Composer.restore`) and the words typed beside them text (one
        chip holding its text where the queue kept no chips), and the
        composer shows a prompt, as it did when it was sent; any other
        prompt whose text starts with `!` (a refused steer's) shows as a `!`
        line, and Enter then runs it as one.
        With nothing queued Up is the composer's: it walks the input history.
        """
        if not isinstance(event, Key) or event.name != UP or self.composer.text or not self.queued:
            return False
        taken = [self.queued.popleft()]
        while self.queued and not taken[0].shell and not self.queued[0].shell:
            taken.append(self.queued.popleft())
        first = taken[0]
        if not first.shell and not first.typed and first.shown and first.text.startswith(SHELL_PREFIX):
            # Its chips again, never a `!` line the composer would run; without them, one chip holding its text.
            if first.chips:
                self.composer.restore(first.shown, first.chips)
            else:
                self.composer.handle(Paste(first.text))
            for line in taken[1:]:
                self.composer.replace(self.composer.caret, self.composer.caret, "\n" + (line.typed or line.text))
        else:
            self._set_composer("\n".join(line.typed or line.text for line in taken))
        return True

    # ── steering ──

    def _steer_or_submit(self, text: str, shown: str = "", chips: tuple[tuple[str, str], ...] = ()) -> None:
        """Ctrl+Enter: steer the running turn with the typed text. Idle, or on a slash command or a `!` line, it is Enter.

        As for Enter, what the line is comes from what the composer showed (`shown`, paste chips unexpanded).
        """
        seen = shown or text
        if self._busy() and seen.strip() and parse_slash(seen) is None and not seen.startswith(SHELL_PREFIX):
            self._steer(text)
        else:
            self._submit(text, shown, chips)

    def _steer_command(self, arg: str) -> None:
        """`/steer <text>`: what Ctrl+Enter does, on any terminal. Idle, the text goes as a prompt."""
        if not arg:
            self.transcript.add_aside(NoticeBlock(STEER_USAGE, NOTICE_WARN))  # no echo: it must not close what runs
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
        block = self.transcript.add_steer(text)  # `↪ steer · <text>` under the live blocks, until Hermes answers
        self._request(
            "session.steer",
            {"session_id": sid, "text": text},
            lambda result: self._on_steered(sid, block, result),
            lambda error: self._on_steer_refused(sid, block, error),
            lambda: self._on_steer_unanswered(sid, block),
        )

    def _on_steered(self, sid: str, block: SteerBlock, result: dict[str, Any]) -> None:
        if sid != self.sid:
            self._steer_back(block.text)
        elif as_text(result.get("status")) == contract.STEER_REJECTED:
            self._steer_refused(block, STEER_REJECTED_REASON)
        else:
            self.transcript.steer_answered(block, STEER_QUEUED)

    def _on_steer_refused(self, sid: str, block: SteerBlock, error: dict[str, Any]) -> bool:
        """Hermes could not steer (an agent still starting, or one that takes no steer): the text is the next prompt."""
        if sid != self.sid:
            self._steer_back(block.text)
            return True
        self._steer_refused(block, as_text(error.get("message")) or "refused")  # Hermes' words, not its code
        return True

    def _on_steer_unanswered(self, sid: str, block: SteerBlock) -> None:
        """No answer within `CALL_ANSWER_SECONDS`: the text comes back to the person and the row leaves.

        Hermes may have taken the steer, so its text is never sent or queued again.
        """
        if sid != self.sid:
            self._steer_back(block.text, STEER_LEFT_UNANSWERED_NOTICE.format(seconds=CALL_ANSWER_SECONDS))
        else:
            self._steer_unanswered(block)

    def _on_agent_steer_unanswered(self, sid: str, session: str, block: SteerBlock) -> None:
        """An agent steer with no answer within `CALL_ANSWER_SECONDS`: as the main steer's, and if `sid`, the session
        it went to, was left, a notice names the agent and `session`."""
        if sid != self.sid:
            said = AGENT_STEER_LEFT_UNANSWERED_NOTICE.format(name=block.agent, seconds=CALL_ANSWER_SECONDS, session=session)
            self._steer_back(block.text, said)
        else:
            self._steer_unanswered(block)

    def _steer_unanswered(self, block: SteerBlock) -> None:
        """A steer, the main turn's or an agent's, whose call expired: its row says so and its text is given back."""
        self.transcript.steer_answered(block, STEER_REFUSED, STEER_UNANSWERED_OUTCOME.format(seconds=CALL_ANSWER_SECONDS))
        self._give_back(block.text)

    def _steer_refused(self, block: SteerBlock, reason: str) -> None:
        """The steer says what became of its text (with Hermes' `reason`), which goes as the next prompt.

        While a turn runs it waits in the TUI's queue, whatever the busy mode;
        only an idle session gets it as a prompt at once.
        """
        fallback = STEER_FALLBACK_QUEUED if self._busy() else STEER_FALLBACK_SENT
        self.transcript.steer_answered(block, STEER_REFUSED, STEER_REFUSED_OUTCOME.format(fallback=fallback, reason=reason))
        self._send_typed(block.text, hold=True)

    def _steer_back(self, text: str, notice: str = STEER_LEFT_NOTICE) -> None:
        """A steer settled after its session was left: the text returns to the person, never to the live session."""
        self._give_back(text)
        self.transcript.add_notice(notice, NOTICE_WARN)

    def _give_back(self, text: str) -> None:
        """A steer's text back to the person, never into an answer to Hermes: on a line of its own after the draft,
        the one set aside while a question has a composer of its own. Its paste chips stay, and so does its caret
        while the draft is not drawn."""
        draft = self.slot_draft if self.slot_draft is not None else self.composer
        caret, end = draft.caret, len(draft.text)
        draft.replace(end, end, f"\n{text}" if draft.text else text)
        if draft is self.slot_draft or self._composer_hidden():
            draft.caret = caret  # a hidden draft comes back as the person left it, the text after its end

    # ── slash commands ──

    def _run_slash(self, invocation: SlashInvocation, depth: int = 0, echo: str = "") -> None:
        """Run a slash command; one the person typed is echoed first, and stays open while its calls are out.

        Everything it says while open (a notice, its output) lands under the
        echo, which is `echo` when paste chips made the line shown differ. A
        command plan mode wrote around a prompt is no command the person ran,
        and an alias's target runs under the alias's echo.
        """
        command = invocation.command
        echoed = command is None or (command.echo and (bool(invocation.arg) or command.echo_bare))
        if depth or invocation.for_plan_mode or not echoed:
            self._route_slash(invocation, depth)
            return
        self._as_command(echo or f"/{invocation.text}", lambda: self._route_slash(invocation, depth))

    def _as_command(self, echo: str, run: Callable[[], None]) -> None:
        """Echo `echo` and run `run` as that command: its calls keep the echo open until they are answered."""
        transcript = self.transcript
        outer = transcript.command
        block = transcript.add_command(echo)
        try:
            run()
        finally:
            transcript.command = outer
        if not any(pending.command is block for pending in self.pending.values()):
            transcript.close_command(block)

    def _route_slash(self, invocation: SlashInvocation, depth: int) -> None:
        command = invocation.command
        if command is not None and command.route == LOCAL:
            self._local_handlers()[command.name](invocation.arg)
        elif command is not None and command.route == HERMES:
            self.transcript.add_notice(hermes_notice(command))
        elif command is not None and command.route == DROPPED:
            self.transcript.add_notice(dropped_notice(command))
        elif command is not None and command.route == RENAMED:
            self._route_slash(SlashInvocation(command.target, invocation.arg, None), depth)
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
        self._show_output(f"/{invocation.text}", f"warning: {warning}\n{body}" if warning else body)

    def _on_dispatch(self, invocation: SlashInvocation, result: dict[str, Any], depth: int) -> None:
        kind = as_text(result.get("type"))
        if kind in ("exec", "plugin"):
            self._show_output(f"/{invocation.text}", as_text(result.get("output")) or "(no output)")
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
                self._send_prompt(message, shown, typed=invocation.typed, hold=invocation.held)
            else:
                self.transcript.add_notice(f"/{invocation.name}: empty message", NOTICE_WARN)
        elif kind == "prefill":
            if message:
                self._set_composer(message)
        else:
            other = as_text(result.get("output")) or as_text(result.get("display"))
            if other:
                self._show_output(f"/{invocation.text}", other)

    def _show_output(self, command: str, text: str, whole: bool = False) -> None:
        """A command's output, in the transcript under its echo (`command` repeats the echo once it has closed).

        However long it is: a long one shows its first rows and a count of the
        rest, which /history shows, unless `whole`. The pager opens only when
        the person asks for one, so output answered while a view has the
        alternate screen waits in the transcript until they leave it.
        """
        self.transcript.add_output(text, command=command, whole=whole)

    def _local_handlers(self) -> dict[str, Callable[[str], None]]:
        return {
            "help": self._help,
            "keys": self._keys,
            "quit": self._quit,
            "new": self._new_session,
            "redraw": self._redraw,
            "history": self._history,
            "rewind": self._rewind,
            "todos": self._todos,
            "model": self._model,
            "export": self._export,
            "details": self._details,
            "queue": self._show_queue,
            "steer": self._steer_command,
            "effort": self._effort_command,
            "logs": self._logs,
            "copy": self._copy,
            "theme": self._theme,
            "omh-model": self._omh_model,
            "reload-skills": self._reload_skills,
            "sessions": self._sessions,
            "image": self._image,
            "paste": self._paste,
        }

    def _help(self, arg: str) -> None:
        """The TUI's commands, Hermes' by category, and how many skill commands there are; paged when long."""
        local = [command for command in COMMANDS if command.route == LOCAL]
        width = max(len(command.name) for command in local)
        rows = ["OMH TUI commands:"]
        rows += [f"/{command.name:<{width}} {command.summary}" for command in local]
        if self.catalog_categories:
            rows.append("Hermes commands:")
            rows += [f"  {category}: {' '.join(names)}" for category, names in self.catalog_categories]
        else:
            rows.append("Hermes commands: none listed.")
        if self.skill_count:
            rows.append(SKILL_COMMANDS_NOTE.format(count=self.skill_count))
        rows.append(YOLO_NOTE)
        rows.append(HELP_KEYS)
        rows.append(BARE_HERMES_NOTE)
        self._show_output("/help", "\n".join(rows), whole=True)

    def _reload_skills(self, arg: str) -> None:
        """Hermes re-scans its skills for this session, then the catalog is read again, as Ink's /reload-skills does.

        Runs here rather than through `slash.exec`: Hermes runs those in a
        separate per-session worker process, and only the gateway's own
        `skills.reload` clears the skill commands `command.dispatch` reads.
        """
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        self._request("skills.reload", {"session_id": self.sid}, self._on_skills_reloaded, self._on_reload_refused)

    def _on_skills_reloaded(self, result: dict[str, Any]) -> None:
        self._show_output("/reload-skills", as_text(result.get("output")) or "Skills reloaded.")
        self._refresh_catalog()

    def _on_reload_refused(self, error: dict[str, Any]) -> bool:
        """A refused rescan, drift included (`contract.OPTIONAL_METHODS`): a notice, and the session goes on."""
        message = as_text(error.get("message")) or "refused"
        self.transcript.add_notice(RELOAD_REFUSED_NOTICE.format(code=error.get("code"), message=message), NOTICE_WARN)
        return True

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
            self._turn_off_yolo_set_here(self.sid)
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
        self.banner_record = self._banner_record(result, resumed="")  # a later full redraw names this session
        self.transcript.add_notice(f"New session {self.stored_id}.")
        self._notice_declined(declined)
        self._refresh_catalog()

    def _on_create_refused(self, error: dict[str, Any]) -> bool:
        message = as_text(error.get("message")) or "refused"
        self.transcript.add_notice(
            f"Could not start a new session ({error.get('code')}): {message}. Still in {self.stored_id}.", NOTICE_WARN
        )
        return True

    def _enter_session(self, result: Mapping[str, Any], fallback_stored: str = "") -> int:
        """Leave the current session for the one `result` opened; how many of its requests were declined.

        The old session's open requests are declined, yolo the TUI set there
        is turned off, and its runtime closed; everything that belonged to it
        (transcript, queue, sticky prompt, attachments, HUD snapshot,
        subagents) is dropped before the new one is adopted.
        The caller says so in the new transcript, after its own notice.
        """
        old = self.sid
        self.in_launch_session = False
        declined = self._decline_all_requests()
        if old and old != as_text(result.get("session_id")):
            self._turn_off_yolo_set_here(old)
            self._request("session.close", {"session_id": old}, lambda result: None, lambda error: True)
        self.transcript = Transcript(self.clock)
        self.queued.clear()
        self.sticky = ""
        self.flushed_since_prompt = 0  # rows printed above the region from the last prompt's first row on
        self.attachments.clear()  # the gateway queued them for the session being left
        self.hud = None
        self._clear_roster()
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
        """Leave the old session and redraw the screen as the resumed one.

        Hermes resolves a title or a compressed ancestor to the session
        already live and answers with that session's id. That is no switch:
        nothing is left, declined or reset, so the yolo the TUI set there is
        still the TUI's to turn off.
        """
        if as_text(result.get("session_id")) == self.sid:
            self.transcript.add_notice(f"Already in session {self.stored_id}.")
            return
        declined = self._enter_session(result, fallback_stored=target)
        self._draw_history(result.get("messages"))
        self._notice_declined(declined)
        self.banner_record = self._banner_record(result, resumed=self.stored_id)
        self._full_redraw()
        self._redeliver(result.get("open_requests"))
        self._refresh_catalog()  # its workspace may hold project-local skills the last one did not

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
            sid = self.sid
            self._request(
                "image.attach",
                {"session_id": sid, "path": arg},
                lambda result: self._on_image_attached(sid, result),
                lambda error: self._attach_failed("/image", error),
            )

    def _paste(self, arg: str) -> None:
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        sid = self.sid
        self._request(
            "clipboard.paste",
            {"session_id": sid},
            lambda result: self._on_clipboard_pasted(sid, result),
            lambda error: self._attach_failed("/paste", error),
        )

    def _take_attachment(self, sid: str, attachment: Attachment) -> None:
        """A chip for an image Hermes queued for `sid`; for a session since left, a notice instead."""
        if sid == self.sid:
            self.attachments.append(attachment)
        else:
            self.transcript.add_notice(ATTACH_LEFT_NOTICE.format(name=attachment.name), NOTICE_WARN)

    def _on_image_attached(self, sid: str, result: dict[str, Any]) -> None:
        attachment = attachment_from(result) if result.get("attached") is True else None
        if attachment is None:
            self.transcript.add_notice("/image: Hermes did not attach the file.", NOTICE_WARN)
            return
        self._take_attachment(sid, attachment)
        remainder = as_text(result.get("remainder")).strip()
        if remainder:  # `/image shot.png what is this?` keeps the question for the person to send
            self.composer.handle(Paste(remainder))

    def _on_clipboard_pasted(self, sid: str, result: dict[str, Any]) -> None:
        attachment = attachment_from(result) if result.get("attached") is True else None
        if attachment is None:
            self.transcript.add_notice(as_text(result.get("message")) or "No image found in the clipboard.")
        else:
            self._take_attachment(sid, attachment)

    def _attach_failed(self, command: str, error: dict[str, Any]) -> bool:
        message = as_text(error.get("message")) or "not attached"
        self.transcript.add_notice(f"{command}: {message} ({error.get('code')}).", NOTICE_WARN)
        return True

    def _on_drop(self, sid: str, text: str, result: dict[str, Any]) -> None:
        """An image drop is already queued (a chip, nothing typed); a file drop types its reference; else the paste.

        Text goes to the composer whichever session is live: it is the
        person's paste, sent nowhere yet. An image Hermes queued for a session
        since left is a notice (`_take_attachment`).
        """
        if result.get("matched") is True and result.get("is_image") is True:
            attachment = attachment_from(result)
            if attachment is not None:
                self._take_attachment(sid, attachment)
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
        """The blocks held now in a pager that renders each one, details shown, only when a page reaches it."""
        transcript = self.transcript
        blocks, dropped = list(transcript.blocks), transcript.dropped  # what the pager holds, as it opens

        def rows(index: int, width: int, theme: Theme) -> list[Line]:
            shown = block_rows(blocks[index], width, theme, expanded=True)
            return [*transcript.dropped_rows(width, theme, dropped), *shown] if index == 0 else shown

        pager = HistoryPager("history", count=len(blocks), render=rows)
        # Stops at the first block with rows, the first one but for a reply that was only a line break, and
        # keeps what it rendered for the first frame.
        if pager.shows_nothing(self.size[0], self.theme):
            transcript.add_notice("Nothing in this session's transcript yet.")
            return
        self._show_pager(pager)

    def _details_view(self) -> None:
        """The whole transcript with every detail (thinking, tool output, whole diffs) in the pager, at its end."""
        self._history("")
        if isinstance(self.pager, HistoryPager):
            self.pager.handle_key(Key(END), self.size[1])

    def _details(self, arg: str) -> None:
        # No notice: its echo is held under a live block (`Transcript.add_command`), the only block the toggle changes.
        self.transcript.toggle_details()

    def _show_queue(self, arg: str) -> None:
        if not self.queued:
            self.transcript.add_notice("Nothing is queued.")
            return
        rows = ["Queued (sent one at a time as each turn ends):"]
        rows += [f"{number}. {line.shown or line.text}" for number, line in enumerate(self.queued, start=1)]
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
        # "replace", as `Terminal.write` encodes: a lone surrogate (JSON can carry one) is copied as the "?" the
        # screen shows, not as bytes that are no UTF-8 for the terminal to put on the clipboard.
        encoded = base64.b64encode(last.text.strip().encode("utf-8", "replace")).decode("ascii")
        self.terminal.write(OSC52_COPY.format(encoded))
        self.transcript.add_notice("Copied the last reply to the clipboard (OSC 52; the terminal must allow it).")

    def _theme(self, arg: str) -> None:
        """`/theme <name>` picks a palette, `/theme auto|light|dark` the background it is measured against; alone, both.

        Either redraws the screen, so the rows above are repainted in it.
        """
        choice = arg.strip().lower()
        if not choice:
            self.transcript.add_notice(
                f"Theme {self.theme.name}: {self._background_text()}. /theme <name> picks a palette "
                f"({', '.join(theme_names())}); /theme auto|light|dark picks the background it is measured against."
            )
            return
        if choice in BACKGROUND_MODES:
            self.background_mode = choice
        elif theme_for_name(arg) or theme_for_skin_name(arg):
            self.theme = theme_named(arg)
        else:
            self.transcript.add_notice(f"No theme named {arg}. Available: {', '.join(theme_names())}.", NOTICE_WARN)
            return
        self.theme = build_theme(self._skin(), self._surface())
        if choice in BACKGROUND_MODES:
            self.transcript.add_notice(f"Theme {self.theme.name} {self._background_text()}.")
        else:
            self.transcript.add_notice(f"Theme {self.theme.name} applies to this session; `omh theme` keeps it.")
        self._full_redraw()

    def _background_text(self) -> str:
        """How the theme's background was chosen, for `/theme`'s notices."""
        if self.background_mode != BACKGROUND_AUTO:
            return f"on a {self.background_mode} background for this session; /theme auto follows the terminal again"
        if self.background_named_dark:
            return "follows the terminal's background (a dark one, as COLORFGBG names it)"
        if self.background is None:
            return "follows the terminal's background (none reported, so a dark one is assumed)"
        tone = "light" if is_light(self.background) else "dark"
        return f"follows the terminal's background ({self.background}, a {tone} one)"

    def _omh_model(self, arg: str) -> None:
        """Run the model-chain picker on the alternate screen, over the TUI's own raw terminal.

        The main screen (scrollback and the live region) stays untouched underneath
        and comes back as it was, so the picker leaves no frame behind; only a resize
        while it was open needs the full redraw. Gateway events wait while it is open: a
        new approval shows once the picker closes, and Hermes may withdraw a request on its
        own timeout meanwhile. Nothing is answered for the person either way.
        """
        before = self.size
        self._set_mouse(False)  # the strip is not on the picker's screen; the next frame asks again
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
            self.terminal.write(ALT_SCREEN_OFF + self._caret_visibility())
        self.size = self.terminal.size()
        if self.size != before:
            self._full_redraw()
        self.dirty = True
        self.transcript.add_notice(message)

    def _picker_reader(self) -> Callable[[], str]:
        """A picker's `read_key` over the TUI's terminal and key decoder; a hung-up terminal cancels.

        The decoder is told what the loop tells it, so a report sent before
        the picker's off still types nothing and its ESC does not cancel. As
        in the loop, an empty read settles held bytes only once input has been
        quiet for `FLUSH_SILENCE_SECONDS`: a resize signal ends a read early
        with nothing read, and must not turn an arrow's ESC into Esc.
        """
        pending: deque[Key | Paste | Mouse] = deque()

        def read_key() -> str:
            while not pending:
                try:
                    data = self.terminal.read(READ_TIMEOUT_SECONDS)
                except EOFError:
                    return KEY_QUIT
                if data:
                    self.input_at = self.clock()
                    pending.extend(self.decoder.feed(data))
                elif self.clock() - self.input_at >= FLUSH_SILENCE_SECONDS:
                    pending.extend(self.decoder.flush(self.terminal.mouse_reporting, self.mouse_off_at))
            return picker_key(pending.popleft())

        return read_key

    def _caret_visibility(self) -> str:
        """The cursor as the live region's last frame left it, for the main screen coming back from the alternate one."""
        return HIDE_CURSOR if self.region.caret_hidden else SHOW_CURSOR

    # ── pager ──

    def _open_pager(self, title: str, lines: list[Line]) -> None:
        """`lines` in a pager on the alternate screen (`_show_pager`)."""
        self._show_pager(Pager(title, lines))

    def _show_pager(self, pager: Pager) -> None:
        """`pager` on the alternate screen; a pager already open is replaced without switching to that screen again.

        A second `ALT_SCREEN_ON` would save the main screen's cursor over the
        one saved on the way in. It is no agent's log: `_open_agent_log` names
        the agent once this returns.
        """
        if self.pager is None:
            self.terminal.write(ALT_SCREEN_ON)
        self.pager = pager
        self.log_agent = ""

    def _leave_pager(self) -> None:
        self.pager = None
        self.log_agent = ""
        self.terminal.write(ALT_SCREEN_OFF + self._caret_visibility())
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
        self._set_mouse(False)
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

        The banner and the transcript's last `REDRAW_TAIL_ROWS` rows are
        rendered anew (`Transcript.retake_tail`), under one row counting the
        messages above them, so nothing wrapped at the old width survives, no
        earlier frame is left in scrollback, and a long session costs a redraw
        no more than that. Scrollback from before the TUI started is erased
        with it.
        """
        self.terminal.write(CLEAR_SCREEN_AND_SCROLLBACK)
        # The clear homes the cursor, so the next first frame starts on the screen's top row: unless a
        # pager is open, when it cleared the alternate screen and the main screen's cursor is where it was.
        self.region = LiveRegion(self.terminal.write, at_top=self.pager is None)
        if self.pager is not None:
            self.pager.invalidate()  # the clear took its rows too
        self.transcript.retake_tail(self.size[0], self.theme, REDRAW_TAIL_ROWS)
        if self.banner_record is not None:
            self.banner_facts = dict(self.banner_record)
            # The logo comes back only on top of the whole session it opened, never above a cut.
            self.logo_due = self.in_launch_session and not self.transcript.left_out
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
        due = (self._busy() or self._panel_live()) and now - self.last_render >= self._tick_seconds()
        if not (self.dirty or due):
            return
        self.dirty = False
        self.last_render = now
        self._render()

    def _render(self) -> None:
        cols, rows = self.size
        self._settle_list()
        self._write_title()
        if self.pager is not None:
            self.clickable = {}
            self._set_mouse(False)
            self._refresh_agent_log()
            self.terminal.write(self.pager.frame(cols, rows, self.theme))
            return
        stable = self.transcript.take_stable_lines(cols, self.theme)
        prompt_row = self.transcript.prompt_row
        if self.banner_facts is not None:
            head = banner_logo(self._skin(), self._surface(), cols, rows) if self.logo_due else []
            head += banner_lines(**self.banner_facts, theme=self.theme, width=cols)
            stable = head + stable
            prompt_row = None if prompt_row is None else prompt_row + len(head)
            self.banner_facts = None
        anchoring = self.anchor
        # Counted from the prompt's own first row: not the blank row or the held rows above it, and after a full
        # redraw only the rows retaken from the prompt on.
        if prompt_row is not None:
            self.flushed_since_prompt = len(stable) - prompt_row
        else:
            self.flushed_since_prompt += len(stable)
        previous = 0 if anchoring else self.region.height
        self.anchor = False
        strip, _hits = self._agent_strip(cols)
        listed: tuple[str, ...] = ()

        def agent_list(room: int) -> list[Line]:
            nonlocal listed
            lines, listed = self._agent_list(cols, room)
            return lines

        build = partial(
            build_frame,
            cols=cols,
            rows=rows,
            composer=None if self._composer_hidden() else self.composer,
            status=self._status_line(cols),
            previous_height=previous,
            tail=self._tail(cols),
            slot=self._slot_lines(cols, rows),
            theme=self.theme,
            attachments=attachment_line(self.attachments, cols, self.theme)
            if self.attachments and self.slot is None
            else None,
            mode=self._display_mode(),
            mode_row=self._mode_row_line(cols, strip),
            placeholder=PLACEHOLDER if self._composer_open() else "",
            agent_list=agent_list if self.list_selected is not None else None,
        )
        frame = build()
        # The flush below fills this frame's pad rows first, so they are transcript rows by the time it shows.
        if self._sticky_needed(rows - frame.height + min(len(stable), frame.pad)):
            frame = build(sticky=sticky_line(self.sticky, cols, self.theme))
        # Flushed after the frame, the lines land in its pad rows: the rows a shrinking tail gave
        # back (a settled tool group is those same rows, moved above the region), or the screen of
        # pad rows an anchoring frame fills. Flushed before it, they would push the region down by
        # their whole count, and the rows the tail gave back would stay as blank pad under them.
        # Frame and flush are one write, so no screen between them shows.
        self.region.render(frame.lines, frame.cursor, cols, pad=frame.pad, flush=stable)
        self._take_clickable(frame.budget, listed)

    def _display_mode(self) -> str:
        """What the composer's frame shows: the shell while it holds a `!` line for Hermes' shell, else the input mode."""
        if self._composer_open() and self.composer.text.startswith(SHELL_PREFIX):
            return MODE_SHELL
        return self.mode

    def _composer_open(self) -> bool:
        """Whether the composer takes the keys for a prompt: no question, picker or search has them."""
        return (
            self.slot is None and self.picker is None and self.search is None and self.rewind_picker is None
            and self.model_picker is None
        )

    def _composer_hidden(self) -> bool:
        """Whether the frame draws no composer: the person picks from a list, so nothing is typed into it.

        A question answered by picking (`PromptSlot.picking`) and the session,
        rewind and model pickers, whose filter is a row of their own. The
        history search, the completion menus and the agent list keep it.
        """
        if self.slot is not None:
            return self.slot.picking
        return self.picker is not None or self.rewind_picker is not None or self.model_picker is not None

    def _mode_row_line(self, cols: int, strip: Line | None = None) -> Line:
        """The mode row, saying what the next key does when that is not the usual (a rewind, Esc's second press).

        A picked steer or rewind keeps room for its last form but one, the shortest that still names what it
        acts on, beside the effort: the strip collapses (`● 2 agents ↓`) before that would be cut. The mode's
        label keeps its room too (`_agent_strip` starts after it).
        """
        say: str | tuple[str, ...] = ""
        say_style = None
        armed = self.escape_armed
        steered = self.roster.agents.get(self.agent_steer or "")
        picker_keys = self._picker_keys()
        if self.slot is not None:
            say = self.slot.keys
        elif picker_keys:
            say = picker_keys
        elif steered is not None:
            say, say_style = tuple(form.format(name=steered.name) for form in STEER_SAY), self.theme.accent
        elif self.rewind is not None:
            say, say_style = tuple(form.format(ordinal=self.rewind.ordinal) for form in REWIND_SAY), self.theme.warn
        elif armed is not None and self.clock() - armed[1] <= DOUBLE_ESCAPE_SECONDS:
            say = ESCAPE_AGAIN.format(armed[0])
        if isinstance(say, tuple):
            effort = cell_width(f"effort {self.effort}") + MODE_ROW_GAP if self.effort else 0
            strip = self._agent_strip(cols - effort - cell_width(STRIP_SEPARATOR + say[max(len(say) - 2, 0)]))[0]
        return mode_row_line(
            mode=self._display_mode(),
            busy=self._busy(),
            queued=len(self.queued),
            keyboard_protocol=self.keyboard_protocol,
            effort=self.effort,
            width=cols,
            theme=self.theme,
            strip=self._agent_strip(cols)[0] if strip is None else strip,
            say=say,
            say_style=say_style,
            esc_interrupts=self._esc_interrupts(),
        )

    def _picker_keys(self) -> tuple[str, ...]:
        """The open picker's keys, said on the mode row in place of the composer's; () with no picker open."""
        if self.picker is not None:
            return SESSION_PICKER_KEYS
        if self.rewind_picker is not None:
            return REWIND_PICKER_KEYS
        return MODEL_PICKER_KEYS if self.model_picker is not None else ()

    def _esc_interrupts(self) -> bool:
        """Whether Esc interrupts the running turn now: nothing else takes it (`_escape`, and what has the keys).

        The rows offer Esc for the interrupt only then, so the screen gives Esc one meaning: a question, a
        picker, the search, the agent list, a picked steer or rewind, the `@` menu and a draft each take it.
        """
        return (
            self._busy() and self._composer_open() and self.list_selected is None and not self.path_items
            and self.agent_steer is None and self.rewind is None and not self.composer.text
        )

    # ── subagents ──

    def _spinner_frame(self) -> int:
        """The frame every spinner and sweep shows now; the first, always, with reduced motion."""
        return int(self.clock() / TICK_SECONDS) if self.motion else 0

    def _tick_seconds(self) -> float:
        return TICK_SECONDS if self.motion else REDUCED_TICK_SECONDS

    @property
    def keyboard_protocol(self) -> bool:
        """Whether Ctrl+Enter reaches the TUI apart from Enter: a key has arrived as a CSI u or modifyOtherKeys report.

        Asking the terminal for those reports is not enough, since a terminal
        on the list can ignore the request and send Ctrl+Enter as Enter. So
        the mode row names `/steer` until the first such key, and Ctrl+Enter
        from then on.
        """
        return self.decoder.protocol_reported

    def _sticky_needed(self, above: int) -> bool:
        """Whether the sent prompt needs echoing above the composer: only once it has scrolled off.

        `above` is how many screen rows lie above the live region. Everything
        there is transcript, so while the rows printed from the prompt's first
        row on still fit there, the prompt itself is on screen and an echo
        would only repeat it.
        """
        if not (self._busy() and self.sticky.strip()):
            return False
        return self.flushed_since_prompt > above

    def _tail(self, cols: int) -> list[Line]:
        now = self.clock()
        # Never more than the screen; running tools' marks pulse unless motion is reduced.
        lines = self.transcript.live_lines(cols, self.theme, max_rows=self.size[1], now=now if self.motion else None)
        if self._busy():
            text = self.transcript.activity or self.transcript.status or "working…"
            frame = self._spinner_frame()
            spinner = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
            elapsed = elapsed_text(now - (self.busy_since or now))
            tokens = self._turn_tokens()
            written = f" · ↓{compact_count(tokens)} tokens" if tokens else ""
            interrupt = f" · {INTERRUPT_HINT}" if self._esc_interrupts() else ""  # else Esc means something else now
            doing = working_spans(text, frame, self.theme)
            row = (Span(spinner, self.theme.accent), Span(" "), *doing,
                   Span(f" ({elapsed}{written}{interrupt})", self.theme.muted))
            lines.append(truncate_line(row, cols))
        return lines

    def _slot_lines(self, cols: int, rows: int) -> list[Line]:
        """The slot's rows; with the composer hidden it has the composer frame's rows too (`layout.choice_cap`)."""
        cap = choice_cap(rows, cols) if self._composer_hidden() else slot_cap(rows, cols)
        if cap <= 0:
            return []
        if self.slot is not None:
            # Where the terminal has no mode row, the question's keys take a row of the slot's own.
            return self.slot.lines(cols, self.theme, cap, keys_row=not under_composer(rows, cols)[1])
        keys = not under_composer(rows, cols)[1]  # a picker's keys go in its header where no mode row says them
        if self.picker is not None:
            return self.picker.lines(cols, self.theme, cap, self.wall_clock(), keys=keys)
        if self.search is not None:
            return self.search.lines(cols, self.theme, cap)
        if self.rewind_picker is not None:
            return self.rewind_picker.lines(cols, self.theme, cap, self.wall_clock(), keys=keys)
        if self.model_picker is not None:
            return self.model_picker.lines(cols, self.theme, cap, keys=keys)
        # Nothing has the keys: the queued prompts take the slot's last rows, right above the composer's
        # top rule, Hermes' notification the row above them, and the menu or the panel gets the rows left. A
        # question or the picker hides them, since Up then does not edit the queue.
        queue = queue_lines([line.shown or line.text for line in self.queued], cols, cap, self.theme)
        notice = [self.notification.line(cols, self.theme)] if self.notification and cap > len(queue) else []
        return self._menu_or_panel_lines(cols, cap - len(queue) - len(notice)) + notice + queue

    def _menu_or_panel_lines(self, cols: int, cap: int) -> list[Line]:
        if cap <= 0:
            return []
        rows = self._menu_rows()
        if rows:
            return _menu_lines(rows, min(self.menu_index, len(rows) - 1), cols, cap, self.theme)
        payload = self.hud.payload if self.hud is not None else None
        if payload is None:
            return []
        return panel_lines(
            payload, cols, cap, self.theme, self.wall_clock(), received_at=self.hud.received_at,
            show_agents=not self.roster.agents,  # the strip and the list show them while the roster holds any
            motion=self.motion,
        )

    def _state_text(self) -> str:
        """What the session is doing; idle with subagents still running says how many, since the turn is not all."""
        if self.slot is not None:
            state = STATE_WAITING
        elif self._busy():
            state = STATE_RUNNING
        else:
            state = STATE_IDLE
            running = self.roster.running_count()
            if running:
                state += f" · {running} agent{'' if running == 1 else 's'} running"
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
        roles = on_status_bar(self.theme)  # measured against the bar they are drawn on
        shown = (
            (MODE_ACCEPT, roles.mode_accept, self.approvals_off),
            (MODE_PLAN, roles.mode_plan, self.plan_mode),
        )
        return tuple((MODE_CHIPS[mode], replace(colour, bg=bar.bg, bold=True)) for mode, colour, on in shown if on)

    def _status_line(self, cols: int) -> Line:
        """The status line, composed again only when something it shows changed (a spinner tick changes nothing)."""
        shown = (cols, self.size[1], self.model, self.context, self.context_percent, self._state_text(),
                 self.approvals_off, self.plan_mode)
        held = self.status_held
        if held is not None and held[0] == shown and held[1] is self.theme and held[2] is self.hud:
            return held[3]
        line = self._compose_status_line(cols)
        self.status_held = (shown, self.theme, self.hud, line)
        return line

    def _compose_status_line(self, cols: int) -> Line:
        """OMH's segments, then model, context and state, on the status bar's background, in roles measured against it.

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
            Span(span.text, replace(span.style, bg=bar.bg))
            for span in status_segments(payload, on_status_bar(self.theme))
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
        return request_fallback(environ)
    terminal = open_terminal()
    app = App(
        terminal,
        client,
        environ,
        poller=make_poller(environ.get("OMH_HOME") or None, environ.get("HERMES_HOME") or None),
        theme=initial_theme(ready.payload, environ.get("OMH_HOME") or None),
    )
    try:
        app.startup()
    except (GatewayCallError, GatewayTimeout, GatewayUnavailable) as error:
        _say(stderr, f"omh: the OMH TUI could not open a Hermes session: {error}")
        return request_fallback(environ)
    with terminal:
        background, typed = terminal.query_background()
        app.adopt_background(background, environ)
        code = app.run(typed)
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
        return request_fallback(environ)
    if attach_refused(environ):
        _say(stderr, attach_message(environ))
        return 1
    try:
        client = make_client(gateway_spawn(environ))
    except GatewayUnavailable as error:
        _say(stderr, f"omh: the Hermes gateway did not start: {error}")
        return request_fallback(environ)
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
