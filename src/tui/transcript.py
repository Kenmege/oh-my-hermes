"""The transcript of one OMH TUI session, and the rule for when its lines may leave the live region.

Inline scrollback (plan §4.2): a line printed above the live region lives in
the terminal's own scrollback and is never repainted; only the live region
repaints. So the model answers three questions for the app loop:

- `take_stable_lines`: lines that will never change again. Each is returned
  once and then belongs to the terminal.
- `live_lines`: everything not yet taken, as it looks now (painted inside the
  live region every frame).
- `history_lines`: the whole transcript, for the in-memory pager.

The invariant the tests hold: at any moment, the lines already taken followed
by `live_lines()` equal `history_lines(details=False)` at the same width
(unless `toggle_details` expanded the live block). Only the LAST block can
still change; appending any block first closes the one before it, so a block
is flushed only after every block above it.

Flush policy: a user or notice block flushes at once; assistant text flushes
per completed source line (a trailing blank line waits for the text after it,
so a reply that ends there leaves nothing to take back) and fully at
`message.complete`; the markdown state (an open fence) carries across the
flush boundary; thinking flushes as ONE collapsed row ("▸ thought for 4.2s");
a tool group flushes when the next block arrives (message text, thinking) or
at `message.complete`, as "⏺ Tool calls (N)" (plus "parallel ×K" when K tools
ran at once) and one line per tool. An edit tool whose `tool.complete` carries
an `inline_diff` shows that diff under its row (`diff_view.py`): the first
`PREVIEW_LINES` diff lines and a count of the rest, which `history_lines` with
details shows in full. Lines rendered at one width stay as they were taken; a
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

from .diff_view import DiffLine, diff_rows, parse_inline_diff
from .gateway import Event
from .markdown import MarkdownState, clean_text, render_lines, wrap_prefixed
from .style import Line, Span, Style
from .theme import Theme
from .width import line_width, truncate, truncate_line

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

USER_MARK = "› "
COLLAPSED_MARK = "▸"
EXPANDED_MARK = "▾"
TOOL_GROUP_MARK = "⏺ "
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
    diff: tuple[DiffLine, ...] = ()  # an edit's inline diff, colour stripped and classified


@dataclass
class ToolGroup:
    tools: list[ToolRow] = field(default_factory=list)
    done: bool = False
    parallel: int = 1  # most tools running at the same moment


Block = UserBlock | AssistantBlock | ThinkingBlock | ToolGroup | NoticeBlock


@dataclass(frozen=True)
class TurnStatus:
    kind: str
    text: str


# ── payload reading ───────────────────────────────────────────────────────


def _str(value: object) -> str:
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
    summary = _first_line(_str(payload.get("summary")))
    if summary:
        return summary
    result = payload.get("result")
    if isinstance(result, dict):
        for key in ("user_summary", "error", "output"):
            line = _first_line(_str(result.get(key)))
            if line:
                return line
        return ""
    return _first_line(_str(result))


def _args_summary(payload: dict[str, Any]) -> str:
    context = _first_line(_str(payload.get("context")))
    if context:
        return context
    args = payload.get("args")
    if isinstance(args, dict):
        for value in args.values():
            line = _first_line(_str(value))
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


def _text_rows(text: str, style: Style, first: Line, rest: Line, width: int) -> list[Line]:
    """Plain text (no markdown), each source line wrapped, `first` before the very first row."""
    rows: list[Line] = []
    lines = _without_trailing_blanks(_source_lines(text))
    for source in lines:
        if _is_blank(source):
            rows.append(())  # never the first row: leading blank lines were dropped
            continue
        prefix = first if not rows else rest
        rows.extend(wrap_prefixed((Span(clean_text(source), style),), prefix, rest, width))
    return rows


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


def _assistant_rows(block: AssistantBlock, width: int, theme: Theme) -> list[Line]:
    return render_lines(_display_lines(block.text), MarkdownState(), width, theme)[0]


def _seconds(value: float) -> str:
    return f"{value:.1f}s"


def _thinking_rows(block: ThinkingBlock, width: int, theme: Theme, expanded: bool) -> list[Line]:
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
        rows.extend(_text_rows(block.text, theme.thinking, indent, indent, width))
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


def _tool_row(tool: ToolRow, width: int, theme: Theme) -> Line:
    """One line per tool: mark, name, args summary (cut first), duration."""
    head: Line = (Span(TOOL_ROW_INDENT), _tool_mark(tool.status, theme), Span(" "), Span(tool.name, theme.tool))
    tail: Line = ()
    if tool.duration_s is not None:
        tail = (Span(COLUMN_GAP + _seconds(tool.duration_s), theme.dim),)
    room = width - line_width(head) - line_width(tail) - len(COLUMN_GAP)
    args: Line = ()
    if tool.args_summary and room > 1:
        args = (Span(COLUMN_GAP + truncate(tool.args_summary, room), theme.dim),)
    return truncate_line((*head, *args, *tail), width)


def _tool_rows(group: ToolGroup, width: int, theme: Theme, expanded: bool) -> list[Line]:
    header = [Span(TOOL_GROUP_MARK, theme.tool), Span(f"Tool calls ({len(group.tools)})", theme.label)]
    if group.parallel > 1:
        header.append(Span(f"{COLUMN_GAP}parallel ×{group.parallel}", theme.accent))
    rows = [truncate_line(tuple(header), width)]
    summary_first: Line = (Span(TOOL_ROW_INDENT * 2 + TOOL_SUMMARY_MARK, theme.dim),)
    summary_rest: Line = (Span(" " * (len(TOOL_ROW_INDENT) * 2 + len(TOOL_SUMMARY_MARK))),)
    for tool in group.tools:
        rows.append(_tool_row(tool, width, theme))
        if expanded and tool.summary:
            rows.extend(wrap_prefixed((Span(tool.summary, theme.dim),), summary_first, summary_rest, width))
        if tool.diff:
            rows.extend(diff_rows(tool.diff, width, theme, full=expanded))
    return rows


def block_rows(block: Block, width: int, theme: Theme, expanded: bool = False) -> list[Line]:
    """The rows one block shows at `width`; `expanded` adds thinking text, tool result summaries, whole diffs."""
    width = max(1, width)
    if isinstance(block, UserBlock):
        return _user_rows(block, width, theme)
    if isinstance(block, NoticeBlock):
        return _notice_rows(block, width, theme)
    if isinstance(block, AssistantBlock):
        return _assistant_rows(block, width, theme)
    if isinstance(block, ThinkingBlock):
        return _thinking_rows(block, width, theme, expanded)
    return _tool_rows(block, width, theme, expanded)


# ── the model ─────────────────────────────────────────────────────────────


class Transcript:
    """Blocks in order, the turn's transient state, and how far the terminal has been handed lines."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.blocks: list[Block] = []
        self.activity = ""  # spinner / wait text from thinking.delta; "" when none
        self.status: TurnStatus | None = None  # the last status.update of the running turn
        self.running = False
        self.details_expanded = False
        self._clock = clock
        self._turn_start = 0
        self._cursor = 0  # first block not fully taken
        self._cursor_lines = 0  # source lines of blocks[_cursor] already taken (assistant text)
        self._cursor_state = MarkdownState()  # markdown state after those lines
        self._taken_any = False  # whether any line has been taken (a spacer goes above the next block)
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

    def add_user(self, text: str) -> None:
        self._append(UserBlock(text))

    def add_notice(self, text: str, level: str = NOTICE_INFO) -> None:
        self._append(NoticeBlock(text, level))

    def add_assistant(self, text: str) -> None:
        """A finished reply (a resumed session's history), rendered like a streamed one."""
        if text.strip():
            self._append(AssistantBlock(text, done=True))

    def toggle_details(self) -> bool:
        """Expand or collapse the live block's details; blocks already taken never repaint."""
        self.details_expanded = not self.details_expanded
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
        text = _str(payload.get("text"))
        if not text:
            return
        block = self._live(AssistantBlock)
        if block is None:
            block = AssistantBlock()
            self._append(block)
        block.text += text

    def _on_message_interim(self, payload: dict[str, Any]) -> None:
        """Commentary the model wrote alongside tool calls: seal it as its own block."""
        text = _str(payload.get("text"))
        block = self._live(AssistantBlock)
        if block is not None:
            if not payload.get("already_streamed"):
                self._settle_text(block, text)
            block.done = True
            return
        if text.strip() and not self._repeats_turn_answer(text):
            self._append(AssistantBlock(text, done=True))

    def _on_message_complete(self, payload: dict[str, Any]) -> None:
        text = _str(payload.get("text"))
        status = _str(payload.get("status")) or "complete"
        error = _str(payload.get("error"))
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
        warning = _str(payload.get("warning")).strip()
        if warning:
            self._append(NoticeBlock(warning, NOTICE_WARN))
        self.running = False
        self.activity = ""
        self.status = None

    def _on_thinking_delta(self, payload: dict[str, Any]) -> None:
        self.activity = _first_line(_str(payload.get("text")))

    def _on_reasoning_delta(self, payload: dict[str, Any]) -> None:
        text = _str(payload.get("text"))
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
        name = _first_line(_str(payload.get("name")))
        if name:
            self._tool_group().tools.append(ToolRow(tool_id="", name=name))

    def _on_tool_start(self, payload: dict[str, Any]) -> None:
        tool_id = _str(payload.get("tool_id"))
        name = _first_line(_str(payload.get("name"))) or "tool"
        group = self._tool_group()
        row = _find_row(group, tool_id) or _drafted_row(group, name)
        if row is None:
            row = ToolRow(tool_id=tool_id, name=name)
            group.tools.append(row)
        row.tool_id = tool_id
        row.name = name
        row.status = TOOL_RUNNING
        row.started_at = self._clock()
        row.args_summary = _args_summary(payload)
        running = sum(1 for tool in group.tools if tool.status == TOOL_RUNNING)
        group.parallel = max(group.parallel, running)

    def _on_tool_complete(self, payload: dict[str, Any]) -> None:
        tool_id = _str(payload.get("tool_id"))
        group = self._tool_group()
        row = _find_row(group, tool_id)
        if row is None:
            name = _first_line(_str(payload.get("name"))) or "tool"
            row = ToolRow(tool_id=tool_id, name=name, args_summary=_args_summary(payload))
            group.tools.append(row)
        row.status = tool_result_status(payload.get("result"))
        row.duration_s = _duration(payload, row, self._clock())
        row.summary = _result_summary(payload)
        row.diff = parse_inline_diff(_str(payload.get("inline_diff")))

    def _on_status_update(self, payload: dict[str, Any]) -> None:
        text = _first_line(_str(payload.get("text")))
        if text:
            self.status = TurnStatus(_str(payload.get("kind")) or "status", text)

    # ── output ──

    def take_stable_lines(self, width: int, theme: Theme) -> list[Line]:
        """Lines that will never change, each returned once: print them above the live region."""
        taken: list[Line] = []
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
        return taken

    def _take_assistant(self, block: AssistantBlock, width: int, theme: Theme) -> list[Line]:
        sources = _display_lines(block.text) if block.done else _stable_lines(block.text)
        fresh = sources[self._cursor_lines :]
        if not fresh:
            return []
        rows, self._cursor_state = render_lines(fresh, self._cursor_state, max(1, width), theme)
        opens_block = self._cursor_lines == 0
        self._cursor_lines = len(sources)
        return self._spaced(rows, opens_block)

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

    def live_lines(self, width: int, theme: Theme) -> list[Line]:
        """Everything not yet taken, as it shows now: the streaming tail, live thinking, running tools."""
        rows: list[Line] = []
        seen = self._taken_any
        for index in range(self._cursor, len(self.blocks)):
            block = self.blocks[index]
            opens_block = True
            if index == self._cursor and isinstance(block, AssistantBlock):
                rest = _display_lines(block.text)[self._cursor_lines :]
                block_lines = render_lines(rest, self._cursor_state, max(1, width), theme)[0]
                opens_block = self._cursor_lines == 0
            else:
                block_lines = block_rows(block, width, theme, expanded=self.details_expanded and not block.done)
            if block_lines:
                if opens_block and seen:
                    rows.append(())
                rows.extend(block_lines)
                seen = True
        return rows

    def history_lines(self, width: int, theme: Theme, details: bool = True) -> list[Line]:
        """The whole transcript at `width`; `details` adds full thinking text, tool result summaries, whole diffs."""
        rows: list[Line] = []
        for block in self.blocks:
            block_lines = block_rows(block, width, theme, expanded=details)
            if block_lines:
                if rows:
                    rows.append(())
                rows.extend(block_lines)
        return rows
