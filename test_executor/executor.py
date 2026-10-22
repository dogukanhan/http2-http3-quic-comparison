"""Run h2load cases and save each iteration under the current test results run."""

from __future__ import annotations

import argparse
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.pre_build_containers import AMD64_H2LOAD_IMAGE, read_version, selected_h2load_image
from test_executor.pre_init_test import get_run_directory, pre_init_test
from test_executor.resource_capture import ResourceSampler
from test_executor.server_host import LocalHost, get_active_environment, server_connection


ROOT_DIR = Path(__file__).resolve().parent.parent
PROTOCOLS = {
    "h2": ("https", "app-https", 8443, "8443:8443"),
    "h3": ("quic-https", "app-quic-https", 9443, "9443:9443/udp"),
}
MAX_ITERATION_ATTEMPTS = 3
BETWEEN_ATTEMPTS_SECONDS = 0.5


def has_zero_requests(output: str) -> bool:
    summary = re.search(
        r"^\s*requests:\s*\d+\s+total,\s*(\d+)\s+started,\s*"
        r"(\d+)\s+done,\s*(\d+)\s+succeeded\b",
        output, re.MULTILINE | re.IGNORECASE,
    )
    return summary is not None and all(int(value) == 0 for value in summary.groups())


def wait_for_server(port: int, host: str = "127.0.0.1") -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for server at {host}:{port}")


def capture_h2load(command: list[str], output_file) -> None:
    """Save stdout and stderr together and forward them to the run's console log."""
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    ) as process:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output_file.write(line)
                output_file.flush()
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


def run_iteration(*, protocol: str, payload: int, total_request: int, client: int,
                  max_streams: int, delay: int, packet_loss: int,
                  server_image: str, h2load_image: str, output_file,
                  request_log_path: Path, server_host=None) -> None:
    server_host = LocalHost() if server_host is None else server_host
    _folder, _image, port, port_mapping = PROTOCOLS[protocol]
    suffix = uuid.uuid4().hex[:12]
    server_name, client_name = f"test-server-{suffix}", f"test-h2load-{suffix}"
    network = ["--cap-add", "NET_ADMIN", "-e", f"NETEM_DELAY={delay}ms",
               "-e", f"NETEM_LOSS={packet_loss}%"]
    try:
        started = server_host.run(
            ["docker", "run", "--rm", "-d", "--name", server_name,
             *network, "-p", port_mapping, server_image],
            check=True,
        )
        print(f"Started {server_image} on {server_host.address}: {started.stdout.strip()}", flush=True)
        if protocol == "h2":
            wait_for_server(port, server_host.readiness_host)
        else:
            time.sleep(2)  # Match the existing runner's QUIC readiness delay.
        with ResourceSampler(server_name, request_log_path.parent / "resource.log", server_host=server_host):
            capture_h2load(
                ["docker", "run", "--rm", "--name", client_name, *network,
                 *(["--platform", "linux/amd64"] if h2load_image == AMD64_H2LOAD_IMAGE else []),
                 "--volume", f"{request_log_path.parent.resolve()}:/test-results",
                 h2load_image, "h2load", f"-n{total_request}", f"-c{client}",
                 f"-m{max_streams}", "--npn-list", protocol,
                 f"--log-file=/test-results/{request_log_path.name}",
                 f"https://{server_host.request_host}:{port}/payloads/{payload}kb"],
                output_file,
            )
    except BaseException as exc:
        # Preserve partial output and the failure alongside successful iterations.
        if isinstance(exc, subprocess.CalledProcessError):
            for text in (exc.stdout, exc.stderr):
                if text:
                    output_file.write(text)
                    print(text, flush=True)
        traceback.print_exc(file=output_file)
        raise
    finally:
        for name, host in ((client_name, LocalHost()), (server_name, server_host)):
            try:
                host.run(["docker", "rm", "-f", name], check=False, timeout=15)
            except Exception as exc:
                print(f"Could not clean up {name}: {exc}", file=sys.stderr)


def execute(folder_name: str, protocol: str, payload: int, total_request: int,
            client: int, max_streams: int, delay: int, packet_loss: int,
            iterations: int = 3) -> Path:
    """Run a case inside pre_init_test(), or initialize a new run when standalone.

    Returns the parameter directory. Repeated calls continue iteration numbering.
    Images must already have been prepared by pre_build_containers().
    """
    if not isinstance(folder_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]*", folder_name):
        raise ValueError("folder_name must be a single folder name starting with a letter or digit")
    if protocol not in PROTOCOLS:
        raise ValueError("protocol must be h2 or h3")
    for name, value in (("payload", payload), ("total_request", total_request),
                        ("client", client), ("max_streams", max_streams),
                        ("iterations", iterations)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if type(delay) is not int or delay < 0:
        raise ValueError("delay must be a nonnegative integer in milliseconds")
    if type(packet_loss) is not int or not 0 <= packet_loss <= 100:
        raise ValueError("packet_loss must be an integer between 0 and 100")
    if not (ROOT_DIR / "payloads" / f"payload_{payload}kb.json").is_file():
        raise ValueError(f"Payload {payload} KB is unavailable")

    run_dir = get_run_directory()
    if run_dir is None:
        with pre_init_test():
            return execute(folder_name, protocol, payload, total_request, client,
                           max_streams, delay, packet_loss, iterations)

    with server_connection() as server_host:
        return _execute_case(run_dir, server_host, folder_name, protocol, payload,
                             total_request, client, max_streams, delay, packet_loss, iterations)


def _execute_case(run_dir, server_host, folder_name, protocol, payload, total_request,
                  client, max_streams, delay, packet_loss, iterations) -> Path:
    server_folder, image, _port, _mapping = PROTOCOLS[protocol]
    server_image = f"{image}:{read_version(ROOT_DIR / 'server' / server_folder)}"
    h2load_image = selected_h2load_image(get_active_environment())
    parameters = f"{protocol}-{payload}-{total_request}-{client}-{max_streams}-{delay}-{packet_loss}"
    case_dir = run_dir / folder_name / parameters
    case_dir.mkdir(parents=True, exist_ok=True)

    next_number = 1
    for path in case_dir.iterdir():
        # Reserve existing iteration numbers, including the earlier flat layout.
        match = (re.fullmatch(r"(\d+)", path.name)
                 or re.fullmatch(r"iteration_(\d+)(?:\.txt|_requests\.log)", path.name))
        if match:
            next_number = max(next_number, int(match[1]) + 1)
    for iteration in range(iterations):
        iteration_dir = case_dir / f"{next_number + iteration:03d}"
        output_path = iteration_dir / "summary.txt"
        request_log_path = iteration_dir / "requests.log"
        for attempt in range(1, MAX_ITERATION_ATTEMPTS + 1):
            if iteration > 0 or attempt > 1:
                time.sleep(BETWEEN_ATTEMPTS_SECONDS)
            iteration_dir.mkdir()
            print(f"Running iteration {iteration + 1}/{iterations} "
                  f"(attempt {attempt}/{MAX_ITERATION_ATTEMPTS}): {output_path}", flush=True)
            command_error = None
            try:
                with output_path.open("x", encoding="utf-8") as output_file:
                    # Create on the host first so logs survive client cleanup.
                    request_log_path.touch(exist_ok=False)
                    run_iteration(
                        protocol=protocol, payload=payload, total_request=total_request,
                        client=client, max_streams=max_streams, delay=delay,
                        packet_loss=packet_loss, server_image=server_image,
                        h2load_image=h2load_image, output_file=output_file,
                        request_log_path=request_log_path,
                        server_host=server_host,
                    )
            except subprocess.CalledProcessError as exc:
                # h2load may also exit nonzero when no requests were started.
                command_error = exc

            if not has_zero_requests(output_path.read_text(encoding="utf-8")):
                if command_error is not None:
                    raise command_error
                break

            print(f"Discarding iteration {iteration_dir.name}, attempt {attempt}: "
                  "0 started, 0 done, 0 succeeded.", flush=True)
            # Only remove the directory just created by this attempt, after
            # run_iteration has stopped its sampler and cleaned up containers.
            shutil.rmtree(iteration_dir)
            if attempt == MAX_ITERATION_ATTEMPTS:
                raise RuntimeError(
                    f"Iteration {iteration_dir.name} produced no requests after "
                    f"{MAX_ITERATION_ATTEMPTS} attempts; stopping the case."
                ) from command_error
            print("Retrying the same iteration after 500 ms.", flush=True)
    return case_dir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder-name", required=True)
    parser.add_argument("--protocol", choices=PROTOCOLS, required=True)
    parser.add_argument("--payload", type=int, required=True, help="payload size in KB")
    parser.add_argument("-n", "--total-request", "--requests", type=int, required=True)
    parser.add_argument("-c", "--client", "--clients", type=int, required=True)
    parser.add_argument("-m", "--max-streams", type=int, required=True)
    parser.add_argument("--delay", type=int, required=True, help="network delay in ms")
    parser.add_argument("--packet-loss", type=int, required=True, help="packet loss percent")
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        execute(**vars(args))
        return 0
    except KeyboardInterrupt:
        print("Test interrupted.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
