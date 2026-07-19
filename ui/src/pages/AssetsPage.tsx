import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { AssetsResponse } from "../types";

type AssetStat = "files" | "orphans" | "missing" | "undef";

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
  const [selectedStat, setSelectedStat] = useState<AssetStat>("files");
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

  const selectStat = (stat: AssetStat) => {
    setSelectedStat(stat);
    if (stat === "files") {
      setFileFilter("all");
    }
    window.requestAnimationFrame(() => {
      document.getElementById(`assets-${stat}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  };

  if (loading) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Assets</h2>
          <span className="hint">inventory · /api/assets</span>
        </div>
        <div className="statusLine">Loading inventory…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="wrap">
        <div className="page-head reveal in">
          <h2>Assets</h2>
          <span className="hint">inventory · /api/assets</span>
        </div>
        <p className="errorText">Unable to load assets: {error}</p>
      </div>
    );
  }

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Assets</h2>
        <span className="hint">inventory · /api/assets</span>
      </div>

      <div className="stat-grid reveal in" style={{ animationDelay: ".05s" }}>
        <button
          type="button"
          className={`stat accent ${selectedStat === "files" ? "sel" : ""}`}
          aria-pressed={selectedStat === "files"}
          onClick={() => selectStat("files")}
        >
          <span className="lbl">Project files</span>
          <span className="num">{assetFiles.length}</span>
        </button>
        <button
          type="button"
          className={`stat warn ${selectedStat === "orphans" ? "sel" : ""}`}
          aria-pressed={selectedStat === "orphans"}
          onClick={() => selectStat("orphans")}
        >
          <span className="lbl">Orphans</span>
          <span className="num">{orphans.length}</span>
        </button>
        <button
          type="button"
          className={`stat danger ${selectedStat === "missing" ? "sel" : ""}`}
          aria-pressed={selectedStat === "missing"}
          onClick={() => selectStat("missing")}
          disabled={missingFiles.length === 0}
        >
          <span className="lbl">Missing files</span>
          <span className="num">{missingFiles.length}</span>
        </button>
        <button
          type="button"
          className={`stat ${selectedStat === "undef" ? "sel" : ""}`}
          aria-pressed={selectedStat === "undef"}
          onClick={() => selectStat("undef")}
          disabled={undefinedImages.length === 0}
        >
          <span className="lbl">Undefined images</span>
          <span className="num">{undefinedImages.length}</span>
        </button>
      </div>

      <div className="cols">
        <section id="assets-files" className={`card reveal in ${selectedStat === "files" ? "asset-focus" : ""}`} style={{ animationDelay: ".10s" }}>
          <div className="card-head">
            <h3>Project files</h3>
            <span className="badge info">{assetFiles.length}</span>
          </div>
          <div className="card-body">
            <div className="toolbar-row">
              <div className="seg">
                <button
                  className={fileFilter === "all" ? "on" : ""}
                  onClick={() => setFileFilter("all")}
                >
                  All
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
                  Videos
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
                <p className="empty">No file matches this filter.</p>
              )}
            </div>
          </div>
        </section>

        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <section id="assets-orphans" className={`card reveal in ${selectedStat === "orphans" ? "asset-focus" : ""}`} style={{ animationDelay: ".15s" }}>
            <div className="card-head">
              <h3>Orphans</h3>
              <span className="badge warn">{orphans.length}</span>
            </div>
             <div className="card-body">
               <p className="empty" style={{ marginBottom: "12px" }}>
                  Assets on disk never referenced in the script.
               </p>
               <div className="orphans" style={{ maxHeight: orphansMaxHeight }}>
                {orphans.map((orphan) => (
                  <span key={orphan} className="orphan">{orphan}</span>
                ))}
                {orphans.length === 0 && (
                  <p className="empty">No orphaned asset.</p>
                )}
              </div>
            </div>
          </section>

          {missingFiles.length > 0 && (
            <section id="assets-missing" className={`card reveal in ${selectedStat === "missing" ? "asset-focus" : ""}`} style={{ animationDelay: ".20s" }}>
              <div className="card-head">
                <h3>Missing files</h3>
                <span className="badge warn" style={{ color: "var(--danger)", background: "var(--danger-soft)" }}>
                  {missingFiles.length}
                </span>
              </div>
              <div className="card-body">
                <p className="empty" style={{ marginBottom: "12px" }}>
                  Files referenced in the script but missing on disk.
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
            <section id="assets-undef" className={`card reveal in ${selectedStat === "undef" ? "asset-focus" : ""}`} style={{ animationDelay: ".25s" }}>
              <div className="card-head">
                <h3>Undefined images</h3>
                <span className="badge warn" style={{ color: "var(--danger)", background: "var(--danger-soft)" }}>
                  {undefinedImages.length}
                </span>
              </div>
              <div className="card-body">
                <p className="empty" style={{ marginBottom: "12px" }}>
                  Images used in dialogue scripts but never declared with an image statement.
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
