"""Export selected case 1 payloads from trimmed_mean.csv to a compact CSV."""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_executor.result_paths import case_directory


PAYLOADS = (1, 5, 20, 100, 256)
COLUMNS = {
    "Payload": "payload",
    "Protocol": "protocol",
    "Time For Request(Mean)": "h2load_time_for_request_mean_ms",
    "Time For Connect(Mean)": "h2load_time_for_connect_mean_ms",
    "Time To 1st Byte(Mean)": "h2load_time_to_1st_byte_mean_ms",
    "Req/s (Mean)": "h2load_req_per_sec_mean",
    "H2 Load Req/s": "h2load_req_per_sec",
}



def three_decimals(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"Invalid summary number: {value}") from None
    if not number.is_finite():
        raise ValueError(f"Invalid summary number: {value}")
    return format(number, ".3f")


def format_value(row: dict, source: str) -> str:
    value = row[source]
    if not value or source in {"payload", "protocol"}:
        return value
    mean = three_decimals(value)
    suffix = " ms" if source.endswith("_ms") else ""
    return f"{mean}{suffix}"


def case_1_summary_table(run_dir: Path | None = None) -> Path:
    run_dir = case_directory(run_dir, "case_1")
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
            if len(parts) < 2 or parts[-2] != "case_1":
                continue
            if int(row["payload"]) not in PAYLOADS:
                continue
            rows.append({label: format_value(row, source) for label, source in COLUMNS.items()})
    rows.sort(key=lambda row: (int(row["Payload"]), row["Protocol"]))
    output = run_dir / "case_1_summary_table.csv"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=run_dir, prefix=".case-1-summary-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=list(COLUMNS), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Case 1 summary saved: {output} ({len(rows)} rows; times in milliseconds, rates in req/s)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_1 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_1_summary_table(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
