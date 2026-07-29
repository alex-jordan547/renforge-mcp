import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { DebugBridgeEvent, LiveChoice, LiveScreenshot, LiveState } from "../types";

const POLL_MS = 1800;
const BRIDGE_PORT = 42547;
const DASH = "—";
const PROMPT_ARROW = "›";

interface Translator {
  (key: string, options?: Record<string, unknown>): string;
}

type LiveStatus = {
  key?: string;
  params?: Record<string, unknown>;
  raw?: string;
};

function formatStatus(status: LiveStatus | null, t: Translator) {
  if (!status) {
    return "";
  }
  if (status.raw) {
    return status.raw;
  }
  if (status.key) {
    return t(status.key, status.params ?? {});
  }
  return t("errors.untranslated", status.params ?? {});
}

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

function describeEvent(event: DebugBridgeEvent, t: Translator): string {
  if (event.type === "label") {
    return t("pages.live.event.label", { label: String(event.label ?? "unknown") });
  }
  if (event.type === "say") {
    return t("pages.live.event.say", { text: String(event.what ?? "") });
  }
  if (event.type === "exception") {
    return t("pages.live.event.exception", { message: String(event.short ?? event.full ?? "runtime error") });
  }
  return formatUnknown(event);
}

interface LivePageProps {
  liveState?: LiveState | null;
  liveFrame?: LiveScreenshot | null;
}

export function LivePage({ liveState = null, liveFrame = null }: LivePageProps = {}) {
  const { t } = useTranslation();

  const [state, setState] = useState<LiveState | null>(null);
  const [screenshot, setScreenshot] = useState<LiveScreenshot | null>(null);
  const [choices, setChoices] = useState<LiveChoice[]>([]);
  const [events, setEvents] = useState<DebugBridgeEvent[]>([]);
  const [expr, setExpr] = useState("");
  const [evalResult, setEvalResult] = useState<string>("");
  const [setVarName, setSetVarName] = useState("");
  const [setVarValue, setSetVarValue] = useState("");
  const [status, setStatus] = useState<LiveStatus | null>(null);
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
      setStatus({ key: "pages.live.status.live" });
    } catch (_error) {
      if (!stoppedByUser) {
        setStatus({ key: "pages.live.status.stopped" });
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

  const runAction = async (
    action: () => Promise<unknown>,
    successKey = "pages.live.status.actionOk",
    actionId = "action",
  ) => {
    if (busyAction) {
      return;
    }
    setBusyAction(actionId);
    try {
      const result = await action();
      if (result && typeof result === "object" && "ok" in result && (result as { ok?: unknown }).ok === false) {
        const error = (result as { error?: string }).error;
        throw new Error(error || t("pages.live.status.actionFailed"));
      }
      setStatus({ key: successKey });
      window.setTimeout(() => {
        void refresh();
      }, 250);
    } catch (error) {
      setStatus({
        raw: error instanceof Error ? error.message : t("pages.live.status.actionFailed"),
      });
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
      setStatus({
        key: result.already_running ? "pages.live.status.launch.alreadyRunning" : "pages.live.status.launch.success",
      });
      await refresh();
    } catch (error) {
      setStatus({
        raw: error instanceof Error ? error.message : t("pages.live.status.launch.failed"),
      });
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
      setStatus({
        key: result.was_running ? "pages.live.status.stop.success" : "pages.live.status.stop.alreadyStopped",
      });
    } catch (error) {
      setStatus({
        raw: error instanceof Error ? error.message : t("pages.live.status.stop.failed"),
      });
    } finally {
      setBusyAction(null);
    }
  };

  const onAdvance = async () => runAction(() => api.control("advance"), "pages.live.status.advance.success", "advance");

  const onRollback = async () => {
    runAction(() => api.control("rollback"), "pages.live.status.rollback.success", "rollback");
  };

  const onToggleSkip = async () => {
    runAction(() => api.control("toggle_skip"), "pages.live.status.skip.success", "skip");
  };

  const onToggleAuto = async () => {
    runAction(() => api.control("toggle_auto"), "pages.live.status.auto.success", "auto");
  };
  const onQuickSave = async () => {
    runAction(() => api.control("quick_save"), "pages.live.status.save.success", "save");
  };

  const onQuickLoad = async () => {
    runAction(() => api.control("quick_load"), "pages.live.status.load.success", "load");
  };

  const onQuit = async () => {
    await runAction(() => api.control("quit"), "pages.live.status.quit.success", "quit");
    setStoppedByUser(true);
    setState(null);
    setScreenshot(null);
    setChoices([]);
    eventCursor.current = 0;
    setEvents([]);
    setStatus({ key: "pages.live.status.stopped" });
  };

  const onReloadGame = async () => {
    runAction(() => api.control("reload_script"), "pages.live.status.reload.success", "reload");
  };

  const onRestartInteraction = async () => {
    runAction(() => api.control("restart_interaction"), "pages.live.status.restartInteraction.success", "restart-ui");
  };

  const onEval = async (submitEvent: FormEvent<HTMLFormElement>) => {
    submitEvent.preventDefault();
    if (!expr.trim()) {
      return;
    }
    try {
      const response = await api.evaluate(expr);
      setEvalResult(`${expr}  =  ${formatUnknown(response.value)}`);
      setStatus({ key: "pages.live.status.eval.success" });
    } catch (error) {
      setEvalResult("");
      setStatus({
        raw: error instanceof Error ? error.message : t("pages.live.status.eval.failed"),
      });
    }
  };

  const onSetVar = async (submitEvent: FormEvent<HTMLFormElement>) => {
    submitEvent.preventDefault();
    if (!setVarName.trim()) {
      return;
    }
    await runAction(
      () => api.setVariable(setVarName, parseVariableValue(setVarValue)),
      "pages.live.status.setVar.success",
      "set-var",
    );
  };

  const onSelectChoice = async (index: number, text: string) => {
    await runAction(() => api.selectChoice(index, text), "pages.live.status.choice.selected", "choice");
    await refresh();
  };

  const displayedState = stoppedByUser ? null : state ?? liveState;
  const displayedFrame = stoppedByUser ? null : screenshot ?? liveFrame;
  const isRunning = Boolean(displayedState);
  const controlsDisabled = Boolean(busyAction) || !isRunning;
  const statusLabel = stoppedByUser
    ? t("pages.live.status.stopped")
    : busyAction === "launch"
      ? t("pages.live.status.launching")
      : busyAction === "stop"
        ? t("pages.live.status.stopping")
        : loading
          ? t("pages.live.status.syncing")
          : formatStatus(status, t) || (isRunning ? t("pages.live.status.live") : t("pages.live.status.stopped"));
  const statusClass = isRunning ? "ok" : busyAction ? "warn" : "off";
  const tags = displayedState?.showing_tags ?? [];
  const variables = displayedState?.variables ?? {};
  const narrativeChoices = displayedState?.menu
    ? choices.filter((choice) => !choice.screen || choice.screen === "choice")
    : [];

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>{t("pages.live.title")}</h2>
        <span className="hint">{t("pages.live.header.hint", { port: BRIDGE_PORT })}</span>
      </div>

      <div className="live-grid">
        <section className="card preview-card reveal in" style={{ animationDelay: ".02s" }}>
          <div className="card-head">
            <h3>{t("pages.live.preview.title")}</h3>
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
                  alt={t("pages.live.preview.alt")}
                />
              ) : (
                <div className="empty-box">{t("pages.live.preview.empty")}</div>
              )}
            </div>
            <div className="transport">
              <button className="tctl primary" type="button" onClick={onLaunchGame} disabled={Boolean(busyAction) || isRunning}>
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
                {t("pages.live.actions.launch")}
              </button>
              <button className="tctl warn" type="button" onClick={onStopGame} disabled={Boolean(busyAction)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
                {t("pages.live.actions.stop")}
              </button>
              <button className="tctl" type="button" onClick={onRollback} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M15 6 9 12l6 6" />
                </svg>
                {t("pages.live.actions.back")}
              </button>
              <button className="tctl primary" type="button" onClick={onAdvance} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
                {t("pages.live.actions.advance")}
              </button>
              <button className="tctl" type="button" onClick={onToggleSkip} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M5 5v14l9-7zM15 5h3v14h-3z" />
                </svg>
                {t("pages.live.actions.skip")}
              </button>
              <button className="tctl" type="button" onClick={onToggleAuto} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 12a8 8 0 1 1 3 6.2" />
                  <path d="M4 20v-4h4" />
                </svg>
                {t("pages.live.actions.auto")}
              </button>
              <button className="tctl" type="button" onClick={onQuickSave} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M5 3h11l3 3v15H5z" />
                  <path d="M8 3v5h7M8 14h8v7H8z" />
                </svg>
                {t("pages.live.actions.save")}
              </button>
              <button className="tctl" type="button" onClick={onQuickLoad} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M3 7h6l2 2h10v10H3z" />
                </svg>
                {t("pages.live.actions.load")}
              </button>
              <button className="tctl" type="button" onClick={onReloadGame} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                  <path d="M21 12a9 9 0 1 1-2.6-6.3" />
                  <path d="M21 4v4h-4" />
                </svg>
                {t("pages.live.actions.reload")}
              </button>
              <button className="tctl warn" type="button" onClick={onQuit} disabled={controlsDisabled}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
                {t("pages.live.actions.quit")}
              </button>
            </div>
            {narrativeChoices.length > 0 && (
              <div className="live-choices">
                <div className="live-choices-head">
                <h4>{t("pages.live.choices.title")}</h4>
                <span className="badge warn">{t("pages.live.choices.badge")}</span>
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
                        {t("pages.live.actions.choose")}
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
            <h3>{t("pages.live.state.title")}</h3>
            <span className="badge info">{t("pages.live.state.badge")}</span>
          </div>
          <div className="card-body">
            <div className="state-row">
              <span className="k">{t("pages.live.state.label")}</span>
              <span className="v">{displayedState?.current_label || t("pages.live.state.labelFallback")}</span>
            </div>
            <div className="state-row">
              <span className="k">{t("pages.live.state.menu.label")}</span>
              <span className="v">
                {displayedState?.menu ? t("pages.live.state.menu.active") : t("pages.live.state.menu.inactive")}
              </span>
            </div>
            <div className="state-row">
              <span className="k">{t("pages.live.state.tags.label")}</span>
              <span className="v">{tags.length ? tags.join(", ") : DASH}</span>
            </div>
            <div className="state-row">
              <span className="k">{t("pages.live.state.port.label")}</span>
              <span className="v">{BRIDGE_PORT}</span>
            </div>
            <div className="vars">
              <div className="vhead">{t("pages.live.state.variables.title")}</div>
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
                <div className="var">
                  <span className="name" style={{ color: "var(--meta)" }}>
                    {t("pages.live.state.variables.empty")}
                  </span>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="card reveal in" style={{ animationDelay: ".16s" }}>
          <div className="card-head">
            <h3>{t("pages.live.console.title")}</h3>
            <span className="badge off">{t("pages.live.console.badge")}</span>
          </div>
          <div className="card-body">
            <form onSubmit={onEval}>
              <label className="field-label" htmlFor="expr">
                {t("pages.live.console.eval.expressionLabel")}
              </label>
              <div className="console-row">
                <input
                  className="input"
                  id="expr"
                  value={expr}
                  onChange={(e) => setExpr(e.target.value)}
                  placeholder={t("pages.live.placeholder.expr")}
                />
                <button type="submit" className="btn btn-primary" disabled={controlsDisabled}>
                  {t("pages.live.console.eval.submit")}
                </button>
              </div>
            </form>
            <div className="console-out">
              {evalResult ? (
                <>
                  <span className="pf">{PROMPT_ARROW}</span>
                  <span className="rs">{evalResult}</span>
                </>
              ) : (
                t("pages.live.console.eval.empty")
              )}
            </div>

            <form onSubmit={onSetVar} className="vars" style={{ marginTop: "16px" }}>
              <div className="vhead">{t("pages.live.console.watch.title")}</div>
              <div className="two-col">
                <div>
                  <label className="field-label" htmlFor="wname">
                    {t("pages.live.console.watch.nameLabel")}
                  </label>
                  <input
                    className="input"
                    id="wname"
                    value={setVarName}
                    onChange={(e) => setSetVarName(e.target.value)}
                    placeholder={t("pages.live.placeholder.varName")}
                  />
                </div>
                <div>
                  <label className="field-label" htmlFor="wval">
                    {t("pages.live.console.watch.valueLabel")}
                  </label>
                  <input
                    className="input"
                    id="wval"
                    value={setVarValue}
                    onChange={(e) => setSetVarValue(e.target.value)}
                    placeholder={t("pages.live.placeholder.varValue")}
                  />
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "11px" }}>
                <button type="submit" className="btn btn-ghost" disabled={controlsDisabled}>
                  {t("pages.live.console.watch.set")}
                </button>
              </div>
            </form>

            <details className="live-advanced">
              <summary>
                <span>{t("pages.live.console.advanced.title")}</span>
                <span>{t("pages.live.console.advanced.events", { count: events.length })}</span>
              </summary>
              <div className="live-advanced-actions">
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={onRestartInteraction}
                  disabled={controlsDisabled}
                  aria-label={t("pages.live.console.advanced.restartAria")}
                >
                  {t("pages.live.console.advanced.restart")}
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => void refresh()} disabled={Boolean(busyAction)}>
                  {t("pages.live.console.advanced.refresh")}
                </button>
              </div>
              <div className="live-events">
                {events.length > 0 ? [...events].reverse().map((event, index) => (
                  <div className={`live-event ${event.type === "exception" ? "error" : ""}`} key={`${event.seq ?? index}-${event.type ?? "event"}`}>
                    <span>{event.seq ?? "-"}</span>
                    <p>{describeEvent(event, t)}</p>
                  </div>
                )) : (
                  <p className="muted">{t("pages.live.console.events.empty")}</p>
                )}
              </div>
            </details>
          </div>
        </section>

      </div>
    </div>
  );
}
