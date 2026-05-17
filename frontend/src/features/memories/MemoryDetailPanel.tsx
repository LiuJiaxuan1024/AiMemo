import { ExternalLink, X } from "lucide-react";

import { Badge, Button } from "../../shared/ui";
import type { MemoryDetail } from "./types";
import {
  CATEGORY_LABELS,
  STATUS_LABELS,
  formatMemoryScore,
  formatMemoryTime,
} from "./memoryUtils";

interface MemoryDetailPanelProps {
  detail: MemoryDetail | null;
  error: string;
  isLoading: boolean;
  onClose: () => void;
}

/**
 * 长期记忆详情面板。
 * 第一版重点解决“这条记忆是什么、为什么可信、从哪里来”；跳转到对话树后续再接入。
 */
export function MemoryDetailPanel({
  detail,
  error,
  isLoading,
  onClose,
}: MemoryDetailPanelProps) {
  return (
    <aside className="memory-detail-panel">
      <header>
        <div>
          <h3>记忆详情</h3>
          <p>{detail ? `Memory #${detail.id}` : "正在读取来源信息"}</p>
        </div>
        <Button aria-label="关闭记忆详情" onClick={onClose} size="icon" variant="ghost">
          <X aria-hidden="true" size={16} />
        </Button>
      </header>

      {isLoading ? <p className="memory-detail-muted">正在读取详情...</p> : null}
      {error ? <div className="job-drawer-error">{error}</div> : null}

      {detail ? (
        <div className="memory-detail-content">
          <section>
            <div className="memory-detail-heading">
              <strong>{CATEGORY_LABELS.get(detail.category) ?? detail.category}</strong>
              <Badge tone={detail.status === "active" ? "success" : "neutral"}>
                {STATUS_LABELS[detail.status]}
              </Badge>
            </div>
            <p>{detail.content}</p>
            {detail.summary ? <small>{detail.summary}</small> : null}
          </section>

          <dl className="memory-detail-grid">
            <div>
              <dt>层级</dt>
              <dd>L{detail.level}</dd>
            </div>
            <div>
              <dt>重要性</dt>
              <dd>{formatMemoryScore(detail.importance)}</dd>
            </div>
            <div>
              <dt>可信度</dt>
              <dd>{formatMemoryScore(detail.confidence)}</dd>
            </div>
            <div>
              <dt>更新时间</dt>
              <dd>{formatMemoryTime(detail.updated_at)}</dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>
                {detail.source_type}#{detail.source_id ?? "-"}
              </dd>
            </div>
            <div>
              <dt>Hash</dt>
              <dd title={detail.content_hash}>{detail.content_hash.slice(0, 12)}...</dd>
            </div>
          </dl>

          <section className="memory-source-card">
            <div className="memory-detail-heading">
              <strong>来源追踪</strong>
              {detail.source_message ? (
                <Badge tone="neutral">conversation #{detail.source_message.conversation_id}</Badge>
              ) : null}
            </div>
            {detail.source_message ? (
              <>
                <p className="memory-detail-muted">
                  {detail.source_message.conversation_title} · {detail.source_message.role} ·{" "}
                  {formatMemoryTime(detail.source_message.created_at)}
                </p>
                <blockquote>{detail.source_message.content}</blockquote>
                <Button disabled size="sm" title="后续会接入跳转到对应对话节点">
                  <ExternalLink aria-hidden="true" size={15} />
                  跳转对话
                </Button>
              </>
            ) : (
              <p className="memory-detail-muted">暂无可解析的来源消息。</p>
            )}
          </section>
        </div>
      ) : null}
    </aside>
  );
}
