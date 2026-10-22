"""Resolve case-local report folders while preserving the dated run context."""

from __future__ import annotations

import re
from pathlib import Path

from test_executor.pre_init_test import get_run_directory


def case_directory(run_dir: Path | None = None, case_name: str | None = None) -> Path:
    """Accept a run or case folder; default to case_1 for run-level calls."""
    directory = get_run_directory() if run_dir is None else Path(run_dir)
    if directory is None:
        raise RuntimeError("No active test run; provide a dated run or case directory")
    directory = directory.resolve()
    if case_name is not None and not re.fullmatch(r"case_[1-9]\d*", case_name):
        raise ValueError("case_name must be a case folder such as case_1 or case_2")
    if re.fullmatch(r"case_[1-9]\d*", directory.name):
        if case_name is not None and directory.name != case_name:
            raise ValueError(f"Expected {case_name}, got {directory.name}")
        return directory
    return directory / (case_name or "case_1")
