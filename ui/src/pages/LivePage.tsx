import { ChangeEvent, FormEvent, useCallback, useEffect, useState } from "react";
import {
  api
} from "../api";
import type { LiveChoice, LiveScreenshot, LiveState } from "../types";

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

export function LivePage() {
  const [state, setState] = useState<LiveState | null>(null);
  const [screenshot, setScreenshot] = useState<LiveScreenshot | null>(null);
  const [choices, setChoices] = useState<LiveChoice[]>([]);
  const [expr, setExpr] = useState("");
  const [evalResult, setEvalResult] = useState<string>("");
  const [setVarName, setSetVarName] = useState("");
  const [setVarValue, setSetVarValue] = useState("");
  const [status, setStatus] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [liveState, liveChoices, frame] = await Promise.all([
        api.fetchLiveState(),
        api.fetchLiveChoices(),
        api.fetchLiveScreenshot().catch(() => null),
      ]);
      setState(liveState);
      setChoices(liveChoices.choices);
      if (frame) {
        setScreenshot(frame);
      }
      setStatus("connected");
    } catch (_error) {
      setStatus("disconnected");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const runAction = async (action: () => Promise<unknown>) => {
    try {
      await action();
      setStatus("action sent");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "action failed");
    }
  };

  const onAdvance = async () => runAction(() => api.advance());
  const onScreenshot = async () =>
    runAction(async () => {
      const frame = await api.fetchLiveScreenshot();
      setScreenshot(frame);
    });

  const onEval = async (submitEvent: FormEvent<HTMLFormElement>) => {
    submitEvent.preventDefault();
    if (!expr.trim()) {
      return;
    }
    try {
      const response = await api.evaluate(expr);
      setEvalResult(formatUnknown(response.value));
      setStatus("eval ok");
    } catch (error) {
      setEvalResult("");
      setStatus(error instanceof Error ? error.message : "eval failed");
    }
  };

  const onSetVar = async (submitEvent: FormEvent<HTMLFormElement>) => {
    submitEvent.preventDefault();
    if (!setVarName.trim()) {
      return;
    }
    await runAction(() => api.setVariable(setVarName, setVarValue));
  };

  const onSelectChoice = async (index: number) => {
    await runAction(() => api.selectChoice(index));
    await refresh();
  };

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Live</h2>
        <span>{loading ? "Synchronisation..." : status || "En attente"}</span>
      </div>

      <div className="liveGrid">
        <div className="card">
          <h3>Preview</h3>
          <div className="screenshotWrap">
            {screenshot ? (
              <img
                src={`data:image/${screenshot.format};base64,${screenshot.base64}`}
                alt="Live screenshot"
              />
            ) : (
              <div className="emptyBox">Aucune image</div>
            )}
          </div>
        </div>

        <div className="card">
          <h3>État courant</h3>
          {state ? (
            <dl className="kv">
              <div>
                <dt>Label</dt>
                <dd>{state.current_label}</dd>
              </div>
              <div>
                <dt>Menu</dt>
                <dd>{state.menu ? "actif" : "inactif"}</dd>
              </div>
              <div>
                <dt>Tags</dt>
                <dd>{state.showing_tags.length ? state.showing_tags.join(", ") : "—"}</dd>
              </div>
              <div>
                <dt>Variables</dt>
                <dd>
                  <ul>
                    {Object.entries(state.variables)
                      .slice(0, 12)
                      .map(([key, value]) => (
                        <li key={key}>
                          <strong>{key}</strong>
                          <code>{formatUnknown(value)}</code>
                        </li>
                      ))}
                  </ul>
                </dd>
              </div>
            </dl>
          ) : (
            <p className="muted">Données live indisponibles.</p>
          )}
        </div>
      </div>

      <div className="actionsRow">
        <button className="btn" type="button" onClick={onAdvance}>
          Advance
        </button>
        <button className="btn" type="button" onClick={onScreenshot}>
          Capture screenshot
        </button>
      </div>

      <div className="panelGrid">
        <div className="card">
          <h3>Console</h3>
          <form className="stack" onSubmit={onEval}>
            <label>
              Expression
              <input
                value={expr}
                onChange={(event) => setExpr(event.target.value)}
                placeholder="store.persistent.score + 1"
              />
            </label>
            <button className="btn" type="submit">
              Eval
            </button>
          </form>
          <p className="muted">{evalResult ? `Résultat: ${evalResult}` : "—"}</p>
        </div>

        <div className="card">
          <h3>Variable watch</h3>
          <form className="stack" onSubmit={onSetVar}>
            <label>
              Variable
              <input
                value={setVarName}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setSetVarName(event.target.value)}
                placeholder="money"
              />
            </label>
            <label>
              Valeur
              <input
                value={setVarValue}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setSetVarValue(event.target.value)}
                placeholder='"bonjour" or 42'
              />
            </label>
            <button className="btn" type="submit">
              Set
            </button>
          </form>
        </div>

        <div className="card">
          <h3>Choix</h3>
          {choices.length ? (
            <ul className="choiceList">
              {choices.map((choice) => (
                <li key={`${choice.text}-${choice.index}`}>
                  <span>{choice.text}</span>
                  <button className="btn small" onClick={() => onSelectChoice(choice.index)}>
                    Choisir
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Aucun choix actif.</p>
          )}
        </div>
      </div>
    </section>
  );
}
