"""Combine iterations of each test into a semicolon-separated trimmed-mean CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.pre_analysis import H2LOAD_FIELDS, INPUT_FIELDS, RESOURCE_FIELDS
from test_executor.result_paths import case_directory


AUDIT_FIELDS = ["min_dismiss", "max_dissmiss", "mean_of_iterations"]
RANKING_FIELD = "h2load_time_for_request_mean_ms"
TEXT_FIELDS = {
    "test_id", "iteration_id", "protocol", "analysis_error", "h2load_throughput_unit",
    "h2load_tls_protocol", "h2load_cipher", "h2load_application_protocol",
    "resource_type", "resource_timestamp", "resource_container", "resource_server_host",
}
THROUGHPUT_SCALES = {f"{prefix}B/s": Decimal(1024) ** power
                     for power, prefix in enumerate(("", "K", "M", "G", "T", "P", "E"))}


def number(value: str) -> Decimal | None:
    """Read numeric cells without losing precision in large byte/CPU counters."""
    try:
        result = Decimal(value.strip())
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def average(values: list[Decimal]) -> str:
    # Calculate with Decimal precision, then round only the exported result.
    with localcontext() as context:
        context.prec = max(50, max(len(value.as_tuple().digits) for value in values) + 20)
        return format(sum(values) / len(values), ".6f")


def read_analysis(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        fields = reader.fieldnames
        if not fields or len(set(fields)) != len(fields):
            raise ValueError("analysis.csv must have unique semicolon-separated column names")
        missing = set(INPUT_FIELDS) - set(fields)
        if missing:
            raise ValueError(f"analysis.csv is missing columns: {', '.join(sorted(missing))}")
        if set(fields) & set(AUDIT_FIELDS):
            raise ValueError("Expected per-iteration analysis.csv, not an already aggregated CSV")
        rows = list(reader)
    if not rows:
        raise ValueError("analysis.csv contains no iteration rows")
    seen = set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("analysis.csv has a row with the wrong number of columns")
        if not row["test_id"] or not row["iteration_id"].isdigit() or int(row["iteration_id"]) < 1:
            raise ValueError("Each row requires a test_id and a positive numeric iteration_id")
        identity = (row["test_id"], int(row["iteration_id"]))
        if identity in seen:
            raise ValueError(f"Duplicate iteration: {identity}")
        seen.add(identity)
    return fields, rows


def trim_indices(rows: list[dict], values: list[Decimal]) -> tuple[list[int], list[int], list[int]]:
    """Discard exactly one minimum and maximum; use all rows when fewer than three."""
    if len(rows) < 3:
        return [], [], list(range(len(rows)))
    ordered = sorted(range(len(rows)), key=lambda i: (values[i], int(rows[i]["iteration_id"])))
    return ordered[:1], ordered[-1:], sorted(ordered[1:-1])


def iteration_ids(rows: list[dict], indices: list[int]) -> list[int]:
    return sorted(int(rows[index]["iteration_id"]) for index in indices)


def summarize_test(fields: list[str], rows: list[dict], *, trim: bool = True) -> dict:
    rows = sorted(rows, key=lambda row: int(row["iteration_id"]))
    summary = {key: rows[0][key] for key in INPUT_FIELDS}
    summary["iteration_id"] = "trimmed_mean" if trim else "average"
    for row in rows:
        if any(row[key] != rows[0][key] for key in INPUT_FIELDS[2:]):
            raise ValueError(f"Inconsistent input parameters for test {row['test_id']}")

    errors = [f"Iteration {row['iteration_id']}: {row['analysis_error']}"
              for row in rows if row.get("analysis_error")]
    eligible, ranks = [], []
    for original in rows:
        row = original.copy()
        rank = number(row.get(RANKING_FIELD, ""))
        if trim and (rank is None or rank < 0):
            errors.append(f"Excluded iteration {row['iteration_id']}: missing/invalid {RANKING_FIELD}")
            continue
        # h2load can change the displayed unit between iterations. Convert before averaging.
        if row.get("h2load_throughput", "").strip():
            rate = number(row["h2load_throughput"])
            scale = THROUGHPUT_SCALES.get(row.get("h2load_throughput_unit", ""))
            if rate is None or scale is None:
                errors.append(f"Iteration {row['iteration_id']}: invalid throughput or unit")
                row["h2load_throughput"] = ""
            else:
                row["h2load_throughput"] = str(rate * scale)
                row["h2load_throughput_unit"] = "B/s"
        eligible.append(row)
        ranks.append(rank)
    minimum, maximum, retained_indices = (trim_indices(eligible, ranks) if trim
                                          else ([], [], list(range(len(eligible)))))
    retained = [eligible[index] for index in retained_indices]
    for field, indices in zip(AUDIT_FIELDS, (minimum, maximum, retained_indices)):
        summary[field] = json.dumps(iteration_ids(eligible, indices))
    if not retained:
        errors.append("No valid iterations available to calculate means")
    elif trim and len(eligible) < 3:
        print(f"Test {summary['test_id']}: only {len(eligible)} valid iteration(s); using all without trimming.",
              flush=True)

    known_numeric = (set(INPUT_FIELDS) | {f"h2load_{key}" for key in H2LOAD_FIELDS}
                     | {f"resource_{key}" for key in RESOURCE_FIELDS}) - TEXT_FIELDS
    for field in fields:
        if field in INPUT_FIELDS or field == "analysis_error":
            continue
        raw = [row[field] for row in retained]
        populated = [value for value in raw if value.strip()]
        if not populated:
            # Nonapplicable columns (such as HTTP/2 UDP counts) remain blank.
            summary.setdefault(field, "")
            continue
        numeric = field in known_numeric or (
            field not in TEXT_FIELDS and any(number(value) is not None for value in populated))
        if numeric:
            values = [number(value) for value in raw]
            if any(value is None for value in values):
                summary[field] = ""
                errors.append(f"No mean for {field}: missing/invalid values among retained iterations")
            else:
                summary[field] = average(values)
        else:
            # Metadata is not averaged; preserve differing values as a JSON list.
            unique = list(dict.fromkeys(raw))
            summary[field] = unique[0] if len(unique) == 1 else json.dumps(unique, ensure_ascii=False)
    summary["analysis_error"] = "; ".join(errors)
    if errors:
        label = "Trimmed mean" if trim else "Average"
        print(f"{label} warning for {summary['test_id']}: {summary['analysis_error']}", flush=True)
    return summary


def execute_trimmed_mean(run_dir: Path | None = None, *, case_name: str | None = None) -> Path:
    """Read analysis.csv and write one aggregate row per test to trimmed_mean.csv."""
    run_dir = case_directory(run_dir, case_name)
    fields, rows = read_analysis(run_dir / "analysis.csv")
    if RANKING_FIELD not in fields:
        raise ValueError(f"analysis.csv is missing ranking column {RANKING_FIELD}")
    groups = {}
    for row in rows:
        groups.setdefault(row["test_id"], []).append(row)
    means = [summarize_test(fields, group) for group in groups.values()]
    output_fields = [*fields]
    if "analysis_error" not in output_fields:
        output_fields.append("analysis_error")
    output_fields.extend(AUDIT_FIELDS)
    output = run_dir / "trimmed_mean.csv"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=run_dir, prefix=".trimmed-mean-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=output_fields, delimiter=";")
            writer.writeheader()
            writer.writerows(means)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Trimmed means saved: {output} ({len(means)} tests)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case folder containing analysis.csv, or a run folder with --case")
    parser.add_argument("--case", dest="case_name", help="case_1 (default), case_2, case_3, or case_4")
    args = parser.parse_args(argv)
    try:
        execute_trimmed_mean(args.run_dir, case_name=args.case_name)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
