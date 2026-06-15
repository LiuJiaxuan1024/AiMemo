import { Check, MessageSquare, Plus, Trash2 } from "lucide-react";

import { Button, CompactMarkdown } from "../../shared/ui";
import { formatRelativeTime } from "./formatRelativeTime";
import type { Conversation } from "./types";

interface ConversationListProps {
  activeConversationId: number | undefined;
  className?: string;
  conversations: Conversation[];
  exportMode?: boolean;
  onDeleteConversation: (conversation: Conversation) => void;
  onToggleExportConversation?: (conversationId: number) => void;
  onNewConversation: () => void;
  onSelectConversation: (conversation: Conversation) => void;
  selectedExportConversationIds?: Set<number>;
}

export function ConversationList({
  activeConversationId,
  className = "",
  conversations,
  exportMode = false,
  onDeleteConversation,
  onToggleExportConversation,
  onNewConversation,
  onSelectConversation,
  selectedExportConversationIds = new Set(),
}: ConversationListProps) {
  return (
    <aside className={`chat-sidebar${className ? ` ${className}` : ""}`}>
      <header>
        <h2>对话</h2>
        <div className="chat-sidebar__actions">
          <Button onClick={onNewConversation} size="sm">
            <Plus aria-hidden="true" size={16} />
            新建
          </Button>
        </div>
      </header>
      <div className="chat-conversation-list">
        {conversations.length === 0 ? (
          <p className="chat-conv-empty">还没有对话，点击右上角"新建"开始。</p>
        ) : null}
        {conversations.map((conversation) => {
          const isActive = conversation.id === activeConversationId;
          const relativeTime = formatRelativeTime(
            conversation.updated_at ?? conversation.created_at,
          );
          return (
            <div
              className={`chat-conv-card${isActive ? " chat-conv-card--active" : ""}`}
              key={conversation.id}
            >
              <button
                className="chat-conv-card__button"
                onClick={() => onSelectConversation(conversation)}
                type="button"
              >
                <span className="chat-conv-card__icon" aria-hidden="true">
                  <MessageSquare size={16} />
                </span>
                <span className="chat-conv-card__body">
                  <span className="chat-conv-card__title" title={conversation.title}>
                    {conversation.title || "新对话"}
                  </span>
                  {conversation.summary ? (
                    <CompactMarkdown className="chat-conv-card__summary" content={conversation.summary} />
                  ) : null}
                  {relativeTime ? (
                    <span className="chat-conv-card__meta">{relativeTime}</span>
                  ) : null}
                </span>
              </button>
              {exportMode ? (
                <button
                  aria-label={
                    selectedExportConversationIds.has(conversation.id)
                      ? `取消导出对话「${conversation.title}」`
                      : `选择导出对话「${conversation.title}」`
                  }
                  aria-pressed={selectedExportConversationIds.has(conversation.id)}
                  className="chat-conv-card__export-check"
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggleExportConversation?.(conversation.id);
                  }}
                  title={selectedExportConversationIds.has(conversation.id) ? "取消导出此对话" : "选择导出此对话"}
                  type="button"
                >
                  {selectedExportConversationIds.has(conversation.id) ? <Check size={14} aria-hidden="true" /> : null}
                </button>
              ) : (
                <button
                  aria-label={`删除对话「${conversation.title}」`}
                  className="chat-conv-card__delete"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDeleteConversation(conversation);
                  }}
                  title="删除对话并释放相关资源"
                  type="button"
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
