import i18next from "../i18n";

type JsonRecord = Record<string, unknown>;

export interface DashboardApiError {
  readonly ok: false;
  readonly error_code?: string;
  readonly details?: JsonRecord;
  readonly error?: string;
}

export class DashboardApiErrorImpl extends Error implements DashboardApiError {
  readonly ok = false as const;
  readonly error_code?: string;
  readonly details?: JsonRecord;
  readonly error?: string;

  constructor(payload: DashboardApiError, messageKey?: string) {
    super(translateDashboardApiError(payload, messageKey));
    this.name = "DashboardApiError";
    this.error_code = payload.error_code;
    this.error = payload.error;
    this.details = payload.details;
  }
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null;
}

export function isDashboardApiError(value: unknown): value is DashboardApiError {
  return (
    isRecord(value) &&
    value.ok === false &&
    (typeof value.error_code === "undefined" || typeof value.error_code === "string") &&
    (typeof value.error === "undefined" || typeof value.error === "string") &&
    (typeof value.details === "undefined" || isRecord(value.details))
  );
}

export function normalizeDashboardApiError(value: unknown, fallbackMessage: string): DashboardApiError {
  if (isDashboardApiError(value)) {
    return value;
  }
  return {
    ok: false,
    error: fallbackMessage,
  };
}

export function translateDashboardApiError(payload: DashboardApiError, messageKey?: string): string {
  if (payload.error_code) {
    const key = `errors.${payload.error_code}`;
    if (i18next.exists(key)) {
      return i18next.t(key);
    }
  }

  const fallback = messageKey ?? "errors.unexpected";
  return i18next.t(fallback);
}

export function dashboardApiError(payload: unknown, fallbackMessage = "errors.unexpected"): DashboardApiErrorImpl {
  const normalized = normalizeDashboardApiError(payload, fallbackMessage);
  return new DashboardApiErrorImpl(normalized, fallbackMessage);
}
