"""Run case 1 using the explicit values in env.yaml."""

from __future__ import annotations

from test_executor.case_runner import run_case
from test_executor.environment import Environment
from test_executor.executor import execute


def main(config: Environment | None = None) -> None:
    run_case("case_1", execute, config)


if __name__ == "__main__":
    main()
