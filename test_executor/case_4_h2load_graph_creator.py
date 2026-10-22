"""Plot case 4 h2load metrics by delay and packet loss in one four-panel PNG."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.case_1_h2load_graph_creator import numeric_value
from test_executor.result_paths import case_directory


SETTINGS = ("total_requests", "payload", "clients", "max_stream")
METRICS = (
    ("H2load req/s (Mean)", "Mean request rate per client (req/s)", "h2load_req_per_sec_mean"),
    ("Time to first byte", "Mean time (ms)", "h2load_time_to_1st_byte_mean_ms"),
    ("Connection time", "Mean time (ms)", "h2load_time_for_connect_mean_ms"),
    ("Time for request", "Mean time (ms)", "h2load_time_for_request_mean_ms"),
)
PROTOCOLS = (("h2", "#2563AD"), ("h3", "#D46B25"))


def read_groups(path: Path) -> dict:
    """Keep delay/loss pairs together and separate different fixed settings."""
    groups = {}
    fields = [field for _title, _label, field in METRICS]
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"test_id", "protocol", "delay", "packet_loss", *SETTINGS, *fields}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"trimmed_mean.csv is missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed trimmed_mean.csv row")
            parts = Path(row["test_id"]).parts
            if len(parts) != 3:
                raise ValueError(f"Invalid test ID: {row['test_id']}")
            if parts[-2] != "case_4":
                continue
            protocol = row["protocol"]
            if protocol not in ("h2", "h3"):
                raise ValueError(f"Unsupported protocol: {protocol}")
            settings = tuple(int(row[field]) for field in SETTINGS)
            pair = (int(row["delay"]), int(row["packet_loss"]))
            if any(value < 1 for value in settings) or pair[0] < 0 or not 0 <= pair[1] <= 100:
                raise ValueError(f"Invalid input parameters for {row['test_id']}")
            group = groups.setdefault(settings, {"h2": {}, "h3": {}})
            if pair in group[protocol]:
                raise ValueError(f"Duplicate {protocol} delay/loss pair {pair} with identical settings")
            group[protocol][pair] = {field: numeric_value(row, field) for field in fields}
    if not groups:
        print("No case_4 rows; skipping case 4 h2load graphs.", flush=True)
    return groups


def create_figure(key: tuple, group: dict, *, average_group: dict | None = None,
                  metrics: tuple = METRICS, heading: str = "Case 4 · h2load performance",
                  footnote: str | None = None):
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import Patch
        from matplotlib.ticker import ScalarFormatter
    except ImportError:
        raise RuntimeError("Install graph dependencies: python3 -m pip install -r requirements.txt") from None

    requests, payload, clients, streams = key
    comparison = average_group is not None
    series = [(protocol, f"{protocol} trimmed" if comparison else protocol, color, "", group)
              for protocol, color in PROTOCOLS]
    if comparison:
        series.extend([(protocol, f"{protocol} average", color, "//", average_group)
                       for protocol, color in (("h2", "#16857A"), ("h3", "#8954B8"))])
    pairs = sorted({pair for protocol, _label, _color, _hatch, data in series for pair in data[protocol]})
    positions = []
    gap = 0
    for index, (delay, _loss) in enumerate(pairs):
        if index and delay != pairs[index - 1][0]:
            gap += .6
        positions.append(index + gap)
    labels = [f"{loss}%" for _delay, loss in pairs]
    delay_centers = {}
    for position, (delay, _loss) in zip(positions, pairs):
        delay_centers.setdefault(delay, []).append(position)
    rows = (len(metrics) + 1) // 2
    figure = Figure(figsize=(max(14, len(pairs) * .95), 10.5 if rows == 2 else 6.5), dpi=180, facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.subplots(rows, 2, squeeze=False)
    figure.subplots_adjust(left=.085, right=.97, bottom=.17 if rows == 2 else .29,
                           top=.82 if rows == 2 else .74, hspace=.65, wspace=.27)
    figure.text(.085, .95, heading, fontsize=23, fontweight="bold", color="#182638")
    figure.text(.085, .91,
                f"{payload} KB payload  ·  {requests:,} requests  ·  {clients} clients  ·  {streams} streams",
                fontsize=12, color="#526176")
    figure.legend(handles=[Patch(facecolor=color, hatch=hatch, label=label) for _protocol, label, color, hatch, _data in series],
                  frameon=False, ncol=4 if comparison else 2, fontsize=10 if comparison else 12,
                  loc="upper right", bbox_to_anchor=(.98, .90))
    for axis, (title, ylabel, field) in zip(axes.flat, metrics):
        for series_index, (protocol, label, color, hatch, data) in enumerate(series):
            values = [data[protocol].get(pair, {}).get(field, math.nan) for pair in pairs]
            spacing = .19 if comparison else .38
            x = [position + (series_index - (len(series) - 1) / 2) * spacing for position in positions]
            axis.bar(x, values, width=spacing - .02, color=color, label=label, hatch=hatch)
            for position, value in zip(x, values):
                if not math.isfinite(value):
                    axis.text(position, .015, "×", ha="center", color=color,
                              transform=axis.get_xaxis_transform(), fontsize=10)
        axis.set_title(title, fontsize=15, fontweight="bold", loc="left", pad=12, color="#182638")
        axis.set_xlabel("Packet loss (%) grouped by delay (ms)", fontsize=10, labelpad=30)
        axis.set_ylabel(ylabel, fontsize=10, labelpad=10)
        axis.set_xticks(positions, labels)
        for delay, group_positions in delay_centers.items():
            axis.text(sum(group_positions) / len(group_positions), -.11, f"{delay} ms",
                      ha="center", va="top", transform=axis.get_xaxis_transform(),
                      fontsize=10, color="#465569")
        axis.set_ylim(bottom=0)
        axis.margins(x=.025)
        formatter = ScalarFormatter(useOffset=False)
        formatter.set_scientific(False)
        axis.yaxis.set_major_formatter(formatter)
        axis.grid(axis="y", color="#DFE5ED", linewidth=.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        for edge in ("left", "bottom"):
            axis.spines[edge].set_color("#BBC5D2")
        axis.tick_params(colors="#465569", labelsize=9)
    caption = ("Bars: h2/h3 trimmed means and all-iteration averages at each delay / packet-loss combination."
               if comparison else "Bars: trimmed means from trimmed_mean.csv. Each group compares h2 and h3 at one delay / packet-loss combination.")
    figure.text(.085, .07, caption,
                fontsize=10, color="#526176")
    figure.text(.085, .04, footnote if footnote is not None else "H2load req/s uses the per-client mean request rate. Timing panels use mean latency. × marks missing data, not zero.",
                fontsize=9, color="#65748A")
    return figure


def case_4_h2load_graph_creator(run_dir: Path | None = None) -> list[Path]:
    run_dir = case_directory(run_dir, "case_4")
    outputs = []
    for key, group in sorted(read_groups(run_dir / "trimmed_mean.csv").items()):
        requests, payload, clients, streams = key
        directory = run_dir / "h2load_graphs" / f"n{requests}-p{payload}-c{clients}-m{streams}"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "h2load_summary.png"
        figure = create_figure(key, group)
        try:
            figure.savefig(output, facecolor="white")
        finally:
            figure.clear()
        outputs.append(output)
        print(f"Case 4 h2load bar graphs saved: {output}", flush=True)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_4 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_4_h2load_graph_creator(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
