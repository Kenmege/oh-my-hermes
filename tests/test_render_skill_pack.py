from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

from _local_package import load_local_package
load_local_package()

ROOT = Path(__file__).resolve().parents[1]


class RenderSkillPackTests(unittest.TestCase):
    def tool(self):
        path = ROOT / "tools/render_skill_pack.py"
        self.assertTrue(path.is_file(), "missing checked canonical pack producer")
        spec = importlib.util.spec_from_file_location("render_skill_pack_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_write_check_missing_stale_extra_and_foreign_preservation(self):
        tool = self.tool()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skills"
            result = tool.materialize(root, write=True)
            self.assertTrue(result["ok"])
            self.assertTrue(tool.materialize(root, write=False)["ok"])
            support = root / "omh-show-reel/toolkit/scripts/timeline.py"
            support.write_text("edited")
            self.assertIn("omh-show-reel/toolkit/scripts/timeline.py", tool.materialize(root, write=False)["stale"])
            foreign = root / "omh-show-reel/toolkit/foreign.py"
            foreign.write_text("foreign")
            result = tool.materialize(root, write=False)
            self.assertIn("omh-show-reel/toolkit/foreign.py", result["extra"])
            with self.assertRaises(ValueError):
                tool.materialize(root, write=True)
            self.assertEqual(foreign.read_text(), "foreign")
            support.unlink()
            self.assertIn("omh-show-reel/toolkit/scripts/timeline.py", tool.materialize(root, write=False)["missing"])


if __name__ == "__main__":
    unittest.main()
