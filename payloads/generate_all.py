#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path


START_KB = 1
END_KB = 256
STEP_KB = 1


def main() -> int:
    payloads_dir = Path(__file__).resolve().parent
    generator_path = payloads_dir / "generator.py"

    for size_kb in range(START_KB, END_KB + 1, STEP_KB):
        output_path = payloads_dir / f"payload_{size_kb}kb.json"
        target_size_bytes = size_kb * 1024

        print(f"Generating {output_path.name} ({target_size_bytes} bytes target)")
        subprocess.run(
            [
                sys.executable,
                str(generator_path),
                "--size",
                str(target_size_bytes),
                "--output",
                str(output_path),
            ],
            check=True,
        )

    print("Finished generating payload files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
