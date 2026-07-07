import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { FileContent } from "../types";

const DEFAULT_PATH = "game/script.rpy";

export function EditorPage() {
  const [pathInput, setPathInput] = useState(DEFAULT_PATH);
  const [activePath, setActivePath] = useState(DEFAULT_PATH);
  const [file, setFile] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const loaded = await api.fetchFile(activePath);
        if (!mounted) {
          return;
        }
        setFile(loaded);
      } catch (err) {
        if (!mounted) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load file");
        setFile(null);
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
  }, [activePath]);

  const handleLoad = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pathInput.trim().length === 0) {
      return;
    }
    setActivePath(pathInput.trim());
  };

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Editor</h2>
        <span>Lecture seule, limité au scope projet backend</span>
      </div>
      <form className="fileForm" onSubmit={handleLoad}>
        <label htmlFor="editor-path">
          Chemin du fichier
          <input
            id="editor-path"
            value={pathInput}
            onChange={(event) => setPathInput(event.target.value)}
            placeholder="game/script.rpy"
            type="text"
          />
        </label>
        <button type="submit" className="btn primary">
          Charger
        </button>
      </form>
      <p className="muted">
        Le backend limite l’accès aux fichiers projet. Le chemin courant est lu en lecture seule.
      </p>

      {loading ? (
        <div className="spinner">Chargement de <code>{activePath}</code>…</div>
      ) : error ? (
        <p className="errorText">Impossible de charger <code>{activePath}</code> : {error}</p>
      ) : (
        <pre className="codeBlock numbered">
          <code>{(file?.content ?? "Contenu indisponible.").split("\n").map((line, i) => (
            <span key={i} className="codeLine">{line}</span>
          ))}</code>
        </pre>
      )}
    </section>
  );
}
