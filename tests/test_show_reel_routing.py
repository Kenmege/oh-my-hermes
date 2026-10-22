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
    def test_explicit_sibling_articles_and_courtesy_keep_the_preparation_owner(self):
        for owner in ("media-input-operator", "workflow-learning", "code-review"):
            for opening in ("Use the", "Please use the", "Could you please run the", "Execute the"):
                message = f"{opening} {owner} skill to create a launch film from the attached transcript."
                with self.subTest(message=message), TemporaryDirectory() as temp:
                    paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
                    self.assertEqual(route_chat_message(message)["selected_skill"], owner)
                    hint = awareness_route_hint(message)
                    self.assertEqual(hint["primary_workflow"], owner)
                    self.assertFalse(any(item["next_action"] == "prepare_show_reel" for item in hint["hints"]))
                    card = build_chat_interaction_payload(message, source="discord", paths=paths)
                    self.assertNotEqual(card["chat_response"]["kind"], "show_reel")
                    mid = pre_llm_call(omh_home=str(paths.omh_home), hermes_home=str(paths.hermes_home), user_message=message, is_first_turn=False)
                    self.assertNotIn("next_action=prepare_show_reel", str(mid))
        self._assert_production_surfaces((
            "Create a launch film using the code-review report as input",
            "Use the show-reel skill with workflow-learning notes as input",
        ), first_recommendation=False)

    def test_source_first_comma_separates_inputs_from_the_production_operation(self):
        self._assert_production_surfaces((
            "Using the media transcript and research notes, create a launch film for the new brand.",
            "Based on the customer history and media transcript, produce a cinematic product showcase video.",
        ), first_recommendation=False)
        self._assert_nonproduction_surfaces((
            "Using the transcript, explain how to create a launch film",
            "Using the transcript, do not create a launch film",
            'Using the quoted example "create a launch film", explain its wording',
        ))

    def test_named_operation_accepts_deictic_inputs_and_temporal_clauses(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        self._assert_production_surfaces((
            "show-reel workflow run it",
            "show-reel workflow run this",
            "show-reel workflow run those",
            "show-reel workflow run while keeping the original narration",
            "show-reel workflow run when the approved assets arrive",
            "show-reel workflow run if the assets are approved",
            "show-reel workflow run once I approve the assets",
        ))
        for message in (
            "show-reel workflow run this_identifier please",
            "show-reel workflow run once count please",
            "show-reel workflow run reference catalog while explaining the method",
        ):
            with self.subTest(message=message):
                self.assertFalse(show_reel_request_admitted(message))
                self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")

    def test_natural_production_object_outranks_source_media_vocabulary(self):
        self._assert_production_surfaces((
            "Could you please produce a gently luminous 45-second English product showcase video from our research notes?",
            "Create an unusually restrained animated product film from the research notes",
            "Produce a launch film with a transcript as source material",
        ), first_recommendation=False)
        self._assert_nonproduction_surfaces((
            "Summarize the attached video transcript",
            "Transcribe this product showcase video",
            "Write a transcript for this existing launch film",
        ))
        self.assertEqual(route_chat_message("Summarize the attached video transcript")["selected_skill"], "media-input-operator")

    def test_named_production_source_feedback_does_not_replace_the_awareness_owner(self):
        self._assert_production_surfaces((
            "Use the show-reel skill with missed route feedback as source material",
        ), first_recommendation=False)
        self.assertEqual(awareness_route_hint("Record a missed route and learn from the run logs")["primary_workflow"], "workflow-learning")

    def test_named_production_input_logs_do_not_become_workflow_feedback(self):
        self._assert_production_surfaces((
            "show-reel skill run please now with the run logs as source material",
            "show-reel workflow run please now with the run logs as source material",
        ))
        self.assertEqual(route_chat_message("Record a missed route and learn from the run logs")["selected_skill"], "workflow-learning")
        self._assert_nonproduction_surfaces(("Learn why the show-reel workflow missed this route",))

    def test_show_reel_capability_discovery_cannot_prepare_production(self):
        messages = tuple(
            f"{name} {marker} run {tail}"
            for name in ("show-reel", "show reel", "showreel")
            for marker in ("skill", "workflow")
            for tail in ("reference catalog please", "capability catalog", "reference index with notes")
        ) + tuple(
            f"what can OMH do for {name}?"
            for name in ("show-reel", "show reel", "showreel", "product showcase video")
        ) + (
            "Explain the show-reel workflow", "show-reel skill run --help",
            "What OMH workflows are available?",
        )
        self._assert_nonproduction_surfaces(messages)
        for message in messages[-7:]:
            route = route_chat_message(message)
            self.assertEqual(route["selected_skill"], "oh-my-hermes")
            self.assertNotIn("prepare_show_reel", str(route))

    def test_temporal_degree_mode_and_coordination_are_compositional(self):
        self._assert_production_surfaces(tuple(
            f"show-reel {marker} run {tail}"
            for marker in ("skill", "workflow") for tail in (
                "right now with the brand guide", "right away using our brief",
                "as soon as the footage arrives", "as soon as approved assets are ready",
                "offline with the approved assets", "online from the licensed archive",
                "very securely using these files", "quite delicately with this testimony",
                "safely and reliably using the source notes",
                "offline and very efficiently with approved inputs",
                "right now gently and consistently with the brand guide",
                "as cautiously as possible using the archive",
                "as rapidly as the renderer permits",
            )
        ))
        self._assert_nonproduction_surfaces(tuple(
            f"show-reel workflow run {tail}" for tail in (
                "right", "right with approved assets", "as soon", "as soon as",
                "as soon as the", "as soon as reference catalog please with notes",
                "as soon as reference notes please with assets",
                "as soon as reference notes",
                "very with inputs", "very family with notes", "safely and with assets",
                "safely and", "as gently as", "as family as possible with notes",
                "offline reference catalog please with footage", "right now reference index",
                "family very safely with approved inputs", "reference as soon as footage arrives",
                "as gently as possible reference catalog with notes",
            )
        ))

    def test_regular_manner_derivation_requires_adjacent_argument_roles(self):
        # Productive formations, not a vocabulary of complete adverbs.
        tails = (
            "safely with the approved local assets", "exclusively with licensed footage",
            "gently using the interview notes", "securely with approved assets",
            "strictly with licensed footage", "reliably using these notes",
            "efficiently from the briefing", "delicately with our sources",
            "consistently using the archive", "rapidly with this source",
            "cautiously without narration", "creatively with unusual source material",
            "respectfully using the testimony", "tirelessly from the transcript",
            "comfortably with approved inputs", "responsibly using licensed sources",
        )
        self._assert_production_surfaces(tuple(
            f"show-reel {marker} run {tail}"
            for marker in ("skill", "workflow") for tail in tails
        ))
        self._assert_nonproduction_surfaces(tuple(
            f"show-reel {marker} run {head} {tail}"
            for marker in ("skill", "workflow")
            for head in ("family", "reply", "assembly", "supply", "anomaly", "tally",
                         "quuxly", "jelly", "monopoly", "butterfly", "reference")
            for tail in ("please", "with approved assets", "please safely with notes")
        ) + (
            "show-reel workflow run safely reference catalog with notes",
            "show-reel workflow run securely --help",
            "show-reel workflow run reliably",
        ))

    def test_english_operational_adjuncts_compose_before_freeform_arguments(self):
        self._assert_production_surfaces(tuple(
            f"show-reel {marker} {verb} {tail}"
            for marker in ("skill", "workflow")
            for verb in ("run", "execute")
            for tail in (
                "without a voiceover", "without narration using the research report",
                "after reading the attached brief", "before presenting the approved cut",
                "tomorrow with approved assets", "quietly with interview notes",
                "only with approved assets", "again only after checking the capability documentation",
                "carefully without subtitles", "later from the customer history archive",
            )
        ))
        self._assert_nonproduction_surfaces(tuple(
            f"show-reel {marker} run {noun} {tail}"
            for marker in ("skill", "workflow")
            for noun in ("family", "reply", "assembly", "supply", "quuxly", "ledger", "reference")
            for tail in ("please", "with approved assets", "only after reading notes")
        ) + (
            "Do not run the show-reel workflow without narration",
            "show-reel workflow run without show-reel",
            'Translate "show-reel workflow run quietly with notes" into Korean',
        ))

    def _assert_nonproduction_surfaces(self, messages):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            for message in messages:
                with self.subTest(message=message):
                    self.assertFalse(show_reel_request_admitted(message))
                    self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")
                    self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                    self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertNotEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                    self.assertNotIn("prepare_show_reel", str(pre_llm_call(omh_home=temp, hermes_home=temp, user_message=message, is_first_turn=False)))

    def test_korean_action_object_particle_requires_a_predicate(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        for message in (
            "show-reel 스킬 사용을",
            "show-reel 워크플로우 실행을",
            "show-reel 스킬 사용법을 부탁드립니다",
        ):
            with self.subTest(message=message):
                self.assertFalse(show_reel_request_admitted(message))
                self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")

    def test_korean_adjacent_adverb_and_request_morphology_families(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        messages = tuple(
            f"show-reel 스킬 실행 {adverb} {predicate}"
            for adverb in ("바로", "꼭", "먼저", "우선", "부디", "조금", "다시", "지금", "정중히", "빠르게")
            for predicate in ("부탁드립니다", "부탁드릴게요")
        ) + (
            "show-reel 스킬 실행 좀 요청드립니다",
            "show-reel 스킬 사용 우선 요청드릴게요",
            "show-reel 스킬 사용해 마케팅 자료로",
            "show-reel 스킬 사용 부탁해 말레이시아 자료로",
            "show-reel 스킬 실행 부탁할게",
            "show-reel 스킬 실행 해 주실 수 있나요",
            "show-reel 스킬 실행 좀 부탁드릴 수 있을까요",
            "show-reel 스킬 부탁드릴게요",
            "show-reel 스킬 사용을 부탁드립니다",
            "show-reel 워크플로우 실행해 주실 수 있나요",
        )
        self._assert_production_surfaces(messages)
        for message in (
            "show-reel 스킬 사용해 꼭 주지 마세요",
            "show-reel 스킬 사용 부탁해 우선 하지 마세요",
            "show-reel 스킬 실행 바로 기록 정리해주세요",
            "show-reel 스킬 실행 어떻게 부탁드릴까요",
            "show-reel 스킬 사용 예시를 정중히 부탁드립니다",
        ):
            with self.subTest(message=message):
                self.assertFalse(show_reel_request_admitted(message))
                self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")

    def test_english_adjacent_courtesy_arguments_and_diagnostic_queries(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        self._assert_production_surfaces((
            "show-reel workflow run now",
            "show-reel workflow run please with the research report as input",
            "show-reel workflow run with --help examples as input",
        ))
        for message in (
            "show-reel workflow run --help",
            "Use the show-reel skill --help",
            "show-reel workflow run details please",
        ):
            with self.subTest(message=message):
                self.assertFalse(show_reel_request_admitted(message))
                self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")

    def test_named_english_command_requires_positive_argument_structure(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        nouns = ("details", "records", "summary", "summaries", "identifier", "number", "timestamp", "instructions", "quuxledger")
        with TemporaryDirectory() as td:
            paths = resolve_paths(Path(td) / "omh", Path(td) / "hermes")
            for marker in ("skill", "workflow"):
                for verb in ("run", "start", "use", "execute", "invoke"):
                    for noun in nouns:
                        for ending in ("", " please"):
                            message = f"show-reel {marker} {verb} {noun}{ending}"
                            with self.subTest(message=message):
                                self.assertFalse(show_reel_request_admitted(message))
                                self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")
                                self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                                self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                                self.assertNotEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                                self.assertNotIn("prepare_show_reel", str(pre_llm_call(omh_home=str(paths.omh_home), hermes_home=str(paths.hermes_home), user_message=message, is_first_turn=False)))
        self._assert_production_surfaces((
            "show-reel workflow run please", "show-reel workflow run with research as input",
            "show-reel skill use our documentation to produce a film",
            "show-reel workflow execute the history report as input",
            "show-reel workflow run from research.md", "show-reel workflow run research.md",
            "show-reel workflow run --duration 60 --language English",
            "show-reel workflow start for a French audience", "show-reel workflow run in English",
            "show-reel workflow run 90 seconds in French", "Run the show-reel workflow with records",
            "Create an unusually textured quietly luminous product showcase video using the supplied research documentation.",
        ))

    def test_nominal_command_complements_are_not_production_on_any_surface(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        messages = [
            "show-reel skill use cases", "show-reel workflow use case documentation",
            "show-reel workflow run history", "show-reel workflow start date",
            "show-reel workflow run count", "show-reel 스킬 사용 예시",
            "show-reel 워크플로우 실행 기록",
        ] + [f"show-reel workflow {verb} {noun}" for verb, nouns in (
            ("use", ("case", "cases")), ("run", ("logs", "results", "status", "duration")),
            ("start", ("dates", "time")), ("사용", ("사례", "기록")), ("실행", ("예시", "횟수")),
        ) for noun in nouns]
        with TemporaryDirectory() as td:
            paths = resolve_paths(Path(td) / "omh", Path(td) / "hermes")
            for message in messages:
                with self.subTest(message=message):
                    self.assertFalse(show_reel_request_admitted(message))
                    self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")
                    self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                    self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertNotEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                    self.assertNotIn("prepare_show_reel", str(pre_llm_call(omh_home=str(paths.omh_home), hermes_home=str(paths.hermes_home), user_message=message, is_first_turn=False)))

    def test_additional_nominal_metadata_requests_do_not_start_production(self):
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted

        messages = tuple(
            f"show-reel {marker} {verb} {noun}"
            for marker in ("skill", "workflow")
            for verb, nouns in (
                ("run", ("date", "dates", "statistics", "statistic", "metadata", "metrics")),
                ("use", ("example", "examples")),
            )
            for noun in nouns
        )
        with TemporaryDirectory() as td:
            paths = resolve_paths(Path(td) / "omh", Path(td) / "hermes")
            for message in messages:
                with self.subTest(message=message):
                    self.assertFalse(show_reel_request_admitted(message))
                    self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")
                    self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                    self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    self.assertNotEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                    self.assertNotIn("prepare_show_reel", str(pre_llm_call(omh_home=str(paths.omh_home), hermes_home=str(paths.hermes_home), user_message=message, is_first_turn=False)))

    def test_korean_action_filler_request_predicates_compose_without_reference_scan(self):
        self._assert_production_surfaces(tuple(
            f"show-reel {marker} {action} {filler}{predicate}"
            for marker in ("스킬", "워크플로우")
            for action in ("실행", "사용")
            for filler in ("", "좀 ", "한번 좀 ")
            for predicate in ("부탁드립니다", "해주세요", "부탁드릴게요", "부탁드리겠습니다", "해 주십시오", "부탁해 주시겠어요")
        ) + (
            "show-reel 스킬 실행 좀 부탁드릴게요 기록을 바탕으로",
            "show-reel 워크플로우 사용 한번 부탁드리겠습니다 연구 문서를 입력으로",
        ))
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted
        with TemporaryDirectory() as td:
            paths = resolve_paths(Path(td) / "omh", Path(td) / "hermes")
            for action in ("실행", "사용"):
                for suffix in ("기록 좀 정리해주세요", "예시를 부탁드립니다", "좀 하지 마세요", "부탁드리지 마세요", "해 주지 마세요", "좀 부탁하지 말아주세요", "기록 좀 부탁드리겠습니다"):
                    message = f"show-reel 스킬 {action} {suffix}"
                    with self.subTest(message=message):
                        self.assertFalse(show_reel_request_admitted(message))
                        self.assertNotEqual(route_chat_message(message).get("selected_skill"), "show-reel")
                        self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                        self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                        self.assertNotEqual(build_chat_interaction_payload(message, source="discord", paths=paths)["chat_response"]["kind"], "show_reel")
                        self.assertNotIn("prepare_show_reel", str(pre_llm_call(omh_home=str(paths.omh_home), hermes_home=str(paths.hermes_home), user_message=message, is_first_turn=False)))

    def test_korean_request_stems_compose_future_and_split_negative_auxiliaries(self):
        self._assert_production_surfaces((
            "show-reel 스킬 실행 좀 부탁하겠습니다",
            "show-reel 워크플로우 사용 한번 부탁할게요",
            "show-reel 스킬 사용 좀 해주시겠어요",
        ))
        from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted
        for action in ("사용해", "실행해", "사용해주세요", "실행해줘"):
            with self.subTest(action=action):
                self.assertFalse(show_reel_request_admitted(f"show-reel 스킬 {action} 주지 마세요"))

    def test_korean_courtesy_after_action_noun_is_still_a_production_request(self):
        self._assert_production_surfaces(tuple(
            f"show-reel {marker} {action} {ending}"
            for marker in ("스킬", "워크플로우")
            for action in ("사용", "실행")
            for ending in ("부탁해", "부탁해요", "부탁드립니다", "부탁드려요", "부탁합니다", "해줘", "해주세요", "합시다")
        ))

    def test_imperatives_keep_arguments_even_when_inputs_describe_documentation_or_history(self):
        self._assert_production_surfaces((
            "show-reel workflow run", "show-reel workflow run.", "show-reel workflow **run**",
            "show-reel workflow run with customer research documentation as input",
            "show-reel skill use our research documentation to create a product showcase video",
            "show-reel workflow start with the run history as input",
            "Use the show-reel skill with use cases from our documentation",
            "show-reel 스킬 실행해줘", "show-reel 스킬 실행해주세요",
            "show-reel 스킬 사용해 주세요", "show-reel 워크플로우 실행해줘 기록을 바탕으로",
        ))

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

    def _assert_production_surfaces(self, messages, *, first_recommendation=True):
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            for message in messages:
                with self.subTest(message=message):
                    from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted
                    self.assertTrue(show_reel_request_admitted(message))
                    recommendations = recommend_skills(message, limit=200)
                    if first_recommendation:
                        self.assertEqual(recommendations[0]["skill"], "show-reel")
                    self.assertIn("show-reel", [item["skill"] for item in recommendations])
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
