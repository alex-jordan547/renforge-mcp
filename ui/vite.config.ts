import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": new URL("./src/", import.meta.url).pathname
    }
  },
  server: {
    port: 5173,
    strictPort: true
  },
  build: {
    outDir: "../src/renforge/ui/static",
    emptyOutDir: true,
    assetsDir: "assets"
  }
});
