"""Conservative native support ownership; declared text is never imported/executed."""
from __future__ import annotations

from pathlib import Path
import re

from ..core.errors import OmhError
from ..hashutil import sha256_file, sha256_text
from ..local_store import atomic_write_text
from ..skills.catalog import omh_skill_install_path
from ..skills.support_files import checked_support_path, validate_support_path, validate_support_templates


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
