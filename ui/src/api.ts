import type {
  AssetsResponse,
  CoverageResponse,
  DebugBridgeEvent,
  DebugEventsResponse,
  FileContent,
  LintDiagnostic,
  LintResponse,
  LiveChoice,
  LiveScreenshot,
  LiveState,
  SocketEnvelope,
  TimelineItem,
  StoryMapResponse,
  TranslationStats,
} from "./types";

type JsonPayload = Record<string, unknown>;
type LanguageCandidate = Record<string, unknown>;

const API_BASE = "";
const JSON_HEADERS = { "Content-Type": "application/json" };
const TIMELINE_RECENT_PATH = "/api/timeline/recent";

function getToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return new URLSearchParams(window.location.search).get("token");
}

function withToken(path: string): string {
  if (typeof window === "undefined" || typeof window.location === "undefined") {
    return `${API_BASE}${path}`;
  }

  const url = new URL(path, window.location.origin);
  const token = getToken();
  if (token) {
    url.searchParams.set("token", token);
  }

  return `${API_BASE}${url.pathname}${url.search}`;
}

type BackendFailure = {
  ok: boolean;
  error?: string;
};

type LiveScreenshotPayload = {
  format?: string;
  base64?: string;
  width?: number;
  height?: number;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function safeRecord(value: unknown): Record<string, unknown> | null {
  return isObject(value) ? value : null;
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

function stableStringify(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((entry) => stableStringify(entry)).join(",")}]`;
  }

  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`)
    .join(",")}}`;
}

function compactIdPart(value: unknown): string {
  if (value === null || typeof value === "undefined") {
    return "";
  }
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return stableStringify(value);
}

function makeTimelineId(parts: unknown[]): string {
  const value = parts.map((part) => compactIdPart(part)).filter((part) => part.length > 0).join("|");
  return value.length > 0 ? value : "timeline";
}

function timelineFallbackId(message: SocketEnvelope): string {
  return makeTimelineId(["socket", message.kind, message.type, message.source, message.event, message.payload, message.timestamp]);
}

function isBackendFailure(payload: unknown): payload is BackendFailure {
  return (
    isObject(payload) &&
    payload.ok === false &&
    (typeof payload.error === "undefined" || typeof payload.error === "string")
  );
}

function extractError(payload: BackendFailure): string {
  return payload.error ?? "Unexpected response";
}

function parseTimelineSeedPayload(payload: unknown): SocketEnvelope[] {
  if (isBackendFailure(payload)) {
    throw new Error(extractError(payload));
  }
  if (Array.isArray(payload)) {
    return payload.filter(isObject) as SocketEnvelope[];
  }
  if (!isObject(payload)) {
    return [];
  }

  const candidate = payload as Record<string, unknown>;
  const arrays = [
    candidate.items,
    candidate.events,
    candidate.messages,
    candidate.timeline,
    candidate.activities,
    candidate.activity,
    candidate.data,
    candidate.payload,
  ];
  for (const entry of arrays) {
    if (Array.isArray(entry)) {
      return entry.filter(isObject) as SocketEnvelope[];
    }
  }

  return Object.keys(candidate).some((key) =>
    ["kind", "source", "type", "event", "payload", "timestamp"].includes(key),
  )
    ? [candidate as SocketEnvelope]
    : [];
}

function parseLiveStatePayload(payload: unknown): LiveState {
  if (isBackendFailure(payload)) {
    throw new Error(extractError(payload));
  }
  if (!isObject(payload)) {
    return { current_label: "", menu: false, showing_tags: [], variables: {} };
  }

  const candidate = payload as Record<string, unknown>;
  const tags = Array.isArray(candidate.showing_tags)
    ? candidate.showing_tags.filter((entry): entry is string => typeof entry === "string")
    : [];
  const variables = isObject(candidate.variables) ? (candidate.variables as Record<string, unknown>) : {};

  return {
    current_label: typeof candidate.current_label === "string" ? candidate.current_label : "",
    menu: candidate.menu === true,
    showing_tags: tags,
    variables,
  };
}

function parseLiveChoicesPayload(payload: unknown): { choices: LiveChoice[] } {
  if (isBackendFailure(payload)) {
    throw new Error(extractError(payload));
  }
  if (!isObject(payload)) {
    return { choices: [] };
  }

  const fromObject = isObject(payload.choices)
    ? (payload as { choices?: unknown }).choices
    : undefined;
  const source = Array.isArray(fromObject) ? fromObject : Array.isArray(payload) ? payload : [];

  return {
    choices: source
      .map((entry): LiveChoice | null => {
        if (!isObject(entry)) {
          return null;
        }
        const candidate = entry as {
          index?: unknown;
          text?: unknown;
          screen?: unknown;
        };
        if (typeof candidate.text !== "string") {
          return null;
        }
        const index =
          typeof candidate.index === "number"
            ? candidate.index
            : typeof candidate.index === "string" && /^\d+$/.test(candidate.index)
              ? Number.parseInt(candidate.index, 10)
              : 0;
        const parsed: LiveChoice = {
          index,
          text: candidate.text,
        };
        if (typeof candidate.screen === "string") {
          parsed.screen = candidate.screen;
        }
        return parsed;
      })
      .filter((entry): entry is LiveChoice => entry !== null),
  };
}

function parseDebugEventsPayload(payload: unknown): DebugEventsResponse {
  if (isBackendFailure(payload)) {
    throw new Error(extractError(payload));
  }
  if (!isObject(payload)) {
    return { ok: true, events: [] };
  }

  const events = Array.isArray(payload.events)
    ? payload.events.filter((entry): entry is DebugBridgeEvent => isObject(entry))
    : [];
  return {
    ok: payload.ok !== false,
    cursor: typeof payload.cursor === "number" ? payload.cursor : undefined,
    events,
    error: typeof payload.error === "string" ? payload.error : undefined,
  };
}

function parseScreenshotPayload(payload: unknown): LiveScreenshot {
  if (isBackendFailure(payload)) {
    throw new Error(extractError(payload));
  }
  if (!isObject(payload)) {
    throw new Error("Invalid screenshot payload");
  }

  const rawPayload = isObject(payload) ? (payload as Record<string, unknown>) : {};
  const candidate = (isObject(rawPayload.screenshot) ? rawPayload.screenshot : rawPayload) as LiveScreenshotPayload;
  if (typeof candidate.format !== "string" || typeof candidate.base64 !== "string") {
    throw new Error("Invalid screenshot payload");
  }

  return {
    format: candidate.format === "jpeg" ? "jpeg" : "png",
    base64: candidate.base64,
    width: typeof candidate.width === "number" ? candidate.width : undefined,
    height: typeof candidate.height === "number" ? candidate.height : undefined,
  };
}

function checkBooleanResponse(payload: unknown, action: string): void {
  if (!isObject(payload) || typeof payload.ok !== "boolean") {
    return;
  }
  if (payload.ok === false) {
    throw new Error((payload as { error?: string }).error ?? `${action} failed`);
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

async function apiGet<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(withToken(path), {
    credentials: "same-origin",
    ...options,
    headers: options?.headers ? options.headers : undefined,
    method: options?.method ?? "GET",
  });
  return parseResponse<T>(response);
}

async function apiPost<T>(path: string, body: JsonPayload): Promise<T> {
  const response = await fetch(withToken(path), {
    method: "POST",
    credentials: "same-origin",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
  return parseResponse<T>(response);
}

function toDiagnosticArray(payload: unknown): LintDiagnostic[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }

  if (
    "diagnostics" in payload &&
    Array.isArray((payload as { diagnostics?: unknown[] }).diagnostics)
  ) {
    return (payload as { diagnostics: unknown[] }).diagnostics.filter(
      (entry): entry is LintDiagnostic =>
        typeof entry === "object" &&
        entry !== null &&
        ("file" in entry || "line" in entry || "severity" in entry || "message" in entry),
    );
  }

  if (Array.isArray(payload)) {
    return payload.filter(
      (entry): entry is LintDiagnostic =>
        typeof entry === "object" &&
        entry !== null &&
        ("file" in entry || "line" in entry || "severity" in entry || "message" in entry),
    );
  }

  return [];
}

function normalizeLanguages(payload: unknown): string[] {
  if (!payload) {
    return [];
  }

  const arrayPayload = Array.isArray(payload)
    ? payload
    : "languages" in (payload as Record<string, unknown>)
      ? ((payload as { languages?: unknown[] }).languages ?? [])
      : "items" in (payload as Record<string, unknown>)
        ? ((payload as { items?: unknown[] }).items ?? [])
        : [];

  if (!Array.isArray(arrayPayload)) {
    return [];
  }

  return arrayPayload
    .map((entry) => {
      if (typeof entry === "string") {
        return entry.trim();
      }
      if (typeof entry === "number") {
        return String(entry);
      }
      if (entry && typeof entry === "object") {
        const candidate = entry as LanguageCandidate;
        if (typeof candidate.code === "string") {
          return candidate.code.trim();
        }
        if (typeof candidate.id === "string") {
          return candidate.id.trim();
        }
        if (typeof candidate.name === "string") {
          return candidate.name.trim();
        }
      }
      return "";
    })
    .filter((entry): entry is string => entry.length > 0);
}

function parseFileResponse(path: string, payload: unknown): FileContent {
  if (typeof payload === "string") {
    return { path, content: payload };
  }
  if (!payload || typeof payload !== "object") {
    return { path, content: "" };
  }

  const filePayload = payload as {
    path?: unknown;
    content?: unknown;
    text?: unknown;
    size?: unknown;
    [key: string]: unknown;
  };

  const content = typeof filePayload.text === "string" && filePayload.text.length > 0
    ? filePayload.text
    : typeof filePayload.content === "string"
      ? filePayload.content
      : "";

  return {
    path: typeof filePayload.path === "string" ? filePayload.path : path,
    content,
    size: typeof filePayload.size === "number" ? filePayload.size : undefined,
  };
}

export function socketMessageToTimeline(message: SocketEnvelope, fallbackAt?: string): TimelineItem | null {
  const kind = message.kind;
  const messageType = message.type;
  const event = safeRecord(message.payload) ?? safeRecord(message.event) ?? null;
  const messageTimestamp = toSafe(message.timestamp, fallbackAt ?? new Date().toISOString());
  const fallbackId = timelineFallbackId(message);

  const isActivity = kind === "activity" || messageType === "activity";
  if (isActivity && event) {
    const activity =
      event.type === "activity" && safeRecord(event.payload) ? (event.payload as Record<string, unknown>) : event;
    const activityTs = safeRecord(activity)?.["ts"] ?? safeRecord(activity)?.timestamp ?? messageTimestamp;
    const normalizedTimestamp = toSafe(activityTs, messageTimestamp);

    const tool = String(activity.tool ?? activity.name ?? "activity");
    const category = String(activity.category ?? "tool");
    const details = `Tool: ${tool} • Duration: ${String(activity.duration_ms ?? "n/a")}ms`;
    return {
      id: makeTimelineId(["activity", normalizedTimestamp, tool, category, activity, fallbackId]),
      source: "activity",
      timestamp: normalizedTimestamp,
      type: category,
      title: String(activity.name ?? "Tool call"),
      details,
      payload: activity,
      level: "info",
    };
  }

  const isBridge =
    kind === "bridge" || messageType === "state" || messageType === "event" || messageType === "screenshot";
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
      id: makeTimelineId(["bridge", toSafe(event.timestamp, messageTimestamp), eventType, event.label, fallbackId]),
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
      id: makeTimelineId(["bridge", toSafe(event.timestamp, messageTimestamp), eventType, event.what, fallbackId]),
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
      id: makeTimelineId(["bridge", toSafe(event.timestamp, messageTimestamp), eventType, event.full, event.short, fallbackId]),
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
    id: makeTimelineId(["bridge", toSafe(event.timestamp, messageTimestamp), eventType, event, fallbackId]),
    source: "bridge",
    timestamp: toSafe(event.timestamp, messageTimestamp),
    type: eventType,
    title: String(event.type ?? "Bridge event"),
    details: JSON.stringify(event),
    payload: event,
    level: "info",
  };
}

export function normalizeTimelineEntries(messages: SocketEnvelope[]): TimelineItem[] {
  return messages.map((message) => socketMessageToTimeline(message)).filter((entry): entry is TimelineItem => entry !== null);
}

export const api = {
  async fetchStoryMap(): Promise<StoryMapResponse> {
    return apiGet<StoryMapResponse>("/api/story-map");
  },

  async fetchLiveState(): Promise<LiveState> {
    const response = await apiGet<unknown>("/api/live/state");
    return parseLiveStatePayload(response);
  },

  async fetchLiveScreenshot(width = 680, height = 380): Promise<LiveScreenshot> {
    const response = await apiPost<unknown>("/api/screenshot", { width, height });
    return parseScreenshotPayload(response);
  },

  async fetchLiveChoices(): Promise<{ choices: LiveChoice[] }> {
    const response = await apiGet<unknown>("/api/live/choices");
    return parseLiveChoicesPayload(response);
  },

  async fetchDebugEvents(since = 0): Promise<DebugEventsResponse> {
    const response = await apiGet<unknown>(`/api/debug/events?since=${encodeURIComponent(String(since))}`);
    return parseDebugEventsPayload(response);
  },

  async fetchRecentTimeline(): Promise<SocketEnvelope[]> {
    const response = await apiGet<unknown>(TIMELINE_RECENT_PATH);
    return parseTimelineSeedPayload(response);
  },

  async jumpToLabel(target: string): Promise<{ ok: boolean; error?: string }> {
    const response = await apiPost<unknown>("/api/warp", { target });
    checkBooleanResponse(response, "Jump");
    return response as { ok: boolean; error?: string };
  },

  async advance(): Promise<{ ok: boolean }> {
    return apiPost<{ ok: boolean }>("/api/advance", {});
  },

  async screenshot(width = 680, height = 380): Promise<LiveScreenshot> {
    return apiPost<LiveScreenshot>("/api/screenshot", { width, height });
  },

  async evaluate(expr: string): Promise<{ expr: string; value: unknown }> {
    const response = await apiPost<{ ok: boolean; value: unknown; expr?: string }>("/api/eval", {
      expr,
    });
    return { expr: response.expr ?? expr, value: response.value };
  },

  async setVariable(name: string, value: unknown): Promise<{ ok: boolean }> {
    return apiPost<{ ok: boolean }>("/api/set-var", { name, value });
  },

  async selectChoice(index: number): Promise<{ ok: boolean; text: string }> {
    const response = await apiPost<unknown>("/api/select-choice", { index });
    checkBooleanResponse(response, "Choice");
    if (isObject(response)) {
      return {
        ok: true,
        text: typeof response.text === "string" ? response.text : "",
      };
    }
    return { ok: true, text: "" };
  },

  async fetchCoverage(): Promise<CoverageResponse> {
    return apiGet<CoverageResponse>("/api/coverage");
  },

  async fetchAssets(): Promise<AssetsResponse> {
    return apiGet<AssetsResponse>("/api/assets");
  },

  async fetchLanguages(): Promise<string[]> {
    const response = await apiGet<unknown>("/api/languages");
    return normalizeLanguages(response);
  },

  async fetchTranslationStats(language: string): Promise<TranslationStats> {
    return apiGet<TranslationStats>(`/api/translation-stats?language=${encodeURIComponent(language)}`);
  },

  async fetchTranslationStrings(language: string): Promise<{ ok: boolean; strings: any[] }> {
    return apiGet<{ ok: boolean; strings: any[] }>(`/api/translation-strings?language=${encodeURIComponent(language)}`);
  },

  async fetchLint(): Promise<LintResponse> {
    const response = await apiGet<unknown>("/api/lint");
    const payload = isObject(response) ? response : {};
    return {
      diagnostics: toDiagnosticArray(response),
      raw: typeof payload.raw === "string" ? payload.raw : undefined,
    };
  },

  async fetchFile(path: string): Promise<FileContent> {
    const response = await apiGet<unknown>(
      `/api/file?path=${encodeURIComponent(path)}`,
    );
    return parseFileResponse(path, response);
  },
};

export { getToken };
