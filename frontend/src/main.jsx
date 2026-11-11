import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

// Small compatibility shims for older Safari/Edge/Chromium builds.
if (typeof String.prototype.replaceAll !== "function") {
  Object.defineProperty(String.prototype, "replaceAll", {
    configurable: true,
    writable: true,
    value(search, replacement) {
      if (search instanceof RegExp) {
        if (!search.global)
          throw new TypeError("replaceAll must be called with a global RegExp");
        return this.replace(search, replacement);
      }
      return this.split(String(search)).join(String(replacement));
    },
  });
}
if (typeof Promise.allSettled !== "function") {
  Promise.allSettled = (promises) =>
    Promise.all(
      Array.from(promises, (promise) =>
        Promise.resolve(promise).then(
          (value) => ({ status: "fulfilled", value }),
          (reason) => ({ status: "rejected", reason }),
        ),
      ),
    );
}

createRoot(document.getElementById("root")).render(<App />);
