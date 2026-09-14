import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { captureMagicLink } from "./lib/auth";

// Grab ?login=<jwt> BEFORE the first render so the auth-gate sees it.
captureMagicLink();

// Register the service worker so iOS Safari treats the dashboard as
// installable to Home Screen. Failure is silent — the dashboard works
// fine without SW, you just don't get the "Add to Home Screen" prompt
// flag on iOS.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/dashboard/sw.js", { scope: "/dashboard/" })
      .catch(() => {
        //
      });
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
