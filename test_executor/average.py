"""Write average.csv using every iteration, with no minimum/maximum trimming."""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.execute_trimmed_mean import AUDIT_FIELDS, read_analysis, summarize_test
from test_executor.result_paths import case_directory
from test_executor.percentiles_average import write_details

percentiles = import_module("test_executor.95_99_percentile")


def execute_average(run_dir: Path | None = None, *, case_name: str | None = None) -> Path:
    directory = case_directory(run_dir, case_name)
    fields, rows = read_analysis(directory / "analysis.csv")
    groups = {}
    for row in rows:
        groups.setdefault(row["test_id"], []).append(row)
    summaries, all_details = [], []
    for group in groups.values():
        summary = summarize_test(fields, group, trim=False)
        details, values = percentiles.calculate_test(directory.parent, group, trim=False)
        all_details.extend(details)
        errors = [f"Iteration {row['iteration_id']} percentiles: {row['percentile_error']}"
                  for row in details if row["percentile_error"]]
        if errors:
            # Never silently present a subset as the mean of all iterations.
            values = {percentiles.P95: "", percentiles.P99: ""}
            summary["analysis_error"] = "; ".join(filter(None, [summary["analysis_error"], *errors]))
        summary.update(values)
        summaries.append(summary)
    output_fields = list(dict.fromkeys([
        *fields, "analysis_error", *AUDIT_FIELDS, percentiles.P95, percentiles.P99,
    ]))
    output = directory / "average.csv"
    percentiles.write_csv(output, output_fields, summaries)
    write_details(directory, all_details)
    print(f"Averages saved: {output} ({len(summaries)} tests; all iterations, no trimming)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Dated results folder or a single case_N folder")
    parser.add_argument("--case", dest="case_name", choices=[f"case_{n}" for n in range(1, 5)])
    args = parser.parse_args(argv)
    try:
        folder = args.folder.expanduser().resolve()
        if not folder.is_dir():
            raise ValueError(f"Results folder does not exist: {folder}")
        if args.case_name or folder.name.startswith("case_"):
            directories = [case_directory(folder, args.case_name)]
        else:
            directories = [folder / f"case_{n}" for n in range(1, 5)
                           if (folder / f"case_{n}").is_dir()]
        if not directories:
            raise ValueError("No case folders found")
        for directory in directories:
            if not (directory / "analysis.csv").is_file():
                raise ValueError(f"Missing {directory / 'analysis.csv'}; run pre_analysis first")
        for directory in directories:
            execute_average(directory)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Average failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
