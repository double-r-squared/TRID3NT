import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// job-0166 — global sans-serif font baseline for body + form controls so
// the Cases / CaseView / ConfirmationDialog surfaces (and any future
// browser-default text) don't fall back to UA serif. See styles/global.css
// header comment for the reasoning.
import "./styles/global.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
