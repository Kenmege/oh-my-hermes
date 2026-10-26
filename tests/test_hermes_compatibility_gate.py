"""Contract tests for the fail-closed Hermes compatibility gate (A6).

These tests do not need an installed Hermes: they drive the helper end to end
against a fixture host that exposes the same seams the real host does
(``PluginManager``, ``tools.registry``, ``tools.tool_search``,
``hermes_cli.config_defaults``), and they inspect the workflow as text because
this repository has no YAML dependency.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

from _local_package import load_local_package

load_local_package()

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "tools" / "check_hermes_compatibility.py"
WORKFLOW = REPO / ".github" / "workflows" / "hermes-compatibility.yml"

FAKE_HERMES_CLI_INIT = '''\
__version__ = "0.22.2"
'''

FAKE_VERSION_INFO = '''\
from dataclasses import dataclass


@dataclass(frozen=True)
class Info:
    base_version: str = "0.22.2"


def get_version_info():
    return Info()
'''

FAKE_CONSTANTS = '''\
from pathlib import Path


def hermes_home_key(path=None):
    return str(Path(path).resolve())
'''

FAKE_CONFIG_DEFAULTS = '''\
DEFAULT_CONFIG = {"tools": {"tool_search": {"enabled": "auto"}}}
'''

FAKE_REGISTRY = '''\
from dataclasses import dataclass


@dataclass
class Entry:
    name: str
    toolset: str
    handler: object


class Registry:
    def __init__(self):
        self.entries = {}

    def register(self, name, toolset, schema, handler, **kwargs):
        self.entries[name] = Entry(name, toolset, handler)

    def get_entry(self, name, *, scope=None):
        return self.entries.get(name)


registry = Registry()
'''

FAKE_TOOL_SEARCH = '''\
CLIENT_SURFACE_TOOLSETS = frozenset({"catalog", "desktop_ui", "onboarding", "project"})
TOOLSET_SESSION_PLATFORMS = {"session": "desktop"}
_DIRECT_SURFACE_TOOLSETS = CLIENT_SURFACE_TOOLSETS | TOOLSET_SESSION_PLATFORMS.keys()


def is_deferrable_tool_name(name, defer_tools=None):
    entry = __import__("tools.registry", fromlist=["registry"]).registry.get_entry(name)
    return entry is not None and entry.toolset not in _DIRECT_SURFACE_TOOLSETS
'''

FAKE_PLUGINS = '''\
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _manifest(path: Path):
    data = {"name": "", "version": "", "provides_tools": [], "provides_hooks": []}
    section = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") and section:
            data[section].append(line[2:].strip().strip('\\'"'))
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('\\'"')
            if key in data and not value:
                section = key
            elif key in data:
                data[key] = value
                section = None
    return SimpleNamespace(**data)


class _Context:
    def __init__(self, manager, manifest):
        self._manager = manager
        self.manifest = manifest
        self.plugin_id = manifest.name
        self._tools = []
        self._hooks = []

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        from tools.registry import registry
        registry.register(name=name, toolset=toolset, schema=schema, handler=handler)
        self._tools.append(name)

    def register_hook(self, name, callback):
        self._manager._hooks.setdefault(name, []).append(callback)
        self._hooks.append(name)

    def get_config(self, key, default=None):
        return default


class PluginManager:
    def __init__(self, scope_key=None):
        self.scope_key = scope_key
        self._plugins = {}
        self._hooks = {}

    def discover_and_load(self, force=False):
        home = Path(self.scope_key)
        plugins = home / "plugins"
        if not plugins.is_dir():
            return
        for directory in sorted(plugins.iterdir()):
            manifest_file = directory / "plugin.yaml"
            if not directory.is_dir() or not manifest_file.is_file():
                continue
            manifest = _manifest(manifest_file)
            name = f"fixture_plugins.{manifest.name}"
            spec = importlib.util.spec_from_file_location(name, directory / "__init__.py",
                                                          submodule_search_locations=[str(directory)])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            context = _Context(self, manifest)
            module.register(context)
            self._plugins[manifest.name] = SimpleNamespace(
                manifest=manifest, enabled=True, error=None,
                tools_registered=context._tools, hooks_registered=context._hooks,
            )

    def iter_hook_callbacks(self, name):
        return tuple(self._hooks.get(name, ()))

    def invoke_hook(self, name, **kwargs):
        # Hermes deliberately swallows observer exceptions and omits None results.
        results = []
        for callback in self._hooks.get(name, ()):
            try:
                value = callback(**kwargs)
                if value is not None:
                    results.append(value)
            except Exception:
                continue
        return results
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(textwrap.dedent(text).encode("utf-8"))


def build_fake_host(root: Path) -> Path:
    _write(root / "hermes_cli" / "__init__.py", FAKE_HERMES_CLI_INIT)
    _write(root / "hermes_cli" / "version_info.py", FAKE_VERSION_INFO)
    _write(root / "hermes_cli" / "plugins.py", FAKE_PLUGINS)
    _write(root / "hermes_cli" / "config_defaults.py", FAKE_CONFIG_DEFAULTS)
    _write(root / "hermes_constants.py", FAKE_CONSTANTS)
    _write(root / "tools" / "__init__.py", "")
    _write(root / "tools" / "registry.py", FAKE_REGISTRY)
    _write(root / "tools" / "tool_search.py", FAKE_TOOL_SEARCH)
    _write(root / "agent" / "__init__.py", "")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    # The gate drops system Git config; commit the same bytes it will inspect.
    subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qm", "fake host"],
        check=True,
    )
    return root


def host_sha(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


PLUGIN_INIT = '''\
def register(ctx):
    from tools.registry import registry

    for name in ("omh_status", "omh_todo"):
        registry.register(name=name, toolset="omh", schema={"name": name}, handler=_handler)
        ctx.register_tool(name, "omh", {"name": name}, _handler)
    ctx.register_hook("pre_llm_call", lambda **kwargs: None)


def _handler(args, **kwargs):
    import json, os
    if "omh_home" in args:
        return json.dumps({"success": False, "error": "native_home_override_refused"})
    return json.dumps({"schema_version": "omh_status/v1", "privacy": "metadata_only",
                       "omh_home": os.environ["OMH_HOME"], "runtime_state_present": False,
                       "runs": [], "evidence_boundary": {"ci": "requires observed CI"}})
'''


def build_bundle(root: Path, *, omit: str | None = None) -> Path:
    bundle = root / "bundle"
    _write(bundle / "plugin.yaml", '''\
        name: omh
        kind: standalone
        version: "4.0.2"
        requires_hermes: ">=0.21.1"
        provides_tools:
          - omh_status
          - omh_todo
        provides_hooks:
          - pre_llm_call
        ''')
    body = PLUGIN_INIT if omit is None else PLUGIN_INIT.replace(
        '        ctx.register_tool(name, "omh", {"name": name}, _handler)\n', ""
    )
    if omit is not None:
        body = body.replace('for name in ("omh_status", "omh_todo"):', f'for name in ("{omit}",):')
    _write(bundle / "__init__.py", body)
    return bundle


class HermesCompatibilityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.host = build_fake_host(self.root / "host")
        self.bundle = build_bundle(self.root)
        self.home = self.root / "home"
        self.home.mkdir()

    def run_gate(self, *extra: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable, str(HELPER),
            "--hermes-root", str(self.host),
            "--hermes-python", sys.executable,
            "--require-host-sha", host_sha(self.host),
            "--omh-bundle", str(self.bundle),
            "--home", str(self.home),
            *extra,
        ]
        return subprocess.run(command, capture_output=True, text=True, timeout=120)

    def test_fixture_stays_clean_when_ambient_git_converts_crlf(self) -> None:
        system_config = self.root / "system.gitconfig"
        system_config.write_bytes(b"[core]\n\tautocrlf = true\n")
        with patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "0", "GIT_CONFIG_SYSTEM": str(system_config)}):
            host = build_fake_host(self.root / "crlf-host")
        isolated = {"PATH": os.defpath, "HOME": str(host), "GIT_CONFIG_NOSYSTEM": "1"}
        done = subprocess.run(["git", "-C", str(host), "status", "--porcelain"],
                              env=isolated, capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout, "", "the gate must inspect exactly the committed fixture bytes")
        self.assertNotIn(b"\r\n", (host / "hermes_cli" / "plugins.py").read_bytes())

    def test_real_registration_request_produces_an_observed_receipt(self) -> None:
        done = self.run_gate("--require-host-version", "0.22.2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        receipt = json.loads(done.stdout)
        self.assertTrue(receipt["observed"])
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["host"]["checkout_sha"], host_sha(self.host))
        self.assertEqual(receipt["host"]["version"]["base_version"], "0.22.2")
        self.assertEqual(receipt["probe"]["registrations"]["tools"], ["omh_status", "omh_todo"])
        self.assertEqual(receipt["probe"]["registrations"]["hooks"], ["pre_llm_call"])
        self.assertTrue(receipt["probe"]["tool"]["ok"])
        self.assertTrue(receipt["probe"]["hook"]["ok"])
        self.assertTrue(receipt["probe"]["deferral"]["ok"])

    def test_dirty_bundle_provenance_never_claims_an_immutable_source_sha(self) -> None:
        source = self.host / "src" / "plugin_bundle" / "omh"
        import shutil
        shutil.copytree(self.bundle, source)
        subprocess.run(["git", "-C", str(self.host), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.host), "-c", "user.name=a", "-c",
                        "user.email=a@b", "commit", "-qm", "source bundle"], check=True)
        # OMH source is independent of the clean, immutable host tree.
        source_repo = self.root / "omh-source"
        subprocess.run(["git", "clone", "-q", str(self.host), str(source_repo)], check=True)
        (source_repo / "src/plugin_bundle/omh/__init__.py").write_text("# changed\n", encoding="utf-8")
        done = self.run_gate("--omh-source-root", str(source_repo))
        self.assertEqual(done.returncode, 0, done.stdout)
        provenance = json.loads(done.stdout)["omh"]
        self.assertTrue(provenance["source_dirty"])
        self.assertIsNone(provenance["source_sha"])
        self.assertEqual(provenance["source_base_sha"], host_sha(source_repo))

    def test_dirty_host_source_cannot_masquerade_as_immutable_sha(self) -> None:
        with (self.host / "hermes_cli" / "plugins.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# local change\n")
        done = self.run_gate()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("dirty", done.stdout)

    def test_plugin_module_outside_host_root_is_refused(self) -> None:
        external = self.root / "external-plugins.py"
        external.write_text(FAKE_PLUGINS, encoding="utf-8")
        target = self.host / "hermes_cli" / "plugins.py"
        target.unlink()
        target.symlink_to(external)
        subprocess.run(["git", "-C", str(self.host), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.host), "-c", "user.name=a", "-c",
                        "user.email=a@b", "commit", "-qm", "external module"], check=True)
        done = self.run_gate()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("bound checkout", done.stdout)

    def test_identity_and_probe_imports_are_isolated_before_host_import(self) -> None:
        checks = '''
import os
from pathlib import Path
assert 'CI_PARENT_CANARY_SECRET' not in os.environ, 'parent canary was forwarded'
home = Path(os.environ['HERMES_HOME'])
assert (home / 'plugins' / 'omh').is_dir(), 'import before disposable preparation'
assert Path(os.environ['HOME']) == home / 'os-home', 'ambient HOME'
assert Path(os.environ['OMH_HOME']) == home / 'omh', 'ambient OMH_HOME'
'''
        _write(self.host / "hermes_cli" / "__init__.py", FAKE_HERMES_CLI_INIT + checks)
        subprocess.run(["git", "-C", str(self.host), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.host), "-c", "user.name=a", "-c",
                        "user.email=a@b", "commit", "-qm", "isolation checks"], check=True)
        with patch.dict(os.environ, {"CI_PARENT_CANARY_SECRET": "not-a-real-credential"}):
            done = self.run_gate()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertNotIn("not-a-real-credential", done.stdout + done.stderr)

    def test_socket_resolution_is_denied_in_probe_children(self) -> None:
        # Numeric loopback resolution has no external DNS traffic even before the fix.
        _write(self.bundle / "__init__.py", PLUGIN_INIT.replace(
            "lambda **kwargs: None",
            "lambda **kwargs: __import__('socket').getaddrinfo('127.0.0.1', 9)"))
        done = self.run_gate()
        self.assertEqual(done.returncode, 1, done.stdout)
        row = json.loads(done.stdout)["probe"]["hook"]["observations"][0]
        self.assertEqual(row["failure"], "PermissionError")

    def test_swallowed_hook_failure_cannot_pass(self) -> None:
        _write(self.bundle / "__init__.py", PLUGIN_INIT.replace(
            "lambda **kwargs: None", "lambda **kwargs: 1 / 0"))
        done = self.run_gate()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertFalse(json.loads(done.stdout)["probe"]["hook"]["ok"])

    def test_missing_callback_execution_cannot_pass(self) -> None:
        path = self.host / "hermes_cli" / "plugins.py"
        _write(path, FAKE_PLUGINS.replace("value = callback(**kwargs)", "value = None"))
        subprocess.run(["git", "-C", str(self.host), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.host), "-c", "user.name=a", "-c",
                        "user.email=a@b", "commit", "-qm", "no delivery"], check=True)
        done = self.run_gate()
        self.assertEqual(done.returncode, 1, done.stdout)

    def test_valid_none_callback_has_observed_completion_with_no_results(self) -> None:
        done = self.run_gate()
        self.assertEqual(done.returncode, 0, done.stdout)
        hook = json.loads(done.stdout)["probe"]["hook"]
        self.assertEqual(hook["results"], 0)
        self.assertEqual(hook.get("completed"), 1)

    def test_error_or_malformed_status_payload_fails_closed(self) -> None:
        for payload in ({"success": False, "error": "package_unavailable"}, {}, [],
                        {"schema_version": "omh_status/v1", "success": False}):
            with self.subTest(payload=payload):
                _write(self.bundle / "__init__.py", PLUGIN_INIT.split("def _handler")[0] +
                       "def _handler(args, **kwargs):\n    return " + repr(json.dumps(payload)) + "\n")
                self.home = self.root / ("denial-" + str(len(list(self.root.iterdir()))))
                done = self.run_gate()
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertFalse(json.loads(done.stdout)["probe"]["tool"]["ok"])

    def test_a_missing_declared_registration_fails_closed(self) -> None:
        self.bundle = build_bundle(self.root, omit="omh_todo")
        done = self.run_gate()
        self.assertEqual(done.returncode, 1, done.stdout)
        receipt = json.loads(done.stdout)
        self.assertTrue(receipt["observed"])
        self.assertFalse(receipt["ok"])
        self.assertTrue(any("omh_todo" in reason for reason in receipt["reasons"]), receipt["reasons"])

    def test_a_host_sha_mismatch_is_not_reported_as_observed(self) -> None:
        done = subprocess.run(
            [sys.executable, str(HELPER), "--hermes-root", str(self.host), "--hermes-python", sys.executable,
             "--require-host-sha", "0" * 40, "--omh-bundle", str(self.bundle), "--home", str(self.home)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(done.returncode, 1)
        receipt = json.loads(done.stdout)
        self.assertFalse(receipt["observed"])
        self.assertFalse(receipt["ok"])
        self.assertTrue(any("expected" in reason for reason in receipt["reasons"]))

    def test_a_relative_host_revision_is_refused(self) -> None:
        done = subprocess.run(
            [sys.executable, str(HELPER), "--hermes-root", str(self.host), "--hermes-python", sys.executable,
             "--require-host-sha", "main", "--omh-bundle", str(self.bundle), "--home", str(self.home)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("not an immutable SHA", done.stdout)

    def test_a_host_without_a_git_boundary_is_refused(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        done = subprocess.run(
            [sys.executable, str(HELPER), "--hermes-root", str(plain), "--hermes-python", sys.executable,
             "--require-host-sha", "1" * 40, "--omh-bundle", str(self.bundle), "--home", str(self.root / "h2")],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("Git boundary", done.stdout)

    def test_a_second_checkout_cannot_stand_in_for_the_bound_one(self) -> None:
        other = build_fake_host(self.root / "other-host")
        _write(other / "marker.txt", "different tree\n")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(other), "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qm", "marker"],
            check=True,
        )
        done = subprocess.run(
            [sys.executable, str(HELPER), "--hermes-root", str(other), "--hermes-python", sys.executable,
             "--require-host-sha", host_sha(self.host), "--omh-bundle", str(self.bundle), "--home", str(self.home)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(done.returncode, 1)
        self.assertFalse(json.loads(done.stdout)["observed"])

    def test_a_live_home_or_existing_store_is_never_reused(self) -> None:
        (self.home / "config.yaml").write_text("plugins:\n  enabled: [omh]\n", encoding="utf-8")
        done = self.run_gate()
        self.assertEqual(done.returncode, 1)
        self.assertIn("already holds a Hermes store", done.stdout)

    def test_the_bundle_digest_is_stable_and_ignores_bytecode(self) -> None:
        script = (
            "import json,sys;sys.path.insert(0,sys.argv[1]);"
            "import check_hermes_compatibility as g;"
            "from pathlib import Path;print(json.dumps([g.bundle_digest(Path(sys.argv[2]))]*1))"
        )
        first = subprocess.run([sys.executable, "-c", script, str(HELPER.parent), str(self.bundle)],
                               capture_output=True, text=True, check=True).stdout.strip()
        (self.bundle / "tools").mkdir()
        (self.bundle / "tools" / "__pycache__").mkdir()
        (self.bundle / "tools" / "__pycache__" / "x.pyc").write_bytes(b"junk")
        second = subprocess.run([sys.executable, "-c", script, str(HELPER.parent), str(self.bundle)],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(first, second)
        self.assertRegex(json.loads(first)[0], r"^[0-9a-f]{64}$")


class NativeHostCallbackRegressionTests(unittest.TestCase):
    """Opt-in real PluginManager checks; fixture semantics are not native evidence."""

    @unittest.skipUnless(os.environ.get("HERMES_COMPAT_HOST_ROOT") and
                         os.environ.get("HERMES_COMPAT_HOST_PYTHON"), "native host not selected")
    def test_actual_host_swallowed_failure_and_none_return_contract(self) -> None:
        import tempfile
        host = Path(os.environ["HERMES_COMPAT_HOST_ROOT"])
        interpreter = os.environ["HERMES_COMPAT_HOST_PYTHON"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, callback, expected in (("failure", "lambda **kwargs: 1 / 0", False),
                                               ("none", "lambda session_id: None", True)):
                with self.subTest(callback=label):
                    bundle = build_bundle(root / label)
                    _write(bundle / "__init__.py", PLUGIN_INIT.replace("lambda **kwargs: None", callback))
                    done = subprocess.run([sys.executable, str(HELPER), "--hermes-root", str(host),
                                           "--hermes-python", interpreter, "--require-host-sha", host_sha(host),
                                           "--omh-bundle", str(bundle), "--home", str(root / (label + "-home"))],
                                          capture_output=True, text=True, timeout=180)
                    receipt = json.loads(done.stdout)
                    self.assertEqual(done.returncode, 0 if expected else 1, done.stdout + done.stderr)
                    hook = receipt["probe"]["hook"]
                    self.assertEqual(hook["ok"], expected, receipt)
                    self.assertEqual(hook["results"], 0)
                    self.assertEqual(hook["completed"], int(expected))


class CompatibilityWorkflowContractTests(unittest.TestCase):
    """The workflow is the admission contract; assert it as text (stdlib only)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_triggers_cover_pr_schedule_and_manual(self) -> None:
        self.assertRegex(self.text, r"(?m)^on:\n")
        self.assertRegex(self.text, r"(?m)^  pull_request:\s*$")
        self.assertRegex(self.text, r"(?m)^  schedule:\n    - cron: \"[^\"]+\"")
        self.assertRegex(self.text, r"(?m)^  workflow_dispatch:")

    def test_the_pr_lane_is_the_required_stable_host_lane(self) -> None:
        pr = self.text.split("stable-host-pr:", 1)
        self.assertEqual(len(pr), 2, "no dedicated stable-host PR job")
        body = pr[1].split("\n  matrix-host:", 1)[0]
        self.assertIn("github.event_name == 'pull_request'", body)
        self.assertIn("host: [stable]", body)
        self.assertIn("omh: [candidate]", body)
        self.assertIn("tools/check_hermes_compatibility.py", body)

    def test_the_scheduled_matrix_is_omh_stable_candidate_by_hermes_stable_main(self) -> None:
        self.assertIn("host: [stable, main]", self.text)
        self.assertIn("omh: [stable, candidate]", self.text)
        self.assertIn("github.event_name != 'pull_request'", self.text)

    def test_every_leg_binds_an_immutable_sha_and_uploads_a_receipt(self) -> None:
        self.assertIn("rev-parse HEAD", self.text)
        self.assertIn("github.sha", self.text)
        self.assertEqual(self.text.count("upload-artifact"), 2, "one artifact step per lane")
        self.assertIn("hermes-compatibility-receipt", self.text)
        self.assertIn("--json-out", self.text)

    def test_skip_as_pass_and_soft_failures_are_absent(self) -> None:
        for forbidden in ("continue-on-error", "|| true", "--exit-zero", "|| echo", "exit 0"):
            self.assertNotIn(forbidden, self.text)
        self.assertIn("timeout-minutes:", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)

    def test_scripts_select_committed_runtime_without_interactive_test_activation(self) -> None:
        self.assertNotIn("source ./activate", self.text)
        self.assertIn("hermes_bootstrap_python", self.text)
        self.assertIn("committed_venv", self.text)
        self.assertIn("venv_python", self.text)
        self.assertIn('--omh-source-root', self.text)
        self.assertIn('path: bundle-source', self.text)
        self.assertIn('ref: ${{ github.sha }}', self.text)
        self.assertIn('$GITHUB_WORKSPACE/tools/check_hermes_compatibility.py', self.text)

    def test_host_acquisition_preserves_version_tags_and_main_history(self) -> None:
        acquisitions = self.text.split('          git init -q "$HOST_DIR"')[1:]
        self.assertEqual(len(acquisitions), 2, "one host acquisition per lane")
        for acquisition in acquisitions:
            script = acquisition.split('          HOST_SHA=', 1)[0]
            with self.subTest(script=script):
                self.assertNotIn("--depth", script, "tagless shallow hosts report an unknown version")
                self.assertIn('"+refs/tags/$HOST_REF:refs/tags/$HOST_REF"', script)
                self.assertIn('fetch --filter=blob:none --tags origin "$HOST_REF"', script)
                self.assertIn('checkout -q --detach FETCH_HEAD', script)

    def test_the_host_is_bootstrapped_from_its_supported_pm_entry_point(self) -> None:
        self.assertIn("setup-hermes.sh", self.text)
        self.assertIn("--runtime-only", self.text)
        for old in (".venv/bin/python", "pip install -e ."):
            self.assertNotIn(old, self.text)


if __name__ == "__main__":
    unittest.main()
