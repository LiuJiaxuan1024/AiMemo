#!/usr/bin/env node

const fs = require("node:fs");

const [configPath, dottedPath, defaultValue = ""] = process.argv.slice(2);

function stripJson5Syntax(text) {
  const output = [];
  let inString = false;
  let quote = "";
  let escaped = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const nextChar = text[index + 1] ?? "";

    if (inString) {
      output.push(char);
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === quote) {
        inString = false;
      }
      continue;
    }

    if (char === '"' || char === "'") {
      inString = true;
      quote = char;
      output.push(char);
      continue;
    }

    if (char === "/" && nextChar === "/") {
      const newlineIndex = text.indexOf("\n", index);
      if (newlineIndex === -1) {
        break;
      }
      output.push("\n");
      index = newlineIndex;
      continue;
    }

    if (char === "/" && nextChar === "*") {
      const commentEndIndex = text.indexOf("*/", index + 2);
      index = commentEndIndex === -1 ? text.length : commentEndIndex + 1;
      continue;
    }

    output.push(char);
  }

  return output.join("").replace(/,\s*([}\]])/g, "$1");
}

try {
  let value = JSON.parse(stripJson5Syntax(fs.readFileSync(configPath, "utf8")));
  for (const part of dottedPath.split(".")) {
    if (!value || typeof value !== "object" || !(part in value)) {
      console.log(defaultValue);
      process.exit(0);
    }
    value = value[part];
  }
  console.log(String(value));
} catch {
  console.log(defaultValue);
}
