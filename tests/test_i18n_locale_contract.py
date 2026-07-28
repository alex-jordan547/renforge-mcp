from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
I18N_DIR = REPO_ROOT / "ui" / "src" / "i18n"
LOCALES_DIR = I18N_DIR / "locales"
EN_FILE = LOCALES_DIR / "en.json"
ZH_FILE = LOCALES_DIR / "zh-CN.json"
INDEX_FILE = I18N_DIR / "index.ts"
MAIN_FILE = REPO_ROOT / "ui" / "src" / "main.tsx"
ALLOWLIST_FILE = REPO_ROOT / "ui" / "scripts" / "i18n-allowlist.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _leaf_keys(data: object, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    keys: set[str] = set()
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        keys |= _leaf_keys(value, full_key)
    return keys


def _run_scanner(root: Path, *, src_dir: Path | None = None) -> tuple[int, dict]:
    script = REPO_ROOT / "ui" / "scripts" / "check-i18n.mjs"
    command = ["node", str(script), "--root", str(root), "--json"]
    if src_dir is not None:
        command.extend(["--src-dir", str(src_dir)])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    payload = json.loads(result.stdout) if result.stdout else {}
    return result.returncode, payload


def _build_temp_project(tmp_path: Path, *, source: str, en: dict, zh: dict, allowlist: dict | None = None) -> Path:
    root = tmp_path / "project"
    src = root / "src"
    locales = src / "i18n" / "locales"
    locales.mkdir(parents=True, exist_ok=True)
    (src / "App.tsx").write_text(source, encoding="utf-8")
    (locales / "en.json").write_text(json.dumps(en, ensure_ascii=False), encoding="utf-8")
    (locales / "zh-CN.json").write_text(json.dumps(zh, ensure_ascii=False), encoding="utf-8")
    allow_payload = allowlist or {"version": 1, "allowlist": []}
    scripts_dir = root / "ui" / "scripts"
    scripts_dir.mkdir(exist_ok=True, parents=True)
    (scripts_dir / "i18n-allowlist.json").write_text(
        json.dumps(allow_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


class TestLocaleFilesExist:
    def test_locales_dir_exists(self):
        assert LOCALES_DIR.exists()

    def test_en_json_exists(self):
        assert EN_FILE.exists()

    def test_zh_cn_json_exists(self):
        assert ZH_FILE.exists()

    def test_index_ts_exists(self):
        assert INDEX_FILE.exists()


class TestLocaleContentContract:
    def test_en_is_not_empty(self):
        assert _load_json(EN_FILE)

    def test_zh_cn_is_not_empty(self):
        assert _load_json(ZH_FILE)

    def test_zh_cn_is_subset_of_en(self):
        en_data = _load_json(EN_FILE)
        zh_data = _load_json(ZH_FILE)
        extra_in_zh = _leaf_keys(zh_data) - _leaf_keys(en_data)
        assert not extra_in_zh, (
            "zh-CN contains keys that are not in en. "
            "Unknown zh-CN keys are not allowed."
        )

    def test_unknown_zh_cn_keys_are_rejected(self):
        en_data = _load_json(EN_FILE)
        zh_data = {**_load_json(ZH_FILE), "unexpected": "bad"}
        extra_in_zh = _leaf_keys(zh_data) - _leaf_keys(en_data)
        assert "unexpected" in extra_in_zh


class TestIndexTsUsesESM:
    def test_index_uses_import_not_require(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "require(" not in content

    def test_index_imports_i18next(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "i18next" in content

    def test_index_imports_react_i18next(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "react-i18next" in content

    def test_partial_locale_displays_missing_key_instead_of_english(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert re.search(r"fallbackLng\s*:\s*false", content)


class TestHtmlLangAttribute:
    def test_index_handles_language_changed_event(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "languageChanged" in content

    def test_index_sets_document_lang(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "document.documentElement.lang" in content


class TestMainTsxContract:
    def test_main_tsx_exists(self):
        assert MAIN_FILE.exists()

    def test_main_tsx_imports_i18n_before_app(self):
        content = MAIN_FILE.read_text(encoding="utf-8")
        assert content.find("./i18n") < content.find("./App")

    def test_index_ts_declares_resources(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert re.search(r'[\"\']en[\"\']', content)
        assert re.search(r'[\"\']zh-CN[\"\']', content)

    def test_i18next_deps(self):
        pkg_file = REPO_ROOT / "ui" / "package.json"
        pkg = _load_json(pkg_file)
        deps = pkg.get("dependencies", {})
        dev_deps = pkg.get("devDependencies", {})
        assert "i18next" in deps
        assert "react-i18next" in deps
        assert "i18next" not in dev_deps
        assert "react-i18next" not in dev_deps

    def test_allowlist_exists(self):
        assert ALLOWLIST_FILE.exists()
        payload = _load_json(ALLOWLIST_FILE)
        assert payload.get("version") == 1
        assert isinstance(payload.get("allowlist"), list)


class TestScannerFixtureContract:
    def test_scanner_reports_hardcoded_visible_text(self, tmp_path: Path) -> None:
        source = """
          import { useTranslation } from \"react-i18next\";
          function Example() {
            const { t } = useTranslation();
            const status = true;
            return <button title=\"Hardcoded title\" aria-label='Pick'>
              Hello
              {status ? \"Running\" : \"Stopped\"}
            </button>;
          }
          export default Example;
        """
        project = _build_temp_project(
            tmp_path,
            source=source,
            en={"status": {"running": "Running", "stopped": "Stopped"}},
            zh={"status": {"running": "运行"}},
        )

        code, payload = _run_scanner(project, src_dir=project / "src")
        assert code == 1
        assert payload["summary"]["hardcodedTextCount"] >= 3

    def test_scanner_validates_unknown_t_keys(self, tmp_path: Path) -> None:
        source = """
          import { useTranslation } from \"react-i18next\";
          export function Example() {
            const { t } = useTranslation();
            return <h1>{t(\"known\")}</h1>;
          }
        """
        project = _build_temp_project(
            tmp_path,
            source=source,
            en={"known": "Known"},
            zh={"known": "已知"},
        )
        code, payload = _run_scanner(project, src_dir=project / "src")
        assert code == 0
        assert payload["summary"]["unknownTKeyCount"] == 0

        source_missing = source.replace("\"known\"", "\"missing\"")
        project_missing = _build_temp_project(
            tmp_path,
            source=source_missing,
            en={"known": "Known"},
            zh={"known": "已知"},
        )
        code, payload = _run_scanner(project_missing, src_dir=project_missing / "src")
        assert code == 1
        assert payload["summary"]["unknownTKeyCount"] == 1
        assert payload["issues"]["unknownKeys"][0]["type"] == "unknown-t-key"

    def test_scanner_enables_dynamic_nav_and_ws_families(self, tmp_path: Path) -> None:
        source = """
          import { useTranslation } from \"react-i18next\";
          export function Example() {
            const { t } = useTranslation();
            const section = \"live\";
            return <h1>
              {t(`nav.${section}`)}
              {t(`ws.${\"connected\"}`)}
            </h1>;
          }
        """
        project = _build_temp_project(
            tmp_path,
            source=source,
            en={
              "nav": {"live": "Live", "storyMap": "Story"},
              "ws": {"connected": "Connected", "offline": "Offline"},
            },
            zh={"nav": {"live": "直播"}, "ws": {"connected": "已连接"}},
        )

        code, payload = _run_scanner(project, src_dir=project / "src")
        assert code == 0
        assert payload["summary"]["unknownTKeyCount"] == 0
        assert payload["summary"]["hardcodedTextCount"] == 0

    def test_scanner_ignores_css_classes_and_symbol_only_placeholders(self, tmp_path: Path) -> None:
        source = """
          export function Example() {
            const active = true;
            return <div className={active ? "on" : ""}>{"—"}</div>;
          }
        """
        project = _build_temp_project(tmp_path, source=source, en={}, zh={})

        code, payload = _run_scanner(project, src_dir=project / "src")
        assert code == 0
        assert payload["summary"]["hardcodedTextCount"] == 0

    def test_scanner_accepts_allowlisted_dynamic_key_families(self, tmp_path: Path) -> None:
        source = """
          import { useTranslation } from "react-i18next";
          export function Example() {
            const { t } = useTranslation();
            const status = "ready";
            return <span>{t(`pages.status.${status}`)}</span>;
          }
        """
        project = _build_temp_project(
            tmp_path,
            source=source,
            en={"pages": {"status": {"ready": "Ready", "failed": "Failed"}}},
            zh={},
            allowlist={
                "version": 1,
                "allowlist": [
                    {
                        "type": "key-pattern",
                        "pattern": r"^pages\.status\.[a-z]+$",
                        "reason": "Runtime status selects one of the canonical status keys.",
                    }
                ],
            },
        )

        code, payload = _run_scanner(project, src_dir=project / "src")
        assert code == 0
        assert payload["summary"]["unknownTKeyCount"] == 0
        assert payload["summary"]["unusedKeyCount"] == 0

    def test_scanner_tracks_i18n_inside_camel_case_aria_label_props(self, tmp_path: Path) -> None:
        source = """
          import { useTranslation } from "react-i18next";
          function Widget(_props: { ariaLabel: string }) { return null; }
          export function Example() {
            const { t } = useTranslation();
            return <Widget ariaLabel={t("a11y.minimap")} />;
          }
        """
        project = _build_temp_project(
            tmp_path,
            source=source,
            en={"a11y": {"minimap": "Minimap"}},
            zh={},
        )

        code, payload = _run_scanner(project, src_dir=project / "src")
        assert code == 0
        assert payload["summary"]["unusedKeyCount"] == 0

    def test_scanner_allowlist_reduces_unused_keys(self, tmp_path: Path) -> None:
        source = """
          import { useTranslation } from \"react-i18next\";
          export function Example() {
            const { t } = useTranslation();
            return <h1>{t(\"nav.live\")}</h1>;
          }
        """
        project = _build_temp_project(
            tmp_path,
            source=source,
            en={"nav": {"live": "Live", "storyMap": "Story"}, "ws": {"connected": "Connected"}},
            zh={"nav": {"live": "直播", "storyMap": "故事"}, "ws": {"connected": "已连接"}},
            allowlist={
                "version": 1,
                "allowlist": [
                    {
                        "type": "key",
                        "key": "ws.connected",
                        "reason": "accepted fallback via runtime state",
                    },
                ],
            },
        )

        code, payload = _run_scanner(project, src_dir=project / "src")
        assert code == 1
        assert "ws.connected" not in payload["issues"]["unusedKeys"]
        assert "nav.storyMap" in payload["issues"]["unusedKeys"]


class TestI18nScannerOnRealSource:
    def test_assets_kpis_render_one_translated_label_each(self):
        source = (REPO_ROOT / "ui" / "src" / "pages" / "AssetsPage.tsx").read_text(encoding="utf-8")
        labels = {
            "Project files": "pages.assets.cards.projectFiles",
            "Orphans": "pages.assets.cards.orphans",
            "Missing files": "pages.assets.cards.missingFiles",
            "Undefined images": "pages.assets.cards.undefinedImages",
        }

        for raw_label, translation_key in labels.items():
            assert f'<span className="lbl">{raw_label}</span>' not in source
            assert source.count(f't("{translation_key}")') == 1

    def test_real_ui_scan_is_green(self):
        code, payload = _run_scanner(REPO_ROOT / "ui")
        assert code == 0
        assert payload["status"] == "GREEN"
        assert payload["summary"]["hardcodedTextCount"] == 0
        assert payload["summary"]["unknownTKeyCount"] == 0
        assert payload["summary"]["unusedKeyCount"] == 0
