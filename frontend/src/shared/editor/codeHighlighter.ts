import { createHighlighterCore } from "@shikijs/core";
import { createJavaScriptRegexEngine } from "@shikijs/engine-javascript";
import type { HighlighterGeneric, LanguageRegistration, ThemeRegistrationRaw } from "@shikijs/types";

export const supportedCodeBlockLanguages: Record<string, { name: string; aliases?: string[] }> = {
  text: { name: "Plain Text", aliases: ["txt", "plain", "plaintext"] },
  bash: { name: "Bash", aliases: ["sh", "shell", "zsh"] },
  c: { name: "C" },
  cpp: { name: "C++", aliases: ["c++", "cc", "cxx"] },
  csharp: { name: "C#", aliases: ["cs"] },
  css: { name: "CSS" },
  go: { name: "Go" },
  html: { name: "HTML" },
  java: { name: "Java" },
  javascript: { name: "JavaScript", aliases: ["js", "jsx"] },
  json: { name: "JSON" },
  markdown: { name: "Markdown", aliases: ["md"] },
  mermaid: { name: "Mermaid" },
  python: { name: "Python", aliases: ["py"] },
  rust: { name: "Rust", aliases: ["rs"] },
  sql: { name: "SQL" },
  typescript: { name: "TypeScript", aliases: ["ts", "tsx"] },
  xml: { name: "XML" },
  yaml: { name: "YAML", aliases: ["yml"] },
};

let codeHighlighterPromise: Promise<HighlighterGeneric<any, any>> | null = null;

export async function getCodeHighlighter(): Promise<HighlighterGeneric<any, any>> {
  if (!codeHighlighterPromise) {
    codeHighlighterPromise = createCodeHighlighter();
  }
  return codeHighlighterPromise;
}

async function createCodeHighlighter(): Promise<HighlighterGeneric<any, any>> {
  const [
    { default: bash },
    { default: c },
    { default: cpp },
    { default: csharp },
    { default: css },
    { default: go },
    { default: html },
    { default: java },
    { default: javascript },
    { default: json },
    { default: markdown },
    { default: mermaid },
    { default: python },
    { default: rust },
    { default: sql },
    { default: typescript },
    { default: xml },
    { default: yaml },
    { default: githubDark },
  ] = await Promise.all([
    import("@shikijs/langs/bash"),
    import("@shikijs/langs/c"),
    import("@shikijs/langs/cpp"),
    import("@shikijs/langs/csharp"),
    import("@shikijs/langs/css"),
    import("@shikijs/langs/go"),
    import("@shikijs/langs/html"),
    import("@shikijs/langs/java"),
    import("@shikijs/langs/javascript"),
    import("@shikijs/langs/json"),
    import("@shikijs/langs/markdown"),
    import("@shikijs/langs/mermaid"),
    import("@shikijs/langs/python"),
    import("@shikijs/langs/rust"),
    import("@shikijs/langs/sql"),
    import("@shikijs/langs/typescript"),
    import("@shikijs/langs/xml"),
    import("@shikijs/langs/yaml"),
    import("@shikijs/themes/github-dark"),
  ]);
  const languageRegistrations: LanguageRegistration[][] = [
    bash,
    c,
    cpp,
    csharp,
    css,
    go,
    html,
    java,
    javascript,
    json,
    markdown,
    mermaid,
    python,
    rust,
    sql,
    typescript,
    xml,
    yaml,
  ];

  const highlighter = await createHighlighterCore({
    engine: createJavaScriptRegexEngine(),
    langs: languageRegistrations,
    themes: [githubDark as ThemeRegistrationRaw],
  });
  return highlighter as unknown as HighlighterGeneric<any, any>;
}
