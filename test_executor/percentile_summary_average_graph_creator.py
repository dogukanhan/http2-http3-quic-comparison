"""Plot h2/h3 percentile averages with and without p99-based iteration trimming."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.case_1_percentile_graph_creator import METRICS, create_figure as line_figure
from test_executor.case_4_h2load_graph_creator import create_figure as bar_figure
from test_executor.execute_trimmed_mean import average, number
from test_executor.graph_settings import graph_settings
from test_executor.pre_analysis import INPUT_FIELDS
from test_executor.result_paths import case_directory


CASES = {
    "case_1": ("payload", ("total_requests", "clients", "max_stream", "delay", "packet_loss")),
    "case_2": ("clients", ("total_requests", "payload", "max_stream", "delay", "packet_loss")),
    "case_3": ("delay", ("total_requests", "payload", "clients", "max_stream", "packet_loss")),
    "case_4": (None, ("total_requests", "payload", "clients", "max_stream")),
}


def read_groups(path: Path, case_name: str, *, untrimmed: bool = False) -> dict:
    x_axis, settings = CASES[case_name]
    tests, seen = {}, set()
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {*INPUT_FIELDS, *(field for _title, field in METRICS),
                    "percentile_trim_role", "percentile_error"}
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path.name} must contain unique percentile detail columns")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed {path.name} row")
            parts = Path(row["test_id"]).parts
            if len(parts) != 3 or parts[-2] != case_name:
                raise ValueError(f"Unexpected case in {path.name}: {row['test_id']}")
            identity = (row["test_id"], int(row["iteration_id"]))
            if identity in seen or identity[1] < 1:
                raise ValueError(f"Duplicate or invalid iteration: {identity}")
            seen.add(identity)
            role = row["percentile_trim_role"]
            if role not in ("mean_of_iterations", "min_dismiss", "max_dissmiss", "excluded"):
                raise ValueError(f"Unknown percentile role: {role}")
            if untrimmed and role in ("min_dismiss", "max_dissmiss"):
                raise ValueError("percentiles_average.csv must not discard minimum/maximum iterations")
            group = tests.setdefault(row["test_id"], [])
            if group and any(row[key] != group[0][key] for key in INPUT_FIELDS[2:]):
                raise ValueError(f"Inconsistent inputs for {row['test_id']}")
            group.append(row)
    groups = {}
    for test_id, rows in tests.items():
        first = rows[0]
        inputs = {field: int(first[field]) for field in INPUT_FIELDS[3:]}
        if (any(inputs[field] < 1 for field in ("payload", "total_requests", "clients", "max_stream"))
                or inputs["delay"] < 0 or not 0 <= inputs["packet_loss"] <= 100):
            raise ValueError(f"Invalid test inputs: {test_id}")
        key = tuple(inputs[field] for field in settings)
        if x_axis is not None:
            key = (case_name, *key)
        x = inputs[x_axis] if x_axis else (inputs["delay"], inputs["packet_loss"])
        protocol = first["protocol"]
        if protocol not in ("h2", "h3"):
            raise ValueError(f"Unsupported protocol: {protocol}")
        series = groups.setdefault(key, {"h2": {}, "h3": {}})[protocol]
        if x in series:
            raise ValueError(f"Duplicate inputs for {protocol}: {x}")
        selected = rows if untrimmed else [row for row in rows if row["percentile_trim_role"] == "mean_of_iterations"]
        values = {}
        for _title, field in METRICS:
            samples = [number(row[field]) if not row["percentile_error"] else None for row in selected]
            valid = bool(samples) and all(value is not None and value >= 0 for value in samples)
            values[field] = float(average(samples)) if valid else math.nan
            if not valid:
                print(f"Percentile comparison warning: {path.name}, {test_id}, {field}: incomplete data; leaving a gap.", flush=True)
        series[x] = values
    return groups


def percentile_summary_average_graph_creator(run_dir: Path | None = None, *, case_name: str | None = None) -> list[Path]:
    directory = case_directory(run_dir, case_name)
    if directory.name not in CASES:
        raise ValueError("Supported cases: case_1 through case_4")
    trimmed = read_groups(directory / "percentiles.csv", directory.name)
    averaged = read_groups(directory / "percentiles_average.csv", directory.name, untrimmed=True)
    x_axis, _settings = CASES[directory.name]
    outputs = []
    for key in sorted(set(trimmed) | set(averaged)):
        group = trimmed.get(key, {"h2": {}, "h3": {}})
        average_group = averaged.get(key, {"h2": {}, "h3": {}})
        if x_axis is None:
            requests, payload, clients, streams = key
            folder = f"n{requests}-p{payload}-c{clients}-m{streams}"
            figure = bar_figure(key, group, average_group=average_group,
                                metrics=tuple((title, "Request latency (ms)", field) for title, field in METRICS),
                                heading="Case 4 · request latency percentiles",
                                footnote="P95/p99 are averaged per iteration; no requests are pooled. × marks missing data.")
        else:
            _label, _subtitle, folder = graph_settings(key, x_axis)
            figure = line_figure(key, group, x_axis=x_axis, average_group=average_group)
        output = directory / "percentile_graphs" / folder / "percentile_summary_average.png"
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            figure.savefig(output, facecolor="white")
        finally:
            figure.clear()
        outputs.append(output)
        print(f"Percentile comparison saved: {output}", flush=True)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Dated results folder or a single case_N folder")
    args = parser.parse_args(argv)
    try:
        folder = args.folder.expanduser().resolve()
        cases = ([folder] if folder.name in CASES else
                 [folder / name for name in CASES if (folder / name).is_dir()])
        if not cases:
            raise ValueError("No supported case folders found")
        for case in cases:
            percentile_summary_average_graph_creator(case)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
