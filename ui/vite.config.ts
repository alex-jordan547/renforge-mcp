import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src/", import.meta.url)),
    }
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8765",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8765",
        ws: true,
      }
    }
  },
  build: {
    outDir: "../src/renforge/ui/static",
    emptyOutDir: true,
    assetsDir: "assets"
  }
});
