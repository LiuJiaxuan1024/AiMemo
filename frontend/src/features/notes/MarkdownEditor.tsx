import {
  BlockNoteSchema,
  createCodeBlockSpec,
  defaultBlockSpecs,
  defaultInlineContentSpecs,
  defaultStyleSpecs,
  type PartialBlock,
} from "@blocknote/core";
import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/mantine/style.css";
import { useCreateBlockNote } from "@blocknote/react";
import type { HighlighterGeneric, LanguageRegistration, ThemeRegistrationRaw } from "@shikijs/types";
import { useEffect, useRef } from "react";
import { createHighlighterCore } from "@shikijs/core";
import { createJavaScriptRegexEngine } from "@shikijs/engine-javascript";

export interface MarkdownEditorChange {
  blocksJson: string;
  markdown: string;
}

export interface MarkdownEditorProps {
  blocksJson?: string;
  className?: string;
  markdown: string;
  onChange: (value: MarkdownEditorChange) => void;
  placeholder?: string;
}

const codeBlockLanguages: Record<string, { name: string; aliases?: string[] }> = {
  text: { name: "Plain Text", aliases: ["txt", "plain", "plaintext"] },
  bash: { name: "Bash", aliases: ["sh", "shell", "zsh"] },
  c: { name: "C" },
  cpp: { name: "C++", aliases: ["c++", "cc", "cxx"] },
  csharp: { name: "C#", aliases: ["cs"] },
  css: { name: "CSS" },
  go: { name: "Go", aliases: ["golang"] },
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
  const codeBlockLanguageRegistrations: LanguageRegistration[][] = [
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
    langs: codeBlockLanguageRegistrations,
    themes: [githubDark as ThemeRegistrationRaw],
  });
  return highlighter as unknown as HighlighterGeneric<any, any>;
}

const blockNoteSchema = BlockNoteSchema.create({
  blockSpecs: {
    ...defaultBlockSpecs,
    codeBlock: createCodeBlockSpec({
      defaultLanguage: "text",
      supportedLanguages: codeBlockLanguages,
      createHighlighter: createCodeHighlighter,
    }),
  },
  inlineContentSpecs: defaultInlineContentSpecs,
  styleSpecs: defaultStyleSpecs,
});

function parseBlocksJson(blocksJson: string | undefined): PartialBlock[] | undefined {
  if (!blocksJson?.trim()) {
    return undefined;
  }

  try {
    const parsed = JSON.parse(blocksJson) as unknown;
    return Array.isArray(parsed) && parsed.length > 0 ? (parsed as PartialBlock[]) : undefined;
  } catch {
    return undefined;
  }
}

function serializeEditor(editor: ReturnType<typeof useCreateBlockNote>): MarkdownEditorChange {
  const blocks = editor.document;
  return {
    blocksJson: JSON.stringify(blocks),
    markdown: editor.blocksToMarkdownLossy(blocks),
  };
}

export function MarkdownEditor({
  blocksJson,
  className = "",
  markdown,
  onChange,
  placeholder,
}: MarkdownEditorProps) {
  const editor = useCreateBlockNote({
    initialContent: [{ type: "paragraph" }],
    placeholders: {
      default: placeholder,
      emptyDocument: placeholder,
    },
    schema: blockNoteSchema,
  });
  const lastSourceRef = useRef<string | null>(null);

  useEffect(() => {
    const nextSource = blocksJson ?? markdown;
    if (nextSource === lastSourceRef.current) {
      return;
    }
    lastSourceRef.current = nextSource;

    const parsedBlocks = parseBlocksJson(blocksJson);
    const nextBlocks = parsedBlocks ?? (markdown.trim() ? editor.tryParseMarkdownToBlocks(markdown) : []);
    editor.replaceBlocks(editor.document, nextBlocks.length > 0 ? nextBlocks : [{ type: "paragraph" }]);
  }, [blocksJson, editor, markdown]);

  const classes = ["note-block-editor", className].filter(Boolean).join(" ");

  return (
    <BlockNoteView
      className={classes}
      editor={editor}
      formattingToolbar
      linkToolbar
      onChange={(nextEditor) => {
        const nextValue = serializeEditor(nextEditor);
        lastSourceRef.current = nextValue.blocksJson;
        onChange(nextValue);
      }}
      portalElements={{ default: null }}
      sideMenu
      slashMenu
      tableHandles
      theme="light"
    />
  );
}
