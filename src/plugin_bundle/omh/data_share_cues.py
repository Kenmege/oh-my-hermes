"""When a sentence asks to redact or pseudonymize a file that is leaving the company.

The owner decided (2026-10-16) that personal data sent to a team inside the
same company stays internal: the review is for data leaving for an outside
party (a vendor, provider, partner, contractor, third party, or an external
service). The gate fails safe: a place is inside only when it is proven
inside. Every word of it must come from four closed tables, read by both
languages -- anchors (a colleague, staging, QA, internal, ...:
`DATA_SHARE_INTERNAL_DESTINATIONS`), units that need an owner (a team, a
department: `DATA_SHARE_INTERNAL_UNITS`), owners (our, my, another, an
internal function such as data, dev, marketing, finance:
`DATA_SHARE_INTERNAL_OWNERS`) and modifiers (the, a; a server or a wiki:
`DATA_SHARE_INTERNAL_MODIFIERS`) -- and it must say an anchor or an owned
unit. Any other word -- a company, a client, a hospital, a regulator, a name
-- keeps the place outside, so "the vendor's team", "the Acme team", "the
data team at Acme" and "외부 연구팀에" keep the review. A caller whose masker
removed quoted, code, link or relayed text from the request says so
(`shielded`); what the removed text named is unread, so no place is then
proven inside.

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
  unless all of these hold (`_sends_stay_inside`): only closed words stand
  from the lead verb to the first send, its object included ("the pii in
  the export before we"; not "the export acme will get", "the csv the bank
  wants", "before acme sees it"), and a word that names whose records the
  data holds (customer, user, patient, client, member, ...) only right
  before a data container or a field name, not possessive and not after
  "for", "to" or "from" ("the customer export", "the patient csv"; not "the
  customer's export", "the export for the customer"); every verb
  that sends it on -- send, share, upload, attach, post, forward, email, cc,
  transfer -- names after its "to" or "with" a place proven inside ("send it
  to the dev team", "share it with a colleague", "upload it to staging");
  and "give", "hand" or "pass" with no "to" either goes to the person
  ("give me a summary", "hand it back") or has a first object proven inside
  ("give the dev team a copy"; not "give acme a copy", "give them a copy").
  A send that names no place is leaving, and so is one whose place holds
  any other word ("the data team and Acme", "the data team at Acme", "a
  colleague who now works at Acme"). A request that is `shielded`, or that
  still has quotation marks, has no inside place;
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
  "보낼 파일") unless a place proven inside the company or a file or folder
  of the person's own is named ("스테이징에", "개발팀에", "동료한테",
  "엑셀로") and every place the sentence names is one
  (`_korean_stays_inside`). A place's words are read back, however many,
  to a step or a joined form of a verb the rule knows ("마스킹해서",
  "지우고"); a noun that merely ends in 서 or 고 is read on ("경찰서 보안팀에").
  Any other word keeps it outside ("외부 팀에", "은행 담당 부서에", "은행 IT
  보안 개발 팀에", "다른 회사 동료에게", "acme qa에"), as does an English
  recipient anywhere in the sentence ("vendor team에") and a `shielded`
  request;
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
# A place inside the company is proven, never assumed: every word in it must
# come from the four tables below, in both languages, and it must say an
# anchor, or a unit with an owner. Any other word -- the vendor's, an external,
# a partner, a client, a customer, Acme, a hospital, the Department of Health,
# a name -- keeps the place outside and the review firing, which is the safe
# direction: "the customer success team" and "고객지원팀" keep it too. "테스트
# 데이터에서 고객 정보 지우고 스테이징에 올려줘" and "redact pii in the export
# before we send it to the dev team" keep the data inside.
#
# Anchors: places inside by themselves -- a teammate, a colleague or coworker,
# the company's internal or in-house systems, QA, staging, a test or dev
# server. A Korean reader takes a Korean anchor as a word's start ("개발서버",
# "동료들한테"); an English reader takes an entry as whole words.
DATA_SHARE_INTERNAL_DESTINATIONS = (
    "사내", "내부", "스테이징", "개발서버", "테스트", "로컬", "동료",
    "staging", "qa", "teammate", "teammates", "colleague", "colleagues", "coworker", "coworkers", "co-worker",
    "co-workers", "internal", "in-house", "test server", "test environment", "dev server",
)
# Units: a team or a department, inside only with an owner ("our team", "the
# data team", "another department", "우리 팀에", "인사 부서에"), or as a
# compound whose every part is an owner or an anchor ("개발팀", "QA팀"); bare,
# it may be anyone's ("the team", "팀 전체에"). A Korean word that starts with
# a unit and goes on is a member of it ("팀원들한테", "부서원들한테").
DATA_SHARE_INTERNAL_UNITS = ("팀", "부서", "team", "teams", "department", "departments")
# Owners: whose unit it is -- ours, mine, another of ours -- or an internal
# function.
DATA_SHARE_INTERNAL_OWNERS = (
    "우리", "내", "제", "저희", "다른", "타",
    "our", "my", "another",
    "개발", "데이터", "사이언스", "분석", "마케팅", "영업", "재무", "인사", "보안", "플랫폼", "지원", "운영", "제품",
    "기획", "디자인", "연구", "백엔드", "프론트엔드", "인프라", "엔지니어링", "품질",
    "data", "dev", "development", "engineering", "test", "testing", "marketing", "sales", "finance", "hr", "security",
    "platform", "analytics", "support", "ops", "operations", "product", "design", "research", "science", "ml", "ai",
    "bi", "backend", "frontend", "infra", "infrastructure", "it", "mobile", "web", "growth",
)
# Modifiers: words that may stand beside an anchor or an owned unit and prove
# nothing alone -- a determiner ("the data team", "우리 팀 전체에", "같은 팀
# 동료한테"), and a system of the company's (the internal wiki, the staging
# server; 사내 위키, 테스트 서버).
DATA_SHARE_INTERNAL_MODIFIERS = (
    "같은", "전체", "모든",
    "the", "a", "an", "both",
    "서버", "환경", "위키", "대시보드", "툴", "채널", "슬랙", "메신저",
    "servers", "wiki", "dashboard", "tool", "channel", "workspace", "slack",
)
# Korean only: a file or folder the data is exported to ("개인정보 지운 다음
# 엑셀로 내보내줘" writes a spreadsheet). English reads no file or folder as
# inside the company.
_KOREAN_EXPORT_DESTINATIONS = ("엑셀", "csv", "pdf", "json", "xlsx", "txt", "텍스트", "파일", "폴더", "백업")


def _place_kind(word: str) -> str:
    """What one whole word of a place is: "anchor", "unit", "owner", "modifier", or "" for anything else."""
    if word in DATA_SHARE_INTERNAL_DESTINATIONS:
        return "anchor"
    if word in DATA_SHARE_INTERNAL_UNITS:
        return "unit"
    if word in DATA_SHARE_INTERNAL_OWNERS:
        return "owner"
    return "modifier" if word in DATA_SHARE_INTERNAL_MODIFIERS else ""


def _proven_inside(kinds: list[str]) -> bool:
    """Every word is an inside word, and an anchor, or a unit with its owner, is said."""
    return bool(kinds) and all(kinds) and ("anchor" in kinds or ("unit" in kinds and "owner" in kinds))


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
# Every verb that sends the data on, for reading where it goes. Only the first
# set admits a request (`_leaving`); the rest are read so that a later send
# outside is seen ("... so they can forward it to acme", "cc the auditor").
# "give", "hand" and "pass" with no place send nothing ("give me a summary").
_ONWARD_SENDS = DATA_SHARE_SEND_TOKENS | {
    "cc", "email", "emailing", "forward", "forwarding", "give", "giving", "hand", "handing", "pass", "passing",
    "transfer", "transferring",
}
_SENDS_ONLY_WITH_A_PLACE = frozenset({"give", "giving", "hand", "handing", "pass", "passing"})
# "give", "hand" and "pass" with no "to" send nothing only to the person alone
# ("give me a summary", "hand it back"): the person, then the thing given, a
# determiner or nothing. Otherwise their first object is the place ("give acme
# a copy", "give me and acme a copy", "give acme back the file") and the thing
# given is not read as part of it.
_FIRST_PERSON = frozenset({"me", "us", "myself", "ourselves"})
_HANDED_THINGS = DATA_SHARE_CARRIER_TOKENS | {"access", "copies", "copy", "link", "links", "version"}
_AFTER_THE_PERSON = _HANDED_THINGS | {"a", "an", "some", "the", "this", "that"}
_HANDED_BACK = (["it", "back"], ["them", "back"])
# Data containers and the names of personal-data fields. Neither can name a
# party.
_DATA_CONTAINERS = DATA_SHARE_CARRIER_TOKENS | {
    "archive", "archives", "backup", "backups", "column", "columns", "copies", "copy", "data", "database",
    "databases", "db", "document", "documents", "field", "fields", "json", "list", "lists", "report", "reports",
    "row", "rows", "sample", "samples", "sheet", "sheets", "table", "tables", "transcript", "transcripts", "tsv",
    "xlsx",
}
_DATA_FIELDS = frozenset(
    {
        "address", "addresses", "birth", "date", "dates", "details", "dob", "email", "emails", "id", "identifier",
        "identifiers", "ids", "info", "information", "ip", "ips", "name", "names", "number", "numbers", "personal",
        "phone", "phones", "pii", "sensitive", "ssn", "ssns",
    }
)
# The only words that may stand between the lead verb and the first send, its
# object included: where the data is, joining words, the speaker ("we", "us",
# "i", "you", "it"), a determiner, data containers and field names ("the pii
# in the export before we", "the email column from this csv before"). Any other
# word keeps the review: "the export acme will get", "the csv the bank wants",
# "before acme sees it", "for them".
_BEFORE_SEND_WORDS = DATA_SHARE_OBJECT_ENDS | _DATA_CONTAINERS | _DATA_FIELDS | {
    "a", "after", "an", "first", "i", "it", "my", "once", "our", "please", "that", "the", "then", "these", "this",
    "those", "us", "we", "you",
}
# Whose records the data holds. One of these is allowed in that span only as a
# compound modifier: right before a data container or a field name, not
# possessive, and not after "for", "to" or "from" ("the customer export", "the
# patient csv", "the user logs", "the customers table"). Anywhere else it may
# name a recipient and keeps the review: "the customer's export", "the export
# for the customer", "the export the customer wants". A word not on this list
# never qualifies: "the acme export" keeps the review.
DATA_SHARE_PARTY_MODIFIERS = frozenset(
    {
        "account", "accounts", "applicant", "applicants", "client", "clients", "customer", "customers", "donor",
        "donors", "employee", "employees", "member", "members", "patient", "patients", "staff", "student",
        "students", "subscriber", "subscribers", "user", "users",
    }
)
_MODIFIER_BLOCKERS = frozenset({"for", "from", "to"})
_SPAN_DETERMINERS = frozenset({"a", "an", "my", "our", "that", "the", "these", "this", "those"})
# A send's place follows one of these ("send it to the dev team", "share it
# with a colleague") and runs to the next send or the sentence's end.
_DESTINATION_PREPOSITIONS = frozenset({"into", "onto", "to", "with"})
# English words that may join the words of an inside place: "the data team and
# the qa team", "the data team as well as the qa team", "a coworker in
# finance", "the dev team today, please". Each word they join must still be an
# inside word: "the data team and acme" and "our team in seoul" are outside,
# and so is any other joiner ("the data team at acme", "the dev team for
# review").
_PLACE_JOINERS = frozenset({"and", "as", "in", "or", "please", "plus", "today", "well"})
# A route or a party named between a send and its place ("upload it via
# acme's portal to the data team", "send it for acme to the dev team") keeps
# the data leaving.
_SEND_ROUTE_WORDS = frozenset({"at", "by", "for", "from", "on", "through", "using", "via"})
# The multi-word English anchors ("test server"): their words are allowed in a
# place ("the staging server"), but only the whole phrase is an anchor.
_ENGLISH_PHRASE_ANCHORS = tuple(entry for entry in DATA_SHARE_INTERNAL_DESTINATIONS if " " in entry)
_ENGLISH_ANCHOR_PARTS = frozenset(word for entry in _ENGLISH_PHRASE_ANCHORS for word in entry.split())
# Quote marks. A `'` or `’` between two word characters is an apostrophe
# ("it's", "it’s"), not a quote: smart punctuation types the second.
_QUOTES = re.compile(r"[\"“”‘`]|(?<!\w)['’]|['’](?!\w)")
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


def _inside(place: list[str]) -> bool:
    """A place proven inside the company: "the dev team", "a coworker in finance", "the test server"; not "the team".

    Hangul in an English place is no inside word, on either surface's fold.
    """
    if not all(word.isascii() for word in place):
        return False
    kinds = [
        _place_kind(word) or ("modifier" if word in _PLACE_JOINERS or word in _ENGLISH_ANCHOR_PARTS else "")
        for word in place
    ]
    if contains_boundary_phrase(" ".join(place), _ENGLISH_PHRASE_ANCHORS):
        kinds.append("anchor")
    return _proven_inside(kinds)


def _closed_before_send(span: list[str], raw_span: list[str]) -> bool:
    """Whether every word from the lead verb to the first send is a closed word or a compound party modifier."""
    for position, word in enumerate(span):
        if word in _BEFORE_SEND_WORDS:
            continue
        following = span[position + 1] if position + 1 < len(span) else ""
        earlier = next((before for before in reversed(span[:position]) if before not in _SPAN_DETERMINERS), "")
        if (
            word in DATA_SHARE_PARTY_MODIFIERS
            and "'" not in raw_span[position]
            and (following in _DATA_CONTAINERS or following in _DATA_FIELDS)
            and earlier not in _MODIFIER_BLOCKERS
        ):
            continue
        return False
    return True


def _sends_stay_inside(words: list[str], raw_words: list[str]) -> bool:
    """Whether every verb that sends the data on names a place proven inside the company.

    Sends are read after the lead verb's object, which is the data ("redact
    the email column" sends nothing). From the lead verb to the first send,
    its object included, only closed words may stand (`_BEFORE_SEND_WORDS`),
    and a party modifier right before a container or a field
    (`_closed_before_send`). Each send's place runs from its "to" or
    "with" to the next send or the sentence's end, so a second place joined
    to the first is read too ("the data team and acme", "the data team or the
    auditor"). "give", "hand" and "pass" with no "to" take their first object
    as the place, unless it is the person or the data comes back.
    """
    start = next((index for index, word in enumerate(words) if index and word in DATA_SHARE_OBJECT_ENDS), len(words))
    sends = [index for index in range(start, len(words)) if words[index] in _ONWARD_SENDS]
    if sends and not _closed_before_send(words[1 : sends[0]], raw_words[1 : sends[0]]):
        return False
    inside = False
    for number, index in enumerate(sends):
        reach = words[index + 1 : sends[number + 1] if number + 1 < len(sends) else len(words)]
        preposition = next((position for position, word in enumerate(reach) if word in _DESTINATION_PREPOSITIONS), None)
        if preposition is None and words[index] in _SENDS_ONLY_WITH_A_PLACE:
            to_the_person = reach[:1] and reach[0] in _FIRST_PERSON and (reach[1:2] == [] or reach[1] in _AFTER_THE_PERSON)
            if to_the_person or reach[:2] in _HANDED_BACK:
                continue
            place = [word for word in reach if word not in _HANDED_THINGS]
        elif preposition is None:
            return False
        elif set(reach[:preposition]) & _SEND_ROUTE_WORDS:
            return False
        else:
            place = reach[preposition + 1 :]
        if not _inside(place):
            return False
        inside = True
    return inside


def _leaving(text: str, tokens: set[str], words: list[str], shielded: bool) -> bool:
    raw_words = _WORD.findall(text)
    if not tokens & DATA_SHARE_CARRIER_TOKENS:
        return False
    if tokens & DATA_SHARE_RECIPIENT_TOKENS or contains_boundary_phrase(text, DATA_SHARE_RECIPIENT_PHRASES):
        return True
    sends = (
        bool(tokens & DATA_SHARE_SEND_TOKENS)
        or contains_boundary_phrase(text, DATA_SHARE_HAND_PHRASES)
        or _gives_it_away(words)
    )
    return sends and (shielded or bool(_QUOTES.search(text)) or not _sends_stay_inside(words, raw_words))


def data_share_request_admitted(text: str, *, shielded: bool = False) -> bool:
    """A request to redact, pseudonymize, anonymize or de-identify data leaving the company, in English or Korean.

    `shielded`: the caller removed a quoted, code or link region from `text`
    before passing it (`reference_regions`). What was there is unread, so no
    place is then proven inside: 'send it to the "acme" data team' arrives as
    "send it to the data team".
    """
    request = after_courtesy(text.strip())
    words = _words(request)
    tokens = set(words) | {part for word in words for part in word.split("-") if part}
    if tokens & DATA_SHARE_WITHDRAWAL_TOKENS or contains_boundary_phrase(request, DATA_SHARE_WITHDRAWAL_PHRASES):
        return False
    if _korean_admitted(request, tokens, shielded):
        return True
    # A question is not a request to clean a file.
    if text.rstrip().endswith("?"):
        return False
    return _protective_lead(words) and _leaving(request, tokens, words, shielded)


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


# Particles that make a Korean word a place, with 도 ("also") too: "벤더에도
# 보내줘" is a second destination.
_KOREAN_PLACE_PARTICLES = ("에게도", "한테도", "께도", "으로도", "로도", "에도", *_DESTINATION_PARTICLES)
_KOREAN_MEANS_PARTICLES = _MEANS_PARTICLES | {"으로도", "로도"}
# Not places: a step before 에 ("지운 다음에") and a channel before 로
# ("이메일로 보내줘").
_KOREAN_TIMES = frozenset({"다음", "후", "뒤"})
_KOREAN_CHANNELS = frozenset({"이메일", "메일", "슬랙", "메신저"})
# English words in a Korean sentence, also where a particle is glued to them
# ("vendor가").
_LATIN_RUN = re.compile(r"[a-z][a-z-]*")
# Where the words of a place stop, read back from it: a joined verb
# ("지우고", "마스킹해서"), a step ("지운 다음"), or the data as an object
# ("파일을"). Any other word before a place is read as part of it.
_KOREAN_PLACE_EDGES = frozenset({"다음", "다음에", "후", "후에", "뒤", "뒤에"})
# A joined 하다 verb ends the clause only when the rule knows the verb: one
# that protects, removes or sends, or 처리 ("가명 처리해서").
_KOREAN_JOINED_ENDINGS = ("하고서", "하고", "해서", "한")
_KOREAN_EDGE_VERBS = frozenset(
    {*DATA_SHARE_KOREAN_PROTECTIVE_VERBS, *DATA_SHARE_KOREAN_REMOVAL_VERBS, *DATA_SHARE_KOREAN_OUTBOUND_VERBS, "처리"}
)
# The parts a unit's compound prefix may be built from: owners and anchors
# ("데이터사이언스팀", "QA팀"), never the English determiners, which would split
# a name ("ana" as "an" + "a").
_KOREAN_COMPOUND_PARTS = tuple(
    sorted(
        {*DATA_SHARE_INTERNAL_OWNERS, *DATA_SHARE_INTERNAL_DESTINATIONS} - {"my", "our", "another"},
        key=len,
        reverse=True,
    )
)
_KOREAN_ANCHORS = tuple(entry for entry in DATA_SHARE_INTERNAL_DESTINATIONS if not entry.isascii())
_KOREAN_UNITS = tuple(entry for entry in DATA_SHARE_INTERNAL_UNITS if not entry.isascii())


def _korean_compound_inside(prefix: str) -> bool:
    """Whether a unit's compound prefix is all owners and anchors ("데이터사이언스", not "외부", "고객지원", "acme")."""
    while prefix:
        part = next((word for word in _KOREAN_COMPOUND_PARTS if prefix.startswith(word)), "")
        if not part:
            return False
        prefix = prefix[len(part) :]
    return True


def _korean_place_word(word: str) -> str:
    """A Korean place word's kind, as `_place_kind` gives it, reading Korean anchors and units inside compounds."""
    if word.startswith(_KOREAN_ANCHORS) or word.startswith(_KOREAN_EXPORT_DESTINATIONS):
        return "anchor"
    unit = next((unit for unit in _KOREAN_UNITS if word.endswith(unit)), "")
    if unit and word != unit and _korean_compound_inside(word[: -len(unit)]):
        return "anchor"
    if word.startswith(_KOREAN_UNITS) and word not in _KOREAN_UNITS:
        return "anchor"
    return _place_kind(word)


def _korean_edge(word: str) -> bool:
    """Whether `word` ends the clause before a place: a step, or a joined form of a verb this rule knows.

    Only a verb it knows: "마스킹해서", "처리하고", "지우고", "삭제한 (다음)".
    A word that merely ends in 서 or 고 ("경찰서", "물류창고", "삼성하고") is
    read as part of the place, and an unknown one keeps it outside.
    """
    if (
        word in _KOREAN_PLACE_EDGES
        or word in DATA_SHARE_KOREAN_REMOVAL_JOINED
        or word in DATA_SHARE_KOREAN_OUTBOUND_JOINED
        or word in DATA_SHARE_KOREAN_REMOVAL_BEFORE_AFTER
    ):
        return True
    ending = next((ending for ending in _KOREAN_JOINED_ENDINGS if word.endswith(ending) and word != ending), "")
    return bool(ending) and word[: -len(ending)] in _KOREAN_EDGE_VERBS


def _korean_place_inside(words: tuple[str, ...], index: int, stem: str) -> bool:
    """Whether the place ending at `index` is proven inside: its stem and every word before it back to an edge are inside words, one an anchor.

    No window: "은행 IT 보안 개발 팀에" reads 은행 too.
    """
    kinds = [_korean_place_word(stem)]
    for earlier in reversed(words[:index]):
        kind = _korean_place_word(earlier)
        if not kind and _korean_edge(earlier):
            break
        kinds.append(kind)
    return _proven_inside(kinds)


def _korean_stays_inside(words: tuple[str, ...], text: str, shielded: bool) -> bool:
    """Whether every place the data goes is proven inside the company ("개발팀에", "사내 위키에", "엑셀로").

    Fails safe: any place with another word ("외부 팀에", "은행 담당 부서에",
    "다른 회사 동료에게", "acme qa에") or a second place outside ("QA팀에
    보내고 캐글에 올려줘") keeps the data leaving, and so does an English
    recipient anywhere ("vendor team에") or quoted text.
    """
    if shielded or set(_LATIN_RUN.findall(text)) & DATA_SHARE_RECIPIENT_TOKENS or _QUOTES.search(text):
        return False
    inside = False
    for index, word in enumerate(words):
        particle = next((particle for particle in _KOREAN_PLACE_PARTICLES if word.endswith(particle) and word != particle), "")
        if not particle:
            continue
        stem = word[: -len(particle)]
        if stem in _KOREAN_TIMES:
            continue
        if particle in _KOREAN_MEANS_PARTICLES:
            sends_next = index + 1 < len(words) and words[index + 1].startswith(tuple(DATA_SHARE_KOREAN_OUTBOUND_VERBS))
            if not sends_next or stem in _KOREAN_CHANNELS:
                continue
        if not _korean_place_inside(words, index, stem):
            return False
        inside = True
    return inside


def _korean_admitted(text: str, latin_tokens: set[str], shielded: bool) -> bool:
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
    outbound = recipient or (sends and not _korean_stays_inside(words, text, shielded))
    said = recipient or _korean_says(words, DATA_SHARE_KOREAN_PERSONAL_DATA + _KOREAN_PROTECTIVE_PHRASES)
    asks_for_it = final in DATA_SHARE_KOREAN_OUTBOUND_VERBS or _korean_protective(final, request.predicate_index, words)
    return asks_for_it and protective and outbound and said
