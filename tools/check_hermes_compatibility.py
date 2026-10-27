#!/usr/bin/env python3
"""Fail-closed Hermes <-> OMH compatibility gate (A6).

This is a CI helper, not a product feature: it never runs from `omh` code and
adds no runtime dependency.  Two modes:

* default: orchestrate one real-host receipt.  Bind the host checkout to an
  immutable SHA, prove the selected interpreter imports Hermes from that
  checkout, hash the staged OMH plugin bundle, stage a disposable HERMES_HOME,
  then re-enter this file under the host interpreter in `--probe` mode.
* `--probe`: run inside the host interpreter.  Load the staged plugin through
  Hermes' own ``PluginManager``, execute a harmless registered tool handler and
  a harmless registered hook, and check the host's tool-deferral invariant.

A missing host, a skipped check, an unknown registration set, an import from
outside the bound checkout, or a probe that could not observe the host all
exit non-zero.  The receipt says what was actually observed; it never claims an
installation or an update happened here.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

SCHEMA = "hermes_compatibility_receipt/v1"
PROBE_SCHEMA = "hermes_compatibility_probe/v1"
SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z|\A[0-9a-f]{64}\Z")
PLUGIN_ID = "omh"
HARMLESS_TOOL = "omh_status"
HARMLESS_HOOK = "pre_llm_call"
NETWORK_EVENTS = {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
                  "socket.gethostbyaddr", "socket.sendto"}


def _deny_network(event: str, _args: Any) -> None:
    if event in NETWORK_EVENTS:
        raise PermissionError("network operations are disabled in compatibility probes")


class GateError(RuntimeError):
    """A fail-closed condition that must stop the gate."""


def bundle_digest(bundle: Path) -> str:
    """SHA-256 over sorted relative paths and file bytes of a plugin directory."""
    root = bundle.resolve()
    if not root.is_dir():
        raise GateError(f"omh bundle is not a directory: {bundle}")
    digest = hashlib.sha256()
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda p: p.relative_to(root).as_posix())
    if not files:
        raise GateError(f"omh bundle has no files: {bundle}")
    for path in files:
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _module_under_root(module_file: str | None, root: Path) -> bool:
    if not module_file:
        return False
    try:
        resolved = Path(module_file).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _checked_host_modules(root: Path) -> dict[str, str]:
    paths = {}
    for name, module in tuple(sys.modules.items()):
        top = name.split(".")[0]
        if top not in {"hermes_cli", "agent", "tools", "pm", "hermes_constants",
                       "hermes_bootstrap", "toolsets", "model_tools", "run_agent"}:
            continue
        path = getattr(module, "__file__", None)
        if not _module_under_root(path, root):
            raise GateError(f"host module {name} is outside the bound checkout: {path}")
        paths[name] = str(Path(path).resolve())
    return paths


def _child_env(root: Path, home: Path) -> dict[str, str]:
    """Allowlist, not a secret-name denylist; establish homes before any import."""
    home = home.resolve()
    for directory in (home / "os-home", home / "tmp", home / "omh"):
        directory.mkdir(parents=True, exist_ok=True)
    return {"PATH": os.defpath, "PYTHONPATH": str(root.resolve()),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "HOME": str(home / "os-home"), "HERMES_HOME": str(home),
            "OMH_HOME": str(home / "omh"), "TMPDIR": str(home / "tmp"),
            "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _host_identity(interpreter: Path, root: Path, home: Path) -> dict[str, object]:
    if not interpreter.is_file():
        raise GateError(f"host interpreter does not exist: {interpreter}")
    code = (
        "import json,sys,importlib\n"
        "def deny_network(event,args):\n"
        f"    if event in {sorted(NETWORK_EVENTS)!r}:\n"
        "        raise PermissionError('network operations are disabled in compatibility probes')\n"
        "sys.addaudithook(deny_network)\n"
        "out={'executable':sys.executable,'python_version':sys.version.split()[0],'modules':{},'version':None}\n"
        "for name in ('hermes_cli','agent','tools.registry','hermes_cli.plugins','hermes_cli.config_defaults','hermes_constants','tools.tool_search','hermes_cli.version_info'):\n"
        "    try:\n"
        "        module=importlib.import_module(name)\n"
        "    except Exception as exc:\n"
        "        out['modules'][name]={'file':None,'error':type(exc).__name__+': '+str(exc)}\n"
        "    else:\n"
        "        out['modules'][name]={'file':getattr(module,'__file__',None),'error':None}\n"
        "try:\n"
        "    from hermes_cli.version_info import get_version_info\n"
        "    info=get_version_info()\n"
        "    out['version']={'base_version':getattr(info,'base_version',None),'source':'version_info'}\n"
        "except Exception:\n"
        "    import hermes_cli\n"
        "    out['version']={'base_version':getattr(hermes_cli,'__version__',None),'source':'__version__'}\n"
        "print(json.dumps(out,sort_keys=True))\n"
    )
    env = _child_env(root, home)
    done = subprocess.run([str(interpreter), "-c", code], capture_output=True, text=True, env=env,
                          cwd=str(root), timeout=180)
    if done.returncode != 0:
        raise GateError(f"host interpreter cannot import Hermes: {done.stderr.strip() or done.stdout.strip()}")
    try:
        identity = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise GateError(f"host identity probe did not return JSON: {exc}") from exc
    modules = identity.get("modules") or {}
    for name, info in modules.items():
        if info.get("error"):
            raise GateError(f"host module {name} is not importable: {info['error']}")
    for name in modules:
        if not _module_under_root(modules.get(name, {}).get("file"), root):
            raise GateError(
                f"host module {name} imported from {modules.get(name, {}).get('file')!r}, not from the bound checkout {root}"
            )
    identity["root"] = str(root)
    identity["checkout_sha"] = _git_sha(root)
    return identity


def _git_sha(root: Path) -> str:
    if not (root / ".git").exists():
        raise GateError(f"host checkout has no Git boundary: {root}")
    done = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.defpath, "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"},
    )
    sha = done.stdout.strip()
    if done.returncode != 0 or not SHA_RE.match(sha):
        raise GateError(f"cannot bind host checkout to an immutable SHA: {done.stderr.strip()}")
    if _git_dirty(root):
        raise GateError("host checkout has dirty source; HEAD cannot identify the executed source")
    return sha


def _git_dirty(root: Path) -> bool:
    done = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                          capture_output=True, text=True, timeout=60,
                          env={"PATH": os.defpath, "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1",
                               "GIT_TERMINAL_PROMPT": "0"})
    if done.returncode:
        raise GateError("cannot inspect source worktree provenance")
    return bool(done.stdout.strip())


def _bundle_provenance(bundle: Path, source_root: str | None, required_sha: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"bundle": str(bundle), "bundle_digest": bundle_digest(bundle),
                              "plugin_id": PLUGIN_ID, "source_sha": None,
                              "source_base_sha": None, "source_dirty": None,
                              "source_bundle_matches": None}
    if source_root:
        root = Path(source_root).resolve()
        if not (root / ".git").exists():
            raise GateError("OMH source has no Git boundary")
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=60, env={"PATH": os.defpath, "HOME": str(root),
                                                         "GIT_CONFIG_NOSYSTEM": "1"})
        if done.returncode or not SHA_RE.fullmatch(done.stdout.strip()):
            raise GateError("OMH source has no immutable base revision")
        result["source_base_sha"] = done.stdout.strip()
        result["source_dirty"] = _git_dirty(root)
        result["source_bundle_matches"] = bundle_digest(root / "src/plugin_bundle/omh") == result["bundle_digest"]
        if not result["source_dirty"] and result["source_bundle_matches"]:
            result["source_sha"] = result["source_base_sha"]
    if required_sha and (not SHA_RE.fullmatch(required_sha) or result["source_sha"] != required_sha):
        raise GateError("OMH SHA requires clean, digest-matching Git source provenance")
    return result


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _prepare_home(home: Path, bundle: Path) -> Path:
    """Create a disposable Hermes home containing only the staged plugin."""
    living = (Path(os.path.expanduser("~")) / ".hermes").resolve()
    resolved = home.resolve()
    if resolved == living:
        raise GateError(f"refusing to use the live Hermes home as the probe home: {home}")
    # A nested directory is fine only while it is not itself a live store.
    if any((resolved / marker).exists() for marker in ("config.yaml", "plugins", "profiles", "hermes_state.db")):
        raise GateError(f"probe home already holds a Hermes store: {home}")
    if resolved.exists() and any(resolved.iterdir()):
        raise GateError(f"probe home is not empty; it must be freshly created: {home}")
    plugins = home / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, plugins / PLUGIN_ID, symlinks=False)
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - omh\n  entries:\n    omh:\n      settings:\n        omh_home: '%s'\n"
        % (home / "omh").as_posix(),
        encoding="utf-8",
    )
    return home


def _probe(args: argparse.Namespace) -> int:
    """Run under the host interpreter; every failure is a receipt, not a crash."""
    receipt: dict[str, object] = {
        "schema_version": PROBE_SCHEMA,
        "observed": False,
        "ok": False,
        "reasons": [],
        "tool": None,
        "hook": None,
        "registrations": {},
        "deferral": {},
    }
    reasons: list[str] = receipt["reasons"]  # type: ignore[assignment]
    sys.addaudithook(_deny_network)
    try:
        root = Path(args.hermes_root).resolve()
        home = Path(args.home).resolve()
        os.environ["HERMES_HOME"] = str(home)
        os.environ["OMH_HOME"] = str(home / "omh")
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.path[:] = [str(root)] + [entry for entry in sys.path if entry and Path(entry).resolve() != Path(__file__).resolve().parents[1]]
        for name in list(sys.modules):
            if name.split(".")[0] in {"hermes_cli", "hermes_bootstrap", "hermes_constants", "pm"}:
                del sys.modules[name]
        import hermes_cli.plugins as host_plugins  # noqa: PLC0415
        from hermes_constants import hermes_home_key  # noqa: PLC0415

        manager = host_plugins.PluginManager(scope_key=hermes_home_key(home))
        manager.discover_and_load()
        receipt["observed"] = True
        loaded = None
        for key, plugin in getattr(manager, "_plugins", {}).items():
            if key == PLUGIN_ID or getattr(getattr(plugin, "manifest", None), "name", None) == PLUGIN_ID:
                loaded = plugin
                break
        if loaded is None:
            reasons.append("hermes PluginManager did not load the omh plugin from the staged home")
            return _emit_probe(receipt)
        if not getattr(loaded, "enabled", False):
            reasons.append(f"omh plugin is not enabled: error={getattr(loaded, 'error', None)!r}")
        manifest = loaded.manifest
        expected_tools = sorted(getattr(manifest, "provides_tools", None) or [])
        expected_hooks = sorted(getattr(manifest, "provides_hooks", None) or [])
        tools = sorted(getattr(loaded, "tools_registered", []) or [])
        hooks = sorted(getattr(loaded, "hooks_registered", []) or [])
        missing_tools = sorted(set(expected_tools) - set(tools))
        extra_tools = sorted(set(tools) - set(expected_tools))
        missing_hooks = sorted(set(expected_hooks) - set(hooks))
        receipt["registrations"] = {
            "tools": tools,
            "hooks": hooks,
            "expected_tools": expected_tools,
            "expected_hooks": expected_hooks,
            "missing_tools": missing_tools,
            "unexpected_tools": extra_tools,
            "missing_hooks": missing_hooks,
        }
        if not tools:
            reasons.append("omh registered no tools through the host PluginContext")
        if missing_tools or extra_tools:
            reasons.append(f"tool registrations do not match plugin.yaml: missing={missing_tools} unexpected={extra_tools}")
        if missing_hooks:
            reasons.append(f"declared hooks were not registered: {missing_hooks}")

        tool = _consume_tool(manager, home, tools)
        receipt["tool"] = tool
        if not tool.get("ok"):
            reasons.append(f"tool consumer failed: {tool.get('name')}: {tool.get('error')}")
        hook = _consume_hook(manager, home, hooks)
        receipt["hook"] = hook
        if not hook.get("ok"):
            reasons.append(f"hook consumer failed: {hook.get('name')}: {hook.get('error')}")
        deferral = _deferral_is_satisfied(manager, tools)
        receipt["deferral"] = deferral
        if not deferral.get("ok"):
            reasons.append(f"tool-deferral invariant is not satisfied: {deferral.get('detail')}")
        # Check the complete imported host namespace, including mixins and helpers.
        receipt["host_module_paths"] = _checked_host_modules(root)
        receipt["staged_bundle_digest"] = bundle_digest(home / "plugins" / PLUGIN_ID)
    except GateError as exc:
        reasons.append(str(exc))
    except Exception as exc:  # noqa: BLE001 - the gate must record, not traceback
        reasons.append(f"{type(exc).__name__}: {exc}")
    receipt["ok"] = bool(receipt["observed"]) and not reasons
    return _emit_probe(receipt)


def _consume_tool(manager: Any, home: Path, tools: list[str]) -> dict[str, object]:
    name = HARMLESS_TOOL if HARMLESS_TOOL in tools else ""
    result: dict[str, object] = {"name": name, "ok": False, "error": "no registered tool to consume"}
    if not name:
        return result
    try:
        from tools.registry import registry  # noqa: PLC0415

        scope = getattr(manager, "scope_key", None)
        entry = registry.get_entry(name, scope=scope) if scope else registry.get_entry(name)
        if entry is None:
            result["error"] = "tool is registered on the plugin but absent from tools.registry"
            return result
        handler = entry.handler
        # Native tool calls use the profile bound at plugin registration, not an override.
        answer = handler({})
        if not isinstance(answer, str) or not answer.strip():
            raise GateError("omh_status did not return a JSON string")
        payload = json.loads(answer)
        if not isinstance(payload, dict) or payload.get("success") is False or payload.get("error"):
            raise GateError("omh_status returned an error or non-object payload")
        if (payload.get("schema_version") != "omh_status/v1"
                or payload.get("privacy") != "metadata_only"
                or not isinstance(payload.get("runs"), list)
                or type(payload.get("runtime_state_present")) is not bool
                or not isinstance(payload.get("evidence_boundary"), dict)
                or payload.get("omh_home") != str(home / "omh")):
            raise GateError("omh_status payload does not satisfy the metadata-only status schema/home")
        result.update(ok=True, toolset=entry.toolset, bytes=len(answer.encode("utf-8")),
                      schema_version=payload["schema_version"], error=None)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _consume_hook(manager: Any, home: Path, hooks: list[str]) -> dict[str, object]:
    name = HARMLESS_HOOK if HARMLESS_HOOK in hooks else ""
    result: dict[str, object] = {"name": name, "ok": False, "error": "no harmless registered hook"}
    if not name:
        return result
    original = None
    try:
        callbacks = tuple(manager.iter_hook_callbacks(name))
        original = manager._hooks.get(name)
        if not callbacks or not isinstance(original, list) or tuple(original) != callbacks:
            raise GateError("host callback observation seam unavailable or inconsistent")
        observations = [{"started": 0, "completed": 0, "failure": None} for _ in callbacks]

        def observe(callback: Any, row: dict[str, Any]) -> Any:
            # Keep the host's signature filtering, timeout and async resolution intact.
            # Instrument only this disposable manager, never the host source or globals.
            if inspect.iscoroutinefunction(callback):
                @functools.wraps(callback)
                async def async_observed(*args: Any, **kwargs: Any) -> Any:
                    row["started"] += 1
                    try:
                        value = await callback(*args, **kwargs)
                    except BaseException as exc:
                        row["failure"] = type(exc).__name__
                        raise
                    row["completed"] += 1
                    return value
                return async_observed

            @functools.wraps(callback)
            def observed(*args: Any, **kwargs: Any) -> Any:
                row["started"] += 1
                try:
                    value = callback(*args, **kwargs)
                    if inspect.isawaitable(value):
                        raise GateError("non-async callback returned an awaitable; completion unobserved")
                except BaseException as exc:
                    row["failure"] = type(exc).__name__
                    raise
                row["completed"] += 1
                return value
            return observed

        manager._hooks[name] = [observe(callback, row) for callback, row in zip(callbacks, observations)]
        returned = manager.invoke_hook(
            name, session_id="compat-gate", turn_id="compat-gate-1",
            omh_home=str(home / "omh"), hermes_home=str(home), messages=[], tool_name="read_file",
        )
        completed = sum(row["completed"] for row in observations)
        healthy = all(row["started"] == row["completed"] == 1 and row["failure"] is None
                      for row in observations)
        result.update(ok=healthy, callbacks=len(callbacks), completed=completed,
                      observations=observations, results=len(returned),
                      error=None if healthy else "callback failed, skipped, or completion not observed")
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if original is not None:
            manager._hooks[name] = original
    return result


def _deferral_is_satisfied(manager: Any, tools: list[str]) -> dict[str, object]:
    """Behavioral deferral check: the omh toolset must not be a direct surface."""
    detail: dict[str, object] = {"ok": False, "tool_search_enabled": None, "omh_toolset": "omh", "direct_surface": None}
    try:
        from hermes_cli.config_defaults import DEFAULT_CONFIG  # noqa: PLC0415
        from tools import tool_search  # noqa: PLC0415

        enabled = DEFAULT_CONFIG.get("tools", {}).get("tool_search", {}).get("enabled")  # type: ignore[union-attr]
        detail["tool_search_enabled"] = enabled
        direct = getattr(tool_search, "_DIRECT_SURFACE_TOOLSETS", None)
        detail["direct_surface"] = sorted(direct) if direct is not None else None
        if direct is None:
            detail["detail"] = "tools/tool_search.py no longer defines _DIRECT_SURFACE_TOOLSETS"
            return detail
        if "omh" in direct:
            detail["detail"] = "'omh' is listed as a direct surface toolset; plugin tools would be eager"
            return detail
        if enabled not in {"auto", "on"}:
            detail["detail"] = f"tool_search default is {enabled!r}, not auto/on"
            return detail
        sample = HARMLESS_TOOL if HARMLESS_TOOL in tools else (tools[0] if tools else "")
        if sample:
            detail["sample_tool"] = sample
            detail["deferrable"] = bool(tool_search.is_deferrable_tool_name(sample))
            if not detail["deferrable"]:
                detail["detail"] = f"{sample} is not deferrable through the host's own predicate"
                return detail
        detail["ok"] = True
        detail["detail"] = "omh toolset is behind tool_search by default"
    except Exception as exc:  # noqa: BLE001
        detail["detail"] = f"{type(exc).__name__}: {exc}"
    return detail


def _emit_probe(receipt: dict[str, object]) -> int:
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("observed") and receipt.get("ok") else 1


def _run_probe(args: argparse.Namespace) -> dict[str, object]:
    env = _child_env(Path(args.hermes_root), Path(args.home))
    command = [
        str(Path(args.hermes_python)), str(Path(__file__).resolve()), "--probe",
        "--hermes-root", str(Path(args.hermes_root).resolve()),
        "--home", str(Path(args.home).resolve()),
    ]
    done = subprocess.run(command, capture_output=True, text=True, env=env,
                          cwd=str(Path(args.hermes_root).resolve()), timeout=args.probe_timeout)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    try:
        receipt = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError as exc:
        raise GateError(f"probe did not emit JSON (exit {done.returncode}): {done.stderr.strip()[:2000]}") from exc
    if not isinstance(receipt, dict) or receipt.get("schema_version") != PROBE_SCHEMA:
        raise GateError(f"probe emitted no {PROBE_SCHEMA} receipt (exit {done.returncode}): {done.stdout[:2000]}")
    if done.returncode != 0 and receipt.get("ok"):
        raise GateError("probe reported ok but exited non-zero")
    receipt["exit_code"] = done.returncode
    receipt["stderr_tail"] = done.stderr.strip()[-2000:]
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Hermes/OMH compatibility gate")
    parser.add_argument("--hermes-root", required=True, help="Hermes checkout bound by its Git SHA")
    parser.add_argument("--hermes-python", default=None, help="interpreter selected by the host's PM environment")
    parser.add_argument("--require-host-sha", default=None, help="immutable Hermes SHA this run must observe")
    parser.add_argument("--require-host-version", default=None, help="host release version this run must observe")
    parser.add_argument("--omh-bundle", default=None, help="staged plugin directory (plugin.yaml + code)")
    parser.add_argument("--home", required=True, help="fresh disposable HERMES_HOME for this probe")
    parser.add_argument("--omh-source-root", default=None, help="Git source checkout for honest bundle provenance")
    parser.add_argument("--omh-sha", default=None, help="require clean, digest-matching OMH source at this SHA")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--probe-timeout", type=int, default=600)
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        return _probe(args)
    for name in ("hermes_python", "require_host_sha", "omh_bundle"):
        if not getattr(args, name):
            parser.error(f"--{name.replace(chr(95), chr(45))} is required")

    receipt: dict[str, object] = {
        "schema_version": SCHEMA,
        "observed": False,
        "ok": False,
        "reasons": [],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    reasons: list[str] = receipt["reasons"]  # type: ignore[assignment]
    try:
        if not SHA_RE.match(args.require_host_sha):
            raise GateError(f"--require-host-sha is not an immutable SHA: {args.require_host_sha!r}")
        root = Path(args.hermes_root).resolve()
        sha = _git_sha(root)
        if sha != args.require_host_sha:
            raise GateError(f"host checkout is {sha}, expected {args.require_host_sha}")
        bundle = Path(args.omh_bundle).resolve()
        home = _prepare_home(Path(args.home).resolve(), bundle)
        identity = _host_identity(Path(args.hermes_python), root, home)
        receipt["host"] = identity
        version = (identity.get("version") or {}).get("base_version")
        if args.require_host_version and version != args.require_host_version:
            raise GateError(f"host version is {version!r}, expected {args.require_host_version!r}")
        bundle = Path(args.omh_bundle).resolve()
        manifest_file = bundle / "plugin.yaml"
        if not manifest_file.is_file():
            raise GateError(f"staged bundle has no plugin.yaml: {bundle}")
        receipt["omh"] = _bundle_provenance(bundle, args.omh_source_root, args.omh_sha)
        receipt["disposable_home"] = {
            "path": str(home.resolve()),
            "outside_live_home": not _inside(home, Path(os.path.expanduser("~")) / ".hermes"),
        }
        probe = _run_probe(args)
        receipt["observed"] = True
        receipt["probe"] = probe
        if _git_sha(root) != sha:
            reasons.append("host revision changed during the probe")
        if probe.get("staged_bundle_digest") != receipt["omh"]["bundle_digest"]:
            reasons.append("staged plugin bytes do not match the observed bundle digest")
        if not probe.get("observed") or not probe.get("ok"):
            reasons.extend(str(item) for item in (probe.get("reasons") or ["probe did not observe a healthy registration"]))
        receipt["ok"] = bool(receipt["observed"]) and not reasons and bool(probe.get("ok"))
    except GateError as exc:
        reasons.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"{type(exc).__name__}: {exc}")
    receipt["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = json.dumps(receipt, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
