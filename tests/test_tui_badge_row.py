"""The row under the info line: the session's git branch and the OMH Native badge (owner's requests of 2026-10-27)."""

from __future__ import annotations

import subprocess
import threading
import unittest

from _tui_lines import line_text
from omh.skin_pack import default_theme
from omh.tui.frame import badge_line
from omh.tui.git_status import GitState, GitWatcher, parse_status, read_status
from omh.tui.layout import BADGE_MIN_ROWS, allocate
from omh.tui.style import Span
from omh.tui.theme import (
    LAUNCH_PRESETS, TEXT_CONTRAST, build_theme, contrast_ratio, launch_palette, launch_styles, launch_wordmark,
)

PORCELAIN = """# branch.oid 9d013ddd827e803eea5b5d2d1259acad43b295d9
# branch.head omh/terminology-unification
# branch.upstream origin/omh/terminology-unification
1 .M N... 100644 100644 100644 aaa bbb src/a.py
1 M. N... 100644 100644 100644 aaa bbb src/b.py
2 R. N... 100644 100644 100644 aaa bbb R100 src/c.py\tsrc/old.py
? notes.md
? scratch/
"""


def native(look: str, background: str):
    skin = default_theme()
    palette = launch_palette(look, background)
    return launch_wordmark("Native", skin, background, palette), launch_styles(skin, background, palette).brand


class StatusTests(unittest.TestCase):
    def test_porcelain_v2_names_the_branch_and_counts_tracked_and_untracked_files(self) -> None:
        self.assertEqual(parse_status(PORCELAIN), GitState("omh/terminology-unification", 3, 2))

    def test_a_detached_head_is_its_short_sha_and_an_unborn_one_is_nothing(self) -> None:
        detached = "# branch.oid 9d013ddd827e803eea5b5d2d1259acad43b295d9\n# branch.head (detached)\n"
        self.assertEqual(parse_status(detached), GitState("9d013dd", 0, 0))
        self.assertIsNone(parse_status("# branch.oid (initial)\n# branch.head (detached)\n"))
        self.assertEqual(parse_status("# branch.oid (initial)\n# branch.head main\n"), GitState("main", 0, 0))
        self.assertIsNone(parse_status("fatal: not a git repository\n"))

    def test_a_missing_or_slow_git_or_a_directory_outside_a_repository_reads_as_nothing(self) -> None:
        def missing(*_args, **_kwargs):
            raise FileNotFoundError("git")

        def slow(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("git", 2.0)

        def outside(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")

        for run in (missing, slow, outside):
            with self.subTest(run=run.__name__):
                self.assertIsNone(read_status("/somewhere", run=run))

    def test_the_read_is_read_only_and_bounded(self) -> None:
        seen = {}

        def run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, PORCELAIN, "")

        self.assertEqual(read_status("/repo", run=run), GitState("omh/terminology-unification", 3, 2))
        self.assertEqual(seen["argv"][:5], ["git", "-c", "core.fsmonitor=false", "--no-optional-locks", "status"])
        self.assertEqual((seen["cwd"], seen["timeout"] > 0), ("/repo", True))

    def test_the_watcher_reads_off_the_caller_s_thread_and_wakes_on_a_change(self) -> None:
        changed = threading.Event()
        calls: list[str] = []

        def read(cwd: str) -> GitState:
            calls.append(threading.current_thread().name)
            return GitState("main", len(calls), 0)

        watcher = GitWatcher("/repo", read=read)
        self.assertIsNone(watcher.state)  # nothing until the thread has read
        watcher.start(changed.set)
        try:
            self.assertTrue(changed.wait(2.0))
            self.assertEqual(watcher.state.branch, "main")
            changed.clear()
            watcher.refresh()
            self.assertTrue(changed.wait(2.0))
        finally:
            watcher.stop()
        self.assertTrue(all(name == "omh-git-status" for name in calls), calls)


class BadgeRowTests(unittest.TestCase):
    def test_the_branch_on_the_left_and_the_badge_on_the_right_edge(self) -> None:
        theme = build_theme(default_theme(), "#0D1117")
        word, accent = native("spectrum", "#0D1117")
        row = badge_line(100, theme, accent, word, GitState("omh/terminology-unification", 3, 128))
        text = line_text(row)
        self.assertEqual(len(text), 100)
        self.assertTrue(text.startswith("  branch:omh/terminology-unification | !3 ?128"), text)
        self.assertTrue(text.endswith("[ ⚚ OMH Native ]"), text)
        styles = {span.text: span.style for span in row}
        self.assertEqual((styles["!3"], styles["?128"], styles["⚚"]), (theme.error, theme.faint, accent))
        self.assertEqual(styles["omh/terminology-unification"], theme.label)

    def test_counts_that_are_zero_are_left_out_and_no_repository_draws_the_badge_alone(self) -> None:
        theme = build_theme(default_theme(), "#0D1117")
        word, accent = native("spectrum", "#0D1117")
        self.assertTrue(line_text(badge_line(80, theme, accent, word, GitState("main", 0, 0))).startswith("  branch:main  "))
        self.assertTrue(line_text(badge_line(80, theme, accent, word, GitState("main", 0, 4))).startswith(
            "  branch:main | ?4  "))
        alone = line_text(badge_line(80, theme, accent, word, None))
        self.assertEqual((alone.strip(), len(alone)), ("[ ⚚ OMH Native ]", 80))

    def test_a_narrow_row_cuts_the_branch_name_then_its_counts_then_the_badge_and_never_wraps(self) -> None:
        theme = build_theme(default_theme(), "#0D1117")
        word, accent = native("spectrum", "#0D1117")
        state = GitState("feature/a-very-long-branch-name-for-a-narrow-terminal", 2, 1)
        cut = line_text(badge_line(60, theme, accent, word, state))
        self.assertEqual(len(cut), 60)
        self.assertIn("…", cut)
        self.assertTrue(cut.endswith("[ ⚚ OMH Native ]") and "| !2 ?1" in cut, cut)
        narrow = line_text(badge_line(19, theme, accent, word, state))
        self.assertNotIn("Native", narrow)  # the badge goes first
        self.assertLessEqual(len(narrow), 19)
        self.assertEqual(badge_line(5, theme, accent, word, None), ())

    def test_native_reads_in_every_launch_look_on_light_and_dark(self) -> None:
        for background in ("#0D1117", "#FFFFFF"):
            for look in LAUNCH_PRESETS:
                word, accent = native(look, background)
                with self.subTest(background=background, look=look):
                    for span in (*word, Span("⚚", accent)):
                        self.assertGreaterEqual(contrast_ratio(span.style.fg, background), TEXT_CONTRAST)
        sweep, _accent = native("spectrum", "#0D1117")
        self.assertGreater(len({span.style.fg for span in sweep}), 3, "the spectrum sweeps its hues across the word")

    def test_the_row_is_drawn_from_the_terminal_height_alone(self) -> None:
        for rows in (BADGE_MIN_ROWS - 1, BADGE_MIN_ROWS, 40):
            budget = allocate(rows, 80, sticky=False, tail_rows=3, slot_rows=4, composer_rows=1, badge_rows=1)
            with self.subTest(rows=rows):
                self.assertEqual(budget.badge, int(rows >= BADGE_MIN_ROWS))
        busy = allocate(24, 80, sticky=False, tail_rows=3, slot_rows=8, composer_rows=1, badge_rows=1, agents_rows=7)
        idle = allocate(24, 80, sticky=False, tail_rows=0, slot_rows=0, composer_rows=1, badge_rows=1)
        self.assertEqual((busy.badge, idle.badge), (1, 1))  # a menu or the view never takes it, so nothing moves


if __name__ == "__main__":
    unittest.main()
