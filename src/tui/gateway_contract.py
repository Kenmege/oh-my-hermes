"""Everything the OMH TUI assumes about Hermes' `tui_gateway` protocol, in one place.

The gateway's own contract (`tui_gateway/contracts/`, generated into an OpenRPC
file under `apps/`) is not packaged in a Hermes wheel, so it cannot be read at
runtime. What this module declares instead is the subset the TUI uses: the
methods it calls and the keys it sends, and how it answers each
server-to-client request. A drift in either surfaces at runtime as a param
rejection (4000) or an unknown method (-32601), which the client treats as
"this Hermes no longer speaks what the TUI was built for". Events need no
declaration: the TUI ignores a type it does not handle, and the gateway adds
event types more often than it changes the ones the TUI reads.

Declared against Hermes 6c61c3e27d (2026-10-07).
"""

from __future__ import annotations

from dataclasses import dataclass

DECLARED_AGAINST_HERMES = "6c61c3e27d"

# JSON-RPC error codes the gateway uses that change what the client does.
PARAM_REJECTED = 4000  # Pydantic `extra="forbid"`: a key this Hermes does not know
METHOD_NOT_FOUND = -32601  # also the client's own answer to a request it does not support
NOT_MINE = 4018  # `slash.exec` refusing a command that `command.dispatch` owns

DRIFT_ERROR_CODES = frozenset({PARAM_REJECTED, METHOD_NOT_FOUND})

# Methods the TUI calls, with the only keys it ever sends. Sending a key outside
# this map is a bug: a Hermes that does not know it rejects the whole call.
CLIENT_METHODS: dict[str, frozenset[str]] = {
    "client.capabilities": frozenset({"server_requests"}),
    "config.get": frozenset({"key"}),
    "commands.catalog": frozenset(),
    "complete.path": frozenset({"word", "session_id"}),
    "session.create": frozenset({"cols", "cwd"}),
    "session.resume": frozenset({"session_id", "cols"}),
    "session.close": frozenset({"session_id"}),
    "session.interrupt": frozenset({"session_id"}),
    # Typed text into the running turn; `rejected` when the session is idle, and the text then goes as a prompt.
    "session.steer": frozenset({"session_id", "text"}),
    # See CONFIG_SET_VALUES: `scope` is deliberately not declared.
    "config.set": frozenset({"key", "value", "session_id"}),
    "session.list": frozenset({"limit"}),
    # Read-only: the last 16 KB of one child's live transcript, for the agent log view. See `call_problem`.
    "subagent.tail": frozenset({"session_id", "subagent_id"}),
    "prompt.submit": frozenset({"session_id", "text", "queued"}),
    # Attachments: the gateway resolves and queues the file; the TUI sends a path, never bytes.
    "image.attach": frozenset({"session_id", "path"}),
    "image.detach": frozenset({"session_id", "path"}),
    "clipboard.paste": frozenset({"session_id"}),
    "input.detect_drop": frozenset({"session_id", "text"}),
    "slash.exec": frozenset({"command", "session_id"}),
    "command.dispatch": frozenset({"name", "arg", "session_id"}),
}

# The reasoning efforts Hermes takes (`hermes_constants.VALID_REASONING_EFFORTS`), and `none` for thinking off.
REASONING_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

# The `config.set` keys the TUI sets, both for one session, with the only values it sends: `yolo`
# (approvals off) and `reasoning` (the effort). Hermes applies either one wider when it cannot tie
# the call to a session: with no `session_id`, yolo sets `HERMES_YOLO_MODE` in the gateway's process
# environment and reasoning rewrites config.yaml, and `scope: "global"` writes `approvals.mode` for
# good. A blank yolo value toggles whatever the state is, and `reasoning` with show, hide, on, off,
# full or clamp is a display setting Hermes writes to config.yaml. So every call carries the session
# id and one of these values, and `scope` is not a key the TUI can send at all.
CONFIG_SET_VALUES: dict[str, frozenset[str]] = {
    "yolo": frozenset({"on", "off"}),
    "reasoning": frozenset(REASONING_LEVELS),
}
CONFIG_SET_SESSION_SCOPE = "session"  # what a yolo answer's `scope` must say

# Events whose envelope `session_id` is not the live session are dropped, except
# these: they belong to the gateway, not to a session.
GATEWAY_SCOPED_EVENT_PREFIX = "gateway."

# Environment the TUI removes before spawning the gateway. The sidecar URL makes
# the gateway tee every event to a socket; the gateway URL is the launcher's
# attach hand-off, which the TUI refuses rather than honours.
STRIPPED_GATEWAY_ENV = ("HERMES_TUI_GATEWAY_URL", "HERMES_TUI_SIDECAR_URL")
ATTACH_ENV = "HERMES_TUI_GATEWAY_URL"


@dataclass(frozen=True)
class ServerRequestTreatment:
    """How the TUI answers one server-to-client request.

    `shown` says whether the TUI opens a slot for it (False: a notice, and the
    decline goes at once). `decline` is the frame it sends when the person
    declines or the TUI does not support the request. An ignored request is
    never an option: over stdio the gateway waits out its full deadline. When
    the gateway gives up first (its own timeout), it withdraws the request with
    `request.cancel` and nothing is sent.
    """

    method: str
    shown: bool
    decline: dict[str, object]
    masked: bool = False


_EMPTY_VALUE: dict[str, object] = {"result": {"value": ""}}
_UNSUPPORTED: dict[str, object] = {"error": {"code": METHOD_NOT_FOUND, "message": "not supported by the OMH TUI"}}

SERVER_REQUESTS: dict[str, ServerRequestTreatment] = {
    treatment.method: treatment
    for treatment in (
        # A result without `answers` is cancel-all.
        ServerRequestTreatment("clarify", shown=True, decline={"result": {}}),
        # `-32601` on an approval WITHDRAWS it instead of denying it; the deny is explicit.
        ServerRequestTreatment("approval", shown=True, decline={"result": {"choice": "deny"}}),
        ServerRequestTreatment("sudo", shown=True, decline=_EMPTY_VALUE, masked=True),
        ServerRequestTreatment("secret", shown=True, decline=_EMPTY_VALUE, masked=True),
        ServerRequestTreatment("vault.code", shown=True, decline=_EMPTY_VALUE),
        ServerRequestTreatment("vault.unlock_prompt", shown=False, decline=_EMPTY_VALUE),
        ServerRequestTreatment("vault.save_login", shown=False, decline=_EMPTY_VALUE),
        ServerRequestTreatment("terminal.read", shown=False, decline=_UNSUPPORTED),
        ServerRequestTreatment("preview.read", shown=False, decline=_UNSUPPORTED),
        ServerRequestTreatment("preview.act", shown=False, decline=_UNSUPPORTED),
        ServerRequestTreatment("window.read", shown=False, decline=_UNSUPPORTED),
        ServerRequestTreatment("tour", shown=False, decline=_UNSUPPORTED),
        ServerRequestTreatment("display.install.sudo", shown=False, decline=_UNSUPPORTED),
    )
}


def server_request_treatment(method: str) -> ServerRequestTreatment:
    """The treatment for `method`; a request the table does not know is unsupported."""
    return SERVER_REQUESTS.get(method, ServerRequestTreatment(method, shown=False, decline=_UNSUPPORTED))


def is_drift_error(error: object) -> bool:
    """Whether a JSON-RPC error means this Hermes no longer speaks the declared contract."""
    return isinstance(error, dict) and error.get("code") in DRIFT_ERROR_CODES


def undeclared_params(method: str, params: dict[str, object]) -> list[str]:
    """Keys in `params` the contract does not declare for `method` (all of them for an unknown method)."""
    allowed = CLIENT_METHODS.get(method, frozenset())
    return sorted(key for key in params if key not in allowed)


def call_problem(method: str, params: dict[str, object]) -> str:
    """Why the TUI must not send this call; "" when it may.

    An undeclared key; a `config.set` that Hermes would apply beyond one
    session (a key or a value outside `CONFIG_SET_VALUES`, or no session id);
    or a `subagent.tail` that does not name both a session and a subagent.
    Hermes answers a tail without a subagent id as a rejected parameter
    (4000), which the client reads as drift and ends the session over.
    """
    undeclared = undeclared_params(method, params)
    if undeclared:
        return f"{method} does not declare {', '.join(undeclared)} in the gateway contract"
    if method == "subagent.tail":
        missing = [key for key in ("session_id", "subagent_id") if not (isinstance(params.get(key), str) and params[key])]
        return f"subagent.tail needs {' and '.join(missing)}: Hermes rejects the call without" if missing else ""
    if method != "config.set":
        return ""
    key, value, session_id = params.get("key"), params.get("value"), params.get("session_id")
    allowed = CONFIG_SET_VALUES.get(key) if isinstance(key, str) else None
    if allowed is None:
        return f"config.set does not declare the key {key!r} in the gateway contract"
    if not isinstance(value, str) or value not in allowed:
        return f"config.set does not declare the value {value!r} for {key} in the gateway contract"
    if not isinstance(session_id, str) or not session_id:
        return f"config.set {key} needs a session id: without one Hermes writes beyond the session"
    return ""
