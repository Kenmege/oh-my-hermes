"""When a sentence asks to redact or pseudonymize a file that is leaving the company.

The owner decided (2026-10-16) that personal data sent to a team inside the
same company stays internal: the review is for data leaving for an outside
party (a vendor, provider, partner, contractor, third party, or an external
service). A team qualified by an outside word is outside ("the vendor's team",
"an external team", "외부 연구팀에"). Both languages read one table of
places inside the company (`DATA_SHARE_INTERNAL_DESTINATIONS`) and one of the
words that put a place outside (`DATA_SHARE_OUTSIDE_QUALIFIERS`).

`data-share-review` takes nothing another skill holds: it has no boost and
stands down no guard. This narrow gate decides only whether the skill may be
scored at all, and whether the awareness route hint may name it. The skill's
phrases ("redact pii", "pseudonymize", ...) also name code, Hermes settings,
legal questions, issue triage, messages, posts and videos, and an everyday
verb ("remove", "strip", "take ... out") as often removes something that is
not personal data at all, so all of these must hold:

- the sentence itself (no later clause) begins, after the courtesy openers
  every reader of a request's opening strips ("hey", "hi,", "pls", "would
  you", "hermes,", ...: `request_lead.COURTESY_OPENERS`), with an inherently
  protective verb: redact, pseudonymize, anonymize, or de-identify. Words are
  read Unicode-aware, so a sentence that opens in Hangul or another script
  has no Latin lead;
- the verb is a command: it is not defined ("de-identify means ...",
  "de-identify is the term ...") and the sentence is not a question;
- that verb's object does not end in a word for something other than data (a
  label, a flag, a script, a pull request, slides, a header row, ...);
- a file-shaped carrier is leaving: an export, dataset, csv, file, logs,
  records, extract, transcript excerpt, corpus, dump or spreadsheet, beside a
  recipient outside the company (a vendor, contractor, agency, researcher,
  the public, a provider, a training set), or beside a verb that sends it
  unless every such verb names a place inside the company as where it goes
  ("send it to the dev team", "share it with a colleague", "upload it to
  staging"; `_sends_stay_inside`). A send that names no place is leaving;
- no word after the openers names a legal question, Hermes (a setting of it,
  or Hermes doing the job: "before hermes uploads it to the vendor"), or a
  storage-wide scan. "hermes," as the address before the request is an
  opener, not a mention: "hermes, redact pii in the export before we send
  it to the vendor" is admitted like "hey, ...".

A Korean request puts its verb last, so its half of the rule
(`_korean_admitted`) reads the first sentence from its end
(`korean_request`), and the request ending ("-해줘", "-해 주세요",
"부탁해") is what marks it as a request. All of this must hold:

- the sentence ends on a request to protect the data or to send it: its last
  predicate is a protective verb ("고객 정보 마스킹해줘") or an outbound one
  ("... 마스킹해서 벤더한테 보내줘");
- the data is protected: an inherently protective verb (익명화, 가명화,
  가명 처리, 비식별화), or a verb that removes something (마스킹, 삭제, 제거,
  블러, 지우-, 가리-, 빼-) with personal data among the three words before
  it ("개인정보 지우고", "고객 정보 마스킹해서"), as the last predicate or in a
  clause joined to it; "사진 배경 마스킹해서 보내줘" removes a background;
- it leaves: a recipient outside the company named as where it goes
  ("벤더한테", "외부에", "협력업체에"; not "업체 목록에서", "외부 로그" or
  "업체별로"), or a verb that sends it (a predicate, a "-기 전에" clause,
  "보낼 파일") to no place inside the company and no file or folder of the
  person's own ("스테이징에", "개발팀에", "동료한테", "엑셀로"), unless an
  outside word qualifies that place ("외부 팀에", "벤더 테스트 서버에");
- no word names a legal question, a Hermes setting, a storage-wide scan, or
  something other than data (코드, 함수, 기능, 버그, ...): "개인정보 마스킹
  기능 만들어서 벤더한테 보내줘" ships a feature;
- one of the skill's Korean phrases is said.

Two readers hold this one function: the router (`omh.routing.policy`) and the
awareness route hint here in the plugin bundle. Stdlib only: this ships in the
plugin bundle, which may not import `omh`. Callers pass lower-cased text,
folded the same way as the phrases.
"""
from __future__ import annotations

from collections.abc import Iterable
import re

from .boundary_phrase import contains_boundary_phrase
from .korean_request import composed, korean_request
from .request_lead import after_courtesy

# The Korean half of the rule (`_korean_admitted`). Personal data a Korean
# request names: personal, customer and sensitive information, identifying
# information and identifiers, a resident registration number, a phone
# number, contact details, an account or card number, a real name, an email
# address. These are phrases the router scores.
DATA_SHARE_KOREAN_PERSONAL_DATA = (
    "개인정보",
    "개인 정보",
    "고객 정보",
    "고객정보",
    "민감정보",
    "민감 정보",
    "식별 정보",
    "식별정보",
    "식별자",
    "주민번호",
    "주민등록번호",
    "전화번호",
    "연락처",
    "계좌번호",
    "카드번호",
    "실명",
    "이메일 주소",
)
# Field names that are personal data only in this frame -- removed, and the
# data then leaving -- and everyday words elsewhere: a name, an email, an
# address, a birth date, a user id, a nickname, an employee or student
# number, an IP, "PII" said in English. They admit a request but are no
# phrase of the skill, so one of its phrases must still be said
# (`_korean_admitted`).
DATA_SHARE_KOREAN_FIELD_NAMES = ("이름", "이메일", "주소", "생년월일", "아이디", "닉네임", "사번", "학번", "ip", "pii")
# Verbs that protect data whatever their object: anonymize, pseudonymize,
# de-identify, also as "<noun> 처리" ("익명 처리", "가명 처리", "비식별 처리").
DATA_SHARE_KOREAN_PROTECTIVE_VERBS = frozenset(
    {"익명화", "익명처리", "익명", "가명화", "가명처리", "가명", "비식별화", "비식별처리", "비식별"}
)
# Verbs that protect data only when it is personal data they remove: mask,
# delete, remove, blur, hash, star out ("별표 처리"), and erase, cover and take
# out ("지워줘", "가려줘", "빼줘"), with their joined forms ("지우고", "지운
# 다음에").
DATA_SHARE_KOREAN_REMOVAL_VERBS = frozenset({"마스킹", "삭제", "제거", "블러", "해시", "별표", "지워", "가려", "빼"})
DATA_SHARE_KOREAN_REMOVAL_JOINED = frozenset({"지우고", "지워서", "가리고", "가려서", "빼고", "빼서"})
DATA_SHARE_KOREAN_REMOVAL_BEFORE_AFTER = frozenset({"지운", "가린", "뺀"})
# Verbs that send it: share, forward, upload, provide, take out, attach,
# publish, submit, send, hand over, post, export; with their joined,
# "-기 전에" and future ("보낼 파일", "공유할 거니까") forms.
DATA_SHARE_KOREAN_OUTBOUND_VERBS = frozenset(
    {"공유", "전달", "업로드", "제공", "반출", "첨부", "공개", "제출", "보내", "보내드려", "넘겨", "올려", "내보내", "넘기", "올리"}
)
DATA_SHARE_KOREAN_OUTBOUND_JOINED = frozenset({"보내고", "보내서", "넘기고", "넘겨서", "올리고", "올려서", "내보내고", "내보내서"})
DATA_SHARE_KOREAN_OUTBOUND_FUTURE = frozenset({"보낼", "넘길", "올릴", "내보낼"})
# Who receives it outside the team: a vendor, the outside, a company, a
# partner, a client firm, a third party, an outsourcer, an agency, a
# researcher. One counts as where the data goes only with a particle that
# names a destination ("벤더한테", "외부로", "협력업체에"), or right before a
# verb that sends ("외부 공유용으로"): "업체 목록에서 전화번호 삭제해줘" deletes
# from a list, and "외부 로그" is a kind of log (`_korean_recipient`).
DATA_SHARE_KOREAN_RECIPIENTS = (
    "벤더",
    "외부",
    "업체",
    "협력사",
    "파트너사",
    "거래처",
    "제3자",
    "외주",
    "대행사",
    "에이전시",
    "연구자",
    "연구기관",
)
_DESTINATION_PARTICLES = ("에게", "한테", "께", "쪽에", "측에", "쪽으로", "으로", "에", "로")
_MEANS_PARTICLES = frozenset({"쪽으로", "으로", "로"})
# Places inside the company, in both languages: a team of the person's own, a
# teammate or a colleague, another department, the company's internal or
# in-house systems, QA, staging, and a test or dev server. A verb that sends
# counts as the data leaving unless one of these is where it goes and no
# outside recipient is named: "테스트 데이터에서 고객 정보 지우고 스테이징에
# 올려줘" and "redact pii in the export before we send it to the dev team" keep
# it inside. A Korean reader takes an entry as a word's start ("개발서버",
# "팀원들한테") and a team or department as a word's end ("QA팀한테", "인사부서에");
# an English reader takes an entry as whole words ("the test server").
DATA_SHARE_INTERNAL_DESTINATIONS = (
    "팀", "사내", "내부", "스테이징", "개발서버", "테스트", "로컬", "동료", "부서",
    "staging", "qa", "team", "teams", "teammate", "teammates", "colleague", "colleagues", "coworker", "coworkers",
    "co-worker", "co-workers", "department", "departments", "internal", "in-house", "test server", "test environment",
    "dev server",
)
# Korean only: a file or folder the data is exported to ("개인정보 지운 다음
# 엑셀로 내보내줘" writes a spreadsheet). English reads no file or folder as
# inside the company.
_KOREAN_EXPORT_DESTINATIONS = ("엑셀", "csv", "pdf", "json", "xlsx", "txt", "텍스트", "파일", "폴더", "백업")
# Words that put a place outside the company when they qualify it, in both
# languages, beside each language's recipients: "외부 팀에", "벤더 테스트
# 서버에", "the vendor's team", "an external team", "the partner team at acme",
# "the client's team". Outside wins over inside. A Korean reader takes them as
# a word's start, an English reader as whole words.
DATA_SHARE_OUTSIDE_QUALIFIERS = (
    "외부", "대학", "타사", "고객사",
    "outside", "partner", "partners", "client", "clients", "customer", "customers", "university", "universities",
    "supplier", "suppliers", "consultant", "consultants", "outsourced", "freelance", "freelancer", "freelancers",
)
# Words that name another job, as a word's start: a legal question, a Hermes
# setting, a storage-wide scan, or something other than data. Hermes named
# alone is no withdrawal: "헤르메스," before a request is its address
# (`korean_request.OPENERS`), as "hermes," is in English.
DATA_SHARE_KOREAN_WITHDRAWAL_STEMS = (
    # legal
    "법률", "법적", "법무", "보호법", "개인정보보호법", "처리방침", "규정", "규제", "컴플라이언스",
    # a Hermes setting
    "설정", "게이트웨이",
    # storage-wide scans
    "버킷",
    # not data
    "코드", "함수", "스크립트", "기능", "로직", "정규식", "모듈", "버그", "이슈", "라벨", "슬라이드", "티켓",
)
# The skill's Korean phrases: the personal data, the protective verbs, and the
# recipients. An admitted Korean request always says one of them, so the
# router scores every request this rule admits. They live here, beside the
# English phrases and not in `routing/trigger_packs/ko.json`, for the reason
# the English ones do: the rule reads them, and the bundle cannot read a pack.
_KOREAN_PROTECTIVE_PHRASES = ("익명화", "익명 처리", "익명처리", "가명화", "가명 처리", "가명처리", "비식별")
DATA_SHARE_REVIEW_KOREAN_PHRASES = (
    *DATA_SHARE_KOREAN_PERSONAL_DATA,
    *_KOREAN_PROTECTIVE_PHRASES,
    *DATA_SHARE_KOREAN_RECIPIENTS,
)

# Each phrase names taking personal data out of something. They are also the
# skill's trigger list, word for word (`tests/test_data_share_review.py`).
DATA_SHARE_REVIEW_PHRASES = (
    "data-share-review",
    "data share review",
    "pii redaction",
    "redact pii",
    "pseudonymize",
    "de-identify",
    "anonymize the dataset",
    "residual pii",
    "redact the pii",
    "redact personal data",
    "strip pii",
    "strip the pii",
    "remove pii",
    "remove the pii",
    "scrub pii",
    "scrub the pii",
    "mask pii",
    "mask personal data",
    "remove personal data",
    "scan for pii",
    "pseudonymise",
    "pseudonymizing",
    "pseudonymization",
    "pseudonymisation",
    "de-identifying",
    "deidentify",
    "quasi-identifier",
    "quasi-identifiers",
    "direct identifiers",
    "anonymize this dataset",
    "anonymize the data",
    "anonymize the logs",
    "redact the logs",
    "redact these logs",
    *DATA_SHARE_REVIEW_KOREAN_PHRASES,
)

# Verbs that protect data whatever their object.
DATA_SHARE_LEAD_VERBS = frozenset(
    {"anonymise", "anonymize", "de-identify", "deidentify", "pseudonymise", "pseudonymize", "redact"}
)
# The object runs from the verb up to the first of these words.
DATA_SHARE_OBJECT_ENDS = frozenset({"and", "before", "for", "from", "in", "of"})
# A lead verb followed by one of these is being defined, not asked for:
# "de-identify means what exactly", "de-identify is the term we use".
DATA_SHARE_DEFINITION_WORDS = frozenset({"is", "means", "refers", "stands"})
# An object ending in one of these is not data: "redact the pii redaction
# label", "pseudonymize the export script".
DATA_SHARE_NON_DATA_NOUNS = frozenset(
    {
        "bug", "bugs", "flag", "flags", "function", "functions", "header", "headers", "html", "issue", "issues",
        "label", "labels", "marker", "markers", "notebook", "notebooks", "overhead", "pr", "request", "requests",
        "row", "rows", "script", "scripts", "slides", "step", "steps", "ticket", "tickets",
    }
)
# The carrier: something file-shaped that can leave. "data" alone is not one,
# and neither is a "sample", which is as often a sample message.
DATA_SHARE_CARRIER_TOKENS = frozenset(
    {
        "corpus", "csv", "csvs", "dataset", "datasets", "dump", "dumps", "excerpt", "excerpts", "export",
        "exports", "extract", "extracts", "file", "files", "log", "logs", "records", "spreadsheet", "spreadsheets",
    }
)
# The outbound cue: who receives it outside the company, or a verb that sends
# it. "hand" counts only as handing something over, and "give" only with a
# recipient (`_gives_it_away`).
DATA_SHARE_RECIPIENT_TOKENS = frozenset(
    {
        "agency", "contractor", "contractors", "external", "provider", "public", "researcher", "researchers",
        "third-party", "vendor", "vendors",
    }
)
DATA_SHARE_RECIPIENT_PHRASES = (
    "third party",
    "fine-tune corpus",
    "fine-tune set",
    "fine-tuning set",
    "training corpus",
    "training set",
)
DATA_SHARE_SEND_TOKENS = frozenset(
    {"attach", "attaching", "post", "posting", "send", "sending", "share", "sharing", "upload", "uploading"}
)
DATA_SHARE_HAND_PHRASES = ("hand over", "hand it", "hand them", "handing it", "handing them", "handing over")
# A send's place follows one of these within the send's clause ("send it to
# the dev team", "share it with a colleague").
_DESTINATION_PREPOSITIONS = frozenset({"into", "onto", "to", "with"})
_CLAUSE_ENDS = frozenset(
    {"after", "and", "because", "before", "but", "if", "once", "or", "so", "that", "then", "when", "which", "while", "who"}
)
# The place is the noun phrase after it, up to the clause's end or the next
# preposition: "send it to acme for the dev team" sends it to acme.
_PLACE_ENDS = _CLAUSE_ENDS | {
    "about", "as", "at", "by", "for", "from", "in", "into", "of", "on", "onto", "over", "to", "via", "with",
}
# A place followed by one of these names another organization: "the data team
# at acme", "a team from acme".
_ORGANIZATION_PREPOSITIONS = frozenset({"at", "from"})
# Words that name another job: a legal question (`legal-compliance-review`),
# Hermes (a setting of it, or Hermes doing the job), or a scan across storage.
DATA_SHARE_WITHDRAWAL_TOKENS = frozenset(
    {
        # legal
        "ccpa", "compliance", "compliant", "contract", "contracts", "dpa", "gdpr", "hipaa", "lawful", "legal",
        "legally", "regulation", "regulations", "regulatory",
        # Hermes
        "config", "configure", "enable", "gateway", "hermes", "setting", "settings",
        # storage-wide scans
        "bucket", "buckets", "drive", "s3",
    }
)
DATA_SHARE_WITHDRAWAL_PHRASES = ("turn on", "turn off", "across our")

# Unicode-aware, so Hangul and other scripts are words too.
_WORD = re.compile(r"\w[\w'-]*")


def _words(text: str) -> list[str]:
    return [word.split("'")[0] for word in _WORD.findall(text)]


def _protective_lead(words: list[str]) -> bool:
    if not words or words[0] not in DATA_SHARE_LEAD_VERBS:
        return False
    if words[1:2] and words[1] in DATA_SHARE_DEFINITION_WORDS:
        return False
    obj: list[str] = []
    for word in words[1:]:
        if word in DATA_SHARE_OBJECT_ENDS:
            break
        obj.append(word)
    return not (obj and obj[-1] in DATA_SHARE_NON_DATA_NOUNS)


def _gives_it_away(words: list[str]) -> bool:
    """"give it to the contractor", never "give me" or "give it back"."""
    for index, word in enumerate(words):
        if word not in {"give", "giving"}:
            continue
        following = words[index + 1 : index + 5]
        if following[:1] in (["me"], ["us"]) or "back" in following:
            continue
        if "to" in following:
            return True
    return False


def _place(words: list[str], index: int) -> tuple[list[str], str]:
    """Where the send at `index` says the data goes, and the word that ends the place: (["the", "dev", "team"], "before")."""
    for position in range(index + 1, len(words)):
        if words[position] in _CLAUSE_ENDS or words[position] in DATA_SHARE_SEND_TOKENS:
            break
        if words[position] in _DESTINATION_PREPOSITIONS:
            place: list[str] = []
            for word in words[position + 1 :]:
                if word in _PLACE_ENDS or word in DATA_SHARE_SEND_TOKENS:
                    return place, word
                place.append(word)
            return place, ""
    return [], ""


def _inside(place: list[str], after: str) -> bool:
    """A place inside the company that no outside word qualifies: "the dev team", not "the partner team at acme".

    A recipient ("the vendor's team") never reaches here: it makes the file
    leave first (`_leaving`).
    """
    return (
        contains_boundary_phrase(" ".join(place), DATA_SHARE_INTERNAL_DESTINATIONS)
        and not set(place) & set(DATA_SHARE_OUTSIDE_QUALIFIERS)
        and after not in _ORGANIZATION_PREPOSITIONS
    )


def _sends_stay_inside(words: list[str]) -> bool:
    """Whether every verb that sends the data names a place inside the company as where it goes.

    A send that names no place ("before we send it") is leaving. "give" and
    "hand" without a place send nothing ("give me a summary").
    """
    inside = False
    for index, word in enumerate(words):
        if word not in DATA_SHARE_SEND_TOKENS and word not in {"give", "giving", "hand", "handing"}:
            continue
        place, after = _place(words, index)
        if not place and word not in DATA_SHARE_SEND_TOKENS:
            continue
        if not _inside(place, after):
            return False
        inside = True
    return inside


def _leaving(text: str, tokens: set[str], words: list[str]) -> bool:
    if not tokens & DATA_SHARE_CARRIER_TOKENS:
        return False
    if tokens & DATA_SHARE_RECIPIENT_TOKENS or contains_boundary_phrase(text, DATA_SHARE_RECIPIENT_PHRASES):
        return True
    sends = (
        bool(tokens & DATA_SHARE_SEND_TOKENS)
        or contains_boundary_phrase(text, DATA_SHARE_HAND_PHRASES)
        or _gives_it_away(words)
    )
    return sends and not _sends_stay_inside(words)


def data_share_request_admitted(text: str) -> bool:
    """A request to redact, pseudonymize, anonymize or de-identify data leaving the team, in English or Korean."""
    request = after_courtesy(text.strip())
    words = _words(request)
    tokens = set(words) | {part for word in words for part in word.split("-") if part}
    if tokens & DATA_SHARE_WITHDRAWAL_TOKENS or contains_boundary_phrase(request, DATA_SHARE_WITHDRAWAL_PHRASES):
        return False
    if _korean_admitted(request, tokens):
        return True
    # A question is not a request to clean a file.
    if text.rstrip().endswith("?"):
        return False
    return _protective_lead(words) and _leaving(request, tokens, words)


def data_share_phrases_said(text: str, phrases: Iterable[str] = DATA_SHARE_REVIEW_PHRASES) -> list[str]:
    """Which of `phrases` `text` says: an English one as whole words, a Korean one anywhere.

    The router's reading of a trigger (`phrase_is_spoken` checks Latin edges
    only): Korean writes compounds without a space, so "업체" in "협력업체"
    and "개인정보" in "고객개인정보" start mid-word, and a whole-word reader
    found neither in "고객개인정보 지우고 협력업체에 공유해줘", which the router
    dispatches here.
    """
    folded = composed(text)
    return [
        phrase
        for phrase in phrases
        if (contains_boundary_phrase(text, (phrase,)) if phrase.isascii() else composed(phrase) in folded)
    ]


def _korean_says(words: tuple[str, ...], phrases: Iterable[str]) -> bool:
    said = " ".join(words)
    return any(phrase in said for phrase in phrases)


def _korean_protective(form: str, index: int, words: tuple[str, ...]) -> bool:
    if form in DATA_SHARE_KOREAN_PROTECTIVE_VERBS:
        return True
    removes = form in DATA_SHARE_KOREAN_REMOVAL_VERBS | DATA_SHARE_KOREAN_REMOVAL_JOINED | DATA_SHARE_KOREAN_REMOVAL_BEFORE_AFTER
    window = words[max(0, index - 3) : index + 1]
    return removes and (
        _korean_says(window, DATA_SHARE_KOREAN_PERSONAL_DATA)
        or any(word.startswith(DATA_SHARE_KOREAN_FIELD_NAMES) for word in window)
    )


def _korean_sends(word: str) -> bool:
    """A word that sends the data on later: "보낼", "공유할" ("업체에 보낼 파일", "공유할 거니까")."""
    return word in DATA_SHARE_KOREAN_OUTBOUND_FUTURE or (word.endswith("할") and word[:-1] in DATA_SHARE_KOREAN_OUTBOUND_VERBS)


def _korean_recipient(words: tuple[str, ...]) -> bool:
    """Whether a recipient outside the team is said as where the data goes.

    The word is the recipient, the recipient with 측 or 쪽, or a compound it
    ends ("협력업체"), with a destination particle: "벤더한테", "외부에",
    "협력업체에", "제3자에게". 로/으로 names a destination only right before a
    verb that sends ("외부로 보내줘"); before anything else it names a means or
    a basis ("업체별로", "업체명으로", "외부키로"). A recipient without a
    particle counts only right before a word that sends ("외부 공유해줘",
    "외부 공유용으로"). Not a source ("업체 목록에서"), a kind ("외부 로그"),
    or text inside another word ("해외부서").
    """
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else ""
        sends_next = following.startswith(tuple(DATA_SHARE_KOREAN_OUTBOUND_VERBS))
        particle = next((particle for particle in _DESTINATION_PARTICLES if word.endswith(particle)), "")
        stem = word[: -len(particle)] if particle else word
        names_one = (
            stem in DATA_SHARE_KOREAN_RECIPIENTS
            or stem.endswith(DATA_SHARE_KOREAN_RECIPIENTS)
            or any(stem in (recipient + "측", recipient + "쪽") for recipient in DATA_SHARE_KOREAN_RECIPIENTS)
        )
        if names_one and particle and (particle not in _MEANS_PARTICLES or sends_next):
            return True
        if word in DATA_SHARE_KOREAN_RECIPIENTS and sends_next:
            return True
    return False


def _korean_internal_destination(words: tuple[str, ...]) -> bool:
    """Whether a place inside the company, or a file or folder, is named ("팀에", "스테이징에", "엑셀로", "백업 폴더로").

    Not when an outside word starts the place or one of the two words before
    it, or a recipient ends one of those words: "외부팀에", "외부 팀에", "벤더
    테스트 서버에", "협력업체 보안팀에".
    """
    for index, word in enumerate(words):
        particle = next((particle for particle in _DESTINATION_PARTICLES if word.endswith(particle)), "")
        if not (particle or (index + 1 < len(words) and words[index + 1].endswith(_DESTINATION_PARTICLES))):
            continue
        stem = word[: -len(particle)] if particle else word
        inside = stem.startswith((*DATA_SHARE_INTERNAL_DESTINATIONS, *_KOREAN_EXPORT_DESTINATIONS)) or stem.endswith(
            ("팀", "부서")
        )
        outside = any(
            qualifier.startswith((*DATA_SHARE_KOREAN_RECIPIENTS, *DATA_SHARE_OUTSIDE_QUALIFIERS))
            or qualifier.endswith(DATA_SHARE_KOREAN_RECIPIENTS)
            for qualifier in (*words[max(0, index - 2) : index], stem)
        )
        if inside and not outside:
            return True
    return False


def _korean_admitted(text: str, latin_tokens: set[str]) -> bool:
    """The Korean half: a request ending on protecting the data or sending it, with both said.

    One of the skill's Korean phrases must be said too, so the router scores
    every request this admits: personal data or a protective verb anywhere
    (Korean writes them into compounds, "고객개인정보"), or a recipient as
    where the data goes. A field name alone ("이름 지우고 공유해줘") admits
    nothing.
    """
    request = korean_request(text)
    if request is None:
        return False
    words = request.words
    if any(word.startswith(DATA_SHARE_KOREAN_WITHDRAWAL_STEMS) for word in words):
        return False
    if latin_tokens & DATA_SHARE_NON_DATA_NOUNS:
        return False
    final = request.predicate
    clauses = [(final, request.predicate_index), *request.joined]
    for index, word in enumerate(words[: request.predicate_index]):
        if word in DATA_SHARE_KOREAN_REMOVAL_JOINED or word in DATA_SHARE_KOREAN_OUTBOUND_JOINED:
            clauses.append((word, index))
    protective = any(_korean_protective(form, index, words) for form, index in clauses)
    recipient = _korean_recipient(words)
    sends = (
        any(form in DATA_SHARE_KOREAN_OUTBOUND_VERBS or form in DATA_SHARE_KOREAN_OUTBOUND_JOINED for form, _ in clauses)
        or any(verb in DATA_SHARE_KOREAN_OUTBOUND_VERBS for verb in request.before)
        or any(_korean_sends(word) for word in words)
    )
    outbound = recipient or (sends and not _korean_internal_destination(words))
    said = recipient or _korean_says(words, DATA_SHARE_KOREAN_PERSONAL_DATA + _KOREAN_PROTECTIVE_PHRASES)
    asks_for_it = final in DATA_SHARE_KOREAN_OUTBOUND_VERBS or _korean_protective(final, request.predicate_index, words)
    return asks_for_it and protective and outbound and said
