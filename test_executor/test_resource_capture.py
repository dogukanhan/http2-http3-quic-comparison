import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_executor.resource_capture import ResourceSampler, parse_counters, read_counters


NETWORK = """NETWORK
Inter-| Receive | Transmit
eth0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0
lo: 900 1 0 0 0 0 0 0 900 1 0 0 0 0 0 0
"""


def counters(cpu):
    return {"cpu_counter_ns": cpu, "memory_bytes": 4096,
            "network_rx_bytes": 100, "network_tx_bytes": 200}


class ResourceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log_path = Path(self.temp.name) / "resource.log"

    def rows(self):
        return json.loads(self.log_path.read_text())

    def test_cgroup_versions_normalize_to_nanoseconds(self):
        v2 = "CGROUP_V2\nusage_usec 250000\nuser_usec 200000\nsystem_usec 50000\nMEMORY\n4096\n" + NETWORK
        v1 = "CGROUP_V1\n250000000\nMEMORY\n4096\n" + NETWORK
        self.assertEqual(parse_counters(v2), counters(250_000_000))
        self.assertEqual(parse_counters(v1), counters(250_000_000))
        with self.assertRaises(KeyError):
            parse_counters(v2.replace("usage_usec", "missing"))
        with self.assertRaises(ValueError):
            parse_counters(v1.replace("250000000", "-1"))

    def test_read_uses_bounded_docker_exec(self):
        raw = "CGROUP_V1\n1000\nMEMORY\n4096\n" + NETWORK
        with patch("test_executor.resource_capture.subprocess.run", return_value=subprocess.CompletedProcess([], 0, raw)) as run:
            self.assertEqual(read_counters("server"), counters(1000))
        self.assertEqual(run.call_args.args[0][:4], ["docker", "exec", "server", "sh"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_short_test_has_baseline_final_and_correct_cpu_time(self):
        sampler = ResourceSampler("server", self.log_path)
        with patch("test_executor.resource_capture.read_counters", side_effect=[counters(1_000_000_000), counters(1_250_000_000)]):
            with sampler:
                self.assertEqual(self.rows()[0]["type"], "baseline")
        rows = self.rows()
        self.assertEqual([r["type"] for r in rows], ["baseline", "final"])
        self.assertEqual(rows[0]["cpu_consumed_ns"], 0)
        self.assertIsNone(rows[0]["cpu_percent"])
        self.assertEqual(rows[-1]["cpu_delta_ns"], 250_000_000)
        self.assertEqual(rows[-1]["cpu_consumed_seconds"], 0.25)
        self.assertEqual(rows[-1]["target_interval_ms"], 500)
        self.assertGreater(rows[-1]["cpu_percent"], 0)
        self.assertFalse(sampler._thread.is_alive())

    def test_periodic_reads_run_in_background_until_stopped(self):
        sampler = ResourceSampler("server", self.log_path)
        sampler.interval_seconds = 0.01
        sampled = threading.Event()
        calls = []

        def read(_container):
            calls.append(threading.get_ident())
            if len(calls) >= 3:
                sampled.set()
            return counters(len(calls) * 1000)

        with patch("test_executor.resource_capture.read_counters", side_effect=read):
            with sampler:
                self.assertTrue(sampled.wait(2))
        rows = self.rows()
        self.assertGreaterEqual(len(rows), 4)
        self.assertEqual(rows[0]["type"], "baseline")
        self.assertEqual(rows[-1]["type"], "final")
        self.assertEqual(rows[-1]["cpu_consumed_ns"], (len(rows) - 1) * 1000)
        self.assertTrue(all(ident != threading.get_ident() for ident in calls))

    def test_failed_baseline_logs_error_and_stops_thread(self):
        sampler = ResourceSampler("server", self.log_path)
        with patch("test_executor.resource_capture.read_counters", side_effect=RuntimeError("no counter")):
            with self.assertRaisesRegex(RuntimeError, "no counter"):
                with sampler:
                    self.fail("load must not start without a baseline")
        self.assertEqual(self.rows()[-1]["type"], "error")
        self.assertFalse(sampler._thread.is_alive())

    def test_decreasing_counter_fails_instead_of_negative_cpu_time(self):
        sampler = ResourceSampler("server", self.log_path)
        with patch("test_executor.resource_capture.read_counters", side_effect=[counters(2000), counters(1000)]):
            with self.assertRaisesRegex(RuntimeError, "decreased"):
                with sampler:
                    pass
        self.assertEqual(self.rows()[-1]["type"], "error")

    def test_capture_error_does_not_hide_load_failure(self):
        sampler = ResourceSampler("server", self.log_path)
        with patch("test_executor.resource_capture.read_counters", side_effect=[counters(0), RuntimeError("read failed")]), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaisesRegex(ValueError, "load failed"):
                with sampler:
                    raise ValueError("load failed")
        self.assertIn("read failed", stderr.getvalue())
        self.assertEqual(self.rows()[-1]["type"], "error")


if __name__ == "__main__":
    unittest.main()
