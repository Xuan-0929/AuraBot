from pathlib import Path

import pytest

from main import load_backend_port, load_endpoint


def write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_mcp_endpoint_and_backend_gateway_port(tmp_path):
    config = write_config(
        tmp_path / "config.yaml",
        (
            "local_mcp_server: http://127.0.0.1:7410/native/mcp/\n"
            "ports:\n"
            "  backend_gateway: 7401\n"
        ),
    )

    endpoint = load_endpoint(config)

    assert endpoint.host == "127.0.0.1"
    assert endpoint.port == 7410
    assert endpoint.path == "/native/mcp"
    assert load_backend_port(config) == 7401


@pytest.mark.parametrize("value", ["missing", "false", "0", "70000"])
def test_rejects_invalid_backend_port(tmp_path, value):
    config = write_config(
        tmp_path / "config.yaml",
        (
            "local_mcp_server: http://127.0.0.1:7410/mcp\n"
            "ports:\n"
            f"  backend_gateway: {value}\n"
        ),
    )
    with pytest.raises(ValueError):
        load_backend_port(config)
