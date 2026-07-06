import { useCallback, useEffect, useRef, useState } from "react";
import type { SocketEnvelope } from "../types";

const MAX_EVENTS = 300;

interface UseWebSocketOptions {
  enabled?: boolean;
  path?: string;
  onMessage?: (msg: SocketEnvelope) => void;
}

interface UseWebSocketState {
  messages: SocketEnvelope[];
  connected: boolean;
  connecting: boolean;
  error: string | null;
  reconnectAttempts: number;
  send: (payload: unknown) => void;
}

export function useWebSocket(options: UseWebSocketOptions = {}): UseWebSocketState {
  const { enabled = true, path = "/ws", onMessage } = options;
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<SocketEnvelope[]>([]);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const reconnectAttemptsRef = useRef(0);
  const connectTokenRef = useRef(0);

  const handleMessage = useCallback(
    (message: SocketEnvelope) => {
      setMessages((prev) => [...prev.slice(-MAX_EVENTS + 1), message]);
      onMessage?.(message);
    },
    [onMessage],
  );

  const disconnectSocket = useCallback(() => {
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch (_error) {
        // Ignore close errors; next connect cycle will replace this socket.
      }
      wsRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!enabled || !mountedRef.current) {
      return;
    }

    const connectToken = ++connectTokenRef.current;
    disconnectSocket();
    setConnecting(true);
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}${path}`;
    const socket = new WebSocket(url);
    wsRef.current = socket;

    socket.onopen = () => {
      if (connectToken !== connectTokenRef.current) {
        return;
      }
      setConnected(true);
      setConnecting(false);
      setError(null);
      reconnectAttemptsRef.current = 0;
      setReconnectAttempts(0);
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as SocketEnvelope;
        handleMessage(data);
      } catch (_error) {
        // Ignore malformed frames; keep transport healthy.
      }
    };

    socket.onerror = () => {
      setError("WebSocket error");
    };

    socket.onclose = () => {
      if (connectToken !== connectTokenRef.current) {
        return;
      }
      setConnected(false);
      setConnecting(false);

      if (!enabled || !mountedRef.current) {
        return;
      }

      const attempt = reconnectAttemptsRef.current + 1;
      reconnectAttemptsRef.current = attempt;
      setReconnectAttempts(attempt);
      const delay = Math.min(1200, 150 * attempt);
      reconnectRef.current = setTimeout(() => {
        connect();
      }, delay);
    };
  }, [enabled, disconnectSocket, handleMessage, path]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      connectTokenRef.current += 1;
      disconnectSocket();
    };
  }, [connect]);

  const send = useCallback((payload: unknown) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }, []);

  return {
    messages,
    connected,
    connecting,
    error,
    reconnectAttempts,
    send,
  };
}
