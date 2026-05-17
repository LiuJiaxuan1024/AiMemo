import type { FormEvent } from "react";
import { ArchiveX, Eye, Pencil, RotateCcw, Save, Trash2, X } from "lucide-react";

import { Badge, Button } from "../../shared/ui";
import type { Memory, MemoryStatus, MemoryUpdateInput } from "./types";
import {
  CATEGORY_LABELS,
  CATEGORY_OPTIONS,
  STATUS_LABELS,
  formatMemoryScore,
  formatMemoryTime,
} from "./memoryUtils";

interface MemoryCardProps {
  draft: MemoryUpdateInput;
  isEditing: boolean;
  isSaving: boolean;
  memory: Memory;
  onActivate: (memoryId: number) => void;
  onArchive: (memoryId: number) => void;
  onCancelEdit: () => void;
  onDelete: (memory: Memory) => void;
  onDraftChange: (nextDraft: MemoryUpdateInput) => void;
  onOpenDetail: (memory: Memory) => void;
  onSave: (event: FormEvent<HTMLFormElement>, memoryId: number) => void;
  onStartEdit: (memory: Memory) => void;
}

/**
 * 单条长期记忆的展示与编辑表单。
 * 它不直接调用 API，只把用户动作抛给父组件，方便后续接入乐观更新或 TanStack Query。
 */
export function MemoryCard({
  draft,
  isEditing,
  isSaving,
  memory,
  onActivate,
  onArchive,
  onCancelEdit,
  onDelete,
  onDraftChange,
  onOpenDetail,
  onSave,
  onStartEdit,
}: MemoryCardProps) {
  return (
    <article className="memory-card">
      <header>
        <div>
          <strong>{CATEGORY_LABELS.get(memory.category) ?? memory.category}</strong>
          <Badge tone={memory.status === "active" ? "success" : "neutral"}>
            {STATUS_LABELS[memory.status]}
          </Badge>
        </div>
        <small>{formatMemoryTime(memory.updated_at)}</small>
      </header>

      {isEditing ? (
        <form className="memory-edit-form" onSubmit={(event) => onSave(event, memory.id)}>
          <label>
            类型
            <select
              onChange={(event) => onDraftChange({ ...draft, category: event.target.value })}
              value={draft.category ?? memory.category}
            >
              {CATEGORY_OPTIONS.filter((option) => option.value).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            内容
            <textarea
              onChange={(event) => onDraftChange({ ...draft, content: event.target.value })}
              value={String(draft.content ?? "")}
            />
          </label>
          <label>
            摘要
            <input
              onChange={(event) => onDraftChange({ ...draft, summary: event.target.value })}
              value={String(draft.summary ?? "")}
            />
          </label>
          <div className="memory-score-grid">
            <label>
              重要性
              <input
                max="1"
                min="0"
                onChange={(event) => onDraftChange({ ...draft, importance: Number(event.target.value) })}
                step="0.05"
                type="number"
                value={Number(draft.importance ?? 0)}
              />
            </label>
            <label>
              可信度
              <input
                max="1"
                min="0"
                onChange={(event) => onDraftChange({ ...draft, confidence: Number(event.target.value) })}
                step="0.05"
                type="number"
                value={Number(draft.confidence ?? 0)}
              />
            </label>
          </div>
          <label>
            状态
            <select
              onChange={(event) =>
                onDraftChange({ ...draft, status: event.target.value as MemoryStatus })
              }
              value={draft.status ?? memory.status}
            >
              <option value="active">生效</option>
              <option value="archived">停用</option>
            </select>
          </label>
          <div className="memory-card-actions">
            <Button disabled={isSaving} size="sm" type="submit">
              <Save aria-hidden="true" size={15} />
              保存
            </Button>
            <Button disabled={isSaving} onClick={onCancelEdit} size="sm">
              <X aria-hidden="true" size={15} />
              取消
            </Button>
          </div>
        </form>
      ) : (
        <>
          <p>{memory.content}</p>
          {memory.summary ? <small className="memory-summary">{memory.summary}</small> : null}
          <div className="memory-meta">
            <span>重要性 {formatMemoryScore(memory.importance)}</span>
            <span>可信度 {formatMemoryScore(memory.confidence)}</span>
            <span>来源 {memory.source_type}#{memory.source_id ?? "-"}</span>
          </div>
          <div className="memory-card-actions">
            <Button onClick={() => onOpenDetail(memory)} size="sm">
              <Eye aria-hidden="true" size={15} />
              详情
            </Button>
            <Button onClick={() => onStartEdit(memory)} size="sm">
              <Pencil aria-hidden="true" size={15} />
              编辑
            </Button>
            {memory.status === "active" ? (
              <Button disabled={isSaving} onClick={() => onArchive(memory.id)} size="sm">
                <ArchiveX aria-hidden="true" size={15} />
                停用
              </Button>
            ) : (
              <>
                <Button disabled={isSaving} onClick={() => onActivate(memory.id)} size="sm">
                  <RotateCcw aria-hidden="true" size={15} />
                  启用
                </Button>
                <Button disabled={isSaving} onClick={() => onDelete(memory)} size="sm">
                  <Trash2 aria-hidden="true" size={15} />
                  删除
                </Button>
              </>
            )}
          </div>
        </>
      )}
    </article>
  );
}
