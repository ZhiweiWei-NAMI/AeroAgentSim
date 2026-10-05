import { defineConfig } from "vite";

// The official pack builder needs only the conversion modules and bundled style.
// Keep this local server separate from the public viewer and its Control API.
export default defineConfig({
  appType: "mpa",
  server: {
    host: "127.0.0.1",
    strictPort: true,
    watch: { ignored: ["**/public/**", "**/node_modules/**"] },
  },
});
