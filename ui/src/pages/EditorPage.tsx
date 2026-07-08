import { FormEvent, PointerEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { FileContent } from "../types";

const DEFAULT_PATH = "game/script.rpy";

const TREE_FILES = [
  { path: "game/script.rpy", name: "script.rpy", isDir: false, indent: true },
  { path: "game/gui.rpy", name: "gui.rpy", isDir: false, indent: true },
  { path: "game/screens.rpy", name: "screens.rpy", isDir: false, indent: true },
  { path: "game/tl/french/script.rpy", name: "script.rpy", isDir: false, indent: true, prefix: "tl / french" },
];

function highlightCode(code: string): ReactNode[] {
  const tokens: ReactNode[] = [];
  const regex = /(#[^\n]*|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b(?:define|default|label|jump|menu|show|scene|play|stop|pass|return|init|python|if|else|elif)\b|\b[A-Z][a-zA-Z0-9_]*(?=\()|\b[a-zA-Z0-9_]+\b)/g;
  
  let match;
  let lastIdx = 0;
  while ((match = regex.exec(code)) !== null) {
    const matchText = match[0];
    const matchIdx = match.index;
    
    if (matchIdx > lastIdx) {
      tokens.push(code.substring(lastIdx, matchIdx));
    }
    
    if (matchText.startsWith("#")) {
      tokens.push(<span key={matchIdx} className="cm">{matchText}</span>);
    } else if (matchText.startsWith('"') || matchText.startsWith("'")) {
      tokens.push(<span key={matchIdx} className="str">{matchText}</span>);
    } else if (["define", "default", "label", "jump", "menu", "show", "scene", "play", "stop", "pass", "return", "init", "python", "if", "else", "elif"].includes(matchText)) {
      tokens.push(<span key={matchIdx} className="kw">{matchText}</span>);
    } else if (matchText[0] === matchText[0].toUpperCase() && matchText !== matchText.toLowerCase()) {
      tokens.push(<span key={matchIdx} className="fn">{matchText}</span>);
    } else if (matchText === "renforge_choice") {
      tokens.push(<span key={matchIdx} className="var2">{matchText}</span>);
    } else {
      tokens.push(matchText);
    }
    
    lastIdx = regex.lastIndex;
  }
  if (lastIdx < code.length) {
    tokens.push(code.substring(lastIdx));
  }
  
  return tokens.length > 0 ? tokens : [code];
}

function highlightLine(line: string) {
  return highlightCode(line);
}

export function EditorPage() {
  const [pathInput, setPathInput] = useState(DEFAULT_PATH);
  const [activePath, setActivePath] = useState(DEFAULT_PATH);
  const [file, setFile] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const codeRef = useRef<HTMLDivElement | null>(null);
  const [scrollbar, setScrollbar] = useState({ visible: false, top: 8, height: 40 });

  const updateScrollbar = useCallback(() => {
    const code = codeRef.current;
    if (!code) {
      setScrollbar({ visible: false, top: 8, height: 40 });
      return;
    }

    const maxScroll = code.scrollHeight - code.clientHeight;
    if (maxScroll <= 1) {
      setScrollbar({ visible: false, top: 8, height: 40 });
      return;
    }

    const trackHeight = Math.max(code.clientHeight - 16, 1);
    const height = Math.max(34, (code.clientHeight / code.scrollHeight) * trackHeight);
    const maxTop = Math.max(trackHeight - height, 0);
    const top = 8 + (code.scrollTop / maxScroll) * maxTop;
    setScrollbar({ visible: true, top, height });
  }, []);

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

  useEffect(() => {
    updateScrollbar();
    const code = codeRef.current;
    if (!code) {
      return;
    }

    const resizeObserver = new ResizeObserver(updateScrollbar);
    resizeObserver.observe(code);
    window.addEventListener("resize", updateScrollbar);
    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", updateScrollbar);
    };
  }, [file?.content, error, loading, updateScrollbar]);

  const handleLoad = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pathInput.trim().length === 0) {
      return;
    }
    setActivePath(pathInput.trim());
  };

  const handleTreeClick = (path: string) => {
    setPathInput(path);
    setActivePath(path);
  };

  const codeLines = file?.content ? file.content.split("\n") : ["Contenu indisponible."];

  const handleScrollbarPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    const code = codeRef.current;
    if (!code) {
      return;
    }

    event.preventDefault();
    const startY = event.clientY;
    const startScrollTop = code.scrollTop;
    const trackHeight = Math.max(code.clientHeight - 16, 1);
    const maxTop = Math.max(trackHeight - scrollbar.height, 1);
    const maxScroll = Math.max(code.scrollHeight - code.clientHeight, 1);

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      const delta = moveEvent.clientY - startY;
      code.scrollTop = startScrollTop + (delta / maxTop) * maxScroll;
    };

    const handlePointerUp = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  };

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Editor</h2>
        <span className="hint">lecture seule · scope projet backend</span>
      </div>

      <form className="path-row reveal in" style={{ animationDelay: ".05s" }} onSubmit={handleLoad}>
        <div className="field">
          <label className="field-label" htmlFor="fpath">Chemin du fichier</label>
          <input
            className="input"
            id="fpath"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary btn-pill">Charger</button>
      </form>

      <div className="ed-note reveal in" style={{ animationDelay: ".08s" }}>
        <svg viewBox="0 0 24 24" width="15" fill="none" stroke="currentColor" strokeWidth="1.8">
          <rect x="5" y="11" width="14" height="9" rx="2" />
          <path d="M8 11V8a4 4 0 0 1 8 0v3" />
        </svg>
        Le backend limite l’accès aux fichiers projet. Le chemin courant est lu en lecture seule.
      </div>

      <div className="ed-cols">
        <aside className="card reveal in" style={{ animationDelay: ".10s" }}>
          <div className="card-body">
            <div className="tree">
              <div className="row" onClick={() => handleTreeClick("game/script.rpy")}>
                <svg viewBox="0 0 24 24">
                  <path d="M3 7h6l2 2h10v10H3z" />
                </svg>
                game
              </div>
              <div className={`row indent ${activePath === "game/script.rpy" ? "on" : ""}`} onClick={() => handleTreeClick("game/script.rpy")}>
                <svg viewBox="0 0 24 24">
                  <path d="M6 3h9l3 3v15H6z" />
                </svg>
                script.rpy
              </div>
              <div className={`row indent ${activePath === "game/gui.rpy" ? "on" : ""}`} onClick={() => handleTreeClick("game/gui.rpy")}>
                <svg viewBox="0 0 24 24">
                  <path d="M6 3h9l3 3v15H6z" />
                </svg>
                gui.rpy
              </div>
              <div className={`row indent ${activePath === "game/screens.rpy" ? "on" : ""}`} onClick={() => handleTreeClick("game/screens.rpy")}>
                <svg viewBox="0 0 24 24">
                  <path d="M6 3h9l3 3v15H6z" />
                </svg>
                screens.rpy
              </div>
              
              <div style={{ marginTop: "8px" }} />
              
              <div className="row" onClick={() => handleTreeClick("game/tl/french/script.rpy")}>
                <svg viewBox="0 0 24 24">
                  <path d="M3 7h6l2 2h10v10H3z" />
                </svg>
                tl / french
              </div>
              <div className={`row indent ${activePath === "game/tl/french/script.rpy" ? "on" : ""}`} onClick={() => handleTreeClick("game/tl/french/script.rpy")}>
                <svg viewBox="0 0 24 24">
                  <path d="M6 3h9l3 3v15H6z" />
                </svg>
                script.rpy
              </div>
            </div>
          </div>
        </aside>

        <section className="editor reveal in" style={{ animationDelay: ".14s" }}>
          <div className="ed-tabbar">
            <span className="ed-tab">
              {activePath}
              <span className="ro">RO</span>
            </span>
          </div>

          {loading ? (
            <div className="code" style={{ padding: "20px", color: "var(--muted)" }}>
              Chargement de {activePath}…
            </div>
          ) : error ? (
            <div className="code" style={{ padding: "20px", color: "var(--danger)" }}>
              Impossible de charger {activePath} : {error}
            </div>
          ) : (
            <div className="code-shell">
              <div className="code" ref={codeRef} onScroll={updateScrollbar}>
                <div className="gutter">
                  {codeLines.map((_, i) => (
                    <span key={i}>{i + 1}</span>
                  ))}
                </div>
                <div className="lines">
                  {codeLines.map((line, i) => (
                    <span key={i} className="codeLine">
                      {highlightLine(line)}
                    </span>
                  ))}
                </div>
              </div>
              {scrollbar.visible && (
                <div className="code-scrollbar" aria-hidden="true">
                  <div
                    className="code-scrollbar-thumb"
                    onPointerDown={handleScrollbarPointerDown}
                    style={{ height: `${scrollbar.height}px`, transform: `translateY(${scrollbar.top}px)` }}
                  />
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
