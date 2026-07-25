"""Launch AuraBot's native MCP server over Streamable HTTP."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from server import create_server


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int
    path: str


def load_endpoint(config_path: Path) -> Endpoint:
    """Load and validate local_mcp_server from the AuraBot YAML config."""

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to read MCP configuration: {config_path}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("AuraBot configuration root must be a mapping.")
    raw_url = loaded.get("local_mcp_server")
    if not isinstance(raw_url, str) or not raw_url:
        raise ValueError("local_mcp_server must be a non-empty URL.")

    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("local_mcp_server must use http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("local_mcp_server must not contain credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("local_mcp_server must not contain query or fragment.")
    if parsed.hostname is None:
        raise ValueError("local_mcp_server must include a host.")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("local_mcp_server contains an invalid port.") from exc

    path = parsed.path or "/mcp"
    return Endpoint(host=parsed.hostname, port=port, path=_normalize_path(path))


def load_backend_port(config_path: Path) -> int:
    """Load the loopback backend gateway port used only by Plan proxy handlers."""

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(
            f"Unable to read backend configuration: {config_path}"
        ) from exc
    ports = loaded.get("ports") if isinstance(loaded, dict) else None
    backend_port = (
        ports.get("backend_gateway")
        if isinstance(ports, dict)
        else None
    )
    if (
        not isinstance(backend_port, int)
        or isinstance(backend_port, bool)
        or not 1 <= backend_port <= 65535
    ):
        raise ValueError(
            "ports.backend_gateway must be an integer between 1 and 65535."
        )
    return backend_port


def _normalize_path(path: str) -> str:
    if not path:
        raise ValueError("MCP path must be non-empty.")
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--path")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="INFO",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    endpoint = load_endpoint(config_path)
    backend_port = load_backend_port(config_path)
    host = args.host or endpoint.host
    port = args.port if args.port is not None else endpoint.port
    path = _normalize_path(args.path) if args.path else endpoint.path

    if not 1 <= port <= 65535:
        raise SystemExit("--port must be between 1 and 65535.")

    server = create_server(
        host=host,
        port=port,
        streamable_http_path=path,
        backend_port=backend_port,
        log_level=args.log_level,
    )
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
