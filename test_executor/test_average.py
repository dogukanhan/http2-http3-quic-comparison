import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from test_executor.average import execute_average, main
from test_executor.pre_analysis import INPUT_FIELDS


class AverageTests(unittest.TestCase):
    def test_keeps_extremes_for_metrics_and_percentiles_without_modifying_sources(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            case = root / "case_4"
            params = "h2-20-5000-20-10-0-0"
            rows = []
            for index, value in enumerate((1, 2, 3, 4, 100), 1):
                iteration = case / params / f"{index:03}"
                iteration.mkdir(parents=True)
                (iteration / "requests.log").write_text(f"1000\t200\t{value * 1000}\n")
                row = dict(zip(INPUT_FIELDS, [f"{root.name}/case_4/{params}", str(index),
                                              "h2", "20", "5000", "20", "10", "0", "0"]))
                row.update(h2load_time_for_request_mean_ms=str(value),
                           h2load_time_for_request_sd_ms=str(value * 2),
                           resource_memory_bytes=str(9007199254740993 + index),
                           h2load_throughput="1", h2load_throughput_unit="KB/s")
                rows.append(row)
            source = case / "analysis.csv"
            with source.open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
                writer.writeheader()
                writer.writerows(rows)
            trimmed = case / "trimmed_mean.csv"
            trimmed.write_text("preserve trimmed results")
            before = source.read_bytes()
            self.assertEqual(main([str(root)]), 0)
            with (case / "average.csv").open() as file:
                result = next(csv.DictReader(file, delimiter=";"))
            self.assertEqual(result["h2load_time_for_request_mean_ms"], "22.000000")
            self.assertEqual(result["h2load_time_for_request_sd_ms"], "44.000000")
            self.assertEqual(result["resource_memory_bytes"], "9007199254740996.000000")
            self.assertEqual(result["h2load_throughput"], "1024.000000")
            for metric in ("p95", "p99"):
                self.assertEqual(result[f"h2load_time_for_request_{metric}_ms"], "22.000000")
            self.assertEqual(json.loads(result["mean_of_iterations"]), [1, 2, 3, 4, 5])
            self.assertEqual(result["min_dismiss"], "[]")
            self.assertEqual(result["max_dissmiss"], "[]")
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(trimmed.read_text(), "preserve trimmed results")
            (case / params / "005" / "requests.log").unlink()
            execute_average(case)
            with (case / "average.csv").open() as file:
                result = next(csv.DictReader(file, delimiter=";"))
            self.assertEqual(result["h2load_time_for_request_p99_ms"], "")
            self.assertIn("Iteration 5 percentiles", result["analysis_error"])
            self.assertEqual(result["h2load_time_for_request_mean_ms"], "22.000000")


if __name__ == "__main__":
    unittest.main()
