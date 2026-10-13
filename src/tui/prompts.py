"""The prompt slot: how the OMH TUI asks the person what Hermes asked.

A server-to-client request (an approval, a clarify question, a sudo password)
takes over the panel slot above the composer until it is answered, declined,
or withdrawn by the gateway. Three rules shape this module:

- Nothing is answered on the person's behalf. Every answer comes from a key
  they pressed or text they submitted; Esc or Ctrl+C sends the contract's
  decline frame (an explicit deny for approvals). Allowing a command takes
  the arrows and then Enter: a letter can only deny, and the app ignores
  every key for a moment after an approval opens. A request the gateway
  withdraws itself (its own timeout, an interrupt) arrives as
  `request.cancel`, and the app closes the slot without answering.
- Nothing is left unanswered. A request the TUI cannot show is answered at
  once with its decline frame and a visible notice, because over stdio the
  gateway would otherwise wait out its whole deadline.
- Nothing typed into a masked prompt is kept: the slot holds no copy of the
  value after it builds the answer frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import gateway_contract as contract
from .keys import CHAR, DOWN, ENTER, ESCAPE, LEFT, RIGHT, UP, Key, Paste
from .style import Line, Span
from .theme import Theme
from .width import line_width, truncate_line, wrap_line

# `always` writes a permanent allow rule into the person's Hermes config. The
# OMH TUI offers the choices that end with this session; `always` stays a
# decision for Hermes' own terminal.
APPROVAL_CHOICES = ("once", "session", "deny")
_APPROVAL_LABELS = {"once": "allow once", "session": "allow this session", "deny": "deny"}
# A letter can only deny. Allowing takes the arrows and Enter, so a key typed
# for the composer as the slot opens can never run a command.
_DENY_KEYS = frozenset("dn")
# The keys that pick a clarify choice. `str.isdigit` also admits "²" or "٣", which `int` rejects.
CHOICE_DIGITS = "123456789"

_DECLINE_NOTICES = {
    "vault.unlock_prompt": "Hermes asked to unlock a password vault. Vault prompts run in `hermes --tui`; declined.",
    "vault.save_login": "Hermes asked to save a login to a password vault. That runs in `hermes --tui`; declined.",
}


@dataclass(frozen=True)
class Answer:
    """The frame to send back for one request."""

    request_id: str
    frame: dict[str, Any]


@dataclass(frozen=True)
class ImmediateAnswer:
    """A request the TUI cannot show: answered at once, with a notice for the transcript."""

    answer: Answer
    notice: str


def _decline(request_id: str, method: str) -> Answer:
    treatment = contract.server_request_treatment(method)
    return Answer(request_id, dict(treatment.decline or {}))


@dataclass
class PromptSlot:
    """One open request. The app routes keys and submitted composer text here while it is open."""

    request_id: str
    method: str
    params: dict[str, Any]
    selected: int = 0
    question_index: int = 0
    answers: dict[str, str | None] = field(default_factory=dict)

    @property
    def masked(self) -> bool:
        return contract.server_request_treatment(self.method).masked

    @property
    def wants_text(self) -> bool:
        """Whether submitted composer text answers this slot (clarify free text, value prompts)."""
        return self.method != "approval"

    def decline(self) -> Answer:
        return _decline(self.request_id, self.method)

    # -- approval ---------------------------------------------------------------------------

    def approval_choices(self) -> tuple[str, ...]:
        offered = self.params.get("choices")
        if not isinstance(offered, list) or not offered:
            return APPROVAL_CHOICES
        return tuple(choice for choice in APPROVAL_CHOICES if choice in offered or choice == "deny")

    # -- clarify ----------------------------------------------------------------------------

    def _questions(self) -> list[dict[str, Any]]:
        questions = self.params.get("questions")
        if not isinstance(questions, list):
            return []
        return [q for q in questions if isinstance(q, dict) and isinstance(q.get("qid"), str)]

    def _current_question(self) -> dict[str, Any] | None:
        questions = self._questions()
        return questions[self.question_index] if self.question_index < len(questions) else None

    def _current_choices(self) -> list[str]:
        question = self._current_question()
        choices = question.get("choices") if question else None
        return [c for c in choices if isinstance(c, str)] if isinstance(choices, list) else []

    def _record_answer(self, value: str | None) -> Answer | None:
        question = self._current_question()
        if question is None:
            return Answer(self.request_id, {"result": {"answers": dict(self.answers)}})
        self.answers[question["qid"]] = value
        self.question_index += 1
        self.selected = 0
        if self.question_index >= len(self._questions()):
            return Answer(self.request_id, {"result": {"answers": dict(self.answers)}})
        return None

    # -- input ------------------------------------------------------------------------------

    def handle_key(self, key: Key | Paste) -> Answer | None:
        """A key pressed while the slot is open; returns the answer frame once the person decided."""
        if isinstance(key, Paste):
            return None
        if key.name == ESCAPE or (key.name == CHAR and key.ctrl and key.char == "c"):
            return self.decline()
        if self.method == "approval":
            return self._approval_key(key)
        if self.method == "clarify":
            return self._clarify_key(key)
        return None

    def _approval_key(self, key: Key) -> Answer | None:
        choices = self.approval_choices()
        if key.name in (LEFT, UP):
            self.selected = (self.selected - 1) % len(choices)
        elif key.name in (RIGHT, DOWN):
            self.selected = (self.selected + 1) % len(choices)
        elif key.name == ENTER:
            return Answer(self.request_id, {"result": {"choice": choices[self.selected]}})
        elif key.name == CHAR and not key.ctrl and not key.alt and key.char.lower() in _DENY_KEYS:
            return self.decline()
        return None

    def _clarify_key(self, key: Key) -> Answer | None:
        choices = self._current_choices()
        if not choices:
            return None
        if key.name == UP:
            self.selected = (self.selected - 1) % len(choices)
        elif key.name == DOWN:
            self.selected = (self.selected + 1) % len(choices)
        elif key.name == CHAR and not key.ctrl and not key.alt and len(key.char) == 1 and key.char in CHOICE_DIGITS:
            index = int(key.char) - 1
            if 0 <= index < len(choices):
                return self._record_answer(choices[index])
        return None

    def submit_text(self, text: str) -> Answer | None:
        """Composer text submitted while the slot is open. An empty clarify submit picks the highlighted choice."""
        if self.method == "approval":
            return None
        if self.method == "clarify":
            choices = self._current_choices()
            if not text.strip() and choices:
                return self._record_answer(choices[self.selected])
            return self._record_answer(text.strip() or None)
        return Answer(self.request_id, {"result": {"value": text}})

    # -- rendering --------------------------------------------------------------------------

    def lines(self, width: int, theme: Theme, max_rows: int) -> list[Line]:
        if max_rows <= 0:
            return []
        if self.method == "approval":
            rows = self._approval_lines(width, theme, max_rows)
        elif self.method == "clarify":
            rows = self._clarify_lines(width, theme)
        else:
            rows = self._value_lines(width, theme)
        if len(rows) > max_rows:
            # The header and the choices matter most; the body is what gets cut.
            rows = rows[:1] + rows[len(rows) - (max_rows - 1):] if max_rows > 1 else rows[:1]
        return [truncate_line(row, width) for row in rows]

    def _approval_lines(self, width: int, theme: Theme, max_rows: int) -> list[Line]:
        """Header, the command's FIRST rows, `… +K more lines` when cut, then the choices.

        The beginning of a command is what says what it does, so a long one is
        cut from the end. The choice row is kept whatever the budget; with a
        single spare row that row shows the command's head, not the marker.
        """
        description = str(self.params.get("description") or "dangerous command")
        command = str(self.params.get("command") or "")
        header: Line = (Span("⚠ Hermes asks to run ", theme.warn), Span(description, theme.warn))
        choices = self._approval_choice_row(width, theme)
        if max_rows <= 1:
            return [choices]
        command_rows = wrap_line((Span("  " + command, theme.text),), width) if command else []
        room = max_rows - 2
        if len(command_rows) > room:
            kept = room - 1 if room >= 2 else room
            hidden = len(command_rows) - kept
            command_rows = command_rows[:kept]
            if room >= 2:
                command_rows.append((Span(f"  … +{hidden} more line{'s' if hidden != 1 else ''}", theme.dim),))
        return [header, *command_rows, choices]

    def _approval_choice_row(self, width: int, theme: Theme) -> Line:
        """The choices on one row; short labels when the long ones would cut `deny` off the edge."""
        keys = "  ←→ then Enter · d or Esc denies"
        for form, hint in (("long", keys), ("short", keys), ("short", ""), ("letter", "")):
            spans: list[Span] = [Span("  ", theme.text)]
            for index, choice in enumerate(self.approval_choices()):
                style = theme.menu_current if index == self.selected else theme.dim
                spans.append(Span(_approval_label(choice, form), style))
                spans.append(Span(" ", theme.text))
            if hint:
                spans.append(Span(hint, theme.dim))
            row = tuple(spans)
            if line_width(row) <= width:
                return row
        return row

    def _clarify_lines(self, width: int, theme: Theme) -> list[Line]:
        question = self._current_question()
        if question is None:
            return []
        total = len(self._questions())
        counter = f" ({self.question_index + 1}/{total})" if total > 1 else ""
        rows: list[Line] = list(
            wrap_line((Span("? ", theme.accent), Span(str(question.get("question", "")) + counter, theme.text)), width)
        )
        for index, choice in enumerate(self._current_choices()):
            style = theme.menu_current if index == self.selected else theme.text
            rows.append((Span(f"  {index + 1}. ", theme.dim), Span(choice, style)))
        hint = "type an answer and press Enter" if not self._current_choices() else "number or Enter picks; or type an answer"
        rows.append((Span(f"  {hint} · Esc skips", theme.dim),))
        return rows

    def _value_lines(self, width: int, theme: Theme) -> list[Line]:
        label = {
            "sudo": "Hermes needs your sudo password",
            "secret": f"Hermes needs {self.params.get('env_var') or 'a secret'}",
            "vault.code": "Hermes needs a verification code",
        }.get(self.method, f"Hermes asks: {self.method}")
        prompt = self.params.get("prompt") or self.params.get("command") or self.params.get("hint") or ""
        rows: list[Line] = [(Span("🔑 " if self.masked else "? ", theme.accent), Span(label, theme.text))]
        if prompt:
            rows.extend(wrap_line((Span("  " + str(prompt), theme.dim),), width))
        rows.append((Span("  type it in the composer and press Enter · Esc declines", theme.dim),))
        return rows


def _approval_label(choice: str, form: str) -> str:
    if form == "long":
        return f" {_APPROVAL_LABELS[choice]} "
    if form == "short":
        return f" {choice} "
    # The narrowest form still spells out the safe choice.
    return " deny " if choice == "deny" else f" {choice[0]} "


def open_request(request_id: str, method: str, params: dict[str, Any]) -> PromptSlot | ImmediateAnswer:
    """A slot for a request the TUI shows, or the immediate answer for one it cannot."""
    treatment = contract.server_request_treatment(method)
    if treatment.prompt is None:
        notice = _DECLINE_NOTICES.get(
            method, f"Hermes asked for `{method}`, which only Hermes' Desktop app answers; declined."
        )
        return ImmediateAnswer(_decline(request_id, method), notice)
    slot = PromptSlot(request_id=request_id, method=method, params=dict(params))
    if method == "approval":
        # Enter on an untouched approval denies: allowing takes the arrows, then Enter.
        slot.selected = slot.approval_choices().index("deny")
    return slot
