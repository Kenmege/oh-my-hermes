# Optional local show-reel toolkit

This is executable **companion data**, not an OMH renderer command. Catalog,
install, update, routing and doctor only materialize/read its declared UTF-8
bytes. They never import/run these scripts or install production tools.

Selected motion/audio/capture techniques are modified MIT derivatives of
revfactory/showreel, pinned at `64521220a2f8e91cb9894400c48c15b7f45205d6`.
Copyright (c) 2026 Minho Hwang (revfactory). Full license: `LICENSE.upstream`;
per-file source hashes/modification inventory: `upstream-manifest.json`.
OMH's skill method is original guidance; companion code is **not** represented
as original/unmodified upstream code or full upstream runtime parity. Font,
brand, recording and other asset rights are separate from the code license.

## Explicit host invocation

Resolve this directory from the loaded skill's base/returned `skill_dir`;
read this README and script help with ordinary file tools. Hermes may use
`read_file(<skill_dir>/toolkit/README.md)`; portable hosts use their own file
reader. No Hermes-only tool is needed to use the portable kit.

Prerequisites: Python >=3.11 for initialization/timeline/PCM; optional Node,
**explicitly supplied** local `puppeteer-core` dependency root, Chrome executable,
FFmpeg (with zscale + libx264) and FFprobe for capture. No npm/pip/font setup,
CDN, network client, provider account or home font scan is performed. Use only
explicit licensed local TTF/OTF fonts for rendering: the cmap gate does not
support WOFF/collections. A missing font/tool/glyph is HOLD, never substitution.
Only macOS optional capture execution has been exercised. Linux/Windows render
execution is HOLD/refused; installing/copying the data remains portable. Actual
host smoke used Chrome **headless-shell** 145.0.7632.6 and puppeteer-core 25.15.0;
full Chrome headless surface capture stalled on that host. This is a toolchain
limitation, not permission to change the adapter silently.

Operator examples, after selecting an output workspace and verifying rights:

```sh
python toolkit/scripts/new_project.py NEW_PROJECT --font /explicit/font.ttf --duration 12 --fps 30 --width 1920 --height 1080 --language en --direction ltr
python toolkit/scripts/timeline.py NEW_PROJECT/timing.json
node toolkit/scripts/render.mjs --dir NEW_PROJECT/reel --out NEW_PROJECT/gallery.mp4 --browser /explicit/chrome-headless-shell --dependency-root /explicit/node_modules --ffmpeg /explicit/ffmpeg --ffprobe /explicit/ffprobe
node toolkit/scripts/render.mjs --dir NEW_PROJECT/reel --out NEW_PROJECT/seeks --browser /explicit/chrome-headless-shell --dependency-root /explicit/node_modules --ffmpeg /explicit/ffmpeg --ffprobe /explicit/ffprobe --stills .2,3.2,6.2,9.2,3.2
python toolkit/scripts/synth_music.py toolkit/examples/music-spec.json --out NEW_PROJECT/score.wav
python toolkit/scripts/mix.py NEW_PROJECT/mix-spec.json --out NEW_PROJECT/mix.wav --ffmpeg /explicit/ffmpeg
python toolkit/scripts/qa_check.py NEW_PROJECT/final.mp4 --audio-mode music-only --ffmpeg /explicit/ffmpeg --ffprobe /explicit/ffprobe
python toolkit/scripts/motion_check.py NEW_PROJECT/final.mp4 --ffmpeg /explicit/ffmpeg --holds NEW_PROJECT/intentional-holds.json
```

All output paths above must be new. Initialization refuses an existing project,
including authored scenes; select a new directory rather than destructive reset.
It copies only fixed kit files and the explicit `--font`/`--asset` inputs.
Edit `reel/scene.js` for your direction, not OMH's installed copy. `project.json`
and `project.js` must carry the same configuration; include authored inputs in
`render_files` (explicit POSIX allowlist, not a recursive serve root), update
`input_sha256` when intentionally replacing assets/fonts, and retain those
inputs/hashes in your source kit. Never use secrets or unreviewed scripts.

## Callable techniques (not a stock film)

`REEL` exposes clamp/lerp/prog/ease/kf/spring/rand. All motion derives from
absolute time and seed, never accumulated frames, CSS animation or Math.random.
The `TECHNIQUES` functions return parameterized geometry/poses:

- `kineticPose(t,{start,duration,stagger,index,rise})`: mask/stagger with
  anticipation and spring settling. Animate **intact runs** by default. Supply
  separate safe runs only for typography that permits segmentation.
- `depthGeometry(t,{seed,count,scale,cx,cy})`: isometric boxes, depth order and
  moving directional light; `FX.project`/`FX.light` are reusable separately.
  Painter ordering is intended for equal disjoint grid cells, not arbitrary
  intersecting geometry. Light/occlusion teaches volume, not a mandatory HUD.
- `particleGeometry(t,{seed,count,cx,cy,radius,start,drag,gravity})`: seeded disk
  samples with closed-form drag/gravity; replace samples with approved geometry
  for a meaningful silhouette. This is not a claim of a glyph-particle engine.
- `matchGeometry(t,{from,to,start,duration})`: carry one semantic object's actual
  measured `{x,y,w,h}` into the next composition. Preserve its focal meaning,
  not merely a decorative wipe between unrelated cards.

The initialized `index.html`/`scene.js` is a four-technique teaching gallery,
NOT the starting storyboard for every film. Choose only techniques that serve
the concept. Palette, content, timing, geometry, seed and light are inputs;
restrained direction is valid. No mandatory four-effect list or quality score.

## Locales, readiness, containment and capture

Each `PROJECT.texts` item has literal `text`, `lang`, `dir`; `literal:true` uses
an isolated LTR `bdi` for URLs/commands/IDs. Arabic stays an intact shaped run;
never reverse strings or infer cues from spoken substrings. Use explicit
language, direction and font families for the run. No Korean/English voice or
segmentation default is inferred from the user's input language. Cmap coverage,
loaded fonts and representative renders do **not** certify shaping, cultural
copy, legibility or native-language correctness. Canvas text must also be
explicitly declared in `texts`; arbitrary authored canvas glyphs cannot be
introspected by the DOM check.

`__seek(seconds)` writes deterministic state synchronously (may return a promise
for explicit readiness); `__ready` is true only after font/image preparation;
`__error` fails the adapter; `__meta` binds width/height/duration/fps. The host
awaits seek/font/image readiness and compositor screenshot completion. Running
CSS/Web Animations are refused. Actual shuffled seeks must match decoded pixels
in the pinned tool/font environment; no cross-platform MP4 byte equality claim.

Serial exact i/fps PNG capture only. No workers, chunking, blur, stale resume or
exists-only caching. Existing output/receipt refuses; partial frames remain
explicitly partial. Loopback static server serves only realpath-contained,
symlink-free allowlisted assets; sibling-prefix, traversal, URL, encoded path,
external request and range access refuse. Page/font/image errors fail before
final success; cancellation/timeout reaps local children/browser and server.
PNG sRGB primaries/transfer are **converted**, not merely tagged, to limited
BT.709 via zscale, then full decode/frame/color checks precede success receipt.

Bounds: 1/60..600 seconds; 1..60 fps; integral duration*fps <=36000; even
16..4096 dimensions; <=20 billion total source pixels; <=512 input files,
32 MiB per input and 256 MiB total snapshotted asset bytes, <=512 MiB capture/output,
symlink-free regular JSON specs <=1 MiB, explicit wall timeout 1..600s
(default 120). Audio <=5,760,000 stereo sample frames; rates 8/16/24/44.1/48kHz.
These are resource policy bounds, not film defaults or artistic thresholds.
No partial file or parse failure becomes a measured zero/success.

## Audio modes and measurement

`synth_music.py` supplies seeded section notes/energy/BPM and optional
`tick`/`boom`/`whoosh` events; returns score/SFX stems. It is a modest local
fallback, not equivalence to a licensed orchestral score or paid provider.
Mix accepts explicit stereo 16-bit PCM WAV at one declared sample rate; missing,
corrupt or mismatched audio refuses (use an explicit host conversion first).
Modes are `muted`, `music-only`, `narration`, `narration+bed/SFX`. Required tracks
never disappear silently; muted cannot discard requested tracks. Narration is
optional, imported/rights-cleared and never synthesized by a default voice.
Music gain automation is `[[seconds,dB],...]`; events use `at` numeric or exact
cue ID and `align:start|peak|end`. Speech energy/lookahead ducks the bed and
restores it outside speech; narration/bed/SFX stems survive. The sample limiter
is **not** a true-peak certificate. `--ffmpeg` measures LUFS/true peak; it does
not normalize to the target. Explicit host mastering/mux is separate; measure
**final AAC** with QA and the chosen `--policy` JSON (`lufs`, `true_peak`,
`duration`, `fps`, `frames`, `max_bytes`, `width`, `height`). Loudnorm decode or
parse failure is HOLD/nonzero, including nonfinite silence measurements.

## Independent acceptance

Technical QA binds SHA256 and full decode to measured checks. Motion QA samples
low-resolution movement/brightness, with authored intentional-hold intervals
`[{"start":0,"end":1,"reason":"reading hold reviewed in storyboard"}]`.
A freeze is not automatically an intentional hold, and brightness heuristics
are not flash/accessibility certification. Strict issues exit nonzero.

A technical PASS never fills artistic, full-speed playback, listening, STT or
native-language approval. Review delivery-size style/motion strips and both
cut boundaries, critique every segment, identify strongest/weakest, rebuild
the weakest with before/after evidence and review the final encoded result.
Reject repeated headline/card/rail grammar even when all technical checks pass.
Small gallery/locale/audio fixtures do not complete a full-length commissioned
film. Delivery, receipt and public publication remain separate observations.
