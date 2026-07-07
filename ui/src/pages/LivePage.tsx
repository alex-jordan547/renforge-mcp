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

interface LivePageProps {
  liveState?: LiveState | null;
  liveFrame?: LiveScreenshot | null;
}

export function LivePage({ liveState = null, liveFrame = null }: LivePageProps = {}) {
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
      const [liveState, liveChoices] = await Promise.all([
        api.fetchLiveState(),
        api.fetchLiveChoices(),
      ]);
      const frame = await api.fetchLiveScreenshot().catch(() => null);
      setState(liveState);
      setChoices(liveChoices.choices);
      if (frame) {
        setScreenshot(frame);
      } else {
        setScreenshot(null);
      }
      setStatus("connected");
    } catch (_error) {
      setStatus("indisponible");
      setState(null);
      setChoices([]);
      setScreenshot(null);
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
      const result = await action();
      if (result && typeof result === "object" && "ok" in result && (result as { ok?: unknown }).ok === false) {
        const error = (result as { error?: string }).error;
        throw new Error(error || "action failed");
      }
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

  const onRollback = async () => {
    try {
      await api.evaluate("renpy.roll_back()");
      setStatus("rollback ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "rollback failed");
    }
  };

  const onToggleSkip = async () => {
    try {
      await api.evaluate("renpy.game.interface.keymap['toggle_skip']()");
      setStatus("toggle skip ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "skip failed");
    }
  };

  const onToggleAuto = async () => {
    try {
      await api.evaluate("renpy.game.interface.keymap['toggle_auto']()");
      setStatus("toggle auto ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "auto failed");
    }
  };

  const onQuickSave = async () => {
    try {
      await api.evaluate("renpy.save('quick-1', 'Sauvegarde auto dashboard')");
      setStatus("save ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "save failed");
    }
  };

  const onQuickLoad = async () => {
    try {
      await api.evaluate("renpy.load('quick-1')");
      setStatus("load ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "load failed");
    }
  };

  const onMainMenu = async () => {
    try {
      await api.evaluate("renpy.utter_restart()");
      setStatus("main menu ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "menu failed");
    }
  };

  const onToggleUI = async () => {
    try {
      await api.evaluate("renpy.toggle_interface()");
      setStatus("toggle ui ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "toggle failed");
    }
  };

  const onQuit = async () => {
    try {
      await api.evaluate("renpy.quit()");
      setStatus("quit ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "quit failed");
    }
  };

  const onPreferences = async () => {
    try {
      await api.evaluate("renpy.call_in_new_context('_game_menu', _game_menu_screen='preferences')");
      setStatus("preferences ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "preferences failed");
    }
  };

  const onReloadGame = async () => {
    try {
      await api.evaluate("renpy.reload_script()");
      setStatus("reload ok");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "reload failed");
    }
  };

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

  // Live WS frames (pushed by the poller) take priority; HTTP polling remains
  // the fallback that also drives choices and actions.
  const displayedState = liveState ?? state;
  const displayedFrame = liveFrame ?? screenshot;
  const tags = displayedState?.showing_tags ?? [];
  const variables = displayedState?.variables ?? {};

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Live</h2>
        <span>{loading ? "Synchronisation..." : status || "En attente"}</span>
      </div>

      <div className="liveGrid">
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <h3 style={{ padding: "20px 20px 0 20px" }}>Preview</h3>
          <div className="screenshotWrap" style={{ margin: "12px 20px 0 20px", borderBottom: "none", borderRadius: "var(--radius) var(--radius) 0 0" }}>
            {displayedFrame ? (
              <img
                src={`data:image/${displayedFrame.format};base64,${displayedFrame.base64}`}
                alt="Live screenshot"
              />
            ) : (
              <div className="emptyBox">Aucune image</div>
            )}
          </div>
          <div className="remote-control-deck">
            <button className="remote-btn" type="button" onClick={onRollback} title="Retourner en arrière (Rollback)">
              ◀ Retour
            </button>
            <button className="remote-btn" type="button" onClick={onAdvance} title="Avancer dans l'histoire">
              ▶ Avancer
            </button>
            <button className="remote-btn" type="button" onClick={onToggleSkip} title="Passer les dialogues rapidement (Skip)">
              ⏩ Skip
            </button>
            <button className="remote-btn" type="button" onClick={onToggleAuto} title="Lecture automatique (Auto)">
              🔄 Auto
            </button>
            <button className="remote-btn" type="button" onClick={onQuickSave} title="Sauvegarde rapide">
              💾 Sauver
            </button>
            <button className="remote-btn" type="button" onClick={onQuickLoad} title="Charger la sauvegarde rapide">
              📂 Charger
            </button>
            <button className="remote-btn" type="button" onClick={onToggleUI} title="Afficher/Masquer l'interface de dialogue (UI)">
              👁️ Afficher UI
            </button>
            <button className="remote-btn" type="button" onClick={onPreferences} title="Ouvrir le menu des préférences">
              ⚙️ Préférences
            </button>
            <button className="remote-btn" type="button" onClick={onMainMenu} title="Retourner au menu principal">
              🏠 Menu
            </button>
            <button className="remote-btn" type="button" onClick={onReloadGame} title="Recharger les scripts du jeu">
              🔃 Recharger
            </button>
            <button className="remote-btn" type="button" onClick={onQuit} title="Quitter le jeu" style={{ borderColor: 'var(--danger)', color: 'var(--danger)' }}>
              ❌ Quitter
            </button>
          </div>
        </div>

        <div className="card">
          <h3>État courant</h3>
          {displayedState ? (
            <dl className="kv">
              <div>
                <dt>Label</dt>
                <dd>{displayedState.current_label || "—"}</dd>
              </div>
              <div>
                <dt>Menu</dt>
                <dd>{displayedState.menu ? "actif" : "inactif"}</dd>
              </div>
              <div>
                <dt>Tags</dt>
                <dd>{tags.length ? tags.join(", ") : "—"}</dd>
              </div>
              <div>
                <dt>Variables</dt>
                <dd>
                  <ul>
                    {Object.entries(variables)
                      .filter(([key]) => !key.startsWith("_") && !key.startsWith("IMG_"))
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
            <button className="btn primary inline" type="submit">
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
                onChange={(event) => setSetVarName(event.target.value)}
                placeholder="money"
              />
            </label>
            <label>
              Valeur
              <input
                value={setVarValue}
                onChange={(event) => setSetVarValue(event.target.value)}
                placeholder='"bonjour" or 42'
              />
            </label>
            <button className="btn primary inline" type="submit">
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
                  <button className="btn small primary" onClick={() => onSelectChoice(choice.index)}>
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
