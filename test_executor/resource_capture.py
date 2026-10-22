"""Sample Linux container resource counters while a benchmark is running."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# Both server images provide a POSIX shell and cat. Read raw counters rather
# than docker stats' rounded percentages and slow first-sample calculation.
READ_COUNTERS = r"""
set -eu
if [ -r /sys/fs/cgroup/cgroup.controllers ]; then
    printf 'CGROUP_V2\n'
    cat /sys/fs/cgroup/cpu.stat
    printf 'MEMORY\n'
    cat /sys/fs/cgroup/memory.current
else
    cpu_file=''
    for candidate in /sys/fs/cgroup/cpuacct/cpuacct.usage /sys/fs/cgroup/cpu,cpuacct/cpuacct.usage /sys/fs/cgroup/cpuacct.usage; do
        if [ -r "$candidate" ]; then cpu_file="$candidate"; break; fi
    done
    if [ -z "$cpu_file" ]; then
        echo 'CPU accounting counter is unavailable' >&2
        exit 1
    fi
    printf 'CGROUP_V1\n'
    cat "$cpu_file"
    printf 'MEMORY\n'
    if [ -r /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
        cat /sys/fs/cgroup/memory/memory.usage_in_bytes
    else
        cat /sys/fs/cgroup/memory.usage_in_bytes
    fi
fi
printf 'NETWORK\n'
cat /proc/net/dev
"""


def parse_counters(text: str) -> dict[str, int]:
    cpu_text, rest = text.split("MEMORY\n", 1)
    memory_text, network_text = rest.split("NETWORK\n", 1)
    lines = cpu_text.strip().splitlines()
    if lines[0] == "CGROUP_V2":
        values = dict(line.split() for line in lines[1:])
        cpu_ns = int(values["usage_usec"]) * 1000
    elif lines[0] == "CGROUP_V1":
        cpu_ns = int(lines[1])
    else:
        raise ValueError("Unsupported CPU counter format")
    received = transmitted = 0
    interfaces = 0
    for line in network_text.splitlines():
        if ":" not in line:
            continue
        interface, data = line.split(":", 1)
        if interface.strip() == "lo":
            continue
        fields = data.split()
        if len(fields) != 16:
            raise ValueError(f"Invalid network counters for {interface.strip()}")
        rx, tx = int(fields[0]), int(fields[8])
        if rx < 0 or tx < 0:
            raise ValueError("Network counters cannot be negative")
        received += rx
        transmitted += tx
        interfaces += 1
    if not interfaces:
        raise ValueError("No non-loopback network counters found")
    counters = {"cpu_counter_ns": cpu_ns, "memory_bytes": int(memory_text.strip()),
                "network_rx_bytes": received, "network_tx_bytes": transmitted}
    if any(value < 0 for value in counters.values()):
        raise ValueError("Resource counters cannot be negative")
    return counters


def read_counters(container: str, server_host=None) -> dict[str, int]:
    command = ["docker", "exec", container, "sh", "-c", READ_COUNTERS]
    result = (server_host.run(command, timeout=5) if server_host is not None else
              subprocess.run(command, check=True, capture_output=True, text=True, timeout=5))
    return parse_counters(result.stdout)


class _JsonArrayLog:
    """Append records before the closing bracket, without rewriting earlier data."""

    def __init__(self, file):
        self.file = file
        self.count = 0
        file.write("[\n")
        self.position = file.tell()
        file.write("]\n")
        file.flush()

    def append(self, record: dict) -> None:
        entry = json.dumps(record, indent=2, allow_nan=False)
        entry = "\n".join("  " + line for line in entry.splitlines())
        self.file.seek(self.position)
        self.file.write((",\n" if self.count else "") + entry)
        self.position = self.file.tell()
        self.file.write("\n]\n")
        self.file.truncate()
        self.file.flush()
        self.count += 1


class ResourceSampler:
    """Write baseline, periodic, and final readings as a JSON array.

    The first read completes before __enter__ returns. __exit__ stops the
    background worker and takes a final reading before container cleanup.
    CPU percentage uses actual elapsed time; 100% means one fully used core.
    """

    def __init__(self, container: str, log_path: Path, server_host=None):
        self.container = container
        self.log_path = log_path
        self.server_host = server_host
        self.interval_seconds = 0.5
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._capture, name="resource-sampler")

    def __enter__(self):
        self._thread.start()
        try:
            self._ready.wait()
        except BaseException:
            self._stop.set()
            self._thread.join()
            raise
        if self.error is not None:
            self._thread.join()
            raise RuntimeError(f"Resource capture failed: {self.error}") from self.error
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self._thread.join()
        if self.error is not None:
            message = f"Resource capture failed: {self.error}"
            if exc_type is None:
                raise RuntimeError(message) from self.error
            print(message, file=sys.stderr)  # Preserve the original benchmark error.
        return False

    def _capture(self) -> None:
        try:
            with self.log_path.open("x", encoding="utf-8") as file:
                log = _JsonArrayLog(file)
                try:
                    self._collect(log)
                except Exception as exc:
                    detail = str(exc)
                    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                        detail += f": {exc.stderr.strip()}"
                    log.append({"type": "error", "message": detail})
                    raise
        except Exception as exc:
            self.error = exc
        finally:
            self._ready.set()

    def _collect(self, log) -> None:
        baseline_cpu = previous_cpu = None
        baseline_time = previous_time = None

        def sample(kind: str) -> None:
            nonlocal baseline_cpu, previous_cpu, baseline_time, previous_time
            read_start = time.monotonic()
            counters = (read_counters(self.container, self.server_host) if self.server_host is not None
                        else read_counters(self.container))
            read_end = time.monotonic()
            # The counters were read somewhere within this subprocess interval.
            observed_at = (read_start + read_end) / 2
            cpu = counters["cpu_counter_ns"]
            if baseline_cpu is None:
                baseline_cpu = previous_cpu = cpu
                baseline_time = previous_time = observed_at
            if cpu < previous_cpu:
                raise ValueError("CPU counter decreased during the iteration")
            elapsed = observed_at - baseline_time
            interval = observed_at - previous_time
            delta = cpu - previous_cpu
            consumed = cpu - baseline_cpu
            record = {
                "type": kind,
                "timestamp": datetime.now(ZoneInfo("Europe/Istanbul")).isoformat(),
                "container": self.container,
                "server_host": self.server_host.address if self.server_host is not None else "local",
                "target_interval_ms": self.interval_seconds * 1000,
                "elapsed_seconds": elapsed,
                "interval_seconds": interval,
                "read_duration_seconds": read_end - read_start,
                **counters,
                "cpu_delta_ns": delta,
                "cpu_consumed_ns": consumed,
                "cpu_consumed_seconds": consumed / 1_000_000_000,
                "cpu_percent": delta / 1_000_000_000 / interval * 100 if interval > 0 else None,
            }
            log.append(record)
            previous_cpu, previous_time = cpu, observed_at

        sample("baseline")
        next_read = time.monotonic() + self.interval_seconds
        self._ready.set()
        while not self._stop.wait(max(0, next_read - time.monotonic())):
            sample("sample")
            next_read += self.interval_seconds
            now = time.monotonic()
            if next_read <= now:
                # Skip missed ticks; do not issue a burst of catch-up reads.
                next_read = now + self.interval_seconds
        sample("final")
