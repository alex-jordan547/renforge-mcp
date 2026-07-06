import { useEffect, useState } from "react";
import { api } from "../api";
import type { TranslationStats } from "../types";

interface TranslationRow {
  language: string;
  status: string;
  ratio: string;
  files: string;
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

  const ratio =
    percent !== null
      ? `${percent.toFixed(0)}%`
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
      percent !== null
        ? "OK"
        : translated !== null && total !== null
          ? "Partiel"
          : "Partiel",
    ratio,
    files,
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
        <p className="muted">Chargement des statistiques de traduction...</p>
      ) : error ? (
        <p className="errorText">{error}</p>
      ) : languages.length === 0 ? (
        <p className="muted">Aucune langue détectée.</p>
      ) : (
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Langue</th>
                <th>État</th>
                <th>Ratio</th>
                <th>Fichiers / manquants</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.language}>
                  <td>{row.language}</td>
                  <td>{row.status}</td>
                  <td>{row.ratio}</td>
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
