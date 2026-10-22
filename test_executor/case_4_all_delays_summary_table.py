"""Export case 4 packet losses across all delays from trimmed_mean.csv to a compact CSV."""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.result_paths import case_directory
from test_executor.case_1_summary_table import COLUMNS as CASE_1_COLUMNS, STANDARD_DEVIATIONS, format_value


COLUMNS = {"delay": "delay", "packet_loss": "packet_loss", **{label: field for label, field in CASE_1_COLUMNS.items()
                                       if field != "payload"}}


def case_4_all_delays_summary_table(run_dir: Path | None = None) -> Path:
    run_dir = case_directory(run_dir, "case_4")
    with (run_dir / "trimmed_mean.csv").open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"test_id", "delay", *COLUMNS.values(),
                    *STANDARD_DEVIATIONS.values()}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"trimmed_mean.csv is missing columns: {', '.join(sorted(missing))}")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed trimmed_mean.csv row")
            parts = Path(row["test_id"]).parts
            if len(parts) < 2 or parts[-2] != "case_4":
                continue
            if row["protocol"] not in ("h2", "h3"):
                continue
            rows.append({label: str(int(row[source])) if source in {"delay", "packet_loss"} else format_value(row, source)
                         for label, source in COLUMNS.items()})
    rows.sort(key=lambda row: (int(row["delay"]), int(row["packet_loss"]), row["Protocol"]))
    output = run_dir / "case_4_all_delays_summary_table.csv"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=run_dir, prefix=".case-4-all-delays-summary-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=list(COLUMNS), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Case 4 all-delays summary saved: {output} ({len(rows)} rows across all delays; times in milliseconds, rates in req/s)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_4 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_4_all_delays_summary_table(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
