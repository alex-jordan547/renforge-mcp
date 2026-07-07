const features = [
  {
    id: "statement_callback",
    label: "Statement Callbacks",
    state: "Non actif",
    stateClass: "off",
    detail: "Le bridge ne publie pas encore les events de ligne en runtime.",
  },
  {
    id: "breakpoints",
    label: "Breakpoints",
    state: "Non actif",
    stateClass: "off",
    detail: "Non opérationnels tant que les callbacks de bridge ne sont pas activés.",
  },
  {
    id: "step",
    label: "Step / Continue",
    state: "Non actif",
    stateClass: "off",
    detail: "Non proposé en production tant que l'exécution contrôlée n'est pas étendue.",
  },
  {
    id: "stack",
    label: "Stack Frames",
    state: "En attente",
    stateClass: "wait",
    detail: "Visible uniquement quand le backend enverra les états de stack.",
  },
  {
    id: "bridge_bridge",
    label: "Panneau d'état",
    state: "Lecture seule",
    stateClass: "read",
    detail: "Affichage non interactif, basé uniquement sur les données reçues.",
  },
] as const;

export function DebuggerPage() {
  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Debugger</h2>
        <span>Vue actuelle alignée P2: aucun contrôle faux-positif</span>
      </div>
      <div className="panelGrid">
        {features.map((feature) => (
          <article
            key={feature.id}
            className={`card ${feature.stateClass === "off" ? "inactive" : ""}`}
          >
            <h3>{feature.label}</h3>
            <p className="debugStatus">
              <span className={`statusDot ${feature.stateClass}`} />
              <strong>{feature.state}</strong>
            </p>
            <p className="muted">{feature.detail}</p>
          </article>
        ))}
      </div>
      <div className="card">
        <h3>État</h3>
        <p className="muted">
          Les callbacks de déclaration de statement, les breakpoints et le pas-à-pas ne sont pas encore branchés
          côté bridge. Cette page reste informative tant que P2 n'autorise pas le contrôle runtime.
        </p>
      </div>
    </section>
  );
}
