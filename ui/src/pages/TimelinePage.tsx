import { useMemo, useState } from "react";
import type { TimelineItem } from "../types";

interface TimelinePageProps {
  items: TimelineItem[];
}

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

function formatTimestamp(timestamp: string): string {
  try {
    const date = new Date(timestamp);
    if (!Number.isFinite(date.getTime())) {
      return timestamp;
    }
    return timeFormatter.format(date);
  } catch {
    return timestamp;
  }
}

function timeAgo(timestamp: string): string {
  try {
    const diff = Date.now() - new Date(timestamp).getTime();
    if (diff < 0) return "";
    const sec = Math.floor(diff / 1000);
    if (sec < 5) return "just now";
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}min ago`;
    const hrs = Math.floor(min / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return "";
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function formatPayload(value: unknown): string {
  if (value === undefined) {
    return "None";
  }
  try {
    const text = JSON.stringify(value, null, 2) ?? String(value);
    return text.length > 4000 ? `${text.slice(0, 4000)}\n…` : text;
  } catch {
    return String(value);
  }
}

function activityFiles(payload: Record<string, unknown>): string[] {
  const raw = payload.files_touched ?? payload.files;
  return Array.isArray(raw) ? raw.filter((file): file is string => typeof file === "string") : [];
}

export function TimelinePage({ items }: TimelinePageProps) {
  const [search, setSearch] = useState("");
  const [showBridge, setShowBridge] = useState(true);
  const [showActivity, setShowActivity] = useState(true);
  const [expandedActivityId, setExpandedActivityId] = useState<string | null>(null);

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
      const matchText = `${item.type} ${item.title} ${item.details} ${JSON.stringify(item.payload ?? "")}`.toLowerCase();
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
        <span className="hint">bridge event stream</span>
      </div>

      <div className="tl-controls reveal in" style={{ animationDelay: ".05s" }}>
        <input
          className="input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search an event…"
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
        <span className="count">{filtered.length} / {items.length} events</span>
      </div>

      {filtered.length ? (
        <div className="feed">
          {filtered.map((item, index) => {
            const delay = `${Math.min(0.3, 0.08 + index * 0.04)}s`;
            
            const payload = item.source === "activity" ? asRecord(item.payload) : null;
            const files = payload ? activityFiles(payload) : [];
            const failed = item.level === "error" || payload?.ok === false || typeof asRecord(payload?.result)?.error === "string";
            const expanded = expandedActivityId === item.id;

            return (
              <div
                key={item.id}
                className={`ev ${item.source} ${item.level === "error" ? "error" : ""} reveal in`}
                style={{ animationDelay: delay }}
              >
                <div className="ev-card">
                  <div className="ev-main">
                    <div className="ev-time" title={item.timestamp}>
                      <time dateTime={item.timestamp}>{formatTimestamp(item.timestamp)}</time>
                      <span className="rel">{timeAgo(item.timestamp)}</span>
                    </div>
                    <div className="ev-name">{item.title}</div>
                    <div className="ev-meta">{item.details}</div>
                    {payload && (
                      <div className="activity-summary">
                        <span className={failed ? "activity-failure" : "activity-success"}>{failed ? "failed" : "ok"}</span>
                        {files.length > 0 && <span>{files.length} file{files.length === 1 ? "" : "s"} touched</span>}
                      </div>
                    )}
                  </div>
                  <span className={`tag-lg ${item.source}`}>{item.source.toUpperCase()}</span>
                  {payload && (
                    <button
                      type="button"
                      className="activity-toggle"
                      aria-expanded={expanded}
                      onClick={() => setExpandedActivityId((current) => current === item.id ? null : item.id)}
                    >
                      {expanded ? "Hide" : "Details"}
                    </button>
                  )}
                </div>
                {payload && expanded && (
                  <div className="activity-details">
                    <div>
                      <span>Parameters</span>
                      <pre>{formatPayload(payload.params)}</pre>
                    </div>
                    <div>
                      <span>Result</span>
                      <pre>{formatPayload(payload.result)}</pre>
                    </div>
                    {files.length > 0 && (
                      <div>
                        <span>Files touched</span>
                        <ul>{files.map((file) => <li key={file}>{file}</li>)}</ul>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="emptyState">
          <img className="emptyState-mascot" src="/brand/renforge-mascot.png" alt="" aria-hidden="true" />
          <h3>No events</h3>
          <p>Bridge and Ren'Py activity events will appear here in real time.</p>
        </div>
      )}
    </div>
  );
}
