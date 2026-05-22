import { GitBranch } from "lucide-react";
import type { RefObject } from "react";

import { Button, EmptyState } from "../../shared/ui";
import { MarkdownMessage } from "./MarkdownMessage";
import type { ChatThought, DraftAssistantMessage } from "./types";

interface MessageListProps {
  endRef: RefObject<HTMLDivElement | null>;
  messages: DraftAssistantMessage[];
  onOpenGraph: (message: DraftAssistantMessage) => void;
  thoughts?: ChatThought[];
}

export function MessageList({ endRef, messages, onOpenGraph, thoughts = [] }: MessageListProps) {
  const activeThought = thoughts[thoughts.length - 1];
  const collapsedThoughts = thoughts.slice(0, -1);

  return (
    <div className="chat-message-list">
      {messages.length === 0 ? (
        <EmptyState className="chat-empty">向 Ai 记提一个关于笔记或记忆的问题</EmptyState>
      ) : null}
      {messages.map((message) => (
        <article className={`chat-message ${message.role}`} key={message.id}>
          <div className="chat-message-bubble">
            {message.role === "assistant" ? (
              <MarkdownMessage content={message.content} fallback={message.isStreaming ? "正在思考..." : ""} />
            ) : (
              <p>{message.content}</p>
            )}
            {message.role === "assistant" && message.turn_id ? (
              <Button
                aria-label="查看本轮 graph"
                onClick={() => onOpenGraph(message)}
                size="icon"
                title="查看本轮 graph"
              >
                <GitBranch aria-hidden="true" size={16} />
              </Button>
            ) : null}
          </div>
        </article>
      ))}
      {activeThought ? (
        <div className="chat-thought-stack">
          {collapsedThoughts.map((thought) => (
            <details className="chat-thought collapsed" key={thought.id}>
              <summary>{thought.title}</summary>
              <p>{thought.summary}</p>
            </details>
          ))}
          <div className="chat-thought active">
            <span>{activeThought.title}</span>
            <p>{activeThought.summary}</p>
          </div>
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  );
}
