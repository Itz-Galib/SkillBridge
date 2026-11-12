import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

function copyMediaPipeWasm() {
  return {
    name: "copy-mediapipe-wasm",
    configResolved(config) {
      const source = resolve(config.root, "node_modules/@mediapipe/tasks-vision/wasm");
      const destination = resolve(config.root, "public/mediapipe/wasm");
      if (!existsSync(source)) {
        throw new Error("MediaPipe WASM files are missing. Run npm install in frontend first.");
      }
      mkdirSync(destination, { recursive: true });
      cpSync(source, destination, { recursive: true });
    },
  };
}

export default defineConfig({
  plugins: [react(), copyMediaPipeWasm()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
