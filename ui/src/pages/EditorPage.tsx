import { FormEvent, PointerEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { FileContent } from "../types";

const DEFAULT_PATH = "game/script.rpy";

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

interface DirNode {
  name: string;
  path: string;
  dirs: DirNode[];
  files: { name: string; path: string }[];
}

function buildTree(paths: string[]): DirNode {
  const root: DirNode = { name: "game", path: "game", dirs: [], files: [] };
  const dirMap = new Map<string, DirNode>([["game", root]]);
  for (const full of paths) {
    const parts = full.split("/");
    if (parts[0] !== "game" || parts.length < 2) {
      continue;
    }
    let parentPath = "game";
    for (let i = 1; i < parts.length - 1; i += 1) {
      const dirPath = `${parentPath}/${parts[i]}`;
      let node = dirMap.get(dirPath);
      if (!node) {
        node = { name: parts[i], path: dirPath, dirs: [], files: [] };
        dirMap.get(parentPath)?.dirs.push(node);
        dirMap.set(dirPath, node);
      }
      parentPath = dirPath;
    }
    dirMap.get(parentPath)?.files.push({ name: parts[parts.length - 1], path: full });
  }
  return root;
}

function TreeFolder({
  node,
  depth,
  collapsed,
  onToggle,
  activePath,
  onOpen,
}: {
  node: DirNode;
  depth: number;
  collapsed: Set<string>;
  onToggle: (path: string) => void;
  activePath: string;
  onOpen: (path: string) => void;
}) {
  const isCollapsed = collapsed.has(node.path);
  return (
    <div>
      <button
        type="button"
        className="tree-row tree-folder"
        style={{ paddingLeft: `${depth * 14 + 10}px` }}
        onClick={() => onToggle(node.path)}
        aria-expanded={!isCollapsed}
      >
        <svg className={`tree-chevron ${isCollapsed ? "" : "open"}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m9 6 6 6-6 6" />
        </svg>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 7h6l2 2h10v10H3z" />
        </svg>
        {node.name}
      </button>
      {!isCollapsed && (
        <>
          {node.dirs.map((dir) => (
            <TreeFolder
              key={dir.path}
              node={dir}
              depth={depth + 1}
              collapsed={collapsed}
              onToggle={onToggle}
              activePath={activePath}
              onOpen={onOpen}
            />
          ))}
          {node.files.map((entry) => (
            <button
              key={entry.path}
              type="button"
              className={`tree-row tree-file ${activePath === entry.path ? "on" : ""}`}
              style={{ paddingLeft: `${(depth + 1) * 14 + 10}px` }}
              onClick={() => onOpen(entry.path)}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 3h9l3 3v15H6z" />
              </svg>
              {entry.name}
            </button>
          ))}
        </>
      )}
    </div>
  );
}

export function EditorPage() {
  const [pathInput, setPathInput] = useState(DEFAULT_PATH);
  const [activePath, setActivePath] = useState(DEFAULT_PATH);
  const [file, setFile] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const codeRef = useRef<HTMLDivElement | null>(null);
  const [scrollbar, setScrollbar] = useState({ visible: false, top: 8, height: 40 });
  const [scriptFiles, setScriptFiles] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    let mounted = true;
    api.fetchScriptFiles()
      .then((response) => {
        if (mounted && response.ok) {
          setScriptFiles(response.files);
        }
      })
      .catch((err) => console.error("Failed to list project scripts", err));
    return () => {
      mounted = false;
    };
  }, []);

  const handleToggleFolder = (path: string) => {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const handleOpenFile = (path: string) => {
    setPathInput(path);
    setActivePath(path);
  };

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

  const codeLines = file?.content ? file.content.split("\n") : ["Content unavailable."];

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
        <h2>Script Reader</h2>
        <span className="hint">read-only · backend project scope</span>
      </div>

      <form className="path-row reveal in" style={{ animationDelay: ".05s" }} onSubmit={handleLoad}>
        <div className="field">
          <label className="field-label" htmlFor="fpath">File path</label>
          <input
            className="input"
            id="fpath"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary btn-pill">Load</button>
      </form>

      <div className="ed-note reveal in" style={{ animationDelay: ".08s" }}>
        <svg viewBox="0 0 24 24" width="15" fill="none" stroke="currentColor" strokeWidth="1.8">
          <rect x="5" y="11" width="14" height="9" rx="2" />
          <path d="M8 11V8a4 4 0 0 1 8 0v3" />
        </svg>
        Read-only view of the project&apos;s scripts. Pick a file on the left or type a path under <code>game/</code>.
      </div>

      <div className="ed-cols reveal in" style={{ animationDelay: ".10s" }}>
        <aside className="tree" aria-label="Project scripts">
          {scriptFiles.length === 0 ? (
            <div className="tree-empty">No .rpy files found.</div>
          ) : (
            <TreeFolder
              node={buildTree(scriptFiles)}
              depth={0}
              collapsed={collapsed}
              onToggle={handleToggleFolder}
              activePath={activePath}
              onOpen={handleOpenFile}
            />
          )}
        </aside>

        <section className="editor">
          <div className="ed-tabbar">
            <span className="ed-tab">
              {activePath}
              <span className="ro">RO</span>
            </span>
          </div>

          {loading ? (
            <div className="code" style={{ padding: "20px", color: "var(--muted)" }}>
              Loading {activePath}…
            </div>
          ) : error ? (
            <div className="code" style={{ padding: "20px", color: "var(--danger)" }}>
              Could not load {activePath}: {error}
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
