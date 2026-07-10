import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProjectBrowserResponse } from "../types";

interface ProjectPickerProps {
  open: boolean;
  onClose: () => void;
  onSelected: (project: string) => void;
}

export function ProjectPicker({ open, onClose, onSelected }: ProjectPickerProps) {
  const [browser, setBrowser] = useState<ProjectBrowserResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (rootId?: string, path = "") => {
    setLoading(true);
    setError(null);
    try {
      setBrowser(await api.browseProjects(rootId, path));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not browse folders");
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
      setError(reason instanceof Error ? reason.message : "Could not switch projects");
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
            <p className="eyebrow">Project</p>
            <h2 id="project-picker-title">Open Ren&apos;Py project</h2>
          </div>
          <button className="project-picker-close" type="button" onClick={onClose} aria-label="Close project picker">
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <div className="project-picker-roots" aria-label="Browse roots">
          {browser?.roots.map((root) => (
            <button
              key={root.id}
              className={`project-picker-root ${root.id === browser.root_id ? "active" : ""}`}
              type="button"
              disabled={loading || selecting}
              onClick={() => void load(root.id)}
            >
              {root.label}
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
            Up
          </button>
          <code>{browser ? (browser.path || browser.roots.find((root) => root.id === browser.root_id)?.path) : "Loading…"}</code>
        </div>

        {error && <p className="project-picker-error" role="alert">{error}</p>}

        <div className="project-picker-list" aria-busy={loading}>
          {loading && <p className="muted">Loading folders…</p>}
          {!loading && browser?.entries.length === 0 && <p className="muted">No folders here.</p>}
          {!loading && browser?.entries.map((entry) => (
            <button
              key={entry.path}
              className="project-picker-entry"
              type="button"
              disabled={selecting}
              onClick={() => void load(browser.root_id, entry.path)}
            >
              <span className="project-picker-folder" aria-hidden="true">□</span>
              <span className="project-picker-entry-name">{entry.name}</span>
              {entry.project && <span className="project-picker-badge">Ren&apos;Py</span>}
            </button>
          ))}
        </div>

        <footer className="project-picker-footer">
          <span className="muted">
            {browser?.project ? "This folder contains game/." : "Choose a folder containing game/."}
          </span>
          <div>
            <button className="btn btn-ghost" type="button" onClick={onClose} disabled={selecting}>Cancel</button>
            <button
              className="btn btn-primary"
              type="button"
              disabled={!browser?.project || loading || selecting}
              onClick={() => void selectCurrent()}
            >
              {selecting ? "Opening…" : "Open project"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
