"""Whether a message asks the agent to spend the person's own money: one rule for the router and the route hint.

`spend-gate` phrases also name the TOPIC of a sentence that asks for something
else: payment software ("pay with my saved card stopped working on safari"),
a question or a comparison ("pay with my card or paypal, which is safer"), an
article ("check out this article on why you should never pay with my card
abroad"), a ticket ("order 5512: did the payment go through?"), a product idea
("pay with my card would be nice to have in v2"), an idiom ("pay a visit to
the bank", "buy time with my card issuer"), someone else's payment, and the
person's own story. Each of those dispatched the skill, or produced its route
hint, while a phrase anywhere in the sentence counted, and every deny list of
verbs or nouns written against them was outrun by the next tense or noun.

So the rule is closed-world and deliberately conservative.
`spend_request_clause(text, phrases)` returns the request clause -- the
sentence's first words after filler and courtesy ("hey", "please", "go ahead
and", "can you", ...), read Unicode-aware with hyphens kept, up to the first
comma, colon, question mark, "and", "or", "if", "when", "which", "about", "on
why" and the like -- only when all of this holds, and "" otherwise:
- the sentence does not end in "?";
- the clause opens with a spend addressed to the agent ("buy", "pay", "order",
  "book", "renew", "place the order", "complete the checkout", "use my card",
  ...), not with an idiom ("pay attention", "pay a visit", "pay tribute",
  "buy time"), a noun ("order 5512", "pay-per-request"), or "check out"
  followed by a determiner ("check out this article") unless "for me" or an
  amount follows; "send" and "transfer" also
  need money, an amount, or the person's own card, wallet or account, and a
  reconcile question ("did the payment go through") needs the agent to be the
  one who paid ("after you clicked pay"); naming the gate as a command
  ("spend-gate: ...", "use spend gate") counts;
- one of the given phrases occurs in the clause, and every occurrence is
  followed only by what may follow a request: the end of the clause (but not
  "or", which makes a comparison); "for",
  "on", "at", "from", "to" or "in" (the item, the payee, the place); "with"
  followed by the person's own card, wallet or account or by an amount; an
  amount; "please", "now", or "right away". Anything else right after the
  phrase -- a verb of any tense, a modal, "or", a noun that extends the
  phrase ("issuer", "option", "button", "flow") -- means the phrase is the
  subject or a label, not the request.

Everything else returns to the router's base behaviour: the router withdraws
the skill (so no phrase or word of it counts) and the route hint and the
awareness markers stay silent. Nothing here lets the gate take a request
another skill's guard holds; an admitted clause only lets the skill's own
phrases score as usual.

`spend_frame_withdraws` keeps the one frame an admitted clause can still
carry: someone else's payment (a user, a customer).

The router (`omh.routing.recommend`) passes the skill's triggers to
`spend_request_clause`; the awareness route hint (`awareness.py`) passes its
own phrase table and its markers to `spend_request_phrases`. Stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_CURRENCY_SYMBOLS = "$€£¥₩"
# Unicode-aware words with hyphens and apostrophes kept, amounts with their
# currency symbol, and clause punctuation.
_TOKEN = re.compile(rf"[{_CURRENCY_SYMBOLS}]\d[\d.,]*|\w+(?:[-']\w+)*|[,;:.!?]")
_WORD = re.compile(r"\w+(?:'\w+)?")

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
    ("we", "need", "to"),
    ("let's",),
    ("lets",),
    ("hey",),
    ("hi",),
    ("hermes",),
    ("ok",),
    ("okay",),
    ("so",),
    ("please",),
    ("pls",),
    ("kindly",),
    ("just",),
    ("now",),
    ("alright",),
)
_FILLER_PUNCTUATION = frozenset({",", ";", ":", ".", "!", "?"})
# Where the request clause ends; the gate's phrases count only before it.
_CLAUSE_BOUNDARIES = frozenset(
    {
        ",", ";", ":", ".", "!", "?",
        "about", "after", "and", "because", "before", "but", "if", "once", "or", "so", "then", "unless", "until",
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
    "my debit card",
    "my wallet",
    "my account",
)
_CURRENCY_WORDS = frozenset(
    {"bucks", "cents", "dollar", "dollars", "eur", "euro", "euros", "gbp", "jpy", "krw", "pound", "pounds", "usd", "won", "yen"}
)
# A reconcile question is the gate's own scenario only when the agent paid.
SPEND_RECONCILE_LEADS = (
    ("did", "the", "payment", "go", "through"),
    ("did", "the", "charge", "go", "through"),
    ("was", "i", "charged"),
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
SPEND_GATE_NAME_LEADS = (
    ("spend-gate",),
    ("spend", "gate"),
    ("use", "spend-gate"),
    ("use", "spend", "gate"),
    ("run", "spend-gate"),
    ("run", "spend", "gate"),
)
# A lead used as an idiom, not a request.
_IDIOMS_AFTER_LEAD = {
    ("pay",): (
        ("attention",),
        ("close", "attention"),
        ("a", "visit"),
        ("tribute",),
        ("heed",),
        ("respects",),
        ("my", "respects"),
        ("your", "respects"),
        ("homage",),
        ("off",),
        ("stubs",),
        ("stub",),
    ),
    ("buy",): (("time",),),
}
# "check out" followed by one of these is "look at", unless "for me" or an amount follows.
_DETERMINERS = frozenset({"a", "an", "that", "the", "these", "this", "those", "our", "my"})

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

# Someone else's payment can still sit in an admitted clause ("checkout times
# out for users who pick pay with my saved card"); it withdraws the skill.
SPEND_FRAME_TOKENS = frozenset({"customer", "customers", "user", "users"})


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
        if token in _CLAUSE_BOUNDARIES or (token == "on" and tokens[index + 1 : index + 2] == ["why"]):
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


def _lead_used_as_a_noun(tokens: list[str], clause: list[str], lead: tuple[str, ...]) -> bool:
    after = tokens[len(lead):]
    if any(_starts_with(after, idiom) for idiom in _IDIOMS_AFTER_LEAD.get(lead, ())):
        return True
    if lead == ("check", "out") and after and after[0] in _DETERMINERS:
        return not (_spoken(" ".join(clause), "for me") or _names_money("", clause))
    # "order 5512 paid with my card" names an order; "transfer 40 dollars"
    # names an amount.
    return len(lead) == 1 and bool(after) and after[0][0].isdigit() and not _is_amount(after)


def _spend_lead(tokens: list[str], clause: list[str], folded: str) -> bool:
    for lead in (*SPEND_LEADS, *SPEND_MONEY_CUED_LEADS):
        if _starts_with(tokens, lead):
            if _lead_used_as_a_noun(tokens, clause, lead):
                return False
            if lead in SPEND_MONEY_CUED_LEADS:
                return _names_money(folded, tokens)
            return True
    if any(_starts_with(tokens, lead) for lead in SPEND_RECONCILE_LEADS):
        return any(_spoken(folded, cue) for cue in _AGENT_PAID_CUES)
    return any(_starts_with(tokens, lead) for lead in SPEND_GATE_NAME_LEADS)


def _continuation_allowed(rest: list[str]) -> bool:
    if not rest:
        return True
    # "pay with my card or paypal": a comparison, not a request.
    if rest[0] == "or":
        return False
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


def _request(text: str, phrases: Iterable[str]) -> tuple[str, list[str]]:
    folded = text.casefold().strip()
    if folded.endswith("?"):
        return "", []
    tokens = _without_filler(_tokens(folded))
    clause = _clause(tokens)
    if not _spend_lead(tokens, clause, folded):
        return "", []
    # Phrases are found in the clause; what follows each is read past the
    # clause's end, so "or" right after a phrase is still seen.
    found = [(phrase, end) for phrase in phrases for end in _phrase_ends(clause, tuple(_tokens(phrase)))]
    if not found or not all(_continuation_allowed(tokens[end:]) for _phrase, end in found):
        return "", []
    return " ".join(clause), list(dict.fromkeys(phrase for phrase, _end in found))


def spend_request_clause(text: str, phrases: Iterable[str]) -> str:
    """The request clause when it asks the agent to spend the person's money through one of `phrases`, else ""."""
    return _request(text, phrases)[0]


def spend_request_phrases(text: str, phrases: Iterable[str]) -> list[str]:
    """Which of `phrases` the admitted request clause says, as whole words; [] when it is not admitted."""
    return _request(text, phrases)[1]


def spend_frame_withdraws(text: str) -> bool:
    """An admitted sentence about someone else's payment: a user or a customer."""
    return bool(SPEND_FRAME_TOKENS.intersection(_WORD.findall(text.casefold())))
