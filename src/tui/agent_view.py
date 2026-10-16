"""The OMH TUI's subagent views over `subagents.Roster`: the strip, the agent list, one agent's log, mixed into `App`.

Subagents: every `subagent.*` event of the live session is folded into one
`subagents.Roster`, which a new turn and a new session empty. While it holds
an agent the mode row carries a strip of them, Down on an empty composer
gives the keys to a list of them, a list the person picks from as from a
picker: the composer is hidden while it is open, the list stands where the
composer was, and the mode row says its keys. Every key goes to the list
then; a key it does not bind does nothing. Enter or a click opens one
agent's log in the pager. The log is
read-only: `subagent.tail` is asked for the open agent while it runs, at most
every `TAIL_POLL_SECONDS`, and no more once `TAIL_MISSES_BEFORE_STOP` answers
in a row had no tail, until that agent sends a frame; nothing is ever sent to
a subagent. Mouse reports are on only while the agent list is open and the
row a click would land on is certain (the owner's decision of 2026-10-14:
during a turn the wheel scrolls and a drag selects, and Down opens the list
from the keyboard). A plain left click on a list row opens that agent's log,
or on the list's main row leaves the list; every other report does nothing. Reports the terminal sent before it acted on an off
arrive after it, so `_set_mouse` notes when reporting went off and the
decoder is told: a report that starts arriving while reporting is on, or
within `keys.MOUSE_REPORT_SECONDS` after the off, types nothing if it
arrives whole within 0.8 s of its first byte, split across reads or not.
When the pause right after its first byte outlasts
`keys.REPORTING_ESCAPE_SECONDS`, that ESC is Esc, and the rest is dropped
only if it arrives within `keys.MOUSE_REPORT_SECONDS` after that. One that
starts later and is split right after its `ESC` or `ESC[` is read as keys
(`keys`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import gateway_contract as contract
from .keys import BUTTON_LEFT, CHAR, DOWN, ENTER, ESCAPE, LEFT, UP, Key, Mouse, Paste
from .layout import Budget, under_composer
from .modes import strip_column
from .style import Line, Style
from .subagents import FINISHED_STATUSES, MAIN_ID, RUNNING, Hit
from .transcript import NOTICE_WARN, STEER_QUEUED, STEER_REFUSED, NoticeBlock, SteerBlock, as_text

# The list Down opens, and one agent's log in the pager.
AGENT_LIST_ROWS = 7  # main and six agents; a longer roster scrolls
# Its keys, on the mode row while it is open, longest first: each keeps the two action keys and the way back.
AGENT_LIST_HINT = "↑↓ select · ⏎ log · x interrupt · s steer · esc back"
AGENT_LIST_HINTS = (AGENT_LIST_HINT, "⏎ log · x interrupt · s steer · esc back", "x interrupt · s steer · esc back")
# What the mode row says once `x` asked, longest first.
INTERRUPT_CONFIRM = "x again interrupts {name} · any other key cancels"
INTERRUPT_CONFIRMS = (INTERRUPT_CONFIRM, "x again interrupts {name}", "x again interrupts")
LEAVE_KEYS = frozenset({"q"})  # with Esc, Left and Ctrl+C, the plain keys that leave the list
# What the mode row says while `s` has picked an agent, longest first: a narrow row takes the first that fits.
STEER_SAY = (
    "↪ steering {name}: enter sends it to that agent · esc cancels",
    "↪ steering {name} · enter sends · esc cancels",
    "↪ steering {name} · esc cancels",
    "↪ steer · esc cancels",
)
AGENT_FINISHED_NOTICE = "{name} has finished; there is nothing to {action}."
# An agent steer that settled after its session was left, as the main steer's notices say it, naming the agent and
# the session it was for.
AGENT_STEER_LEFT_NOTICE = (
    "Hermes answered the steer for {name} after you left session {session}. "
    "Its text is back in the composer and was not sent to this one."
)
AGENT_STEER_LEFT_UNANSWERED_NOTICE = (
    "Hermes did not answer the steer for {name} in {seconds:g} s, and you had left session {session}. "
    "Its text is back in the composer and was not sent to this one."
)
AGENT_INTERRUPTED_NOTICE = "Interrupted {name}."
AGENT_ALREADY_DONE_NOTICE = "{name} had already finished."
AGENT_INTERRUPT_REFUSED_NOTICE = "Hermes did not interrupt {name}: {reason}."
# A refused agent steer's row says what became of its text (`transcript.SteerBlock`'s outcome), with Hermes' reason
# when it gave one. Hermes refuses one for an agent that has finished or has no tool call left to take it at.
AGENT_STEER_REFUSED = "back in the composer — Hermes did not take the steer"
AGENT_STEER_REFUSED_BECAUSE = AGENT_STEER_REFUSED + " ({reason})"
AGENT_LOG_KEYS = "←/→ agents"
TAIL_POLL_SECONDS = 2.0
# Hermes answers `available: false` while an agent's log is not there yet, as it starts. Five answers in a row
# like that (ten seconds at the pace above) stop the calls for that agent, so one whose `subagent.complete`
# never comes is not asked forever; a new frame for it asks again.
TAIL_MISSES_BEFORE_STOP = 5
# A call with no answer this long no longer holds the next one back; its answer, if it still comes, is dropped.
TAIL_ANSWER_SECONDS = 10.0


class AgentViewMixin:
    """`App`'s subagent strip, list and log.

    The state it reads and writes is set in `App.__init__`, `mode` is
    `ModesMixin`'s, and the methods it calls without defining them are `App`'s.
    """

    def _agent_strip(self, cols: int) -> tuple[Line, tuple[Hit, ...]]:
        """The roster's strip for the mode row, and the cells each entry covers as 0-based screen columns."""
        start = strip_column(self.mode, self.theme)
        line, hits = self.roster.strip_line(cols - start, self.theme, self._spinner_frame())
        return line, tuple(Hit(hit.start + start, hit.end + start, hit.subagent_id) for hit in hits)

    def _agent_list(self, cols: int, room: int) -> tuple[list[Line], tuple[str, ...]]:
        """The open agent list in `room` rows (at most `AGENT_LIST_ROWS`), scrolled to the highlight, and the id of
        each row; nothing while it is closed."""
        if self.list_selected is None:
            return [], ()
        return self.roster.list_lines(
            cols, self.theme, self.list_selected, self._spinner_frame(), min(AGENT_LIST_ROWS, room), self.clock()
        )

    def _agent_list_say(self) -> tuple[tuple[str, ...], Style]:
        """What the mode row says while the list is open: its keys, or, once `x` asked, the confirmation in warn."""
        armed = self.roster.agents.get(self.interrupt_armed or "") if self.interrupt_armed == self.list_selected else None
        if armed is not None:
            return tuple(form.format(name=armed.name) for form in INTERRUPT_CONFIRMS), self.theme.warn
        return AGENT_LIST_HINTS, self.theme.hint

    def _take_clickable(self, budget: Budget, listed: Sequence[str]) -> None:
        """Note the agent list's rows a click opens in the frame just drawn, and ask for mouse reports only while it is open.

        The list is the slot's rows, from its first, with the composer hidden,
        so its first row is the slot's, mode and status rows above the
        region's last. A list row is clickable from edge to edge; nothing else
        is, the strip included. Reports stay off while the region's place on
        the screen is not certain, since no click could be read then.
        """
        cols, rows = self.size
        top = budget.slot + budget.mode + budget.status - 1  # rows above the region's last row of the slot's first row
        self.clickable = {top - index: (Hit(0, cols, row_id),) for index, row_id in enumerate(listed[: budget.slot])}
        self._set_mouse(bool(self.clickable) and self.region.last_row(rows) is not None)

    def _list_available(self) -> bool:
        """Whether the agent list can be shown: agents to list, a mode row to say its keys, and nothing else holding
        the keys (a question, a picker, the history search)."""
        cols, rows = self.size
        return (
            bool(self.roster.agents and under_composer(rows, cols)[1]) and self.slot is None and self.picker is None
            and self.rewind_picker is None and self.model_picker is None and self.search is None
        )

    def _settle_list(self) -> None:
        """Take the keys back from a list that can no longer be shown; keep its highlight on a row it still has."""
        if self.list_selected is None:
            return
        if not self._list_available():
            self.list_selected = None
        elif self.list_selected not in self.roster.agents:
            self.list_selected = MAIN_ID

    def _agent_list_key(self, event: Key | Paste) -> bool:
        """The agent list's keys; True when the list took the key, which it does with every key while it is open.

        Down on an empty composer gives the list the keys, on the first
        agent, while there is one to list and no completion menu is open.
        Then Up and Down move, stopping at either end; Enter opens the
        highlighted agent's log (on main it only leaves); Esc, Left, `q` or
        Ctrl+C leaves; and on an agent `x` asks to interrupt it (a second `x`
        does) and `s` makes the next line sent its steer (`_agent_action`).
        Any other key, a paste or a sequence the decoder has no name for
        does nothing, so nothing typed with the list open reaches the
        composer (with mouse reports on, a terminal that answers in the
        legacy encoding sends such a sequence for every click). The one
        exception is an Alt chord in the legacy encoding, whose bytes are also
        Esc and a key arriving in one read: it leaves, as Esc does, and is then
        read as it would have been, so the key after the Esc is typed.
        """
        self._settle_list()
        name = event.name if isinstance(event, Key) else ""  # a paste is no key of the list's
        if self.list_selected is None:
            if name != DOWN or self.composer.text or self._menu_rows() or not self._list_available():
                return False
            self.list_selected = next(iter(self.roster.agents))
            return True
        armed, self.interrupt_armed = self.interrupt_armed, None  # any key ends a question it did not answer
        if isinstance(event, Key) and name == CHAR and event.alt and not event.protocol:
            self.list_selected = None
            return False
        action = event.char if isinstance(event, Key) and name == CHAR and not event.ctrl and not event.alt else ""
        if action in ("x", "s") and self.list_selected != MAIN_ID:
            self._agent_action(action, self.list_selected, armed)
        elif name in (UP, DOWN):
            ids = self.roster.ids()
            row = ids.index(self.list_selected) + (-1 if name == UP else 1)
            self.list_selected = ids[min(max(row, 0), len(ids) - 1)]
        elif name == ENTER and self.list_selected != MAIN_ID:
            self._open_agent_log(self.list_selected)  # over the list: leaving the log comes back to it
        elif name in (ENTER, ESCAPE, LEFT) or action in LEAVE_KEYS or event == Key(CHAR, "c", ctrl=True):
            self.list_selected = None
        return True

    def _agent_action(self, action: str, subagent_id: str, armed: str | None) -> None:
        """`x` (once a second `x` confirms) interrupts the highlighted agent; `s` makes the next line sent its steer."""
        agent = self.roster.agents.get(subagent_id)
        if agent is None:
            return
        if agent.status in FINISHED_STATUSES:
            verb = "interrupt" if action == "x" else "steer"
            self.transcript.add_aside(NoticeBlock(AGENT_FINISHED_NOTICE.format(name=agent.name, action=verb)))
        elif action == "s":
            self.agent_steer = subagent_id
            self.list_selected = None  # the composer has the keys for the text
        elif armed == subagent_id:
            self._interrupt_agent(subagent_id, agent.name)
        else:
            self.interrupt_armed = subagent_id

    def _interrupt_agent(self, subagent_id: str, name: str) -> None:
        if not self.sid:
            return
        self._request(
            "subagent.interrupt",
            {"session_id": self.sid, "subagent_id": subagent_id},
            lambda result: self.transcript.add_aside(NoticeBlock(
                (AGENT_INTERRUPTED_NOTICE if result.get("found") is True else AGENT_ALREADY_DONE_NOTICE).format(name=name)
            )),
            lambda error: self._agent_refused(
                AGENT_INTERRUPT_REFUSED_NOTICE.format(name=name, reason=(as_text(error.get("message")) or "refused").rstrip("."))
            ),
        )

    def _agent_refused(self, notice: str) -> bool:
        self.transcript.add_aside(NoticeBlock(notice, NOTICE_WARN))
        return True

    def _steer_agent(self, text: str) -> None:
        """Send `text`, the line the person typed after `s`, to the agent they picked; back to the composer if it cannot go."""
        subagent_id, self.agent_steer = self.agent_steer, None
        agent = self.roster.agents.get(subagent_id or "")
        if agent is None or agent.status in FINISHED_STATUSES or not self.sid:
            name = agent.name if agent is not None else "That agent"
            self.transcript.add_aside(NoticeBlock(AGENT_FINISHED_NOTICE.format(name=name, action="steer"), NOTICE_WARN))
            self._set_composer(text)
            return
        block = self.transcript.add_steer(text, agent=agent.name)  # held under what runs, as the main steer is
        sid, session = self.sid, self.stored_id or self.sid
        self._request(
            "subagent.steer",
            {"session_id": sid, "subagent_id": agent.subagent_id, "text": text},
            lambda result: self._on_agent_steered(sid, session, block, result),
            lambda error: self._on_agent_steered(sid, session, block, {}, as_text(error.get("message")).strip().rstrip(".")),
            lambda: self._on_agent_steer_unanswered(sid, session, block),  # its text comes back, never sent again
        )

    def _on_agent_steered(
        self, sid: str, session: str, block: SteerBlock, result: Mapping[str, Any], reason: str = ""
    ) -> bool:
        """Hermes' answer to an agent steer: taken, or refused (with `reason` when Hermes gave one) and given back.

        Settled after `sid`, the session it went to, was left (named `session`), whatever the answer, its text
        comes back with a notice saying so, as the main steer's does.
        """
        if sid != self.sid:
            self._steer_back(block.text, AGENT_STEER_LEFT_NOTICE.format(name=block.agent, session=session))
            return True
        if as_text(result.get("status")) == contract.STEER_QUEUED:
            self.transcript.steer_answered(block, STEER_QUEUED)
        else:
            said = AGENT_STEER_REFUSED_BECAUSE.format(reason=reason) if reason else AGENT_STEER_REFUSED
            self.transcript.steer_answered(block, STEER_REFUSED, said)
            self._give_back(block.text)
        return True

    def _on_mouse(self, event: Mouse) -> None:
        """A plain left press on an agent's row of the list opens that agent's log; nothing else does anything.

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

    def _tail_due_at(self) -> float | None:
        """When `_ask_tail_when_due` may next send a call, on the app's clock; None while it sends none."""
        agent = self.roster.agents.get(self.log_agent) if self.pager is not None else None
        if agent is None or agent.status != RUNNING or not self.sid:
            return None
        if self.tail_misses.get(agent.subagent_id, 0) >= TAIL_MISSES_BEFORE_STOP:
            return None
        if self.tail_asked_at is None:
            return self.clock()
        return self.tail_asked_at + (TAIL_ANSWER_SECONDS if self.tail_open else TAIL_POLL_SECONDS)

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
        # A call still out timed out: forgetting it drops its answer (`_on_response` ignores an id not in `pending`).
        self._forget_tail_calls()
        self.tail_asked_at, self.tail_open = now, True
        sid, subagent_id = self.sid, agent.subagent_id
        self._request(
            "subagent.tail",
            {"session_id": sid, "subagent_id": subagent_id},
            lambda result: self._on_tail(sid, subagent_id, result),
            lambda error: self._on_tail(sid, subagent_id, {}),  # the log keeps what the events gave it
        )

    def _clear_roster(self) -> None:
        """Drop the agents and every piece of tail bookkeeping and pending action that names them."""
        self.roster.clear()
        self.interrupt_armed = self.agent_steer = None
        self.tail_misses.clear()
        self._forget_tail_calls()  # an answer still on its way is for an agent no longer listed
        self.tail_asked_at, self.tail_open = None, False

    def _forget_tail_calls(self) -> None:
        """Drop the `subagent.tail` calls still waiting for an answer, here and in the client, so neither grows."""
        for request_id in [rid for rid, call in self.pending.items() if call.method == "subagent.tail"]:
            del self.pending[request_id]
            self.client.forget(request_id)

    def _on_tail(self, sid: str, subagent_id: str, result: Mapping[str, Any]) -> bool:
        """A `subagent.tail` answer: read only when Hermes has a tail, for an agent still running in this session.

        A tail that arrives after its agent finished would replace the reply
        its `subagent.complete` put in the log, so it is dropped. An answer
        without a tail (`available` anything but true, or an error) counts
        towards `TAIL_MISSES_BEFORE_STOP`. An answer to a call that timed out
        (`TAIL_ANSWER_SECONDS`) never reaches here: the call after it forgot it.
        """
        self.tail_open = False
        if result.get(contract.TAIL_AVAILABLE) is not True:
            self.tail_misses[subagent_id] = self.tail_misses.get(subagent_id, 0) + 1
            return True
        self.tail_misses.pop(subagent_id, None)
        agent = self.roster.agents.get(subagent_id)
        if sid == self.sid and agent is not None and agent.status == RUNNING:
            text, truncated = as_text(result.get(contract.TAIL_TEXT)), result.get(contract.TAIL_TRUNCATED) is True
            self.roster.ingest_tail(subagent_id, text, truncated)
        return True
