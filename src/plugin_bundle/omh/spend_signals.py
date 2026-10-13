"""Whether a message asks the agent to spend the person's own money: one rule for the router and the route hint.

`spend-gate` phrases also name the TOPIC of a sentence that asks for something
else: payment software ("pay with my saved card returns a 500 error",
"checkout with my card breaks when the cart has 0 items"), a question ("pay
with my card or paypal, which is safer?"), an article or a book ("check out
this article on why you should never pay with my card abroad"), a ticket
("order 5512: did the payment go through?"), a reminder, someone else's
payment, and the person's own story. Each of those dispatched the skill, or
produced its route hint, while a phrase anywhere in the sentence counted.

So the gate is deliberately conservative: it reads the REQUEST, and only the
part of the sentence that is the request. `spend_lead_clause` returns that part
-- the sentence's first words after filler and courtesy ("hey", "please", "go
ahead and", "can you", ...), up to the first comma, colon, question mark,
"and", "or", "if", "when", "which", "about", "on why" and the like -- when it
is a spend addressed to the agent, and "" otherwise. A later clause never
counts ("transcribe this call, then pay the vendor" belongs to whoever owns
transcription), and the gate's phrases count only inside the returned clause.
Words are read Unicode-aware with hyphens kept, so a sentence that opens in
Hangul has no English lead and "pay-per-request" or "buy-side" is not "pay"
or "buy".

The clause is a spend when it opens with:
- a spend lead ("buy", "pay", "order", "book", "top up", "renew", "place the
  order", "complete the checkout", "use my card", ...) whose object -- the
  words up to the next "for", "with", "to" and the like -- does not end in a
  thing being built or filed ("complete the checkout flow", "pay again
  button", "order the slides");
- "send" or "transfer" with the same object rule and a money cue in the
  sentence: money, an amount with its currency, or the person's own card,
  wallet or account;
- a reconcile question ("did the payment go through") in a sentence that says
  the agent paid ("after you clicked pay");
- or the gate's own name as a command ("spend-gate: ...", "use spend gate").

Noun, subject and question shapes are not requests and return "": a sentence
ending in "?"; a one-word lead followed by "of", a colon, or a number that is
not an amount ("order of events", "order 5512:"); "check out this / that /
the new ..."; "pay attention", "pay close attention", "pay off", "pay stubs";
and a clause in which a finite verb follows the lead ("pay with my saved
card returns a 500 error", "buy it for me is the copy marketing wants") --
that is a statement about a feature, not a command.

`spend_frame_withdraws` keeps the few frames an admitted clause can still
carry: an app or component being built, procurement, a comparison, a quoted
phrase, and "the order of" things.

The router (`omh.routing.recommend`) and the awareness route hint and markers
(`awareness.py`) all read this module, so the surfaces cannot disagree.
Nothing here lets the gate take a request another skill's guard holds: an
admitted clause only lets the skill's own phrases score as usual. Stdlib only.
"""

from __future__ import annotations

import re

_SYMBOL_AMOUNT = re.compile(r"[$€£¥₩]\s?\d")
# Unicode-aware words with hyphens and apostrophes kept, plus clause punctuation.
_TOKEN = re.compile(r"\w+(?:[-']\w+)*|[,;:.!?]")
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
# A finite verb after the lead makes the clause a statement about a feature.
SPEND_STATEMENT_VERBS = frozenset(
    {
        "are", "breaks", "can", "crashes", "does", "doesn't", "failed", "fails", "hangs", "has", "is", "isn't",
        "keeps", "loads", "need", "needs", "returns", "seems", "should", "shows", "stops", "throws", "times", "was",
        "were", "will", "works",
    }
)
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
SPEND_GATE_NAME_LEADS = (
    ("spend-gate",),
    ("spend", "gate"),
    ("use", "spend-gate"),
    ("use", "spend", "gate"),
    ("run", "spend-gate"),
    ("run", "spend", "gate"),
)
# A lead used as a noun or an idiom, not a request.
_NOT_A_REQUEST_AFTER_LEAD = {
    ("check", "out"): (("this",), ("that",), ("the", "new")),
    ("pay",): (("attention",), ("close", "attention"), ("off",), ("stubs",), ("stub",)),
}

# Where a spend verb's object ends.
_OBJECT_BOUNDARIES = frozenset(
    {
        ",", ";", ":", ".", "!", "?",
        "after", "and", "as", "at", "before", "but", "by", "for", "from", "if", "in", "into", "on", "once", "or",
        "so", "then", "to", "until", "under", "using", "via", "when", "with",
    }
    | SPEND_STATEMENT_VERBS
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

# A label word right after one of these is the name of a UI element ("buy it for
# me buttons", "my card toggle", "pay again button", "the checkout flow"), not
# the thing bought. "page" is left out: "the checkout page" is where a real
# purchase is completed.
_LABEL_HEADS = frozenset({"account", "again", "behalf", "card", "cards", "checkout", "me", "order", "purchase", "wallet"})

# Frames an admitted clause can still carry; each withdraws the skill.
SPEND_FRAME_TOKENS = frozenset(
    {
        "android",
        "coupa",
        "customer",
        "customers",
        "figma",
        "fixture",
        "fixtures",
        "flutter",
        "ios",
        "mockup",
        "phrase",
        "procurement",
        "react",
        "redesign",
        "requisition",
        "sandbox",
        "seed",
        "staging",
        "user",
        "users",
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
    "as the default",
    "our rule",
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


def _clause(tokens: list[str]) -> list[str]:
    clause: list[str] = []
    for index, token in enumerate(tokens):
        if token in _CLAUSE_BOUNDARIES or (token == "on" and tokens[index + 1 : index + 2] == ["why"]):
            break
        clause.append(token)
    return clause


def _object_words(rest: list[str]) -> list[str]:
    words: list[str] = []
    for token in rest:
        if token in _OBJECT_BOUNDARIES:
            break
        words.append(token)
    return words


def _names_a_label(clause: list[str]) -> bool:
    return any(
        token in SPEND_NON_SPEND_OBJECT_NOUNS and token not in {"page", "pages"} and clause[index - 1] in _LABEL_HEADS
        for index, token in enumerate(clause)
        if index
    )


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


def _lead_used_as_a_noun(tokens: list[str], lead: tuple[str, ...]) -> bool:
    after = tokens[len(lead):]
    for idiom in _NOT_A_REQUEST_AFTER_LEAD.get(lead, ()):
        if tuple(after[: len(idiom)]) == idiom:
            return True
    if len(lead) != 1 or not after:
        return False
    if after[0] in {"of", ":"}:
        return True
    # "order 5512:" names an order; "transfer 40 dollars" names an amount.
    return after[0][0].isdigit() and not (len(after) > 1 and after[1] in _CURRENCY_WORDS)


def _spend_clause(tokens: list[str], clause: list[str], folded: str) -> bool:
    for lead, verb_words in (*SPEND_LEADS, *SPEND_MONEY_CUED_LEADS):
        if _leads_with(tokens, lead):
            if (
                _lead_used_as_a_noun(tokens, lead)
                or not _object_is_a_spend(tokens[verb_words:])
                or _names_a_label(clause)
            ):
                return False
            if (lead, verb_words) in SPEND_MONEY_CUED_LEADS:
                return _names_money(folded)
            return True
    if any(_leads_with(tokens, lead) for lead in SPEND_RECONCILE_LEADS):
        return any(_spoken(folded, cue) for cue in _AGENT_PAID_CUES)
    return any(_leads_with(tokens, lead) for lead in SPEND_GATE_NAME_LEADS)


def spend_lead_clause(text: str) -> str:
    """The request clause when the sentence asks the agent to spend the person's money, else ""."""
    folded = text.casefold().strip()
    if folded.endswith("?"):
        return ""
    tokens = _without_filler(_TOKEN.findall(folded))
    clause = _clause(tokens)
    if SPEND_STATEMENT_VERBS.intersection(clause[1:]) or not _spend_clause(tokens, clause, folded):
        return ""
    return " ".join(clause)


def spend_request_admitted(text: str) -> bool:
    """The sentence's request clause asks the agent to spend the person's money."""
    return bool(spend_lead_clause(text))


def spend_frame_withdraws(text: str) -> bool:
    """An admitted sentence that is still about building, procurement, a comparison, or an ordering."""
    folded = text.casefold()
    if SPEND_FRAME_TOKENS.intersection(_WORD.findall(folded)):
        return True
    return any(_spoken(folded, phrase) for phrase in SPEND_FRAME_PHRASES)
