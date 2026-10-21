"""English market-method routing and non-market negative controls."""
from __future__ import annotations

import unittest

from _route_owner import route_owner
from omh.routing.chat import route_chat_message
from omh.routing.recommend import recommend_skills
from omh.wrapper.contract import build_chat_interaction_payload


class MarketResearchRoutingTests(unittest.TestCase):
    def test_domain_requests_get_market_card_and_policy(self) -> None:
        for prompt in (
            "Estimate TAM SAM SOM for US dental clinics using market sizing with sources and sensitivity",
            "Prepare a customer evidence analysis and JTBD evidence matrix for dental buyers",
            "Prepare competitor pricing profiles for three dental SaaS vendors",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_owner(route_chat_message(prompt)), "market-research")
                self.assertEqual(recommend_skills(prompt)[0]["next_action"], "prepare_market_research")
                payload = build_chat_interaction_payload(prompt)
                self.assertEqual(payload["chat_response"]["kind"], "market_research")
                self.assertEqual(payload["next_action"], "prepare_market_research")

    def test_adjacent_requests_retain_their_owner(self) -> None:
        for prompt, owner in (
            ("triage this customer feedback backlog", "feedback-triage"),
            ("analyze this CSV and summarize anomalies", "data-analysis"),
            ("explain this paper PDF section by section", "paper-learning"),
            ("prepare a research brief for the board", "research-brief"),
            ("set up a Scout Analyst Briefer source inbox for weekly competitors", "research-department"),
            ("fix the checkout bug in our app", "ultrawork"),
            ("explain this paper PDF on competitive pricing analysis section by section", "paper-learning"),
            ("set up a Scout Analyst Briefer source inbox for weekly competitor pricing profiles", "research-department"),
            ("analyze this CSV of competitor pricing profiles and summarize anomalies", "data-analysis"),
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_owner(route_chat_message(prompt)), owner)

    def test_existing_workflows_keep_market_methods_as_their_input(self) -> None:
        cases = (
            (
                "Create a research brief for the board from the supplied competitor pricing profiles",
                "research-brief",
            ),
            (
                "Triage this customer feedback backlog about competitive pricing analysis",
                "feedback-triage",
            ),
        )
        for prompt, owner in cases:
            with self.subTest(prompt=prompt):
                decision = route_chat_message(prompt)
                self.assertEqual(route_owner(decision), owner)
                self.assertNotEqual(build_chat_interaction_payload(prompt)["chat_response"]["kind"], "market_research")
                for row in recommend_skills(prompt, limit=100):
                    if row["skill"] == "market-research":
                        self.assertNotIn("guard:market_comparison", row["matched"])

    def test_sending_or_translating_market_material_does_not_request_analysis(self) -> None:
        for prompt in (
            "Send the supplied competitor pricing profiles to Slack",
            "Translate competitive pricing analysis into Korean",
        ):
            with self.subTest(prompt=prompt):
                decision = route_chat_message(prompt)
                self.assertNotEqual(decision.get("selected_skill"), "market-research")
                self.assertNotEqual(build_chat_interaction_payload(prompt)["chat_response"]["kind"], "market_research")
                for row in recommend_skills(prompt, limit=100):
                    if row["skill"] == "market-research":
                        self.assertNotIn("guard:market_comparison", row["matched"])

    def test_analysis_predicates_still_request_the_market_method(self) -> None:
        for prompt in (
            "Perform competitive pricing analysis for three SaaS vendors",
            "Please conduct competitive pricing analysis for dental SaaS vendors",
            "Build competitor pricing profiles for the three shortlisted vendors",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_owner(route_chat_message(prompt)), "market-research")
                self.assertEqual(build_chat_interaction_payload(prompt)["chat_response"]["kind"], "market_research")

    def test_analysis_predicates_allow_normal_determiners(self) -> None:
        for prompt in (
            "Perform a competitive pricing analysis for three SaaS vendors",
            "Conduct a competitive pricing analysis for dental SaaS vendors",
            "Prepare the competitor pricing profiles for three dental SaaS vendors",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_owner(route_chat_message(prompt)), "market-research")
                self.assertEqual(build_chat_interaction_payload(prompt)["chat_response"]["kind"], "market_research")

    def test_generic_market_word_does_not_trigger_a_method(self) -> None:
        prompt = "the market stall is closing; price the used monitor"
        rows = recommend_skills(prompt, limit=100)
        for row in rows:
            if row["skill"] == "market-research":
                self.assertNotIn("trigger:market", row["matched"])
                self.assertNotIn("guard:market_comparison", row["matched"])
        decision = route_chat_message(prompt)
        self.assertNotEqual(decision.get("candidate_skill"), "market-research")
        self.assertNotEqual(decision.get("selected_skill"), "market-research")


if __name__ == "__main__":
    unittest.main()
