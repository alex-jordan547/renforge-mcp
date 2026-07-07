import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { AssetsResponse } from "../types";

type AssetCollectionKey = "asset_files" | "orphans" | "missing_files" | "undefined_images";

function toArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function SummaryList({
  title,
  values,
  type,
}: {
  title: string;
  values: string[];
  type: string;
}) {
  const [isCollapsed, setIsCollapsed] = useState(type === "asset_files");

  const getClassName = () => {
    if (type === "orphans") return "assetPill orphan";
    if (type === "missing_files" || type === "undefined_images") return "assetPill missing";
    return "assetPill";
  };

  return (
    <div className="card">
      <div
        onClick={() => setIsCollapsed(!isCollapsed)}
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        <h3 style={{ margin: 0 }}>
          {title} <span style={{ fontSize: "0.85rem", color: "var(--muted)", fontWeight: "normal" }}>({values.length})</span>
        </h3>
        <span
          style={{
            transform: isCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
            transition: "transform 150ms ease",
            fontSize: "0.8rem",
            color: "var(--muted)",
          }}
        >
          ▼
        </span>
      </div>
      {!isCollapsed && (
        <div style={{ marginTop: 12 }}>
          {values.length === 0 ? (
            <p className="muted">Aucun élément.</p>
          ) : (
            <div className="assetPills">
              {values.map((value) => (
                <span key={value} className={getClassName()}>{value}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatSummary(summary: AssetsResponse["summary"]): string {
  if (summary === undefined) {
    return "Aucun résumé disponible.";
  }
  if (typeof summary === "string") {
    return summary;
  }
  return Object.entries(summary)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" • ");
}

export function AssetsPage() {
  const [assets, setAssets] = useState<AssetsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await api.fetchAssets();
        if (!mounted) {
          return;
        }
        setAssets(result);
      } catch (err) {
        if (!mounted) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load assets");
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

  const assetFiles = useMemo(() => toArray(assets?.asset_files), [assets?.asset_files]);
  const orphans = useMemo(() => toArray(assets?.orphans), [assets?.orphans]);
  const missingFiles = useMemo(() => toArray(assets?.missing_files), [assets?.missing_files]);
  const undefinedImages = useMemo(() => toArray(assets?.undefined_images), [assets?.undefined_images]);

  const collections: Array<{ key: AssetCollectionKey; title: string; values: string[] }> = [
    { key: "asset_files", title: "Fichiers projet", values: assetFiles },
    { key: "orphans", title: "Orphelins", values: orphans },
    { key: "missing_files", title: "Fichiers manquants", values: missingFiles },
    { key: "undefined_images", title: "Images non définies", values: undefinedImages },
  ];

  if (loading) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Assets</h2>
          <span>Chargement des assets</span>
        </div>
        <div className="spinner">Chargement de l'inventaire…</div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Assets</h2>
          <span>Erreur API assets</span>
        </div>
        <p className="errorText">Impossible de charger les assets: {error}</p>
      </section>
    );
  }

  const hasData = collections.some((item) => item.values.length > 0);

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Assets</h2>
        <span>Inventaire projet depuis <code>/api/assets</code></span>
      </div>

      <div className="panelGrid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
        <div className="card">
          <h3>Asset Files</h3>
          <p style={{ fontSize: "1.8rem", fontWeight: "bold", margin: 0, color: "var(--brand)" }}>{assetFiles.length}</p>
        </div>
        <div className="card" style={{ borderColor: orphans.length > 0 ? "rgba(245, 158, 11, 0.4)" : undefined }}>
          <h3>Orphans</h3>
          <p style={{ fontSize: "1.8rem", fontWeight: "bold", margin: 0, color: orphans.length > 0 ? "var(--accent)" : "var(--muted)" }}>{orphans.length}</p>
        </div>
        <div className="card" style={{ borderColor: missingFiles.length > 0 ? "rgba(239, 68, 68, 0.4)" : undefined }}>
          <h3>Missing Files</h3>
          <p style={{ fontSize: "1.8rem", fontWeight: "bold", margin: 0, color: missingFiles.length > 0 ? "var(--danger)" : "var(--muted)" }}>{missingFiles.length}</p>
        </div>
        <div className="card" style={{ borderColor: undefinedImages.length > 0 ? "rgba(239, 68, 68, 0.4)" : undefined }}>
          <h3>Undefined Images</h3>
          <p style={{ fontSize: "1.8rem", fontWeight: "bold", margin: 0, color: undefinedImages.length > 0 ? "var(--danger)" : "var(--muted)" }}>{undefinedImages.length}</p>
        </div>
      </div>

      {hasData ? (
        <div className="panelGrid" style={{ marginTop: 8 }}>
          {collections.map((collection) => (
            <SummaryList key={collection.key} title={collection.title} values={collection.values} type={collection.key} />
          ))}
        </div>
      ) : (
        <p className="muted">Aucune donnée listable reçue depuis <code>/api/assets</code>.</p>
      )}
    </section>
  );
}
