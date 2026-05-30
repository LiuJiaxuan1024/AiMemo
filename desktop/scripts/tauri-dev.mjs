import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

const port = process.env.AIMEMO_DESKTOP_PORT || "1420";
const config = JSON.stringify({
  build: {
    devUrl: `http://127.0.0.1:${port}`,
  },
});
const configPath = join("src-tauri", ".tauri-dev.override.json");
writeFileSync(configPath, config, "utf8");

const child = spawn("npx", ["tauri", "dev", "--config", configPath], {
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
