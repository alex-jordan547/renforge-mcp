import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { AssetsResponse } from "../types";

function toArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function getFileType(path: string): "image" | "audio" | "video" | "other" {
  const ext = path.toLowerCase().split('.').pop() || '';
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'avif', 'tga', 'bmp'].includes(ext)) return "image";
  if (['ogg', 'wav', 'mp3', 'm4a', 'opus', 'flac', 'aac', 'mp2', 'wma'].includes(ext)) return "audio";
  if (['webm', 'mp4', 'ogv', 'avi', 'mkv', 'mov', 'mpg', 'mpeg', 'flv'].includes(ext)) return "video";
  return "other";
}

export function AssetsPage() {
  const [assets, setAssets] = useState<AssetsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedStat, setSelectedStat] = useState<"files" | "orphans" | "missing" | "undef">("files");
  const [fileFilter, setFileFilter] = useState<"all" | "image" | "audio" | "video">("all");

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

  const filteredFiles = useMemo(() => {
    return assetFiles.filter((file) => {
      if (fileFilter === "all") return true;
      return getFileType(file) === fileFilter;
    });
  }, [assetFiles, fileFilter]);

  const rightCardsCount = useMemo(() => {
    return 1 + (missingFiles.length > 0 ? 1 : 0) + (undefinedImages.length > 0 ? 1 : 0);
  }, [missingFiles.length, undefinedImages.length]);

  const orphansMaxHeight = useMemo(() => {
    if (rightCardsCount === 1) return "500px";
    if (rightCardsCount === 2) return "190px";
    return "140px"; // rightCardsCount === 3
  }, [rightCardsCount]);

  const secondaryMaxHeight = useMemo(() => {
    if (rightCardsCount === 2) return "190px";
    return "70px"; // rightCardsCount === 3
  }, [rightCardsCount]);

  if (loading) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Assets</h2>
          <span className="hint">inventaire · /api/assets</span>
        </div>
        <div className="statusLine">Chargement de l'inventaire…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Assets</h2>
          <span className="hint">inventaire · /api/assets</span>
        </div>
        <p className="errorText">Impossible de charger les assets: {error}</p>
      </div>
    );
  }

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Assets</h2>
        <span className="hint">inventaire · /api/assets</span>
      </div>

      <div className="stat-grid reveal in" style={{ animationDelay: ".05s" }}>
        <div
          className={`stat accent ${selectedStat === "files" ? "sel" : ""}`}
          onClick={() => setSelectedStat("files")}
        >
          <div className="lbl">Fichiers projet</div>
          <div className="num">{assetFiles.length}</div>
        </div>
        <div
          className={`stat warn ${selectedStat === "orphans" ? "sel" : ""}`}
          onClick={() => setSelectedStat("orphans")}
        >
          <div className="lbl">Orphelins</div>
          <div className="num">{orphans.length}</div>
        </div>
        <div
          className={`stat danger ${selectedStat === "missing" ? "sel" : ""}`}
          onClick={() => setSelectedStat("missing")}
        >
          <div className="lbl">Fichiers manquants</div>
          <div className="num">{missingFiles.length}</div>
        </div>
        <div
          className={`stat ${selectedStat === "undef" ? "sel" : ""}`}
          onClick={() => setSelectedStat("undef")}
        >
          <div className="lbl">Images non définies</div>
          <div className="num">{undefinedImages.length}</div>
        </div>
      </div>

      <div className="cols">
        <section className="card reveal in" style={{ animationDelay: ".10s" }}>
          <div className="card-head">
            <h3>Fichiers projet</h3>
            <span className="badge info">{assetFiles.length}</span>
          </div>
          <div className="card-body">
            <div className="toolbar-row">
              <div className="seg">
                <button
                  className={fileFilter === "all" ? "on" : ""}
                  onClick={() => setFileFilter("all")}
                >
                  Tout
                </button>
                <button
                  className={fileFilter === "image" ? "on" : ""}
                  onClick={() => setFileFilter("image")}
                >
                  Images
                </button>
                <button
                  className={fileFilter === "audio" ? "on" : ""}
                  onClick={() => setFileFilter("audio")}
                >
                  Audio
                </button>
                <button
                  className={fileFilter === "video" ? "on" : ""}
                  onClick={() => setFileFilter("video")}
                >
                  Vidéos
                </button>
              </div>
            </div>

            <div className="asset-list">
              {filteredFiles.map((file) => {
                const type = getFileType(file);
                const extLabel = file.split(".").pop()?.toUpperCase() || "FILE";
                
                return (
                  <div key={file} className={`asset-row ${type}`}>
                    <span className="ic">
                      {type === "image" ? (
                        <svg viewBox="0 0 24 24" width="15" fill="none" stroke="currentColor" strokeWidth="1.8">
                          <rect x="3" y="4" width="18" height="16" rx="2" />
                          <path d="m3 15 5-4 4 3 3-2 6 5" />
                          <circle cx="8.5" cy="9" r="1.4" />
                        </svg>
                      ) : type === "audio" ? (
                        <svg viewBox="0 0 24 24" width="15" fill="none" stroke="currentColor" strokeWidth="1.8">
                          <path d="M9 18V6l10-2v12" />
                          <circle cx="6" cy="18" r="3" />
                          <circle cx="16" cy="16" r="3" />
                        </svg>
                      ) : type === "video" ? (
                        <svg viewBox="0 0 24 24" width="15" fill="none" stroke="currentColor" strokeWidth="1.8">
                          <rect x="3" y="4" width="18" height="16" rx="2.5" />
                          <path d="m10 9 5 3-5 3z" fill="currentColor" stroke="none" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" width="15" fill="none" stroke="currentColor" strokeWidth="1.8">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                        </svg>
                      )}
                    </span>
                    <span className="path" title={file}>{file}</span>
                    <span className="size">{extLabel}</span>
                  </div>
                );
              })}
              {filteredFiles.length === 0 && (
                <p className="empty">Aucun fichier ne correspond à ce filtre.</p>
              )}
            </div>
          </div>
        </section>

        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <section className="card reveal in" style={{ animationDelay: ".15s" }}>
            <div className="card-head">
              <h3>Orphelins</h3>
              <span className="badge warn">{orphans.length}</span>
            </div>
             <div className="card-body">
               <p className="empty" style={{ marginBottom: "12px" }}>
                 Assets présents sur disque mais jamais référencés dans le script.
               </p>
               <div className="orphans" style={{ maxHeight: orphansMaxHeight }}>
                {orphans.map((orphan) => (
                  <span key={orphan} className="orphan">{orphan}</span>
                ))}
                {orphans.length === 0 && (
                  <p className="empty">Aucun asset orphelin.</p>
                )}
              </div>
            </div>
          </section>

          {missingFiles.length > 0 && (
            <section className="card reveal in" style={{ animationDelay: ".20s" }}>
              <div className="card-head">
                <h3>Fichiers manquants</h3>
                <span className="badge warn" style={{ color: "var(--danger)", background: "var(--danger-soft)" }}>
                  {missingFiles.length}
                </span>
              </div>
              <div className="card-body">
                <p className="empty" style={{ marginBottom: "12px" }}>
                  Fichiers référencés dans le script mais introuvables sur le disque.
                </p>
                <div className="orphans" style={{ maxHeight: secondaryMaxHeight }}>
                  {missingFiles.map((file) => (
                    <span
                      key={file}
                      className="orphan"
                      style={{
                        background: "var(--danger-soft)",
                        color: "var(--danger)",
                        borderColor: "color-mix(in oklab, var(--danger) 22%, transparent)",
                      }}
                    >
                      {file}
                    </span>
                  ))}
                </div>
              </div>
            </section>
          )}

          {undefinedImages.length > 0 && (
            <section className="card reveal in" style={{ animationDelay: ".25s" }}>
              <div className="card-head">
                <h3>Images non définies</h3>
                <span className="badge warn" style={{ color: "var(--danger)", background: "var(--danger-soft)" }}>
                  {undefinedImages.length}
                </span>
              </div>
              <div className="card-body">
                <p className="empty" style={{ marginBottom: "12px" }}>
                  Images utilisées dans le script de dialogue mais jamais déclarées avec une instruction image.
                </p>
                <div className="orphans" style={{ maxHeight: secondaryMaxHeight }}>
                  {undefinedImages.map((img) => (
                    <span
                      key={img}
                      className="orphan"
                      style={{
                        background: "var(--danger-soft)",
                        color: "var(--danger)",
                        borderColor: "color-mix(in oklab, var(--danger) 22%, transparent)",
                      }}
                    >
                      {img}
                    </span>
                  ))}
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
