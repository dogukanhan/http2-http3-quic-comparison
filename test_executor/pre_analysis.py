"""Export iteration inputs, h2load results, and final resource records to CSV."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.result_paths import case_directory


INPUT_FIELDS = ["test_id", "iteration_id", "protocol", "payload", "total_requests",
                "clients", "max_stream", "delay", "packet_loss"]
REQUEST_FIELDS = ["total", "started", "done", "succeeded", "failed", "errored", "timeout"]
TIMINGS = {"time for request": "time_for_request", "time for connect": "time_for_connect",
           "time to 1st byte": "time_to_1st_byte", "req/s": "req_per_sec"}
STATS = ["min", "max", "mean", "sd"]
H2LOAD_FIELDS = [
    "finished_ms", "req_per_sec", "throughput", "throughput_unit",
    "tls_protocol", "cipher", "application_protocol",
    *[f"requests_{name}" for name in REQUEST_FIELDS],
    *[f"status_{code}" for code in ("2xx", "3xx", "4xx", "5xx")],
    "traffic_total_bytes", "traffic_headers_bytes", "traffic_data_bytes",
    "header_space_savings_percent", "udp_datagrams_sent", "udp_datagrams_received",
    *[f"{metric}_{stat}" + ("_ms" if metric != "req_per_sec" else "")
      for metric in TIMINGS.values() for stat in STATS],
    *[f"{metric}_within_sd_percent" for metric in TIMINGS.values()],
]
RESOURCE_FIELDS = [
    "type", "timestamp", "container", "server_host", "target_interval_ms",
    "elapsed_seconds", "interval_seconds", "read_duration_seconds", "cpu_counter_ns",
    "memory_bytes", "network_rx_bytes", "network_tx_bytes", "cpu_delta_ns",
    "cpu_consumed_ns", "cpu_consumed_seconds", "cpu_percent",
]
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
PARAMETERS = re.compile(r"(h[23])-(\d+)-(\d+)-(\d+)-(\d+)-(\d+)-(\d+)")


def finite_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Non-finite h2load number: {value}")
    return number


def duration_milliseconds(value: str) -> float:
    match = re.fullmatch(rf"({NUMBER})(ns|us|µs|ms|s|m|h)", value)
    if not match:
        raise ValueError(f"Invalid h2load duration: {value}")
    scale = {"ns": 1e-6, "us": 1e-3, "µs": 1e-3, "ms": 1,
             "s": 1000, "m": 60000, "h": 3600000}
    return finite_number(match[1]) * scale[match[2]]


def parse_summary(text: str) -> tuple[dict, list[str]]:
    """Keep exact byte counts; normalize durations to numeric milliseconds."""
    values, errors = {}, []

    def required(pattern, label):
        match = re.search(pattern, text, re.MULTILINE)
        if match is None:
            errors.append(f"Missing or invalid h2load {label}")
        return match

    finished = required(rf"^\s*finished in (\S+),\s*({NUMBER}) req/s,\s*({NUMBER})(\S+/s)\s*$",
                        "finished summary")
    if finished:
        try:
            values.update(finished_ms=duration_milliseconds(finished[1]),
                          req_per_sec=finite_number(finished[2]),
                          throughput=finite_number(finished[3]), throughput_unit=finished[4])
        except ValueError as exc:
            errors.append(str(exc))

    for label, key in (("TLS Protocol", "tls_protocol"), ("Cipher", "cipher"),
                       ("Application protocol", "application_protocol")):
        match = re.search(rf"^{label}:\s*(.+)$", text, re.MULTILINE)
        if match:
            values[key] = match[1].strip()

    for label, names, prefix in (("requests", REQUEST_FIELDS, "requests"),
                                  ("status codes", ["2xx", "3xx", "4xx", "5xx"], "status")):
        line = required(rf"^\s*{label}:\s*(.+)$", label)
        if line:
            for name in names:
                match = re.search(rf"\b(\d+)\s+{name}\b", line[1])
                if match:
                    values[f"{prefix}_{name}"] = int(match[1])
                else:
                    errors.append(f"Missing h2load {prefix}_{name}")

    traffic = required(r"^\s*traffic:\s*(.+)$", "traffic")
    if traffic:
        for name in ("total", "headers", "data"):
            match = re.search(rf"\((\d+)\)\s+{name}\b", traffic[1])
            if match:
                values[f"traffic_{name}_bytes"] = int(match[1])
            else:
                errors.append(f"Missing h2load traffic_{name}_bytes")
        savings = re.search(rf"space savings ({NUMBER})%", traffic[1])
        if savings:
            values["header_space_savings_percent"] = finite_number(savings[1])

    udp = re.search(r"^\s*UDP datagram:\s*(\d+) sent,\s*(\d+) received", text, re.MULTILINE)
    if udp:
        values.update(udp_datagrams_sent=int(udp[1]), udp_datagrams_received=int(udp[2]))
    for label, metric in TIMINGS.items():
        match = required(rf"^\s*{re.escape(label)}\s*:\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+({NUMBER})%\s*$",
                         metric)
        if match:
            try:
                for stat, raw in zip(STATS, match.groups()[:4]):
                    key = f"{metric}_{stat}"
                    if metric == "req_per_sec":
                        values[key] = finite_number(raw)
                    else:
                        values[f"{key}_ms"] = duration_milliseconds(raw)
                values[f"{metric}_within_sd_percent"] = finite_number(match[5])
            except ValueError as exc:
                errors.append(str(exc))
    return {f"h2load_{key}": value for key, value in values.items()}, errors


def final_resource(path: Path) -> dict:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("resource.log must contain a JSON array")
    final = next((record for record in reversed(records)
                  if isinstance(record, dict) and record.get("type") == "final"), None)
    if final is None:
        raise ValueError("resource.log has no final record")
    # Preserve all final fields, including additional fields from future samplers.
    return {f"resource_{key}": json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list)) else value for key, value in final.items()}


def collect_rows(run_dir: Path, case_name: str | None = None) -> list[dict]:
    rows = []
    for case_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
        if case_name is not None and case_dir.name != case_name:
            continue
        parameter_dirs = [path for path in case_dir.iterdir()
                          if path.is_dir() and PARAMETERS.fullmatch(path.name)]
        parameter_dirs.sort(key=lambda p: (p.name.split("-")[0],
                                           *map(int, p.name.split("-")[1:])))
        for parameter_dir in parameter_dirs:
            parameters = PARAMETERS.fullmatch(parameter_dir.name).groups()
            inputs = dict(zip(INPUT_FIELDS[2:], [parameters[0], *map(int, parameters[1:])]))
            test_id = f"{run_dir.name}/{parameter_dir.relative_to(run_dir).as_posix()}"
            iterations = sorted((path for path in parameter_dir.iterdir()
                                 if path.is_dir() and re.fullmatch(r"\d+", path.name)),
                                key=lambda p: int(p.name))
            for iteration_dir in iterations:
                row = dict(test_id=test_id, iteration_id=int(iteration_dir.name), **inputs)
                errors = []
                try:
                    parsed, summary_errors = parse_summary(
                        (iteration_dir / "summary.txt").read_text(encoding="utf-8"))
                    row.update(parsed)
                    errors.extend(summary_errors)
                except (OSError, ValueError) as exc:
                    errors.append(f"summary.txt: {exc}")
                try:
                    row.update(final_resource(iteration_dir / "resource.log"))
                except (OSError, ValueError) as exc:
                    errors.append(f"resource.log: {exc}")
                row["analysis_error"] = "; ".join(errors)
                if errors:
                    print(f"Analysis warning for {iteration_dir.relative_to(run_dir)}: "
                          f"{row['analysis_error']}", flush=True)
                rows.append(row)
    return rows


def pre_analysis(run_dir: Path | None = None, *, case_name: str | None = None) -> Path:
    """Write analysis.csv inside one case folder, keeping test IDs unchanged."""
    output_dir = case_directory(run_dir, case_name)
    run_dir = output_dir.parent
    if not run_dir.is_dir():
        raise ValueError(f"Test run directory does not exist: {run_dir}")
    rows = collect_rows(run_dir, output_dir.name)
    if not rows:
        raise ValueError(f"No iteration folders found in {output_dir}")
    fields = [*INPUT_FIELDS, *[f"h2load_{key}" for key in H2LOAD_FIELDS],
              *[f"resource_{key}" for key in RESOURCE_FIELDS]]
    extras = sorted({key for row in rows for key in row} - set(fields) - {"analysis_error"})
    fields.extend([*extras, "analysis_error"])
    output = output_dir / "analysis.csv"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=output_dir, prefix=".analysis-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    issues = sum(bool(row["analysis_error"]) for row in rows)
    print(f"Analysis saved: {output} ({len(rows)} iterations, {issues} with warnings)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case folder, or dated run folder with --case")
    parser.add_argument("--case", dest="case_name", help="case_1 (default), case_2, case_3, or case_4")
    args = parser.parse_args(argv)
    try:
        pre_analysis(args.run_dir, case_name=args.case_name)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
