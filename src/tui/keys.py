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
"""

from __future__ import annotations

from dataclasses import dataclass, replace

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


@dataclass(frozen=True)
class Key:
    name: str
    char: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


@dataclass(frozen=True)
class Paste:
    text: str


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


def _csi_key(parameters: str, final: str) -> Key:
    fields = parameters.split(";")
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


def _decode_csi(data: bytes, start: int, final: bool) -> tuple[Key | None, int]:
    index = start + 2  # past ESC [
    while index < len(data):
        byte = data[index]
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


def _decode_escape(data: bytes, start: int, final: bool) -> tuple[Key | None, int]:
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


def _decode_one(data: bytes, start: int, final: bool) -> tuple[Key | None, int]:
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

    def feed(self, data: bytes) -> list[Key | Paste]:
        self._pending.extend(data)
        return self._drain(final=False)

    def flush(self) -> list[Key | Paste]:
        """Settle what is held after a silence: a lone ESC becomes `Key("escape")`."""
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[Key | Paste]:
        events: list[Key | Paste] = []
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
