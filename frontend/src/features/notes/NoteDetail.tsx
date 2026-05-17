import { FormEvent, useEffect, useState } from "react";
import { ArchiveX, Pencil, RotateCcw, Save, Trash2, X } from "lucide-react";

import { Badge, Button, EmptyState } from "../../shared/ui";
import type { Note } from "../../types/note";
import { formatNoteDate } from "./noteUtils";

interface NoteDetailProps {
  isMutating: boolean;
  note: Note | null;
  onDelete: (note: Note) => void;
  onHardDelete: (note: Note) => void;
  onRestore: (note: Note) => void;
  onUpdate: (note: Note, input: { title: string; content: string }) => void;
}

/**
 * 选中笔记的详情展示。
 * 后续如果加入编辑、chunk、embedding 调试信息，可以继续在这个组件内扩展展示区。
 */
export function NoteDetail({
  isMutating,
  note,
  onDelete,
  onHardDelete,
  onRestore,
  onUpdate,
}: NoteDetailProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [contentDraft, setContentDraft] = useState("");

  useEffect(() => {
    setIsEditing(false);
    setTitleDraft(note?.title ?? "");
    setContentDraft(note?.content ?? "");
  }, [note?.id, note?.title, note?.content]);

  if (!note) {
    return (
      <article className="note-detail">
        <EmptyState className="empty-state">选择或创建一条笔记</EmptyState>
      </article>
    );
  }

  function submitEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!note) {
      return;
    }
    onUpdate(note, {
      title: titleDraft,
      content: contentDraft,
    });
    setIsEditing(false);
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
      <div className="note-detail-actions">
        {note.status === "active" ? (
          <>
            <Button disabled={isMutating} onClick={() => setIsEditing(true)} size="sm">
              <Pencil aria-hidden="true" size={15} />
              编辑
            </Button>
            <Button disabled={isMutating} onClick={() => onDelete(note)} size="sm">
              <ArchiveX aria-hidden="true" size={15} />
              删除
            </Button>
          </>
        ) : (
          <>
            <Button disabled={isMutating} onClick={() => onRestore(note)} size="sm">
              <RotateCcw aria-hidden="true" size={15} />
              恢复
            </Button>
            <Button disabled={isMutating} onClick={() => onHardDelete(note)} size="sm">
              <Trash2 aria-hidden="true" size={15} />
              永久删除
            </Button>
          </>
        )}
      </div>
      {isEditing ? (
        <form className="note-edit-form" onSubmit={submitEdit}>
          <input
            aria-label="编辑笔记标题"
            onChange={(event) => setTitleDraft(event.target.value)}
            value={titleDraft}
          />
          <textarea
            aria-label="编辑笔记内容"
            onChange={(event) => setContentDraft(event.target.value)}
            value={contentDraft}
          />
          <div className="note-detail-actions">
            <Button disabled={isMutating || !contentDraft.trim()} size="sm" type="submit">
              <Save aria-hidden="true" size={15} />
              保存
            </Button>
            <Button disabled={isMutating} onClick={() => setIsEditing(false)} size="sm">
              <X aria-hidden="true" size={15} />
              取消
            </Button>
          </div>
        </form>
      ) : null}
      {note.summary ? <section className="summary-block">{note.summary}</section> : null}
      {!isEditing ? <p>{note.content}</p> : null}
    </article>
  );
}
