/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the server at /agent (server/agent_assist_server/portal.py).
// `npm run dev` proxies the queue API and websocket to a local server on :8000.
const server = "http://127.0.0.1:8000";

export default defineConfig({
  base: "/agent/",
  plugins: [react()],
  server: {
    proxy: {
      "/agents": server,
      "/queue": server,
      "/ws": { target: server.replace(/^http/, "ws"), ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
