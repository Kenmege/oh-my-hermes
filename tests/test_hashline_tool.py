"""Tests for hashline_read / hashline_edit tools and hashline core logic."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from omh.plugin_bundle.omh import hashline
from omh.plugin_bundle.omh.hashline import (
    FileContent,
    HashlineError,
    line_hash,
    parse_anchor,
    resolve_path,
)
from omh.plugin_bundle.omh.tools.hashline_tool import (
    HASHLINE_EDIT_SCHEMA,
    HASHLINE_READ_SCHEMA,
    _hashline_tools_enabled,
    hashline_edit_handler,
    hashline_read_handler,
)

SAMPLE = "alpha\nbeta\ngamma\n"
SAMPLE_LINES = ["alpha", "beta", "gamma"]


class _RootTestCase(unittest.TestCase):
    """Base: sandbox root + TERMINAL_CWD so files stay inside the confined root."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._old_cwd = os.environ.get("TERMINAL_CWD")
        os.environ["TERMINAL_CWD"] = self.root

    def tearDown(self):
        if self._old_cwd is None:
            os.environ.pop("TERMINAL_CWD", None)
        else:
            os.environ["TERMINAL_CWD"] = self._old_cwd
        shutil.rmtree(self.root, ignore_errors=True)

    def make_file(self, raw: str) -> str:
        path = os.path.join(self.root, "f.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw)
        return path

    def read_raw(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


def expected_anchor(idx: int) -> str:
    return f"{idx + 1}:{line_hash(SAMPLE_LINES[idx])}"


class HashFormatTests(_RootTestCase):
    def test_line_hash_is_first_three_sha1_hex_chars(self):
        for text in SAMPLE_LINES:
            expected = hashlib.sha1(text.encode("utf-8")).hexdigest()[:3]
            self.assertEqual(line_hash(text), expected)
            self.assertEqual(len(line_hash(text)), 3)

    def test_read_output_format(self):
        path = self.make_file(SAMPLE)
        result = json.loads(hashline_read_handler({"path": path}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["total_lines"], 3)
        self.assertFalse(result["truncated"])
        for i, line in enumerate(result["lines"]):
            self.assertTrue(line.startswith(f"{i + 1}:{line_hash(SAMPLE_LINES[i])}|"), line)
            self.assertTrue(line.endswith(SAMPLE_LINES[i]))


class ReadPaginationTests(_RootTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.make_file("".join(f"line{i}\n" for i in range(1, 11)))

    def test_offset_and_limit(self):
        result = hashline.read_lines(self.path, offset=3, limit=4)
        self.assertEqual(result["total_lines"], 10)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["lines"]), 4)
        self.assertTrue(result["lines"][0].startswith("3:"))
        self.assertTrue(result["lines"][-1].startswith("6:"))

    def test_offset_beyond_end_returns_empty(self):
        result = hashline.read_lines(self.path, offset=50, limit=10)
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["total_lines"], 10)

    def test_read_default_limit_is_400(self):
        self.assertEqual(HASHLINE_READ_SCHEMA["parameters"]["properties"]["limit"]["default"], 400)


class ReplaceInsertDeleteTests(_RootTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.make_file(SAMPLE)

    def test_replace_single_line(self):
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [{"op": "replace", "start": expected_anchor(1), "text": "ALPHA"}],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read_raw(self.path), "alpha\nALPHA\ngamma\n")

    def test_replace_range(self):
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [{"op": "replace", "start": expected_anchor(0), "end": expected_anchor(1),
                       "text": "one\ntwo"}],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read_raw(self.path), "one\ntwo\ngamma\n")

    def test_replace_with_empty_text_deletes(self):
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [{"op": "replace", "start": expected_anchor(1), "text": ""}],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read_raw(self.path), "alpha\ngamma\n")

    def test_insert_after_middle(self):
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [{"op": "insert_after", "anchor": expected_anchor(0), "text": "new1\nnew2"}],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read_raw(self.path), "alpha\nnew1\nnew2\nbeta\ngamma\n")

    def test_delete_range(self):
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [{"op": "delete", "start": expected_anchor(0), "end": expected_anchor(1)}],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read_raw(self.path), "gamma\n")

    def test_result_returns_fresh_anchors_for_chaining(self):
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [{"op": "replace", "start": expected_anchor(0), "text": "ALPHA"}],
        }))
        anchors = result["regions"][0]["fresh_anchors"]
        self.assertTrue(any(a.startswith("1:") for a in anchors))
        n, rest = anchors[0].split(":", 1)
        h, text = rest.split("|", 1)
        lines = self.read_raw(self.path).split("\n")
        self.assertEqual(h, line_hash(lines[int(n) - 1]))


class MultiEditAtomicityTests(_RootTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.make_file(SAMPLE)

    def test_multiple_edits_applied(self):
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [
                {"op": "replace", "start": expected_anchor(0), "text": "ALPHA"},
                {"op": "insert_after", "anchor": expected_anchor(2), "text": "delta"},
            ],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read_raw(self.path), "ALPHA\nbeta\ngamma\ndelta\n")

    def test_stale_anchor_writes_nothing(self):
        bad_anchor = f"2:{line_hash('wrong')}"
        before = self.read_raw(self.path)
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [
                {"op": "replace", "start": expected_anchor(0), "text": "X"},
                {"op": "replace", "start": bad_anchor, "text": "Y"},
            ],
        }))
        self.assertFalse(result["ok"])
        self.assertIn("stale_anchor", result["error"])
        self.assertIn(bad_anchor, result["error"])
        self.assertIn("context", result)
        self.assertEqual(self.read_raw(self.path), before)

    def test_overlap_rejected_and_nothing_written(self):
        before = self.read_raw(self.path)
        result = json.loads(hashline_edit_handler({
            "path": self.path,
            "edits": [
                {"op": "replace", "start": expected_anchor(0), "end": expected_anchor(2), "text": "x"},
                {"op": "replace", "start": expected_anchor(2), "text": "y"},
            ],
        }))
        self.assertFalse(result["ok"])
        self.assertIn("overlap", result["error"])
        self.assertEqual(self.read_raw(self.path), before)


class PathSafetyTests(unittest.TestCase):
    def test_path_escape_refused(self):
        with self.assertRaises(HashlineError):
            resolve_path("../outside.txt", "/tmp/omh-hashline-root/sub")

    def test_path_within_root_allowed(self):
        resolved = resolve_path("sub/file.txt", "/tmp/omh-hashline-root")
        self.assertEqual(resolved, os.path.realpath("/tmp/omh-hashline-root/sub/file.txt"))

    def test_absolute_path_through_symlinked_prefix_is_inside(self):
        real = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, real, True)
        link_parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, link_parent, True)
        link = os.path.join(link_parent, "ws")
        os.symlink(real, link)
        target = os.path.join(link, "a.py")
        self.assertEqual(resolve_path(target, real), os.path.realpath(target))
        self.assertEqual(resolve_path(os.path.join(real, "a.py"), link), os.path.realpath(target))

    def test_symlink_inside_root_pointing_outside_is_refused(self):
        root, outside = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, os.path.join(root, "out"))
        with self.assertRaises(HashlineError):
            resolve_path("out/x.txt", root)

    def test_handler_uses_host_session_root_over_terminal_cwd(self):
        import types
        from unittest import mock

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("a\n")
        host = types.ModuleType("tools.file_tools_paths")
        host._resolve_base_dir = lambda task_id, container_paths=False: Path(root) if task_id == "t1" else None
        with mock.patch.dict(sys.modules, {"tools.file_tools_paths": host}), \
                mock.patch.dict(os.environ, {"TERMINAL_CWD": "/nonexistent-gateway-cwd"}):
            result = json.loads(hashline_read_handler({"path": "f.txt"}, task_id="t1"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["lines"], [f"1:{line_hash('a')}|a"])

    def test_handler_refuses_escape_with_terminal_cwd(self):
        root = tempfile.mkdtemp()
        old = os.environ.get("TERMINAL_CWD")
        os.environ["TERMINAL_CWD"] = root
        try:
            result = json.loads(hashline_edit_handler({
                "path": "../evil.txt",
                "edits": [{"op": "insert_after", "anchor": "0:000", "text": "x"}],
            }))
            self.assertFalse(result["ok"])
            self.assertIn("scope_mismatch", result["error"])
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(root), "evil.txt")))
        finally:
            if old is None:
                del os.environ["TERMINAL_CWD"]
            else:
                os.environ["TERMINAL_CWD"] = old
            shutil.rmtree(root, ignore_errors=True)


class NewlinePreservationTests(_RootTestCase):
    def test_trailing_newline_preserved(self):
        with open(os.path.join(self.root, "a.txt"), "w", encoding="utf-8") as f:
            f.write(SAMPLE)
        with open(os.path.join(self.root, "b.txt"), "w", encoding="utf-8") as f:
            f.write(SAMPLE.strip("\n"))
        for name, raw in (("a.txt", SAMPLE), ("b.txt", SAMPLE.strip("\n"))):
            path = os.path.join(self.root, name)
            result = json.loads(hashline_edit_handler({
                "path": path,
                "edits": [{"op": "replace", "start": "1:" + line_hash("alpha"), "text": "A"}],
            }))
            self.assertTrue(result["ok"], result)
            self.assertEqual(self.read_raw(path), raw.replace("alpha", "A"))

    def test_crlf_preserved(self):
        path = os.path.join(self.root, "crlf.txt")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("alpha\r\nbeta\r\n")
        result = json.loads(hashline_edit_handler({
            "path": path,
            "edits": [{"op": "insert_after", "anchor": "2:" + line_hash("beta"), "text": "gamma"}],
        }))
        self.assertTrue(result["ok"], result)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"alpha\r\nbeta\r\ngamma\r\n")

    def test_crlf_parse_render(self):
        content = FileContent.parse("a\r\nb\r\n")
        self.assertEqual(content.lines, ["a", "b"])
        self.assertTrue(content.uses_crlf)
        self.assertTrue(content.ends_with_newline)
        self.assertEqual(content.render(), "a\r\nb\r\n")


class CreateFileTests(_RootTestCase):
    def test_create_via_insert_after_0_000(self):
        path = os.path.join(self.root, "new.txt")
        result = json.loads(hashline_edit_handler({
            "path": path,
            "edits": [{"op": "insert_after", "anchor": "0:000", "text": "first\nsecond"}],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read_raw(path), "first\nsecond\n")

    def test_edit_on_missing_file_refused(self):
        path = os.path.join(self.root, "nope.txt")
        result = json.loads(hashline_edit_handler({
            "path": path,
            "edits": [{"op": "replace", "start": "1:abc", "text": "x"}],
        }))
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(path))

    def test_create_refused_if_file_exists(self):
        with self.assertRaises(HashlineError):
            hashline.create_file(__file__, "x")


class CheckFnGatingTests(unittest.TestCase):
    def test_check_fn_disabled_by_default(self):
        old = os.environ.pop("OMH_HASHLINE_TOOLS", None)
        try:
            self.assertFalse(_hashline_tools_enabled())
        finally:
            if old is not None:
                os.environ["OMH_HASHLINE_TOOLS"] = old

    def test_check_fn_accepts_1_and_true(self):
        for value in ("1", "true", "True", "TRUE"):
            os.environ["OMH_HASHLINE_TOOLS"] = value
            try:
                self.assertTrue(_hashline_tools_enabled())
            finally:
                del os.environ["OMH_HASHLINE_TOOLS"]

    def test_check_fn_rejects_other_values(self):
        for value in ("0", "false", "", "maybe"):
            os.environ["OMH_HASHLINE_TOOLS"] = value
            try:
                self.assertFalse(_hashline_tools_enabled())
            finally:
                del os.environ["OMH_HASHLINE_TOOLS"]

    def _with_profile_config(self, text):
        import tempfile
        from unittest import mock

        from omh.plugin_bundle.omh import runtime_paths

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        if text is not None:
            (home / "config.yaml").write_text(text, encoding="utf-8")
        patcher = mock.patch.object(runtime_paths, "default_hermes_home", return_value=home)
        patcher.start()
        self.addCleanup(patcher.stop)
        old = os.environ.pop("OMH_HASHLINE_TOOLS", None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, "OMH_HASHLINE_TOOLS", old)

    def test_check_fn_reads_profile_setting_when_env_unset(self):
        self._with_profile_config("plugins:\n  entries:\n    omh:\n      settings:\n        hashline_tools: true\n")
        self.assertTrue(_hashline_tools_enabled())

    def test_check_fn_profile_setting_off_or_missing_is_off(self):
        for text in ("plugins:\n  entries:\n    omh:\n      settings:\n        hashline_tools: off\n",
                     "plugins:\n  entries:\n    omh:\n      settings: {}\n", None):
            with self.subTest(text=text):
                self._with_profile_config(text)
                self.assertFalse(_hashline_tools_enabled())

    def test_registration_uses_dedicated_toolset_and_check_fn(self):
        from omh.plugin_bundle.omh import register

        recorded = {}

        class Ctx:
            def register_tool(self, name, toolset, schema, handler, **kwargs):
                recorded[name] = (toolset, schema, handler, kwargs)

            def register_hook(self, name, handler):
                pass

        register(Ctx())
        self.assertIn("hashline_read", recorded)
        self.assertIn("hashline_edit", recorded)
        self.assertEqual(recorded["hashline_read"][0], "omh-hashline")
        self.assertEqual(recorded["hashline_edit"][0], "omh-hashline")
        self.assertEqual(recorded["hashline_read"][1], HASHLINE_READ_SCHEMA)
        self.assertEqual(recorded["hashline_edit"][1], HASHLINE_EDIT_SCHEMA)
        self.assertEqual(recorded["hashline_read"][3].get("check_fn"), _hashline_tools_enabled)
        self.assertEqual(recorded["hashline_edit"][3].get("check_fn"), _hashline_tools_enabled)


class AnchorParsingTests(unittest.TestCase):
    def test_parse_anchor_valid(self):
        self.assertEqual(parse_anchor("12:abc"), (12, "abc"))
        self.assertEqual(parse_anchor("0:000"), (0, "000"))

    def test_parse_anchor_invalid(self):
        for bad in ("12", "12:ab", "12:abcd", "a:abc", "-1:abc", "12:ABC", ""):
            with self.assertRaises(HashlineError, msg=bad):
                parse_anchor(bad)

    def test_anchor_matches(self):
        content = FileContent.parse(SAMPLE)
        self.assertTrue(hashline.anchor_matches(content, expected_anchor(0)))
        self.assertFalse(hashline.anchor_matches(content, "1:" + line_hash("wrong")))
        self.assertFalse(hashline.anchor_matches(content, "99:" + line_hash("alpha")))
        self.assertTrue(hashline.anchor_matches(content, "0:000"))


class BinaryTests(_RootTestCase):
    def test_binary_file_refused(self):
        path = os.path.join(self.root, "bin.bin")
        with open(path, "wb") as f:
            f.write(b"a\x00b\n")
        result = json.loads(hashline_edit_handler({
            "path": path,
            "edits": [{"op": "insert_after", "anchor": "0:000", "text": "x"}],
        }))
        self.assertFalse(result["ok"])
        result = json.loads(hashline_read_handler({"path": path}))
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
