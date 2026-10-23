"""The aura a `dag-ulw` in the composer draws (owner request 2026-10-23, after Claude Code's `ultrathink`).

The token moves through a purple gradient a step a tick and the composer's rules take the same gradient, only while
the draft holds it, at the truecolor depth with motion on; the rows, the caret and the cells stay the draft's own.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.tui.app import IDLE_READ_SECONDS, TICK_SECONDS  # noqa: E402
from omh.tui.aura import aura_ranges  # noqa: E402
from omh.tui.composer import Composer  # noqa: E402
from omh.tui.frame import RULE, build_frame  # noqa: E402
from omh.tui.keys import BACKSPACE, ENTER, Key, Paste  # noqa: E402
from omh.tui.style import COLOR_16, COLOR_256, NO_COLOR, TRUECOLOR, Span  # noqa: E402
from omh.tui.theme import DEFAULT_THEME, without_colour  # noqa: E402
from omh.tui.width import line_width  # noqa: E402
from test_tui_modes import make_app, type_text  # noqa: E402

KNOWN = frozenset({"dag-ulw", "plan"})


def composer_with(text: str) -> Composer:
    composer = Composer()
    composer.replace(0, 0, text)
    return composer


def frame_for(text: str, *, aura: int = 0, depth: str = TRUECOLOR, theme=DEFAULT_THEME, cols: int = 80):
    return build_frame(cols=cols, rows=24, composer=composer_with(text), info=(), theme=theme, known_slash=KNOWN,
                       aura=aura, aura_depth=depth)


def rows_of(frame) -> tuple[tuple[Span, ...], tuple[Span, ...], tuple[Span, ...]]:
    """The top rule, the composer's first row and the bottom rule."""
    lines = list(frame.lines)
    top = next(index for index, line in enumerate(lines) if line and line[0].text.startswith(RULE))
    return lines[top], lines[top + 1], lines[top + 2]


def styles_over(line: tuple[Span, ...], text: str, word: str) -> list:
    """The style of each character of `word`'s first occurrence on `line` (whose text is `text`)."""
    at = text.index(word)
    styles, cell = [], 0
    for span in line:
        for char in span.text:
            if at <= cell < at + len(word):
                styles.append(span.style)
            cell += 1
    return styles


def text_of(line: tuple[Span, ...]) -> str:
    return "".join(span.text for span in line)


class KeywordTests(unittest.TestCase):
    def test_the_token_whole_in_any_case_and_nothing_that_goes_on_from_it(self) -> None:
        for text, found in (("dag-ulw", ["dag-ulw"]), ("/dag-ulw migrate every service", ["/dag-ulw"]),
                            ("run DAG ULW now", ["DAG ULW"]), ("(dag-ulw), then Dag-Ulw.", ["dag-ulw", "Dag-Ulw"])):
            with self.subTest(text=text):
                self.assertEqual([text[start:end] for start, end in aura_ranges(text)], found)
        for text in ("dag-ulwx", "undag ulw", "src/dag-ulw/plan.md", "dag_ulw", "dagulw", "dag-ulw-plan", "my-dag-ulw"):
            with self.subTest(text=text):
                self.assertEqual(aura_ranges(text), [])


class DrawTests(unittest.TestCase):
    def test_the_token_takes_the_gradient_a_step_a_cell_over_its_slash_colour(self) -> None:
        top, row, bottom = rows_of(frame_for("please /dag-ulw migrate then /plan"))
        text = text_of(row)
        lit = styles_over(row, text, "/dag-ulw")
        self.assertEqual(lit, list(DEFAULT_THEME.aura[:8]), "one step a cell from the first, bold")
        self.assertTrue(all(style.bold for style in lit))
        self.assertEqual(styles_over(row, text, "/plan"), [DEFAULT_THEME.slash] * 5, "another /name keeps its colour")
        self.assertNotIn(DEFAULT_THEME.slash, styles_over(row, text, "migrate"))

    def test_the_rules_take_the_gradient_at_a_rules_contrast_in_the_same_cells(self) -> None:
        plain_top, plain_row, plain_bottom = rows_of(frame_for("migrate every service", cols=83))
        top, row, bottom = rows_of(frame_for("dag ulw migrate every service", cols=83))
        for rule, plain in ((top, plain_top), (bottom, plain_bottom)):
            self.assertEqual(text_of(rule), text_of(plain), "the same rule characters, full width")
            self.assertEqual({span.style for span in plain}, {DEFAULT_THEME.rule})
            self.assertTrue({span.style for span in rule} <= set(DEFAULT_THEME.aura_rule))
            self.assertGreater(len({span.style for span in rule}), 4, "a gradient, not one colour")
        self.assertEqual(len(frame_for("dag ulw migrate").lines), len(frame_for("migrate").lines), "no row added")

    def test_it_reads_on_a_dark_and_a_light_background(self) -> None:
        from omh.tui.theme import build_theme, contrast_ratio, default_theme, surface_for

        for background in ("#0D1117", "#FFFFFF", "#3A3A3A", "#DDDDDD"):
            with self.subTest(background=background):
                theme = build_theme(default_theme(), background)
                surface = surface_for(default_theme(), background)
                self.assertGreaterEqual(min(contrast_ratio(style.fg, surface) for style in theme.aura), 4.5)
                self.assertGreaterEqual(min(contrast_ratio(style.fg, surface) for style in theme.aura_rule), 3.0)
                self.assertEqual(len(theme.aura), 16, "nine steps there and seven back: it wraps without a seam")

    def test_the_gradient_moves_one_step_a_phase(self) -> None:
        first, second = (rows_of(frame_for("dag-ulw", aura=phase)) for phase in (0, 1))
        styles = [styles_over(row, text_of(row), "dag-ulw") for _top, row, _bottom in (first, second)]
        self.assertEqual(styles[1][1:], styles[0][:-1])
        self.assertNotEqual(first[0], second[0], "the rule moves with it")

    def test_256_and_16_colours_paint_it_at_their_depth_and_no_colour_leaves_it_bold(self) -> None:
        for depth, code in ((COLOR_256, "38;5;"), (COLOR_16, "")):
            with self.subTest(depth=depth):
                top, row, _bottom = rows_of(frame_for("dag-ulw", depth=depth))
                lit = styles_over(row, text_of(row), "dag-ulw")
                self.assertEqual({style.depth for style in lit} | {span.style.depth for span in top}, {depth})
                self.assertIn(code, lit[0].sgr())
                self.assertNotIn("38;2;", lit[0].sgr())
        plain = without_colour(DEFAULT_THEME)
        top, row, bottom = rows_of(frame_for("dag-ulw", theme=plain, depth=NO_COLOR))
        lit = styles_over(row, text_of(row), "dag-ulw")
        self.assertTrue(all(style.bold for style in lit))
        self.assertTrue(all("38;" not in style.sgr() for style in lit), "no colour")
        self.assertTrue(all("38;" not in span.style.sgr() for span in (*top, *bottom)))

    def test_a_masked_draft_or_one_holding_a_paste_has_none(self) -> None:
        aura = set(DEFAULT_THEME.aura) | set(DEFAULT_THEME.aura_rule)
        masked = Composer(masked=True)
        masked.replace(0, 0, "dag-ulw")
        pasted = composer_with("dag-ulw ")
        pasted.handle(Paste("\n".join(f"line {number}" for number in range(40))))
        for composer in (masked, pasted):
            frame = build_frame(cols=80, rows=24, composer=composer, info=(), theme=DEFAULT_THEME, aura=3)
            self.assertFalse([span for line in frame.lines for span in line if span.style in aura])

    def test_the_caret_and_the_cells_are_the_drafts_own(self) -> None:
        for text in ("/dag-ulw migrate every service", "한글 작업 dag ulw 서비스 이전", "x" * 70 + " dag-ulw " + "y" * 30):
            with self.subTest(text=text[:20]):
                lit = frame_for(text, cols=60)
                plain = build_frame(cols=60, rows=24, composer=composer_with(text), info=(), theme=DEFAULT_THEME)
                self.assertEqual(lit.cursor, plain.cursor)
                self.assertEqual([text_of(line) for line in lit.lines], [text_of(line) for line in plain.lines])
                self.assertTrue(all(line_width(line) <= 60 for line in lit.lines))


class AppTests(unittest.TestCase):
    @staticmethod
    def app(depth: str = TRUECOLOR, *, motion: bool = True):
        clock = [10.0]
        app, client = make_app(clock=lambda: clock[0])
        app.color_depth, app.motion = depth, motion
        return app, client, clock

    def test_it_ticks_only_while_the_token_is_in_the_draft_and_would_move(self) -> None:
        app, _client, clock = self.app()
        type_text(app, "migrate every service")
        app.last_render = app.clock()
        self.assertFalse(app._aura_live())
        self.assertEqual(app._read_timeout(), IDLE_READ_SECONDS, "no tick for a draft without it")
        app.dirty, app.last_render = False, app.clock()
        before = app.last_render
        clock[0] += 2 * TICK_SECONDS
        app._render_when_due()
        self.assertEqual(app.last_render, before, "no frame for a draft without it")
        type_text(app, " with dag ulw")
        app.last_render = app.clock()
        self.assertTrue(app._aura_live())
        self.assertAlmostEqual(app._read_timeout(), TICK_SECONDS)
        app.dirty = False
        clock[0] += 2 * TICK_SECONDS
        app._render_when_due()
        self.assertEqual(app.last_render, clock[0], "a frame a tick while it is there")
        for depth, motion in ((COLOR_256, True), (NO_COLOR, True), (TRUECOLOR, False)):
            with self.subTest(depth=depth, motion=motion):
                app.color_depth, app.motion = depth, motion
                self.assertFalse(app._aura_live(), "it would not move: nothing ticks")
                self.assertEqual(app._read_timeout(), IDLE_READ_SECONDS)

    def test_the_phase_follows_the_tick_and_stands_at_zero_when_it_does_not_move(self) -> None:
        app, _client, clock = self.app()
        type_text(app, "dag-ulw")
        frames = []
        for at in (10.0, 10.0 + 2.5 * TICK_SECONDS):
            clock[0] = at
            app._render()
            frames.append(app.region._lines)
        self.assertTrue(frames[0] != frames[1], "the gradient moved")
        app.color_depth = COLOR_256
        still = []
        for at in (20.0, 20.0 + 2.5 * TICK_SECONDS):
            clock[0] = at
            app._render()
            still.append(app.region._lines)
        self.assertTrue(still[0] == still[1], "a static gradient at 256 colours")
        aura = {style.fg for style in app.theme.aura}
        self.assertEqual({span.style.depth for line in still[1] for span in line if span.style.fg in aura}, {COLOR_256})

    def test_it_ends_the_frame_the_token_goes_or_the_prompt_is_sent(self) -> None:
        app, client, _clock = self.app()
        aura = set(app.theme.aura) | set(app.theme.aura_rule)

        def lit() -> bool:
            app._render()
            return any(span.style in aura for line in app.region._lines for span in line)

        type_text(app, "dag-ulw")
        self.assertTrue(lit())
        app._on_input(Key(BACKSPACE))
        self.assertFalse(lit(), "dag-ul is no token")
        self.assertFalse(app._aura_live())
        type_text(app, "w migrate")
        self.assertTrue(lit())
        app._on_input(Key(ENTER))
        self.assertFalse(lit(), "sent: the composer is empty")
        self.assertFalse(app._aura_live())


if __name__ == "__main__":
    unittest.main()
