"""The OMH TUI's event loop and entry point.

`main` is what `python -m omh.tui` runs once Hermes' launcher has handed the
frontend its environment. Everything that can stop the TUI from starting is
settled before the first byte is drawn, and each such failure exits with
`FALLBACK_EXIT`, so `omh` opens Hermes' own terminal instead: a terminal that
cannot run the TUI, a gateway that does not start, and a startup call this
Hermes no longer understands. An attach hand-off is refused with exit 1 and a
line naming `hermes --tui --resume <id>` (0 would make `omh` print a resume
hint for a session this TUI never opened).

`App` is the loop. One thread owns the terminal and every model: it reads
input with a short timeout, routes keys (the pager while it is open, then
Ctrl+O and Ctrl+L, an open prompt slot, the session picker, the attachment
keys and Esc, the completion menu, then the composer), takes everything the
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
import sys
import time
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
from .frame import STATUS_SEPARATOR, build_frame, sticky_line
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
from .keys import BACKSPACE, CHAR, DOWN, ENTER, ESCAPE, LEFT, RIGHT, UP, Key, KeyDecoder, Paste
from .launch import FALLBACK_EXIT
from .layout import slot_cap
from .markdown import clean_text
from .pager import ALT_SCREEN_OFF, ALT_SCREEN_ON, Pager
from .panels import SPINNER_FRAMES, HudPoller, HudSnapshot, panel_lines, status_segments
from .prompts import CHOICE_DIGITS, Answer, ImmediateAnswer, PromptSlot, open_request
from .screen import CLEAR_SCREEN_AND_SCROLLBACK, CURSOR_HOME, HIDE_CURSOR, SHOW_CURSOR, LiveRegion
from .session_picker import PICK, SessionPicker, session_rows
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
)
from .style import Line, Span
from .terminal import Terminal, terminal_supported
from .theme import DEFAULT_THEME, Theme, build_theme, theme_named
from .transcript import NOTICE_ERROR, NOTICE_WARN, AssistantBlock, Transcript, as_text
from .width import line_width, truncate_left, truncate_line, wrap_line
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

BANNER_BRAND = "⚚ OH-MY-HERMES"
BANNER_HINT = "/help for commands · Ctrl+C twice to exit · bare hermes opens Hermes' own terminal"

BUSY_QUEUE = "queue"
BUSY_MODES = frozenset({"queue", "steer", "interrupt"})

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_WAITING = "waiting on you"

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
HELP_KEYS = (
    "Keys: Enter sends · \\ then Enter, Alt+Enter or Ctrl+J adds a line · Tab completes a /command or an @path\n"
    "Esc interrupts a running turn · Ctrl+V attaches the clipboard image (drag & drop works too) · "
    "Backspace on empty input removes the last one\n"
    "Ctrl+O shows or hides details · Ctrl+L redraws · Ctrl+C interrupts a turn or clears the input · "
    "Ctrl+D on empty input exits"
)


class TerminalLike(Protocol):
    def __enter__(self) -> Any: ...
    def __exit__(self, *exc: object) -> Any: ...
    def size(self) -> tuple[int, int]: ...
    def resized(self) -> bool: ...
    def read(self, timeout: float) -> bytes: ...
    def write(self, text: str) -> None: ...


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


def picker_key(event: Key | Paste) -> str:
    """One decoded key as an OMH picker's key token. Raw mode makes Ctrl+C a key, and it cancels like Esc."""
    if isinstance(event, Paste) or event.alt:
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
        self.decoder = KeyDecoder()
        self.size = terminal.size()
        self.sid = ""
        self.stored_id = ""
        self.model = ""
        self.context = ""
        self.busy_mode = BUSY_QUEUE
        self.catalog: list[tuple[str, str]] = []
        self.slot: PromptSlot | None = None
        self.slot_opened_at = 0.0
        self.slot_draft = ""
        self.waiting_requests: deque[ServerRequest] = deque()
        self.queued: deque[tuple[str, str]] = deque()  # (text sent, text shown)
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
        cwd = as_text(info.get("cwd")) if isinstance(info, dict) else ""
        self.transcript.cwd = cwd or str(self._create_params().get("cwd", ""))
        self.turn_pending = bool(result.get("running"))
        self.busy_since = self.clock()
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
        for event in self.decoder.feed(data) if data else self.decoder.flush():
            self.dirty = True
            self._on_input(event)
            if self.exit_code is not None:
                return
        self._drain_gateway()
        if self.exit_code is not None:
            return
        self._track_path_token()
        self._ask_path_completion_when_due()
        self._take_hud()
        self._render_when_due()

    def _shutdown(self) -> None:
        self.poller.stop()
        if self.pager is not None:
            self.pager = None
            self.terminal.write(ALT_SCREEN_OFF + SHOW_CURSOR)
        cols = self.size[0]
        stable = self.transcript.take_stable_lines(cols, self.theme)
        if stable:
            self.region.flush_above(stable, cols)
        self.region.clear()

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
            self.context = context_text(payload.get("usage")) or self.context
        elif event.type == "connection.request":
            self._on_connection_request(payload)
        elif event.type == "error":
            self._on_error_event(as_text(payload.get("message")) or "The gateway reported an error.")
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
        self.context = context_text(payload.get("usage")) or self.context
        # `running` is not read: the previous turn's closing `running: false` arrives after
        # `message.complete`, by which time a queued prompt may already be pending.
        self._adopt_stored(as_text(payload.get("stored_session_id")))

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

    def _decline_all_requests(self) -> None:
        """Answer every open and waiting request with its decline frame (the session is going away)."""
        while self.slot is not None:
            self._answer(self.slot.decline())
        while self.waiting_requests:
            request = self.waiting_requests.popleft()
            opened = open_request(request.id, request.method, request.params)
            self._respond(opened.answer if isinstance(opened, ImmediateAnswer) else opened.decline())

    # ── input ──

    def _set_composer(self, text: str) -> None:
        self.composer.replace(0, len(self.composer.text), text)

    def _on_input(self, event: Key | Paste) -> None:
        if self.pager is not None:
            if not self.pager.handle_key(event, self.size[1]):
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
        if self.slot is None and (self._attachment_input(event) or self._escape(event)):
            return
        if self.slot is None and isinstance(event, Key) and self._menu_moves(event):
            return
        before = self.composer.text
        action = self.composer.handle(event)
        if self.composer.text != before:
            self.menu_index = 0
        self._on_composer(action)

    def _slot_arming(self) -> bool:
        """Whether the open slot opened too recently to take a key or a submit (`SLOT_ARM_SECONDS`)."""
        return self.slot is not None and self.clock() - self.slot_opened_at < SLOT_ARM_SECONDS

    def _on_composer(self, action: ComposerAction) -> None:
        if action.kind == SUBMIT:
            if self.slot is not None:
                if self._slot_arming():
                    return  # an Enter meant for something else answers nothing, not the highlighted choice
                answer = self.slot.submit_text(action.text)
                if answer is not None:
                    self._answer(answer)
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
            self._send_prompt(text)

    def _send_prompt(self, text: str, shown: str = "") -> None:
        if not self.sid:
            self.transcript.add_notice(NO_SESSION_NOTICE, NOTICE_WARN)
            return
        if self._busy() and self.busy_mode == BUSY_QUEUE:
            self.queued.append((text, shown))
            return
        self._submit_prompt(text, shown)

    def _submit_prompt(self, text: str, shown: str = "", queued: bool = False) -> None:
        self.transcript.add_user(shown or text)
        self.sticky = shown or text
        self.flushed_since_prompt = 0
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
            text, shown = self.queued.popleft()
            self._submit_prompt(text, shown, queued=True)

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
                self._send_prompt(message, as_text(result.get("display")).strip())
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
        """A command's output as a notice, or in the pager when it is longer than half the screen."""
        cols, rows = self.size
        source = text.split("\n")
        if len(source) <= max(rows // 2, 3):
            self.transcript.add_notice(text)
            return
        lines = [wrapped for line in source for wrapped in (wrap_line((Span(line),), cols) or [()])]
        self._open_pager(f"/{name}", lines)
        self.transcript.add_notice(f"/{name}: {len(source)} lines, shown in the pager.")

    def _local_handlers(self) -> dict[str, Callable[[str], None]]:
        return {
            "help": self._help,
            "quit": self._quit,
            "new": self._new_session,
            "redraw": self._redraw,
            "history": self._history,
            "details": self._details,
            "queue": self._show_queue,
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
        self.transcript.add_notice("\n".join(rows))

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
        self._enter_session(result)
        self.transcript.add_notice(f"New session {self.stored_id}.")

    def _on_create_refused(self, error: dict[str, Any]) -> bool:
        message = as_text(error.get("message")) or "refused"
        self.transcript.add_notice(
            f"Could not start a new session ({error.get('code')}): {message}. Still in {self.stored_id}.", NOTICE_WARN
        )
        return True

    def _enter_session(self, result: Mapping[str, Any], fallback_stored: str = "") -> None:
        """Leave the current session for the one `result` opened.

        The old session's open requests are declined and its runtime closed;
        everything that belonged to it (transcript, queue, sticky prompt,
        attachments, HUD snapshot) is dropped before the new one is adopted.
        """
        old = self.sid
        self._decline_all_requests()
        if old and old != as_text(result.get("session_id")):
            self._request("session.close", {"session_id": old}, lambda result: None, lambda error: True)
        self.transcript = Transcript(self.clock)
        self.queued.clear()
        self.sticky = ""
        self.flushed_since_prompt = 0  # rows printed above the region since the last prompt was sent
        self.attachments.clear()  # the gateway queued them for the session being left
        self.hud = None
        self._adopt_session(result, fallback_stored)

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
        self._enter_session(result, fallback_stored=target)
        self._draw_history(result.get("messages"))
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
        rows += [f"{number}. {shown or text}" for number, (text, shown) in enumerate(self.queued, start=1)]
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
        pending: deque[Key | Paste] = deque()

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
        self.pager = Pager(title, lines)
        self.terminal.write(ALT_SCREEN_ON)

    def _leave_pager(self) -> None:
        self.pager = None
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
        """
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
        self.region = LiveRegion(self.terminal.write)
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
        if self.pager is not None:
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
        )
        self.region.render(frame.lines, frame.cursor, cols, pad=frame.pad)
        if stable and anchoring:
            # An anchoring frame fills the screen with pad rows. Lines flushed before it
            # (the banner, a resumed history) would be pushed off the top before anyone
            # saw them; flushed after it, they land in those pad rows instead.
            self.region.flush_above(stable, cols)

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

    def _status_line(self, cols: int) -> Line:
        """OMH's segments, then model, context and state, on the status bar's background.

        When it does not fit, the context goes first and the model next; then
        OMH's segments are cut from their right, so the state (idle / running /
        waiting on you) is the last thing cut.
        """
        bar = self.theme.status
        payload = self.hud.payload if self.hud is not None else None
        head: Line = tuple(
            Span(span.text, replace(span.style, bg=bar.bg)) for span in status_segments(payload, self.theme)
        )
        state = self._state_text()
        tail: Line = ()
        for fields in ((self.model, self.context, state), (self.model, state), (state,)):
            tail = tuple(
                span for text in fields if text for span in (Span(STATUS_SEPARATOR, self.theme.status_dim), Span(text, bar))
            )
            if line_width(head) + line_width(tail) <= cols:
                break
        line = truncate_line(truncate_line(head, max(cols - line_width(tail), 0)) + tail, cols)
        filler = cols - line_width(line)
        return line + ((Span(" " * filler, bar),) if filler > 0 else ())


# ── entry point ───────────────────────────────────────────────────────────


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
    open_terminal: Callable[[], TerminalLike] = Terminal,
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
    try:
        return _run(client, environ, stderr, open_terminal, make_poller)
    except KeyboardInterrupt:
        # Ctrl+C is a signal, not a key, until the terminal goes raw: while the gateway starts
        # and the session opens. The child is reaped below either way.
        _say(stderr, "omh: the OMH TUI was interrupted.")
        return INTERRUPTED_EXIT
    finally:
        # The screen has settled and the terminal is the shell's again; only now wait on the child.
        client.close(grace=CLOSE_GRACE_SECONDS, terminate_grace=TERMINATE_GRACE_SECONDS)
