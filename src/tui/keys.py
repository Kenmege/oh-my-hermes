"""Turn raw terminal bytes into keys and pastes, incrementally.

A terminal delivers input in arbitrary chunks: a UTF-8 character, an escape
sequence or a bracketed paste can straddle two reads. `KeyDecoder.feed`
decodes everything complete and keeps the incomplete tail for the next call,
so the caller never sees half a key.

A lone ESC is ambiguous until time passes: it is the Escape key, or the first
byte of an arrow or an Alt chord whose next byte is still in flight. The
decoder never guesses; `flush()`, which the caller runs after a short silence,
settles a held ESC as `Key("escape")`. A paste is not settled by `flush()`: it
ends only at the terminal's paste-end marker.

Nothing here raises on input. A sequence it does not know becomes
`Key("unknown")` and decoding continues with the next byte.

Terminals that `Terminal` asks for richer key reports send some keys as kitty
CSI u (`ESC[99;5u`) or xterm modifyOtherKeys (`ESC[27;5;99~`). Each of those
decodes to the very `Key` its legacy bytes give, so Ctrl+C, Esc, Tab, Alt+b or
Shift+a behave the same either way; the reports only add what legacy bytes
cannot say, such as Ctrl+Enter and Shift+Enter. A key decoded from either
form carries `protocol=True`, and `KeyDecoder.protocol_reported` turns on at
the first one: the terminal is known to send them only once it has.

While `Terminal.set_mouse(True)` has mouse reporting on, a click arrives as an
SGR report (`ESC[<b;x;yM` press, `ESC[<b;x;ym` release) and decodes to a
`Mouse`. A wheel turn or a motion report decodes too, so no byte of a report
is ever typed as text. A terminal that ignores the SGR request sends the
legacy `ESC[M` and three payload bytes instead; that is one unknown key.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

CHAR = "char"
ENTER = "enter"
TAB = "tab"
BACKSPACE = "backspace"
ESCAPE = "escape"
UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"
HOME = "home"
END = "end"
INSERT = "insert"
DELETE = "delete"
PAGE_UP = "pageup"
PAGE_DOWN = "pagedown"
UNKNOWN = "unknown"

# Bytes and sequences the decoder reads, in one place.
ESC_BYTE = 0x1B
CSI_BYTE = ord("[")
SS3_BYTE = ord("O")
CARRIAGE_RETURN = 0x0D
LINE_FEED = 0x0A
HORIZONTAL_TAB = 0x09
BACKSPACE_BYTE = 0x08
DELETE_BYTE = 0x7F
PASTE_START_PARAMETER = "200"
PASTE_END = b"\x1b[201~"

# A CSI longer than this without a final byte is garbage, not a slow key; the
# bound keeps a hostile or broken stream from growing the held tail forever.
_MAX_CSI_LENGTH = 32

_CSI_LETTER_KEYS = {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT, "H": HOME, "F": END}
_SS3_KEYS = _CSI_LETTER_KEYS
_CSI_TILDE_KEYS = {
    "1": HOME,
    "2": INSERT,
    "3": DELETE,
    "4": END,
    "5": PAGE_UP,
    "6": PAGE_DOWN,
    "7": HOME,
    "8": END,
}
# xterm modifier parameter: 1 + (shift 1 | alt 2 | ctrl 4 | meta 8).
_SHIFT_BIT = 1
_ALT_BIT = 2
_CTRL_BIT = 4
# kitty adds caps lock 64 and num lock 128, which change no key OMH reads;
# super, hyper and meta (8, 16, 32) have no place in `Key`, so such a chord is unknown.
_LOCK_BITS = 64 | 128
_READ_BITS = _SHIFT_BIT | _ALT_BIT | _CTRL_BIT

# kitty CSI u (`ESC[<code>[:<shifted>[:<base>]][;<mods>[:<event>]]u`) and xterm
# modifyOtherKeys (`ESC[27;<mods>;<code>~`) name a key by its code point.
CSI_U_FINAL = "u"
MODIFY_OTHER_KEYS_PARAMETER = "27"
# kitty event types: 1 press, 2 repeat, 3 release. A release is not a key.
_PRESS_EVENTS = ("", "1", "2")
_PROTOCOL_KEYS = {
    CARRIAGE_RETURN: ENTER,
    HORIZONTAL_TAB: TAB,
    ESC_BYTE: ESCAPE,
    DELETE_BYTE: BACKSPACE,
    57414: ENTER,  # keypad Enter
}
# kitty's keypad code points (functional key table), for the keys that type text.
_KEYPAD_TEXT = {57399 + digit: str(digit) for digit in range(10)} | {
    57409: ".",
    57410: "/",
    57411: "*",
    57412: "-",
    57413: "+",
    57415: "=",
}
# Besides the letters, the keys whose Ctrl chord a legacy terminal sends as a C0 byte.
_CTRL_PUNCTUATION = "@[\\]^_ "

# Mouse buttons a `Mouse` names. NO_BUTTON is a release that does not say which
# button, or motion with none held.
BUTTON_LEFT = "left"
BUTTON_MIDDLE = "middle"
BUTTON_RIGHT = "right"
NO_BUTTON = "none"
WHEEL_UP = "wheel-up"
WHEEL_DOWN = "wheel-down"
WHEEL_LEFT = "wheel-left"
WHEEL_RIGHT = "wheel-right"

# SGR mouse report: `ESC[<b;x;yM` press, `ESC[<b;x;ym` release (xterm mode 1006).
SGR_MOUSE_PREFIX = "<"
SGR_MOUSE_PRESS = "M"
SGR_MOUSE_RELEASE = "m"
# Legacy (X10) mouse report: `ESC[M` and three bytes, each 32 plus a value.
X10_MOUSE_FINAL = ord("M")
_X10_MOUSE_PAYLOAD_BYTES = 3
# The button code: the low two bits pick the button, 64 and 128 move to the
# wheel and the extra buttons, and these four flags ride on top of any of them.
_MOUSE_SHIFT_BIT = 4
_MOUSE_ALT_BIT = 8  # xterm calls it meta; it is the Alt key
_MOUSE_CTRL_BIT = 16
_MOUSE_MOTION_BIT = 32
_MOUSE_FLAG_BITS = _MOUSE_SHIFT_BIT | _MOUSE_ALT_BIT | _MOUSE_CTRL_BIT | _MOUSE_MOTION_BIT
# Buttons 8 to 11 (code 128 and up) have no name here, so their reports are unknown keys.
_MOUSE_BUTTONS = {
    0: BUTTON_LEFT,
    1: BUTTON_MIDDLE,
    2: BUTTON_RIGHT,
    3: NO_BUTTON,
    64: WHEEL_UP,
    65: WHEEL_DOWN,
    66: WHEEL_LEFT,
    67: WHEEL_RIGHT,
}


@dataclass(frozen=True)
class Key:
    """One key press. `protocol` says it arrived as a kitty CSI u or xterm modifyOtherKeys report.

    `protocol` is not part of a key's identity: such a report decodes to the
    very `Key` its legacy bytes give, and compares equal to it. It says which
    form the terminal used, a form only a terminal that honoured the request
    for richer reports sends.
    """

    name: str
    char: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    protocol: bool = field(default=False, compare=False, repr=False)


@dataclass(frozen=True)
class Paste:
    text: str


@dataclass(frozen=True)
class Mouse:
    """One SGR mouse report.

    `x` and `y` are 1-based cells, exactly as the terminal reports them: the
    top-left cell is (1, 1) and the screen's last row is `y == rows`. A wheel
    turn is a press of WHEEL_UP or WHEEL_DOWN; `motion` marks a report sent
    because the pointer moved, which only a motion tracking mode produces, so
    a click is a press that is not motion.
    """

    button: str
    x: int
    y: int
    pressed: bool = True
    motion: bool = False
    shift: bool = False
    alt: bool = False
    ctrl: bool = False


# Internal marker for ESC[200~; it never leaves the decoder.
_PASTE_START = Key("paste-start")


def _single_byte_key(byte: int) -> Key:
    if byte == CARRIAGE_RETURN:
        return Key(ENTER)
    if byte == HORIZONTAL_TAB:
        return Key(TAB)
    if byte in (DELETE_BYTE, BACKSPACE_BYTE):
        return Key(BACKSPACE)
    if byte == LINE_FEED:
        return Key(CHAR, "j", ctrl=True)
    if 0x01 <= byte <= 0x1A:
        return Key(CHAR, chr(byte + 0x60), ctrl=True)
    if byte == 0x00 or 0x1C <= byte <= 0x1F:
        # Ctrl+@ (also what Ctrl+Space sends), Ctrl+\, Ctrl+], Ctrl+^, Ctrl+_
        return Key(CHAR, chr(byte + 0x40), ctrl=True)
    return Key(CHAR, chr(byte))


def _utf8_length(lead: int) -> int:
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return 0


def _decode_utf8(data: bytes, start: int, final: bool) -> tuple[Key | None, int]:
    length = _utf8_length(data[start])
    if not length:
        return Key(UNKNOWN), start + 1
    available = min(len(data), start + length)
    for index in range(start + 1, available):
        if not 0x80 <= data[index] <= 0xBF:
            # The byte that broke the sequence starts the next key.
            return Key(UNKNOWN), index
    if available < start + length:
        return (Key(UNKNOWN), len(data)) if final else (None, start)
    try:
        text = data[start : start + length].decode("utf-8")
    except UnicodeDecodeError:  # overlong form or surrogate: well framed, not a character
        return Key(UNKNOWN), start + length
    return Key(CHAR, text), start + length


def _modifiers(parameter: str) -> dict[str, bool] | None:
    if not parameter:
        return {}
    if not (parameter.isascii() and parameter.isdigit()) or int(parameter) < 1:
        return None
    bits = int(parameter) - 1
    return {"shift": bool(bits & _SHIFT_BIT), "alt": bool(bits & _ALT_BIT), "ctrl": bool(bits & _CTRL_BIT)}


def _digits(field: str) -> bool:
    return field.isascii() and field.isdigit()


def _protocol_modifiers(field: str) -> dict[str, bool] | None:
    """`<mods>[:<event>]` of a CSI u or modifyOtherKeys key; None when it is no key press `Key` can carry."""
    mods, _, event = field.partition(":")
    if event not in _PRESS_EVENTS or not _digits(mods) or int(mods) < 1:
        return None
    bits = (int(mods) - 1) & ~_LOCK_BITS
    if bits & ~_READ_BITS:
        return None
    return {"shift": bool(bits & _SHIFT_BIT), "alt": bool(bits & _ALT_BIT), "ctrl": bool(bits & _CTRL_BIT)}


def _code_text(field: str) -> str:
    """The character a code point field names; "" when it is empty or names nothing printable."""
    if not _digits(field) or int(field) > 0x10FFFF:
        return ""
    text = chr(int(field))
    return text if text.isprintable() else ""


def _protocol_key(code_field: str, modifier_field: str) -> Key:
    """A kitty CSI u or xterm modifyOtherKeys key, as the `Key` its legacy bytes give.

    `code_field` is `<code>[:<shifted>[:<base>]]`. Enter, Tab, Esc and
    Backspace keep every modifier, which is how Ctrl+Enter and Shift+Enter
    exist at all. A Ctrl chord on a letter or on `@[\\]^_` and space is what
    its C0 byte decodes to (Ctrl+C, Ctrl+J a newline, Ctrl+M Enter, Ctrl+[
    Esc), and Shift on a printable key is in its character (Shift+a is "A"),
    as a legacy terminal sends them.
    """
    codes = code_field.split(":")
    flags = _protocol_modifiers(modifier_field)
    if flags is None or len(codes) > 3 or not _digits(codes[0]) or any(part and not _digits(part) for part in codes):
        return Key(UNKNOWN)
    code = int(codes[0])
    if code in _PROTOCOL_KEYS:
        return Key(_PROTOCOL_KEYS[code], **flags)
    text = _KEYPAD_TEXT.get(code) or _code_text(codes[0])
    if not text:
        return Key(UNKNOWN)
    lowered = text.lower()
    if flags["ctrl"] and (lowered.isascii() and lowered.isalpha() or text in _CTRL_PUNCTUATION):
        control = ord(lowered) & 0x1F
        key = Key(ESCAPE) if control == ESC_BYTE else _single_byte_key(control)
        return replace(key, alt=True) if flags["alt"] else key
    if flags["shift"]:
        upper = text.upper()
        text = _code_text(codes[1] if len(codes) > 1 else "") or (upper if len(upper) == 1 else text)
    return Key(CHAR, text, ctrl=flags["ctrl"], alt=flags["alt"])


def _sgr_mouse(parameters: str, pressed: bool) -> Key | Mouse:
    """`b;x;y` of an SGR mouse report; one unknown key when it is malformed or names a button `Mouse` does not."""
    fields = parameters.split(";")
    if len(fields) != 3 or not all(_digits(field) for field in fields):
        return Key(UNKNOWN)
    code, x, y = (int(field) for field in fields)
    button = _MOUSE_BUTTONS.get(code & ~_MOUSE_FLAG_BITS)
    if button is None or x < 1 or y < 1:
        return Key(UNKNOWN)
    return Mouse(
        button,
        x,
        y,
        pressed=pressed,
        motion=bool(code & _MOUSE_MOTION_BIT),
        shift=bool(code & _MOUSE_SHIFT_BIT),
        alt=bool(code & _MOUSE_ALT_BIT),
        ctrl=bool(code & _MOUSE_CTRL_BIT),
    )


def _reported(key: Key) -> Key:
    """A key decoded from a CSI u or modifyOtherKeys report, marked as such; an unknown one is not marked."""
    return key if key.name == UNKNOWN else replace(key, protocol=True)


def _csi_key(parameters: str, final: str) -> Key | Mouse:
    if parameters.startswith(SGR_MOUSE_PREFIX) and final in (SGR_MOUSE_PRESS, SGR_MOUSE_RELEASE):
        return _sgr_mouse(parameters[len(SGR_MOUSE_PREFIX) :], final == SGR_MOUSE_PRESS)
    fields = parameters.split(";")
    if final == CSI_U_FINAL and len(fields) <= 2:
        return _reported(_protocol_key(fields[0], fields[1] if len(fields) == 2 else "1"))
    if final == "~" and len(fields) == 3 and fields[0] == MODIFY_OTHER_KEYS_PARAMETER and ":" not in parameters:
        return _reported(_protocol_key(fields[2], fields[1]))
    if len(fields) > 2:
        return Key(UNKNOWN)
    modifier = fields[1] if len(fields) == 2 else ""
    if final == "~":
        if parameters == PASTE_START_PARAMETER:
            return _PASTE_START
        name = _CSI_TILDE_KEYS.get(fields[0])
    elif final == "Z" and not parameters:
        return Key(TAB, shift=True)
    elif final in _CSI_LETTER_KEYS and fields[0] in ("", "1"):
        name = _CSI_LETTER_KEYS[final]
    else:
        name = None
    flags = _modifiers(modifier)
    if name is None or flags is None:
        return Key(UNKNOWN)
    return Key(name, **flags)


def _decode_x10_mouse(data: bytes, start: int, final: bool) -> tuple[Key | None, int]:
    """`ESC[M` and its three payload bytes as one unknown key, so none of them is typed as text.

    Every payload byte is 32 or more; a control byte among them (an ESC
    starting the next sequence) means this `ESC[M` carries no payload, and the
    bytes after it decode on their own, as Hermes' own TUI reads them
    (hermes-ink `termio/tokenize.ts`).
    """
    payload_start = start + 3
    payload = data[payload_start : payload_start + _X10_MOUSE_PAYLOAD_BYTES]
    if any(byte < 0x20 for byte in payload):
        return Key(UNKNOWN), payload_start
    if len(payload) < _X10_MOUSE_PAYLOAD_BYTES and not final:
        return None, start
    return Key(UNKNOWN), payload_start + len(payload)


def _decode_csi(data: bytes, start: int, final: bool) -> tuple[Key | Mouse | None, int]:
    index = start + 2  # past ESC [
    while index < len(data):
        byte = data[index]
        if byte == X10_MOUSE_FINAL and index == start + 2:
            return _decode_x10_mouse(data, start, final)
        if 0x40 <= byte <= 0x7E:
            return _csi_key(data[start + 2 : index].decode("ascii"), chr(byte)), index + 1
        if not 0x20 <= byte <= 0x3F or index - start >= _MAX_CSI_LENGTH:
            return Key(UNKNOWN), index
        index += 1
    if not final:
        return None, start
    if len(data) == start + 2:
        return Key(CHAR, "[", alt=True), start + 2
    return Key(UNKNOWN), len(data)


def _decode_ss3(data: bytes, start: int, final: bool) -> tuple[Key | None, int]:
    if len(data) < start + 3:
        return (Key(CHAR, "O", alt=True), start + 2) if final else (None, start)
    byte = data[start + 2]
    if not 0x40 <= byte <= 0x7E:
        return Key(CHAR, "O", alt=True), start + 2
    name = _SS3_KEYS.get(chr(byte))
    return (Key(name) if name else Key(UNKNOWN)), start + 3


def _decode_escape(data: bytes, start: int, final: bool) -> tuple[Key | Mouse | None, int]:
    if len(data) == start + 1:
        return (Key(ESCAPE), start + 1) if final else (None, start)
    second = data[start + 1]
    if second == CSI_BYTE:
        return _decode_csi(data, start, final)
    if second == SS3_BYTE:
        return _decode_ss3(data, start, final)
    if second == ESC_BYTE:
        return Key(ESCAPE), start + 1
    key, end = _decode_plain(data, start + 1, final)
    if key is None:
        return None, start
    if key.name == UNKNOWN:
        return key, end
    return replace(key, alt=True), end


def _decode_plain(data: bytes, start: int, final: bool) -> tuple[Key | None, int]:
    byte = data[start]
    if byte < 0x80:
        return _single_byte_key(byte), start + 1
    return _decode_utf8(data, start, final)


def _decode_one(data: bytes, start: int, final: bool) -> tuple[Key | Mouse | None, int]:
    """The key at `start` and the index after it; `(None, start)` means "wait for more"."""
    if data[start] == ESC_BYTE:
        return _decode_escape(data, start, final)
    return _decode_plain(data, start, final)


def _paste_text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


class KeyDecoder:
    def __init__(self) -> None:
        self._pending = bytearray()
        self._paste: bytearray | None = None
        # True from the first key that arrived as a CSI u or modifyOtherKeys report: the terminal does
        # send them, so Ctrl+Enter reaches the caller apart from Enter. Asking for them proves nothing.
        self.protocol_reported = False

    def feed(self, data: bytes) -> list[Key | Paste | Mouse]:
        self._pending.extend(data)
        return self._drain(final=False)

    def flush(self) -> list[Key | Paste | Mouse]:
        """Settle what is held after a silence: a lone ESC becomes `Key("escape")`."""
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[Key | Paste | Mouse]:
        events: list[Key | Paste | Mouse] = []
        while True:
            if self._paste is not None:
                paste = self._take_paste()
                if paste is None:
                    return events
                events.append(paste)
            data = bytes(self._pending)
            index = 0
            while index < len(data) and self._paste is None:
                key, end = _decode_one(data, index, final)
                if key is None:
                    break
                index = end
                if key is _PASTE_START:
                    self._paste = bytearray()
                else:
                    self.protocol_reported = self.protocol_reported or (isinstance(key, Key) and key.protocol)
                    events.append(key)
            del self._pending[:index]
            if self._paste is None:
                return events

    def _take_paste(self) -> Paste | None:
        """Move held bytes into the open paste; the finished `Paste` once its end marker arrived."""
        body = self._paste
        if body is None:
            return None
        # Only the tail can hold a marker split across the previous chunk.
        searched = max(0, len(body) - len(PASTE_END) + 1)
        body.extend(self._pending)
        self._pending.clear()
        end = body.find(PASTE_END, searched)
        if end < 0:
            return None
        self._pending.extend(body[end + len(PASTE_END) :])
        self._paste = None
        return Paste(_paste_text(bytes(body[:end])))
