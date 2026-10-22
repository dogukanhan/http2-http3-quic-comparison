import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from test_executor.case_4_resource_summary_table import COLUMNS, case_4_resource_summary_table


class Case4ResourceSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_dir = self.root / "case_4"
        self.case_dir.mkdir()
        self.source = self.case_dir / "trimmed_mean.csv"
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()
        self.addCleanup(self.console.__exit__, None, None, None)

    def row(self, loss=0, protocol="h2", delay=10, case="case_4"):
        return dict(test_id=f"run/{case}/{protocol}-20-5000-20-10-{delay}-{loss}",
                    protocol=protocol, packet_loss=str(loss), delay=str(delay),
                    resource_cpu_counter_ns="12345678", resource_memory_bytes="1572864",
                    resource_network_rx_bytes="1048576", resource_network_tx_bytes="2621440")

    def write(self, rows):
        with self.source.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

    def read(self, path):
        with path.open(newline="") as file:
            return list(csv.DictReader(file, delimiter=";"))

    def test_filter_units_sorting_and_preservation(self):
        rows = [self.row(loss, protocol, delay) for loss in (2, 0, 1)
                for protocol in ("h3", "h2") for delay in (100, 10, 50, 0)]
        rows.extend([self.row(case="case_3"), self.row(protocol="http1")])
        self.write(rows)
        original = self.source.read_bytes()
        first_table = self.case_dir / "case_4_summary_table.csv"
        first_table.write_text("existing h2load summary")
        output = case_4_resource_summary_table(self.root)
        self.assertEqual(output, self.case_dir / "case_4_resource_summary_table.csv")
        results = self.read(output)
        self.assertEqual([(r["packet_loss"], r["Protocol"]) for r in results],
                         [(str(loss), protocol) for loss in (0, 1, 2) for protocol in ("h2", "h3")])
        self.assertEqual(list(results[0]), list(COLUMNS))
        self.assertEqual(results[0]["CPU Counter (ms)"], "12.346")
        self.assertEqual(results[0]["Memory Usage (MiB)"], "1.500")
        self.assertEqual(results[0]["Network Usage (MiB)"], "3.500")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(first_table.read_text(), "existing h2load summary")
        self.assertEqual(self.read(case_4_resource_summary_table(self.case_dir)), results)

    def test_missing_invalid_and_zero_measurements(self):
        row = self.row()
        row.update(resource_cpu_counter_ns="NaN", resource_memory_bytes="0", resource_network_rx_bytes="")
        self.write([row])
        result = self.read(case_4_resource_summary_table(self.case_dir))[0]
        self.assertEqual(result["CPU Counter (ms)"], "")
        self.assertEqual(result["Memory Usage (MiB)"], "0.000")
        self.assertEqual(result["Network Usage (MiB)"], "")

    def test_missing_source_column_preserves_previous_output(self):
        output = self.case_dir / "case_4_resource_summary_table.csv"
        output.write_text("previous output")
        row = self.row()
        del row["resource_network_tx_bytes"]
        self.write([row])
        with self.assertRaisesRegex(ValueError, "missing columns: resource_network_tx_bytes"):
            case_4_resource_summary_table(self.case_dir)
        self.assertEqual(output.read_text(), "previous output")


if __name__ == "__main__":
    unittest.main()
