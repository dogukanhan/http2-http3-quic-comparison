"""Build missing image versions locally or on the configured server host.

Increment a version file when its image needs rebuilding. Existing versions
are reused; latest is updated for compatibility with the existing test runners.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.server_host import server_connection
from test_executor.environment import Environment, load_environment


ROOT_DIR = Path(__file__).resolve().parent.parent
AMD64_H2LOAD_IMAGE = "tvsjsdock/h2load-http3:latest"
AMD64_NETEM_REPOSITORY = "h2load-http3-amd64-netem"


def is_amd64_h2load_image(image: str) -> bool:
    return image == AMD64_H2LOAD_IMAGE or image.startswith(f"{AMD64_NETEM_REPOSITORY}:")


def selected_h2load_image(config: Environment) -> str:
    if config.archtype == "amd64":
        if config.container_limits.h2load.bandwidth_mbps is not None:
            return f"{AMD64_NETEM_REPOSITORY}:{read_version(ROOT_DIR / 'h2load-amd64')}"
        return AMD64_H2LOAD_IMAGE
    if config.archtype == "arm":
        return f"h2load-http3-arm64:{read_version(ROOT_DIR / 'h2load-native')}"
    raise ValueError("archtype must be amd64 or arm")


def check_docker_daemon() -> None:
    print("Checking Docker daemon access...", flush=True)
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            check=True, capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI was not found. Install Docker and make it available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Docker daemon did not respond within 15 seconds.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(
            "Docker daemon is not accessible. Start Docker and check your access permissions."
            + (f"\n{detail}" if detail else "")
        ) from exc
    print(f"Docker daemon is accessible (version {result.stdout.strip()}).", flush=True)


def run_build(command: list[str]) -> None:
    """Forward both output streams through Python so pre_init_test logs them."""
    print(f"Running: {shlex.join(command)}", flush=True)
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    ) as process:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
            returncode = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


def read_version(build_dir: Path) -> str:
    version_file = build_dir / "version.txt"
    version = version_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", version) or version == "latest":
        raise RuntimeError(f"Invalid image version in {version_file}: {version!r}")
    return version


def image_exists(image: str, host=None) -> bool:
    # A successful empty listing means absent; daemon/permission errors must fail.
    command = ["docker", "image", "ls", "--quiet", "--filter", f"reference={image}"]
    result = (host.run(command, timeout=15) if host is not None else
              subprocess.run(command, check=True, capture_output=True, text=True, timeout=15))
    return bool(result.stdout.strip())


def pre_build_containers(config: Environment | None = None) -> None:
    config = load_environment() if config is None else config
    check_docker_daemon()
    with server_connection(config) as server:
        if server.remote:
            info = server.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
            print(f"Remote Docker at {server.address} is accessible (version {info.stdout.strip()}).", flush=True)
        prepare_images(server, config)


def prepare_images(server, config: Environment) -> None:
    client_image = selected_h2load_image(config)
    builds = [
        ("app-https", ROOT_DIR / "server" / "https" / "Dockerfile", ROOT_DIR, False),
        ("app-quic-https", ROOT_DIR / "server" / "quic-https" / "Dockerfile", ROOT_DIR, False),
    ]
    if config.archtype == "arm":
        builds.insert(0, ("h2load-http3-arm64", ROOT_DIR / "h2load-native" / "Dockerfile",
                          ROOT_DIR / "h2load-native", True))
    versioned_builds = []
    for image, dockerfile, context, arm64 in builds:
        if not dockerfile.is_file():
            raise FileNotFoundError(f"Dockerfile not found: {dockerfile}")
        version = read_version(dockerfile.parent)
        versioned_builds.append((image, f"{image}:{version}", dockerfile, context, arm64))

    if config.archtype == "amd64":
        if image_exists(client_image):
            print(f"Using existing local h2load image: {client_image}", flush=True)
        elif client_image != AMD64_H2LOAD_IMAGE:
            print(f"Adding traffic control to the AMD64 h2load image: {client_image}", flush=True)
            context = ROOT_DIR / "h2load-amd64"
            run_build(["docker", "buildx", "build", "--platform", "linux/amd64", "--load",
                       "--progress=plain", "-f", str(context / "Dockerfile"),
                       "-t", client_image, str(context)])
        else:
            print(f"Pulling h2load image: {client_image}", flush=True)
            run_build(["docker", "pull", "--platform", "linux/amd64", client_image])

    for image, tagged_image, dockerfile, context, arm64 in versioned_builds:
        # The load generator always stays local; both server images follow env.yaml.
        remote_host = server if server.remote and not arm64 else None
        location = server.address if remote_host is not None else "local Docker"
        if image_exists(tagged_image, remote_host):
            print(f"\nSkipping build: {tagged_image} already exists on {location}.", flush=True)
            # Existing runners use unversioned names, which resolve to latest.
            command = ["docker", "image", "tag", tagged_image, f"{image}:latest"]
            if remote_host is not None:
                remote_host.run(command, stream=True, timeout=15)
            else:
                run_build(command)
            continue
        print(f"\nBuilding {tagged_image} on {location}...", flush=True)
        if remote_host is not None:
            remote_host.build_image(ROOT_DIR, dockerfile, tagged_image, f"{image}:latest")
            print(f"Built {tagged_image} on {location}.", flush=True)
            continue
        command = (["docker", "buildx", "build", "--platform", "linux/arm64", "--load"]
                   if arm64 else ["docker", "build"])
        command.extend(["--progress=plain", "-f", str(dockerfile),
                        "-t", tagged_image, "-t", f"{image}:latest", str(context)])
        run_build(command)
        print(f"Built {tagged_image}.", flush=True)
    print("All requested test image versions are available.", flush=True)


def main() -> int:
    try:
        pre_build_containers()
        return 0
    except KeyboardInterrupt:
        print("Image builds interrupted.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
