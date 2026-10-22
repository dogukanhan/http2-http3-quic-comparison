"""Create case 3 h2load graphs with delay (ms) on the x-axis."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.case_1_h2load_graph_creator import METRICS, numeric_value
from test_executor.case_1_h2load_graph_creator import create_figure as render_figure
from test_executor.result_paths import case_directory


SETTINGS = ("total_requests", "payload", "clients", "max_stream", "packet_loss")


def read_groups(path: Path, measurements: list[str] | None = None) -> dict:
    groups = {}
    if measurements is None:
        measurements = [field for metric in METRICS for field in metric[2:]]
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"test_id", "protocol", "delay", *SETTINGS, *measurements}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"trimmed_mean.csv is missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed trimmed_mean.csv row")
            parts = Path(row["test_id"]).parts
            if len(parts) != 3:
                raise ValueError(f"Invalid test ID: {row['test_id']}")
            if parts[-2] != "case_3":
                continue
            if row["protocol"] not in ("h2", "h3"):
                raise ValueError(f"Unsupported protocol: {row['protocol']}")
            delay = int(row["delay"])
            settings = tuple(int(row[field]) for field in SETTINGS)
            if delay < 0 or any(value < 1 for value in settings[:4]) or not 0 <= settings[4] <= 100:
                raise ValueError(f"Invalid input parameters for {row['test_id']}")
            group = groups.setdefault(("case_3", *settings), {"h2": {}, "h3": {}})
            series = group[row["protocol"]]
            if delay in series:
                raise ValueError(f"Duplicate {row['protocol']} delay {delay} with identical settings")
            series[delay] = {field: numeric_value(row, field) for field in measurements}
    if not groups:
        print("No case_3 rows; skipping case 3 graphs.", flush=True)
    return groups


def create_figure(key: tuple, group: dict):
    return render_figure(key, group, x_axis="delay")


def case_3_h2load_graph_creator(run_dir: Path | None = None) -> list[Path]:
    """Save one four-panel PNG per fixed payload and other case 3 settings."""
    run_dir = case_directory(run_dir, "case_3")
    groups = read_groups(run_dir / "trimmed_mean.csv")
    outputs = []
    for key, group in sorted(groups.items()):
        case, requests, payload, clients, streams, loss = key
        directory = run_dir / "h2load_graphs" / f"n{requests}-p{payload}-c{clients}-m{streams}-loss{loss}"
        directory.mkdir(parents=True, exist_ok=True)
        figure = create_figure(key, group)
        output = directory / "h2load_summary.png"
        try:
            figure.savefig(output, facecolor="white")
        finally:
            figure.clear()
        outputs.append(output)
        print(f"Case 3 h2load graphs saved: {output}", flush=True)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_3 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_3_h2load_graph_creator(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
