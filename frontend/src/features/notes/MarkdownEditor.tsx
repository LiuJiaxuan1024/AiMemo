import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/mantine/style.css";
import { useCreateBlockNote } from "@blocknote/react";
import { useEffect, useRef } from "react";

import {
  aimemoBlockNoteSchema,
  parseBlocksJson,
  serializeBlockNoteEditor,
  type MarkdownEditorChange,
} from "../../shared/editor/blockNoteMarkdown";

export interface MarkdownEditorProps {
  blocksJson?: string;
  className?: string;
  markdown: string;
  onChange: (value: MarkdownEditorChange) => void;
  placeholder?: string;
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
    schema: aimemoBlockNoteSchema,
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
        const nextValue = serializeBlockNoteEditor(nextEditor);
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
