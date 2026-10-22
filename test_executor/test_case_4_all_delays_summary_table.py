import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from test_executor.case_4_all_delays_summary_table import COLUMNS, STANDARD_DEVIATIONS, case_4_all_delays_summary_table


class Case4AllDelaysSummaryTests(unittest.TestCase):
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

    def row(self, packet_loss, protocol="h2", case="case_4", delay=10):
        row = {field: "1.234567" for field in COLUMNS.values()}
        row.update({field: "0.123456" for field in STANDARD_DEVIATIONS.values()})
        row.update(test_id=f"run/{case}/{protocol}-20-5000-20-10-{delay}-{packet_loss}",
                   delay=str(delay), packet_loss=str(packet_loss), protocol=protocol, payload="20")
        return row

    def write(self, rows):
        with self.source.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

    def read(self, path):
        with path.open(newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file, delimiter=";"))

    def test_selection_order_formatting_and_case_local_output(self):
        rows = [self.row(n, protocol, delay=delay) for n in (2, 0, 1)
                for delay in (100, 10, 0, 50) for protocol in ("h3", "h2")]
        rows.extend([self.row(0, case="case_1"), self.row(0, protocol="http1")])
        self.write(rows)
        original = self.source.read_bytes()
        existing = self.case_dir / "case_4_summary_table.csv"
        existing.write_text("existing 10 ms table")
        output = case_4_all_delays_summary_table(self.root)
        self.assertEqual(output, self.case_dir / "case_4_all_delays_summary_table.csv")
        results = self.read(output)
        self.assertEqual([(int(row["delay"]), int(row["packet_loss"]), row["Protocol"]) for row in results],
                         [(delay, n, protocol) for delay in (0, 10, 50, 100)
                          for n in (0, 1, 2) for protocol in ("h2", "h3")])
        self.assertEqual(list(results[0]), list(COLUMNS))
        self.assertNotIn("Payload", results[0])
        self.assertEqual(list(results[0])[:3], ["delay", "packet_loss", "Protocol"])
        for column in ("Time For Request(Mean)", "Time For Connect(Mean)", "Time To 1st Byte(Mean)"):
            self.assertEqual(results[0][column], "1.235 ms ± 0.123 ms")
        self.assertEqual(results[0]["Req/s (Mean)"], "1.235 ± 0.123")
        self.assertEqual(results[0]["H2 Load Req/s"], "1.235")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(existing.read_text(), "existing 10 ms table")
        self.assertEqual(self.read(case_4_all_delays_summary_table(self.case_dir)), results)
        self.assertFalse(list(self.root.glob("*.csv")))

    def test_missing_combinations_and_measurements_are_not_invented(self):
        row = self.row(1, "h3")
        row["h2load_time_for_request_mean_ms"] = ""
        row["h2load_time_for_connect_sd_ms"] = ""
        self.write([row])
        results = self.read(case_4_all_delays_summary_table(self.case_dir))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["Protocol"], "h3")
        self.assertEqual(results[0]["Time For Request(Mean)"], "")
        self.assertEqual(results[0]["Time For Connect(Mean)"], "1.235 ms")

    def test_delays_outside_the_case_4_sweep_are_also_included(self):
        self.write([self.row(0, delay=7), self.row(1, "h3", delay=150)])
        rows = self.read(case_4_all_delays_summary_table(self.case_dir))
        self.assertEqual([row["delay"] for row in rows], ["7", "150"])

    def test_missing_delay_column_preserves_previous_output(self):
        output = self.case_dir / "case_4_all_delays_summary_table.csv"
        output.write_text("previous output")
        row = self.row(0)
        del row["delay"]
        self.write([row])
        with self.assertRaisesRegex(ValueError, "missing columns: delay"):
            case_4_all_delays_summary_table(self.case_dir)
        self.assertEqual(output.read_text(), "previous output")


if __name__ == "__main__":
    unittest.main()
