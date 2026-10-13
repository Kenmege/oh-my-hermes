"""Whether a message asks the agent to spend the person's own money: one rule for the router and the route hint.

`spend-gate` phrases also name the TOPIC, the SUBJECT or a LABEL of a sentence
that asks for something else: a bug report ("pay with my card in safari is
broken"), a product idea ("pay with my card would be nice to have in v2"), a
comparison ("pay with my card or paypal"), button copy ("buy it for me and
wishlist are the two CTAs"), an article, a ticket, an idiom ("pay a visit",
"buy time"), someone else's payment, and the person's own story. Four review
rounds found each of those dispatching the skill or producing its route hint,
and every list of forbidden verbs, nouns or frames was outrun by the next one.

So the rule is closed-world and deliberately conservative: it requires what a
request has, rather than forbidding what a statement has.
`spend_request_clause(text, phrases)` returns the request clause -- the
sentence's first words after filler and courtesy, read Unicode-aware, up to
the first comma, colon, question mark, "and", "if", "when", "which", "about"
and the like -- only when all of this holds, and "" otherwise:
- The message is marked as a request outside the gate's own phrase: "please",
  "can you", "could you", "would you", "will you", "go ahead (and)", "i need
  you to", "i want you to" or "hermes" before the lead; "please" in the
  clause; an amount in the clause; or "for me" in the clause where it is not
  part of a matched phrase ("buy it for me" says it as part of the phrase).
  An unmarked imperative ("pay with my card for the hotel") routes as before
  this skill existed -- an accepted recall cost.
- The clause opens with a spend addressed to the agent ("buy", "pay",
  "order", "book", "renew", "place the order", "complete the checkout", "use
  my card", ...), not an idiom ("pay attention to the spend cap", "pay a
  visit"). "send" and "transfer" also need money, an amount, or the person's
  own card or wallet in the sentence; a reconcile question ("did the payment
  go through") needs the agent to be the one who paid ("after you clicked
  pay"); naming the gate as a command ("use spend gate") opens a clause too.
- One of the given phrases occurs in the clause, and every occurrence is
  followed only by what may follow a request: the end of the clause (but not
  "or", which makes a comparison); "for", "on", "at", "from", "to" or "in";
  "with" followed by the person's own card, wallet or account or by an
  amount; an amount; "please", "now", or "right away".

Everything else returns to the router's base behaviour: the router withdraws
the skill (so no phrase or word of it counts) and the route hint and the
awareness markers stay silent. Nothing here lets the gate take a request
another skill's guard holds; an admitted clause only lets the skill's own
phrases score as usual.

The router (`omh.routing.recommend`) passes the skill's triggers to
`spend_request_clause`; the awareness route hint (`awareness.py`) passes its
own phrase table and its markers to `spend_request_phrases`. Stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_CURRENCY_SYMBOLS = "$€£¥₩"
# Unicode-aware words with apostrophes kept, amounts with their currency
# symbol, and clause punctuation.
_TOKEN = re.compile(rf"[{_CURRENCY_SYMBOLS}]\d[\d.,]*|\w+(?:'\w+)*|[,;:.!?]")

# A request is marked as one, outside the gate's own phrase: one of these in the
# words before the lead, "please" in the clause, an amount in the clause, or
# "for me" outside a matched phrase. "Pay with my card in safari is broken"
# has the phrase and none of these.
SPEND_REQUEST_MARKERS = (
    ("please",),
    ("can", "you"),
    ("could", "you"),
    ("would", "you"),
    ("will", "you"),
    ("go", "ahead"),
    ("i", "need", "you", "to"),
    ("i", "want", "you", "to"),
    ("hermes",),
)
# Stripped, repeatedly and longest first, from the start of the sentence.
SPEND_LEAD_FILLER = (
    ("i", "need", "you", "to"),
    ("i", "want", "you", "to"),
    ("go", "ahead", "and"),
    ("go", "ahead"),
    ("can", "you"),
    ("could", "you"),
    ("would", "you"),
    ("will", "you"),
    ("hey",),
    ("hi",),
    ("hermes",),
    ("ok",),
    ("okay",),
    ("please",),
    ("pls",),
    ("kindly",),
    ("just",),
    ("alright",),
)
_FILLER_PUNCTUATION = frozenset({",", ";", ":", ".", "!", "?"})
# Where the request clause ends. The gate's phrases count only before it, and
# one may follow a phrase ("please buy it for me when it is back in stock").
_CLAUSE_BOUNDARIES = frozenset(
    {
        ",", ";", ":", ".", "!", "?",
        "about", "after", "and", "because", "before", "but", "if", "once", "so", "then", "unless", "until",
        "when", "which", "while",
    }
)
# Leads that ask the agent to spend.
SPEND_LEADS = (
    ("buy",),
    ("pay",),
    ("purchase",),
    ("order",),
    ("book",),
    ("top", "up"),
    ("renew",),
    ("subscribe",),
    ("tip",),
    ("donate",),
    ("check", "out"),
    ("checkout",),
    ("place", "the", "order"),
    ("place", "an", "order"),
    ("complete", "the", "purchase"),
    ("complete", "the", "checkout"),
    ("approve", "the", "spend"),
    ("approve", "the", "purchase"),
    ("approve", "this", "purchase"),
    ("use", "my", "card"),
    ("use", "my", "credit", "card"),
    ("use", "my", "debit", "card"),
    ("click", "pay"),
)
# Leads whose object is often not money; they also need a money cue.
SPEND_MONEY_CUED_LEADS = (("send",), ("transfer",))
_MONEY_CUES = (
    "money",
    "my card",
    "my credit card",
    "my wallet",
)
_CURRENCY_WORDS = frozenset(
    {"bucks", "cents", "dollar", "dollars", "eur", "euro", "euros", "gbp", "jpy", "krw", "pound", "pounds", "usd", "won", "yen"}
)
# A reconcile question is the gate's own scenario only when the agent paid.
SPEND_RECONCILE_LEADS = (
    ("did", "the", "payment", "go", "through"),
    ("did", "the", "charge", "go", "through"),
)
_AGENT_PAID_CUES = (
    "you clicked pay",
    "you paid",
    "you bought",
    "you ordered",
    "you booked",
    "you charged",
)
# Naming the gate as a command is asking for it; its artifact's name is not
# ("what is a pre-spend card" asks a question).
SPEND_GATE_NAME_LEADS = (("use", "spend", "gate"), ("run", "spend", "gate"))
# "pay" used as an idiom, not a request: "please pay attention to the spend cap".
_PAY_IDIOMS = (("attention",), ("close", "attention"), ("a", "visit"))

# What may follow a gate phrase inside the request clause.
_ALLOWED_PREPOSITIONS = frozenset({"at", "for", "from", "in", "on", "to"})
_ALLOWED_ADVERBS = (("please",), ("now",), ("right", "away"))
_OWN_MONEY_AFTER_WITH = (
    ("my", "card"),
    ("my", "saved", "card"),
    ("my", "credit", "card"),
    ("my", "debit", "card"),
    ("my", "wallet"),
    ("my", "account"),
)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def _spoken(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _without_filler(tokens: list[str]) -> list[str]:
    rest = list(tokens)
    stripped = True
    while stripped and rest:
        stripped = False
        if rest[0] in _FILLER_PUNCTUATION:
            rest = rest[1:]
            stripped = True
            continue
        for filler in SPEND_LEAD_FILLER:
            if tuple(rest[: len(filler)]) == filler:
                rest = rest[len(filler):]
                stripped = True
                break
    return rest


def _clause(tokens: list[str]) -> list[str]:
    clause: list[str] = []
    for index, token in enumerate(tokens):
        if token in _CLAUSE_BOUNDARIES:
            break
        clause.append(token)
    return clause


def _starts_with(tokens: list[str], words: tuple[str, ...]) -> bool:
    return tuple(tokens[: len(words)]) == words


def _is_amount(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if tokens[0][0] in _CURRENCY_SYMBOLS:
        return True
    return tokens[0][0].isdigit() and len(tokens) > 1 and tokens[1] in _CURRENCY_WORDS


def _names_money(text: str, tokens: list[str]) -> bool:
    if any(_spoken(text, cue) for cue in _MONEY_CUES):
        return True
    return any(_is_amount(tokens[index:]) for index in range(len(tokens)))


def _spend_lead(tokens: list[str], folded: str) -> bool:
    for lead in (*SPEND_LEADS, *SPEND_MONEY_CUED_LEADS):
        if _starts_with(tokens, lead):
            if lead == ("pay",) and any(_starts_with(tokens[1:], idiom) for idiom in _PAY_IDIOMS):
                return False
            if lead in SPEND_MONEY_CUED_LEADS:
                return _names_money(folded, tokens)
            return True
    if any(_starts_with(tokens, lead) for lead in SPEND_RECONCILE_LEADS):
        return any(_spoken(folded, cue) for cue in _AGENT_PAID_CUES)
    return any(_starts_with(tokens, lead) for lead in SPEND_GATE_NAME_LEADS)


def _continuation_allowed(rest: list[str]) -> bool:
    # Anything else after a phrase means it is not the request: "or" ("pay
    # with my card or paypal") makes a comparison, and a verb or a noun makes
    # the phrase a subject or a label.
    if not rest:
        return True
    if rest[0] in _CLAUSE_BOUNDARIES or rest[0] in _ALLOWED_PREPOSITIONS or _is_amount(rest):
        return True
    if rest[0] == "with":
        return _is_amount(rest[1:]) or any(_starts_with(rest[1:], own) for own in _OWN_MONEY_AFTER_WITH)
    return any(_starts_with(rest, adverb) for adverb in _ALLOWED_ADVERBS)


def _phrase_ends(clause: list[str], phrase: tuple[str, ...]) -> list[int]:
    return [
        index + len(phrase)
        for index in range(len(clause) - len(phrase) + 1)
        if tuple(clause[index : index + len(phrase)]) == phrase
    ]


def _has_request_marker(prefix: list[str], clause: list[str], spans: list[tuple[int, int]]) -> bool:
    if any(marker == tuple(prefix[index : index + len(marker)]) for marker in SPEND_REQUEST_MARKERS for index in range(len(prefix))):
        return True
    if "please" in clause:
        return True
    if any(_is_amount(clause[index:]) for index in range(len(clause))):
        return True
    # "for me" counts only outside a matched phrase: "buy it for me" says it as
    # part of the phrase, "buy the cheapest one for me with my card" asks.
    return any(
        clause[index : index + 2] == ["for", "me"] and not any(start <= index < end for start, end in spans)
        for index in range(len(clause) - 1)
    )


def _request(text: str, phrases: Iterable[str]) -> tuple[str, list[str]]:
    folded = text.casefold().strip()
    every = _tokens(folded)
    tokens = _without_filler(every)
    prefix = every[: len(every) - len(tokens)]
    clause = _clause(tokens)
    if not _spend_lead(tokens, folded):
        return "", []
    # Phrases are found in the clause; what follows each is read past the
    # clause's end, so "or" right after a phrase is still seen.
    found = [
        (phrase, end - len(words), end)
        for phrase in phrases
        for words in (tuple(_tokens(phrase)),)
        for end in _phrase_ends(clause, words)
    ]
    if not found or not all(_continuation_allowed(tokens[end:]) for _phrase, _start, end in found):
        return "", []
    if not _has_request_marker(prefix, clause, [(start, end) for _phrase, start, end in found]):
        return "", []
    return " ".join(clause), list(dict.fromkeys(phrase for phrase, _start, _end in found))


def spend_request_clause(text: str, phrases: Iterable[str]) -> str:
    """The request clause when it asks the agent to spend the person's money through one of `phrases`, else ""."""
    return _request(text, phrases)[0]


def spend_request_phrases(text: str, phrases: Iterable[str]) -> list[str]:
    """Which of `phrases` the admitted request clause says, as whole words; [] when it is not admitted."""
    return _request(text, phrases)[1]
