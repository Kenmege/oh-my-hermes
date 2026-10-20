"""Refuse a delegation route that Hermes would not send where it says.

`omh_delegate_route` writes `delegation.model`, `delegation.reasoning_effort`
and `delegation.provider` into `config.yaml`. Hermes resolves the child from
the merged config, and the rungs below can send that child somewhere the
route does not name while OMH answers `routed`. Each is mirrored from Hermes'
source, read at Hermes main 8b9ac5773e and at v0.22.2 (d923a5eb48); a line
number without a revision is the same at both.

1. Direct endpoint. `_resolve_delegation_credentials`
   (tools/delegate_tool_config.py:421-440) takes its direct-endpoint branch
   whenever `delegation.base_url` is set and the provider is not one of
   `_NATIVE_SDK_PROVIDERS` (:316, :432). The child gets that URL,
   `delegation.api_key` or else the parent's key (`_resolve_child_runtime`,
   :583), and `delegation.api_mode` or else a mode detected from the URL
   (:343-354). The provider it gets comes from the URL (:340-347), and
   `delegation.provider` only lends request overrides (:358-362).
2. Local-endpoint bypass. `_local_endpoint_bypass`
   (hermes_cli/runtime_provider.py:1001-1011 at main, :957-967 at v0.22.2)
   runs for any requested provider the earlier rungs did not resolve. It
   checks the MAIN model's config, not the request: `model.base_url` on a host
   other than openrouter.ai, anthropic.com and openai.com or their subdomains
   (`_LOCAL_BYPASS_CLOUD_HOSTS`, :914 at main, :875 at v0.22.2), with
   `model.provider` empty or `auto`. The request then goes to
   `_resolve_openrouter_runtime` (runtime_provider_backends.py:116-200),
   which returns OpenRouter for every provider but `custom` and its aliases.
3. Managed overlay. `_merge_managed_overlay` (hermes_cli/config.py:2282 at
   main, :2261 at v0.22.2) deep-merges `$HERMES_MANAGED_DIR/config.yaml`, or
   else `/etc/hermes/config.yaml`, over the user's file leaf by leaf
   (hermes_cli/managed_scope.py:45-57, :107-109), so a key set there wins
   over what OMH writes.
4. Unparseable file. Hermes' loader raises on a repeated key and then uses
   its last good copy of the file, or else its defaults
   (hermes_cli/config.py:2250-2275 at main), so a route written to that file
   does not take effect.

OMH reads YAML with line scans, not a parser, and Hermes' loader reads more
shapes than these scans do. Any line this module does not model makes its
section unreadable, and the route is refused. Reading a value as "unset" by
mistake is the unsafe answer here; refusing is the safe one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"
DIRECT_ENDPOINT_KEYS = ("base_url", "api_key", "api_mode")
ROUTE_KEYS = ("model", "reasoning_effort", "provider")
# Providers Hermes resolves through their own SDK even with a direct
# `delegation.base_url` (`_NATIVE_SDK_PROVIDERS`, delegate_tool_config.py:316).
NATIVE_SDK_PROVIDERS = frozenset({"bedrock", "vertex", "google", "google-genai"})
# Requested providers the local-endpoint bypass leaves where they would go
# anyway. `moa`, `azure-foundry` and the Vertex names are decided by
# `_resolve_requested_shortcuts` before the bypass (runtime_provider.py:978-998
# at main, :934-954 at v0.22.2; `_VERTEX_NAMES` :913 / :874). `openai` becomes
# a direct alias with an explicit endpoint, which skips the bypass
# (`expand_direct_api_alias`, runtime_provider_custom.py:478-495).
# `openrouter` and `custom` reach `_resolve_openrouter_runtime` either way.
_BYPASS_EXEMPT_PROVIDERS = frozenset(
    {"openrouter", "custom", "openai", "moa", "azure-foundry",
     "vertex", "google-vertex", "vertex-ai", "gcp-vertex", "vertexai"}
)
_LOCAL_BYPASS_CLOUD_HOSTS = ("openrouter.ai", "anthropic.com", "openai.com")
# Plain scalars Hermes' loader reads as null, false or zero, all of which
# `str(value or "").strip()` reads as unset. Measured with Hermes' own
# `utils.fast_safe_load` at both revisions; `y`, `On`, `0o0` and `0:0` read
# as set and are left out.
_UNSET_PLAIN_SCALARS = frozenset(
    {"~", "null", "Null", "NULL", "false", "False", "FALSE", "n", "N", "no", "No", "NO",
     "off", "Off", "OFF"}
)
_ZERO_RE = re.compile(r"[-+]?(?:0b[0_]+|0x[0_]+|(?:0[0_]*(?:\.[0_]*)?|\.0[0_]*)(?:[eE][-+]?[0-9]+)?)")
# A key this module reads: a plain name, or one quoted with no escape in it.
_PLAIN_KEY_RE = re.compile(
    r"(?P<key>[A-Za-z0-9_][A-Za-z0-9_.-]*|'[A-Za-z0-9_.-]+'|\"[A-Za-z0-9_.-]+\")[ ]*:(?:[ ]+(?P<value>.*))?"
)
_COMMENT_RE = re.compile(r"(?:^|\s)#")
_YAML_INDICATORS = frozenset("[]{}|>&*!%@`-?:,")
_TRACKED_SECTIONS = ("delegation", "model")
_ROOT_MODEL_KEYS = ("base_url", "api_base", "provider")
_MISSING = object()


@dataclass
class Layer:
    """What one config file says, as far as these scans can read it."""

    root: dict[str, str | None] = field(default_factory=dict)
    sections: dict[str, dict[str, str | None]] = field(default_factory=dict)
    unreadable_sections: set[str] = field(default_factory=set)
    unreadable_top_level: bool = False


def scan_config(text: str) -> Layer:
    """Read the top level and the `delegation` and `model` sections strictly.

    A value is "" when Hermes reads it as unset, a string when it is set, and
    None when this scan cannot decide; the caller counts None as set.
    """
    layer = Layer()
    if text.startswith(BOM):
        text = text[len(BOM):]
    seen: set[str] = set()
    top_key: str | None = None
    header_empty = False
    body: list[str] = []
    first_content = True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace():
            _close_section(layer, top_key, header_empty, body)
            top_key, body = None, []
            if first_content and stripped == "---":
                first_content = False
                continue
            first_content = False
            match = _PLAIN_KEY_RE.fullmatch(stripped)
            if not match:
                layer.unreadable_top_level = True
                continue
            key = _unquote(match["key"])
            value = match["value"] or ""
            if key in seen or _opens_unclosed(value):
                layer.unreadable_top_level = True
            seen.add(key)
            top_key, header_empty = key, _strip_comment(value) == ""
            layer.root[key] = scalar(value)
            continue
        first_content = False
        if top_key is None:
            layer.unreadable_top_level = True
            continue
        body.append(line)
    _close_section(layer, top_key, header_empty, body)
    return layer


def _close_section(layer: Layer, key: str | None, header_empty: bool, body: list[str]) -> None:
    if key is None or not body:
        return
    if key not in _TRACKED_SECTIONS:
        # A nested value, or a scalar continued onto deeper lines: whatever
        # Hermes reads there, it is not a value this scan can name.
        layer.root[key] = None
        return
    values: dict[str, str | None] = {}
    layer.sections[key] = values
    indent = min(_indent(line) for line in body)
    if not header_empty:
        layer.unreadable_sections.add(key)
        return
    last_key, last_empty = "", False
    for line in body:
        stripped = line.strip()
        depth = _indent(line)
        if "\t" in line[: len(line) - len(line.lstrip())]:
            layer.unreadable_sections.add(key)
            return
        if depth == indent:
            match = _PLAIN_KEY_RE.fullmatch(stripped)
            if not match:
                layer.unreadable_sections.add(key)
                return
            child = _unquote(match["key"])
            value = match["value"] or ""
            if child in values or _opens_unclosed(value):
                # Hermes' loader raises on a repeated key.
                layer.unreadable_sections.add(key)
                return
            values[child] = scalar(value)
            last_key, last_empty = child, _strip_comment(value) == ""
            continue
        if not last_empty:
            # Deeper content under a key that already has a value: a continued
            # scalar or a block scalar's text.
            layer.unreadable_sections.add(key)
            return
        # A nested mapping or list; Hermes reads its text as set.
        values[last_key] = None
        nested = stripped[1:].lstrip() if stripped.startswith("-") else stripped
        match = _PLAIN_KEY_RE.fullmatch(nested)
        nested_value = (match["value"] or "") if match else nested
        if _opens_unclosed(nested_value):
            layer.unreadable_sections.add(key)
            return


def scalar(raw: str) -> str | None:
    """The value Hermes reads for one plain or quoted scalar; "" when unset."""
    value = raw.strip()
    if value[:1] in {"'", '"'}:
        end = value.find(value[0], 1)
        if end < 0:
            return None
        token = value[1:end]
        rest = value[end + 1 :].strip()
        if rest and not rest.startswith("#"):
            # A doubled quote or trailing text.
            return None
        if (value[0] == '"' and "\\" in token) or "${" in token:
            # An escape, or an environment reference Hermes expands.
            return None
        return token.strip()
    value = _strip_comment(value)
    if not value or value in _UNSET_PLAIN_SCALARS or _ZERO_RE.fullmatch(value):
        return ""
    if value[0] in _YAML_INDICATORS or "${" in value:
        # An anchor, alias, tag, block or flow value, or an environment
        # reference: not decidable here.
        return None
    return value


def _strip_comment(value: str) -> str:
    match = _COMMENT_RE.search(value)
    return (value[: match.start()] if match else value).strip()


def _opens_unclosed(raw: str) -> bool:
    """True when the value opens a quote or flow collection it does not close."""
    value = raw.strip()
    if not value or value[0] not in "'\"[{":
        return False
    depth, quote, index = 0, "", 0
    while index < len(value):
        char = value[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                if quote == "'" and value[index + 1 : index + 2] == "'":
                    index += 2
                    continue
                quote = ""
                if depth == 0:
                    return False
        elif char in "'\"":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return False
        index += 1
    return True


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _unquote(key: str) -> str:
    return key[1:-1] if key[:1] in {"'", '"'} else key


def managed_config_layer() -> tuple[str, str | None, str]:
    """`(path, text, error)` for Hermes' managed config, mirroring `get_managed_dir`.

    `$HERMES_MANAGED_DIR` when it is non-empty and a directory, otherwise
    `/etc/hermes` when that is one; no directory means no layer. A file this
    process cannot read returns its error, which the guard refuses on.
    """
    override = os.environ.get("HERMES_MANAGED_DIR", "").strip()
    directory = Path(override) if override else Path("/etc/hermes")
    if not directory.is_dir():
        return "", None, ""
    path = directory / "config.yaml"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return str(path), None, ""
    except OSError as exc:
        return str(path), None, type(exc).__name__
    try:
        return str(path), raw.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        return str(path), None, "UnicodeDecodeError"


def direct_endpoint_provider(base_url: str) -> str:
    """The provider Hermes gives a child on this direct endpoint, "" if unreadable.

    Mirrors `_direct_endpoint_credentials` (:340-347) and Hermes'
    `base_url_hostname` (utils.py:735-752 at main, :724-741 at v0.22.2).
    """
    raw = base_url.strip()
    host = _hostname(raw)
    if host is None:
        return ""
    if host == "chatgpt.com" and "/backend-api/codex" in raw.lower():
        return "openai-codex"
    if host == "api.anthropic.com":
        return "anthropic"
    return "custom"


def _hostname(raw: str) -> str | None:
    try:
        return (urlparse(raw if "://" in raw else f"//{raw}").hostname or "").lower().rstrip(".")
    except ValueError:
        return None


def _is_cloud_host(base_url: str) -> bool:
    """Hermes' `base_url_host_matches` against `_LOCAL_BYPASS_CLOUD_HOSTS`."""
    host = _hostname(base_url.strip())
    return bool(host) and any(host == cloud or host.endswith("." + cloud) for cloud in _LOCAL_BYPASS_CLOUD_HOSTS)


def route_endpoint_refusal(
    text: str,
    *,
    provider: str,
    current_provider: str,
    managed: tuple[str, str | None, str],
) -> dict[str, Any] | None:
    """The refusal for a route Hermes would not send where it says, or None.

    `text` is the `config.yaml` the route would be written into, `provider`
    the provider the route names ("" for a route that names none),
    `current_provider` the `delegation.provider` the write would replace, and
    `managed` what `managed_config_layer` returned.
    """
    user = scan_config(text)
    if user.unreadable_top_level:
        return _refusal(
            "config_unreadable", ["config.yaml"],
            "config.yaml has a top-level line this check cannot read safely: a repeated key, which Hermes' "
            "loader rejects before falling back to its last good copy, or a line that is not a plain `key:`. "
            "It cannot tell where Hermes would send the child.",
            "Make every top-level line a plain, unrepeated `key:` yourself, then route again.",
        )
    managed_path, managed_text, managed_error = managed
    layer = scan_config(managed_text) if managed_text is not None else Layer()
    if managed_error or layer.unreadable_top_level or "delegation" in layer.unreadable_sections:
        return _refusal(
            "managed_config_unreadable", [managed_path],
            f"Hermes' managed config {managed_path} applies over config.yaml, and this check cannot read it "
            f"safely ({managed_error or 'a line it does not model'}), so it cannot tell where Hermes would "
            "send the child.",
            "That file is managed outside OMH; once it reads as plain `key: value` lines, route again.",
        )
    managed_delegation = layer.sections.get("delegation", {})
    pinned = [f"delegation.{key}" for key in ROUTE_KEYS if key in managed_delegation]
    if pinned:
        return _refusal(
            "managed_config_pins_route", pinned,
            f"Hermes' managed config {managed_path} sets {', '.join(pinned)}, and Hermes applies it over "
            "config.yaml, so the route OMH writes to config.yaml would not take effect.",
            "That file is managed outside OMH.",
        )
    if "delegation" in user.unreadable_sections:
        return _refusal(
            "delegation_unreadable", ["delegation"],
            "The delegation section of config.yaml has a line this check cannot read safely; it reads only "
            "plain `key: value` lines, comments, and nested lines under a key with no value. It cannot tell "
            "whether delegation.base_url sends the child somewhere else.",
            "Rewrite those lines yourself, then route again.",
        )
    delegation = {**user.sections.get("delegation", {}), **managed_delegation}
    base_url = delegation.get("base_url", "")
    source = " (from Hermes' managed config)" if "base_url" in managed_delegation else ""
    wanted = provider.strip().lower()
    if not wanted:
        if base_url != "" and current_provider.strip().lower() in NATIVE_SDK_PROVIDERS:
            return _refusal(
                "native_provider_removed", ["delegation.provider", "delegation.base_url"],
                f"delegation.provider is {current_provider!r}, which Hermes resolves through its own SDK even "
                f"though delegation.base_url is set{source}. A route that names no provider removes it, and "
                "Hermes would then build the child against delegation.base_url instead.",
                "Name the provider with the model, or remove delegation.base_url yourself "
                "(`hermes config unset delegation.base_url`).",
            )
        return None
    if base_url != "" and wanted not in NATIVE_SDK_PROVIDERS:
        return _direct_endpoint_refusal(delegation, base_url, provider, wanted, source)
    return _local_bypass_refusal(user, layer, provider, wanted)


def _direct_endpoint_refusal(
    delegation: dict[str, str | None], base_url: str | None, provider: str, wanted: str, source: str
) -> dict[str, Any] | None:
    endpoint_provider = direct_endpoint_provider(base_url) if base_url is not None else ""
    keys = [f"delegation.{key}" for key in DIRECT_ENDPOINT_KEYS if delegation.get(key, "") != ""]
    if endpoint_provider == wanted:
        if delegation.get("api_key", ""):
            return None
        return _refusal(
            "direct_endpoint_without_key", ["delegation.base_url", "delegation.api_key"],
            f"delegation.base_url{source} is the endpoint Hermes names as {provider!r}, but delegation.api_key "
            "is unset or unreadable, so the child sends the parent session's key there unless a credential "
            f"pool for {provider!r} serves that endpoint.",
            "Set delegation.api_key yourself, or remove delegation.base_url "
            "(`hermes config unset delegation.base_url`) to route by provider.",
        )
    lead = (
        f"delegation.base_url is set{source}"
        if base_url is not None
        else "delegation.base_url holds a value this check cannot read, so it counts as set"
    )
    built_as = f" as provider '{endpoint_provider}'" if endpoint_provider else ""
    companions = [key for key in keys if key != "delegation.base_url"]
    companion_note = (
        f" {' and '.join(companions)} {'is' if len(companions) == 1 else 'are'} read only with it, so "
        "removing delegation.base_url is the change that matters."
        if companions
        else ""
    )
    return _refusal(
        "direct_endpoint_overrides_provider", keys or ["delegation.base_url"],
        f"{lead}. While it is set, Hermes' delegate_task builds the child against that endpoint{built_as} and "
        f"takes no endpoint, key or transport from delegation.provider: a route to provider {provider!r} would "
        "report routed while requests went to the existing endpoint.",
        "To route by provider, remove delegation.base_url yourself "
        f"(`hermes config unset delegation.base_url`).{companion_note}",
    )


def _local_bypass_refusal(user: Layer, managed: Layer, provider: str, wanted: str) -> dict[str, Any] | None:
    if wanted in _BYPASS_EXEMPT_PROVIDERS:
        return None
    if "model" in user.unreadable_sections or "model" in managed.unreadable_sections:
        base_url, model_provider = None, None
    else:
        base_url = _merged(_base_url_candidates(managed), _base_url_candidates(user))
        model_provider = _merged(_provider_candidates(managed), _provider_candidates(user))
    if base_url == "":
        return None
    if model_provider is not None and model_provider.strip().lower() not in ("", "auto"):
        return None
    if base_url is not None and _is_cloud_host(base_url):
        return None
    shape = (
        "model.base_url names a host Hermes treats as local (any host but openrouter.ai, anthropic.com and "
        "openai.com) and model.provider is unset or `auto`"
        if base_url is not None and model_provider is not None
        else "the model section has a value this check cannot read safely, so model.base_url and "
        "model.provider may have the shape Hermes treats as a local endpoint"
    )
    return _refusal(
        "local_endpoint_bypass", ["model.base_url", "model.provider"],
        f"{shape}. Hermes then sends an explicit delegation provider through its local-endpoint fallback and "
        f"does not resolve {provider!r} itself: the child goes to OpenRouter, or to the custom endpoint for a "
        "custom alias, or the dispatch fails for want of an OpenRouter key.",
        "Set model.provider to the provider that serves model.base_url yourself (for example "
        "`hermes config set model.provider custom`), or clear model.base_url.",
    )


def _base_url_candidates(layer: Layer) -> list[Any]:
    """Hermes' order: model.base_url, model.api_base, root base_url, root api_base."""
    section = layer.sections.get("model", {})
    return [
        section.get("base_url", _MISSING), section.get("api_base", _MISSING),
        layer.root.get("base_url", _MISSING), layer.root.get("api_base", _MISSING),
    ]


def _provider_candidates(layer: Layer) -> list[Any]:
    """model.provider, root provider, then a provider nested in model.default/model/name."""
    section = layer.sections.get("model", {})
    candidates: list[Any] = [section.get("provider", _MISSING), layer.root.get("provider", _MISSING)]
    if layer.root.get("model", "") is None and "model" not in layer.sections:
        candidates.append(None)
    candidates.extend(None for key in ("default", "model", "name") if section.get(key, "") is None)
    return candidates


def _merged(managed: list[Any], user: list[Any]) -> str | None:
    """Managed leaves win where the managed file sets any of them."""
    chosen = managed if any(value is not _MISSING for value in managed) else user
    for value in chosen:
        if value is _MISSING:
            continue
        if value is None or value != "":
            return value
    return ""


def _refusal(reason: str, keys: list[str], lead: str, remedy: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": f"{lead} No route was written. {remedy} OMH does not change these keys.",
        "reason": reason,
        "conflicting_keys": keys,
    }
