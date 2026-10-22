"""Create a results directory and capture console output for one test run."""

from __future__ import annotations

import logging
import sys
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo


RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
_ACTIVE_RUN_DIR: ContextVar[Path | None] = ContextVar("test_run_directory", default=None)


def get_run_directory() -> Path | None:
    return _ACTIVE_RUN_DIR.get()


class _TeeStream:
    def __init__(self, console, log_file):
        self.console = console
        self.log_file = log_file

    def write(self, text: str) -> int:
        self.console.write(text)
        self.log_file.write(text)
        self.flush()
        return len(text)

    def flush(self) -> None:
        self.console.flush()
        self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


@contextmanager
def pre_init_test(results_dir: Path | None = None) -> Iterator[Path]:
    """Save Python stdout, stderr, logging, and exception traces in logs.txt.

    Keep test execution inside this context so its output is captured. Dates
    use Europe/Istanbul; a numeric suffix distinguishes same-second runs.
    """
    timestamp = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d_%H-%M-%S")
    root = RESULTS_DIR if results_dir is None else Path(results_dir)
    suffix = 0
    while True:
        run_dir = root / (timestamp if suffix == 0 else f"{timestamp}_{suffix}")
        try:
            run_dir.mkdir(parents=True)
            break
        except FileExistsError:
            suffix += 1

    with (run_dir / "logs.txt").open("w", encoding="utf-8") as log_file:
        with redirect_stdout(_TeeStream(sys.stdout, log_file)), \
                redirect_stderr(_TeeStream(sys.stderr, log_file)):
            logger = logging.getLogger()
            previous_handlers, previous_level = logger.handlers[:], logger.level
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.handlers = [handler]
            logger.setLevel(logging.DEBUG)
            run_token = _ACTIVE_RUN_DIR.set(run_dir)
            try:
                print(f"Test results: {run_dir}")
                yield run_dir
            except BaseException as exc:
                # The interpreter prints the traceback after streams are restored.
                # Save it here as well, without printing it twice to the console.
                if not isinstance(exc, SystemExit) or exc.code not in (None, 0):
                    traceback.print_exc(file=log_file)
                raise
            finally:
                _ACTIVE_RUN_DIR.reset(run_token)
                handler.flush()
                logger.handlers = previous_handlers
                logger.setLevel(previous_level)
                handler.close()


if __name__ == "__main__":
    with pre_init_test():
        pass
