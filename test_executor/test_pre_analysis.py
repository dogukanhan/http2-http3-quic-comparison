import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_executor.pre_analysis import duration_milliseconds, parse_summary, pre_analysis
from test_executor.pre_init_test import get_run_directory, pre_init_test


SUMMARY = """TLS Protocol: TLSv1.3
Cipher: TLS_AES_128_GCM_SHA256
Application protocol: h2
finished in 4.15s, 1204.04 req/s, 10.76MB/s
requests: 5000 total, 5000 started, 4999 done, 4998 succeeded, 2 failed, 1 errored, 1 timeout
status codes: 4998 2xx, 0 3xx, 1 4xx, 0 5xx
traffic: 44.68MB (46848760) total, 17.13KB (17540) headers (space savings 95.06%), 44.53MB (46695000) data
                     min         max         mean         sd        +/- sd
time for request:    28.56ms    210.50ms    160.74ms     13.86ms    92.10%
time for connect:    18.30ms     46.02ms     25.58ms      5.21ms    90.00%
time to 1st byte:    25.11ms     55.73ms     38.53ms      7.64ms    80.00%
req/s           :      60.35       61.24       60.77        0.19    85.00%
"""


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def iteration(self, run=None, parameters="h2-20-5000-20-10-0-0", number="001", case="case_1"):
        directory = (run or self.root) / case / parameters / number
        directory.mkdir(parents=True)
        (directory / "summary.txt").write_text(SUMMARY)
        (directory / "requests.log").write_text("1000\t200\t100000\n1000\t200\t200000\n")
        (directory / "resource.log").write_text(json.dumps([
            {"type": "baseline", "cpu_consumed_seconds": 0, "memory_bytes": 100},
            {"type": "sample", "cpu_consumed_seconds": 0.1, "memory_bytes": 200},
            {"type": "final", "cpu_consumed_seconds": 0.25, "memory_bytes": 300,
             "network_rx_bytes": 9007199254740993, "cpu_percent": None,
             "extra_counter": 42, "server_host": "192.0.2.10"},
            {"type": "sample", "memory_bytes": 999},
        ]))
        return directory

    def read(self, path):
        with path.open(newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file, delimiter=";"))

    def test_summary_metrics_and_units(self):
        parsed, errors = parse_summary(SUMMARY)
        self.assertEqual(errors, [])
        self.assertEqual(parsed["h2load_finished_ms"], 4150)
        self.assertEqual(parsed["h2load_requests_failed"], 2)
        self.assertEqual(parsed["h2load_status_4xx"], 1)
        self.assertEqual(parsed["h2load_traffic_headers_bytes"], 17540)
        self.assertEqual(parsed["h2load_throughput"], 10.76)
        self.assertEqual(parsed["h2load_throughput_unit"], "MB/s")
        self.assertAlmostEqual(parsed["h2load_time_for_request_mean_ms"], 160.74)
        self.assertEqual(parsed["h2load_req_per_sec_mean"], 60.77)
        self.assertEqual(parsed["h2load_time_for_connect_within_sd_percent"], 90)
        self.assertEqual(parsed["h2load_tls_protocol"], "TLSv1.3")
        for text, expected in (("100us", .1), ("1e+03ms", 1000), ("2m", 120000),
                               ("1h", 3600000), ("3ns", .000003), ("2µs", .002)):
            self.assertAlmostEqual(duration_milliseconds(text), expected)

    def test_h3_udp_and_zero_byte_throughput(self):
        text = SUMMARY.replace("4.15s, 1204.04 req/s, 10.76MB/s", "58.06ms, 0.00 req/s, 0B/s")
        text += "UDP datagram: 123 sent, 456 received\n"
        parsed, errors = parse_summary(text)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(parsed["h2load_finished_ms"], 58.06)
        self.assertEqual(parsed["h2load_throughput"], 0)
        self.assertEqual(parsed["h2load_throughput_unit"], "B/s")
        self.assertEqual(parsed["h2load_udp_datagrams_sent"], 123)
        self.assertEqual(parsed["h2load_udp_datagrams_received"], 456)

    def test_csv_groups_iterations_and_selects_only_final_resources(self):
        self.iteration(number="010")
        self.iteration(number="002")
        self.iteration(parameters="h3-256-5000-20-10-5-1")
        path = pre_analysis(self.root)
        rows = self.read(path)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["iteration_id"] for row in rows], ["2", "10", "1"])
        self.assertEqual(rows[0]["test_id"], rows[1]["test_id"])
        self.assertNotEqual(rows[0]["test_id"], rows[2]["test_id"])
        self.assertEqual(rows[2]["payload"], "256")
        self.assertEqual(rows[2]["total_requests"], "5000")
        self.assertEqual(rows[2]["max_stream"], "10")
        self.assertEqual(rows[2]["delay"], "5")
        self.assertEqual(rows[2]["packet_loss"], "1")
        self.assertEqual(rows[0]["resource_type"], "final")
        self.assertEqual(rows[0]["resource_memory_bytes"], "300")
        self.assertEqual(rows[0]["resource_network_rx_bytes"], "9007199254740993")
        self.assertEqual(rows[0]["resource_cpu_consumed_seconds"], "0.25")
        self.assertEqual(rows[0]["resource_cpu_percent"], "")
        self.assertEqual(rows[0]["resource_extra_counter"], "42")
        self.assertEqual(rows[0]["analysis_error"], "")
        self.assertEqual(rows, self.read(pre_analysis(self.root)))

    def test_incomplete_or_malformed_logs_remain_visible(self):
        first = self.iteration()
        (first / "resource.log").write_text('[{"type": "baseline"}]')
        second = self.iteration(number="002")
        (second / "summary.txt").write_text("interrupted benchmark")
        (second / "resource.log").write_text("invalid JSON")
        third = self.iteration(number="003")
        (third / "summary.txt").unlink()
        rows = self.read(pre_analysis(self.root))
        self.assertEqual(len(rows), 3)
        self.assertIn("no final record", rows[0]["analysis_error"])
        self.assertEqual(rows[0]["resource_type"], "")
        self.assertIn("finished summary", rows[1]["analysis_error"])
        self.assertIn("resource.log:", rows[1]["analysis_error"])
        self.assertIn("summary.txt:", rows[2]["analysis_error"])
        self.assertEqual(rows[2]["resource_type"], "final")

    def test_cases_export_separately_with_stable_test_ids(self):
        self.iteration()
        self.iteration(case="case_2", parameters="h3-20-5000-1-10-0-0")
        for case in ("case_1", "case_2"):
            output = pre_analysis(self.root, case_name=case)
            self.assertEqual(output, self.root / case / "analysis.csv")
            rows = self.read(output)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["test_id"].startswith(f"{self.root.name}/{case}/"))
            self.assertEqual(self.read(pre_analysis(self.root / case)), rows)
        self.assertFalse(list(self.root.glob("*.csv")))

    def test_empty_run_does_not_overwrite_previous_export(self):
        export = self.root / "case_1" / "analysis.csv"
        export.parent.mkdir()
        export.write_text("previous export")
        with self.assertRaisesRegex(ValueError, "No iteration folders"):
            pre_analysis(self.root)
        self.assertEqual(export.read_text(), "previous export")

    def test_run_test_exports_after_execution_in_current_run(self):
        import run_test
        from test_executor.environment import CaseParameters, Environment
        from test_executor.execute_trimmed_mean import execute_trimmed_mean
        events = []

        def execute(**kwargs):
            events.append("execute")
            self.iteration(get_run_directory())

        def analyze(**kwargs):
            events.append("analysis")
            return pre_analysis(**kwargs)

        def trimmed_mean(**kwargs):
            events.append("trimmed_mean")
            return execute_trimmed_mean(**kwargs)

        execute_percentiles = run_test.execute_percentiles

        def percentile_step(**kwargs):
            events.append("percentiles")
            return execute_percentiles(**kwargs)

        summary_table = run_test.case_1_summary_table

        def summary_step():
            events.append("summary")
            return summary_table()

        with patch.object(run_test, "pre_init_test", side_effect=lambda: pre_init_test(self.root)), \
                patch.object(run_test, "load_environment", return_value=Environment(case_1=CaseParameters(
                    protocols=("h2",), payloads=(20,), clients=(20,), delays=(0,),
                    packet_losses=(0,), total_requests=5000, max_streams=10, iterations=1))), \
                patch.object(run_test, "server_connection", return_value=contextlib.nullcontext()), \
                patch.object(run_test, "pre_build_containers", side_effect=lambda **kwargs: events.append("build")), \
                patch.object(run_test, "execute_case_1", side_effect=execute), \
                patch.object(run_test, "pre_analysis", side_effect=analyze), \
                patch.object(run_test, "execute_trimmed_mean", side_effect=trimmed_mean), \
                patch.object(run_test, "execute_percentiles", side_effect=percentile_step), \
                patch.object(run_test, "case_1_summary_table", side_effect=summary_step), \
                patch.object(run_test, "case_1_resource_graph_creator", side_effect=lambda: events.append("graphs")), \
                patch.object(run_test, "case_1_h2load_graph_creator", side_effect=lambda: events.append("h2load_graphs")), \
                patch.object(run_test, "case_1_percentile_graph_creator", side_effect=lambda: events.append("percentile_graphs")):
            run_test.main()
        self.assertEqual(events, ["build", "execute", "analysis", "trimmed_mean", "percentiles", "summary", "graphs", "h2load_graphs", "percentile_graphs"])
        exports = list(self.root.glob("*/case_1/analysis.csv"))
        self.assertEqual(len(exports), 1)
        self.assertEqual(len(self.read(exports[0])), 1)
        self.assertEqual(len(self.read(exports[0].parent / "trimmed_mean.csv")), 1)
        self.assertEqual(self.read(exports[0].parent / "trimmed_mean.csv")[0]
                         ["h2load_time_for_request_p99_ms"], "199.000000")
        self.assertTrue((exports[0].parent / "percentiles.csv").is_file())
        summary = self.read(exports[0].parent / "case_1_summary_table.csv")
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["Payload"], "20")
        self.assertEqual(summary[0]["Req/s (Mean)"], "60.770")
        self.assertEqual(summary[0]["Time For Request(Mean)"], "160.740 ms")
        self.assertEqual(summary[0]["Time For Connect(Mean)"], "25.580 ms")
        self.assertEqual(summary[0]["Time To 1st Byte(Mean)"], "38.530 ms")
        self.assertEqual(summary[0]["H2 Load Req/s"], "1204.040")
        self.assertNotIn("H2 Load Throughput", summary[0])
        self.assertIn("Analysis saved:", (exports[0].parent.parent / "logs.txt").read_text())


if __name__ == "__main__":
    unittest.main()
