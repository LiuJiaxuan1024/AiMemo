import { lazy, Suspense } from "react";

import type { MarkdownEditorProps } from "./MarkdownEditor";

const MarkdownEditor = lazy(() =>
  import("./MarkdownEditor").then((module) => ({
    default: module.MarkdownEditor,
  })),
);

export function LazyMarkdownEditor(props: MarkdownEditorProps) {
  const classes = ["note-block-editor", "note-block-editor--loading", props.className]
    .filter(Boolean)
    .join(" ");

  return (
    <Suspense fallback={<div className={classes}>正在加载编辑器...</div>}>
      <MarkdownEditor {...props} />
    </Suspense>
  );
}
