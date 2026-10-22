import contextlib
import csv
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from test_executor.execute_trimmed_mean import RANKING_FIELD, execute_trimmed_mean


class TrimmedMeanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case_dir = self.root / "case_1"
        self.case_dir.mkdir()
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()
        self.addCleanup(self.console.__exit__, None, None, None)

    def rows(self, test_id="run/case_1/h2-20-5000-20-10-0-0"):
        return [dict(test_id=test_id, iteration_id=str(i), protocol="h2", payload="20",
                     total_requests="5000", clients="20", max_stream="10", delay="0", packet_loss="0",
                     h2load_time_for_request_mean_ms=str(rank),
                     h2load_req_per_sec=str(i * 10), resource_cpu_consumed_seconds=str(cpu),
                     resource_memory_bytes=str(9007199254740992 + i),
                     h2load_throughput="1", h2load_throughput_unit="MB/s",
                     resource_timestamp=f"timestamp-{i}", resource_server_host="192.0.2.10",
                     resource_type="final", h2load_udp_datagrams_sent="", analysis_error="")
                for i, (rank, cpu) in enumerate(zip([5, 1, 3, 9, 4], [100, 200, 300, 1, 500]), 1)]

    def write(self, rows):
        path = self.case_dir / "analysis.csv"
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def calculate(self, rows):
        self.write(rows)
        result = execute_trimmed_mean(self.root)
        with result.open(newline="") as file:
            return list(csv.DictReader(file, delimiter=";"))

    def test_request_mean_selects_same_iterations_for_all_numeric_means(self):
        rows = self.rows()
        source = self.write(rows)
        original = source.read_bytes()
        output = execute_trimmed_mean(self.root)
        with output.open(newline="") as file:
            result = list(csv.DictReader(file, delimiter=";"))[0]
        self.assertEqual(json.loads(result["min_dismiss"]), [2])
        self.assertEqual(json.loads(result["max_dissmiss"]), [4])
        self.assertEqual(json.loads(result["mean_of_iterations"]), [1, 3, 5])
        self.assertEqual(Decimal(result[RANKING_FIELD]), 4)
        self.assertEqual(result[RANKING_FIELD], "4.000000")
        self.assertEqual(Decimal(result["resource_cpu_consumed_seconds"]), 300)
        self.assertEqual(Decimal(result["resource_memory_bytes"]), Decimal("9007199254740995"))
        self.assertEqual(Decimal(result["h2load_req_per_sec"]), 30)
        self.assertEqual(result["iteration_id"], "trimmed_mean")
        self.assertEqual(result["total_requests"], "5000")
        self.assertEqual(result["resource_server_host"], "192.0.2.10")
        self.assertEqual(json.loads(result["resource_timestamp"]), ["timestamp-1", "timestamp-3", "timestamp-5"])
        self.assertEqual(result["h2load_udp_datagrams_sent"], "")
        self.assertEqual(result["analysis_error"], "")
        self.assertEqual(source.read_bytes(), original)
        before = output.read_bytes()
        execute_trimmed_mean(self.root)
        self.assertEqual(output.read_bytes(), before)

    def test_multiple_tests_are_aggregated_separately(self):
        results = self.calculate(self.rows("first") + self.rows("second"))
        self.assertEqual([row["test_id"] for row in results], ["first", "second"])

    def test_ties_discard_distinct_iterations_deterministically(self):
        rows = self.rows()
        for row in rows:
            row[RANKING_FIELD] = "2"
        result = self.calculate(list(reversed(rows)))[0]
        self.assertEqual(json.loads(result["min_dismiss"]), [1])
        self.assertEqual(json.loads(result["max_dissmiss"]), [5])
        self.assertEqual(json.loads(result["mean_of_iterations"]), [2, 3, 4])

    def test_one_or_two_iterations_use_all_without_trimming(self):
        for count in (1, 2):
            with self.subTest(count=count):
                result = self.calculate(self.rows()[:count])[0]
                self.assertEqual(result["min_dismiss"], "[]")
                self.assertEqual(result["max_dissmiss"], "[]")
                self.assertEqual(json.loads(result["mean_of_iterations"]), list(range(1, count + 1)))
                self.assertEqual(Decimal(result[RANKING_FIELD]), 5 if count == 1 else 3)

    def test_missing_metric_does_not_silently_change_retained_iterations(self):
        rows = self.rows()
        rows[0]["resource_cpu_consumed_seconds"] = ""
        result = self.calculate(rows)[0]
        self.assertEqual(result["resource_cpu_consumed_seconds"], "")
        self.assertIn("No mean for resource_cpu_consumed_seconds", result["analysis_error"])
        self.assertEqual(json.loads(result["mean_of_iterations"]), [1, 3, 5])

    def test_invalid_ranking_values_are_excluded_and_reported(self):
        rows = self.rows()
        rows[0][RANKING_FIELD] = "NaN"
        result = self.calculate(rows)[0]
        self.assertIn("Excluded iteration 1", result["analysis_error"])
        self.assertEqual(json.loads(result["mean_of_iterations"]), [3, 5])
        for row in rows:
            row[RANKING_FIELD] = ""
        result = self.calculate(rows)[0]
        self.assertEqual(result["mean_of_iterations"], "[]")
        self.assertEqual(result["resource_cpu_consumed_seconds"], "")
        self.assertIn("No valid iterations", result["analysis_error"])

    def test_mixed_throughput_units_are_normalized_before_averaging(self):
        rows = self.rows()
        rows[2].update(h2load_throughput="1024", h2load_throughput_unit="KB/s")
        rows[4].update(h2load_throughput="1048576", h2load_throughput_unit="B/s")
        result = self.calculate(rows)[0]
        self.assertEqual(result["h2load_throughput_unit"], "B/s")
        self.assertEqual(Decimal(result["h2load_throughput"]), 1048576)

    def test_invalid_input_does_not_replace_previous_output(self):
        output = self.case_dir / "trimmed_mean.csv"
        output.write_text("previous output")
        rows = self.rows()
        self.write(rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, "Duplicate iteration"):
            execute_trimmed_mean(self.root)
        self.assertEqual(output.read_text(), "previous output")
        rows[1]["payload"] = "21"
        self.write(rows)
        with self.assertRaisesRegex(ValueError, "Inconsistent input parameters"):
            execute_trimmed_mean(self.root)
        self.assertEqual(output.read_text(), "previous output")


if __name__ == "__main__":
    unittest.main()
