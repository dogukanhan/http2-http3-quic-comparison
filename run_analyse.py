"""Regenerate analysis, summary tables, and graphs from an existing results folder."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from importlib import import_module
from pathlib import Path
import sys
import traceback
from zoneinfo import ZoneInfo

from test_executor.pre_analysis import PARAMETERS, pre_analysis
from test_executor.execute_trimmed_mean import execute_trimmed_mean
from test_executor.average import execute_average
from test_executor.h2load_comparison_graph_creator import h2load_comparison_graph_creator
from test_executor.percentile_summary_average_graph_creator import percentile_summary_average_graph_creator
from test_executor.pre_init_test import _TeeStream
from test_executor.case_1_summary_table import case_1_summary_table
from test_executor.case_2_summary_table import case_2_summary_table
from test_executor.case_3_summary_table import case_3_summary_table
from test_executor.case_4_summary_table import case_4_summary_table
from test_executor.case_4_all_delays_summary_table import case_4_all_delays_summary_table
from test_executor.case_4_resource_summary_table import case_4_resource_summary_table
from test_executor.case_4_all_delays_resource_summary_table import case_4_all_delays_resource_summary_table
from test_executor.case_1_resource_graph_creator import case_1_resource_graph_creator
from test_executor.case_1_h2load_graph_creator import case_1_h2load_graph_creator
from test_executor.case_1_percentile_graph_creator import case_1_percentile_graph_creator
from test_executor.resource_graph_creator import resource_graph_creator
from test_executor.h2load_graph_creator import h2load_graph_creator
from test_executor.percentile_graph_creator import percentile_graph_creator
from test_executor.case_3_resource_graph_creator import case_3_resource_graph_creator
from test_executor.case_3_h2load_graph_creator import case_3_h2load_graph_creator
from test_executor.case_3_percentile_graph_creator import case_3_percentile_graph_creator
from test_executor.case_4_h2load_graph_creator import case_4_h2load_graph_creator


execute_percentiles = import_module("test_executor.95_99_percentile").execute_percentiles
REPORT_STEPS = {
    "case_1": (case_1_summary_table, case_1_resource_graph_creator,
               case_1_h2load_graph_creator, case_1_percentile_graph_creator, h2load_comparison_graph_creator, percentile_summary_average_graph_creator),
    "case_2": (case_2_summary_table, resource_graph_creator,
               h2load_graph_creator, percentile_graph_creator, h2load_comparison_graph_creator, percentile_summary_average_graph_creator),
    "case_3": (case_3_summary_table, case_3_resource_graph_creator,
               case_3_h2load_graph_creator, case_3_percentile_graph_creator, h2load_comparison_graph_creator, percentile_summary_average_graph_creator),
    "case_4": (case_4_summary_table, case_4_all_delays_summary_table,
               case_4_resource_summary_table, case_4_all_delays_resource_summary_table,
               case_4_h2load_graph_creator, h2load_comparison_graph_creator, percentile_summary_average_graph_creator),
}


def find_cases(folder: Path) -> list[Path]:
    """Select existing cases and check raw iteration folders before replacing reports."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"Results folder does not exist: {folder}")
    cases = ([folder] if folder.name in REPORT_STEPS else
             [folder / name for name in REPORT_STEPS if (folder / name).is_dir()])
    if not cases:
        raise ValueError("Provide a dated results folder containing case_1–case_4, or one of those case folders")
    for case in cases:
        has_iterations = any(
            iteration.is_dir() and iteration.name.isascii() and iteration.name.isdigit()
            for parameters in case.iterdir()
            if parameters.is_dir() and PARAMETERS.fullmatch(parameters.name)
            for iteration in parameters.iterdir()
        )
        if not has_iterations:
            raise ValueError(f"No raw iteration folders found in {case}; CSV files alone cannot regenerate all analyses")
    return cases


def run_analyse(folder: Path) -> None:
    cases = find_cases(folder)
    run_dir = cases[0].parent
    with (run_dir / "logs.txt").open("a", encoding="utf-8") as log:
        with redirect_stdout(_TeeStream(sys.stdout, log)), redirect_stderr(_TeeStream(sys.stderr, log)):
            timestamp = datetime.now(ZoneInfo("Europe/Istanbul")).isoformat(timespec="seconds")
            print(f"\nAnalysis regeneration started: {timestamp}", flush=True)
            print(f"Results: {run_dir}; cases: {', '.join(case.name for case in cases)}", flush=True)
            try:
                for case in cases:
                    print(f"Regenerating analysis for {case.name}...", flush=True)
                    pre_analysis(case)
                    execute_trimmed_mean(case)
                    execute_percentiles(case)
                    execute_average(case)
                for case in cases:
                    for step in REPORT_STEPS[case.name]:
                        print(f"Running {step.__name__} for {case.name}...", flush=True)
                        step(case)
            except BaseException:
                traceback.print_exc(file=log)
                raise
            print("Analysis, summary tables, and graphs regenerated successfully.", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path,
                        help="Dated test_results folder or a single case_N folder; replaces generated reports")
    args = parser.parse_args(argv)
    try:
        run_analyse(args.folder)
    except KeyboardInterrupt:
        print("Analysis interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
