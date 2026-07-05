from pathlib import Path


def test_bridge_rpy_resources_present():
    path = Path(__file__).resolve().parents[1] / "src/renforge/bridge/bridge.rpy"
    content = path.read_text(encoding="utf-8")

    required = [
        "getsockname",
        "renforge_bridge_port",
        "periodic_callback",
        "BRIDGE_TOKEN",
        "start_bridge_listener()",
        "RENFORGE_BRIDGE_TOKEN",
        "RENFORGE_BRIDGE_PORT",
    ]

    for token in required:
        assert token in content
