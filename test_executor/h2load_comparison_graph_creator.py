"""Compare h2/h3 trimmed means and all-iteration averages on the existing case axes."""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.case_1_h2load_graph_creator import create_figure as line_figure
from test_executor.case_4_h2load_graph_creator import create_figure as bar_figure
from test_executor.graph_settings import graph_settings
from test_executor.result_paths import case_directory


CASES = {
    "case_1": ("case_1_h2load_graph_creator", "payload"),
    "case_2": ("h2load_graph_creator", "clients"),
    "case_3": ("case_3_h2load_graph_creator", "delay"),
    "case_4": ("case_4_h2load_graph_creator", None),
}


def h2load_comparison_graph_creator(run_dir: Path | None = None, *, case_name: str | None = None) -> list[Path]:
    directory = case_directory(run_dir, case_name)
    if directory.name not in CASES:
        raise ValueError("Comparison graphs support case_1 through case_4")
    module_name, x_axis = CASES[directory.name]
    reader = import_module(f"test_executor.{module_name}").read_groups
    # Read both sources fully before writing any output. Match by fixed inputs,
    # protocol, and x value, rather than relying on CSV row order.
    trimmed = reader(directory / "trimmed_mean.csv")
    averages = reader(directory / "average.csv")
    outputs = []
    for key in sorted(set(trimmed) | set(averages)):
        group = trimmed.get(key, {"h2": {}, "h3": {}})
        average_group = averages.get(key, {"h2": {}, "h3": {}})
        if x_axis is None:
            requests, payload, clients, streams = key
            folder = f"n{requests}-p{payload}-c{clients}-m{streams}"
            figure = bar_figure(key, group, average_group=average_group)
        else:
            _label, _subtitle, folder = graph_settings(key, x_axis)
            figure = line_figure(key, group, x_axis=x_axis, average_group=average_group)
        output = directory / "h2load_graphs" / folder / "h2load_summary_comparison.png"
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            figure.savefig(output, facecolor="white")
        finally:
            figure.clear()
        outputs.append(output)
        print(f"H2load comparison graphs saved: {output}", flush=True)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Dated run folder or single case folder containing both CSVs")
    parser.add_argument("--case", dest="case_name", choices=CASES)
    args = parser.parse_args(argv)
    try:
        folder = args.folder.expanduser().resolve()
        if args.case_name or folder.name in CASES:
            cases = [case_directory(folder, args.case_name)]
        else:
            cases = [folder / name for name in CASES if (folder / name).is_dir()]
        if not cases:
            raise ValueError("No supported case folders found")
        for case in cases:
            h2load_comparison_graph_creator(case)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
