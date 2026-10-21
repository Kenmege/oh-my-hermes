"""Whether a message asks to run `dag-ulw` by name: one reading for the router and the in-Hermes route hint.

People type the name with a space, either way round ("dag ulw", "ulw dag"), and in Korean with the verb
glued to it ("dag ulw해줘"). A message invokes the skill when the name opens the request (after a sigil,
``use``, ``run`` or ``please``), when the request runs something with it ("refactor auth with dag ulw"),
or when a Korean run ending follows it ("결제 모듈 dag ulw로 고쳐줘"). A negated mention never does
("don't use dag ulw", "dag ulw 말고"). Callers pass the executable text, so a quoted, fenced or relayed
mention never reaches this reader.

Stdlib only: the plugin bundle loads this file with Hermes' interpreter (#1623).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

DAG_ULW: Final = "dag-ulw"

_NAME: Final = r"(?:dag[\s_-]*(?:ulw|ultrawork)|(?:ulw|ultrawork)[\s_-]*dag)(?![a-z0-9_-])"
_LEAD: Final = re.compile(rf"^(?:(?:please|pls)\s+)?(?:(?:use|run|start)\s+(?:omh\s+)?)?(?:\$|\./|/|@)?{_NAME}")
_WITH: Final = re.compile(rf"\b(?:with|via|using|through)\s+(?:the\s+)?{_NAME}")
# The name with a run ending right after it ("dag ulw해줘"), or the name as an instrument ("dag ulw로") and,
# within four words, a request ending ("dag ulw로 결제 모듈 고쳐줘"). "dag ulw 결과 알려줘" asks about a run.
_KOREAN_RUN: Final = re.compile(
    rf"{_NAME}\s*(?:해\s*줘|해\s*주세요|해\b|돌려|실행|진행|부탁)"
    rf"|{_NAME}\s*(?:으로|로)\s+(?:\S+\s+){{0,3}}\S*(?:줘|주세요)"
)
_NEGATED: Final = re.compile(
    rf"(?:\b(?:don'?t|do not|never|not|without|instead of|other than)\s+(?:use\s+|run\s+)?(?:the\s+)?{_NAME})"
    rf"|(?:{_NAME}\s*(?:은|는|을|를)?\s*(?:말고|쓰지|하지\s*마|대신|아니))"
)


def invokes_dag_ulw(text: str) -> bool:
    """Whether ``text`` (the executable part of a message) asks to run `dag-ulw`."""
    folded = unicodedata.normalize("NFKC", text).casefold().strip()
    if not folded or _NEGATED.search(folded):
        return False
    return bool(_LEAD.match(folded) or _WITH.search(folded) or _KOREAN_RUN.search(folded))


def declines_dag_ulw(text: str) -> bool:
    """Whether ``text`` names `dag-ulw` only to decline it ("don't use dag ulw", "dag ulw 말고")."""
    return bool(_NEGATED.search(unicodedata.normalize("NFKC", text).casefold()))
