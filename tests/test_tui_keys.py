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
