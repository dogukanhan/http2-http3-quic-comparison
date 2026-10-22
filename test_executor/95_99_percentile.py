"""Calculate request p95/p99 and append p99-trimmed means to trimmed_mean.csv."""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.execute_trimmed_mean import RANKING_FIELD, average, read_analysis, trim_indices
from test_executor.pre_analysis import INPUT_FIELDS
from test_executor.result_paths import case_directory


P95 = "h2load_time_for_request_p95_ms"
P99 = "h2load_time_for_request_p99_ms"
DETAIL_FIELDS = [*INPUT_FIELDS, P95, P99, "request_samples", "failed_streams",
                 "percentile_trim_role", "percentile_error"]


def percentile_milliseconds(sorted_microseconds: list[int], percent: int) -> Decimal:
    """Linear interpolation at (n - 1) * p, equivalent to PERCENTILE.INC."""
    if not sorted_microseconds:
        raise ValueError("No completed request samples")
    if not 0 <= percent <= 100:
        raise ValueError("Percentile must be between 0 and 100")
    position = Decimal(len(sorted_microseconds) - 1) * Decimal(percent) / 100
    lower = int(position)
    upper = min(lower + 1, len(sorted_microseconds) - 1)
    microseconds = (Decimal(sorted_microseconds[lower])
                    + (Decimal(sorted_microseconds[upper]) - sorted_microseconds[lower]) * (position - lower))
    return microseconds / 1000


def read_requests(path: Path) -> tuple[list[int], int]:
    """Read completed response durations; h2load marks failed streams with -1."""
    durations, failed = [], 0
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            fields = line.rstrip("\r\n").split("\t")
            try:
                if len(fields) < 3:
                    raise ValueError("Expected at least three tab-separated columns")
                start, status, elapsed = map(int, fields[:3])
                if status == -1:
                    failed += 1
                    continue
                if start < 0 or elapsed < 0:
                    raise ValueError("Negative request timestamp or duration")
                if not 100 <= status <= 599:
                    raise ValueError(f"Invalid HTTP status {status}")
            except ValueError as exc:
                raise ValueError(f"Invalid requests.log line {line_number}: {exc}") from exc
            durations.append(elapsed)
    durations.sort()
    return durations, failed


def request_log(run_dir: Path, row: dict) -> Path:
    """Resolve the exact test ID, including the case folder, from analysis.csv."""
    run_dir = run_dir.resolve()
    parts = Path(row["test_id"]).parts
    if len(parts) != 3 or parts[0] != run_dir.name or any(part in (".", "..") for part in parts):
        raise ValueError(f"Test ID does not belong to this run: {row['test_id']}")
    parameter_dir = (run_dir / parts[1] / parts[2]).resolve()
    if not parameter_dir.is_relative_to(run_dir):
        raise ValueError("Test folder is outside the run directory")
    candidates = [path for path in parameter_dir.iterdir()
                  if path.is_dir() and path.name.isdigit()
                  and int(path.name) == int(row["iteration_id"])]
    if len(candidates) != 1:
        raise ValueError(f"Expected one folder for iteration {row['iteration_id']}; found {len(candidates)}")
    path = (candidates[0] / "requests.log").resolve()
    if not path.is_relative_to(run_dir):
        raise ValueError("Request log is outside the run directory")
    return path


def calculate_test(run_dir: Path, rows: list[dict], *, trim: bool = True) -> tuple[list[dict], dict]:
    details, eligible, ranks = [], [], []
    for row in sorted(rows, key=lambda item: int(item["iteration_id"])):
        detail = {key: row[key] for key in INPUT_FIELDS}
        detail.update({P95: "", P99: "", "request_samples": "", "failed_streams": "",
                       "percentile_trim_role": "excluded", "percentile_error": ""})
        try:
            durations, failed = read_requests(request_log(run_dir, row))
            detail.update(request_samples=len(durations), failed_streams=failed)
            p95 = percentile_milliseconds(durations, 95)
            p99 = percentile_milliseconds(durations, 99)
            detail.update({P95: p95, P99: p99})
            eligible.append(detail)
            ranks.append(p99)
        except (OSError, ValueError) as exc:
            detail["percentile_error"] = str(exc)
            print(f"Percentile warning for {row['test_id']} iteration {row['iteration_id']}: {exc}", flush=True)
        details.append(detail)
    minimum, maximum, retained = (trim_indices(eligible, ranks) if trim
                                  else ([], [], list(range(len(eligible)))))
    for role, indices in (("min_dismiss", minimum), ("max_dissmiss", maximum),
                          ("mean_of_iterations", retained)):
        for index in indices:
            eligible[index]["percentile_trim_role"] = role
    result = {key: average([eligible[index][key] for index in retained]) if retained else ""
              for key in (P95, P99)}
    # Ranking and averaging use full precision. Round only the output cells.
    for detail in details:
        for key in (P95, P99):
            if isinstance(detail[key], Decimal):
                detail[key] = format(detail[key], ".6f")
    return details, result


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def execute_percentiles(run_dir: Path | None = None, *, case_name: str | None = None) -> Path:
    """Save per-iteration percentiles and join their trimmed means by test ID."""
    run_dir = case_directory(run_dir, case_name)
    _fields, analysis = read_analysis(run_dir / "analysis.csv")
    groups = {}
    for row in analysis:
        parts = Path(row["test_id"]).parts
        if len(parts) != 3 or parts[-2] != run_dir.name:
            raise ValueError(f"Analysis row belongs to a different case: {row['test_id']}")
        group = groups.setdefault(row["test_id"], [])
        if group and any(row[key] != group[0][key] for key in INPUT_FIELDS[2:]):
            raise ValueError(f"Inconsistent input parameters for {row['test_id']}")
        group.append(row)
    output = run_dir / "trimmed_mean.csv"
    with output.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        fields, means = reader.fieldnames, list(reader)
    if not fields or len(set(fields)) != len(fields) or not set(INPUT_FIELDS).issubset(fields):
        raise ValueError("trimmed_mean.csv must have unique semicolon-separated columns and all inputs")
    if RANKING_FIELD not in fields:
        raise ValueError("trimmed_mean.csv uses an older duration format; regenerate analysis and trimmed means in milliseconds first")
    seen = set()
    for row in means:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Malformed trimmed_mean.csv row")
        test_id = row["test_id"]
        if test_id in seen or test_id not in groups:
            raise ValueError(f"Duplicate or unmatched trimmed mean test: {test_id}")
        if any(row[key] != groups[test_id][0][key] for key in INPUT_FIELDS[2:]):
            raise ValueError(f"Input parameters do not match analysis.csv for {test_id}")
        seen.add(test_id)
    if seen != set(groups):
        raise ValueError("trimmed_mean.csv does not contain all analysis tests; regenerate it first")

    details = []
    for index, row in enumerate(means, 1):
        iterations, values = calculate_test(run_dir.parent, groups[row["test_id"]])
        details.extend(iterations)
        row.update(values)
        if index % 50 == 0:
            print(f"Calculated request percentiles for {index}/{len(means)} tests.", flush=True)
    fields = [*fields, *[key for key in (P95, P99) if key not in fields]]
    detail_path = run_dir / "percentiles.csv"
    write_csv(detail_path, DETAIL_FIELDS, details)
    write_csv(output, fields, means)
    print(f"Iteration percentiles saved: {detail_path} ({len(details)} iterations)", flush=True)
    print(f"P99-trimmed percentile means added: {output} ({len(means)} tests)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case folder containing analysis.csv and trimmed_mean.csv")
    parser.add_argument("--case", dest="case_name", help="case_1 (default), case_2, case_3, or case_4 when supplying a run folder")
    args = parser.parse_args(argv)
    try:
        execute_percentiles(args.run_dir, case_name=args.case_name)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
