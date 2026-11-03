import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from test_executor import executor
from test_executor.environment import ContainerLimits, ContainerSettings, Environment, load_environment
from test_executor.pre_init_test import pre_init_test
from test_executor.server_host import server_connection


class ContainerLimitTests(unittest.TestCase):
    def test_https_readiness_requires_tls_not_just_a_tcp_listener(self):
        for ready in (True, False):
            with self.subTest(ready=ready), patch.object(executor.socket, "create_connection") as connect, \
                    patch.object(executor.ssl, "_create_unverified_context") as context, \
                    patch.object(executor.time, "sleep") as sleep, \
                    patch.object(executor.time, "monotonic", side_effect=[0, 0, 1 if ready else 11]):
                context.return_value.wrap_socket.side_effect = [
                    executor.ssl.SSLError("application not ready"), MagicMock()]
                if ready:
                    executor.wait_for_server(8443)
                    self.assertEqual(connect.call_count, 2)
                    self.assertEqual(context.return_value.wrap_socket.call_count, 2)
                else:
                    with self.assertRaisesRegex(RuntimeError, "Timed out"):
                        executor.wait_for_server(8443)
                sleep.assert_called_once_with(0.2)

    def test_defaults_valid_values_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "env.yaml"
            path.write_text("remote: false\n")
            self.assertEqual(load_environment(path).container_limits, ContainerSettings())
            values = {"remote": False, "container_limits": {
                "h2load": {"memory": "512M", "cpus": 0.5, "bandwidth_mbps": 12.5},
                "http2": {"memory": None, "cpus": None, "bandwidth_mbps": None},
            }}
            path.write_text(yaml.safe_dump(values))
            config = load_environment(path)
            self.assertEqual(config.container_limits.h2load.docker_args(), [
                "--memory", "512m", "--memory-swap", "512m", "--cpus", "0.5",
                "-e", "NETEM_RATE=12.5mbit",
            ])
            self.assertEqual(config.container_limits.http2.docker_args(), [])
            self.assertEqual(config.container_limits.http3.docker_args(), [])
            for key, value in [("memory", "5m"), ("memory", 512), ("memory", "1g; bad"),
                               ("cpus", True), ("cpus", 0), ("cpus", 0.001),
                               ("cpus", float("inf")), ("bandwidth_mbps", float("nan")),
                               ("bandwidth_mbps", -1), ("bandwidth_mbps", "100mbit"),
                               ("bandwith", 100)]:
                values["container_limits"]["h2load"] = {key: value}
                path.write_text(yaml.safe_dump(values))
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    load_environment(path)
            for invalid in (None, [], {"hload": {}}, {"http3": None}):
                values["container_limits"] = invalid
                path.write_text(yaml.safe_dump(values))
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    load_environment(path)

    def test_limits_reach_local_client_and_remote_server_for_both_architectures(self):
        settings = ContainerSettings(
            h2load=ContainerLimits("1g", 2, 12.5),
            http2=ContainerLimits("512m", 0.5, 100),
            http3=ContainerLimits("768m", 1, 50),
        )
        host = MagicMock(remote=True, address="192.0.2.10", request_host="192.0.2.10",
                         readiness_host="192.0.2.10")
        host.run.return_value = subprocess.CompletedProcess([], 0, "server-id")
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(executor, "ResourceSampler"), patch.object(executor, "wait_for_server"), \
                patch.object(executor.time, "sleep"), patch.object(executor, "capture_h2load") as capture, \
                patch.object(executor, "LocalHost") as local, \
                patch("test_executor.server_host.RemoteHost", return_value=host):
            for arch in ("arm", "amd64"):
                config = Environment(remote=True, archtype=arch, container_limits=settings)
                with server_connection(config), pre_init_test(Path(temp)):
                    for protocol, server_limits in (("h2", settings.http2), ("h3", settings.http3)):
                        host.reset_mock()
                        with self.subTest(arch=arch, protocol=protocol):
                            executor.execute("case_1", protocol, 1, 100, 2, 3, 5, 1, 1)
                            server = host.run.call_args_list[0].args[0]
                            client = capture.call_args.args[0]
                            for command, limits in ((client, settings.h2load), (server, server_limits)):
                                self.assertEqual(command[command.index("--memory") + 1], limits.memory)
                                self.assertEqual(command[command.index("--memory-swap") + 1], limits.memory)
                                self.assertEqual(command[command.index("--cpus") + 1], str(limits.cpus))
                                self.assertIn(f"NETEM_RATE={limits.bandwidth_mbps}mbit", command)
                                self.assertIn("NETEM_DELAY=5ms", command)
                                self.assertIn("NETEM_LOSS=1%", command)
                            self.assertEqual(client.count("h2load"), 1)
                            self.assertEqual(client[-1], f"https://192.0.2.10:{8443 if protocol == 'h2' else 9443}/payloads/1kb")
                            if arch == "amd64":
                                self.assertEqual(client[client.index("--entrypoint") + 1], "/bin/sh")
                                self.assertIn("/benchmark-entrypoint.sh", client)
                                self.assertIn(f"{executor.ROOT_DIR}/h2load-native/docker-entrypoint.sh:/benchmark-entrypoint.sh:ro", client)
                            else:
                                self.assertNotIn("--entrypoint", client)
                            self.assertFalse(any(call.args[0][:2] == ["docker", "run"]
                                                 for call in local.return_value.run.call_args_list))

    def test_entrypoints_combine_netem_settings_and_stop_on_failure(self):
        # Run the real shell entrypoints against a fake tc/application, without Docker.
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            tc = directory / "tc"
            tc.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TRACE"\nexit "${TC_EXIT:-0}"\n')
            tc.chmod(0o755)
            for name in ("https-server", "quic-https-server", "h2load"):
                app = directory / name
                app.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STARTED"\n')
                app.chmod(0o755)
            trace, started = directory / "tc.txt", directory / "started.txt"
            env = {**os.environ, "PATH": f"{directory}:/usr/bin:/bin", "TRACE": str(trace),
                   "STARTED": str(started), "NETEM_DELAY": "10ms", "NETEM_LOSS": "2%"}
            for folder in ("h2load-native", "server/https", "server/quic-https"):
                script = executor.ROOT_DIR / folder / "docker-entrypoint.sh"
                command = ["/bin/sh", str(script)]
                if folder == "h2load-native":
                    command += ["h2load", "-n100", "https://example.test"]
                for rate in ("", "12.5mbit"):
                    for status in (0, 2):
                        started.unlink(missing_ok=True)
                        with self.subTest(folder=folder, rate=rate, status=status):
                            result = subprocess.run(command, cwd=directory,
                                                    env={**env, "NETEM_RATE": rate, "TC_EXIT": str(status)},
                                                    capture_output=True, text=True)
                            self.assertEqual(result.returncode, status, result.stderr)
                            self.assertEqual(started.exists(), status == 0)
                            self.assertEqual(trace.read_text().splitlines(), [
                                "qdisc", "replace", "dev", "eth0", "root", "netem",
                                "delay", "10ms", "loss", "2%",
                                *(["rate", rate] if rate else []),
                            ])
                            if status == 0 and folder == "h2load-native":
                                self.assertEqual(started.read_text().splitlines(), ["-n100", "https://example.test"])


if __name__ == "__main__":
    unittest.main()
