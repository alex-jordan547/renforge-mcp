init python:
    # RenForge in-game bridge.
    #
    # Injected temporarily into <project>/game/ by the launcher and removed
    # afterwards. Opens a localhost TCP server (token-authenticated) so an
    # external client can inspect and drive the running game.
    #
    # Threading model: the socket listener runs on a background thread, but any
    # call into the Ren'Py API MUST happen on the main thread. Each request is
    # therefore handed to the main thread through a queue and executed inside a
    # `config.periodic_callbacks` drain; the listener thread blocks on a
    # per-request Event until the result is ready, then writes the reply.
    #
    # Configuration comes from the environment:
    #   RENFORGE_BRIDGE_TOKEN  required; the bridge stays off if unset
    #   RENFORGE_BRIDGE_HOST   default 127.0.0.1
    #   RENFORGE_BRIDGE_PORT   default 0 (an ephemeral port is chosen)
    # On startup the chosen host/port/token are published to
    #   <project>/.renforge/bridge.json
    # so the client can discover them.

    import base64
    import json
    import os
    import queue
    import socket
    import threading

    _RENFORGE_BRIDGE = None

    class _RenforgeRequest(object):
        # NB: no __slots__ — Ren'Py forbids slotted classes in init python
        # (they are incompatible with its rollback machinery).
        def __init__(self, command, payload):
            self.command = command
            self.payload = payload
            self.event = threading.Event()
            self.result = None
            self.error = None

    class _RenforgeBridge(object):
        def __init__(self, host, port, token, basedir):
            self.host = host
            self.port = port
            self.token = token
            self.basedir = basedir
            self.requests = queue.Queue()
            self.stop = threading.Event()
            self.thread = None
            self.current_label = None

    def _renforge_jsonable(value):
        """Best-effort conversion of a Python value to something JSON-safe."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [_renforge_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _renforge_jsonable(v) for k, v in value.items()}
        return repr(value)

    def _renforge_store_snapshot():
        snapshot = {}
        for name, value in list(vars(renpy.store).items()):
            if name.startswith("_"):
                continue
            if callable(value):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            snapshot[name] = value
        return snapshot

    # --- handlers: all run on the MAIN thread -----------------------------

    def _renforge_h_ping(payload):
        return {"ok": True, "pong": True}

    def _renforge_h_get_state(payload):
        try:
            showing = list(renpy.get_showing_tags())
        except Exception:
            showing = []
        try:
            screens = [s for s in renpy.display.screen.get_all_screen_names()] if hasattr(renpy.display, "screen") else []
        except Exception:
            screens = []
        return {
            "current_label": _RENFORGE_BRIDGE.current_label,
            "showing_tags": showing,
            "variables": _renforge_store_snapshot(),
        }

    def _renforge_h_eval(payload):
        expr = (payload or {}).get("expr", "")
        value = eval(expr, {"__builtins__": __builtins__}, vars(renpy.store))
        return {"expr": expr, "value": _renforge_jsonable(value)}

    def _renforge_h_get_var(payload):
        name = (payload or {}).get("name")
        return {"name": name, "value": _renforge_jsonable(getattr(renpy.store, name))}

    def _renforge_h_set_var(payload):
        name = (payload or {}).get("name")
        value = (payload or {}).get("value")
        setattr(renpy.store, name, value)
        return {"name": name, "value": value, "ok": True}

    def _renforge_h_screenshot(payload):
        payload = payload or {}
        width = int(payload.get("width", 0) or 0)
        height = int(payload.get("height", 0) or 0)
        size = (width, height) if (width and height) else None
        data = renpy.screenshot_to_bytes(size)  # PNG bytes
        return {"format": "png", "base64": base64.b64encode(data).decode("ascii")}

    _RENFORGE_HANDLERS = {
        "ping": _renforge_h_ping,
        "get_state": _renforge_h_get_state,
        "eval": _renforge_h_eval,
        "get_var": _renforge_h_get_var,
        "set_var": _renforge_h_set_var,
        "screenshot": _renforge_h_screenshot,
    }

    def renforge_drain_bridge():
        # Runs on the MAIN thread via config.periodic_callbacks.
        bridge = _RENFORGE_BRIDGE
        if bridge is None:
            return
        while True:
            try:
                req = bridge.requests.get_nowait()
            except queue.Empty:
                break
            handler = _RENFORGE_HANDLERS.get(req.command)
            try:
                if handler is None:
                    req.error = "unknown_command: %s" % req.command
                else:
                    req.result = handler(req.payload)
            except Exception as exc:
                req.error = "%s: %s" % (type(exc).__name__, exc)
            finally:
                req.event.set()

    # --- listener: background thread --------------------------------------

    def _renforge_reply(conn, obj):
        conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def _renforge_publish(bridge, port):
        try:
            out_dir = os.path.join(bridge.basedir, ".renforge")
            os.makedirs(out_dir, exist_ok=True)
            tmp = os.path.join(out_dir, "bridge.json.tmp")
            final = os.path.join(out_dir, "bridge.json")
            with open(tmp, "w") as fp:
                json.dump({"host": bridge.host, "port": port, "token": bridge.token}, fp)
            os.replace(tmp, final)
        except Exception:
            pass

    def _renforge_listener(bridge):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((bridge.host, bridge.port))
            bridge.port = server.getsockname()[1]
            setattr(renpy.store, "renforge_bridge_port", bridge.port)
            _renforge_publish(bridge, bridge.port)
            server.listen(5)

            while not bridge.stop.is_set():
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
                        msg = json.loads(line)
                    except ValueError:
                        _renforge_reply(conn, {"error": "invalid_json"})
                        continue
                    if msg.get("token") != bridge.token:
                        _renforge_reply(conn, {"error": "bad_token", "ok": False})
                        continue

                    req = _RenforgeRequest(msg.get("command"), msg.get("payload"))
                    bridge.requests.put(req)
                    if req.event.wait(timeout=15.0):
                        if req.error is not None:
                            _renforge_reply(conn, {"error": req.error})
                        else:
                            _renforge_reply(conn, req.result)
                    else:
                        _renforge_reply(conn, {"error": "timeout_waiting_for_main_thread"})
        finally:
            server.close()

    def renforge_start_bridge():
        global _RENFORGE_BRIDGE
        if _RENFORGE_BRIDGE is not None:
            return

        token = os.environ.get("RENFORGE_BRIDGE_TOKEN", "")
        token = "" if token is None else str(token).strip()
        if not token:
            return

        host = os.environ.get("RENFORGE_BRIDGE_HOST", "127.0.0.1")
        try:
            port = int(os.environ.get("RENFORGE_BRIDGE_PORT", "0") or "0")
        except (TypeError, ValueError):
            port = 0

        basedir = getattr(renpy.config, "basedir", "") or os.getcwd()
        bridge = _RenforgeBridge(host, port, token, basedir)
        _RENFORGE_BRIDGE = bridge

        def _renforge_on_label(name, abnormal):
            bridge.current_label = name

        renpy.config.label_callbacks.append(_renforge_on_label)
        renpy.config.periodic_callbacks.append(renforge_drain_bridge)

        thread = threading.Thread(
            target=_renforge_listener,
            args=(bridge,),
            daemon=True,
            name="renforge.bridge.listener",
        )
        bridge.thread = thread
        thread.start()

    renforge_start_bridge()
