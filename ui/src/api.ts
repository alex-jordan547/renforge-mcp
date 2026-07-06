import type {
  AssetsResponse,
  CoverageResponse,
  FileContent,
  LintDiagnostic,
  LintResponse,
  LiveChoice,
  LiveScreenshot,
  LiveState,
  StoryMapResponse,
  TranslationStats,
} from "./types";

type JsonPayload = Record<string, unknown>;
type LanguageCandidate = Record<string, unknown>;

const API_BASE = "";
const JSON_HEADERS = { "Content-Type": "application/json" };

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
    size?: unknown;
    [key: string]: unknown;
  };

  return {
    path: typeof filePayload.path === "string" ? filePayload.path : path,
    content: typeof filePayload.content === "string" ? filePayload.content : "",
    size: typeof filePayload.size === "number" ? filePayload.size : undefined,
  };
}

export const api = {
  async fetchStoryMap(): Promise<StoryMapResponse> {
    return apiGet<StoryMapResponse>("/api/story-map");
  },

  async fetchLiveState(): Promise<LiveState> {
    return apiGet<LiveState>("/api/live/state");
  },

  async fetchLiveScreenshot(width = 680, height = 380): Promise<LiveScreenshot> {
    return apiPost<LiveScreenshot>("/api/screenshot", { width, height });
  },

  async fetchLiveChoices(): Promise<{ choices: LiveChoice[] }> {
    return apiGet<{ choices: LiveChoice[] }>("/api/live/choices");
  },

  async jumpToLabel(target: string): Promise<{ ok: boolean; error?: string }> {
    return apiPost<{ ok: boolean; error?: string }>("/api/warp", { target });
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

  async setVariable(name: string, value: string): Promise<{ ok: boolean }> {
    return apiPost<{ ok: boolean }>("/api/set-var", { name, value });
  },

  async selectChoice(index: number): Promise<{ ok: boolean; text: string }> {
    return apiPost<{ ok: boolean; text: string }>("/api/select-choice", { index });
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

  async fetchLint(): Promise<LintResponse> {
    const response = await apiGet<unknown>("/api/lint");
    return {
      diagnostics: toDiagnosticArray(response),
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
