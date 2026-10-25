"""`omh setup` records the release that is installed, not the `preview` default.

The documented flow is the curl installer (stable by default, the release
wheel) followed by a plain `omh setup`. install.sh hands setup the resolved
channel, version, and package URL only under `OMH_RUN_SETUP=1`, and setup's
`--channel` defaulted to `preview`, so on 2026-10-25 a sandboxed stable install
of 4.0.2 recorded `release_channel=preview`, the `main` archive URL,
`source_ref=main`, and an empty version. `omh update --dry-run` then planned
`main.zip -> main.zip`. The agent guide's SHA-pinned archive lost its commit
the same way.

pip and uv write a PEP 610 `direct_url.json` naming exactly what they
installed. These tests hand setup that record for every install path and pin
what lands in state.json.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from _cli_harness import run_cli
from omh.version import __version__

COMMIT = "4a05e87a85847df10db59a840b88e813f30a9f84"
WHEEL_URL = "https://github.com/rlaope/oh-my-hermes/releases/download/v4.0.2/oh_my_hermes-4.0.2-py3-none-any.whl"
TAG_ARCHIVE_URL = "https://github.com/rlaope/oh-my-hermes/archive/refs/tags/v4.0.2.zip"
MAIN_URL = "https://github.com/rlaope/oh-my-hermes/archive/refs/heads/main.zip"
COMMIT_URL = f"https://github.com/rlaope/oh-my-hermes/archive/{COMMIT}.zip"
REPO_URL = "https://github.com/rlaope/oh-my-hermes"
ARCHIVE_INFO = {"hash": "sha256=" + "0" * 64, "hashes": {"sha256": "0" * 64}}


def _archive(url: str) -> dict[str, object]:
    return {"url": url, "archive_info": ARCHIVE_INFO}


def _git(revision: str | None) -> dict[str, object]:
    vcs: dict[str, object] = {"vcs": "git", "commit_id": COMMIT}
    if revision is not None:
        vcs["requested_revision"] = revision
    return {"url": REPO_URL, "vcs_info": vcs}


class _Distribution:
    def __init__(self, direct_url: dict[str, object] | None) -> None:
        self._text = json.dumps(direct_url) if direct_url is not None else None

    def read_text(self, filename: str) -> str | None:
        return self._text if filename == "direct_url.json" else None


def _recorded(direct_url: dict[str, object] | None, *extra: str, manager: str = "", command: str = "setup") -> dict[str, object]:
    """Run one command against a fake installed distribution; return the recorded release."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        argv = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes"), command]
        if command == "setup":
            argv.append("--no-menubar")
        with patch.dict(os.environ, {"OMH_COMMAND_PACKAGE_MANAGER": manager, "OMH_MENUBAR": "0"}), patch(
            "importlib.metadata.distribution", return_value=_Distribution(direct_url)
        ):
            status, stdout, stderr = run_cli([*argv, *extra])
        if status != 0:
            raise AssertionError(f"{command} exited {status}: {stderr or stdout}")
        state = json.loads((root / ".omh" / "runtime" / "state.json").read_text(encoding="utf-8"))
    return {key: state[key] for key in ("release_channel", "release_version", "release_package_url", "release_source_ref")}


def _release(channel: str, version: str, url: str, ref: str) -> dict[str, object]:
    return {"release_channel": channel, "release_version": version, "release_package_url": url, "release_source_ref": ref}


class SetupRecordsInstalledReleaseTests(unittest.TestCase):
    def test_stable_release_wheel_install(self) -> None:
        # The curl and PowerShell installers' default: the newest release wheel.
        self.assertEqual(_recorded(_archive(WHEEL_URL)), _release("stable", "4.0.2", WHEEL_URL, "v4.0.2"))

    def test_stable_tag_archive_install(self) -> None:
        # The documented fallback for a tag with no published wheel.
        self.assertEqual(_recorded(_archive(TAG_ARCHIVE_URL)), _release("stable", "4.0.2", TAG_ARCHIVE_URL, "v4.0.2"))

    def test_preview_main_archive_install(self) -> None:
        self.assertEqual(_recorded(_archive(MAIN_URL)), _release("preview", "", MAIN_URL, "main"))

    def test_sha_pinned_archive_install_keeps_its_commit(self) -> None:
        # INSTALL_FOR_AGENTS.md pins the installer and the archive to one SHA.
        self.assertEqual(_recorded(_archive(COMMIT_URL)), _release("preview", "", COMMIT_URL, COMMIT))

    def test_pip_and_uv_git_installs_keep_their_commit(self) -> None:
        # `pip install git+...@<sha>` and `uv tool install git+...@<sha>` write
        # the same PEP 610 shape; uv omits requested_revision when none was named.
        for revision in (COMMIT, "main", None):
            with self.subTest(requested_revision=revision):
                self.assertEqual(
                    _recorded(_git(revision)),
                    _release("preview", "", f"git+{REPO_URL}@{COMMIT}", COMMIT),
                )

    def test_git_install_of_a_release_tag_is_stable(self) -> None:
        self.assertEqual(_recorded(_git("v4.0.2")), _release("stable", "4.0.2", f"git+{REPO_URL}@v4.0.2", "v4.0.2"))

    def test_package_manager_installs_record_their_version_and_no_channel(self) -> None:
        # npm extracts the bundled wheel (no PEP 610 record); Homebrew pip-installs
        # it from its build directory (a `file://` record). Neither names a
        # channel, and npm ships stable and beta from the same wheel.
        for manager, record in (
            ("npm", None),
            ("bun", None),
            ("homebrew", _archive(f"file:///private/tmp/omh-20261025/oh_my_hermes-{__version__}-py3-none-any.whl")),
        ):
            with self.subTest(manager=manager):
                self.assertEqual(_recorded(record, manager=manager), _release("", __version__, "", ""))

    def test_an_explicit_release_still_wins(self) -> None:
        self.assertEqual(
            _recorded(_archive(MAIN_URL), "--channel", "stable", "--version", "4.0.1"),
            _release(
                "stable",
                "4.0.1",
                "https://github.com/rlaope/oh-my-hermes/releases/download/v4.0.1/oh_my_hermes-4.0.1-py3-none-any.whl",
                "v4.0.1",
            ),
        )
        self.assertEqual(
            _recorded(_archive(WHEEL_URL), "--channel", "preview"),
            _release("preview", "", MAIN_URL, "main"),
        )

    def test_an_explicit_source_ref_overrides_only_the_ref(self) -> None:
        self.assertEqual(
            _recorded(_archive(COMMIT_URL), "--source-ref", "main@4a05e87"),
            _release("preview", "", COMMIT_URL, "main@4a05e87"),
        )

    def test_an_unrecognized_url_is_recorded_without_a_channel(self) -> None:
        # A fork's wheel, a local wheel, or a local clone: the URL is a fact,
        # the channel is not.
        fork = "https://github.com/someone/oh-my-hermes/releases/download/v4.0.2/oh_my_hermes-4.0.2-py3-none-any.whl"
        for record in (_archive(fork), {"url": "file:///src/oh-my-hermes", "dir_info": {}}):
            with self.subTest(record=record):
                self.assertEqual(_recorded(record), _release("", "", str(record["url"]), "custom-url"))

    def test_an_install_that_records_nothing_keeps_the_preview_default(self) -> None:
        # No PEP 610 record at all, or an editable development checkout, says
        # nothing a channel can be read from; this is the long-standing default.
        for record in (None, {"url": "file:///dev/checkout", "dir_info": {"editable": True}}):
            with self.subTest(record=record):
                self.assertEqual(_recorded(record), _release("preview", "", MAIN_URL, "main"))

    def test_update_dry_run_compares_the_installed_release(self) -> None:
        # The dry run used to read `main.zip -> main.zip` after a stable install.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            homes = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            with patch.dict(os.environ, {"OMH_COMMAND_PACKAGE_MANAGER": "", "OMH_MENUBAR": "0"}), patch(
                "importlib.metadata.distribution", return_value=_Distribution(_archive(WHEEL_URL))
            ):
                setup_status, _, setup_stderr = run_cli([*homes, "setup", "--no-menubar"])
                status, stdout, stderr = run_cli([*homes, "update", "--dry-run"])
        self.assertEqual((setup_status, status), (0, 0), setup_stderr or stderr)
        release_update = json.loads(stdout)["release_update"]
        expected = _release("stable", "4.0.2", WHEEL_URL, "v4.0.2")
        for side in ("previous", "current"):
            with self.subTest(side=side):
                self.assertEqual({key: release_update[side][key] for key in expected}, expected)


class UvToolUpdateGuidanceTests(unittest.TestCase):
    """`uv tool upgrade` cannot move a pinned uv tool install.

    Measured 2026-10-25 on uv 0.12.5 in a sandbox: `uv tool install
    git+https://github.com/rlaope/oh-my-hermes@<sha>` and `uv tool install
    <release wheel URL>` both answered `uv tool upgrade oh-my-hermes` with
    "Nothing to upgrade", while OMH told both to run exactly that.
    """

    def _guidance(self, record: dict[str, object]) -> tuple[str, str]:
        from omh.commands import setup as setup_commands

        with patch.dict(os.environ, {"OMH_COMMAND_PACKAGE_MANAGER": ""}), patch(
            "importlib.metadata.distribution", return_value=_Distribution(record)
        ), patch.object(setup_commands.sys, "prefix", "/home/u/.local/share/uv/tools/oh-my-hermes"):
            return setup_commands._command_package_update_guidance()

    def test_a_commit_or_tag_pinned_git_install_is_reinstalled_at_a_new_ref(self) -> None:
        for revision in (COMMIT, "v4.0.2"):
            with self.subTest(requested_revision=revision):
                self.assertEqual(
                    self._guidance(_git(revision)),
                    ("uv-tool", f'uv tool install --force "git+{REPO_URL}@<new-ref>"'),
                )

    def test_a_release_wheel_install_is_reinstalled_from_the_new_release(self) -> None:
        self.assertEqual(
            self._guidance(_archive(WHEEL_URL)),
            (
                "uv-tool",
                'uv tool install --force "https://github.com/rlaope/oh-my-hermes/releases/download/'
                'v<version>/oh_my_hermes-<version>-py3-none-any.whl"',
            ),
        )
        self.assertEqual(
            self._guidance(_archive(COMMIT_URL)),
            ("uv-tool", 'uv tool install --force "<new package URL>"'),
        )

    def test_a_branch_or_unpinned_git_install_keeps_the_upgrade_command(self) -> None:
        for revision in ("main", None):
            with self.subTest(requested_revision=revision):
                self.assertEqual(self._guidance(_git(revision)), ("uv-tool", "uv tool upgrade oh-my-hermes"))


class DirectUrlShapesTests(unittest.TestCase):
    def test_records_that_name_no_release_of_this_repository_are_not_guessed(self) -> None:
        from omh.release import release_from_direct_url

        for record in (
            None,
            [],
            {"url": WHEEL_URL},
            _archive("file:///tmp/oh_my_hermes-4.0.2-py3-none-any.whl"),
            _archive("https://github.com/someone/oh-my-hermes/releases/download/v4.0.2/oh_my_hermes-4.0.2-py3-none-any.whl"),
            _archive("https://github.com/rlaope/oh-my-hermes/releases/download/v4.0.2/oh_my_hermes-4.0.1-py3-none-any.whl"),
            _archive("https://github.com/rlaope/oh-my-hermes/archive/refs/heads/feature.zip"),
            _archive("https://github.com/rlaope/oh-my-hermes/archive/abc123.zip"),
            {"url": "https://github.com/someone/oh-my-hermes", "vcs_info": {"vcs": "git", "commit_id": COMMIT}},
            {"url": REPO_URL, "vcs_info": {"vcs": "git", "commit_id": "main"}},
            {"url": "file:///dev/checkout", "dir_info": {"editable": True}},
        ):
            with self.subTest(record=record):
                self.assertIsNone(release_from_direct_url(record))

    def test_a_dot_git_repository_url_is_the_same_repository(self) -> None:
        from omh.release import release_from_direct_url

        selection = release_from_direct_url({"url": f"{REPO_URL}.git", "vcs_info": {"vcs": "git", "commit_id": COMMIT}})
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual((selection.channel, selection.source_label), ("preview", COMMIT))


if __name__ == "__main__":
    unittest.main()
