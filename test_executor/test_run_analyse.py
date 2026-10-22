import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_analyse
from test_executor.test_pre_analysis import SUMMARY


class RunAnalyseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = io.StringIO()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(contextlib.redirect_stderr(self.output))

    def make_case(self, name):
        for protocol in ("h2", "h3"):
            for number in (1, 2, 3):
                iteration = self.root / name / f"{protocol}-20-5000-20-10-10-0" / f"{number:03}"
                iteration.mkdir(parents=True)
                (iteration / "summary.txt").write_text(SUMMARY.replace("Application protocol: h2", f"Application protocol: {protocol}"))
                (iteration / "requests.log").write_text("1000\t200\t100000\n1000\t200\t200000\n")
                (iteration / "resource.log").write_text(json.dumps([
                    {"type": "final", "cpu_counter_ns": 2000000, "cpu_percent": 30,
                     "memory_bytes": 1048576, "network_rx_bytes": 100,
                     "network_tx_bytes": 200},
                ]))
        return self.root / name

    def test_discovers_all_cases_and_runs_analysis_before_reports(self):
        cases = [self.make_case(f"case_{n}") for n in range(1, 5)]
        events = []
        report_steps = {}
        expected_reports = []
        for case in cases:
            steps = []
            for original in run_analyse.REPORT_STEPS[case.name]:
                def step(folder, name=original.__name__):
                    events.append((name, folder))
                step.__name__ = original.__name__
                steps.append(step)
                expected_reports.append((original.__name__, case))
            report_steps[case.name] = tuple(steps)
        with contextlib.ExitStack() as stack:
            for name in ("pre_analysis", "execute_trimmed_mean", "execute_percentiles", "execute_average"):
                stack.enter_context(patch.object(run_analyse, name,
                                    side_effect=lambda folder, name=name: events.append((name, folder))))
            stack.enter_context(patch.object(run_analyse, "REPORT_STEPS", report_steps))
            run_analyse.run_analyse(self.root)
        self.assertEqual(events, [(name, case) for case in cases for name in
                                 ("pre_analysis", "execute_trimmed_mean", "execute_percentiles", "execute_average")]
                         + expected_reports)

    def test_case_folder_rebuilds_outputs_preserves_raw_logs_and_appends_run_log(self):
        case = self.make_case("case_4")
        sibling = self.root / "case_1"
        sibling.mkdir()
        (sibling / "analysis.csv").write_text("keep sibling export")
        raw = {path: path.read_bytes() for path in case.glob("*/*/*") if path.is_file()}
        log = self.root / "logs.txt"
        log.write_text("Original benchmark log\n")
        self.assertEqual(run_analyse.main([str(case)]), 0)
        expected_csvs = ["analysis.csv", "trimmed_mean.csv", "percentiles.csv", "average.csv", "percentiles_average.csv",
                         "case_4_summary_table.csv", "case_4_all_delays_summary_table.csv",
                         "case_4_resource_summary_table.csv", "case_4_all_delays_resource_summary_table.csv"]
        for name in expected_csvs:
            self.assertTrue((case / name).is_file(), name)
        with (case / "trimmed_mean.csv").open(newline="") as file:
            rows = list(csv.DictReader(file, delimiter=";"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["h2load_req_per_sec"], "1204.040000")
        self.assertIn("h2load_time_for_request_p99_ms", rows[0])
        image = case / "h2load_graphs/n5000-p20-c20-m10/h2load_summary.png"
        self.assertTrue(image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        (case / "analysis.csv").write_text("obsolete export")
        image.write_bytes(b"obsolete graph")
        self.assertEqual(run_analyse.main([str(case)]), 0)
        self.assertTrue((case / "analysis.csv").read_text().startswith("test_id;"))
        self.assertTrue(image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        for path, contents in raw.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual((sibling / "analysis.csv").read_text(), "keep sibling export")
        self.assertTrue(log.read_text().startswith("Original benchmark log\n"))
        self.assertEqual(log.read_text().count("regenerated successfully"), 2)
        self.assertFalse((case / "logs.txt").exists())

    def test_invalid_folder_or_csv_only_case_fails_before_overwriting(self):
        self.assertEqual(run_analyse.main([str(self.root / "missing")]), 1)
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(run_analyse.main([str(self.root)]), 1)
        case = self.root / "case_2"
        case.mkdir()
        export = case / "analysis.csv"
        export.write_text("existing export")
        self.assertEqual(run_analyse.main([str(self.root)]), 1)
        self.assertEqual(export.read_text(), "existing export")
        self.assertFalse((self.root / "logs.txt").exists())

    def test_failed_stage_is_logged_and_returns_nonzero(self):
        case = self.make_case("case_1")
        with patch.object(run_analyse, "pre_analysis", side_effect=ValueError("broken input")):
            self.assertEqual(run_analyse.main([str(case)]), 1)
        self.assertIn("ValueError: broken input", (self.root / "logs.txt").read_text())
        self.assertNotIn("regenerated successfully", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
