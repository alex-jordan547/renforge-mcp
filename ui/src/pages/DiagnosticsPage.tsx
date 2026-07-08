import { useEffect, useState } from "react";
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

  if (loading) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Diagnostics</h2>
          <span>Analyse des diagnostics <code>/api/lint</code></span>
        </div>
        <div className="spinner">Analyse des diagnostics en cours…</div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Diagnostics</h2>
          <span>Erreur endpoint</span>
        </div>
        <p className="errorText">Impossible de charger /api/lint : {error}</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Diagnostics</h2>
        <span>Liste <code>file:line</code> / <code>severity</code> / <code>message</code></span>
      </div>
      {diagnostics.length === 0 ? (
        rawReport ? (
          <div className="stack">
            <p className="muted">
              Aucun diagnostic structuré retourné. Le rapport lint brut est disponible ci-dessous.
            </p>
            <pre className="codeBlock lintRaw">
              <code>{rawReport}</code>
            </pre>
          </div>
        ) : (
          <p className="muted">Aucun diagnostic retourné.</p>
        )
      ) : (
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>file:line</th>
                <th>severity</th>
                <th>message</th>
              </tr>
            </thead>
            <tbody>
              {diagnostics.map((item, index) => (
                <tr key={`${item.file}-${item.line}-${index}`}>
                  <td>
                    {item.file || "—"}
                    {item.line ? `:${item.line}` : ""}
                  </td>
                  <td>
                    <span className={`diagBadge ${severityClass(item.severity)}`}>
                      {String(item.severity || "info")}
                    </span>
                  </td>
                  <td>{item.message || item.details || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
