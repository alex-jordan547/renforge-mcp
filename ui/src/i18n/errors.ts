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
  switch (payload.error_code) {
    case "assets_game_root_missing":
      return i18next.t("errors.assets_game_root_missing");
    case "assets_read_failed":
      return i18next.t("errors.assets_read_failed");
    case "coverage_file_missing":
      return i18next.t("errors.coverage_file_missing");
    case "coverage_read_failed":
      return i18next.t("errors.coverage_read_failed");
    case "debug_events_since_invalid":
      return i18next.t("errors.debug_events_since_invalid");
    case "file_access_failed":
      return i18next.t("errors.file_access_failed");
    case "file_not_found":
      return i18next.t("errors.file_not_found");
    case "file_path_out_of_bounds":
      return i18next.t("errors.file_path_out_of_bounds");
    case "invalid_token":
      return i18next.t("errors.invalid_token");
    case "launch_version_invalid":
      return i18next.t("errors.launch_version_invalid");
    case "launch_editor_invalid":
      return i18next.t("errors.launch_editor_invalid");
    case "live_action_missing":
      return i18next.t("errors.live_action_missing");
    case "live_warp_invalid":
      return i18next.t("errors.live_warp_invalid");
    case "project_browser_failed":
      return i18next.t("errors.project_browser_failed");
    case "project_browser_unknown_root":
      return i18next.t("errors.project_browser_unknown_root");
    case "project_folder_invalid":
      return i18next.t("errors.project_folder_invalid");
    case "project_folder_not_accessible":
      return i18next.t("errors.project_folder_not_accessible");
    case "project_folder_not_found":
      return i18next.t("errors.project_folder_not_found");
    case "project_folder_outside_root":
      return i18next.t("errors.project_folder_outside_root");
    case "project_not_renpy_project":
      return i18next.t("errors.project_not_renpy_project");
    case "project_selection_payload_invalid":
      return i18next.t("errors.project_selection_payload_invalid");
    case "project_switch_blocked":
      return i18next.t("errors.project_switch_blocked");
    case "screenshot_failed":
      return i18next.t("errors.screenshot_failed");
    case "story_map_failed":
      return i18next.t("errors.story_map_failed");
    case "story_map_root_missing":
      return i18next.t("errors.story_map_root_missing");
    case "timeline_limit_invalid":
      return i18next.t("errors.timeline_limit_invalid");
    case "translation_language_missing":
      return i18next.t("errors.translation_language_missing");
    case "warp_target_invalid":
      return i18next.t("errors.warp_target_invalid");
    case "warp_target_missing":
      return i18next.t("errors.warp_target_missing");
    case "warp_target_unknown":
      return i18next.t("errors.warp_target_unknown");
  }

  void messageKey;
  return i18next.t("errors.unexpected");
}

export function dashboardApiError(payload: unknown, fallbackMessage = "errors.unexpected"): DashboardApiErrorImpl {
  const normalized = normalizeDashboardApiError(payload, fallbackMessage);
  return new DashboardApiErrorImpl(normalized, fallbackMessage);
}
