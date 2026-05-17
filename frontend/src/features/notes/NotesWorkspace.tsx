import type { FormEvent } from "react";

import type { Note } from "../../types/note";
import { NoteComposer } from "./NoteComposer";
import { NoteDetail } from "./NoteDetail";

interface NotesWorkspaceProps {
  content: string;
  error: string;
  isSaving: boolean;
  onContentChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onTitleChange: (value: string) => void;
  selectedNote: Note | null;
  title: string;
}

/**
 * 笔记工作区的组合组件。
 * 它把“新建笔记、查看详情、精灵提示”放在同一块区域，App 只需要传入状态和回调。
 */
export function NotesWorkspace({
  content,
  error,
  isSaving,
  onContentChange,
  onSubmit,
  onTitleChange,
  selectedNote,
  title,
}: NotesWorkspaceProps) {
  return (
    <>
      <NoteComposer
        content={content}
        error={error}
        isSaving={isSaving}
        onContentChange={onContentChange}
        onSubmit={onSubmit}
        onTitleChange={onTitleChange}
        title={title}
      />

      <NoteDetail note={selectedNote} />

      <section className="elf-panel">
        <div>
          <strong>精灵</strong>
          <p>现在可以切换到“对话”，让 Memory Chat Graph 基于笔记回答问题。</p>
        </div>
      </section>
    </>
  );
}
