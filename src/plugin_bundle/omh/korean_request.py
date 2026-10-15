"""Where a Korean request puts its verb, and the ending that makes it a request.

The two safety gates admit an English request by its first words: a lead verb
after the courtesy openers (`request_lead`), with "please", "can you" or the
like marking it as a request. Korean puts the verb last, and the request is
marked by the verb's ending: "-해줘", "-해 주세요", "-해줄래?", or "부탁해"
after the verb's noun. "결제 시스템 설계해줘" ends on 설계 (design); "내 카드로
결제해줘" ends on 결제 (pay). So the Korean mirror of the English lead is the
first sentence's last predicate, read only when a request ending closes it.

`korean_request(text)` returns that predicate, the 하다 verbs of the clauses
joined before it ("마스킹해서 ... 보내줘", "삭제하고 ... 공유해줘"), and the
sentence's words, or None when the first sentence does not end on a request:
a statement ("결제했어"), a prohibition ("결제하지 마"), a past tense
("송금해 줬어"), or a question that is not itself a request ("결제해줘?"; the
asking forms "-해줄래?" and "-해 줄 수 있어?" are requests). Which predicates
admit a gate is each gate's own closed list (`spend_signals`,
`data_share_cues`), and so is any other verb's joined form ("지우고",
"가려서"), which a gate reads from the words.

A predicate is the verb's noun for a 하다 verb ("결제" from "결제해줘", "결제 좀
해줘", "결제 진행해 주세요", "결제 부탁해"), with a particle such as 을/를/까지
taken off, and the word before the ending for any other verb ("보내" from
"보내줘", "사" from "사 주세요").

The text is read composed (NFC): the router folds Hangul into jamo (NFKD) and
the route hint keeps it composed, and both must read one sentence the same
way. Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

# 주다 as the request auxiliary ("do it for me"), in the forms that ask for it.
REQUEST_AUXILIARIES = (
    "줘",
    "줘요",
    "주세요",
    "주십시오",
    "주라",
    "줄래",
    "줄래요",
    "주실래요",
    "주시겠어요",
    "줄 수 있어",
    "줄 수 있어요",
    "줄 수 있나요",
    "주실 수 있나요",
    "주실 수 있어요",
)
# The forms that ask as a question: "결제해 줄 수 있어?" is a request, while
# "결제해줘?" echoes one. Compared without spaces, as they may be typed.
ASKING_AUXILIARIES = frozenset(
    {"줄래", "줄래요", "주실래요", "주시겠어요", "줄수있어", "줄수있어요", "줄수있나요", "주실수있나요", "주실수있어요"}
)
# 부탁 ("please do") after the verb's noun: "결제 부탁해", "송금 부탁드립니다".
REQUEST_FAVOURS = frozenset({"부탁해", "부탁해요", "부탁할게", "부탁할게요", "부탁합니다", "부탁드려요", "부탁드립니다"})
# Words between a 하다 verb's noun and its ending that change nothing about
# which verb it is: "결제 좀 해줘", "결제 다시 해줘".
SOFTENERS = frozenset({"좀", "다시", "바로", "지금", "먼저", "빨리", "대신", "꼭"})
# 하다 verbs that take their work from the noun before them: "결제 진행해줘"
# pays, "비식별 처리해줘" de-identifies.
LIGHT_VERBS = frozenset({"진행", "처리", "완료"})
# Particles a request leaves on the verb's noun: "결제를 해줘", "결제까지 해줘".
PARTICLES = ("까지", "부터", "을", "를", "도", "만", "은", "는")
# How a 하다 verb's clause joins the next one: "마스킹해서", "삭제하고",
# "처리한 다음에"; and how a clause says what comes after it: "보내기 전에".
_JOINING_ENDINGS = ("하고서", "하고", "해서")
_AFTER_WORDS = frozenset({"다음", "다음에", "뒤", "뒤에", "후", "후에"})
_BEFORE_WORDS = frozenset({"전", "전에"})

_AUXILIARY = re.compile(
    r"(?:^|\s)(?P<head>\S*?)\s?(?P<aux>"
    + "|".join(
        re.escape(aux).replace(r"\ ", r"\s?") for aux in sorted(REQUEST_AUXILIARIES, key=len, reverse=True)
    )
    + r")$"
)
# A sentence ends at ".", "!" or "?" followed by a space or the end; "0.1개"
# and "v2.3" do not end one.
_SENTENCE_END = re.compile(r"[.!?。！？]+(?=\s|$)")
# What may trail a request without changing it: "결제해줘~", "결제해줘 ㅎㅎ",
# "결제해줘요 :)".
_TRAILING = re.compile(r"[\s~^;:()ㄱ-ㆎ☀-➿\U0001f000-\U0001faff]+$")
# Punctuation inside the sentence that only separates words.
_SEPARATORS = re.compile(r"[,;:\"'“”‘’()\[\]<>]")


@dataclass(frozen=True)
class KoreanRequest:
    """The first sentence of a Korean request, read from its end."""

    # The first sentence as the caller passed it, in the caller's own fold,
    # so a caller can read its own phrase table on it.
    sentence: str
    # The same sentence's words, composed (NFC) and lower-cased.
    words: tuple[str, ...]
    # The requested verb, and the index of the word that holds it.
    predicate: str
    predicate_index: int
    # The 하다 verbs of the clauses joined before it, with their word index.
    joined: tuple[tuple[str, int], ...]
    # The verbs of "-기 전에" clauses: "보내" from "보내기 전에", "업로드" from
    # "업로드하기 전에".
    before: tuple[str, ...]


def composed(text: str) -> str:
    """`text` composed (NFC) and lower-cased: how the gates read Korean phrases."""
    return unicodedata.normalize("NFC", text).casefold()


def first_sentence(text: str) -> tuple[str, bool]:
    """`text` up to its first sentence end, and whether that end is a question mark."""
    stripped = text.strip()
    end = _SENTENCE_END.search(stripped)
    if end is None:
        return stripped, False
    return stripped[: end.start()], "?" in end.group() or "？" in end.group()


def without_particle(word: str) -> str:
    """`word` without one particle a request leaves on a verb's noun."""
    for particle in PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            return word[: -len(particle)]
    return word


def _noun_at(words: list[str], index: int) -> tuple[str, int]:
    """The 하다 verb's noun at or before `index`, past softeners and light verbs."""
    while index >= 0 and words[index] in SOFTENERS:
        index -= 1
    if index < 0:
        return "", -1
    noun = without_particle(words[index])
    if noun in LIGHT_VERBS and index > 0:
        return _noun_at(words, index - 1)
    return noun, index


def _hada_noun(words: list[str], index: int, stem: str) -> tuple[str, int]:
    """The noun of a 하다 verb whose word at `index` is `stem` plus its ending."""
    if stem:
        return _noun_at([*words[:index], stem], index)
    return _noun_at(words, index - 1)


def _final_predicate(words: list[str]) -> tuple[str, int, str] | None:
    """The requested verb, its word index and its ending; None without a request ending."""
    if words[-1] in REQUEST_FAVOURS:
        noun, index = _noun_at(words, len(words) - 2)
        return (noun, index, words[-1]) if noun else None
    match = _AUXILIARY.search(" ".join(words))
    if match is None or not match.group("head"):
        return None
    head = match.group("head")
    index = len(" ".join(words)[: match.start("head")].split())
    if head.endswith("해"):
        noun, index = _hada_noun(words, index, head[:-1])
        return (noun, index, match.group("aux")) if noun else None
    return head, index, match.group("aux")


def _joined_clauses(words: list[str], end: int) -> tuple[tuple[tuple[str, int], ...], tuple[str, ...]]:
    joined: list[tuple[str, int]] = []
    before: list[str] = []
    for index in range(end):
        word = words[index]
        following = words[index + 1] if index + 1 < len(words) else ""
        if following in _BEFORE_WORDS and word.endswith("기") and len(word) > 1:
            stem = word[:-1]
            before.append(_hada_noun(words, index, stem[:-1])[0] if stem.endswith("하") else stem)
            continue
        ending = next((ending for ending in _JOINING_ENDINGS if word.endswith(ending)), "")
        if not ending and word.endswith("한") and following in _AFTER_WORDS:
            ending = "한"
        if ending:
            noun, noun_index = _hada_noun(words, index, word[: -len(ending)])
            if noun:
                joined.append((noun, noun_index))
    return tuple(joined), tuple(before)


def korean_request(text: str, *, require_ending: bool = True) -> KoreanRequest | None:
    """The first sentence's requested verb when a request ending closes it, else None.

    With `require_ending=False` (a loop's prompt, which the loop itself marks
    as an instruction) a sentence that ends on a bare 하다 verb's noun or on
    "-해" is read too, as English waives the request marker there.
    """
    sentence, question = first_sentence(text)
    words = _TRAILING.sub("", _SEPARATORS.sub(" ", composed(sentence))).split()
    if not words:
        return None
    final = _final_predicate(words)
    if final is None and not require_ending:
        last = words[-1][:-1] if words[-1].endswith("해") else words[-1]
        final = (without_particle(last), len(words) - 1, "") if last else None
    if final is None:
        return None
    predicate, index, ending = final
    if question and ending.replace(" ", "") not in ASKING_AUXILIARIES:
        return None
    joined, before = _joined_clauses(words, index)
    return KoreanRequest(
        sentence=sentence,
        words=tuple(words),
        predicate=predicate,
        predicate_index=index,
        joined=joined,
        before=before,
    )
