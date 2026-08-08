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
        ".renforge/control",
        "bridge.json",
        "RENFORGE_BRIDGE_TOKEN",
        "RENFORGE_BRIDGE_SESSION_ID",
        "RENFORGE_BRIDGE_PROJECT_ROOT",
        "RENFORGE_BRIDGE_PORT",
        "RENFORGE_BRIDGE_STARTUP_ERROR=",
        "BRIDGE_MANIFEST_PUBLICATION_FAILED",
        "BRIDGE_INFO_CONFLICT",
        "BRIDGE_MANIFEST_IDENTITY_MISMATCH",
        '"schema_version"',
        '"protocol_version"',
        '"state"',
        '"session_id"',
        '"project_root"',
        '"ready"',
        '"starting"',
        "0o600",
        "O_NOFOLLOW",
        "O_EXCL",
        "os.path.isabs",
        "os.path.realpath",
        "renforge directory must not be a symlink",
    ]
    for token in required:
        assert token in content, f"missing expected symbol: {token}"

    forbidden = [
        '".renforge", "bridge.json"',
        'join(bridge.basedir, ".renforge")',
        '"pid"',
        "makedirs(out_dir",
        "os.makedirs",
        "_os.makedirs",
    ]
    for token in forbidden:
        assert token not in content, f"legacy or unsafe symbol still present: {token!r}"

    # Ready publication must target the private control path only.
    assert 'join(project_root, ".renforge", "control", "bridge.json")' in content
    # Invalid identity must emit the mismatch marker rather than return silently.
    assert "_RENFORGE_BRIDGE_STARTUP_IDENTITY_MISMATCH" in content
    assert "identity_ok" in content

def test_editor_rpy_exposes_expected_environment_variables() -> None:
    path = Path(__file__).resolve().parents[1] / "src/renforge/bridge/editor.rpy"
    content = path.read_text(encoding="utf-8")

    required = [
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ]
    for token in required:
        assert token in content, f"missing expected editor env symbol: {token}"
