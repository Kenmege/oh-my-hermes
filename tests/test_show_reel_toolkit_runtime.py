"""Companion behavior, explicitly executed here, never imported by OMH."""
from __future__ import annotations

import json
from pathlib import Path
import os
import hashlib
import shutil
import subprocess
import sys
import tempfile
import runpy
import signal
import time
import socket
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "src/skills/show_reel_toolkit"
NODE = shutil.which("node")


class MotionHelpersTests(unittest.TestCase):
    def test_callable_absolute_time_techniques_are_order_independent(self):
        self.assertTrue((KIT / "assets/engine/engine.js").is_file(), "missing real motion helpers")
        if not NODE:
            self.skipTest("optional Node unavailable; real host smoke is separate")
        script = """
const fs = require('node:fs'), vm = require('node:vm');
const root = process.argv[1];
const c = {console}; c.globalThis = c; vm.createContext(c);
for (const f of ['assets/engine/engine.js', 'assets/engine/fx.js',
'examples/techniques/kinetic-type.js', 'examples/techniques/depth-light.js',
'examples/techniques/particles.js', 'examples/techniques/match-transform.js'])
  vm.runInContext(fs.readFileSync(root+'/'+f, 'utf8'), c);
const {REEL:R, TECHNIQUES:T} = c;
const times = [0, .15, .7, 1.9, .15, 0, 1.9];
const evaluate = t => [T.kineticPose(t,{start:.1,duration:.8,stagger:.05,index:2}),
 T.depthGeometry(t,{seed:7,count:9}), T.particleGeometry(t,{seed:7,count:20}),
 T.matchGeometry(t,{from:{x:10,y:20,w:30,h:40},to:{x:80,y:90,w:120,h:140},start:0,duration:2})];
const rows = times.map(evaluate);
if (JSON.stringify(rows[0])!==JSON.stringify(rows[5]) || JSON.stringify(rows[2])===JSON.stringify(rows[3]) ||
JSON.stringify(rows[3])!==JSON.stringify(rows[6])) throw Error('seek history leaked');
if (JSON.stringify(T.particleGeometry(1,{seed:7,count:20})) === JSON.stringify(T.particleGeometry(1,{seed:8,count:20}))) throw Error('seed ignored');
if (R.spring(-1)!==0 || !Number.isFinite(R.spring(.4))) throw Error('spring');
console.log(JSON.stringify(rows));
"""
        result = subprocess.run([NODE, "-e", script, str(KIT)], text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 7)


class LocalProjectTests(unittest.TestCase):
    def load(self, name):
        path = KIT / "scripts" / name
        self.assertTrue(path.is_file(), f"missing callable {name}")
        with patch.dict(sys.modules):
            sys.path.insert(0, str(path.parent))
            try:
                return runpy.run_path(str(path))
            finally:
                sys.path.pop(0)

    def test_specs_are_regular_bounded_json_without_symlink_reads(self):
        api = self.load("timeline.py")
        self.assertTrue(callable(api.get("read_spec")), "missing bounded standalone spec reader")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            good = root / "timing.json"
            good.write_text('{"duration":1,"fps":30,"cues":[]}')
            self.assertEqual(api["read_spec"](good)["duration"], 1)
            large = root / "large.json"
            large.write_bytes(b" " * 1_048_577)
            bad = root / "invalid.json"
            bad.write_text("not json")
            for path in (root, large, bad):
                with self.assertRaises(ValueError):
                    api["read_spec"](path)
            link = root / "link.json"
            try:
                link.symlink_to(good)
            except OSError:
                self.skipTest("symlink capability unavailable")
            with self.assertRaises(ValueError):
                api["read_spec"](link)

    def test_explicit_locale_neutral_cues_validate_before_output(self):
        api = self.load("timeline.py")
        spec = {"duration": 2, "fps": 30, "cues": [{"id": "مرحلة.一", "frame": 12}, {"id": "cut", "after": "مرحلة.一", "offset": .2}]}
        compiled = api["compile_timeline"](spec)
        self.assertEqual(compiled["cues"], {"مرحلة.一": .4, "cut": .6})
        for bad in [float("nan"), -1, 601]:
            with self.assertRaises(ValueError):
                api["compile_timeline"]({**spec, "duration": bad})
        for cues in [[{"id": "a", "after": "b"}, {"id": "b", "after": "a"}], [{"id": "a", "frame": 100}], [{"id": "a", "seconds": 0}, {"id": "a", "seconds": 1}], [{"id": "x", "after": "unknown"}]]:
            with self.assertRaises(ValueError):
                api["compile_timeline"]({**spec, "cues": cues})

    def test_initializer_copies_only_explicit_inputs_preserves_existing_scene(self):
        api = self.load("new_project.py")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            font = root / "chosen.ttf"
            font.write_bytes(b"local-font-fixture")
            with patch("subprocess.Popen", side_effect=AssertionError("implicit process")), patch("socket.socket", side_effect=AssertionError("implicit network")):
                out = root / "project"
                api["initialize"](out, duration=2, fps=30, width=640, height=360, language="ar", direction="rtl", fonts=[font])
            config = json.loads((out / "reel/project.json").read_text())
            self.assertEqual(config["language"], "ar")
            self.assertEqual(config["direction"], "rtl")
            self.assertEqual((out / "reel/assets/chosen.ttf").read_bytes(), font.read_bytes())
            scene = out / "reel/scene.js"
            scene.write_text("AUTHORED")
            with self.assertRaises(FileExistsError):
                api["initialize"](out, duration=2, fonts=[font])
            self.assertEqual(scene.read_text(), "AUTHORED")
            for kwargs in [{"duration": 0}, {"fps": float("inf")}, {"fonts": [root / "missing.ttf"]}, {"fonts": []}]:
                dest = root / "refused"
                with self.assertRaises((ValueError, FileNotFoundError)):
                    api["initialize"](dest, **kwargs)
                self.assertFalse(dest.exists())
            link = root / "linked"
            link.symlink_to(root / "project", target_is_directory=True)
            with self.assertRaises(ValueError):
                api["initialize"](link / "escape", fonts=[font])


class RendererBoundaryTests(unittest.TestCase):
    def test_local_asset_resolution_refuses_escape_symlink_url_and_sibling_prefix(self):
        adapter = KIT / "scripts/render.mjs"
        self.assertTrue(adapter.is_file(), "missing optional renderer")
        if not NODE:
            self.skipTest("optional Node unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            root.mkdir()
            (root / "ok.js").write_text("local")
            (root / "link.js").symlink_to(Path(td) / "outside.js")
            script = """
const {resolveAsset,noLinks,snapshotAssets} = await import(process.argv[1]);
let refusedLink=false;try{noLinks(process.argv[2]+'/link.js');}catch{refusedLink=true;}if(!refusedLink)throw Error('dangling symlink admitted');
const root=process.argv[2];
const snap=snapshotAssets(root,['ok.js']);
const fs=await import('node:fs');fs.writeFileSync(root+'/ok.js','changed after snapshot');
if(snap.buffers['ok.js'].toString()!=='local')throw Error('live mutable asset used');
let bounded=false;try{snapshotAssets(root,['ok.js'],2);}catch{bounded=true;}if(!bounded)throw Error('asset total byte cap ignored');
if(!resolveAsset(root,'ok.js',new Set(['ok.js'])).endsWith('/ok.js'))throw Error('valid local refused');
for(const p of ['../outside.js','/outside.js','http://example.org/x','..%2foutside.js','a\\\\b','link.js','missing.js']) {
 let refused=false;try{resolveAsset(root,p,new Set([p]));}catch{refused=true;}
 if(!refused)throw Error('unsafe admitted '+p);
}
console.log('safe');
"""
            result = subprocess.run([NODE, "--input-type=module", "-e", script, adapter.as_uri(), str(root)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(os.environ.get("SHOW_REEL_HOST_TOOLS"), "optional real browser/FFmpeg: run explicit host smoke")
class HostRenderTests(unittest.TestCase):
    def test_popup_cannot_bypass_primary_page_local_request_scope(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        hits = []
        connections = []
        class ObservedServer(HTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(1)
                connections.append(address)
                return connection, address
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(204)
                self.end_headers()
            def log_message(self, *args):
                return None
        listener = ObservedServer(("127.0.0.1", 0), Handler)
        listener.timeout = .5
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        tools = json.loads(Path(os.environ["SHOW_REEL_HOST_TOOLS"]).read_text())
        try:
            for scheme in ("http", "https"):
                with self.subTest(scheme=scheme), tempfile.TemporaryDirectory() as td:
                    hits.clear()
                    connections.clear()
                    root = Path(td)
                    dest = root / "project"
                    result = subprocess.run([sys.executable, str(KIT / "scripts/new_project.py"), str(dest), "--font", os.environ["SHOW_REEL_FONT"], "--duration", "1", "--fps", "4", "--width", "320", "--height", "180"], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    scene = dest / "reel/scene.js"
                    scene.write_text(scene.read_text()+f"\nsetTimeout(()=>window.open('{scheme}://127.0.0.1:{listener.server_port}/sentinel'),0);")
                    out = root / "popup.mp4"
                    command = [tools["binaries"]["node"], str(KIT / "scripts/render.mjs"), "--dir", str(dest / "reel"), "--out", str(out), "--browser", tools["chrome"], "--dependency-root", tools["available_puppeteer_core"]["root"], "--ffmpeg", tools["binaries"]["ffmpeg"], "--ffprobe", tools["binaries"]["ffprobe"]]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                    self.assertNotEqual(result.returncode, 0, "popup escaped request admission")
                    self.assertEqual(hits, [], "browser sent an undeclared loopback HTTP request")
                    self.assertEqual(connections, [], "browser connected outside declared asset origin")
                    self.assertFalse(out.exists())
                    self.assertFalse(Path(str(out) + ".receipt.json").exists())
                    receipt = json.loads(Path(str(out) + ".failure.json").read_text())
                    self.assertIn("proxy CONNECT refused" if scheme == "https" else "proxy request refused", receipt["error"])
                    self.assertTrue(all(receipt["cleanup"].values()))
                    with self.assertRaises(ProcessLookupError):
                        os.kill(receipt["browser_pid"], 0)
                    with socket.socket() as probe:
                        probe.settimeout(.1)
                        self.assertNotEqual(probe.connect_ex(("127.0.0.1", receipt["loopback_port"])), 0)
        finally:
            listener.shutdown()
            listener.server_close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_runtime_faults_and_cancellation_leave_no_final_success_or_process(self):
        tools = json.loads(Path(os.environ["SHOW_REEL_HOST_TOOLS"]).read_text())
        faults = {
            "late-pageerror": "const savedSeek=globalThis.__seek;globalThis.__seek=async t=>{await savedSeek(t);if(t>=1.875)setTimeout(()=>{throw Error('late injected pageerror')},50);};",
            "pageerror": "setTimeout(()=>{throw Error('injected pageerror')},0);",
            "image": "const im=new Image();im.src='missing.png';document.body.append(im);",
            "font": "new FontFace('Missing','url(missing.ttf)').load();",
            "external": "fetch('https://example.invalid/private').catch(()=>{});",
            "timeout": "globalThis.__ready=false;setInterval(()=>{globalThis.__ready=false},1);",
        }
        for name, injected in faults.items():
            with self.subTest(fault=name), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                init = subprocess.run([sys.executable, str(KIT / "scripts/new_project.py"), str(base / "project"), "--font", os.environ["SHOW_REEL_FONT"], "--duration", "2", "--fps", "8", "--width", "320", "--height", "180"], capture_output=True, text=True, timeout=20)
                self.assertEqual(init.returncode, 0, init.stderr)
                scene = base / "project/reel/scene.js"
                scene.write_text(scene.read_text() + "\n" + injected)
                out = base / "fault.mp4"
                command = [tools["binaries"]["node"], str(KIT / "scripts/render.mjs"), "--dir", str(base / "project/reel"), "--out", str(out), "--browser", tools["chrome"], "--dependency-root", tools["available_puppeteer_core"]["root"], "--ffmpeg", tools["binaries"]["ffmpeg"], "--ffprobe", tools["binaries"]["ffprobe"], "--timeout", "2"]
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(out.exists())
                self.assertFalse(Path(str(out) + ".receipt.json").exists())
                receipt = json.loads(Path(str(out) + ".failure.json").read_text())
                self.assertTrue(all(receipt["cleanup"].values()))
                pid = receipt["browser_pid"]
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
                with socket.socket() as probe:
                    probe.settimeout(.1)
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", receipt["loopback_port"])), 0)
                if name == "timeout":
                    cancel_out = base / "cancel.mp4"
                    command[command.index(str(out))] = str(cancel_out)
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    time.sleep(.7)
                    process.send_signal(signal.SIGTERM)
                    stdout, stderr = process.communicate(timeout=15)
                    self.assertNotEqual(process.returncode, 0, stdout + stderr)
                    cancelled = json.loads(Path(str(cancel_out) + ".failure.json").read_text())
                    self.assertIn("cancelled", cancelled["error"])
                    with self.assertRaises(ProcessLookupError):
                        os.kill(cancelled["browser_pid"], 0)

    def test_exact_gallery_frames_and_shuffled_seeks(self):
        tools = json.loads(Path(os.environ["SHOW_REEL_HOST_TOOLS"]).read_text())
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            init = subprocess.run([sys.executable, str(KIT / "scripts/new_project.py"), str(base / "project"), "--font", os.environ["SHOW_REEL_FONT"], "--duration", "2", "--fps", "8", "--width", "320", "--height", "180"], capture_output=True, text=True, timeout=20)
            self.assertEqual(init.returncode, 0, init.stderr)
            command = [tools["binaries"]["node"], str(KIT / "scripts/render.mjs"), "--dir", str(base / "project/reel"), "--browser", tools["chrome"], "--dependency-root", tools["available_puppeteer_core"]["root"], "--ffmpeg", tools["binaries"]["ffmpeg"], "--ffprobe", tools["binaries"]["ffprobe"]]
            out = base / "gallery.mp4"
            result = subprocess.run(command + ["--out", str(out)], capture_output=True, text=True, timeout=100)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.is_file(), "no actual rendered artifact")
            receipt = json.loads(Path(str(out) + ".receipt.json").read_text())
            self.assertEqual(receipt["decoded_frames"], 16)
            self.assertEqual(receipt["color"], {"range": "tv", "space": "bt709", "transfer": "bt709", "primaries": "bt709"})
            self.assertTrue(all(receipt["cleanup"].values()))
            stills = base / "seeks"
            result = subprocess.run(command + ["--out", str(stills), "--stills", ".2,.7,1.2,1.7,.7,.2"], capture_output=True, text=True, timeout=100)
            self.assertEqual(result.returncode, 0, result.stderr)
            hashes = [hashlib.sha256((stills / f"{i:06}.png").read_bytes()).hexdigest() for i in range(6)]
            self.assertEqual(hashes[0], hashes[5])
            self.assertEqual(hashes[1], hashes[4])
            self.assertEqual(len(set(hashes)), 4)
            config_path = base / "project/reel/project.json"
            config = json.loads(config_path.read_text())
            config["texts"] = [{"text": chr(0x10ffff), "lang": "en", "dir": "ltr"}]
            config_path.write_text(json.dumps(config))
            (base / "project/reel/project.js").write_text("globalThis.PROJECT=" + json.dumps(config) + ";")
            result = subprocess.run(command + ["--out", str(base / "missing-glyph.mp4")], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0, "missing glyph silently used browser fallback")
            self.assertIn("glyph", result.stderr.lower())
            self.assertFalse((base / "missing-glyph.mp4").exists())


if __name__ == "__main__":
    unittest.main()
