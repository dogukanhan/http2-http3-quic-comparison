"""Plot case 3 server resources against delay (ms), comparing h2 and h3."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.case_1_resource_graph_creator import MIB, RESOURCE_FIELDS, write_graphs
from test_executor.case_3_h2load_graph_creator import read_groups as read_measurement_groups
from test_executor.result_paths import case_directory


def read_groups(path: Path) -> dict:
    groups = read_measurement_groups(path, list(RESOURCE_FIELDS))
    for group in groups.values():
        for series in group.values():
            for delay, values in series.items():
                series[delay] = {
                    "cpu_usage": values["resource_cpu_percent"],
                    "cpu_counter": values["resource_cpu_counter_ns"] / 1_000_000,
                    "memory_usage": values["resource_memory_bytes"] / MIB,
                    "network_usage": (values["resource_network_rx_bytes"] + values["resource_network_tx_bytes"]) / MIB,
                }
    return groups


def case_3_resource_graph_creator(run_dir: Path | None = None) -> list[Path]:
    """Save four PNG/SVG graphs per fixed payload and other case 3 settings."""
    run_dir = case_directory(run_dir, "case_3")
    return write_graphs(run_dir, read_groups(run_dir / "trimmed_mean.csv"), x_axis="delay")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_3 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_3_resource_graph_creator(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
