import { FormEvent, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createConversation,
  deleteConversation,
  getTurnGraph,
  listActiveTurns,
  listConversations,
  listMessages,
  resumeInterruptedTurn,
  streamChat,
  streamTurnResume,
} from "./chatApi";
import { ChatComposer } from "./ChatComposer";
import { ConversationList } from "./ConversationList";
import { MessageList } from "./MessageList";
import {
  emitChatAnswerStartedElfEvent,
  emitChatDoneElfEvent,
  emitChatErrorElfEvent,
  emitChatGraphOpenElfEvent,
  emitChatNodeElfEvent,
} from "./chatElfEvents";
import { applyChatStreamEvent, streamingStore, useConversationView } from "./streamingStore";
import type {
  ChatStreamEvent,
  ChatTurnGraph,
  Conversation,
  DraftAssistantMessage,
  UserInputAnswer,
} from "./types";

const ChatGraphPanel = lazy(() =>
  import("./ChatGraphPanel").then((module) => ({ default: module.ChatGraphPanel })),
);

export function ChatWindow() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [input, setInput] = useState("");
  const [selectedGraph, setSelectedGraph] = useState<ChatTurnGraph | null>(null);
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const selectedGraphRef = useRef<ChatTurnGraph | null>(null);

  const activeConversationId = activeConversation?.id;
  const view = useConversationView(activeConversationId);
  const messages = view?.messages ?? [];
  const nodeStatuses = view?.nodeStatuses ?? {};
  const thoughts = view?.thoughts ?? [];
  const isStreaming = view?.isStreaming ?? false;
  const error = view?.error ?? "";

  const runningNodes = useMemo(
    () =>
      Object.entries(nodeStatuses)
        .filter(([, status]) => status === "running" || status === "pending")
        .slice(0, 4)
        .map(([node]) => node),
    [nodeStatuses],
  );
  const scrollAnchor = useMemo(() => {
    const lastMessage = messages[messages.length - 1];
    return `${messages.length}:${lastMessage?.id ?? ""}:${lastMessage?.content.length ?? 0}`;
  }, [messages]);

  const setError = useCallback((conversationId: number, value: string) => {
    streamingStore.patch(conversationId, { error: value });
  }, []);

  const dispatchStreamEvent = useCallback(
    (conversationId: number, event: ChatStreamEvent) => {
      applyChatStreamEvent(conversationId, event);
      if (event.event === "node") {
        emitChatNodeElfEvent(event.data.node);
      } else if (event.event === "answer_delta") {
        const slot = streamingStore.peek(conversationId);
        if (slot && !slot.hasEmittedAnswerStarted && event.data.content.length > 0) {
          streamingStore.patch(conversationId, { hasEmittedAnswerStarted: true });
          emitChatAnswerStartedElfEvent();
        }
      } else if (event.event === "done") {
        emitChatDoneElfEvent(event.data.turn_id);
      } else if (event.event === "error") {
        emitChatErrorElfEvent(event.data.message, event.data.turn_id);
      } else if (event.event === "interrupt") {
        emitChatNodeElfEvent("interrupt");
      }
    },
    [],
  );

  useEffect(() => {
    bootstrapConversation().catch((currentError: unknown) => {
      if (activeConversationId) {
        setError(
          activeConversationId,
          currentError instanceof Error ? currentError.message : "初始化对话失败",
        );
      }
    });
  }, []);

  useEffect(() => {
    if (!shouldAutoScroll) {
      return;
    }
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [scrollAnchor, shouldAutoScroll]);

  const updateAutoScrollIntent = useCallback(() => {
    const list = messageListRef.current;
    if (!list) {
      return;
    }
    const distanceToBottom = list.scrollHeight - list.scrollTop - list.clientHeight;
    setShouldAutoScroll(distanceToBottom < 96);
  }, []);

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

  async function ensureConversationLoaded(conversation: Conversation): Promise<void> {
    const existing = streamingStore.peek(conversation.id);
    if (existing?.loaded) {
      return;
    }
    streamingStore.patch(conversation.id, { loaded: true });
    const messagesList = await listMessages(conversation.id);
    streamingStore.update(conversation.id, (current) => ({
      ...current,
      messages: messagesList as DraftAssistantMessage[],
      error: "",
    }));
    // 一并探测是否有正在跑的 turn；有就接上 SSE 重放 + 增量。
    await attachActiveTurns(conversation.id);
  }

  async function attachActiveTurns(conversationId: number): Promise<void> {
    try {
      const { items } = await listActiveTurns(conversationId);
      for (const item of items) {
        if (item.status === "interrupted") {
          streamingStore.patch(conversationId, {
            isStreaming: false,
            streamingTurnId: item.turn_id,
            nodeStatuses: item.node_statuses,
          });
          if (item.pending_interrupt) {
            streamingStore.updateMessages(conversationId, (messages) =>
              messages.map((message) =>
                message.turn_id === item.turn_id || message.id === item.assistant_message?.id
                  ? {
                      ...message,
                      status: "interrupted",
                      isStreaming: false,
                      pending_interrupt: item.pending_interrupt ?? null,
                    }
                  : message,
              ),
            );
            if (
              item.assistant_message &&
              !streamingStore
                .peek(conversationId)
                ?.messages.some(
                  (message) =>
                    message.turn_id === item.turn_id || message.id === item.assistant_message?.id,
                )
            ) {
              streamingStore.updateMessages(conversationId, (messages) => {
                const next = [...messages];
                if (item.user_message && !next.some((message) => message.id === item.user_message?.id)) {
                  next.push({ ...item.user_message, conversation_id: conversationId });
                }
                next.push({
                  ...item.assistant_message!,
                  conversation_id: conversationId,
                  turn_id: item.turn_id,
                  status: "interrupted",
                  isStreaming: false,
                  pending_interrupt: item.pending_interrupt ?? null,
                });
                return next;
              });
            }
          }
          continue;
        }
        const slot = streamingStore.peek(conversationId);
        if (slot?.streamingTurnId === item.turn_id && slot.abortController) {
          continue;
        }
        const controller = new AbortController();
        streamingStore.patch(conversationId, {
          isStreaming: true,
          streamingTurnId: item.turn_id,
          nodeStatuses: item.node_statuses,
          abortController: controller,
        });
        // 不 await：后台跟随 stream，事件直接写入对应 conversation 的 slot。
        streamTurnResume(
          conversationId,
          item.turn_id,
          (event) => dispatchStreamEvent(conversationId, event),
          { signal: controller.signal },
        )
          .catch((currentError: unknown) => {
            if (controller.signal.aborted) {
              return;
            }
            setError(
              conversationId,
              currentError instanceof Error ? currentError.message : "重连流失败",
            );
          })
          .finally(() => {
            const latest = streamingStore.peek(conversationId);
            if (latest?.abortController === controller) {
              streamingStore.patch(conversationId, { abortController: null });
            }
            void refreshConversations();
          });
      }
    } catch {
      // active-turns 拉取失败不打断主流程；用户可以正常发新消息。
    }
  }

  async function refreshConversations(): Promise<void> {
    try {
      setConversations(await listConversations());
    } catch {
      // 列表刷新失败时保留旧数据；下次操作会再触发。
    }
  }

  async function bootstrapConversation() {
    const items = await listConversations();
    const conversation = items[0] ?? (await createConversation());
    setConversations(items[0] ? items : [conversation]);
    setActiveConversation(conversation);
    await ensureConversationLoaded(conversation);
  }

  async function handleNewConversation() {
    const conversation = await createConversation();
    setConversations((current) => [conversation, ...current]);
    setActiveConversation(conversation);
    setShouldAutoScroll(true);
    streamingStore.patch(conversation.id, { loaded: true });
    setSelectedGraph(null);
  }

  async function handleSelectConversation(conversation: Conversation) {
    setActiveConversation(conversation);
    setShouldAutoScroll(true);
    setSelectedGraph(null);
    // 切到正在 streaming 的会话不会重新拉 listMessages，view 已经包含最新 messages；
    // 第一次切入未加载的会话才会执行 fetch + active-turns 探测。
    try {
      await ensureConversationLoaded(conversation);
    } catch (currentError) {
      setError(
        conversation.id,
        currentError instanceof Error ? currentError.message : "加载消息失败",
      );
    }
  }

  async function handleDeleteConversation(conversation: Conversation) {
    if (typeof window !== "undefined") {
      const confirmed = window.confirm(
        `确认删除对话「${conversation.title}」？\n该操作会同时释放：消息、长期记忆、后台任务、Graph checkpoint 等全部相关资源。`,
      );
      if (!confirmed) {
        return;
      }
    }
    try {
      await deleteConversation(conversation.id);
      streamingStore.remove(conversation.id);
      const remaining = conversations.filter((item) => item.id !== conversation.id);
      setConversations(remaining);
      if (activeConversationId !== conversation.id) {
        return;
      }
      if (remaining.length > 0) {
        const next = remaining[0];
        setActiveConversation(next);
        await ensureConversationLoaded(next);
      } else {
        const created = await createConversation("新对话");
        setConversations([created]);
        setActiveConversation(created);
        streamingStore.patch(created.id, { loaded: true });
      }
      setSelectedGraph(null);
    } catch (currentError) {
      if (activeConversationId) {
        setError(
          activeConversationId,
          currentError instanceof Error ? currentError.message : "删除对话失败",
        );
      }
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeConversationId || !input.trim() || isStreaming) {
      return;
    }

    const conversationId = activeConversationId;
    const content = input.trim();
    setShouldAutoScroll(true);
    const optimisticUserId = -Date.now();
    const optimisticAssistantId = optimisticUserId - 1;
    const optimisticUser: DraftAssistantMessage = {
      id: optimisticUserId,
      conversation_id: conversationId,
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
      id: optimisticAssistantId,
      role: "assistant",
      content: "",
      parent_id: optimisticUserId,
      isStreaming: true,
    };

    const controller = new AbortController();
    streamingStore.update(conversationId, (current) => ({
      ...current,
      messages: [...current.messages, optimisticUser, optimisticAssistant],
      nodeStatuses: {},
      thoughts: [],
      hasEmittedAnswerStarted: false,
      error: "",
      isStreaming: true,
      streamingTurnId: null,
      pendingOptimisticIds: {
        userId: optimisticUserId,
        assistantId: optimisticAssistantId,
      },
      abortController: controller,
    }));
    setInput("");

    try {
      await streamChat(
        conversationId,
        content,
        (streamEvent) => dispatchStreamEvent(conversationId, streamEvent),
        { signal: controller.signal },
      );
      await refreshConversations();
    } catch (currentError) {
      if (!controller.signal.aborted) {
        setError(
          conversationId,
          currentError instanceof Error ? currentError.message : "发送失败",
        );
        streamingStore.update(conversationId, (current) => {
          const messages = current.messages.map((message) =>
            message.isStreaming && message.role === "assistant"
              ? { ...message, isStreaming: false, status: "failed" }
              : message,
          );
          return {
            ...current,
            messages,
            isStreaming: false,
            streamingTurnId: null,
            thoughts: [],
            pendingOptimisticIds: null,
            abortController: null,
          };
        });
      }
    } finally {
      const latest = streamingStore.peek(conversationId);
      if (latest?.abortController === controller) {
        streamingStore.patch(conversationId, { abortController: null });
      }
      if (latest?.isStreaming) {
        streamingStore.patch(conversationId, { isStreaming: false });
      }
      scheduleConversationRefresh();
    }
  }

  async function handleUserInputAnswer(
    message: DraftAssistantMessage,
    answer: UserInputAnswer,
  ) {
    if (!activeConversationId || !message.turn_id || isStreaming) {
      return;
    }
    const conversationId = activeConversationId;
    setShouldAutoScroll(true);
    const controller = new AbortController();
    streamingStore.update(conversationId, (current) => ({
      ...current,
      error: "",
      isStreaming: true,
      streamingTurnId: message.turn_id ?? null,
      abortController: controller,
      messages: current.messages.map((item) =>
        item.id === message.id
          ? { ...item, isStreaming: true, status: "streaming", pending_interrupt: null }
          : item,
      ),
    }));
    try {
      await resumeInterruptedTurn(
        conversationId,
        message.turn_id,
        answer,
        (streamEvent) => dispatchStreamEvent(conversationId, streamEvent),
        { signal: controller.signal },
      );
      await refreshConversations();
    } catch (currentError) {
      if (!controller.signal.aborted) {
        setError(
          conversationId,
          currentError instanceof Error ? currentError.message : "继续执行失败",
        );
      }
    } finally {
      const latest = streamingStore.peek(conversationId);
      if (latest?.abortController === controller) {
        streamingStore.patch(conversationId, { abortController: null });
      }
      if (latest?.isStreaming) {
        streamingStore.patch(conversationId, { isStreaming: false });
      }
      scheduleConversationRefresh();
    }
  }

  function scheduleConversationRefresh() {
    // 后端自动命名 job 由 worker 每 2 秒拉取，给它 ~6 秒窗口刷新两次列表，
    // 让侧栏 title 从「新对话」过渡到 LLM 生成的短标题。
    const delays = [1500, 4500];
    delays.forEach((delay) => {
      window.setTimeout(() => {
        listConversations()
          .then((items) => setConversations(items))
          .catch(() => {
            // 列表刷新失败不打断主流程；下一次发送会再触发一次。
          });
      }, delay);
    });
  }

  async function handleOpenGraph(message: DraftAssistantMessage) {
    if (!activeConversationId) {
      return;
    }
    setIsGraphLoading(true);
    setSelectedGraph(null);
    setError(activeConversationId, "");
    try {
      if (!message.turn_id) {
        throw new Error("这条消息没有关联的 ChatTurn Graph。");
      }
      emitChatGraphOpenElfEvent(message.turn_id);
      setSelectedGraph(await getTurnGraph(activeConversationId, message.turn_id));
    } catch (currentError) {
      setError(
        activeConversationId,
        currentError instanceof Error ? currentError.message : "读取 graph 失败",
      );
    } finally {
      setIsGraphLoading(false);
    }
  }

  return (
    <section className="chat-shell">
      <ConversationList
        activeConversationId={activeConversationId}
        conversations={conversations}
        onDeleteConversation={handleDeleteConversation}
        onNewConversation={handleNewConversation}
        onSelectConversation={handleSelectConversation}
      />

      <section className="chat-main">
        <header className="chat-main-header">
          <div>
            <h2>{activeConversation?.title ?? "新对话"}</h2>
            <p>{activeConversation?.langgraph_thread_id ?? "准备连接 Memory Chat Graph"}</p>
          </div>
          {runningNodes.length > 0 ? <span>Graph: {runningNodes.join(", ")}</span> : null}
        </header>

        <MessageList
          endRef={messagesEndRef}
          listRef={messageListRef}
          messages={messages}
          onOpenGraph={handleOpenGraph}
          onScroll={updateAutoScrollIntent}
          onSubmitUserInput={handleUserInputAnswer}
          thoughts={thoughts}
        />

        {error ? <p className="chat-error">{error}</p> : null}

        <ChatComposer
          input={input}
          isSending={isStreaming}
          onInputChange={setInput}
          onSubmit={handleSubmit}
        />
      </section>

      {selectedGraph || isGraphLoading ? (
        <Suspense
          fallback={
            <div className="chat-debug-workspace chat-debug-workspace--loading">
              正在加载 Graph 调试面板...
            </div>
          }
        >
          <ChatGraphPanel
            graph={selectedGraph}
            isLoading={isGraphLoading}
            onClose={() => setSelectedGraph(null)}
          />
        </Suspense>
      ) : null}
    </section>
  );
}
