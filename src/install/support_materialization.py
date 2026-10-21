"""Conservative native support ownership; declared text is never imported/executed."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import os
import re

from ..core.errors import OmhError
from ..hashutil import sha256_file, sha256_text
from ..local_store import atomic_write_text
from ..skills.catalog import omh_skill_install_path
from ..skills.support_files import checked_support_path, validate_support_path, validate_support_templates


def legacy_support_paths(paths):
    """Translate only the bootstrap's direct link to the migration's legacy pack.

    The general support reader/writer still refuses every symlink component.
    Check both lexical parents and the named destination before translating;
    resolving arbitrary aliases would erase the boundary that reader enforces.
    """
    alias = paths.skills_dir.absolute()
    if not alias.is_symlink() or alias.parts[-3:] != ("generations", "bootstrap-legacy", "skills"):
        return paths
    legacy = (paths.omh_home / "skills").absolute()
    try:
        checked_support_path(alias.parent, "toolkit/root-check.py")
        checked_support_path(legacy, "toolkit/root-check.py")
        # Match migrate_legacy's existing default-store fallback, not an arbitrary
        # manifest root: a newly named store has no legacy pack of its own yet.
        if not legacy.is_dir():
            from ..plugin_bundle.omh import runtime_paths

            legacy = (runtime_paths.standalone_default_omh_home(paths.hermes_home) / "skills").absolute()
            checked_support_path(legacy, "toolkit/root-check.py")
        target = Path(os.path.abspath(alias.parent / os.readlink(alias)))
        checked_support_path(target, "toolkit/root-check.py")
        # Windows readlink may return an extended-length spelling of the same
        # directory. Identity is safe only after both lexical roots reject links.
        if not legacy.is_dir() or not os.path.samefile(target, legacy):
            raise ValueError("bootstrap legacy link must directly name this home's skills directory")
    except (ValueError, OSError) as exc:
        raise OmhError(str(exc)) from exc
    return replace(paths, managed_skills_dir=legacy)


def support_records(manifest):
    records = (manifest or {}).get("support_files", {})
    if not isinstance(records, dict):
        raise OmhError("invalid native support_files manifest")
    seen = set()
    for relative, digest in records.items():
        prefix = omh_skill_install_path("show-reel") + "/"
        if not isinstance(relative, str) or not relative.startswith(prefix):
            raise OmhError("invalid native support ownership path")
        try:
            validate_support_path(relative.removeprefix(prefix))
        except ValueError as exc:
            raise OmhError(str(exc)) from exc
        if relative.casefold() in seen or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise OmhError("invalid/colliding native support ownership digest")
        seen.add(relative.casefold())
    return dict(records)


def support_path(skills_dir: Path, relative: str):
    prefix = omh_skill_install_path("show-reel") + "/"
    if not relative.startswith(prefix):
        raise OmhError("invalid support owner")
    try:
        return checked_support_path(skills_dir / omh_skill_install_path("show-reel"), relative.removeprefix(prefix))
    except ValueError as exc:
        raise OmhError(str(exc)) from exc


def preflight_support(skills_dir, templates, manifest, *, overwrite=False):
    validate_support_templates(templates)
    previous = support_records(manifest)
    current = {f"{omh_skill_install_path(t.skill_name)}/{t.relative_path}": t for t in templates}
    for relative in previous.keys() | current.keys():
        target = support_path(skills_dir, relative)
        template = current.get(relative)
        if template is None or not target.exists():
            continue
        observed = sha256_file(target)
        desired = sha256_text(template.content)
        if relative not in previous and observed != desired:
            raise OmhError(f"unowned support collision preserved: {relative}")
        if relative in previous and observed != previous[relative] and not overwrite:
            raise OmhError(f"edited support preserved; review or explicit --force required: {relative}")
    return current


def write_support(skills_dir, current, manifest, *, rendered_names):
    previous = support_records(manifest)
    result = dict(previous)
    for relative, template in current.items():
        atomic_write_text(support_path(skills_dir, relative), template.content)
        result[relative] = sha256_text(template.content)
    if "show-reel" in rendered_names:
        for relative in previous.keys() - current.keys():
            target = support_path(skills_dir, relative)
            # Only the previous unchanged owned FILE, never a directory/foreign toolkit tree.
            if target.is_file() and sha256_file(target) == previous[relative]:
                target.unlink()
                result.pop(relative, None)
            elif not target.exists():
                result.pop(relative, None)
    return result
