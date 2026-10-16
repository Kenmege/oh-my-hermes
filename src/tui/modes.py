"""The OMH TUI's input modes (normal, accept, plan) and reasoning effort, mixed into `App`.

Input modes: Shift+Tab cycles normal, accept and plan. Accept is Hermes'
per-session yolo, so entering and leaving it are `config.set` calls that
always name this session and an explicit value and never a `scope`; the mode
shown changes only once Hermes confirms it for this session, an answer that
confirms anything else is treated as a failure and followed by an explicit
off (accept is shown again if that off is not confirmed either: approvals
may be off), and `session.info` corrects what is shown (one saying approvals
are on within `YOLO_SETTLE_SECONDS` of a confirmed on waits for the next: it
may be a snapshot Hermes took before the switch). With approvals off Hermes
sends no approval request, so the TUI still answers none. Plan is the
TUI's own flag, apart from what accept shows: a prompt typed while it is on
goes as `/plan <text>`. When Hermes has approvals off on its own (an off it
confirmed did not end them, or they were off without the TUI turning them
on), accept stays shown and Shift+Tab turns only plan on and off, sending
nothing.
Ctrl+T and `/effort <level>` set the session's reasoning effort the same way
(`config.set` with the session id); the level shown is the one Hermes
confirmed or reports in `session.info`.

The mode row under the composer frame is drawn here too (`mode_row_line`),
since its label is the mode's and the agent strip starts after it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import json
from typing import Any

from . import gateway_contract as contract
from .frame import MODE_ACCEPT, MODE_ACCEPT_PLAN, MODE_NORMAL, MODE_PLAN, MODE_SHELL
from .keys import CHAR, TAB, Key, Paste
from .style import Line, Span, Style
from .theme import Theme
from .transcript import NOTICE_ERROR, NOTICE_WARN, as_text
from .width import cell_width, line_width, truncate_line

# Said by every call that needs a session when none is open yet; it lives here because `ModesMixin`
# says it too, and `app` imports this module.
NO_SESSION_NOTICE = "No session is open yet; try again in a moment."

# The mode row under the composer frame. In normal mode it carries the keys that matter now: what Shift+Tab turns on,
# the next mode first, and Ctrl+T beside the effort Hermes confirmed. Each list is longest first: a row takes the
# first that fits. `{cycle}` is every mode Shift+Tab reaches from here in order, `{next}` the first of them,
# `{effort}` " (medium)" once Hermes reported one.
IDLE_KEYS_HINTS = (
    "? shortcuts · ⇧⇥ {cycle} · ctrl+t effort{effort}",
    "? shortcuts · ⇧⇥ {next} · ctrl+t effort{effort}",
    "⇧⇥ {next} · ctrl+t effort{effort}",
    "? shortcuts · ⇧⇥ {next}",
    "? shortcuts",
)
# Accept all: Hermes asks for no approval in this session. Plan: each prompt goes to Hermes as `/plan <text>`.
ACCEPT_HINTS = (" · Hermes asks no approvals in this session (⇧⇥ {cycle})", " · no approvals (⇧⇥ {next})", " (⇧⇥ {next})")
PLAN_HINTS = (" · prompts go as /plan (⇧⇥ {cycle})", " · prompts go as /plan (⇧⇥ {next})", " (⇧⇥ {next})")
BOTH_HINTS = (" (⇧⇥ {cycle})",)
# What Shift+Tab reaches from each mode, in order (`ModesMixin._cycle_mode`). While Hermes has approvals off on its
# own, the key turns plan on and off and nothing else (`HERMES_OWN_CYCLE`).
MODE_CYCLE = {MODE_NORMAL: "accept all/plan", MODE_ACCEPT: "plan/normal", MODE_PLAN: "normal/accept all"}
HERMES_OWN_CYCLE = {MODE_ACCEPT: "plan", MODE_ACCEPT_PLAN: "plan off"}
EFFORT_KEY = "ctrl+t effort"  # the effort's one display: in the idle hint, or on the row's right in any other state
INTERRUPT_KEY_HINT = "esc interrupt"
STEER_KEY_HINT = "ctrl+enter steer"  # only once the terminal has sent a key in a CSI u or modifyOtherKeys report
STEER_COMMAND_HINT = "/steer <text> steers"
QUEUED_KEY_HINT = "↑ edit queued"
ACCEPT_LABEL = "⏵⏵ accept all on"
PLAN_LABEL = "⏸ plan mode on"
SHELL_LABEL = "! shell (not sent to the model)"
# Each label's shorter forms, for a row where what the next key does would not fit beside the full words.
ACCEPT_LABELS = (ACCEPT_LABEL, "⏵⏵ accept", "⏵⏵")
PLAN_LABELS = (PLAN_LABEL, "⏸ plan", "⏸")
SHELL_LABELS = (SHELL_LABEL, "! shell", "!")
SHELL_HINT = " · enter runs it in Hermes' shell, 30 s at most"
MODE_ROW_GAP = 3
STRIP_SEPARATOR = " · "  # between the mode's label, the agent strip and the hint on the mode row

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
SWITCH_BACK_FAILED_NOTICE = (
    "Hermes did not confirm the switch back either ({}), so approvals may be off: accept all is shown again, "
    "and Shift+Tab asks Hermes to turn them back on."
)
LEFT_YOLO_NOTICE = (
    "Hermes did not confirm that the session you left asks for approvals again ({}). "
    "Its approvals may stay off while this Hermes gateway runs."
)

# Ctrl+T steps through these; `/effort` takes any level in `contract.REASONING_LEVELS`.
EFFORT_CYCLE = ("low", "medium", "high", "xhigh", "max")
# What the mode row says while Hermes reports no level for the session: none is configured, so each route sends its
# own default or none at all (Hermes 6c61c3e27d `_session_info`: "" = provider default). No level Ctrl+T steps from.
EFFORT_DEFAULT = "default"
EFFORT_REFUSED_NOTICE = "Hermes did not set the reasoning effort to {} ({}); it stays as shown."


def mode_label(mode: str, theme: Theme, form: int = 0) -> Line:
    """The mode's own words on the mode row: accept and plan have them, both when both are on, normal has none.

    `form` picks a shorter form (`ACCEPT_LABELS`, `PLAN_LABELS`, `SHELL_LABELS`): 1 the short words, 2 the glyph.
    """
    accept: Line = (Span(ACCEPT_LABELS[form], replace(theme.mode_accept, bold=True)),)
    plan: Line = (Span(PLAN_LABELS[form], replace(theme.mode_plan, bold=True)),)
    if mode == MODE_ACCEPT_PLAN:
        return (*accept, Span(STRIP_SEPARATOR, theme.hint), *plan)
    if mode == MODE_SHELL:
        return (Span(SHELL_LABELS[form], replace(theme.accent, bold=True)),)
    return {MODE_ACCEPT: accept, MODE_PLAN: plan}.get(mode, ())


def strip_column(mode: str, theme: Theme) -> int:
    """The mode row cell the agent strip starts on: after the mode's label and a separator, or the row's first."""
    label = mode_label(mode, theme)
    return line_width(label) + cell_width(STRIP_SEPARATOR) if label else 0


def effort_words(effort: str) -> str:
    """`ctrl+t effort (medium)`, or `ctrl+t effort` while Hermes has reported none: the effort's one display."""
    return f"{EFFORT_KEY} ({effort})" if effort else EFFORT_KEY


def default_hints(mode: str, busy: bool, cycle: str, effort: str) -> tuple[str, ...]:
    """The mode row's own words when nothing else is said: the mode's meaning and Shift+Tab, or the idle keys."""
    cycle = cycle or MODE_CYCLE.get(mode, "")
    words = {"cycle": cycle, "next": cycle.split("/", 1)[0], "effort": f" ({effort})" if effort else ""}
    if mode == MODE_ACCEPT:
        forms = ACCEPT_HINTS
    elif mode == MODE_PLAN:
        forms = PLAN_HINTS
    elif mode == MODE_ACCEPT_PLAN:
        forms = BOTH_HINTS
    elif not busy:
        forms = IDLE_KEYS_HINTS
    else:
        return ()
    return tuple(form.format(**words) for form in forms)


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
    say: str | tuple[str, ...] = "",
    say_style: Style | None = None,
    esc_interrupts: bool = True,
    cycle: str = "",
) -> Line:
    """The row under the composer frame: the mode's label (accept, plan), the agent strip, the keys that matter now, the effort.

    `strip` is the roster's strip while it holds an agent, drawn from cell
    `strip_column(mode, theme)` on. `say`, when given, is what the next key
    will do (a rewind waiting to be sent, Esc's second press) and stands in
    for the hint, in `say_style`; given as forms, longest first, the first
    that fits whole is said, so a narrow row keeps the point rather than the
    start of the long one. When none fits beside the effort the effort goes,
    and when none fits beside the label's words the label takes its short
    words, then its glyph alone, before the last form is cut. During a turn
    the hint offers Esc for the interrupt only while `esc_interrupts` says Esc
    does that now. The label always survives, in some form, and the strip
    after it: the hint (its longest form that fits, `default_hints`) is cut
    from its end first, and the effort on the right goes only when even the
    label and the strip beside it do not fit. Accept and plan say what they
    mean and where Shift+Tab goes next (`cycle`, every mode it reaches from
    here; `MODE_CYCLE` when not given). Idle, the effort is said in the hint
    beside Ctrl+T (`effort_words`) and not again on the right.
    """
    forms = (say,) if isinstance(say, str) else say
    idle = not any(forms) and mode == MODE_NORMAL and not busy
    for form in range(len(ACCEPT_LABELS)):
        mode_part = mode_label(mode, theme, form)
        label = (*mode_part, Span(STRIP_SEPARATOR, theme.hint), *strip) if strip and mode_part else strip or mode_part
        right: Line = (Span(effort_words(effort), theme.muted),) if effort and not idle else ()
        room = width - line_width(label) - (line_width(right) + MODE_ROW_GAP if right else 0)
        if room < 0:
            right, room = (), width - line_width(label)
        if not any(forms):
            break
        said = [text if not label else STRIP_SEPARATOR + text for text in forms]
        fits = [text for text in said if cell_width(text) <= room]
        if not fits and right:  # what the next key does outranks the effort
            right, room = (), width - line_width(label)
            fits = [text for text in said if cell_width(text) <= room]
        if fits or not mode_part:
            break  # and it outranks the label's words, which shorten while nothing fits
    if any(forms):
        hint = fits[0] if fits else said[-1]
    elif mode == MODE_SHELL:
        hint = SHELL_HINT
    elif mode == MODE_NORMAL and busy:
        keys = [*([INTERRUPT_KEY_HINT] if esc_interrupts else []), STEER_KEY_HINT if keyboard_protocol else STEER_COMMAND_HINT]
        hint = " · ".join(keys + ([QUEUED_KEY_HINT] if queued else []))
    else:
        own = default_hints(mode, busy, cycle, effort)
        placed = [text if not strip else STRIP_SEPARATOR + text.removeprefix(STRIP_SEPARATOR).strip() for text in own]
        hint = next((text for text in placed if cell_width(text) <= max(room, 0)), placed[-1] if placed else "")
    if strip:
        hint = hint if hint.startswith(STRIP_SEPARATOR) else STRIP_SEPARATOR + hint.strip()
    left = truncate_line(label, width) + truncate_line((Span(hint, say_style or theme.hint),), max(room, 0))
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


class ModesMixin:
    """`App`'s input modes and reasoning effort.

    The state it reads and writes is set in `App.__init__`; `_request` and
    `_menu_rows` are `App`'s.
    """

    @property
    def mode(self) -> str:
        """What the composer's rules and mode row show: accept while approvals are off, plan while its flag is on."""
        if self.approvals_off:
            return MODE_ACCEPT_PLAN if self.plan_mode else MODE_ACCEPT
        return MODE_PLAN if self.plan_mode else MODE_NORMAL

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
        effort = info.get(contract.SESSION_INFO_REASONING_EFFORT)
        if isinstance(effort, str):
            self.effort = effort or EFFORT_DEFAULT
        yolo = info.get(contract.SESSION_INFO_YOLO)
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
            self.mode_request_open = True  # the switch-back is a yolo call out too: possibly on until it is answered
            self.transcript.add_notice(ACCEPT_REFUSED_NOTICE.format(problem), NOTICE_ERROR)
            self._request(
                "config.set",
                {"key": "yolo", "session_id": sid, "value": YOLO_OFF},
                lambda result: self._on_switched_back(sid, yolo_answer_problem(result, False)),
                lambda error: self._on_switched_back(
                    sid, f"{error.get('code')}: {as_text(error.get('message')) or 'refused'}"
                ),
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

    def _on_switched_back(self, sid: str, problem: str) -> bool:
        """The explicit off after an on Hermes did not confirm; `problem` says why this answer confirms nothing either.

        Unconfirmed, approvals may be off, so accept is shown again, as the
        TUI's own: Shift+Tab then asks for the off once more, and leaving the
        session sends one. Accept shown with approvals on costs nothing;
        normal shown with them off would hide that Hermes asks nothing.
        An answer for a session already left changes nothing and says
        nothing: leaving it counted the switch-back as possibly on and sent
        an off of its own, whose answer reports for it.
        """
        if sid != self.sid:
            return True
        self.mode_request_open = False
        if problem:
            self.transcript.add_notice(SWITCH_BACK_FAILED_NOTICE.format(problem), NOTICE_ERROR)
            self.approvals_off = self.yolo_by_tui = True
        return True

    # ── reasoning effort ──

    def _effort_key(self, event: Key | Paste) -> bool:
        """Ctrl+T steps the reasoning effort through `EFFORT_CYCLE` from the level shown, from low when it is at none
        of them (the provider's default, `EFFORT_DEFAULT`, is no step of it)."""
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
            if self.effort == EFFORT_DEFAULT:
                now = "Hermes sets no reasoning effort for this session, so the provider's default applies."
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

    def _turn_off_yolo_set_here(self, sid: str) -> None:
        """Before leaving `sid`: an explicit yolo off for it, when the TUI turned yolo on there or a yolo call is out.

        Hermes clears a session's yolo as it closes it only when it owns the
        session's lifecycle, and never for a session a messaging gateway
        started (`tui_gateway/session_lifecycle.py`), so the switch the TUI
        set would outlive the TUI's attachment. Sent before `session.close`:
        Hermes runs both inline, in the order they arrive, and answers the id
        of a session it closed with 4001. An off for a call still out is
        harmless and lands after it. A refused off is a notice in whatever
        transcript is live when the answer comes; nothing waits for it, so a
        switch or an exit is never held up.
        """
        if not (self.yolo_by_tui or self.mode_request_open):
            return
        self.yolo_by_tui = self.mode_request_open = False  # sent once; the session is being left
        self._request(
            "config.set",
            {"key": "yolo", "session_id": sid, "value": YOLO_OFF},
            lambda result: self._on_left_yolo_off(yolo_answer_problem(result, False)),
            lambda error: self._on_left_yolo_off(f"{error.get('code')}: {as_text(error.get('message')) or 'refused'}"),
        )

    def _on_left_yolo_off(self, problem: str) -> bool:
        if problem:
            self.transcript.add_notice(LEFT_YOLO_NOTICE.format(problem), NOTICE_ERROR)
        return True
