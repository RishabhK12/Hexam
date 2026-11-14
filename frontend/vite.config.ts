/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend, so the app works without CORS setup.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.HEXAM_API ?? "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { chunkSizeWarningLimit: 600, rollupOptions: { output: { manualChunks: { react: ["react", "react-dom"], charts: ["recharts"] } } } },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});
