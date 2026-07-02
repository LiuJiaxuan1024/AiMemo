import { FormEvent, useEffect, useState } from "react";
import { ArchiveX, CalendarDays, Check, ChevronDown, FileText, Folder, Pencil, Pin, RotateCcw, Save, Star, Tags, Trash2, X } from "lucide-react";

import { Badge, Button, EmptyState, MarkdownView } from "../../shared/ui";
import type { Note, NoteCategory, UpdateNoteInput } from "../../types/note";
import { LazyMarkdownEditor } from "./LazyMarkdownEditor";
import { formatNoteDate } from "./noteUtils";

interface NoteDetailProps {
  categories: NoteCategory[];
  isMutating: boolean;
  note: Note | null;
  onDelete: (note: Note) => void;
  onHardDelete: (note: Note) => void;
  onRestore: (note: Note) => void;
  onUpdate: (note: Note, input: UpdateNoteInput) => void;
  onUpdateOrganization: (note: Note, input: UpdateNoteInput) => void;
}

/**
 * 选中笔记的详情展示。
 * 后续如果加入编辑、chunk、embedding 调试信息，可以继续在这个组件内扩展展示区。
 */
export function NoteDetail({
  categories,
  isMutating,
  note,
  onDelete,
  onHardDelete,
  onRestore,
  onUpdate,
  onUpdateOrganization,
}: NoteDetailProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [contentDraft, setContentDraft] = useState("");
  const [contentBlocksDraft, setContentBlocksDraft] = useState("");
  const [tagsDraft, setTagsDraft] = useState("");
  const [isCategoryMenuOpen, setIsCategoryMenuOpen] = useState(false);

  useEffect(() => {
    setIsEditing(false);
    setIsCategoryMenuOpen(false);
    setTitleDraft(note?.title ?? "");
    setContentDraft(note?.content_markdown ?? note?.content ?? "");
    setContentBlocksDraft(note?.content_blocks ?? "");
    setTagsDraft(note?.tags.join(", ") ?? "");
  }, [note?.id, note?.title, note?.content, note?.content_markdown, note?.content_blocks, note?.tags]);

  if (!note) {
    return (
      <article className="note-detail">
        <EmptyState className="empty-state">选择或创建一条笔记</EmptyState>
      </article>
    );
  }
  const characterCount = note.content.trim().length;
  const paragraphCount = note.content.split(/\n{2,}/).filter((item) => item.trim()).length;
  const isProcessing = note.processing_status === "pending" || note.processing_status === "processing";
  const isEmbedding = note.embedding_status === "pending" || note.embedding_status === "processing";

  function submitEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!note) {
      return;
    }
    onUpdate(note, {
      title: titleDraft,
      content: contentDraft,
      content_markdown: contentDraft,
      content_blocks: contentBlocksDraft,
      content_format: "blocknote",
    });
    setIsEditing(false);
  }

  function saveTags() {
    if (!note) {
      return;
    }
    const tags = tagsDraft
      .split(/[,，]/)
      .map((tag) => tag.trim())
      .filter(Boolean);
    onUpdateOrganization(note, { tags });
  }

  function selectCategory(categoryId: number | null) {
    if (!note) {
      return;
    }
    setIsCategoryMenuOpen(false);
    if ((note.category_id ?? null) === categoryId) {
      return;
    }
    onUpdateOrganization(note, { category_id: categoryId });
  }

  return (
    <article className="note-detail">
      <header className="note-detail-header">
        <div>
          <h2>{note.title}</h2>
          <div className="note-meta-row">
            <span>
              <CalendarDays aria-hidden="true" size={14} />
              更新于 {formatNoteDate(note.updated_at)}
            </span>
            <span>
              <FileText aria-hidden="true" size={14} />
              {characterCount} 字 / {paragraphCount || 1} 段
            </span>
          </div>
        </div>
        <time>{formatNoteDate(note.created_at)}</time>
      </header>
      <div className="note-detail-actions">
        {note.status === "active" ? (
          <>
            <Button
              disabled={isMutating}
              onClick={() => onUpdateOrganization(note, { is_favorite: !note.is_favorite })}
              size="sm"
            >
              <Star aria-hidden="true" size={15} />
              {note.is_favorite ? "取消收藏" : "收藏"}
            </Button>
            <Button
              disabled={isMutating}
              onClick={() => onUpdateOrganization(note, { pinned: !note.pinned_at })}
              size="sm"
            >
              <Pin aria-hidden="true" size={15} />
              {note.pinned_at ? "取消置顶" : "置顶"}
            </Button>
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
      {note.status === "active" ? (
        <section className="note-organization-panel" aria-label="笔记组织">
          <label className="note-organization-control note-category-control">
            <span className="note-organization-label">
              <Folder aria-hidden="true" size={15} />
              分类
            </span>
            <div className="note-category-menu">
              <button
                aria-expanded={isCategoryMenuOpen}
                aria-haspopup="menu"
                className="note-category-trigger"
                disabled={isMutating}
                onClick={() => setIsCategoryMenuOpen((current) => !current)}
                type="button"
              >
                <span>{note.category_name || "未分类"}</span>
                <ChevronDown aria-hidden="true" size={15} />
              </button>
              {isCategoryMenuOpen ? (
                <div className="note-category-menu-popover" role="menu">
                  <button
                    className={note.category_id === null ? "active" : ""}
                    onClick={() => selectCategory(null)}
                    role="menuitem"
                    type="button"
                  >
                    <span>未分类</span>
                    {note.category_id === null ? <Check aria-hidden="true" size={14} /> : null}
                  </button>
                  {categories.map((category) => (
                    <button
                      className={note.category_id === category.id ? "active" : ""}
                      key={category.id}
                      onClick={() => selectCategory(category.id)}
                      role="menuitem"
                      type="button"
                    >
                      <span>{category.name}</span>
                      {note.category_id === category.id ? <Check aria-hidden="true" size={14} /> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </label>
          <label className="note-organization-control note-tags-control">
            <span className="note-organization-label">
              <Tags aria-hidden="true" size={15} />
              标签
            </span>
            <div className="note-tag-editor">
              <input
                disabled={isMutating}
                onChange={(event) => setTagsDraft(event.target.value)}
                placeholder="用逗号分隔标签"
                value={tagsDraft}
              />
              <Button disabled={isMutating} onClick={saveTags} size="sm">
                保存标签
              </Button>
            </div>
          </label>
        </section>
      ) : null}
      <section className="note-health-grid" aria-label="笔记处理状态">
        <div className={isProcessing ? "running" : ""}>
          <span>AI 整理</span>
          {note.processing_status === "failed" ? (
            <Badge tone="danger">失败</Badge>
          ) : isProcessing ? (
            <Badge tone="warning">进行中</Badge>
          ) : (
            <Badge tone="success">已完成</Badge>
          )}
          {note.processing_error ? <small>{note.processing_error}</small> : null}
        </div>
        <div className={isEmbedding ? "running" : ""}>
          <span>记忆索引</span>
          {note.embedding_status === "failed" ? (
            <Badge tone="danger">失败</Badge>
          ) : isEmbedding ? (
            <Badge tone="warning">进行中</Badge>
          ) : (
            <Badge tone="success">已完成</Badge>
          )}
          {note.embedding_error ? <small>{note.embedding_error}</small> : null}
        </div>
      </section>
      {isEditing ? (
        <form className="note-edit-form" onSubmit={submitEdit}>
          <input
            aria-label="编辑笔记标题"
            onChange={(event) => setTitleDraft(event.target.value)}
            value={titleDraft}
          />
          <LazyMarkdownEditor
            blocksJson={contentBlocksDraft}
            className="note-edit-block-editor"
            markdown={contentDraft}
            onChange={(value) => {
              setContentDraft(value.markdown);
              setContentBlocksDraft(value.blocksJson);
            }}
            placeholder="继续写这条笔记..."
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
      {note.tags.length > 0 ? (
        <div className="tag-row">
          <Tags aria-hidden="true" size={15} />
          {note.tags.map((tag) => (
            <span className="tag" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      ) : null}
      {note.summary ? (
        <section className="summary-block">
          <strong>摘要</strong>
          <p>{note.summary}</p>
        </section>
      ) : null}
      {!isEditing ? (
        <section className="note-content-reader">
          <MarkdownView className="note-markdown" content={note.content} />
        </section>
      ) : null}
    </article>
  );
}
