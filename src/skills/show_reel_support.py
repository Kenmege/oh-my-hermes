"""Read the reviewed, fixed companion inventory via package resources, never cwd."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json

from .support_files import SkillSupportTemplate

SHOW_REEL_RESOURCES = (
    "README.md", "LICENSE.upstream", "upstream-manifest.json",
    "assets/engine/engine.js", "assets/engine/fx.js", "assets/engine/index.html",
    "scripts/new_project.py", "scripts/render.mjs", "scripts/font-coverage.mjs",
    "scripts/timeline.py", "scripts/synth_music.py", "scripts/mix.py",
    "scripts/motion_check.py", "scripts/qa_check.py",
    "examples/techniques/kinetic-type.js", "examples/techniques/depth-light.js",
    "examples/techniques/particles.js", "examples/techniques/match-transform.js",
    "examples/techniques/gallery.js", "examples/timing.json",
    "examples/music-spec.json", "examples/README.md",
)

UPSTREAM_SOURCES = {'LICENSE': 'd20cf1fae030dacd0a5775ca23e0d3e4a4abf677e1a6f0f8ef34603cc20d7f53', 'assets/engine/index.html': '4140bc2a6be1bdc00cfab85e4d4e1bb838be9845c9898ec1fede3e71238641ae', 'assets/engine/engine.js': 'b595f361e0e1f661f2cbe816326c54a893c4c668492120772c917e01fa1b2bb0', 'assets/engine/fx.js': 'cf028c72328aacc6a6a307538fa3f3668e6890b063f021b915dc061aa92c6f1d', 'assets/engine/scenes.js': '70ba3bcb53f4d061cd0e5789bea154bbf17901628fa5202d1b5c61954b14607b', 'scripts/render.mjs': '52ac114394256491721b3c86fdd7a7eb42796dd350701d38688382827a18d3a7', 'scripts/timeline.py': '95e872e1fb23f1509e8e60b64294c275260ced1aeaa27f66e5ba3cd00a6f8d77', 'scripts/synth_music.py': 'bd035ddeee3790e0990cdd4841942fd92fca4ba6533487427cd6ce78d625a4f7', 'scripts/mix.py': '569f5b2e285dd7239f20c278e274119ec5b685c2906a4dc270c161cc0e686f7d', 'scripts/motion_check.py': 'd2feb0d8dba77a1bd389507faf35cb2c761977c10565ef45b108de99ee2dc7c1', 'scripts/qa_check.py': '0765be750b043e0d99d544f52367ab512f04b391988f3f05faa47b0d1ebc5c15', 'scripts/new_project.py': 'b43f42fca4ba4ccd3b69c5d5372199b102b36de0f2dc496c2ad9f4d425200023', 'references/techniques.md': 'bb6f32abb1f557b58958748e0461b57f2da117a1ffc360e5148e56a418976cc8', 'references/design.md': 'd4e8ff5a84db3658e6d070dd1c5df62e60c83bcd478c794727147f339942db4f', 'references/engine.md': '2639e9fdb49a846bb18d1a4382890f228e5a85f3123637588d7e21668864fcf6', 'references/audio.md': '671d920b4ccde7444a686cc32a1e8d860d914b06487138c69faf8b86fe32c885', 'references/qa.md': 'bb5cbfaaba8ed87a8cdcbb1d92566760ee872dbdfe5605198fa81bb033262727'}
DERIVATIONS = {'assets/engine/engine.js': ['assets/engine/engine.js'], 'assets/engine/fx.js': ['assets/engine/fx.js', 'references/techniques.md'], 'assets/engine/index.html': ['assets/engine/index.html'], 'scripts/new_project.py': ['scripts/new_project.py'], 'scripts/render.mjs': ['scripts/render.mjs'], 'scripts/timeline.py': ['scripts/timeline.py'], 'scripts/synth_music.py': ['scripts/synth_music.py'], 'scripts/mix.py': ['scripts/mix.py'], 'scripts/motion_check.py': ['scripts/motion_check.py'], 'scripts/qa_check.py': ['scripts/qa_check.py'], 'examples/techniques/kinetic-type.js': ['assets/engine/engine.js', 'assets/engine/scenes.js'], 'examples/techniques/depth-light.js': ['references/techniques.md'], 'examples/techniques/particles.js': ['assets/engine/fx.js', 'references/techniques.md'], 'examples/techniques/match-transform.js': ['references/techniques.md'], 'examples/techniques/gallery.js': ['assets/engine/scenes.js', 'references/techniques.md'], 'LICENSE.upstream': ['LICENSE']}


def provenance_manifest():
    root = files("omh.skills").joinpath("show_reel_toolkit")
    entries = {}
    for name in SHOW_REEL_RESOURCES:
        if name == "upstream-manifest.json":
            continue
        source_paths = DERIVATIONS.get(name, [])
        entries[name] = {
            "local_sha256": hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest(),
            "classification": "unmodified_license" if name == "LICENSE.upstream" else "modified_derivative" if source_paths else "original_omh",
            "upstream": [{"path": source, "sha256": UPSTREAM_SOURCES[source]} for source in source_paths],
            "modification_notice": "Modified selected techniques for OMH local-only deterministic companion; not upstream runtime parity" if source_paths and name != "LICENSE.upstream" else "License verbatim" if source_paths else "Original OMH companion guidance/helper/example",
            "license": "MIT",
        }
    return {"schema": "omh_show_reel_provenance/v1", "repository": "https://github.com/revfactory/showreel", "revision": "64521220a2f8e91cb9894400c48c15b7f45205d6", "copyright": "Copyright (c) 2026 Minho Hwang (revfactory)", "license": "LICENSE.upstream", "self_hash": "excluded; outer native/portable inventory hashes this file", "files": entries}


def show_reel_support_templates():
    root = files("omh.skills").joinpath("show_reel_toolkit")
    texts = {name: root.joinpath(name).read_text(encoding="utf-8") for name in SHOW_REEL_RESOURCES}
    provenance = json.loads(texts["upstream-manifest.json"])
    expected = set(SHOW_REEL_RESOURCES) - {"upstream-manifest.json"}
    if provenance.get("schema") != "omh_show_reel_provenance/v1" or set(provenance.get("files", {})) != expected:
        raise ValueError("invalid closed toolkit provenance inventory")
    for name in expected:
        if hashlib.sha256(texts[name].encode("utf-8")).hexdigest() != provenance["files"][name].get("local_sha256"):
            raise ValueError(f"toolkit provenance digest mismatch: {name}; regenerate from reviewed canonical resources")
    return [SkillSupportTemplate("show-reel", "toolkit/" + name, texts[name]) for name in SHOW_REEL_RESOURCES]
