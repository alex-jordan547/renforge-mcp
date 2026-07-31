#!/usr/bin/env python3
"""Run a minimal end-to-end smoke test against the installed RenForge UI package."""

from __future__ import annotations

import argparse
import json
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from collections.abc import Iterable
from pathlib import Path
from queue import Queue
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT_PREFIXES = ("/assets/", "/brand/")


ATTRIBUTE_RE = re.compile(r"\b(?:href|src)\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
QUOTED_ROOT_RE = re.compile(r"(?<![A-Za-z0-9_])(['\"])(/[^'\"\s]+)\1")
DASHBOARD_RE = re.compile(
    r"(https?://[^\s'\"<>]*?[?&]token=([^&\s'\"<>]+)[^\s'\"<>]*)",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"([?&]token=)[^&\s]+", re.IGNORECASE)


class SmokerError(RuntimeError):
    pass


def _read_lines(stream, q: Queue[str], output: list[str]) -> None:
    for line in iter(stream.readline, ""):
        stripped = line.rstrip("\n")
        output.append(stripped)
        q.put(stripped)


def _redact_server_output(output: list[str]) -> str:
    return TOKEN_RE.sub(r"\1[REDACTED]", "\n".join(output))


def _pick_port() -> int:
    sock = socket.socket()
    with sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request_text(url: str, *, timeout: int = 5) -> str:
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as response:
        payload = response.read()
    return payload.decode("utf-8", errors="ignore")


def _collect_local_ref_paths(body: str) -> list[str]:
    refs = set[str]()

    for _, value in ATTRIBUTE_RE.findall(body):
        refs.add(value)

    for _, value in CSS_URL_RE.findall(body):
        refs.add(value)

    for _, value in QUOTED_ROOT_RE.findall(body):
        refs.add(value)

    out: list[str] = []
    for raw in refs:
        if not raw.startswith("/"):
            continue

        parsed = urlsplit(raw)
        if parsed.scheme or parsed.netloc or parsed.path.startswith("//"):
            continue

        if not parsed.path.startswith(ROOT_PREFIXES):
            continue

        out.append(parsed.path)

    return out


def _is_local_asset(ref: str) -> bool:
    return ref.startswith(ROOT_PREFIXES)


def _assert_json_response(payload_text: str, endpoint: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise SmokerError(f"{endpoint} did not return valid JSON") from exc

    if not isinstance(payload, dict):
        raise SmokerError(f"{endpoint} did not return an object payload")
    if payload.get("ok") is not True:
        raise SmokerError(f"{endpoint} did not return ok=true")
    return payload


def _collect_local_refs(base_url: str, body: str) -> set[str]:
    discovered: set[str] = set()
    frontier: list[str] = _collect_local_ref_paths(body)

    while frontier:
        path = frontier.pop()
        if path in discovered:
            continue
        discovered.add(path)

        if not path.endswith((".js", ".css")):
            continue

        try:
            nested = _request_text(f"{base_url}{path}")
        except Exception as exc:  # pragma: no cover - surfaced in caller
            raise SmokerError(f"failed to fetch {path}: {exc}") from exc

        for nested_ref in _collect_local_ref_paths(nested):
            if nested_ref not in discovered:
                frontier.append(nested_ref)

    return discovered


def _assert_health(base_url: str, token: str) -> None:
    payload = _request_text(f"{base_url}/api/health?token={token}")
    _assert_json_response(payload, "/api/health")


def _assert_project(base_url: str, token: str) -> None:
    payload = _request_text(f"{base_url}/api/project?token={token}")
    _assert_json_response(payload, "/api/project")


def _assert_local_assets(base_url: str, refs: Iterable[str]) -> None:
    for ref in refs:
        if not _is_local_asset(ref):
            continue
        try:
            _request_text(f"{base_url}{ref}")
        except urllib.error.HTTPError as exc:
            raise SmokerError(f"asset {ref} returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise SmokerError(f"asset {ref} request failed: {exc}") from exc


def _create_minimal_project(root: Path) -> None:
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text(
        "label start:\n    return\n",
        encoding="utf-8",
    )


def _run_smoke(host: str = "127.0.0.1", port: int = 0, timeout: int = 30) -> int:
    if port == 0:
        port = _pick_port()

    with tempfile.TemporaryDirectory() as workdir:
        project_dir = Path(workdir) / "project"
        _create_minimal_project(project_dir)
        base_url = f"http://{host}:{port}"

        command = [
            sys.executable,
            "-m",
            "renforge",
            "ui",
            "--project",
            str(project_dir),
            "--host",
            host,
            "--port",
            str(port),
            "--no-open",
        ]

        # Arguments are passed as a list with shell execution disabled, so user-provided
        # host/port values cannot be interpreted as shell syntax.
        # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
        proc = subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=workdir,
        )

        if proc.stdout is None:
            raise SmokerError("could not capture server output")

        q: Queue[str] = queue.Queue()
        server_output: list[str] = []
        reader = threading.Thread(
            target=_read_lines,
            args=(proc.stdout, q, server_output),
            daemon=True,
        )
        reader.start()

        start = time.perf_counter()
        token = None

        try:
            while time.perf_counter() - start < timeout:
                try:
                    line = q.get(timeout=0.2)
                    match = DASHBOARD_RE.search(line)
                    if match:
                        token = match.group(2)
                        dashboard_url = match.group(1)
                        base = urlsplit(dashboard_url)
                        if base.scheme and base.netloc:
                            base_url = f"{base.scheme}://{base.netloc}"
                        break
                except queue.Empty:
                    if proc.poll() is not None:
                        raise SmokerError("server exited during startup")

            if token is None:
                raise SmokerError("did not capture dashboard token from startup output")

            deadline = time.perf_counter() + timeout
            while True:
                try:
                    payload = _request_text(f"{base_url}/?token={token}")
                    break
                except Exception:
                    if time.perf_counter() >= deadline:
                        raise SmokerError("dashboard never became reachable")
                    time.sleep(0.2)

            _assert_health(base_url, token)
            _assert_project(base_url, token)

            refs = _collect_local_refs(base_url, payload)
            if not refs:
                raise SmokerError("dashboard exposed no local assets")
            _assert_local_assets(base_url, refs)
            print("smoke test passed")
            return 0

        except Exception as exc:
            output = _redact_server_output(server_output)
            details = f"\n\nServer output:\n{output}" if output else ""
            raise SmokerError(f"{exc}{details}") from exc
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RenForge UI smoke test")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=30)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    try:
        return _run_smoke(host=args.host, port=args.port, timeout=args.timeout)
    except SmokerError as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
