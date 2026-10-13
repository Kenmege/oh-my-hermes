"""When a sentence asks to redact or pseudonymize a file that is leaving the team.

`data-share-review` takes nothing another skill holds: it has no boost and
stands down no guard. This narrow gate decides only whether the skill may be
scored at all, and whether the awareness route hint may name it. The skill's
phrases ("redact pii", "pseudonymize", ...) also name code, Hermes settings,
legal questions, issue triage, messages, posts and videos, and an everyday
verb ("remove", "strip", "take ... out") as often removes something that is
not personal data at all, so all of these must hold:

- the sentence itself (no later clause) begins, after filler such as "hey" or
  "can you", with an inherently protective verb: redact, pseudonymize,
  anonymize, or de-identify. Words are read Unicode-aware, so a sentence that
  opens in Hangul or another script has no Latin lead;
- the verb is a command: it is not defined ("de-identify means ...",
  "de-identify is the term ...") and the sentence is not a question;
- that verb's object does not end in a word for something other than data (a
  label, a flag, a script, a pull request, slides, a header row, ...);
- a file-shaped carrier is leaving: an export, dataset, csv, file, logs,
  records, extract, transcript excerpt, corpus, dump or spreadsheet, beside an
  outbound cue (a vendor, contractor, agency, researcher, the public, a
  provider, a training set, or a verb that sends it);
- no word names a legal question, a Hermes setting, or a storage-wide scan.

Two readers hold this one function: the router (`omh.routing.policy`) and the
awareness route hint here in the plugin bundle. Stdlib only: this ships in the
plugin bundle, which may not import `omh`. Callers pass lower-cased text,
folded the same way as the phrases.
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
# Hermes setting, or a scan across storage.
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

# Unicode-aware, so Hangul and other scripts are words too.
_WORD = re.compile(r"\w[\w'-]*")


def _words(text: str) -> list[str]:
    return [word.split("'")[0] for word in _WORD.findall(text)]


def _without_filler(text: str) -> str:
    text = text.strip()
    changed = True
    while changed:
        changed = False
        for opener in DATA_SHARE_FILLER_OPENERS:
            if text == opener or text.startswith(opener + " ") or text.startswith(opener + ","):
                text = text[len(opener) :].lstrip(" ,")
                changed = True
    return text


def _protective_lead(text: str) -> bool:
    words = _words(_without_filler(text))
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


def _leaving(text: str, tokens: set[str], words: list[str]) -> bool:
    if not tokens & DATA_SHARE_CARRIER_TOKENS:
        return False
    return (
        bool(tokens & DATA_SHARE_OUTBOUND_TOKENS)
        or contains_boundary_phrase(text, DATA_SHARE_OUTBOUND_PHRASES)
        or _gives_it_away(words)
    )


def data_share_request_admitted(text: str) -> bool:
    """A sentence that leads with redact, pseudonymize, anonymize or de-identify a file leaving the team."""
    # A question is not a request to clean a file.
    if text.rstrip().endswith("?"):
        return False
    words = _words(text)
    tokens = set(words) | {part for word in words for part in word.split("-") if part}
    if tokens & DATA_SHARE_WITHDRAWAL_TOKENS or contains_boundary_phrase(text, DATA_SHARE_WITHDRAWAL_PHRASES):
        return False
    return _protective_lead(text) and _leaving(text, tokens, words)
