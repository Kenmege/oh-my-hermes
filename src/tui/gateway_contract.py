"""Everything the OMH TUI assumes about Hermes' `tui_gateway` protocol, in one place.

The gateway's own contract (`tui_gateway/contracts/`, generated into an OpenRPC
file under `apps/`) is not packaged in a Hermes wheel, so it cannot be read at
runtime. What this module declares instead is the subset the TUI uses: the
methods it calls and the keys it sends, the events it renders, and how it
answers each server-to-client request. A drift in any of these surfaces at
runtime as a param rejection (4000) or an unknown method (-32601), which the
client treats as "this Hermes no longer speaks what the TUI was built for".

Declared against Hermes 6c61c3e27d (2026-10-07).
"""

from __future__ import annotations

from dataclasses import dataclass

DECLARED_AGAINST_HERMES = "6c61c3e27d"

# JSON-RPC error codes the gateway uses that change what the client does.
PARAM_REJECTED = 4000  # Pydantic `extra="forbid"`: a key this Hermes does not know
METHOD_NOT_FOUND = -32601  # also the client's own answer to a request it does not support
NOT_MINE = 4018  # `slash.exec` refusing a command that `command.dispatch` owns
LEASE_HELD_ELSEWHERE = 4090  # `prompt.submit` when another process owns the session

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
    "session.list": frozenset({"limit"}),
    "prompt.submit": frozenset({"session_id", "text", "queued"}),
    # Attachments: the gateway resolves and queues the file; the TUI sends a path, never bytes.
    "image.attach": frozenset({"session_id", "path"}),
    "image.detach": frozenset({"session_id", "path"}),
    "clipboard.paste": frozenset({"session_id"}),
    "input.detect_drop": frozenset({"session_id", "text"}),
    "slash.exec": frozenset({"command", "session_id"}),
    "command.dispatch": frozenset({"name", "arg", "session_id"}),
}

# Events the TUI renders. Anything else is ignored, never an error: the gateway
# adds event types more often than it changes the ones below.
RENDERED_EVENTS = frozenset(
    {
        "gateway.ready",
        "session.info",
        "session.title",
        "session.usage",
        "status.update",
        "message.start",
        "message.delta",
        "message.interim",
        "message.complete",
        "thinking.delta",
        "reasoning.delta",
        "reasoning.available",
        "tool.generating",
        "tool.start",
        "tool.complete",
        "request.cancel",
        "approval.cancelled",
        "error",
    }
)

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

    `prompt` is the kind of slot the TUI shows (`None` = it shows a notice and
    answers at once). `decline` is the frame it sends when the person declines,
    the request times out, or the TUI does not support it. An ignored request is
    never an option: over stdio the gateway waits out its full deadline.
    """

    method: str
    prompt: str | None
    decline: dict[str, object] | None
    masked: bool = False


_EMPTY_VALUE: dict[str, object] = {"result": {"value": ""}}
_UNSUPPORTED: dict[str, object] = {"error": {"code": METHOD_NOT_FOUND, "message": "not supported by the OMH TUI"}}

SERVER_REQUESTS: dict[str, ServerRequestTreatment] = {
    treatment.method: treatment
    for treatment in (
        # A result without `answers` is cancel-all.
        ServerRequestTreatment("clarify", prompt="clarify", decline={"result": {}}),
        # `-32601` on an approval WITHDRAWS it instead of denying it; the deny is explicit.
        ServerRequestTreatment("approval", prompt="approval", decline={"result": {"choice": "deny"}}),
        ServerRequestTreatment("sudo", prompt="value", decline=_EMPTY_VALUE, masked=True),
        ServerRequestTreatment("secret", prompt="value", decline=_EMPTY_VALUE, masked=True),
        ServerRequestTreatment("vault.code", prompt="value", decline=_EMPTY_VALUE),
        ServerRequestTreatment("vault.unlock_prompt", prompt=None, decline=_EMPTY_VALUE),
        ServerRequestTreatment("vault.save_login", prompt=None, decline=_EMPTY_VALUE),
        ServerRequestTreatment("terminal.read", prompt=None, decline=_UNSUPPORTED),
        ServerRequestTreatment("preview.read", prompt=None, decline=_UNSUPPORTED),
        ServerRequestTreatment("preview.act", prompt=None, decline=_UNSUPPORTED),
        ServerRequestTreatment("window.read", prompt=None, decline=_UNSUPPORTED),
        ServerRequestTreatment("tour", prompt=None, decline=_UNSUPPORTED),
        ServerRequestTreatment("display.install.sudo", prompt=None, decline=_UNSUPPORTED),
    )
}


def server_request_treatment(method: str) -> ServerRequestTreatment:
    """The treatment for `method`; a request the table does not know is unsupported."""
    return SERVER_REQUESTS.get(method, ServerRequestTreatment(method, prompt=None, decline=_UNSUPPORTED))


def is_drift_error(error: object) -> bool:
    """Whether a JSON-RPC error means this Hermes no longer speaks the declared contract."""
    return isinstance(error, dict) and error.get("code") in DRIFT_ERROR_CODES


def undeclared_params(method: str, params: dict[str, object]) -> list[str]:
    """Keys in `params` the contract does not declare for `method` (all of them for an unknown method)."""
    allowed = CLIENT_METHODS.get(method, frozenset())
    return sorted(key for key in params if key not in allowed)
