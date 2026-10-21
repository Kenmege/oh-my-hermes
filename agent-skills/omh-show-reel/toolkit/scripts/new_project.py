# MIT; Copyright (c) 2026 Minho Hwang (revfactory).
# Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6
# scripts/new_project.py: local-only inputs; no font search/download or npm setup.
# Full permission/warranty: ../LICENSE.upstream. Standalone, not an OMH import.
"""Initialize a NEW directory from this local kit and explicitly selected fonts/assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from timeline import settings

KIT = Path(__file__).resolve().parents[1]
ENGINE_FILES = ("engine.js", "fx.js", "index.html")
TECHNIQUE_FILES = ("kinetic-type.js", "depth-light.js", "particles.js", "match-transform.js")


def no_links(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"symlink input/destination refused: {path}")
    return path


def initialize(destination, *, duration=12, fps=30, width=1920, height=1080, language="en", direction="ltr", fonts=(), assets=(), seed=7):
    config = settings(duration, fps, width, height)
    if direction not in ("ltr", "rtl") or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,34}", language):
        raise ValueError("explicit BCP47-like language and ltr/rtl direction required")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be an integer in [0, 2147483647]")
    destination = no_links(destination)
    if destination.exists():
        raise FileExistsError(f"preserving existing project: {destination}; select a new directory")
    if not fonts:
        raise ValueError("HOLD: supply explicit licensed local --font; no automatic font discovery/download")
    inputs = []
    seen = set()
    for source in (*fonts, *assets):
        path = no_links(source)
        if not path.is_file():
            raise FileNotFoundError(f"HOLD: local input missing: {path}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", path.name) or path.name.casefold() in seen:
            raise ValueError("unsafe/colliding local asset name")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("local asset exceeds 32 MiB limit")
        seen.add(path.name.casefold())
        inputs.append(path)
    for font in fonts:
        if Path(font).suffix.lower() not in (".ttf", ".otf", ".woff", ".woff2"):
            raise ValueError("font must be explicit TTF/OTF/WOFF/WOFF2; font load validated by renderer")
    copies = {"assets/engine/" + name: KIT / "assets/engine" / name for name in ENGINE_FILES if name != "index.html"}
    copies.update({"techniques/" + name: KIT / "examples/techniques" / name for name in TECHNIQUE_FILES})
    copies.update({"index.html": KIT / "assets/engine/index.html", "scene.js": KIT / "examples/techniques/gallery.js"})
    copies.update({"assets/" + path.name: path for path in inputs})
    # Read/preflight every source before creating destination. No partial success on refusal.
    payloads = {name: path.read_bytes() for name, path in copies.items()}
    config.update(language=language, direction=direction, seed=seed,
                  fonts=[{"family": f"ReelFont{i}", "file": "assets/" + Path(p).name} for i, p in enumerate(fonts)],
                  audio_mode="muted", texts=[{"text": "Motion is a material", "lang": language, "dir": direction}],
                  input_sha256={"assets/" + p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    config["render_files"] = sorted([*payloads, "project.json", "project.js"])
    payloads["project.json"] = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()
    payloads["project.js"] = ("globalThis.PROJECT=" + json.dumps(config, ensure_ascii=True) + ";\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        for name, data in payloads.items():
            target = destination / "reel" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (destination / "timing.json").write_text(json.dumps({"duration": duration, "fps": fps, "cues": [{"id": "opening", "seconds": 0}]}, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        shutil.rmtree(destination)
        raise
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--duration", type=float, default=12)
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--language", default="en")
    parser.add_argument("--direction", choices=("ltr", "rtl"), default="ltr")
    parser.add_argument("--font", action="append", type=Path, default=[])
    parser.add_argument("--asset", action="append", type=Path, default=[])
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    config = initialize(args.destination, duration=args.duration, fps=args.fps, width=args.width, height=args.height, language=args.language, direction=args.direction, fonts=args.font, assets=args.asset, seed=args.seed)
    print(json.dumps({"status": "initialized_not_rendered", **config}, ensure_ascii=False))


if __name__ == "__main__":
    main()
