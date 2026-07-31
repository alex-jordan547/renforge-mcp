import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { ProjectBrowserResponse } from "../types";

interface ProjectPickerProps {
  open: boolean;
  onClose: () => void;
  onSelected: (project: string) => void;
}

/** 将后端返回的 root.id 映射为翻译后的标签。 */
function translateRootLabel(rootId: string, t: ReturnType<typeof useTranslation>['t']): string {
  switch (rootId) {
    case 'current-project': return t('projectPicker.roots.current-project');
    case 'project-parent': return t('projectPicker.roots.project-parent');
    case 'home': return t('projectPicker.roots.home');
    case 'windows-drives': return t('projectPicker.roots.windows-drives');
    default:
      if (rootId.startsWith('drive-')) {
        const letter = rootId.replace('drive-', '').toUpperCase();
        return t('projectPicker.roots.drive', { letter });
      }
      return rootId;
  }
}

export function ProjectPicker({ open, onClose, onSelected }: ProjectPickerProps) {
  const [browser, setBrowser] = useState<ProjectBrowserResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { t } = useTranslation();
  const renpyLabel = "Ren'Py";
  const folderMarker = "□";
  const closeGlyph = "×";
  const loadErrorLabel = t("projectPicker.loadError");
  const switchErrorLabel = t("projectPicker.switchError");

  const load = async (rootId?: string, path = "") => {
    setLoading(true);
    setError(null);
    try {
      setBrowser(await api.browseProjects(rootId, path));
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : null;
      setError(detail ? `${loadErrorLabel}: ${detail}` : loadErrorLabel);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      void load();
    }
  }, [open]);

  if (!open) {
    return null;
  }

  const selectCurrent = async () => {
    if (!browser) {
      return;
    }
    setSelecting(true);
    setError(null);
    try {
      const result = await api.selectProject(browser.root_id, browser.path);
      onSelected(result.project);
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : null;
      setError(detail ? `${switchErrorLabel}: ${detail}` : switchErrorLabel);
    } finally {
      setSelecting(false);
    }
  };

  return (
    <div className="project-picker-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="project-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-picker-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="project-picker-header">
          <div>
            <p className="eyebrow">{t("projectPicker.title")}</p>
            <h2 id="project-picker-title">{`${t("projectPicker.open")} ${renpyLabel} ${t("projectPicker.project")}`}</h2>
          </div>
          <button className="project-picker-close" type="button" onClick={onClose} aria-label={t("projectPicker.close")}>
            <span aria-hidden="true">{closeGlyph}</span>
          </button>
        </header>

        <div className="project-picker-roots" aria-label={t("projectPicker.browseRoots")}>
          {browser?.roots.map((root) => (
            <button
              key={root.id}
              className={`project-picker-root ${root.id === browser.root_id ? "active" : ""}`}
              type="button"
              disabled={loading || selecting}
              onClick={() => void load(root.id)}
            >
              {translateRootLabel(root.id, t)}
            </button>
          ))}
        </div>

        <div className="project-picker-path">
          <button
            className="btn btn-ghost"
            type="button"
            disabled={!browser?.path || loading || selecting}
            onClick={() => browser && void load(browser.root_id, browser.parent_path)}
            >
            {t("projectPicker.parent")}
          </button>
          <code>{browser ? (browser.path || browser.roots.find((root) => root.id === browser.root_id)?.path) : t("projectPicker.loadingPath")}</code>
        </div>

        {error && <p className="project-picker-error" role="alert">{error}</p>}

        <div className="project-picker-list" aria-busy={loading}>
          {loading && <p className="muted">{t("projectPicker.loadingFolders")}</p>}
          {!loading && browser?.entries.length === 0 && <p className="muted">{t("projectPicker.noFolders")}</p>}
          {!loading && browser?.entries.map((entry) => (
            <button
              key={entry.path}
              className="project-picker-entry"
              type="button"
              disabled={selecting}
              onClick={() => void load(browser.root_id, entry.path)}
            >
              <span className="project-picker-folder" aria-hidden="true">{folderMarker}</span>
              <span className="project-picker-entry-name">{entry.name}</span>
              {entry.project && <span className="project-picker-badge">{renpyLabel}</span>}
            </button>
          ))}
        </div>

        <footer className="project-picker-footer">
          <span className="muted">
            {browser?.project ? t("projectPicker.containsGame") : t("projectPicker.chooseGame")}
          </span>
          <div>
            <button className="btn btn-ghost" type="button" onClick={onClose} disabled={selecting}>
              {t("app.cancel")}
            </button>
            <button
              className="btn btn-primary"
              type="button"
              disabled={!browser?.project || loading || selecting}
              onClick={() => void selectCurrent()}
            >
              {selecting ? t("projectPicker.opening") : t("projectPicker.openProject")}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
