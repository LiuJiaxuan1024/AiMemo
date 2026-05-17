import { Badge, EmptyState } from "../../shared/ui";
import type { Note } from "../../types/note";
import { formatNoteDate } from "./noteUtils";

interface NoteDetailProps {
  note: Note | null;
}

/**
 * 选中笔记的详情展示。
 * 后续如果加入编辑、chunk、embedding 调试信息，可以继续在这个组件内扩展展示区。
 */
export function NoteDetail({ note }: NoteDetailProps) {
  if (!note) {
    return (
      <article className="note-detail">
        <EmptyState className="empty-state">选择或创建一条笔记</EmptyState>
      </article>
    );
  }

  return (
    <article className="note-detail">
      <header>
        <div>
          <h2>{note.title}</h2>
          {note.tags.length > 0 ? (
            <div className="tag-row">
              {note.tags.map((tag) => (
                <span className="tag" key={tag}>
                  {tag}
                </span>
              ))}
            </div>
          ) : null}
          {note.processing_status === "pending" || note.processing_status === "processing" ? (
            <Badge className="detail-badge" tone="warning">
              AI 整理中
            </Badge>
          ) : null}
          {note.processing_status === "failed" ? (
            <Badge className="detail-badge" tone="danger">
              AI 整理失败{note.processing_error ? `：${note.processing_error}` : ""}
            </Badge>
          ) : null}
          {note.embedding_status === "pending" || note.embedding_status === "processing" ? (
            <Badge className="detail-badge" tone="success">
              建立记忆中
            </Badge>
          ) : null}
          {note.embedding_status === "failed" ? (
            <Badge className="detail-badge" tone="danger">
              建立记忆失败{note.embedding_error ? `：${note.embedding_error}` : ""}
            </Badge>
          ) : null}
        </div>
        <time>{formatNoteDate(note.updated_at)}</time>
      </header>
      {note.summary ? <section className="summary-block">{note.summary}</section> : null}
      <p>{note.content}</p>
    </article>
  );
}
