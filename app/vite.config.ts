import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };

// The page runs on 5173; /api goes to `python -m app.server` on 8000 (API_PORT=... if 8000 is taken).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: `http://127.0.0.1:${process.env.API_PORT ?? "8000"}`, changeOrigin: true } } },
});
