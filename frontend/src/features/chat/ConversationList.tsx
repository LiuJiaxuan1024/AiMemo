import { MessageSquare, Plus, Trash2 } from "lucide-react";

import { Button } from "../../shared/ui";
import { formatRelativeTime } from "./formatRelativeTime";
import type { Conversation } from "./types";

interface ConversationListProps {
  activeConversationId: number | undefined;
  conversations: Conversation[];
  onDeleteConversation: (conversation: Conversation) => void;
  onNewConversation: () => void;
  onSelectConversation: (conversation: Conversation) => void;
}

export function ConversationList({
  activeConversationId,
  conversations,
  onDeleteConversation,
  onNewConversation,
  onSelectConversation,
}: ConversationListProps) {
  return (
    <aside className="chat-sidebar">
      <header>
        <h2>对话</h2>
        <Button onClick={onNewConversation} size="sm">
          <Plus aria-hidden="true" size={16} />
          新建
        </Button>
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
                    <span className="chat-conv-card__summary">{conversation.summary}</span>
                  ) : null}
                  {relativeTime ? (
                    <span className="chat-conv-card__meta">{relativeTime}</span>
                  ) : null}
                </span>
              </button>
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
            </div>
          );
        })}
      </div>
    </aside>
  );
}
