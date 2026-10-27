"""A negated or refused Jev mention is not consent: `omh_jev_ask` sends nothing.

The external review's case: "Do not use Jev. Review this locally." named Jev,
so the gate recorded consent and the tool sent. The rule now denies any Jev
mention in text that negates or refuses anything (`jev_consent`). No test here
opens a socket: the transport is an injected recorder.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh.jev_ask_client import TransportReply  # noqa: E402
from omh.plugin_bundle.omh.jev_ask_store import forget_answered_asks  # noqa: E402
from omh.plugin_bundle.omh.jev_consent import (  # noqa: E402
    consent_observed,
    message_requests_jev,
    reset_turn_markers,
)
from omh.plugin_bundle.omh.tools.jev_ask_tool import omh_jev_ask_handler  # noqa: E402

REVIEWER_SENTENCE = "Do not use Jev. Review this locally."
KEY = "sk-test-negation-0000"
STATE = "## Install\npip install oh-my-hermes"
QUESTION = {"q": {"type": "noul", "instructions": "Does the README cover installation?"}}
ANSWERED = json.dumps(
    {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.82}},
     "usage": {"input_tokens": 120, "output_tokens": 4}}
).encode("utf-8")

NEGATED = (
    REVIEWER_SENTENCE,
    "don't use jev",
    "Don\u2019t ask Jev, just review this diff",
    "dont use jev for this",
    "do not ask jev",
    "never ask jev about this command",
    "no jev, please",
    "review this without jev",
    "stop using jev",
    "avoid jev here",
    "instead of jev, use claude",
    "use claude rather than jev",
    "not jev",
    "jev, not now",
    "jev should not see this",
    "skip jev",
    "jev won't help here",
    "you cannot use jev",
    "ask jev? no, review it locally",
    "hold off on jev",
    "jev 쓰지마",
    "jev 쓰지 마",
    "jev 쓰지 말고 로컬에서 리뷰해",
    "jev 말고 직접 봐줘",
    "jev 없이 리뷰해",
    "jev 빼고 리뷰해",
    "jev 하지마",
    "jev 안 써",
    "jev 안써도 돼",
    "jev 못 쓰게 해",
    "jev 쓰지 않아도 돼",
    "jev는 그만",
    "jev 대신 claude로",
    "jev는 아니야",
    "jev 쓰기 싫어",
    "jev 금지",
    # Scripts whose negation the cues cannot read: denied, not guessed.
    "jevを使わないで",
    "不要用jev",
)

AFFIRMATIVE = (
    "ask jev",
    "jev로 물어봐",
    "use omh-jev-ask",
    "Jev한테 리뷰 받아",
    "jev로 확인해줘",
    "Jev한테 보안 리뷰 받아",
    "jev한테 안전한지 물어봐",
    "ask jev whether the readme covers install",
    "ask jev if the client content is current",
    "use omh-jev-review-gate",
    "omh_jev_ask",
    "jev's view",
    "JEV check",
)


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return TransportReply(200, {}, ANSWERED)


def _through_the_tool(message: str, session: str) -> tuple[dict[str, object], _Recorder]:
    """Run the real path: `pre_llm_call` records, `pre_tool_call` arms, the handler sends or refuses."""
    from omh.plugin_bundle.omh.hooks.llm_hooks import pre_llm_call
    from omh.plugin_bundle.omh.hooks.tool_hooks import pre_tool_call

    transport = _Recorder()
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        env = {"OMH_HOME": str(root / ".omh"), "HERMES_HOME": str(root / ".hermes"), "TYPESAFE_API_KEY": KEY}
        homes = {"omh_home": env["OMH_HOME"], "hermes_home": env["HERMES_HOME"]}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            pre_llm_call(user_message=message, session_id=session, is_first_turn=False,
                         platform="cli", turn_id="t1", **homes)
            pre_tool_call(tool_name="omh_jev_ask", args={}, session_id=session, turn_id="t1", **homes)
            result = json.loads(omh_jev_ask_handler(
                {"state": STATE, "questions": QUESTION}, transport=transport, session_id=session))
    return result, transport


class JevNegationConsentTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_markers()
        forget_answered_asks()

    def test_the_reviewers_sentence_sends_nothing(self) -> None:
        result, transport = _through_the_tool(REVIEWER_SENTENCE, "neg-reviewer")
        self.assertFalse(consent_observed("neg-reviewer"))
        self.assertEqual(result["status"], "consent_not_observed")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["answers"])
        self.assertEqual(transport.requests, [])

    def test_an_affirmative_request_still_sends_through_the_same_path(self) -> None:
        result, transport = _through_the_tool("ask jev whether the readme covers install", "neg-control")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(transport.requests), 1)

    def test_negated_phrasings_are_denied(self) -> None:
        for message in NEGATED:
            with self.subTest(message=message):
                self.assertFalse(message_requests_jev(message))
                self.assertFalse(message_requests_jev(message, messaging=False))

    def test_negated_phrasings_record_no_marker_through_the_hook(self) -> None:
        for index, message in enumerate(NEGATED):
            with self.subTest(message=message):
                result, transport = _through_the_tool(message, f"neg-{index}")
                self.assertEqual(result["status"], "consent_not_observed")
                self.assertEqual(transport.requests, [])

    def test_affirmative_phrasings_are_allowed(self) -> None:
        for message in AFFIRMATIVE:
            with self.subTest(message=message):
                self.assertTrue(message_requests_jev(message))
                self.assertTrue(message_requests_jev(message, messaging=False))

    def test_a_word_that_merely_contains_a_cue_is_not_a_negation(self) -> None:
        # `안` inside 보안/안전 and `nt` inside client/content/want are not cues.
        for message in ("Jev한테 보안 리뷰 받아", "ask jev what we want from the client"):
            with self.subTest(message=message):
                self.assertTrue(message_requests_jev(message))


if __name__ == "__main__":
    unittest.main()
