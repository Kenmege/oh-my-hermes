"""When a request asks to take personal data out of a file that is leaving the team.

`data-share-review` is admitted only for a request, never for a topic. The
same phrases ("redact pii", "pseudonymize", ...) name code, Hermes settings,
legal questions, issue triage, messages, blog posts and videos, and a file
leaving the team is often only what such a sentence is about. So three things
must hold together:

- the request leads with a data-protection verb: the sentence, or a clause
  after a comma, a semicolon or "then", begins (after filler such as "hey" or
  "can you") with redact, strip, scrub, remove, mask, pseudonymize, anonymize,
  de-identify, sanitize or minimize, or with "take ... out", "scan ... for
  pii", "check ... for pii", or "review ... before we share/send";
- a file-shaped carrier is leaving: an export, dataset, csv, file, logs,
  records, extract, transcript excerpt, corpus, dump or spreadsheet, beside an
  outbound cue (a vendor, contractor, agency, researcher, the public, a
  provider, a training set, or a verb that sends it);
- one of the skill's phrases is spoken, or the lead is "take ... out" or a
  scan or check for pii, which name the data themselves.

A legal question, a Hermes setting, or a storage-wide scan withdraws it.

Two readers hold this one function: the router (`omh.routing.policy`, for the
skill's score, its boost, and the four destination guards that stand down for
it -- GitHub event ops, the connector operator, the media operator, and the
research-brief vendor guard) and the awareness route hint here in the plugin
bundle. Stdlib only: this ships in the plugin bundle, which may not import
`omh`. Callers pass lower-cased text, folded the same way as the phrases.
"""
from __future__ import annotations

import re

from .boundary_phrase import contains_boundary_phrase

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
)

# Filler a request may open with before its verb.
DATA_SHARE_FILLER_OPENERS = (
    "hey",
    "ok",
    "okay",
    "so",
    "please",
    "i need you to",
    "go ahead and",
    "can you",
    "could you",
    "let's",
    "lets",
    "we need to",
)
# The verbs a request to protect the data leads with.
DATA_SHARE_LEAD_VERBS = frozenset(
    {
        "anonymise", "anonymize", "de-identify", "deidentify", "mask", "minimise", "minimize", "pseudonymise",
        "pseudonymize", "redact", "remove", "sanitise", "sanitize", "scrub", "strip",
    }
)
# "take ... out" leads only when the clause names the data it takes out.
DATA_SHARE_TAKE_OUT_OBJECTS = frozenset({"details", "emails", "identifiers", "names", "personal", "pii"})
DATA_SHARE_SCAN_FOR_PHRASES = ("for pii", "for residual pii", "for personal data")
DATA_SHARE_REVIEW_BEFORE_PHRASES = (
    "before we share",
    "before we send",
    "before i share",
    "before i send",
    "before sharing",
    "before sending",
)

# The carrier: something file-shaped that can leave. "data" alone is not one
# ("strip pii from the training data before fine-tuning" is model-finetuning's),
# and neither is a "sample", which is as often a sample message.
DATA_SHARE_CARRIER_TOKENS = frozenset(
    {
        "corpus", "csv", "csvs", "dataset", "datasets", "dump", "dumps", "excerpt", "excerpts", "export",
        "exports", "extract", "extracts", "file", "files", "log", "logs", "records", "spreadsheet", "spreadsheets",
    }
)
# The outbound cue: who receives it, or a verb that sends it. "hand" counts only
# as handing something over, and "give" only with a recipient (`_gives_it_away`).
DATA_SHARE_OUTBOUND_TOKENS = frozenset(
    {
        "agency", "attach", "attaching", "contractor", "contractors", "external", "post", "posting", "provider",
        "public", "researcher", "researchers", "send", "sending", "share", "sharing", "third-party", "upload",
        "uploading", "vendor", "vendors",
    }
)
DATA_SHARE_OUTBOUND_PHRASES = (
    "third party",
    "fine-tune corpus",
    "fine-tune set",
    "fine-tuning set",
    "training corpus",
    "training set",
    "hand over",
    "hand it",
    "hand them",
    "handing it",
    "handing them",
    "handing over",
)
# Words that name another job: a legal question (`legal-compliance-review`), a
# Hermes setting, or a scan across storage. Beside any of them the request is
# not this skill's.
DATA_SHARE_WITHDRAWAL_TOKENS = frozenset(
    {
        # legal
        "ccpa", "compliance", "compliant", "contract", "contracts", "dpa", "gdpr", "hipaa", "lawful", "legal",
        "legally", "regulation", "regulations", "regulatory",
        # Hermes settings
        "config", "configure", "enable", "gateway", "hermes", "setting", "settings",
        # storage-wide scans
        "bucket", "buckets", "drive", "s3",
    }
)
DATA_SHARE_WITHDRAWAL_PHRASES = ("turn on", "turn off", "across our")

_WORD = re.compile(r"[a-z0-9][a-z0-9_-]*")
_CLAUSE_BREAK = re.compile(r"[,;]|\bthen\b")


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _tokens(text: str) -> set[str]:
    words = _words(text)
    return set(words) | {part for word in words for part in word.split("-") if part}


def data_share_phrase_spoken(text: str) -> bool:
    """One of the skill's phrases as its own words; "pseudonymized" is not "pseudonymize"."""
    return contains_boundary_phrase(text, DATA_SHARE_REVIEW_PHRASES)


def _without_filler(clause: str) -> str:
    text = clause.strip()
    changed = True
    while changed:
        changed = False
        for opener in DATA_SHARE_FILLER_OPENERS:
            if text == opener or text.startswith(opener + " "):
                text = text[len(opener) :].lstrip(" ,")
                changed = True
    return text


def _lead(text: str) -> str:
    """How the request leads: "verb", "names_the_data" (take-out or scan-for), or ""."""
    for clause in _CLAUSE_BREAK.split(text):
        request = _without_filler(clause)
        words = _words(request)
        if not words:
            continue
        verb = words[0]
        if verb in DATA_SHARE_LEAD_VERBS:
            return "verb"
        if verb == "take" and "out" in words[1:] and DATA_SHARE_TAKE_OUT_OBJECTS & set(words):
            return "names_the_data"
        if verb in {"scan", "check"} and contains_boundary_phrase(request, DATA_SHARE_SCAN_FOR_PHRASES):
            return "names_the_data"
        if verb == "review" and contains_boundary_phrase(request, DATA_SHARE_REVIEW_BEFORE_PHRASES):
            return "verb"
    return ""


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


def _leaving(text: str, tokens: set[str]) -> bool:
    if not tokens & DATA_SHARE_CARRIER_TOKENS:
        return False
    return (
        bool(tokens & DATA_SHARE_OUTBOUND_TOKENS)
        or contains_boundary_phrase(text, DATA_SHARE_OUTBOUND_PHRASES)
        or _gives_it_away(_words(text))
    )


def data_share_request_admitted(text: str) -> bool:
    """A request, led by a data-protection verb, to clean a file before it leaves the team."""
    tokens = _tokens(text)
    if tokens & DATA_SHARE_WITHDRAWAL_TOKENS or contains_boundary_phrase(text, DATA_SHARE_WITHDRAWAL_PHRASES):
        return False
    if not _leaving(text, tokens):
        return False
    lead = _lead(text)
    if not lead:
        return False
    return lead == "names_the_data" or data_share_phrase_spoken(text)
