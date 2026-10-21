from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.routing.chat import route_chat_message
from omh.routing.recommend import recommend_skills
from omh.skills.skill_record import skill_record
from omh.wrapper_contract import build_chat_interaction_payload
from omh.plugin_bundle.omh.awareness import awareness_primer_payload, awareness_route_hint, workflow_context_card_for_workflow
from omh.plugin_bundle.omh.hooks.llm_hooks import pre_llm_call


class ShowReelRoutingTests(unittest.TestCase):
    def test_invocation_cues_are_lexical_commands_not_noun_prefixes(self):
        from omh.plugin_bundle.omh.skill_invocation import contains_run_cue

        for noun in ("user", "users", "runtime", "runbook", "runner", "starter", "invoker", "execution", "run.py", "/usr/bin/run", "start.sh", "user/run", "사용법", "사용자", "실행기", "부탁말"):
            with self.subTest(noun=noun):
                self.assertFalse(contains_run_cue(noun))
        for command in ("use", "run", "run.", "run,", "**run**", "“run”", "execute", "invoke", "start", "써줘", "써", "쓰자", "사용해", "사용해주세요", "실행해줘", "실행해 주세요", "돌려줘", "해줘", "해주세요", "부탁해요"):
            with self.subTest(command=command):
                self.assertTrue(contains_run_cue(command))

    def test_noun_prefixes_are_not_invocations_on_any_surface(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        messages = ("show-reel skill user manual", "show-reel workflow runtime documentation", "show-reel workflow starter guide") + tuple(
            f"show-reel {marker} {noun} documentation" for marker in ("skill", "workflow")
            for noun in ("user", "runtime", "starter", "runbook", "invoker", "run.py", "/usr/bin/run", "start.sh", "user/run", "사용법", "사용자", "실행기"))
        with TemporaryDirectory() as td:
            paths = resolve_paths(Path(td)/"omh", Path(td)/"hermes")
            for message in messages:
                with self.subTest(message=message):
                    self.assertFalse(show_reel_request_admitted(message))
                    self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")
                    self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                    self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertNotEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                    payload = pre_llm_call(omh_home=str(paths.omh_home), hermes_home=str(paths.hermes_home), user_message=message, is_first_turn=False)
                    self.assertNotIn("prepare_show_reel", str(payload))

    def test_explicit_marker_word_order_matches_cli_recommend_card_hint_and_mid_session(self):
        messages = (
            "use the show-reel skill", "omh show-reel 써줘", "omh로 show-reel 해줘", "show-reel 스킬 실행해줘",
            "Please execute the show-reel workflow", "show-reel workflow start", "omh show-reel 사용해 주세요",
            "show-reel workflow run.", "show-reel workflow run,",
        )
        self._assert_production_surfaces(messages)
        with TemporaryDirectory() as td:
            env = {**os.environ, "HOME": td, "OMH_HOME": str(Path(td)/"omh"), "HERMES_HOME": str(Path(td)/"hermes")}
            for message in messages:
                result = subprocess.run([sys.executable, "-I", "-m", "omh.cli", "chat", "route", "--json", message], env=env, cwd=td, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["route"]["selected_skill"], "show-reel", message)

    def test_marker_words_do_not_turn_explanation_negation_or_reference_into_invocation(self):
        messages = (
            "Explain how to use the show-reel skill", "Translate use the show-reel skill into Japanese",
            "Do not use the show-reel skill", "omh show-reel 말고 다른 스킬 써줘",
            "Read use-show-reel-skill.md", 'Translate "omh show-reel 써줘" into English',
            "Use the show-reel skill as a reference to create a static infographic",
            "show-reel repository explanation", "show-reel 스킬 설명을 번역해줘",
        )
        with TemporaryDirectory() as td:
            paths = resolve_paths(Path(td)/"omh", Path(td)/"hermes")
            for message in messages:
                with self.subTest(message=message):
                    self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")
                    self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                    self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertNotEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                    payload = pre_llm_call(omh_home=str(paths.omh_home), hermes_home=str(paths.hermes_home), user_message=message, is_first_turn=False)
                    self.assertNotIn("prepare_show_reel", str(payload))

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
        for message in ("제품 쇼케이스 영상 만들어줘", "제품 쇼케이스 영상 만들어 주세요", "製品ショーケース動画を作って", "製品ショーケース動画を作ってください", "制作产品展示短片", "请制作产品展示短片", "omh-show-reel اصنع عرضًا للمنتج"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertEqual(route.get("selected_skill"), "show-reel")
                self.assertEqual(route["action"], "dispatch")
                self.assertEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")

    def test_production_refreshes_real_mid_session_hook(self):
        with TemporaryDirectory() as temp:
            for message in (
                "Create a product showcase video for a Japanese audience.",
                "제품 쇼케이스 영상 만들어줘",
                "製品ショーケース動画を作って",
                "制作产品展示短片",
            ):
                with self.subTest(message=message):
                    payload = pre_llm_call(omh_home=temp, hermes_home=temp, user_message=message, is_first_turn=False)
                    self.assertIsNotNone(payload)
                    self.assertIn("prepare_show_reel", str(payload))

    def test_production_verb_article_and_polite_family(self):
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            messages = [
                f"{prefix}{verb} {article}{noun} for our audience."
                for prefix in ("", "Please ", "Could you please ")
                for verb in ("Make", "Create", "Write", "Produce")
                for article, noun in (("", "product showcase video"), ("a ", "product showcase video"), ("an ", "animated product film"), ("the ", "launch film"))
            ]
            messages += [
                "show-reel",
                "omh-show-reel",
                "showreel",
                "show reel",
                "Use this review to create a product showcase video.",
                "Create a product showcase video using this review as input.",
                "Produce a product showcase video with narration; do not translate the quoted review.",
                "Make a launch film with translated subtitles and explicit narration.",
            ]
            for message in messages:
                with self.subTest(message=message):
                    self.assertEqual(recommend_skills(message, limit=1)[0]["skill"], "show-reel")
                    self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], "show-reel")
                    self.assertEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")

    def test_modified_production_objects_keep_their_head(self):
        messages = [
            f"{verb} a {modifier} {noun} for OMH."
            for verb, noun in (("Create", "product showcase video"), ("Produce", "launch film"))
            for modifier in ("cinematic", "high-quality", "60-second English", "engaging", "quietly confident", "bold hand-drawn", "warm accessible", "90 second French")
        ] + [
            "Could you make an engaging product showcase video?",
            "Create a showreel that introduces OMH.",
            "Produce a launch film which explains our product.",
            "Use this review of what not to translate to create a vivid product showcase video.",
        ]
        self._assert_production_surfaces(messages)

    def _assert_production_surfaces(self, messages):
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            for message in messages:
                with self.subTest(message=message):
                    self.assertEqual(recommend_skills(message, limit=1)[0]["skill"], "show-reel")
                    self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], "show-reel")
                    self.assertEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                    payload = pre_llm_call(omh_home=temp, hermes_home=temp, user_message=message, is_first_turn=False)
                    self.assertIsNotNone(payload)
                    self.assertIn("prepare_show_reel", str(payload))

    def test_existing_pack_qualifiers_keep_production_intent(self):
        self._assert_production_surfaces((
            "제품 쇼케이스 영상을 영어로 만들어줘",
            "제품 쇼케이스 영상 60초짜리로 만들어줘",
            "제품 쇼케이스 영상을 프랑스어로 90초짜리로 제작해 주세요",
            "製品ショーケース動画を英語で作ってください",
            "製品ショーケース動画を60秒で制作してください",
            "请帮我制作一个产品展示短片",
            "請幫我製作一個产品展示短片",
        ))

    def test_native_qualifiers_do_not_cross_reference_or_text_heads(self):
        for message in (
            "제품 쇼케이스 영상을 참고로 정적인 카드로 만들어줘",
            "제품 쇼케이스 영상 문구를 영어로 만들어줘",
            "제품 쇼케이스 영상을 영어로 만들지 마",
            "제품 쇼케이스 영상을 영어로 만들어줘야 한다는 문구를 번역해줘",
            "製品ショーケース動画を参考に静的なカードを作ってください",
            "製品ショーケース動画を英語で作ってはいけない",
            "请帮我制作一个产品展示短片的字幕",
            "请帮我制作一个产品展示短片的翻译",
            "请不要制作一个产品展示短片",
            "请使用产品展示短片作为参考制作一个静态卡片",
        ):
            with self.subTest(message=message):
                self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                self.assertNotEqual(route_chat_message(message, source="discord").get("selected_skill"), "show-reel")

    def test_modified_nonproduction_objects_do_not_change_owner(self):
        for message in (
            "Create an engaging script for a product showcase video.",
            "Make a vivid caption for a launch film.",
            "Produce a high-quality translation of a showreel.",
            "Write an engaging product showcase video script.",
            "Create a cinematic launch film caption.",
            "Create a high-quality showreel translation.",
            "Make no cinematic product showcase video.",
            "Create a static infographic using a cinematic product showcase video as reference.",
            "Use a cinematic product showcase video as reference to make a static infographic.",
            "Review a cinematic product showcase video.",
            "Convert a 60-second English product showcase video to MP4.",
            "Create a cinematic product showcase video.md file.",
            "Create a cinematic xshowreely identifier.",
            'Translate "Create a cinematic product showcase video" into Japanese.',
            'Metadata: title="Create a cinematic product showcase video"',
        ):
            with self.subTest(message=message):
                self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                self.assertNotEqual(route_chat_message(message, source="discord").get("selected_skill"), "show-reel")

    def test_nonproduction_nouns_earn_no_recommendation_or_dispatch(self):
        for message in (
            "Convert this product showcase video to a smaller MP4.",
            "Review the typography in this product showcase video.",
            "Explain how the showreel repository works; do not create a video.",
            "Write narration for this existing product showcase video; do not produce video.",
            "제품 쇼케이스 영상 문구를 영어로 번역해줘",
            "製品ショーケース動画という文章を英語に翻訳して",
            "把产品展示短片这句话翻译成英文",
        ):
            with self.subTest(message=message):
                self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                self.assertNotEqual(route_chat_message(message, source="discord").get("selected_skill"), "show-reel")

    def test_hint_and_card_admit_the_same_production_intent(self):
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            for message in (
                "Use this product showcase video as a reference for a static infographic.",
                "Review the typography in this product showcase video.",
                "Explain how the showreel repository works; do not create a video.",
                "Write narration for this existing product showcase video; do not produce video.",
                "Convert this product showcase video to a smaller MP4.",
                "Do not create a product showcase video.",
                "Use this review not to create a product showcase video.",
                "Write a product showcase video script.",
                "Create a product showcase video translation.",
                "제품 쇼케이스 영상 문구를 영어로 번역해줘",
                "製品ショーケース動画という文章を英語に翻訳して",
                "把产品展示短片这句话翻译成英文",
                "Showreelish is our internal code name, not a video.",
                "Please document the identifier xshowreely; do not make media.",
                "Read show-reel.md before answering.",
                "show-reel.md",
                "show-reel repository explanation",
                "製品ショーケース動画を作ってはいけない",
                "不要制作产品展示短片",
                "제품 쇼케이스 영상 만들어줘야 한다는 문구를 번역해줘",
                'Translate "Create a product showcase video" into Japanese.',
                'Metadata: title="product showcase video"',
            ):
                with self.subTest(message=message):
                    self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                    interaction = build_chat_interaction_payload(message, source="discord", paths=paths)
                    self.assertNotEqual(interaction["route"].get("selected_skill"), "show-reel")
                    self.assertNotEqual(interaction["chat_response"]["kind"], "show_reel")
            message = "Use this product showcase video as a reference for a static infographic."
            self.assertEqual(awareness_route_hint(message)["primary_workflow"], "img-summary")
            self.assertEqual(recommend_skills(message, limit=1)[0]["skill"], "img-summary")
            self.assertEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "img_summary")

    def test_negative_senses_and_sibling_boundaries_do_not_dispatch_here(self):
        for message in ("Which fishing reel should I buy?", "The tape reel is damaged.", "What is the plot of this film?", "Convert this MP4 to a smaller file.", "Make a static infographic summary card", "Build a landing page website", "Create a PowerPoint deck", "visual QA this screenshot against the reference", "Show the video attachment in this bug report"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertNotEqual(route.get("selected_skill"), "show-reel")


if __name__ == "__main__":
    unittest.main()
