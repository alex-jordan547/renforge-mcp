import { useMemo, useState } from "react";
import type { TimelineItem } from "../types";

interface TimelinePageProps {
  items: TimelineItem[];
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
        <span>{items.length} événements</span>
      </div>
      <div className="timelineTop">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="filtrer texte"
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
      </div>

      {filtered.length ? (
        <ul className="timelineList">
          {filtered.map((item) => (
            <li key={item.id} className={`timelineItem ${item.level || "info"}`}>
              <small>{item.timestamp}</small>
              <div className="titleRow">
                <strong>{item.title}</strong>
                <span>{item.source}</span>
              </div>
              <p>{item.details}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Aucun événement pour le moment.</p>
      )}
    </section>
  );
}
