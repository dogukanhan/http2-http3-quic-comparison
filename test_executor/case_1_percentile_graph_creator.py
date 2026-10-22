"""Plot p95 and p99 against payload in one PNG, with h2 and h3 lines."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.case_1_h2load_graph_creator import read_groups as read_measurement_groups
from test_executor.result_paths import case_directory
from test_executor.graph_settings import graph_settings


METRICS = (
    ("95th percentile (p95)", "h2load_time_for_request_p95_ms"),
    ("99th percentile (p99)", "h2load_time_for_request_p99_ms"),
)


def read_groups(path: Path) -> dict:
    return read_measurement_groups(path, [field for _title, field in METRICS])


def create_figure(key: tuple, group: dict, *, x_axis: str = "payload", average_group: dict | None = None):
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.ticker import MaxNLocator, ScalarFormatter
    except ImportError:
        raise RuntimeError("Install graph dependencies: python3 -m pip install -r requirements.txt") from None

    x_label, subtitle, _folder = graph_settings(key, x_axis)
    available_label = {"payload": "payloads", "clients": "client counts", "delay": "delays"}[x_axis]
    comparison = average_group is not None
    series = [(protocol, f"{protocol} trimmed" if comparison else protocol, color, marker, style, group)
              for protocol, color, marker, style in (("h2", "#2563AD", "o", "-"), ("h3", "#D46B25", "s", "--"))]
    if comparison:
        series.extend([(protocol, f"{protocol} average", color, marker, style, average_group)
                       for protocol, color, marker, style in (("h2", "#16857A", "^", "-."), ("h3", "#8954B8", "D", ":"))])
    x_values = sorted({x for protocol, _label, _color, _marker, _style, data in series for x in data[protocol]})
    figure = Figure(figsize=(13, 5.8), dpi=180, facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.subplots(1, 2)
    figure.subplots_adjust(left=.085, right=.965, bottom=.23, top=.66 if comparison else .72, wspace=.28)
    figure.text(.085, .93, f"Request latency percentiles by {x_axis}", fontsize=21, fontweight="bold", color="#182638")
    figure.text(.085, .855,
                subtitle,
                fontsize=10, color="#526176")
    for axis, (title, field) in zip(axes, METRICS):
        unavailable = []
        for protocol, label, color, marker, style, data in series:
            values = [data[protocol].get(x, {}).get(field, math.nan) for x in x_values]
            if not any(math.isfinite(value) for value in values):
                unavailable.append(label)
            axis.plot(x_values, values, label=label, color=color, marker=marker,
                      linestyle=style, linewidth=2, markersize=5, markevery=max(1, len(x_values) // 24),
                      markeredgecolor="white", markeredgewidth=.7)
        axis.set_title(title, fontsize=14, fontweight="bold", loc="left", pad=12, color="#182638")
        axis.set_xlabel(x_label, fontsize=11, labelpad=9)
        axis.set_ylabel("Request latency (ms)", fontsize=11, labelpad=9)
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
        axis.tick_params(colors="#465569", labelsize=10)
        if unavailable:
            axis.text(.5, .5, f"No valid data for {', '.join(unavailable)}", ha="center", transform=axis.transAxes, color="#65748A")
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, frameon=False, ncol=4 if comparison else 2, fontsize=10 if comparison else 11,
                  loc="upper right", bbox_to_anchor=(.975, .81))
    caption = ("Per-iteration percentiles: p99-trimmed means versus all-iteration averages. No request samples are pooled."
               if comparison else "Values from trimmed_mean.csv: averages of per-iteration percentiles after p99-based trimming.")
    figure.text(.085, .095, caption,
                fontsize=9, color="#526176")
    figure.text(.085, .055, f"All available {available_label} are plotted. Missing measurements are left as gaps.",
                fontsize=9, color="#65748A")
    return figure


def case_1_percentile_graph_creator(run_dir: Path | None = None) -> list[Path]:
    """Save a two-panel percentile PNG per case and combination of test settings."""
    run_dir = case_directory(run_dir, "case_1")
    groups = read_groups(run_dir / "trimmed_mean.csv")
    outputs = []
    for key, group in sorted(groups.items()):
        case, requests, clients, streams, delay, loss = key
        directory = run_dir / "percentile_graphs" / f"n{requests}-c{clients}-m{streams}-delay{delay}-loss{loss}"
        directory.mkdir(parents=True, exist_ok=True)
        figure = create_figure(key, group)
        output = directory / "percentile_summary.png"
        try:
            figure.savefig(output, facecolor="white")
        finally:
            figure.clear()
        outputs.append(output)
        print(f"Percentile graphs saved: {output}", flush=True)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_1 folder containing trimmed_mean.csv (or its dated run folder) with p95/p99 columns")
    args = parser.parse_args(argv)
    try:
        case_1_percentile_graph_creator(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
