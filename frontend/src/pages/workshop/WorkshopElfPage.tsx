import { Activity, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { listMessages } from "../../features/chat/chatApi";
import { ChatGraphPanel } from "../../features/chat/ChatGraphPanel";
import type { ChatMessage, ChatTurnGraph } from "../../features/chat/types";
import { getElfRuntimeStatus, getElfTurnGraph } from "../../features/elf/elfRuntimeApi";
import type { ElfRuntimeStatusRead } from "../../features/elf/types";
import { Button } from "../../shared/ui";

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  thinking: "整理上下文",
  tool_running: "执行工具",
  streaming_answer: "生成回复",
  speaking: "播放语音",
  waiting_user_input: "等待选择",
  completed: "已完成",
  failed: "已失败",
  recovering: "恢复中",
};

export function WorkshopElfPage() {
  const [status, setStatus] = useState<ElfRuntimeStatusRead | null>(null);
  const [error, setError] = useState("");
  const [graph, setGraph] = useState<ChatTurnGraph | null>(null);
  const [graphError, setGraphError] = useState("");
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const [isGraphOpen, setIsGraphOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyError, setHistoryError] = useState("");

  const loadGraph = useCallback(async (turnId: number) => {
    setIsGraphLoading(true);
    try {
      const nextGraph = await getElfTurnGraph(turnId);
      setGraph(nextGraph);
      setGraphError("");
    } catch (currentError) {
      setGraphError(currentError instanceof Error ? currentError.message : "读取精灵 graph 失败。");
    } finally {
      setIsGraphLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const nextStatus = await getElfRuntimeStatus();
        if (!cancelled) {
          setStatus(nextStatus);
          setError("");
        }
      } catch (currentError) {
        if (!cancelled) {
          setError(currentError instanceof Error ? currentError.message : "读取精灵状态失败。");
        }
      }
    }

    void refresh();
    const intervalId = window.setInterval(refresh, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  useEffect(() => {
    if (!isGraphOpen || typeof status?.turn_id !== "number") {
      return;
    }
    void loadGraph(status.turn_id);
  }, [isGraphOpen, loadGraph, status?.turn_id, status?.status, status?.updated_at]);

  useEffect(() => {
    if (typeof status?.conversation_id !== "number") {
      setMessages([]);
      setHistoryError("");
      return;
    }

    let cancelled = false;

    async function refreshHistory() {
      if (typeof status?.conversation_id !== "number") {
        return;
      }
      try {
        const nextMessages = await listMessages(status.conversation_id);
        if (!cancelled) {
          setMessages(nextMessages);
          setHistoryError("");
        }
      } catch (currentError) {
        if (!cancelled) {
          setHistoryError(currentError instanceof Error ? currentError.message : "读取精灵历史消息失败。");
        }
      }
    }

    void refreshHistory();
    return () => {
      cancelled = true;
    };
  }, [status?.conversation_id, status?.updated_at]);

  if (error && !status) {
    return <div className="workshop-error-slot">{error}</div>;
  }

  return (
    <div className="elf-runtime-panel">
      <section className="elf-runtime-card elf-runtime-card--primary">
        <div>
          <span className="elf-runtime-kicker">Memo Elf Runtime</span>
          <h2>精灵状态</h2>
          <p>{status?.message || "当前没有需要恢复的精灵对话。"}</p>
          {graphError ? <p className="elf-runtime-inline-error">{graphError}</p> : null}
          <div className="elf-runtime-actions">
            <Button
              disabled={typeof status?.turn_id !== "number"}
              onClick={() => {
                if (typeof status?.turn_id !== "number") {
                  return;
                }
                setIsGraphOpen(true);
                void loadGraph(status.turn_id);
              }}
              size="sm"
            >
              <Activity aria-hidden="true" size={15} />
              查看上下文 / Graph
            </Button>
            <Button
              disabled={typeof status?.turn_id !== "number" || isGraphLoading}
              onClick={() => {
                if (typeof status?.turn_id !== "number") {
                  return;
                }
                void loadGraph(status.turn_id);
              }}
              size="sm"
              variant="ghost"
            >
              <RefreshCw aria-hidden="true" size={15} />
              刷新
            </Button>
          </div>
        </div>
        <span className={`elf-runtime-status elf-runtime-status--${status?.status ?? "idle"}`}>
          {STATUS_LABELS[status?.status ?? "idle"] ?? status?.status ?? "空闲"}
        </span>
      </section>

      <section className="elf-runtime-grid">
        <RuntimeField label="是否占用" value={status?.busy ? "是" : "否"} />
        <RuntimeField label="Conversation" value={formatNullable(status?.conversation_id)} />
        <RuntimeField label="Turn" value={formatNullable(status?.turn_id)} />
        <RuntimeField label="更新时间" value={status ? new Date(status.updated_at).toLocaleString() : "-"} />
      </section>

      {status?.pending_interrupt ? (
        <section className="elf-runtime-card">
          <h3>等待用户选择</h3>
          <pre>{JSON.stringify(status.pending_interrupt, null, 2)}</pre>
        </section>
      ) : null}

      {status?.last_message ? (
        <section className="elf-runtime-card">
          <h3>最后消息</h3>
          <p>{status.last_message}</p>
        </section>
      ) : null}

      {status?.last_error ? (
        <section className="elf-runtime-card elf-runtime-card--error">
          <h3>最后错误</h3>
          <p>{status.last_error}</p>
        </section>
      ) : null}

      <ElfChatHistory
        error={historyError}
        isGraphLoading={isGraphLoading}
        messages={messages}
        onOpenGraph={(turnId) => {
          setIsGraphOpen(true);
          void loadGraph(turnId);
        }}
      />

      {isGraphOpen ? (
        <ChatGraphPanel
          graph={graph}
          isLoading={isGraphLoading}
          onClose={() => setIsGraphOpen(false)}
        />
      ) : null}
    </div>
  );
}

function ElfChatHistory({
  error,
  isGraphLoading,
  messages,
  onOpenGraph,
}: {
  error: string;
  isGraphLoading: boolean;
  messages: ChatMessage[];
  onOpenGraph: (turnId: number) => void;
}) {
  const visibleMessages = messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime());

  return (
    <section className="elf-runtime-card elf-history-card">
      <div className="elf-history-header">
        <div>
          <span className="elf-runtime-kicker">Conversation History</span>
          <h3>历史对话</h3>
        </div>
        <span>{visibleMessages.length} 条</span>
      </div>

      {error ? <p className="elf-runtime-inline-error">{error}</p> : null}
      {visibleMessages.length === 0 ? (
        <p>当前还没有可展示的精灵历史消息。</p>
      ) : (
        <ol className="elf-history-list">
          {visibleMessages.map((message) => (
            <li className="elf-history-item" key={message.id}>
              <div className={`elf-history-dot elf-history-dot--${message.role}`} aria-hidden="true" />
              <article className="elf-history-entry">
                <header>
                  <span className={`elf-history-role elf-history-role--${message.role}`}>
                    {message.role === "assistant" ? "精灵" : "用户"}
                  </span>
                  <time dateTime={message.created_at}>{formatDateTime(message.created_at)}</time>
                </header>
                <div className="elf-history-content">{message.content || "（空消息）"}</div>
                {message.role === "assistant" && typeof message.turn_id === "number" ? (
                  <Button
                    disabled={isGraphLoading}
                    onClick={() => onOpenGraph(message.turn_id as number)}
                    size="sm"
                    variant="ghost"
                  >
                    <Activity aria-hidden="true" size={14} />
                    查看 Graph
                  </Button>
                ) : null}
              </article>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function RuntimeField({ label, value }: { label: string; value: string }) {
  return (
    <div className="elf-runtime-field">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatNullable(value: number | null | undefined) {
  return typeof value === "number" ? `#${value}` : "-";
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString([], {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
