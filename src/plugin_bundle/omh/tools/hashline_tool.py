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


_REPEATS: dict[tuple[str, str, str], int] = {}
_REPEATS_CAP = 256
_REPEAT_AT = 3


class _PathMiss(Exception):
    """A missing or out-of-root path; answered with the recovery payload."""

    def __init__(self, error: str, relpath: str) -> None:
        super().__init__(error)
        self.error, self.relpath = error, relpath


def _resolve(requested: str, root: str) -> str:
    try:
        return resolve_path(requested, root)
    except HashlineError as exc:
        raise _PathMiss(str(exc), requested) from None


def _require_exists(path: str, root: str) -> None:
    if not os.path.exists(path):
        rel = os.path.relpath(path, root)
        raise _PathMiss(f"not_found:/path — {rel} does not exist", rel)


def _miss_payload(miss: _PathMiss, root: str, task_id: object, op: str) -> str:
    """Recovery payload; the 3rd and later identical misses in a task add ``repeated``. Never raises."""
    try:
        payload = hashline.recovery_payload(root, miss.relpath, miss.error)
    except Exception:  # noqa: BLE001 - never raise to the host
        payload = {"ok": False, "error": miss.error, "not_found": miss.error.startswith("not_found:"),
                   "similar_files": [], "hint": f"Paths are relative to the workspace root {root}."}
    key = (str(task_id or "default"), op, miss.relpath)
    count = _REPEATS.pop(key, 0) + 1
    while len(_REPEATS) >= _REPEATS_CAP:
        _REPEATS.pop(next(iter(_REPEATS)))
    _REPEATS[key] = count
    if count >= _REPEAT_AT:
        payload["repeated"] = count
        payload["hint"] = (
            f"Stop retrying this path. It failed {count} times; pick one of similar_files "
            "or list files with terminal. " + payload["hint"]
        )
    return json.dumps(payload, ensure_ascii=False)


def _root_for(kwargs: dict[str, object]) -> str:
    root = _workspace_root(kwargs.get("task_id"))
    return os.path.realpath(root if root else os.getcwd())


def hashline_read_handler(args: dict[str, Any], **kwargs: object) -> str:
    root = ""
    try:
        root = _root_for(kwargs)
        path = _resolve(str(args.get("path", "")), root)
        _require_exists(path, root)
        offset = args.get("offset", 1)
        limit = args.get("limit", 400)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 1:
            raise HashlineError("bounds:/offset — must be a positive integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise HashlineError("bounds:/limit — must be a non-negative integer")
        result = hashline.read_lines(path, offset=offset, limit=limit)
        return json.dumps({"ok": True, **result}, ensure_ascii=False)
    except _PathMiss as miss:
        return _miss_payload(miss, root, kwargs.get("task_id"), "read")
    except HashlineError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - never raise to the host
        return json.dumps({"ok": False, "error": f"unexpected_error:{exc}"}, ensure_ascii=False)


def hashline_edit_handler(args: dict[str, Any], **kwargs: object) -> str:
    root = ""
    try:
        root = _root_for(kwargs)
        path = _resolve(str(args.get("path", "")), root)
        edits_raw = args.get("edits")
        if not isinstance(edits_raw, list):
            raise HashlineError("invalid_type:/edits — must be an array")
        creates = len(edits_raw) == 1 and isinstance(edits_raw[0], dict) \
            and edits_raw[0].get("op") == "insert_after" and str(edits_raw[0].get("anchor", "")) == "0:000"
        if edits_raw and not creates:
            _require_exists(path, root)
        result = hashline.edit_file(path, edits_raw)
        return json.dumps(result, ensure_ascii=False)
    except _PathMiss as miss:
        return _miss_payload(miss, root, kwargs.get("task_id"), "edit")
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
