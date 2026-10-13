"""The plugin bundle must load well inside Hermes' per-plugin load deadline.

Hermes (0.21.5 and later) imports a plugin and calls its ``register()`` on a
worker thread, and waits ``plugins.load_timeout_seconds`` for it -- 10 seconds
unless the operator changed it. Past that the plugin is dropped: Hermes
records ``load timed out after <N>s``, moves on, and refuses every
registration the abandoned worker still attempts. For OMH that is every tool
and hook missing from the plugin loader's registry, and the only signs are a
WARNING in Hermes' log and the error in its ``/plugins`` listing.

The bundle is over a hundred modules that nothing had ever timed. This file
times it the way Hermes loads it -- a fresh interpreter, the bundle under the
``hermes_plugins.<name>`` package name, ``register()`` against a host context
-- with nothing cached: the copy has no ``__pycache__``, the interpreter may
not write one, and ``PYTHONPYCACHEPREFIX`` points at an empty directory, so the
standard library and (where the host can import it) the ``omh`` package
compile from source too. That is colder than any real host, on purpose.

Not observed here, and stated rather than assumed:

- Windows. Cold load on Windows is on the audit plan's blocked-unless-observed
  list; no Windows number existed when this was written. The Windows CI shards
  run this file, so a green Windows run is the first observation that the load
  fits under the ceiling there, and a red one is the measurement that says by
  how much it does not.
- The host's side of the span. The context below is a stub, so the time Hermes
  spends inside its own ``register_tool`` / ``register_hook`` is not in the
  number. Replayed seven times against Hermes' real loader in an isolated
  home, the whole deadline-covered span was 0.23-0.31 s on the machine named
  beside the ceiling.
- The memory-provider loader. Hermes can load the bundle a second time there,
  and that load is not under this deadline, so it is not what this file guards.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package
from _standalone_bundle import bundle_dir

load_local_package()
from omh.config_adapter import plugin_load_timeout_setting
from omh.install.plugin_load_deadline import (
    ERROR_LOG_ABSENT,
    ERROR_LOG_UNREADABLE,
    LOAD_TIMEOUT_RECORDED,
    NO_LOAD_TIMEOUT_RECORDED,
    load_timeout_log_marker,
    read_recorded_load_timeouts,
)
from omh.maintenance import doctor as doctor_module
from omh.maintenance.doctor import run_doctor
from omh.paths import resolve_paths
from omh.plugin_bundle.omh.metadata import PROVIDED_HOOKS, PROVIDED_TOOLS
from omh.plugin_pack import PLUGIN_NAME

# Hermes' `_LOAD_TIMEOUT_SECS`: what a plugin gets when the operator set nothing.
HERMES_DEFAULT_LOAD_TIMEOUT_SECONDS = 10.0

# A ceiling with headroom, not a ratchet: do not lower it to track a fast
# machine. Half of Hermes' default is the line, so a bundle that passes still
# has 2x left on a host twice as slow as the runner that passed it.
#
# Measured 2026-10-13, 14-core macOS, Python 3.14, load average 12-26 from
# other work on the machine, nothing cached, seven runs each:
# 0.40 / 0.42 / 0.45 s (min / median / max) on a host without the `omh`
# package and 0.58 / 0.63 / 0.79 s on one that can import it. The ceiling is
# therefore more than 6x the slowest contended run seen and about 8x the
# heavier median. That margin is for what was not measured: CI shards run two
# workers per runner, and no Windows number exists yet.
COLD_LOAD_CEILING_SECONDS = HERMES_DEFAULT_LOAD_TIMEOUT_SECONDS / 2

# Scheduler noise only ever adds time, so the fastest of a few runs is the
# bundle's own cost. A real regression is slow every time; a starved runner
# has to starve all of these in a row to fail the gate.
ATTEMPTS = 3

_RESULT_PREFIX = "OMH_COLD_LOAD="

# Mirrors `_load_directory_module` + `register(ctx)` in Hermes'
# hermes_cli/plugins_loader.py -- the span `run_with_load_deadline` covers.
# Self-contained: the child may have no `omh` package and no test helpers.
_HOST_LOAD_SCRIPT = r"""
import importlib.util
import json
import sys
import time
import types
from pathlib import Path


class HostContext:
    def __init__(self):
        self.tools = []
        self.hooks = []

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools.append(name)

    def register_hook(self, name, handler):
        self.hooks.append(name)

    def register_memory_provider(self, provider):
        return None

    def get_config(self, key, default=None):
        return default


plugin_dir = Path(sys.argv[1])
before = set(sys.modules)
cpu_start = time.process_time()
start = time.perf_counter()
namespace = types.ModuleType("hermes_plugins")
namespace.__path__ = []
namespace.__package__ = "hermes_plugins"
sys.modules["hermes_plugins"] = namespace
name = "hermes_plugins.omh"
spec = importlib.util.spec_from_file_location(
    name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)]
)
module = importlib.util.module_from_spec(spec)
module.__package__ = name
module.__path__ = [str(plugin_dir)]
sys.modules[name] = module
spec.loader.exec_module(module)
imported = time.perf_counter()
context = HostContext()
module.register(context)
done = time.perf_counter()
cpu_done = time.process_time()
loaded = set(sys.modules) - before
print("OMH_COLD_LOAD=" + json.dumps({
    "import_seconds": round(imported - start, 4),
    "register_seconds": round(done - imported, 4),
    "wall_seconds": round(done - start, 4),
    "cpu_seconds": round(cpu_done - cpu_start, 4),
    "tools": sorted(context.tools),
    "hooks": sorted(context.hooks),
    "bundle_modules": sum(1 for item in loaded if item.startswith(name + ".")),
    "omh_package_modules": sum(1 for item in loaded if item == "omh" or item.startswith("omh.")),
}, sort_keys=True))
"""


def cold_bundle_copy(root: Path) -> Path:
    """The bundle as `omh setup` leaves it in a Hermes home, minus any bytecode."""
    target = root / "plugins" / "omh"
    shutil.copytree(bundle_dir(), target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def measure_cold_load(plugin_dir: Path, pycache_prefix: Path, *, host_can_import_omh: bool) -> dict[str, object]:
    """One cold import + `register()` in a fresh interpreter; the child's own timing.

    `-P` keeps the working directory -- and the checkout's top-level `omh/`
    shim -- off `sys.path`. `-S` is the host most installs produce: Hermes'
    interpreter with no `omh` package in it. Without `-S` the child imports
    whatever `omh` this interpreter has installed, which is the heavier load.
    """
    command = [sys.executable, "-P", "-B"]
    if not host_can_import_omh:
        command.append("-S")
    command += ["-c", _HOST_LOAD_SCRIPT, str(plugin_dir)]
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONPYCACHEPREFIX"] = str(pycache_prefix)
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=300, env=env, cwd=str(pycache_prefix.parent)
    )
    if result.returncode != 0:
        raise AssertionError(f"the bundle did not load in a fresh interpreter: {result.stderr[-2000:]}")
    for line in result.stdout.splitlines():
        if line.startswith(_RESULT_PREFIX):
            return json.loads(line.removeprefix(_RESULT_PREFIX))
    raise AssertionError(f"the load child printed no measurement: {result.stdout[-500:]!r}")


class ColdLoadCeilingTests(unittest.TestCase):
    def test_cold_import_and_register_stay_under_the_ceiling(self) -> None:
        for host, host_can_import_omh in (
            ("a host without the omh package", False),
            ("a host with site-packages, where an installed omh is importable", True),
        ):
            with self.subTest(host=host), TemporaryDirectory() as tmp:
                plugin_dir = cold_bundle_copy(Path(tmp))
                pycache_prefix = Path(tmp) / "pycache"
                pycache_prefix.mkdir()
                attempts: list[dict[str, object]] = []
                for _ in range(ATTEMPTS):
                    attempts.append(
                        measure_cold_load(plugin_dir, pycache_prefix, host_can_import_omh=host_can_import_omh)
                    )
                    if float(attempts[-1]["wall_seconds"]) <= COLD_LOAD_CEILING_SECONDS:
                        break
                # A load that registered nothing is fast and proves nothing, so
                # the timed load has to be the complete one first.
                self.assertEqual(attempts[-1]["tools"], sorted(PROVIDED_TOOLS))
                self.assertEqual(attempts[-1]["hooks"], sorted(PROVIDED_HOOKS))
                self.assertEqual(list(pycache_prefix.iterdir()), [], "the cold load wrote bytecode it could reuse")
                best = min(float(attempt["wall_seconds"]) for attempt in attempts)
                runs = [
                    {key: value for key, value in attempt.items() if key not in {"tools", "hooks"}}
                    for attempt in attempts
                ]
                self.assertLessEqual(
                    best,
                    COLD_LOAD_CEILING_SECONDS,
                    f"cold import + register() took {best:.2f}s at best over {len(attempts)} run(s) on {host}; "
                    f"the ceiling is {COLD_LOAD_CEILING_SECONDS:g}s and Hermes skips the plugin at "
                    f"{HERMES_DEFAULT_LOAD_TIMEOUT_SECONDS:g}s by default. Read the runs before the bundle: "
                    "`wall_seconds` far above `cpu_seconds` in every run is a starved or I/O-bound runner, "
                    "while the two rising together is work the bundle now does at load -- find it with "
                    "`python -X importtime` and move it out of import + register(). "
                    f"Runs: {json.dumps(runs, sort_keys=True)}",
                )

    def test_the_timed_span_covers_both_import_and_register(self) -> None:
        """The gated number moves when either half slows down.

        A stand-in plugin whose only cost is two planted sleeps -- one at
        module level, one inside `register` -- so each half of the span Hermes'
        deadline covers has a floor nothing else can supply. Had the timer
        started after the import, or stopped before `register`, the matching
        half would read near zero.
        """
        planted = 0.2
        # A sleep is a lower bound, but a coarse clock can report a hair less.
        floor = planted * 0.75
        with TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugins" / "omh"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "__init__.py").write_text(
                "import time\n\n"
                f"time.sleep({planted})\n\n\n"
                "def register(ctx):\n"
                f"    time.sleep({planted})\n"
                "    ctx.register_tool('planted', 'omh', {}, None)\n",
                encoding="utf-8",
                newline="\n",
            )
            pycache_prefix = Path(tmp) / "pycache"
            pycache_prefix.mkdir()
            slowed = measure_cold_load(plugin_dir, pycache_prefix, host_can_import_omh=False)
        self.assertEqual(slowed["tools"], ["planted"])
        self.assertGreaterEqual(float(slowed["import_seconds"]), floor, slowed)
        self.assertGreaterEqual(float(slowed["register_seconds"]), floor, slowed)
        self.assertGreaterEqual(float(slowed["wall_seconds"]), 2 * floor, slowed)


# The lines Hermes' loader wrote for a timed-out load, copied from an isolated
# replay of its real `PluginManager` over this bundle with
# `plugins.load_timeout_seconds: 0.05`. Verbatim on purpose: the reader matches
# on wording, and a paraphrase here would pin the paraphrase.
RECORDED_TIMEOUT_LINE = (
    "2026-10-13 19:24:45,527 WARNING hermes_cli.plugins: Failed to load plugin 'omh': "
    "load timed out after 0.05s (import + register() never returned)"
)
REFUSED_LATE_REGISTRATION_LINE = (
    "2026-10-13 19:24:45,570 WARNING hermes_cli.plugins: Plugin 'omh' called "
    "register_memory_provider() after its load timed out; ignored"
)
# Lines that are not a record of this plugin overrunning the deadline: the
# consequence of one, another plugin's timeout, and this plugin failing some
# other way. None may be counted.
UNRELATED_LINES = (
    REFUSED_LATE_REGISTRATION_LINE,
    "2026-10-13 19:24:23,670 WARNING hermes_cli.plugins: Failed to load plugin 'slowplug': "
    "load timed out after 1s (import + register() never returned)",
    "2026-10-13 19:24:23,670 WARNING hermes_cli.plugins: Failed to load plugin 'omh-extras': "
    "load timed out after 10s (import + register() never returned)",
    "2026-10-13 19:24:23,670 WARNING hermes_cli.plugins: Failed to load plugin 'omh': No module named 'yaml'",
    "2026-10-13 19:24:23,670 WARNING agent.loop: the user said the omh plugin load timed out after lunch",
)


class RecordedLoadTimeoutReaderTests(unittest.TestCase):
    def _home(self, root: Path, lines: tuple[str, ...] | None) -> Path:
        home = root / ".hermes"
        (home / "logs").mkdir(parents=True)
        if lines is not None:
            (home / "logs" / "errors.log").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return home

    def test_the_marker_is_the_text_hermes_writes(self) -> None:
        self.assertIn(load_timeout_log_marker("omh"), RECORDED_TIMEOUT_LINE)

    def test_a_recorded_timeout_is_reported_with_its_own_stamp_and_deadline(self) -> None:
        later = RECORDED_TIMEOUT_LINE.replace("19:24:45,527", "21:02:11,004").replace("0.05s", "10s")
        with TemporaryDirectory() as tmp:
            home = self._home(Path(tmp), (RECORDED_TIMEOUT_LINE, *UNRELATED_LINES, later))
            record = read_recorded_load_timeouts(home, "omh")
        self.assertTrue(record["observed"])
        self.assertEqual(record["reason"], LOAD_TIMEOUT_RECORDED)
        self.assertEqual(record["count"], 2)
        self.assertEqual(record["latest_at"], "2026-10-13 21:02:11,004")
        self.assertEqual(record["latest_deadline"], "10s")

    def test_no_unrelated_line_counts_as_a_record(self) -> None:
        for line in UNRELATED_LINES:
            with self.subTest(line=line), TemporaryDirectory() as tmp:
                record = read_recorded_load_timeouts(self._home(Path(tmp), (line,)), "omh")
                self.assertTrue(record["observed"])
                self.assertEqual(record["reason"], NO_LOAD_TIMEOUT_RECORDED)
                self.assertEqual(record["count"], 0)

    def test_a_missing_log_is_not_observed_rather_than_clean(self) -> None:
        with TemporaryDirectory() as tmp:
            record = read_recorded_load_timeouts(self._home(Path(tmp), None), "omh")
        self.assertFalse(record["observed"])
        self.assertEqual(record["reason"], ERROR_LOG_ABSENT)

    def test_a_log_that_cannot_be_read_is_not_observed_and_names_why(self) -> None:
        with TemporaryDirectory() as tmp:
            home = self._home(Path(tmp), None)
            (home / "logs" / "errors.log").mkdir()
            record = read_recorded_load_timeouts(home, "omh")
        self.assertFalse(record["observed"])
        self.assertEqual(record["reason"], ERROR_LOG_UNREADABLE)
        self.assertTrue(record["detail"])


# Each expectation is what Hermes itself loads from the same text, measured
# with the PyYAML in Hermes' venv (`yaml.safe_load(text)["plugins"]
# ["load_timeout_seconds"]`), not reasoned about here. Hermes interprets the
# value afterwards -- `0` is no deadline, a negative or non-numeric value
# falls back to its default, anything above 600 is clamped -- and the reader
# reports the raw text so that judgement stays with the host.
READ_LOAD_TIMEOUT_SETTINGS: tuple[tuple[str, str], ...] = (
    ("plugins:\n  enabled:\n    - omh\n  load_timeout_seconds: 30\n", "30"),
    ("plugins:\n  load_timeout_seconds: 2.5\n  enabled:\n    - omh\n", "2.5"),
    ("plugins:\n  load_timeout_seconds: 0\n", "0"),
    ("plugins:  # third-party\n  load_timeout_seconds: 45  # slow disk\n", "45"),
    ("plugins:\n  load_timeout_seconds: '15'\n", "15"),
    ("plugins:\n  # a note at column two\n# a note at column zero\n  load_timeout_seconds: 20\n", "20"),
    ("plugins:\n  load_timeout_seconds: fast\n", "fast"),
    ("plugins:\n  load_timeout_seconds: -1\n", "-1"),
)

# Texts the reader answers "" for. Hermes loads no value from the first six;
# from the last two it loads 30, through forms this reader does not follow.
# That is why "" is "not read" and a caller may never print it as "unset".
UNREAD_LOAD_TIMEOUT_SETTINGS: tuple[str, ...] = (
    "",
    "plugins:\n  enabled:\n    - omh\n",
    "plugins:\n  load_timeout_seconds:\n",
    "plugins.load_timeout_seconds: 30\n",
    "plugins:\n  entries:\n    omh:\n      load_timeout_seconds: 30\n",
    "gateway:\n  load_timeout_seconds: 30\nplugins:\n  enabled: []\n",
    "plugins: {enabled: [omh], load_timeout_seconds: 30}\n",
    "plugins: &p\n  load_timeout_seconds: 30\n",
)


class LoadTimeoutSettingReaderTests(unittest.TestCase):
    def test_a_block_form_value_is_read_as_written(self) -> None:
        for text, expected in READ_LOAD_TIMEOUT_SETTINGS:
            with self.subTest(text=text):
                self.assertEqual(plugin_load_timeout_setting(text), expected)

    def test_everything_else_is_not_read(self) -> None:
        for text in UNREAD_LOAD_TIMEOUT_SETTINGS:
            with self.subTest(text=text):
                self.assertEqual(plugin_load_timeout_setting(text), "")


class DoctorLoadDeadlineCheckTests(unittest.TestCase):
    """Three answers that must not collapse into one green row."""

    def _paths(self, root: Path, config_text: str, log_lines: tuple[str, ...] | None = None):
        hermes = root / ".hermes"
        (hermes / "plugins" / PLUGIN_NAME).mkdir(parents=True)
        (hermes / "config.yaml").write_text(config_text, encoding="utf-8", newline="\n")
        if log_lines is not None:
            (hermes / "logs").mkdir()
            (hermes / "logs" / "errors.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8", newline="\n")
        return resolve_paths(root / ".omh", hermes)

    def _check(self, root: Path, config_text: str, log_lines: tuple[str, ...] | None = None):
        paths = self._paths(root, config_text, log_lines)
        return doctor_module._plugin_load_deadline_check(paths, config_text)

    def test_no_log_is_not_observed_and_still_reports_the_setting(self) -> None:
        with TemporaryDirectory() as tmp:
            check = self._check(Path(tmp), "plugins:\n  load_timeout_seconds: 30\n")
        self.assertEqual((check.ok, check.severity, check.observed), (True, "ok", False))
        self.assertIn("plugins.load_timeout_seconds=30", check.message)
        self.assertIn("not_observed", check.message)
        self.assertIn(ERROR_LOG_ABSENT, check.message)
        self.assertIn("is not established", check.message)

    def test_a_log_without_a_record_does_not_claim_the_plugin_loaded(self) -> None:
        with TemporaryDirectory() as tmp:
            check = self._check(Path(tmp), "plugins:\n  enabled:\n    - omh\n", UNRELATED_LINES)
        self.assertEqual((check.ok, check.severity, check.observed), (True, "ok", True))
        self.assertIn("no plugins.load_timeout_seconds read", check.message)
        self.assertIn("no load-timeout record", check.message)
        self.assertIn("not evidence that the plugin loaded", check.message)

    def test_a_recorded_skip_warns_with_the_record_and_never_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            check = self._check(
                Path(tmp),
                "plugins:\n  load_timeout_seconds: 0.05\n",
                (RECORDED_TIMEOUT_LINE, REFUSED_LATE_REGISTRATION_LINE),
            )
        self.assertEqual((check.ok, check.severity, check.observed), (True, "warning", True))
        self.assertIn("1 record(s)", check.message)
        self.assertIn("2026-10-13 19:24:45,527", check.message)
        self.assertIn("load timed out after 0.05s", check.message)
        self.assertIn("plugins.load_timeout_seconds=0.05", check.message)
        self.assertIn("record of a past load", check.message)
        self.assertIn("/plugins", check.next_action)

    def test_the_row_joins_doctor_only_beside_an_installed_bundle(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp), "plugins:\n  enabled:\n    - omh\n  load_timeout_seconds: 30\n")
            names = [check.name for check in run_doctor(paths)]
            self.assertEqual(names.count("plugin_load_deadline"), 1)
        with TemporaryDirectory() as tmp:
            hermes = Path(tmp) / ".hermes"
            hermes.mkdir()
            (hermes / "config.yaml").write_text(
                "plugins:\n  load_timeout_seconds: 30\n", encoding="utf-8", newline="\n"
            )
            names = [check.name for check in run_doctor(resolve_paths(Path(tmp) / ".omh", hermes))]
            self.assertNotIn("plugin_load_deadline", names)


if __name__ == "__main__":
    unittest.main()
