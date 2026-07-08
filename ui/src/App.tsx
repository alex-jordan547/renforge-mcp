import { Component, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, getToken, normalizeTimelineEntries, socketMessageToTimeline } from "./api";
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

function timelineTime(item: TimelineItem): number {
  const parsed = Date.parse(item.timestamp);
  return Number.isFinite(parsed) ? parsed : 0;
}

function mergeTimelineItems(current: TimelineItem[], incoming: TimelineItem[]): TimelineItem[] {
  const byId = new Map<string, TimelineItem>();
  for (const item of [...current, ...incoming]) {
    if (!byId.has(item.id)) {
      byId.set(item.id, item);
    }
  }
  return [...byId.values()]
    .sort((a, b) => timelineTime(b) - timelineTime(a))
    .slice(0, 250);
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
    const next = socketMessageToTimeline(message);
    if (!next) {
      return;
    }
    setTimelineEvents((prev) => mergeTimelineItems(prev, [next]));
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

    const loadRecentTimeline = async () => {
      try {
        const seed = await api.fetchRecentTimeline();
        if (mounted && seed.length > 0) {
          setTimelineEvents((prev) => mergeTimelineItems(prev, normalizeTimelineEntries(seed)));
        }
      } catch (err) {
        console.error("Failed to load recent timeline seed in App", err);
      }
    };

    load();
    loadLive();
    loadRecentTimeline();

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
            currentLabel={liveState?.current_label ?? null}
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
        return <StoryMapPage data={storyMap} loading={storyMapLoading} error={storyMapError} onJump={handleJump} currentLabel={liveState?.current_label ?? null} />;
    }
  }, [activeSection, storyMap, storyMapLoading, storyMapError, handleJump, timelineEvents, liveState, liveFrame]);

  const SECTION_KEYS: Record<SectionId, string> = {
    "story-map": "S",
    live: "L",
    timeline: "T",
    assets: "A",
    translation: "R",
    diagnostics: "D",
    editor: "E",
    debugger: "B",
  };

  const SECTION_TITLES: Record<SectionId, [string, string]> = {
    "story-map": ["Story Map", "Graphe des labels et transitions"],
    live: ["Live", "Console opérationnelle — exécution en direct"],
    timeline: ["Timeline", "Historique des événements runtime"],
    assets: ["Assets", "Inventaire du projet Ren’Py"],
    translation: ["Translation", "Progression de traduction par langue"],
    diagnostics: ["Diagnostics", "Rapport lint et contrôles statiques"],
    editor: ["Editor", "Lecture du script, scope projet"],
    debugger: ["Debugger", "Contrôle runtime via bridge"],
  };

  const SECTION_ICONS: Record<SectionId, ReactNode> = {
    "story-map": (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="5" cy="12" r="2.4" />
        <circle cx="19" cy="6" r="2.4" />
        <circle cx="19" cy="18" r="2.4" />
        <path d="M7 11 17 6.8M7 13l10 4.2" />
      </svg>
    ),
    live: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="5" width="18" height="14" rx="2.5" />
        <path d="m10 9 5 3-5 3z" fill="currentColor" stroke="none" />
      </svg>
    ),
    timeline: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 8v4l3 2" />
        <circle cx="12" cy="12" r="9" />
      </svg>
    ),
    assets: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="m3 15 5-4 4 3 3-2 6 5" />
        <circle cx="8.5" cy="9.5" r="1.4" />
      </svg>
    ),
    translation: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 6h9M8 4v2c0 4-2 6-4 7M6 9c0 2 2 3.6 5 4" />
        <path d="m13 20 4-9 4 9M14.6 17h4.8" />
      </svg>
    ),
    diagnostics: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4M12 16h.01" />
      </svg>
    ),
    editor: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <path d="m8 8-4 4 4 4M16 8l4 4-4 4M13 5l-2 14" />
      </svg>
    ),
    debugger: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <rect x="8" y="6" width="8" height="12" rx="4" />
        <path d="M12 4v2M4 9h4M4 15h4M16 9h4M16 15h4M4 12h2M18 12h2" />
      </svg>
    ),
  };

  const [titleText, subText] = SECTION_TITLES[activeSection];

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="traffic" aria-hidden="true">
          <i></i>
          <i></i>
          <i></i>
        </div>
        <div className="brand">
          <div className="logo">
            <span className="mark">
              <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3 4 7v6c0 4.5 3.4 7.3 8 8 4.6-.7 8-3.5 8-8V7z" />
                <path d="m9 12 2 2 4-4" />
              </svg>
            </span>
            <span className="name">Renforge</span>
          </div>
          <div className="sub">Ren’Py runtime console</div>
        </div>

        <nav className="nav">
          <div className="nav-label">Atelier</div>
          {SECTIONS.slice(0, 5).map((item) => (
            <button
              key={item.id}
              className={`nav-btn ${activeSection === item.id ? "active" : ""}`}
              type="button"
              onClick={() => setActiveSection(item.id as SectionId)}
            >
              {SECTION_ICONS[item.id]}
              {item.label}
              <span className="kbd">{SECTION_KEYS[item.id]}</span>
            </button>
          ))}

          <div className="nav-label">Contrôle</div>
          {SECTIONS.slice(5).map((item) => (
            <button
              key={item.id}
              className={`nav-btn ${activeSection === item.id ? "active" : ""}`}
              type="button"
              onClick={() => setActiveSection(item.id as SectionId)}
            >
              {SECTION_ICONS[item.id]}
              {item.label}
              <span className="kbd">{SECTION_KEYS[item.id]}</span>
            </button>
          ))}
        </nav>

        <div className="side-foot">
          <div className="row">
            <span className="k">WS</span>
            <span className="ws">
              <span className="dot"></span>
              {stats.socket}
            </span>
          </div>
          <div className="row">
            <span className="k">nodes</span>
            <span className="v">{stats.nodeCount}</span>
          </div>
          <div className="row">
            <span className="k">edges</span>
            <span className="v">{stats.edgeCount}</span>
          </div>
          <div className="row">
            <span className="k">events</span>
            <span className="v">{stats.messageCount}</span>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="toolbar">
          <div className="title">
            <h1>{titleText}</h1>
            <p>{subText}</p>
          </div>
          <div className="pilot">
            <div className="label-chip">
              <span
                className="thumb"
                style={
                  liveFrame
                    ? { backgroundImage: `url(data:image/${liveFrame.format};base64,${liveFrame.base64})` }
                    : undefined
                }
              />
              <span>
                <span className="k">Label courant</span>
                <br />
                <span className="v">{liveState?.current_label || "—"}</span>
              </span>
            </div>

            <form
              className="warp"
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
                placeholder="Sauter au label…"
                aria-label="Sauter au label"
                type="text"
              />
              <button type="submit">Warp</button>
            </form>

            <button className="btn btn-primary" type="button" onClick={handleAdvance}>
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5v14l11-7z" />
              </svg>
              Advance
            </button>

            <button
              className="theme-toggle"
              type="button"
              onClick={() => setTheme((prev) => (prev === "light" ? "dark" : "light"))}
              aria-label="Basculer clair / sombre"
              title="Basculer clair / sombre"
            >
              <svg className="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
              </svg>
              <svg className="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </svg>
            </button>
          </div>
        </header>

        <main className="content">
          <DashboardErrorBoundary key={activeSection}>
            {dashboard}
          </DashboardErrorBoundary>
        </main>
      </div>
    </div>
  );
}
