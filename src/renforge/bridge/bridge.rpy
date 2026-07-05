init python:
    import json
    import os
    import queue
    import socket
    import threading

    renpy = None
    try:
        import renpy as _renpy
        renpy = _renpy
    except Exception:
        pass

    store = None
    try:
        if renpy is not None:
            store = renpy.store
    except Exception:
        store = None
    if store is None:
        class _NullStore:
            pass

        store = _NullStore()

    def _to_int(value, default=0):
        try:
            return int(value)
        except Exception:
            return default

    def _store_get(name, default=None):
        try:
            return getattr(store, name)
        except Exception:
            return default

    def _store_set(name, value):
        try:
            setattr(store, name, value)
        except Exception:
            pass

    BRIDGE_HOST = _store_get("bridge_host", os.environ.get("RENFORGE_BRIDGE_HOST", "127.0.0.1"))
    BRIDGE_PORT = _to_int(_store_get("bridge_port", os.environ.get("RENFORGE_BRIDGE_PORT", 0)), 0)
    BRIDGE_TOKEN = _store_get("bridge_token", os.environ.get("RENFORGE_BRIDGE_TOKEN", ""))
    BRIDGE_TOKEN = "" if BRIDGE_TOKEN is None else str(BRIDGE_TOKEN).strip()

    BRIDGE_THREAD = None
    BRIDGE_COMMAND_QUEUE = queue.Queue()
    _bridge_stop_event = threading.Event()
    _BRIDGE_PERIODIC_CALLBACK = None

    class BridgeCommand:
        def __init__(self, command, payload):
            self.command = command
            self.payload = payload


    def _handle_ping(payload):
        return {"ok": True, "command": "ping"}


    def _handle_get_state(payload):
        return {"state": "idle", "payload": payload}


    def _send_reply(conn, reply):
        payload = json.dumps(reply) + "\n"
        conn.sendall(payload.encode("utf-8"))


    def _listener_loop(host, port, token):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((host, port))
            actual_port = server.getsockname()[1]
            setattr(store, "renforge_bridge_port", actual_port)
            global BRIDGE_PORT
            BRIDGE_PORT = actual_port
            server.listen(5)

            while not _bridge_stop_event.is_set():
                conn = None
                try:
                    server.settimeout(0.5)
                    conn, _ = server.accept()
                except socket.timeout:
                    continue

                with conn:
                    line = conn.makefile("r", encoding="utf-8").readline()
                    if not line:
                        continue
                    try:
                        request = json.loads(line)
                    except ValueError:
                        _send_reply(conn, {"error": "invalid_json"})
                        continue

                    if request.get("token") != token:
                        _send_reply(conn, {"error": "bad_token", "ok": False})
                        continue

                    command = request.get("command")
                    payload = request.get("payload")
                    if command == "ping":
                        BRIDGE_COMMAND_QUEUE.put(BridgeCommand("ping", payload))
                        _send_reply(conn, _handle_ping(payload))
                    elif command == "get_state":
                        BRIDGE_COMMAND_QUEUE.put(BridgeCommand("get_state", payload))
                        _send_reply(conn, _handle_get_state(payload))
                    else:
                        _send_reply(conn, {"error": "unknown_command", "command": command})
        finally:
            server.close()

    def _install_periodic_callback():
        global _BRIDGE_PERIODIC_CALLBACK
        if renpy is None:
            return
        config = getattr(renpy, "config", None)
        if config is None:
            return

        existing = getattr(config, "periodic_callback", None)
        if _BRIDGE_PERIODIC_CALLBACK is existing:
            return

        def _bridge_periodic_with_existing(*_args, **_kwargs):
            _drain_bridge_commands_periodic()
            if callable(existing):
                try:
                    return existing(*_args, **_kwargs)
                finally:
                    _drain_bridge_commands_periodic()
            _drain_bridge_commands_periodic()
            return None

        def _drain_bridge_commands_periodic():
            drain_bridge_commands()

        _BRIDGE_PERIODIC_CALLBACK = _bridge_periodic_with_existing
        config.periodic_callback = _BRIDGE_PERIODIC_CALLBACK


    def start_bridge_listener():
        # Listener binds localhost only, keeps the socket surface local.
        global BRIDGE_THREAD
        if BRIDGE_THREAD is not None:
            return
        if not BRIDGE_TOKEN:
            return

        BRIDGE_THREAD = threading.Thread(
            target=_listener_loop,
            args=(BRIDGE_HOST, BRIDGE_PORT, BRIDGE_TOKEN),
            daemon=True,
            name="renforge.bridge.listener",
        )
        BRIDGE_THREAD.start()
        _install_periodic_callback()


    def drain_bridge_commands():
        # In-game work must be done on the main thread.
        # Hook this function in Ren'Py via config.periodic_callback.
        commands = []
        while True:
            try:
                cmd = BRIDGE_COMMAND_QUEUE.get_nowait()
            except queue.Empty:
                break
            commands.append(cmd)

        return commands


    if BRIDGE_TOKEN:
        start_bridge_listener()
