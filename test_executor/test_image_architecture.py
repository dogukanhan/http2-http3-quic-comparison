import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_executor.environment import Environment, load_environment
from test_executor import pre_build_containers as images
from test_executor.server_host import get_active_environment, server_connection


class ImageArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("server/https", "server/quic-https"):
            directory = self.root / folder
            directory.mkdir(parents=True)
            (directory / "Dockerfile").write_text("FROM scratch\n")
            (directory / "version.txt").write_text("1.2.3")
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(images, "ROOT_DIR", self.root))
        self.server = MagicMock(remote=True, address="192.0.2.10")

    def test_architecture_yaml_values_and_default(self):
        path = self.root / "env.yaml"
        path.write_text("remote: false\n")
        self.assertEqual(load_environment(path).archtype, "arm")
        for archtype in ("amd64", "arm"):
            path.write_text(f"remote: false\narchtype: {archtype}\n")
            self.assertEqual(load_environment(path).archtype, archtype)
        for invalid in ("x86", "arm64", "false", "[]", "null"):
            path.write_text(f"remote: false\narchtype: {invalid}\n")
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "archtype"):
                load_environment(path)

    def test_amd64_pulls_locally_without_arm_build_files(self):
        with patch.object(images, "image_exists", return_value=False) as exists, \
                patch.object(images, "run_build") as build:
            images.prepare_images(self.server, Environment(archtype="amd64"))
        build.assert_called_once_with(["docker", "pull", "--platform", "linux/amd64", images.AMD64_H2LOAD_IMAGE])
        self.assertEqual(exists.call_args_list[0].args, (images.AMD64_H2LOAD_IMAGE,))
        self.assertEqual(self.server.build_image.call_count, 2)
        self.assertEqual(images.selected_h2load_image(Environment(archtype="amd64")), images.AMD64_H2LOAD_IMAGE)

    def test_cached_amd64_skips_pull_and_build(self):
        with patch.object(images, "image_exists", return_value=True), patch.object(images, "run_build") as build:
            images.prepare_images(self.server, Environment(archtype="amd64"))
        build.assert_not_called()
        self.server.build_image.assert_not_called()

    def test_arm_builds_missing_version_and_reuses_existing_version(self):
        directory = self.root / "h2load-native"
        directory.mkdir()
        (directory / "Dockerfile").write_text("FROM scratch\n")
        (directory / "version.txt").write_text("2.4.6")
        self.assertEqual(images.selected_h2load_image(Environment(archtype="arm")), "h2load-http3-arm64:2.4.6")
        for cached in (False, True):
            with self.subTest(cached=cached), patch.object(images, "image_exists", return_value=cached) as exists, \
                    patch.object(images, "run_build") as build:
                images.prepare_images(self.server, Environment(archtype="arm"))
            self.assertEqual(exists.call_args_list[0].args, ("h2load-http3-arm64:2.4.6", None))
            build.assert_called_once()
            command = build.call_args.args[0]
            if cached:
                self.assertEqual(command, ["docker", "image", "tag", "h2load-http3-arm64:2.4.6", "h2load-http3-arm64:latest"])
            else:
                self.assertEqual(command[:6], ["docker", "buildx", "build", "--platform", "linux/arm64", "--load"])
                self.assertIn("h2load-http3-arm64:2.4.6", command)

    def test_architecture_is_kept_for_the_entire_connection(self):
        config = Environment(archtype="amd64")
        with server_connection(config):
            with server_connection():
                self.assertIs(get_active_environment(), config)
        with self.assertRaisesRegex(RuntimeError, "No active"):
            get_active_environment()
