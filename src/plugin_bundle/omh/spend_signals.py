"""Whether a message asks the agent to spend the person's own money: one rule for the router and the route hint.

`spend-gate` phrases also name the TOPIC of a sentence that asks for something
else: payment software being built ("the place the order button doesn't respond
on safari", "complete the checkout flow in the react native app"), a question
("how does a wallet transfer work"), a document about paying ("write a blog
post on how to pay with my saved card"), a reminder ("schedule a reminder to
pay my rent"), someone else's payment ("my mom asked did the payment go
through") and the person's own story ("I paid for lunch with my card"). Each
of those dispatched the skill, or produced its route hint, while a phrase
anywhere in the sentence counted.

So the gate reads the REQUEST, not the topic. `spend_request_admitted` reads
only the sentence's first words, once leading filler and courtesy are
stripped ("hey", "please", "go ahead and", "can you", "i need you to", ...).
A later clause never counts: "transcribe this call, then pay the vendor"
belongs to whoever owns transcription. Words are read Unicode-aware, so a
sentence that opens in Hangul or another script has no English lead at all.

The sentence is admitted when its first words are:
- a spend lead addressed to the agent ("buy", "pay", "order", "book", "top
  up", "renew", "place the order", "complete the checkout", "use my card",
  ...), whose object -- the words up to the next "for", "with", "to", comma
  and the like -- does not end in a thing being built or filed ("complete the
  checkout flow", "pay with my saved card toggle", "order the slides");
- "send" or "transfer" with the same object rule and a money cue in the
  sentence: money, an amount with its currency, or the person's own card,
  wallet or account ("transfer the repo to the new org" moves no money);
- a reconcile question ("did the payment go through") in a sentence that says
  the agent paid ("after you clicked pay");
- or the gate's own name as a command ("spend-gate: ...", "use spend gate").

Everything else returns to the router's base behaviour: the router withdraws
the skill (so no phrase, boost or description word of it counts) and the route
hint stays silent. Nothing here lets the gate take a request another skill's
guard holds; an admitted sentence still competes in ordinary scoring.

`spend_frame_withdraws` keeps the few frames an admitted sentence can still
carry: an app or component being built ("in the react native app"),
procurement ("approve this purchase request in coupa"), a comparison ("pay
per request pricing versus a flat plan"), a quoted phrase, and "the order of"
things.

The router (`omh.routing.recommend`) and the awareness route hint
(`awareness.py`) both read this module, so the two surfaces cannot disagree.
Stdlib only.
"""

from __future__ import annotations

import re

_SYMBOL_AMOUNT = re.compile(r"[$€£¥₩]\s?\d")
# Unicode-aware words, plus the punctuation that ends an object.
_TOKEN = re.compile(r"\w+(?:'\w+)?|[,;:.!?]")
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
# (lead words, how many of them are the verb): the object starts after the verb.
SPEND_LEADS = (
    (("buy",), 1),
    (("pay",), 1),
    (("purchase",), 1),
    (("order",), 1),
    (("book",), 1),
    (("top", "up"), 2),
    (("renew",), 1),
    (("subscribe",), 1),
    (("tip",), 1),
    (("donate",), 1),
    (("check", "out"), 2),
    (("checkout",), 1),
    (("place", "the", "order"), 1),
    (("place", "an", "order"), 1),
    (("complete", "the", "purchase"), 1),
    (("complete", "the", "checkout"), 1),
    (("approve", "the", "spend"), 1),
    (("approve", "the", "purchase"), 1),
    (("approve", "this", "purchase"), 1),
    (("use", "my", "card"), 1),
    (("use", "my", "credit", "card"), 1),
    (("use", "my", "debit", "card"), 1),
    (("click", "pay"), 2),
)
# Leads whose object is often not money; they also need a money cue.
SPEND_MONEY_CUED_LEADS = ((("send",), 1), (("transfer",), 1))
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
SPEND_GATE_NAME_LEADS = (("spend", "gate"), ("use", "spend", "gate"), ("run", "spend", "gate"))

# Where a spend verb's object ends.
_OBJECT_BOUNDARIES = frozenset(
    {
        ",", ";", ":", ".", "!", "?",
        "after", "and", "are", "as", "at", "before", "but", "by", "does", "doesn't", "for", "from", "if", "in",
        "into", "is", "isn't", "keeps", "on", "once", "so", "then", "to", "until", "under", "using", "via", "was",
        "were", "when", "with",
    }
)
_OBJECT_PREPOSITIONS = frozenset({"for", "from", "on", "to", "with"})
# An object ending in one of these is a thing being built or filed, not a purchase.
SPEND_NON_SPEND_OBJECT_NOUNS = frozenset(
    {
        "button", "buttons", "component", "components", "docs", "endpoint", "endpoints", "flag", "flags", "flow",
        "flows", "form", "forms", "function", "functions", "issue", "issues", "label", "labels", "page", "pages",
        "policies", "policy", "pr", "prs", "screen", "screens", "slides", "spec", "specs", "stories", "story",
        "string", "strings", "template", "templates", "test", "tests", "toggle", "workflow", "workflows",
    }
)

# Frames an admitted sentence can still carry; each withdraws the skill.
SPEND_FRAME_TOKENS = frozenset(
    {
        "android",
        "coupa",
        "figma",
        "flutter",
        "ios",
        "mockup",
        "phrase",
        "procurement",
        "react",
        "redesign",
        "requisition",
        "versus",
        "vs",
        "wireframe",
    }
)
SPEND_FRAME_PHRASES = (
    "our api",
    "my own api",
    "our app",
    "our store",
    "our checkout",
    "purchase order",
    "purchase request",
    "purchase requests",
    "the order of",
    "which is better",
    "which one is better",
)


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


def _object_words(rest: list[str]) -> list[str]:
    words: list[str] = []
    for token in rest:
        if token in _OBJECT_BOUNDARIES:
            break
        words.append(token)
    return words


def _object_is_a_spend(after_verb: list[str]) -> bool:
    words = _object_words(after_verb)
    if not words and after_verb and after_verb[0] in _OBJECT_PREPOSITIONS:
        # "pay with my saved card toggle", "pay for the booking": the object follows the preposition.
        words = _object_words(after_verb[1:])
    if not words:
        return True
    if words[-2:] == ["pull", "request"]:
        return False
    return words[-1] not in SPEND_NON_SPEND_OBJECT_NOUNS


def _names_money(text: str) -> bool:
    if _SYMBOL_AMOUNT.search(text) or any(_spoken(text, cue) for cue in _MONEY_CUES):
        return True
    words = _WORD.findall(text)
    return bool(_CURRENCY_WORDS.intersection(words)) and any(word[0].isdigit() for word in words)


def _leads_with(tokens: list[str], lead: tuple[str, ...]) -> bool:
    return tuple(tokens[: len(lead)]) == lead


def spend_request_admitted(text: str) -> bool:
    """The sentence's first words, after filler, ask the agent to spend the person's money."""
    folded = text.casefold()
    tokens = _without_filler(_TOKEN.findall(folded))
    if any(_leads_with(tokens, lead) for lead in SPEND_GATE_NAME_LEADS):
        return True
    for lead, verb_words in SPEND_LEADS:
        if _leads_with(tokens, lead):
            return _object_is_a_spend(tokens[verb_words:])
    for lead, verb_words in SPEND_MONEY_CUED_LEADS:
        if _leads_with(tokens, lead):
            return _names_money(folded) and _object_is_a_spend(tokens[verb_words:])
    if any(_leads_with(tokens, lead) for lead in SPEND_RECONCILE_LEADS):
        return any(_spoken(folded, cue) for cue in _AGENT_PAID_CUES)
    return False


def spend_frame_withdraws(text: str) -> bool:
    """An admitted sentence that is still about building, procurement, a comparison, or an ordering."""
    folded = text.casefold()
    if SPEND_FRAME_TOKENS.intersection(_WORD.findall(folded)):
        return True
    return any(_spoken(folded, phrase) for phrase in SPEND_FRAME_PHRASES)
