import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const watchMode = process.env.AIMEMO_FRONTEND_WATCH_MODE ?? "auto";
const usePollingWatcher =
  process.env.CHOKIDAR_USEPOLLING === "true" ||
  watchMode === "polling" ||
  (watchMode === "auto" && process.platform === "linux");

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: Number(process.env.AIMEMO_FRONTEND_PORT ?? 5173),
    watch: usePollingWatcher
      ? {
          interval: Number(process.env.AIMEMO_FRONTEND_POLL_INTERVAL ?? 300),
          usePolling: true,
        }
      : undefined,
  },
});
