"""Marker + run cue + adjacency proof shared by native policy and standalone hints.

No catalog import, routing decision or execution. The caller supplies known names;
standalone consumers may read the existing catalog sidecar, never a copied table.
"""
from __future__ import annotations

import re
import unicodedata

from .reference_regions import executable_routing_text
from .request_lead import courtesy_opener_length

OMH_INVOCATION_MARKERS = frozenset({"omh", "oh-my-hermes", "oh-my-hermes-agent", "ohmyhermes"})
SKILL_INVOCATION_MARKERS = frozenset({"skill", "workflow", "스킬", "워크플로우", "워크플로"})
KOREAN_NOUN_PARTICLES = ("으로", "로", "를", "을", "는", "은", "이", "가", "에게", "에", "도", "만")
INVOCATION_RUN_CUES = ("use", "run", "execute", "invoke", "start", "써줘", "써", "쓰자", "사용", "실행", "돌려", "해줘", "해주세요", "부탁")


def fold(message):
    return "".join(c for c in unicodedata.normalize("NFKD", message.casefold()) if not unicodedata.combining(c))


def invocation_token(word):
    token = word.strip(":,.!?~…'\"()[]").lower()
    for particle in KOREAN_NOUN_PARTICLES:
        if token.endswith(particle) and len(token) > len(particle):
            return token[:-len(particle)]
    return token


def contains_run_cue(message, normalize=fold):
    # Restore composed Hangul after the shared fold, then match whole lexical
    # commands. Korean stems accept imperative endings, not arbitrary nouns.
    normalized = unicodedata.normalize("NFC", normalize(message))
    return any(re.fullmatch(
        r"(?<![\w-])(?:use|run|execute|invoke|start|써(?:줘|주세요)?|쓰자|"
        r"(?:사용|실행)(?:해(?:줘|요|주세요)?|하(?:자|세요|라)|합시다)?|"
        r"돌려(?:줘|주세요)?|해줘|해주세요|부탁(?:해(?:줘|요)?|드립니다|드려요|합니다)?)"
        r"(?![\w-])", token.strip(":,.!?~…'\"()[];*‘’“”"),
    ) for token in normalized.split())


def marker_candidates(words, names, aliases=None):
    tokens = [invocation_token(word) for word in words]
    positions = {i for i, token in enumerate(tokens) if token in OMH_INVOCATION_MARKERS or token in SKILL_INVOCATION_MARKERS}
    candidates = []
    for index, token in enumerate(tokens):
        if not {index-1, index+1} & positions:
            continue
        name = token if token in names else (aliases or {}).get(token, "")
        if name in names and name not in candidates:
            candidates.append(name)
    return candidates


# Functional operators compose at the command edge. Do not infer adverbhood
# from an arbitrary -ly suffix: family/reply/assembly and unknown nouns remain
# nominal, even when a later preposition or courtesy would make them tempting.
_EN_COMMAND_ADJUNCTS = frozenset({
    "please", "pls", "plz", "kindly", "now", "again", "once", "twice",
    "immediately", "today", "tomorrow", "tonight", "later", "soon", "then",
    "first", "next", "finally", "only", "just", "also", "even", "still",
    "quietly", "silently", "carefully", "slowly", "quickly", "deliberately",
    "automatically", "manually", "locally", "separately", "together",
})
_EN_DIAGNOSTIC_OPTIONS = frozenset({"-h", "--help", "--version"})
_EN_ARGUMENT_PRONOUNS = frozenset({"it", "them", "this", "that", "these", "those"})
_EN_CLAUSE_SUBJECTS = frozenset({"i", "you", "we", "they", "he", "she", "it"})


def _after_command_adjuncts(words):
    position = 0
    while position < len(words) and words[position] in _EN_COMMAND_ADJUNCTS:
        if words[position] == "once" and position + 1 < len(words) and words[position + 1] in _EN_CLAUSE_SUBJECTS:
            break
        position += 1
    return words[position:]


def _english_command_arguments(words):
    """An operation has arguments; a bare unknown noun may instead name metadata.

    Recognize argument structure, never a vocabulary of metadata or styles. A
    trailing courtesy cannot promote an otherwise ambiguous nominal compound.
    """
    words = _after_command_adjuncts(words)
    if not words:
        return True
    head = words[0]
    if head in _EN_DIAGNOSTIC_OPTIONS:
        return False
    if head in _EN_ARGUMENT_PRONOUNS:
        return True
    if head in {"with", "without", "after", "before", "until", "following", "while", "when", "if", "once", "from", "using", "to", "for", "in", "on", "at", "by", "into", "the", "a", "an", "my", "your", "our", "their", "his", "her", "its"}:
        return len(words) > 1
    # Explicit CLI options, input paths/files and quantity+unit arguments.
    if re.fullmatch(r"--?[a-z][a-z0-9-]*(?:=\S+)?", head):
        return True
    if re.fullmatch(r"(?:\.?\.?/|~/)\S+|[\w-]+(?:/[\w.-]+)+|[\w-]+\.[a-z0-9]{1,12}", head):
        return True
    return bool(re.match(r"^\d+(?:\.\d+)?(?:[- ]?(?:seconds?|secs?|s|minutes?|mins?|m))\b", " ".join(words)))


# Predicate pieces compose with the action noun and optional adjacent fillers.
# The formal/future branches belong to verb stems, not whole request phrases.
_KO_FUTURE = r"겠(?:습니다|어요|습니까)"
_KO_MODAL = r"\s+수\s+있(?:나요|을까요|겠어요)"
_KO_DO = r"(?:해(?:요)?|하(?:자|라|세요|십시오|" + _KO_FUTURE + r")|할(?:게(?:요)?|까요)|합(?:시다|니다))"
_KO_GIVE = r"주(?:세요|십시오|시" + _KO_FUTURE + r"|실(?:래요|까요|" + _KO_MODAL + r"))|줘(?:요)?"
_KO_DEFER = r"드(?:려(?:요)?|리겠(?:습니다|어요|습니까)|립(?:니다|시다)|릴(?:게(?:요)?|까요|" + _KO_MODAL + r"))"
_KO_ADJUNCT = (
    r"(?:좀|조금|꼭|바로|먼저|우선|다시|지금|부디|제발|한\s*번|"
    r"(?!어떻게(?=$|\s))[가-힣]{1,24}(?:히|게))"
)
_KO_ADJUNCTS = r"(?:" + _KO_ADJUNCT + r"\s+)*"
_KO_NEGATIVE_WORD = (
    r"(?:(?:주지|드리지|하지)(?:는|도)?|"
    r"말(?:아(?:줘(?:요)?|주(?:세요|십시오|시겠어요|실래요|실까요))?|자|라|고(?:요)?)?|"
    r"않(?:아(?:요)?|도록|게)?|마(?:세요|십시오|요|라)?)(?=$|\s)"
)
_KO_NEGATIVE_AFTER_ADJUNCTS = re.compile(r"^" + _KO_ADJUNCTS + _KO_NEGATIVE_WORD)
_KO_REQUEST = re.compile(
    r"^" + _KO_ADJUNCTS
    + r"(?:(?:부탁|요청)\s*(?:" + _KO_DEFER + r"|" + _KO_DO + r"(?:\s*(?:" + _KO_GIVE + r"))?)"
    r"|" + _KO_DO + r"(?:\s*(?:" + _KO_GIVE + r"))?)"
    r"(?=$|\s)(?!\s*" + _KO_ADJUNCTS + _KO_NEGATIVE_WORD + r")"
)


def _korean_noun_request(words):
    """Read the adjacent predicate, never search across a reference object."""
    return bool(_KO_REQUEST.match(" ".join(words)))


def affirmative_marked_request(message, name, names):
    """Domain admission preserves genuine named requests, not explanation/reference text.

    This request-lead guard is separate from the generic naming proof; names
    next to markers in an explanatory sentence are not a production operation.
    """
    text = unicodedata.normalize("NFKC", executable_routing_text(message)).casefold().strip()
    words = [word.strip(":,.!?;") for word in text.split()]
    while words and invocation_token(words[0]) not in OMH_INVOCATION_MARKERS:
        count = courtesy_opener_length(words)
        if not count:
            break
        words = words[count:]
    if not words:
        return False
    tokens = [invocation_token(word) for word in words]
    first = tokens[0]
    verbs = {"use", "run", "execute", "invoke", "start"}
    if first in verbs:
        position = 1
        while position < len(tokens) and (tokens[position] == "the" or tokens[position] in OMH_INVOCATION_MARKERS):
            position += 1
        if position >= len(tokens) or tokens[position] != name:
            return False
    else:
        position = 1 if first in OMH_INVOCATION_MARKERS else 0
        if position >= len(tokens) or tokens[position] != name:
            return False
        suffix = words[position+1:]
        if suffix and invocation_token(suffix[0]) in SKILL_INVOCATION_MARKERS:
            suffix = suffix[1:]
        if not suffix:
            return False
        command = suffix[0].strip(":,.!?~…'\"()[];*‘’“”")
        arguments = [word.strip(":,.!?~…'\"()[];*‘’“”") for word in suffix[1:]]
        if command in verbs:
            if not _english_command_arguments(arguments):
                return False
        elif command.startswith(("사용", "실행")):
            predicate = command[2:]
            if predicate in {"을", "를"}:
                if not arguments:
                    return False
                predicate = ""
            if predicate or arguments:
                if not _korean_noun_request(([predicate] if predicate else []) + arguments):
                    return False
        elif command == "부탁" and arguments:
            if not _korean_noun_request([command] + arguments):
                return False
        elif not _korean_noun_request([command] + arguments) and not contains_run_cue(command):
            return False
        # Only adjacent negative auxiliaries negate the command; a content word
        # such as 마케팅 or 말레이시아 must not be rejected by a stem prefix.
        if re.match(r"^(?:사용|실행|부탁|해|돌려|써)", command):
            if _KO_NEGATIVE_AFTER_ADJUNCTS.match(" ".join(arguments)):
                return False
    request_tail = [word.strip(":,.!?~…'\"()[];*‘’“”") for word in words[position+1:]]
    if request_tail and invocation_token(request_tail[0]) in SKILL_INVOCATION_MARKERS:
        request_tail = request_tail[1:]
    request_tail = _after_command_adjuncts(request_tail)
    if request_tail and request_tail[0] in _EN_DIAGNOSTIC_OPTIONS:
        return False
    candidates = marker_candidates(words, names)
    if candidates != [name]:
        return False
    normalized = fold(text)
    negations = (f"{name} 말고", f"{name} 대신", f"not {name}", f"without {name}", f"instead of {name}", f"other than {name}", f"except {name}", f"do not use {name}", f"don't use {name}")
    if any(fold(phrase) in normalized for phrase in negations):
        return False
    # Naming a method AS reference is not requesting that method's output.
    if re.search(r"\b(?:skill|workflow)\s+(?:as|for)\s+(?:a\s+)?reference\b", text):
        return False
    return True
