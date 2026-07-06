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

  const handleMessage = useCallback(
    (message: SocketEnvelope) => {
      setMessages((prev) => [...prev.slice(-MAX_EVENTS + 1), message]);
      onMessage?.(message);
    },
    [onMessage],
  );

  const connect = useCallback(() => {
    if (!enabled || !mountedRef.current) {
      return;
    }

    setConnecting(true);
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}${path}`;
    const socket = new WebSocket(url);
    wsRef.current = socket;

    socket.onopen = () => {
      setConnected(true);
      setConnecting(false);
      setError(null);
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
      setConnected(false);
      setConnecting(false);

      if (!enabled || !mountedRef.current) {
        return;
      }

      const attempt = reconnectAttempts + 1;
      setReconnectAttempts(attempt);
      const delay = Math.min(1200, 150 * attempt);
      reconnectRef.current = setTimeout(() => {
        connect();
      }, delay);
    };
  }, [enabled, handleMessage, path, reconnectAttempts]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
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
