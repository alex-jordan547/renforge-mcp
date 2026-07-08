import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DebugBridgeEvent, LiveChoice, LiveState } from "../types";

const POLL_MS = 1200;

function formatUnknown(value: unknown): string {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

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

export function DebuggerPage() {
  const [state, setState] = useState<LiveState | null>(null);
  const [choices, setChoices] = useState<LiveChoice[]>([]);
  const [events, setEvents] = useState<DebugBridgeEvent[]>([]);
  const [expr, setExpr] = useState("renpy.get_filename_line()");
  const [evalResult, setEvalResult] = useState("");
  const [varName, setVarName] = useState("");
  const [varValue, setVarValue] = useState("");
  const [status, setStatus] = useState("synchronisation...");
  const [refreshing, setRefreshing] = useState(true);
  const cursorRef = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const [nextState, nextChoices, nextEvents] = await Promise.all([
        api.fetchLiveState(),
        api.fetchLiveChoices(),
        api.fetchDebugEvents(cursorRef.current),
      ]);

      setState(nextState);
      setChoices(nextChoices.choices);
      if (typeof nextEvents.cursor === "number") {
        cursorRef.current = nextEvents.cursor;
      }
      if (nextEvents.events.length > 0) {
        setEvents((prev) => [...prev, ...nextEvents.events].slice(-80));
      }
      setStatus("connecté au bridge");
    } catch (error) {
      setState(null);
      setChoices([]);
      setStatus(error instanceof Error ? error.message : "bridge indisponible");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const runAction = async (action: () => Promise<unknown>, success: string) => {
    setRefreshing(true);
    try {
      const result = await action();
      if (result && typeof result === "object" && "ok" in result && (result as { ok?: unknown }).ok === false) {
        throw new Error((result as { error?: string }).error ?? "action failed");
      }
      setStatus(success);
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "action failed");
      setRefreshing(false);
    }
  };

  const handleEval = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const expression = expr.trim();
    if (!expression) {
      return;
    }
    await runAction(async () => {
      const response = await api.evaluate(expression);
      setEvalResult(formatUnknown(response.value));
      return response;
    }, "évaluation exécutée");
  };

  const handleSetVariable = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = varName.trim();
    if (!name) {
      return;
    }
    await runAction(
      () => api.setVariable(name, parseVariableValue(varValue)),
      `variable ${name} mise à jour`,
    );
  };

  const variables = state?.variables ?? {};
  const visibleVariables = Object.entries(variables)
    .filter(([key]) => !key.startsWith("_") && !key.startsWith("IMG_"))
    .slice(0, 18);
  const narrativeChoices = state?.menu
    ? choices.filter((choice) => !choice.screen || choice.screen === "choice")
    : [];

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Debugger</h2>
        <span className="hint">contrôle runtime via bridge</span>
      </div>

      <div className="debugger-grid">
        <section className="debug-panel span-2 reveal in">
          <div className="debug-panel-head">
            <div>
              <h3>Session</h3>
              <p>{state ? "Bridge actif et état runtime synchronisé." : "Aucun état bridge disponible."}</p>
            </div>
            <span className={`status ${state ? "ro" : "off"}`}>
              <span className="dot" />
              {refreshing ? "sync" : state ? "actif" : "offline"}
            </span>
          </div>
          <div className="debug-metrics">
            <div>
              <span>Label</span>
              <b>{state?.current_label || "—"}</b>
            </div>
            <div>
              <span>Menu</span>
              <b>{state?.menu ? "actif" : "inactif"}</b>
            </div>
            <div>
              <span>Tags</span>
              <b>{state?.showing_tags.length ? state.showing_tags.join(", ") : "—"}</b>
            </div>
            <div>
              <span>Events</span>
              <b>{events.length}</b>
            </div>
          </div>
          <div className="debug-actions">
            <button className="btn btn-primary" type="button" onClick={() => runAction(() => api.control("advance"), "avancé")}>
              Avancer
            </button>
            <button className="btn btn-ghost" type="button" onClick={() => runAction(() => api.control("rollback"), "rollback exécuté")}>
              Rollback
            </button>
            <button className="btn btn-ghost" type="button" onClick={() => runAction(() => api.control("restart_interaction"), "interaction relancée")}>
              Restart UI
            </button>
            <button className="btn btn-ghost" type="button" onClick={refresh}>
              Rafraîchir
            </button>
          </div>
          <p className="debug-status">{status}</p>
        </section>

        <section className="debug-panel reveal in" style={{ animationDelay: ".05s" }}>
          <h3>Console Python</h3>
          <form onSubmit={handleEval}>
            <label className="field-label" htmlFor="debug-expr">Expression</label>
            <div className="console-row">
              <input
                className="input"
                id="debug-expr"
                value={expr}
                onChange={(event) => setExpr(event.target.value)}
                placeholder="store.player_name"
              />
              <button className="btn btn-primary" type="submit">Eval</button>
            </div>
          </form>
          <pre className="debug-output">{evalResult || "Le résultat s'affiche ici."}</pre>
        </section>

        <section className="debug-panel reveal in" style={{ animationDelay: ".08s" }}>
          <h3>Variable</h3>
          <form onSubmit={handleSetVariable}>
            <label className="field-label" htmlFor="debug-var-name">Nom</label>
            <input
              className="input"
              id="debug-var-name"
              value={varName}
              onChange={(event) => setVarName(event.target.value)}
              placeholder="score"
            />
            <label className="field-label" htmlFor="debug-var-value">Valeur JSON ou texte</label>
            <input
              className="input"
              id="debug-var-value"
              value={varValue}
              onChange={(event) => setVarValue(event.target.value)}
              placeholder='42, true, "Alex"'
            />
            <div className="debug-form-actions">
              <button className="btn btn-primary" type="submit">Définir</button>
            </div>
          </form>
        </section>

        <section className="debug-panel reveal in" style={{ animationDelay: ".11s" }}>
          <h3>Variables du store</h3>
          <div className="debug-list">
            {visibleVariables.length > 0 ? visibleVariables.map(([key, value]) => (
              <div className="debug-row" key={key}>
                <span>{key}</span>
                <b>{formatUnknown(value)}</b>
              </div>
            )) : (
              <p className="muted">Aucune variable exposée par le bridge.</p>
            )}
          </div>
        </section>

        <section className="debug-panel reveal in" style={{ animationDelay: ".14s" }}>
          <h3>Choix actifs</h3>
          <div className="debug-list">
            {narrativeChoices.length > 0 ? narrativeChoices.map((choice) => (
              <div className="debug-choice" key={`${choice.index}-${choice.text}`}>
                <span>{choice.text}</span>
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={() => runAction(() => api.selectChoice(choice.index), "choix sélectionné")}
                >
                  Choisir
                </button>
              </div>
            )) : (
              <p className="muted">Aucun choix narratif actif.</p>
            )}
          </div>
        </section>

        <section className="debug-panel span-2 reveal in" style={{ animationDelay: ".17s" }}>
          <h3>Events runtime</h3>
          <div className="debug-events">
            {events.length > 0 ? [...events].reverse().map((event, index) => (
              <div className={`debug-event ${event.type === "exception" ? "error" : ""}`} key={`${event.seq ?? index}-${event.type ?? "event"}`}>
                <span>{event.seq ?? "—"}</span>
                <b>{String(event.type ?? "event")}</b>
                <p>{describeEvent(event)}</p>
              </div>
            )) : (
              <p className="muted">Aucun événement reçu. Lance ou avance le jeu pour alimenter le flux.</p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
