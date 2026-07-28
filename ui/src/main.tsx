// Initialise i18next + react-i18next (détection navigateur / localStorage)
// Doit être importé en premier, avant React et App, pour que i18next soit
// prêt dès le premier render.
import "./i18n";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
