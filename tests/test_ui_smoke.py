from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
_SMOKE_PATH = ROOT / "scripts" / "smoke_ui.py"

spec = importlib.util.spec_from_file_location("rf_smoke_ui", _SMOKE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load smoke script from {_SMOKE_PATH}")

_smoke_ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_smoke_ui)


class _FakeProcess:
    def __init__(self, startup_line: str) -> None:
        self.stdout = io.StringIO(startup_line)
        self.terminate_count = 0
        self.kill_count = 0
        self.waited = False

    def poll(self) -> int | None:
        return 0 if (self.terminate_count or self.kill_count) else None

    def terminate(self) -> None:
        self.terminate_count += 1

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0

    def kill(self) -> None:
        self.kill_count += 1


def _raise(exc: Exception):
    def _inner(*_args: object, **_kwargs: object) -> None:
        raise exc

    return _inner


def _fake_popen_factory(captured: dict[str, object], process: _FakeProcess):
    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        return process

    return fake_popen


def test_json_contract_accepts_compact_ok_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _smoke_ui,
        "_request_text",
        lambda _url, timeout=5: '{"ok":true}',
    )

    _smoke_ui._assert_health("http://127.0.0.1:0", "abc")
    _smoke_ui._assert_project("http://127.0.0.1:0", "abc")


def test_dashboard_url_parser_does_not_depend_on_log_prefix() -> None:
    match = _smoke_ui.DASHBOARD_RE.search(
        "INFO UI ready at http://127.0.0.1:43123/dashboard?token=abc123"
    )

    assert match is not None
    assert match.group(1) == "http://127.0.0.1:43123/dashboard?token=abc123"
    assert match.group(2) == "abc123"


def test_json_contract_rejects_invalid_or_false_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _smoke_ui,
        "_request_text",
        lambda _url, timeout=5: "{this is not json}",
    )
    with pytest.raises(_smoke_ui.SmokerError):
        _smoke_ui._assert_health("http://127.0.0.1:0", "abc")

    monkeypatch.setattr(
        _smoke_ui,
        "_request_text",
        lambda _url, timeout=5: json.dumps({"ok": False}),
    )
    with pytest.raises(_smoke_ui.SmokerError):
        _smoke_ui._assert_project("http://127.0.0.1:0", "abc")


def test_isolation_uses_temp_cwd_for_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    process = _FakeProcess(
        "RenForge dashboard: http://127.0.0.1:0/dashboard?token=abc123\n"
    )

    monkeypatch.setattr(
        _smoke_ui.subprocess,
        "Popen",
        _fake_popen_factory(captured, process),
    )
    monkeypatch.setattr(_smoke_ui, "_request_text", lambda _url, timeout=5: "<html></html>")
    monkeypatch.setattr(
        _smoke_ui,
        "_collect_local_refs",
        lambda *_args, **_kwargs: {"/assets/index.js"},
    )
    monkeypatch.setattr(_smoke_ui, "_assert_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_smoke_ui, "_assert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_smoke_ui, "_assert_local_assets", lambda *_args, **_kwargs: None)

    result = _smoke_ui._run_smoke(host="127.0.0.1", port=0, timeout=1)
    assert result == 0

    assert "cwd" in captured
    assert isinstance(captured["cwd"], str)
    assert not str(captured["cwd"]).startswith(str(ROOT))
    assert process.terminate_count == 1
    assert process.waited


def test_port_default_is_ephemeral_and_override_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = _smoke_ui.parse_args([])
    assert defaults.port == 0

    def fake_pick_port() -> int:
        return 43123

    monkeypatch.setattr(_smoke_ui, "_pick_port", fake_pick_port)

    captured_default: dict[str, object] = {}
    process_default = _FakeProcess(
        "RenForge dashboard: http://127.0.0.1:43123/dashboard?token=abc123\n"
    )
    default_requests: list[str] = []

    def fake_request(url: str, timeout: int = 5) -> str:
        default_requests.append(url)
        return "<html></html>"

    monkeypatch.setattr(
        _smoke_ui.subprocess,
        "Popen",
        _fake_popen_factory(captured_default, process_default),
    )
    monkeypatch.setattr(_smoke_ui, "_request_text", fake_request)
    monkeypatch.setattr(
        _smoke_ui,
        "_collect_local_refs",
        lambda *_args, **_kwargs: {"/assets/index.js"},
    )
    monkeypatch.setattr(_smoke_ui, "_assert_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_smoke_ui, "_assert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_smoke_ui, "_assert_local_assets", lambda *_args, **_kwargs: None)

    result = _smoke_ui._run_smoke(host="127.0.0.1", port=defaults.port, timeout=1)
    assert result == 0

    command_default = list(captured_default["command"])  # type: ignore[assignment]
    assert command_default[command_default.index("--port") + 1] == "43123"
    assert default_requests[0].startswith("http://127.0.0.1:43123")

    captured_overridden: dict[str, object] = {}
    process_overridden = _FakeProcess(
        "RenForge dashboard: http://127.0.0.1:9001/dashboard?token=abc123\n"
    )
    monkeypatch.setattr(
        _smoke_ui.subprocess,
        "Popen",
        _fake_popen_factory(captured_overridden, process_overridden),
    )
    _smoke_ui._run_smoke(host="127.0.0.1", port=4242, timeout=1)
    command_overridden = list(captured_overridden["command"])  # type: ignore[assignment]
    assert command_overridden[command_overridden.index("--port") + 1] == "4242"


def test_collect_local_refs_recurses_js_css_and_ignores_api_ws_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {
        "/assets/main.js": "import '/assets/chunk.js'; const icon = '/brand/icon.svg';",
        "/assets/chunk.js": "const css = '/assets/chunk.css'; const dup = '/assets/chunk.js';",
        "/assets/chunk.css": "background:url('/brand/logo.svg');",
        "/brand/app.css": "background:url('/assets/main.js');",
        "/assets/main.css": "@import '/assets/chunk.css'; url('/brand/logo.svg');",
    }

    def fake_request(url: str, timeout: int = 5) -> str:
        route = url.split("http://127.0.0.1:0", 1)[1]
        return routes.get(route, "")

    monkeypatch.setattr(_smoke_ui, "_request_text", fake_request)

    body = """
    <html>
      <script src="/assets/main.js"></script>
      <script src="/assets/main.js"></script>
      <link href="/brand/app.css" rel="stylesheet">
      <link href="/assets/main.css" rel="stylesheet">
      <a href="/api/health?token=abc">api</a>
      <a href="/ws?token=abc">ws</a>
    </html>
    """

    refs = _smoke_ui._collect_local_refs("http://127.0.0.1:0", body)
    assert refs == {
        "/assets/main.js",
        "/assets/chunk.js",
        "/assets/chunk.css",
        "/assets/main.css",
        "/brand/app.css",
        "/brand/icon.svg",
        "/brand/logo.svg",
    }


def test_run_smoke_requires_local_asset_references(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    process = _FakeProcess(
        "RenForge dashboard: http://127.0.0.1:0/dashboard?token=abc123\n"
    )
    monkeypatch.setattr(
        _smoke_ui.subprocess,
        "Popen",
        _fake_popen_factory(captured, process),
    )
    monkeypatch.setattr(_smoke_ui, "_assert_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_smoke_ui, "_assert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        _smoke_ui,
        "_request_text",
        lambda _url, timeout=5: "<html></html>",
    )

    with pytest.raises(_smoke_ui.SmokerError, match="no local assets") as exc_info:
        _smoke_ui._run_smoke(host="127.0.0.1", port=0, timeout=1)

    message = str(exc_info.value)
    assert "Server output:" in message
    assert "RenForge dashboard:" in message
    assert "token=[REDACTED]" in message
    assert "abc123" not in message
    assert process.terminate_count == 1
    assert process.waited


def test_cleanup_terminates_subprocess_on_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_success: dict[str, object] = {}
    process_success = _FakeProcess(
        "RenForge dashboard: http://127.0.0.1:0/dashboard?token=abc123\n"
    )
    monkeypatch.setattr(
        _smoke_ui.subprocess,
        "Popen",
        _fake_popen_factory(captured_success, process_success),
    )
    monkeypatch.setattr(
        _smoke_ui,
        "_collect_local_refs",
        lambda *_args, **_kwargs: {"/assets/index.js"},
    )
    monkeypatch.setattr(_smoke_ui, "_assert_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_smoke_ui, "_assert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_smoke_ui, "_assert_local_assets", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        _smoke_ui,
        "_request_text",
        lambda _url, timeout=5: (
            "<html></html>" if "?token=" in _url else '{"ok":true}'
        ),
    )

    result = _smoke_ui._run_smoke(host="127.0.0.1", port=0, timeout=1)
    assert result == 0
    assert process_success.terminate_count == 1
    assert process_success.waited

    captured_fail: dict[str, object] = {}
    process_fail = _FakeProcess(
        "RenForge dashboard: http://127.0.0.1:0/dashboard?token=abc123\n"
    )
    monkeypatch.setattr(
        _smoke_ui.subprocess,
        "Popen",
        _fake_popen_factory(captured_fail, process_fail),
    )
    monkeypatch.setattr(
        _smoke_ui,
        "_assert_health",
        _raise(_smoke_ui.SmokerError("boom")),
    )

    with pytest.raises(_smoke_ui.SmokerError):
        _smoke_ui._run_smoke(host="127.0.0.1", port=0, timeout=1)

    assert process_fail.terminate_count == 1
    assert process_fail.waited
