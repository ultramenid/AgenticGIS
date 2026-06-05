import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from AgenticGis.core.layer_id_error import build_layer_id_error


class TestBuildLayerIdError(unittest.TestCase):
    def test_includes_missing_layer_id(self):
        result = build_layer_id_error("stale_id_xyz", [])
        self.assertIn("stale_id_xyz", result["error"])
        self.assertEqual(result["missing_layer_id"], "stale_id_xyz")
        self.assertFalse(result["ok"])

    def test_includes_all_available_ids_when_few(self):
        result = build_layer_id_error("stale", ["layer_a_1", "layer_b_2"])
        self.assertIn("layer_a_1", result["error"])
        self.assertIn("layer_b_2", result["error"])
        self.assertEqual(set(result["available_layer_ids"]),
                         {"layer_a_1", "layer_b_2"})

    def test_suggests_refresh_when_layers_exist(self):
        result = build_layer_id_error("stale", ["layer_a_1"])
        self.assertTrue(
            "list_layers" in result["error"] or "get_project_state" in result["error"],
            f"Expected refresh hint in error, got: {result['error']!r}",
        )

    def test_handles_no_layers_loaded(self):
        result = build_layer_id_error("stale", [])
        self.assertIn("stale", result["error"])
        self.assertEqual(result["available_layer_ids"], [])
        self.assertTrue(
            "list_layers" in result["error"] or "get_project_state" in result["error"],
            f"Expected refresh hint even with no layers loaded, got: {result['error']!r}",
        )

    def test_caps_long_id_list_in_error_string(self):
        many = [f"layer_{i:04d}" for i in range(50)]
        result = build_layer_id_error("stale", many)
        # Error string should not contain all 50 (would be huge).
        self.assertLess(result["error"].count("layer_"), 25)
        # But should still mention the overflow.
        self.assertIn("more", result["error"])
        # And the structured list should be complete.
        self.assertEqual(len(result["available_layer_ids"]), 50)


if __name__ == "__main__":
    unittest.main()
