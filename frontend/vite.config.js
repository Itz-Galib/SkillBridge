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

function copyFaceApiModels() {
  return {
    name: "copy-faceapi-models",
    configResolved(config) {
      const source = resolve(config.root, "node_modules/@vladmandic/face-api/model");
      const destination = resolve(config.root, "public/face-api/model");
      const required = [
        "tiny_face_detector_model-weights_manifest.json",
        "tiny_face_detector_model.bin",
        "face_landmark_68_tiny_model-weights_manifest.json",
        "face_landmark_68_tiny_model.bin",
        "face_recognition_model-weights_manifest.json",
        "face_recognition_model.bin",
      ];
      if (!existsSync(source)) {
        throw new Error("face-api models are missing. Run npm install in frontend first.");
      }
      mkdirSync(destination, { recursive: true });
      for (const file of required) cpSync(resolve(source, file), resolve(destination, file));
    },
  };
}

export default defineConfig({
  plugins: [react(), copyMediaPipeWasm(), copyFaceApiModels()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
