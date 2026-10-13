"""Slash command routing in the OMH TUI."""

from __future__ import annotations

import unittest

from omh.tui import slash


class ParseSlashTests(unittest.TestCase):
    def test_a_known_command_resolves_through_its_alias(self) -> None:
        invocation = slash.parse_slash("/exit")
        self.assertEqual((invocation.command.name, invocation.command.route), ("quit", slash.LOCAL))

    def test_an_unknown_command_passes_through_with_its_argument(self) -> None:
        invocation = slash.parse_slash("/model  gpt-x --global ")
        self.assertIsNone(invocation.command)
        self.assertEqual(invocation.text, "model gpt-x --global")

    def test_text_that_only_looks_like_a_command_is_not_one(self) -> None:
        for text in ("hello /new", "/", "//comment", "/usr/bin/env python", "  "):
            with self.subTest(text=text):
                self.assertIsNone(slash.parse_slash(text))

    def test_surrounding_whitespace_is_ignored(self) -> None:
        self.assertEqual(slash.parse_slash("  /history  ").command.name, "history")


class RouteTableTests(unittest.TestCase):
    def test_names_and_aliases_are_unique_across_the_table(self) -> None:
        names = [name for command in slash.COMMANDS for name in (command.name, *command.aliases)]
        self.assertEqual(len(names), len(set(names)))

    def test_every_route_is_one_of_the_three_owned_kinds(self) -> None:
        for command in slash.COMMANDS:
            with self.subTest(command=command.name):
                self.assertIn(command.route, (slash.LOCAL, slash.HERMES, slash.DROPPED))

    def test_commands_that_would_persist_raw_prompts_go_to_hermes(self) -> None:
        for name in ("save", "prompt", "compose"):
            with self.subTest(name=name):
                self.assertEqual(slash.parse_slash(f"/{name}").command.route, slash.HERMES)

    def test_attachments_and_session_switching_run_in_the_tui(self) -> None:
        for name, command in (
            ("image", "image"),
            ("paste", "paste"),
            ("sessions", "sessions"),
            ("switch", "sessions"),
            ("session", "sessions"),
            ("resume", "sessions"),
        ):
            with self.subTest(name=name):
                invocation = slash.parse_slash(f"/{name} x")
                self.assertEqual((invocation.command.name, invocation.command.route), (command, slash.LOCAL))
        names = [c.name for c in slash.completions("", [])]
        self.assertTrue({"image", "paste", "sessions"} <= set(names))

    def test_the_network_widget_is_dropped(self) -> None:
        self.assertEqual(slash.parse_slash("/weather").command.route, slash.DROPPED)

    def test_hermes_notice_names_the_hermes_door(self) -> None:
        notice = slash.hermes_notice(slash.parse_slash("/voice").command)
        self.assertIn("hermes --tui", notice)


class CompletionTests(unittest.TestCase):
    CATALOG = {
        "pairs": [
            ["/model", "Switch model"],
            ["/moa", "Mixture of agents"],
            ["/new", "Start a new session"],
            ["/voice", "Voice mode"],
            ["bad"],
        ]
    }

    def test_local_commands_lead_and_catalog_follows(self) -> None:
        names = [c.name for c in slash.completions("", slash.catalog_pairs(self.CATALOG))]
        local = [command.name for command in slash.COMMANDS if command.route == slash.LOCAL]
        self.assertEqual(names[: len(local)], local)
        self.assertIn("model", names)
        self.assertIn("moa", names)

    def test_omh_model_is_exact_and_ranks_ahead_of_a_longer_catalog_name(self) -> None:
        # In Hermes' TUI, Enter on `/omh-model` picked the `/omh-model-optimization` completion.
        catalog = [("/omh-model-optimization", "Model optimization skill")]
        self.assertEqual(slash.parse_slash("/omh-model").command.route, slash.LOCAL)
        names = [c.name for c in slash.completions("omh-model", catalog)]
        self.assertEqual(names, ["omh-model", "omh-model-optimization"])

    def test_prefix_filters_both_sources(self) -> None:
        names = [c.name for c in slash.completions("mo", slash.catalog_pairs(self.CATALOG))]
        self.assertEqual(names, ["model", "moa"])

    def test_catalog_commands_routed_elsewhere_are_not_offered_twice_or_at_all(self) -> None:
        names = [c.name for c in slash.completions("", slash.catalog_pairs(self.CATALOG))]
        self.assertEqual(names.count("new"), 1)
        self.assertNotIn("voice", names)

    def test_malformed_catalog_rows_are_skipped(self) -> None:
        self.assertEqual(slash.catalog_pairs({"pairs": "nope"}), [])
        self.assertEqual(len(slash.catalog_pairs(self.CATALOG)), 4)


if __name__ == "__main__":
    unittest.main()
