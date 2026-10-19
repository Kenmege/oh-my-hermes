"""Why a native tool's `omh` package backend did or did not import here (#2166).

Hermes registers every tool and hook of a bundle whose interpreter has no
`oh-my-hermes` package -- every documented install puts the package in OMH's
own environment -- and that is a supported, metadata-only install. A tool
that needs the package must still refuse with a bounded result that says
which failure it met and which install routes are real, never raise. Each such
tool classifies its failed import here, so one host gets one answer.

Stdlib only, like every module of the bundle: the classifier must work on the
very host it describes.
"""

from __future__ import annotations

import importlib.util
import sys

# `absent` is the supported metadata-only install, and only one failure is it:
# a ModuleNotFoundError naming an `omh` module while no top-level `omh` exists.
# Every other failure is never `absent`. With an `omh` package present, a
# missing `omh.*` module or name is `incompatible` (the package does not
# provide what this bundle imports); anything else is `broken`.
PACKAGE_BACKEND_AVAILABLE = "available"
PACKAGE_BACKEND_ABSENT = "absent"
PACKAGE_BACKEND_INCOMPATIBLE = "incompatible"
PACKAGE_BACKEND_BROKEN = "broken"
# Where the package-dependent features, and the install routes that exist for
# each kind of Hermes environment, are written down.
PACKAGE_BACKEND_ROUTE = "docs/INSTALLATION.md#what-the-hermes-interpreter-must-be-able-to-import"


def _omh_package_present() -> bool:
    # Asked at call time, never cached: the answer is a property of the
    # interpreter now, and a module-level answer would outlive a package
    # installed, or an import finished, after this bundle loaded.
    if sys.modules.get("omh") is not None:
        return True
    return importlib.util.find_spec("omh") is not None


def _distribution_version() -> str:
    # Read from distribution metadata, so a package whose import is the
    # failure being reported is never imported again to name its version.
    # Imported here: `importlib.metadata` costs about 18 ms cold, and only a
    # refusal or `omh_probe` asks, never the plugin load Hermes times.
    import importlib.metadata

    try:
        return importlib.metadata.version("oh-my-hermes")
    except importlib.metadata.PackageNotFoundError:
        return ""


def classify_backend_failure(error: ImportError) -> dict[str, str]:
    """The `package_backend` block for an import that just failed."""
    failed = str(error.name or "")
    names_omh = failed == "omh" or failed.startswith("omh.")
    if not _omh_package_present():
        absent = isinstance(error, ModuleNotFoundError) and names_omh
        status = PACKAGE_BACKEND_ABSENT if absent else PACKAGE_BACKEND_BROKEN
    else:
        status = PACKAGE_BACKEND_INCOMPATIBLE if names_omh else PACKAGE_BACKEND_BROKEN
    return {
        "status": status,
        "failed_import": failed,
        "package_version": "" if status == PACKAGE_BACKEND_ABSENT else _distribution_version(),
        "install_route": PACKAGE_BACKEND_ROUTE,
    }


def available_backend() -> dict[str, str]:
    """The `package_backend` block for an import that succeeded."""
    return {
        "status": PACKAGE_BACKEND_AVAILABLE,
        "failed_import": "",
        "package_version": _distribution_version(),
        "install_route": PACKAGE_BACKEND_ROUTE,
    }


def package_backend_detail(backend: dict[str, str], *, feature: str, cli_route: str = "") -> str:
    """The sentence a person reads for a `package_backend` block.

    It names only routes that exist. A Hermes environment its package manager
    prepares has none yet, so it is never told to take the package by hand.
    `cli_route` is the feature's own CLI path to the same store, when it has
    one a person may use in its place.
    """
    status = backend["status"]
    if status == PACKAGE_BACKEND_AVAILABLE:
        return f"The Python interpreter running this plugin imports the oh-my-hermes backend for {feature}."
    version = f" {backend['package_version']}" if backend["package_version"] else ""
    failed = f" (failed import: {backend['failed_import']})" if backend["failed_import"] else ""
    if status == PACKAGE_BACKEND_ABSENT:
        lead = (
            "The OMH plugin is registered, but the Python interpreter running it cannot import "
            f"the oh-my-hermes package, so {feature} has no backend here. When Hermes' package "
            "manager owns that environment there is no supported way to add the package yet; "
            "on a Hermes environment you manage yourself, install oh-my-hermes there."
        )
    elif status == PACKAGE_BACKEND_INCOMPATIBLE:
        lead = (
            f"The Python interpreter running this plugin imports an oh-my-hermes package{version} "
            f"that does not provide what {feature} imports{failed}, so {feature} does not run "
            "against it. Bring that package to the version that matches this plugin, the same "
            "way it was installed."
        )
    else:
        lead = (
            f"Importing the oh-my-hermes backend for {feature} on the Python interpreter running "
            f"this plugin failed{failed}, and not because the package is absent. Repair that "
            "package the same way it was installed."
        )
    route = f" {cli_route}" if cli_route else ""
    return f"{lead}{route} See {PACKAGE_BACKEND_ROUTE}."


__all__ = [
    "PACKAGE_BACKEND_ABSENT",
    "PACKAGE_BACKEND_AVAILABLE",
    "PACKAGE_BACKEND_BROKEN",
    "PACKAGE_BACKEND_INCOMPATIBLE",
    "PACKAGE_BACKEND_ROUTE",
    "available_backend",
    "classify_backend_failure",
    "package_backend_detail",
]
