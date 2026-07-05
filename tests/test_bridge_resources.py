from pathlib import Path


def test_bridge_rpy_exposes_expected_symbols() -> None:
    path = Path(__file__).resolve().parents[1] / "src/renforge/bridge/bridge.rpy"
    content = path.read_text(encoding="utf-8")

    required = [
        "renforge_start_bridge",
        "renforge_drain_bridge",
        "periodic_callbacks",
        "label_callbacks",
        "screenshot_to_bytes",
        "getsockname",
        "bridge.json",
        "RENFORGE_BRIDGE_TOKEN",
        "RENFORGE_BRIDGE_PORT",
    ]
    for token in required:
        assert token in content, f"missing expected symbol: {token}"
