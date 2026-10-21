"""When a message reaches `dag-ulw`: one reading for the router and the in-Hermes route hint.

By name. People type the name with a space, either way round ("dag ulw", "ulw dag"), and in Korean with the
verb glued to it ("dag ulw해줘"). A message invokes the skill when the name opens the request (after a sigil,
``use``, ``run``, ``please`` or a polite ``can you``), when the request runs something with it ("refactor auth
with dag ulw"), or when a Korean run ending follows it ("결제 모듈 dag ulw로 고쳐줘"). A negated mention never does
("don't use dag ulw", "dag ulw 말고"), and neither does a question about the skill ("dag ulw는 뭐야?",
"dag ulw 설명해줘", "dag ulw 결과 알려줘", "how does dag-ulw handle repairs?"): those are answered, the
way a question about any other skill is. A word for the run in progress ("dag ulw stop", "dag ulw 취소",
"dag ulw 상태 보여줘") never does either: it points at that run's `omh_dag` cancel or status. A Korean stop noun
after other words ("dag ulw 주문 취소") is neither a run nor a cancel until the person says which.

By size. A request that says how the work splits or how wide it reaches (`work_has_several_parts`), does not ask
to be told how, and does not name ultrawork is big work for `dag-ulw`; the router reads it only on a route it
already gave `ultrawork`, and the route hint on its coding-delivery hint. Where no hint rule matched, the hint
reads only an outright split (`work_is_split`).

Callers pass the executable text, so a quoted, fenced or relayed mention never reaches these readers.
Stdlib only: the plugin bundle loads this file with Hermes' interpreter (#1623).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

DAG_ULW: Final = "dag-ulw"

_NAME: Final = r"(?:dag[\s_-]*(?:ulw|ultrawork)|(?:ulw|ultrawork)[\s_-]*dag)(?![a-z0-9_-])"
# A request put politely as a question ("can you dag ulw the migration?") still asks to run it.
_POLITE_OPENER: Final = r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)"
_LEAD: Final = re.compile(
    rf"^(?:(?:please|pls)\s+)?{_POLITE_OPENER}?(?:(?:use|run|start)\s+(?:omh\s+)?)?(?:\$|\./|/|@)?{_NAME}"
)
_WITH: Final = re.compile(rf"\b(?:with|via|using|through)\s+(?:the\s+)?{_NAME}")
# The name with a run ending right after it ("dag ulw해줘"), or the name as an instrument ("dag ulw로") and,
# within four words, a request ending ("dag ulw로 결제 모듈 고쳐줘").
_KOREAN_RUN: Final = re.compile(
    rf"{_NAME}\s*(?:해\s*줘|해\s*주세요|해\b|돌려|실행|진행|부탁)"
    rf"|{_NAME}\s*(?:으로|로)\s+(?:\S+\s+){{0,3}}\S*(?:줘|주세요)"
)
_NEGATED: Final = re.compile(
    rf"(?:\b(?:don'?t|do not|never|not|without|instead of|other than)\s+(?:use\s+|run\s+)?(?:the\s+)?{_NAME})"
    rf"|(?:\b(?:stop|quit)\s+(?:using|running)\s+(?:the\s+)?{_NAME})"
    rf"|(?:{_NAME}\s*(?:은|는|을|를)?\s*(?:말고|쓰지|하지\s*마|대신|아니))"
)
# A question about the skill, not a request to run it: a topic or subject particle on the name ("dag ulw는",
# "dag ulw가"); a word right after the name that asks about it (what it is, how it works, what it costs, its
# docs or result); an English verb that describes it ("dag ulw is broken"); or an English question that opens
# before the name, put politely or not ("can you explain dag ulw?"). A question mark anywhere at the end is read
# by `asks_about_dag_ulw` itself.
_ASKED_ABOUT: Final = re.compile(
    rf"{_NAME}\s*(?:은|는|이란|란|이|가)(?![가-힣])"
    rf"|{_NAME}\s*(?:설명|뭐|뭔|무엇|무슨|알려|결과|문서|사용법|어떻게|어때|쓰면|비용|원리|왜)"
    rf"|{_NAME}\s+(?:is|are|was|were|does|should|has|seems?|vs\.?|versus)\b"
    rf"|^{_POLITE_OPENER}?(?:how|what|why|when|where|who|which|is|are|does|should|tell\s+me|explain|describe)\b"
    rf"(?:\s+\S+){{0,6}}?\s+(?:the\s+)?(?:\$|/)?{_NAME}"
)
_POLITE_REQUEST: Final = re.compile(rf"^(?:(?:please|pls)\s+)?{_POLITE_OPENER}")
# A polite question that weighs the run against another way asks about it, not for it ("can you dag ulw this or
# should I just fix it myself?", "could you dag ulw this, or is that overkill?", "would you dag ulw it, if you were
# me?"). An `or` between two targets weighs nothing ("can you dag ulw the auth or billing refactor?").
_OR_ANOTHER_WAY: Final = (
    r"\bor\s+(?:should|shall|is|are|would|will|could|can|do|does|just|not|maybe|keep|leave|skip|ultrawork|ulw)\b"
)
_WEIGHED: Final = re.compile(rf"{_NAME}.*(?:{_OR_ANOTHER_WAY}|아니면)|\bif\s+you\s+were\b")
# A word for the run a person already has, said to the name: stop or cancel it ("dag ulw stop", "dag ulw 멈춰",
# "dag ulw 돌린 거 취소해줘", "cancel dag ulw", "can you stop dag ulw"), or show how it stands ("dag ulw status",
# "dag ulw 상태 보여줘", "dag ulw 진행 상황"). The words must end the request, or be all it asks about, so a thing
# named for them stays work ("dag ulw add a cancel button", "dag ulw 결제 취소 기능 구현해줘",
# "dag ulw 상태 관리 모듈 리팩터링해줘").
_RUN_OBJECT: Final = r"(?:\s+(?:it|this|that|now|please|everything|all|the\s+(?:run|dag|graph|job)))*"
# Korean says a stop two ways. 멈춰, 그만 and 꺼 are a stop whatever comes before them. 취소, 중지, 중단, 정지 and 종료
# are nouns too, and after other words they can name the thing to stop as easily as the run ("dag ulw 주문 취소" is an
# order cancel as much as a cancel of the run). They stop the run with a verb ending, plain or polite, after up to
# three words ("dag ulw 주문 취소해줘", "dag ulw 취소해줘요", "dag ulw 취소 부탁해"); or alone, after stop adverbs or
# after words for the run itself, with a verb ending or a bare 요, 좀 or ㄱ ("dag ulw 취소요", "dag ulw 지금 바로 취소",
# "dag ulw 진행 중인 작업 취소", "dag ulw 그것도 취소"). After other words a bare 요, 좀 or ㄱ is no verb, so
# "dag ulw 주문 취소요" stays the noun phrase. The folded text is NFKC, which turns the jamo ㄱ into its conjoining
# form, so the pattern carries that form.
_KOREAN_STOP_NOUN: Final = r"(?:취소|중지|중단|정지|종료|스톱|스탑)"
_KOREAN_VERB_TAIL: Final = (
    r"\s*(?:좀\s*)?(?:해\s*(?:줘|주세요|줄래|주라|봐|라|버려|줄\s*수\s*있(?:어|나|을까)|주시겠어|주실래|주실\s*수\s*있(?:나|을까))?"
    r"|하자|하세요|할게|할래|합니다|하고\s*싶어"
    r"|해야\s*(?:해|돼|겠어)|시켜\s*(?:줘|주세요)?|둬\s*(?:줘|주세요)?|부탁(?:해|드려|드립니다|합니다)?|줘|주세요|줄래)\s*요?"
)
_KOREAN_PARTICLE_TAIL: Final = rf"\s*(?:요|좀|{unicodedata.normalize('NFKC', 'ㄱ')}+)"
_KOREAN_RUN_MODIFIER: Final = (
    r"(?:현재|지금|이번|이전|방금|아까|다른|모든|전체|전부|모두|그|이|저|진행\s*중인|실행\s*중인|돌리던|돌리는|돌던|돌아가는"
    r"|돌린)"
)
_KOREAN_RUN_NOUN: Final = (
    r"(?:작업|실행|런|run|job|그것도|이것도|그거도|이거도|그거|이거|저거|거|것|전부|모두|다|전체)"
)
_KOREAN_STOP_ADVERB: Final = r"(?:바로|당장|즉시|일단|그냥|얼른|빨리|지금)"
# A stop said as the act of stopping, after the bare stop word or words for the run ("dag ulw 작업 취소 처리해줘",
# "dag ulw 런 중단 요청 보내줘", "dag ulw 취소 눌러줘"): a stop of the run, not work on one.
_KOREAN_STOP_ACT: Final = (
    rf"\s*(?:좀\s*)?(?:(?:처리|진행|요청|실행)(?:{_KOREAN_VERB_TAIL})?|(?:요청|명령)\s*(?:보내|내려)\s*(?:줘|주세요)"
    r"|눌러\s*(?:줘|주세요))\s*요?"
)
_STOP: Final = re.compile(
    rf"{_NAME}[\s,:]*(?:please\s+)?(?:stop|cancel|abort|halt|kill){_RUN_OBJECT}\s*[.!?]*$"
    rf"|^(?:please\s+)?{_POLITE_OPENER}?(?:stop|cancel|abort|halt|kill)\s+(?:the\s+|this\s+|that\s+|my\s+)?"
    rf"(?:current\s+|running\s+)?(?:\$|/)?{_NAME}(?:\s+(?:run|job|graph))?(?:\s+(?:now|please))*\s*[.!?]*$"
    rf"|{_NAME}[\s,:]*(?!으?로\s)(?:의\s*)?(?:\S+\s+){{0,3}}?(?:멈춰(?:라)?|멈추(?:세요|자)?|그만(?:\s*(?:돌려|돌리자))?)"
    rf"(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL})?\s*[.!?~]*$"
    rf"|{_NAME}[\s,:]*(?!으?로\s)(?:의\s*)?(?:\S+\s+){{0,3}}?\S*?{_KOREAN_STOP_NOUN}{_KOREAN_VERB_TAIL}\s*[.!?~]*$"
    rf"|{_NAME}[\s,:]*(?:의\s*)?(?:\S+\s+말고\s+)?(?:(?:{_KOREAN_RUN_MODIFIER}\s*){{0,2}}{_KOREAN_RUN_NOUN}\s*)?"
    rf"(?:{_KOREAN_STOP_ADVERB}\s*){{0,2}}"
    rf"(?:{_KOREAN_STOP_NOUN}(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL}|{_KOREAN_STOP_ACT})?"
    rf"|꺼\s*(?:줘|주세요|라)?\s*요?)\s*[.!?~]*$"
)
# Any other Korean stop word after the name is never a run (owner decision O7). The last stop word decides. With
# nothing after it that names work, it may stop the run or name the work, so it asks which ("dag ulw 주문 취소",
# "dag ulw 취소 요청", "dag ulw 결제 취소 기능 만드는 거 그만해줘"). The message is work instead when:
# - a work noun or verb follows it ("dag ulw 주문 취소 기능 만들어요", "dag ulw 결제 취소 이슈 해결해줘"), or a clause
#   after it makes the stop a condition of the work ("dag ulw 주문 취소 시 환불되게 해줘");
# - past the stop word's own ending, a request verb with its own stem ends the message ("dag ulw 결제 취소 내역
#   확인해줘", "dag ulw 종료 훅 등록해줘"), where a stop request leaves nothing but its ending ("… 작업 취소해줘");
# - a condition clause comes right before it ("dag ulw 결제 모듈 리팩터링하고 실패하면 중단해줘");
# - a work word is the object of 꺼줘 ("dag ulw 다크모드 꺼줘"), unless a word for the run is ("dag ulw 결제 모듈
#   작업 꺼줘").
# A how word after it asks about stopping, and so does a past or passive form ("dag ulw 취소됐어?", "dag ulw
# 멈췄어?") or 여부: those are answered (`asks_about_dag_ulw`).
_STOP_AFTER_NAME: Final = re.compile(
    rf"{_NAME}(?P<before>.*)(?P<stem>{_KOREAN_STOP_NOUN}|멈추|멈춰|멈출|멈췄|그만(?!큼)|꺼\s*(?:줘|주세요|라))(?P<after>.*)$",
    re.DOTALL,
)
_KOREAN_WORK_WORD: Final = re.compile(
    r"기능|로직|버튼|화면|페이지|모듈|컴포넌트|서비스|엔드포인트|핸들러|함수|메서드|클래스|코드|처리|구현|만들|추가|리팩터"
    r"|고쳐|고치|수정|정리|작성|개발|테스트|변경|개선|설계|연동|적용|배포|마이그레이션|흐름|플로우|정책|조건|이벤트|알림|넣"
    r"|붙여|바꿔|분리|나눠|해결|잡아|버그|이슈|디버깅|디버그|최적화|캐싱|캐시|검토|스펙|문서|써\s*(?:줘|주세요)|정의|쿼리"
    r"|스키마|설정|중단점|모드|로그|\b(?:api|ui|button|page|feature|logic|flow|handler|endpoint|dto|tests?)\b"
)
_KOREAN_WORK_CONDITION: Final = re.compile(r"^\s*시\s|[할될]\s*때|[하되]면|\S(?:되게|되도록|하도록)")
_KOREAN_CONDITION_BEFORE: Final = re.compile(r"(?:[하되]면|[할될]\s*때|다가|\s시)\s*$")
_KOREAN_RUN_OBJECT_BEFORE: Final = re.compile(r"(?:작업|거|것|run|job|그거|이거|저거)\s*$")
_KOREAN_STOP_ENDING: Final = re.compile(rf"(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL})")
_KOREAN_REQUEST_VERB: Final = re.compile(r"(?P<stem>\S*?)(?:해)?(?:줘|주세요|줄래|주라)요?|(?P<stem2>\S*?)(?:해요|하자|해라|하세요)")
_KOREAN_NOT_A_VERB_STEM: Final = frozenset(
    {"", "해", "하", "좀", "부탁", "다시", "빨리", "지금", "그냥", "일단", "바로", "얼른", "당장", "꼭", "제발", "어서",
     "게", "되게", "하게", "도록", "되도록"}
)
_KOREAN_HOW: Final = re.compile(r"어떻게|방법|뭐|왜|가능|(?:^|[할될])\s*수\s*있|되나|될까|돼\s*요?\s*[?？]")
_KOREAN_PAST: Final = re.compile(r"^\s*(?:완료\s*)?(?:됐|되었|됨|된|했|하였|한\s*거|뒀)|여부")
# A sigil before a question is a question, not a command ("/dag-ulw what is it?", "/dag-ulw how do I cancel it?"),
# unless a request for work follows it ("/dag-ulw explain the plan, then run the migration", "/dag-ulw is the auth
# refactor done? if not, finish it").
_SIGIL_QUESTION: Final = re.compile(
    r"^(?!.*(?:\b(?:then|and)\s+(?:run|do|start|finish|implement|fix|migrate|build)\b|\bif\s+not\b"
    r"|[,?!.]\s*(?:finish|run|do|fix|start|implement)\b))"
    rf"(?:\$|\./|/){_NAME}[\s,:]+(?:how|what|why|when|where|who|which|is|are|does|do\s+i|can\s+i|should|tell\s+me"
    r"|explain|describe)\b",
    re.DOTALL,
)
# A stop the person already made, said while the run still goes ("dag ulw 취소했는데 계속 돌아가", "dag ulw 취소
# 눌렀는데 계속 돌아가"): stop it again.
_KOREAN_PRESSED: Final = re.compile(r"\s*눌렀")
_REQUEST_BEFORE_STOP: Final = re.compile(r"(?:^|\s)(?:다시\s*)?(?!해|하|좀)\S+?(?:해)?(?:줘|주세요)(?=[\s,.]|$)|다시\s*해\s*(?:줘|주세요)")
_STATUS_REQUEST: Final = re.compile(r"(?:확인|체크|봐|보여|알려|살펴|점검|조회)\S*?(?:해)?(?:줘|주세요)")
_KOREAN_STILL_RUNNING: Final = re.compile(r"(?:계속|아직|여전히)\s*(?:돌아|돌고|실행|진행|안\s*멈)|안\s*(?:멈춰|멈추|꺼져|꺼지)")
# A stop of the run right after the name, then more work ("dag ulw 지금 거 취소하고 결제 모듈부터 다시 해줘",
# "dag ulw 취소하고 다시 처음부터 구현해줘"): stop and restart, or work on a cancel, so it asks before either.
_STOP_THEN_WORK: Final = re.compile(
    rf"{_NAME}[\s,:]*(?:의\s*)?(?:(?:{_KOREAN_RUN_MODIFIER}\s*){{0,2}}{_KOREAN_RUN_NOUN}\s*)?"
    rf"(?:{_KOREAN_STOP_ADVERB}\s*){{0,2}}(?:{_KOREAN_STOP_NOUN}|멈추|그만)\s*(?:하|시키)?고(?:서)?\s"
)
_STATUS: Final = re.compile(
    rf"{_NAME}[\s,:]*(?:status|progress|logs?)(?:\s+(?:please|now|of\s+the\s+(?:run|dag|graph)))*\s*[.!?]*$"
    rf"|{_NAME}[\s,:]*(?:의\s*)?(?:상태|진행\s*상황|진행\s*상태|진행도|로그)\s*(?:좀\s*)?"
    rf"(?:보여|알려|확인|어때|어떻게|는\s*[?？]|[?？]|$)"
)


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def dag_ulw_control(text: str) -> str:
    """``cancel`` or ``status`` when ``text`` says that word to dag-ulw about the run in progress,
    ``cancel_or_run`` when another Korean stop word follows the name with no work after it (`_STOP_AFTER_NAME`), or
    a stop of the run is followed by more work (`_STOP_THEN_WORK`), else ""."""
    folded = _folded(text)
    if _STOP.search(folded):
        return "cancel"
    if _STATUS.search(folded):
        return "status"
    if _STOP_THEN_WORK.search(folded):
        return "cancel_or_run"
    stop = _STOP_AFTER_NAME.search(folded)
    if not stop or _stop_names_work(stop):
        return ""
    after = stop.group("after")
    if (_KOREAN_PAST.search(after) or _KOREAN_PRESSED.match(after)) and _KOREAN_STILL_RUNNING.search(after):
        return "cancel"
    return "" if _asks_about_stopping(stop) else "cancel_or_run"


def _ends_in_its_own_request(after: str) -> bool:
    """Whether, past the stop word's own ending, ``after`` ends in a request verb with its own stem."""
    ending = _KOREAN_STOP_ENDING.match(after)
    words = after[ending.end() if ending else 0 :].strip(" .!?~").split()
    verb = _KOREAN_REQUEST_VERB.fullmatch(words[-1]) if words else None
    if not verb:
        return False
    stem = verb.group("stem") if verb.group("stem") is not None else verb.group("stem2")
    if stem in ("", "해", "하") and len(words) > 1:
        stem = words[-2]
    return stem not in _KOREAN_NOT_A_VERB_STEM


def _stop_names_work(stop: re.Match[str]) -> bool:
    """Whether the words around the last stop word (`_STOP_AFTER_NAME`) make the message work, not a stop."""
    before, stem, after = stop.group("before", "stem", "after")
    if stem.startswith("꺼"):
        return bool(_KOREAN_WORK_WORD.search(before)) and not _KOREAN_RUN_OBJECT_BEFORE.search(before)
    if _KOREAN_WORK_WORD.search(after):
        return True
    if _KOREAN_HOW.search(after):
        return False  # a question about stopping (`_asks_about_stopping`), even after …하면 ("중단하면 어떻게 돼")
    if _KOREAN_WORK_CONDITION.search(after) or _KOREAN_CONDITION_BEFORE.search(before):
        return True
    if _KOREAN_PAST.search(after) and _REQUEST_BEFORE_STOP.search(before) and not _STATUS_REQUEST.search(before):
        return True  # a request of its own, then the stop it names: "dag ulw 다시 돌려줘, 아까 취소한 거"
    return _ends_in_its_own_request(after)


def _asks_about_stopping(stop: re.Match[str] | None) -> bool:
    """Whether the last stop word asks how to stop, or whether it stopped, rather than stopping or naming work."""
    if stop is None or _stop_names_work(stop):
        return False
    after = stop.group("after")
    return bool(_KOREAN_HOW.search(after) or _KOREAN_PAST.search(after) or stop.group("stem") == "멈췄")


def asks_about_dag_ulw(text: str) -> bool:
    """Whether ``text`` names `dag-ulw` to ask about it rather than to run it."""
    folded = _folded(text)
    if not re.search(_NAME, folded):
        return False
    if _ASKED_ABOUT.search(folded) or _SIGIL_QUESTION.match(folded):
        return True
    if _asks_about_stopping(_STOP_AFTER_NAME.search(folded)):
        return True  # "dag ulw 취소 어떻게 해", "dag ulw 멈출 수 있어", "dag ulw 취소됐어?"
    if _POLITE_REQUEST.match(folded):
        return bool(_WEIGHED.search(folded))
    return folded.endswith(("?", "？"))


_SIGIL_LEAD: Final = re.compile(rf"^(?:\$|\./|/){_NAME}")


def invokes_dag_ulw(text: str) -> bool:
    """Whether ``text`` (the executable part of a message) asks to run `dag-ulw`.

    A sigil (``/dag-ulw``, ``$dag-ulw``) is a command whatever follows it, as it is for every skill, except a word
    for the run in progress (`dag_ulw_control`) or a question about the skill or about stopping it: stopping,
    checking or asking never starts a run.
    """
    folded = _folded(text)
    if not folded or _NEGATED.search(folded) or dag_ulw_control(folded):
        return False
    if _SIGIL_LEAD.match(folded):
        return not (_SIGIL_QUESTION.match(folded) or _asks_about_stopping(_STOP_AFTER_NAME.search(folded)))
    if asks_about_dag_ulw(folded):
        return False
    return bool(_LEAD.match(folded) or _WITH.search(folded) or _KOREAN_RUN.search(folded))


def declines_dag_ulw(text: str) -> bool:
    """Whether ``text`` names `dag-ulw` only to decline it ("don't use dag ulw", "dag ulw 말고")."""
    return bool(_NEGATED.search(_folded(text)))


# ── big work by size ─────────────────────────────────────────────────────

# How the WORK is split (lanes, a fan-out, parts integrated afterwards) or how wide it reaches (every module, the
# whole codebase). A bare concurrency word is not one: "in parallel", "concurrently", "병렬로" and "동시에" name
# the bug or the feature as often as the work's shape ("fix the bug where uploads run in parallel",
# "동시에 두 요청이 오면 캐시가 깨지는 버그 고쳐줘"), so they count only beside several named parts
# (`_PART_WORDS`). Each of these left the list after an unrelated sentence using it moved: "then integrate" alone,
# "parallel lanes" alone, "parallel work" and "parallel implementation" as nouns ("the parallel work queue drops
# jobs, fix it"), "fan out" as a noun or of something other than the work ("the fan out of this function is too
# high", "fan out the notifications to all subscribers"), and a Korean concurrency word with a work verb
# ("병렬로 구현", "동시에 진행"); the probes are kept with the measurement.
SHAPE_CUES: Final = (
    "into parallel lanes",
    "into parallel work",
    "into parallel parts",
    "into parallel tasks",
    "in parallel lanes",
    "parallel then integrate",
    "fan out across",
    "병렬로 나눠",
    "나눠서 병렬로",
)
BREADTH_CUES: Final = (
    "across the codebase",
    "across every module",
    "across all modules",
    "across modules",
    "every module",
    "all modules",
    "whole codebase",
    "entire codebase",
    "여러 모듈",
    "모든 모듈",
    "전체 모듈",
    "코드베이스 전체",
)
CONCURRENCY_CUES: Final = ("in parallel", "concurrently", "simultaneously", "at the same time", "병렬로", "동시에")
# Several named parts: an English plural part noun, or a Korean part noun said at least twice
# ("api 모듈과 ui 모듈"), since Korean marks no plural.
_PART_WORDS: Final = frozenset(
    {"modules", "services", "packages", "components", "apps", "repos", "repositories", "microservices", "libraries"}
)
_KOREAN_PART_WORDS: Final = ("모듈", "서비스", "패키지", "컴포넌트")
_ULTRAWORK_STEMS: Final = ("ultrawork", "ulw")
# A shape cue counts only where the work is asked for. Not in the subject of a remark ("the fan out across the
# services is too high"), and not where the split's object is one paragraph or sentence, a small text task ("split
# the paragraph into parallel parts for the translation", "이 문단을 병렬로 나눠서 번역해줘"); a paragraph named
# elsewhere ("the paragraph parser", "fix the paragraph in the README") changes nothing.
_REMARK_SUBJECT: Final = re.compile(
    r"^(?:the|a|an|this|that|these|those|our|its|their|my|your)\s+([^.?!,;]*?)\s(?:is|are|was|were|seems|looks)\b"
)
_SMALL_TEXT_OBJECT: Final = re.compile(
    r"\bsplit\s+(?:the|this|that|a|one|my|your)\s+(?:paragraph|sentence)\s+into\b|(?:이|그|저|한)\s*(?:문단|문장|단락)(?:을|를)\s"
)
# A fault the parts cause together, with one fix asked for: the concurrency word says when the bug happens, not how
# the work splits ("api 모듈과 ui 모듈이 동시에 요청하면 캐시가 깨져, 고쳐줘").
_FAULT: Final = re.compile(
    r"\b(?:breaks|broke|fails|failed|crash(?:es|ed)|hangs|hung|deadlocks|deadlocked|corrupts|corrupted"
    r"|(?:is|are|gets?|got)\s+(?:broken|corrupted|stuck)|times?\s+out|timed\s+out)\b"
    r"|깨져|깨지|깨졌|터져|터지|터졌|죽어|죽었|실패해|실패하|실패했|꼬여|꼬이|꼬였|(?:에러|오류)가?\s*(?:나|났|떠|뜨)"
    r"|충돌이?\s*(?:나|났)|데드락|교착"
)
_FIX_REQUEST: Final = re.compile(r"\b(?:fix|resolve|debug|repair)\b|고쳐|수정해|해결해")
# Unless the concurrency word says how the work or the fixes run ("테스트가 실패하는데 병렬로 고쳐줘", "병렬로 각각
# 고쳐줘", "동시에 진행해줘, 실패하는 테스트도 고쳐줘", "fix them in parallel"): a request, a verb with a request
# ending, follows it at most two words on. In the fault it is a condition ("동시에 요청하면 캐시가 깨져", "동시에
# 처리해서 캐시가 깨져").
_PARALLEL_WORK: Final = re.compile(
    r"(?:병렬로|동시에)\s*(?:(?:각각|따로|모두|전부|다|같이|함께|한꺼번에|\S+해서)\s*){0,2}"
    r"(?:고쳐|수정해|해결해|진행해|작업해|처리해|구현해)\s*(?:줘|주세요|줄래|봐)"
    r"|\bfix\s+(?:them|both|all|each|these|those)(?:\s+of\s+them)?\s+"
    r"(?:in\s+parallel|concurrently|simultaneously|at\s+the\s+same\s+time)\b"
)
# A question or an explain request about how work splits asks to be told, not for the work ("explain how to split
# the work into parallel lanes", "show me how to split ...", "is it possible to split ...", "how do I split the work
# into parallel lanes?", "병렬로 나누는 방법 설명해줘", "병렬로 나눠서 하는 법 알려줘"). Not an opener that proposes the
# work ("how about we split ...", "why don't we split ...", "what we need is to split ...", "tell me when done:
# split ..."), not a Korean explain clause after the work it explains ("... 구현하고 나눈 방법 설명해줘"), and not a
# request put as a question ("can we split ...?", "let's split ...?", "..., ok?", "... 구현해줄 수 있어?").
_ASKS_TO_BE_TOLD: Final = re.compile(
    rf"^{_POLITE_OPENER}?(?:how(?!\s+about)|what(?!\s+(?:we|i)\s+(?:need|want)\b)|why\s+not\s+just\s+explain"
    r"|why(?!\s+(?:don'?t|not)\b)|explain|describe|tell\s+me(?!\s+when)|show\s+me\s+how"
    r"|i(?:\s+(?:want|need)|['’]d\s+like|\s+would\s+like)\s+to\s+know|is\s+it\s+possible)\b"
)
# Talking it over is not asking for it either ("can we talk about how to split ...", "can we discuss whether to
# split ..."), nor is a question that weighs the split against another way ("could we split ..., or is it overkill?").
_TALKS_IT_OVER: Final = re.compile(r"\b(?:talk|think)\s+(?:about|through)\b|\bdiscuss\b|\bwhether\s+(?:to|we|i)\b")
_WEIGHS_THE_SPLIT: Final = re.compile(_OR_ANOTHER_WAY)
_KOREAN_ASKS_TO_BE_TOLD: Final = re.compile(
    r"(?:방법|는\s*법)\s*(?:을|를|이|가)?\s*(?:좀\s*)?(?:설명해|알려|뭐)|지\s*(?:를\s*)?(?:좀\s*)?(?:설명해|알려)"
)
_KOREAN_WORK_FIRST: Final = re.compile(
    r"(?:구현|만들|고치|고쳐|수정|작성|리팩터링|추가|개발|빌드|배포|진행|테스트|마이그레이션|적용|처리)\S*(?:고|서)\s"
)
_REQUEST_AS_QUESTION: Final = re.compile(
    rf"^(?:(?:please|pls)\s+)?(?:{_POLITE_OPENER}|(?:can|could|shall)\s+we\b|let'?s\b)"
    r"|,\s*(?:ok|okay|alright|right)\s*[?？]$|(?:줄\s*수\s*있|주실\s*수\s*있|줄래|주시겠|주겠)\S*\s*[?？]$"
    r"|면\s*어때요?\s*[?？]$"
)


def _cue_text(text: str) -> str:
    """Lower-cased and accent-folded, the way the router's `contains_cue_phrase` reads a message."""
    folded = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(character for character in folded if not unicodedata.combining(character))


def _compact(text: str) -> str:
    return "".join(character for character in text if character.isalnum())


def _contains(text: str, cues: tuple[str, ...]) -> bool:
    """`contains_cue_phrase`'s match: the cue in the text, or with every space and mark taken out of both."""
    compact = _compact(text)
    return any(_cue_text(cue) in text or _compact(_cue_text(cue)) in compact for cue in cues)


def names_ultrawork(text: str) -> bool:
    """Whether a word of ``text`` carries ultrawork's name or alias (`ulw`, `ulw-work`, `ultrawork로`)."""
    words = re.findall(r"[^\W_]+", _folded(text))
    return any(stem in word for word in words for stem in _ULTRAWORK_STEMS)


def _asks_for_a_split(text: str) -> bool:
    """Whether a `SHAPE_CUES` phrase in ``text`` asks for the work to split (`_REMARK_SUBJECT`, `_SMALL_TEXT_OBJECT`).

    The regular expressions read the composed text: `_cue_text` takes Hangul apart into its letters."""
    if not _contains(_cue_text(text), SHAPE_CUES):
        return False
    folded = _folded(text)
    subject = _REMARK_SUBJECT.match(folded)
    if subject and _contains(_cue_text(subject.group(1)), SHAPE_CUES):
        return False
    return not _SMALL_TEXT_OBJECT.search(folded)


def _asks_to_be_told(text: str) -> bool:
    folded = _folded(text)
    if _ASKS_TO_BE_TOLD.search(folded) or _TALKS_IT_OVER.search(folded):
        return True
    if _KOREAN_ASKS_TO_BE_TOLD.search(folded) and not _KOREAN_WORK_FIRST.search(folded):
        return True
    if not folded.endswith(("?", "？")):
        return False
    return bool(_WEIGHS_THE_SPLIT.search(folded)) or not _REQUEST_AS_QUESTION.search(folded)


def work_has_several_parts(text: str) -> bool:
    """Whether ``text`` says its work splits into parts or reaches across many (`SHAPE_CUES`, `BREADTH_CUES`), or
    runs at once across several named parts (a `CONCURRENCY_CUES` phrase beside `_PART_WORDS`) for a reason other
    than a fault it asks to fix (`_FAULT`)."""
    folded = _cue_text(text)
    if _asks_for_a_split(text) or _contains(folded, BREADTH_CUES):
        return True
    if not _contains(folded, CONCURRENCY_CUES):
        return False
    plain = _folded(text)
    if _FAULT.search(plain) and _FIX_REQUEST.search(plain) and not _PARALLEL_WORK.search(plain):
        return False
    words = set(re.findall(r"[^\W_]+", folded))
    korean_parts = sum(folded.count(_cue_text(word)) for word in _KOREAN_PART_WORDS)
    return not words.isdisjoint(_PART_WORDS) or korean_parts >= 2


def work_is_split(text: str) -> bool:
    """Whether ``text`` asks outright for its work to split (`SHAPE_CUES`), does not ask to be told how
    (`_ASKS_TO_BE_TOLD`), and does not name ultrawork.

    The route hint reads this where no rule of its own matched: breadth ("all modules") or a concurrency word
    alone is ordinary in a question about code, while a split ("fan out", "split into parallel") asks for work.
    """
    return not names_ultrawork(text) and not _asks_to_be_told(text) and _asks_for_a_split(text)


def big_work_for_dag_ulw(text: str) -> bool:
    """Whether ``text`` asks for big work by size, rather than to be told about it, and does not name ultrawork,
    which keeps a request named for it."""
    return not names_ultrawork(text) and not _asks_to_be_told(text) and work_has_several_parts(text)
