import { Plus } from "lucide-react";

import { Button } from "../../shared/ui";
import type { Conversation } from "./types";

interface ConversationListProps {
  activeConversationId: number | undefined;
  conversations: Conversation[];
  onNewConversation: () => void;
  onSelectConversation: (conversation: Conversation) => void;
}

export function ConversationList({
  activeConversationId,
  conversations,
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
        {conversations.map((conversation) => (
          <button
            className={conversation.id === activeConversationId ? "active" : ""}
            key={conversation.id}
            onClick={() => onSelectConversation(conversation)}
            type="button"
          >
            <strong>{conversation.title}</strong>
            {conversation.summary ? <span>{conversation.summary}</span> : null}
          </button>
        ))}
      </div>
    </aside>
  );
}
