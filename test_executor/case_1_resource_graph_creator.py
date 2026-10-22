"""Plot server resource usage against payload, comparing h2 and h3."""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.result_paths import case_directory
from test_executor.graph_settings import graph_settings


SETTINGS = ("total_requests", "clients", "max_stream", "delay", "packet_loss")
RESOURCE_FIELDS = ("resource_cpu_percent", "resource_cpu_counter_ns", "resource_memory_bytes",
                   "resource_network_rx_bytes", "resource_network_tx_bytes")
MIB = 1024 ** 2
METRICS = {
    "cpu_usage": ("CPU usage by payload", "CPU usage (%)",
                  "CPU percentage from the final sampling interval."),
    "cpu_counter": ("CPU counter by payload", "CPU counter (ms)",
                    "Final cumulative CPU time since container start; nanoseconds converted to milliseconds."),
    "memory_usage": ("Memory usage by payload", "Memory usage (MiB)",
                     "Final container memory, including cache. 1 MiB = 1,048,576 bytes."),
    "network_usage": ("Network usage by payload", "Received + transmitted (MiB)",
                      "Final cumulative network counters. 1 MiB = 1,048,576 bytes."),
}


def numeric_resource(row: dict, field: str) -> float:
    try:
        value = float(row[field])
        if not math.isfinite(value) or value < 0:
            raise ValueError("Expected a finite nonnegative value")
        return value
    except ValueError:
        print(f"Graph warning: {row['test_id']} has missing/invalid {field}; leaving a gap.", flush=True)
        return math.nan


def read_groups(path: Path) -> dict:
    groups = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"test_id", "payload", "protocol", *SETTINGS, *RESOURCE_FIELDS}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"trimmed_mean.csv is missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed trimmed_mean.csv row")
            parts = Path(row["test_id"]).parts
            if len(parts) < 2 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]*", parts[-2]):
                raise ValueError(f"Invalid test ID: {row['test_id']}")
            if parts[-2] != "case_1":
                continue
            protocol = row["protocol"]
            if protocol not in ("h2", "h3"):
                raise ValueError(f"Unsupported protocol: {protocol}")
            payload = int(row["payload"])
            settings = tuple(int(row[field]) for field in SETTINGS)
            if payload < 1 or any(value < 1 for value in settings[:3]) or settings[3] < 0 or not 0 <= settings[4] <= 100:
                raise ValueError(f"Invalid input parameters for {row['test_id']}")
            group = groups.setdefault((parts[-2], *settings), {"h2": {}, "h3": {}})
            if payload in group[protocol]:
                raise ValueError(f"Duplicate {protocol} payload {payload} with identical settings; cannot plot a unique value")
            values = {field: numeric_resource(row, field) for field in RESOURCE_FIELDS}
            group[protocol][payload] = {
                "cpu_usage": values["resource_cpu_percent"],
                "cpu_counter": values["resource_cpu_counter_ns"] / 1_000_000,
                "memory_usage": values["resource_memory_bytes"] / MIB,
                "network_usage": (values["resource_network_rx_bytes"] + values["resource_network_tx_bytes"]) / MIB,
            }
    if not groups:
        print("No case_1 resource rows; skipping case 1 resource graphs.", flush=True)
    return groups


def write_graphs(run_dir: Path, groups: dict, *, x_axis: str = "payload") -> list[Path]:
    """Render resource groups against payload or client count."""
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.ticker import MaxNLocator, ScalarFormatter
    except ImportError:
        raise RuntimeError("Install graph dependencies: python3 -m pip install -r requirements.txt") from None

    outputs = []
    for key, group in sorted(groups.items()):
        x_label, subtitle, folder = graph_settings(key, x_axis)
        directory = run_dir / "resource_graphs" / folder
        directory.mkdir(parents=True, exist_ok=True)
        x_values = sorted(set(group["h2"]) | set(group["h3"]))
        for metric, (title, ylabel, note) in METRICS.items():
            figure = Figure(figsize=(9, 5.8), dpi=180, facecolor="white")
            FigureCanvasAgg(figure)
            axis = figure.add_subplot(111)
            figure.subplots_adjust(left=.12, right=.97, bottom=.19, top=.80)
            figure.text(.12, .93, title.replace("by payload", f"by {x_axis}"), fontsize=18, fontweight="bold", color="#182638")
            figure.text(.12, .87, subtitle, fontsize=9, color="#526176")
            unavailable = []
            for protocol, color, marker, style in (("h2", "#2563AD", "o", "-"), ("h3", "#D46B25", "s", "--")):
                values = [group[protocol].get(x, {}).get(metric, math.nan) for x in x_values]
                if not any(math.isfinite(value) for value in values):
                    unavailable.append(protocol)
                axis.plot(x_values, values, label=protocol, color=color, marker=marker,
                          linestyle=style, linewidth=2, markersize=5, markeredgecolor="white", markeredgewidth=.7)
            axis.set_xlabel(x_label, fontsize=11, labelpad=10)
            axis.set_ylabel(ylabel, fontsize=11, labelpad=10)
            axis.set_ylim(bottom=0)
            axis.margins(x=.04)
            if len(x_values) <= 12:
                axis.set_xticks(x_values)
            else:
                axis.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True))
            formatter = ScalarFormatter(useOffset=False)
            formatter.set_scientific(False)
            axis.yaxis.set_major_formatter(formatter)
            axis.grid(axis="y", color="#DFE5ED", linewidth=.8)
            axis.set_axisbelow(True)
            axis.spines[["top", "right"]].set_visible(False)
            for edge in ("left", "bottom"):
                axis.spines[edge].set_color("#BBC5D2")
            axis.tick_params(colors="#465569", labelsize=10)
            axis.legend(frameon=False, loc="best", ncol=2)
            figure.text(.12, .065, note, fontsize=9, color="#526176")
            figure.text(.12, .03, "Trimmed means across iterations. Missing measurements are left as gaps.", fontsize=8, color="#65748A")
            if unavailable:
                axis.text(.5, .5, f"No valid data for {', '.join(unavailable)}", ha="center", transform=axis.transAxes, color="#65748A")
            for extension in ("png", "svg"):
                output = directory / f"{metric}.{extension}"
                figure.savefig(output, facecolor="white")
                outputs.append(output)
            figure.clear()
        print(f"Resource graphs saved: {directory}", flush=True)
    return outputs


def case_1_resource_graph_creator(run_dir: Path | None = None) -> list[Path]:
    """Write four PNG/SVG graphs for each combination of case 1 settings."""
    run_dir = case_directory(run_dir, "case_1")
    return write_graphs(run_dir, read_groups(run_dir / "trimmed_mean.csv"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_1 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_1_resource_graph_creator(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
