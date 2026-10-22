"""Plot case 2 p95 and p99 against client count in one PNG, with h2/h3 lines."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.case_1_percentile_graph_creator import METRICS
from test_executor.case_1_percentile_graph_creator import create_figure as render_figure
from test_executor.h2load_graph_creator import read_groups as read_measurement_groups
from test_executor.result_paths import case_directory


def read_groups(path: Path) -> dict:
    return read_measurement_groups(path, [field for _title, field in METRICS])


def create_figure(key: tuple, group: dict):
    return render_figure(key, group, x_axis="clients")


def percentile_graph_creator(run_dir: Path | None = None) -> list[Path]:
    """Save a two-panel percentile PNG per fixed payload and other case 2 settings."""
    run_dir = case_directory(run_dir, "case_2")
    groups = read_groups(run_dir / "trimmed_mean.csv")
    outputs = []
    for key, group in sorted(groups.items()):
        case, requests, payload, streams, delay, loss = key
        directory = run_dir / "percentile_graphs" / f"n{requests}-p{payload}-m{streams}-delay{delay}-loss{loss}"
        directory.mkdir(parents=True, exist_ok=True)
        figure = create_figure(key, group)
        output = directory / "percentile_summary.png"
        try:
            figure.savefig(output, facecolor="white")
        finally:
            figure.clear()
        outputs.append(output)
        print(f"Case 2 percentile graphs saved: {output}", flush=True)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_2 folder containing trimmed_mean.csv (or its dated run folder) with p95/p99 columns")
    args = parser.parse_args(argv)
    try:
        percentile_graph_creator(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
