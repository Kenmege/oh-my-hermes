"""Motion-production admission shared by scoring and standalone awareness.

A format noun is not a request to author that format. `show-reel` is admitted
only by a request that opens on producing one: the first sentence, after the
courtesy openers (`request_lead.COURTESY_OPENERS`), is a verb from a closed
list, at most one indefinite determiner, at most two modifiers from a closed
list, one of the skill's own noun phrases as whole words, and then nothing, a
comma or colon, or a word from `_FOLLOWERS`. A film named as the object of
another request ("make sure the launch film is uploaded", "write a tweet
announcing the launch film"), a causative ("make the launch film shorter",
"make our showreel public") or a bare noun ("showreel") is not one. Explicit
invocation of the skill by name is the router's, not this reader's.

The everyday film nouns this module adds to main's reel nouns (promo, launch,
demo, product, explainer videos and trailers, sizzle reel) are closed rather
than open, because each is as often a clip of a fault for a ticket. One is
admitted only when the rest of its sentence is a chain of closed adjuncts
(`_closed_adjuncts`) and that sentence is the whole message; `cut` and `edit`
never lead one, since editing an everyday video changes an existing one. A
brief that opens on the renderer name instead of a verb is held to the same
form, with at least one adjunct.

Stdlib only; this leaf imports no catalog or router.
"""
from __future__ import annotations

import re
import unicodedata

from .reference_regions import executable_routing_text
from .request_lead import after_courtesy

# main's reel nouns. One names an authored film whatever follows it, reads
# only its first sentence, and these are the only nouns `cut` and `edit` may
# lead: "cut a show reel from last year's work" authors one.
_REEL_PHRASES = (
    "show-reel", "show reel", "showreel", "product showcase video",
    "animated product film", "motion showcase", "animated portfolio",
    "launch film", "product teaser video",
)
# Promo, launch, teaser and trailer films by their everyday names. Each is a
# whole phrase: "promo", "launch", "video" and "trailer" alone are not (a bare
# "trailer" is also the one you tow).
_MARKETING_PHRASES = (
    "promo video", "promotional video", "promo clip", "launch video", "product launch video",
    "teaser video", "launch trailer", "teaser trailer", "promo trailer", "promotional trailer",
    "product trailer", "sizzle reel", "hyperframes video",
)
# A demo, product or explainer video also needs at least one adjunct: "make a
# demo video" alone does not say what it shows.
_RECORDING_PHRASES = ("demo video", "product demo video", "product video", "explainer video")
# Only phrases built from words no other skill's catalog text uses, or that
# show-reel already has, are also catalog triggers (`show_reel_skill`): "demo",
# "clip", "explainer" and "trailer" there would dilute the shortlist anchor of
# the media, connector, paper and commit skills that already own them.
SHOW_REEL_PHRASES = _REEL_PHRASES + _MARKETING_PHRASES + _RECORDING_PHRASES
_LEAD_VERBS = frozenset({"animate", "build", "create", "design", "make", "produce", "write"})
_REEL_LEAD_VERBS = _LEAD_VERBS | {"cut", "edit"}
# Definite and possessive determiners are refused: "make the launch film
# shorter" is a causative over an existing film, not a request to author one.
_DETERMINERS = frozenset({"a", "an"})
_MODIFIERS = frozenset({
    "animated", "bilingual", "brief", "cinematic", "hyperframes", "multilingual", "new", "quick", "short", "vertical",
    "arabic", "chinese", "english", "french", "german", "hindi", "italian", "japanese", "korean",
    "portuguese", "spanish", "vietnamese",
})
# `-second`/`-minute` durations such as "60-second" or "2-minute", or the
# same as two words ("30 second").
_DURATION = re.compile(r"\d+-(?:second|sec|minute|min)")
_DURATION_UNITS = frozenset({"second", "seconds", "sec", "minute", "minutes", "min"})
# HyperFrames is an HTML-to-video renderer: a brief may open on its name
# ("hyperframes promo video for the launch") in place of a verb.
_RENDERER_LEADS = frozenset({"hyperframes"})
# A closed adjunct is a preposition from `_ADJUNCT_PREPOSITIONS`, at most one
# determiner, then one to three words from these closed lists ending on an
# `_ADJUNCT_NOUNS` word: "of our terminal UI", "for the sales team", "about
# the memory system". The words name an audience, a channel, an occasion or a
# product surface, never a state, so "of the login failure", "for the release
# regression" and "showing the checkout hang" are not one.
_ADJUNCT_PREPOSITIONS = frozenset({"about", "covering", "featuring", "for", "of", "showing"})
_ADJUNCT_DETERMINERS = frozenset({"a", "an", "the", "our", "my", "your", "their"})
_ADJUNCT_NOUNS = frozenset({
    "agents", "announcement", "api", "app", "audience", "beta", "board", "booth", "cli", "conference", "customers",
    "dashboard", "docs", "editor", "event", "extension", "feature", "features", "flow", "game", "hackathon",
    "hero", "homepage", "instagram", "integration", "investors", "keynote", "launch", "linkedin", "marketing",
    "marketplace", "meetup", "newsletter", "onboarding", "page", "pitch", "platform", "plugin", "product",
    "readme", "release", "sales", "sdk", "site", "social", "submission", "system", "team", "tier", "tiktok",
    "tool", "tui", "twitter", "ui", "users", "website", "wizard", "workflow", "youtube",
})
_ADJUNCT_MODIFIERS = frozenset({
    "agent", "app", "hackathon", "landing", "launch", "memory", "new", "next", "omh", "onboarding", "plugin",
    "pricing", "product", "sales", "setup", "store", "terminal", "upcoming",
    "arabic", "chinese", "english", "french", "german", "hindi", "italian", "japanese", "korean",
    "portuguese", "spanish", "vietnamese",
})
# A "v4" version may qualify a closed noun. A bare number may not: "the 404
# page" is a fault.
_VERSION = re.compile(r"v\d+")
_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*|[,:]")
_NOUN_PHRASES = tuple(sorted({tuple(_WORD.findall(phrase)) for phrase in SHOW_REEL_PHRASES}, key=len, reverse=True))
_REEL_NOUNS = frozenset(tuple(_WORD.findall(phrase)) for phrase in _REEL_PHRASES)
_RECORDING_NOUNS = frozenset(tuple(_WORD.findall(phrase)) for phrase in _RECORDING_PHRASES)
# "and", "in" and "with" are left out: "make the show reel in the README link
# work" and "make the launch film with subtitles the default" continue a
# causative, not a brief.
_FOLLOWERS = frozenset({"about", "covering", "featuring", "for", "from", "of", "showcasing", "showing", "that", "using", "which"})
# A second request chained on in the same sentence ("make a launch film,
# then deploy the landing page", "... and post it to Slack") belongs to
# whichever skill owns the chain, not to this one.
_CHAIN_WORDS = frozenset({"afterward", "afterwards", "also", "then"})
_CHAINED_VERBS = frozenset({
    "add", "build", "create", "deploy", "do", "email", "file", "fix", "make", "open", "post", "publish",
    "review", "schedule", "send", "share", "submit", "translate", "update", "upload", "write",
})


def _fold(text: str) -> str:
    # The router's normalized_phrase uses NFKD without combining marks.
    # Apply that same fold here, then recompose Hangul, so the raw plugin
    # input and its router-normalized form read the same.
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return unicodedata.normalize("NFKC", "".join(c for c in decomposed if not unicodedata.combining(c)))


# The ko/ja/zh pack nouns keep their own production word order, anchored at
# the start, so a translation or inspection of the noun is not production.
# Case particles mark the Korean/Japanese adverbials; they cannot consume a
# different object.
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


def _closed_adjuncts(rest: list[str]) -> int:
    """How many closed adjuncts make up all of `rest`; -1 when it is anything else."""
    count = 0
    index = 0
    while index < len(rest):
        if rest[index] not in _ADJUNCT_PREPOSITIONS:
            return -1
        index += 1
        if index < len(rest) and rest[index] in _ADJUNCT_DETERMINERS:
            index += 1
        start = index
        while index < len(rest) and (
            rest[index] in _ADJUNCT_NOUNS or rest[index] in _ADJUNCT_MODIFIERS or _VERSION.fullmatch(rest[index])
        ):
            index += 1
        if not 0 < index - start <= 3 or rest[index - 1] not in _ADJUNCT_NOUNS:
            return -1
        count += 1
    return count


def _english_production_lead(sentence: str, *, whole_message: bool) -> bool:
    words = _WORD.findall(sentence)
    if not words or (words[0] not in _REEL_LEAD_VERBS and words[0] not in _RENDERER_LEADS):
        return False
    verbless = words[0] in _RENDERER_LEADS
    # A renderer lead is read again as the first modifier.
    index = 0 if verbless else 1
    if index < len(words) and words[index] in _DETERMINERS:
        index += 1
    # The noun is tried before each modifier is taken, so "animated" can open
    # "animated product film" as well as qualify "launch film".
    for _ in range(3):
        for phrase in _NOUN_PHRASES:
            if tuple(words[index:index + len(phrase)]) == phrase:
                rest = words[index + len(phrase):]
                if phrase not in _REEL_NOUNS or verbless:
                    adjuncts = _closed_adjuncts(rest)
                    least = 1 if verbless or phrase in _RECORDING_NOUNS else 0
                    return whole_message and words[0] in _LEAD_VERBS | _RENDERER_LEADS and adjuncts >= least
                if rest and rest[0] not in {",", ":"} and rest[0] not in _FOLLOWERS:
                    return False
                return not any(
                    word in _CHAIN_WORDS or (word == "and" and following in _CHAINED_VERBS)
                    for word, following in zip(rest, [*rest[1:], ""])
                )
        if index + 1 < len(words) and words[index].isdigit() and words[index + 1] in _DURATION_UNITS:
            index += 2
            continue
        if index >= len(words) or not (words[index] in _MODIFIERS or _DURATION.fullmatch(words[index])):
            return False
        index += 1
    return False


def show_reel_request_admitted(message: str) -> bool:
    """Admit only a request that leads with producing a show reel."""
    folded = _fold(executable_routing_text(message)).strip()
    text = after_courtesy(folded)
    sentence, *ending = re.split(r"([.!?;。！？])(?:\s|$)", text, maxsplit=1)
    # A first sentence asked, not told ("make a show reel? no, just send the
    # old one", "make a launch film, right?") is a question about one, unless
    # a courtesy opener put it as a request ("can you make a show reel?").
    asked = bool(ending) and ending[0] in "?？" and text == folded
    whole_message = not ending or not ending[1].strip()
    return (not asked and _english_production_lead(sentence, whole_message=whole_message)) or bool(_NATIVE_PRODUCTION.search(text))


def show_reel_request_phrases(message: str, phrases: list[str]) -> list[str]:
    """Awareness phrase-reader interface; admission is the evidence, not a noun."""
    del phrases
    return ["request:show_reel_production"] if show_reel_request_admitted(message) else []
