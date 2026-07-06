import { Component, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, getToken } from "./api";
import type { LiveScreenshot, LiveState, SocketEnvelope, StoryMapResponse, TimelineItem } from "./types";
import { useWebSocket } from "./hooks/useWebSocket";
import { AssetsPage } from "./pages/AssetsPage";
import { DebuggerPage } from "./pages/DebuggerPage";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { EditorPage } from "./pages/EditorPage";
import { LivePage } from "./pages/LivePage";
import { StoryMapPage } from "./pages/StoryMapPage";
import { TimelinePage } from "./pages/TimelinePage";
import { TranslationPage } from "./pages/TranslationPage";

const SECTIONS = [
  { id: "story-map", label: "Story Map" },
  { id: "live", label: "Live" },
  { id: "timeline", label: "Timeline" },
  { id: "assets", label: "Assets" },
  { id: "translation", label: "Translation" },
  { id: "diagnostics", label: "Diagnostics" },
  { id: "editor", label: "Editor" },
  { id: "debugger", label: "Debugger" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: string | null;
}

class DashboardErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: unknown) {
    return {
      hasError: true,
      error: error instanceof Error ? error.message : "Une erreur est survenue",
    };
  }

  componentDidCatch(_error: unknown, _info: unknown) {
    // Keep section-level errors from collapsing the whole dashboard.
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="errorBoundaryPanel">
          <h3>Erreur de section</h3>
          <p className="muted">{this.state.error}</p>
          <p className="muted">Cette section a été isolée pour préserver l’application.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

function safeRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function socketMessageToTimeline(message: SocketEnvelope, fallbackAt: string): TimelineItem | null {
  const kind = message.kind;
  const messageType = message.type;
  const event =
    safeRecord(message.payload) ??
    safeRecord(message.event) ??
    null;
  const messageTimestamp = toSafe(message.timestamp, fallbackAt);

  const isActivity = kind === "activity" || messageType === "activity";
  if (isActivity && event) {
    const activity = event.type === "activity" && safeRecord(event.payload) ? (event.payload as Record<string, unknown>) : event;
    const activityTs =
      safeRecord(activity)?.["ts"] ??
      safeRecord(activity)?.timestamp ??
      messageTimestamp;
    const normalizedTimestamp = toSafe(activityTs, messageTimestamp);

    const tool = String(activity.tool ?? activity.name ?? "activity");
    const category = String(activity.category ?? "tool");
    const details = `Tool: ${tool} • Duration: ${String(activity.duration_ms ?? "n/a")}ms`;
    return {
      id: `${normalizedTimestamp}-${tool}`,
      source: "activity",
      timestamp: normalizedTimestamp,
      type: category,
      title: String(activity.name ?? "Tool call"),
      details,
      payload: activity,
      level: "info",
    };
  }

  const isBridge = kind === "bridge" || messageType === "state" || messageType === "event" || messageType === "screenshot";
  if (!isBridge || !event) {
    return null;
  }

  const eventType = String(event.type ?? messageType ?? "event");
  // State snapshots and screenshot frames drive the Live view, not the
  // Timeline — keeping them out avoids flooding it with base64 blobs.
  if (eventType === "state" || eventType === "screenshot") {
    return null;
  }
  if (eventType === "label") {
    return {
      id: `${toSafe(event.timestamp, messageTimestamp)}-${eventType}-${String(event.label ?? "")}`,
      source: "bridge",
      timestamp: toSafe(event.timestamp, messageTimestamp),
      type: eventType,
      title: "Label",
      details: `Entered ${String(event.label ?? "unknown")}`,
      payload: event,
      level: "info",
    };
  }
  if (eventType === "say") {
    return {
      id: `${toSafe(event.timestamp, messageTimestamp)}-${eventType}-${String(event.what ?? "")}`,
      source: "bridge",
      timestamp: toSafe(event.timestamp, messageTimestamp),
      type: eventType,
      title: "Say",
      details: String(event.what ?? ""),
      payload: event,
      level: "info",
    };
  }
  if (eventType === "exception") {
    return {
      id: `${toSafe(event.timestamp, messageTimestamp)}-${eventType}`,
      source: "bridge",
      timestamp: toSafe(event.timestamp, messageTimestamp),
      type: eventType,
      title: "Exception",
      details: String(event.full ?? event.short ?? "Runtime error"),
      payload: event,
      level: "error",
    };
  }

  return {
    id: `${toSafe(event.timestamp, messageTimestamp)}-${eventType}-${Math.random().toString(16).slice(2, 6)}`,
    source: "bridge",
    timestamp: toSafe(event.timestamp, messageTimestamp),
    type: eventType,
    title: String(event.type ?? "Bridge event"),
    details: JSON.stringify(event),
    payload: event,
    level: "info",
  };
}

function toSafe(value: string | number | unknown, fallback: string = new Date().toISOString()): string {
  if (typeof value === "string" && value.length > 0) {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toISOString();
    }
  }
  return fallback;
}

export function App() {
  const [activeSection, setActiveSection] = useState<SectionId>("story-map");
  const [storyMap, setStoryMap] = useState<StoryMapResponse>({ nodes: [], edges: [] });
  const [storyMapLoading, setStoryMapLoading] = useState(true);
  const [storyMapError, setStoryMapError] = useState<string | null>(null);
  const [timelineEvents, setTimelineEvents] = useState<TimelineItem[]>([]);
  const [liveState, setLiveState] = useState<LiveState | null>(null);
  const [liveFrame, setLiveFrame] = useState<LiveScreenshot | null>(null);
  const token = getToken();

  const handleSocketMessage = useCallback((message: SocketEnvelope) => {
    // Route live frames to the Live view and narrative events to the Timeline.
    if (message.type === "state" && message.payload) {
      setLiveState(message.payload as unknown as LiveState);
    } else if (message.type === "screenshot" && message.payload) {
      setLiveFrame(message.payload as unknown as LiveScreenshot);
    }
    const next = socketMessageToTimeline(message, new Date().toISOString());
    if (!next) {
      return;
    }
    setTimelineEvents((prev) => [next, ...prev].slice(0, 250));
  }, []);

  const wsPath = token ? `/ws?token=${encodeURIComponent(token)}` : "/ws";
  const ws = useWebSocket({ path: wsPath, onMessage: handleSocketMessage });

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setStoryMapLoading(true);
      try {
        const map = await api.fetchStoryMap();
        if (mounted) {
          setStoryMap(map);
          setStoryMapError(null);
        }
      } catch (error) {
        if (mounted) {
          setStoryMapError(error instanceof Error ? error.message : "Failed to load story map");
        }
      } finally {
        if (mounted) {
          setStoryMapLoading(false);
        }
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, []);

  const handleJump = useCallback(async (target: string) => {
    try {
      await api.jumpToLabel(target);
      setTimelineEvents((prev) => {
        const item: TimelineItem = {
          id: `${Date.now()}-jump-${target}`,
          source: "ui",
          timestamp: new Date().toISOString(),
          type: "ui",
          title: "Jump",
          details: `Requested jump to ${target}`,
          level: "info",
          payload: { target },
        };
        return [item, ...prev].slice(0, 250);
      });
    } catch (error) {
      setTimelineEvents((prev) => {
        const item: TimelineItem = {
          id: `${Date.now()}-jump-fail`,
          source: "ui",
          timestamp: new Date().toISOString(),
          type: "ui",
          title: "Jump",
          details: error instanceof Error ? error.message : "Jump failed",
          level: "error",
          payload: { target },
        };
        return [item, ...prev].slice(0, 250);
      });
    }
  }, []);

  const stats = useMemo(
    () => ({
      socket: ws.connected ? "connected" : ws.connecting ? "connecting" : "offline",
      nodeCount: storyMap.nodes.length,
      edgeCount: storyMap.edges.length,
      messageCount: timelineEvents.length,
    }),
    [ws.connected, ws.connecting, storyMap.edges.length, storyMap.nodes.length, timelineEvents.length],
  );

  const dashboard = useMemo(() => {
    switch (activeSection) {
      case "story-map":
        return (
          <StoryMapPage
            data={storyMap}
            loading={storyMapLoading}
            error={storyMapError}
            onJump={handleJump}
          />
        );
      case "live":
        return <LivePage liveState={liveState} liveFrame={liveFrame} />;
      case "timeline":
        return <TimelinePage items={timelineEvents} />;
      case "assets":
        return <AssetsPage />;
      case "translation":
        return <TranslationPage />;
      case "diagnostics":
        return <DiagnosticsPage />;
      case "editor":
        return <EditorPage />;
      case "debugger":
        return <DebuggerPage />;
      default:
        return <StoryMapPage data={storyMap} loading={storyMapLoading} error={storyMapError} onJump={handleJump} />;
    }
  }, [activeSection, storyMap, storyMapLoading, storyMapError, handleJump, timelineEvents, liveState, liveFrame]);

  return (
    <div className="dashboard">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-name">RenForge</span>
          <small>V2 Dashboard</small>
        </div>
        <nav aria-label="Sections">
          {SECTIONS.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${activeSection === item.id ? "active" : ""}`}
              type="button"
              onClick={() => setActiveSection(item.id as SectionId)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="statusCard">
          <p className="statusTitle">État</p>
          <p>WS: {stats.socket}</p>
          <p>Nodes: {stats.nodeCount}</p>
          <p>Edges: {stats.edgeCount}</p>
          <p>Events: {stats.messageCount}</p>
          {ws.error ? <p className="errorText">WS: {ws.error}</p> : null}
        </div>
      </aside>

      <main className="content">
        <header className="topbar">
          <h1>RenForge Dashboard</h1>
          <p className="topbarSub">Vue opérationnelle des données live, carte narrative et activité IA.</p>
        </header>
        <DashboardErrorBoundary key={activeSection}>
          {dashboard}
        </DashboardErrorBoundary>
      </main>
    </div>
  );
}
