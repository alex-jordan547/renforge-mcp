import { useEffect, useState } from "react";
import { api } from "../api";
import type { TranslationStats } from "../types";

interface TranslationRow {
  language: string;
  status: string;
  ratio: string;
  files: string;
  percent: number | null;
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function readValue(stats: TranslationStats, keys: string[]): number | null {
  for (const key of keys) {
    const value = toNumber((stats as Record<string, unknown>)[key]);
    if (value !== null) {
      return value;
    }
  }
  return null;
}

function formatRow(language: string, stats: TranslationStats | null, error?: string): TranslationRow {
  if (!stats) {
    return {
      language,
      status: error || "Stats indisponibles",
      ratio: "—",
      files: "—",
      percent: null,
    };
  }

  const translated = readValue(stats, [
    "translated_lines",
    "translated",
    "done",
    "translated_files",
  ]);
  const total = readValue(stats, ["total_lines", "total", "total_files", "files"]);
  const missing = readValue(stats, ["missing_lines", "missing", "missing_files", "missing_translations"]);
  const percent = toNumber((stats as Record<string, unknown>).percent);

  const calculatedPercent =
    percent !== null
      ? percent
      : translated !== null && total !== null && total > 0
        ? (translated / total) * 100
        : null;

  const ratio =
    calculatedPercent !== null
      ? `${calculatedPercent.toFixed(0)}%`
      : translated !== null && total !== null
        ? `${translated}/${total}`
        : "—";

  const files =
    total !== null
      ? `${total} fichiers`
      : missing !== null
        ? `${missing} manquants`
        : "—";

  return {
    language,
    status:
      calculatedPercent !== null && calculatedPercent >= 100
        ? "Complet"
        : calculatedPercent !== null && calculatedPercent > 0
          ? "Partiel"
          : "Partiel",
    ratio,
    files,
    percent: calculatedPercent,
  };
}

export function TranslationPage() {
  const [languages, setLanguages] = useState<string[]>([]);
  const [rows, setRows] = useState<TranslationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const languageList = await api.fetchLanguages();
        if (!mounted) {
          return;
        }

        setLanguages(languageList);
        if (languageList.length === 0) {
          setRows([]);
          return;
        }

        const results = await Promise.allSettled(
          languageList.map(async (language) => ({
            language,
            stats: await api.fetchTranslationStats(language),
          })),
        );

        if (!mounted) {
          return;
        }

        setRows(
          results.map((result, index) =>
            result.status === "fulfilled"
              ? formatRow(languageList[index], result.value.stats)
              : formatRow(
                  languageList[index],
                  null,
                  result.reason instanceof Error ? result.reason.message : "Erreur endpoint",
                ),
          ),
        );
      } catch (err) {
        if (!mounted) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load translation data");
        setLanguages([]);
        setRows([]);
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };

    load();
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Translation</h2>
        <span>Progression par langue via <code>/api/languages</code> et <code>/api/translation-stats</code></span>
      </div>
      {loading ? (
        <div className="spinner">Chargement des statistiques de traduction…</div>
      ) : error ? (
        <p className="errorText">{error}</p>
      ) : languages.length === 0 ? (
        <div className="emptyState">
          <div className="emptyState-icon">🌐</div>
          <h3>Aucune langue détectée</h3>
          <p>
            Configurez les langues dans votre projet Ren'Py pour voir les statistiques de traduction ici.
            Les langues sont détectées automatiquement via <code>/api/languages</code>.
          </p>
        </div>
      ) : (
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Langue</th>
                <th>État</th>
                <th>Ratio / Progression</th>
                <th>Fichiers / manquants</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.language}>
                  <td style={{ fontWeight: 600 }}>{row.language}</td>
                  <td>
                    <span className={`diagBadge ${row.status === "Complet" ? "diagInfo" : "diagWarn"}`}>
                      {row.status}
                    </span>
                  </td>
                  <td>
                    <div className="progressContainer">
                      <span style={{ minWidth: "48px", fontWeight: "bold" }}>{row.ratio}</span>
                      {row.percent !== null && (
                        <div className="progressBar">
                          <div className="progressFill" style={{ width: `${row.percent}%` }} />
                        </div>
                      )}
                    </div>
                  </td>
                  <td>{row.files}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
