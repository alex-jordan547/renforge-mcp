from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import ipaddress
import json
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..project import RenpyProject
from ..sdk import RenpySdk
from .constants import (
    AUTH_FRAME_MAX_BYTES,
    COMMAND_FRAME_MAX_BYTES,
    COMMIT_STATES,
    MAX_INTENTS,
    MAX_PATH_BYTES,
    MAX_STRING_BYTES,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    RENFORGE_DIRNAME,
    TRANSACTION_DIRNAME,
)
from .exceptions import EditorError
from .paths import EditorPathError, atomic_write_file, fsync_directory, resolve_game_path, sha256_bytes
from .runtime import RuntimeProbe
from .shadow import ShadowLintResult, build_shadow_project, run_shadow_lint
from .source import (
    DEFAULT_ALIGN_PARENT_SIZE,
    EditorSourceError,
    align_geometry_matches_parent,
    analyze_button_statement,
    analyze_editable_statement,
    analyze_textbutton_block_statement,
    apply_button_patch,
    apply_editable_statement_patch,
    apply_textbutton_patch,
    is_slider_style_bar_line,
    is_textbutton_block_header,
    peek_statement_kind,
)


def _now_deadline(seconds: float) -> float:
    return time.monotonic() + max(0.01, seconds)




@dataclass(frozen=True)
class EditorEndpoint:
    host: str
    port: int
    token: str
    protocol_version: int


@dataclass
class _AnalysisRecord:
    analysis_id: str
    session_id: str
    runtime_key: dict[str, Any]
    source_key: dict[str, Any] | None
    original_position: list[int]
    runtime_position: list[int]
    generation: int
    lock_reason: dict[str, Any] | None
    independent_frame_id: str


@dataclass
class _TransactionRecord:
    transaction_id: str
    source_relative_path: str
    source_absolute_path: Path
    original_bytes: bytes
    staged_bytes: bytes
    original_sha256: str
    staged_sha256: str
    generation: int
    expected_targets: list[dict[str, Any]]
    state: str = "staged"
    diagnostics: dict[str, Any] = field(default_factory=dict)
    uncertain_paths: list[str] = field(default_factory=list)
    timer: threading.Timer | None = None


class EditorCoordinator:
    def __init__(
        self,
        project: RenpyProject,
        sdk: RenpySdk,
        *,
        token: str | None = None,
        attestation_timeout: float = 30.0,
    ):
        self._project = project
        self._sdk = sdk
        self._token = token or uuid.uuid4().hex
        self._attestation_timeout = float(attestation_timeout)
        self._runtime_probe: RuntimeProbe | None = None

        self._session_id = uuid.uuid4().hex
        self._server_nonce = uuid.uuid4().hex
        self._script_generation: int = -1
        self._client_nonce: str | None = None

        self._request_cache: dict[str, tuple[bytes, dict[str, Any]]] = {}
        self._analyses: dict[str, _AnalysisRecord] = {}
        self._transactions: dict[str, _TransactionRecord] = {}
        self._recovered: list[str] = []

        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._connection_threads: set[threading.Thread] = set()
        self._busy_command_threads: set[threading.Thread] = set()
        self._connections: set[socket.socket] = set()
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._request_lock = threading.Lock()

        self._transaction_root = self._project.root / RENFORGE_DIRNAME / TRANSACTION_DIRNAME
        self._transaction_root.mkdir(parents=True, exist_ok=True)
        fsync_directory(self._transaction_root.parent)
        fsync_directory(self._transaction_root)
        self._recover_transactions()

    def start(self) -> EditorEndpoint:
        with self._lock:
            if self._listener is not None:
                host, port = self._listener.getsockname()
                return EditorEndpoint(str(host), int(port), self._token, PROTOCOL_VERSION)

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(16)
            listener.settimeout(0.2)
            self._listener = listener
            self._stop_event.clear()
            self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._accept_thread.start()
            host, port = listener.getsockname()
            return EditorEndpoint(str(host), int(port), self._token, PROTOCOL_VERSION)

    def attach_runtime_probe(self, probe: RuntimeProbe) -> None:
        with self._lock:
            self._runtime_probe = probe

    def close(self, timeout: float = 10.0) -> dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            listener = self._listener
            self._listener = None
            accept_thread = self._accept_thread
            self._accept_thread = None
            connections = list(self._connections)
            connection_threads = list(self._connection_threads)
            busy_threads = list(self._busy_command_threads)
            self._connections.clear()
            self._connection_threads.clear()
            self._request_cache.clear()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        # Join the deduplicated union of idle readers and command executors; the
        # busy set is persisted (never cleared on failure), so a retry also waits
        # for a command the previous close() already saw.
        for thread in set(connection_threads) | set(busy_threads):
            thread.join(timeout=max(0.1, timeout))
        if accept_thread is not None:
            accept_thread.join(timeout=max(0.1, timeout))

        # Only a handler *executing a command* can reach atomic_write_file and
        # publish source after the caller has torn the session down. A thread
        # parked on _read_frame, or the accept thread, writes nothing and exits
        # once the stop event or the closed socket wakes it, so it must NOT fail
        # the shutdown (that broke the protocol close test on slower sockets).
        # The _stop_event recheck in _handle_connection closes the race: a handler
        # only enters _busy_command_threads while stop is clear, so after the
        # stop set any command-in-flight is already tracked before we snapshot it.
        with self._lock:
            active = [thread for thread in self._busy_command_threads if thread.is_alive()]
        if active:
            # Retention needs no bookkeeping here: _busy_command_threads is never
            # cleared on the failure path, so the live executors are already in it
            # for the next close() to join.
            raise EditorError(
                "SHUTDOWN_INCOMPLETE",
                "editor command handlers outlived close(); the project lock is held",
                {"active_commands": len(active)},
            )

        with self._lock:
            for record in self._transactions.values():
                if record.timer is not None:
                    record.timer.cancel()
                    record.timer = None
            for record in self._transactions.values():
                if record.state == "published":
                    self._conditional_rollback(record)
            states = {txid: record.state for txid, record in self._transactions.items()}
            return {"session_id": self._session_id, "transactions": states, "recovered": list(self._recovered)}

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop_event.is_set():
            try:
                conn, addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_connection, args=(conn, addr), daemon=True)
            with self._lock:
                self._connection_threads.add(thread)
            thread.start()

    def _handle_connection(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        with self._lock:
            self._connections.add(conn)
        try:
            if not ipaddress.ip_address(addr[0]).is_loopback:
                self._send_json(
                    conn,
                    self._error_reply(
                        request_id=None,
                        code="BAD_PEER",
                        message="connection must come from loopback peer",
                    ),
                )
                return

            file_obj = conn.makefile("rb")
            authenticated = False
            connection_id = ""
            while not self._stop_event.is_set():
                frame_limit = AUTH_FRAME_MAX_BYTES if not authenticated else COMMAND_FRAME_MAX_BYTES
                payload_bytes, frame_error = self._read_frame(file_obj, frame_limit)
                if frame_error is not None:
                    if frame_error != "EOF":
                        self._send_json(
                            conn,
                            self._error_reply(request_id=None, code=frame_error, message=frame_error.lower()),
                        )
                    return
                if payload_bytes is None:
                    return

                try:
                    frame = json.loads(payload_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(
                        conn,
                        self._error_reply(request_id=None, code="MALFORMED_FRAME", message="frame is not valid JSON"),
                    )
                    return
                if not isinstance(frame, dict):
                    self._send_json(
                        conn,
                        self._error_reply(
                            request_id=None,
                            code="MALFORMED_FRAME",
                            message="frame must be a JSON object",
                        ),
                    )
                    return

                if not authenticated:
                    response, ok = self._handle_auth_frame(frame)
                    self._send_json(conn, response)
                    if not ok:
                        return
                    authenticated = True
                    connection_id = str(response["connection_id"])
                    continue

                # Register as command-in-flight only while stop is clear, under
                # the lock close() inspects. If stop is already set the thread
                # returns without entering the set, so close() either waits for a
                # command it has seen, or sees none and never tears down under one.
                with self._lock:
                    if self._stop_event.is_set():
                        return
                    self._busy_command_threads.add(threading.current_thread())
                try:
                    response, should_close = self._handle_command_frame(
                        frame, expected_connection_id=connection_id
                    )
                finally:
                    with self._lock:
                        self._busy_command_threads.discard(threading.current_thread())
                # _send_json may block on a slow socket but can never write
                # source, so it runs outside the command-in-flight guard.
                self._send_json(conn, response)
                if should_close:
                    return
        finally:
            with self._lock:
                self._connections.discard(conn)
                self._connection_threads.discard(threading.current_thread())
            try:
                conn.close()
            except OSError:
                pass

    def _read_frame(self, file_obj: Any, limit: int) -> tuple[bytes | None, str | None]:
        data = file_obj.readline(limit + 1)
        if not data:
            return None, "EOF"
        if len(data) > limit:
            return None, "FRAME_TOO_LARGE"
        if not data.endswith(b"\n"):
            return None, "TRUNCATED_FRAME"
        return data[:-1], None

    def _send_json(self, conn: socket.socket, payload: dict[str, Any]) -> None:
        try:
            conn.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Client may have timed out or closed while a long command (e.g. shadow
            # lint) was still running. Teardown is handled by the connection finally.
            return

    def _error_reply(self, *, request_id: str | None, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        error_payload: dict[str, Any] = {"code": code, "message": message}
        if details:
            error_payload["details"] = details
        return {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": False,
            "error": error_payload,
        }

    def _ok_reply(self, *, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": True,
            "result": result,
        }

    def _handle_auth_frame(self, frame: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            required_keys = {"protocol", "version", "token", "client_nonce"}
            if not required_keys.issubset(frame):
                raise EditorError("AUTH_REQUIRED", "first frame must authenticate before commands")
            if set(frame) != required_keys:
                raise EditorError("AUTH_FRAME_SCHEMA_INVALID", "auth frame must contain exactly the required fields")
            protocol = self._require_string(frame, "protocol")
            version = frame.get("version")
            token = self._require_string(frame, "token")
            client_nonce = self._require_string(frame, "client_nonce")
            if protocol != PROTOCOL_NAME:
                raise EditorError("PROTOCOL_MISMATCH", f"expected protocol {PROTOCOL_NAME}")
            if version != PROTOCOL_VERSION:
                raise EditorError("PROTOCOL_VERSION_MISMATCH", "unsupported protocol version")
            if token != self._token:
                raise EditorError("AUTH_TOKEN_INVALID", "invalid editor token")

            with self._lock:
                if self._client_nonce is None:
                    self._client_nonce = client_nonce
                elif self._client_nonce != client_nonce:
                    raise EditorError("CLIENT_NONCE_MISMATCH", "client nonce mismatch on reconnect")

            connection_id = uuid.uuid4().hex
            response = {
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "ok": True,
                "connection_id": connection_id,
                "session_id": self._session_id,
                "server_nonce": self._server_nonce,
            }
            return response, True
        except EditorError as exc:
            return self._error_reply(request_id=None, code=exc.code, message=exc.message, details=exc.details), False

    def _handle_command_frame(self, frame: dict[str, Any], *, expected_connection_id: str) -> tuple[dict[str, Any], bool]:
        required_keys = {"protocol", "version", "connection_id", "request_id", "command", "payload"}
        try:
            protocol = self._require_string(frame, "protocol")
            version = frame.get("version")
            connection_id = self._require_string(frame, "connection_id")
            request_id = self._require_string(frame, "request_id")
            command = self._require_string(frame, "command")
            payload = frame.get("payload")
            if set(frame) != required_keys:
                raise EditorError("COMMAND_FRAME_SCHEMA_INVALID", "command frame must contain exactly the required fields")
            if protocol != PROTOCOL_NAME or version != PROTOCOL_VERSION:
                raise EditorError("PROTOCOL_MISMATCH", "protocol mismatch")
            if connection_id != expected_connection_id:
                raise EditorError("CONNECTION_ID_MISMATCH", "connection_id does not match authenticated session")
            if not isinstance(payload, dict):
                raise EditorError("PAYLOAD_INVALID", "payload must be a JSON object")

            canonical_frame = {
                "protocol": protocol,
                "version": version,
                "request_id": request_id,
                "command": command,
                "payload": payload,
            }
            canonical = json.dumps(
                canonical_frame,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            with self._request_lock:
                cached = self._request_cache.get(request_id)
                if cached is not None:
                    previous_bytes, previous_reply = cached
                    if previous_bytes == canonical:
                        return deepcopy(previous_reply), False
                    return (
                        self._error_reply(
                            request_id=request_id,
                            code="DUPLICATE_REQUEST_ID",
                            message="request_id was already used with different payload",
                        ),
                        True,
                    )

                try:
                    result = self._dispatch_command(command, payload)
                    reply = self._ok_reply(request_id=request_id, result=result)
                except EditorError as exc:
                    reply = self._error_reply(
                        request_id=request_id,
                        code=exc.code,
                        message=exc.message,
                        details=exc.details,
                    )
                self._request_cache[request_id] = (canonical, deepcopy(reply))
            return deepcopy(reply), False
        except EditorError as exc:
            request_id = frame.get("request_id") if isinstance(frame.get("request_id"), str) else None
            return (
                self._error_reply(
                    request_id=request_id,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                ),
                False,
            )

    def _require_string(self, payload: dict[str, Any], key: str, *, max_bytes: int = MAX_STRING_BYTES) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise EditorError("FIELD_TYPE_INVALID", f"field '{key}' must be a string")
        if len(value.encode("utf-8")) > max_bytes:
            raise EditorError("FIELD_TOO_LONG", f"field '{key}' exceeds size limit")
        return value

    def _dispatch_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "analyze_target":
            return self._command_analyze_target(payload)
        if command == "commit":
            return self._command_commit(payload)
        if command == "commit_status":
            return self._command_commit_status(payload)
        if command == "reload_handshake":
            return self._command_reload_handshake(payload)
        raise EditorError("UNKNOWN_COMMAND", f"unsupported command: {command}")

    def _command_analyze_target(self, payload: dict[str, Any]) -> dict[str, Any]:
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            raise EditorError("OBSERVATION_INVALID", "analyze_target requires an observation object")
        runtime_key = observation.get("runtime_key")
        if not isinstance(runtime_key, dict):
            raise EditorError("RUNTIME_KEY_INVALID", "observation.runtime_key must be an object")
        probe = self._runtime_probe
        if probe is None:
            raise EditorError("RUNTIME_PROBE_UNAVAILABLE", "runtime probe is not attached")

        independent = probe.observe(runtime_key, deadline=_now_deadline(5.0))
        if not isinstance(independent, dict):
            raise EditorError("INDEPENDENT_OBSERVATION_INVALID", "runtime probe returned invalid observation")

        generation = self._coerce_generation(independent.get("script_generation"))
        if generation is None:
            raise EditorError("SCRIPT_GENERATION_INVALID", "independent observation is missing script_generation")
        with self._lock:
            if self._script_generation == -1:
                self._script_generation = generation
            if self._script_generation != generation:
                raise EditorError("SCRIPT_GENERATION_MISMATCH", "observation generation does not match coordinator")

        lock_reason = self._runtime_lock_reason(runtime_key)
        source_key: dict[str, Any] | None = None
        runtime_position = self._extract_position(independent)
        original_position = list(runtime_position)

        try:
            source_location = runtime_key.get("source_location")
            if not (
                isinstance(source_location, list)
                and len(source_location) == 2
                and isinstance(source_location[0], str)
                and isinstance(source_location[1], int)
            ):
                raise EditorError("SOURCE_LOCATION_INVALID", "runtime_key.source_location must be [path, line]")

            def _strip_game_prefix(path: str) -> str:
                return path[5:] if path.startswith("game/") else path

            relative_path = _strip_game_prefix(source_location[0])
            if len(relative_path.encode("utf-8")) > MAX_PATH_BYTES:
                raise EditorError("PATH_TOO_LONG", "source path exceeds 1 KiB")
            source_line = int(source_location[1])
            source_path = resolve_game_path(self._project.root, relative_path)
            source_bytes = source_path.read_bytes()
            source_sha = sha256_bytes(source_bytes)
            source_text = source_bytes.decode("utf-8")
            lines = source_text.splitlines(keepends=True)
            if source_line < 1 or source_line > len(lines):
                raise EditorError("SOURCE_LINE_INVALID", "source line is out of range")
            widget_id = self._require_runtime_widget_id(runtime_key)
            header_line = lines[source_line - 1]
            header_kind = peek_statement_kind(header_line)
            if header_kind == "button":
                statement = analyze_button_statement(
                    source_text,
                    source_line=source_line,
                    expected_widget_id=widget_id,
                )
                statement_kind = "button"
            elif header_kind == "textbutton" and is_textbutton_block_header(header_line):
                statement = analyze_textbutton_block_statement(
                    source_text,
                    source_line=source_line,
                    expected_widget_id=widget_id,
                )
                statement_kind = "textbutton"
            else:
                statement_kind, statement = analyze_editable_statement(
                    header_line,
                    expected_widget_id=widget_id,
                )
            # Align is fractional in source; the editor delta formula works in
            # logical pixels, so original_position is the measured runtime rect.
            position_mode = getattr(statement, "position_mode", "xy")
            if position_mode == "align":
                original_position = list(runtime_position)
            else:
                original_position = [int(statement.xpos), int(statement.ypos)]
            ancestry = runtime_key.get("ancestry")
            normalized_ancestry = (
                [
                    {
                        **ancestor,
                        "source_location": [
                            _strip_game_prefix(ancestor["source_location"][0]),
                            ancestor["source_location"][1],
                        ],
                    }
                    if isinstance(ancestor, dict)
                    and isinstance(ancestor.get("source_location"), list)
                    and len(ancestor["source_location"]) == 2
                    and isinstance(ancestor["source_location"][0], str)
                    and isinstance(ancestor["source_location"][1], int)
                    else ancestor
                    for ancestor in ancestry
                ]
                if isinstance(ancestry, list)
                else ancestry
            )
            source_key = {
                "relative_path": relative_path,
                "line": source_line,
                "screen": runtime_key.get("screen"),
                "widget_id": widget_id,
                "invocation_path": runtime_key.get("invocation_path"),
                "instance_discriminator": runtime_key.get("instance_discriminator"),
                "ancestry": normalized_ancestry,
                "statement_kind": statement_kind,
                "baseline_sha256": source_sha,
                "position_mode": position_mode,
            }
            if position_mode == "align":
                # Authored fractions + independent geometry: Ren'Py align also
                # sets anchor, so TL moves over (parent − widget) extent.
                # Parent size is only unlocked when independent focus proves the
                # full-screen 1280×720 placement model (issue #39).
                source_key["align_authored"] = [
                    float(statement.xpos),
                    float(statement.ypos),
                ]
                source_key["align_runtime_baseline"] = [
                    int(runtime_position[0]),
                    int(runtime_position[1]),
                ]
                parent_size = tuple(
                    getattr(statement, "align_parent_size", DEFAULT_ALIGN_PARENT_SIZE)
                )
                source_key["align_parent_size"] = list(parent_size)
                # Widget size must come from the independent focus_list rect —
                # never fall back to a missing/stale input observation.
                ind_rect = independent.get("rect") if isinstance(independent, dict) else None
                if (
                    isinstance(ind_rect, list)
                    and len(ind_rect) >= 4
                    and type(ind_rect[2]) is int
                    and type(ind_rect[3]) is int
                    and int(ind_rect[2]) > 0
                    and int(ind_rect[3]) > 0
                ):
                    widget_size = (int(ind_rect[2]), int(ind_rect[3]))
                    source_key["align_widget_size"] = list(widget_size)
                    if lock_reason is None:
                        extent_w = int(parent_size[0]) - widget_size[0]
                        extent_h = int(parent_size[1]) - widget_size[1]
                        if extent_w == 0 and extent_h == 0:
                            lock_reason = self._lock_reason(
                                "ALIGN_EXTENT_ZERO",
                                "align placement extent is zero on both axes",
                            )
                        elif not align_geometry_matches_parent(
                            authored=(float(statement.xpos), float(statement.ypos)),
                            runtime_xy=(int(runtime_position[0]), int(runtime_position[1])),
                            widget_size=widget_size,
                            parent_size=parent_size,
                            tolerance=1,
                        ):
                            lock_reason = self._lock_reason(
                                "ALIGN_PARENT_UNPROVEN",
                                "independent geometry does not match proven full-screen align parent",
                            )
                else:
                    source_key["align_widget_size"] = [0, 0]
                    if lock_reason is None:
                        lock_reason = self._lock_reason(
                            "ALIGN_WIDGET_SIZE_UNPROVEN",
                            "independent observation must provide positive widget width and height",
                        )
            if lock_reason is None:
                if observation.get("measurement_method") != "focus_list":
                    lock_reason = self._lock_reason(
                        "MEASUREMENT_METHOD_INVALID",
                        "input observation measurement_method must be focus_list",
                    )
                elif independent.get("measurement_method") != "focus_list":
                    lock_reason = self._lock_reason(
                        "INDEPENDENT_MEASUREMENT_INVALID",
                        "independent measurement_method must be focus_list",
                    )
                elif not self._runtime_keys_equivalent_for_reobservation(runtime_key, independent.get("runtime_key")):
                    lock_reason = self._lock_reason(
                        "RUNTIME_KEY_MISMATCH",
                        "independent runtime key does not match input runtime key",
                    )
                elif independent.get("frame_id") == observation.get("frame_id"):
                    lock_reason = self._lock_reason(
                        "INDEPENDENT_FRAME_NOT_FRESH",
                        "independent observation did not provide a fresh frame",
                    )
                elif statement.widget_id != widget_id:
                    lock_reason = self._lock_reason("ID_MISMATCH", "source literal id does not match runtime widget id")
        except EditorPathError as exc:
            lock_reason = self._lock_reason(exc.code, str(exc))
        except EditorSourceError as exc:
            lock_reason = self._lock_reason(exc.code, str(exc))
        except EditorError as exc:
            lock_reason = self._lock_reason(exc.code, exc.message)
        except (OSError, UnicodeDecodeError) as exc:
            lock_reason = self._lock_reason("SOURCE_READ_FAILED", f"unable to read source: {exc}")

        analysis_id = uuid.uuid4().hex
        record = _AnalysisRecord(
            analysis_id=analysis_id,
            session_id=self._session_id,
            runtime_key=runtime_key,
            source_key=source_key,
            original_position=original_position,
            runtime_position=runtime_position,
            generation=generation,
            lock_reason=lock_reason,
            independent_frame_id=str(independent.get("frame_id", "")),
        )
        with self._lock:
            self._analyses[analysis_id] = record

        return {
            "analysis_id": analysis_id,
            "source_key": source_key,
            "original_position": original_position,
            "capabilities": {"move": lock_reason is None},
            "lock_reason": lock_reason,
        }

    def _command_commit(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_string(payload, "session_id")
        if session_id != self._session_id:
            raise EditorError("SESSION_ID_MISMATCH", "commit session_id does not match current editor session")
        intents = payload.get("intents")
        if not isinstance(intents, list) or not intents:
            raise EditorError("INTENTS_INVALID", "commit requires a non-empty intents list")
        if len(intents) > MAX_INTENTS:
            raise EditorError("INTENTS_LIMIT_EXCEEDED", "commit intents exceed limit of 256")

        probe = self._runtime_probe
        if probe is None:
            raise EditorError("RUNTIME_PROBE_UNAVAILABLE", "runtime probe is not attached")

        seen_analysis_ids: set[str] = set()
        selected_records: list[tuple[_AnalysisRecord, int, int, dict[str, Any]]] = []
        source_paths: set[str] = set()
        with self._lock:
            generation = self._script_generation
        if generation is None:
            raise EditorError("SCRIPT_GENERATION_UNKNOWN", "no analyzed generation is available")

        for intent in intents:
            if not isinstance(intent, dict):
                raise EditorError("INTENT_INVALID", "each intent must be an object")
            analysis_id = self._require_string(intent, "analysis_id")
            if analysis_id in seen_analysis_ids:
                raise EditorError("DUPLICATE_ANALYSIS_ID", f"duplicate analysis_id in commit payload: {analysis_id}")
            seen_analysis_ids.add(analysis_id)
            source_key = intent.get("source_key")
            if not isinstance(source_key, dict):
                raise EditorError("SOURCE_KEY_INVALID", "intent source_key must be an object")
            x = intent.get("x")
            y = intent.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                raise EditorError("INTENT_POSITION_INVALID", "intent x and y must be integers")
            with self._lock:
                record = self._analyses.get(analysis_id)
            if record is None:
                raise EditorError("ANALYSIS_NOT_FOUND", f"analysis_id not found: {analysis_id}")
            if record.session_id != self._session_id:
                raise EditorError("ANALYSIS_SESSION_MISMATCH", "analysis does not belong to this session")
            if record.lock_reason is not None:
                raise EditorError("ANALYSIS_LOCKED", "analysis is locked and cannot be committed")
            if record.generation != generation:
                raise EditorError("ANALYSIS_STALE_GENERATION", "analysis was created under a stale generation")
            if record.source_key is None:
                raise EditorError("ANALYSIS_SOURCE_KEY_MISSING", "analysis does not include a writable source key")
            if record.source_key != source_key:
                raise EditorError("SOURCE_KEY_MISMATCH", "intent source_key does not match analyzed source key")
            relative_path = self._require_source_relative_path(source_key)
            source_paths.add(relative_path)
            selected_records.append((record, x, y, source_key))

        if len(source_paths) != 1:
            raise EditorError("MULTI_FILE_UNSUPPORTED", "all intents must resolve to exactly one source file")

        relative_path = next(iter(source_paths))
        source_path = resolve_game_path(self._project.root, relative_path)
        current_bytes = source_path.read_bytes()
        current_sha = sha256_bytes(current_bytes)

        for record, _x, _y, source_key in selected_records:
            baseline = source_key.get("baseline_sha256")
            if not isinstance(baseline, str):
                raise EditorError("SOURCE_BASELINE_INVALID", "source_key baseline_sha256 is missing")
            if current_sha != baseline:
                raise EditorError("STALE_SOURCE", "source file has changed since analysis")
            independent = probe.observe(record.runtime_key, deadline=_now_deadline(5.0))
            if not isinstance(independent, dict):
                raise EditorError("INDEPENDENT_OBSERVATION_INVALID", "runtime probe returned invalid observation")
            if not self._runtime_keys_equivalent_for_reobservation(record.runtime_key, independent.get("runtime_key")):
                raise EditorError("RUNTIME_KEY_MISMATCH", "runtime reanalysis key mismatch")
            if independent.get("measurement_method") != "focus_list":
                raise EditorError("MEASUREMENT_METHOD_INVALID", "independent reanalysis must use focus_list")
            if self._coerce_generation(independent.get("script_generation")) != generation:
                raise EditorError("SCRIPT_GENERATION_MISMATCH", "runtime reanalysis returned stale generation")
            if independent.get("frame_id") == record.independent_frame_id:
                raise EditorError("INDEPENDENT_FRAME_NOT_FRESH", "runtime reanalysis did not produce a fresh frame")

        staged_bytes = self._apply_same_file_intents(current_bytes, selected_records)
        transaction_id = uuid.uuid4().hex
        transaction = _TransactionRecord(
            transaction_id=transaction_id,
            source_relative_path=relative_path,
            source_absolute_path=source_path,
            original_bytes=current_bytes,
            staged_bytes=staged_bytes,
            original_sha256=current_sha,
            staged_sha256=sha256_bytes(staged_bytes),
            generation=generation,
            expected_targets=[
                {
                    "analysis_id": record.analysis_id,
                    "source_key": source_key,
                    "runtime_key": deepcopy(record.runtime_key),
                    "position": [
                        int(record.runtime_position[0]) + int(x) - int(record.original_position[0]),
                        int(record.runtime_position[1]) + int(y) - int(record.original_position[1]),
                    ],
                }
                for record, x, y, source_key in selected_records
            ],
            state="staged",
        )
        with self._lock:
            self._transactions[transaction_id] = transaction
        self._persist_transaction(transaction)

        lint_result = self._validate_shadow(transaction)
        if not lint_result.ok:
            transaction.state = "failed"
            transaction.diagnostics = self._lint_diagnostics(lint_result)
            self._persist_transaction(transaction)
            raise EditorError(
                "VALIDATION_FAILED",
                "Ren'Py lint failed in validation shadow",
                details={"transaction_id": transaction_id},
            )

        transaction.diagnostics = self._lint_diagnostics(lint_result)
        if sha256_bytes(source_path.read_bytes()) != transaction.original_sha256:
            transaction.state = "failed"
            self._persist_transaction(transaction)
            raise EditorError("STALE_SOURCE", "source file changed before atomic publication")

        atomic_write_file(source_path, transaction.staged_bytes)
        transaction.state = "published"
        self._persist_transaction(transaction)
        self._schedule_attestation_timeout(transaction)

        return {"transaction_id": transaction_id, "state": "published", "reload_required": True}

    def _command_commit_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        transaction_id = self._require_string(payload, "transaction_id")
        with self._lock:
            record = self._transactions.get(transaction_id)
        if record is None:
            raise EditorError("TRANSACTION_NOT_FOUND", f"transaction not found: {transaction_id}")
        return {
            "transaction_id": transaction_id,
            "state": record.state,
            "diagnostics": dict(record.diagnostics),
            "uncertain_paths": list(record.uncertain_paths),
        }

    def _command_reload_handshake(self, payload: dict[str, Any]) -> dict[str, Any]:
        transaction_id = self._require_string(payload, "transaction_id")
        script_generation = payload.get("script_generation")
        if not isinstance(script_generation, int):
            raise EditorError("SCRIPT_GENERATION_INVALID", "reload_handshake requires integer script_generation")
        with self._lock:
            record = self._transactions.get(transaction_id)
        if record is None:
            raise EditorError("TRANSACTION_NOT_FOUND", f"transaction not found: {transaction_id}")
        if record.state != "published":
            return {"transaction_id": transaction_id, "state": record.state}
        if script_generation != record.generation + 1:
            raise EditorError("GENERATION_MISMATCH", "reload_handshake generation must be previous + 1")

        probe = self._runtime_probe
        if probe is None:
            raise EditorError("RUNTIME_PROBE_UNAVAILABLE", "runtime probe is not attached")

        result = probe.attest(
            transaction_id=transaction_id,
            script_generation=script_generation,
            deadline=_now_deadline(self._attestation_timeout),
            expected_targets=list(record.expected_targets),
        )
        if not isinstance(result, dict):
            self._conditional_rollback(record)
            raise EditorError("ATTESTATION_FAILED", "runtime probe returned invalid attestation payload")
        if result.get("ok") is not True or result.get("state") != "all_targets_attested":
            self._conditional_rollback(record)
            raise EditorError("ATTESTATION_FAILED", "runtime attestation did not reach all_targets_attested")

        with self._lock:
            if record.state != "published":
                return {"transaction_id": transaction_id, "state": record.state}
            if record.timer is not None:
                record.timer.cancel()
                record.timer = None
            record.state = "committed"
            self._script_generation = script_generation
        self._persist_transaction(record)
        return {"transaction_id": transaction_id, "state": "committed"}

    def _require_source_relative_path(self, source_key: dict[str, Any]) -> str:
        relative_path = source_key.get("relative_path")
        if not isinstance(relative_path, str):
            raise EditorError("SOURCE_PATH_INVALID", "source_key.relative_path must be a string")
        if len(relative_path.encode("utf-8")) > MAX_PATH_BYTES:
            raise EditorError("SOURCE_PATH_INVALID", "source_key.relative_path exceeds size limit")
        return relative_path

    def _require_runtime_widget_id(self, runtime_key: dict[str, Any]) -> str:
        widget_id = runtime_key.get("widget_id")
        if not isinstance(widget_id, str) or widget_id == "":
            raise EditorError("WIDGET_ID_INVALID", "runtime_key.widget_id must be a non-empty string")
        if len(widget_id.encode("utf-8")) > MAX_STRING_BYTES:
            raise EditorError("WIDGET_ID_INVALID", "runtime_key.widget_id exceeds size limit")
        return widget_id

    def _runtime_keys_equivalent_for_reobservation(self, runtime_key_a: Any, runtime_key_b: Any) -> bool:
        if not (isinstance(runtime_key_a, dict) and isinstance(runtime_key_b, dict)):
            return runtime_key_a == runtime_key_b

        def _single_static_instance(discriminator: Any) -> bool:
            return (
                isinstance(discriminator, dict)
                and discriminator.get("kind") == "static"
                and discriminator.get("instance_count") == 1
            )

        allow_ordinal_drift = _single_static_instance(runtime_key_a.get("instance_discriminator")) and _single_static_instance(
            runtime_key_b.get("instance_discriminator")
        )

        key_a = deepcopy(runtime_key_a)
        key_b = deepcopy(runtime_key_b)
        if allow_ordinal_drift:
            discriminator_a = key_a.get("instance_discriminator")
            discriminator_b = key_b.get("instance_discriminator")
            if isinstance(discriminator_a, dict):
                discriminator_a.pop("ordinal", None)
            if isinstance(discriminator_b, dict):
                discriminator_b.pop("ordinal", None)
        return key_a == key_b

    def _runtime_lock_reason(self, runtime_key: dict[str, Any]) -> dict[str, Any] | None:
        required_string_keys = ("screen", "invocation_path", "widget_id")
        for key in required_string_keys:
            value = runtime_key.get(key)
            if not isinstance(value, str) or value == "":
                return self._lock_reason("RUNTIME_KEY_INVALID", f"runtime_key.{key} must be a non-empty string")

        source_location = runtime_key.get("source_location")
        if not (
            isinstance(source_location, list)
            and len(source_location) == 2
            and isinstance(source_location[0], str)
            and isinstance(source_location[1], int)
        ):
            return self._lock_reason("SOURCE_LOCATION_INVALID", "runtime_key.source_location must be [path, line]")

        instance_discriminator = runtime_key.get("instance_discriminator")
        if not isinstance(instance_discriminator, dict):
            return self._lock_reason("INSTANCE_PROOF_UNPROVEN", "instance_discriminator is missing or invalid")
        if instance_discriminator.get("kind") == "loop" or bool(instance_discriminator.get("loop")):
            return self._lock_reason("LOOP_INSTANCE_UNSUPPORTED", "loop instances are read-only in V1")
        if instance_discriminator.get("kind") == "use" and bool(instance_discriminator.get("repeated")):
            return self._lock_reason("REPEATED_USE_UNSUPPORTED", "repeated use instances are read-only in V1")
        if bool(instance_discriminator.get("repeated_use")):
            return self._lock_reason("REPEATED_USE_UNSUPPORTED", "repeated use instances are read-only in V1")
        instance_count = instance_discriminator.get("instance_count")
        if isinstance(instance_count, int) and instance_count != 1:
            return self._lock_reason("MULTI_INSTANCE_UNSUPPORTED", "runtime descriptor resolves to multiple instances")

        ancestry = runtime_key.get("ancestry")
        if not isinstance(ancestry, list):
            return self._lock_reason("ANCESTRY_TYPE_UNPROVEN", "ancestry must be a typed list")

        allowed_types = {
            "ScreenDisplayable",
            "MultiBox",
            "Button",
            "Text",
            "Window",
            "Fixed",
            "VBox",
            "HBox",
            "Grid",
            "Transform",
            "Container",
            "Frame",
            "ImageButton",
            "Bar",
            "Null",
            "Viewport",
            "Crop",
        }
        for ancestor in ancestry:
            if not isinstance(ancestor, dict):
                return self._lock_reason("ANCESTRY_TYPE_UNPROVEN", "ancestry entries must be typed objects")
            ancestor_type = ancestor.get("type")
            if not isinstance(ancestor_type, str):
                return self._lock_reason("ANCESTRY_TYPE_UNPROVEN", "ancestor type is missing")
            if ancestor_type not in allowed_types:
                return self._lock_reason("ANCESTRY_TYPE_UNPROVEN", f"unproven ancestor type: {ancestor_type}")
            if ancestor_type in {"VBox", "HBox", "Grid"} or (
                ancestor_type == "MultiBox"
                and ancestor.get("layout") in {"horizontal", "vertical"}
            ):
                return self._lock_reason(
                    "CONTAINER_POSITION_UNSUPPORTED",
                    "direct movement inside layout containers is not editable",
                )
            if ancestor_type == "Viewport":
                return self._lock_reason("VIEWPORT_ANCESTRY_UNSUPPORTED", "viewport ancestry is not editable in V1")
            if ancestor_type == "Crop":
                return self._lock_reason("CROP_ANCESTRY_UNSUPPORTED", "Crop ancestry is not editable in V1")
            if bool(ancestor.get("editor_owned")):
                return self._lock_reason("EDITOR_OWNED_TARGET", "editor-owned displayables are never editable")
            screen_owner = ancestor.get("screen_owner")
            if isinstance(screen_owner, str) and screen_owner == "renforge.editor.v1":
                return self._lock_reason("EDITOR_OWNED_TARGET", "editor-owned displayables are never editable")
            crop_state = ancestor.get("crop_state")
            if crop_state == "crop":
                return self._lock_reason("CROP_ANCESTRY_UNSUPPORTED", "crop ancestry is not editable in V1")
            if crop_state == "transform_crop":
                return self._lock_reason("TRANSFORM_CROP_UNSUPPORTED", "transform crop ancestry is not editable in V1")
            if crop_state not in {"none"}:
                return self._lock_reason("ANCESTRY_CROP_UNPROVEN", f"unproven crop state: {crop_state}")
        return None

    def _lock_reason(self, code: str, message: str) -> dict[str, Any]:
        return {"code": code, "message": message}

    def _coerce_generation(self, value: Any) -> int | None:
        if type(value) is int:
            return value
        return None

    def _extract_position(self, observation: dict[str, Any]) -> list[int]:
        rect = observation.get("rect")
        if not isinstance(rect, list) or len(rect) < 2:
            raise EditorError("RECT_INVALID", "observation rect must contain at least x and y")
        x, y = rect[0], rect[1]
        if not isinstance(x, int) or not isinstance(y, int):
            raise EditorError("RECT_INVALID", "observation rect coordinates must be integers")
        return [x, y]

    def _apply_same_file_intents(
        self,
        source_bytes: bytes,
        selected_records: list[tuple[_AnalysisRecord, int, int, dict[str, Any]]],
    ) -> bytes:
        lines = source_bytes.decode("utf-8").splitlines(keepends=True)
        seen_targets: set[tuple[str, int, str]] = set()

        for _record, x, y, source_key in selected_records:
            line_no = source_key.get("line")
            widget_id = source_key.get("widget_id")
            if not isinstance(line_no, int) or not isinstance(widget_id, str):
                raise EditorError("SOURCE_KEY_INVALID", "source_key line/widget_id is invalid")
            if line_no < 1 or line_no > len(lines):
                raise EditorError("SOURCE_LINE_INVALID", "source_key line is out of range")
            target_key = (source_key.get("relative_path"), line_no, widget_id)
            if target_key in seen_targets:
                raise EditorError("DUPLICATE_SOURCE_TARGET", "multiple intents target the same source statement")
            seen_targets.add(target_key)
            source_text = "".join(lines)
            recorded_kind = source_key.get("statement_kind")
            actual_kind = peek_statement_kind(lines[line_no - 1])
            if not isinstance(actual_kind, str):
                raise EditorError("SOURCE_KEY_INVALID", "source line statement kind is invalid")
            if isinstance(recorded_kind, str):
                # Slider adapters are authored as bar + style "slider" (no SL keyword).
                if recorded_kind == "slider":
                    if actual_kind != "bar" or not is_slider_style_bar_line(lines[line_no - 1]):
                        raise EditorError(
                            "STATEMENT_KIND_MISMATCH",
                            "source_key statement_kind does not match source line",
                        )
                elif recorded_kind != actual_kind:
                    raise EditorError(
                        "STATEMENT_KIND_MISMATCH",
                        "source_key statement_kind does not match source line",
                    )
            if actual_kind == "button":
                statement = analyze_button_statement(
                    source_text,
                    source_line=line_no,
                    expected_widget_id=widget_id,
                )
                lines = apply_button_patch(
                    "".join(lines).encode("utf-8"),
                    statement,
                    x=x,
                    y=y,
                ).decode("utf-8").splitlines(keepends=True)
            elif actual_kind == "textbutton" and is_textbutton_block_header(lines[line_no - 1]):
                statement = analyze_textbutton_block_statement(
                    source_text,
                    source_line=line_no,
                    expected_widget_id=widget_id,
                )
                lines = apply_textbutton_patch(
                    "".join(lines).encode("utf-8"),
                    statement,
                    x=x,
                    y=y,
                ).decode("utf-8").splitlines(keepends=True)
            else:
                kind, statement = analyze_editable_statement(
                    lines[line_no - 1],
                    expected_widget_id=widget_id,
                )
                align_baseline = source_key.get("align_runtime_baseline")
                align_size = source_key.get("align_widget_size")
                if kind == "textbutton" and getattr(statement, "position_mode", "xy") == "align":
                    lines[line_no - 1] = apply_textbutton_patch(
                        lines[line_no - 1].encode("utf-8"),
                        statement,
                        x=x,
                        y=y,
                        align_runtime_baseline=(
                            tuple(align_baseline)
                            if isinstance(align_baseline, (list, tuple)) and len(align_baseline) == 2
                            else None
                        ),
                        align_widget_size=(
                            tuple(align_size)
                            if isinstance(align_size, (list, tuple)) and len(align_size) == 2
                            else None
                        ),
                    ).decode("utf-8")
                else:
                    lines[line_no - 1] = apply_editable_statement_patch(
                        lines[line_no - 1].encode("utf-8"),
                        kind,
                        statement,
                        x=x,
                        y=y,
                    ).decode("utf-8")

        return "".join(lines).encode("utf-8")

    def _validate_shadow(self, transaction: _TransactionRecord) -> ShadowLintResult:
        tx_dir = self._transaction_root / transaction.transaction_id
        shadow_root = tx_dir / "shadow"
        build_shadow_project(
            self._project,
            shadow_root=shadow_root,
            staged_replacements={transaction.source_relative_path: transaction.staged_bytes},
        )
        return run_shadow_lint(
            self._project,
            self._sdk,
            shadow_root=shadow_root,
            timeout=min(180.0, max(1.0, self._attestation_timeout * 3)),
        )

    def _lint_diagnostics(self, lint_result: ShadowLintResult) -> dict[str, Any]:
        return {
            "returncode": lint_result.returncode,
            "timed_out": lint_result.timed_out,
            "stdout": lint_result.stdout,
            "stderr": lint_result.stderr,
            "truncated": lint_result.truncated,
        }

    def _schedule_attestation_timeout(self, transaction: _TransactionRecord) -> None:
        timer = threading.Timer(self._attestation_timeout, self._on_attestation_timeout, args=(transaction.transaction_id,))
        timer.daemon = True
        transaction.timer = timer
        timer.start()

    def _on_attestation_timeout(self, transaction_id: str) -> None:
        with self._lock:
            record = self._transactions.get(transaction_id)
        if record is None:
            return
        if record.state != "published":
            return
        self._conditional_rollback(record)

    def _conditional_rollback(self, transaction: _TransactionRecord, *, allow_staged: bool = False) -> None:
        allowed_states = {"published", "staged"} if allow_staged else {"published"}
        with self._lock:
            if transaction.state not in allowed_states:
                return
            if transaction.timer is not None:
                transaction.timer.cancel()
                transaction.timer = None
        try:
            current_bytes = transaction.source_absolute_path.read_bytes()
        except OSError:
            with self._lock:
                if transaction.state != "published":
                    return
                transaction.state = "rollback_conflict"
                transaction.uncertain_paths = [transaction.source_relative_path]
                self._persist_transaction(transaction)
            return
        current_sha = sha256_bytes(current_bytes)
        with self._lock:
            if transaction.state not in allowed_states:
                return
            if current_sha == transaction.staged_sha256:
                try:
                    atomic_write_file(transaction.source_absolute_path, transaction.original_bytes)
                except EditorPathError:
                    transaction.state = "rollback_conflict"
                    transaction.uncertain_paths = [transaction.source_relative_path]
                    self._persist_transaction(transaction)
                    return
                transaction.state = "rolled_back"
                transaction.uncertain_paths = []
                self._persist_transaction(transaction)
                return
            if current_sha == transaction.original_sha256:
                transaction.state = "rolled_back"
                transaction.uncertain_paths = []
                self._persist_transaction(transaction)
                return
            transaction.state = "rollback_conflict"
            transaction.uncertain_paths = [transaction.source_relative_path]
            self._persist_transaction(transaction)

    def _persist_transaction(self, transaction: _TransactionRecord) -> None:
        tx_dir = self._transaction_root / transaction.transaction_id
        original_dir = tx_dir / "original"
        staged_dir = tx_dir / "staged"
        original_dir.mkdir(parents=True, exist_ok=True)
        staged_dir.mkdir(parents=True, exist_ok=True)

        original_path = original_dir / Path(transaction.source_relative_path)
        staged_path = staged_dir / Path(transaction.source_relative_path)
        original_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_file(original_path, transaction.original_bytes)
        atomic_write_file(staged_path, transaction.staged_bytes)

        manifest = {
            "schema_version": 1,
            "transaction_id": transaction.transaction_id,
            "session_id": self._session_id,
            "state": transaction.state,
            "source_relative_path": transaction.source_relative_path,
            "generation": transaction.generation,
            "original_sha256": transaction.original_sha256,
            "staged_sha256": transaction.staged_sha256,
            "expected_targets": list(transaction.expected_targets),
            "uncertain_paths": list(transaction.uncertain_paths),
            "diagnostics": dict(transaction.diagnostics),
        }
        manifest_path = tx_dir / "manifest.json"
        atomic_write_file(manifest_path, json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        fsync_directory(tx_dir)

    def _recover_transactions(self) -> None:
        if not self._transaction_root.exists():
            return
        for child in sorted(self._transaction_root.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            transaction_id = manifest.get("transaction_id")
            relative_path = manifest.get("source_relative_path")
            state = manifest.get("state")
            if not isinstance(transaction_id, str) or not isinstance(relative_path, str) or not isinstance(state, str):
                continue
            if state not in COMMIT_STATES:
                continue
            if transaction_id != child.name:
                continue
            try:
                source_path = resolve_game_path(self._project.root, relative_path)
                original_path = child / "original" / Path(relative_path)
                staged_path = child / "staged" / Path(relative_path)
                original_bytes = original_path.read_bytes()
                staged_bytes = staged_path.read_bytes()
            except (EditorPathError, OSError):
                continue
            actual_original_sha256 = sha256_bytes(original_bytes)
            actual_staged_sha256 = sha256_bytes(staged_bytes)
            manifest_original_sha256 = manifest.get("original_sha256")
            manifest_staged_sha256 = manifest.get("staged_sha256")
            manifest_intact = True
            if isinstance(manifest_original_sha256, str) and manifest_original_sha256 != actual_original_sha256:
                manifest_intact = False
            if isinstance(manifest_staged_sha256, str) and manifest_staged_sha256 != actual_staged_sha256:
                manifest_intact = False
            if not isinstance(manifest.get("generation"), int):
                generation = 0
            else:
                generation = int(manifest.get("generation", 0))
            expected_targets = manifest.get("expected_targets")
            if not isinstance(expected_targets, list):
                expected_targets = []
            diagnostics = manifest.get("diagnostics")
            if not isinstance(diagnostics, dict):
                diagnostics = {}
            uncertain_paths = manifest.get("uncertain_paths")
            if not isinstance(uncertain_paths, list):
                uncertain_paths = []

            record = _TransactionRecord(
                transaction_id=transaction_id,
                source_relative_path=relative_path,
                source_absolute_path=source_path,
                original_bytes=original_bytes,
                staged_bytes=staged_bytes,
                original_sha256=actual_original_sha256,
                staged_sha256=actual_staged_sha256,
                generation=generation,
                expected_targets=list(expected_targets),
                state=state,
                diagnostics=dict(diagnostics),
                uncertain_paths=list(uncertain_paths),
            )
            self._transactions[transaction_id] = record
            if state in {"staged", "published"} and manifest_intact:
                self._conditional_rollback(record, allow_staged=True)
                self._recovered.append(transaction_id)
            elif state in {"staged", "published"} and not manifest_intact:
                record.state = "rollback_conflict"
                record.uncertain_paths = [relative_path]
                self._recovered.append(transaction_id)
                self._persist_transaction(record)
