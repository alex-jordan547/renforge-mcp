import { useCallback, useEffect, useMemo, useState } from "react";
import { api, getToken } from "./api";
import type { SocketEnvelope, StoryMapResponse, TimelineItem } from "./types";
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

function socketMessageToTimeline(message: SocketEnvelope, fallbackAt: string): TimelineItem | null {
  if (message.kind === "activity") {
    const payload = message.payload;
    const event = message.event ?? payload;
    if (typeof event !== "object" || event === null) {
      return null;
    }
    const activity = event as Record<string, unknown>;
    const tool = String(activity.tool ?? activity.name ?? "activity");
    const category = String(activity.category ?? "tool");
    const details = `Tool: ${tool} • Duration: ${String(activity.duration_ms ?? "n/a")}ms`;
    return {
      id: `${message.timestamp ?? fallbackAt}-${tool}`,
      source: "activity",
      timestamp: message.timestamp ?? fallbackAt,
      type: category,
      title: String(activity.name ?? "Tool call"),
      details,
      payload: activity,
      level: "info",
    };
  }

  const event = (message.payload ?? message.event) as Record<string, unknown> | undefined;
  if (!event) {
    return null;
  }

  if (message.type === "bridge" || typeof event.type === "string") {
    const eventType = String(event.type ?? message.type ?? "event");
    if (eventType === "label") {
      return {
        id: `${message.timestamp ?? fallbackAt}-${eventType}-${String(event.label ?? "")}`,
        source: "bridge",
        timestamp: String(event.timestamp ?? fallbackAt),
        type: eventType,
        title: "Label",
        details: `Entered ${String(event.label ?? "unknown")}`,
        payload: event,
        level: "info",
      };
    }
    if (eventType === "say") {
      return {
        id: `${message.timestamp ?? fallbackAt}-${eventType}-${String(event.what ?? "")}`,
        source: "bridge",
        timestamp: String(event.timestamp ?? fallbackAt),
        type: eventType,
        title: "Say",
        details: String(event.what ?? ""),
        payload: event,
        level: "info",
      };
    }
    if (eventType === "exception") {
      return {
        id: `${message.timestamp ?? fallbackAt}-${eventType}`,
        source: "bridge",
        timestamp: String(event.timestamp ?? fallbackAt),
        type: eventType,
        title: "Exception",
        details: String(event.full ?? event.short ?? "Runtime error"),
        payload: event,
        level: "error",
      };
    }
    return {
      id: `${message.timestamp ?? fallbackAt}-${eventType}-${String(eventType)}-${Math.random().toString(16).slice(2, 6)}`,
      source: "bridge",
      timestamp: String(event.timestamp ?? fallbackAt),
      type: eventType,
      title: String(event.type ?? "Bridge event"),
      details: JSON.stringify(event),
      payload: event,
      level: "info",
    };
  }

  return null;
}

export function App() {
  const [activeSection, setActiveSection] = useState<SectionId>("story-map");
  const [storyMap, setStoryMap] = useState<StoryMapResponse>({ nodes: [], edges: [] });
  const [storyMapLoading, setStoryMapLoading] = useState(true);
  const [storyMapError, setStoryMapError] = useState<string | null>(null);
  const [timelineEvents, setTimelineEvents] = useState<TimelineItem[]>([]);
  const token = getToken();

  const handleSocketMessage = useCallback((message: SocketEnvelope) => {
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
        return <LivePage />;
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
  }, [activeSection, storyMap, storyMapLoading, storyMapError, handleJump, timelineEvents]);

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
        {dashboard}
      </main>
    </div>
  );
}
