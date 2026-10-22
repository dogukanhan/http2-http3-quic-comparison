"""Execute the explicit parameter combinations configured for one case."""

from __future__ import annotations

from itertools import product
import math
import time
from typing import Callable

from test_executor.environment import Environment, load_environment


def format_duration(seconds: float) -> str:
    hours, remainder = divmod(math.ceil(max(0, seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def run_case(name: str, execute: Callable, config: Environment | None = None) -> None:
    config = load_environment() if config is None else config
    parameters = config.case_parameters(name)
    dimensions = (parameters.protocols, parameters.payloads, parameters.clients,
                  parameters.delays, parameters.packet_losses)
    total = math.prod(len(values) for values in dimensions)
    total_duration = 0.0
    for index, (protocol, payload, clients, delay, loss) in enumerate(product(*dimensions), 1):
        remaining = (format_duration(total_duration / (index - 1) * (total - index + 1))
                     if index > 1 else "estimating after first test")
        description = (f"{name}: {protocol}, payload {payload} KB, {clients} clients, delay {delay} ms, "
                       f"loss {loss}%, {parameters.total_requests} requests, {parameters.max_streams} streams, "
                       f"{parameters.iterations} iterations [{index}/{total}]")
        print(f"Running {description} | Estimated total time left: {remaining}", flush=True)
        started = time.monotonic()
        execute(folder_name=name, protocol=protocol, payload=payload,
                total_request=parameters.total_requests, client=clients,
                max_streams=parameters.max_streams, delay=delay, packet_loss=loss,
                iterations=parameters.iterations)
        duration = time.monotonic() - started
        total_duration += duration
        average = total_duration / index
        print(f"Completed {description} | Duration: {format_duration(duration)} | "
              f"Average per test: {format_duration(average)} | "
              f"Estimated total time left: {format_duration(average * (total - index))}", flush=True)
