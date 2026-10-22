import contextlib
import csv
import io
from pathlib import Path
import tempfile
import unittest
from importlib import import_module

from test_executor.pre_analysis import INPUT_FIELDS
from test_executor.percentiles_average import execute_percentiles_average
from test_executor.percentile_summary_average_graph_creator import (
    CASES, read_groups, percentile_summary_average_graph_creator,
)

percentiles = import_module("test_executor.95_99_percentile")


class PercentilesAverageTests(unittest.TestCase):
    def test_all_cases_keep_extremes_and_comparison_matches_by_inputs(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            for case_name in CASES:
                with self.subTest(case=case_name):
                    directory = root / case_name
                    rows, trimmed_details = [], []
                    for protocol, factor in (("h2", 1), ("h3", 2)):
                        group = []
                        params = f"{protocol}-20-5000-20-10-10-0"
                        for index, duration in enumerate((1, 2, 3, 4, 100), 1):
                            iteration = directory / params / f"{index:03}"
                            iteration.mkdir(parents=True)
                            (iteration / "requests.log").write_text(f"1000\t200\t{duration * factor * 1000}\n")
                            group.append(dict(zip(INPUT_FIELDS, [f"{root.name}/{case_name}/{params}", str(index),
                                                                 protocol, "20", "5000", "20", "10", "10", "0"])))
                        rows.extend(group)
                        details, _result = percentiles.calculate_test(root, group)
                        trimmed_details.extend(details)
                    percentiles.write_csv(directory / "analysis.csv", INPUT_FIELDS, rows)
                    percentiles.write_csv(directory / "percentiles.csv", percentiles.DETAIL_FIELDS, trimmed_details)
                    before = (directory / "percentiles.csv").read_bytes()
                    output = execute_percentiles_average(directory)
                    with output.open() as file:
                        details = list(csv.DictReader(file, delimiter=";"))
                    self.assertEqual(len(details), 10)
                    self.assertEqual({row["percentile_trim_role"] for row in details}, {"mean_of_iterations"})
                    # Reversing rows must not change protocol/input matching.
                    percentiles.write_csv(output, percentiles.DETAIL_FIELDS, list(reversed(details)))
                    trimmed = read_groups(directory / "percentiles.csv", case_name)
                    averaged = read_groups(output, case_name, untrimmed=True)
                    key = next(iter(trimmed))
                    for protocol, factor in (("h2", 1), ("h3", 2)):
                        x = next(iter(trimmed[key][protocol]))
                        for field in (percentiles.P95, percentiles.P99):
                            self.assertEqual(trimmed[key][protocol][x][field], 3 * factor)
                            self.assertEqual(averaged[key][protocol][x][field], 22 * factor)
                    original = directory / "percentile_graphs" / "percentile_summary.png"
                    original.parent.mkdir()
                    original.write_bytes(b"original graph")
                    images = percentile_summary_average_graph_creator(directory)
                    self.assertEqual(len(images), 1)
                    self.assertEqual(images[0].name, "percentile_summary_average.png")
                    self.assertTrue(images[0].read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertEqual(original.read_bytes(), b"original graph")
                    self.assertEqual((directory / "percentiles.csv").read_bytes(), before)
                    details[0]["percentile_trim_role"] = "min_dismiss"
                    percentiles.write_csv(output, percentiles.DETAIL_FIELDS, details)
                    with self.assertRaisesRegex(ValueError, "must not discard"):
                        read_groups(output, case_name, untrimmed=True)

    def test_missing_request_log_is_flagged_and_untrimmed_mean_is_a_gap(self):
        import math
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            case = root / "case_1"
            params = "h2-20-5000-20-10-0-0"
            iteration = case / params / "001"
            iteration.mkdir(parents=True)
            row = dict(zip(INPUT_FIELDS, [f"{root.name}/case_1/{params}", "1", "h2", "20", "5000", "20", "10", "0", "0"]))
            percentiles.write_csv(case / "analysis.csv", INPUT_FIELDS, [row])
            output = execute_percentiles_average(case)
            with output.open() as file:
                detail = next(csv.DictReader(file, delimiter=";"))
            self.assertEqual(detail["percentile_trim_role"], "excluded")
            self.assertTrue(detail["percentile_error"])
            group = next(iter(read_groups(output, "case_1", untrimmed=True).values()))
            self.assertTrue(math.isnan(group["h2"][20][percentiles.P95]))


if __name__ == "__main__":
    unittest.main()
