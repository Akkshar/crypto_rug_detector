import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI routes live at the root; in dev the Vite server proxies them to :8000.
const api = ["/scan", "/history", "/label", "/weights", "/stats", "/health", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
  server: {
    port: 5173,
    proxy: Object.fromEntries(api.map((p) => [p, { target: "http://127.0.0.1:8000", changeOrigin: true }])),
  },
});
