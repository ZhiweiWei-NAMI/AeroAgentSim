import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
// @ts-expect-error local ESM server plugin
import { workspaceTracesPlugin } from "./scripts/workspace-traces.mjs";
// @ts-expect-error local ESM server plugin
import { assetLibraryPlugin } from "./scripts/asset-library.mjs";

const proxy = {
  "/v1": { target: process.env.AERO_CONTROL_API_TARGET ?? "http://127.0.0.1:8123", changeOrigin: true },
  "/authoring/v1": { target: process.env.AERO_AUTHORING_API_TARGET ?? "http://127.0.0.1:8124", changeOrigin: true },
};
const previewKeyPath = process.env.AERO_PREVIEW_TLS_KEY;
const previewCertPath = process.env.AERO_PREVIEW_TLS_CERT;
if (Boolean(previewKeyPath) !== Boolean(previewCertPath)) {
  throw new Error("AERO_PREVIEW_TLS_KEY and AERO_PREVIEW_TLS_CERT must be set together");
}
const previewTls = previewKeyPath && previewCertPath
  ? { key: readFileSync(previewKeyPath), cert: readFileSync(previewCertPath) }
  : undefined;
const frontendRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: "./",
  appType: "mpa",
  server: { host: "0.0.0.0", port: 5173, proxy },
  preview: { host: "0.0.0.0", port: 5173, proxy, https: previewTls },
  plugins: [workspaceTracesPlugin(frontendRoot), assetLibraryPlugin(frontendRoot)],
  build: {
    rollupOptions: {
      input: {
        index: resolve(frontendRoot, "index.html"),
        assetLibrary: resolve(frontendRoot, "asset-library.html"),
        cityStudio: resolve(frontendRoot, "city-studio.html"),
      },
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/three/")) return "three";
          if (id.includes("node_modules/ajv") || id.includes("generated/contract-validators")) return "contracts";
        },
      },
    },
  },
});
