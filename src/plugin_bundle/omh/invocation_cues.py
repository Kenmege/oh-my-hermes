"""What the words after an explicit skill invocation ask for, when they ask for anything but the skill's work.

The person chose the skill ("/ulw-work …", "$code-review …", and every slash skill Hermes sends), so the default is
to run it. It is answered instead only when the instruction is about the skill or its run, never a task:

- a stop with no task object (`dag_ulw_cues.stops_the_run`: "stop", "멈춰", "이거 취소", "停止", "止めて", "取消"). A
  stop word with a task object is work: "kill the zombie processes", "stop the modal from flickering", "로그 폭주
  멈춰줘", "abort the rebase";
- a request to see how the run stands (`dag_ulw_cues.asks_how_the_run_stands`: "status", "show me the run", "상태
  보여줘", "끝났어?", "is it running?", "状態"), except after a skill whose own work is that status
  (`STATUS_IS_THE_WORK`: "/omh-running-work-board status", "/omh-verification-gate is it done?");
- a question about what the skill is (`asks_after_the_name` without its run-state words: "what is it?", "what does
  this do?", "이게 뭐야?", "어떻게 써?", "何これ", "这是什么"), or about dag-ulw ("/ulw-loop dag-ulw가 뭐야?"; a report
  on it, "/ulw-work dag-ulw is broken", is work).

Every other instruction runs the skill, a question with a task object included: "/omh-code-review is it safe to
merge?", "/ulw-plan how should we migrate billing?", "/research is rust faster than go?", "/omh-app-debugging why did
the run stop?". dag-ulw reads its own sigil (`dag_ulw_cues`). The router (`policy.bare_name_reports_or_asks`) and the
route hint (`awareness._invoked_name_stops_or_asks`) both read this one function. Stdlib only: the plugin bundle loads
this file with Hermes' interpreter (#1623).
"""

from __future__ import annotations

import unicodedata
from typing import Final

from .dag_ulw_cues import (
    asks_about_dag_ulw,
    asks_how_the_run_stands,
    ja_zh_asks_what_it_is,
    reports_on_dag_ulw,
    stops_the_run,
)
from .name_report_cues import QUESTION_IS_THE_WORK, asks_after_the_name

# The skills whose own work is answering how a run stands, so a status request after their sigil runs them:
# `QUESTION_IS_THE_WORK` but app-debugging, whose work is diagnosing a failure and whose status questions are answered
# (lead's ruling on #2221), and the ops command board, whose work is the operations status.
STATUS_IS_THE_WORK: Final = (QUESTION_IS_THE_WORK - {"app-debugging"}) | {"ops-observability-card"}

STOP: Final = "stop"
STATUS: Final = "status"
META: Final = "meta"


def invoked_instruction(name: str, rest: str) -> str:
    """`STOP`, `STATUS` or `META` when ``rest``, the words after an explicitly invoked skill (``name``, its canonical
    name), are about the skill or its run, else "": run the skill."""
    if stops_the_run(rest):
        return STOP
    if asks_how_the_run_stands(rest):
        return "" if name in STATUS_IS_THE_WORK else STATUS
    folded = unicodedata.normalize("NFKC", rest).casefold().strip(" ,:")
    if asks_after_the_name(folded, run_state=False) or ja_zh_asks_what_it_is(folded):
        return META
    if asks_about_dag_ulw(folded) and not reports_on_dag_ulw(folded):
        return META  # "/ulw-loop dag-ulw가 뭐야?"; "/ulw-work dag-ulw is broken" is work for ulw-work
    return ""


__all__ = ["META", "STATUS", "STATUS_IS_THE_WORK", "STOP", "invoked_instruction"]
