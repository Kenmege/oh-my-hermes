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

Three narrower forms are closed rather than open. A demo, product or
explainer video needs a production marker or a purpose adjunct, since it is as
often a recording of a fault. A brief that opens on the renderer name instead
of a verb needs a purpose adjunct and nothing after it. And `cut` or `edit`
leads only a reel noun, because editing an everyday video changes one.

Stdlib only; this leaf imports no catalog or router.
"""
from __future__ import annotations

import re
import unicodedata

from .reference_regions import executable_routing_text
from .request_lead import after_courtesy

# A reel noun names an authored film whatever follows it, and these are the
# only nouns `cut` and `edit` may lead: "cut a show reel from last year's
# work" authors one, "edit a promo video, just trim the intro" changes one.
_REEL_PHRASES = (
    "show-reel", "show reel", "showreel", "product showcase video",
    "animated product film", "motion showcase", "animated portfolio",
    "launch film", "product teaser video", "sizzle reel",
)
# Promo, launch, teaser and trailer films by their everyday names. Each is a
# whole phrase: "promo", "launch", "video" and "trailer" alone are not (a bare
# "trailer" is also the one you tow).
_MARKETING_PHRASES = (
    "promo video", "promotional video", "promo clip", "launch video", "product launch video",
    "teaser video", "launch trailer", "teaser trailer", "promo trailer", "promotional trailer",
    "product trailer", "hyperframes video",
)
# A demo, product or explainer video is as often a recording of a fault for a
# ticket as a film for an audience, so one is admitted only with a production
# marker before the noun (`_PRODUCTION_MARKERS` or a duration) or a purpose
# adjunct right after it (`_purpose_adjunct`).
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
_PRODUCTION_MARKERS = frozenset({"animated", "cinematic"})
# `-second`/`-minute` durations such as "60-second" or "2-minute", or the
# same as two words ("30 second").
_DURATION = re.compile(r"\d+-(?:second|sec|minute|min)")
_DURATION_UNITS = frozenset({"second", "seconds", "sec", "minute", "minutes", "min"})
# HyperFrames is an HTML-to-video renderer: a brief that opens on its name
# ("hyperframes promo video for the launch") asks for a film without a verb,
# but only as the noun and a purpose adjunct with nothing after it.
_RENDERER_LEADS = frozenset({"hyperframes"})
# A purpose adjunct is "for", at most one determiner, at most two words from
# these closed lists, the last one a purpose noun, and then the end of the
# sentence: "for the landing page", "for our new TUI". "for the landing page
# won't render" and "for the release regression" are not one.
_PURPOSE_DETERMINERS = frozenset({"a", "an", "the", "our", "my", "your", "their"})
_PURPOSE_NOUNS = frozenset({
    "announcement", "api", "app", "beta", "booth", "cli", "conference", "customers", "dashboard", "docs",
    "event", "extension", "feature", "features", "homepage", "instagram", "investors", "keynote", "launch",
    "linkedin", "marketing", "newsletter", "page", "pitch", "platform", "plugin", "product", "readme",
    "release", "sales", "sdk", "site", "social", "tiktok", "tool", "tui", "twitter", "ui", "website", "youtube",
})
_PURPOSE_MODIFIERS = frozenset({"app", "landing", "launch", "new", "next", "product", "store", "upcoming"})
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
_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*|[,:]")
_NOUN_PHRASES = tuple(sorted({tuple(_WORD.findall(phrase)) for phrase in SHOW_REEL_PHRASES}, key=len, reverse=True))
_REEL_NOUNS = frozenset(tuple(_WORD.findall(phrase)) for phrase in _REEL_PHRASES)
_RECORDING_NOUNS = frozenset(tuple(_WORD.findall(phrase)) for phrase in _RECORDING_PHRASES)


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


def _purpose_adjunct(rest: list[str]) -> bool:
    if not rest or rest[0] != "for":
        return False
    words = rest[1:]
    if words and words[0] in _PURPOSE_DETERMINERS:
        words = words[1:]
    return (
        0 < len(words) <= 2
        and words[-1] in _PURPOSE_NOUNS
        and all(word in _PURPOSE_NOUNS or word in _PURPOSE_MODIFIERS for word in words[:-1])
    )


def _english_production_lead(sentence: str) -> bool:
    words = _WORD.findall(sentence)
    if not words or (words[0] not in _REEL_LEAD_VERBS and words[0] not in _RENDERER_LEADS):
        return False
    verbless = words[0] in _RENDERER_LEADS
    # A renderer lead is read again as the first modifier.
    index = 0 if verbless else 1
    if index < len(words) and words[index] in _DETERMINERS:
        index += 1
    marked = False
    # The noun is tried before each modifier is taken, so "animated" can open
    # "animated product film" as well as qualify "launch film".
    for _ in range(3):
        for phrase in _NOUN_PHRASES:
            if tuple(words[index:index + len(phrase)]) == phrase:
                rest = words[index + len(phrase):]
                if words[0] in _REEL_LEAD_VERBS - _LEAD_VERBS and phrase not in _REEL_NOUNS:
                    return False
                if verbless or (phrase in _RECORDING_NOUNS and not marked):
                    return _purpose_adjunct(rest)
                if rest and rest[0] not in {",", ":"} and rest[0] not in _FOLLOWERS:
                    return False
                return not any(
                    word in _CHAIN_WORDS or (word == "and" and following in _CHAINED_VERBS)
                    for word, following in zip(rest, [*rest[1:], ""])
                )
        if index + 1 < len(words) and words[index].isdigit() and words[index + 1] in _DURATION_UNITS:
            index += 2
            marked = True
            continue
        if index >= len(words) or not (words[index] in _MODIFIERS or _DURATION.fullmatch(words[index])):
            return False
        marked = marked or words[index] in _PRODUCTION_MARKERS or bool(_DURATION.fullmatch(words[index]))
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
    return (not asked and _english_production_lead(sentence)) or bool(_NATIVE_PRODUCTION.search(text))


def show_reel_request_phrases(message: str, phrases: list[str]) -> list[str]:
    """Awareness phrase-reader interface; admission is the evidence, not a noun."""
    del phrases
    return ["request:show_reel_production"] if show_reel_request_admitted(message) else []
