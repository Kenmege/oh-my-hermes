"""Motion-production admission shared by scoring and standalone awareness.

A format noun is not a request to author that format. Read the requested
operation and its object after shielding reference text. This leaf imports no
catalog/router and makes no inference from words in supplied source material.
"""
from __future__ import annotations

import re
import unicodedata

from .reference_regions import executable_routing_text
from .request_lead import after_courtesy
from .skill_invocation import affirmative_marked_request
from .skill_shortlist import catalog_skill_names

SHOW_REEL_PHRASES = (
    "show-reel", "show reel", "showreel", "product showcase video",
    "animated product film", "motion showcase", "animated portfolio",
    "launch film", "product teaser video",
)
_NOUN = "(?:" + "|".join(re.escape(p) for p in SHOW_REEL_PHRASES) + ")"
# Read a bounded noun phrase, not an adjective vocabulary. A modifier can
# describe style, language or duration, but cannot cross an object/link boundary
# ("a script for a film", "a card using a film") or a local negation.
_OBJECT_BOUNDARY = (
    r"(?:for|of|to|from|with|using|as|about|in|on|by|based|without|and|or"
    r"|that|which|who|not|no|never|do|dont|don’t"
    r"|scripts?|captions?|translations?|narration|copy|reviews?|references?)\b"
)
_MODIFIER = r"(?:(?!" + _OBJECT_BOUNDARY + r")[a-z0-9]+(?:-[a-z0-9]+)*\s+){0,8}"
_PRODUCTION = re.compile(
    r"^(?:make|create|write|produce|animate|design)\s+" + _MODIFIER + _NOUN
    + r"(?![a-z0-9_]|[./-][a-z0-9])"
    # Post-head complements include relative clauses, but never a bare second
    # head such as "film script" or "showreel translation".
    + r"(?=\s*(?:$|[,;.!?]|(?:for|with|using|from|based on|about|in|without|and|that|which|who)\b))"
)
_INVOCATION = re.compile(r"^(?:(?:\$|\./|/)(?:omh-)?show-reel|omh-show-reel)(?:\s|$)")
_NAMED_OPERATION = re.compile(r"^show-reel[:,]?\s+(?:make|create|write|produce|animate|design)\b")


def _fold(text: str) -> str:
    # The router's normalized_phrase uses NFKD without combining marks.
    # Apply that same fold to both operands (not only to the message), then
    # recompose Hangul. Kana dakuten and Latin accents otherwise distinguish
    # the raw plugin input from its router-normalized equivalent.
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return unicodedata.normalize("NFKC", "".join(c for c in decomposed if not unicodedata.combining(c)))


# Qualifiers stay inside the existing pack's production clause. Case particles
# mark the Korean/Japanese adverbials; they cannot consume a different object.
# Chinese courtesy and quantity precede the same shipped noun. Anchor each
# clause so a reference/translation operation cannot expose an inner command.
_NATIVE_PRODUCTION = re.compile(_fold(
    r"^제품\s*쇼케이스\s*영상(?:을|를)?\s*"
    r"(?:(?!(?:참고|바탕))[a-z0-9가-힣-]{1,24}로\s*){0,4}"
    r"(?:만들어\s*(?:줘|주세요)|제작해\s*(?:줘|주세요))(?=$|[\s.!?。！？,、;])"
    r"|^製品ショーケース動画を\s*(?:(?!参考)[a-z0-9一-龯ァ-ヶー-]{1,24}で\s*){0,4}"
    r"(?:作って|制作して)(?:ください)?(?=$|[\s.!?。！？,、;])"
    r"|^(?:请|請)?\s*(?:帮我|幫我)?\s*(?:制作|製作)\s*"
    r"(?:[一二两兩三四五六七八九十\d]+[个個部段支])?\s*产品展示短片"
    r"(?=$|[\s.!?。！？,、;]|为|爲|来|來|用于|用於)"
))


def show_reel_request_admitted(message: str) -> bool:
    """Admit explicit invocation or an affirmative production operation/object.

    NFKC recomposes a router-normalized Hangul input as well as folding wide
    Latin input, so plugin and control-plane callers read the same grammar.
    The existing ko/ja/zh pack nouns keep their own production word order;
    they do not become triggers for translation or inspection of those nouns.
    """
    text = _fold(executable_routing_text(message)).strip()
    lead = after_courtesy(text)
    known_names = catalog_skill_names()
    if known_names and affirmative_marked_request(text, "show-reel", known_names):
        return True
    if (
        lead.rstrip(".!?:,") in {"show-reel", "show reel", "showreel"}
        or _INVOCATION.match(lead)
        or _NAMED_OPERATION.match(lead)
        or _PRODUCTION.match(lead)
    ):
        return True
    # Source-first production is still production; inspect the operation
    # after a source-introducing lead, not every verb in an explanatory topic.
    if re.match(r"^(?:use|using|based on)\b", lead):
        if "," in lead:
            # A source adjunct ends at its comma. Read the immediately
            # requested operation, not a later example inside an explanation.
            return bool(_PRODUCTION.match(lead.split(",", 1)[1].lstrip()))
        for match in re.finditer(r"\b(?:to|and)\s+", lead):
            # Negation belongs to the operation it modifies, not to an
            # unrelated 'not' inside the supplied review/material.
            if re.search(r"\b(?:not|never)\s*$", lead[:match.start()]):
                continue
            if _PRODUCTION.match(lead[match.end():]):
                return True
    return bool(_NATIVE_PRODUCTION.search(lead))


def show_reel_request_phrases(message: str, phrases: list[str]) -> list[str]:
    """Awareness phrase-reader interface; admission is the evidence, not a noun."""
    return ["request:show_reel_production"] if show_reel_request_admitted(message) else []
