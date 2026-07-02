import { Folder, Inbox, Pin, Plus, Search, Star, Tags } from "lucide-react";

import { Badge } from "../../shared/ui";
import type { Note, NoteCategory, NoteListItem, NoteTag } from "../../types/note";
import { formatNoteDate } from "./noteUtils";

export type NoteFilter =
  | { type: "all" }
  | { type: "uncategorized" }
  | { type: "favorite" }
  | { type: "pinned" }
  | { type: "category"; id: number }
  | { type: "tag"; name: string };

interface NoteSidebarProps {
  isLoading: boolean;
  isTransitioning: boolean;
  listViewKey: string;
  mode: "active" | "deleted";
  activeFilter: NoteFilter;
  categories: NoteCategory[];
  tags: NoteTag[];
  notes: NoteListItem[];
  onCreateCategory: () => void;
  onFilterChange: (filter: NoteFilter) => void;
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
  isTransitioning,
  listViewKey,
  mode,
  activeFilter,
  categories,
  tags,
  notes,
  onCreateCategory,
  onFilterChange,
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
  const filterActive = (filter: NoteFilter) => {
    if (filter.type !== activeFilter.type) {
      return false;
    }
    if (filter.type === "category" && activeFilter.type === "category") {
      return filter.id === activeFilter.id;
    }
    if (filter.type === "tag" && activeFilter.type === "tag") {
      return filter.name === activeFilter.name;
    }
    return true;
  };

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

      {mode === "active" ? (
        <nav className="note-nav" aria-label="笔记组织筛选">
          <button
            className={filterActive({ type: "all" }) ? "active" : ""}
            onClick={() => onFilterChange({ type: "all" })}
            type="button"
          >
            <Inbox aria-hidden="true" size={15} />
            全部笔记
          </button>
          <button
            className={filterActive({ type: "uncategorized" }) ? "active" : ""}
            onClick={() => onFilterChange({ type: "uncategorized" })}
            type="button"
          >
            <Folder aria-hidden="true" size={15} />
            未分类
          </button>
          <button
            className={filterActive({ type: "favorite" }) ? "active" : ""}
            onClick={() => onFilterChange({ type: "favorite" })}
            type="button"
          >
            <Star aria-hidden="true" size={15} />
            收藏
          </button>
          <button
            className={filterActive({ type: "pinned" }) ? "active" : ""}
            onClick={() => onFilterChange({ type: "pinned" })}
            type="button"
          >
            <Pin aria-hidden="true" size={15} />
            置顶
          </button>

          <div className="note-nav-section">
            <div className="note-nav-section-header">
              <span>分类</span>
              <button aria-label="新建分类" onClick={onCreateCategory} type="button">
                <Plus aria-hidden="true" size={14} />
              </button>
            </div>
            {categories.length === 0 ? <small className="note-nav-empty">暂无分类</small> : null}
            {categories.map((category) => (
              <button
                className={filterActive({ type: "category", id: category.id }) ? "active" : ""}
                key={category.id}
                onClick={() => onFilterChange({ type: "category", id: category.id })}
                type="button"
              >
                <Folder aria-hidden="true" size={15} />
                <span>{category.name}</span>
                <small>{category.note_count}</small>
              </button>
            ))}
          </div>

          <div className="note-nav-section">
            <div className="note-nav-section-header">
              <span>标签</span>
              <Tags aria-hidden="true" size={14} />
            </div>
            {tags.length === 0 ? <small className="note-nav-empty">暂无标签</small> : null}
            {tags.slice(0, 10).map((tag) => (
              <button
                className={filterActive({ type: "tag", name: tag.name }) ? "active" : ""}
                key={tag.name}
                onClick={() => onFilterChange({ type: "tag", name: tag.name })}
                type="button"
              >
                <Tags aria-hidden="true" size={15} />
                <span>#{tag.name}</span>
                <small>{tag.note_count}</small>
              </button>
            ))}
          </div>
        </nav>
      ) : null}

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

      <div
        aria-busy={isTransitioning}
        aria-label="笔记列表"
        className={isTransitioning ? "note-list note-list--transitioning" : "note-list"}
        key={listViewKey}
      >
        {isLoading ? <p className="muted">正在加载...</p> : null}
        {!isLoading && notes.length === 0 ? (
          <p className="muted">
            {searchQuery ? "没有匹配的笔记" : mode === "active" ? "暂无笔记" : "最近删除为空"}
          </p>
        ) : null}
        {notes.map((note, index) => {
          const hasBadges =
            note.processing_status === "pending" ||
            note.processing_status === "processing" ||
            note.processing_status === "failed" ||
            note.embedding_status === "pending" ||
            note.embedding_status === "processing" ||
            note.status === "deleted";

          return (
            <article
              className={note.id === selectedNote?.id ? "note-item active" : "note-item"}
              key={note.id}
              style={{ animationDelay: `${Math.min(index, 8) * 18}ms` }}
            >
              <button
                aria-label={`打开笔记：${note.title}`}
                className="note-item-hitbox"
                onClick={() => onSelectNote(note.id)}
                type="button"
              />
              <span className="note-item-content">
                <span className="note-item-body">
                  <span className="note-item-title">{note.title}</span>
                  {hasBadges ? (
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
                  ) : null}
                  {note.summary ? <em className="note-item-summary">{note.summary}</em> : null}
                  {note.tags.length > 0 ? (
                    <span className="note-item-tags">
                      {note.tags.slice(0, 3).map((tag) => (
                        <small key={tag}>#{tag}</small>
                      ))}
                    </span>
                  ) : null}
                </span>
                <span className="note-item-date">{formatNoteDate(note.updated_at)}</span>
              </span>
            </article>
          );
        })}
      </div>
    </aside>
  );
}
