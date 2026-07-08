import { useEffect, useState, useMemo } from "react";
import { api } from "../api";
import type { LintDiagnostic } from "../types";

function severityClass(severity?: string): string {
  if (!severity) {
    return "diagUnknown";
  }
  const value = severity.toLowerCase();
  if (value.includes("error") || value.includes("fatal") || value.includes("critical")) {
    return "diagError";
  }
  if (value.includes("warn") || value.includes("warning")) {
    return "diagWarn";
  }
  return "diagInfo";
}

function renderColorizedReport(rawReport: string) {
  const lines = rawReport.split("\n");
  return lines.map((line, index) => {
    const trimmed = line.trim();
    if (
      trimmed.startsWith("Ren'Py") ||
      trimmed.endsWith("report") ||
      trimmed === "Orphan Translations:" ||
      trimmed === "Statistics:"
    ) {
      return (
        <span key={index} className="h">
          {line}
          {"\n"}
        </span>
      );
    }
    
    if (trimmed.endsWith(".rpy") || trimmed.endsWith(".rpy:")) {
      return (
        <span key={index} className="path">
          {line}
          {"\n"}
        </span>
      );
    }
    
    if (trimmed.startsWith("*") || trimmed.toLowerCase().includes("warning") || trimmed.toLowerCase().includes("error")) {
      return (
        <span key={index} className="warn">
          {line}
          {"\n"}
        </span>
      );
    }
    
    if (
      trimmed.startsWith("Lint is not a substitute") ||
      trimmed.startsWith("before releasing") ||
      trimmed.startsWith("New releases fix")
    ) {
      return (
        <span key={index} className="dim">
          {line}
          {"\n"}
        </span>
      );
    }
    
    return line + "\n";
  });
}

export function DiagnosticsPage() {
  const [diagnostics, setDiagnostics] = useState<LintDiagnostic[]>([]);
  const [rawReport, setRawReport] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await api.fetchLint();
        if (!mounted) {
          return;
        }
        setDiagnostics(response.diagnostics);
        setRawReport(response.raw ?? "");
      } catch (err) {
        if (!mounted) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load diagnostics");
        setRawReport("");
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

  const counts = useMemo(() => {
    let errCount = diagnostics.filter((d) => severityClass(d.severity) === "diagError").length;
    let warnCount = diagnostics.filter((d) => severityClass(d.severity) === "diagWarn").length;
    
    // Attempt to parse screen count from the raw report
    const screenMatch = rawReport.match(/(\d+)\s+screens/);
    const screenCount = screenMatch ? parseInt(screenMatch[1], 10) : 0;
    
    // If no structured diagnostics are returned but raw report mentions orphans or warning, we count them as warning
    if (warnCount === 0 && (rawReport.includes("warning") || rawReport.includes("Orphan"))) {
      warnCount = 1;
    }

    return {
      errors: errCount,
      warnings: warnCount,
      screens: screenCount,
    };
  }, [diagnostics, rawReport]);

  if (loading) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Diagnostics</h2>
          <span className="hint">file:line / severity / message</span>
        </div>
        <div className="statusLine">Analyse des diagnostics en cours…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Diagnostics</h2>
          <span className="hint">file:line / severity / message</span>
        </div>
        <p className="errorText">Impossible de charger /api/lint : {error}</p>
      </div>
    );
  }

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Diagnostics</h2>
        <span className="hint">file:line / severity / message</span>
      </div>

      <div className="diag-grid reveal in" style={{ animationDelay: ".05s" }}>
        <div className="sev err">
          <span className="ic">
            <svg viewBox="0 0 24 24" width="20" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v5M12 16h.01" />
            </svg>
          </span>
          <div>
            <div className="num">{counts.errors}</div>
            <div className="lbl">Erreurs</div>
          </div>
        </div>

        <div className="sev warn">
          <span className="ic">
            <svg viewBox="0 0 24 24" width="20" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 3 2 20h20z" />
              <path d="M12 9v5M12 17h.01" />
            </svg>
          </span>
          <div>
            <div className="num">{counts.warnings}</div>
            <div className="lbl">Avertissements</div>
          </div>
        </div>

        <div className="sev info">
          <span className="ic">
            <svg viewBox="0 0 24 24" width="20" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 11v5M12 8h.01" />
            </svg>
          </span>
          <div>
            <div className="num">{counts.screens}</div>
            <div className="lbl">Écrans analysés</div>
          </div>
        </div>
      </div>

      {diagnostics.length === 0 && (
        <div className="banner reveal in" style={{ animationDelay: ".08s" }}>
          <svg viewBox="0 0 24 24" width="18" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M20 6 9 17l-5-5" />
          </svg>
          Aucun diagnostic structuré bloquant. Le rapport lint brut est disponible ci-dessous.
        </div>
      )}

      {diagnostics.length > 0 && (
        <section className="card reveal in" style={{ animationDelay: ".08s", marginBottom: "20px" }}>
          <div className="card-head">
            <h3>Diagnostics structurés</h3>
            <span className="badge info">{diagnostics.length}</span>
          </div>
          <div className="card-body" style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Fichier:Ligne</th>
                  <th>Sévérité</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {diagnostics.map((item, index) => (
                  <tr key={`${item.file}-${item.line}-${index}`}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "12.5px" }}>
                      {item.file || "—"}
                      {item.line ? `:${item.line}` : ""}
                    </td>
                    <td>
                      <span className={`st ${severityClass(item.severity) === "diagError" ? "todo" : "orphan"}`}>
                        {String(item.severity || "info").toUpperCase()}
                      </span>
                    </td>
                    <td style={{ fontSize: "13px" }}>{item.message || item.details || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {rawReport && (
        <section className="card reveal in" style={{ animationDelay: ".12s" }}>
          <div className="card-head">
            <h3>Rapport lint Ren’Py</h3>
            <span className="badge off">brut</span>
          </div>
          <div className="card-body">
            <pre className="report">
              {renderColorizedReport(rawReport)}
            </pre>
          </div>
        </section>
      )}
    </div>
  );
}
