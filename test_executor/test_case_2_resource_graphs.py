import contextlib
import csv
import io
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_executor.resource_graph_creator import MIB, read_groups, resource_graph_creator


class Case2ResourceGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_dir = self.root / "case_2"
        self.case_dir.mkdir()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def rows(self):
        return [dict(test_id=f"run/case_2/{protocol}-20-5000-{clients}-10-0-0",
                     payload="20", protocol=protocol, total_requests="5000", clients=str(clients),
                     max_stream="10", delay="0", packet_loss="0", resource_cpu_percent=str(clients * 2),
                     resource_cpu_counter_ns=str(clients * 1_000_000),
                     resource_memory_bytes=str(clients * MIB), resource_network_rx_bytes=str(2 * MIB),
                     resource_network_tx_bytes=str(clients * MIB))
                for protocol in ("h2", "h3") for clients in (20, 1, 5)]

    def write(self, rows):
        path = self.case_dir / "trimmed_mean.csv"
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_units_network_total_and_parameter_groups(self):
        rows = self.rows()
        rows.append({**rows[0], "payload": "40"})
        groups = read_groups(self.write(rows))
        self.assertEqual(len(groups), 2)
        values = groups[("case_2", 5000, 20, 10, 0, 0)]["h2"][20]
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

    def test_case_1_rows_are_ignored(self):
        rows = self.rows()
        case_1 = [{**row, "test_id": row["test_id"].replace("/case_2/", "/case_1/")} for row in rows]
        groups = read_groups(self.write(rows + case_1))
        self.assertEqual(len(groups), 1)
        self.assertEqual(read_groups(self.write(case_1)), {})

    def test_graphs_use_sorted_clients_and_fixed_payload_caption(self):
        from matplotlib.figure import Figure
        self.write(self.rows())
        observed = []

        def inspect_figure(figure, *args, **kwargs):
            axis = figure.axes[0]
            self.assertEqual(axis.get_xlabel(), "Number of clients")
            self.assertEqual([line.get_label() for line in axis.lines], ["h2", "h3"])
            for line in axis.lines:
                self.assertEqual(list(line.get_xdata()), [1, 5, 20])
            self.assertIn("20 KB payload", figure.texts[1].get_text())
            observed.append(axis.get_ylabel())

        with patch.object(Figure, "savefig", autospec=True, side_effect=inspect_figure):
            resource_graph_creator(self.case_dir)
        self.assertEqual(len(observed), 8)
        self.assertEqual(set(observed), {"CPU usage (%)", "CPU counter (ms)",
                                         "Memory usage (MiB)", "Received + transmitted (MiB)"})

    def test_creates_four_png_and_svg_graphs_without_changing_csv(self):
        source = self.write(self.rows())
        original = source.read_bytes()
        outputs = resource_graph_creator(self.root)
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
