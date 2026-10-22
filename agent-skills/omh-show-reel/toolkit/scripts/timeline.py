# MIT; Copyright (c) 2026 Minho Hwang (revfactory).
# Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6
# scripts/timeline.py: explicit cue graph replaces spoken-substring inference.
# Full permission/warranty: ../LICENSE.upstream. Standalone, not an OMH import.
"""Validate first, then emit a language-neutral, narration-optional cue timeline."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def read_spec(path, expected=dict):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("spec must be a regular symlink-free UTF-8 JSON file <=1 MiB")
    def invalid_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid_constant)
    if not isinstance(value, expected):
        raise ValueError("spec has incorrect JSON root type")
    return value


def number(value, label, minimum: float=0, maximum: float=600):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be finite in [{minimum}, {maximum}]")
    return float(value)


def settings(duration, fps, width=1920, height=1080):
    duration = number(duration, "duration", 1 / 60, 600)
    fps = number(fps, "fps", 1, 60)
    frames = duration * fps
    if abs(frames - round(frames)) > 1e-7 or frames > 36000:
        raise ValueError("duration * fps must be an integer <= 36000 frames")
    for label, size in (("width", width), ("height", height)):
        number(size, label, 16, 4096)
        if int(size) != size or size % 2:
            raise ValueError(f"{label} must be an even integer")
    if width * height * frames > 20_000_000_000:
        raise ValueError("render pixel budget exceeded")
    return {"duration": duration, "fps": fps, "frames": round(frames), "width": int(width), "height": int(height)}


def compile_timeline(spec):
    config = settings(spec["duration"], spec["fps"], spec.get("width", 1920), spec.get("height", 1080))
    rows = spec.get("cues", [])
    if not isinstance(rows, list) or len(rows) > 4096:
        raise ValueError("cues must be a list of <=4096 entries")
    by_id = {}
    for row in rows:
        key = row.get("id")
        if not isinstance(key, str) or not key or len(key) > 128 or any(ord(c) < 32 for c in key) or key in by_id:
            raise ValueError("invalid/duplicate cue id")
        if sum(k in row for k in ("seconds", "frame", "after")) != 1:
            raise ValueError(f"cue {key}: choose exactly seconds, frame or after")
        by_id[key] = row
    resolved = {}
    pending = dict(by_id)
    while pending:
        progressed = False
        for key, row in list(pending.items()):
            if "after" in row:
                if row["after"] not in by_id:
                    raise ValueError(f"unknown cue: {row['after']}")
                if row["after"] not in resolved:
                    continue
                value = resolved[row["after"]] + number(row.get("offset", 0), "offset", -600, 600)
            elif "frame" in row:
                frame = number(row["frame"], "frame", 0, config["frames"])
                if frame != int(frame):
                    raise ValueError("cue frame must be integer")
                value = frame / config["fps"]
            else:
                value = row["seconds"]
            resolved[key] = round(number(value, f"cue {key}", 0, config["duration"]), 10)
            del pending[key]
            progressed = True
        if not progressed:
            raise ValueError("cyclic cue graph")
    return {**config, "cues": resolved, "alignment": spec.get("alignment", []), "alignment_evidence": "caller_supplied_not_verified"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timing", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = compile_timeline(read_spec(args.timing))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
