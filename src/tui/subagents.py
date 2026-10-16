"""Hermes subagents as the OMH TUI holds them: a roster folded from gateway events, and the rows it draws.

Pure state and rendering. Nothing here reads a file, opens a socket, starts a
process or calls the gateway: the app hands `Roster.apply` each `subagent.*`
event it reads, hands `Roster.ingest_tail` a `subagent.tail` reply, and asks
for rows when it repaints.

What the gateway sends, measured against Hermes 6c61c3e27d. One function,
`tui_gateway/tool_progress.py::_progress_subagent`, builds every frame; the
frames it built for a scripted run are `tests/fixtures/tui_gateway/subagent_relay.jsonl`.

- Every frame carries `goal`, and from the child relay's identity
  `subagent_id`, `model` and `tool_count`. So any frame can introduce an
  agent, not only its first.
- `subagent.start` is the first frame of a child. `subagent.spawn_requested`
  is in the gateway's event contract, but the child relay has no entry for
  it and drops it; it is folded here (as a queued agent) in case it arrives.
- `subagent.tool` names the call in `tool_name` and carries `tool_preview`
  when the call has one; its `tool_count` already counts that call.
- `subagent.thinking` carries one line in `text`, or no `text` at all while
  the session hides reasoning.
- `subagent.progress` carries a summary naming the child's last five tools:
  calls the `subagent.tool` frames already reported, so it adds no log row.
- `subagent.complete` carries `status` (`completed`, `interrupted`, or a
  failure: `failed`, `error`, `timeout`), `duration_seconds`, and `summary`,
  the child's reply cut to 500 characters, or its error.
- `subagent.text`, the child's streamed reply, is never sent on the parent's
  session: the gateway mirrors it onto the child's own session and skips the
  emit. It is not in the event contract and it is not folded here. An agent's
  reply reaches its log as the `summary` of its `subagent.complete`, or
  through `subagent.tail` while the child is live.
- No frame carries a role or a name. An agent's display name is the first
  words of its goal.

The fold. An agent is keyed by `subagent_id`; a frame without a usable one
changes nothing. The id is stored as it arrived and is never drawn: rows show
the goal and `Agent.name`. A frame that shows life (`start`, `tool`, `thinking`,
`progress`) makes the agent when it is new, so one whose `start` was missed,
or which `clear` dropped while it was still running, comes back the next time
it reports. `complete` never makes one: a verdict on an agent that was never
shown has no row to land on. A finished agent is frozen, so a repeated
`complete` or a late frame cannot change its verdict, its time or its log,
and a late `subagent.tail` can only fill a log that is still empty.

Bounds. The roster holds `ROSTER_CAP` agents; one more pushes out a finished
agent, or the one seen longest ago when none has finished (every frame that
names an agent counts as seeing it, so a busy agent stays however early it
first reported), and `evicted` counts them until `clear`. A log holds `LOG_ROW_CAP` rows; `dropped` counts
the ones pushed out, and the log view says so on its first row. Every stored
string is cut to a fixed length when it is read.

Every gateway string is cleaned line by line by `markdown.clean_text` when it
is stored, as the transcript cleans its text, so widths are measured on what
will show. `screen.paint_line` strips every row again when it paints.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..plugin_bundle.omh.hud_text import elapsed_text
from . import gateway_contract as contract
from .markdown import clean_text, wrap_prefixed
from .panels import SPINNER_FRAMES
from .style import Line, Span
from .theme import Theme
from .width import cell_width, truncate, truncate_line, wrap_line

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
INTERRUPTED = "interrupted"
FAILED_STATUSES = frozenset({"failed", "error", "timeout"})  # Hermes' SUBAGENT_FAILURE_STATUSES
FINISHED_STATUSES = FAILED_STATUSES | {COMPLETED, INTERRUPTED}

MAIN_ID = ""  # the main agent's row and strip entry: `apply` ignores an empty `subagent_id`, so no agent has it

ROSTER_CAP = 64
LOG_ROW_CAP = 400
ID_CHARS = 128
GOAL_CHARS = 500
LABEL_CHARS = 80  # a model or a tool name
ROW_CHARS = 600  # one log row, and a tool call's preview
SUMMARY_CHARS = 2000  # four times what Hermes sends
TAIL_CHARS = 32768  # twice the gateway's 16 KB tail, so a reply within its contract is never cut here
MAX_TOOL_COUNT = 999_999
MAX_SECONDS = 1_000_000_000

NAME_CELLS = 16
MIN_GOAL_CELLS = 12  # a list row keeps its right-hand figures only while the goal has this much room

MAIN_NAME = "main"
UNNAMED = "agent"
UNNAMED_TOOL = "tool"
MAIN_MARK = "●"
DONE_MARK = "○"
FAILED_MARK = "✗"
QUEUED_MARK = "◌"
INTERRUPTED_MARK = "▪"
OPEN_MARK = "↓ agents"  # what Down opens, at the end of the strip
COLLAPSED_OPEN_MARK = "↓"  # after `2 agents`, which already says it
NUMBER_MARK = "·"  # between the number and the name of agents the strip cannot tell apart by their goals' words
SELECTED_MARK = "▸ "
UNSELECTED_MARK = "  "
STRIP_GAP = "  "
COLUMN_GAP = "  "
FACT_SEPARATOR = " · "
CUT_MARK = "…"
TOOL_MARK = "⏺ "
THINKING_MARK = "▸ "
LOG_INDENT = "  "
NO_OUTPUT = "no output yet"
TAIL_CUT_NOTICE = "… earlier output not shown"

LOG_TEXT = "text"
LOG_THINKING = "thinking"
LOG_TOOL = "tool"


class LogRow(NamedTuple):
    kind: str
    text: str
    detail: str = ""  # a tool call's preview


class Hit(NamedTuple):
    """The cells one strip entry covers: `start` to `end` (exclusive), 0-based from the strip's first cell."""

    start: int
    end: int
    subagent_id: str


def _new_log() -> deque[LogRow]:
    return deque(maxlen=LOG_ROW_CAP)


@dataclass
class Agent:
    subagent_id: str
    status: str
    started_at: float  # the app's clock at the first frame seen, or at `start` after a wait in the queue
    goal: str = ""
    model: str = ""
    tool_count: int = 0
    last_tool: str = ""
    finished_at: float | None = None
    duration: float | None = None  # Hermes' own `duration_seconds`, when the `subagent.complete` carried one
    log: deque[LogRow] = field(default_factory=_new_log)
    dropped: int = 0  # rows the cap pushed out of `log`
    tail_cut: bool = False  # `log` was seeded from a tail that had already lost its beginning
    seen: int = 0  # the roster's count of frames when one last named this agent; the lowest was seen longest ago
    log_gen: int = 0  # moves whenever `log`, `dropped` or `tail_cut` changes, so the wrapped log is redone only then

    @property
    def name(self) -> str:
        """The first words of the goal that fit `NAME_CELLS`; a first word longer than that is cut."""
        return _first_words(self.goal.split(), NAME_CELLS) or UNNAMED

    def elapsed(self, now: float) -> float:
        """Seconds it ran: Hermes' own figure once it finished with one, else measured from `started_at`."""
        if self.duration is not None:
            return self.duration
        end = now if self.finished_at is None else self.finished_at
        return max(0.0, end - self.started_at)


# ── reading a payload ─────────────────────────────────────────────────────


def _first_words(words: list[str], cells: int) -> str:
    """The first of `words` that fit `cells`; a first word longer than that is cut. Empty with no words."""
    if not words:
        return ""
    name = words[0]
    for word in words[1:]:
        if cell_width(f"{name} {word}") > cells:
            break
        name = f"{name} {word}"
    return truncate(name, cells)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + CUT_MARK


def _lines(text: str) -> list[str]:
    """The non-blank lines of `text`, each cleaned the way the transcript cleans a line."""
    cleaned = (clean_text(line).rstrip() for line in text.split("\n"))
    return [line for line in cleaned if line.strip()]


def _one_line(value: object, limit: int) -> str:
    """A payload string as one clean line: breaks and runs of spaces become one space, a cut ends in `…`.

    At most `limit` characters are read, so a huge field costs what a small one does.
    """
    raw = _text(value)
    line = " ".join(" ".join(_lines(raw[:limit])).split())
    return line + CUT_MARK if len(raw) > limit else line


def _bounded(value: object, ceiling: int) -> bool:
    """True for a real number from 0 to `ceiling`; NaN, an infinity and a bool are not one."""
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= ceiling


def _identify(agent: Agent, payload: dict[str, Any]) -> None:
    """Take the identity fields a frame carries; one that is missing or mistyped leaves the agent's own."""
    goal = _one_line(payload.get("goal"), GOAL_CHARS)
    if goal:
        agent.goal = goal
    model = _one_line(payload.get("model"), LABEL_CHARS)
    if model:
        agent.model = model
    count = payload.get("tool_count")
    if isinstance(count, int) and _bounded(count, MAX_TOOL_COUNT):
        agent.tool_count = count


def _push(agent: Agent, kind: str, text: str, detail: str = "") -> None:
    if len(agent.log) == LOG_ROW_CAP:
        agent.dropped += 1
    agent.log.append(LogRow(kind, _cut(text, ROW_CHARS), detail))
    agent.log_gen += 1


# ── rendering ─────────────────────────────────────────────────────────────


def _mark(agent: Agent, theme: Theme, spinner_frame: int) -> Span:
    if agent.status == RUNNING:
        return Span(SPINNER_FRAMES[spinner_frame % len(SPINNER_FRAMES)], theme.accent)
    if agent.status == QUEUED:
        return Span(QUEUED_MARK, theme.muted)
    if agent.status in FAILED_STATUSES:
        return Span(FAILED_MARK, theme.error)
    if agent.status == INTERRUPTED:
        return Span(INTERRUPTED_MARK, theme.muted)
    return Span(DONE_MARK, theme.muted)


def _tools_text(count: int) -> str:
    return f"{count} tool{'' if count == 1 else 's'}"


def _dropped_text(agent: Agent) -> str:
    """The log view's first row when rows are missing; a cut tail hides how many, so it gives no number."""
    if agent.tail_cut:
        return TAIL_CUT_NOTICE
    if agent.dropped:
        return f"… {agent.dropped} earlier row{'' if agent.dropped == 1 else 's'} dropped"
    return ""


def _log_row(row: LogRow, width: int, theme: Theme) -> list[Line]:
    rest: Line = (Span(LOG_INDENT),)
    if row.kind == LOG_TOOL:
        preview = f"{COLUMN_GAP}{row.detail}" if row.detail else ""
        spans = (Span(row.text, theme.tool), Span(preview, theme.muted))
        return wrap_prefixed(spans, (Span(TOOL_MARK, theme.tool),), rest, width)
    if row.kind == LOG_THINKING:
        return wrap_prefixed((Span(row.text, theme.thinking),), (Span(THINKING_MARK, theme.muted),), rest, width)
    return wrap_line((Span(row.text, theme.text),), width)


# ── the roster ────────────────────────────────────────────────────────────


class Roster:
    """The subagents the TUI is showing, in the order they first reported."""

    def __init__(self) -> None:
        self.agents: dict[str, Agent] = {}
        self.evicted = 0  # agents the cap pushed out since the last `clear`
        self._frames = 0  # frames that named a held agent, for `Agent.seen`
        # Each agent's log rows as `log_lines` last wrapped them, with the agent, log generation, width and theme
        # they were wrapped for: a log view repaints every tick, and its log changes far less often.
        self._wrapped: dict[str, tuple[Agent, int, int, Theme, list[Line]]] = {}
        self._handlers: dict[str, Callable[[str, dict[str, Any], float], None]] = {
            contract.SUBAGENT_SPAWN_REQUESTED: self._on_spawn_requested,
            contract.SUBAGENT_START: self._on_alive,
            contract.SUBAGENT_PROGRESS: self._on_alive,
            contract.SUBAGENT_THINKING: self._on_thinking,
            contract.SUBAGENT_TOOL: self._on_tool,
            contract.SUBAGENT_COMPLETE: self._on_complete,
        }

    # ── input ──

    def apply(self, event_type: str, payload: object, now: float) -> None:
        """Fold one gateway event; `now` is the app's clock.

        An event type that is not one of the six above, a payload that is not
        an object, and a `subagent_id` that is missing, blank (the gateway
        contract refuses a call naming it), over-long or not a string each
        change nothing. Nothing raises.
        """
        handler = self._handlers.get(event_type) if isinstance(event_type, str) else None
        if handler is None or not isinstance(payload, dict):
            return
        subagent_id = payload.get("subagent_id")
        if not isinstance(subagent_id, str) or not subagent_id.strip() or len(subagent_id) > ID_CHARS:
            return
        handler(subagent_id, payload, now)
        agent = self.agents.get(subagent_id)
        if agent is not None:
            self._frames += 1
            agent.seen = self._frames

    def clear(self) -> None:
        """Drop every agent: the app calls this when a new turn starts and when it leaves a session."""
        self.agents.clear()
        self._wrapped.clear()
        self.evicted = 0

    def ingest_tail(self, subagent_id: str, text: str, truncated: bool = False) -> None:
        """Replace an agent's log with the `text` of a `subagent.tail` reply; `truncated` is the reply's own flag.

        A tail the gateway cut opens in the middle of a line, so that line is
        dropped and the log view says earlier output is missing. A tail with
        no line in it, and an id the roster does not hold, change nothing. A
        finished agent is frozen: a tail can only seed its log while the log
        is empty, never replace the reply its `subagent.complete` put there.
        """
        agent = self.agents.get(subagent_id)
        if agent is None or not isinstance(text, str) or (agent.status in FINISHED_STATUSES and agent.log):
            return
        cut = truncated is True or len(text) > TAIL_CHARS
        tail = text[-TAIL_CHARS:]
        if cut:
            tail = tail.partition("\n")[2]
        rows = _lines(tail)
        if not rows:
            return
        agent.log = deque((LogRow(LOG_TEXT, _cut(row, ROW_CHARS)) for row in rows), maxlen=LOG_ROW_CAP)
        agent.dropped = max(0, len(rows) - LOG_ROW_CAP)
        agent.tail_cut = cut
        agent.log_gen += 1

    def _make_room(self) -> None:
        """Keep the roster under its cap: a finished agent leaves first, else the one seen longest ago."""
        if len(self.agents) < ROSTER_CAP:
            return
        finished = next((key for key, agent in self.agents.items() if agent.status in FINISHED_STATUSES), None)
        evicted = finished if finished is not None else min(self.agents, key=lambda key: self.agents[key].seen)
        del self.agents[evicted]
        self._wrapped.pop(evicted, None)
        self.evicted += 1

    def _live(self, subagent_id: str, payload: dict[str, Any], now: float, status: str) -> Agent | None:
        """The agent a sign of life belongs to, made when it is new; None once it has finished."""
        agent = self.agents.get(subagent_id)
        if agent is None:
            self._make_room()
            agent = self.agents[subagent_id] = Agent(subagent_id, status, now)
        elif agent.status in FINISHED_STATUSES:
            return None
        elif agent.status == QUEUED and status == RUNNING:
            agent.status, agent.started_at = RUNNING, now  # the wait for a worker is not run time
        _identify(agent, payload)
        return agent

    def _on_spawn_requested(self, subagent_id: str, payload: dict[str, Any], now: float) -> None:
        self._live(subagent_id, payload, now, QUEUED)

    def _on_alive(self, subagent_id: str, payload: dict[str, Any], now: float) -> None:
        self._live(subagent_id, payload, now, RUNNING)

    def _on_thinking(self, subagent_id: str, payload: dict[str, Any], now: float) -> None:
        agent = self._live(subagent_id, payload, now, RUNNING)
        text = _one_line(payload.get("text"), ROW_CHARS)
        if agent is not None and text:
            _push(agent, LOG_THINKING, text)

    def _on_tool(self, subagent_id: str, payload: dict[str, Any], now: float) -> None:
        agent = self._live(subagent_id, payload, now, RUNNING)
        if agent is None:
            return
        agent.last_tool = _one_line(payload.get("tool_name"), LABEL_CHARS) or UNNAMED_TOOL
        preview = _one_line(payload.get("tool_preview") or payload.get("text"), ROW_CHARS)
        _push(agent, LOG_TOOL, agent.last_tool, preview)

    def _on_complete(self, subagent_id: str, payload: dict[str, Any], now: float) -> None:
        agent = self.agents.get(subagent_id)
        if agent is None or agent.status in FINISHED_STATUSES:
            return
        _identify(agent, payload)
        status = _text(payload.get("status")).lower()
        agent.status = status if status in FINISHED_STATUSES else COMPLETED
        agent.finished_at = now
        duration = payload.get("duration_seconds")
        if _bounded(duration, MAX_SECONDS):
            agent.duration = float(duration)
        for line in _lines(_text(payload.get("summary") or payload.get("text"))[:SUMMARY_CHARS]):
            _push(agent, LOG_TEXT, line)

    # ── output ──

    def ids(self) -> tuple[str, ...]:
        """`MAIN_ID`, then every agent in roster order: the rows of the list, top to bottom."""
        return (MAIN_ID, *self.agents)

    def running_count(self) -> int:
        """How many agents are still running."""
        return sum(1 for agent in self.agents.values() if agent.status == RUNNING)

    def strip_names(self) -> dict[str, str]:
        """Each agent's name on the strip: its own, or, where two or more share it, the words where their goals part.

        The agents sharing a name drop the leading words all their goals have
        in common and show the first of what follows behind `CUT_MARK`
        (`…alpha in the`), in `NAME_CELLS`. Where that still leaves one with no
        words, two alike, or one reading as another agent's own name, each of
        them is `n·` and the name instead (n from 1, roster order).
        """
        names = {subagent_id: agent.name for subagent_id, agent in self.agents.items()}
        numbers = {subagent_id: number for number, subagent_id in enumerate(names, start=1)}
        shown = dict(names)
        for name in {name for name in names.values() if list(names.values()).count(name) > 1}:
            group = [subagent_id for subagent_id, own in names.items() if own == name]
            goals = [self.agents[subagent_id].goal.split() for subagent_id in group]
            common = 0
            while all(len(words) > common for words in goals) and len({words[common] for words in goals}) == 1:
                common += 1
            # With no word in common the names matched because both were cut or a goal had no words; the parts
            # then match too, or one is empty, so numbers follow.
            parts = [CUT_MARK + _first_words(words[common:], NAME_CELLS - cell_width(CUT_MARK)) for words in goals]
            told_apart = (
                all(part != CUT_MARK for part in parts) and len(set(parts)) == len(parts)
                and not set(parts) & set(names.values())
            )
            for subagent_id, part in zip(group, parts, strict=True):
                shown[subagent_id] = part if told_apart else f"{numbers[subagent_id]}{NUMBER_MARK}{name}"
        return shown

    def strip_line(self, width: int, theme: Theme, spinner_frame: int) -> tuple[Line, tuple[Hit, ...]]:
        """`● main  ⠹ explore  ○ review  ↓ agents`, and the cells each entry covers on it.

        A running agent shows frame `spinner_frame` of the spinner, a finished
        one `○`, a failed one `✗`. Agents whose names match are told apart
        (`strip_names`), so no two entries read the same. When that row is wider than `width` it
        becomes `● 2 agents ↓` (cut to `width`), which names no agent and so
        has no hits; `64+ agents` when the cap pushed some out. With no agent
        there is no strip.
        """
        if not self.agents:
            return (), ()
        spans = [Span(f"{MAIN_MARK} ", theme.accent), Span(MAIN_NAME, theme.hint)]
        column = cell_width(f"{MAIN_MARK} {MAIN_NAME}")
        hits = [Hit(0, column, MAIN_ID)]
        tail = f"{STRIP_GAP}{OPEN_MARK}"
        names = self.strip_names()
        for agent in self.agents.values():
            mark = _mark(agent, theme, spinner_frame)
            name = names[agent.subagent_id]
            start = column + cell_width(STRIP_GAP)
            column = start + cell_width(f"{mark.text} {name}")
            if column + cell_width(tail) > width:
                return self._collapsed_strip(width, theme), ()
            style = theme.muted if agent.status in FINISHED_STATUSES else theme.hint
            spans += [Span(STRIP_GAP), mark, Span(f" {name}", style)]
            hits.append(Hit(start, column, agent.subagent_id))
        spans.append(Span(tail, theme.hint))
        return tuple(spans), tuple(hits)

    def _collapsed_strip(self, width: int, theme: Theme) -> Line:
        count = len(self.agents)
        more = "+" if self.evicted else ""
        label = f"{count}{more} agent{'' if count == 1 else 's'} {COLLAPSED_OPEN_MARK}"
        return truncate_line((Span(f"{MAIN_MARK} ", theme.accent), Span(label, theme.hint)), width)

    def list_lines(
        self, width: int, theme: Theme, selected: str, spinner_frame: int, max_rows: int, now: float
    ) -> tuple[list[Line], tuple[str, ...]]:
        """One row for main and one per agent, and the id each row is for (`MAIN_ID` for main).

        A row is its selection marker, status glyph and goal, with
        `elapsed · N tools` at the right edge; the `selected` row is one
        `menu_current` bar across `width`. More rows than `max_rows` scroll
        just far enough to keep the selected one on screen. Where the goal
        would be left less than `MIN_GOAL_CELLS`, the tool count goes first,
        then the elapsed time. With no agent there is no list.
        """
        if not self.agents or max_rows <= 0:
            return [], ()
        ids = self.ids()
        index = ids.index(selected) if selected in ids else 0
        start = min(max(index - max_rows + 1, 0), max(len(ids) - max_rows, 0))
        shown = ids[start : start + max_rows]
        rows = [self._list_row(row_id, row_id == selected, width, theme, spinner_frame, now) for row_id in shown]
        return rows, shown

    def _list_row(
        self, row_id: str, current: bool, width: int, theme: Theme, spinner_frame: int, now: float
    ) -> Line:
        agent = self.agents.get(row_id)
        if agent is None:
            mark, label, figures = Span(MAIN_MARK, theme.accent), MAIN_NAME, [""]
        else:
            elapsed = elapsed_text(agent.elapsed(now))
            mark, label = _mark(agent, theme, spinner_frame), agent.goal or UNNAMED
            figures = [f"{elapsed}{FACT_SEPARATOR}{_tools_text(agent.tool_count)}", elapsed, ""]
        lead = SELECTED_MARK if current else UNSELECTED_MARK
        fixed = cell_width(f"{lead}{mark.text} ")
        for figure in figures:  # ends on "", which always stays
            room = width - fixed - (cell_width(f"{COLUMN_GAP}{figure}") if figure else 0)
            if room >= MIN_GOAL_CELLS:
                break
        label = truncate(label, room)
        pad = " " * (room - cell_width(label))
        right = f"{pad}{COLUMN_GAP}{figure}" if figure else ""
        if current:
            return truncate_line((Span(f"{lead}{mark.text} {label}{right or pad}", theme.menu_current),), width)
        return truncate_line((Span(lead), mark, Span(f" {label}", theme.text), Span(right, theme.muted)), width)

    def log_lines(self, subagent_id: str, width: int, theme: Theme, now: float) -> list[Line]:
        """An agent's log view: its goal, then `status · elapsed · N tools · model`, a blank row, and its log.

        Everything is wrapped to `width`. A log that lost rows opens by saying
        so; an empty one says `no output yet`. An id the roster does not hold
        (`MAIN_ID` included) has no rows. The rows under the header are
        wrapped again only when the log, the width or the theme changed since
        the last call; the header, whose elapsed time moves, every time.
        """
        agent = self.agents.get(subagent_id)
        if agent is None:
            return []
        facts = [elapsed_text(agent.elapsed(now)), _tools_text(agent.tool_count), agent.model]
        status = Span(agent.status, _mark(agent, theme, 0).style)
        rest = Span("".join(f"{FACT_SEPARATOR}{fact}" for fact in facts if fact), theme.muted)
        header: list[Line] = [
            *wrap_line((Span(agent.goal or UNNAMED, theme.accent),), width),
            *wrap_line((status, rest), width),
            (),
        ]
        return header + self._wrapped_log(agent, width, theme)

    def _wrapped_log(self, agent: Agent, width: int, theme: Theme) -> list[Line]:
        """The rows under the log view's header, from `_wrapped` while nothing they depend on has changed."""
        held = self._wrapped.get(agent.subagent_id)
        if held is not None and held[0] is agent and held[1:3] == (agent.log_gen, width) and held[3] is theme:
            return held[4]
        rows: list[Line] = []
        notice = _dropped_text(agent)
        if notice:
            rows.append(truncate_line((Span(notice, theme.muted),), width))
        if not agent.log:
            rows.append(truncate_line((Span(NO_OUTPUT, theme.muted),), width))
        for row in agent.log:
            rows.extend(_log_row(row, width, theme))
        self._wrapped[agent.subagent_id] = (agent, agent.log_gen, width, theme, rows)
        return rows
