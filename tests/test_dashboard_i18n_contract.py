from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
API_FILE = REPO_ROOT / "ui" / "src" / "api.ts"
WS_FILE = REPO_ROOT / "ui" / "src" / "hooks" / "useWebSocket.ts"
I18N_ERRORS_FILE = REPO_ROOT / "ui" / "src" / "i18n" / "errors.ts"
TRANSLATION_PAGE_FILE = REPO_ROOT / "ui" / "src" / "pages" / "TranslationPage.tsx"
TIMELINE_PAGE_FILE = REPO_ROOT / "ui" / "src" / "pages" / "TimelinePage.tsx"
EN_FILE = REPO_ROOT / "ui" / "src" / "i18n" / "locales" / "en.json"


def _load(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dashboard_api_error_is_structured_and_typed():
    content = _load(I18N_ERRORS_FILE)

    assert "export interface DashboardApiError" in content
    assert "error_code?: string" in content
    assert "details?: JsonRecord" in content
    assert "error?: string" in content
    assert "class DashboardApiErrorImpl" in content
    assert "translateDashboardApiError" in content


def test_dashboard_errors_translate_known_and_unknown_code_keys():
    content = _load(I18N_ERRORS_FILE)

    assert "errors.unexpected" in content
    assert "translateDashboardApiError(payload" in content
    assert 'case "invalid_token"' in content
    assert 'i18next.t("errors.invalid_token")' in content
    assert 'i18next.t("errors.unexpected")' in content
    assert "errors.${payload.error_code}" not in content


def test_timeline_labels_use_i18next_namespaces():
    content = _load(API_FILE)
    banned = [
        '"Failed"',
        '"Completed"',
        '"Label"',
        '"Entered"',
        '"Say"',
        '"Exception"',
        '"Runtime error"',
        '"Bridge event"',
    ]
    for token in banned:
        assert token not in content, f"raw timeline label found: {token}"

    for token in [
        'i18next.t("timeline.failed")',
        'i18next.t("timeline.completed")',
        'i18next.t("timeline.label")',
        'i18next.t("timeline.entered")',
        'i18next.t("timeline.say")',
        'i18next.t("timeline.exception")',
        'i18next.t("timeline.runtimeError")',
        'i18next.t("timeline.bridgeEvent")',
    ]:
        assert token in content


def test_websocket_error_is_localized_and_not_hardcoded():
    content = _load(WS_FILE)
    assert 'import { useTranslation } from "react-i18next"' in content
    assert "const { t } = useTranslation()" in content
    assert "setOffline(true)" in content
    assert 'offline ? t("ws.offline") : null' in content
    assert 'import i18next from "../i18n"' not in content
    assert "WebSocket error" not in content


def test_no_raw_backend_message_is_user_visible_text():
    content = _load(API_FILE)
    assert "Unexpected response" not in content
    assert "Could not parse" not in content


def test_story_map_rejects_http_200_error_payloads():
    content = _load(API_FILE)

    assert 'checkBooleanResponse(response, "Story map")' in content


def test_translation_missing_counts_are_incomplete():
    content = _load(TRANSLATION_PAGE_FILE)

    assert "const hasMissing = [missing, missingDialogue, missingStrings].some" in content
    assert "hasMissing || (showProgress && calculatedPercent === 0)" in content


def test_timeline_preserves_ui_source_badge():
    content = _load(TIMELINE_PAGE_FILE)
    en = json.loads(_load(EN_FILE))

    assert 'item.source === "ui"' in content
    assert 't("pages.timeline.badge.ui")' in content
    assert en["pages"]["timeline"]["badge"]["ui"] == "ui"


def test_invalid_warp_target_is_translated():
    content = _load(I18N_ERRORS_FILE)
    en = json.loads(_load(EN_FILE))

    assert 'case "warp_target_invalid"' in content
    assert 'i18next.t("errors.warp_target_invalid")' in content
    assert en["errors"]["warp_target_invalid"] == "Invalid warp target"
