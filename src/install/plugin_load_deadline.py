"""What Hermes' per-plugin load deadline left on disk for one plugin.

Hermes times the load of every plugin -- importing it, then calling its
``register()`` -- against ``plugins.load_timeout_seconds``: 10 seconds by
default, ``0`` for no limit, at most 600. Past the limit Hermes drops that
plugin and carries on with the others. The mechanism is
``run_with_load_deadline`` in Hermes' ``hermes_cli/plugins_loader.py`` (first
released in Hermes 0.21.5); what follows was measured against that code in an
isolated ``HERMES_HOME`` rather than read off it:

- The skipped plugin is recorded in the running process only -- ``/plugins``
  and ``list_plugins()`` carry ``load timed out after <N>s (import +
  register() never returned)``. Nothing outside the process can read that.
- The one trace that outlives the process is a log line. Hermes logs
  ``Failed to load plugin '<name>': <that error>`` at WARNING, and its
  ``logs/errors.log`` keeps WARNING and above for the home that was loading.
- The load is not cancelled. It runs on a worker thread Hermes abandons, so
  the plugin's modules stay in ``sys.modules`` (some half-initialized until
  the worker finishes) and every ``register_*`` call the worker still makes
  is refused with its own WARNING. Those later lines are a consequence of the
  skip, not a second record of it, and are not counted here.

So this reads that one log for that one line, and answers three ways. A record
is a past load: it says nothing about the load a running Hermes did last. No
record in a log that was read says only what the retained log holds -- the file
rotates, and a process that never set up file logging writes none -- so it is
not evidence that the plugin loaded. No log is ``observed: False``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

RECORD_SCHEMA_VERSION = "plugin_load_timeout_record/v1"

LOAD_TIMEOUT_RECORDED = "load_timeout_recorded"
NO_LOAD_TIMEOUT_RECORDED = "no_load_timeout_recorded"
ERROR_LOG_ABSENT = "hermes_error_log_absent"
ERROR_LOG_UNREADABLE = "hermes_error_log_unreadable"

# Hermes rotates errors.log at 2 MiB. The bound is for a file that grew some
# other way: the newest lines are the ones a reader of this check wants.
_READ_LIMIT_BYTES = 4 * 1024 * 1024
_FIELD_LIMIT = 40


def load_timeout_log_marker(plugin_name: str) -> str:
    """The text Hermes writes when `plugin_name` overran the load deadline.

    Quoted name and reason together: another plugin's timeout, this plugin's
    other load failures, and the refused late registrations all lack it.
    """
    return f"Failed to load plugin '{plugin_name}': load timed out after "


def read_recorded_load_timeouts(hermes_home: Path, plugin_name: str) -> dict[str, Any]:
    """Whether `hermes_home`'s retained error log records a load timeout for `plugin_name`."""
    log_path = hermes_home / "logs" / "errors.log"
    try:
        with log_path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - _READ_LIMIT_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return _record(log_path, observed=False, reason=ERROR_LOG_ABSENT)
    except OSError as exc:
        return _record(log_path, observed=False, reason=ERROR_LOG_UNREADABLE, detail=type(exc).__name__)
    marker = load_timeout_log_marker(plugin_name)
    matches = [line for line in text.splitlines() if marker in line]
    if not matches:
        return _record(log_path, observed=True, reason=NO_LOAD_TIMEOUT_RECORDED)
    latest = matches[-1]
    return _record(
        log_path,
        observed=True,
        reason=LOAD_TIMEOUT_RECORDED,
        count=len(matches),
        # Hermes' line opens with its own local-time stamp; it is carried as
        # written, never converted, because the log does not name a zone.
        latest_at=latest.partition(" WARNING")[0].strip()[:_FIELD_LIMIT],
        latest_deadline=latest.partition(marker)[2].split(" ", 1)[0][:_FIELD_LIMIT],
    )


def _record(
    log_path: Path,
    *,
    observed: bool,
    reason: str,
    detail: str = "",
    count: int = 0,
    latest_at: str = "",
    latest_deadline: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "observed": observed,
        "reason": reason,
        "detail": detail,
        "log_path": str(log_path),
        "count": count,
        "latest_at": latest_at,
        "latest_deadline": latest_deadline,
    }


__all__ = [
    "ERROR_LOG_ABSENT",
    "ERROR_LOG_UNREADABLE",
    "LOAD_TIMEOUT_RECORDED",
    "NO_LOAD_TIMEOUT_RECORDED",
    "load_timeout_log_marker",
    "read_recorded_load_timeouts",
]
