"""When a message is about a file leaving the team with personal data still in it.

`data-share-review` owns one target shape: a file-shaped carrier (an export, a
dataset, logs, a transcript excerpt) AND an outbound cue (a vendor, a
contractor, a public issue, a model provider, a training corpus, or a verb
that sends it). Its phrases ("redact pii", "pseudonymize", ...) also name
code, Hermes settings, legal questions, scanners, issue triage and messages,
so a phrase counts only beside that shape, and never beside a word that names
one of those other jobs.

Two readers hold the same vocabulary: the router (`omh.routing.policy`, which
gates the skill's score, its boost, and the four destination guards that stand
down for it -- GitHub event ops, the connector operator, the media operator,
and the research-brief vendor guard) and the awareness route hint here in the
plugin bundle.
Stdlib only: this ships in the plugin bundle, which may not import `omh`.
"""
from __future__ import annotations

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
    "customer details out",
    "personal details out",
)

# The carrier: something file-shaped that can leave. "data" alone is not one
# ("strip pii from the training data before fine-tuning" is model-finetuning's).
DATA_SHARE_CARRIER_TOKENS = frozenset(
    {
        "corpus", "csv", "csvs", "dataset", "datasets", "dump", "dumps", "excerpt", "excerpts", "export",
        "exports", "extract", "extracts", "file", "files", "log", "logs", "records", "sample", "samples",
        "spreadsheet", "spreadsheets",
    }
)
# The outbound cue: who receives it, or a verb that sends it.
DATA_SHARE_OUTBOUND_TOKENS = frozenset(
    {
        "agency", "attach", "attaching", "contractor", "contractors", "external", "give", "giving", "hand",
        "handing", "post", "posting", "provider", "public", "researcher", "researchers", "send", "sending",
        "share", "sharing", "third-party", "upload", "uploading", "vendor", "vendors",
    }
)
DATA_SHARE_OUTBOUND_PHRASES = (
    "third party",
    "fine-tune corpus",
    "fine-tune set",
    "fine-tuning set",
    "training corpus",
    "training set",
)
# Words that name another job: a legal question (`legal-compliance-review`), a
# Hermes setting, or code. Beside any of them the phrases claim nothing.
DATA_SHARE_WITHDRAWAL_TOKENS = frozenset(
    {
        # legal
        "ccpa", "compliance", "compliant", "contract", "contracts", "dpa", "gdpr", "hipaa", "lawful", "legal",
        "legally", "regulation", "regulations", "regulatory",
        # Hermes settings
        "config", "configure", "enable", "gateway", "hermes", "setting", "settings",
        # code
        "api", "benchmark", "codebase", "crash", "crashed", "endpoint", "function", "helper", "implement",
        "library", "logger", "middleware", "migration", "pipeline", "refactor", "regex", "script",
    }
)
DATA_SHARE_WITHDRAWAL_PHRASES = (
    "turn on",
    "turn off",
    "my account",
    "google account",
    "data broker",
    "data brokers",
)
# A request whose first word acts on a message, an issue, or a pull request:
# the object is the message or the issue itself, not a file leaving.
DATA_SHARE_MESSAGE_COMMAND_WORDS = frozenset(
    {"assign", "close", "comment", "email", "forward", "label", "merge", "message", "reopen", "reply", "rerun", "triage"}
)
