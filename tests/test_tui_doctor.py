"""`omh doctor`'s `omh_tui_support` check: can bare `omh` open the OMH TUI here?"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from omh.maintenance.doctor import _omh_tui_check
from omh.paths import OmhPaths


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(omh_home=root / "omh", hermes_home=root / "hermes")


def _hermes_install(root: Path, *, hook: bool = True, gateway: bool = True) -> None:
    install = root / "hermes" / "hermes-agent"
    (install / "hermes_cli").mkdir(parents=True)
    launcher = 'ext_dir = os.environ.get("HERMES_TUI_DIR")\n' if hook else "def _launch_tui(): pass\n"
    (install / "hermes_cli" / "main_tui_launch.py").write_text(launcher, encoding="utf-8")
    if gateway:
        (install / "tui_gateway").mkdir()
        (install / "tui_gateway" / "entry.py").write_text("", encoding="utf-8")


class OmhTuiDoctorTests(unittest.TestCase):
    def test_all_parts_present_reports_the_omh_tui_reachable(self) -> None:
        with TemporaryDirectory() as tmp, patch("omh.tui.launch.node_available", return_value=True):
            _hermes_install(Path(tmp))
            check = _omh_tui_check(_paths(Path(tmp)))
        self.assertTrue(check.ok)
        self.assertEqual(check.severity, "ok")
        self.assertIn("can open the OMH TUI", check.message)

    def test_a_hermes_without_the_launcher_hook_warns_and_names_the_fallback(self) -> None:
        with TemporaryDirectory() as tmp, patch("omh.tui.launch.node_available", return_value=True):
            _hermes_install(Path(tmp), hook=False)
            check = _omh_tui_check(_paths(Path(tmp)))
        self.assertTrue(check.ok)
        self.assertEqual(check.severity, "warning")
        self.assertIn("HERMES_TUI_DIR", check.message)
        self.assertIn("Hermes' own terminal", check.next_action)

    def test_a_hermes_without_the_gateway_warns(self) -> None:
        with TemporaryDirectory() as tmp, patch("omh.tui.launch.node_available", return_value=True):
            _hermes_install(Path(tmp), gateway=False)
            check = _omh_tui_check(_paths(Path(tmp)))
        self.assertEqual(check.severity, "warning")

    def test_no_node_warns(self) -> None:
        with TemporaryDirectory() as tmp, patch("omh.tui.launch.node_available", return_value=False):
            _hermes_install(Path(tmp))
            check = _omh_tui_check(_paths(Path(tmp)))
        self.assertEqual(check.severity, "warning")
        self.assertIn("Node", check.message)

    def test_a_missing_hermes_install_is_not_observed(self) -> None:
        with TemporaryDirectory() as tmp:
            check = _omh_tui_check(_paths(Path(tmp)))
        self.assertTrue(check.ok)
        self.assertFalse(check.observed)

    def test_a_missing_shim_warns_before_anything_else(self) -> None:
        with TemporaryDirectory() as tmp, patch("omh.tui.launch.FRONTEND_ENTRY", Path(tmp) / "missing.js"):
            check = _omh_tui_check(_paths(Path(tmp)))
        self.assertEqual(check.severity, "warning")
        self.assertIn("shim", check.message)


if __name__ == "__main__":
    unittest.main()
