"""The OMH TUI's incremental key decoder (`omh.tui.keys`).

Input arrives in whatever chunks the terminal and the kernel choose, so every
multi-byte form is also fed split at each possible point: the decoder must
produce the same keys as from one chunk and never half a key.
"""

from __future__ import annotations

import random
import unittest

from omh.tui.keys import Key, KeyDecoder, Paste


def decode(data: bytes) -> list:
    decoder = KeyDecoder()
    return decoder.feed(data) + decoder.flush()


def decode_split_everywhere(test: unittest.TestCase, data: bytes) -> list:
    """Decode `data` whole, then split at every offset; all must agree."""
    whole = decode(data)
    for cut in range(1, len(data)):
        decoder = KeyDecoder()
        events = decoder.feed(data[:cut]) + decoder.feed(data[cut:]) + decoder.flush()
        test.assertEqual(events, whole, f"split at byte {cut} of {data!r}")
    return whole


def char(text: str, **modifiers: bool) -> Key:
    return Key("char", text, **modifiers)


class PlainKeyTests(unittest.TestCase):
    def test_printable_ascii(self) -> None:
        self.assertEqual(decode(b"aZ ~"), [char("a"), char("Z"), char(" "), char("~")])

    def test_enter_tab_backspace(self) -> None:
        self.assertEqual(decode(b"\r"), [Key("enter")])
        self.assertEqual(decode(b"\t"), [Key("tab")])
        self.assertEqual(decode(b"\x7f"), [Key("backspace")])
        self.assertEqual(decode(b"\x08"), [Key("backspace")])

    def test_line_feed_is_ctrl_j_not_enter(self) -> None:
        self.assertEqual(decode(b"\n"), [char("j", ctrl=True)])

    def test_ctrl_letters(self) -> None:
        self.assertEqual(decode(b"\x01"), [char("a", ctrl=True)])
        self.assertEqual(decode(b"\x03"), [char("c", ctrl=True)])  # raw mode: Ctrl+C is a key
        self.assertEqual(decode(b"\x04"), [char("d", ctrl=True)])
        self.assertEqual(decode(b"\x1a"), [char("z", ctrl=True)])

    def test_ctrl_punctuation(self) -> None:
        self.assertEqual(decode(b"\x00"), [char("@", ctrl=True)])  # also Ctrl+Space
        self.assertEqual(decode(b"\x1c\x1d\x1f"), [char("\\", ctrl=True), char("]", ctrl=True), char("_", ctrl=True)])


class Utf8Tests(unittest.TestCase):
    def test_multibyte_characters(self) -> None:
        self.assertEqual(decode("é한😀".encode()), [char("é"), char("한"), char("😀")])

    def test_a_character_split_across_chunks_waits_for_its_tail(self) -> None:
        data = "한".encode()
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed(data[:1]), [])
        self.assertEqual(decoder.feed(data[1:2]), [])
        self.assertEqual(decoder.feed(data[2:]), [char("한")])

    def test_every_split_of_mixed_text_agrees(self) -> None:
        events = decode_split_everywhere(self, "a한😀b".encode())
        self.assertEqual(events, [char("a"), char("한"), char("😀"), char("b")])

    def test_invalid_bytes_become_unknown_and_decoding_continues(self) -> None:
        self.assertEqual(decode(b"\xffa"), [Key("unknown"), char("a")])
        self.assertEqual(decode(b"\x80a"), [Key("unknown"), char("a")])
        # A lead byte whose sequence is broken: the breaking byte is decoded on its own.
        self.assertEqual(decode(b"\xe1\x84a"), [Key("unknown"), char("a")])
        # Well framed but overlong.
        self.assertEqual(decode(b"\xe0\x80\x80a"), [Key("unknown"), char("a")])

    def test_an_unfinished_character_is_settled_by_flush(self) -> None:
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed("한".encode()[:2]), [])
        self.assertEqual(decoder.flush(), [Key("unknown")])
        self.assertEqual(decoder.feed(b"a"), [char("a")])


class EscapeSequenceTests(unittest.TestCase):
    def test_csi_and_ss3_arrows(self) -> None:
        for prefix in (b"\x1b[", b"\x1bO"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    decode(prefix + b"A" + prefix + b"B" + prefix + b"C" + prefix + b"D"),
                    [Key("up"), Key("down"), Key("right"), Key("left")],
                )

    def test_home_and_end_in_every_form(self) -> None:
        self.assertEqual(decode(b"\x1b[H\x1b[F\x1bOH\x1bOF"), [Key("home"), Key("end"), Key("home"), Key("end")])
        self.assertEqual(decode(b"\x1b[1~\x1b[4~\x1b[7~\x1b[8~"), [Key("home"), Key("end"), Key("home"), Key("end")])

    def test_editing_and_paging_keys(self) -> None:
        self.assertEqual(
            decode(b"\x1b[2~\x1b[3~\x1b[5~\x1b[6~"),
            [Key("insert"), Key("delete"), Key("pageup"), Key("pagedown")],
        )

    def test_modifier_forms(self) -> None:
        self.assertEqual(decode(b"\x1b[1;5C"), [Key("right", ctrl=True)])
        self.assertEqual(decode(b"\x1b[1;2A"), [Key("up", shift=True)])
        self.assertEqual(decode(b"\x1b[1;3D"), [Key("left", alt=True)])
        self.assertEqual(decode(b"\x1b[1;8B"), [Key("down", shift=True, alt=True, ctrl=True)])
        self.assertEqual(decode(b"\x1b[3;5~"), [Key("delete", ctrl=True)])
        self.assertEqual(decode(b"\x1b[1;5H"), [Key("home", ctrl=True)])

    def test_shift_tab(self) -> None:
        self.assertEqual(decode(b"\x1b[Z"), [Key("tab", shift=True)])

    def test_alt_chords(self) -> None:
        self.assertEqual(decode(b"\x1bb"), [char("b", alt=True)])
        self.assertEqual(decode(b"\x1bB"), [char("B", alt=True)])
        self.assertEqual(decode(b"\x1b\r"), [Key("enter", alt=True)])
        self.assertEqual(decode(b"\x1b\x7f"), [Key("backspace", alt=True)])
        self.assertEqual(decode("\x1b한".encode()), [char("한", alt=True)])

    def test_every_split_of_an_escape_sequence_agrees(self) -> None:
        for data in (b"\x1b[1;5C", b"\x1b[3~", b"\x1bOA", b"\x1b[Z", "\x1b한".encode(), b"x\x1b[Ay"):
            with self.subTest(data=data):
                decode_split_everywhere(self, data)

    def test_unknown_sequences_never_raise(self) -> None:
        self.assertEqual(decode(b"\x1b[99~"), [Key("unknown")])
        self.assertEqual(decode(b"\x1b[1;5X"), [Key("unknown")])
        self.assertEqual(decode(b"\x1b[5A"), [Key("unknown")])
        self.assertEqual(decode(b"\x1b[1;2;3A"), [Key("unknown")])
        self.assertEqual(decode(b"\x1bOP"), [Key("unknown")])  # F1: not bound
        self.assertEqual(decode(b"\x1b[201~"), [Key("unknown")])  # a paste end with no start

    def test_a_broken_sequence_hands_its_breaking_byte_on(self) -> None:
        self.assertEqual(decode(b"\x1b[1\x03"), [Key("unknown"), char("c", ctrl=True)])

    def test_a_runaway_sequence_is_cut_off(self) -> None:
        events = decode(b"\x1b[" + b"1" * 100 + b"A")
        self.assertEqual(events[0], Key("unknown"))
        self.assertEqual(events[-1], char("A"))


class LoneEscapeTests(unittest.TestCase):
    def test_a_lone_escape_waits_for_flush(self) -> None:
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed(b"\x1b"), [])
        self.assertEqual(decoder.flush(), [Key("escape")])
        self.assertEqual(decoder.flush(), [])

    def test_an_escape_that_turns_out_to_start_a_sequence(self) -> None:
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed(b"\x1b"), [])
        self.assertEqual(decoder.feed(b"[A"), [Key("up")])

    def test_double_escape_is_two_escapes(self) -> None:
        self.assertEqual(decode(b"\x1b\x1b"), [Key("escape"), Key("escape")])

    def test_escape_bracket_or_o_at_a_silence_is_an_alt_chord(self) -> None:
        self.assertEqual(decode(b"\x1b["), [char("[", alt=True)])
        self.assertEqual(decode(b"\x1bO"), [char("O", alt=True)])

    def test_an_unfinished_csi_at_a_silence_is_unknown(self) -> None:
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed(b"\x1b[1;"), [])
        self.assertEqual(decoder.flush(), [Key("unknown")])


class BracketedPasteTests(unittest.TestCase):
    def test_a_paste_is_one_event(self) -> None:
        self.assertEqual(decode(b"\x1b[200~hello world\x1b[201~"), [Paste("hello world")])

    def test_keys_around_a_paste_keep_their_order(self) -> None:
        self.assertEqual(
            decode(b"a\x1b[200~mid\x1b[201~\r"),
            [char("a"), Paste("mid"), Key("enter")],
        )

    def test_carriage_returns_inside_a_paste_become_newlines(self) -> None:
        self.assertEqual(decode(b"\x1b[200~one\r\ntwo\rthree\nfour\x1b[201~"), [Paste("one\ntwo\nthree\nfour")])

    def test_escape_sequences_inside_a_paste_are_text(self) -> None:
        self.assertEqual(decode(b"\x1b[200~x\x1b[Ay\x03\x1b[201~"), [Paste("x\x1b[Ay\x03")])

    def test_a_paste_spanning_chunks(self) -> None:
        data = "\x1b[200~줄 하나\r\n줄 둘\x1b[201~k".encode()
        events = decode_split_everywhere(self, data)
        self.assertEqual(events, [Paste("줄 하나\n줄 둘"), char("k")])

    def test_flush_does_not_end_an_open_paste(self) -> None:
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed(b"\x1b[200~par"), [])
        self.assertEqual(decoder.flush(), [])
        self.assertEqual(decoder.feed(b"tial\x1b[201~"), [Paste("partial")])

    def test_an_empty_paste(self) -> None:
        self.assertEqual(decode(b"\x1b[200~\x1b[201~"), [Paste("")])


def csi_u(code: int, modifiers: int | None = None) -> bytes:
    return f"\x1b[{code}u".encode() if modifiers is None else f"\x1b[{code};{modifiers}u".encode()


def modify_other_keys(code: int, modifiers: int) -> bytes:
    return f"\x1b[27;{modifiers};{code}~".encode()


SHIFT, ALT, CTRL = 2, 3, 5  # the modifier parameter: 1 + (shift 1 | alt 2 | ctrl 4)


class KeyboardProtocolTests(unittest.TestCase):
    """kitty CSI u and xterm modifyOtherKeys, which `Terminal` asks for on some terminals."""

    def assert_same_key(self, report: bytes, legacy: bytes) -> None:
        expected = decode(legacy)
        self.assertEqual(len(expected), 1, legacy)
        self.assertEqual(decode(report), expected, f"{report!r} must decode as {legacy!r} does")

    def test_every_ctrl_letter_decodes_as_its_control_byte(self) -> None:
        # Ctrl+C stays an interrupt, Ctrl+J a newline, and Ctrl+H, Ctrl+I, Ctrl+M stay
        # Backspace, Tab and Enter, exactly as without the protocol.
        for letter in "abcdefghijklmnopqrstuvwxyz":
            control = bytes([ord(letter) - 0x60])
            for report in (csi_u(ord(letter), CTRL), modify_other_keys(ord(letter), CTRL)):
                with self.subTest(report=report):
                    self.assert_same_key(report, control)
        self.assert_same_key(csi_u(ord("c"), CTRL), b"\x03")
        self.assert_same_key(modify_other_keys(ord("C"), CTRL + 1), b"\x03")  # xterm may send the shifted code

    def test_the_keys_legacy_bytes_already_have_decode_to_the_same_key(self) -> None:
        cases = {
            "Esc": (csi_u(27), b"\x1b"),
            "Enter": (csi_u(13), b"\r"),
            "Tab": (csi_u(9), b"\t"),
            "Shift+Tab": (csi_u(9, SHIFT), b"\x1b[Z"),
            "Shift+Tab (modifyOtherKeys)": (modify_other_keys(9, SHIFT), b"\x1b[Z"),
            "Backspace": (csi_u(127), b"\x7f"),
            "Alt+Backspace": (csi_u(127, ALT), b"\x1b\x7f"),
            "Alt+Enter": (csi_u(13, ALT), b"\x1b\r"),
            "Alt+b": (csi_u(ord("b"), ALT), b"\x1bb"),
            "Alt+b (modifyOtherKeys)": (modify_other_keys(ord("b"), ALT), b"\x1bb"),
            "Alt+Shift+b": (csi_u(ord("b"), ALT + 1), b"\x1bB"),
            "Ctrl+Alt+c": (csi_u(ord("c"), CTRL + 2), b"\x1b\x03"),
            "Ctrl+[ is Esc": (csi_u(ord("["), CTRL), b"\x1b"),
            "Ctrl+[ is Esc (modifyOtherKeys)": (modify_other_keys(ord("["), CTRL), b"\x1b"),
            "Ctrl+space": (csi_u(32, CTRL), b"\x00"),
            "Ctrl+@": (csi_u(ord("@"), CTRL), b"\x00"),
            "Ctrl+\\": (csi_u(ord("\\"), CTRL), b"\x1c"),
            "Ctrl+]": (csi_u(ord("]"), CTRL), b"\x1d"),
            "Ctrl+^": (csi_u(ord("^"), CTRL), b"\x1e"),
            "Ctrl+_": (csi_u(ord("_"), CTRL), b"\x1f"),
            "a": (csi_u(ord("a")), b"a"),
            "Shift+a": (csi_u(ord("a"), SHIFT), b"A"),
            "Shift+a (shifted key reported)": (b"\x1b[97:65;2u", b"A"),
            "Shift+1 (shifted key reported)": (b"\x1b[49:33;2u", b"!"),
            "Shift+a (modifyOtherKeys)": (modify_other_keys(ord("A"), SHIFT), b"A"),
            "Shift+space": (csi_u(32, SHIFT), b" "),
            "Shift+non-ASCII letter": (csi_u(ord("ф"), SHIFT), "Ф".encode()),
            "Ctrl+c with caps lock": (csi_u(ord("c"), 1 + 4 + 64), b"\x03"),
            "Ctrl+c with num lock": (csi_u(ord("c"), 1 + 4 + 128), b"\x03"),
            "Ctrl+c, press event": (b"\x1b[99;5:1u", b"\x03"),
            "Ctrl+c, repeat event": (b"\x1b[99;5:2u", b"\x03"),
            "Ctrl+c, base layout key": (b"\x1b[99::99;5u", b"\x03"),
            "keypad Enter": (csi_u(57414), b"\r"),
            "keypad 1": (csi_u(57400), b"1"),
            "keypad +": (csi_u(57413), b"+"),
        }
        for name, (report, legacy) in cases.items():
            with self.subTest(name):
                self.assert_same_key(report, legacy)

    def test_ctrl_enter_and_shift_enter_are_keys_of_their_own(self) -> None:
        for report in (b"\x1b[13;5u", b"\x1b[27;5;13~"):
            with self.subTest(report=report):
                self.assertEqual(decode(report), [Key("enter", ctrl=True)])
        for report in (b"\x1b[13;2u", b"\x1b[27;2;13~"):
            with self.subTest(report=report):
                self.assertEqual(decode(report), [Key("enter", shift=True)])
        self.assertNotEqual(Key("enter", ctrl=True), Key("enter"))
        self.assertNotEqual(Key("enter", shift=True), Key("enter"))

    def test_every_split_of_a_report_agrees(self) -> None:
        for data in (b"\x1b[13;5u", b"\x1b[27;5;13~", b"\x1b[99;5u", b"\x1b[97:65;2u", b"\x1b[99;5:2u", b"x\x1b[27uy"):
            with self.subTest(data=data):
                decode_split_everywhere(self, data)

    def test_a_report_split_across_two_reads_waits_for_its_final_byte(self) -> None:
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed(b"\x1b[13;"), [])
        self.assertEqual(decoder.feed(b"5u"), [Key("enter", ctrl=True)])
        self.assertEqual(decoder.feed(b"\x1b[27;5;"), [])
        self.assertEqual(decoder.feed(b"99~"), [char("c", ctrl=True)])

    def test_malformed_and_unknown_reports_are_one_unknown_key_and_no_text(self) -> None:
        for data in (
            b"\x1b[99;9u",  # super: no place in Key, so not Ctrl or plain c
            b"\x1b[99;33u",  # meta
            b"\x1b[99;5:3u",  # a release is not a key
            b"\x1b[99;5:4u",  # no such event type
            b"\x1b[99;5:1:2u",
            b"\x1b[99;0u",
            b"\x1b[99;u",
            b"\x1b[;5u",
            b"\x1b[u",
            b"\x1b[99;5;99u",  # associated text: never asked for
            b"\x1b[99:65:99:1u",
            b"\x1b[99:=;2u",  # a sub-field that is not a number
            b"\x1b[1114112u",  # past the last code point
            b"\x1b[55296u",  # a surrogate
            b"\x1b[0u",
            b"\x1b[57441u",  # a modifier key on its own (kitty private use code point)
            b"\x1b[?1u",  # a keyboard-flags reply, not a key
            b"\x1b[>1u",
            b"\x1b[27;9;99~",
            b"\x1b[27;5;99:67~",
            b"\x1b[27;5;0~",
            b"\x1b[27;0;99~",
            b"\x1b[27;;99~",
        ):
            with self.subTest(data=data):
                self.assertEqual(decode(data), [Key("unknown")])
        self.assertEqual(decode(b"\x1b[99;9ua"), [Key("unknown"), char("a")])

    def test_any_parameters_with_a_u_final_are_exactly_one_key(self) -> None:
        # Whatever digits, colons and semicolons arrive, the report is one key: no byte of it is left over as text.
        generator = random.Random(2095)
        for _ in range(2000):
            parameters = "".join(generator.choice("0123456789:;") for _ in range(generator.randrange(0, 20)))
            data = f"\x1b[{parameters}u".encode()
            with self.subTest(data=data):
                self.assertEqual(len(decode(data)), 1)


class RobustnessTests(unittest.TestCase):
    def test_random_bytes_in_random_chunks_never_raise(self) -> None:
        generator = random.Random(2084)
        alphabet = [bytes([value]) for value in range(256)] + [b"\x1b[", b"\x1b[200~", b"\x1b[201~", b"\x1bO"]
        for _ in range(500):
            decoder = KeyDecoder()
            for _ in range(generator.randrange(1, 8)):
                chunk = b"".join(generator.choice(alphabet) for _ in range(generator.randrange(0, 12)))
                for event in decoder.feed(chunk):
                    self.assertIsInstance(event, (Key, Paste))
            for event in decoder.flush():
                self.assertIsInstance(event, (Key, Paste))


if __name__ == "__main__":
    unittest.main()
