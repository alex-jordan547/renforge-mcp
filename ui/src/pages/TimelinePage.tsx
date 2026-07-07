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

function sourceBadgeClass(source: string): string {
  const s = source.toLowerCase();
  if (s === "bridge") return "sourceBadge source-bridge";
  if (s === "activity") return "sourceBadge source-activity";
  if (s === "error") return "sourceBadge source-error";
  if (s === "ui") return "sourceBadge source-ui";
  return "sourceBadge";
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
    <section className="panel">
      <div className="panelHeader">
        <h2>Timeline</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Rechercher…"
            style={{ maxWidth: 220, fontSize: "0.85rem" }}
          />
          <div className="toggleGroup">
            <label>
              <input
                type="checkbox"
                checked={showBridge}
                onChange={(event) => setShowBridge(event.target.checked)}
              />
              Bridge ({sources.bridge})
            </label>
            <label>
              <input
                type="checkbox"
                checked={showActivity}
                onChange={(event) => setShowActivity(event.target.checked)}
              />
              Activity ({sources.activity})
            </label>
          </div>
          <span style={{ color: "var(--muted)", fontSize: "0.82rem", whiteSpace: "nowrap" }}>
            {filtered.length} / {items.length} événements
          </span>
        </div>
      </div>

      {filtered.length ? (
        <ul className="timelineList">
          {filtered.map((item) => (
            <li key={item.id} className={`timelineItem ${item.level || "info"}`}>
              <small>
                {item.timestamp}
                <span className="timeAgo">{timeAgo(item.timestamp)}</span>
              </small>
              <div className="titleRow">
                <strong>{item.title}</strong>
                <span className={sourceBadgeClass(item.source)}>{item.source}</span>
              </div>
              <p>{item.details}</p>
            </li>
          ))}
        </ul>
      ) : (
        <div className="emptyState">
          <div className="emptyState-icon">📭</div>
          <h3>Aucun événement</h3>
          <p>Les événements du bridge et de l'activité Ren'Py apparaîtront ici en temps réel.</p>
        </div>
      )}
    </section>
  );
}
