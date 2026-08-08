"""Bounded newline-delimited JSON frame I/O for local bridge protocols."""

from __future__ import annotations

import json
import socket
import time
from typing import Any

FRAME_TOO_LARGE = "FRAME_TOO_LARGE"
FRAME_TIMEOUT = "FRAME_TIMEOUT"
TRUNCATED_FRAME = "TRUNCATED_FRAME"
RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"

# Bridge defaults from the remediation plan.
BRIDGE_REQUEST_MAX_BYTES = 1 * 1024 * 1024
BRIDGE_RESPONSE_MAX_BYTES = 64 * 1024 * 1024
BRIDGE_READ_DEADLINE_SECONDS = 2.0
BRIDGE_WRITE_DEADLINE_SECONDS = 5.0


class FrameError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def remaining_timeout(deadline: float, *, minimum: float = 0.001) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise FrameError(FRAME_TIMEOUT, "absolute deadline elapsed")
    return max(minimum, left)


def recv_until_newline(sock: socket.socket, *, max_bytes: int, deadline: float) -> bytes:
    """Read one newline-terminated frame without buffering the rest of the stream.

    Preserves no post-newline bytes on the socket (one-request-per-connection
    protocols close after the reply). Rejects oversized frames before decode.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        timeout = remaining_timeout(deadline)
        sock.settimeout(timeout)
        try:
            piece = sock.recv(min(4096, max(1, max_bytes - total + 1)))
        except socket.timeout as exc:
            raise FrameError(FRAME_TIMEOUT, "timed out waiting for a frame") from exc
        except OSError as exc:
            raise FrameError(FRAME_TIMEOUT, f"socket read failed: {exc}") from exc
        if not piece:
            if total == 0:
                raise FrameError(TRUNCATED_FRAME, "peer closed before sending a frame")
            raise FrameError(TRUNCATED_FRAME, "peer closed before newline")
        chunks.append(piece)
        total += len(piece)
        if b"\n" in piece:
            data = b"".join(chunks)
            line, _sep, rest = data.partition(b"\n")
            if rest:
                # One-shot protocols should not send trailing bytes; treat as oversized/truncation risk.
                raise FrameError(TRUNCATED_FRAME, "frame contained bytes after newline")
            if len(line) > max_bytes:
                raise FrameError(FRAME_TOO_LARGE, f"frame exceeds {max_bytes} bytes")
            return line
        if total > max_bytes:
            raise FrameError(FRAME_TOO_LARGE, f"frame exceeds {max_bytes} bytes")


def send_all_deadline(sock: socket.socket, payload: bytes, *, deadline: float) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(payload):
        timeout = remaining_timeout(deadline)
        sock.settimeout(timeout)
        try:
            sent = sock.send(view[offset:])
        except socket.timeout as exc:
            raise FrameError(FRAME_TIMEOUT, "timed out while sending a frame") from exc
        except OSError as exc:
            raise FrameError(FRAME_TIMEOUT, f"socket write failed: {exc}") from exc
        if sent == 0:
            raise FrameError(TRUNCATED_FRAME, "socket closed while sending")
        offset += sent


def encode_json_line(payload: Any) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def decode_json_object(line: bytes) -> dict[str, Any]:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrameError(TRUNCATED_FRAME, "frame is not valid UTF-8") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FrameError(TRUNCATED_FRAME, "frame is not valid JSON") from exc
    if not isinstance(value, dict):
        raise FrameError(TRUNCATED_FRAME, "frame must be a JSON object")
    return value
