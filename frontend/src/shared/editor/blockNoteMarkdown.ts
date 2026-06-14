import {
  BlockNoteSchema,
  createCodeBlockSpec,
  defaultBlockSpecs,
  defaultInlineContentSpecs,
  defaultStyleSpecs,
  type PartialBlock,
} from "@blocknote/core";
import type { useCreateBlockNote } from "@blocknote/react";

import { getCodeHighlighter, supportedCodeBlockLanguages } from "./codeHighlighter";

export interface MarkdownEditorChange {
  blocksJson: string;
  markdown: string;
}

export const aimemoBlockNoteSchema = BlockNoteSchema.create({
  blockSpecs: {
    ...defaultBlockSpecs,
    codeBlock: createCodeBlockSpec({
      createHighlighter: getCodeHighlighter,
      defaultLanguage: "text",
      supportedLanguages: supportedCodeBlockLanguages,
    }),
  },
  inlineContentSpecs: defaultInlineContentSpecs,
  styleSpecs: defaultStyleSpecs,
});

export function parseBlocksJson(blocksJson: string | undefined): PartialBlock[] | undefined {
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

export function serializeBlockNoteEditor(editor: ReturnType<typeof useCreateBlockNote>): MarkdownEditorChange {
  const blocks = editor.document;
  return {
    blocksJson: JSON.stringify(blocks),
    markdown: editor.blocksToMarkdownLossy(blocks),
  };
}
