"""`omh update` prints which `omh` package files it changed, git `--stat` style.

A staged update leaves the previous generation on disk and re-enters the new
one; the activation marker in `self-update.json` names both. Every case builds
that layout in a temporary command root: two generations, each with a
`venv/lib/python3.11/site-packages/omh` tree, and the marker the outer update
writes before it switches the pointer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from _cli_harness import run_cli

from omh.commands.update_file_changes import (
    GRAPH_WIDTH,
    ROW_LIMIT,
    _bar,
    generation_package_dir,
    package_file_changes,
    update_file_changes,
    update_file_changes_lines,
)

TITLE = "Files changed in the OMH package"


def _package(generation: Path) -> Path:
    package = generation / "venv" / "lib" / "python3.11" / "site-packages" / "omh"
    package.mkdir(parents=True)
    return package


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class CommandRoot:
    """A command root with a previous and a candidate generation."""

    def __init__(self, root: Path) -> None:
        self.root = root / "omh-command"
        self.previous = self.root / "generations" / "20261025000000000000-aaaaaaaaaa"
        self.candidate = self.root / "generations" / "20261027000000000000-bbbbbbbbbb"
        self.before = _package(self.previous)
        self.after = _package(self.candidate)
        (self.root / "venv").mkdir(parents=True)

    def mark_activation(self, candidate: Path | None = None) -> None:
        marker = {
            "candidate": str(candidate or self.candidate),
            "previous": str(self.previous),
            "marker_written_at": "2026-10-27T00:00:00Z",
            "phase": "post_switch",
        }
        _write(self.root / "self-update.json", json.dumps({"activation_in_progress": marker}))

    def reentry_env(self) -> dict[str, str]:
        return {
            "OMH_SELF_UPDATE_GENERATION": str(self.candidate),
            "OMH_UPDATE_COMMAND_PACKAGE_REENTERED": "1",
        }


class PackageFileChangesTests(unittest.TestCase):
    def test_rows_cover_modified_added_removed_and_binary_and_skip_bytecode(self) -> None:
        with TemporaryDirectory() as tmp:
            before, after = Path(tmp) / "a", Path(tmp) / "b"
            _write(before / "same.py", "x = 1\n")
            _write(after / "same.py", "x = 1\n")
            _write(before / "commands" / "setup.py", "a\nb\nc\n")
            _write(after / "commands" / "setup.py", "a\nB\nc\nd\n")
            _write(before / "gone.py", "1\n2\n")
            _write(after / "new.py", "1\n2\n3\n")
            (before / "logo.bin").write_bytes(b"\xff\x00")
            (after / "logo.bin").write_bytes(b"\xff\x01")
            _write(after / "__pycache__" / "setup.cpython-311.pyc", "cache")
            _write(after / "stray.pyc", "cache")

            rows = package_file_changes(before, after)

        self.assertEqual(
            rows,
            [
                {"path": "commands/setup.py", "binary": False, "insertions": 2, "deletions": 1},
                {"path": "gone.py", "binary": False, "insertions": 0, "deletions": 2},
                {"path": "logo.bin", "binary": True, "insertions": 0, "deletions": 0},
                {"path": "new.py", "binary": False, "insertions": 3, "deletions": 0},
            ],
        )


    def test_lines_are_counted_as_git_counts_them(self) -> None:
        cases = {
            "a removed line that reads like a diff header": ("a\n---\nb\n", "a\nb\n", (0, 1)),
            "an added line that reads like a diff header": ("a\n", "a\n++x\n+++\n", (2, 0)),
            "a new file": ("", "1\n2\n3\n", (3, 0)),
            "a final newline added": ("a", "a\n", (1, 1)),
            "a form feed is not a line break": ("a\x0cb\n", "a\x0cc\n", (1, 1)),
            "CRLF turned into LF": ("a\r\nb\r\n", "a\nb\n", (2, 2)),
        }
        for name, (old, new, expected) in cases.items():
            with self.subTest(name), TemporaryDirectory() as tmp:
                before, after = Path(tmp) / "a", Path(tmp) / "b"
                before.mkdir()
                after.mkdir()
                if old:
                    (before / "f.py").write_bytes(old.encode())
                (after / "f.py").write_bytes(new.encode())
                row = package_file_changes(before, after)[0]
                self.assertEqual((row["insertions"], row["deletions"]), expected)

    def test_utf8_with_a_nul_byte_is_binary(self) -> None:
        with TemporaryDirectory() as tmp:
            before, after = Path(tmp) / "a", Path(tmp) / "b"
            _write(before / "data.txt", "a\x00\n")
            _write(after / "data.txt", "b\x00\n")
            self.assertTrue(package_file_changes(before, after)[0]["binary"])

    def test_the_windows_venv_layout_is_found(self) -> None:
        with TemporaryDirectory() as tmp:
            generation = Path(tmp) / "g"
            package = generation / "venv" / "Lib" / "site-packages" / "omh"
            package.mkdir(parents=True)
            self.assertEqual(generation_package_dir(generation), package)


class UpdateFileChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = CommandRoot(Path(self._tmp.name))
        _write(self.layout.before / "cli.py", "old\n")
        _write(self.layout.after / "cli.py", "new\nline\n")

    def test_reentered_update_compares_the_marked_previous_generation(self) -> None:
        self.layout.mark_activation()

        payload = update_file_changes(self.layout.reentry_env())

        self.assertEqual(payload["status"], "observed")
        self.assertEqual(payload["from_generation"], self.layout.previous.name)
        self.assertEqual(payload["to_generation"], self.layout.candidate.name)
        self.assertEqual((payload["files_changed"], payload["insertions"], payload["deletions"]), (1, 2, 1))
        self.assertEqual(payload["files"][0]["path"], "cli.py")

    def test_anything_but_a_reentered_update_is_unavailable_with_its_reason(self) -> None:
        self.layout.mark_activation()
        env = self.layout.reentry_env()
        smoke = {"OMH_SELF_UPDATE_GENERATION": env["OMH_SELF_UPDATE_GENERATION"]}
        cases = {
            "not_a_staged_update": [{}, smoke],
            "rollback_restore": [dict(env, OMH_UPDATE_ROLLBACK_RESTORE="1")],
        }
        for reason, environments in cases.items():
            for environ in environments:
                with self.subTest(reason=reason, environ=sorted(environ)):
                    self.assertEqual(update_file_changes(environ), {"schema_version": "update_file_changes/v1", "status": "unavailable", "reason": reason})

    def test_a_marker_that_does_not_name_this_generation_is_refused(self) -> None:
        cases = {
            "self_update_state_unreadable": lambda: None,
            "activation_marker_missing": lambda: _write(self.layout.root / "self-update.json", json.dumps({"activation_in_progress": None})),
            "activation_marker_invalid": lambda: _write(self.layout.root / "self-update.json", json.dumps({"activation_in_progress": {"candidate": "/elsewhere/x", "previous": "/elsewhere/y", "phase": "post_switch"}})),
            "activation_marker_names_another_generation": lambda: self.layout.mark_activation(self.layout.previous),
        }
        for broken in ("[1]", "{trunc", "\xff"):
            cases[f"self_update_state_unreadable:{broken!r}"] = lambda broken=broken: (self.layout.root / "self-update.json").write_bytes(broken.encode("latin-1"))
        for reason, arrange in cases.items():
            with self.subTest(reason=reason):
                (self.layout.root / "self-update.json").unlink(missing_ok=True)
                arrange()
                self.assertEqual(update_file_changes(self.layout.reentry_env())["reason"], reason.partition(":")[0])

    def test_recover_known_good_is_a_downgrade_and_prints_no_stat(self) -> None:
        """`--recover-known-good` re-enters the older generation through the same
        marker shape, with the newer one as `previous`."""
        self.layout.mark_activation()
        state = json.loads((self.layout.root / "self-update.json").read_text(encoding="utf-8"))
        state["previous_known_good"] = {"id": self.layout.candidate.name, "kind": "generation", "path": str(self.layout.candidate), "version": ""}
        _write(self.layout.root / "self-update.json", json.dumps(state))

        self.assertEqual(update_file_changes(self.layout.reentry_env())["reason"], "known_good_recovery")


class UpdateFileChangesLinesTests(unittest.TestCase):
    def _payload(self, rows: list[dict]) -> dict:
        return {
            "status": "observed",
            "files": rows,
            "files_changed": len(rows),
            "insertions": sum(row["insertions"] for row in rows),
            "deletions": sum(row["deletions"] for row in rows),
        }

    def test_stat_is_capped_and_closes_with_the_full_totals(self) -> None:
        rows = [{"path": f"m{index:03}.py", "binary": False, "insertions": 2, "deletions": 1} for index in range(ROW_LIMIT + 5)]

        lines = update_file_changes_lines(self._payload(rows), language="en")

        self.assertEqual(lines[0], TITLE)
        self.assertEqual(lines[1], " m000.py | 3 ++-")
        self.assertEqual(len(lines), 1 + ROW_LIMIT + 2)
        self.assertEqual(lines[-2], " ... and 5 more files")
        self.assertEqual(lines[-1], f" {ROW_LIMIT + 5} files changed, {2 * (ROW_LIMIT + 5)} insertions(+), {ROW_LIMIT + 5} deletions(-)")

    def test_a_large_change_scales_the_graph_and_keeps_both_sides_of_a_small_one(self) -> None:
        rows = [
            {"path": "big.py", "binary": False, "insertions": 200, "deletions": 100},
            {"path": "small.py", "binary": False, "insertions": 1, "deletions": 1},
            {"path": "logo.bin", "binary": True, "insertions": 0, "deletions": 0},
        ]

        lines = update_file_changes_lines(self._payload(rows), language="en")

        self.assertEqual(lines[1], " big.py   | 300 " + "+" * 20 + "-" * 10)
        self.assertEqual(lines[2], " small.py |   2 +-")
        self.assertEqual(lines[3], " logo.bin | Bin")

    def test_the_graph_never_exceeds_its_width(self) -> None:
        widths = {
            sum(_bar(insertions, deletions, largest))
            for largest in range(GRAPH_WIDTH + 1, 120)
            for insertions in range(largest + 1)
            for deletions in [largest - insertions]
        }
        self.assertEqual(max(widths), GRAPH_WIDTH)
        self.assertEqual(_bar(2, 56, 58), (2, 28))
        self.assertEqual(_bar(1, 1, 300), (1, 1))

    def test_summary_is_singular_for_one_and_omits_an_empty_side(self) -> None:
        one = [{"path": "a.py", "binary": False, "insertions": 1, "deletions": 0}]
        self.assertEqual(update_file_changes_lines(self._payload(one), language="en")[-1], " 1 file changed, 1 insertion(+)")
        two = [{"path": "a.py", "binary": False, "insertions": 0, "deletions": 3}, {"path": "b.py", "binary": False, "insertions": 0, "deletions": 1}]
        self.assertEqual(update_file_changes_lines(self._payload(two), language="en")[-1], " 2 files changed, 4 deletions(-)")
        self.assertEqual(update_file_changes_lines(self._payload(two), language="ko")[-1], " 파일 2개 변경, 4줄 삭제(-)")

    def test_nothing_to_show_unless_observed(self) -> None:
        self.assertEqual(update_file_changes_lines({"status": "unavailable", "reason": "not_a_staged_update"}, language="en"), [])
        self.assertEqual(update_file_changes_lines(self._payload([]), language="en"), [TITLE, "  No package files changed."])


class UpdateCommandPrintsFileChangesTests(unittest.TestCase):
    def test_reentered_update_prints_the_stat_and_carries_it_in_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = CommandRoot(root)
            _write(layout.before / "commands" / "setup.py", "a\n")
            _write(layout.after / "commands" / "setup.py", "a\nb\n")
            layout.mark_activation()
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            with mock.patch.dict(os.environ, {"OMH_VENV_DIR": str(layout.root / "venv"), **layout.reentry_env()}):
                os.environ.pop("OMH_LANG", None)
                status, stdout, stderr = run_cli(base + ["update"], output_json=False)
                self.assertEqual(status, 0, stderr)
                status, raw, stderr = run_cli(base + ["update", "--json"])
                self.assertEqual(status, 0, stderr)
            payload = json.loads(raw)

        self.assertIn(TITLE, stdout)
        self.assertIn(" commands/setup.py | 1 +", stdout)
        self.assertIn(" 1 file changed, 1 insertion(+)", stdout)
        self.assertEqual(payload["update_file_changes"]["status"], "observed")
        self.assertEqual(payload["update_file_changes"]["files"], [{"path": "commands/setup.py", "binary": False, "insertions": 1, "deletions": 0}])


if __name__ == "__main__":
    unittest.main()
