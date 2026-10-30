"""Offline dashboard contracts, using tiny artificial saved files only."""

import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nevsky_dashboard", ROOT / "build_dashboard.py")
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


def feature(identifier, **properties):
    return {"type": "Feature", "properties": {"entity_id": identifier, **properties},
            "geometry": {"type": "Polygon", "coordinates": [[[30, 59], [30.01, 59],
                         [30.01, 59.01], [30, 59]]]}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="dashboard_test_", dir=ROOT)
        self.addCleanup(self.workspace.cleanup)
        self.folder = Path(self.workspace.name).resolve()
        assert self.folder.is_relative_to(ROOT.resolve())
        self.results = self.folder / "results"
        self.results.mkdir()

    def write_json(self, name, value):
        (self.results / name).write_text(json.dumps(value), encoding="utf-8")

    def minimal_results(self):
        self.write_json("nevsky_blocks_results.geojson", collection(
            feature(0, block_id=0, capacity_within=3, population=20),
            feature(1, block_id=1, capacity_within=0, population=0)))
        self.write_json("summary.json", {"blocks": 2})
        self.write_json("run_info.json", {})

    def test_corrected_saved_layer_is_authoritative_and_stale_stats_are_marked(self):
        self.minimal_results()
        self.write_json("blocks_school_provision_checked.geojson", collection(
            feature(0, capacity_within=7, capacity_self_supply=4),
            feature(1, capacity_within=0, capacity_self_supply=0)))
        (self.results / "indicators_summary.csv").write_text(
            ",count,mean\ncapacity_within,2,1.5\npopulation,2,10\n", encoding="utf-8")
        payload = dashboard.load_inputs(self.folder)
        self.assertEqual(payload["blocks"]["features"][0]["properties"]["capacity_within"], 7)
        self.assertEqual(payload["blocks"]["features"][0]["properties"]["capacity_self_supply"], 4)
        table = next(t for t in payload["tables"] if t["file"] == "indicators_summary.csv")
        self.assertIsNone(table["rows"][0]["mean"])
        self.assertIn("Устарело", table["rows"][0]["Статус"])
        self.assertEqual(table["rows"][1]["mean"], "10")

    def test_overlay_rejects_mixed_runs_by_id_without_mutating_source_layer(self):
        primary = collection(feature(0, value=1))
        original = deepcopy(primary)
        indexed = dashboard.unique_features(primary, "base")
        with self.assertRaises(dashboard.DashboardError):
            dashboard.merge_layer(indexed, collection(feature(2, value=3)), "other", override=True)
        self.assertEqual(primary, original)

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(dashboard.DashboardError):
            dashboard.unique_features(collection(feature(0), feature(0)), "duplicate")

    def test_matrix_retains_asymmetry_zero_infinity_and_existing_order(self):
        path = self.results / "travel_time_minutes.csv"
        path.write_text("block_id,1,0\n0,inf,0\n1,0,12.3456789\n", encoding="utf-8")
        packed = dashboard.pack_matrix(path, {"0", "1"})
        self.assertEqual(packed["rows"], ["0", "1"])
        self.assertEqual(packed["columns"], ["1", "0"])
        numbers = struct.unpack("<4d", zlib.decompress(base64.b64decode(packed["data"])))
        self.assertEqual(numbers, (float("inf"), 0, 0, 12.3456789))

    def test_matrix_rejects_different_blocks(self):
        path = self.results / "travel_time_minutes.csv"
        path.write_text("block_id,0\n0,0\n", encoding="utf-8")
        with self.assertRaises(dashboard.DashboardError):
            dashboard.pack_matrix(path, {"0", "1"})

    def test_reviewed_findings_and_unknown_fields_are_kept(self):
        self.minimal_results()
        (self.results / "findings_draft.txt").write_text("draft", encoding="utf-8")
        (self.results / "findings_reviewed.txt").write_text("reviewed", encoding="utf-8")
        data = json.loads((self.results / "nevsky_blocks_results.geojson").read_text())
        data["features"][1]["properties"]["custom_metric"] = 42
        self.write_json("nevsky_blocks_results.geojson", data)
        payload = dashboard.load_inputs(self.folder)
        self.assertTrue(payload["reviewed"])
        self.assertEqual(payload["findings"], "reviewed")
        self.assertIn("custom_metric", payload["fields"])

    def test_html_keeps_user_text_inside_json_and_has_no_external_resources(self):
        dangerous = '</script><img src="https://example.invalid/tracker">'
        encoded = dashboard.inline_json({"note": dangerous})
        self.assertNotIn("</script>", encoded)
        self.assertEqual(json.loads(encoded)["note"], dangerous)
        document = dashboard.render_html({"note": dangerous})
        self.assertIn("connect-src 'none'", document)
        self.assertNotIn('<script src=', document)
        self.assertNotIn('<link ', document)
        self.assertNotIn('fetch(', dashboard.JS)

    def test_missing_research_results_give_actionable_error(self):
        with self.assertRaisesRegex(dashboard.DashboardError, "разделе 15"):
            dashboard.load_inputs(self.folder)


if __name__ == "__main__":
    unittest.main()
