"""Every key the OMH TUI binds, in one table.

`SHORTCUTS` is the only list of what each key does. `?` on an empty composer
and `/keys` draw it (`shortcut_lines`), and the Keys table in
docs/INSTALLATION.md is held to the same rows, in the same order, by
`tests/test_tui_shortcuts.py`: a key that gains a row here without its doc
row, or the reverse, fails there. The doc table writes the same text with
backticks around literals; nothing else may differ.

A row is `(keys, what they do)`. Keys that only work in one place say where.
"""

from __future__ import annotations

from .style import Line, Span
from .theme import Theme
from .width import cell_width, wrap_line

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Enter", "Sends the composer"),
    ("\\ then Enter, Alt+Enter, Shift+Enter, Ctrl+J", "Adds a line"),
    ("Ctrl+Enter", "Steers a running turn with the composer's text, on a terminal that tells it from Enter "
                   "(/steer <text> steers on any terminal); idle, sends it as Enter does"),
    ("Tab", "Completes a slash command, or the @ reference being typed"),
    ("Shift+Tab", "Cycles the input mode: normal, accept all, plan; while Hermes itself has approvals off, "
                  "turns plan on and off"),
    ("Ctrl+T", "Steps the reasoning effort: low, medium, high, xhigh, max"),
    ("@", "Starts a file or folder reference; Hermes offers matches above the composer"),
    ("!", "At the start of a line, makes it a shell command Hermes runs when you send it (30 s at most); "
          "it and its output are not sent to the model"),
    ("?", "On an empty composer, shows this list; /keys does too"),
    ("↑ / ↓", "Move between the composer's rows, walk the input history at its first and last row "
              "(earlier sessions' too, shared with hermes --tui), or move through an open completion menu"),
    ("Ctrl+R", "Searches the input history; Ctrl+R again finds an older match, Enter puts it in the composer, "
               "Esc leaves the composer as it was"),
    ("↑", "On an empty composer with prompts queued, takes them back into the composer to edit; a queued shell line comes back on its own"),
    ("↓", "On an empty composer while the mode row shows subagents, opens the agent list under the composer"),
    ("← / →", "Move by a character; with Ctrl or Alt, by a word (Alt+B and Alt+F too)"),
    ("Home / End, Ctrl+A / Ctrl+E", "Go to the start or the end of the line"),
    ("Backspace / Delete", "Delete a character, or a paste chip whole; Backspace on an empty composer removes "
                           "the last attached image"),
    ("Ctrl+W, Alt+Backspace, Ctrl+Backspace", "Deletes the word before the caret (Ctrl+Backspace where the terminal reports it apart from Backspace)"),
    ("Ctrl+U / Ctrl+K", "Delete to the start or the end of the line"),
    ("Esc", "Closes the @ menu; on an empty composer, interrupts a running turn "
            "(the activity row shows how long it has run); cancels a rewind you picked; "
            "cancels an agent steer you picked and keeps what you typed"),
    ("Esc Esc", "With text, clears it and keeps it in this session's history; on an empty composer between turns, "
                "lists your prompts to rewind to (/rewind does too)"),
    ("Ctrl+V", "Attaches the clipboard image"),
    ("Ctrl+O", "Opens the whole transcript with every detail (thinking, tool output, whole diffs) at its end; "
               "q leaves it"),
    ("Ctrl+L", "Redraws the screen"),
    ("Ctrl+C", "Clears the composer, interrupts a running turn, or (twice when idle) exits"),
    ("Ctrl+D", "On an empty composer, exits; with text, deletes the character ahead"),
    ("↑ / ↓ or ← / → then Enter", "On an approval, highlight allow once or allow this session and allow it; "
                                  "Enter alone denies"),
    ("d, n", "On an approval, deny"),
    ("1 to 9", "On a clarify question's list, pick that choice, or the last number opens the field for your own "
               "answer; ↑ / ↓ then Enter picks too, and any other character starts your own answer"),
    ("Esc, Ctrl+C", "On a question Hermes asked, decline it (an approval is denied); "
                    "Ctrl+C first clears an answer being typed"),
    ("↑ / ↓, PageUp / PageDown, Enter, Esc", "In the /sessions, /rewind or /model list, move, take the highlighted "
                                             "row (switch to that session, put that prompt in the composer to send "
                                             "again, or switch this session to that model), or close it; typing "
                                             "filters the list"),
    ("↑ / ↓, Enter, Esc", "In the agent list, move, open the highlighted agent's log (on main, only leave), "
                          "or go back; other typing leaves it too"),
    ("x, s", "In the agent list, on an agent: x twice interrupts it; s steers it with the next line you send "
             "(Esc cancels)"),
    ("Click", "On a row of the agent list, opens that agent's log (on main, only leaves the list)"),
    ("← / →, q or Esc", "In an agent's log, show the agent before or after, or go back; the pager's keys scroll it"),
    ("j / k, Space / b, g / G, q or Esc", "In a pager (/history, /logs, an agent's log, this list), "
                                          "move a line, a page, or to either end, or leave"),
)

TITLE = "OMH TUI keys"
_COLUMN_GAP = 2
_MIN_TEXT_CELLS = 24  # under this the description goes on the rows below its keys


def shortcut_lines(width: int, theme: Theme) -> list[Line]:
    """`SHORTCUTS` as pager rows at `width`: keys in a column, the description wrapped beside them.

    A terminal too narrow for both stacks them: the keys on one row, the
    description indented under them.
    """
    column = max(cell_width(keys) for keys, _does in SHORTCUTS) + _COLUMN_GAP
    beside = width - column >= _MIN_TEXT_CELLS
    indent = column if beside else _COLUMN_GAP
    lines: list[Line] = [(Span(TITLE, theme.heading),), ()]
    for keys, does in SHORTCUTS:
        text = wrap_line((Span(does, theme.text),), max(width - indent, 1))
        if beside:
            lines.append((Span(keys, theme.accent), Span(" " * (column - cell_width(keys))), *text[0]))
            text = text[1:]
        else:
            lines.extend(wrap_line((Span(keys, theme.accent),), max(width, 1)))
        lines.extend((Span(" " * indent), *row) for row in text)
    return lines
