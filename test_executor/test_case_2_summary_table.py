import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from test_executor.case_2_summary_table import COLUMNS, case_2_summary_table


class Case2SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_dir = self.root / "case_2"
        self.case_dir.mkdir()
        self.source = self.case_dir / "trimmed_mean.csv"
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()
        self.addCleanup(self.console.__exit__, None, None, None)

    def row(self, clients, protocol="h2", case="case_2"):
        row = {field: "1.234567" for field in COLUMNS.values()}
        row.update({field: "0.123456" for field in (source.replace("_mean", "_sd") for source in COLUMNS.values() if "_mean" in source)})
        row.update(test_id=f"run/{case}/{protocol}-20-5000-{clients}-10-0-0",
                   clients=str(clients), protocol=protocol, payload="20")
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
        rows = [self.row(n, protocol) for n in (100, 1, 25, 10, 2, 50, 99)
                for protocol in ("h3", "h2")]
        rows.extend([self.row(2, case="case_1"), self.row(2, protocol="http1")])
        self.write(rows)
        original = self.source.read_bytes()
        output = case_2_summary_table(self.root)
        self.assertEqual(output, self.case_dir / "case_2_summary_table.csv")
        results = self.read(output)
        self.assertEqual([(int(row["client_size"]), row["Protocol"]) for row in results],
                         [(n, protocol) for n in (2, 10, 25, 50, 100) for protocol in ("h2", "h3")])
        self.assertEqual(list(results[0]), list(COLUMNS))
        self.assertNotIn("Payload", results[0])
        for column in ("Time For Request(Mean)", "Time For Connect(Mean)", "Time To 1st Byte(Mean)"):
            self.assertEqual(results[0][column], "1.235 ms")
        self.assertEqual(results[0]["Req/s (Mean)"], "1.235")
        self.assertEqual(results[0]["H2 Load Req/s"], "1.235")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.read(case_2_summary_table(self.case_dir)), results)
        self.assertFalse(list(self.root.glob("*.csv")))

    def test_missing_combinations_and_measurements_are_not_invented(self):
        row = self.row(10, "h3")
        row["h2load_time_for_request_mean_ms"] = ""
        for field in list(row):
            if "_sd" in field:
                del row[field]
        self.write([row])
        results = self.read(case_2_summary_table(self.case_dir))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["Protocol"], "h3")
        self.assertEqual(results[0]["Time For Request(Mean)"], "")
        self.assertEqual(results[0]["Time For Connect(Mean)"], "1.235 ms")


if __name__ == "__main__":
    unittest.main()
