import { spawn } from "node:child_process";

const port = process.env.AIMEMO_DESKTOP_PORT || "1420";
const config = JSON.stringify({
  build: {
    devUrl: `http://127.0.0.1:${port}`,
  },
});

const child = spawn("npx", ["tauri", "dev", "--config", config], {
  env: process.env,
  shell: process.platform === "win32",
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
