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
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("renforge-theme") as "light" | "dark") || "light",
  );
  const [storyMap, setStoryMap] = useState<StoryMapResponse>({ nodes: [], edges: [] });
  const [storyMapLoading, setStoryMapLoading] = useState(true);
  const [storyMapError, setStoryMapError] = useState<string | null>(null);
  const [timelineEvents, setTimelineEvents] = useState<TimelineItem[]>([]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("renforge-theme", theme);
  }, [theme]);
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

    const loadLive = async () => {
      try {
        const state = await api.fetchLiveState();
        if (mounted) {
          setLiveState(state);
        }
        const frame = await api.fetchLiveScreenshot().catch(() => null);
        if (mounted && frame) {
          setLiveFrame(frame);
        }
      } catch (err) {
        console.error("Failed to load initial live state in App", err);
      }
    };

    load();
    loadLive();

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

  const handleAdvance = useCallback(async () => {
    try {
      await api.advance();
      setTimelineEvents((prev) => {
        const item: TimelineItem = {
          id: `${Date.now()}-advance`,
          source: "ui",
          timestamp: new Date().toISOString(),
          type: "ui",
          title: "Advance",
          details: "Requested story advancement from Command Center",
          level: "info",
        };
        return [item, ...prev].slice(0, 250);
      });
    } catch (error) {
      setTimelineEvents((prev) => {
        const item: TimelineItem = {
          id: `${Date.now()}-advance-fail`,
          source: "ui",
          timestamp: new Date().toISOString(),
          type: "ui",
          title: "Advance Failed",
          details: error instanceof Error ? error.message : "Advance failed",
          level: "error",
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

  const SECTION_ICONS: Record<SectionId, ReactNode> = {
    "story-map": (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M19 15c-1.1 0-2 .9-2 2H7c0-1.1-.9-2-2-2s-2 .9-2 2s.9 2 2 2s2-.9 2-2h10c0 1.1.9 2 2 2s2-.9 2-2s-.9-2-2-2zM7 9c0 1.1.9 2 2 2h6c1.1 0 2-.9 2-2s-.9-2-2-2H9c-1.1 0-2 .9-2 2z" />
      </svg>
    ),
    live: (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M21 3H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h5v2h8v-2h5c1.1 0 1.99-.9 1.99-2L23 5c0-1.1-.9-2-2-2zm-1 14H4V5h16v12zm-10-2l6-4l-6-4v8z" />
      </svg>
    ),
    timeline: (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89l.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7s-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42C8.27 19.99 10.51 21 13 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54l.72-1.21l-3.5-2.08V8H12z" />
      </svg>
    ),
    assets: (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-8 7H4V8h8v5z" />
      </svg>
    ),
    translation: (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M12.87 15.07l-2.54-2.51l.03-.03c1.74-1.94 2.98-4.17 3.71-6.53H17V4h-7V2H8v2H1v2h11.17C11.5 7.92 10.44 9.75 9 11.35C8.07 10.32 7.3 9.19 6.69 8h-2c.73 1.63 1.73 3.17 2.98 4.56l-5.09 5.02L4 19l5-5l3.11 3.11l.76-2.04zM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5-12zm-2.62 7l1.62-4.33L19.12 17h-3.24z" />
      </svg>
    ),
    diagnostics: (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10s10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" />
      </svg>
    ),
    editor: (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6l6 6l1.4-1.4zm5.2 0l4.6-4.6l-4.6-4.6L16 6l6 6l-6 6l-1.4-1.4z" />
      </svg>
    ),
    debugger: (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" d="M20 8h-2.81c-.45-.78-1.07-1.45-1.82-1.96L17 4.41L15.59 3l-2.17 2.17a6.002 6.002 0 0 0-2.83 0L8.41 3L7 4.41l1.62 1.63C7.88 6.55 7.26 7.22 6.81 8H4v2h2.09c-.05.33-.09.66-.09 1v1H4v2h2v1c0 .34.04.67.09 1H4v2h2.81c1.04 1.79 2.97 3 5.19 3s4.15-1.21 5.19-3H20v-2h-2.09c.05-.33.09-.66.09-1v-1h2v-2h-2v-1c0-.34-.04-.67-.09-1H20V8zm-6 8h-4v-2h4v2zm0-4h-4v-2h4v2z" />
      </svg>
    ),
  };

  return (
    <div className="dashboard">
      <aside className="sidebar">
        <div className="brand" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <span className="brand-name">RenForge</span>
            <small>V2 Dashboard</small>
          </div>
          <button 
            className="theme-toggle-btn" 
            type="button" 
            onClick={() => setTheme((prev) => (prev === "light" ? "dark" : "light"))}
            title={theme === "light" ? "Passer au thème sombre" : "Passer au thème clair"}
          >
            {theme === "light" ? (
              <svg viewBox="0 0 24 24">
                <path fill="currentColor" d="M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9s9-4.03 9-9c0-.46-.04-.92-.1-1.36a.994.994 0 0 0-1.11-.8c-.89.12-1.78-.12-2.52-.66a5.008 5.008 0 0 1-2.02-3.82c0-1.74.88-3.32 2.37-4.22c.38-.23.54-.7.38-1.1A9.097 9.097 0 0 0 12 3z" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24">
                <path fill="currentColor" d="M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5s5-2.24 5-5s-2.24-5-5-5zM2 13h2c.55 0 1-.45 1-1s-.45-1-1-1H2c-.55 0-1 .45-1 1s.45 1 1 1zm18 0h2c.55 0 1-.45 1-1s-.45-1-1-1h-2c-.55 0-1 .45-1 1s.45 1 1 1zM11 2v2c0 .55.45 1 1 1s1-.45 1-1V2c0-.55-.45-1-1-1s-1 .45-1 1zm0 18v2c0 .55.45 1 1 1s1-.45 1-1v-2c0-.55-.45-1-1-1s-1 .45-1 1zM5.99 4.58a.996.996 0 0 0-1.41 0a.996.996 0 0 0 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0s.39-1.03 0-1.41L5.99 4.58zm12.37 12.37a.996.996 0 0 0-1.41 0a.996.996 0 0 0 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0s.39-1.03 0-1.41l-1.06-1.06zm-1.06-11.3a.996.996 0 0 0 0-1.41a.996.996 0 0 0-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06zm-11.3 11.3a.996.996 0 0 0 0-1.41a.996.996 0 0 0-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06z" />
              </svg>
            )}
          </button>
        </div>
        <nav aria-label="Sections">
          {SECTIONS.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${activeSection === item.id ? "active" : ""}`}
              type="button"
              onClick={() => setActiveSection(item.id as SectionId)}
            >
              {SECTION_ICONS[item.id]}
              {item.label}
            </button>
          ))}
        </nav>
        <div className="statusCard">
          <p className="statusTitle">État</p>
          <div className="statusRow">
            <strong>WS:</strong>
            <span className="wsIndicator">
              <span className={`wsDot ${stats.socket}`} />
              {stats.socket}
            </span>
          </div>
          <div className="statusRow">
            <strong>Nodes:</strong>
            <span>{stats.nodeCount}</span>
          </div>
          <div className="statusRow">
            <strong>Edges:</strong>
            <span>{stats.edgeCount}</span>
          </div>
          <div className="statusRow">
            <strong>Events:</strong>
            <span>{stats.messageCount}</span>
          </div>
          {ws.error ? <div className="statusRow errorText" style={{ marginTop: 8 }}><strong>Err:</strong> <span>{ws.error}</span></div> : null}
        </div>
      </aside>

      <main className="content">
        <header className="topbar">
          <div className="topbar-info">
            <h1>RenForge Dashboard</h1>
            <p className="topbarSub" style={{ margin: 0 }}>Console opérationnelle unifiée</p>
          </div>
          
          <div className="topbar-hud">
            {liveFrame && (
              <div className="hud-thumbnail-container" title="Survolez pour agrandir">
                <img
                  className="hud-thumbnail"
                  src={`data:image/${liveFrame.format};base64,${liveFrame.base64}`}
                  alt="Aperçu live"
                />
                <div className="hud-preview-popover">
                  <img
                    src={`data:image/${liveFrame.format};base64,${liveFrame.base64}`}
                    alt="Aperçu live grand"
                  />
                </div>
              </div>
            )}
            
            <div className="hud-label">
              Label courant
              <span>{liveState?.current_label || "—"}</span>
            </div>

            <div className="hud-actions">
              <button className="btn small primary" type="button" onClick={handleAdvance} title="Avancer dans le jeu">
                Advance
              </button>
              
              <form 
                className="hud-warp-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  const formData = new FormData(e.currentTarget);
                  const target = formData.get("warpTarget") as string;
                  if (target?.trim()) {
                    handleJump(target.trim());
                    e.currentTarget.reset();
                  }
                }}
              >
                <input
                  name="warpTarget"
                  className="hud-warp-input"
                  placeholder="Sauter au label..."
                  type="text"
                />
                <button className="btn small" type="submit">Warp</button>
              </form>
            </div>
          </div>
        </header>
        
        <DashboardErrorBoundary key={activeSection}>
          {dashboard}
        </DashboardErrorBoundary>
      </main>
    </div>
  );
}





