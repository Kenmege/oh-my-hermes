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

A slash command the person runs is a block of its own: `› /name arg` in the
hint colour, then what it printed under `⎿`, each part in its level's colour
and, for a warning or an error, its glyph. Its echo stays live while the
command may still print (`close_command` ends that); what it says meanwhile
is its output (`command` names the command speaking). Typed while a block
is live (a running tool, a streaming reply), the echo is held under that
block, as a steer is, and written at the next boundary once the command is
over. A long output shows its first `OUTPUT_PREVIEW_ROWS` rows and a count
of the rest, which names Ctrl+O (`/history` shows it too).

Flush policy: a user or notice block flushes at once; assistant text flushes
per completed source line (a trailing blank line waits for the text after it,
so a reply that ends there leaves nothing to take back) and fully at
`message.complete`; the markdown state (an open fence) carries across the
flush boundary; thinking flushes as ONE collapsed row ("▸ thought for 4.2s");
a tool group flushes when the next block arrives (message text, thinking) or
at `message.complete`, as one row per call (its mark, name and argument
summary) with what it printed under it, no header over them. A
`tool.generating` row (`◌`) is a draft: a `tool.start` takes over the first
unclaimed draft of its name, or else the first unclaimed draft of the
`tool_call` bridge (`gateway_contract.TOOL_CALL_BRIDGE`), whose call starts
under the name of the tool it reached. A draft no start took is dropped when
its group closes, so it is never written or counted, and a group left with no
calls shows nothing. An edit tool whose `tool.complete` carries
an `inline_diff` shows that diff under its row (`diff_view.py`): the first
`PREVIEW_LINES` diff lines and a count of the rest, which Ctrl+O's details
view (and `history_lines` with details) shows in full. Lines rendered at one width stay as they were taken; a
resize changes only what has not been taken yet.

Two events do not mean what their names suggest, measured against Hermes
6c61c3e27d (the recorded fixtures and the gateway source):

- `thinking.delta` is the agent's spinner and wait text ("(⌐■_■) analyzing...",
  "" clears it; `tui_gateway/agent_callbacks.py::_agent_thinking_update` says
  "They are not reasoning blocks"). It sets `activity`, never a block.
- `reasoning.available` relays the assistant's own content, cut to 500 chars
  (`agent/turn_response_intake.py::_relay_thinking`); in every recorded turn it
  repeats the answer. It is ignored. Real reasoning arrives as `reasoning.delta`.

A MoA turn (`moa.*`) asks reference models before the aggregator answers.
Each `moa.reference` is a block of its own, a thinking row labelled
`◇ Reference 1/3 — <model>` whose text the details view shows, as Hermes' Ink
TUI keeps it; `moa.progress` and an aggregator `moa.phase` set `moa`, the
words the working row adds (`MoA: refs 2/3`, `MoA: aggregating…`), until the
turn ends.

Nothing here is written to disk.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import time
from typing import Any

from ..plugin_bundle.omh.hud_text import elapsed_text
from .diff_view import DIFF_FILE, FILE_ARROW, DiffLine, diff_rows, parse_inline_diff
from .gateway import Event
from . import gateway_contract as contract
from .gateway_contract import TOOL_CALL_BRIDGE
from .markdown import MarkdownState, clean_text, render_lines, wrap_prefixed
from .style import Line, Span, Style, fill
from .theme import Theme, on_band
from .width import line_width, truncate, truncate_left, truncate_line

TOOL_GENERATING = "generating"
TOOL_RUNNING = "running"
TOOL_OK = "ok"
TOOL_ERROR = "error"
TOOL_DENIED = "denied"
TOOL_INTERRUPTED = "interrupted"  # still open when its group closed: it never reported a result

NOTICE_INFO = "info"
NOTICE_OK = "ok"  # something the person allowed went ahead
NOTICE_WARN = "warn"
NOTICE_ERROR = "error"
# What a command that failed printed: on the red band in the error colour, as a failed tool's output lines are,
# with no mark of its own (the command's `✗` line under it has the one mark).
OUTPUT_FAILED = "failed"

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
FROM_QUEUE_TAG = "⧗ from queue"  # on a prompt the queue sent once the turn before it ended
# A steer the person sent: SENT until Hermes answers, then QUEUED (Hermes took it: it reads it after its next
# tool call, or sends it as the next prompt while compressing, and says the same for both) or REFUSED.
STEER_MARK = "↪ "
STEER_SENT = "sent"
STEER_QUEUED = "queued"
STEER_REFUSED = "refused"
STEER_REFUSED_MARK = "⧗ "
COMMAND_MARK = "› "  # a command the person ran, echoed in the hint colour above what it printed
OUTPUT_MARK = "⎿ "
OUTPUT_INDENT = "  "
# The rows a command's output shows under its echo; past them one row counts the rest (rows at that width, so the
# count changes with it), which Ctrl+O's details view shows.
OUTPUT_PREVIEW_ROWS = 12
OUTPUT_MORE = "… +{count} {noun} · ctrl+o shows all"
# What a tool's output or a diff cut short says after its count: the key whose view shows the rest.
SHOWS_ALL = " · ctrl+o shows all"
COLLAPSED_MARK = "▸"
EXPANDED_MARK = "▾"
ASSISTANT_MARK = "⏺ "
ASSISTANT_INDENT = "  "
TOOL_SUMMARY_MARK = "⎿ "
# A running tool's mark alternates the accent and dim this often, counted from its own start, so calls that run
# side by side pulse each on its own. Only the live region is drawn with a clock; scrollback gets the settled mark.
PULSE_SECONDS = 0.5
TOOL_ROW_INDENT = "  "
DETAIL_INDENT = "  "
COLUMN_GAP = "  "


@dataclass
class UserBlock:
    text: str
    done: bool = True
    from_queue: bool = False


@dataclass
class SteerBlock:
    """A steer the person sent (`text`) and what came of it (`state`; for STEER_REFUSED, `outcome` says what).

    `agent` names the subagent it was for; "" is the main turn.
    """

    text: str
    state: str = STEER_SENT
    outcome: str = ""
    agent: str = ""
    done: bool = True


@dataclass
class NoticeBlock:
    text: str
    level: str = NOTICE_INFO
    done: bool = True


@dataclass
class CommandBlock:
    """A command the person ran (`command`, as typed), and what it printed under it, each part with its level.

    Open (`done` False) while the command may still print; the app closes it
    once the command's calls are answered, and appending any other block
    closes it too. `whole` keeps the output uncut.
    """

    command: str
    parts: list[tuple[str, str]] = field(default_factory=list)
    whole: bool = False
    done: bool = False


@dataclass
class AssistantBlock:
    text: str = ""
    done: bool = False


@dataclass
class TurnSummaryBlock:
    """The line that closes a turn: how long it took, its tool calls, and what it wrote (None when unknown)."""

    seconds: float
    tools: int
    output_tokens: int | None = None
    done: bool = True


@dataclass
class ThinkingBlock:
    text: str = ""
    done: bool = False
    started_at: float | None = None
    ended_at: float | None = None
    label: str = ""  # a MoA reference's header, shown in place of "thought for"; "" for the model's own reasoning


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
    # `surrogatepass` both ways: JSON can carry a lone surrogate, which plain UTF-8 refuses to encode.
    inline_diff: bytes = b""
    diff_root: str = ""  # the session's working directory, which the diff's file names are shown relative to
    # The parsed diff, kept while the row's group is live (it is drawn again on every change); None otherwise.
    held_lines: tuple[DiffLine, ...] | None = field(default=None, repr=False, compare=False)
    # The first lines of what the tool printed (`TOOL_OUTPUT_KEEP` at most), and how many more it printed; for a
    # failed tool, its error's first line.
    output: tuple[str, ...] = ()
    output_more: int = 0

    def diff_lines(self, hold: bool = False) -> tuple[DiffLine, ...]:
        """The diff colour-stripped and classified, its file names relative to `diff_root`.

        With `hold` the lines are kept for the next call that holds; without,
        any kept lines are let go and the diff is parsed from its bytes.
        """
        if hold and self.held_lines is not None:
            return self.held_lines
        lines = tuple(
            DiffLine(line.kind, FILE_ARROW.join(_relative(self.diff_root, side) for side in line.text.split(FILE_ARROW)))
            if line.kind == DIFF_FILE
            else line
            for line in parse_inline_diff(self.inline_diff.decode("utf-8", "surrogatepass"))
        )
        self.held_lines = lines if hold else None
        return lines


@dataclass
class ToolGroup:
    tools: list[ToolRow] = field(default_factory=list)
    done: bool = False


Block = UserBlock | AssistantBlock | ThinkingBlock | ToolGroup | NoticeBlock | CommandBlock | TurnSummaryBlock | SteerBlock


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


def compact_count(value: float) -> str:
    """`950`, `1.2k`, `3M`: a count in at most four characters and a unit."""
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= size:
            return f"{value / size:.1f}".removesuffix(".0") + suffix
    return str(int(value))


def _earlier(count: int) -> str:
    return f"{count} earlier {'message' if count == 1 else 'messages'}"


# Where a tool's printed text is in its result, in order; a failed tool's error first.
_OUTPUT_KEYS = ("output", "stdout", "content", "text")
_ERROR_KEYS = ("error", "stderr", "user_summary", "output")
# A denied call says so in the person's words first; `error` there is what Hermes told the model.
_DENIED_KEYS = ("user_summary", "error", "output")
DENIED_LINE = "denied"  # a denied call whose result says nothing more
TOOL_OUTPUT_ROWS = 3  # shown under a tool row; the details view shows up to TOOL_OUTPUT_KEEP
TOOL_OUTPUT_KEEP = 40


def _tool_output(result: object, status: str) -> tuple[tuple[str, ...], int]:
    """(lines, how many more) of what a tool printed: its first lines; for a failed or denied tool, one line, its
    reason's first.

    A failed tool whose result names no reason, only what it printed (Hermes' terminal tool reports a non-zero
    exit that way: `output` and `exit_code`, `error` None), gives the end of that instead, blank lines left out:
    a test run's summary is its last lines. `_output_rows` shows a failed tool's lines from their end.
    """
    if isinstance(result, str):
        found = [("text", result)]
    elif isinstance(result, dict):
        keys = _ERROR_KEYS if status == TOOL_ERROR else _DENIED_KEYS if status == TOOL_DENIED else _OUTPUT_KEYS
        found = [(key, as_text(result.get(key))) for key in keys]
    else:
        return (), 0
    key, text = next(((key, candidate) for key, candidate in found if candidate.strip()), ("", ""))
    lines = [clean_text(line).rstrip() for line in text.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if status == TOOL_ERROR and key == "output":
        printed = [line for line in lines if line.strip()]
        return tuple(printed[-TOOL_OUTPUT_KEEP:]), max(len(printed) - TOOL_OUTPUT_KEEP, 0)
    if status in (TOOL_ERROR, TOOL_DENIED):
        return tuple(lines[:1]), 0
    return tuple(lines[:TOOL_OUTPUT_KEEP]), max(len(lines) - TOOL_OUTPUT_KEEP, 0)


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
    """The first `tool.generating` row for `name` that no `tool.start` has claimed yet, else the first unclaimed
    draft of Hermes' `tool_call` bridge, whose call starts under the name of the tool it reached."""
    drafts = [row for row in group.tools if row.status == TOOL_GENERATING and not row.tool_id]
    named = next((row for row in drafts if row.name == name), None)
    return named or next((row for row in drafts if row.name == TOOL_CALL_BRIDGE), None)


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
    """Your prompt behind `›` on its band, which every row fills to the right edge; `⧗ from queue` when the queue sent it."""
    rows = _text_rows(block.text, theme.text, (Span(USER_MARK, theme.user),), (Span(" " * len(USER_MARK)),), width)
    if block.from_queue and rows:
        roles = on_band(theme, theme.user_band) if theme.user_band.bg is not None else theme
        tag: Line = (Span(COLUMN_GAP + FROM_QUEUE_TAG, roles.muted),)  # read against the band it sits on
        if line_width(rows[0]) + line_width(tag) <= width:
            rows[0] = (*rows[0], *tag)
    band = theme.user_band
    return [(*(Span(span.text, replace(span.style, bg=band.bg)) for span in row), fill(band)) for row in rows]


def _awaits_answer(block: Block) -> bool:
    """A held block that is not finished yet: a steer Hermes has not answered, a command still printing."""
    if isinstance(block, CommandBlock):
        return not block.done
    return isinstance(block, SteerBlock) and block.state == STEER_SENT


def _steer_rows(block: SteerBlock, width: int, theme: Theme) -> list[Line]:
    """`↪ steer · <text>` while it waits for Hermes, `↪ steer queued by Hermes · <text>` once taken, or the refusal.

    A subagent's steer names it before the text: `<agent>: <text>`.
    """
    text = _first_line(block.text)
    if block.agent:
        text = f"{block.agent}: {text}"
    if block.state == STEER_REFUSED:
        return [truncate_line((Span(f"{STEER_REFUSED_MARK}{block.outcome} · {text}", theme.warn),), width)]
    words = "steer queued by Hermes · " if block.state == STEER_QUEUED else "steer · "
    return [truncate_line((Span(STEER_MARK, theme.accent), Span(words, theme.muted), Span(text, theme.text)), width)]


def _notice_style(level: str, theme: Theme) -> tuple[str, Style]:
    if level == NOTICE_OK:
        return "✓ ", theme.ok
    if level == NOTICE_ERROR:
        return "✗ ", theme.error
    if level == NOTICE_WARN:
        return "⚠ ", theme.warn
    return "· ", theme.muted


def _banded(rows: list[Line], band: Style) -> list[Line]:
    """`rows` on `band`: every span on its background and an erase to the right edge, so a copy has no trailing spaces."""
    return [(*(Span(span.text, replace(span.style, bg=band.bg)) for span in row), fill(band)) for row in rows]


def _outcome_band(level: str, theme: Theme) -> Style | None:
    """The band behind a row of `level`: green for what the person allowed, red for an error; None for the rest,
    or when the theme paints no band (no background was reported)."""
    band = {NOTICE_OK: theme.ok_band, NOTICE_ERROR: theme.error_band, OUTPUT_FAILED: theme.error_band}.get(level)
    return band if band is not None and band.bg is not None else None


def _notice_rows(block: NoticeBlock, width: int, theme: Theme) -> list[Line]:
    band = _outcome_band(block.level, theme)
    roles = on_band(theme, band) if band is not None else theme
    mark, style = _notice_style(block.level, roles)
    rows = _text_rows(block.text, style, (Span(mark, style),), (Span(" " * len(mark)),), width)
    return _banded(rows, band) if band is not None else rows


TURN_DONE_MARK = "✻ "
TURN_RULE = "─"


def _summary_rows(block: TurnSummaryBlock, width: int, theme: Theme) -> list[Line]:
    """`✻ 34s · 3 tools · ↓1.2k  ────`: the turn's facts on the thin rule that closes it (no tool count for none)."""
    facts = [elapsed_text(block.seconds)]
    if block.tools:
        facts.append(f"{block.tools} tool{'' if block.tools == 1 else 's'}")
    if block.output_tokens:
        facts.append(f"↓{compact_count(block.output_tokens)}")
    done: Line = (Span(TURN_DONE_MARK, theme.dim), Span(" · ".join(facts), theme.muted))
    rule = width - line_width(done) - len(COLUMN_GAP)
    return [truncate_line((*done, Span(COLUMN_GAP + TURN_RULE * rule, theme.rule)) if rule > 0 else done, width)]


_OUTPUT_GLYPHS = {NOTICE_WARN: "⚠ ", NOTICE_ERROR: "✗ "}


def _command_rows(block: CommandBlock, width: int, theme: Theme, expanded: bool) -> list[Line]:
    """`› /name arg`, then each part of its output under `⎿` in its level's colour (and glyph), cut unless `expanded`."""
    rows = wrap_prefixed((Span(block.command, theme.hint),), (Span(COMMAND_MARK, theme.dim),), (Span("  "),), width)
    rest: Line = (Span(OUTPUT_INDENT + " " * len(OUTPUT_MARK)),)
    output: list[Line] = []
    for text, level in block.parts:
        band = _outcome_band(level, theme)
        roles = on_band(theme, band) if band is not None else theme
        style = {NOTICE_WARN: roles.warn, NOTICE_ERROR: roles.error, OUTPUT_FAILED: roles.error}.get(level, roles.text)
        lead = rest if output else (Span(OUTPUT_INDENT), Span(OUTPUT_MARK, roles.dim))
        part = _text_rows(_OUTPUT_GLYPHS.get(level, "") + text, style, lead, rest, width)
        output.extend(_banded(part, band) if band is not None else part)
    if not expanded and not block.whole and len(output) > OUTPUT_PREVIEW_ROWS:
        hidden = len(output) - OUTPUT_PREVIEW_ROWS
        more = OUTPUT_MORE.format(count=hidden, noun="row" if hidden == 1 else "rows")  # rows at this width
        output = [*output[:OUTPUT_PREVIEW_ROWS], truncate_line((*rest, Span(more, theme.muted)), width)]
    return rows + output


def _marked(rows: list[Line], opens_block: bool, theme: Theme) -> list[Line]:
    """Assistant rows behind the reply's margin: `⏺ ` on the block's first row, an indent on every other one."""
    first: Line = (Span(ASSISTANT_MARK, theme.text),)  # what it said; what it did is in the tool colour
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
    if block.label:
        head: Line = (Span(f"{mark} {block.label}", theme.thinking),)
    elif block.done:
        header = f"{mark} thought"
        if block.started_at is not None and block.ended_at is not None:
            header += f" for {_seconds(max(0.0, block.ended_at - block.started_at))}"
        head = (Span(header, theme.thinking),)
    elif expanded:
        head = (Span(f"{mark} thinking…", theme.thinking),)
    else:
        head = (Span(f"{mark} thinking… ", theme.thinking), Span(_last_line(block.text), theme.muted))
    rows = [truncate_line(head, width)]
    if expanded:
        indent: Line = (Span(DETAIL_INDENT),)
        rows.extend(_text_rows(block.text, theme.thinking, indent, indent, width, max_rows))
    return rows


def _pulse_dim(tool: ToolRow, now: float | None) -> bool:
    """Whether a running tool's mark is in its dim half at `now`; never without a clock (scrollback, reduced motion)."""
    if now is None or tool.started_at is None:
        return False
    return int(max(0.0, now - tool.started_at) / PULSE_SECONDS) % 2 == 1


def _tool_mark(status: str, theme: Theme, dim: bool = False) -> Span:
    if status == TOOL_OK:
        return Span("✓", theme.ok)
    if status == TOOL_ERROR:
        return Span("✗", theme.error)
    if status == TOOL_DENIED:
        return Span("✗", theme.error)  # one mark for a denial, as its approval row has; its line says it was denied
    if status == TOOL_RUNNING:
        return Span("⏺", theme.dim if dim else theme.accent)
    if status == TOOL_INTERRUPTED:
        return Span("▪", theme.dim)
    return Span("◌", theme.dim)


def _is_path(text: str) -> bool:
    """A single path argument (`/abs/x.py`, `~/x`, `./x`): its end names the file, so it is cut from the left."""
    return " " not in text and text.startswith(("/", "~/", "./", "../"))


def _tool_row(tool: ToolRow, width: int, theme: Theme, now: float | None = None) -> Line:
    """One line per tool: mark (pulsing while it runs, given `now`), name, args summary (cut first), duration."""
    mark = _tool_mark(tool.status, theme, tool.status == TOOL_RUNNING and _pulse_dim(tool, now))
    head: Line = (Span(TOOL_ROW_INDENT), mark, Span(" "), Span(tool.name, theme.tool))
    tail: Line = ()
    if tool.duration_s is not None:
        tail = (Span(COLUMN_GAP + _seconds(tool.duration_s), theme.muted),)
    room = width - line_width(head) - line_width(tail) - len(COLUMN_GAP)
    args: Line = ()
    if tool.args_summary and room > 1:
        cut = truncate_left if _is_path(tool.args_summary) else truncate
        args = (Span(COLUMN_GAP + cut(tool.args_summary, room), theme.muted),)
    return truncate_line((*head, *args, *tail), width)


def _output_rows(tool: ToolRow, width: int, theme: Theme, expanded: bool) -> list[Line]:
    """The tool's printed lines under its name, each cut to the width: three of them, or all kept with `expanded`.

    A failed tool's are its last ones, with the count of the rest above them
    (`_tool_output`). A denied call whose result gave no reason says
    `denied`, so a denial reads apart from a failure without colour.
    """
    output = tool.output or ((DENIED_LINE,) if tool.status == TOOL_DENIED else ())
    if not output:
        return []
    failed = tool.status in (TOOL_ERROR, TOOL_DENIED)
    shown = output if expanded else output[-TOOL_OUTPUT_ROWS:] if failed else output[:TOOL_OUTPUT_ROWS]
    hidden = len(output) - len(shown) + tool.output_more
    style = theme.error if failed else theme.muted
    first: Line = (Span(TOOL_ROW_INDENT * 2), Span(TOOL_SUMMARY_MARK, theme.dim))
    rest: Line = (Span(TOOL_ROW_INDENT * 2 + " " * len(TOOL_SUMMARY_MARK)),)
    texts = [Span(line, style) for line in shown]
    more = [Span(f"… +{hidden} line{'' if hidden == 1 else 's'}{SHOWS_ALL}", theme.muted)] if hidden else []
    ordered = [*more, *texts] if failed else [*texts, *more]
    return [truncate_line((*(rest if index else first), span), width) for index, span in enumerate(ordered)]


def _tool_rows(group: ToolGroup, width: int, theme: Theme, expanded: bool, now: float | None = None) -> list[Line]:
    """Each call's own row (what ran, on what) and what it printed under it; a group with no call shows nothing."""
    rows: list[Line] = []
    summary_rest: Line = (Span(" " * (len(TOOL_ROW_INDENT) * 2 + len(TOOL_SUMMARY_MARK))),)
    band = theme.error_band if theme.error_band.bg is not None else None
    for tool in group.tools:
        failed = band is not None and tool.status in (TOOL_ERROR, TOOL_DENIED)  # a settled mark: nothing pulses on a band
        roles = on_band(theme, band) if failed else theme
        own = [_tool_row(tool, width, roles, now)]
        if expanded and tool.summary and tool.summary not in tool.output[:1]:  # a failure's line is said once
            summary_first: Line = (Span(TOOL_ROW_INDENT * 2 + TOOL_SUMMARY_MARK, roles.dim),)  # read against its band
            own.extend(wrap_prefixed((Span(tool.summary, roles.muted),), summary_first, summary_rest, width))
        lines = tool.diff_lines(hold=not group.done) if tool.inline_diff else ()
        if not lines:
            own.extend(_output_rows(tool, width, roles, expanded))
        rows.extend(_banded(own, band) if failed and band is not None else own)
        if lines:
            rows.extend(diff_rows(lines, width, theme, full=expanded))  # a diff keeps its own bands
    return rows


def block_rows(
    block: Block, width: int, theme: Theme, expanded: bool = False, max_rows: int | None = None, now: float | None = None
) -> list[Line]:
    """The rows one block shows at `width`; `expanded` adds thinking text, tool result summaries, whole diffs.

    `max_rows` lets a block whose rows end the same either way (thinking
    text) render only its last rows; the rows returned may start lower.
    `now` pulses running tools' marks; without it every mark is settled.
    """
    width = max(1, width)
    if isinstance(block, UserBlock):
        return _user_rows(block, width, theme)
    if isinstance(block, NoticeBlock):
        return _notice_rows(block, width, theme)
    if isinstance(block, CommandBlock):
        return _command_rows(block, width, theme, expanded)
    if isinstance(block, TurnSummaryBlock):
        return _summary_rows(block, width, theme)
    if isinstance(block, SteerBlock):
        return _steer_rows(block, width, theme)
    if isinstance(block, AssistantBlock):
        return _assistant_rows(block, width, theme)
    if isinstance(block, ThinkingBlock):
        return _thinking_rows(block, width, theme, expanded, max_rows)
    return _tool_rows(block, width, theme, expanded, now)


# ── the model ─────────────────────────────────────────────────────────────


class Transcript:
    """Blocks in order, the turn's transient state, and how far the terminal has been handed lines."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.cwd = ""  # the session's working directory; paths inside it are shown relative to it
        self.blocks: list[Block] = []
        self.activity = ""  # spinner / wait text from thinking.delta; "" when none
        self.status = ""  # the last status.update text of the running turn; "" when none
        self.moa = ""  # a MoA turn's progress (`MoA: refs 2/3`), added to the working row; "" when none
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
        # Where the last take's lines hold the first row of the newest prompt in them (not the blank row above it);
        # None when that take held no prompt.
        self.prompt_row: int | None = None
        # Blocks about a live one (a steer, an answer to Hermes, a command's echo), waiting for a block boundary
        # (`add_steer`, `add_command`).
        self._held: list[Block] = []
        # The command whose output is being written: a notice or an output lands under its echo while it is open.
        # `add_command` sets it; the app points it at the command whose handler or call answer is running.
        self.command: CommandBlock | None = None
        # Moves with every change to what `live_lines` draws; its last answer is kept with the version, width,
        # row limit and theme it was for, so a frame that changed nothing (a spinner tick) renders nothing.
        self._version = 0
        self._live_held: tuple[tuple[int, int, int | None, tuple[bool, ...]], Theme, list[Line]] | None = None
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
            "moa.reference": self._on_moa_reference,
            "moa.progress": self._on_moa_progress,
            "moa.phase": self._on_moa_phase,
        }

    # ── input ──

    def apply(self, event: Event) -> None:
        """Fold one gateway event into the transcript; other event types are not the transcript's."""
        handler = self._handlers.get(event.type)
        if handler is not None:
            handler(event.payload if isinstance(event.payload, dict) else {})
            self._version += 1

    def add_user(self, text: str, from_queue: bool = False) -> None:
        self._append(UserBlock(text, from_queue=from_queue))

    def add_steer(self, text: str, agent: str = "") -> SteerBlock:
        """A steer the person just sent, held until Hermes answers and the transcript reaches a block boundary.

        A steer never closes a live block (closing a tool group would mark its
        running calls cut off, and a streaming reply would split): it is shown
        under the live blocks and written into the transcript at the next
        boundary after Hermes answered, which for a steer Hermes reads after
        its next tool call is where it reads it. Change its `state` with
        `steer_answered`. `agent` names the subagent a steer from the agent
        list is for.
        """
        block = SteerBlock(text, agent=agent)
        self._held.append(block)
        self._write_held()
        self._version += 1
        return block

    def add_answer(self, text: str, level: str) -> None:
        """What the person answered Hermes (`✓ allowed once · <command>`), ahead of the tool call it was about.

        A tool group still live has printed nothing yet, so the answer goes in
        above it; above any other live block it waits for the next boundary,
        as a steer does, rather than closing it.
        """
        block = NoticeBlock(text, level)
        last = self.blocks[-1] if self.blocks else None
        if isinstance(last, ToolGroup) and not last.done and len(self.blocks) - 1 >= self._cursor:
            self.blocks.insert(len(self.blocks) - 1, block)
            self._version += 1
        else:
            self.add_aside(block)

    def steer_answered(self, block: SteerBlock, state: str, outcome: str = "") -> None:
        block.state, block.outcome = state, outcome
        self._write_held()
        self._version += 1

    def add_notice(self, text: str, level: str = NOTICE_INFO) -> None:
        """A notice; while `command` is set, that command's output instead: under its echo while the echo is open,
        else under a repeat of the echo, held like any aside (as `add_output` with `command`)."""
        command = self._open_command()
        if command is not None:
            command.parts.append((text, level))
            self._version += 1
        elif self.command is not None:
            self.add_aside(CommandBlock(self.command.command, [(text, level)], done=True))
        else:
            self._append(NoticeBlock(text, level))

    def add_command(self, command: str) -> CommandBlock:
        """Echo a command the person ran and make it `command`: what it prints lands under it until `close_command`.

        Over a live block (a running tool, a streaming reply) the echo is held
        under it, as a steer is, and written into the transcript at the next
        boundary once the command is over: a command typed during a turn never
        closes what is running.
        """
        block = CommandBlock(command)
        self.add_aside(block)
        self.command = block
        return block

    def add_output(self, text: str, level: str = NOTICE_INFO, *, command: str = "", whole: bool = False) -> None:
        """A command's output: under its open echo, or, once that echo has closed, under a repeat of `command`.

        With no echo open and no `command` named, it is a notice.
        """
        open_command = self._open_command()
        if open_command is not None:
            open_command.parts.append((text, level))
            open_command.whole = open_command.whole or whole
            self._version += 1
        elif command:
            self.add_aside(CommandBlock(command, [(text, level)], whole, done=True))
        else:
            self._append(NoticeBlock(text, level))

    def add_turn_summary(self, seconds: float, output_tokens: int | None) -> None:
        """Close the turn that just ended with its done line; its tool calls are counted from the turn's blocks.

        A command typed during the turn whose echo is still held for Hermes'
        answer is written above the done line with what has arrived, and
        closed: what it prints later goes under a repeat of its echo.
        """
        tools = sum(len(block.tools) for block in self.blocks[self._turn_start :] if isinstance(block, ToolGroup))
        for block in self._held:
            if isinstance(block, CommandBlock):
                block.done = True
        self._append(TurnSummaryBlock(max(seconds, 0.0), tools, output_tokens))

    def close_command(self, block: CommandBlock | None = None) -> None:
        """A command is over (`block`, or the open `command`): its echo and output leave the live region at the next take."""
        command = block if block is not None else self._open_command()
        if command is not None and not command.done:
            command.done = True
            self._write_held()  # a steer sent with /steer goes under its echo; a held echo goes at a boundary
            self._version += 1

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
        self.moa = ""

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

    def _open_command(self) -> CommandBlock | None:
        """`command` while its echo is open and this transcript's; None otherwise."""
        block = self.command
        if block is None or block.done:
            return None
        if (self.blocks and self.blocks[-1] is block) or any(held is block for held in self._held):
            return block
        return None

    def add_aside(self, block: Block) -> None:
        """A block about what is live (a command's echo, a row the agent list writes): held under the live blocks
        until the next boundary, as a steer is; appended when nothing is live, so it never closes a running tool
        group or splits a streaming reply."""
        last = self.blocks[-1] if self.blocks else None
        if last is not None and not last.done:
            self._held.append(block)
            self._version += 1
        else:
            self._append(block)

    def _close_live(self) -> None:
        last = self.blocks[-1] if self.blocks else None
        if last is None or last.done:
            return
        last.done = True
        if isinstance(last, ThinkingBlock):
            last.ended_at = self._clock()
        if isinstance(last, ToolGroup):
            # A draft no `tool.start` took never became a call: Hermes discarded that streamed attempt, or the turn
            # ended while the model was still writing it. Only a call that started can be cut off.
            last.tools = [row for row in last.tools if row.status != TOOL_GENERATING]
            for row in last.tools:
                if row.status == TOOL_RUNNING:
                    row.status = TOOL_INTERRUPTED

    def _append(self, block: Block) -> None:
        self._close_live()
        self._write_held()
        self.blocks.append(block)
        self._version += 1
        self._drop_past_cap()

    def _write_held(self) -> None:
        """The held blocks that wait for nothing more (an answered steer, a finished command), into the transcript,
        when its last block is no longer live."""
        if self.blocks and not self.blocks[-1].done:
            return
        answered = [block for block in self._held if not _awaits_answer(block)]
        if answered:
            self._held = [block for block in self._held if _awaits_answer(block)]
            self.blocks.extend(answered)

    def _drop_past_cap(self) -> None:
        """Past `BLOCK_CAP`, drop the oldest blocks already taken (or left out); one not taken yet stays until it is.

        Runs after an append, a take and a retake, so a resumed session's
        history longer than the cap goes as soon as it is printed.
        """
        drop = min(len(self.blocks) - BLOCK_CAP, self._cursor)
        if drop > 0:
            del self.blocks[:drop]
            self._cursor -= drop
            self._turn_start = max(self._turn_start - drop, 0)
            self.dropped += drop
            self._version += 1

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
        self.moa = ""

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

    def _on_moa_reference(self, payload: dict[str, Any]) -> None:
        label = _first_line(as_text(payload.get(contract.MOA_LABEL))) or "reference"
        index, count = payload.get(contract.MOA_INDEX), payload.get(contract.MOA_COUNT)
        known = all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in (index, count))
        header = f"◇ Reference {index}/{count} — {label}" if known else f"◇ Reference — {label}"
        self._append(ThinkingBlock(text=as_text(payload.get(contract.MOA_TEXT)).strip(), done=True, label=header))

    def _on_moa_progress(self, payload: dict[str, Any]) -> None:
        done, total = payload.get(contract.MOA_REFS_DONE), payload.get(contract.MOA_REFS_TOTAL)
        if all(isinstance(value, int) and not isinstance(value, bool) for value in (done, total)):
            self.moa = f"MoA: refs {done}/{total}"

    def _on_moa_phase(self, payload: dict[str, Any]) -> None:
        if payload.get(contract.MOA_PHASE) == contract.MOA_PHASE_AGGREGATOR:
            self.moa = "MoA: aggregating…"

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
        row.output, row.output_more = _tool_output(payload.get("result"), row.status)
        row.inline_diff, row.diff_root = as_text(payload.get("inline_diff")).encode("utf-8", "surrogatepass"), self.cwd
        row.held_lines = None

    def _display_args(self, summary: str) -> str:
        return _relative(self.cwd, summary) if _is_path(summary) else summary

    def _on_status_update(self, payload: dict[str, Any]) -> None:
        text = _first_line(as_text(payload.get("text")))
        if text:
            self.status = text

    # ── output ──

    def take_stable_lines(self, width: int, theme: Theme) -> list[Line]:
        """Lines that will never change, each returned once: print them above the live region.

        `prompt_row` says where among them the newest prompt's first row is.
        """
        taken: list[Line] = []
        start = (self._cursor, self._cursor_lines)
        self.prompt_row = None
        if self._left_out:
            earlier, self._left_out = _earlier(self._left_out), 0
            notice = (
                LEFT_OUT_DROPPED_NOTICE.format(earlier=earlier, held=len(self.blocks))
                if self.dropped
                else LEFT_OUT_NOTICE.format(earlier=earlier)
            )
            taken.append(truncate_line((Span(notice, theme.muted),), width))
            self._taken_any = True
            self._version += 1
        while self._cursor < len(self.blocks):
            block = self.blocks[self._cursor]
            if isinstance(block, AssistantBlock):
                taken.extend(self._take_assistant(block, width, theme))
            elif block.done:
                rows = block_rows(block, width, theme)
                if isinstance(block, UserBlock) and rows:
                    self.prompt_row = len(taken) + (1 if self._taken_any else 0)  # after the spacer `_spaced` adds
                taken.extend(self._spaced(rows, opens_block=True))
            if not block.done:
                break
            self._cursor += 1
            self._cursor_lines = 0
            self._cursor_state = MarkdownState()
        if (self._cursor, self._cursor_lines) != start:
            self._version += 1
        self._drop_past_cap()
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

    @property
    def taken_any(self) -> bool:
        """Whether any line has been taken, so what is shown next under them opens with a spacer row."""
        return self._taken_any

    @property
    def left_out(self) -> int:
        """How many blocks the next take counts in its opening row instead of showing (`retake_tail`)."""
        return self._left_out

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
        self._drop_past_cap()  # before the take, so the row names how many /history still holds

    def live_lines(self, width: int, theme: Theme, max_rows: int | None = None, now: float | None = None) -> list[Line]:
        """Everything not yet taken, as it shows now: the streaming tail, live thinking, running tools.

        With `max_rows`, only the blocks that reach into the last `max_rows`
        rows are rendered, and of live thinking text only its last rows: the
        rows returned end exactly as the whole tail does, and may start below
        its top. With `now` a running tool's mark pulses (`PULSE_SECONDS`).
        The answer is kept until the transcript changes or a mark turns, so
        asking again with the same width, limit and theme renders nothing.
        """
        key = (self._version, width, max_rows, self._pulses(now))
        held = self._live_held
        if held is None or held[0] != key or held[1] is not theme:
            held = self._live_held = (key, theme, self._render_live(width, theme, max_rows, now))
        return list(held[2])

    def _pulses(self, now: float | None) -> tuple[bool, ...]:
        """The pulse half of every running tool not yet taken: what a live render depends on beyond the version."""
        if now is None:
            return ()
        return tuple(
            _pulse_dim(tool, now)
            for block in self.blocks[self._cursor :]
            if isinstance(block, ToolGroup)
            for tool in block.tools
            if tool.status == TOOL_RUNNING
        )

    def _render_live(self, width: int, theme: Theme, max_rows: int | None, now: float | None = None) -> list[Line]:
        """`live_lines` rendered, from the last block up until `max_rows` rows are covered (all of them for None)."""
        rendered: list[tuple[bool, list[Line]]] = []  # (opens a block, its rows), the last block first
        held = [(True, block_rows(block, width, theme)) for block in reversed(self._held)]  # under the live blocks
        rendered.extend(held)
        count = sum(len(rows) for _opens, rows in held)
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
                block_lines = block_rows(block, width, theme, self.details_expanded and not block.done, max_rows, now)
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

    def dropped_rows(self, width: int, theme: Theme, dropped: int | None = None) -> list[Line]:
        """/history's opening rows once `BLOCK_CAP` has dropped blocks (one counting them, then a blank one); else none.

        `dropped` is the count to name, `self.dropped` when None: a pager
        opened earlier names the count of the blocks it holds.
        """
        count = self.dropped if dropped is None else dropped
        if not count:
            return []
        return [truncate_line((Span(DROPPED_NOTICE.format(earlier=_earlier(count)), theme.muted),), width), ()]

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
