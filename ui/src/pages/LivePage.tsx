import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DebugBridgeEvent, LiveChoice, LiveScreenshot, LiveState } from "../types";

const POLL_MS = 1800;

const formatUnknown = (value: unknown) => {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
};

function parseVariableValue(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function describeEvent(event: DebugBridgeEvent): string {
  if (event.type === "label") {
    return `Label: ${String(event.label ?? "unknown")}`;
  }
  if (event.type === "say") {
    return `Say: ${String(event.what ?? "")}`;
  }
  if (event.type === "exception") {
    return `Exception: ${String(event.short ?? event.full ?? "runtime error")}`;
  }
  return formatUnknown(event);
}

interface LivePageProps {
  liveState?: LiveState | null;
  liveFrame?: LiveScreenshot | null;
}

export function LivePage({ liveState = null, liveFrame = null }: LivePageProps = {}) {
  const [state, setState] = useState<LiveState | null>(null);
  const [screenshot, setScreenshot] = useState<LiveScreenshot | null>(null);
  const [choices, setChoices] = useState<LiveChoice[]>([]);
  const [events, setEvents] = useState<DebugBridgeEvent[]>([]);
  const [expr, setExpr] = useState("");
  const [evalResult, setEvalResult] = useState<string>("");
  const [setVarName, setSetVarName] = useState("");
  const [setVarValue, setSetVarValue] = useState("");
  const [status, setStatus] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [stoppedByUser, setStoppedByUser] = useState(false);
  const eventCursor = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const [liveStateVal, liveChoices, liveEvents] = await Promise.all([
        api.fetchLiveState(),
        api.fetchLiveChoices(),
        api.fetchDebugEvents(eventCursor.current).catch(() => ({ events: [], cursor: eventCursor.current })),
      ]);
      const frame = await api.fetchLiveScreenshot().catch(() => null);
      setState(liveStateVal);
      setChoices(liveChoices.choices);
      if (typeof liveEvents.cursor === "number") {
        eventCursor.current = liveEvents.cursor;
      }
      if (liveEvents.events.length > 0) {
        setEvents((current) => [...current, ...liveEvents.events].slice(-80));
      }
      if (frame) {
        setScreenshot(frame);
      } else {
        setScreenshot(null);
      }
      setStatus("live");
    } catch (_error) {
      if (!stoppedByUser) {
        setStatus("stopped");
      }
      setState(null);
      setChoices([]);
      setScreenshot(null);
    } finally {
      setLoading(false);
    }
  }, [stoppedByUser]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const runAction = async (action: () => Promise<unknown>, successMsg = "action ok", actionId = "action") => {
    if (busyAction) {
      return;
    }
    setBusyAction(actionId);
    try {
      const result = await action();
      if (result && typeof result === "object" && "ok" in result && (result as { ok?: unknown }).ok === false) {
        const error = (result as { error?: string }).error;
        throw new Error(error || "action failed");
      }
      setStatus(successMsg);
      window.setTimeout(() => {
        void refresh();
      }, 250);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "action failed");
    } finally {
      setBusyAction(null);
    }
  };

  const onLaunchGame = async () => {
    if (busyAction || displayedState) {
      return;
    }
    setBusyAction("launch");
    setStoppedByUser(false);
    eventCursor.current = 0;
    setEvents([]);
    try {
      const result = await api.launchGame();
      setStatus(result.already_running ? "already running" : "launched");
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "launch failed");
    } finally {
      setBusyAction(null);
    }
  };

  const onStopGame = async () => {
    if (busyAction) {
      return;
    }
    setBusyAction("stop");
    try {
      const result = await api.stopGame();
      setStoppedByUser(true);
      setState(null);
      setScreenshot(null);
      setChoices([]);
      eventCursor.current = 0;
      setEvents([]);
      setStatus(result.was_running ? "stopped" : "already stopped");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "stop failed");
    } finally {
      setBusyAction(null);
    }
  };

  const onAdvance = async () => runAction(() => api.control("advance"), "advanced", "advance");

  const onRollback = async () => {
    runAction(() => api.control("rollback"), "rollback ok", "rollback");
  };

  const onToggleSkip = async () => {
    runAction(() => api.control("toggle_skip"), "skip ok", "skip");
  };

  const onToggleAuto = async () => {
    runAction(() => api.control("toggle_auto"), "auto ok", "auto");
  };
  const onQuickSave = async () => {
    runAction(() => api.control("quick_save"), "save ok", "save");
  };

  const onQuickLoad = async () => {
    runAction(() => api.control("quick_load"), "load ok", "load");
  };

  const onQuit = async () => {
    await runAction(() => api.control("quit"), "quit", "quit");
    setStoppedByUser(true);
    setState(null);
    setScreenshot(null);
    setChoices([]);
    eventCursor.current = 0;
    setEvents([]);
    setStatus("stopped");
  };

  const onReloadGame = async () => {
    runAction(() => api.control("reload_script"), "reload ok", "reload");
  };

  const onRestartInteraction = async () => {
    runAction(() => api.control("restart_interaction"), "interface restarted", "restart-ui");
  };

  const onEval = async (submitEvent: FormEvent<HTMLFormElement>) => {
    submitEvent.preventDefault();
    if (!expr.trim()) {
      return;
    }
    try {
      const response = await api.evaluate(expr);
      setEvalResult(`${expr}  =  ${formatUnknown(response.value)}`);
      setStatus("evaluation ok");
    } catch (error) {
      setEvalResult("");
      setStatus(error instanceof Error ? error.message : "evaluation failed");
    }
  };

  const onSetVar = async (submitEvent: FormEvent<HTMLFormElement>) => {
    submitEvent.preventDefault();
    if (!setVarName.trim()) {
      return;
    }
    await runAction(() => api.setVariable(setVarName, parseVariableValue(setVarValue)), `defined: ${setVarName}`, "set-var");
  };

  const onSelectChoice = async (index: number, text: string) => {
    await runAction(() => api.selectChoice(index, text), "choice selected", "choice");
    await refresh();
  };

  const displayedState = stoppedByUser ? null : state ?? liveState;
  const displayedFrame = stoppedByUser ? null : screenshot ?? liveFrame;
  const isRunning = Boolean(displayedState);
  const controlsDisabled = Boolean(busyAction) || !isRunning;
  const statusLabel = stoppedByUser
    ? "stopped"
    : busyAction === "launch"
      ? "launching..."
      : busyAction === "stop"
        ? "stopping..."
        : loading
          ? "syncing..."
          : status || (isRunning ? "live" : "stopped");
  const statusClass = isRunning ? "ok" : busyAction ? "warn" : "off";
  const tags = displayedState?.showing_tags ?? [];
  const variables = displayedState?.variables ?? {};
  const narrativeChoices = displayedState?.menu
    ? choices.filter((choice) => !choice.screen || choice.screen === "choice")
    : [];

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Live</h2>
        <span className="hint">bridge · port 42547</span>
      </div>

      <div className="live-grid">
        <section className="card preview-card reveal in" style={{ animationDelay: ".02s" }}>
          <div className="card-head">
            <h3>Preview</h3>
            <span className={`badge ${statusClass}`}>
              <span className="dot" style={{ width: "6px", height: "6px" }} />
              {statusLabel}
            </span>
          </div>
          <div className="card-body">
            <div className="scene">
              {displayedFrame ? (
                <img
                  src={`data:image/${displayedFrame.format};base64,${displayedFrame.base64}`}
                  alt="Live preview"
                />
              ) : (
                <div className="empty-box">No image — waiting for bridge</div>
              )}
            </div>
            <div className="transport">
              <button className="tctl primary" type="button" onClick={onLaunchGame} disabled={Boolean(busyAction) || isRunning}>
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
                Launch game
              </button>
              <button className="tctl warn" type="button" onClick={onStopGame} disabled={Boolean(busyAction)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
                Stop game
              </button>
              <button className="tctl" type="button" onClick={onRollback} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M15 6 9 12l6 6" />
                </svg>
                Back
              </button>
              <button className="tctl primary" type="button" onClick={onAdvance} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
                Advance
              </button>
              <button className="tctl" type="button" onClick={onToggleSkip} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M5 5v14l9-7zM15 5h3v14h-3z" />
                </svg>
                Skip
              </button>
              <button className="tctl" type="button" onClick={onToggleAuto} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 12a8 8 0 1 1 3 6.2" />
                  <path d="M4 20v-4h4" />
                </svg>
                Auto
              </button>
              <button className="tctl" type="button" onClick={onQuickSave} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M5 3h11l3 3v15H5z" />
                  <path d="M8 3v5h7M8 14h8v7H8z" />
                </svg>
                Save
              </button>
              <button className="tctl" type="button" onClick={onQuickLoad} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M3 7h6l2 2h10v10H3z" />
                </svg>
                Load
              </button>
              <button className="tctl" type="button" onClick={onReloadGame} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                  <path d="M21 12a9 9 0 1 1-2.6-6.3" />
                  <path d="M21 4v4h-4" />
                </svg>
                Reload
              </button>
              <button className="tctl warn" type="button" onClick={onQuit} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
                Quit
              </button>
            </div>
            {narrativeChoices.length > 0 && (
              <div className="live-choices">
                <div className="live-choices-head">
                  <h4>Narrative choice</h4>
                  <span className="badge warn">interactive</span>
                </div>
                <div className="choice-list">
                  {narrativeChoices.map((choice) => (
                    <div key={`${choice.text}-${choice.index}`} className="choice-item">
                      <span>{choice.text}</span>
                      <button
                        className="btn btn-primary"
                        onClick={() => onSelectChoice(choice.index, choice.text)}
                        disabled={Boolean(busyAction)}
                      >
                        Choose
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>

        <section className="card reveal in" style={{ animationDelay: ".10s" }}>
          <div className="card-head">
            <h3>Current state</h3>
            <span className="badge info">runtime</span>
          </div>
          <div className="card-body">
            <div className="state-row">
              <span className="k">Label</span>
              <span className="v">{displayedState?.current_label || "start"}</span>
            </div>
            <div className="state-row">
              <span className="k">Menu</span>
              <span className="v">{displayedState?.menu ? "active" : "inactive"}</span>
            </div>
            <div className="state-row">
              <span className="k">Tags</span>
              <span className="v">{tags.length ? tags.join(", ") : "—"}</span>
            </div>
            <div className="state-row">
              <span className="k">Bridge port</span>
              <span className="v">42547</span>
            </div>
            <div className="vars">
              <div className="vhead">Store variables</div>
              {Object.entries(variables)
                .filter(([key]) => !key.startsWith("_") && !key.startsWith("IMG_"))
                .slice(0, 12)
                .map(([key, value]) => {
                  let valClass = "num";
                  if (value === null || value === undefined) {
                    valClass = "n";
                  } else if (typeof value === "boolean") {
                    valClass = value ? "t" : "f";
                  }
                  return (
                    <div className="var" key={key}>
                      <span className="name">{key}</span>
                      <span className={`val ${valClass}`}>{formatUnknown(value)}</span>
                    </div>
                  );
                })}
              {Object.keys(variables).length === 0 && (
                <div className="var"><span className="name" style={{ color: "var(--meta)" }}>No variables</span></div>
              )}
            </div>
          </div>
        </section>

        <section className="card reveal in" style={{ animationDelay: ".16s" }}>
          <div className="card-head">
            <h3>Console</h3>
            <span className="badge off">eval</span>
          </div>
          <div className="card-body">
            <form onSubmit={onEval}>
              <label className="field-label" htmlFor="expr">Python expression</label>
              <div className="console-row">
                <input
                  className="input"
                  id="expr"
                  value={expr}
                  onChange={(e) => setExpr(e.target.value)}
                  placeholder="store.persistent.score + 1"
                />
                <button type="submit" className="btn btn-primary" disabled={controlsDisabled}>Eval</button>
              </div>
            </form>
            <div className="console-out">
              {evalResult ? (
                <>
                  <span className="pf">›</span>
                  <span className="rs">{evalResult}</span>
                </>
              ) : (
                "→ The evaluation result appears here."
              )}
            </div>

            <form onSubmit={onSetVar} className="vars" style={{ marginTop: "16px" }}>
              <div className="vhead">Variable watch</div>
              <div className="two-col">
                <div>
                  <label className="field-label" htmlFor="wname">Variable</label>
                  <input
                    className="input"
                    id="wname"
                    value={setVarName}
                    onChange={(e) => setSetVarName(e.target.value)}
                    placeholder="money"
                  />
                </div>
                <div>
                  <label className="field-label" htmlFor="wval">JSON or text value</label>
                  <input
                    className="input"
                    id="wval"
                    value={setVarValue}
                    onChange={(e) => setSetVarValue(e.target.value)}
                    placeholder='"hello" or 42'
                  />
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "11px" }}>
                <button type="submit" className="btn btn-ghost" disabled={controlsDisabled}>Set</button>
              </div>
            </form>

            <details className="live-advanced">
              <summary>
                Advanced runtime
                <span>{events.length} events</span>
              </summary>
              <div className="live-advanced-actions">
                <button type="button" className="btn btn-ghost" onClick={onRestartInteraction} disabled={controlsDisabled}>
                  Restart UI
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => void refresh()} disabled={Boolean(busyAction)}>
                  Refresh
                </button>
              </div>
              <div className="live-events">
                {events.length > 0 ? [...events].reverse().map((event, index) => (
                  <div className={`live-event ${event.type === "exception" ? "error" : ""}`} key={`${event.seq ?? index}-${event.type ?? "event"}`}>
                    <span>{event.seq ?? "-"}</span>
                    <p>{describeEvent(event)}</p>
                  </div>
                )) : (
                  <p className="muted">No runtime event received yet.</p>
                )}
              </div>
            </details>
          </div>
        </section>

      </div>
    </div>
  );
}
