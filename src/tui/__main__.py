"""`python -m omh.tui`: the OMH TUI, as Hermes' launcher starts it through the frontend shim."""

from __future__ import annotations

import os

from .app import main

if __name__ == "__main__":
    raise SystemExit(main(os.environ))
