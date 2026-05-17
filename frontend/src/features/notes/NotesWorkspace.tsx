import type { FormEvent } from "react";

import type { Note } from "../../types/note";
import { NoteComposer } from "./NoteComposer";
import { NoteDetail } from "./NoteDetail";

interface NotesWorkspaceProps {
  content: string;
  error: string;
  isMutatingNote: boolean;
  isSaving: boolean;
  noteMode: "active" | "deleted";
  onContentChange: (value: string) => void;
  onDeleteNote: (note: Note) => void;
  onHardDeleteNote: (note: Note) => void;
  onRestoreNote: (note: Note) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onTitleChange: (value: string) => void;
  onUpdateNote: (note: Note, input: { title: string; content: string }) => void;
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
  isMutatingNote,
  isSaving,
  noteMode,
  onContentChange,
  onDeleteNote,
  onHardDeleteNote,
  onRestoreNote,
  onSubmit,
  onTitleChange,
  onUpdateNote,
  selectedNote,
  title,
}: NotesWorkspaceProps) {
  return (
    <>
      {noteMode === "active" ? (
        <NoteComposer
          content={content}
          error={error}
          isSaving={isSaving}
          onContentChange={onContentChange}
          onSubmit={onSubmit}
          onTitleChange={onTitleChange}
          title={title}
        />
      ) : (
        <section className="composer recycle-hint">
          <strong>最近删除</strong>
          <p>这里的笔记不会进入默认列表，也不会被 RAG 检索。你可以恢复，或永久删除。</p>
          {error ? <p className="error">{error}</p> : null}
        </section>
      )}

      <NoteDetail
        isMutating={isMutatingNote}
        note={selectedNote}
        onDelete={onDeleteNote}
        onHardDelete={onHardDeleteNote}
        onRestore={onRestoreNote}
        onUpdate={onUpdateNote}
      />

      <section className="elf-panel">
        <div>
          <strong>精灵</strong>
          <p>现在可以切换到“对话”，让 Memory Chat Graph 基于笔记回答问题。</p>
        </div>
      </section>
    </>
  );
}
