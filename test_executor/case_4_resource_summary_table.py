"""Export case 4 server resource usage at 10 ms delay to a second summary CSV."""

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
from test_executor.case_4_summary_table import DELAY_MS


COLUMNS = ("Protocol", "packet_loss", "CPU Counter (ms)", "Memory Usage (MiB)", "Network Usage (MiB)")
RESOURCE_FIELDS = ("resource_cpu_counter_ns", "resource_memory_bytes",
                   "resource_network_rx_bytes", "resource_network_tx_bytes")
MIB = Decimal(1024 ** 2)


def measurement(row: dict, *fields: str, divisor: Decimal = Decimal(1)) -> str:
    values = []
    for field in fields:
        if not row[field].strip():
            return ""
        try:
            value = Decimal(row[field])
        except InvalidOperation:
            value = Decimal("NaN")
        if not value.is_finite() or value < 0:
            print(f"Resource summary warning: {row['test_id']} has invalid {field}; leaving blank.", flush=True)
            return ""
        values.append(value)
    return format(sum(values) / divisor, ".3f")


def case_4_resource_summary_table(run_dir: Path | None = None) -> Path:
    run_dir = case_directory(run_dir, "case_4")
    with (run_dir / "trimmed_mean.csv").open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"test_id", "delay", "protocol", "packet_loss", *RESOURCE_FIELDS}
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
            if row["protocol"] not in ("h2", "h3") or int(row["delay"]) != DELAY_MS:
                continue
            rows.append({
                "Protocol": row["protocol"],
                "packet_loss": str(int(row["packet_loss"])),
                "CPU Counter (ms)": measurement(row, "resource_cpu_counter_ns", divisor=Decimal(1_000_000)),
                "Memory Usage (MiB)": measurement(row, "resource_memory_bytes", divisor=MIB),
                "Network Usage (MiB)": measurement(row, "resource_network_rx_bytes", "resource_network_tx_bytes", divisor=MIB),
            })
    rows.sort(key=lambda row: (int(row["packet_loss"]), row["Protocol"]))
    output = run_dir / "case_4_resource_summary_table.csv"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=run_dir, prefix=".case-4-resource-summary-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=list(COLUMNS), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Case 4 resource summary saved: {output} ({len(rows)} rows at 10 ms delay; CPU counter in ms, memory/network in MiB)", flush=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="case_4 folder containing trimmed_mean.csv (or its dated run folder)")
    args = parser.parse_args(argv)
    try:
        case_4_resource_summary_table(args.run_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
