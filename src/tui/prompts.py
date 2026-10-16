"""The prompt slot: how the OMH TUI asks the person what Hermes asked.

A server-to-client request (an approval, a clarify question, a sudo password)
takes over the panel slot above the composer until it is answered, declined,
or withdrawn by the gateway. Three rules shape this module:

- Nothing is answered on the person's behalf. Every answer comes from a key
  they pressed or text they submitted; Esc or Ctrl+C sends the contract's
  decline frame (an explicit deny for approvals). Allowing a command takes
  the arrows and then Enter: deny is the first row and selected, the
  narrowest allow sits under it, the selection does not wrap, no digit picks
  a choice, and a letter can only deny. The app ignores every key for a
  moment after any slot opens, or its next question appears. A request the gateway
  withdraws itself (its own timeout, an interrupt) arrives as
  `request.cancel`, and the app closes the slot without answering.
- Nothing is left unanswered. A request the TUI cannot show is answered at
  once with its decline frame and a visible notice, because over stdio the
  gateway would otherwise wait out its whole deadline.
- Nothing typed into a masked prompt is kept: the slot holds no copy of the
  value after it builds the answer frame.

A question answered by picking (`PromptSlot.picking`: an approval, or a
clarify question with choices) lists its choices, each from a row of its own
and wrapped when long, the highlighted one marked `❯`, and the app hides the
composer while it is open. A clarify
question's last row opens a text field for an answer of the person's own
(Hermes' wording, "Other (type your answer)"); while that field is open the
composer is the field. Every slot row hangs off a `▌` bar, in the warn colour
for an approval and the accent for anything else, so a question Hermes asked
never reads as panel or transcript text. The command an approval would run is
drawn in the code style; the choices are plain text, the highlighted one also
highlighted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import Any

from . import gateway_contract as contract
from .keys import CHAR, DOWN, ENTER, ESCAPE, LEFT, RIGHT, UP, Key, Paste
from .markdown import wrap_prefixed
from .style import Line, Span, Style
from .theme import Theme
from .width import cell_width, line_width, truncate_line, wrap_line

# `always` writes a permanent allow rule into the person's Hermes config. The
# OMH TUI offers the choices that end with this session; `always` stays a
# decision for Hermes' own terminal. The order is the distance from safe:
# deny first (and selected), then the narrowest allow, then the broadest.
APPROVAL_CHOICES = ("deny", "once", "session")
_APPROVAL_LABELS = {"once": "allow once", "session": "allow this session", "deny": "deny"}
# A letter can only deny. Allowing takes the arrows and Enter, so a key typed
# for the composer as the slot opens can never run a command.
_DENY_KEYS = frozenset("dn")
# The keys that pick a clarify choice. `str.isdigit` also admits "²" or "٣", which `int` rejects.
CHOICE_DIGITS = "123456789"
# The last row of a clarify question's choices: it opens a text field (Hermes' Ink TUI, `prompt.clarify.other`).
CLARIFY_OTHER = "Other (type your answer)"
SLOT_BAR = "▌ "
CHOICE_MARK = "❯ "
# Each question's keys, longest first, for the mode row while it is open: Esc then has one meaning on the screen.
APPROVAL_KEYS = ("↑↓ pick · enter confirms · d or esc denies", "↑↓ enter · d or esc denies", "esc denies")
CLARIFY_CHOICE_KEYS = (
    "↑↓ or 1-9 · enter picks · type to answer · esc cancels", "↑↓ enter picks · esc cancels", "esc cancels"
)
CLARIFY_FIELD_KEYS = (
    "type your answer · enter sends · ↑↓ back to the choices · esc cancels",
    "enter sends · ↑↓ choices · esc cancels",
    "esc cancels",
)
CLARIFY_TEXT_KEYS = ("type an answer · enter sends · esc cancels",)
VALUE_KEYS = ("type it · enter sends · esc declines",)
# A question the slot cannot show whole says that Ctrl+O shows it (`App._question_view`), longest form that fits
# first: on a row of its own (counting the command's rows left out, when any are), or at the end of the last row
# the cut leaves, in a form that leaves that row whole if one does.
MORE_ROWS = ("  … +{count} more line{s} · ctrl+o shows all", "  … +{count} · ctrl+o", "  … ctrl+o")
MORE_ANY_ROWS = ("  … ctrl+o shows all", "  … ctrl+o")
MORE_FORMS = (" … ctrl+o shows all", " … ctrl+o")

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


@dataclass(frozen=True)
class TypeIt:
    """A key that opened a clarify question's text field: the app types it into the field (the composer)."""

    key: Key | Paste


def _decline(request_id: str, method: str) -> Answer:
    return Answer(request_id, dict(contract.server_request_treatment(method).decline))


@dataclass
class PromptSlot:
    """One open request. The app routes keys and submitted composer text here while it is open."""

    request_id: str
    method: str
    params: dict[str, Any]
    selected: int = 0
    question_index: int = 0
    answers: dict[str, str | None] = field(default_factory=dict)
    typing: bool = False  # a clarify question's text field is open: the composer is the field

    @property
    def masked(self) -> bool:
        return contract.server_request_treatment(self.method).masked

    @property
    def wants_text(self) -> bool:
        """Whether submitted composer text answers this slot (clarify free text, value prompts)."""
        return self.method != "approval"

    @property
    def picking(self) -> bool:
        """Whether the person answers by picking from the list now, so no composer is drawn.

        An approval, and a clarify question with choices while its text field
        is closed. A question answered with text (a sudo password, a secret, a
        clarify question without choices, or one whose text field is open)
        has the composer.
        """
        if self.method == "approval":
            return True
        return self.method == "clarify" and bool(self._current_choices()) and not self.typing

    @property
    def keys(self) -> tuple[str, ...]:
        """The keys this question takes now, longest form first, said on the mode row."""
        if self.method == "approval":
            return APPROVAL_KEYS
        if self.method == "clarify":
            if not self._current_choices():
                return CLARIFY_TEXT_KEYS
            return CLARIFY_FIELD_KEYS if self.typing else CLARIFY_CHOICE_KEYS
        return VALUE_KEYS

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
        self.typing = False
        if self.question_index >= len(self._questions()):
            return Answer(self.request_id, {"result": {"answers": dict(self.answers)}})
        return None

    def _pick(self, index: int) -> Answer | None:
        """Choice `index` answers the question; the row after the choices opens the text field instead."""
        choices = self._current_choices()
        if index < len(choices):
            return self._record_answer(choices[index])
        self.selected, self.typing = len(choices), True
        return None

    # -- input ------------------------------------------------------------------------------

    def handle_key(self, key: Key | Paste) -> Answer | TypeIt | None:
        """A key pressed while the slot is open: the answer frame once the person decided, or a key to type.

        `TypeIt` hands back a key that opened a clarify question's text field,
        for the app to type into it.
        """
        if isinstance(key, Key) and (key.name == ESCAPE or (key.name == CHAR and key.ctrl and key.char == "c")):
            return self.decline()
        if self.method == "approval":
            return self._approval_key(key) if isinstance(key, Key) else None
        if self.method == "clarify":
            return self._clarify_key(key)
        return None

    def _approval_key(self, key: Key) -> Answer | None:
        """Up and Down (Left and Right too) move and stop at either end; Enter answers the highlighted choice.

        Deny is the first row and selected as the slot opens, so Up then Enter,
        the composer's recall-and-resend typed as the slot opened, still
        denies: Up moves nothing off the first row. No digit picks a choice,
        and the only letters that answer deny.
        """
        choices = self.approval_choices()
        if key.name in (UP, LEFT):
            self.selected = max(self.selected - 1, 0)
        elif key.name in (DOWN, RIGHT):
            self.selected = min(self.selected + 1, len(choices) - 1)
        elif key.name == ENTER:
            return Answer(self.request_id, {"result": {"choice": choices[self.selected]}})
        elif key.name == CHAR and not key.ctrl and not key.alt and key.char.lower() in _DENY_KEYS:
            return self.decline()
        return None

    def _clarify_key(self, key: Key | Paste) -> Answer | TypeIt | None:
        """The choices' keys, and the keys that open the text field.

        In the list: Up and Down move over the choices and the text field's row
        and stop at either end; Enter picks the highlighted row; a digit picks
        that choice, or opens the field when it numbers the field's row. Any
        other character, or a paste, opens the field and is its first text.
        With the field open, Up and Down go back to the list, and what was
        typed stays in the field.
        """
        choices = self._current_choices()
        if not choices:
            return None
        if isinstance(key, Paste):
            return None if self.typing else self._open_field(key)
        if key.name in (UP, DOWN):
            self.typing = False
            step = -1 if key.name == UP else 1
            self.selected = min(max(self.selected + step, 0), len(choices))
            return None
        if self.typing:
            return None
        if key.name == ENTER:
            return self._pick(self.selected)
        if key.name != CHAR or key.ctrl or key.alt:
            return None
        if len(key.char) == 1 and key.char in CHOICE_DIGITS and int(key.char) - 1 <= len(choices):
            return self._pick(int(key.char) - 1)
        return self._open_field(key)

    def _open_field(self, key: Key | Paste) -> TypeIt:
        self.selected, self.typing = len(self._current_choices()), True
        return TypeIt(key)

    def submit_text(self, text: str) -> Answer | None:
        """Composer text submitted while the slot is open.

        A clarify question with choices takes an empty submit as Enter on the
        highlighted row while the list is shown, and as a way back to the list
        while the text field is open.
        """
        if self.method == "approval":
            return None
        if self.method == "clarify":
            if self._current_choices() and not text.strip():
                if self.typing:
                    self.typing = False
                    return None
                return self._pick(self.selected)
            return self._record_answer(text.strip() or None)
        return Answer(self.request_id, {"result": {"value": text}})

    # -- rendering --------------------------------------------------------------------------

    def lines(self, width: int, theme: Theme, max_rows: int, keys_row: bool = False) -> list[Line]:
        """The slot's rows, at most `max_rows`; `keys_row` adds the question's keys (where no mode row says them).

        A list keeps the highlighted row whatever the budget (`_fit`). A
        question with no list keeps its first row and its last ones. Whatever
        is cut, a marker says Ctrl+O shows the question whole.
        """
        if max_rows <= 0:
            return []
        inner = max(width - len(SLOT_BAR), 1)
        keys: list[Line] = []
        if keys_row:
            said = next((form for form in self.keys if cell_width(f"  {form}") <= inner), self.keys[-1])
            keys = [(Span(f"  {said}", theme.hint),)]
        if self.method == "approval":
            rows = self._approval_lines(inner, theme, max_rows, keys)
        elif self.method == "clarify":
            rows = self._clarify_lines(inner, theme, max_rows, keys)
        else:
            # The header and the hint matter most; the body is what gets cut.
            rows = _keep_ends(self._value_lines(inner, theme), max_rows, inner, theme)
        bar = Span(SLOT_BAR, theme.warn if self.method == "approval" else theme.accent)
        return [truncate_line((bar, *truncate_line(row, inner)), width) for row in rows]

    def whole_lines(self, width: int, theme: Theme) -> list[Line]:
        """Every row of the question, nothing cut: what Ctrl+O pages while it is open (the pager has its own keys)."""
        return self.lines(width, theme, sys.maxsize)

    def _choice_rows(self, labels: list[tuple[str, str]], width: int, theme: Theme, mark_style: Style) -> list[list[Line]]:
        """Each `(lead, label)` as its rows: the label wrapped under its lead (a number), whole, so a tag at its end
        (Hermes' `(Recommended)`) shows; the highlighted one marked `❯` and highlighted."""
        rows: list[list[Line]] = []
        for index, (lead, label) in enumerate(labels):
            current = index == self.selected
            style = theme.menu_current if current else theme.text
            first: Line = (Span(CHOICE_MARK if current else " " * len(CHOICE_MARK), mark_style), Span(lead, style))
            rest: Line = (Span(" " * (len(CHOICE_MARK) + cell_width(lead))),)
            rows.append(wrap_prefixed((Span(label, style),), first, rest, width))
        return rows

    def _window(self, choices: list[list[Line]], room: int) -> list[Line]:
        """The rows of the choices that fit in `room`: the highlighted one, then whole choices before it, then after.

        A highlighted choice taller than `room` alone shows its first rows.
        """
        current = choices[self.selected]
        if len(current) >= room:
            return current[:room]
        first = last = self.selected
        used = len(current)
        while first > 0 and used + len(choices[first - 1]) <= room:
            first -= 1
            used += len(choices[first])
        while last + 1 < len(choices) and used + len(choices[last + 1]) <= room:
            last += 1
            used += len(choices[last])
        return [row for choice in choices[first : last + 1] for row in choice]

    def _fit(
        self, head: list[Line], body: list[Line], choices: list[list[Line]], keys: list[Line], max_rows: int,
        width: int, theme: Theme,
    ) -> list[Line]:
        """`head`, `body`, the `choices` (each one's rows) and `keys`, in that order, within `max_rows`.

        Rows are counted, not choices. Everything is drawn when it fits, the
        keys only on a row nothing else needs. Otherwise the highlighted choice
        always shows, whole when it can, and with two rows or more the head's
        first row comes before it. The rows left go to the body's first row,
        the rest of the head, the other choices (`_window`), a keys row while
        two rows are still left beside a body, and as much of the body as
        fits, in that order. The cut is marked: on a row of its own when one
        is left over (counting the body's rows left out), else at the end of
        the last row above the choices, or of the highlighted choice when it
        is the only row.
        """
        listed = sum(len(choice) for choice in choices)
        if len(head) + len(body) + listed <= max_rows:
            left = max_rows - len(head) - len(body) - listed
            return [*head, *body, *(row for choice in choices for row in choice), *keys[:left]]
        if max_rows == 1:
            return [_ends_with_more(choices[self.selected][0], width, theme)]
        left = max_rows - 1 - min(len(choices[self.selected]), max_rows - 1)  # beside the head's first row and it
        reserved = min(1, left) if body else 0  # the command's first row says what it does
        rest = head[1 : 1 + left - reserved]
        shown = self._window(choices, max_rows - 1 - len(rest) - reserved)
        spare = max_rows - 1 - len(rest) - len(shown)
        keys_row = keys[:1] if body and spare >= 2 else []
        spare -= len(keys_row)
        kept = body[: spare - 1] if len(body) > spare >= 2 else body[:spare]
        above = [head[0], *rest, *kept]
        if spare > len(kept):
            hidden = len(body) - len(kept)
            forms = MORE_ANY_ROWS
            if hidden:
                forms = tuple(form.format(count=hidden, s="s" if hidden != 1 else "") for form in MORE_ROWS)
            above.append((Span(next((form for form in forms if cell_width(form) <= width), forms[-1]), theme.muted),))
        else:
            above[-1] = _ends_with_more(above[-1], width, theme)
        return [*above, *shown, *keys_row]

    def _approval_lines(self, width: int, theme: Theme, max_rows: int, keys: list[Line]) -> list[Line]:
        """The header (wrapped), the command's rows from its first, then the choices (`_fit`).

        The beginning of a command is what says what it does, so a long one is
        cut from the end.
        """
        description = str(self.params.get("description") or "dangerous command")
        command = str(self.params.get("command") or "")
        header = wrap_prefixed(
            (Span(f"Hermes asks to run {description}", theme.warn),), (Span("⚠ ", theme.warn),), (Span("  "),), width
        )
        labels = [("", _APPROVAL_LABELS[choice]) for choice in self.approval_choices()]
        choices = self._choice_rows(labels, width, theme, theme.warn)
        indent: Line = (Span("  ", theme.code),)  # every row of the command under one indent
        command_rows = wrap_prefixed((Span(command, theme.code),), indent, indent, width) if command else []
        return self._fit(header, command_rows, choices, keys, max_rows, width, theme)

    def _clarify_lines(self, width: int, theme: Theme, max_rows: int, keys: list[Line]) -> list[Line]:
        """The question (its counter when there are several), then its choices and the text field's row.

        A question with no choices says how to answer under it instead.
        """
        question = self._current_question()
        if question is None:
            return []
        total = len(self._questions())
        counter = f" ({self.question_index + 1}/{total})" if total > 1 else ""
        head: list[Line] = list(
            wrap_line((Span("? ", theme.accent), Span(str(question.get("question", "")) + counter, theme.text)), width)
        )
        choices = self._current_choices()
        if not choices:
            return _keep_ends([*head, (Span("  type an answer and press enter · esc cancels", theme.hint),)], max_rows,
                              width, theme)
        labels = [(f"{number}. ", choice) for number, choice in enumerate([*choices, CLARIFY_OTHER], start=1)]
        return self._fit(head, [], self._choice_rows(labels, width, theme, theme.accent), keys, max_rows, width, theme)

    def _value_lines(self, width: int, theme: Theme) -> list[Line]:
        label = {
            "sudo": "Hermes needs your sudo password",
            "secret": f"Hermes needs {self.params.get('env_var') or 'a secret'}",
            "vault.code": "Hermes needs a verification code",
        }.get(self.method, f"Hermes asks: {self.method}")
        prompt = self.params.get("prompt") or self.params.get("command") or self.params.get("hint") or ""
        rows = wrap_prefixed(
            (Span(label, theme.text),), (Span("🔑 " if self.masked else "? ", theme.accent),), (Span("   "),), width
        )
        if prompt:
            rows.extend(wrap_line((Span("  " + str(prompt), theme.muted),), width))
        rows.append((Span("  type it in the composer and press enter · esc declines", theme.hint),))
        return rows


def _ends_with_more(row: Line, width: int, theme: Theme) -> Line:
    """`row` with the cut marker at its end (`MORE_FORMS`), the row cut to make room when no form leaves it whole."""
    fits = [form for form in MORE_FORMS if line_width(row) + cell_width(form) <= width]
    form = fits[0] if fits else MORE_FORMS[-1]
    kept = row if fits else truncate_line(row, max(width - cell_width(form), 0), ellipsis="")
    return (*kept, Span(form, theme.muted))


def _keep_ends(rows: list[Line], max_rows: int, width: int, theme: Theme) -> list[Line]:
    """`rows` within `max_rows`: when they do not fit, the first row, marked as cut, and the last ones."""
    if len(rows) <= max_rows:
        return rows
    return [_ends_with_more(rows[0], width, theme), *rows[len(rows) - (max_rows - 1) :]]


def open_request(request_id: str, method: str, params: dict[str, Any]) -> PromptSlot | ImmediateAnswer:
    """A slot for a request the TUI shows, or the immediate answer for one it cannot."""
    if not contract.server_request_treatment(method).shown:
        notice = _DECLINE_NOTICES.get(
            method, f"Hermes asked for `{method}`, which only Hermes' Desktop app answers; declined."
        )
        return ImmediateAnswer(_decline(request_id, method), notice)
    slot = PromptSlot(request_id=request_id, method=method, params=dict(params))
    if method == "clarify" and not slot._questions():
        # An empty slot would wait on a question nobody can see; answer it now, visibly.
        return ImmediateAnswer(slot.decline(), "Hermes sent a clarify request with no question in it; declined.")
    if method == "approval":
        # Enter on an untouched approval denies: allowing takes the arrows, then Enter.
        slot.selected = slot.approval_choices().index("deny")
    return slot
