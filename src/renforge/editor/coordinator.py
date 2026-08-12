from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import ipaddress
import json
import os
import shutil
import socket
import stat
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
from .paths import (
    EditorPathError,
    atomic_write_file,
    conditional_replace_file,
    fsync_directory,
    hash_file_nofollow,
    resolve_game_path,
    sha256_bytes,
)
from .runtime import RuntimeProbe
from .shadow import ShadowLintResult, build_shadow_project, run_shadow_lint
from .source import (
    BAR_SIZE_MODE_XSIZE_YSIZE,
    DEFAULT_ALIGN_PARENT_SIZE,
    SAY_WHAT_STYLE_POSITION_MODE,
    BarStatement,
    EditorSourceError,
    SayDialogueStyleBinding,
    SayWhatStylePositionStatement,
    TextColorStyleStatement,
    TextPositionStatement,
    align_geometry_matches_parent,
    _literal_button_id,
    analyze_button_statement,
    analyze_editable_statement,
    analyze_raise_adjacent_sibling,
    analyze_say_dialogue_style_binding,
    analyze_say_what_style_position,
    analyze_text_color_style,
    analyze_text_position_statement,
    analyze_textbutton_block_statement,
    apply_button_patch,
    apply_button_sibling_swap,
    apply_editable_statement_patch,
    apply_say_what_style_position_patch,
    apply_text_color_patch,
    apply_text_position_patch,
    apply_textbutton_patch,
    is_slider_style_bar_line,
    is_textbutton_block_header,
    peek_statement_kind,
    textbutton_patch_kwargs,
    uses_runtime_delta_position,
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
    original_size: list[int] | None = None
    runtime_size: list[int] | None = None


@dataclass(frozen=True)
class _SelectedIntent:
    record: _AnalysisRecord
    source_key: dict[str, Any]
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    color: str | None = None
    swap_sibling: tuple[str, int] | None = None


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
            # CRITICAL: Don't rollback "publishing" OR "published" on coordinator shutdown
            # For gui.rpy commits, Ren'Py does full restart: bridge stops OLD coordinator,
            # new coordinator starts. Between CAS write and state="published" persist, a
            # shutdown can happen leaving state="publishing" but disk==staged.
            # Old close() would rollback, causing NEW coordinator to see disk==original.
            # Let NEW coordinator's recovery SHA-check logic handle all in-flight transactions.
            # Only rollback "staged" (no CAS attempted) to ensure clean shutdown.
            for record in self._transactions.values():
                if record.state == "staged":
                    self._conditional_rollback(record, allow_staged=True)
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
            import hmac

            if not hmac.compare_digest(
                token.encode("utf-8"),
                str(self._token).encode("utf-8"),
            ):
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
        if command == "undo_commit":
            return self._command_undo_commit(payload)
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

        runtime_lock_reason = self._runtime_lock_reason(runtime_key)
        lock_reason = runtime_lock_reason
        source_key: dict[str, Any] | None = None
        runtime_position = self._extract_position(independent)
        original_position = list(runtime_position)
        runtime_size: list[int] | None = self._extract_size(independent)
        original_size: list[int] | None = None

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
            widget_id = self._runtime_widget_alias(runtime_key)
            header_line = lines[source_line - 1]
            header_kind = peek_statement_kind(header_line)
            position_mode: str | None = None
            text_position: TextPositionStatement | None = None
            text_style: TextColorStyleStatement | None = None
            say_style_position: SayWhatStylePositionStatement | None = None
            move_lock_reason: dict[str, Any] | None = None
            style_lock_reason: dict[str, Any] | None = None
            gui_rpy_path: str | None = None
            
            if header_kind == "text":
                # Try direct position first
                try:
                    text_position = analyze_text_position_statement(
                        header_line,
                        expected_widget_id=widget_id,
                    )
                except EditorSourceError as exc:
                    move_lock_reason = self._lock_reason(exc.code, str(exc))
                
                # Try style color
                try:
                    text_style = analyze_text_color_style(
                        header_line,
                        expected_widget_id=widget_id,
                    )
                    if text_style.style_lock_code is not None:
                        style_lock_reason = self._lock_reason(
                            text_style.style_lock_code,
                            text_style.style_lock_message or text_style.style_lock_code,
                        )
                except EditorSourceError as exc:
                    style_lock_reason = self._lock_reason(exc.code, str(exc))
                
                # Critical finding #1/#2: If direct position failed for say.what,
                # attempt style-backed ownership resolution
                if (
                    text_position is None
                    and widget_id == "what"
                    and runtime_key.get("screen") == "say"
                ):
                    # CRITICAL: Clear move_lock_reason from direct position failure
                    # say.what has separate ownership path via gui.rpy
                    move_lock_reason = None
                    
                    # Ownership proof part 1: screens.rpy must have `text ... id "what"`
                    # RuntimeProbe gave us widget_id == "what", but verify source identity
                    if 'id "what"' not in header_line and "id 'what'" not in header_line:
                        if move_lock_reason is None:
                            move_lock_reason = self._lock_reason(
                                "STYLE_POSITION_SOURCE_UNRESOLVED",
                                "say.what widget ID not found in source (screens.rpy)",
                            )
                        say_style_position = None
                    # Ownership proof part 2: screens.rpy must NOT have inline xpos/ypos
                    # (fail-closed: if xpos/ypos keywords present, reject even if in comments)
                    elif "xpos" in header_line or "ypos" in header_line:
                        if move_lock_reason is None:
                            move_lock_reason = self._lock_reason(
                                "STYLE_POSITION_SOURCE_AMBIGUOUS",
                                "say.what has inline position; style-backed ownership unclear",
                            )
                        say_style_position = None
                    else:
                        # Ownership proof part 3: screens.rpy must have style say_dialogue binding
                        # xpos/ypos to gui.dialogue_xpos/ypos (fail-closed if missing/ambiguous/expressions)
                        try:
                            style_binding = analyze_say_dialogue_style_binding(
                                source_text,
                                xpos_var="gui.dialogue_xpos",
                                ypos_var="gui.dialogue_ypos",
                            )
                            if not style_binding.binding_proven:
                                if move_lock_reason is None:
                                    move_lock_reason = self._lock_reason(
                                        style_binding.lock_code or "STYLE_POSITION_SOURCE_UNRESOLVED",
                                        style_binding.lock_message or "style say_dialogue binding not proven",
                                    )
                                say_style_position = None
                        except Exception as exc:
                            if move_lock_reason is None:
                                move_lock_reason = self._lock_reason(
                                    "STYLE_POSITION_SOURCE_UNRESOLVED",
                                    f"screens.rpy style binding analysis failed: {str(exc)}",
                                )
                            say_style_position = None
                        
                        # Ownership proof part 4: gui.rpy must have unlocked gui.dialogue_xpos/ypos
                        # Use game-relative path: resolve_game_path already joins project_root/game/
                        if move_lock_reason is None:
                            gui_rpy_path = "gui.rpy"
                            
                            try:
                                # Load gui.rpy to analyze style-backed position
                                gui_absolute = resolve_game_path(self._project.root, gui_rpy_path)
                                gui_source = gui_absolute.read_text(encoding="utf-8")
                                say_style_position = analyze_say_what_style_position(
                                    gui_source,
                                    xpos_var="gui.dialogue_xpos",
                                    ypos_var="gui.dialogue_ypos",
                                )
                                if say_style_position.position_lock_code is not None:
                                    # Style position locked - use its reason instead of misleading XPOS_DUPLICATE
                                    move_lock_reason = self._lock_reason(
                                        say_style_position.position_lock_code,
                                        say_style_position.position_lock_message or say_style_position.position_lock_code,
                                    )
                                    say_style_position = None  # Don't unlock
                            except EditorPathError as exc:
                                # gui.rpy not found or path error - keep original lock reason but don't use XPOS_DUPLICATE
                                if move_lock_reason is None:
                                    move_lock_reason = self._lock_reason(
                                        "STYLE_POSITION_SOURCE_UNRESOLVED",
                                        f"gui.rpy path error: {exc.code}",
                                    )
                                say_style_position = None
                            except Exception as exc:
                                # Malformed gui.rpy or analysis error - surface as lock reason
                                if move_lock_reason is None:
                                    move_lock_reason = self._lock_reason(
                                        "STYLE_POSITION_SOURCE_UNRESOLVED",
                                        f"gui.rpy analysis failed: {str(exc)}",
                                    )
                                say_style_position = None
                
                statement = text_position or text_style or say_style_position
                if statement is None:
                    reason = move_lock_reason or style_lock_reason
                    raise EditorError(
                        (reason or {}).get("code", "TEXT_SOURCE_UNSUPPORTED"),
                        (reason or {}).get("message", "text source is not writable"),
                    )
                statement_kind = "text"
                
                if text_position is not None:
                    position_mode = "xy"
                    original_position = [int(text_position.xpos), int(text_position.ypos)]
                elif say_style_position is not None and say_style_position.position_mode:
                    # Style-backed position unlocked
                    position_mode = say_style_position.position_mode
                    original_position = [int(say_style_position.xpos), int(say_style_position.ypos)]
                else:
                    # Style color only, no position
                    position_mode = None
                    original_position = list(runtime_position)
            elif header_kind == "button":
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
            if header_kind != "text" and not isinstance(statement, TextColorStyleStatement):
                # Runtime-delta modes (align/offset): original_position is focus TL.
                position_mode = getattr(statement, "position_mode", "xy")
                if uses_runtime_delta_position(position_mode):
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
                "locator": runtime_key.get("locator"),
                "invocation_path": runtime_key.get("invocation_path"),
                "instance_discriminator": runtime_key.get("instance_discriminator"),
                "ancestry": normalized_ancestry,
                "statement_kind": statement_kind,
                "baseline_sha256": source_sha,
                "position_mode": position_mode,
            }
            
            # For say.what style position: store gui.rpy path and parsed statement
            if say_style_position is not None and say_style_position.position_mode:
                source_key["gui_rpy_path"] = gui_rpy_path
                source_key["say_style_position_xpos"] = say_style_position.xpos
                source_key["say_style_position_ypos"] = say_style_position.ypos
                source_key["say_style_position_baseline_sha256"] = say_style_position.baseline_sha256
            if statement_kind == "text":
                source_key["move_lock_reason"] = move_lock_reason
                source_key["style_lock_reason"] = style_lock_reason
                source_key["style_mode"] = text_style.style_mode if text_style is not None else None
                source_key["style_color"] = text_style.color if text_style is not None else None
            # Issue #47: bar-only resize capability from pure xsize/ysize.
            if statement_kind == "bar" and isinstance(statement, BarStatement):
                if (
                    statement.size_mode == BAR_SIZE_MODE_XSIZE_YSIZE
                    and statement.xsize is not None
                    and statement.ysize is not None
                ):
                    original_size = [int(statement.xsize), int(statement.ysize)]
                    source_key["size_mode"] = BAR_SIZE_MODE_XSIZE_YSIZE
                    source_key["authored_size"] = list(original_size)
                else:
                    source_key["size_mode"] = None
                    if statement.resize_lock_code is not None:
                        source_key["resize_lock_reason"] = self._lock_reason(
                            statement.resize_lock_code,
                            statement.resize_lock_message or statement.resize_lock_code,
                        )
            if (
                not isinstance(statement, TextColorStyleStatement)
                and isinstance(position_mode, str)
                and uses_runtime_delta_position(position_mode)
            ):
                # Shared authored + measured baseline fields for delta write-back.
                authored = (
                    [float(statement.xpos), float(statement.ypos)]
                    if position_mode == "align"
                    else [int(statement.xpos), int(statement.ypos)]
                )
                source_key[f"{position_mode}_authored"] = authored
                source_key[f"{position_mode}_runtime_baseline"] = [
                    int(runtime_position[0]),
                    int(runtime_position[1]),
                ]
            if position_mode == "align" and not isinstance(statement, TextColorStyleStatement):
                # Align-only geometry gate: parent must prove full-screen 1280×720.
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
                expected_measurement = "scene_tree_text" if statement_kind == "text" else "focus_list"
                if observation.get("measurement_method") != expected_measurement:
                    lock_reason = self._lock_reason(
                        "MEASUREMENT_METHOD_INVALID",
                        f"input observation measurement_method must be {expected_measurement}",
                    )
                elif independent.get("measurement_method") != expected_measurement:
                    lock_reason = self._lock_reason(
                        "INDEPENDENT_MEASUREMENT_INVALID",
                        f"independent measurement_method must be {expected_measurement}",
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
                elif (
                    text_style is not None
                    and text_style.style_mode is not None
                    and self._normalize_style_color(independent.get("style_color"))
                    != self._normalize_style_color(text_style.color)
                ):
                    lock_reason = self._lock_reason(
                        "RUNTIME_STYLE_COLOR_MISMATCH",
                        "independent runtime colour does not match the authored literal",
                    )
                elif statement.widget_id != widget_id:
                    lock_reason = self._lock_reason("ID_MISMATCH", "source literal id does not match runtime widget id")
        except EditorPathError as exc:
            lock_reason = self._lock_reason(exc.code, str(exc))
        except EditorSourceError as exc:
            # Identity outranks source form: a repeated statement stays locked
            # for being repeated, whatever its position keywords read like, so
            # the reason does not depend on which gate ran last. Read, path and
            # generation failures below are not form questions and keep their
            # own reason.
            lock_reason = runtime_lock_reason or self._lock_reason(exc.code, str(exc))
        except EditorError as exc:
            lock_reason = self._lock_reason(exc.code, exc.message)
        except (OSError, UnicodeDecodeError) as exc:
            lock_reason = self._lock_reason("SOURCE_READ_FAILED", f"unable to read source: {exc}")

        analysis_id = uuid.uuid4().hex
        can_style_color = (
            lock_reason is None
            and source_key is not None
            and source_key.get("statement_kind") == "text"
            and source_key.get("style_mode") == "literal_hex"
        )
        can_move = (
            lock_reason is None
            and source_key is not None
            and source_key.get("position_mode") is not None
        )
        can_resize = (
            can_move
            and original_size is not None
            and runtime_size is not None
            and isinstance(source_key, dict)
            and source_key.get("size_mode") == BAR_SIZE_MODE_XSIZE_YSIZE
        )
        zorder_sibling = (
            self._find_zorder_adjacent_sibling(source_text, source_line, widget_id)
            if lock_reason is None
            and isinstance(source_key, dict)
            and source_key.get("statement_kind") == "button"
            else None
        )
        can_zorder = zorder_sibling is not None
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
            original_size=list(original_size) if original_size is not None else None,
            runtime_size=list(runtime_size) if runtime_size is not None else None,
        )
        with self._lock:
            self._analyses[analysis_id] = record

        return {
            "analysis_id": analysis_id,
            "source_key": source_key,
            "original_position": original_position,
            "original_size": list(original_size) if original_size is not None else None,
            "capabilities": {
                "move": can_move,
                "resize": can_resize,
                **(
                    {
                        "zorder_raise_adjacent_sibling": True,
                        "zorder_sibling_widget_id": zorder_sibling[0],
                        "zorder_sibling_line": zorder_sibling[1],
                    }
                    if can_zorder and zorder_sibling is not None
                    else {}
                ),
                **(
                    {
                        "style_color": True,
                        "style_color_preview": True,
                        "style_color_commit": True,
                        "style_color_undo": True,
                        "style_color_attestation_rollback": True,
                    }
                    if can_style_color
                    else {}
                ),
            },
            "lock_reason": lock_reason,
        }

    def _find_zorder_adjacent_sibling(
        self,
        source_text: str,
        target_line: int,
        target_widget_id: str,
    ) -> tuple[str, int] | None:
        lines = source_text.splitlines(keepends=True)
        if target_line < 1 or target_line > len(lines):
            return None
        header = lines[target_line - 1]
        if peek_statement_kind(header) != "button":
            return None
        indent = len(header) - len(header.lstrip())
        next_idx = target_line
        while next_idx < len(lines):
            line = lines[next_idx]
            if not line.strip() or line.strip().startswith("#"):
                next_idx += 1
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= indent:
                break
            next_idx += 1
        while next_idx < len(lines) and (not lines[next_idx].strip() or lines[next_idx].strip().startswith("#")):
            next_idx += 1
        if next_idx >= len(lines):
            return None
        sibling_header = lines[next_idx]
        if peek_statement_kind(sibling_header) != "button":
            return None
        try:
            sibling_id = _literal_button_id(sibling_header)
            sibling_line = next_idx + 1
            analyze_raise_adjacent_sibling(
                source_text,
                target_source_line=target_line,
                sibling_source_line=sibling_line,
                target_widget_id=target_widget_id,
                sibling_widget_id=sibling_id,
            )
            return (sibling_id, sibling_line)
        except EditorSourceError:
            return None

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
        selected_records: list[_SelectedIntent] = []
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
            color = intent.get("color")
            is_structural_swap = (
                intent.get("operation") == "raise_adjacent_sibling"
                or "sibling_widget_id" in intent
                or "swap_sibling" in intent
            )
            is_text = source_key.get("statement_kind") == "text"
            if is_structural_swap:
                if any(key in intent for key in ("x", "y", "w", "h", "width", "height", "color")):
                    raise EditorError("STRUCTURAL_INTENT_COMBINATION_REJECTED", "structural swap intent cannot include position, size, or color fields")
                sibling_id = intent.get("sibling_widget_id") or (
                    intent.get("swap_sibling")[0] if isinstance(intent.get("swap_sibling"), (list, tuple)) else None
                )
                sibling_line = intent.get("sibling_line", intent.get("sibling_source_line")) or (
                    intent.get("swap_sibling")[1] if isinstance(intent.get("swap_sibling"), (list, tuple)) else None
                )
                if not isinstance(sibling_id, str) or not isinstance(sibling_line, int):
                    raise EditorError("INTENT_STRUCTURAL_INVALID", "structural intent missing valid sibling_widget_id or sibling_line")
                selected = _SelectedIntent(
                    record=record,
                    source_key=source_key,
                    swap_sibling=(sibling_id, sibling_line),
                )
            elif is_text:
                has_position = "x" in intent or "y" in intent
                if not has_position and color is None:
                    raise EditorError("INTENT_INVALID", "text intent requires position and/or color")
                x = intent.get("x") if has_position else None
                y = intent.get("y") if has_position else None
                if has_position:
                    if not isinstance(x, int) or not isinstance(y, int):
                        raise EditorError("INTENT_POSITION_INVALID", "intent x and y must be integers")
                    if source_key.get("position_mode") is None:
                        raise EditorError("ANALYSIS_MOVE_UNSUPPORTED", "analysis does not unlock movement for this text")
                if any(key in intent for key in ("w", "h", "width", "height")):
                    raise EditorError("ANALYSIS_RESIZE_UNSUPPORTED", "text resize is not supported")
                literal_color = None
                if color is not None:
                    if not isinstance(color, str):
                        raise EditorError("INTENT_STYLE_COLOR_INVALID", "text style intent color must be a string")
                    literal_color = self._literal_style_color(color)
                    literal_baseline = self._literal_style_color(source_key.get("style_color"))
                    if literal_color is None:
                        raise EditorError("INTENT_STYLE_COLOR_INVALID", "text style intent color must be a supported hex literal")
                    if literal_baseline is None:
                        raise EditorError("SOURCE_KEY_INVALID", "style source key is missing its literal baseline colour")
                    if len(literal_color) != len(literal_baseline):
                        raise EditorError(
                            "STYLE_COLOR_HEX_FAMILY_MISMATCH",
                            "text style intent must preserve the authored hex literal length",
                        )
                    if source_key.get("style_mode") != "literal_hex":
                        raise EditorError("ANALYSIS_STYLE_COLOR_UNSUPPORTED", "analysis does not unlock literal style color")
                selected = _SelectedIntent(
                    record=record,
                    source_key=source_key,
                    x=x,
                    y=y,
                    color=literal_color,
                )
            else:
                if color is not None:
                    raise EditorError("ANALYSIS_STYLE_COLOR_UNSUPPORTED", "style color is only supported for text")
                if source_key.get("position_mode") is None:
                    raise EditorError("ANALYSIS_MOVE_UNSUPPORTED", "analysis does not unlock movement for this target")
                x = intent.get("x")
                y = intent.get("y")
                if not isinstance(x, int) or not isinstance(y, int):
                    raise EditorError("INTENT_POSITION_INVALID", "intent x and y must be integers")
                width = intent.get("w", intent.get("width"))
                height = intent.get("h", intent.get("height"))
                if width is not None or height is not None:
                    if type(width) is not int or type(height) is not int:
                        raise EditorError("INTENT_SIZE_INVALID", "intent w and h must both be integers when resizing")
                    if int(width) <= 0 or int(height) <= 0:
                        raise EditorError("BAR_SIZE_NON_POSITIVE", "intent size must be positive")
                    if (
                        record.original_size is None
                        or record.runtime_size is None
                        or source_key.get("size_mode") != BAR_SIZE_MODE_XSIZE_YSIZE
                    ):
                        raise EditorError(
                            "ANALYSIS_RESIZE_UNSUPPORTED",
                            "analysis does not unlock resize for this target",
                        )
                selected = _SelectedIntent(
                    record=record,
                    source_key=source_key,
                    x=x,
                    y=y,
                    width=int(width) if width is not None else None,
                    height=int(height) if height is not None else None,
                )
            relative_path = self._require_source_relative_path(source_key)
            source_paths.add(relative_path)
            selected_records.append(selected)

        if len(source_paths) != 1:
            raise EditorError("MULTI_FILE_UNSUPPORTED", "all intents must resolve to exactly one source file")

        if any(selected.swap_sibling is not None for selected in selected_records):
            if len(selected_records) > 1 or any(
                selected.x is not None or selected.y is not None or selected.color is not None
                for selected in selected_records
            ):
                raise EditorError(
                    "STRUCTURAL_INTENT_COMBINATION_REJECTED",
                    "structural swap cannot be combined with position or color intents",
                )

        relative_path = next(iter(source_paths))
        source_path = resolve_game_path(self._project.root, relative_path)
        current_bytes = source_path.read_bytes()
        current_sha = sha256_bytes(current_bytes)

        for selected in selected_records:
            record = selected.record
            source_key = selected.source_key
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
            expected_measurement = (
                "scene_tree_text"
                if selected.source_key.get("statement_kind") == "text"
                else "focus_list"
            )
            if independent.get("measurement_method") != expected_measurement:
                raise EditorError(
                    "MEASUREMENT_METHOD_INVALID",
                    f"independent reanalysis must use {expected_measurement}",
                )
            if self._coerce_generation(independent.get("script_generation")) != generation:
                raise EditorError("SCRIPT_GENERATION_MISMATCH", "runtime reanalysis returned stale generation")
            if independent.get("frame_id") == record.independent_frame_id:
                raise EditorError("INDEPENDENT_FRAME_NOT_FRESH", "runtime reanalysis did not produce a fresh frame")
            if (
                selected.color is not None
                and self._normalize_style_color(independent.get("style_color"))
                != self._normalize_style_color(selected.color)
            ):
                raise EditorError(
                    "RUNTIME_STYLE_COLOR_MISMATCH",
                    "runtime preview colour does not match the requested style intent",
                )

        try:
            staged_bytes, swap_locations = self._apply_same_file_intents(current_bytes, selected_records)
        except EditorSourceError as exc:
            raise EditorError(exc.code, str(exc)) from exc
        
        # Critical finding #1: Two-file write path for say.what style position
        # Detect gui.rpy intents (position lives in gui.rpy, identity in screens.rpy)
        gui_intents = [
            sel for sel in selected_records
            if sel.source_key.get("position_mode") == SAY_WHAT_STYLE_POSITION_MODE
            and sel.x is not None
            and sel.y is not None
        ]
        
        if gui_intents:
            # Verify screens.rpy unchanged (identity-only path)
            if staged_bytes != current_bytes:
                raise EditorError(
                    "MULTI_FILE_WRITE_UNSUPPORTED",
                    "say.what style position cannot be combined with other screen changes in V1",
                )
            
            # Create gui.rpy transaction
            if len(gui_intents) != len(selected_records):
                raise EditorError(
                    "MULTI_FILE_WRITE_UNSUPPORTED",
                    "say.what style position cannot be combined with other intents in V1",
                )
            
            # All intents are gui.rpy - create gui transaction
            gui_rpy_path = gui_intents[0].source_key.get("gui_rpy_path")
            if not gui_rpy_path:
                raise EditorError("GUI_RPY_PATH_MISSING", "gui.rpy path is missing from source_key")
            
            gui_absolute = resolve_game_path(self._project.root, gui_rpy_path)
            gui_current_bytes = gui_absolute.read_bytes()
            gui_current_sha = sha256_bytes(gui_current_bytes)
            
            # Verify gui.rpy baseline
            gui_baseline = gui_intents[0].source_key.get("say_style_position_baseline_sha256")
            if gui_current_sha != gui_baseline:
                raise EditorError("STALE_SOURCE", "gui.rpy has changed since analysis")
            
            # Apply gui.rpy patch
            gui_source_text = gui_current_bytes.decode("utf-8")
            say_statement = analyze_say_what_style_position(
                gui_source_text,
                xpos_var="gui.dialogue_xpos",
                ypos_var="gui.dialogue_ypos",
            )
            if say_statement.position_lock_code is not None:
                raise EditorError(
                    say_statement.position_lock_code,
                    say_statement.position_lock_message or "say.what style position is locked",
                )
            
            # Calculate logical-pixel deltas: apply to authored gui.scale ints
            # NEVER write absolute screen coords into gui.dialogue_xpos/ypos
            authored_x = gui_intents[0].source_key.get("say_style_position_xpos")
            authored_y = gui_intents[0].source_key.get("say_style_position_ypos")
            
            # CRITICAL: For style-backed position, baseline is AUTHORED values (window-relative)
            # NOT original_position (which is screen-absolute including window offset)
            # Intent x/y from preview are ALSO window-relative (preview mutates style, not screen coords)
            runtime_baseline_x = authored_x
            runtime_baseline_y = authored_y
            
            # New position from intent (after drag, window-relative from preview)
            intent_x = gui_intents[0].x
            intent_y = gui_intents[0].y
            
            # Calculate logical-pixel delta (both in window-relative space)
            delta_x = intent_x - runtime_baseline_x
            delta_y = intent_y - runtime_baseline_y
            
            # Apply delta to authored values (window-relative coords)
            patched_x = authored_x + delta_x
            patched_y = authored_y + delta_y
            
            gui_staged_bytes = apply_say_what_style_position_patch(
                gui_current_bytes,
                say_statement,
                x=patched_x,
                y=patched_y,
            )
            
            transaction_id = uuid.uuid4().hex
            transaction = _TransactionRecord(
                transaction_id=transaction_id,
                source_relative_path=gui_rpy_path,
                source_absolute_path=gui_absolute,
                original_bytes=gui_current_bytes,
                staged_bytes=gui_staged_bytes,
                original_sha256=gui_current_sha,
                staged_sha256=sha256_bytes(gui_staged_bytes),
                generation=generation,
                expected_targets=[
                    {
                        **self._expected_target_for_intent(sel, None),
                        "say_style_position_previous_x": authored_x,
                        "say_style_position_previous_y": authored_y,
                        "say_style_position_new_x": patched_x,
                        "say_style_position_new_y": patched_y,
                    }
                    for sel in gui_intents
                ],
                state="staged",
            )
        else:
            # Normal single-file transaction (screens.rpy)
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
                expected_targets=[self._expected_target_for_intent(selected, swap_locations) for selected in selected_records],
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
        self._publish_transaction_bytes(transaction)
        self._schedule_attestation_timeout(transaction)

        return {"transaction_id": transaction_id, "state": "published", "reload_required": True}

    def _expected_target_for_intent(
        self,
        selected: _SelectedIntent,
        swap_locations: tuple[tuple[str, int], tuple[str, int]] | None = None,
    ) -> dict[str, Any]:
        record = selected.record
        expected: dict[str, Any] = {
            "analysis_id": record.analysis_id,
            "source_key": selected.source_key,
            "runtime_key": deepcopy(record.runtime_key),
        }
        if selected.swap_sibling is not None:
            target_id = selected.source_key.get("widget_id")
            old_target_line = selected.source_key.get("line")
            sibling_id, old_sibling_line = selected.swap_sibling
            new_target_line = old_target_line
            new_sibling_line = old_sibling_line
            if swap_locations:
                for loc_id, loc_line in swap_locations:
                    if loc_id == target_id:
                        new_target_line = loc_line
                    elif loc_id == sibling_id:
                        new_sibling_line = loc_line
            if isinstance(expected["runtime_key"].get("source_location"), list) and len(expected["runtime_key"]["source_location"]) == 2:
                expected["runtime_key"]["source_location"][1] = new_target_line
            expected["structural_swap"] = {
                "target_widget_id": target_id,
                "sibling_widget_id": sibling_id,
                "target_line": new_target_line,
                "sibling_line": new_sibling_line,
                "previous_target_line": old_target_line,
                "previous_sibling_line": old_sibling_line,
            }
            return expected

        if selected.color is not None:
            previous = self._literal_style_color(selected.source_key.get("style_color"))
            if previous is None:
                raise EditorError("SOURCE_KEY_INVALID", "style source key is missing its baseline colour")
            expected["style_color"] = selected.color
            expected["previous_style_color"] = previous
        if selected.x is None and selected.y is None:
            if selected.color is not None:
                return expected
            raise EditorError("INTENT_POSITION_INVALID", "position intent is missing x or y")
        if selected.x is None or selected.y is None:
            raise EditorError("INTENT_POSITION_INVALID", "position intent is missing x or y")
        expected["position"] = [
            int(record.runtime_position[0]) + selected.x - int(record.original_position[0]),
            int(record.runtime_position[1]) + selected.y - int(record.original_position[1]),
        ]
        if (
            selected.width is not None
            and selected.height is not None
            and record.original_size is not None
            and record.runtime_size is not None
        ):
            expected["size"] = [
                int(record.runtime_size[0]) + selected.width - int(record.original_size[0]),
                int(record.runtime_size[1]) + selected.height - int(record.original_size[1]),
            ]
        return expected

    def _command_undo_commit(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_string(payload, "session_id")
        if session_id != self._session_id:
            raise EditorError("SESSION_ID_MISMATCH", "undo session_id does not match current editor session")
        prior_id = self._require_string(payload, "transaction_id")
        with self._lock:
            prior = self._transactions.get(prior_id)
            generation = self._script_generation
        if prior is None:
            raise EditorError("TRANSACTION_NOT_FOUND", f"transaction not found: {prior_id}")
        if prior.state != "committed":
            raise EditorError("UNDO_TRANSACTION_INVALID", "only a committed transaction can be undone")

        is_style_color_tx = bool(prior.expected_targets) and all(
            self._literal_style_color(target.get("style_color")) is not None
            and self._literal_style_color(target.get("previous_style_color")) is not None
            for target in prior.expected_targets
        )
        is_structural_tx = bool(prior.expected_targets) and all(
            isinstance(target.get("structural_swap"), dict)
            for target in prior.expected_targets
        )
        is_say_style_position_tx = bool(prior.expected_targets) and all(
            target.get("say_style_position_previous_x") is not None
            and target.get("say_style_position_previous_y") is not None
            and target.get("say_style_position_new_x") is not None
            and target.get("say_style_position_new_y") is not None
            for target in prior.expected_targets
        )
        if not (is_style_color_tx or is_structural_tx or is_say_style_position_tx):
            raise EditorError("UNDO_STYLE_COLOR_ONLY", "product commit undo is limited to text style color, zorder structural swap, and say.what style position")

        current_bytes = prior.source_absolute_path.read_bytes()
        current_sha = sha256_bytes(current_bytes)
        if current_sha != prior.staged_sha256:
            raise EditorError("STALE_SOURCE", "source file changed after the committed transaction")

        expected_targets: list[dict[str, Any]] = []
        for target in prior.expected_targets:
            reversed_target = deepcopy(target)
            if is_style_color_tx:
                current_color = self._literal_style_color(target.get("style_color"))
                previous_color = self._literal_style_color(target.get("previous_style_color"))
                if current_color is None or previous_color is None:
                    raise EditorError("UNDO_STYLE_COLOR_ONLY", "style undo target is missing colour evidence")
                reversed_target["style_color"] = previous_color
                reversed_target["previous_style_color"] = current_color
            elif is_structural_tx:
                swap_info = target.get("structural_swap")
                if not isinstance(swap_info, dict):
                    raise EditorError("UNDO_STYLE_COLOR_ONLY", "structural undo target is missing swap evidence")
                reversed_swap = deepcopy(swap_info)
                reversed_swap["target_line"] = swap_info["previous_target_line"]
                reversed_swap["sibling_line"] = swap_info["previous_sibling_line"]
                reversed_swap["previous_target_line"] = swap_info["target_line"]
                reversed_swap["previous_sibling_line"] = swap_info["sibling_line"]
                reversed_target["structural_swap"] = reversed_swap
                if (
                    isinstance(reversed_target.get("runtime_key", {}).get("source_location"), list)
                    and len(reversed_target["runtime_key"]["source_location"]) == 2
                ):
                    reversed_target["runtime_key"]["source_location"][1] = reversed_swap["target_line"]
            elif is_say_style_position_tx:
                # Swap current and previous positions for undo
                prev_x = target.get("say_style_position_previous_x")
                prev_y = target.get("say_style_position_previous_y")
                new_x = target.get("say_style_position_new_x")
                new_y = target.get("say_style_position_new_y")
                if prev_x is None or prev_y is None or new_x is None or new_y is None:
                    raise EditorError("UNDO_STYLE_COLOR_ONLY", "say style position undo target is missing position evidence")
                reversed_target["say_style_position_previous_x"] = new_x
                reversed_target["say_style_position_previous_y"] = new_y
                reversed_target["say_style_position_new_x"] = prev_x
                reversed_target["say_style_position_new_y"] = prev_y
            expected_targets.append(reversed_target)

        transaction_id = uuid.uuid4().hex
        transaction = _TransactionRecord(
            transaction_id=transaction_id,
            source_relative_path=prior.source_relative_path,
            source_absolute_path=prior.source_absolute_path,
            original_bytes=current_bytes,
            staged_bytes=prior.original_bytes,
            original_sha256=current_sha,
            staged_sha256=sha256_bytes(prior.original_bytes),
            generation=generation,
            expected_targets=expected_targets,
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
                "Ren'Py lint failed while validating product undo",
                details={"transaction_id": transaction_id},
            )
        transaction.diagnostics = {
            **self._lint_diagnostics(lint_result),
            "undo_of_transaction_id": prior_id,
        }
        self._publish_transaction_bytes(transaction)
        self._schedule_attestation_timeout(transaction)
        return {
            "transaction_id": transaction_id,
            "undo_of_transaction_id": prior_id,
            "state": "published",
            "reload_required": True,
        }

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

        try:
            result = probe.attest(
                transaction_id=transaction_id,
                script_generation=script_generation,
                deadline=_now_deadline(self._attestation_timeout),
                expected_targets=list(record.expected_targets),
            )
        except EditorError:
            # A refusal from the bridge arrives as a raised EditorError, not a
            # falsy reply, so the rollbacks below were unreachable for it. Left
            # alone, the published bytes stayed in the author's file until the
            # attestation timer fired seconds later.
            self._conditional_rollback(record)
            # Game already reloaded; forget the pre-reload generation so later
            # analyses can re-lock to the live script_generation (including any
            # follow-up reload that re-syncs rolled-back source into memory).
            with self._lock:
                self._script_generation = -1
            raise
        if not isinstance(result, dict):
            self._conditional_rollback(record)
            with self._lock:
                self._script_generation = -1
            raise EditorError("ATTESTATION_FAILED", "runtime probe returned invalid attestation payload")
        if result.get("ok") is not True or result.get("state") != "all_targets_attested":
            self._conditional_rollback(record)
            with self._lock:
                self._script_generation = -1
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

    def _runtime_widget_alias(self, runtime_key: dict[str, Any]) -> str | None:
        widget_id = runtime_key.get("widget_id")
        if widget_id is None:
            return None
        if not isinstance(widget_id, str) or widget_id == "":
            raise EditorError("WIDGET_ID_INVALID", "runtime_key.widget_id must be null or a non-empty string")
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
        required_string_keys = ("screen", "invocation_path")
        for key in required_string_keys:
            value = runtime_key.get(key)
            if not isinstance(value, str) or value == "":
                return self._lock_reason("RUNTIME_KEY_INVALID", f"runtime_key.{key} must be a non-empty string")

        widget_id = runtime_key.get("widget_id")
        if widget_id is not None and (not isinstance(widget_id, str) or widget_id == ""):
            return self._lock_reason("WIDGET_ID_INVALID", "runtime_key.widget_id must be null or a non-empty string")

        source_location = runtime_key.get("source_location")
        if not (
            isinstance(source_location, list)
            and len(source_location) == 2
            and isinstance(source_location[0], str)
            and isinstance(source_location[1], int)
        ):
            return self._lock_reason("SOURCE_LOCATION_INVALID", "runtime_key.source_location must be [path, line]")

        if widget_id is None:
            locator = runtime_key.get("locator")
            if not (
                isinstance(locator, dict)
                and locator.get("kind") == "source"
                and locator.get("source_location") == source_location
                and isinstance(locator.get("statement_kind"), str)
            ):
                return self._lock_reason(
                    "SOURCE_LOCATOR_INVALID",
                    "anonymous targets require a matching source locator",
                )

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
        # Issue #44: one viewport is editable because the engine already offsets
        # focus rects by its scroll, measured across scroll positions. Nested
        # viewports compose two scroll offsets and were never measured.
        if sum(1 for a in ancestry if isinstance(a, dict) and a.get("type") == "Viewport") > 1:
            return self._lock_reason(
                "NESTED_VIEWPORT_UNSUPPORTED",
                "nested viewport ancestry is not editable in V1",
            )
        # Issue #45: only a single pure-crop Transform was measured. Nested crops
        # (any transform crop state) compose two clip windows and stay locked.
        _transform_crop_states = {
            "transform_crop",
            "transform_crop_partial",
            "transform_crop_composite",
            "transform_crop_unproven",
        }
        if (
            sum(
                1
                for a in ancestry
                if isinstance(a, dict) and a.get("crop_state") in _transform_crop_states
            )
            > 1
        ):
            return self._lock_reason(
                "NESTED_TRANSFORM_CROP_UNSUPPORTED",
                "nested transform crop ancestry is not editable in V1",
            )

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
            # Issue #45: Ren'Py has no runtime Crop class (Crop() returns
            # Transform). Keep a defensive lock if a future build ever surfaces
            # one; pure transform_crop is handled below.
            if ancestor_type == "Crop":
                return self._lock_reason("CROP_ANCESTRY_UNSUPPORTED", "Crop ancestry is not editable in V1")
            if bool(ancestor.get("editor_owned")):
                return self._lock_reason("EDITOR_OWNED_TARGET", "editor-owned displayables are never editable")
            screen_owner = ancestor.get("screen_owner")
            if isinstance(screen_owner, str) and screen_owner == "renforge.editor.v1":
                return self._lock_reason("EDITOR_OWNED_TARGET", "editor-owned displayables are never editable")
            crop_state = ancestor.get("crop_state")
            # crop+rotate / crop+zoom (issue #46) — distinct from pure crop.
            if crop_state == "transform_crop_composite":
                return self._lock_reason(
                    "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED",
                    "transform crop combined with rotate/zoom is not editable in V1",
                )
            # Partially crop-clamped focus: source xpos/ypos are unclipped layout
            # while focus x/y can sit on the crop edge (Codex P1 / issue #45).
            if crop_state == "transform_crop_partial":
                return self._lock_reason(
                    "TRANSFORM_CROP_PARTIAL_UNSUPPORTED",
                    "partially crop-clipped targets are not editable in V1",
                )
            # Visibility proof failed (render/size unavailable) — fail closed.
            if crop_state == "transform_crop_unproven":
                return self._lock_reason(
                    "TRANSFORM_CROP_UNPROVEN",
                    "transform crop full-visibility could not be proven",
                )
            # Legacy / defensive labels that live pure-crop never emits.
            if crop_state in {"crop", "crop_displayable"}:
                return self._lock_reason("CROP_ANCESTRY_UNSUPPORTED", "crop ancestry is not editable in V1")
            # Issue #45: pure fully-visible transform_crop is editable.
            if crop_state not in {"none", "viewport", "transform_crop"}:
                return self._lock_reason("ANCESTRY_CROP_UNPROVEN", f"unproven crop state: {crop_state}")
        return None

    def _lock_reason(self, code: str, message: str) -> dict[str, Any]:
        return {"code": code, "message": message}

    def _coerce_generation(self, value: Any) -> int | None:
        if type(value) is int:
            return value
        return None

    def _normalize_style_color(self, value: Any) -> str | None:
        """Return a comparable hex colour, collapsing opaque alpha and short form."""
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if not normalized.startswith("#"):
            return None
        body = normalized[1:]
        if any(char not in "0123456789abcdef" for char in body):
            return None
        if len(body) == 3:
            body = "".join(ch * 2 for ch in body)
        elif len(body) == 8 and body.endswith("ff"):
            body = body[:6]
        elif len(body) == 6:
            pass
        elif len(body) == 8:
            # Keep non-opaque alpha colours as 8-digit so they never match 6-digit authored forms accidentally.
            return "#" + body
        else:
            return None
        return "#" + body

    def _literal_style_color(self, value: Any) -> str | None:
        """Validate a writable hex literal while preserving its authored family."""
        if not isinstance(value, str):
            return None
        literal = value.strip().lower()
        if not literal.startswith("#"):
            return None
        body = literal[1:]
        if len(body) not in (3, 6, 8):
            return None
        if any(char not in "0123456789abcdef" for char in body):
            return None
        return literal

    def _extract_position(self, observation: dict[str, Any]) -> list[int]:
        rect = observation.get("rect")
        if not isinstance(rect, list) or len(rect) < 2:
            raise EditorError("RECT_INVALID", "observation rect must contain at least x and y")
        x, y = rect[0], rect[1]
        if not isinstance(x, int) or not isinstance(y, int):
            raise EditorError("RECT_INVALID", "observation rect coordinates must be integers")
        return [x, y]

    def _extract_size(self, observation: dict[str, Any]) -> list[int] | None:
        rect = observation.get("rect")
        if not isinstance(rect, list) or len(rect) < 4:
            return None
        width, height = rect[2], rect[3]
        if type(width) is not int or type(height) is not int:
            return None
        if width <= 0 or height <= 0:
            return None
        return [int(width), int(height)]

    def _apply_same_file_intents(
        self,
        source_bytes: bytes,
        selected_records: list[_SelectedIntent],
    ) -> tuple[bytes, tuple[tuple[str, int], tuple[str, int]] | None]:
        lines = source_bytes.decode("utf-8").splitlines(keepends=True)
        seen_targets: set[tuple[object, int, str | None]] = set()
        swap_locations: tuple[tuple[str, int], tuple[str, int]] | None = None

        for selected in selected_records:
            x = selected.x
            y = selected.y
            width = selected.width
            height = selected.height
            color = selected.color
            swap_sibling = selected.swap_sibling
            source_key = selected.source_key
            line_no = source_key.get("line")
            widget_id = source_key.get("widget_id")
            if not isinstance(line_no, int) or not (widget_id is None or isinstance(widget_id, str)):
                raise EditorError("SOURCE_KEY_INVALID", "source_key line/widget_id is invalid")
            if widget_id is None and source_key.get("statement_kind") != "text":
                raise EditorError("SOURCE_KEY_INVALID", "only source-located text may omit widget_id")
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

            if swap_sibling is not None:
                if len(selected_records) > 1 or any(
                    val is not None for val in (x, y, width, height, color)
                ):
                    raise EditorError(
                        "STRUCTURAL_INTENT_COMBINATION_REJECTED",
                        "structural swap cannot be combined with position or color intents",
                    )
                target_line = line_no
                target_id = widget_id
                sibling_id, sibling_line = swap_sibling
                plan = analyze_raise_adjacent_sibling(
                    source_text,
                    target_source_line=target_line,
                    sibling_source_line=sibling_line,
                    target_widget_id=target_id,
                    sibling_widget_id=sibling_id,
                )
                staged_bytes, swap_locations = apply_button_sibling_swap(source_bytes, plan)
                lines = staged_bytes.decode("utf-8").splitlines(keepends=True)
                continue

            if actual_kind == "text":
                if width is not None or height is not None:
                    raise EditorError("ANALYSIS_RESIZE_UNSUPPORTED", "text resize is not supported")
                
                # Critical finding #1: Two-file write path for say.what style position
                # Position lives in gui.rpy, identity stays in screens.rpy  
                position_mode = source_key.get("position_mode")
                if (
                    x is not None
                    and y is not None
                    and position_mode == SAY_WHAT_STYLE_POSITION_MODE
                ):
                    # Skip patching screens.rpy - will be handled as gui.rpy transaction
                    continue
                
                if x is not None or y is not None:
                    if x is None or y is None:
                        raise EditorError("INTENT_POSITION_INVALID", "text position intent shape is invalid")
                    statement = analyze_text_position_statement(
                        lines[line_no - 1],
                        expected_widget_id=widget_id,
                    )
                    lines[line_no - 1] = apply_text_position_patch(
                        lines[line_no - 1].encode("utf-8"),
                        statement,
                        x=x,
                        y=y,
                    ).decode("utf-8")
                if color is not None:
                    statement = analyze_text_color_style(
                        lines[line_no - 1],
                        expected_widget_id=widget_id,
                    )
                    lines[line_no - 1] = apply_text_color_patch(
                        lines[line_no - 1].encode("utf-8"),
                        statement,
                        color=color,
                    ).decode("utf-8")
                if x is None and y is None and color is None:
                    raise EditorError("INTENT_INVALID", "text intent requires position and/or color")
                continue

            if x is None or y is None:
                raise EditorError("INTENT_POSITION_INVALID", "position intent is missing x or y")
            if actual_kind == "button":
                if width is not None or height is not None:
                    raise EditorError(
                        "ANALYSIS_RESIZE_UNSUPPORTED",
                        "resize is only supported for bar xsize/ysize",
                    )
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
                if width is not None or height is not None:
                    raise EditorError(
                        "ANALYSIS_RESIZE_UNSUPPORTED",
                        "resize is only supported for bar xsize/ysize",
                    )
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
                if kind == "textbutton" and uses_runtime_delta_position(
                    getattr(statement, "position_mode", "xy")
                ):
                    if width is not None or height is not None:
                        raise EditorError(
                            "ANALYSIS_RESIZE_UNSUPPORTED",
                            "resize is only supported for bar xsize/ysize",
                        )
                    lines[line_no - 1] = apply_textbutton_patch(
                        lines[line_no - 1].encode("utf-8"),
                        statement,
                        x=x,
                        y=y,
                        **textbutton_patch_kwargs(statement, source_key),
                    ).decode("utf-8")
                else:
                    lines[line_no - 1] = apply_editable_statement_patch(
                        lines[line_no - 1].encode("utf-8"),
                        kind,
                        statement,
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                    ).decode("utf-8")

        return "".join(lines).encode("utf-8"), swap_locations

    def _validate_shadow(self, transaction: _TransactionRecord) -> ShadowLintResult:
        tx_dir = self._transaction_root / transaction.transaction_id
        shadow_root = tx_dir / "shadow"
        try:
            build_shadow_project(
                self._project,
                shadow_root=shadow_root,
                staged_replacements={transaction.source_relative_path: transaction.staged_bytes},
            )
            try:
                return run_shadow_lint(
                    self._project,
                    self._sdk,
                    shadow_root=shadow_root,
                    timeout=min(180.0, max(1.0, self._attestation_timeout * 3)),
                )
            except EditorError:
                raise
            except (OSError, ValueError) as exc:
                raise EditorError(
                    "SHADOW_LINT_FAILED",
                    "could not run Ren'Py lint in the shadow project",
                ) from exc
        finally:
            try:
                # Windows marks some copied modes read-only; clear the bit so
                # rmtree does not raise PermissionError after a successful lint.
                def _clear_readonly(func: Any, path: str, _exc_info: Any) -> None:
                    try:
                        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                        func(path)
                    except OSError:
                        raise

                shutil.rmtree(shadow_root, onerror=_clear_readonly)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise EditorError(
                    "SHADOW_CLEANUP_FAILED",
                    "could not remove the validation shadow project",
                ) from exc

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

    def _publish_transaction_bytes(self, transaction: _TransactionRecord) -> None:
        """CAS-publish staged bytes over the live source, retaining displaced evidence."""
        exchange_dir = self._transaction_root / transaction.transaction_id / "exchange"
        exchange_dir.mkdir(parents=True, exist_ok=True)
        replacement_path = exchange_dir / "replacement"
        displaced_path = exchange_dir / f"displaced-{uuid.uuid4().hex}"
        if replacement_path.exists() or replacement_path.is_symlink():
            try:
                replacement_path.unlink()
            except OSError as exc:
                raise EditorError("SOURCE_CAS_UNAVAILABLE", f"cannot prepare replacement path: {exc}") from exc
        try:
            from .paths import write_exclusive_bytes

            write_exclusive_bytes(replacement_path, transaction.staged_bytes)
        except OSError as exc:
            raise EditorError("SOURCE_CAS_UNAVAILABLE", f"cannot stage replacement bytes: {exc}") from exc

        transaction.state = "publishing"
        transaction.diagnostics = {
            **dict(transaction.diagnostics or {}),
            "replacement_path": str(replacement_path),
            "displaced_path": str(displaced_path),
            "operation": "publish",
        }
        self._persist_transaction(transaction)

        try:
            result = conditional_replace_file(
                transaction.source_absolute_path,
                expected_sha256=transaction.original_sha256,
                replacement_path=replacement_path,
                displaced_path=displaced_path,
            )
        except EditorPathError as exc:
            if exc.code == "STALE_SOURCE":
                transaction.state = "failed"
                self._persist_transaction(transaction)
                raise EditorError("STALE_SOURCE", "source file changed before atomic publication") from exc
            if exc.code == "SOURCE_EXCHANGE_CONFLICT":
                details = dict(exc.details or {})
                transaction.state = "rollback_conflict"
                transaction.uncertain_paths = list(details.get("uncertain_paths") or [transaction.source_relative_path])
                transaction.diagnostics = {**dict(transaction.diagnostics or {}), **details}
                self._persist_transaction(transaction)
                raise EditorError(
                    "SOURCE_EXCHANGE_CONFLICT",
                    "source changed during atomic exchange; all versions retained",
                    details=details,
                ) from exc
            if exc.code == "SOURCE_CAS_UNAVAILABLE":
                transaction.state = "failed"
                self._persist_transaction(transaction)
                raise EditorError("SOURCE_CAS_UNAVAILABLE", str(exc)) from exc
            transaction.state = "failed"
            self._persist_transaction(transaction)
            raise EditorError(exc.code, str(exc), details=getattr(exc, "details", None)) from exc

        transaction.state = "published"
        transaction.diagnostics = {
            **dict(transaction.diagnostics or {}),
            "published_sha256": result.published_sha256,
            "displaced_sha256": result.displaced_sha256,
            "displaced_path": str(result.displaced_path),
            "source_mode": result.source_mode,
        }
        self._persist_transaction(transaction)

    def _conditional_rollback(self, transaction: _TransactionRecord, *, allow_staged: bool = False) -> None:
        allowed_states = {"published", "publishing", "staged"} if allow_staged else {"published", "publishing"}
        with self._lock:
            if transaction.state not in allowed_states:
                return
            if transaction.timer is not None:
                transaction.timer.cancel()
                transaction.timer = None
        try:
            current_sha = hash_file_nofollow(transaction.source_absolute_path)
        except (EditorPathError, OSError):
            with self._lock:
                if transaction.state not in allowed_states:
                    return
                transaction.state = "rollback_conflict"
                transaction.uncertain_paths = [transaction.source_relative_path]
                self._persist_transaction(transaction)
            return

        with self._lock:
            if transaction.state not in allowed_states:
                return
            if current_sha == transaction.original_sha256:
                transaction.state = "rolled_back"
                transaction.uncertain_paths = []
                self._persist_transaction(transaction)
                return
            if current_sha != transaction.staged_sha256:
                transaction.state = "rollback_conflict"
                transaction.uncertain_paths = [transaction.source_relative_path]
                self._persist_transaction(transaction)
                return

        # Current disk matches staged: CAS original back into place.
        exchange_dir = self._transaction_root / transaction.transaction_id / "exchange"
        exchange_dir.mkdir(parents=True, exist_ok=True)
        original_candidate = exchange_dir / f"rollback-original-{uuid.uuid4().hex}"
        displaced_path = exchange_dir / f"rollback-displaced-{uuid.uuid4().hex}"
        try:
            from .paths import write_exclusive_bytes

            write_exclusive_bytes(original_candidate, transaction.original_bytes)
            conditional_replace_file(
                transaction.source_absolute_path,
                expected_sha256=transaction.staged_sha256,
                replacement_path=original_candidate,
                displaced_path=displaced_path,
            )
        except EditorPathError as exc:
            with self._lock:
                transaction.state = "rollback_conflict"
                details = dict(getattr(exc, "details", None) or {})
                transaction.uncertain_paths = list(
                    details.get("uncertain_paths")
                    or [transaction.source_relative_path, str(original_candidate), str(displaced_path)]
                )
                transaction.diagnostics = {
                    **dict(transaction.diagnostics or {}),
                    "rollback_error": exc.code,
                    **details,
                }
                self._persist_transaction(transaction)
            return
        except OSError:
            with self._lock:
                transaction.state = "rollback_conflict"
                transaction.uncertain_paths = [transaction.source_relative_path]
                transaction.diagnostics = {
                    **dict(transaction.diagnostics or {}),
                    "rollback_error": "ROLLBACK_FILE_ERROR",
                }
                self._persist_transaction(transaction)
            return

        with self._lock:
            transaction.state = "rolled_back"
            transaction.uncertain_paths = []
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
            
            # CRITICAL: Durable two-phase commit for restart resilience
            # CAS write can complete between state="publishing" and state="published".
            # If restart happens in this window, new coordinator must PROMOTE not rollback.
            #
            # Recovery logic:
            # 1. publishing/published + disk==staged → PROMOTE to published + arm timer
            # 2. publishing + disk==original → rollback (CAS never happened)
            # 3. staged → rollback immediately (incomplete)
            
            if state == "published" and manifest_intact:
                # Already published and waiting for handshake
                # Verify disk still has staged content
                try:
                    current_sha = hash_file_nofollow(source_path)
                except (EditorPathError, OSError):
                    current_sha = None
                
                if current_sha == actual_staged_sha256:
                    # Disk has staged content - arm attestation timer
                    timer = threading.Timer(self._attestation_timeout, self._conditional_rollback, args=(record,))
                    record.timer = timer
                    timer.start()
                    self._recovered.append(transaction_id)
                else:
                    # Disk doesn't match staged - rollback
                    self._conditional_rollback(record, allow_staged=False)
                    self._recovered.append(transaction_id)
                    
            elif state == "publishing" and manifest_intact:
                # Transaction was publishing when restart happened
                # Check if CAS completed (disk == staged) or not (disk == original)
                try:
                    current_sha = hash_file_nofollow(source_path)
                except (EditorPathError, OSError):
                    current_sha = None
                
                if current_sha == actual_staged_sha256:
                    # CAS completed! PROMOTE to published and arm timer
                    record.state = "published"
                    self._persist_transaction(record)
                    timer = threading.Timer(self._attestation_timeout, self._conditional_rollback, args=(record,))
                    record.timer = timer
                    timer.start()
                    self._recovered.append(transaction_id)
                elif current_sha == actual_original_sha256:
                    # CAS never happened - rollback to clean state
                    self._conditional_rollback(record, allow_staged=True)
                    self._recovered.append(transaction_id)
                else:
                    # Unknown state - mark conflict
                    record.state = "rollback_conflict"
                    record.uncertain_paths = [relative_path]
                    self._recovered.append(transaction_id)
                    self._persist_transaction(record)
                    
            elif state == "staged" and manifest_intact:
                # Incomplete transaction - rollback immediately
                self._conditional_rollback(record, allow_staged=True)
                self._recovered.append(transaction_id)
                
            elif state in {"staged", "publishing", "published"} and not manifest_intact:
                record.state = "rollback_conflict"
                record.uncertain_paths = [relative_path]
                self._recovered.append(transaction_id)
                self._persist_transaction(record)
