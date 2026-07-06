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
}: {
  title: string;
  values: string[];
}) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {values.length === 0 ? (
        <p className="muted">Aucun élément.</p>
      ) : (
        <ul className="list">
          {values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
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

  const summaryText = useMemo(() => formatSummary(assets?.summary), [assets?.summary]);
  const assetFiles = useMemo(() => toArray(assets?.asset_files), [assets?.asset_files]);
  const orphans = useMemo(() => toArray(assets?.orphans), [assets?.orphans]);
  const missingFiles = useMemo(() => toArray(assets?.missing_files), [assets?.missing_files]);
  const undefinedImages = useMemo(() => toArray(assets?.undefined_images), [assets?.undefined_images]);

  const collections: Array<{ key: AssetCollectionKey; title: string; values: string[] }> = [
    { key: "asset_files", title: "asset_files", values: assetFiles },
    { key: "orphans", title: "orphans", values: orphans },
    { key: "missing_files", title: "missing_files", values: missingFiles },
    { key: "undefined_images", title: "undefined_images", values: undefinedImages },
  ];

  if (loading) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Assets</h2>
          <span>Chargement des assets</span>
        </div>
        <p className="muted">Connexion API ...</p>
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
      <div className="card">
        <h3>Summary</h3>
        <p>{summaryText}</p>
      </div>
      {hasData ? (
        <div className="panelGrid">
          {collections.map((collection) => (
            <SummaryList key={collection.key} title={collection.title} values={collection.values} />
          ))}
        </div>
      ) : (
        <p className="muted">Aucune donnée listable reçue depuis <code>/api/assets</code>.</p>
      )}
    </section>
  );
}
