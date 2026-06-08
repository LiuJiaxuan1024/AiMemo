import { FormEvent, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  cancelTurn,
  createConversation,
  deleteConversation,
  deleteMessageBranch,
  executeCommand,
  getTurnGraph,
  listActiveTurns,
  listCommands,
  listConversationKnowledgeMounts,
  listConversations,
  listMessages,
  replaceConversationKnowledgeMounts,
  resumeInterruptedTurn,
  serializeSegmentFollowupMessage,
  streamChat,
  streamTurnResume,
  uploadConversationAttachment,
} from "./chatApi";
import { ChatComposer } from "./ChatComposer";
import { ConversationList } from "./ConversationList";
import { KnowledgeMountControl } from "./KnowledgeMountControl";
import { MessageList, SegmentFollowupPanel } from "./MessageList";
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
  ChatAttachment,
  ChatTurnGraph,
  CommandSchema,
  ConversationKnowledgeMount,
  Conversation,
  DraftAssistantMessage,
  PendingChatAttachment,
  SegmentFollowupRequest,
  UserInputAnswer,
} from "./types";

const ChatGraphPanel = lazy(() =>
  import("./ChatGraphPanel").then((module) => ({ default: module.ChatGraphPanel })),
);

export function ChatWindow() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [input, setInput] = useState("");
  const [composerAttachments, setComposerAttachments] = useState<PendingChatAttachment[]>([]);
  const [commands, setCommands] = useState<CommandSchema[]>([]);
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);
  const [selectedGraph, setSelectedGraph] = useState<ChatTurnGraph | null>(null);
  const [knowledgeMountsByConversation, setKnowledgeMountsByConversation] = useState<Record<number, ConversationKnowledgeMount[]>>({});
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  const [isGraphClosing, setIsGraphClosing] = useState(false);
  const [activeFollowupSourceId, setActiveFollowupSourceId] = useState<number | null>(null);
  const [activeFollowupSegmentId, setActiveFollowupSegmentId] = useState<string | null>(null);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const selectedGraphRef = useRef<ChatTurnGraph | null>(null);
  const graphCloseTimerRef = useRef<number | null>(null);
  const composerAttachmentsRef = useRef<PendingChatAttachment[]>([]);

  const activeConversationId = activeConversation?.id;
  const activeKnowledgeMounts = activeConversationId ? knowledgeMountsByConversation[activeConversationId] ?? [] : [];
  const view = useConversationView(activeConversationId);
  const messages = view?.messages ?? [];
  const nodeStatuses = view?.nodeStatuses ?? {};
  const thoughts = view?.thoughts ?? [];
  const isStreaming = view?.isStreaming ?? false;
  const error = view?.error ?? "";
  const activeFollowupSource =
    activeFollowupSourceId == null
      ? null
      : messages.find((message) => message.id === activeFollowupSourceId) ?? null;

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

  useEffect(() => {
    if (!activeConversationId) {
      return;
    }
    refreshKnowledgeMounts(activeConversationId).catch((currentError: unknown) => {
      setError(
        activeConversationId,
        currentError instanceof Error ? currentError.message : "读取知库挂载失败",
      );
    });
  }, [activeConversationId, setError]);

  useEffect(() => {
    if (!activeConversationId) {
      setCommands([]);
      return;
    }
    listCommands(activeConversationId)
      .then((response) => setCommands(response.items))
      .catch(() => setCommands([]));
  }, [activeConversationId]);

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

  useEffect(
    () => () => {
      if (graphCloseTimerRef.current != null) {
        window.clearTimeout(graphCloseTimerRef.current);
      }
      revokePendingAttachmentUrls(composerAttachmentsRef.current);
    },
    [],
  );

  useEffect(() => {
    composerAttachmentsRef.current = composerAttachments;
  }, [composerAttachments]);

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
      await refreshKnowledgeMounts(conversation.id);
      await attachActiveTurns(conversation.id);
      return;
    }
    streamingStore.patch(conversation.id, { loaded: true });
    const messagesList = hydrateSegmentFollowups(await listMessages(conversation.id));
    streamingStore.update(conversation.id, (current) => ({
      ...current,
      messages: messagesList as DraftAssistantMessage[],
      error: "",
    }));
    // 一并探测是否有正在跑的 turn；有就接上 SSE 重放 + 增量。
    await refreshKnowledgeMounts(conversation.id);
    await attachActiveTurns(conversation.id);
  }

  async function refreshKnowledgeMounts(conversationId: number): Promise<void> {
    const mounts = await listConversationKnowledgeMounts(conversationId);
    setKnowledgeMountsByConversation((current) => ({
      ...current,
      [conversationId]: mounts,
    }));
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
    await refreshKnowledgeMounts(conversation.id);
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
    if (!activeConversationId || isStreaming || isUploadingAttachments) {
      return;
    }
    const trimmedInput = input.trim();
    if (!trimmedInput && composerAttachments.length === 0) {
      return;
    }
    if (trimmedInput.startsWith("/")) {
      if (composerAttachments.length > 0) {
        setError(activeConversationId, "斜杠指令暂不支持附件，请先移除附件。");
        return;
      }
      await submitSlashCommand(activeConversationId, trimmedInput);
      return;
    }

    await submitChatMessage(activeConversationId, trimmedInput, {
      pendingAttachments: composerAttachments,
    });
  }

  async function submitSlashCommand(conversationId: number, command: string): Promise<void> {
    setInput("");
    setShouldAutoScroll(true);
    streamingStore.patch(conversationId, { error: "" });
    const parentMessageId = messages.length > 0 ? messages[messages.length - 1].id : null;
    try {
      const response = await executeCommand(conversationId, command, parentMessageId);
      streamingStore.update(conversationId, (current) => ({
        ...current,
        messages: hydrateSegmentFollowups([
          ...current.messages,
          response.user_message,
          response.assistant_message,
        ] as DraftAssistantMessage[]),
        error: "",
      }));
      await refreshConversations();
      await refreshKnowledgeMounts(conversationId);
    } catch (currentError) {
      setError(conversationId, currentError instanceof Error ? currentError.message : "执行指令失败");
    }
  }

  async function handleExecuteCommandSuggestion(command: string): Promise<void> {
    if (!activeConversationId || isStreaming) {
      return;
    }
    await submitSlashCommand(activeConversationId, command);
  }

  async function handleStopGeneration() {
    if (!activeConversationId) {
      return;
    }
    const slot = streamingStore.peek(activeConversationId);
    const turnId = slot?.streamingTurnId;
    if (slot?.abortController) {
      try {
        slot.abortController.abort();
      } catch {
        // 已经关闭的 fetch 不需要再处理。
      }
    }
    streamingStore.update(activeConversationId, (current) => ({
      ...current,
      isStreaming: false,
      streamingTurnId: null,
      thoughts: [],
      nodeStatuses: {},
      abortController: null,
      messages: current.messages.map((message) =>
        message.isStreaming
          ? { ...message, isStreaming: false, status: "failed" }
          : message,
      ),
      error: "",
    }));
    if (!turnId) {
      return;
    }
    try {
      await cancelTurn(activeConversationId, turnId);
      await refreshActiveMessages(activeConversationId);
    } catch (currentError) {
      setError(
        activeConversationId,
        currentError instanceof Error ? currentError.message : "中断生成失败",
      );
    }
  }

  async function handleDeleteMessage(message: DraftAssistantMessage) {
    if (!activeConversationId || isStreaming || message.isStreaming) {
      return;
    }
    try {
      await deleteMessageBranch(activeConversationId, message.id);
      await refreshActiveMessages(activeConversationId);
      if (activeFollowupSourceId === message.id) {
        setActiveFollowupSourceId(null);
        setActiveFollowupSegmentId(null);
      }
      if (
        selectedGraph?.assistant_message_id === message.id ||
        selectedGraph?.user_message_id === message.id
      ) {
        setSelectedGraph(null);
      }
      await refreshConversations();
    } catch (currentError) {
      setError(
        activeConversationId,
        currentError instanceof Error ? currentError.message : "删除消息失败",
      );
    }
  }

  async function refreshActiveMessages(conversationId: number): Promise<void> {
    const messagesList = hydrateSegmentFollowups(await listMessages(conversationId));
    streamingStore.update(conversationId, (current) => ({
      ...current,
      messages: messagesList as DraftAssistantMessage[],
      isStreaming: false,
      streamingTurnId: null,
      abortController: null,
      pendingOptimisticIds: null,
      thoughts: [],
      nodeStatuses: {},
    }));
  }

  async function submitChatMessage(
    conversationId: number,
    content: string,
    options: {
      displayContent?: string;
      hidden?: boolean;
      onDone?: (assistant: DraftAssistantMessage | null) => void;
      parentMessageId?: number | null;
      pendingAttachments?: PendingChatAttachment[];
    } = {},
  ) {
    if (!options.hidden) {
      setShouldAutoScroll(true);
    }
    const pendingAttachments = options.pendingAttachments ?? [];
    let uploadedAttachmentIds: number[] = [];
    let optimisticAttachments = pendingAttachments.map((attachment) => pendingToOptimisticAttachment(conversationId, attachment));
    if (pendingAttachments.length > 0) {
      setIsUploadingAttachments(true);
      try {
        const uploaded = await Promise.all(
          pendingAttachments.map((attachment) => uploadConversationAttachment(conversationId, attachment.file)),
        );
        uploadedAttachmentIds = uploaded.map((attachment) => attachment.id);
        optimisticAttachments = uploaded;
      } catch (currentError) {
        setError(
          conversationId,
          currentError instanceof Error ? currentError.message : "上传附件失败",
        );
        return;
      } finally {
        setIsUploadingAttachments(false);
      }
    }
    const optimisticUserId = -Date.now();
    const optimisticAssistantId = optimisticUserId - 1;
    const optimisticUser: DraftAssistantMessage = {
      id: optimisticUserId,
      conversation_id: conversationId,
      role: "user",
      content: options.displayContent ?? content,
      parent_id: options.parentMessageId ?? (messages.length > 0 ? messages[messages.length - 1].id : null),
      checkpoint_id: null,
      status: "streaming",
      token_count: 0,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      ui_hidden: options.hidden,
      attachments: optimisticAttachments,
    };
    const optimisticAssistant: DraftAssistantMessage = {
      ...optimisticUser,
      id: optimisticAssistantId,
      role: "assistant",
      content: "",
      parent_id: optimisticUserId,
      isStreaming: true,
      ui_hidden: options.hidden,
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
    if (!options.hidden && pendingAttachments.length > 0) {
      setComposerAttachments([]);
      revokePendingAttachmentUrls(pendingAttachments);
    }

    try {
      await streamChat(
        conversationId,
        content,
        (streamEvent) => dispatchStreamEvent(conversationId, streamEvent),
        {
          attachmentIds: uploadedAttachmentIds,
          parentMessageId: options.parentMessageId,
          signal: controller.signal,
        },
      );
      if (options.onDone) {
        const latest = streamingStore.peek(conversationId);
        const latestAssistant = [...(latest?.messages ?? [])]
          .reverse()
          .find((message) => message.role === "assistant" && message.ui_hidden === options.hidden) ?? null;
        options.onDone(latestAssistant);
      }
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

  function handleAddFiles(files: File[]) {
    if (files.length === 0) {
      return;
    }
    setComposerAttachments((current) => {
      const next = [...current];
      for (const file of files) {
        const kind = file.type.startsWith("image/") ? "image" : "file";
        next.push({
          localId: `local-${Date.now()}-${Math.random().toString(36).slice(2)}`,
          file,
          kind,
          name: file.name || (kind === "image" ? "pasted-image" : "attachment"),
          mimeType: file.type || "application/octet-stream",
          sizeBytes: file.size,
          previewUrl: kind === "image" ? URL.createObjectURL(file) : undefined,
        });
      }
      return next.slice(-20);
    });
  }

  function handleRemoveAttachment(localId: string) {
    setComposerAttachments((current) => {
      const removed = current.find((attachment) => attachment.localId === localId);
      if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return current.filter((attachment) => attachment.localId !== localId);
    });
  }

  async function handleSegmentFollowup(request: SegmentFollowupRequest) {
    if (!activeConversationId || isStreaming) {
      return;
    }
    setActiveFollowupSourceId(request.source_message_id);
    const followupId = `f-${Date.now().toString(36)}`;
    const segmentId = request.segment_id ?? createSegmentId(request.original_text);
    setActiveFollowupSegmentId(segmentId);
    const timestamp = new Date().toISOString();
    const displayContent = `针对片段「${compactText(request.original_text, 48)}」追问：${request.user_question}`;

    streamingStore.updateMessages(activeConversationId, (currentMessages) =>
      currentMessages.map((message) =>
        message.id === request.source_message_id
          ? upsertFollowupThread(message, {
              segment_id: segmentId,
              original_text: request.original_text,
              position: request.position ?? null,
              followups: [
                {
                  followup_id: followupId,
                  user_question: request.user_question,
                  status: "pending",
                  timestamp,
                },
              ],
            })
          : message,
      ),
    );

    await submitChatMessage(
      activeConversationId,
      serializeSegmentFollowupMessage({
        ...request,
        segment_id: segmentId,
      }),
      {
        displayContent,
        hidden: true,
        parentMessageId: request.source_message_id,
        onDone: (assistant) => {
          streamingStore.updateMessages(activeConversationId, (currentMessages) =>
            currentMessages.map((message) =>
              message.id === request.source_message_id
                ? completeFollowup(message, segmentId, followupId, assistant?.content ?? "")
                : message,
            ),
          );
        },
      },
    );
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
    if (graphCloseTimerRef.current != null) {
      window.clearTimeout(graphCloseTimerRef.current);
      graphCloseTimerRef.current = null;
    }
    setIsGraphClosing(false);
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

  function handleCloseGraph() {
    setIsGraphClosing(true);
    setIsGraphLoading(false);
    if (graphCloseTimerRef.current != null) {
      window.clearTimeout(graphCloseTimerRef.current);
    }
    graphCloseTimerRef.current = window.setTimeout(() => {
      setSelectedGraph(null);
      setIsGraphClosing(false);
      graphCloseTimerRef.current = null;
    }, 220);
  }

  function handleOpenFollowups(message: DraftAssistantMessage, segmentId?: string | null) {
    setActiveFollowupSourceId(message.id);
    setActiveFollowupSegmentId(segmentId ?? null);
  }

  async function handleSaveKnowledgeMounts(spaceIds: number[]) {
    if (!activeConversationId) {
      return;
    }
    const mounts = await replaceConversationKnowledgeMounts(activeConversationId, spaceIds);
    setKnowledgeMountsByConversation((current) => ({
      ...current,
      [activeConversationId]: mounts,
    }));
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
          <div className="chat-main-header__tools">
            <KnowledgeMountControl
              conversationId={activeConversationId}
              disabled={isStreaming}
              mounts={activeKnowledgeMounts}
              onSave={handleSaveKnowledgeMounts}
            />
            {runningNodes.length > 0 ? <span>Graph: {runningNodes.join(", ")}</span> : null}
          </div>
        </header>

        <div className={`chat-dialogue-zone ${activeFollowupSource ? "chat-dialogue-zone--with-followups" : ""}`}>
          <MessageList
            activeFollowupSegmentId={activeFollowupSegmentId}
            activeFollowupSourceId={activeFollowupSourceId}
            endRef={messagesEndRef}
            listRef={messageListRef}
            messages={messages}
            onExecuteCommandSuggestion={handleExecuteCommandSuggestion}
            onOpenFollowups={handleOpenFollowups}
            onOpenGraph={handleOpenGraph}
            onDeleteMessage={handleDeleteMessage}
            onScroll={updateAutoScrollIntent}
            onSegmentFollowup={handleSegmentFollowup}
            onStopGeneration={handleStopGeneration}
            onSubmitUserInput={handleUserInputAnswer}
            isStreaming={isStreaming}
            thoughts={thoughts}
          />
          {activeFollowupSource ? (
            <SegmentFollowupPanel
              activeSegmentId={activeFollowupSegmentId}
              messages={messages}
              onClose={() => {
                setActiveFollowupSourceId(null);
                setActiveFollowupSegmentId(null);
              }}
              onOpenGraph={handleOpenGraph}
              onDeleteMessage={handleDeleteMessage}
              onOpenSegment={setActiveFollowupSegmentId}
              onSegmentFollowup={handleSegmentFollowup}
              onStopGeneration={handleStopGeneration}
              sourceMessage={activeFollowupSource}
              thoughts={thoughts}
            />
          ) : null}
        </div>

        {error ? <p className="chat-error">{error}</p> : null}

        <ChatComposer
          attachments={composerAttachments}
          commands={commands}
          input={input}
          isSending={isStreaming}
          isUploading={isUploadingAttachments}
          onAddFiles={handleAddFiles}
          onInputChange={setInput}
          onRemoveAttachment={handleRemoveAttachment}
          onStop={handleStopGeneration}
          onSubmit={handleSubmit}
        />
      </section>

      {selectedGraph || isGraphLoading || isGraphClosing ? (
        <Suspense
          fallback={
            <div className="chat-debug-workspace chat-debug-workspace--loading">
              正在加载 Graph 调试面板...
            </div>
          }
        >
          <ChatGraphPanel
            graph={selectedGraph}
            isClosing={isGraphClosing}
            isLoading={isGraphLoading}
            onClose={handleCloseGraph}
          />
        </Suspense>
      ) : null}
    </section>
  );
}

function upsertFollowupThread(
  message: DraftAssistantMessage,
  thread: NonNullable<DraftAssistantMessage["followupThreads"]>[number],
): DraftAssistantMessage {
  const threads = message.followupThreads ?? [];
  const index = threads.findIndex((item) => item.segment_id === thread.segment_id);
  if (index < 0) {
    return { ...message, followupThreads: [...threads, thread] };
  }
  return {
    ...message,
    followupThreads: threads.map((item, itemIndex) =>
      itemIndex === index
        ? {
            ...item,
            followups: [...item.followups, ...thread.followups],
          }
        : item,
    ),
  };
}

function completeFollowup(
  message: DraftAssistantMessage,
  segmentId: string,
  followupId: string,
  answer: string,
): DraftAssistantMessage {
  const threads = message.followupThreads ?? [];
  return {
    ...message,
    followupThreads: threads.map((thread) =>
      thread.segment_id === segmentId
        ? {
            ...thread,
            followups: thread.followups.map((followup) =>
              followup.followup_id === followupId
                ? {
                    ...followup,
                    assistant_answer: answer,
                    status: answer.trim() ? "answered" : "failed",
                  }
                : followup,
            ),
          }
        : thread,
    ),
  };
}

function createSegmentId(text: string): string {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `seg-${(hash >>> 0).toString(16)}`;
}

function compactText(text: string, maxLength: number): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized;
}

function pendingToOptimisticAttachment(
  conversationId: number,
  attachment: PendingChatAttachment,
): ChatAttachment {
  return {
    id: 0,
    conversation_id: conversationId,
    message_id: null,
    kind: attachment.kind,
    original_name: attachment.name,
    mime_type: attachment.mimeType,
    size_bytes: attachment.sizeBytes,
    width: null,
    height: null,
    sha256: "",
    status: "pending",
    retention_policy: "chat_only",
    url: attachment.previewUrl ?? "",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function revokePendingAttachmentUrls(attachments: PendingChatAttachment[]) {
  for (const attachment of attachments) {
    if (attachment.previewUrl) {
      URL.revokeObjectURL(attachment.previewUrl);
    }
  }
}

function hydrateSegmentFollowups(messages: DraftAssistantMessage[]): DraftAssistantMessage[] {
  let nextMessages = messages.map((message) => ({ ...message }));
  for (const message of nextMessages) {
    if (message.role !== "user") {
      continue;
    }
    const payload = parseSegmentFollowupPayload(message.content);
    if (!payload) {
      continue;
    }
    const segmentId = payload.segment_id ?? createSegmentId(payload.original_text);
    const assistantAnswer = nextMessages.find(
      (candidate) => candidate.role === "assistant" && candidate.parent_id === message.id,
    );
    nextMessages = nextMessages.map((candidate) => {
      if (candidate.id === message.id || candidate.id === assistantAnswer?.id) {
        return { ...candidate, ui_hidden: true };
      }
      if (candidate.id !== payload.source_message_id) {
        return candidate;
      }
      return upsertFollowupThread(candidate, {
        segment_id: segmentId,
        original_text: payload.original_text,
        position: payload.position ?? null,
        followups: [
          {
            followup_id: `f-${message.id}`,
            user_question: payload.user_question,
            assistant_answer: assistantAnswer?.content,
            status: assistantAnswer?.content ? "answered" : "failed",
            timestamp: message.created_at,
          },
        ],
      });
    });
  }
  return nextMessages;
}

function parseSegmentFollowupPayload(content: string): SegmentFollowupRequest | null {
  try {
    const payload = JSON.parse(content) as Partial<SegmentFollowupRequest> & { type?: string };
    if (
      payload.type !== "segment_followup" ||
      typeof payload.source_message_id !== "number" ||
      typeof payload.original_text !== "string" ||
      typeof payload.user_question !== "string"
    ) {
      return null;
    }
    return {
      source_message_id: payload.source_message_id,
      segment_id: typeof payload.segment_id === "string" ? payload.segment_id : null,
      original_text: payload.original_text,
      user_question: payload.user_question,
      position: payload.position ?? null,
    };
  } catch {
    return null;
  }
}
