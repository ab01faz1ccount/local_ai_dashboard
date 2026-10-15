import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./theme.css";
import { applyMode, applyPalette, loadMode, loadPalette } from "./theme";

applyPalette(loadPalette()); // before the first paint: no flash of the wrong palette
applyMode(loadMode());

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
