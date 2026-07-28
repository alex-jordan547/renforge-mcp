import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TimelineItem } from "../types";

interface TimelinePageProps {
  items: TimelineItem[];
}

type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

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

function timeAgo(timestamp: string, t: TranslateFn): string {
  try {
    const diff = Date.now() - new Date(timestamp).getTime();
    if (diff < 0) return "";
    const sec = Math.floor(diff / 1000);
    if (sec < 5) return t("pages.timeline.timeAgo.justNow");
    if (sec < 60) return t("pages.timeline.timeAgo.seconds", { count: sec });
    const min = Math.floor(sec / 60);
    if (min < 60) return t("pages.timeline.timeAgo.minutes", { count: min });
    const hrs = Math.floor(min / 60);
    if (hrs < 24) return t("pages.timeline.timeAgo.hours", { count: hrs });
    return t("pages.timeline.timeAgo.days", { count: Math.floor(hrs / 24) });
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
  const { t } = useTranslation();
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
        <h2>{t("pages.timeline.title")}</h2>
        <span className="hint">{t("pages.timeline.hint")}</span>
      </div>

      <div className="tl-controls reveal in" style={{ animationDelay: ".05s" }}>
        <input
          className="input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("pages.timeline.searchPlaceholder")}
        />
        <div className="filters">
          <button
            className="chip"
            aria-pressed={showBridge}
            onClick={() => setShowBridge((prev) => !prev)}
          >
            <span className="dot" style={{ background: "var(--meta)" }} />
            {t("pages.timeline.sourceBridge")} <span className="n">{sources.bridge}</span>
          </button>
          <button
            className="chip"
            aria-pressed={showActivity}
            onClick={() => setShowActivity((prev) => !prev)}
          >
            <span className="dot" style={{ background: "var(--accent)" }} />
            {t("pages.timeline.sourceActivity")} <span className="n">{sources.activity}</span>
          </button>
        </div>
        <span className="count">
          {t("pages.timeline.filteredEvents", { count: filtered.length, visible: filtered.length, total: items.length })}
        </span>
      </div>

      {filtered.length ? (
        <div className="feed">
          {filtered.map((item, index) => {
            const delay = `${Math.min(0.3, 0.08 + index * 0.04)}s`;
            
              const payload = item.source === "activity" ? asRecord(item.payload) : null;
            const files = payload ? activityFiles(payload) : [];
            const failed = item.level === "error" || payload?.ok === false || typeof asRecord(payload?.result)?.error === "string";
            const failedStatusClass = "activity-failure";
            const successStatusClass = "activity-success";
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
                      <span className="rel">{timeAgo(item.timestamp, t)}</span>
                    </div>
                    <div className="ev-name">{item.title}</div>
                    <div className="ev-meta">{item.details}</div>
                    {payload && (
                      <div className="activity-summary">
                        <span className={failed ? failedStatusClass : successStatusClass}>
                          {failed ? t("status.failed") : t("status.ok")}
                        </span>
                        {files.length > 0 && <span>{t("pages.timeline.filesTouched", { count: files.length })}</span>}
                      </div>
                    )}
                  </div>
                  <span className={`tag-lg ${item.source}`}>
                    {item.source === "bridge"
                      ? t("pages.timeline.badge.bridge")
                      : item.source === "ui"
                        ? t("pages.timeline.badge.ui")
                        : t("pages.timeline.badge.activity")}
                  </span>
                  {payload && (
                    <button
                      type="button"
                      className="activity-toggle"
                      aria-expanded={expanded}
                      onClick={() => setExpandedActivityId((current) => current === item.id ? null : item.id)}
                    >
                      {expanded ? t("pages.timeline.hideDetails") : t("pages.timeline.showDetails")}
                    </button>
                  )}
                </div>
                {payload && expanded && (
                  <div className="activity-details">
                    <div>
                      <span>{t("pages.timeline.parameters")}</span>
                      <pre>{formatPayload(payload.params)}</pre>
                    </div>
                    <div>
                      <span>{t("pages.timeline.result")}</span>
                      <pre>{formatPayload(payload.result)}</pre>
                    </div>
                    {files.length > 0 && (
                      <div>
                        <span>{t("pages.timeline.filesTouchedHeader")}</span>
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
          <h3>{t("pages.timeline.emptyTitle")}</h3>
          <p>{t("pages.timeline.emptyDescription")}</p>
        </div>
      )}
    </div>
  );
}
