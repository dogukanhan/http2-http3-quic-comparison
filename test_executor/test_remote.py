import contextlib
import io
import json
import shlex
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_executor.environment import Environment, load_environment
from test_executor.server_host import LocalHost, RemoteHost, create_build_archive, server_connection
from test_executor.resource_capture import ResourceSampler, read_counters
from test_executor import executor, pre_build_containers
from test_executor.pre_init_test import pre_init_test


RAW_COUNTERS = ("CGROUP_V2\nusage_usec 1000\nMEMORY\n4096\nNETWORK\n"
                "eth0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n")


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "env.yaml"

    def test_false_flag_is_local_and_password_is_not_in_repr(self):
        self.path.write_text('remote: false\npassword: "private-value"\n')
        config = load_environment(self.path)
        self.assertFalse(config.remote)
        self.assertNotIn("private-value", repr(config))

    def test_remote_fields_and_ipv6(self):
        self.path.write_text('remote: true\nusername: ubuntu\npassword: "private-value"\nip_address: "2001:db8::10"\nssh_port: 2222\n')
        config = load_environment(self.path)
        self.assertEqual(config.ssh_port, 2222)
        self.assertEqual(config.password, "private-value")

    def test_invalid_yaml_does_not_leak_password(self):
        self.path.write_text('remote: true\npassword: ["private-value"\n')
        with self.assertRaisesRegex(ValueError, "Invalid YAML") as caught:
            load_environment(self.path)
        self.assertNotIn("private-value", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_case_switch_defaults_and_explicit_selection(self):
        self.path.write_text("remote: false\n")
        config = load_environment(self.path)
        self.assertTrue(config.case_1_enabled)
        self.assertFalse(config.case_2_enabled)
        self.assertFalse(config.case_3_enabled)
        self.assertFalse(config.case_4_enabled)
        self.path.write_text("remote: false\ncase_1_enabled: false\ncase_2_enabled: true\ncase_3_enabled: true\ncase_4_enabled: true\n")
        config = load_environment(self.path)
        self.assertFalse(config.case_1_enabled)
        self.assertTrue(config.case_2_enabled)
        self.assertTrue(config.case_3_enabled)
        self.assertTrue(config.case_4_enabled)
        for field in ("case_1_enabled", "case_2_enabled", "case_3_enabled", "case_4_enabled"):
            self.path.write_text(f'remote: false\n{field}: "true"\n')
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                load_environment(self.path)

    def test_invalid_configuration_fails_before_connecting(self):
        for text in ('remote: "false"', 'remote: true',
                     'remote: true\nusername: ubuntu\nip_address: not-an-ip',
                     'remote: false\nssh_port: 0', 'remote: false\npassword: 123',
                     'remote: false\nremtoe: true'):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_environment(self.path)

    def test_explicit_case_parameters_are_loaded_without_expanding_ranges(self):
        self.path.write_text("""remote: false
case_2:
  protocols: [h3, h2]
  payloads: [5, 20]
  clients: [2, 10, 100]
  delays: [0, 50]
  packet_losses: [0, 2]
  total_requests: 137
  max_streams: 3
  iterations: 2
""")
        case = load_environment(self.path).case_parameters("case_2")
        self.assertEqual(case.protocols, ("h3", "h2"))
        self.assertEqual(case.payloads, (5, 20))
        self.assertEqual(case.clients, (2, 10, 100))
        self.assertEqual(case.delays, (0, 50))
        self.assertEqual(case.packet_losses, (0, 2))
        self.assertEqual((case.total_requests, case.max_streams, case.iterations), (137, 3, 2))

    def test_invalid_case_parameters_are_rejected(self):
        import yaml
        valid = dict(protocols=["h2", "h3"], payloads=[20], clients=[20], delays=[0, 10],
                     packet_losses=[0, 1], total_requests=5000, max_streams=10, iterations=5)
        for field, value in (("protocols", ["http1"]), ("payloads", []), ("payloads", [True]),
                             ("clients", "1-100"), ("clients", {"start": 1, "end": 100}),
                             ("delays", [0, 0]), ("delays", [-1]), ("delays", [1.5]),
                             ("packet_losses", [101]), ("iterations", 0), ("iterations", True),
                             ("total_requests", "5000"), ("max_streams", -1)):
            self.path.write_text(yaml.safe_dump({"remote": False, "case_1": {**valid, field: value}}))
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                load_environment(self.path)
        for invalid in ({}, {**valid, "typo": 1}, None, []):
            self.path.write_text(yaml.safe_dump({"remote": False, "case_1": invalid}))
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "case_1"):
                load_environment(self.path)


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def fake_host(self):
        host = MagicMock()
        host.remote = True
        host.address = host.request_host = host.readiness_host = "192.0.2.10"

        def run(command, **kwargs):
            output = RAW_COUNTERS if command[:2] == ["docker", "exec"] else "remote-id\n"
            return subprocess.CompletedProcess(command, 0, output, "")

        host.run.side_effect = run
        return host

    def test_shared_connection_is_reused_then_closed(self):
        host = self.fake_host()
        with patch("test_executor.server_host.RemoteHost", return_value=host) as create:
            config = Environment(remote=True, username="ubuntu", ip_address="192.0.2.10")
            with server_connection(config) as outer:
                with server_connection() as inner:
                    self.assertIs(inner, outer)
                host.close.assert_not_called()
            host.close.assert_called_once()
            create.assert_called_once_with(config)

    def test_local_config_does_not_open_ssh(self):
        with patch("test_executor.server_host.RemoteHost") as create:
            with server_connection(Environment()) as host:
                self.assertIsInstance(host, LocalHost)
        create.assert_not_called()

    def test_password_auth_checks_host_key_and_ipv6_url(self):
        with patch("paramiko.SSHClient") as client:
            config = Environment(remote=True, username="ubuntu", password="private-value",
                                 ip_address="2001:db8::10", ssh_port=2222)
            host = RemoteHost(config)
        kwargs = client.return_value.connect.call_args.kwargs
        self.assertEqual(kwargs["password"], "private-value")
        self.assertFalse(kwargs["allow_agent"])
        self.assertFalse(kwargs["look_for_keys"])
        client.return_value.load_system_host_keys.assert_called_once()
        policy = client.return_value.set_missing_host_key_policy.call_args.args[0]
        self.assertEqual(type(policy).__name__, "RejectPolicy")
        self.assertEqual(host.request_host, "[2001:db8::10]")

    def test_remote_command_quotes_arguments_and_preserves_output(self):
        host = RemoteHost.__new__(RemoteHost)
        host.address = "192.0.2.10"
        host.client = MagicMock()
        channel = host.client.get_transport.return_value.open_session.return_value
        channel.recv_ready.side_effect = [True, False]
        channel.recv_stderr_ready.side_effect = [True, False]
        channel.recv.return_value = b"output\n"
        channel.recv_stderr.return_value = b"error\n"
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 2
        command = ["docker", "exec", "server", "sh", "-c", "echo $HOME; printf 'quoted text'"]
        with self.assertRaises(subprocess.CalledProcessError) as error:
            host.run(command, timeout=5)
        channel.exec_command.assert_called_once_with(shlex.join(command))
        self.assertEqual(error.exception.stdout, "output\n")
        self.assertEqual(error.exception.stderr, "error\n")
        channel.close.assert_called_once()

    def test_resource_counters_use_remote_host_only(self):
        host = self.fake_host()
        with patch("test_executor.resource_capture.subprocess.run") as local:
            counters = read_counters("remote-server", host)
            with ResourceSampler("remote-server", self.root / "resource.log", server_host=host):
                pass
        self.assertEqual(counters["cpu_counter_ns"], 1_000_000)
        local.assert_not_called()
        self.assertTrue(all(call.args[0][:3] == ["docker", "exec", "remote-server"]
                            for call in host.run.call_args_list))
        records = json.loads((self.root / "resource.log").read_text())
        self.assertTrue(all(row["server_host"] == "192.0.2.10" for row in records))

    def test_server_runs_remotely_but_h2load_and_files_stay_local(self):
        host = self.fake_host()
        for protocol, port, mapping in (("h2", 8443, "8443:8443"), ("h3", 9443, "9443:9443/udp")):
            directory = self.root / protocol
            directory.mkdir()
            requests = directory / "requests.log"
            requests.touch()
            with self.subTest(protocol=protocol), patch.object(executor, "capture_h2load") as capture, \
                    patch.object(executor, "wait_for_server") as ready, patch.object(executor.time, "sleep"), \
                    patch.object(executor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")) as local:
                with (directory / "summary.txt").open("w") as summary:
                    executor.run_iteration(protocol=protocol, payload=20, total_request=100, client=2,
                                           max_streams=3, delay=5, packet_loss=1,
                                           server_image="server:1.0", h2load_image="h2load:1.0",
                                           output_file=summary, request_log_path=requests, server_host=host)
                command = capture.call_args.args[0]
                self.assertEqual(command[-1], f"https://192.0.2.10:{port}/payloads/20kb")
                self.assertIn(f"{directory.resolve()}:/test-results", command)
                self.assertIn("h2load:1.0", command)
                local.assert_called_once()  # Only local h2load cleanup, never server launch.
                self.assertTrue(local.call_args.args[0][-1].startswith("test-h2load-"))
                if protocol == "h2":
                    ready.assert_called_once_with(port, "192.0.2.10")
                server_calls = [call.args[0] for call in host.run.call_args_list if call.args[0][:2] == ["docker", "run"]]
                self.assertIn(mapping, server_calls[-1])
                self.assertEqual(server_calls[-1][-1], "server:1.0")

    def test_prebuild_targets_local_client_and_remote_servers(self):
        host = self.fake_host()
        with patch.object(pre_build_containers, "check_docker_daemon"), \
                patch.object(pre_build_containers, "server_connection", return_value=contextlib.nullcontext(host)), \
                patch.object(pre_build_containers, "image_exists", return_value=False) as exists, \
                patch.object(pre_build_containers, "run_build") as local_build:
            pre_build_containers.pre_build_containers(config=Environment())
        self.assertEqual([call.args[1] for call in exists.call_args_list], [None, host, host])
        local_build.assert_called_once()
        self.assertIn("linux/arm64", local_build.call_args.args[0])
        self.assertEqual(host.build_image.call_count, 2)
        self.assertTrue(all("app-" in call.args[2] for call in host.build_image.call_args_list))

    def test_remote_existing_versions_skip_remote_builds(self):
        host = self.fake_host()
        with patch.object(pre_build_containers, "image_exists", return_value=True), \
                patch.object(pre_build_containers, "run_build") as local:
            pre_build_containers.prepare_images(host, Environment())
        host.build_image.assert_not_called()
        local.assert_called_once()
        self.assertEqual(len([c for c in host.run.call_args_list if c.args[0][:3] == ["docker", "image", "tag"]]), 2)

    def test_archive_excludes_credentials_results_and_tls_session_logs(self):
        server = self.root / "server" / "quic-https"
        server.mkdir(parents=True)
        (server / "Dockerfile").write_text("FROM alpine")
        (server / "main.go").write_text("package main")
        (self.root / "payloads").mkdir()
        (self.root / "payloads" / "payload_1kb.json").write_text("{}")
        (self.root / "env.yaml").write_text("password: secret")
        certs = self.root / "certs"
        certs.mkdir()
        (certs / "localhost.pem").write_text("test certificate")
        (certs / "localhost-key.pem").write_text("test certificate key")
        (certs / "quic-https-tls-keys.log").write_text("not for transfer")
        archive = self.root / "context.tar"
        create_build_archive(self.root, server / "Dockerfile", archive)
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        self.assertIn("server/quic-https/Dockerfile", names)
        self.assertIn("payloads/payload_1kb.json", names)
        self.assertIn("certs/localhost.pem", names)
        self.assertNotIn("env.yaml", names)
        self.assertNotIn("certs/quic-https-tls-keys.log", names)

    def test_remote_build_uploads_context_and_cleans_up_even_on_failure(self):
        server = self.root / "server" / "https"
        server.mkdir(parents=True)
        dockerfile = server / "Dockerfile"
        dockerfile.write_text("FROM alpine")
        (self.root / "payloads").mkdir()
        for failure in (None, "upload", "build"):
            with self.subTest(failure=failure):
                host = RemoteHost.__new__(RemoteHost)
                host.client = MagicMock()
                host.run = MagicMock()
                sftp = host.client.open_sftp.return_value.__enter__.return_value

                def upload(local, remote):
                    with tarfile.open(local) as archive:
                        self.assertIn("server/https/Dockerfile", archive.getnames())
                    self.assertTrue(remote.endswith("/context.tar"))
                    if failure == "upload":
                        raise OSError("upload failed")

                def run(command, **kwargs):
                    if command[:2] == ["docker", "build"] and failure == "build":
                        raise subprocess.CalledProcessError(1, command)

                sftp.put.side_effect = upload
                host.run.side_effect = run
                if failure:
                    with self.assertRaises((OSError, subprocess.CalledProcessError)):
                        host.build_image(self.root, dockerfile, "app-https:1.0", "app-https:latest")
                else:
                    host.build_image(self.root, dockerfile, "app-https:1.0", "app-https:latest")
                sftp.put.assert_called_once()
                remote_dir = host.run.call_args_list[0].args[0][-1]
                self.assertEqual(host.run.call_args.args[0], ["rm", "-rf", remote_dir])
                builds = [call for call in host.run.call_args_list if call.args[0][:2] == ["docker", "build"]]
                if failure == "upload":
                    self.assertEqual(builds, [])
                else:
                    self.assertEqual(len(builds), 1)
                    self.assertIn(f"{remote_dir}/server/https/Dockerfile", builds[0].args[0])
                    self.assertIn("app-https:1.0", builds[0].args[0])
                    self.assertTrue(builds[0].kwargs["stream"])


if __name__ == "__main__":
    unittest.main()
