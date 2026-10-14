"""Removing the Hermes status widget earlier versions installed.

OMH used to install `omh-status.mjs` into `$HERMES_HOME/tui-widgets/`, with a
manifest recording its digest, so bare `hermes` drew the OMH HUD. Bare `omh`
opens the OMH TUI now and nothing OMH ships draws inside Hermes' terminal, so
this module only takes the old file back: setup and update through the
display release, uninstall on its own. Ownership is the manifest's digest
against the file on disk, never the shipped bytes, so it still decides with
no widget source in the package.
"""

from __future__ import annotations

import json
from pathlib import Path

WIDGET_FILENAME = "omh-status.mjs"
MANIFEST_FILENAME = ".omh-status.manifest.json"


class TuiWidgetInstallError(RuntimeError):
    """The managed Hermes widget destination is unsafe."""


def _reject_symlink_path(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise TuiWidgetInstallError(f"refusing symlinked widget path: {current}")
        if current == current.parent:
            return
        current = current.parent


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _is_managed_widget(destination: Path, manifest: Path) -> bool:
    try:
        if manifest.is_symlink() or not manifest.is_file():
            return False
        record = json.loads(manifest.read_text(encoding="utf-8"))
        return (
            record.get("schema_version") == "omh_tui_widget_manifest/v1"
            and record.get("filename") == WIDGET_FILENAME
            and record.get("sha256") == _sha256(destination.read_bytes())
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def uninstall_tui_widget(hermes_home: Path, *, dry_run: bool = False) -> dict[str, str]:
    destination_dir = hermes_home / "tui-widgets"
    destination = destination_dir / WIDGET_FILENAME
    manifest = destination_dir / MANIFEST_FILENAME
    _reject_symlink_path(destination_dir)
    _reject_symlink_path(destination)
    _reject_symlink_path(manifest)
    if not destination.exists() and not manifest.exists():
        return {"status": "absent", "path": str(destination)}
    if not _is_managed_widget(destination, manifest):
        return {"status": "kept_unmanaged", "path": str(destination)}
    if not dry_run:
        destination.unlink()
        manifest.unlink()
    return {"status": "would_remove" if dry_run else "removed", "path": str(destination)}
