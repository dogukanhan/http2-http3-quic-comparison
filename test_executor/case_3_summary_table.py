"""Export selected case 3 delays from trimmed_mean.csv to a compact CSV."""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.result_paths import case_directory
from test_executor.case_1_summary_table import COLUMNS as CASE_1_COLUMNS, format_value


DELAYS = (0, 50, 100)
COLUMNS = {"delay": "delay", **{label: field for label, field in CASE_1_COLUMNS.items()
                                       if field != "payload"}}


def case_3_summary_table(run_dir: Path | None = None) -> Path:
    run_dir = case_directory(run_dir, "case_3")
    with (run_dir / "trimmed_mean.csv").open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"test_id", *COLUMNS.values()}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"trimmed_mean.csv is missing columns: {', '.join(sorted(missing))}")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed trimmed_mean.csv row")
            parts = Path(row["test_id"]).parts
            if len(parts) < 2 or parts[-2] != "case_3":
                continue
            if row["protocol"] not in ("h2", "h3") or int(row["delay"]) not in DELAYS:
                continue
            rows.append({label: str(int(row[source])) if source == "delay" else format_value(row, source)
                         for label, source in COLUMNS.items()})
    rows.sort(key=lambda row: (int(row["delay"]), row["Protocol"]))
    output = run_dir / "case_3_summary_table.csv"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=run_dir, prefix=".case-3-summary-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=list(COLUMNS), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Case 3 summary saved: {output} ({len(rows)} rows; times in milliseconds, rates in req/s)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_3 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_3_summary_table(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
