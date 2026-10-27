"""Hash-anchored line editing: pure core logic, testable without Hermes.

Lines are addressed as ``N:HHH`` anchors, where ``N`` is the 1-based line
number and ``HHH`` is the first 3 hex chars of the SHA-1 of the line text
(without its newline). An anchor whose hash no longer matches the file is
stale; a stale anchor never causes a write (all-or-nothing validation).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass


class HashlineError(Exception):
    """Validation failure; surfaced as JSON, never raised to the host."""


def line_hash(line_text: str) -> str:
    """First 3 hex chars of SHA-1 of the line text (no newline), UTF-8."""
    return hashlib.sha1(line_text.encode("utf-8")).hexdigest()[:3]


@dataclass
class FileContent:
    """A text file split into lines, keeping line ending style and trailing newline."""

    lines: list[str]
    uses_crlf: bool
    ends_with_newline: bool

    @classmethod
    def parse(cls, raw: str) -> "FileContent":
        uses_crlf = "\r\n" in raw
        if uses_crlf:
            raw = raw.replace("\r\n", "\n")
        ends_with_newline = raw.endswith("\n")
        lines = raw.split("\n")
        if ends_with_newline:
            lines.pop()  # drop the empty piece after the final newline
        return cls(lines=lines, uses_crlf=uses_crlf, ends_with_newline=ends_with_newline)

    def render(self) -> str:
        eol = "\r\n" if self.uses_crlf else "\n"
        body = eol.join(self.lines)
        return body + eol if self.ends_with_newline else body


def parse_anchor(anchor: str) -> tuple[int, str]:
    """Parse ``N:HHH``; raise HashlineError on malformed anchors."""
    parts = anchor.split(":")
    if len(parts) != 2:
        raise HashlineError(f"invalid_type:/anchor — {anchor!r} is not in N:HHH form")
    n_raw, h = parts
    if not n_raw.isdigit() or int(n_raw) < 0:
        raise HashlineError(f"invalid_type:/anchor — line number in {anchor!r} must be a non-negative integer")
    if len(h) != 3 or any(c not in "0123456789abcdef" for c in h):
        raise HashlineError(f"invalid_type:/anchor — hash in {anchor!r} must be 3 hex chars")
    return int(n_raw), h


def anchor_matches(content: FileContent, anchor: str) -> bool:
    """Whether the anchor is valid for the content. ``0:000`` is always valid."""
    if anchor == "0:000":
        return True
    n, h = parse_anchor(anchor)
    if n < 1 or n > len(content.lines):
        return False
    return line_hash(content.lines[n - 1]) == h


def resolve_path(path: str, terminal_cwd: str | None) -> str:
    """Resolve a path against the workspace root (else cwd); refuse paths escaping that root.

    Both sides are compared after ``realpath``: a symlink inside the root that points outside
    it is an escape, and an absolute path spelled through a symlinked prefix (macOS ``/var`` ->
    ``/private/var``) is still inside.
    """
    root_abs = os.path.realpath(terminal_cwd if terminal_cwd else os.getcwd())
    resolved = os.path.realpath(os.path.join(root_abs, path))
    if resolved != root_abs and not resolved.startswith(root_abs + os.sep):
        raise HashlineError(f"scope_mismatch:/path — {path!r} resolves outside the root {root_abs}")
    return resolved


def is_binary_file(path: str) -> bool:
    """Heuristic: a NUL byte in the first 8 KiB marks a binary file."""
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return False


def read_lines(path: str, offset: int = 1, limit: int = 400) -> dict:
    """Read a file as ``{n}:{h}|{text}`` lines with total count and truncation flag."""
    if is_binary_file(path):
        raise HashlineError("invalid_type:/path — file appears to be binary")
    with open(path, "r", encoding="utf-8", errors="strict", newline="") as fh:
        raw = fh.read()
    content = FileContent.parse(raw)
    total = len(content.lines)
    start = max(offset, 1)
    end = min(total, start - 1 + max(limit, 0))
    lines = [f"{start + i}:{line_hash(t)}|{t}" for i, t in enumerate(content.lines[start - 1:end])]
    return {"path": path, "total_lines": total, "truncated": end < total, "lines": lines}


@dataclass
class _PlannedEdit:
    op: str
    lo: int  # 0-based first affected original line
    hi: int  # exclusive
    text: str


def _validate_and_plan(content: FileContent, edits: list[dict]) -> list[_PlannedEdit]:
    """Validate every anchor first; collect ALL mismatches; reject overlaps."""
    stale: list[str] = []
    for raw in edits:
        if not isinstance(raw, dict):
            raise HashlineError("invalid_type:/edits — each edit must be an object")
        op = raw.get("op")
        if op not in ("replace", "insert_after", "delete"):
            raise HashlineError(f"unsupported_value:/edits/op — {op!r}")
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise HashlineError("invalid_type:/edits/text — must be a string")
        anchors: list[str] = []
        if op == "insert_after":
            if not raw.get("anchor"):
                raise HashlineError("missing_field:/edits/anchor")
            anchors = [str(raw["anchor"])]
        else:
            if not raw.get("start"):
                raise HashlineError("missing_field:/edits/start")
            anchors = [str(raw["start"])]
            if raw.get("end"):
                anchors.append(str(raw["end"]))
        for a in anchors:
            if not anchor_matches(content, a):
                stale.append(a)
    if stale:
        raise HashlineError("stale_anchor:" + ", ".join(sorted(set(stale))))

    planned: list[_PlannedEdit] = []
    for raw in edits:
        op = raw["op"]
        if op == "insert_after":
            n, _ = parse_anchor(str(raw["anchor"]))
            pos = 0 if n == 0 else n
            planned.append(_PlannedEdit(op, pos, pos, str(raw.get("text") or "")))
        else:
            ns, _ = parse_anchor(str(raw["start"]))
            ne, _ = parse_anchor(str(raw.get("end") or raw["start"]))
            planned.append(_PlannedEdit(op, ns - 1, ne, str(raw.get("text") or "")))
    for i in range(len(planned)):
        for j in range(i + 1, len(planned)):
            a, b = planned[i], planned[j]
            if a.lo < b.hi and b.lo < a.hi:
                raise HashlineError(
                    f"overlap:/edits — edit {i + 1} ({a.lo + 1}..{a.hi}) overlaps edit {j + 1} ({b.lo + 1}..{b.hi})"
                )
    return planned


def _apply(content: FileContent, planned: list[_PlannedEdit]) -> FileContent:
    new_lines: list[str] = []
    cursor = 0
    for p in sorted(planned, key=lambda p: p.lo):
        new_lines.extend(content.lines[cursor:p.lo])
        if p.op == "replace" and p.text == "":
            pass  # empty text = delete
        elif p.op in ("replace", "insert_after"):
            new_lines.extend(p.text.split("\n"))
        cursor = p.hi  # delete / replace consume the original range
    new_lines.extend(content.lines[cursor:])
    return FileContent(lines=new_lines, uses_crlf=content.uses_crlf, ends_with_newline=content.ends_with_newline)


def _mismatch_context(content: FileContent, anchors: list[str]) -> list[dict]:
    """Fresh ``n:h|text`` lines ±3 around each mismatched anchor."""
    out = []
    for a in anchors:
        try:
            n, _ = parse_anchor(a)
        except HashlineError:
            continue
        lines = [
            f"{i}:{line_hash(content.lines[i - 1])}|{content.lines[i - 1]}"
            for i in range(max(1, n - 3), min(len(content.lines), n + 3) + 1)
        ]
        out.append({"anchor": a, "lines": lines})
    return out


def _atomic_write(path: str, content: FileContent) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content.render())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _edit_result(new: FileContent, planned: list[_PlannedEdit]) -> dict:
    """Lines changed + fresh anchors ±2 around each edited region for chaining."""
    regions = []
    for p in planned:
        if p.op == "delete" or (p.op == "replace" and p.text == ""):
            inserted, removed = 0, p.hi - p.lo
            new_start = p.lo
        elif p.op == "insert_after":
            seg = p.text.split("\n")
            inserted, removed = len(seg), 0
            new_start = p.lo
        else:
            seg = p.text.split("\n")
            inserted, removed = len(seg), p.hi - p.lo
            new_start = p.lo
        fresh = [
            f"{i}:{line_hash(new.lines[i - 1])}|{new.lines[i - 1]}"
            for i in range(max(1, new_start - 2 + 1), min(len(new.lines), new_start + inserted + 2) + 1)
        ]
        regions.append({"op": p.op, "lines_changed": inserted + removed, "fresh_anchors": fresh})
    return {"ok": True, "lines_changed": sum(r["lines_changed"] for r in regions), "regions": regions}


def create_file(path: str, text: str) -> dict:
    """Create a nonexistent file (insert_after ``0:000`` on a missing file)."""
    if os.path.exists(path):
        raise HashlineError(f"invalid_state:/path — file already exists: {path}")
    if os.path.exists(path):
        content = FileContent.parse(text)
        _atomic_write(path, content)
    else:
        content = FileContent.parse(text)
        content.ends_with_newline = True
        _atomic_write(path, content)
    return {"ok": True, "created": path, "total_lines": len(content.lines)}


def edit_file(path: str, edits_raw: list) -> dict:
    """Validate all anchors, then apply the edits atomically; on mismatch write nothing."""
    if not edits_raw:
        raise HashlineError("missing_field:/edits — at least one edit is required")
    if not os.path.exists(path):
        # A nonexistent file may be created only via insert_after "0:000".
        if len(edits_raw) == 1 and isinstance(edits_raw[0], dict) \
                and edits_raw[0].get("op") == "insert_after" and str(edits_raw[0].get("anchor", "")) == "0:000":
            created = create_file(path, str(edits_raw[0].get("text") or ""))
            return {"ok": True, "lines_changed": created["total_lines"],
                    "regions": [{"op": "insert_after", "lines_changed": created["total_lines"],
                                 "fresh_anchors": [f"{i}:{line_hash(t)}|{t}"
                                                   for i, t in enumerate(FileContent.parse(str(edits_raw[0].get('text') or '')).lines)]}],
                    "path": path}
        raise HashlineError(f"invalid_state:/path — file not found: {path}")
    if is_binary_file(path):
        raise HashlineError("invalid_type:/path — file appears to be binary")
    try:
        with open(path, "r", encoding="utf-8", errors="strict", newline="") as fh:
            content = FileContent.parse(fh.read())
    except UnicodeDecodeError:
        raise HashlineError("invalid_type:/path — file cannot be decoded as UTF-8 text")
    if not edits_raw:
        raise HashlineError("missing_field:/edits — at least one edit is required")
    try:
        planned = _validate_and_plan(content, edits_raw)
    except HashlineError as exc:
        msg = str(exc)
        payload: dict = {"ok": False, "error": msg}
        if msg.startswith("stale_anchor:"):
            anchors = msg[len("stale_anchor:"):].split(", ")
            payload["context"] = _mismatch_context(content, anchors)
        return payload
    new_content = _apply(content, planned)
    _atomic_write(path, new_content)
    result = _edit_result(new_content, planned)
    result["path"] = path
    return result
