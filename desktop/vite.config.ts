import { defineConfig } from "vite";

export default defineConfig({
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: Number(process.env.AIMEMO_DESKTOP_PORT ?? 1420),
    strictPort: true,
  },
});
