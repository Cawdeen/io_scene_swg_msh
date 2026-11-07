"""Run with: python -m unittest discover -s tests -v"""

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


# Loading the module directly avoids importing bpy through the addon package.
spec = importlib.util.spec_from_file_location(
    "asset_paths", Path(__file__).parents[1] / "io_scene_swg" / "asset_paths.py")
paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paths)


class AssetPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.flat = self.base / "client"
        self.dsrc = self.base / "dsrc"
        self.roots = tuple(self.dsrc / "sku.0" / system / "compiled" / "game"
                           for system in ("sys.client", "sys.shared", "sys.server"))
        for root in (self.flat,) + self.roots:
            root.mkdir(parents=True)

    def asset(self, root, relative):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"asset")
        return str(path)

    def test_auto_detects_flat_and_split_layouts(self):
        self.assertEqual(paths.asset_roots(self.flat), (str(self.flat),))
        self.assertEqual(paths.asset_roots(self.dsrc), tuple(map(str, self.roots)))
        self.assertEqual(paths.asset_roots(self.dsrc, "FLAT"), (str(self.dsrc),))

    def test_split_dependencies_and_client_priority(self):
        mesh = self.asset(self.roots[1], "appearance/mesh/room.msh")
        texture = self.asset(self.roots[0], "texture/room.dds")
        shader = self.asset(self.roots[0], "shader/room.sht")
        self.asset(self.roots[1], "shader/room.sht")
        server = self.asset(self.roots[2], "appearance/server.msh")
        for relative, expected in (("appearance/mesh/room.msh", mesh),
                                   ("texture/room.dds", texture),
                                   ("shader/room.sht", shader),
                                   ("appearance/server.msh", server)):
            self.assertEqual(paths.find_file(relative, self.dsrc), expected)

    def test_flat_lookup_and_mixed_separators(self):
        expected = self.asset(self.flat, "appearance/mesh/room.msh")
        self.assertEqual(paths.find_file("appearance\\mesh/room.msh", self.flat), expected)
        self.assertEqual(paths.find_file(expected, ()), expected)

    def test_layout_switch_retains_independent_paths(self):
        prefs = SimpleNamespace(asset_layout="FLAT", swg_root=str(self.flat),
                                dsrc_root=str(self.dsrc), auto_root="")
        self.assertEqual(paths.roots_from_preferences(prefs), (str(self.flat),))
        prefs.asset_layout = "DSRC"
        self.assertEqual(paths.roots_from_preferences(prefs), tuple(map(str, self.roots)))
        prefs.asset_layout = "FLAT"
        self.assertEqual(paths.roots_from_preferences(prefs), (str(self.flat),))

    def test_existing_preferences_and_auto_override(self):
        prefs = SimpleNamespace(swg_root=str(self.flat))
        self.assertEqual(paths.roots_from_preferences(prefs), (str(self.flat),))
        prefs.auto_root = str(self.dsrc)
        self.assertEqual(paths.roots_from_preferences(prefs), tuple(map(str, self.roots)))
        self.assertEqual(prefs.swg_root, str(self.flat))

    def test_blender_relative_root_expansion(self):
        prefs = SimpleNamespace(swg_root="//client")
        self.assertEqual(paths.roots_from_preferences(prefs, lambda p: str(self.flat)),
                         (str(self.flat),))

    def test_browser_finds_shared_appearance_and_client_shader(self):
        self.asset(self.roots[1], "appearance/lod/room.lod")
        self.asset(self.roots[0], "shader/room.sht")
        roots = paths.asset_roots(self.dsrc)
        self.assertEqual(Path(paths.browse_directory("appearance/lod", roots)),
                         self.roots[1] / "appearance/lod")
        self.assertEqual(Path(paths.browse_directory("shader", roots)), self.roots[0] / "shader")
        self.assertEqual(Path(paths.browse_directory("missing", roots)), self.roots[0])

    def test_missing_file_reports_search_locations_and_avoids_basename_fallback(self):
        self.asset(self.roots[1], "appearance/mesh/room.msh")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertIsNone(paths.find_file("wrong/room.msh", paths.asset_roots(self.dsrc)))
            self.assertIsNone(paths.find_file("appearance", self.flat))
            self.assertIsNone(paths.find_file("room.msh", ()))
        for root in self.roots:
            self.assertIn(str(root), output.getvalue())
        self.assertEqual(paths.asset_roots(""), ())
        self.assertEqual(paths.browse_directory("appearance", ()), "")


if __name__ == "__main__":
    unittest.main()
