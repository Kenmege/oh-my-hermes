"""The delegation view where people see it: appended to a chat reply, drawn in the HUD, printed by `omh hud`.

The chat block goes through OMH's own `transform_llm_output` handler with the
keyword arguments Hermes passes it (`agent/turn_finalizer.py`). It is
appended on a chat platform that renders a fenced block, only on a turn where
the graph changed, and never anywhere else.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from _cli_harness import run_cli  # noqa: E402
from _delegation_state import PARENT, dispatched, homes, route, start, stop  # noqa: E402
from omh.plugin_bundle.omh.delegation_snapshot import (  # noqa: E402
    MESSENGER_MAX_CHARS,
    MESSENGER_PLATFORMS,
    MESSENGER_WIDTH,
)
from omh.plugin_bundle.omh.delegation_view import cells  # noqa: E402
from omh.plugin_bundle.omh.hooks import result_transforms  # noqa: E402
from omh.plugin_bundle.omh.runtime_reader import read_omh_hud  # noqa: E402


def reply(text: str = "Dispatched three lanes.", *, platform: str = "discord", session: str = PARENT,
          turn: str = "turn-3") -> str | None:
    return result_transforms.transform_llm_output(
        response_text=text, session_id=session, model="claude-opus-5-5", platform=platform, turn_id=turn
    )


def three_lanes(home) -> None:
    now = time.time()
    home.session(PARENT, "claude-opus-5-5", effort="high")
    for index, (title, category, model) in enumerate((("Refresh HUD docs", "writing", "claude-opus-5-5"),
                                                      ("Polish HUD CLI", "quick", "glm-5.3-flash"),
                                                      ("Update HUD tests", "deep", "claude-opus-5-5"))):
        route(home, category, model, "medium", written_at=now - 5 + index)
        start(home, f"child-{index}", f"sa-{index}", title, now=now - 4 + index)
        home.session(f"child-{index}", model, parent=PARENT, effort="medium")
    dispatched(home, ["sa-0", "sa-1", "sa-2"], now=now)


class ChatBlockTests(unittest.TestCase):
    def test_a_chat_reply_carries_the_graph_once_per_change(self) -> None:
        with homes() as home:
            three_lanes(home)
            first = reply()
            self.assertTrue(first.startswith("Dispatched three lanes.\n\n```\nDAG · delegation · 4 nodes · 3 edges"))
            self.assertTrue(first.endswith("\n```"))
            block = first.split("\n\n", 1)[1]
            self.assertLessEqual(len(block), MESSENGER_MAX_CHARS)
            body = block.splitlines()[1:-1]
            self.assertTrue(all(cells(line) <= MESSENGER_WIDTH for line in body), body)
            for text in ("Refresh HUD docs", "writing", "opus-5-5 · medium", "dispatched"):
                self.assertIn(text, block)
            self.assertIsNone(reply("Still working.", turn="turn-4"), "an unchanged graph is not posted again")
            home.usage("child-1", "glm-5.3-flash", last_seen=time.time())
            stop(home, "child-1", now=time.time())
            again = reply("One lane finished.", turn="turn-5")
            self.assertIn("done", again)
            self.assertIn("✓ Polish HUD CLI", again)

    def test_every_supported_platform_and_no_other(self) -> None:
        for platform in sorted(MESSENGER_PLATFORMS):
            with self.subTest(platform=platform), homes() as home:
                three_lanes(home)
                self.assertIn("```\nDAG ·", reply(platform=platform))
        for platform in ("tui", "cli", "desktop", "subagent", "cron", "api_server", "", "Discord-ish"):
            with self.subTest(platform=platform), homes() as home:
                three_lanes(home)
                self.assertIsNone(reply(platform=platform))

    def test_the_graph_switch_turns_the_block_off(self) -> None:
        with homes() as home, patch.dict(os.environ, {"OMH_SUBAGENT_GRAPH": "off"}):
            three_lanes(home)
            self.assertIsNone(reply())

    def test_a_childs_own_turn_carries_nothing(self) -> None:
        with homes() as home:
            three_lanes(home)
            self.assertIsNone(reply(session="child-0"))

    def test_a_block_that_could_not_be_recorded_is_not_posted(self) -> None:
        with homes() as home, patch(
            "omh.plugin_bundle.omh.delegation_snapshot.note_posted", return_value=False
        ):
            three_lanes(home)
            self.assertIsNone(reply())

    def test_the_block_follows_the_honest_stop_and_a_failure_in_it_keeps_the_stop(self) -> None:
        with homes() as home, patch.object(
            result_transforms, "honest_stop_output", return_value="Dispatched three lanes.\n\nStopping here."
        ):
            three_lanes(home)
            composed = reply()
            self.assertTrue(composed.startswith("Dispatched three lanes.\n\nStopping here.\n\n```\nDAG ·"))
            with patch("omh.plugin_bundle.omh.delegation_snapshot.build_delegation_graph",
                       side_effect=AttributeError("bug")):
                self.assertEqual(reply(turn="turn-9"), "Dispatched three lanes.\n\nStopping here.")
            self.assertGreaterEqual(result_transforms.snapshot_failures["AttributeError"], 1)

    def test_no_delegation_no_block(self) -> None:
        with homes():
            self.assertIsNone(reply())


class HudTests(unittest.TestCase):
    def test_the_hud_carries_the_reading_sessions_graph(self) -> None:
        with homes() as home:
            three_lanes(home)
            payload = read_omh_hud(home.omh, home.hermes, session_ref=PARENT, status={"runs": []})
            graph = payload["delegation_graph"]
            self.assertEqual((graph["status"], graph["scope"], graph["source"]), ("active", "session", "native"))
            self.assertEqual(len(graph["nodes"]), 4)
            other = read_omh_hud(home.omh, home.hermes, session_ref="someone-else", status={"runs": []})
            self.assertEqual(other["delegation_graph"]["status"], "inactive")
            unscoped = read_omh_hud(home.omh, home.hermes, status={"runs": []})
            self.assertEqual(unscoped["delegation_graph"]["scope"], "global")
            self.assertEqual(len(unscoped["delegation_graph"]["nodes"]), 4)
            with patch.dict(os.environ, {"OMH_SUBAGENT_GRAPH": "off"}):
                off = read_omh_hud(home.omh, home.hermes, session_ref=PARENT, status={"runs": []})
            self.assertEqual((off["delegation_graph"]["status"], off["delegation_graph"]["reason"]),
                             ("inactive", "explicit_off"))

    def test_omh_hud_graph_prints_the_view(self) -> None:
        with homes() as home:
            three_lanes(home)
            status, stdout, stderr = run_cli(
                ["--omh-home", str(home.omh), "--hermes-home", str(home.hermes), "hud", "--graph"],
                output_json=False,
            )
            self.assertEqual((status, stderr), (0, ""))
            # The harness's stdout names no encoding, so the drawing is the ASCII one.
            lines = stdout.splitlines()
            self.assertTrue(any(line.startswith("DAG - delegation - 4 nodes - 3 edges") for line in lines), stdout)
            self.assertTrue(any("Polish HUD CLI" in line for line in lines))
            self.assertTrue(stdout.isascii())
            with patch("omh.commands.hud._encodes", return_value=True):
                _status, stdout, _stderr = run_cli(
                    ["--omh-home", str(home.omh), "--hermes-home", str(home.hermes), "hud", "--graph"],
                    output_json=False,
                )
            self.assertIn("DAG · delegation · 4 nodes · 3 edges", stdout)
            self.assertIn("┌", stdout)
            status, stdout, _stderr = run_cli(
                ["--omh-home", str(home.omh), "--hermes-home", str(home.hermes), "hud", "--graph", "--json"],
                output_json=False,
            )
            self.assertEqual(json.loads(stdout)["delegation_graph"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
