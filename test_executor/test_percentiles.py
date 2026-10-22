import contextlib
import csv
import importlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from test_executor.execute_trimmed_mean import RANKING_FIELD, execute_trimmed_mean


percentiles = importlib.import_module("test_executor.95_99_percentile")
P95, P99 = percentiles.P95, percentiles.P99


class PercentileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def create_test(self, case="case_1", protocol="h2"):
        folder = f"{protocol}-20-2-1-1-0-0"
        test_id = f"{self.root.name}/{case}/{folder}"
        rows = []
        for i, (rank, seconds) in enumerate(zip([5, 1, 3, 9, 4], [50, 10, 40, 30, 20]), 1):
            row = dict(test_id=test_id, iteration_id=str(i), protocol=protocol, payload="20",
                       total_requests="2", clients="1", max_stream="1", delay="0", packet_loss="0",
                       resource_memory_bytes=str(i * 100), analysis_error="")
            row[RANKING_FIELD] = str(rank)
            path = self.root / case / folder / f"{i:03d}"
            path.mkdir(parents=True)
            (path / "requests.log").write_text(f"1000\t200\t{seconds * 1_000_000}\n1000\t200\t0\n")
            rows.append(row)
        return rows

    def prepare(self, rows):
        for case in {Path(row["test_id"]).parts[-2] for row in rows}:
            case_dir = self.root / case
            with (case_dir / "analysis.csv").open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
                writer.writeheader()
                writer.writerows(row for row in rows if Path(row["test_id"]).parts[-2] == case)
            execute_trimmed_mean(case_dir)

    def read(self, name, case="case_1"):
        with (self.root / case / name).open(newline="") as file:
            return list(csv.DictReader(file, delimiter=";"))

    def test_linear_percentiles_and_single_sample(self):
        samples = [i * 1_000_000 for i in range(1, 101)]
        self.assertEqual(percentiles.percentile_milliseconds(samples, 95), Decimal("95050"))
        self.assertEqual(percentiles.percentile_milliseconds(samples, 99), Decimal("99010"))
        self.assertEqual(percentiles.percentile_milliseconds([123456], 99), Decimal("123.456"))
        with self.assertRaisesRegex(ValueError, "No completed"):
            percentiles.percentile_milliseconds([], 95)

    def test_p99_selects_iterations_for_both_columns_without_changing_existing_means(self):
        self.prepare(self.create_test())
        original = self.read("trimmed_mean.csv")[0]
        analysis = (self.root / "case_1" / "analysis.csv").read_bytes()
        percentiles.execute_percentiles(self.root)
        result = self.read("trimmed_mean.csv")[0]
        self.assertEqual(result[P95], "28500.000000")
        self.assertEqual(result[P99], "29700.000000")
        self.assertEqual({key: result[key] for key in original}, original)
        self.assertEqual(len(result), len(original) + 2)
        self.assertEqual(json.loads(result["mean_of_iterations"]), [1, 3, 5])
        details = self.read("percentiles.csv")
        self.assertEqual(details[0]["percentile_trim_role"], "max_dissmiss")
        self.assertEqual(details[1]["percentile_trim_role"], "min_dismiss")
        self.assertTrue(all(row["percentile_trim_role"] == "mean_of_iterations" for row in details[2:]))
        self.assertEqual(details[0][P95], "47500.000000")
        self.assertEqual(details[0][P99], "49500.000000")
        self.assertEqual(details[0]["request_samples"], "2")
        self.assertEqual((self.root / "case_1" / "analysis.csv").read_bytes(), analysis)
        before = (self.root / "case_1" / "trimmed_mean.csv").read_bytes()
        percentiles.execute_percentiles(self.root)
        self.assertEqual((self.root / "case_1" / "trimmed_mean.csv").read_bytes(), before)

    def test_joins_multiple_cases_and_protocols_by_test_id(self):
        rows = self.create_test() + self.create_test("case_2") + self.create_test("case_1", "h3")
        self.prepare(rows)
        percentiles.execute_percentiles(self.root)
        percentiles.execute_percentiles(self.root, case_name="case_2")
        case_1 = self.read("trimmed_mean.csv")
        case_2 = self.read("trimmed_mean.csv", "case_2")
        self.assertEqual(len(case_1), 2)
        self.assertEqual(len(case_2), 1)
        for case, result in (("case_1", case_1), ("case_2", case_2)):
            self.assertTrue(all(Path(row["test_id"]).parts[-2] == case for row in result))
            self.assertEqual(len(self.read("percentiles.csv", case)), len(result) * 5)
        self.assertFalse(list(self.root.glob("*.csv")))
        result = case_1 + case_2
        self.assertEqual(len(result), 3)
        self.assertEqual(len({row["test_id"] for row in result}), 3)
        self.assertTrue(all(row[P99] == "29700.000000" for row in result))

    def test_failed_streams_excluded_but_completed_http_errors_included(self):
        path = self.root / "requests.log"
        path.write_text("1000\t200\t10\textra\n1000\t-1\t-1\n1000\t500\t30\n\n")
        self.assertEqual(percentiles.read_requests(path), ([10, 30], 1))
        path.write_text("1000\t200\tbad\n")
        with self.assertRaisesRegex(ValueError, "line 1"):
            percentiles.read_requests(path)

    def test_missing_empty_and_malformed_logs_are_reported_and_not_zeros(self):
        rows = self.create_test()
        self.prepare(rows)
        percentiles.request_log(self.root, rows[0]).unlink()
        percentiles.request_log(self.root, rows[1]).write_text("")
        percentiles.request_log(self.root, rows[2]).write_text("broken\n")
        percentiles.execute_percentiles(self.root)
        details = self.read("percentiles.csv")
        self.assertTrue(all(row[P99] == "" and row["percentile_error"] for row in details[:3]))
        self.assertTrue(all(row["percentile_trim_role"] == "mean_of_iterations" for row in details[3:]))
        self.assertEqual(self.read("trimmed_mean.csv")[0][P99], "24750.000000")
        for row in rows[3:]:
            percentiles.request_log(self.root, row).write_text("")
        percentiles.execute_percentiles(self.root)
        self.assertEqual(self.read("trimmed_mean.csv")[0][P99], "")

    def test_microsecond_conversion_preserves_p99_order(self):
        rows = self.create_test()[:3]
        self.prepare(rows)
        for i, row in enumerate(rows, 1):
            percentiles.request_log(self.root, row).write_text("1000\t200\t0\n" * i + "1000\t200\t1\n")
        percentiles.execute_percentiles(self.root)
        details = self.read("percentiles.csv")
        self.assertEqual([row[P99] for row in details], ["0.000990", "0.000980", "0.000970"])
        self.assertEqual([row["percentile_trim_role"] for row in details],
                         ["max_dissmiss", "mean_of_iterations", "min_dismiss"])

    def test_ties_use_iteration_ids_and_single_iteration_uses_itself(self):
        rows = self.create_test()
        self.prepare(rows)
        for row in rows:
            percentiles.request_log(self.root, row).write_text("1000\t200\t1000000\n")
        percentiles.execute_percentiles(self.root)
        details = self.read("percentiles.csv")
        self.assertEqual(details[0]["percentile_trim_role"], "min_dismiss")
        self.assertEqual(details[-1]["percentile_trim_role"], "max_dissmiss")
        self.prepare(rows[:1])
        percentiles.execute_percentiles(self.root)
        self.assertEqual(self.read("trimmed_mean.csv")[0][P99], "1000.000000")
        self.assertEqual(self.read("percentiles.csv")[0]["percentile_trim_role"], "mean_of_iterations")

    def test_mismatched_inputs_leave_existing_csv_unchanged(self):
        self.prepare(self.create_test())
        path = self.root / "case_1" / "trimmed_mean.csv"
        text = path.read_text().replace(";h2;20;", ";h2;21;")
        path.write_text(text)
        with self.assertRaisesRegex(ValueError, "Input parameters do not match"):
            percentiles.execute_percentiles(self.root)
        self.assertEqual(path.read_text(), text)


if __name__ == "__main__":
    unittest.main()
