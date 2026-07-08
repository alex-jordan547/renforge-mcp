import { useEffect, useState, useMemo } from "react";
import { api } from "../api";
import type { TranslationStats } from "../types";

interface TranslationRow {
  language: string;
  status: string;
  ratio: string;
  files: string;
  percent: number | null;
  showProgress: boolean;
  rawStats: TranslationStats | null;
}

interface TranslationString {
  id: string;
  src: string;
  tr: string;
  status: "orphan" | "todo" | "ok";
  statusLabel: string;
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
      showProgress: false,
      rawStats: null,
    };
  }

  const done = readValue(stats, ["done", "translated"]);
  const total = readValue(stats, ["total"]);
  const missing = readValue(stats, ["missing_lines", "missing", "missing_files", "missing_translations"]);
  const missingDialogue = readValue(stats, ["missing_dialogue", "missing_dialogues"]);
  const missingStrings = readValue(stats, ["missing_strings"]);
  const percent = toNumber((stats as Record<string, unknown>).percent);

  const showProgress = percent !== null || (done !== null && total !== null && total > 0);
  const calculatedPercent =
    percent !== null ? percent : done !== null && total !== null && total > 0 ? (done / total) * 100 : null;

  const ratio =
    calculatedPercent !== null
      ? `${calculatedPercent.toFixed(0)}%`
      : done !== null && total !== null
        ? `${done}/${total}`
        : "—";

  const fileParts = [
    missingDialogue !== null ? `${missingDialogue} dialogues manquants` : null,
    missingStrings !== null ? `${missingStrings} strings manquants` : null,
    missing !== null && missingDialogue === null && missingStrings === null ? `${missing} manquants` : null,
    total !== null && missingDialogue === null && missingStrings === null && missing === null ? `${total} fichiers` : null,
  ].filter((entry): entry is string => entry !== null);

  const files = fileParts.length > 0 ? fileParts.join(" / ") : "—";

  return {
    language,
    status:
      calculatedPercent !== null && calculatedPercent >= 100
        ? "Complet"
        : showProgress
          ? "Partiel"
          : missingDialogue !== null || missingStrings !== null || missing !== null
            ? "Incomplet"
            : "Partiel",
    ratio,
    files,
    percent: calculatedPercent,
    showProgress,
    rawStats: stats,
  };
}

export function TranslationPage() {
  const [languages, setLanguages] = useState<string[]>([]);
  const [rows, setRows] = useState<TranslationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedLanguage, setSelectedLanguage] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [realStrings, setRealStrings] = useState<TranslationString[]>([]);

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

        // Set default selected language to French if it exists, otherwise the first in the list
        const defaultLang = languageList.includes("french")
          ? "french"
          : languageList.includes("fr")
            ? "fr"
            : languageList[0];
        setSelectedLanguage(defaultLang);

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

  useEffect(() => {
    if (!selectedLanguage) return;
    let mounted = true;
    const loadStrings = async () => {
      try {
        const res = await api.fetchTranslationStrings(selectedLanguage);
        if (mounted && res && res.strings) {
          setRealStrings(res.strings);
        }
      } catch (err) {
        console.error("Failed to load translation strings", err);
      }
    };
    loadStrings();
    return () => {
      mounted = false;
    };
  }, [selectedLanguage]);

  const activeRow = useMemo(() => {
    return rows.find((r) => r.language === selectedLanguage) || null;
  }, [rows, selectedLanguage]);

  const stringsList = useMemo(() => {
    if (!selectedLanguage) return [];
    return realStrings;
  }, [selectedLanguage, realStrings]);

  const filteredStrings = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return stringsList;
    return stringsList.filter(
      (s) =>
        s.id.toLowerCase().includes(q) ||
        s.src.toLowerCase().includes(q) ||
        s.tr.toLowerCase().includes(q)
    );
  }, [stringsList, searchQuery]);

  if (loading) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Translation</h2>
          <span className="hint">/api/languages · /api/translation-stats</span>
        </div>
        <div className="statusLine">Chargement des statistiques de traduction…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Translation</h2>
          <span className="hint">/api/languages · /api/translation-stats</span>
        </div>
        <p className="errorText">{error}</p>
      </div>
    );
  }

  if (languages.length === 0) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Translation</h2>
          <span className="hint">/api/languages · /api/translation-stats</span>
        </div>
        <div className="emptyState">
          <div className="emptyState-icon">🌐</div>
          <h3>Aucune langue détectée</h3>
          <p>
            Configurez les langues dans votre projet Ren'Py pour voir les statistiques de traduction ici.
            Les langues sont détectées automatiquement via <code>/api/languages</code>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Translation</h2>
        <span className="hint">/api/languages · /api/translation-stats</span>
      </div>

      <div className="cols">
        <aside className="reveal in" style={{ animationDelay: ".05s" }}>
          {rows.map((row) => {
            const isFr = row.language.toLowerCase() === "french" || row.language.toLowerCase() === "fr";
            const percentVal = row.percent !== null ? row.percent : 0;
            const isSelected = row.language === selectedLanguage;
            
            return (
              <div key={row.language} className="card" style={{ marginBottom: "14px" }}>
                <div className="card-body">
                  <div
                    className={`lang-row ${isSelected ? "on" : ""}`}
                    onClick={() => setSelectedLanguage(row.language)}
                  >
                    <span className={`flag ${isFr ? "fr" : "generic"}`} />
                    <div>
                      <div className="nm">{row.language}</div>
                      <div className="sub">{row.language} · {row.status.toLowerCase()}</div>
                    </div>
                    <span
                      className={`st ${row.status === "Complet" ? "ok" : "todo"}`}
                      style={{ marginLeft: "auto" }}
                    >
                      {row.status.toUpperCase()}
                    </span>
                  </div>
                  <div className="progress">
                    <i style={{ width: `${percentVal}%` }} />
                  </div>
                  <div className="prog-meta">
                    <span>{row.ratio} progress</span>
                    <span>{row.files}</span>
                  </div>
                </div>
              </div>
            );
          })}

          <div className="card">
            <div className="card-body">
              <div className="vhead">Résumé script</div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", fontSize: "12.5px" }}>
                <span style={{ color: "var(--muted)" }}>Dialogues source</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>6</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", fontSize: "12.5px" }}>
                <span style={{ color: "var(--muted)" }}>Traduits ({selectedLanguage || "—"})</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                  {activeRow && activeRow.rawStats
                    ? (6 - (toNumber(activeRow.rawStats.missing_dialogue) ?? 0))
                    : 0}
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", fontSize: "12.5px" }}>
                <span style={{ color: "var(--muted)" }}>Orphelins</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--warn)" }}>1</span>
              </div>
            </div>
          </div>
        </aside>

        <section className="card reveal in" style={{ animationDelay: ".10s" }}>
          <div className="card-head">
            <h3>Chaînes — {selectedLanguage}</h3>
            {activeRow && (
              <span className="badge warn">{activeRow.ratio} traduites</span>
            )}
          </div>
          <div className="card-body">
            <div className="tbl-tools">
              <input
                className="input"
                id="tr-search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Rechercher une chaîne…"
              />
            </div>
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th style={{ width: "44%" }}>Source (en)</th>
                    <th style={{ width: "44%" }}>Traduction ({selectedLanguage})</th>
                    <th>État</th>
                  </tr>
                </thead>
                <tbody id="tr-body">
                  {filteredStrings.map((str) => (
                    <tr key={str.id}>
                      <td className="src">
                        <span className="id">{str.id}</span>
                        {str.src}
                      </td>
                      <td className={`tr ${!str.tr ? "miss" : ""}`}>
                        {str.tr ? (
                          <>
                            <span className="id">tl/{selectedLanguage}</span>
                            {str.tr}
                          </>
                        ) : (
                          "— non traduit —"
                        )}
                      </td>
                      <td>
                        <span className={`st ${str.status}`}>
                          {str.statusLabel}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {filteredStrings.length === 0 && (
                    <tr>
                      <td colSpan={3} style={{ textAlign: "center", color: "var(--meta)" }}>
                        Aucune chaîne ne correspond à votre recherche.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
