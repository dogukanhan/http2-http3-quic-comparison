"""Create one four-panel PNG comparing h2load means for h2 and h3."""

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
from test_executor.case_1_resource_graph_creator import SETTINGS


METRICS = (
    ("Requests per second", "Mean rate per client (req/s)", "h2load_req_per_sec_mean"),
    ("Time to first byte", "Mean time (ms)", "h2load_time_to_1st_byte_mean_ms"),
    ("Connection time", "Mean time (ms)", "h2load_time_for_connect_mean_ms"),
    ("Time for request", "Mean time (ms)", "h2load_time_for_request_mean_ms"),
)


def numeric_value(row: dict, field: str) -> float:
    try:
        value = float(row[field])
        if not math.isfinite(value) or value < 0:
            raise ValueError("Expected a finite nonnegative value")
        return value
    except ValueError:
        print(f"H2load graph warning: {row['test_id']} has missing/invalid {field}; leaving a gap.", flush=True)
        return math.nan


def read_groups(path: Path, measurements: list[str] | None = None) -> dict:
    groups = {}
    if measurements is None:
        measurements = [field for metric in METRICS for field in metric[2:]]
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"test_id", "protocol", "payload", *SETTINGS, *measurements}
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
            if row["protocol"] not in ("h2", "h3"):
                raise ValueError(f"Unsupported protocol: {row['protocol']}")
            payload = int(row["payload"])
            settings = tuple(int(row[field]) for field in SETTINGS)
            if payload < 1 or any(value < 1 for value in settings[:3]) or settings[3] < 0 or not 0 <= settings[4] <= 100:
                raise ValueError(f"Invalid input parameters for {row['test_id']}")
            group = groups.setdefault((parts[-2], *settings), {"h2": {}, "h3": {}})
            series = group[row["protocol"]]
            if payload in series:
                raise ValueError(f"Duplicate {row['protocol']} payload {payload} with identical settings")
            series[payload] = {field: numeric_value(row, field) for field in measurements}
    if not groups:
        print("No case_1 rows; skipping case 1 graphs.", flush=True)
    return groups


def create_figure(key: tuple, group: dict, *, x_axis: str = "payload", average_group: dict | None = None):
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.ticker import MaxNLocator, ScalarFormatter
    except ImportError:
        raise RuntimeError("Install graph dependencies: python3 -m pip install -r requirements.txt") from None

    x_label, subtitle, _folder = graph_settings(key, x_axis)
    comparison = average_group is not None
    series = [(protocol, f"{protocol} trimmed" if comparison else protocol, color, marker, style, group)
              for protocol, color, marker, style in (("h2", "#2563AD", "o", "-"), ("h3", "#D46B25", "s", "--"))]
    if comparison:
        series.extend([(protocol, f"{protocol} average", color, marker, style, average_group)
                       for protocol, color, marker, style in (("h2", "#16857A", "^", "-."), ("h3", "#8954B8", "D", ":"))])
    x_values = sorted({x for protocol, _label, _color, _marker, _style, data in series for x in data[protocol]})
    figure = Figure(figsize=(13, 9.5), dpi=180, facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    figure.subplots_adjust(left=.085, right=.965, bottom=.15, top=.82, hspace=.50, wspace=.28)
    figure.text(.085, .95, f"h2load performance by {x_axis}", fontsize=22, fontweight="bold", color="#182638")
    figure.text(.085, .905,
                subtitle,
                fontsize=11, color="#526176")
    for axis, (title, ylabel, mean_field) in zip(axes.flat, METRICS):
        unavailable = []
        for protocol, label, color, marker, style, data in series:
            means = [data[protocol].get(x, {}).get(mean_field, math.nan) for x in x_values]
            if not any(math.isfinite(value) for value in means):
                unavailable.append(label)
            axis.plot(x_values, means, label=label, color=color, marker=marker,
                      linestyle=style, linewidth=2, markersize=5, markevery=max(1, len(x_values) // 24),
                      markeredgecolor="white", markeredgewidth=.7)
        axis.set_title(title, fontsize=14, fontweight="bold", loc="left", pad=12, color="#182638")
        axis.set_xlabel(x_label, fontsize=10, labelpad=8)
        axis.set_ylabel(ylabel, fontsize=10, labelpad=8)
        axis.set_ylim(bottom=0)
        axis.margins(x=.04)
        if len(x_values) <= 10:
            axis.set_xticks(x_values)
        else:
            axis.xaxis.set_major_locator(MaxNLocator(nbins=7, integer=True))
        formatter = ScalarFormatter(useOffset=False)
        formatter.set_scientific(False)
        axis.yaxis.set_major_formatter(formatter)
        axis.grid(axis="y", color="#DFE5ED", linewidth=.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        for edge in ("left", "bottom"):
            axis.spines[edge].set_color("#BBC5D2")
        axis.tick_params(colors="#465569", labelsize=9)
        if unavailable:
            axis.text(.5, .5, f"No valid data for {', '.join(unavailable)}", ha="center", transform=axis.transAxes, color="#65748A")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    figure.legend(handles, labels, frameon=False, ncol=4 if comparison else 2, fontsize=10 if comparison else 12,
                  loc="upper right", bbox_to_anchor=(.975, .885))
    caption = ("Lines: trimmed means and all-iteration averages."
               if comparison else "Lines: trimmed means.")
    figure.text(.085, .075, caption,
                fontsize=9, color="#526176")
    figure.text(.085, .045, "Missing data is left as gaps.",
                fontsize=8.5, color="#65748A")
    return figure


def case_1_h2load_graph_creator(run_dir: Path | None = None) -> list[Path]:
    """Save a four-panel PNG for each case and fixed combination of test settings."""
    run_dir = case_directory(run_dir, "case_1")
    groups = read_groups(run_dir / "trimmed_mean.csv")
    outputs = []
    for key, group in sorted(groups.items()):
        case, requests, clients, streams, delay, loss = key
        directory = run_dir / "h2load_graphs" / f"n{requests}-c{clients}-m{streams}-delay{delay}-loss{loss}"
        directory.mkdir(parents=True, exist_ok=True)
        figure = create_figure(key, group)
        output = directory / "h2load_summary.png"
        try:
            figure.savefig(output, facecolor="white")
        finally:
            figure.clear()
        outputs.append(output)
        print(f"H2load graphs saved: {output}", flush=True)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_1 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_1_h2load_graph_creator(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
