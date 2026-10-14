"""The transcript of one OMH TUI session, and the rule for when its lines may leave the live region.

Inline scrollback: a line printed above the live region lives in
the terminal's own scrollback and is never repainted; only the live region
repaints. So the model answers three questions for the app loop:

- `take_stable_lines`: lines that will never change again. Each is returned
  once and then belongs to the terminal.
- `live_lines`: everything not yet taken, as it looks now (painted inside the
  live region every frame).
- `history_lines`: every block held, as `/history` shows them (its pager
  renders them a page at a time, with `block_rows`). At most `BLOCK_CAP`
  blocks are held; past it the oldest ones already taken are dropped
  (`dropped` counts them).

The invariant the tests hold: at any moment, the lines already taken followed
by `live_lines()` equal `history_lines(details=False)` at the same width
(unless `toggle_details` expanded the live block, or the cap dropped blocks).
Only the LAST block can still change; appending any block first closes the
one before it, so a block is flushed only after every block above it.

Assistant text opens with `⏺ ` in the accent on its first row, and every
later row of that reply, however many flushes it arrives in, is indented to
match, so a reply reads as one block beside the `› ` of the prompt above it.

Flush policy: a user or notice block flushes at once; assistant text flushes
per completed source line (a trailing blank line waits for the text after it,
so a reply that ends there leaves nothing to take back) and fully at
`message.complete`; the markdown state (an open fence) carries across the
flush boundary; thinking flushes as ONE collapsed row ("▸ thought for 4.2s");
a tool group flushes when the next block arrives (message text, thinking) or
at `message.complete`, as "⏺ Tool calls (N)" (plus "parallel ×K" when K tools
ran at once) and one line per tool; a group of one call is that one line. An edit tool whose `tool.complete` carries
an `inline_diff` shows that diff under its row (`diff_view.py`): the first
`PREVIEW_LINES` diff lines and a count of the rest, which `/history` (and
`history_lines` with details) shows in full. Lines rendered at one width stay as they were taken; a
resize changes only what has not been taken yet.

Two events do not mean what their names suggest, measured against Hermes
6c61c3e27d (the recorded fixtures and the gateway source):

- `thinking.delta` is the agent's spinner and wait text ("(⌐■_■) analyzing...",
  "" clears it; `tui_gateway/agent_callbacks.py::_agent_thinking_update` says
  "They are not reasoning blocks"). It sets `activity`, never a block.
- `reasoning.available` relays the assistant's own content, cut to 500 chars
  (`agent/turn_response_intake.py::_relay_thinking`); in every recorded turn it
  repeats the answer. It is ignored. Real reasoning arrives as `reasoning.delta`.

Nothing here is written to disk.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import time
from typing import Any

from .diff_view import DIFF_FILE, FILE_ARROW, DiffLine, diff_rows, parse_inline_diff
from .gateway import Event
from .markdown import MarkdownState, clean_text, render_lines, wrap_prefixed
from .style import Line, Span, Style
from .theme import Theme
from .width import line_width, truncate, truncate_left, truncate_line

TOOL_GENERATING = "generating"
TOOL_RUNNING = "running"
TOOL_OK = "ok"
TOOL_ERROR = "error"
TOOL_DENIED = "denied"
TOOL_INTERRUPTED = "interrupted"  # still open when its group closed: it never reported a result

NOTICE_INFO = "info"
NOTICE_WARN = "warn"
NOTICE_ERROR = "error"

INTERRUPTED_NOTICE = "Interrupted."
FAILED_NOTICE = "The turn ended with an error."
# The first row a full redraw prints when `retake_tail` left the session's earlier blocks out; once the cap
# below has dropped blocks, /history no longer holds them all, and the row says how many it does.
LEFT_OUT_NOTICE = "⋯ {earlier} · /history shows all"
LEFT_OUT_DROPPED_NOTICE = "⋯ {earlier} · /history shows the last {held}"
# /history's first row once the cap below has dropped blocks.
DROPPED_NOTICE = "⋯ {earlier} no longer held"
# The most blocks a transcript holds (Hermes' own TUI keeps 800 messages). Past it the oldest blocks already
# printed are dropped: they stay in the terminal's scrollback until a full redraw clears it, and /history no
# longer has them.
BLOCK_CAP = 2000

USER_MARK = "› "
COLLAPSED_MARK = "▸"
EXPANDED_MARK = "▾"
TOOL_GROUP_MARK = "⏺ "
ASSISTANT_MARK = "⏺ "
ASSISTANT_INDENT = "  "
TOOL_SUMMARY_MARK = "⎿ "
TOOL_ROW_INDENT = "  "
DETAIL_INDENT = "  "
COLUMN_GAP = "  "


@dataclass
class UserBlock:
    text: str
    done: bool = True


@dataclass
class NoticeBlock:
    text: str
    level: str = NOTICE_INFO
    done: bool = True


@dataclass
class AssistantBlock:
    text: str = ""
    done: bool = False


@dataclass
class ThinkingBlock:
    text: str = ""
    done: bool = False
    started_at: float | None = None
    ended_at: float | None = None


@dataclass
class ToolRow:
    tool_id: str
    name: str
    args_summary: str = ""
    status: str = TOOL_GENERATING
    started_at: float | None = None
    duration_s: float | None = None
    summary: str = ""
    # An edit's `inline_diff` as Hermes sent it, UTF-8 encoded, parsed when drawn (`diff_lines`). Encoded, because
    # a str holding one character past Latin-1 (every Hermes diff has `┊` and `→`) takes two bytes for each of
    # its characters, which made the diff held as a str larger than the parsed lines; as bytes it is about half.
    inline_diff: bytes = b""
    diff_root: str = ""  # the session's working directory, which the diff's file names are shown relative to

    def diff_lines(self) -> tuple[DiffLine, ...]:
        """The diff colour-stripped and classified, its file names relative to `diff_root`."""
        return tuple(
            DiffLine(line.kind, FILE_ARROW.join(_relative(self.diff_root, side) for side in line.text.split(FILE_ARROW)))
            if line.kind == DIFF_FILE
            else line
            for line in parse_inline_diff(self.inline_diff.decode("utf-8"))
        )


@dataclass
class ToolGroup:
    tools: list[ToolRow] = field(default_factory=list)
    done: bool = False
    parallel: int = 1  # most tools running at the same moment


Block = UserBlock | AssistantBlock | ThinkingBlock | ToolGroup | NoticeBlock


# ── payload reading ───────────────────────────────────────────────────────


def as_text(value: object) -> str:
    """`value` when it is a string, else "": how every gateway payload field is read."""
    return value if isinstance(value, str) else ""


def _first_line(text: str) -> str:
    for line in text.split("\n"):
        cleaned = clean_text(line).strip()
        if cleaned:
            return cleaned
    return ""


def _last_line(text: str) -> str:
    for line in reversed(text.split("\n")):
        cleaned = clean_text(line).strip()
        if cleaned:
            return cleaned
    return ""


def _is_blank(line: str) -> bool:
    return not clean_text(line).strip()


def _source_lines(text: str) -> list[str]:
    """`text` split into lines without the blank lines it opens with (a segment starts "\\n\\n")."""
    lines = text.split("\n")
    while len(lines) > 1 and _is_blank(lines[0]):
        lines.pop(0)
    return lines


def _without_trailing_blanks(lines: list[str]) -> list[str]:
    while lines and _is_blank(lines[-1]):
        lines.pop()
    return lines


def _display_lines(text: str) -> list[str]:
    """Every source line as it shows now, the unfinished last one included."""
    return _without_trailing_blanks(_source_lines(text))


def _stable_lines(text: str) -> list[str]:
    """The source lines that can no longer change: complete ones, minus trailing blanks."""
    return _without_trailing_blanks(_source_lines(text)[:-1])


def _is_bare_error(text: str, error: str) -> bool:
    """True when the reply slot holds nothing but the backend's "Error: …" fallback (Ink's rule)."""
    stripped = text.strip()
    return not stripped or stripped == f"Error: {error}".strip() or stripped == error.strip()


def tool_result_status(result: object) -> str:
    """ok / error / denied from a `tool.complete` result (the wire carries no status flag of its own).

    An approval the user denied comes back from the terminal tool as `status: "blocked"`
    (recorded: exit_code -1, `user_summary` "You denied this command — it did not run.");
    the approval gates' own dicts say `approved: false` / `user_consent: false`.
    """
    if not isinstance(result, dict):
        return TOOL_OK
    if result.get("status") == "blocked" or result.get("approved") is False or result.get("user_consent") is False:
        return TOOL_DENIED
    if result.get("error") or result.get("success") is False or result.get("status") == "error":
        return TOOL_ERROR
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return TOOL_ERROR
    return TOOL_OK


def _result_summary(payload: dict[str, Any]) -> str:
    summary = _first_line(as_text(payload.get("summary")))
    if summary:
        return summary
    result = payload.get("result")
    if isinstance(result, dict):
        for key in ("user_summary", "error", "output"):
            line = _first_line(as_text(result.get(key)))
            if line:
                return line
        return ""
    return _first_line(as_text(result))


def _args_summary(payload: dict[str, Any]) -> str:
    context = _first_line(as_text(payload.get("context")))
    if context:
        return context
    args = payload.get("args")
    if isinstance(args, dict):
        for value in args.values():
            line = _first_line(as_text(value))
            if line:
                return line
    return ""


def _duration(payload: dict[str, Any], row: ToolRow, now: float) -> float | None:
    value = payload.get("duration_s")
    if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
        return float(value)
    if row.started_at is not None:
        return max(0.0, now - row.started_at)
    return None


def _earlier(count: int) -> str:
    return f"{count} earlier {'message' if count == 1 else 'messages'}"


def _relative(root: str, path: str) -> str:
    """`path` relative to the directory `root` when it lies inside it."""
    root = root.rstrip("/")
    prefix = root + "/" if root else ""  # "/" itself is no prefix: every absolute path stays absolute
    return path[len(prefix):] if prefix and path.startswith(prefix) and len(path) > len(prefix) else path


def _find_row(group: ToolGroup, tool_id: str) -> ToolRow | None:
    for row in group.tools:
        if tool_id and row.tool_id == tool_id:
            return row
    return None


def _drafted_row(group: ToolGroup, name: str) -> ToolRow | None:
    """The first `tool.generating` row for `name` that no `tool.start` has claimed yet."""
    for row in group.tools:
        if row.status == TOOL_GENERATING and not row.tool_id and row.name == name:
            return row
    return None


# ── rendering ─────────────────────────────────────────────────────────────


def _text_rows(
    text: str, style: Style, first: Line, rest: Line, width: int, max_rows: int | None = None
) -> list[Line]:
    """Plain text (no markdown), each source line wrapped, `first` before the very first row.

    Each source line wraps on its own, so with `max_rows` only the last lines
    that cover that many rows are wrapped; the rows end as all of them would.
    """
    wrapped: list[list[Line]] = []  # each source line's rows, the last line first
    count = 0
    lines = _without_trailing_blanks(_source_lines(text))
    for index in range(len(lines) - 1, -1, -1):
        if max_rows is not None and count >= max_rows:
            break
        source = lines[index]
        if _is_blank(source):
            rows: list[Line] = [()]  # never the first row: leading blank lines were dropped
        else:
            rows = wrap_prefixed((Span(clean_text(source), style),), first if index == 0 else rest, rest, width)
        wrapped.append(rows)
        count += len(rows)
    return [row for rows in reversed(wrapped) for row in rows]


def _user_rows(block: UserBlock, width: int, theme: Theme) -> list[Line]:
    return _text_rows(block.text, theme.text, (Span(USER_MARK, theme.user),), (Span(" " * len(USER_MARK)),), width)


def _notice_style(level: str, theme: Theme) -> tuple[str, Style]:
    if level == NOTICE_ERROR:
        return "✗ ", theme.error
    if level == NOTICE_WARN:
        return "⚠ ", theme.warn
    return "· ", theme.dim


def _notice_rows(block: NoticeBlock, width: int, theme: Theme) -> list[Line]:
    mark, style = _notice_style(block.level, theme)
    return _text_rows(block.text, style, (Span(mark, style),), (Span(" " * len(mark)),), width)


def _marked(rows: list[Line], opens_block: bool, theme: Theme) -> list[Line]:
    """Assistant rows behind the reply's margin: `⏺ ` on the block's first row, an indent on every other one."""
    first: Line = (Span(ASSISTANT_MARK, theme.accent),)
    rest: Line = (Span(ASSISTANT_INDENT),)
    return [(*(first if opens_block and index == 0 else rest), *row) if row else () for index, row in enumerate(rows)]


def _reply_width(width: int) -> int:
    """The cells left for reply text beside its margin."""
    return max(1, width - len(ASSISTANT_INDENT))


def _assistant_rows(block: AssistantBlock, width: int, theme: Theme) -> list[Line]:
    rows = render_lines(_display_lines(block.text), MarkdownState(), _reply_width(width), theme)[0]
    return _marked(rows, True, theme)


def _seconds(value: float) -> str:
    return f"{value:.1f}s"


def _thinking_rows(
    block: ThinkingBlock, width: int, theme: Theme, expanded: bool, max_rows: int | None = None
) -> list[Line]:
    mark = EXPANDED_MARK if expanded else COLLAPSED_MARK
    if block.done:
        header = f"{mark} thought"
        if block.started_at is not None and block.ended_at is not None:
            header += f" for {_seconds(max(0.0, block.ended_at - block.started_at))}"
        head: Line = (Span(header, theme.thinking),)
    elif expanded:
        head = (Span(f"{mark} thinking…", theme.thinking),)
    else:
        head = (Span(f"{mark} thinking… ", theme.thinking), Span(_last_line(block.text), theme.dim))
    rows = [truncate_line(head, width)]
    if expanded:
        indent: Line = (Span(DETAIL_INDENT),)
        rows.extend(_text_rows(block.text, theme.thinking, indent, indent, width, max_rows))
    return rows


def _tool_mark(status: str, theme: Theme) -> Span:
    if status == TOOL_OK:
        return Span("✓", theme.ok)
    if status == TOOL_ERROR:
        return Span("✗", theme.error)
    if status == TOOL_DENIED:
        return Span("⊘", theme.warn)
    if status == TOOL_RUNNING:
        return Span("◔", theme.accent)
    if status == TOOL_INTERRUPTED:
        return Span("▪", theme.dim)
    return Span("◌", theme.dim)


def _is_path(text: str) -> bool:
    """A single path argument (`/abs/x.py`, `~/x`, `./x`): its end names the file, so it is cut from the left."""
    return " " not in text and text.startswith(("/", "~/", "./", "../"))


def _tool_row(tool: ToolRow, width: int, theme: Theme) -> Line:
    """One line per tool: mark, name, args summary (cut first), duration."""
    head: Line = (Span(TOOL_ROW_INDENT), _tool_mark(tool.status, theme), Span(" "), Span(tool.name, theme.tool))
    tail: Line = ()
    if tool.duration_s is not None:
        tail = (Span(COLUMN_GAP + _seconds(tool.duration_s), theme.dim),)
    room = width - line_width(head) - line_width(tail) - len(COLUMN_GAP)
    args: Line = ()
    if tool.args_summary and room > 1:
        cut = truncate_left if _is_path(tool.args_summary) else truncate
        args = (Span(COLUMN_GAP + cut(tool.args_summary, room), theme.dim),)
    return truncate_line((*head, *args, *tail), width)


def _tool_rows(group: ToolGroup, width: int, theme: Theme, expanded: bool) -> list[Line]:
    header = [Span(TOOL_GROUP_MARK, theme.tool), Span(f"Tool calls ({len(group.tools)})", theme.label)]
    if group.parallel > 1:
        header.append(Span(f"{COLUMN_GAP}parallel ×{group.parallel}", theme.accent))
    # One call needs no header: its own row already says what ran.
    rows = [truncate_line(tuple(header), width)] if len(group.tools) != 1 else []
    summary_first: Line = (Span(TOOL_ROW_INDENT * 2 + TOOL_SUMMARY_MARK, theme.dim),)
    summary_rest: Line = (Span(" " * (len(TOOL_ROW_INDENT) * 2 + len(TOOL_SUMMARY_MARK))),)
    for tool in group.tools:
        rows.append(_tool_row(tool, width, theme))
        if expanded and tool.summary:
            rows.extend(wrap_prefixed((Span(tool.summary, theme.dim),), summary_first, summary_rest, width))
        lines = tool.diff_lines() if tool.inline_diff else ()
        if lines:
            rows.extend(diff_rows(lines, width, theme, full=expanded))
    return rows


def block_rows(block: Block, width: int, theme: Theme, expanded: bool = False, max_rows: int | None = None) -> list[Line]:
    """The rows one block shows at `width`; `expanded` adds thinking text, tool result summaries, whole diffs.

    `max_rows` lets a block whose rows end the same either way (thinking
    text) render only its last rows; the rows returned may start lower.
    """
    width = max(1, width)
    if isinstance(block, UserBlock):
        return _user_rows(block, width, theme)
    if isinstance(block, NoticeBlock):
        return _notice_rows(block, width, theme)
    if isinstance(block, AssistantBlock):
        return _assistant_rows(block, width, theme)
    if isinstance(block, ThinkingBlock):
        return _thinking_rows(block, width, theme, expanded, max_rows)
    return _tool_rows(block, width, theme, expanded)


# ── the model ─────────────────────────────────────────────────────────────


class Transcript:
    """Blocks in order, the turn's transient state, and how far the terminal has been handed lines."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.cwd = ""  # the session's working directory; paths inside it are shown relative to it
        self.blocks: list[Block] = []
        self.activity = ""  # spinner / wait text from thinking.delta; "" when none
        self.status = ""  # the last status.update text of the running turn; "" when none
        self.running = False
        self.details_expanded = False
        self._clock = clock
        self._turn_start = 0
        self._cursor = 0  # first block not fully taken
        self._cursor_lines = 0  # source lines of blocks[_cursor] already taken (assistant text)
        self._cursor_state = MarkdownState()  # markdown state after those lines
        self._taken_any = False  # whether any line has been taken (a spacer goes above the next block)
        self._left_out = 0  # blocks `retake_tail` left out: the next take opens with LEFT_OUT_NOTICE
        self.dropped = 0  # blocks BLOCK_CAP dropped from the front
        # Moves with every change to what `live_lines` draws; its last answer is kept with the version, width,
        # row limit and theme it was for, so a frame that changed nothing (a spinner tick) renders nothing.
        self._version = 0
        self._live_held: tuple[tuple[int, int, int | None], Theme, list[Line]] | None = None
        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "message.start": self._on_message_start,
            "message.delta": self._on_message_delta,
            "message.interim": self._on_message_interim,
            "message.complete": self._on_message_complete,
            "thinking.delta": self._on_thinking_delta,
            "reasoning.delta": self._on_reasoning_delta,
            "reasoning.available": self._on_reasoning_available,
            "tool.generating": self._on_tool_generating,
            "tool.start": self._on_tool_start,
            "tool.complete": self._on_tool_complete,
            "status.update": self._on_status_update,
        }

    # ── input ──

    def apply(self, event: Event) -> None:
        """Fold one gateway event into the transcript; other event types are not the transcript's."""
        handler = self._handlers.get(event.type)
        if handler is not None:
            handler(event.payload if isinstance(event.payload, dict) else {})
            self._version += 1

    def add_user(self, text: str) -> None:
        self._append(UserBlock(text))

    def add_notice(self, text: str, level: str = NOTICE_INFO) -> None:
        self._append(NoticeBlock(text, level))

    def add_assistant(self, text: str) -> None:
        """A finished reply (a resumed session's history), rendered like a streamed one."""
        if text.strip():
            self._append(AssistantBlock(text, done=True))

    def fail_turn(self, message: str) -> None:
        """A turn the gateway ended with a bare `error` event instead of `message.complete`.

        The live block closes as it stands, the error follows it, and the turn
        state clears, as `message.complete` with `status: error` would leave it.
        """
        self._close_live()
        self._append(NoticeBlock(message, NOTICE_ERROR))
        self.running = False
        self.activity = ""
        self.status = ""

    def toggle_details(self) -> bool:
        """Expand or collapse the live block's details; blocks already taken never repaint."""
        self.details_expanded = not self.details_expanded
        self._version += 1
        return self.details_expanded

    # ── block bookkeeping ──

    def _live(self, kind: type) -> Any:
        last = self.blocks[-1] if self.blocks else None
        if isinstance(last, kind) and not last.done:
            return last
        return None

    def _close_live(self) -> None:
        last = self.blocks[-1] if self.blocks else None
        if last is None or last.done:
            return
        last.done = True
        if isinstance(last, ThinkingBlock):
            last.ended_at = self._clock()
        if isinstance(last, ToolGroup):
            for row in last.tools:
                if row.status in (TOOL_GENERATING, TOOL_RUNNING):
                    row.status = TOOL_INTERRUPTED

    def _append(self, block: Block) -> None:
        self._close_live()
        self.blocks.append(block)
        self._version += 1
        # Past the cap, drop the oldest blocks already taken; one not taken yet stays until it is.
        drop = min(len(self.blocks) - BLOCK_CAP, self._cursor)
        if drop > 0:
            del self.blocks[:drop]
            self._cursor -= drop
            self._turn_start = max(self._turn_start - drop, 0)
            self.dropped += drop

    def _tool_group(self) -> ToolGroup:
        group = self._live(ToolGroup)
        if group is None:
            group = ToolGroup()
            self._append(group)
        return group

    def _taken_lines_of(self, block: Block) -> int:
        at_cursor = self._cursor < len(self.blocks) and self.blocks[self._cursor] is block
        return self._cursor_lines if at_cursor else 0

    def _settle_text(self, block: AssistantBlock, text: str) -> None:
        """Adopt the gateway's authoritative text unless it contradicts lines already taken."""
        new_lines = _display_lines(text)
        old_lines = _display_lines(block.text)
        if not new_lines or new_lines == old_lines:
            return
        taken = self._taken_lines_of(block)
        if new_lines[:taken] == old_lines[:taken]:
            block.text = text

    def _repeats_turn_answer(self, text: str) -> bool:
        lines = _display_lines(text)
        return any(
            isinstance(block, AssistantBlock) and _display_lines(block.text) == lines
            for block in self.blocks[self._turn_start :]
        )

    # ── event handlers ──

    def _on_message_start(self, payload: dict[str, Any]) -> None:
        self._close_live()
        self._turn_start = len(self.blocks)
        self.running = True

    def _on_message_delta(self, payload: dict[str, Any]) -> None:
        text = as_text(payload.get("text"))
        if not text:
            return
        block = self._live(AssistantBlock)
        if block is None:
            block = AssistantBlock()
            self._append(block)
        block.text += text

    def _on_message_interim(self, payload: dict[str, Any]) -> None:
        """Commentary the model wrote alongside tool calls: seal it as its own block."""
        text = as_text(payload.get("text"))
        block = self._live(AssistantBlock)
        if block is not None:
            if not payload.get("already_streamed"):
                self._settle_text(block, text)
            block.done = True
            return
        if text.strip() and not self._repeats_turn_answer(text):
            self._append(AssistantBlock(text, done=True))

    def _on_message_complete(self, payload: dict[str, Any]) -> None:
        text = as_text(payload.get("text"))
        status = as_text(payload.get("status")) or "complete"
        error = as_text(payload.get("error"))
        bare_error = status == "error" and _is_bare_error(text, error)
        block = self._live(AssistantBlock)
        if block is not None and not bare_error:
            self._settle_text(block, text)
        elif block is None and not bare_error and text.strip() and not self._repeats_turn_answer(text):
            self._append(AssistantBlock(text))
        self._close_live()
        if status == "interrupted":
            self._append(NoticeBlock(INTERRUPTED_NOTICE, NOTICE_WARN))
        if status == "error":
            self._append(NoticeBlock(error.strip() or FAILED_NOTICE, NOTICE_ERROR))
        warning = as_text(payload.get("warning")).strip()
        if warning:
            self._append(NoticeBlock(warning, NOTICE_WARN))
        self.running = False
        self.activity = ""
        self.status = ""

    def _on_thinking_delta(self, payload: dict[str, Any]) -> None:
        self.activity = _first_line(as_text(payload.get("text")))

    def _on_reasoning_delta(self, payload: dict[str, Any]) -> None:
        text = as_text(payload.get("text"))
        if not text:
            return
        block = self._live(ThinkingBlock)
        if block is None:
            block = ThinkingBlock(started_at=self._clock())
            self._append(block)
        block.text += text

    def _on_reasoning_available(self, payload: dict[str, Any]) -> None:
        """Ignored: it relays the assistant's own content, so it would repeat the answer as thought."""

    def _on_tool_generating(self, payload: dict[str, Any]) -> None:
        name = _first_line(as_text(payload.get("name")))
        if name:
            self._tool_group().tools.append(ToolRow(tool_id="", name=name))

    def _on_tool_start(self, payload: dict[str, Any]) -> None:
        tool_id = as_text(payload.get("tool_id"))
        name = _first_line(as_text(payload.get("name"))) or "tool"
        group = self._tool_group()
        row = _find_row(group, tool_id) or _drafted_row(group, name)
        if row is None:
            row = ToolRow(tool_id=tool_id, name=name)
            group.tools.append(row)
        row.tool_id = tool_id
        row.name = name
        row.status = TOOL_RUNNING
        row.started_at = self._clock()
        row.args_summary = self._display_args(_args_summary(payload))
        running = sum(1 for tool in group.tools if tool.status == TOOL_RUNNING)
        group.parallel = max(group.parallel, running)

    def _on_tool_complete(self, payload: dict[str, Any]) -> None:
        tool_id = as_text(payload.get("tool_id"))
        group = self._tool_group()
        row = _find_row(group, tool_id)
        if row is None:
            name = _first_line(as_text(payload.get("name"))) or "tool"
            row = ToolRow(tool_id=tool_id, name=name, args_summary=self._display_args(_args_summary(payload)))
            group.tools.append(row)
        row.status = tool_result_status(payload.get("result"))
        row.duration_s = _duration(payload, row, self._clock())
        row.summary = _result_summary(payload)
        row.inline_diff, row.diff_root = as_text(payload.get("inline_diff")).encode("utf-8"), self.cwd

    def _display_args(self, summary: str) -> str:
        return _relative(self.cwd, summary) if _is_path(summary) else summary

    def _on_status_update(self, payload: dict[str, Any]) -> None:
        text = _first_line(as_text(payload.get("text")))
        if text:
            self.status = text

    # ── output ──

    def take_stable_lines(self, width: int, theme: Theme) -> list[Line]:
        """Lines that will never change, each returned once: print them above the live region."""
        taken: list[Line] = []
        start = (self._cursor, self._cursor_lines)
        if self._left_out:
            earlier, self._left_out = _earlier(self._left_out), 0
            notice = (
                LEFT_OUT_DROPPED_NOTICE.format(earlier=earlier, held=len(self.blocks))
                if self.dropped
                else LEFT_OUT_NOTICE.format(earlier=earlier)
            )
            taken.append(truncate_line((Span(notice, theme.dim),), width))
            self._taken_any = True
            self._version += 1
        while self._cursor < len(self.blocks):
            block = self.blocks[self._cursor]
            if isinstance(block, AssistantBlock):
                taken.extend(self._take_assistant(block, width, theme))
            elif block.done:
                taken.extend(self._spaced(block_rows(block, width, theme), opens_block=True))
            if not block.done:
                break
            self._cursor += 1
            self._cursor_lines = 0
            self._cursor_state = MarkdownState()
        if (self._cursor, self._cursor_lines) != start:
            self._version += 1
        return taken

    def _take_assistant(self, block: AssistantBlock, width: int, theme: Theme) -> list[Line]:
        sources = _display_lines(block.text) if block.done else _stable_lines(block.text)
        fresh = sources[self._cursor_lines :]
        if not fresh:
            return []
        rows, self._cursor_state = render_lines(fresh, self._cursor_state, _reply_width(width), theme)
        opens_block = self._cursor_lines == 0
        self._cursor_lines = len(sources)
        return self._spaced(_marked(rows, opens_block, theme), opens_block)

    def _spaced(self, rows: list[Line], opens_block: bool) -> list[Line]:
        if not rows:
            return []
        spacer: list[Line] = [()] if opens_block and self._taken_any else []
        self._taken_any = True
        return spacer + rows

    def retake(self) -> None:
        """Forget which lines were taken: the next `take_stable_lines` returns the transcript from the top.

        For a full redraw at a new width, after the terminal's copy has been
        erased. Blocks are untouched; only the bookkeeping of what left the live
        region is reset, so every line is rendered again at the new width.
        """
        self._cursor = 0
        self._cursor_lines = 0
        self._cursor_state = MarkdownState()
        self._taken_any = False
        self._left_out = 0
        self._version += 1

    def retake_tail(self, width: int, theme: Theme, max_rows: int) -> None:
        """`retake`, keeping to the session's last `max_rows` rows at `width`: a full redraw costs that, not the session.

        The blocks are measured from the last finished one up, each with the
        blank row above it, and the next take starts at the first block that
        fits, so the cut always falls between blocks (a reply's markdown state
        starts over there, as it does for every reply). The last finished
        block is kept however tall it is, and the live block is never left
        out: it is the live region's. When blocks are left out, the next take
        opens with one row counting them, blocks `BLOCK_CAP` dropped included.
        """
        self.retake()
        end = len(self.blocks) - (1 if self.blocks and not self.blocks[-1].done else 0)
        cut, rows = end, 0
        while cut > 0:
            height = len(block_rows(self.blocks[cut - 1], width, theme)) + 1
            if rows + height > max_rows and cut < end:
                break
            rows += height
            cut -= 1
        self._cursor = cut
        self._left_out = cut + self.dropped

    def live_lines(self, width: int, theme: Theme, max_rows: int | None = None) -> list[Line]:
        """Everything not yet taken, as it shows now: the streaming tail, live thinking, running tools.

        With `max_rows`, only the blocks that reach into the last `max_rows`
        rows are rendered, and of live thinking text only its last rows: the
        rows returned end exactly as the whole tail does, and may start below
        its top. The answer is kept until the
        transcript changes, so asking again with the same width, limit and
        theme renders nothing.
        """
        key = (self._version, width, max_rows)
        held = self._live_held
        if held is None or held[0] != key or held[1] is not theme:
            held = self._live_held = (key, theme, self._render_live(width, theme, max_rows))
        return list(held[2])

    def _render_live(self, width: int, theme: Theme, max_rows: int | None) -> list[Line]:
        """`live_lines` rendered, from the last block up until `max_rows` rows are covered (all of them for None)."""
        rendered: list[tuple[bool, list[Line]]] = []  # (opens a block, its rows), the last block first
        count = 0
        whole = True
        for index in range(len(self.blocks) - 1, self._cursor - 1, -1):
            if max_rows is not None and count >= max_rows:
                whole = False  # the blocks above are above the rows asked for
                break
            block = self.blocks[index]
            opens_block = True
            if index == self._cursor and isinstance(block, AssistantBlock):
                rest = _display_lines(block.text)[self._cursor_lines :]
                opens_block = self._cursor_lines == 0
                block_lines = _marked(render_lines(rest, self._cursor_state, _reply_width(width), theme)[0], opens_block, theme)
            else:  # only the last block can be live thinking, the one kind the limit shortens, so it gets all of it
                block_lines = block_rows(block, width, theme, self.details_expanded and not block.done, max_rows)
            if block_lines:
                rendered.append((opens_block, block_lines))
                count += len(block_lines)
        rows: list[Line] = []
        seen = self._taken_any if whole else True  # a spacer above the first block shown is past the limit
        for opens_block, block_lines in reversed(rendered):
            if opens_block and seen:
                rows.append(())
            rows.extend(block_lines)
            seen = True
        return rows

    def dropped_rows(self, width: int, theme: Theme) -> list[Line]:
        """/history's opening rows once `BLOCK_CAP` has dropped blocks (one counting them, then a blank one); else none."""
        if not self.dropped:
            return []
        return [truncate_line((Span(DROPPED_NOTICE.format(earlier=_earlier(self.dropped)), theme.dim),), width), ()]

    def history_lines(self, width: int, theme: Theme, details: bool = True) -> list[Line]:
        """Every block held at `width`; `details` adds full thinking text, tool result summaries, whole diffs."""
        rows: list[Line] = []
        for block in self.blocks:
            block_lines = block_rows(block, width, theme, expanded=details)
            if block_lines:
                if rows:
                    rows.append(())
                rows.extend(block_lines)
        return rows
