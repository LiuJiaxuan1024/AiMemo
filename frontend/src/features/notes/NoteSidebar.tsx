import { Badge } from "../../shared/ui";
import type { Note, NoteListItem } from "../../types/note";
import { formatNoteDate } from "./noteUtils";

interface NoteSidebarProps {
  isLoading: boolean;
  mode: "active" | "deleted";
  notes: NoteListItem[];
  onSelectNote: (noteId: number) => void;
  onModeChange: (mode: "active" | "deleted") => void;
  selectedNote: Note | null;
}

/**
 * 应用左侧的笔记导航区。
 * 它只负责展示笔记摘要和选择行为，不直接读取接口，避免列表 UI 和数据获取耦合。
 */
export function NoteSidebar({
  isLoading,
  mode,
  notes,
  onModeChange,
  onSelectNote,
  selectedNote,
}: NoteSidebarProps) {
  const noteCountText = mode === "active" ? `${notes.length} 条笔记` : `${notes.length} 条最近删除`;

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">Ai</span>
        <div>
          <h1>Ai 记</h1>
          <p>{noteCountText}</p>
        </div>
      </div>

      <div className="sidebar-tabs" aria-label="笔记状态筛选">
        <button
          className={mode === "active" ? "active" : ""}
          onClick={() => onModeChange("active")}
          type="button"
        >
          笔记
        </button>
        <button
          className={mode === "deleted" ? "active" : ""}
          onClick={() => onModeChange("deleted")}
          type="button"
        >
          最近删除
        </button>
      </div>

      <div className="note-list" aria-label="笔记列表">
        {isLoading ? <p className="muted">正在加载...</p> : null}
        {!isLoading && notes.length === 0 ? (
          <p className="muted">{mode === "active" ? "暂无笔记" : "最近删除为空"}</p>
        ) : null}
        {notes.map((note) => (
          <button
            className={note.id === selectedNote?.id ? "note-item active" : "note-item"}
            key={note.id}
            onClick={() => onSelectNote(note.id)}
            type="button"
          >
            <span>{note.title}</span>
            {note.processing_status === "pending" || note.processing_status === "processing" ? (
              <Badge tone="warning">AI 整理中</Badge>
            ) : null}
            {note.processing_status === "failed" ? (
              <Badge tone="danger">AI 整理失败</Badge>
            ) : null}
            {note.embedding_status === "pending" || note.embedding_status === "processing" ? (
              <Badge tone="success">建立记忆中</Badge>
            ) : null}
            {note.status === "deleted" ? <Badge tone="neutral">最近删除</Badge> : null}
            {note.summary ? <em>{note.summary}</em> : null}
            <small>{formatNoteDate(note.updated_at)}</small>
          </button>
        ))}
      </div>
    </aside>
  );
}
