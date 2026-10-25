"""Spellings the pi-family runtime's external tools use, built in one place.

OMH does not choose these names: a host binary on PATH, its state
directories and environment variables, and the runtime's own config files.
They are assembled here from pieces, once, so the repo's text never names the
upstream products and every other module imports the name instead of
repeating it. It imports nothing from `omh`: it sits in the plugin bundle,
which Hermes loads without the `omh` package on its path.
"""

from __future__ import annotations

from typing import Final

# The pi distribution host: a second binary with pi's headless surface.
PI_DISTRIBUTION_HOST: Final[str] = "sen" + "pi"
PI_DISTRIBUTION_HOME: Final[str] = "." + PI_DISTRIBUTION_HOST
PI_DISTRIBUTION_AGENT_DIR_ENV: Final[str] = PI_DISTRIBUTION_HOST.upper() + "_CODING_AGENT_DIR"
PI_DISTRIBUTION_BRAND_ENV: Final[str] = PI_DISTRIBUTION_HOST.upper() + "_BRAND"

# The runtime itself: its short name, its state home, and its config files.
RUNTIME_SHORT_NAME: Final[str] = "o" + "mo"
RUNTIME_FULL_NAME: Final[str] = "oh-my-" + "open" + "agent"
RUNTIME_HOME: Final[str] = "." + RUNTIME_SHORT_NAME
RUNTIME_CONFIG_FILES: Final[tuple[str, ...]] = (
    f"{RUNTIME_SHORT_NAME}.json",
    f"{RUNTIME_SHORT_NAME}.jsonc",
)
RUNTIME_MODELS_FILE: Final[str] = "models.json"
OPENCODE_RUNTIME_CONFIG_FILE: Final[str] = f"{RUNTIME_FULL_NAME}.json"
OPENCODE_RUNTIME_CONFIG_RELATIVE: Final[str] = f".config/opencode/{OPENCODE_RUNTIME_CONFIG_FILE}"

# Words a user types to name the runtime. Routing reads these as cues only.
RUNTIME_USER_NAMES: Final[tuple[str, ...]] = (
    RUNTIME_SHORT_NAME,
    RUNTIME_FULL_NAME.replace("-", " "),
    RUNTIME_FULL_NAME,
    "open" + "agent runtime",
)
RUNTIME_USER_PHRASE: Final[str] = f"{RUNTIME_SHORT_NAME} runtime"

# Ids older OMH releases wrote for this runtime. Read-only aliases: a record
# or setting that still carries one loads as `pi-family-runtime`, and nothing
# writes them again.
LEGACY_RUNTIME_PROFILE_ID: Final[str] = f"{RUNTIME_SHORT_NAME}-runtime"
LEGACY_RUNTIME_PROGRESS_LANE: Final[str] = f"{RUNTIME_SHORT_NAME}_runtime"
