import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { captureMagicLink } from "./lib/auth";

// Grab ?login=<jwt> BEFORE the first render so the auth-gate sees it.
captureMagicLink();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
