import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  createConversation,
  getMessageGraph,
  getTurnGraph,
  listConversations,
  listMessages,
  streamChat,
} from "./chatApi";
import { ChatComposer } from "./ChatComposer";
import { ChatGraphPanel } from "./ChatGraphPanel";
import { ConversationList } from "./ConversationList";
import { MessageList } from "./MessageList";
import type { DraftAssistantMessage, ChatTurnGraph, Conversation } from "./types";

export function ChatWindow() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<DraftAssistantMessage[]>([]);
  const [input, setInput] = useState("");
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, string>>({});
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const [selectedGraph, setSelectedGraph] = useState<ChatTurnGraph | null>(null);
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const selectedGraphRef = useRef<ChatTurnGraph | null>(null);

  const activeConversationId = activeConversation?.id;
  const runningNodes = useMemo(
    () =>
      Object.entries(nodeStatuses)
        .filter(([, status]) => status === "running" || status === "pending")
        .slice(0, 4)
        .map(([node]) => node),
    [nodeStatuses],
  );

  useEffect(() => {
    bootstrapConversation().catch((currentError: unknown) => {
      setError(currentError instanceof Error ? currentError.message : "初始化对话失败");
    });
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    selectedGraphRef.current = selectedGraph;
  }, [selectedGraph]);

  useEffect(() => {
    const graph = selectedGraphRef.current;
    if (!activeConversationId || !graph || graph.status !== "running") {
      return;
    }

    let canceled = false;
    getTurnGraph(activeConversationId, graph.turn_id)
      .then((nextGraph) => {
        if (!canceled) {
          setSelectedGraph(nextGraph);
        }
      })
      .catch(() => {
        // 调试面板刷新失败不打断正在生成的回答；用户仍可再次点击图按钮重试。
      });

    return () => {
      canceled = true;
    };
  }, [activeConversationId, nodeStatuses]);

  async function bootstrapConversation() {
    const items = await listConversations();
    const conversation = items[0] ?? (await createConversation("记忆对话"));
    setConversations(items[0] ? items : [conversation]);
    setActiveConversation(conversation);
    setMessages(await listMessages(conversation.id));
  }

  async function handleNewConversation() {
    setError("");
    const conversation = await createConversation("记忆对话");
    setConversations((current) => [conversation, ...current]);
    setActiveConversation(conversation);
    setMessages([]);
    setSelectedGraph(null);
  }

  async function handleSelectConversation(conversation: Conversation) {
    setError("");
    setActiveConversation(conversation);
    setMessages(await listMessages(conversation.id));
    setSelectedGraph(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeConversationId || !input.trim() || isSending) {
      return;
    }

    const content = input.trim();
    const optimisticUser: DraftAssistantMessage = {
      id: -Date.now(),
      conversation_id: activeConversationId,
      role: "user",
      content,
      parent_id: messages.length > 0 ? messages[messages.length - 1].id : null,
      checkpoint_id: null,
      status: "streaming",
      token_count: 0,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    const optimisticAssistant: DraftAssistantMessage = {
      ...optimisticUser,
      id: optimisticUser.id - 1,
      role: "assistant",
      content: "",
      parent_id: optimisticUser.id,
      isStreaming: true,
    };

    setMessages((current) => [...current, optimisticUser, optimisticAssistant]);
    setInput("");
    setError("");
    setIsSending(true);
    setNodeStatuses({});

    try {
      await streamChat(activeConversationId, content, (streamEvent) => {
        if (streamEvent.event === "turn" || streamEvent.event === "node") {
          setNodeStatuses(streamEvent.data.node_statuses);
        }
        if (streamEvent.event === "turn") {
          const { turn_id, user_message, assistant_message } = streamEvent.data;
          setMessages((current) =>
            current
              .filter(
                (message) =>
                  message.id !== optimisticUser.id && message.id !== optimisticAssistant.id,
              )
              .concat([
                user_message,
                { ...assistant_message, turn_id, isStreaming: true },
              ]),
          );
        }
        if (streamEvent.event === "answer_delta") {
          setMessages((current) =>
            current.map((message) =>
              message.id === optimisticAssistant.id || message.isStreaming
                ? { ...message, content: message.content + streamEvent.data.content }
                : message,
            ),
          );
        }
        if (streamEvent.event === "done") {
          const { user_message, assistant_message } = streamEvent.data.response;
          setMessages((current) =>
            current
              .filter(
                (message) =>
                  message.id !== optimisticUser.id &&
                  message.id !== optimisticAssistant.id &&
                  message.id !== user_message.id &&
                  message.id !== assistant_message.id,
              )
              .concat([user_message, assistant_message]),
          );
        }
        if (streamEvent.event === "error") {
          setError(streamEvent.data.message);
        }
      });
      setConversations(await listConversations());
    } catch (currentError) {
      setError(currentError instanceof Error ? currentError.message : "发送失败");
    } finally {
      setIsSending(false);
      setNodeStatuses({});
    }
  }

  async function handleOpenGraph(message: DraftAssistantMessage) {
    if (!activeConversationId) {
      return;
    }
    setIsGraphLoading(true);
    setSelectedGraph(null);
    setError("");
    try {
      setSelectedGraph(
        message.turn_id
          ? await getTurnGraph(activeConversationId, message.turn_id)
          : await getMessageGraph(activeConversationId, message.id),
      );
    } catch (currentError) {
      setError(currentError instanceof Error ? currentError.message : "读取 graph 失败");
    } finally {
      setIsGraphLoading(false);
    }
  }

  return (
    <section className="chat-shell">
      <ConversationList
        activeConversationId={activeConversationId}
        conversations={conversations}
        onNewConversation={handleNewConversation}
        onSelectConversation={handleSelectConversation}
      />

      <section className="chat-main">
        <header className="chat-main-header">
          <div>
            <h2>{activeConversation?.title ?? "记忆对话"}</h2>
            <p>{activeConversation?.langgraph_thread_id ?? "准备连接 Memory Chat Graph"}</p>
          </div>
          {runningNodes.length > 0 ? <span>Graph: {runningNodes.join(", ")}</span> : null}
        </header>

        <MessageList endRef={messagesEndRef} messages={messages} onOpenGraph={handleOpenGraph} />

        {error ? <p className="chat-error">{error}</p> : null}

        <ChatComposer
          input={input}
          isSending={isSending}
          onInputChange={setInput}
          onSubmit={handleSubmit}
        />
      </section>

      {selectedGraph || isGraphLoading ? (
        <ChatGraphPanel
          graph={selectedGraph}
          isLoading={isGraphLoading}
          onClose={() => setSelectedGraph(null)}
        />
      ) : null}
    </section>
  );
}
