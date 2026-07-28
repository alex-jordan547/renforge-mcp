"""
Contrat TDD pour les ressources i18n du frontend (ui/src/i18n/).

Règles vérifiées :
- Les locales sont dans ui/src/i18n/locales/{en,zh-CN}.json (ESM, pas à la racine)
- zh-CN.json possède exactement les mêmes clés terminales (récursives) que en.json
- Aucune locale n'est vide (au moins une clé requise)
- L'index ESM ui/src/i18n/index.ts existe et contient les imports attendus
- document.documentElement.lang est mis à jour via l'événement languageChanged
- main.tsx importe l'init i18n avant le rendu React, utilise .use(initReactI18next)
  et déclare en + zh-CN avec leurs ressources
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
I18N_DIR = REPO_ROOT / "ui" / "src" / "i18n"
LOCALES_DIR = I18N_DIR / "locales"
EN_FILE = LOCALES_DIR / "en.json"
ZH_FILE = LOCALES_DIR / "zh-CN.json"
INDEX_FILE = I18N_DIR / "index.ts"
MAIN_FILE = REPO_ROOT / "ui" / "src" / "main.tsx"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _leaf_keys(data: object, prefix: str = "") -> set[str]:
    """Retourne récursivement toutes les clés terminales (feuilles) d'un objet JSON."""
    if not isinstance(data, dict):
        return {prefix}
    result: set[str] = set()
    for k, v in data.items():
        full_key = f"{prefix}.{k}" if prefix else k
        result |= _leaf_keys(v, full_key)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLocaleFilesExist:
    def test_locales_dir_exists(self):
        assert LOCALES_DIR.exists(), (
            f"Répertoire locales manquant : {LOCALES_DIR}\n"
            "Créer ui/src/i18n/locales/ avec en.json et zh-CN.json"
        )

    def test_en_json_exists(self):
        assert EN_FILE.exists(), f"Locale EN manquante : {EN_FILE}"

    def test_zh_cn_json_exists(self):
        assert ZH_FILE.exists(), f"Locale zh-CN manquante : {ZH_FILE}"

    def test_index_ts_exists(self):
        assert INDEX_FILE.exists(), f"Index i18n manquant : {INDEX_FILE}"


class TestLocaleContentContract:
    def test_en_is_not_empty(self):
        data = _load_json(EN_FILE)
        assert len(data) > 0, "en.json est vide — au moins une clé requise"

    def test_zh_cn_is_not_empty(self):
        data = _load_json(ZH_FILE)
        assert len(data) > 0, (
            "zh-CN.json est vide {}. "
            "Il doit contenir les mêmes clés que en.json (valeurs traduites ou placeholder)."
        )

    def test_zh_cn_has_same_leaf_keys_as_en(self):
        """Parité récursive : toutes les clés terminales (pas seulement premier niveau)."""
        en_data = _load_json(EN_FILE)
        zh_data = _load_json(ZH_FILE)

        en_keys = _leaf_keys(en_data)
        zh_keys = _leaf_keys(zh_data)

        missing_in_zh = en_keys - zh_keys
        extra_in_zh = zh_keys - en_keys

        errors = []
        if missing_in_zh:
            errors.append(
                f"Clés terminales présentes en EN mais absentes en zh-CN : {sorted(missing_in_zh)}"
            )
        if extra_in_zh:
            errors.append(
                f"Clés terminales présentes en zh-CN mais absentes en EN : {sorted(extra_in_zh)}"
            )

        assert not errors, "\n".join(errors)


class TestIndexTsUsesESM:
    def test_index_uses_import_not_require(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "require(" not in content, (
            "index.ts utilise require() — incompatible avec ESM/Vite. "
            "Remplacer par import ... from '...'"
        )

    def test_index_imports_i18next(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "i18next" in content, "index.ts doit importer i18next"

    def test_index_imports_react_i18next(self):
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "react-i18next" in content, (
            "index.ts doit initialiser react-i18next (initReactI18next)"
        )


# ---------------------------------------------------------------------------
# Point de blocage 1 — document.documentElement.lang via languageChanged
# ---------------------------------------------------------------------------

class TestHtmlLangAttribute:
    """index.ts doit mettre à jour document.documentElement.lang à l'init
    et à chaque changement de langue via l'événement i18next languageChanged."""

    def test_index_handles_language_changed_event(self):
        """index.ts doit s'abonner à l'événement languageChanged d'i18next."""
        content = INDEX_FILE.read_text(encoding="utf-8")
        # On attend soit .on('languageChanged', ...) soit .on("languageChanged", ...)
        assert "languageChanged" in content, (
            "index.ts doit écouter l'événement 'languageChanged' de i18next "
            "pour mettre à jour document.documentElement.lang."
        )

    def test_index_sets_document_lang(self):
        """index.ts doit affecter document.documentElement.lang."""
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "document.documentElement.lang" in content, (
            "index.ts doit affecter document.documentElement.lang "
            "afin que les lecteurs d'écran et le navigateur connaissent la langue active."
        )


# ---------------------------------------------------------------------------
# Point de blocage 2 (déjà couvert par test_zh_cn_has_same_leaf_keys_as_en)
# Ce bloc documente explicitement l'intention récursive.
# ---------------------------------------------------------------------------

class TestLocaleParity:
    """Parité totale : chaque clé terminale de EN doit exister dans zh-CN et vice-versa."""

    def test_leaf_key_parity_is_recursive(self):
        """Vérifie que _leaf_keys descend bien dans les objets imbriqués."""
        sample = {"a": {"b": "v1", "c": {"d": "v2"}}}
        keys = _leaf_keys(sample)
        assert keys == {"a.b", "a.c.d"}, (
            "_leaf_keys doit retourner les clés terminales pointées par chemin complet"
        )


# ---------------------------------------------------------------------------
# Point de blocage 3 — Contrat main.tsx
# ---------------------------------------------------------------------------

class TestMainTsxContract:
    """main.tsx doit satisfaire le contrat d'amorçage i18n."""

    def test_main_tsx_exists(self):
        assert MAIN_FILE.exists(), f"main.tsx introuvable : {MAIN_FILE}"

    def test_main_tsx_imports_i18n_before_app(self):
        """L'import side-effect ./i18n doit précéder l'import de App."""
        content = MAIN_FILE.read_text(encoding="utf-8")
        assert "./i18n" in content or '"./i18n"' in content, (
            "main.tsx doit importer './i18n' comme side-effect "
            "afin que i18next soit initialisé avant le premier render React."
        )
        # Vérifier l'ordre : i18n avant App
        i18n_pos = content.find("./i18n")
        app_pos = content.find("./App")
        if app_pos != -1:
            assert i18n_pos < app_pos, (
                "L'import './i18n' doit apparaître avant l'import './App' dans main.tsx."
            )

    def test_index_ts_uses_dot_use_init_react_i18next(self):
        """.use(initReactI18next) doit être chaîné dans index.ts."""
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "initReactI18next" in content, (
            "index.ts doit appeler .use(initReactI18next) pour brancher "
            "le pont React-i18next."
        )

    def test_index_ts_declares_en_resource(self):
        """Les ressources 'en' doivent être référencées dans index.ts."""
        content = INDEX_FILE.read_text(encoding="utf-8")
        # Accepte : "en" ou 'en' comme clé de ressource
        assert '"en"' in content or "'en'" in content, (
            "index.ts doit déclarer la ressource 'en' dans l'objet resources."
        )

    def test_index_ts_declares_zh_cn_resource(self):
        """Les ressources 'zh-CN' doivent être référencées dans index.ts."""
        content = INDEX_FILE.read_text(encoding="utf-8")
        assert "zh-CN" in content, (
            "index.ts doit déclarer la ressource 'zh-CN' dans l'objet resources."
        )

    def test_i18next_in_production_deps(self):
        """i18next et react-i18next doivent être dans dependencies, pas devDependencies."""
        pkg_file = REPO_ROOT / "ui" / "package.json"
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        deps = pkg.get("dependencies", {})
        dev_deps = pkg.get("devDependencies", {})
        assert "i18next" in deps, (
            "i18next doit être dans dependencies (pas devDependencies) — c'est une dep de production."
        )
        assert "react-i18next" in deps, (
            "react-i18next doit être dans dependencies (pas devDependencies)."
        )
        assert "i18next" not in dev_deps, "i18next ne doit pas être dans devDependencies."
        assert "react-i18next" not in dev_deps, "react-i18next ne doit pas être dans devDependencies."

