import { Search } from "lucide-react";

import { Badge } from "../../shared/ui";
import type { Note, NoteListItem } from "../../types/note";
import { formatNoteDate } from "./noteUtils";

interface NoteSidebarProps {
  isLoading: boolean;
  mode: "active" | "deleted";
  notes: NoteListItem[];
  onSearchChange: (value: string) => void;
  onSelectNote: (noteId: number) => void;
  onModeChange: (mode: "active" | "deleted") => void;
  onSortChange: (mode: "updated" | "created" | "title") => void;
  searchQuery: string;
  selectedNote: Note | null;
  sortMode: "updated" | "created" | "title";
  stats: {
    processingCount: number;
    tagCount: number;
    totalCount: number;
  };
}

/**
 * 应用左侧的笔记导航区。
 * 它只负责展示笔记摘要和选择行为，不直接读取接口，避免列表 UI 和数据获取耦合。
 */
export function NoteSidebar({
  isLoading,
  mode,
  notes,
  onSearchChange,
  onModeChange,
  onSelectNote,
  onSortChange,
  searchQuery,
  selectedNote,
  sortMode,
  stats,
}: NoteSidebarProps) {
  const noteCountText = mode === "active" ? `${stats.totalCount} 条笔记` : `${stats.totalCount} 条最近删除`;

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">Ai</span>
        <div>
          <h1>Ai 记</h1>
          <p>{noteCountText}</p>
        </div>
      </div>

      <div className="note-stat-grid" aria-label="笔记概览">
        <span>
          <strong>{stats.totalCount}</strong>
          <small>全部</small>
        </span>
        <span>
          <strong>{stats.tagCount}</strong>
          <small>标签</small>
        </span>
        <span>
          <strong>{stats.processingCount}</strong>
          <small>处理中</small>
        </span>
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

      <label className="note-search">
        <Search aria-hidden="true" size={16} />
        <input
          aria-label="搜索笔记"
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="搜索标题、摘要或标签"
          value={searchQuery}
        />
      </label>

      <div className="note-sort-row">
        <span>排序</span>
        <select
          aria-label="笔记排序"
          onChange={(event) => onSortChange(event.target.value as "updated" | "created" | "title")}
          value={sortMode}
        >
          <option value="updated">最近更新</option>
          <option value="created">最近创建</option>
          <option value="title">标题</option>
        </select>
      </div>

      <div className="note-list" aria-label="笔记列表">
        {isLoading ? <p className="muted">正在加载...</p> : null}
        {!isLoading && notes.length === 0 ? (
          <p className="muted">
            {searchQuery ? "没有匹配的笔记" : mode === "active" ? "暂无笔记" : "最近删除为空"}
          </p>
        ) : null}
        {notes.map((note) => (
          <button
            className={note.id === selectedNote?.id ? "note-item active" : "note-item"}
            key={note.id}
            onClick={() => onSelectNote(note.id)}
            type="button"
          >
            <span>{note.title}</span>
            <span className="note-item-badges">
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
            </span>
            {note.summary ? <em>{note.summary}</em> : null}
            {note.tags.length > 0 ? (
              <span className="note-item-tags">
                {note.tags.slice(0, 3).map((tag) => (
                  <small key={tag}>#{tag}</small>
                ))}
              </span>
            ) : null}
            <small className="note-item-date">{formatNoteDate(note.updated_at)}</small>
          </button>
        ))}
      </div>
    </aside>
  );
}
