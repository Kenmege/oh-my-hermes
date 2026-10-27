"""The `ru` pack's hold-back is derived from its phrases, and this re-derives it.

A Russian word scores on its own only when it is a software word with no
everyday sense; every other word of a phrase scores only inside that phrase
(#2301). The pack stores the result as `whole_phrase_only_tokens`, which is
data: a phrase added later with an everyday word (`проверь`, `сайт`, `память`)
would score that word on its own unless someone remembered to hold it back.
So the rule lives here as code and the stored list has to equal what it
derives. A new software word is a reviewed entry in `SOFTWARE_WORDS`; a new
everyday word needs nothing, because the rule holds it back.
"""

from __future__ import annotations

import unittest

from omh.routing.localization import normalized_phrase, phrase_is_spoken, routing_tokens
from omh.routing.recommend import builtin_definitions, recommend_skills
from omh.routing.trigger_language_packs import shipped_trigger_language_packs

# The words each skill may score on their own, as written. Each is a software
# term a non-technical Russian sentence does not contain. Words that stay out
# on purpose: `ревью` (a film review), `кода` (a door code), `память` (a
# person's memory), `интернете`, `сборка` (furniture), `билд` (a game build),
# `секьюрити` (a guard), `кодексу` (the labour code), `зациклился` (fixated).
SOFTWARE_WORDS: dict[str, frozenset[str]] = {
    "accessibility-audit": frozenset({"скринридера"}),
    "adversarial-consensus": frozenset({"адверсариальное"}),
    "app-debugging": frozenset({"баг", "флаки"}),
    "build-failure-triage": frozenset({"компиляции"}),
    "code-review": frozenset({"баги", "реквеста"}),
    "codebase-onboarding": frozenset({"репо", "репозитории", "репозитория"}),
    "commit-pr-authoring": frozenset({"коммит", "коммита", "реквеста"}),
    "dag-ulw": frozenset({"даг", "ультраворк"}),
    "data-analysis": frozenset({"логи", "логов"}),
    "deep-interview": frozenset({"грилл"}),
    "deploy-and-monitor": frozenset({"деплоем", "деплоя"}),
    "executor-runtime-readiness": frozenset({"рантайм"}),
    "frontend": frozenset({"лендинг", "лендинге", "лэндинг", "скролле"}),
    "frontend-refactor": frozenset({"рефакторинг"}),
    "github-event-ops": frozenset({"гитхаба", "гитхабе", "реквест", "репозиторием", "репозитории"}),
    "github-issue-intake": frozenset({"гитхаб", "гитхабе", "зарепорть", "ишью"}),
    "harness-session-inventory": frozenset({"харнес", "харнесс", "харнессов"}),
    "img-summary": frozenset({"инфографику"}),
    "live-incident-response": frozenset({"прод", "проде"}),
    "long-document-reading": frozenset({"суммаризируй"}),
    "maestro": frozenset({"хэндофф"}),
    "media-input-operator": frozenset({"суммаризируй"}),
    "ralplan": frozenset({"рефакторинг"}),
    "refactor-plan": frozenset({"рефакторинг", "рефакторинга"}),
    "reliability-review": frozenset({"стека"}),
    "research": frozenset({"ресерч"}),
    "tech-debt-audit": frozenset({"техдолга"}),
    "ultraqa": frozenset({"адверсариальное"}),
    "verification-gate": frozenset({"гейт", "мерджем", "пушем"}),
    "web-research": frozenset({"ресерч"}),
    "workspace-audit": frozenset({"воркспейса", "конфиги", "репо", "репозитория", "харнесов", "харнессов"}),
}


def _ru_pack():
    known = frozenset(definition.name for definition in builtin_definitions())
    return next(pack for pack in shipped_trigger_language_packs(known) if pack.language == "ru")


def _folded(words) -> set[str]:
    folded: set[str] = set()
    for word in words:
        folded |= routing_tokens(word)
    return folded


def _derived_holdback(skill: str, phrases: tuple[str, ...]) -> set[str]:
    software = _folded(SOFTWARE_WORDS.get(skill, ()))
    held: set[str] = set()
    for phrase in phrases:
        words = routing_tokens(phrase)
        # A one-word phrase never credits its own word: said alone it asks.
        held |= words if len(phrase.split()) == 1 else words - software
    # ASCII words belong to the English catalog; holding one back here would
    # silence it in English too.
    return {word for word in held if not word.isascii()}


class RuHoldbackDerivationTests(unittest.TestCase):
    def test_the_stored_holdback_is_the_derived_one(self) -> None:
        pack = _ru_pack()
        stored = {skill: _folded(tokens) for skill, tokens in pack.whole_phrase_only_tokens}
        derived = {skill: _derived_holdback(skill, phrases) for skill, phrases in pack.skills}
        derived = {skill: words for skill, words in derived.items() if words}
        diffs = [
            f"{skill}: missing {sorted(derived.get(skill, set()) - stored.get(skill, set()))}, "
            f"extra {sorted(stored.get(skill, set()) - derived.get(skill, set()))}"
            for skill in sorted(set(stored) | set(derived))
            if stored.get(skill, set()) != derived.get(skill, set())
        ]
        self.assertEqual(
            diffs,
            [],
            "ru.json `whole_phrase_only_tokens` differs from the rule. A missing word "
            "is an everyday word that would score on its own: hold it back. A word "
            "that should score on its own is a reviewed SOFTWARE_WORDS entry.",
        )

    def test_every_software_word_is_said_by_a_phrase_of_its_skill(self) -> None:
        # An entry no phrase carries is dead, and a dead allow entry is how a
        # later phrase would inherit credit nobody reviewed for it.
        pack = dict(_ru_pack().skills)
        dead = [
            f"{skill}: {word}"
            for skill, words in sorted(SOFTWARE_WORDS.items())
            for word in sorted(words)
            if not any(
                routing_tokens(word) <= routing_tokens(phrase) and len(phrase.split()) > 1
                for phrase in pack.get(skill, ())
            )
        ]
        self.assertEqual(dead, [])

    def test_no_held_back_phrase_sits_inside_another_of_its_skill(self) -> None:
        # Two phrases whose words are all held back, one inside the other, both
        # score on the longer one's text, and 6 + 6 dispatches what neither
        # phrase could alone ("запомни это на всю жизнь").
        pack = _ru_pack()
        stored = {skill: _folded(tokens) for skill, tokens in pack.whole_phrase_only_tokens}

        def all_held(skill: str, phrase: str) -> bool:
            words = {word for word in routing_tokens(phrase) if not word.isascii()}
            return bool(words) and words <= stored.get(skill, set())

        nested = [
            f"{skill}: {shorter!r} in {longer!r}"
            for skill, phrases in pack.skills
            for longer in phrases
            for shorter in phrases
            if shorter != longer
            and all_held(skill, longer)
            and all_held(skill, shorter)
            and phrase_is_spoken(normalized_phrase(longer), normalized_phrase(shorter))
        ]
        self.assertEqual(nested, [])


class HeldBackPhraseCreditTests(unittest.TestCase):
    """At most one held-back pack phrase scores per skill per message (#2301).

    Two of them in one sentence, overlapping ("месячный отчёт для начальства"
    holds two) or apart, scored 6 + 6 and dispatched an everyday sentence.
    The pack cannot prevent that the way it prevents nesting, because any two
    of a skill's phrases can be said together; the scorer has to.
    """

    def _held_back_phrases(self) -> dict[str, list[str]]:
        pack = _ru_pack()
        stored = {skill: _folded(tokens) for skill, tokens in pack.whole_phrase_only_tokens}
        return {
            skill: [
                phrase
                for phrase in phrases
                # An ASCII word is never held back here, so a phrase with one
                # scores that word too and is not a held-back phrase.
                if (words := routing_tokens(phrase)) and words <= stored.get(skill, set())
            ]
            for skill, phrases in pack.skills
        }

    def _held_back_credits(self, message: str, skill: str, phrases: list[str]) -> int:
        labels = {f"trigger:{normalized_phrase(phrase)}" for phrase in phrases}
        rows = [row for row in recommend_skills(message, limit=200) if row["skill"] == skill]
        return len(labels & set(rows[0]["matched"])) if rows else 0

    def test_two_held_back_phrases_said_together_credit_once(self) -> None:
        doubled = [
            f"{skill}: {first!r} + {second!r}"
            for skill, phrases in self._held_back_phrases().items()
            for index, first in enumerate(phrases)
            for second in phrases[index + 1:]
            if self._held_back_credits(f"{first}, а потом {second}", skill, phrases) > 1
        ]
        self.assertEqual(doubled, [])

    def test_overlapping_held_back_phrases_credit_once(self) -> None:
        phrases = self._held_back_phrases()["report-package"]
        message = "ежемесячный отчёт для начальства"
        self.assertTrue(
            {"ежемесячный отчёт", "отчёт для начальства"} <= set(phrases),
            "the overlap this case pins needs both phrases in the pack",
        )
        self.assertEqual(self._held_back_credits(message, "report-package", phrases), 1)


if __name__ == "__main__":
    unittest.main()
