import { MessageCircleQuestion, MessageSquare, PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { CompactMarkdown, MarkdownView } from "../../shared/ui";
import { ChatMessageBody } from "./ChatMessageBody";
import type {
  ConversationExportSnapshot,
  ConversationMultiExportSnapshot,
  ExportAttachment,
  ExportMessage,
  ExportSegmentFollowupThread,
} from "./types";
import type { ChatAttachment, DraftAssistantMessage, SegmentFollowupThread } from "../chat/types";

interface ExportConversationViewProps {
  snapshot: ConversationExportSnapshot | ConversationMultiExportSnapshot;
}

export function ExportConversationView({ snapshot }: ExportConversationViewProps) {
  const snapshots = isMultiSnapshot(snapshot) ? snapshot.conversations : [snapshot];
  const primary = snapshots[0];
  const exportedAt = isMultiSnapshot(snapshot) ? snapshot.exported_at : primary?.conversation.exported_at;
  return (
    <main className="aimemo-export-shell">
      <header className="aimemo-export-appbar">
        <div className="aimemo-export-brand">
          <span className="aimemo-export-brand-mark">Ai</span>
          <div>
            <strong>AiMemo</strong>
            <small>本地个人知识中心</small>
          </div>
        </div>
        <nav className="aimemo-export-nav" aria-label="静态导出导航">
          <span>Ai 记</span>
          <span className="active">对话</span>
          <span>知库</span>
          <span>工坊</span>
        </nav>
      </header>

      <section className="chat-shell aimemo-export-chat-shell">
        <button
          aria-label="打开导出的对话列表"
          className="chat-sidebar-toggle aimemo-export-sidebar-toggle"
          data-export-toggle-sidebar="true"
          title="打开或收起对话列表"
          type="button"
        >
          <PanelLeftOpen aria-hidden="true" className="aimemo-export-sidebar-icon-open" size={18} />
          <PanelLeftClose aria-hidden="true" className="aimemo-export-sidebar-icon-close" size={18} />
        </button>
        <button
          aria-label="关闭导出的对话列表"
          className="aimemo-export-sidebar-scrim"
          data-export-close-sidebar="true"
          type="button"
        />

        <aside className="chat-sidebar aimemo-export-sidebar" aria-label="导出的会话">
          <header>
            <h2>对话</h2>
          </header>
          <div className="chat-conversation-list">
            {snapshots.map((item, index) => (
              <article
                className={`chat-conv-card${index === 0 ? " chat-conv-card--active" : ""}`}
                data-export-conversation-card={item.conversation.id}
                key={item.conversation.id}
              >
                <a
                  className="chat-conv-card__button"
                  data-export-select-conversation={item.conversation.id}
                  href={`#conversation-${item.conversation.id}`}
                >
                  <span className="chat-conv-card__icon" aria-hidden="true">
                    <MessageSquare size={16} />
                  </span>
                  <span className="chat-conv-card__body">
                    <strong className="chat-conv-card__title">{item.conversation.title}</strong>
                    <CompactMarkdown
                      className="chat-conv-card__summary"
                      content={item.conversation.summary}
                      fallback="导出的静态对话"
                    />
                    <span className="chat-conv-card__meta">{formatExportDate(item.conversation.exported_at)}</span>
                  </span>
                </a>
              </article>
            ))}
          </div>
        </aside>

        <section className="chat-main aimemo-export-chat-main">
          {snapshots.map((item, index) => (
            <section
              className="aimemo-export-conversation"
              id={`conversation-${item.conversation.id}`}
              data-export-conversation={item.conversation.id}
              hidden={index !== 0}
              key={item.conversation.id}
            >
              <header className="chat-main-header aimemo-export-hero">
                <div className="aimemo-export-hero__title">
                  <h2>{item.conversation.title}</h2>
                  <p>{item.conversation.langgraph_thread_id}</p>
                  {item.conversation.summary ? (
                    <details className="aimemo-export-summary">
                      <summary>会话摘要</summary>
                      <div className="aimemo-export-summary-body">
                        <MarkdownView
                          className="aimemo-export-summary-markdown"
                          content={item.conversation.summary}
                        />
                      </div>
                    </details>
                  ) : null}
                </div>
                <dl className="aimemo-export-meta">
                  <div>
                    <dt>消息</dt>
                    <dd>{item.messages.length}</dd>
                  </div>
                  <div>
                    <dt>导出</dt>
                    <dd title={item.conversation.exported_at}>{formatExportDate(item.conversation.exported_at)}</dd>
                  </div>
                </dl>
              </header>

              <section className="chat-message-list aimemo-export-timeline" aria-label="对话内容">
                {item.messages.map((message) => (
                  <ExportMessageCard
                    key={message.id}
                    message={message}
                    snapshot={item}
                  />
                ))}
              </section>
            </section>
          ))}

          <div className="chat-input-bar aimemo-export-composer" aria-hidden="true">
            <textarea disabled placeholder={`${snapshots.length} 个对话导出于 ${formatExportDate(exportedAt ?? "")}`} />
            <button disabled type="button">发送</button>
          </div>
        </section>
      </section>
    </main>
  );
}

function isMultiSnapshot(
  snapshot: ConversationExportSnapshot | ConversationMultiExportSnapshot,
): snapshot is ConversationMultiExportSnapshot {
  return "conversations" in snapshot;
}

function ExportMessageCard({
  message,
  snapshot,
}: {
  message: ExportMessage;
  snapshot: ConversationExportSnapshot;
}) {
  const draftMessage = exportMessageToDraft(message, snapshot);
  const roleLabel =
    message.role === "user" ? "用户" : message.role === "assistant" ? "AiMemo" : message.role;
  return (
    <article className={`chat-message ${message.role} aimemo-export-message`} id={`message-${message.id}`}>
      <div className="aimemo-export-message-frame">
        <div className="aimemo-export-message-meta">
          <span>{roleLabel}</span>
          <time>{message.created_at}</time>
        </div>
        <div className="chat-message-bubble">
          <div className="chat-message-content">
            {message.role === "assistant" ? (
              <div className="aimemo-export-message-body" data-export-message-body={message.id}>
                <ChatMessageBody
                  commandActionsDisabled
                  message={draftMessage}
                  onOpenSegment={() => undefined}
                />
              </div>
            ) : (
              <p>{message.content}</p>
            )}
            {message.attachments.length > 0 ? (
              <ExportAttachments attachments={message.attachments} />
            ) : null}
          </div>
        </div>
      </div>

      {message.followup_threads.length > 0 ? (
        <ExportFollowups messageId={message.id} threads={message.followup_threads} />
      ) : null}
    </article>
  );
}

function ExportAttachments({ attachments }: { attachments: ExportAttachment[] }) {
  return (
    <div className="chat-message-attachments">
      {attachments.map((attachment, index) => {
        const href = attachment.data_uri || attachment.url || undefined;
        const key = attachment.id ? String(attachment.id) : `${attachment.original_name}-${index}`;
        if (attachment.kind === "image" && href) {
          return (
            <button
              className="chat-message-attachment chat-message-attachment--image"
              data-export-image-name={attachment.original_name}
              data-export-image-preview={href}
              key={key}
              title={attachment.original_name}
              type="button"
            >
              <img alt={attachment.original_name} src={href} />
              <span>{attachment.original_name}</span>
            </button>
          );
        }
        return (
          <a
            className="chat-message-attachment"
            href={href}
            key={key}
            rel="noreferrer"
            target="_blank"
            title={attachment.original_name}
          >
            <span>{attachment.original_name}</span>
            <small>{formatAttachmentSize(attachment.size_bytes)}</small>
          </a>
        );
      })}
    </div>
  );
}

function ExportFollowups({
  messageId,
  threads,
}: {
  messageId: number;
  threads: ExportSegmentFollowupThread[];
}) {
  return (
    <section className="aimemo-export-followups" id={`followups-${messageId}`}>
      <header>
        <MessageCircleQuestion aria-hidden="true" size={16} />
        <h2>片段追问</h2>
      </header>
      {threads.map((thread) => (
        <details
          className="segment-followup-panel__item"
          data-export-followup-thread={thread.segment_id}
          key={thread.segment_id}
          open
        >
          <summary className="segment-followup-panel__summary">
            <span className="segment-followup-panel__badge">片段</span>
            <span className="segment-followup-panel__source-text">{thread.original_text}</span>
            <span className={`segment-followup-panel__status segment-followup-panel__status--${thread.status}`}>
              {followupStatusText(thread.status)}
            </span>
            <strong>{thread.turns[thread.turns.length - 1]?.question ?? "片段追问"}</strong>
            <small>{thread.turns.length} 轮对话</small>
          </summary>
          <div className="segment-followup-thread-turns">
            {thread.turns.map((turn, index) => (
              <article className="segment-followup-turn" key={`${thread.segment_id}-${index}`}>
                <div className="segment-followup-turn__question">
                  <span>追问</span>
                  <p>{turn.question}</p>
                </div>
                <div className="segment-followup-turn__answer">
                  <span>回答 · {turn.timestamp}</span>
                  <div
                    className="markdown-message"
                    dangerouslySetInnerHTML={{
                      __html: turn.answer_html || escapeHtml(turn.answer || "暂无回答"),
                    }}
                  />
                </div>
              </article>
            ))}
          </div>
        </details>
      ))}
    </section>
  );
}

function exportMessageToDraft(
  message: ExportMessage,
  snapshot: ConversationExportSnapshot,
): DraftAssistantMessage {
  return {
    id: message.id,
    conversation_id: snapshot.conversation.id,
    role: message.role,
    content: message.content,
    parent_id: null,
    checkpoint_id: null,
    status: message.status,
    token_count: message.token_count,
    attachments: message.attachments.map(exportAttachmentToChatAttachment),
    turn_id: message.turn_id,
    pending_interrupt: null,
    created_at: message.created_at,
    updated_at: message.created_at,
    followupThreads: message.followup_threads.map(exportThreadToDraftThread),
  };
}

function exportAttachmentToChatAttachment(attachment: ExportAttachment): ChatAttachment {
  return {
    id: attachment.id,
    conversation_id: 0,
    message_id: null,
    kind: attachment.kind,
    original_name: attachment.original_name,
    mime_type: attachment.mime_type,
    size_bytes: attachment.size_bytes,
    width: attachment.width,
    height: attachment.height,
    sha256: "",
    status: attachment.status,
    retention_policy: "export",
    url: attachment.data_uri || attachment.url,
    created_at: "",
    updated_at: "",
  };
}

function exportThreadToDraftThread(thread: ExportSegmentFollowupThread): SegmentFollowupThread {
  return {
    segment_id: thread.segment_id,
    original_text: thread.original_text,
    position: thread.position,
    followups: thread.turns.map((turn, index) => ({
      followup_id: `${thread.segment_id}-${index}`,
      user_question: turn.question,
      assistant_answer: turn.answer,
      status: turn.status,
      timestamp: turn.timestamp,
    })),
  };
}

function followupStatusText(status: string) {
  if (status === "answered") {
    return "已回答";
  }
  if (status === "failed") {
    return "失败";
  }
  return "等待中";
}

function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes >= 1024 * 1024) {
    return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
  }
  if (sizeBytes >= 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${sizeBytes} B`;
}

function formatExportDate(value: string): string {
  return value.replace("T", " ").slice(0, 16) || value;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
