"""Local or SSH command execution for server builds, containers, and counters."""

from __future__ import annotations

import codecs
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from test_executor.environment import Environment, load_environment


class LocalHost:
    remote = False
    address = "local"
    request_host = "host.docker.internal"
    readiness_host = "127.0.0.1"

    def run(self, command: list[str], *, check=True, timeout=None, stream=False):
        if stream:
            from test_executor.pre_build_containers import run_build
            run_build(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.run(command, check=check, capture_output=True, text=True, timeout=timeout)

    def close(self) -> None:
        pass


class RemoteHost:
    remote = True

    def __init__(self, config: Environment):
        self.address = config.ip_address
        self.readiness_host = self.address
        self.request_host = f"[{self.address}]" if ":" in self.address else self.address
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("Install SSH dependencies: python3 -m pip install -r requirements.txt") from None
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            self.client.connect(
                hostname=self.address, port=config.ssh_port, username=config.username,
                password=config.password or None, timeout=15, banner_timeout=15,
                auth_timeout=15, allow_agent=not bool(config.password),
                look_for_keys=not bool(config.password),
            )
            self.client.get_transport().set_keepalive(30)
        except Exception:
            self.client.close()
            # Connection failures must not include arbitrary credential-bearing text.
            raise RuntimeError(
                f"SSH connection to {self.address}:{config.ssh_port} failed. Check the username/password, "
                "network access, and the verified server key in ~/.ssh/known_hosts."
            ) from None
        print(f"Connected to remote server {self.address} via SSH.", flush=True)

    def run(self, command: list[str], *, check=True, timeout=None, stream=False):
        """Use one SSH channel per command; drain both streams to avoid deadlocks."""
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError(f"SSH connection to {self.address} is closed")
        shell_command = shlex.join(command)
        if stream:
            print(f"Remote {self.address}: {shell_command}", flush=True)
        channel = transport.open_session(timeout=timeout or 15)
        output, errors = bytearray(), bytearray()
        stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            channel.settimeout(timeout or 15)
            channel.exec_command(shell_command)
            channel.shutdown_write()
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                received = False
                if channel.recv_ready():
                    data = channel.recv(65536)
                    output.extend(data)
                    if stream:
                        print(stdout_decoder.decode(data), end="", flush=True)
                    received = True
                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(65536)
                    errors.extend(data)
                    if stream:
                        print(stderr_decoder.decode(data), end="", flush=True)
                    received = True
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if not transport.is_active():
                    raise RuntimeError(f"SSH connection to {self.address} was lost")
                if not received:
                    time.sleep(0.01)
            status = channel.recv_exit_status()
            if stream:
                print(stdout_decoder.decode(b"", final=True) + stderr_decoder.decode(b"", final=True), end="", flush=True)
        finally:
            channel.close()
        result = subprocess.CompletedProcess(command, status, output.decode("utf-8", "replace"),
                                             errors.decode("utf-8", "replace"))
        if check and status != 0:
            raise subprocess.CalledProcessError(status, command, result.stdout, result.stderr)
        return result

    def build_image(self, root: Path, dockerfile: Path, tagged_image: str, latest: str) -> None:
        """Transfer only this server's sources, payloads, and required certificates."""
        remote_dir = f"/tmp/test-executor-{uuid.uuid4().hex}"
        self.run(["mkdir", "-m", "700", remote_dir], timeout=15)
        try:
            with tempfile.TemporaryDirectory(prefix="test-executor-build-") as temporary:
                archive = Path(temporary) / "context.tar"
                create_build_archive(root, dockerfile, archive)
                with self.client.open_sftp() as sftp:
                    sftp.get_channel().settimeout(60)
                    sftp.put(str(archive), f"{remote_dir}/context.tar")
                self.run(["tar", "-xf", f"{remote_dir}/context.tar", "-C", remote_dir], timeout=60)
                self.run(["rm", f"{remote_dir}/context.tar"], timeout=15)
                self.run(["docker", "build", "--progress=plain", "-f",
                          f"{remote_dir}/{dockerfile.relative_to(root).as_posix()}",
                          "-t", tagged_image, "-t", latest, remote_dir], stream=True)
        finally:
            try:
                self.run(["rm", "-rf", remote_dir], timeout=15)
            except Exception as exc:
                print(f"Could not remove remote build context {remote_dir}: {exc}", file=sys.stderr)

    def close(self) -> None:
        self.client.close()


def create_build_archive(root: Path, dockerfile: Path, archive: Path) -> None:
    def exclude_generated(info):
        if any(part in {".git", "__pycache__", ".DS_Store"} for part in Path(info.name).parts):
            return None
        return info

    with tarfile.open(archive, "w") as tar:
        for source in (dockerfile.parent, root / "payloads"):
            tar.add(source, arcname=source.relative_to(root).as_posix(), filter=exclude_generated)
        if dockerfile.parent.name == "quic-https":
            # The Dockerfile needs certs/, even when the app generates its own
            # certificate. Do not upload TLS session-key logs or other secrets.
            directory = tarfile.TarInfo("certs")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o700
            tar.addfile(directory)
            for name in ("localhost.pem", "localhost-key.pem"):
                certificate = root / "certs" / name
                if certificate.is_file():
                    tar.add(certificate, arcname=f"certs/{name}")


_ACTIVE_HOST = ContextVar("test_server_host", default=None)
_ACTIVE_ENVIRONMENT = ContextVar("test_environment", default=None)


def get_active_environment() -> Environment:
    config = _ACTIVE_ENVIRONMENT.get()
    if config is None:
        raise RuntimeError("No active server connection configuration")
    return config


@contextmanager
def server_connection(config: Environment | None = None):
    """Reuse a connection across image preparation and all iterations in a run."""
    active = _ACTIVE_HOST.get()
    if active is not None:
        yield active
        return
    config = load_environment() if config is None else config
    host = RemoteHost(config) if config.remote else LocalHost()
    token = _ACTIVE_HOST.set(host)
    config_token = _ACTIVE_ENVIRONMENT.set(config)
    try:
        yield host
    finally:
        _ACTIVE_HOST.reset(token)
        _ACTIVE_ENVIRONMENT.reset(config_token)
        host.close()
