import contextlib
import csv
import io
import math
import tempfile
import unittest
from pathlib import Path

from test_executor.case_1_resource_graph_creator import MIB, read_groups, case_1_resource_graph_creator


class ResourceGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_dir = self.root / "case_1"
        self.case_dir.mkdir()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def rows(self):
        return [dict(test_id=f"run/case_1/{protocol}-{payload}-5000-20-10-0-0",
                     payload=str(payload), protocol=protocol, total_requests="5000", clients="20",
                     max_stream="10", delay="0", packet_loss="0", resource_cpu_percent=str(payload * 2),
                     resource_cpu_counter_ns=str(payload * 1_000_000),
                     resource_memory_bytes=str(payload * MIB), resource_network_rx_bytes=str(2 * MIB),
                     resource_network_tx_bytes=str(payload * MIB))
                for protocol in ("h2", "h3") for payload in (20, 1, 5)]

    def write(self, rows):
        path = self.case_dir / "trimmed_mean.csv"
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_units_network_total_and_parameter_groups(self):
        rows = self.rows()
        rows.append({**rows[0], "delay": "5"})
        groups = read_groups(self.write(rows))
        self.assertEqual(len(groups), 2)
        values = groups[("case_1", 5000, 20, 10, 0, 0)]["h2"][20]
        self.assertEqual(values, {"cpu_usage": 40, "cpu_counter": 20, "memory_usage": 20, "network_usage": 22})

    def test_missing_values_are_gaps_not_zero(self):
        rows = self.rows()
        rows[0]["resource_cpu_percent"] = ""
        rows[0]["resource_network_rx_bytes"] = "NaN"
        groups = read_groups(self.write(rows))
        values = next(iter(groups.values()))["h2"][20]
        self.assertTrue(math.isnan(values["cpu_usage"]))
        self.assertTrue(math.isnan(values["network_usage"]))
        self.assertEqual(values["memory_usage"], 20)

    def test_duplicate_points_are_rejected(self):
        rows = self.rows()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            read_groups(self.write(rows + [rows[0]]))

    def test_case_2_rows_are_ignored(self):
        rows = self.rows()
        case_2 = [{**row, "test_id": row["test_id"].replace("/case_1/", "/case_2/")} for row in rows]
        groups = read_groups(self.write(rows + case_2))
        self.assertEqual(len(groups), 1)
        self.assertEqual(read_groups(self.write(case_2)), {})

    def test_creates_four_png_and_svg_graphs_without_changing_csv(self):
        source = self.write(self.rows())
        original = source.read_bytes()
        outputs = case_1_resource_graph_creator(self.root)
        self.assertTrue(all(path.is_relative_to(self.case_dir / "resource_graphs") for path in outputs))
        self.assertFalse((self.root / "resource_graphs").exists())
        self.assertEqual(len(outputs), 8)
        self.assertEqual({path.stem for path in outputs}, {"cpu_usage", "cpu_counter", "memory_usage", "network_usage"})
        for path in outputs:
            self.assertGreater(path.stat().st_size, 1000)
            if path.suffix == ".png":
                self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            else:
                self.assertIn("<svg", path.read_text())
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
