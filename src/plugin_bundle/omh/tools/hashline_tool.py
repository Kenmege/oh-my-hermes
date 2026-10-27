"""`hashline_read` / `hashline_edit`: opt-in hash-anchored line edit tools.

Registered under the dedicated toolset `omh-hashline` (not the shared `omh`
toolset) and hidden behind a `check_fn` that requires env `OMH_HASHLINE_TOOLS`
to be `1` or `true`, so normal sessions pay no schema cost. Handlers return
JSON strings and never raise to the host. All editing rules live in the pure
module `omh.plugin_bundle.omh.hashline`.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .. import hashline
from ..hashline import HashlineError, resolve_path

HASHLINE_TOOLSET = "omh-hashline"

HASHLINE_READ_SCHEMA = {
    "name": "hashline_read",
    "description": (
        "Read a text file with each line tagged as `n:hhh|text`, where hhh is a "
        "3-char content hash. Use these anchors with hashline_edit."
    ),
    "parameters": {
        "type": "object", "additionalProperties": False, "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "File path (relative to the terminal working directory)"},
            "offset": {"type": "integer", "description": "1-based first line to return", "default": 1, "minimum": 1},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 400, "minimum": 0},
        },
    },
}

HASHLINE_EDIT_SCHEMA = {
    "name": "hashline_edit",
    "description": (
        "Edit a file by `N:HHH` line-hash anchors from hashline_read. All anchors are "
        "validated before anything is written; a stale anchor writes nothing. Ops: replace, "
        "insert_after (anchor `0:000` = beginning of file), delete."
    ),
    "parameters": {
        "type": "object", "additionalProperties": False, "required": ["path", "edits"],
        "properties": {
            "path": {"type": "string", "description": "File path (relative to the terminal working directory)"},
            "edits": {
                "type": "array", "minItems": 1,
                "description": (
                    "Edits applied atomically; must not overlap. "
                    "replace: {op, start: 'N:hhh', end: 'N:hhh' (optional, default = start), text} "
                    "(empty text deletes). insert_after: {op, anchor: 'N:hhh' or '0:000', text}. "
                    "delete: {op, start, end}. text may be multi-line."
                ),
                "items": {"type": "object"},
            },
        },
    },
}


_ON = ("1", "true", "on", "yes")


def _hashline_tools_enabled() -> bool:
    """`check_fn`: True when env `OMH_HASHLINE_TOOLS` or the active profile's
    `plugins.entries.omh.settings.hashline_tools` setting is on.

    The env alone is not enough on a gateway-served session: the host gateway
    builds tool definitions in its own process, so a client's env never reaches
    this check. The profile setting is read from the profile's `config.yaml`
    text, the same block scan other OMH settings use; any read failure is off.
    """
    if os.environ.get("OMH_HASHLINE_TOOLS", "").strip().lower() in _ON:
        return True
    try:
        from .. import runtime_paths

        text = (runtime_paths.default_hermes_home() / "config.yaml").read_text(encoding="utf-8")
        value = runtime_paths.plugin_setting_text(text, "hashline_tools")
    except Exception:
        return False
    return value is not None and value.strip().lower() in _ON


def _workspace_root(task_id: object) -> str | None:
    """The session's workspace root as Hermes' own file tools resolve it.

    A gateway-served session runs in the gateway process, whose ``TERMINAL_CWD`` and cwd are
    the gateway's, not the session's ``--in`` directory; Hermes records that per task. Fall back
    to ``TERMINAL_CWD`` when the host helper is unavailable (tests, older hosts).
    """
    try:
        from tools.file_tools_paths import _resolve_base_dir

        return str(_resolve_base_dir(str(task_id or "default"), container_paths=False))
    except Exception:
        return os.environ.get("TERMINAL_CWD")


def hashline_read_handler(args: dict[str, Any], **kwargs: object) -> str:
    try:
        path = resolve_path(str(args.get("path", "")), _workspace_root(kwargs.get("task_id")))
        offset = args.get("offset", 1)
        limit = args.get("limit", 400)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 1:
            raise HashlineError("bounds:/offset — must be a positive integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise HashlineError("bounds:/limit — must be a non-negative integer")
        result = hashline.read_lines(path, offset=offset, limit=limit)
        return json.dumps({"ok": True, **result}, ensure_ascii=False)
    except HashlineError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - never raise to the host
        return json.dumps({"ok": False, "error": f"unexpected_error:{exc}"}, ensure_ascii=False)


def hashline_edit_handler(args: dict[str, Any], **kwargs: object) -> str:
    try:
        path = resolve_path(str(args.get("path", "")), _workspace_root(kwargs.get("task_id")))
        edits_raw = args.get("edits")
        if not isinstance(edits_raw, list):
            raise HashlineError("invalid_type:/edits — must be an array")
        result = hashline.edit_file(path, edits_raw)
        return json.dumps(result, ensure_ascii=False)
    except HashlineError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - never raise to the host
        return json.dumps({"ok": False, "error": f"unexpected_error:{exc}"}, ensure_ascii=False)


__all__ = [
    "HASHLINE_EDIT_SCHEMA",
    "HASHLINE_READ_SCHEMA",
    "HASHLINE_TOOLSET",
    "hashline_edit_handler",
    "hashline_read_handler",
    "_hashline_tools_enabled",
]
