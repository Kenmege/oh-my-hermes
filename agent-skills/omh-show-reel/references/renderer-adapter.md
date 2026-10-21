# Selected host-tool renderer recipe

## Companion decision

This release deliberately ships a host-tool recipe, not vendored upstream engine
code or a new core execution command. A local browser/Canvas or HTML renderer plus
FFmpeg can supply deterministic seek, frames and reproducible export without an
additional maintained renderer dependency. This is narrower than upstream parity:
parallel chunk scheduling, resumable manifests and automatic QA are not shipped
engines. The author must implement and verify those only when the project needs
them, or mark them unavailable. No automatic installs or network API clients.
The original method was informed conceptually by revfactory/showreel revision
64521220a2f8e91cb9894400c48c15b7f45205d6 (MIT, Copyright 2026 Minho Hwang
(revfactory)); no upstream code/templates/prose are included. Retain its MIT
license/copyright and pin hashes if a later approved adapter reuses substantial
portions; asset/font licenses are independent.

## Preflight and scope

Record `show_reel_renderer_handoff/v1`: selected executor, exact versions and paths,
observed capabilities, approved cwd/input/output roots, dimensions/fps/duration,
network/asset policy, seed/time semantics, font readiness, commands, timeout,
cancel/reap method, chunk/resume policy and failure log references. Probe through
the exact interpreter/Node/browser invocation that will render, not another tool
context. Browser/Node/FFmpeg are optional host dependencies, never core packages.
Do not scan accounts, credentials or another profile; no API keys needed here.

An unavailable renderer returns storyboard/source handoff and HOLD on a requested
video. Missing font, pageerror, timeout, failed subprocess or partial frames must
surface as failed/partial evidence; never silently retry to an unrelated renderer.
No promised voice, translation or Arabic support without observed exact capability.

## Implement a deterministic local scene with observed host tools

1. Author an isolated local HTML/Canvas project. Expose `seek(tSeconds)` as a pure
   function of absolute time, fixed seed, approved assets and dimensions. A paused
   timeline may be used; no wall-clock delta, random unseeded values, external
   fonts, CSS animation clock, async race or network asset. Wait for local images
   and `document.fonts.ready`; render representative script runs before capture.
2. Capture exact frame `i` at `t=i/fps`, for `i=0..N-1`, with documented rounding
   of duration×fps. For Canvas, export the actual `toDataURL('image/png')` surface
   after synchronous draw; HTML needs a proven compositor screenshot barrier.
   Browser seek should update text and layout before that barrier. Record errors.
3. Seek 0, an interior time, end, then the same interior time out of order; compare
   decoded pixel hashes at identical times. Independent seeks must not depend on
   prior frames. A matching file hash alone is weaker than decoded pixel equality.
4. If chunking is needed, give chunks disjoint half-open frame ranges `[a,b)`, same
   global time base/seed/assets and one output owner. Resume only after recipe,
   asset, font and tool digests match and existing frames decode at expected
   dimensions. Missing/mismatched frames re-render; never concatenate partial
   files as success. Parallel/resume is unavailable until this is actually tested.
5. Encode a selected existing frame sequence with explicit container and color
   conversion. This example is for verified sRGB Canvas frames only; other input
   profiles need their own conversion. Agent/operator recipe (replace paths/rates):

```sh
ffmpeg -framerate 24 -i frames/%06d.png -an -vf 'scale=out_color_matrix=bt709:out_range=tv,colorspace=iall=bt709:itrc=iec61966-2-1:all=bt709:range=tv:format=yuv420p,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709' -c:v libx264 -pix_fmt yuv420p -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 -movflags +faststart share.mp4
ffprobe -v error -show_format -show_streams -of json share.mp4
ffmpeg -v error -i share.mp4 -f null -
```

These are preparation examples, not auto-run commands or universal channel
compatibility. Explicitly confirm source color interpretation before conversion;
metadata labels alone do not prove correct color. Color transfer conversion and
frame metadata must survive the filter chain; encoder flags alone may leave
primaries/transfer unknown, so inspect the final ffprobe fields and decoded colors
rather than trusting command success. With voice/music, mix approved local audio
and measure loudness/true peak and sync; do not use `-an` then claim audio.
For requested GIF use an actual palette encode and inspect decoded timing/loop;
an MP4 renamed GIF is not a GIF. Do not install dependencies to make a recipe work
without permission. Keep browser profiles and temporary frames invocation-owned,
reap child processes on cancel/timeout, and retain receipts outside deleted homes.

## Timing first, only as needed

Choose muted, music-only or narration explicitly. Muted needs measured reading
and visual rhythm, no synthetic audio. Music-only needs rights and beat/cue timings.
Narration needs approved literal script plus separate pronunciation_text, licensed
voice/locale capability, cost/consent and measured utterance/word cues before scene
locking. Translation permission and native review remain separate. STT on real
speech can compare words/times, not certify pronunciation or replace listening.
