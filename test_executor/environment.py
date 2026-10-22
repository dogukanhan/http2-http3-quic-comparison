"""Load local/remote execution settings without exposing credentials in logs."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parent.parent / "env.yaml"
CASE_NAMES = ("case_1", "case_2", "case_3", "case_4")


@dataclass(frozen=True)
class CaseParameters:
    protocols: tuple[str, ...]
    payloads: tuple[int, ...]
    clients: tuple[int, ...]
    delays: tuple[int, ...]
    packet_losses: tuple[int, ...]
    total_requests: int
    max_streams: int
    iterations: int


def parse_case_parameters(name: str, values: dict) -> CaseParameters:
    required = set(CaseParameters.__dataclass_fields__)
    if not isinstance(values, dict) or set(values) != required:
        raise ValueError(f"env.yaml {name} must contain exactly: {', '.join(sorted(required))}")
    parsed = dict(values)
    for field_name in ("protocols", "payloads", "clients", "delays", "packet_losses"):
        items = values[field_name]
        location = f"env.yaml {name}.{field_name}"
        if not isinstance(items, list) or not items:
            raise ValueError(f"{location} must be a nonempty list of explicit values, not a range")
        if field_name == "protocols":
            if any(type(item) is not str or item not in ("h2", "h3") for item in items):
                raise ValueError(f"{location} must contain h2 and/or h3")
        else:
            minimum = 0 if field_name in ("delays", "packet_losses") else 1
            if any(type(item) is not int or item < minimum for item in items):
                raise ValueError(f"{location} must contain integers >= {minimum}")
            if field_name == "packet_losses" and any(item > 100 for item in items):
                raise ValueError(f"{location} must contain percentages between 0 and 100")
        if len(set(items)) != len(items):
            raise ValueError(f"{location} must not contain duplicate values")
        parsed[field_name] = tuple(items)
    for field_name in ("total_requests", "max_streams", "iterations"):
        if type(values[field_name]) is not int or values[field_name] < 1:
            raise ValueError(f"env.yaml {name}.{field_name} must be a positive integer")
    return CaseParameters(**parsed)


@dataclass(frozen=True)
class Environment:
    remote: bool = False
    username: str = ""
    password: str = field(default="", repr=False)
    ip_address: str = ""
    ssh_port: int = 22
    archtype: str = "arm"
    case_1_enabled: bool = True
    case_2_enabled: bool = False
    case_3_enabled: bool = False
    case_4_enabled: bool = False
    case_1: CaseParameters | None = None
    case_2: CaseParameters | None = None
    case_3: CaseParameters | None = None
    case_4: CaseParameters | None = None

    def case_parameters(self, name: str) -> CaseParameters:
        if name not in CASE_NAMES:
            raise ValueError("Unknown test case")
        parameters = getattr(self, name)
        if parameters is None:
            raise ValueError(f"env.yaml is missing {name} parameters; copy its block from env.example.yaml")
        return parameters


def load_environment(path: Path | None = None) -> Environment:
    path = CONFIG_PATH if path is None else Path(path)
    if not path.is_file():
        raise RuntimeError(f"Configuration not found: {path}. Copy env.example.yaml to env.yaml.")
    try:
        import yaml
    except ImportError:
        raise RuntimeError("Install test dependencies: python3 -m pip install -r requirements.txt") from None
    try:
        values = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        # YAML parser errors can include the password line; never echo them.
        raise ValueError(f"Invalid YAML in {path}; check its syntax.") from None
    if not isinstance(values, dict) or type(values.get("remote")) is not bool:
        raise ValueError("env.yaml must contain remote: true or remote: false (without quotes)")
    allowed = {"remote", "username", "password", "ip_address", "ssh_port", "archtype",
               "case_1_enabled", "case_2_enabled", "case_3_enabled", "case_4_enabled", *CASE_NAMES}
    if set(values) - allowed:
        raise ValueError("Unknown env.yaml field; see env.example.yaml for connection settings, case switches, and case parameter blocks")
    if values.get("archtype", "arm") not in ("amd64", "arm"):
        raise ValueError("env.yaml archtype must be amd64 or arm")
    for name in ("case_1_enabled", "case_2_enabled", "case_3_enabled", "case_4_enabled"):
        if name in values and type(values[name]) is not bool:
            raise ValueError(f"env.yaml {name} must be true or false (without quotes)")
    for name in ("username", "password", "ip_address"):
        if not isinstance(values.get(name, ""), str):
            raise ValueError(f"env.yaml {name} must be a quoted string")
    port = values.get("ssh_port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("ssh_port must be an integer from 1 to 65535")
    for name in CASE_NAMES:
        if name in values:
            values[name] = parse_case_parameters(name, values[name])
    config = Environment(**values)
    if config.remote:
        if not config.username.strip():
            raise ValueError("Remote mode requires a username in env.yaml")
        try:
            ipaddress.ip_address(config.ip_address)
        except ValueError:
            raise ValueError("Remote mode requires a valid IPv4 or IPv6 ip_address in env.yaml") from None
    return config
