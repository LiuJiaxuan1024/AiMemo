import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: Number(process.env.AIMEMO_FRONTEND_PORT ?? 5173),
  },
});
