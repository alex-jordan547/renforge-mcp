import { useMemo, useState } from "react";
import type { TimelineItem } from "../types";

interface TimelinePageProps {
  items: TimelineItem[];
}

function timeAgo(timestamp: string): string {
  try {
    const diff = Date.now() - new Date(timestamp).getTime();
    if (diff < 0) return "";
    const sec = Math.floor(diff / 1000);
    if (sec < 5) return "à l'instant";
    if (sec < 60) return `il y a ${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `il y a ${min}min`;
    const hrs = Math.floor(min / 60);
    if (hrs < 24) return `il y a ${hrs}h`;
    return `il y a ${Math.floor(hrs / 24)}j`;
  } catch {
    return "";
  }
}

export function TimelinePage({ items }: TimelinePageProps) {
  const [search, setSearch] = useState("");
  const [showBridge, setShowBridge] = useState(true);
  const [showActivity, setShowActivity] = useState(true);

  const sources = useMemo(
    () => ({
      bridge: items.filter((item) => item.source === "bridge").length,
      activity: items.filter((item) => item.source === "activity").length,
    }),
    [items],
  );

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return items.filter((item) => {
      const matchText = `${item.type} ${item.title} ${item.details}`.toLowerCase();
      if (term && !matchText.includes(term)) {
        return false;
      }
      if (!showBridge && item.source === "bridge") {
        return false;
      }
      if (!showActivity && item.source === "activity") {
        return false;
      }
      return true;
    });
  }, [items, search, showActivity, showBridge]);

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Timeline</h2>
        <span className="hint">flux d’événements du bridge</span>
      </div>

      <div className="tl-controls reveal in" style={{ animationDelay: ".05s" }}>
        <input
          className="input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Rechercher un événement…"
        />
        <div className="filters">
          <button
            className="chip"
            aria-pressed={showBridge}
            onClick={() => setShowBridge((prev) => !prev)}
          >
            <span className="dot" style={{ background: "var(--meta)" }} />
            Bridge <span className="n">{sources.bridge}</span>
          </button>
          <button
            className="chip"
            aria-pressed={showActivity}
            onClick={() => setShowActivity((prev) => !prev)}
          >
            <span className="dot" style={{ background: "var(--accent)" }} />
            Activity <span className="n">{sources.activity}</span>
          </button>
        </div>
        <span className="count">{filtered.length} / {items.length} événements</span>
      </div>

      {filtered.length ? (
        <div className="feed">
          {filtered.map((item, index) => {
            const delay = `${Math.min(0.3, 0.08 + index * 0.04)}s`;
            
            // Format details to match mockup if duration is present
            let detailNode = <>{item.details}</>;
            if (item.source === "activity" && item.payload && typeof item.payload === "object") {
              const payload = item.payload as Record<string, unknown>;
              if (payload.tool || payload.duration_ms) {
                detailNode = (
                  <>
                    Tool <b>{String(payload.tool ?? payload.name ?? "activity")}</b> · Duration <b>{String(payload.duration_ms ?? "n/a")} ms</b>
                  </>
                );
              }
            }

            return (
              <div
                key={item.id}
                className={`ev ${item.source} reveal in`}
                style={{ animationDelay: delay }}
              >
                <div className="ev-card">
                  <div className="ev-main">
                    <div className="ev-time">
                      {item.timestamp}
                      <span className="rel">{timeAgo(item.timestamp)}</span>
                    </div>
                    <div className="ev-name">{item.title}</div>
                    <div className="ev-meta">
                      {detailNode}
                    </div>
                  </div>
                  <span className={`tag-lg ${item.source}`}>{item.source.toUpperCase()}</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="emptyState">
          <div className="emptyState-icon">📭</div>
          <h3>Aucun événement</h3>
          <p>Les événements du bridge et de l'activité Ren'Py apparaîtront ici en temps réel.</p>
        </div>
      )}
    </div>
  );
}
