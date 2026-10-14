"""Bare `hermes` is stock Hermes again: setup and update take back what earlier versions wrote.

Every case runs the real CLI against throwaway homes. An install made before
the display release is rebuilt by running today's setup and then rewriting
the home the way the old version left it (`_legacy_display`): the branded
display keys in the config, the write record naming them without the release
marker, the status widget and every skin file with their manifests.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from _cli_harness import run_cli
from _legacy_display import brand_legacy_home, seed_legacy_widget
from _platform_support import requires_posix_permissions, requires_symlinks
from omh.install.config_adapter import external_dirs, memory_provider_selection, plugin_omh_home_setting
from omh.install.config_reversal import MANAGED_CONFIG_WRITES_STATE_KEY
from omh.skin_pack import SKIN_THEMES, read_theme_choice, skin_payload, theme_for_name, write_theme_choice


# A config with a fallback provider and a compression provider, so the
# compression defaults have a chain to write: every key the registration pass
# manages then lands.
BEFORE = (
    "version: 1\nmodel:\n  fallback_providers:\n    - provider: zai\n      model: glm-5.3\n"
    "auxiliary:\n  compression:\n    provider: og\n"
)


class _Home:
    """One primary Hermes home and its OMH store under a temp root."""

    def __init__(self, root: Path) -> None:
        self.omh_home = root / ".omh"
        self.hermes_home = root / ".hermes"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.config = self.hermes_home / "config.yaml"
        self.base = ["--omh-home", str(self.omh_home), "--hermes-home", str(self.hermes_home)]

    @property
    def state_path(self) -> Path:
        return self.omh_home / "runtime" / "state.json"

    def run(self, *argv: str, output_json: bool = True) -> tuple[int, str, str]:
        return run_cli([*self.base, *argv], output_json=output_json)

    def record(self, config: Path | None = None) -> dict[str, object]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return state[MANAGED_CONFIG_WRITES_STATE_KEY][str((config or self.config).resolve())]

    def brand(self, config: Path, skin: str = "omh") -> None:
        brand_legacy_home(self.omh_home, config, skin)

    def legacy_install(self, before: str = BEFORE, skin: str = "omh") -> None:
        self.config.write_text(before, encoding="utf-8", newline="")
        status, _stdout, stderr = self.run("setup", "--with-plugin")
        assert status == 0, stderr
        self.brand(self.config, skin)


def _omh_skin_files(hermes_home: Path) -> list[str]:
    skins = hermes_home / "skins"
    return sorted(path.name for path in skins.glob("*.yaml")) if skins.is_dir() else []


def _assert_every_managed_key_landed(case: unittest.TestCase, home: _Home, config_text: str) -> None:
    """Only the screen goes: each non-display key in `_apply_result`'s chain is in the config.

    The chain hands each step the previous step's text, so a step re-linked
    to the wrong text drops every key written before it.
    """
    case.assertIn((home.omh_home / "skills").resolve().as_posix(), external_dirs(config_text))
    case.assertIn("fallback_chain:", config_text)
    case.assertIn("  enabled:\n    - omh\n", config_text)
    case.assertEqual(memory_provider_selection(config_text), "omh")
    case.assertEqual(Path(plugin_omh_home_setting(config_text)), home.omh_home.resolve())


class FreshInstallTests(unittest.TestCase):
    def test_setup_writes_no_display_key_and_installs_no_widget_or_skin(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.config.write_text(BEFORE, encoding="utf-8", newline="")

            status, stdout, stderr = home.run("setup", "--with-plugin", "--yes")

            self.assertEqual(status, 0, stderr)
            config_text = home.config.read_text(encoding="utf-8")
            self.assertNotIn("display:", config_text)
            _assert_every_managed_key_landed(self, home, config_text)
            self.assertFalse((home.hermes_home / "tui-widgets").exists())
            self.assertFalse((home.hermes_home / "skins").exists())
            self.assertIsNone(read_theme_choice(home.omh_home))
            self.assertNotIn("tui_verdict", json.loads(stdout))

    def test_setup_reports_config_changed_when_only_plugin_enablement_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            self.assertEqual(home.run("setup")[0], 0)
            home.config.write_text(
                home.config.read_text(encoding="utf-8").replace("plugins:\n  enabled:\n    - omh\n", "plugins:\n"),
                encoding="utf-8",
                newline="",
            )

            status, stdout, stderr = home.run("setup")

            self.assertEqual(status, 0, stderr)
            apply = json.loads(stdout)["steps"]["apply"]
            self.assertTrue(apply["plugin_enabled"]["changed"])
            self.assertTrue(apply["changed"])

    def test_no_omh_tui_is_still_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))

            self.assertEqual(home.run("setup", "--no-omh-tui")[0], 0)
            self.assertEqual(home.run("update", "--no-omh-tui", "--yes")[0], 0)

            self.assertNotIn("display:", home.config.read_text(encoding="utf-8"))


class UpdateReleaseTests(unittest.TestCase):
    def test_update_returns_a_branded_install_to_stock_hermes(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            self.assertIn("  interface: tui\n", home.config.read_text(encoding="utf-8"))

            status, _stdout, stderr = home.run("update")

            self.assertEqual(status, 0, stderr)
            config_text = home.config.read_text(encoding="utf-8")
            self.assertNotIn("display", config_text)
            _assert_every_managed_key_landed(self, home, config_text)
            self.assertFalse((home.hermes_home / "tui-widgets" / "omh-status.mjs").exists())
            self.assertFalse((home.hermes_home / "tui-widgets" / ".omh-status.manifest.json").exists())
            self.assertEqual(_omh_skin_files(home.hermes_home), [])
            self.assertFalse((home.hermes_home / "skins" / ".omh-skin.manifest.json").exists())
            # The `omh` setup wrote by itself is not a theme anybody picked.
            self.assertIsNone(read_theme_choice(home.omh_home))
            record = home.record()
            self.assertEqual(record["restores"], {})
            self.assertFalse({"display.interface", "display.skin", "display.sections"} & set(record["keys"]))  # type: ignore[arg-type]
            self.assertIn("display_released_at", record)

    def test_update_carries_a_chosen_theme_into_the_omh_store(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(skin="omh-crimson")

            self.assertEqual(home.run("update")[0], 0)

            self.assertEqual(read_theme_choice(home.omh_home), theme_for_name("crimson"))
            self.assertNotIn("skin:", home.config.read_text(encoding="utf-8"))
            status, stdout, _stderr = home.run("theme", "status")
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout)["active_theme"], "crimson")

    def test_a_theme_saved_after_the_old_install_wins_over_the_config_skin(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(skin="omh-crimson")
            mono = theme_for_name("mono")
            assert mono is not None
            write_theme_choice(home.omh_home, mono)

            self.assertEqual(home.run("update")[0], 0)

            self.assertEqual(read_theme_choice(home.omh_home), mono)

    def test_update_puts_back_the_display_values_consent_replaced(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(before="version: 1\ndisplay:\n  interface: cli\n  skin: ares\n")
            self.assertIn("  skin: omh\n", home.config.read_text(encoding="utf-8"))

            self.assertEqual(home.run("update")[0], 0)

            config_text = home.config.read_text(encoding="utf-8")
            self.assertIn("display:\n  interface: cli\n  skin: ares\n", config_text)
            self.assertNotIn("collapsed", config_text)

    def test_a_value_the_person_changed_after_omh_wrote_it_is_left_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            home.config.write_text(
                home.config.read_text(encoding="utf-8")
                .replace("  interface: tui\n", "  interface: cli\n")
                .replace("    tools: collapsed\n", "    tools: expanded\n"),
                encoding="utf-8",
                newline="",
            )

            self.assertEqual(home.run("update")[0], 0)

            config_text = home.config.read_text(encoding="utf-8")
            self.assertIn("  interface: cli\n", config_text)
            self.assertIn("    tools: expanded\n", config_text)
            self.assertNotIn("skin:", config_text)
            self.assertNotIn("collapsed", config_text)

    def test_an_install_from_before_the_record_loses_its_omh_skin_only(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(skin="omh-mono")
            state = json.loads(home.state_path.read_text(encoding="utf-8"))
            del state[MANAGED_CONFIG_WRITES_STATE_KEY]
            home.state_path.write_text(json.dumps(state), encoding="utf-8", newline="")

            self.assertEqual(home.run("update")[0], 0)

            config_text = home.config.read_text(encoding="utf-8")
            self.assertNotIn("skin:", config_text)
            # The record-less skin is OMH's by its name, and its theme is
            # carried before the key goes.
            self.assertEqual(read_theme_choice(home.omh_home), theme_for_name("mono"))
            # `tui` and `collapsed` are values a person can hold; with no
            # record nothing says OMH wrote them, so they stay.
            self.assertIn("  interface: tui\n", config_text)
            self.assertIn("    thinking: collapsed\n", config_text)

    def test_a_persons_own_display_choices_survive_update(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.config.write_text(
                "version: 1\ndisplay:\n  interface: cli\n  skin: ares\n", encoding="utf-8", newline=""
            )
            self.assertEqual(home.run("setup", "--with-plugin")[0], 0)
            state = json.loads(home.state_path.read_text(encoding="utf-8"))
            del state[MANAGED_CONFIG_WRITES_STATE_KEY]
            home.state_path.write_text(json.dumps(state), encoding="utf-8", newline="")

            self.assertEqual(home.run("update")[0], 0)

            self.assertIn("display:\n  interface: cli\n  skin: ares\n", home.config.read_text(encoding="utf-8"))

    def test_a_skin_file_the_person_edited_stays_and_is_named(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            edited = home.hermes_home / "skins" / "omh-amber.yaml"
            edited.write_text(edited.read_text(encoding="utf-8") + "# mine\n", encoding="utf-8")

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertEqual(_omh_skin_files(home.hermes_home), ["omh-amber.yaml"])
            self.assertIn(f"kept {edited.resolve()}", stdout)
            self.assertIn("bare `hermes` opens stock Hermes", stdout)

            # Said on the pass that records the release, not on every later one.
            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertTrue(edited.is_file())
            self.assertNotIn(str(edited.resolve()), stdout)
            self.assertNotIn("Hermes display:", stdout)

    def test_a_widget_manifest_left_without_its_widget_goes_without_a_kept_line(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            widget = home.hermes_home / "tui-widgets" / "omh-status.mjs"
            widget.unlink()

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertFalse((widget.parent / ".omh-status.manifest.json").exists())
            self.assertNotIn(str(widget.resolve()), stdout)
            self.assertNotIn("the OMH status widget", stdout)

    def test_a_widget_of_the_persons_own_beside_ours_stays(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            personal = home.hermes_home / "tui-widgets" / "personal.mjs"
            personal.write_text("export default function register() {}\n", encoding="utf-8")

            self.assertEqual(home.run("update")[0], 0)

            self.assertFalse((home.hermes_home / "tui-widgets" / "omh-status.mjs").exists())
            self.assertEqual(personal.read_text(encoding="utf-8"), "export default function register() {}\n")

    @requires_posix_permissions
    def test_a_widget_that_cannot_be_unlinked_still_lets_the_record_land(self) -> None:
        # The release has already rewritten the config when the files go, so
        # a file that will not unlink is reported, not raised: raising there
        # skipped the record write, and the next update read the released
        # keys as never released.
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            widgets = home.hermes_home / "tui-widgets"
            widgets.chmod(0o555)
            try:
                status, stdout, stderr = home.run("update", output_json=False)
            finally:
                widgets.chmod(0o755)

            self.assertEqual(status, 0, stderr)
            self.assertIn("left the status widget in place:", stdout)
            self.assertTrue((widgets / "omh-status.mjs").is_file())
            self.assertIn("display_released_at", home.record())
            self.assertNotIn("display", home.config.read_text(encoding="utf-8"))

            # Once the directory is writable again, the next update removes it and says so.
            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertFalse((widgets / "omh-status.mjs").exists())
            self.assertIn("removed the OMH status widget", stdout)

    def test_a_refused_release_is_not_recorded_and_keeps_the_skin_file_it_names(self) -> None:
        # The display editor refuses a whole file over a quoted key or an
        # anchor anywhere in it. Recording the release then would make OMH's
        # keys the person's for good, and removing omh-crimson.yaml would
        # leave Hermes naming a skin that is not there.
        for name, extra in (
            ("quoted key", 'aliases:\n  "fast/model": glm-5.3\n'),
            ("anchor", "defaults: &defaults\n  retries: 1\n"),
        ):
            with self.subTest(shape=name), TemporaryDirectory() as tmp:
                home = _Home(Path(tmp))
                home.legacy_install(skin="omh-crimson")
                shaped = home.config.read_text(encoding="utf-8") + extra
                home.config.write_text(shaped, encoding="utf-8", newline="")

                status, stdout, stderr = home.run("update", output_json=False)

                self.assertEqual(status, 0, stderr)
                self.assertEqual(home.config.read_text(encoding="utf-8"), shaped)
                record = home.record()
                self.assertNotIn("display_released_at", record)
                self.assertLessEqual({"display.interface", "display.skin", "display.sections"}, set(record["keys"]))  # type: ignore[arg-type]
                self.assertEqual(_omh_skin_files(home.hermes_home), ["omh-crimson.yaml"])
                self.assertFalse((home.hermes_home / "tui-widgets" / "omh-status.mjs").exists())
                self.assertIn("could not release display.skin (", stdout)
                self.assertNotIn("bare `hermes` opens stock Hermes", stdout)

                # The person fixes the shape; the next update releases and the file goes.
                home.config.write_text(shaped.replace(extra, ""), encoding="utf-8", newline="")
                self.assertEqual(home.run("update")[0], 0)

                self.assertNotIn("display", home.config.read_text(encoding="utf-8"))
                self.assertIn("display_released_at", home.record())
                self.assertEqual(_omh_skin_files(home.hermes_home), [])

    def test_a_second_update_changes_nothing_and_keeps_a_skin_chosen_since(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            self.assertEqual(home.run("update")[0], 0)
            # The person now picks an OMH palette for Hermes themselves.
            chosen = home.config.read_text(encoding="utf-8") + "display:\n  skin: omh-crimson\n"
            home.config.write_text(chosen, encoding="utf-8", newline="")

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertEqual(home.config.read_text(encoding="utf-8"), chosen)
            self.assertNotIn("bare `hermes` opens stock Hermes", stdout)
            self.assertIsNone(read_theme_choice(home.omh_home))

    def test_a_skin_file_copied_back_after_the_release_survives_later_updates(self) -> None:
        # The release removed every OMH skin file and its manifest. A copy of
        # the shipped bytes put back afterwards matches the template, which
        # proved ownership before the release; after it, only a manifest
        # record does, so the person's copy stays whether or not
        # `display.skin` names it.
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            self.assertEqual(home.run("update")[0], 0)
            self.assertEqual(_omh_skin_files(home.hermes_home), [])
            for theme in ("omh-crimson", "omh-amber"):
                (home.hermes_home / "skins" / f"{theme}.yaml").write_bytes(skin_payload(theme))
            chosen = home.config.read_text(encoding="utf-8") + "display:\n  skin: omh-crimson\n"
            home.config.write_text(chosen, encoding="utf-8", newline="")

            for _ in range(2):
                status, stdout, stderr = home.run("update", output_json=False)

                self.assertEqual(status, 0, stderr)
                self.assertEqual(_omh_skin_files(home.hermes_home), ["omh-amber.yaml", "omh-crimson.yaml"])
                self.assertEqual(home.config.read_text(encoding="utf-8"), chosen)

    def test_a_theme_store_it_cannot_write_holds_only_the_release(self) -> None:
        # The failure used to raise out of the whole registration pass, and
        # update swallowed it, so plugin enablement and the memory provider
        # were silently not carried either.
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(skin="omh-crimson")
            (home.omh_home / "tui").write_text("not a directory\n", encoding="utf-8")
            home.config.write_text(
                home.config.read_text(encoding="utf-8")
                .replace("plugins:\n  enabled:\n    - omh\n", "plugins:\n")
                .replace("memory:\n  provider: omh\n", ""),
                encoding="utf-8",
                newline="",
            )

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            config_text = home.config.read_text(encoding="utf-8")
            _assert_every_managed_key_landed(self, home, config_text)
            self.assertIn("  skin: omh-crimson\n", config_text)
            self.assertIn("  interface: tui\n", config_text)
            self.assertNotIn("display_released_at", home.record())
            self.assertEqual(len(_omh_skin_files(home.hermes_home)), len(SKIN_THEMES))
            self.assertTrue((home.hermes_home / "tui-widgets" / "omh-status.mjs").is_file())
            self.assertIn("could not save OMH theme crimson to", stdout)
            self.assertNotIn("bare `hermes` opens stock Hermes", stdout)

            (home.omh_home / "tui").unlink()
            self.assertEqual(home.run("update")[0], 0)

            self.assertNotIn("display", home.config.read_text(encoding="utf-8"))
            self.assertEqual(read_theme_choice(home.omh_home), theme_for_name("crimson"))
            self.assertEqual(_omh_skin_files(home.hermes_home), [])

    def test_a_dry_run_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(skin="omh-amber")
            config_before = home.config.read_text(encoding="utf-8")
            state_before = home.state_path.read_text(encoding="utf-8")

            status, _stdout, stderr = home.run("update", "--dry-run")

            self.assertEqual(status, 0, stderr)
            self.assertEqual(home.config.read_text(encoding="utf-8"), config_before)
            self.assertEqual(home.state_path.read_text(encoding="utf-8"), state_before)
            self.assertTrue((home.hermes_home / "tui-widgets" / "omh-status.mjs").exists())
            self.assertEqual(len(_omh_skin_files(home.hermes_home)), len(SKIN_THEMES))
            self.assertIsNone(read_theme_choice(home.omh_home))

    def test_uninstall_after_the_migration_restores_the_config_before_setup(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            before = "version: 1\ndisplay:\n  interface: cli\n  compact: true\n"
            home.legacy_install(before=before)
            self.assertEqual(home.run("update")[0], 0)

            status, stdout, stderr = home.run("uninstall")

            self.assertEqual(status, 0, stderr)
            self.assertEqual(home.config.read_text(encoding="utf-8"), before)
            rows = {row["key"]: row["status"] for row in json.loads(stdout)["config_keys"]}
            self.assertEqual(rows["display.skin"], "absent")
            self.assertEqual(rows["display.interface"], "kept")

    def test_setup_runs_the_same_release(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(skin="omh-mono")

            status, stdout, stderr = home.run("setup", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertNotIn("display", home.config.read_text(encoding="utf-8"))
            self.assertEqual(_omh_skin_files(home.hermes_home), [])
            self.assertEqual(read_theme_choice(home.omh_home), theme_for_name("mono"))
            self.assertIn("saved OMH theme mono for the OMH TUI", stdout)

    def test_apply_runs_the_same_release_and_says_so(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install(skin="omh-mono")

            status, stdout, stderr = home.run("apply", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertNotIn("display", home.config.read_text(encoding="utf-8"))
            self.assertIn("Hermes display: bare `hermes` opens stock Hermes", stdout)
            self.assertIn("saved OMH theme mono for the OMH TUI", stdout)


class UninstallTests(unittest.TestCase):
    def test_uninstall_removes_only_the_widget_an_earlier_version_installed(self) -> None:
        # For an install update never reached: uninstall still takes the
        # widget back, by its manifest, and leaves a widget of the person's.
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            self.assertEqual(home.run("setup")[0], 0)
            destination = seed_legacy_widget(home.hermes_home)
            unrelated = destination.parent / "personal.mjs"
            unrelated.write_text("personal\n", encoding="utf-8")

            status, stdout, stderr = home.run("uninstall", "--all", "--keep-command")

            self.assertEqual((status, stderr), (0, ""))
            self.assertFalse(destination.exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "personal\n")
            self.assertEqual(json.loads(stdout)["tui_widget"]["status"], "removed")

    @requires_symlinks
    def test_a_widget_it_must_refuse_to_remove_does_not_end_uninstall_halfway(self) -> None:
        # The refusal used to escape as a traceback after the config was
        # reversed and the plugin removed, skipping the profiles and the
        # command package. It is a kept path now, primary and profile alike.
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            home = _Home(root)
            profile = home.hermes_home / "profiles" / "bot"
            profile.mkdir(parents=True)
            home.legacy_install()
            home.brand(profile / "config.yaml")
            self.assertEqual(home.run("update")[0], 0)
            for name, hermes_home in (("primary", home.hermes_home), ("bot", profile)):
                elsewhere = root / f"elsewhere-{name}"
                seed_legacy_widget(elsewhere)
                (hermes_home / "tui-widgets").rmdir()  # the update emptied it
                (hermes_home / "tui-widgets").symlink_to(elsewhere / "tui-widgets", target_is_directory=True)

            status, stdout, stderr = home.run("uninstall", "--keep-command")

            self.assertEqual((status, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual(payload["tui_widget"]["status"], "refused")
            self.assertIn("refusing symlinked widget path", payload["tui_widget"]["reason"])
            (bot,) = payload["hermes_profiles"]
            self.assertIn(str(profile / "tui-widgets"), [row["path"] for row in bot["kept_paths"]])
            self.assertEqual(home.config.read_text(encoding="utf-8"), BEFORE)
            self.assertFalse((home.hermes_home / "plugins" / "omh").exists())
            self.assertFalse((profile / "plugins" / "omh").exists())
            self.assertTrue((root / "elsewhere-primary" / "tui-widgets" / "omh-status.mjs").is_file())

    def test_uninstall_after_the_release_leaves_a_skin_file_the_person_put_back(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            home.legacy_install()
            self.assertEqual(home.run("update")[0], 0)
            for theme in ("omh-crimson", "omh-amber"):
                (home.hermes_home / "skins" / f"{theme}.yaml").write_bytes(skin_payload(theme))
            home.config.write_text(
                home.config.read_text(encoding="utf-8") + "display:\n  skin: omh-crimson\n", encoding="utf-8", newline=""
            )

            status, _stdout, stderr = home.run("uninstall", "--keep-command")

            self.assertEqual((status, stderr), (0, ""))
            self.assertEqual(_omh_skin_files(home.hermes_home), ["omh-amber.yaml", "omh-crimson.yaml"])
            self.assertIn("  skin: omh-crimson\n", home.config.read_text(encoding="utf-8"))


class ProfileReleaseTests(unittest.TestCase):
    def test_every_profile_is_released_and_its_theme_lands_in_its_own_store(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = _Home(root)
            shared = home.hermes_home / "profiles" / "shared"
            split = home.hermes_home / "profiles" / "split"
            split_store = root / "split-store"
            for profile in (shared, split):
                profile.mkdir(parents=True)
            # The split profile names its own store (#1679, #1680); the
            # shared one names none and is given the primary's.
            (split / "config.yaml").write_text(
                f"plugins:\n  entries:\n    omh:\n      settings:\n        omh_home: {split_store.as_posix()}\n",
                encoding="utf-8",
                newline="",
            )
            home.legacy_install()
            home.brand(shared / "config.yaml", skin="omh-amber")
            home.brand(split / "config.yaml", skin="omh-crimson")

            status, stdout, stderr = home.run("update")

            self.assertEqual(status, 0, stderr)
            for profile in (shared, split):
                with self.subTest(profile=profile.name):
                    self.assertNotIn("display", (profile / "config.yaml").read_text(encoding="utf-8"))
                    self.assertFalse((profile / "tui-widgets" / "omh-status.mjs").exists())
                    self.assertEqual(_omh_skin_files(profile), [])
                    self.assertIn("display_released_at", home.record(profile / "config.yaml"))
            # The primary's `omh` is the default setup wrote, not a pick, so
            # the amber the shared profile picked is what its store keeps.
            self.assertEqual(read_theme_choice(home.omh_home), theme_for_name("amber"))
            self.assertEqual(read_theme_choice(split_store), theme_for_name("crimson"))

    def test_two_picks_in_one_store_keep_the_first_and_name_the_other(self) -> None:
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            shared = home.hermes_home / "profiles" / "shared"
            shared.mkdir(parents=True)
            home.legacy_install(skin="omh-crimson")
            home.brand(shared / "config.yaml", skin="omh-amber")

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertEqual(read_theme_choice(home.omh_home), theme_for_name("crimson"))
            self.assertIn("saved OMH theme crimson for the OMH TUI", stdout)
            self.assertIn("kept the saved OMH theme crimson over amber, which display.skin named", stdout)

    def test_a_profile_whose_env_names_its_store_gets_no_theme_in_the_primarys(self) -> None:
        # OMH reads the names in a `.env`, never the values, so it cannot
        # tell where this profile's theme belongs; the primary's store would
        # be the wrong place, since the primary would then open with it.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = _Home(root)
            bot = home.hermes_home / "profiles" / "bot"
            bot.mkdir(parents=True)
            (bot / ".env").write_text(f"OMH_HOME={(root / 'env-store').as_posix()}\n", encoding="utf-8")
            home.legacy_install()
            home.brand(bot / "config.yaml", skin="omh-amber")

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertIsNone(read_theme_choice(home.omh_home))
            self.assertIn("did not save OMH theme amber: this home's .env names its OMH home", stdout)
            self.assertNotIn("display", (bot / "config.yaml").read_text(encoding="utf-8"))

    def test_a_home_left_unregistered_keeps_its_config_and_loses_the_old_files(self) -> None:
        # Registering is what carries the config release, so an opted-out
        # home keeps its config byte for byte. The widget and the skins its
        # config does not name are OMH's own files and still go, primary and
        # profile alike.
        with TemporaryDirectory() as tmp:
            home = _Home(Path(tmp))
            bot = home.hermes_home / "profiles" / "bot"
            bot.mkdir(parents=True)
            home.legacy_install(skin="omh-crimson")
            home.brand(bot / "config.yaml", skin="omh-amber")
            skills = (home.omh_home / "skills").resolve().as_posix()
            for config in (home.config, bot / "config.yaml"):
                config.write_text(
                    config.read_text(encoding="utf-8").replace(f"    - {skills}\n", ""), encoding="utf-8", newline=""
                )
            before = {config: config.read_bytes() for config in (home.config, bot / "config.yaml")}

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertIn("Bot profiles: bot (left unregistered)", stdout)
            self.assertEqual({config: config.read_bytes() for config in before}, before)
            for hermes_home, kept in ((home.hermes_home, "omh-crimson.yaml"), (bot, "omh-amber.yaml")):
                with self.subTest(home=hermes_home.name):
                    self.assertFalse((hermes_home / "tui-widgets" / "omh-status.mjs").exists())
                    self.assertEqual(_omh_skin_files(hermes_home), [kept])
            self.assertIn("this home is not registered, so its config is left as it is", stdout)
            self.assertNotIn("bare `hermes` opens stock Hermes", stdout)

    @requires_symlinks
    def test_a_widget_it_must_refuse_to_remove_costs_no_profile_its_registration(self) -> None:
        # Removing the widget refuses a symlinked destination, as uninstall
        # does. The refusal is a line in the output: a bot that update
        # bootstraps still gets its plugin, its skills and its memory
        # provider, and its display keys still go.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = _Home(root)
            profile = home.hermes_home / "profiles" / "bot"
            profile.mkdir(parents=True)
            home.legacy_install()
            home.brand(profile / "config.yaml")
            # A bot with no bundle and no registration is a new bot to the
            # sync, so this run registers it from nothing.
            shutil.rmtree(profile / "plugins" / "omh")
            skills = (home.omh_home / "skills").resolve().as_posix()
            config = profile / "config.yaml"
            config.write_text(
                config.read_text(encoding="utf-8").replace(f"    - {skills}\n", ""), encoding="utf-8", newline=""
            )
            self.assertNotIn(skills, config.read_text(encoding="utf-8"))
            elsewhere = root / "elsewhere"
            shutil.move(str(profile / "tui-widgets"), elsewhere)
            (profile / "tui-widgets").symlink_to(elsewhere, target_is_directory=True)

            status, stdout, stderr = home.run("update", output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertIn("Bot profiles: bot (set up)", stdout)
            self.assertIn("left the status widget in place: refusing symlinked widget path", stdout)
            config_text = config.read_text(encoding="utf-8")
            self.assertIn(skills, config_text)
            self.assertIn("  provider: omh\n", config_text)
            self.assertNotIn("display", config_text)
            self.assertTrue((profile / "plugins" / "omh" / "plugin.yaml").is_file())
            self.assertTrue((elsewhere / "omh-status.mjs").is_file())


# The on-disk state the last release left, recorded from a real run of its own
# `omh setup` and `omh theme use` (the fixture names the tag, the commit and
# the commands), with the scratch root templated as `{ROOT}`. The widget
# bytes are not in it: the real file embeds the installing interpreter's
# absolute path. Ownership is the manifest's digest of whatever is on disk,
# so a stand-in with its own digest recorded takes the same branch.
UPGRADE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "upgrade_from_v3_0_2.json"


class UpgradeFromLastReleaseTests(unittest.TestCase):
    """A machine set up by v3.0.2, primary home and two bot profiles, comes through update clean.

    v3.0.2's, from the fixture: every config before and after its setup and
    `omh theme use`, the per-config write records, and the skin manifest.
    Today's: the plugin bundle and managed skills the configs point at (laid
    down by today's setup before the configs are put back), the rest of
    `state.json` around the records, the skin files (today's shipped bytes,
    beside v3.0.2's manifest), and the widget, a stand-in recorded under
    v3.0.2's widget manifest schema.
    """

    PRIMARY = "hermes/config.yaml"
    BOT = "hermes/profiles/bot/config.yaml"
    SHARED = "hermes/profiles/shared/config.yaml"

    def _restore_v3_0_2(self, root: Path) -> dict[str, object]:
        fixture = json.loads(UPGRADE_FIXTURE.read_text(encoding="utf-8"))

        def text(value: str) -> str:
            return value.replace("{ROOT}", root.as_posix())

        for rel, body in fixture["configs_before_setup"].items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text(body), encoding="utf-8", newline="")
        # Today's setup lays down what the configs point at (the bundle and
        # the skills); then every home is put back exactly as v3.0.2 left it.
        status, _stdout, stderr = run_cli(
            ["--omh-home", str(root / "omh"), "--hermes-home", str(root / "hermes"), "setup", "--with-plugin"]
        )
        assert status == 0, stderr
        for rel, body in fixture["configs"].items():
            (root / rel).write_text(text(body), encoding="utf-8", newline="")
        state_path = root / "omh" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state[MANAGED_CONFIG_WRITES_STATE_KEY] = {
            str((root / rel).resolve()): json.loads(text(json.dumps(record)))
            for rel, record in fixture["hermes_config_writes"].items()
        }
        state_path.write_text(json.dumps(state), encoding="utf-8", newline="")
        for home in (root / "hermes", root / "hermes" / "profiles" / "bot", root / "hermes" / "profiles" / "shared"):
            seed_legacy_widget(home)
            skins = home / "skins"
            skins.mkdir(parents=True, exist_ok=True)
            for theme in SKIN_THEMES:
                (skins / theme.filename).write_bytes(skin_payload(theme.skin_name))
            (skins / ".omh-skin.manifest.json").write_text(json.dumps(fixture["skin_manifest"]), encoding="utf-8")
        return fixture

    def test_update_releases_every_home_keeps_omh_working_and_a_second_run_changes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = self._restore_v3_0_2(root)
            base = ["--omh-home", str(root / "omh"), "--hermes-home", str(root / "hermes")]

            status, stdout, stderr = run_cli([*base, "update"], output_json=False)

            self.assertEqual(status, 0, stderr)
            primary = (root / self.PRIMARY).read_text(encoding="utf-8")
            bot = (root / self.BOT).read_text(encoding="utf-8")
            shared = (root / self.SHARED).read_text(encoding="utf-8")
            self.assertNotIn("display", primary)
            self.assertNotIn("display", bot)
            # The values consent replaced in the shared profile come back.
            self.assertIn("display:\n  interface: cli\n  skin: ares\n", shared)
            self.assertNotIn("collapsed", shared)
            # Only the screen goes: everything else v3.0.2 wrote is still there.
            for rel, after in ((self.PRIMARY, primary), (self.BOT, bot), (self.SHARED, shared)):
                with self.subTest(home=rel):
                    before = fixture["configs"][rel].replace("{ROOT}", root.as_posix())
                    self.assertEqual(external_dirs(after), external_dirs(before))
                    self.assertEqual(memory_provider_selection(after), "omh")
                    self.assertIn("  enabled:\n    - omh\n", after)
                    self.assertEqual(plugin_omh_home_setting(after), plugin_omh_home_setting(before))
                    home = (root / rel).parent
                    self.assertFalse((home / "tui-widgets" / "omh-status.mjs").exists())
                    self.assertFalse((home / "tui-widgets" / ".omh-status.manifest.json").exists())
                    self.assertEqual(_omh_skin_files(home), [])
                    self.assertFalse((home / "skins" / ".omh-skin.manifest.json").exists())
            self.assertIn("fallback_chain:", primary)
            # Each theme `omh theme use` set lands in the store its home reads.
            self.assertEqual(read_theme_choice(root / "omh"), theme_for_name("crimson"))
            self.assertEqual(read_theme_choice(root / "bot-store"), theme_for_name("amber"))
            self.assertEqual(stdout.count("bare `hermes` opens stock Hermes"), 3)
            state = json.loads((root / "omh" / "runtime" / "state.json").read_text(encoding="utf-8"))
            for rel in (self.PRIMARY, self.BOT, self.SHARED):
                with self.subTest(record=rel):
                    record = state[MANAGED_CONFIG_WRITES_STATE_KEY][str((root / rel).resolve())]
                    self.assertIn("display_released_at", record)
                    self.assertEqual(record["restores"], {})
                    self.assertFalse({"display.interface", "display.skin", "display.sections"} & set(record["keys"]))

            configs = {rel: (root / rel).read_bytes() for rel in (self.PRIMARY, self.BOT, self.SHARED)}
            status, stdout, stderr = run_cli([*base, "update"], output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertEqual({rel: (root / rel).read_bytes() for rel in configs}, configs)
            self.assertNotIn("bare `hermes` opens stock Hermes", stdout)

    def test_uninstall_after_the_upgrade_neither_refuses_nor_needs_force(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = self._restore_v3_0_2(root)
            base = ["--omh-home", str(root / "omh"), "--hermes-home", str(root / "hermes")]
            self.assertEqual(run_cli([*base, "update"])[0], 0)

            status, stdout, stderr = run_cli([*base, "uninstall", "--keep-command"])

            self.assertEqual((status, stderr), (0, ""))
            for rel, before in fixture["configs_before_setup"].items():
                with self.subTest(home=rel):
                    self.assertEqual(
                        (root / rel).read_text(encoding="utf-8"), before.replace("{ROOT}", root.as_posix())
                    )
            self.assertFalse((root / "hermes" / "plugins" / "omh").exists())


if __name__ == "__main__":
    unittest.main()
