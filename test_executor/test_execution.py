import contextlib
import importlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_executor import executor
from test_executor.environment import Environment
from test_executor.pre_init_test import get_run_directory, pre_init_test


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.results = Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.console = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.console))
        self.stack.enter_context(patch("test_executor.server_host.load_environment", return_value=Environment()))
        self.run = self.stack.enter_context(patch.object(
            executor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "server-id\n")))
        self.popen = self.stack.enter_context(patch.object(executor.subprocess, "Popen", side_effect=self.process))
        self.stack.enter_context(patch.object(executor, "wait_for_server"))
        self.sleep = self.stack.enter_context(patch.object(executor.time, "sleep"))
        self.stack.enter_context(patch("test_executor.resource_capture.read_counters", return_value={
            "cpu_counter_ns": 1_000_000, "memory_bytes": 4096,
            "network_rx_bytes": 100, "network_tx_bytes": 200,
        }))
        self.args = dict(folder_name="payload-test", protocol="h2", payload=1,
                         total_request=100, client=2, max_streams=3,
                         delay=5, packet_loss=1, iterations=2)

    @staticmethod
    def process(*args, **kwargs):
        if args:
            command = args[0]
            host_dir, container_dir = command[command.index("--volume") + 1].rsplit(":", 1)
            log_file = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--log-file="))
            assert Path(log_file).parent.as_posix() == container_dir
            (Path(host_dir) / Path(log_file).name).write_text("123456\t200\t1000\n")
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.StringIO("h2load output\nfinished in 1s\n")
        process.wait.return_value = 0
        return process

    def test_case_folder_iterations_versions_and_existing_outputs(self):
        with pre_init_test(self.results) as run_dir:
            case = executor.execute(**self.args)
            first = (case / "001" / "summary.txt").read_text()
            first_requests = (case / "001" / "requests.log").read_text()
            executor.execute(**{**self.args, "iterations": 1})
        self.assertEqual(case, run_dir / "payload-test" / "h2-1-100-2-3-5-1")
        self.assertEqual(sorted(path.name for path in case.iterdir()), ["001", "002", "003"])
        self.assertEqual(len(list(case.glob("*/summary.txt"))), 3)
        self.assertEqual((case / "001" / "summary.txt").read_text(), first)
        self.assertEqual((case / "001" / "requests.log").read_text(), first_requests)
        self.assertEqual(first_requests, "123456\t200\t1000\n")
        self.assertEqual(len(list(case.glob("*/requests.log"))), 3)
        self.assertEqual(len(list(case.glob("*/resource.log"))), 3)
        resource_rows = json.loads((case / "001" / "resource.log").read_text())
        self.assertEqual([row["type"] for row in resource_rows], ["baseline", "final"])
        self.assertIn("h2load output", first)
        self.assertIn("h2load output", (run_dir / "logs.txt").read_text())
        command = self.popen.call_args.args[0]
        version = executor.read_version(executor.ROOT_DIR / "h2load-native")
        self.assertIn(f"h2load-http3-arm64:{version}", command)
        for argument in ("-n100", "-c2", "-m3", "NETEM_DELAY=5ms", "NETEM_LOSS=1%"):
            self.assertIn(argument, command)
        self.assertEqual(command[-1], "https://host.docker.internal:8443/payloads/1kb")
        self.assertIn("--log-file=/test-results/requests.log", command)
        self.assertEqual(command[command.index("--volume") + 1],
                         f"{(case / '003').resolve()}:/test-results")
        self.assertEqual(sum(call.args[0][:3] == ["docker", "rm", "-f"]
                             for call in self.run.call_args_list), 6)
        self.assertIsNone(get_run_directory())

    def test_amd64_execution_uses_registry_image_and_platform(self):
        with patch("test_executor.server_host.load_environment", return_value=Environment(archtype="amd64")), \
                patch("test_executor.pre_build_containers.read_version", side_effect=AssertionError("ARM version must not be read")), \
                pre_init_test(self.results):
            executor.execute(**{**self.args, "iterations": 1})
        command = self.popen.call_args.args[0]
        self.assertIn("tvsjsdock/h2load-http3:latest", command)
        self.assertEqual(command[command.index("--platform") + 1], "linux/amd64")
        self.assertFalse(any("h2load-http3-arm64" in part for part in command))

    def test_standalone_creates_run_and_h3_uses_udp(self):
        initializer = importlib.import_module("test_executor.pre_init_test")
        with patch.object(initializer, "RESULTS_DIR", self.results):
            case = executor.execute(**{**self.args, "protocol": "h3", "iterations": 1})
        self.assertEqual(case.parents[1].parent, self.results)
        self.assertTrue((case.parents[1] / "logs.txt").is_file())
        self.assertIn("9443:9443/udp", self.run.call_args_list[0].args[0])
        self.assertEqual(self.popen.call_args.args[0][-1],
                         "https://host.docker.internal:9443/payloads/1kb")
        self.assertTrue((case / "001" / "requests.log").is_file())

    def test_orphan_request_logs_are_not_overwritten(self):
        with pre_init_test(self.results) as run_dir:
            case = run_dir / "payload-test" / "h2-1-100-2-3-5-1"
            case.mkdir(parents=True)
            existing_log = case / "iteration_007_requests.log"
            existing_log.write_text("previous request log")
            executor.execute(**{**self.args, "iterations": 1})
        self.assertEqual(existing_log.read_text(), "previous request log")
        self.assertTrue((case / "008" / "summary.txt").is_file())
        self.assertTrue((case / "008" / "requests.log").is_file())

    def test_incomplete_iteration_directory_is_preserved(self):
        with pre_init_test(self.results) as run_dir:
            case = run_dir / "payload-test" / "h2-1-100-2-3-5-1"
            (case / "004").mkdir(parents=True)
            executor.execute(**{**self.args, "iterations": 1})
        self.assertEqual(list((case / "004").iterdir()), [])
        self.assertTrue((case / "005" / "summary.txt").is_file())
        self.assertTrue((case / "005" / "requests.log").is_file())

    def test_failed_h2load_preserves_output_and_cleans_up(self):
        process = self.process()
        process.wait.return_value = 1
        self.popen.side_effect = None
        self.popen.return_value = process
        with pre_init_test(self.results) as run_dir:
            with self.assertRaises(subprocess.CalledProcessError):
                executor.execute(**self.args)
        files = list(run_dir.glob("*/*/*/summary.txt"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        self.assertIn("h2load output", text)
        self.assertIn("CalledProcessError", text)
        resource_rows = json.loads((files[0].parent / "resource.log").read_text())
        self.assertEqual(resource_rows[-1]["type"], "final")
        self.assertEqual(self.run.call_count, 3)  # server start and both cleanup calls

    def test_invalid_inputs_do_not_start_containers(self):
        for changes in ({"folder_name": "../outside"}, {"iterations": 0},
                        {"delay": -1}, {"packet_loss": 101}, {"protocol": "h1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                executor.execute(**{**self.args, **changes})
        self.run.assert_not_called()

    def test_zero_request_detection_requires_all_three_counters(self):
        self.assertTrue(executor.has_zero_requests(
            "requests: 100 total, 0 started, 0 done, 0 succeeded, 100 failed"))
        for counts in ((100, 100, 100), (100, 0, 0), (0, 1, 0), (0, 0, 1)):
            text = "requests: 100 total, %d started, %d done, %d succeeded" % counts
            self.assertFalse(executor.has_zero_requests(text))
        self.assertFalse(executor.has_zero_requests("unrelated error"))

    def set_attempt_results(self, results):
        attempts = iter(results)

        def process(*args, **kwargs):
            successful, returncode = next(attempts)
            child = self.process(*args, **kwargs)
            count = 100 if successful else 0
            child.stdout = io.StringIO(
                f"requests: 100 total, {count} started, {count} done, {count} succeeded\n")
            child.wait.return_value = returncode
            return child

        self.popen.side_effect = process

    def test_empty_attempt_retries_same_number_and_discards_old_files(self):
        self.set_attempt_results([(False, 0), (True, 0), (True, 0)])
        with pre_init_test(self.results) as run_dir:
            case = executor.execute(**self.args)
        self.assertEqual(self.popen.call_count, 3)
        self.assertEqual(sorted(p.name for p in case.iterdir()), ["001", "002"])
        self.assertIn("100 started", (case / "001" / "summary.txt").read_text())
        self.assertNotIn("0 started, 0 done", (case / "001" / "summary.txt").read_text())
        rows = json.loads((case / "001" / "resource.log").read_text())
        self.assertEqual(len(rows), 2)  # Only the successful attempt's samples.
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.5, 0.5])
        self.assertIn("Discarding iteration 001, attempt 1", (run_dir / "logs.txt").read_text())
        servers = [c.args[0][c.args[0].index("--name") + 1]
                   for c in self.run.call_args_list if c.args[0][:2] == ["docker", "run"]]
        self.assertEqual(len(set(servers)), 3)

    def test_third_empty_attempt_stops_and_preserves_prior_success(self):
        self.set_attempt_results([(True, 0), (False, 0), (False, 0), (False, 0)])
        with pre_init_test(self.results) as run_dir:
            with self.assertRaisesRegex(RuntimeError, "no requests after 3 attempts"):
                executor.execute(**self.args)
        case = run_dir / "payload-test" / "h2-1-100-2-3-5-1"
        self.assertEqual(sorted(p.name for p in case.iterdir()), ["001"])
        self.assertEqual(self.popen.call_count, 4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.5, 0.5, 0.5])

    def test_empty_summary_is_retried_even_with_nonzero_h2load_exit(self):
        self.set_attempt_results([(False, 1), (True, 0)])
        with pre_init_test(self.results):
            case = executor.execute(**{**self.args, "iterations": 1})
        self.assertEqual(self.popen.call_count, 2)
        self.assertNotIn("CalledProcessError", (case / "001" / "summary.txt").read_text())
        self.sleep.assert_called_once_with(0.5)


if __name__ == "__main__":
    unittest.main()
