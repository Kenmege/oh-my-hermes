"""How bare `omh` reaches the OMH TUI: availability, environment, and the Node shim."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from omh.tui import launch


class _Tty:
    def isatty(self) -> bool:
        return True


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


class NodeAvailabilityTests(unittest.TestCase):
    def test_an_executable_hermes_node_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            node = _executable(Path(tmp) / "node", "#!/bin/sh\n")
            self.assertTrue(launch.node_available({"HERMES_NODE": str(node), "PATH": ""}, Path(tmp)))

    @unittest.skipIf(os.name == "nt", "shutil.which needs a PATHEXT extension on Windows, where the TUI is never attempted")
    def test_node_on_path_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            _executable(Path(tmp) / "bin" / "node", "#!/bin/sh\n")
            self.assertTrue(launch.node_available({"PATH": str(Path(tmp) / "bin")}, Path(tmp) / "home"))

    def test_hermes_own_tools_node_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            _executable(home / "tools" / "node-26.7.0-darwin-arm64" / "bin" / "node", "#!/bin/sh\n")
            self.assertTrue(launch.node_available({"PATH": ""}, home))

    def test_no_node_anywhere_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(launch.node_available({"PATH": "", "HERMES_NODE": str(Path(tmp) / "missing")}, Path(tmp)))


class AvailabilityTests(unittest.TestCase):
    def test_unsupported_terminal_means_no_attempt(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(launch.omh_tui_available(_Tty(), _Tty(), {"TERM": "dumb"}, Path(tmp)))

    def test_missing_node_means_no_attempt(self) -> None:
        with TemporaryDirectory() as tmp, patch.object(launch, "terminal_supported", return_value=True):
            self.assertFalse(launch.omh_tui_available(_Tty(), _Tty(), {"PATH": ""}, Path(tmp)))

    def test_supported_terminal_with_node_and_the_shim_present_attempts(self) -> None:
        with TemporaryDirectory() as tmp, patch.object(launch, "terminal_supported", return_value=True):
            node = _executable(Path(tmp) / "node", "#!/bin/sh\n")
            self.assertTrue(launch.omh_tui_available(_Tty(), _Tty(), {"HERMES_NODE": str(node)}, Path(tmp)))

    def test_the_shim_ships_where_hermes_looks_for_it(self) -> None:
        self.assertTrue(launch.FRONTEND_ENTRY.is_file())
        self.assertEqual(launch.FRONTEND_ENTRY, launch.FRONTEND_DIR / "dist" / "entry.js")


class EnvironmentTests(unittest.TestCase):
    def test_the_environment_adds_four_names_and_keeps_the_rest(self) -> None:
        env = launch.omh_tui_environment(
            {"PATH": "/bin", "HERMES_HOME": "/h"}, python="/py", omh_home=Path("/o"), fallback_sentinel=Path("/t/f")
        )
        self.assertEqual(env["HERMES_TUI_DIR"], str(launch.FRONTEND_DIR))
        self.assertEqual(env["OMH_TUI_PYTHON"], "/py")
        self.assertEqual(env["OMH_HOME"], str(Path("/o")))
        self.assertEqual(env["OMH_TUI_FALLBACK_SENTINEL"], str(Path("/t/f")))
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["HERMES_HOME"], "/h")

    def test_the_fallback_code_is_none_hermes_or_a_session_uses(self) -> None:
        self.assertNotIn(launch.FALLBACK_EXIT, (0, 1, 2, 42, 130))


class FallbackSentinelTests(unittest.TestCase):
    """Exit 75 is a fallback request only together with the file `omh` named."""

    def test_the_sentinel_is_a_fresh_path_in_a_directory_removed_afterwards(self) -> None:
        with launch.fallback_sentinel() as sentinel:
            self.assertFalse(sentinel.exists())
            self.assertTrue(sentinel.parent.is_dir())
            self.assertEqual(list(sentinel.parent.iterdir()), [])
            sentinel.touch()
        self.assertFalse(sentinel.parent.exists())

    def test_two_launches_never_share_a_sentinel(self) -> None:
        with launch.fallback_sentinel() as first, launch.fallback_sentinel() as second:
            self.assertNotEqual(first.parent, second.parent)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits; the OMH TUI is never attempted on Windows")
    def test_the_sentinel_directory_is_private(self) -> None:
        with launch.fallback_sentinel() as sentinel:
            self.assertEqual(sentinel.parent.stat().st_mode & 0o777, 0o700)

    def test_the_directory_is_removed_when_the_launch_raises(self) -> None:
        with self.assertRaises(KeyboardInterrupt), launch.fallback_sentinel() as sentinel:
            sentinel.touch()
            raise KeyboardInterrupt
        self.assertFalse(sentinel.parent.exists())

    @unittest.skipIf(os.name == "nt", "SIGHUP is POSIX-only, and the OMH TUI is never attempted on Windows")
    def test_exit_on_signals_turns_the_pair_into_system_exit_and_puts_the_handlers_back(self) -> None:
        before = {name: signal.getsignal(getattr(signal, name)) for name in launch.EXIT_SIGNALS}
        with launch.exit_on_signals():
            for name in launch.EXIT_SIGNALS:
                with self.subTest(signal=name), self.assertRaises(SystemExit) as raised:
                    os.kill(os.getpid(), getattr(signal, name))
                    time.sleep(1)  # the handler raises before this returns
                self.assertEqual(raised.exception.code, 128 + getattr(signal, name))
        self.assertEqual({name: signal.getsignal(getattr(signal, name)) for name in launch.EXIT_SIGNALS}, before)

    def test_only_the_fallback_code_with_the_file_is_a_request(self) -> None:
        with launch.fallback_sentinel() as sentinel:
            self.assertFalse(launch.fallback_requested(launch.FALLBACK_EXIT, sentinel))
            sentinel.touch()
            self.assertTrue(launch.fallback_requested(launch.FALLBACK_EXIT, sentinel))
            for code in (0, 1, 2, 42, 130):
                with self.subTest(code=code):
                    self.assertFalse(launch.fallback_requested(code, sentinel))

    def test_request_fallback_creates_the_named_file_and_returns_the_code(self) -> None:
        with launch.fallback_sentinel() as sentinel:
            code = launch.request_fallback({launch.FALLBACK_SENTINEL_ENV: str(sentinel)})
            self.assertEqual(code, launch.FALLBACK_EXIT)
            self.assertTrue(sentinel.is_file())
            self.assertEqual(launch.request_fallback({launch.FALLBACK_SENTINEL_ENV: str(sentinel)}), launch.FALLBACK_EXIT)

    def test_request_fallback_without_a_name_or_a_place_still_returns_the_code(self) -> None:
        self.assertEqual(launch.request_fallback({}), launch.FALLBACK_EXIT)
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "gone" / "fallback"
            self.assertEqual(launch.request_fallback({launch.FALLBACK_SENTINEL_ENV: str(missing)}), launch.FALLBACK_EXIT)
            self.assertFalse(missing.exists())


@unittest.skipIf(shutil.which("node") is None or os.name == "nt", "needs node and a POSIX shell")
class ShimTests(unittest.TestCase):
    """Run the shipped `entry.js` under Node with a fake interpreter that records what it got."""

    def _run_shim(self, tmp: Path, *, exit_code: int = 0, extra_env: dict[str, str] | None = None):
        record = tmp / "record.json"
        fake_python = _executable(
            tmp / "fake-python",
            "#!/bin/sh\n"
            f'"{sys.executable}" -c \'import json, os, sys; '
            f'json.dump({{"argv": sys.argv[1:], "env": dict(os.environ)}}, open("{record}", "w"))\' "$@"\n'
            f"exit {exit_code}\n",
        )
        env = {
            "PATH": os.environ.get("PATH", ""),
            "OMH_TUI_PYTHON": str(fake_python),
            "PYTHONPATH": "/hermes/root:/hermes/runtime/site-packages",
            "NODE_OPTIONS": "--max-old-space-size=8192",
            "HERMES_PYTHON": "/hermes/python",
            **(extra_env or {}),
        }
        result = subprocess.run(
            ["node", str(launch.FRONTEND_ENTRY)], env=env, capture_output=True, text=True, timeout=30
        )
        recorded = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
        return result, recorded

    def test_the_shim_starts_python_with_dash_e_dash_p_and_moves_the_launcher_path_aside(self) -> None:
        with TemporaryDirectory() as tmp:
            result, recorded = self._run_shim(Path(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        # -E drops PYTHONPATH/PYTHONHOME, -P the working directory; -I (and -s) would drop the
        # user site-packages a `pip install --user` OMH lives in.
        self.assertEqual(recorded["argv"][:3], ["-E", "-P", "-c"])
        self.assertIn("from omh.tui.app import main", recorded["argv"][3])
        self.assertFalse({"-I", "-s"} & set(recorded["argv"]))
        self.assertNotIn("PYTHONPATH", recorded["env"])
        self.assertNotIn("NODE_OPTIONS", recorded["env"])
        self.assertEqual(recorded["env"]["OMH_TUI_GATEWAY_PYTHONPATH"], "/hermes/root:/hermes/runtime/site-packages")
        self.assertEqual(recorded["env"]["HERMES_PYTHON"], "/hermes/python")

    def test_the_tui_exit_code_reaches_the_launcher_unchanged(self) -> None:
        for code in (0, 3, launch.FALLBACK_EXIT, 130):
            with self.subTest(code=code), TemporaryDirectory() as tmp:
                sentinel = Path(tmp) / "fallback"
                result, _recorded = self._run_shim(
                    Path(tmp), exit_code=code, extra_env={launch.FALLBACK_SENTINEL_ENV: str(sentinel)}
                )
                self.assertEqual(result.returncode, code)
                # The TUI's own 75 carries its own sentinel; the shim passes the code on and creates nothing.
                self.assertFalse(sentinel.exists())

    def test_an_interpreter_that_cannot_import_omh_asks_for_the_fallback(self) -> None:
        # `-S` leaves site-packages, where this OMH is installed, off the path: `import omh` fails.
        with TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "fallback"
            no_site = _executable(Path(tmp) / "python", f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n')
            result = subprocess.run(
                ["node", str(launch.FRONTEND_ENTRY)],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "OMH_TUI_PYTHON": str(no_site),
                    launch.FALLBACK_SENTINEL_ENV: str(sentinel),
                },
                capture_output=True, text=True, timeout=30, cwd=tmp,
            )
            self.assertTrue(sentinel.is_file())
        self.assertEqual(result.returncode, launch.FALLBACK_EXIT, result.stderr)
        self.assertIn("could not import", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_without_an_interpreter_the_shim_asks_for_the_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "fallback"
            result = subprocess.run(
                ["node", str(launch.FRONTEND_ENTRY)],
                env={"PATH": os.environ.get("PATH", ""), launch.FALLBACK_SENTINEL_ENV: str(sentinel)},
                capture_output=True, text=True, timeout=30,
            )
            self.assertTrue(sentinel.is_file())
        self.assertEqual(result.returncode, launch.FALLBACK_EXIT)
        self.assertIn("OMH_TUI_PYTHON", result.stderr)

    def test_an_interpreter_that_cannot_start_asks_for_the_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "fallback"
            result = subprocess.run(
                ["node", str(launch.FRONTEND_ENTRY)],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "OMH_TUI_PYTHON": str(Path(tmp) / "missing-python"),
                    launch.FALLBACK_SENTINEL_ENV: str(sentinel),
                },
                capture_output=True, text=True, timeout=30,
            )
            self.assertTrue(sentinel.is_file())
        self.assertEqual(result.returncode, launch.FALLBACK_EXIT)

    def test_a_kill_that_fails_after_the_child_started_asks_for_no_fallback(self) -> None:
        # Node emits 'error' for a failed kill too. The preload makes every kill fail that way; the SIGTERM sent to
        # the shim alone then reaches only the failing kill, and the child goes on to exit 0 on its own.
        with TemporaryDirectory() as tmp:
            sentinel, started = Path(tmp) / "fallback", Path(tmp) / "started"
            preload = Path(tmp) / "failing-kill.js"
            preload.write_text(
                "const cp = require('node:child_process')\n"
                "const spawn = cp.spawn\n"
                "cp.spawn = function (...args) {\n"
                "  const child = spawn.apply(this, args)\n"
                "  child.kill = () => { process.nextTick(() => child.emit('error', new Error('kill EPERM'))); return false }\n"
                "  return child\n"
                "}\n",
                encoding="utf-8",
            )
            fake_python = _executable(Path(tmp) / "fake-python", f'#!/bin/sh\ntouch "{started}"\nsleep 1\nexit 0\n')
            shim = subprocess.Popen(
                ["node", "-r", str(preload), str(launch.FRONTEND_ENTRY)],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "OMH_TUI_PYTHON": str(fake_python),
                    launch.FALLBACK_SENTINEL_ENV: str(sentinel),
                },
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 20
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                shim.send_signal(signal.SIGTERM)
                _out, err = shim.communicate(timeout=30)
            finally:
                if shim.poll() is None:  # a failed assertion or a timeout above must not leave node running
                    shim.kill()
                    shim.communicate(timeout=10)
            self.assertFalse(sentinel.exists())
        self.assertEqual(shim.returncode, 0, err)

    def test_a_fallback_without_a_sentinel_name_still_exits_75(self) -> None:
        # `python -m omh.tui` by hand, or a launcher that set no name: the code is the same, no file is made.
        result = subprocess.run(
            ["node", str(launch.FRONTEND_ENTRY)], env={"PATH": os.environ.get("PATH", "")},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, launch.FALLBACK_EXIT)


if __name__ == "__main__":
    unittest.main()
