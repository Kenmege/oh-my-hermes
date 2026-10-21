from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.routing.chat import route_chat_message
from omh.skills.skill_record import skill_record
from omh.wrapper_contract import build_chat_interaction_payload
from omh.plugin_bundle.omh.awareness import awareness_primer_payload, awareness_route_hint, workflow_context_card_for_workflow


class ShowReelRoutingTests(unittest.TestCase):
    def test_real_messenger_surface_has_dedicated_card_not_generic_plan(self):
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            for source in ("discord", "slack", "telegram"):
                for message in ("omh-show-reel create an audience-specific film", "Create a product showcase video for a Japanese audience."):
                    with self.subTest(source=source, message=message):
                        result = build_chat_interaction_payload(message, source=source, paths=paths)
                        response = result["chat_response"]
                        self.assertEqual(response["kind"], "show_reel")
                        self.assertEqual(response["state"]["next_action"], "prepare_show_reel")
                        self.assertTrue(response["state"]["evidence_not_observed"])
                        self.assertIn("audience", str(response).lower())
                        self.assertIn("source", str(response).lower())
                        self.assertIn("renderer", str(response).lower())

    def test_preparation_policy_harness_and_awareness_are_explicit(self):
        record = skill_record("show-reel")
        self.assertEqual(record.policy.next_action, "prepare_show_reel")
        self.assertEqual(record.next_action_label, "prepare a show reel")
        self.assertEqual(record.harness.name, "show-reel")
        primer = awareness_primer_payload()
        lane = next(x for x in primer["lanes"] if x["id"] == "materials_and_visuals")
        self.assertIn("show-reel", lane["skills"])
        self.assertEqual(workflow_context_card_for_workflow("show-reel")["id"], "materials_and_visuals")

    def test_plugin_hint_agrees_with_real_route_for_named_and_unnamed_production(self):
        for message in ("Create a product showcase video for a Japanese audience.", "omh-show-reel اصنع عرضًا للمنتج"):
            hint = awareness_route_hint(message)
            self.assertEqual(hint["selected_workflow"], "show-reel")
            self.assertEqual(hint["primary_next_action"], "prepare_show_reel")
            self.assertEqual(hint["hints"][0]["workflow_context_card"]["id"], "materials_and_visuals")

    def test_supported_pack_phrases_route_without_claiming_arabic_lexical_support(self):
        for message in ("제품 쇼케이스 영상 만들어줘", "製品ショーケース動画を作って", "制作产品展示短片", "omh-show-reel اصنع عرضًا للمنتج"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertEqual(route.get("selected_skill"), "show-reel")
                self.assertEqual(route["action"], "dispatch")

    def test_negative_senses_and_sibling_boundaries_do_not_dispatch_here(self):
        for message in ("Which fishing reel should I buy?", "The tape reel is damaged.", "What is the plot of this film?", "Convert this MP4 to a smaller file.", "Make a static infographic summary card", "Build a landing page website", "Create a PowerPoint deck", "visual QA this screenshot against the reference", "Show the video attachment in this bug report"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertNotEqual(route.get("selected_skill"), "show-reel")


if __name__ == "__main__":
    unittest.main()
