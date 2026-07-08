import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: the SPA talks to the FastAPI service on :8600 under /api.
// In mock mode (VITE_API_MOCK=1) the client never hits the network — fixtures
// are served from src/api/fixtures — so this proxy is inert.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8600",
        changeOrigin: true,
      },
    },
  },
});
