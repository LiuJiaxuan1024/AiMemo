import type { FormEvent } from "react";
import { PenLine } from "lucide-react";

import { Button } from "../../shared/ui";
import type { Note, UpdateNoteInput } from "../../types/note";
import { NoteComposer } from "./NoteComposer";
import { NoteDetail } from "./NoteDetail";

interface NotesWorkspaceProps {
  contentBlocks: string;
  content: string;
  error: string;
  isMutatingNote: boolean;
  isSaving: boolean;
  noteMode: "active" | "deleted";
  onContentChange: (value: { blocksJson: string; markdown: string }) => void;
  onDeleteNote: (note: Note) => void;
  onHardDeleteNote: (note: Note) => void;
  onRestoreNote: (note: Note) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onTitleChange: (value: string) => void;
  onUpdateNote: (note: Note, input: UpdateNoteInput) => void;
  onWriteNote: () => void;
  selectedNote: Note | null;
  title: string;
  workspaceMode: "compose" | "read";
}

/**
 * 笔记工作区的组合组件。
 * 它把“新建笔记、查看详情、精灵提示”放在同一块区域，App 只需要传入状态和回调。
 */
export function NotesWorkspace({
  contentBlocks,
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
  onWriteNote,
  selectedNote,
  title,
  workspaceMode,
}: NotesWorkspaceProps) {
  if (workspaceMode === "compose" && noteMode === "active") {
    return (
      <section className="memo-workspace-panel memo-workspace-panel--compose">
        <NoteComposer
          blocksJson={contentBlocks}
          content={content}
          error={error}
          isSaving={isSaving}
          onContentChange={onContentChange}
          onSubmit={onSubmit}
          onTitleChange={onTitleChange}
          title={title}
        />
      </section>
    );
  }

  return (
    <section className="memo-workspace-panel memo-workspace-panel--reader">
      <div className="memo-reader-header">
        <div>
          <strong>{noteMode === "active" ? "查阅笔记" : "最近删除"}</strong>
          <p>{noteMode === "active" ? "从左侧选择历史笔记查看、编辑或删除。" : "恢复误删笔记，或永久删除不再需要的内容。"}</p>
        </div>
        {noteMode === "active" ? (
          <Button onClick={onWriteNote} size="sm" variant="primary">
            <PenLine aria-hidden="true" size={15} />
            写新笔记
          </Button>
        ) : null}
      </div>

      {noteMode === "deleted" && !selectedNote ? (
        <section className="composer recycle-hint">
          <strong>最近删除</strong>
          <p>这里的笔记不会进入默认列表，也不会被 RAG 检索。你可以恢复，或永久删除。</p>
          {error ? <p className="error">{error}</p> : null}
        </section>
      ) : null}

      <NoteDetail
        isMutating={isMutatingNote}
        note={selectedNote}
        onDelete={onDeleteNote}
        onHardDelete={onHardDeleteNote}
        onRestore={onRestoreNote}
        onUpdate={onUpdateNote}
      />

      <section className="elf-panel memo-reader-footer">
        <div>
          <strong>精灵</strong>
          <p>现在可以切换到“对话”，让 Memory Chat Graph 基于笔记回答问题。</p>
        </div>
      </section>
    </section>
  );
}
