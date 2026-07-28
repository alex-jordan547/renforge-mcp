import { useEffect, useState, useMemo } from "react";
import { api } from "../api";
import type { TranslationStats } from "../types";
import { useTranslation } from "react-i18next";

const SOURCE_DIALOGUE_COUNT = 6;
const ORPHAN_COUNT = 1;
const TRANSLATION_PREFIX = "tl/";

type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

interface TranslationRow {
  language: string;
  status: "complete" | "partial" | "incomplete" | "unavailable";
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

function formatRatio(percent: number | null, done: number | null, total: number | null): string {
  if (percent !== null) {
    return `${percent.toFixed(0)}%`;
  }
  if (done !== null && total !== null) {
    return `${done}/${total}`;
  }
  return "—";
}

function formatRow(language: string, stats: TranslationStats | null, t: TranslateFn, error?: string): TranslationRow {
  if (!stats) {
    return {
      language,
      status: "unavailable",
      ratio: "—",
      files: error ? t("errors.translationUnavailableReason", { error }) : t("pages.translation.summary.unavailable"),
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

  const ratio = formatRatio(calculatedPercent, done, total);

  const fileSummary: string[] = [];
  if (missingDialogue !== null) {
    fileSummary.push(t("pages.translation.summary.missingDialogue", { count: missingDialogue }));
  }
  if (missingStrings !== null) {
    fileSummary.push(t("pages.translation.summary.missingStrings", { count: missingStrings }));
  }
  if (missing !== null && missingDialogue === null && missingStrings === null) {
    fileSummary.push(t("pages.translation.summary.missing", { count: missing }));
  }
  if (total !== null && missingDialogue === null && missingStrings === null && missing === null) {
    fileSummary.push(t("pages.translation.summary.total", { count: total }));
  }
  const files = fileSummary.length > 0 ? fileSummary.join(" / ") : t("pages.translation.summary.none");

  let status: TranslationRow["status"] = "partial";
  if (calculatedPercent !== null && calculatedPercent >= 100) {
    status = "complete";
  } else if (showProgress && calculatedPercent === 0) {
    status = "incomplete";
  }

  return {
    language,
    status,
    ratio,
    files,
    percent: calculatedPercent,
    showProgress,
    rawStats: stats,
  };
}

function statusLabel(t: TranslateFn, status: TranslationString["status"]): string {
  if (status === "ok") {
    return t("status.ok");
  }
  if (status === "todo") {
    return t("status.incomplete");
  }
  return t("status.warning");
}

function defaultPageHint(t: TranslateFn): string {
  return t("pages.translation.hint", { apiLanguages: "/api/languages", apiStats: "/api/translation-stats" });
}

export function TranslationPage() {
  const { t } = useTranslation();
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
              ? formatRow(languageList[index], result.value.stats, t)
              : formatRow(
                  languageList[index],
                  null,
                  t,
                  result.reason instanceof Error ? result.reason.message : "Endpoint error",
                ),
          ),
        );
      } catch (err) {
        if (!mounted) {
          return;
        }
        setError(err instanceof Error ? t("errors.translationLoad") : t("errors.translationLoad"));
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
  }, [t]);

  useEffect(() => {
    if (!selectedLanguage) {
      return;
    }
    let mounted = true;
    const loadStrings = async () => {
      try {
        const res = await api.fetchTranslationStrings(selectedLanguage);
        if (mounted && res && res.strings) {
          setRealStrings(res.strings);
        }
      } catch (err) {
        console.error(t("errors.translationStringsError"), err);
      }
    };
    loadStrings();
    return () => {
      mounted = false;
    };
  }, [selectedLanguage, t]);

  const activeRow = useMemo(() => {
    return rows.find((r) => r.language === selectedLanguage) || null;
  }, [rows, selectedLanguage]);

  const stringsList = useMemo(() => {
    if (!selectedLanguage) {
      return [];
    }
    return realStrings;
  }, [selectedLanguage, realStrings]);

  const filteredStrings = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) {
      return stringsList;
    }
    return stringsList.filter(
      (s) =>
        s.id.toLowerCase().includes(q) ||
        s.src.toLowerCase().includes(q) ||
        s.tr.toLowerCase().includes(q),
    );
  }, [stringsList, searchQuery]);

  if (loading) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>{t("pages.translation.title")}</h2>
          <span className="hint">{defaultPageHint(t)}</span>
        </div>
        <div className="statusLine">{t("pages.translation.loading")}</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>{t("pages.translation.title")}</h2>
          <span className="hint">{defaultPageHint(t)}</span>
        </div>
        <p className="errorText">{error}</p>
      </div>
    );
  }

  if (languages.length === 0) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>{t("pages.translation.title")}</h2>
          <span className="hint">{defaultPageHint(t)}</span>
        </div>
        <div className="emptyState">
          <img className="emptyState-mascot" src="/brand/renforge-mascot.png" alt="" aria-hidden="true" />
          <h3>{t("pages.translation.emptyTitle")}</h3>
          <p>{t("pages.translation.emptyDescription", { apiLanguages: "/api/languages" })}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>{t("pages.translation.title")}</h2>
        <span className="hint">{defaultPageHint(t)}</span>
      </div>

      <div className="cols">
        <aside className="reveal in" style={{ animationDelay: ".05s" }}>
          {rows.map((row) => {
            const isFr = row.language.toLowerCase() === "french" || row.language.toLowerCase() === "fr";
            const percentVal = row.percent !== null ? row.percent : 0;
            const isSelected = row.language === selectedLanguage;
            const rowStatusText = t(`pages.translation.status.${row.status}`);

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
                      <div className="sub">{t("pages.translation.languageSummary", { language: row.language, status: rowStatusText })}</div>
                    </div>
                    <span className={`st ${row.status === "complete" ? "ok" : "todo"}`} style={{ marginLeft: "auto" }}>
                      {t(`pages.translation.badge.${row.status}`)}
                    </span>
                  </div>
                  <div className="progress">
                    <i style={{ width: `${percentVal}%` }} />
                  </div>
                  <div className="prog-meta">
                    <span>{t("pages.translation.ratioLabel", { ratio: row.ratio })}</span>
                    <span>{row.files}</span>
                  </div>
                </div>
              </div>
            );
          })}

          <div className="card">
            <div className="card-body">
              <div className="vhead">{t("pages.translation.scriptSummaryTitle")}</div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", fontSize: "12.5px" }}>
                <span style={{ color: "var(--muted)" }}>{t("pages.translation.summary.sourceDialogues")}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>{SOURCE_DIALOGUE_COUNT}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", fontSize: "12.5px" }}>
                <span style={{ color: "var(--muted)" }}>
                  {t("pages.translation.summary.translated", { language: selectedLanguage || "—" })}
                </span>
                  <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                  {activeRow && activeRow.rawStats
                    ? (SOURCE_DIALOGUE_COUNT - (toNumber(activeRow.rawStats.missing_dialogue) ?? 0))
                    : 0}
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", fontSize: "12.5px" }}>
                <span style={{ color: "var(--muted)" }}>{t("pages.translation.summary.orphans")}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--warn)" }}>{ORPHAN_COUNT}</span>
              </div>
            </div>
          </div>
        </aside>

        <section className="card reveal in" style={{ animationDelay: ".10s" }}>
          <div className="card-head">
            <h3>{t("pages.translation.stringHeader", { language: selectedLanguage })}</h3>
            {activeRow && <span className="badge warn">{t("pages.translation.translatedBadge", { ratio: activeRow.ratio })}</span>}
          </div>
          <div className="card-body">
            <div className="tbl-tools">
              <input
                className="input"
                id="tr-search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t("pages.translation.searchPlaceholder")}
              />
            </div>
            <div className="translation-table">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: "44%" }}>{t("pages.translation.table.sourceHeader")}</th>
                    <th style={{ width: "44%" }}>{t("pages.translation.table.translationHeader", { language: selectedLanguage })}</th>
                    <th>{t("pages.translation.table.stateHeader")}</th>
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
                            <span className="id">{TRANSLATION_PREFIX}{selectedLanguage}</span>
                            {str.tr}
                          </>
                        ) : (
                          t("pages.translation.untranslatedMarker")
                        )}
                      </td>
                      <td>
                        <span className={`st ${str.status}`}>
                          {statusLabel(t, str.status)}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {filteredStrings.length === 0 && (
                    <tr>
                      <td colSpan={3} style={{ textAlign: "center", color: "var(--meta)" }}>
                        {t("pages.translation.noSearchMatch")}
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
