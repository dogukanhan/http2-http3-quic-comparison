"""Export per-iteration p95/p99 with every valid iteration retained."""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.execute_trimmed_mean import read_analysis
from test_executor.pre_analysis import INPUT_FIELDS
from test_executor.result_paths import case_directory

percentiles = import_module("test_executor.95_99_percentile")


def write_details(directory: Path, details: list[dict]) -> Path:
    output = directory / "percentiles_average.csv"
    percentiles.write_csv(output, percentiles.DETAIL_FIELDS, details)
    print(f"Untrimmed iteration percentiles saved: {output} ({len(details)} iterations)", flush=True)
    return output


def execute_percentiles_average(run_dir: Path | None = None, *, case_name: str | None = None) -> Path:
    directory = case_directory(run_dir, case_name)
    _fields, rows = read_analysis(directory / "analysis.csv")
    groups = {}
    for row in rows:
        parts = Path(row["test_id"]).parts
        if len(parts) != 3 or parts[-2] != directory.name:
            raise ValueError(f"Analysis row belongs to a different case: {row['test_id']}")
        group = groups.setdefault(row["test_id"], [])
        if group and any(row[key] != group[0][key] for key in INPUT_FIELDS[2:]):
            raise ValueError(f"Inconsistent input parameters for {row['test_id']}")
        group.append(row)
    details = []
    for group in groups.values():
        iterations, _values = percentiles.calculate_test(directory.parent, group, trim=False)
        details.extend(iterations)
    return write_details(directory, details)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Dated results folder or a single case_N folder")
    parser.add_argument("--case", dest="case_name", choices=[f"case_{n}" for n in range(1, 5)])
    args = parser.parse_args(argv)
    try:
        folder = args.folder.expanduser().resolve()
        cases = ([case_directory(folder, args.case_name)] if args.case_name or folder.name.startswith("case_")
                 else [folder / f"case_{n}" for n in range(1, 5) if (folder / f"case_{n}").is_dir()])
        if not cases:
            raise ValueError("No supported case folders found")
        for case in cases:
            execute_percentiles_average(case)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
