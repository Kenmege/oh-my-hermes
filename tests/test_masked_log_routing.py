"""A pasted log reaches the routing readers as one blank, not a run of its own length.

`reference_regions` masks quoted, fenced and block-quoted text. Blanked to its own length, a pasted log was one long
run of spaces, and the cue regexes that open on `\\s*` or `[^.]*?` (`_KOREAN_SPLIT_OBJECT`, `_EN_REPORT_TAIL`,
`korean_request._TRAILING`, ...) re-read it from each position in it: quadratic, minutes for a few thousand lines in
the `pre_llm_call` hook, which holds the GIL and stalls the gateway. A request written after the log was the shape the
regex-by-regex fix left open. These pin the squeeze and route such messages through the real entry points in a child
process with a budget, so a regression fails instead of hanging the suite.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.reference_regions import _LONG_MASKED_RUN, executable_routing_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOG_LINES = 3_000  # minutes on the unsqueezed text; milliseconds now
BUDGET_SECONDS = 20


def fenced(head: str, tail: str, lines: int = LOG_LINES) -> str:
    return head + "\n```\n" + "build step ok\n" * lines + "```\n" + tail


class SqueezedMaskTest(unittest.TestCase):
    def test_a_long_fenced_log_is_one_line_break(self) -> None:
        self.assertEqual(executable_routing_text(fenced("이 로그 보고", "고쳐줘")), "이 로그 보고\n고쳐줘")

    def test_long_block_quoted_lines_are_one_line_break(self) -> None:
        message = "look at this\n" + "> error: boom\n" * 500 + "fix it"
        self.assertEqual(executable_routing_text(message), "look at this\nfix it")

    def test_a_long_masked_run_on_one_line_is_one_space(self) -> None:
        message = 'see "' + "x" * 1_000 + '" then fix it'
        self.assertEqual(executable_routing_text(message), "see then fix it")

    def test_short_masks_keep_their_positions(self) -> None:
        for message in ('use "$ulw-work" now', "a ```\n$ulw-work\n```\nb", "> quoted line\nfix it"):
            with self.subTest(message=message):
                self.assertEqual(len(executable_routing_text(message)), len(message))
        boundary = 'x "' + "y" * (_LONG_MASKED_RUN - 4) + '" z'
        self.assertEqual(len(executable_routing_text(boundary)), len(boundary))

    def test_spaces_the_person_typed_stay(self) -> None:
        for message in ("fix all the failing tests" + " " * 2_000 + ", ok?", "a" + "\n" * 1_000 + "b"):
            with self.subTest(length=len(message)):
                self.assertEqual(executable_routing_text(message), message)

    def test_a_squeezed_projection_is_stable(self) -> None:
        once = executable_routing_text(fenced("here is the log", "fix all the failing tests"))
        self.assertEqual(executable_routing_text(once), once)


_CHILD = r"""
import sys
sys.path.insert(0, "tests")
from _local_package import load_local_package
load_local_package()
from omh.plugin_bundle.omh.awareness import awareness_route_hint
from omh.routing.chat import route_chat_message
log = "\n```\n" + "build step ok\n" * int(sys.argv[1]) + "```\n"
for head, tail in (
    ("이 로그 보고", "전부 병렬로 나눠서 고쳐줘"),
    ("이 로그", "를 병렬로 나눠서 고쳐줘"),
    ("이 로그 보고", "고쳐줘"),
    ("here is the log", "fix all the failing tests, ok?"),
    ("the checkout page", "split it into parallel parts and implement them"),
    ("fix all the failing tests", ": a, b, c"),
):
    awareness_route_hint(head + log + tail)
    route_chat_message(head + log + tail, source="discord")
print("routed")
"""


class PastedLogRoutesInTimeTest(unittest.TestCase):
    def test_a_request_after_a_long_fenced_log_routes_within_budget(self) -> None:
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, ("tests", os.environ.get("PYTHONPATH"))))}
        try:
            done = subprocess.run(
                [sys.executable, "-c", _CHILD, str(LOG_LINES)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=BUDGET_SECONDS,
            )
        except subprocess.TimeoutExpired:
            self.fail(f"routing a {LOG_LINES}-line fenced log took over {BUDGET_SECONDS}s")
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertIn("routed", done.stdout)


if __name__ == "__main__":
    unittest.main()
