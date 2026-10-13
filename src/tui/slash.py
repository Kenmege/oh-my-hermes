"""Slash commands in the OMH TUI: which ones the TUI runs, and which belong elsewhere.

Hermes' Ink app runs about sixty slash commands itself and sends the rest to
the gateway. The gateway's `slash.exec` already answers most of them on its
own (it runs a full Hermes CLI per session), so the OMH TUI only has to own
three kinds:

- `local`: commands about this terminal (help, quit, history, theme, the OMH
  model picker) and the ones whose screen is the TUI's own (attaching an
  image, the session picker). The TUI handles them, calling the gateway
  method underneath where there is one.
- `hermes`: commands whose surface is Ink-only (voice, the pet, the billing
  flows, overlays) or that would make the TUI persist raw prompts (`/save`,
  `/prompt`). The TUI names the Hermes door instead of half-supporting them.
- `dropped`: Ink display preferences and debug widgets that mean nothing here.

Everything else goes to `slash.exec`, and a 4018 refusal from it falls back to
`command.dispatch`, the order Ink uses.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

LOCAL = "local"
HERMES = "hermes"
DROPPED = "dropped"
PASSTHROUGH = "passthrough"

HERMES_DOOR = "hermes --tui"


@dataclass(frozen=True)
class SlashCommand:
    name: str
    route: str
    summary: str
    aliases: tuple[str, ...] = ()


COMMANDS: tuple[SlashCommand, ...] = (
    # The terminal's own commands.
    SlashCommand("help", LOCAL, "Show commands and keys"),
    SlashCommand("quit", LOCAL, "Leave the OMH TUI", ("exit",)),
    SlashCommand("new", LOCAL, "Start a new session", ("clear", "reset")),
    SlashCommand("sessions", LOCAL, "Switch to a stored session (a picker, or /sessions <id or title>)",
                 ("switch", "session", "resume")),
    SlashCommand("redraw", LOCAL, "Repaint the screen"),
    SlashCommand("history", LOCAL, "Page through this session's transcript"),
    SlashCommand("details", LOCAL, "Show or hide thinking and tool details", ("detail",)),
    SlashCommand("queue", LOCAL, "Show messages waiting for the current turn", ("q",)),
    SlashCommand("logs", LOCAL, "Show the gateway's recent stderr (kept in memory only)"),
    SlashCommand("copy", LOCAL, "Copy the last reply to the clipboard"),
    SlashCommand("image", LOCAL, "Attach an image file to the next message (/image <path>)"),
    SlashCommand("paste", LOCAL, "Attach the clipboard image to the next message (also Ctrl+V)"),
    SlashCommand("theme", LOCAL, "Switch the OMH colour theme"),
    SlashCommand("omh-model", LOCAL, "Pick OMH model chains"),
    # Surfaces that only Hermes' own terminal has.
    SlashCommand("update", HERMES, "Update Hermes"),
    SlashCommand("voice", HERMES, "Voice mode"),
    SlashCommand("wake", HERMES, "Wake word listener"),
    SlashCommand("pet", HERMES, "Animated pet"),
    SlashCommand("browser", HERMES, "Agent browser"),
    SlashCommand("journey", HERMES, "Learning timeline", ("learning", "memory-graph")),
    SlashCommand("replay", HERMES, "Replay a spawn tree"),
    SlashCommand("replay-diff", HERMES, "Diff two spawn trees"),
    SlashCommand("agents", HERMES, "Spawn-tree dashboard", ("tasks",)),
    SlashCommand("topup", HERMES, "Add credits"),
    SlashCommand("subscription", HERMES, "Change plan", ("upgrade",)),
    SlashCommand("setup", HERMES, "Run Hermes setup"),
    SlashCommand("skin", HERMES, "Hermes skin (the OMH TUI uses /theme)"),
    SlashCommand("save", HERMES, "Export the transcript to a file"),
    SlashCommand("prompt", HERMES, "Compose in $EDITOR", ("compose",)),
    SlashCommand("terminal-setup", HERMES, "Write IDE keybindings"),
    # Ink display preferences and debug widgets.
    SlashCommand("mouse", DROPPED, "Mouse tracking", ("scroll",)),
    SlashCommand("density", DROPPED, "Compact display"),
    SlashCommand("focus", DROPPED, "Focus view"),
    SlashCommand("statusbar", DROPPED, "Status bar position", ("sb",)),
    SlashCommand("indicator", DROPPED, "Busy indicator style"),
    SlashCommand("battery", DROPPED, "Battery indicator"),
    SlashCommand("fortune", DROPPED, "Fortune text"),
    SlashCommand("weather", DROPPED, "Weather widget"),
    SlashCommand("widgets-reload", DROPPED, "Rescan Hermes widgets"),
    SlashCommand("heapdump", DROPPED, "V8 heap snapshot"),
    SlashCommand("theme-info", DROPPED, "Ink theme diagnostics"),
    SlashCommand("mem", DROPPED, "Ink heap numbers"),
    SlashCommand("dialog-test", DROPPED, "Demo widget"),
    SlashCommand("grid-test", DROPPED, "Demo widget"),
    SlashCommand("ticker", DROPPED, "Demo widget"),
)

_BY_NAME: dict[str, SlashCommand] = {}
for _command in COMMANDS:
    for _name in (_command.name, *_command.aliases):
        _BY_NAME[_name] = _command


@dataclass(frozen=True)
class SlashInvocation:
    """One typed slash command: `name` as typed (no slash), the rest as `arg`."""

    name: str
    arg: str
    command: SlashCommand | None

    @property
    def route(self) -> str:
        return self.command.route if self.command else PASSTHROUGH

    @property
    def text(self) -> str:
        """The command as `slash.exec` takes it: no leading slash."""
        return f"{self.name} {self.arg}".rstrip()


def parse_slash(text: str) -> SlashInvocation | None:
    """The invocation in `text`, or None when it is not a slash command.

    Only a leading `/` followed by a command name counts; `/` alone, `//x`,
    and a path like `/usr/bin` with a slash inside the name are ordinary text.
    """
    stripped = text.strip()
    if not stripped.startswith("/") or stripped.startswith("//"):
        return None
    head, _, arg = stripped[1:].partition(" ")
    if not head or "/" in head:
        return None
    return SlashInvocation(name=head, arg=arg.strip(), command=_BY_NAME.get(head))


def hermes_notice(command: SlashCommand) -> str:
    """The notice for a command that only Hermes' own terminal runs."""
    return f"/{command.name} runs in Hermes' own terminal: `{HERMES_DOOR}`."


def dropped_notice(command: SlashCommand) -> str:
    return f"/{command.name} is an Ink display setting; the OMH TUI has no equivalent."


@dataclass(frozen=True)
class Completion:
    name: str
    summary: str
    local: bool


def completions(prefix: str, catalog_pairs: Iterable[tuple[str, str]] = ()) -> list[Completion]:
    """Commands starting with `prefix` (no slash): the TUI's own first, then the gateway catalog.

    Catalog entries the TUI routes to Hermes or drops are left out, so the menu
    only offers what will run here.
    """
    local = [
        Completion(command.name, command.summary, True)
        for command in COMMANDS
        if command.route == LOCAL and command.name.startswith(prefix)
    ]
    seen = {completion.name for completion in local}
    gateway: list[Completion] = []
    for raw_name, summary in catalog_pairs:
        name = raw_name.lstrip("/")
        routed = _BY_NAME.get(name)
        if name in seen or not name.startswith(prefix) or routed is not None:
            continue
        seen.add(name)
        gateway.append(Completion(name, summary, False))
    return local + gateway


def catalog_pairs(catalog: Mapping[str, object]) -> list[tuple[str, str]]:
    """The `(name, summary)` pairs out of a `commands.catalog` result; malformed rows are skipped."""
    pairs = catalog.get("pairs")
    if not isinstance(pairs, list):
        return []
    return [
        (pair[0], pair[1])
        for pair in pairs
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(part, str) for part in pair)
    ]
