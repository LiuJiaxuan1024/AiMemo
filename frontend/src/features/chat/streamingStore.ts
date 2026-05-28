import { useSyncExternalStore } from "react";

import type {
  ChatStreamEvent,
  ChatThought,
  DraftAssistantMessage,
  MessageSegment,
  ToolInvocation,
} from "./types";

/**
 * 单个会话当前要显示的全部派生 UI 状态。
 *
 * 关键点：messages / nodeStatuses / thoughts 是按 conversationId 隔离的，
 * 这样 conv A 还在 streaming 时切到 conv B 不会把 A 的状态当 B 的渲染。
 */
export interface ConversationView {
  conversationId: number;
  messages: DraftAssistantMessage[];
  nodeStatuses: Record<string, string>;
  thoughts: ChatThought[];
  isStreaming: boolean;
  streamingTurnId: number | null;
  // 同一轮里 optimistic user / assistant 草稿的负数 id，real id 抵达后用它做替换。
  pendingOptimisticIds: { userId: number; assistantId: number } | null;
  // 这一轮发生过 answer_delta 的本地标记，避免对每个 token 都触发 elf "开始回答" 事件。
  hasEmittedAnswerStarted: boolean;
  error: string;
  // listMessages 是否已经拉取过。第一次进入会话时为 false，触发拉取 + active-turns 检测。
  loaded: boolean;
  // 当前正在跑 / 重连的 fetch AbortController；用于会话被删除时主动断开。
  abortController: AbortController | null;
}

function createEmptyView(conversationId: number): ConversationView {
  return {
    conversationId,
    messages: [],
    nodeStatuses: {},
    thoughts: [],
    isStreaming: false,
    streamingTurnId: null,
    pendingOptimisticIds: null,
    hasEmittedAnswerStarted: false,
    error: "",
    loaded: false,
    abortController: null,
  };
}

type Listener = () => void;

class StreamingStore {
  private slots = new Map<number, ConversationView>();
  private listeners = new Set<Listener>();
  private version = 0;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getVersion = (): number => this.version;

  get(conversationId: number): ConversationView {
    let slot = this.slots.get(conversationId);
    if (!slot) {
      slot = createEmptyView(conversationId);
      this.slots.set(conversationId, slot);
    }
    return slot;
  }

  peek(conversationId: number): ConversationView | undefined {
    return this.slots.get(conversationId);
  }

  private notify(): void {
    this.version += 1;
    this.listeners.forEach((listener) => listener());
  }

  patch(conversationId: number, patch: Partial<ConversationView>): void {
    const current = this.get(conversationId);
    this.slots.set(conversationId, { ...current, ...patch });
    this.notify();
  }

  update(conversationId: number, updater: (current: ConversationView) => ConversationView): void {
    const current = this.get(conversationId);
    const next = updater(current);
    if (next === current) {
      return;
    }
    this.slots.set(conversationId, next);
    this.notify();
  }

  updateMessages(
    conversationId: number,
    updater: (messages: DraftAssistantMessage[]) => DraftAssistantMessage[],
  ): void {
    this.update(conversationId, (current) => ({ ...current, messages: updater(current.messages) }));
  }

  remove(conversationId: number): void {
    const slot = this.slots.get(conversationId);
    if (slot?.abortController) {
      try {
        slot.abortController.abort();
      } catch {
        // 已经 abort 或者还没启动都无所谓；目的只是让在跑的 fetch 释放掉。
      }
    }
    if (this.slots.delete(conversationId)) {
      this.notify();
    }
  }

  resetForTests(): void {
    this.slots.clear();
    this.notify();
  }
}

export const streamingStore = new StreamingStore();

/**
 * 订阅 store 全局版本号；任意会话的状态变更都会让所有挂载这个 hook 的组件重新渲染。
 * 返回值是指定会话的当前视图。规模小，按 conversationId 局部订阅暂不必要。
 */
export function useConversationView(conversationId: number | undefined): ConversationView | null {
  useSyncExternalStore(streamingStore.subscribe, streamingStore.getVersion);
  if (!conversationId) {
    return null;
  }
  return streamingStore.get(conversationId);
}

/**
 * 把一个 SSE 事件应用到指定会话的视图。stream callback 和 resume callback 共用同一份处理逻辑，
 * 避免逻辑发散；同时也保证切走再回来时事件 replay 后状态收敛。
 */
export function applyChatStreamEvent(conversationId: number, event: ChatStreamEvent): void {
  if (event.event === "turn") {
    const { turn_id, user_message, assistant_message, node_statuses } = event.data;
    streamingStore.update(conversationId, (current) => {
      const pending = current.pendingOptimisticIds;
      const messages = current.messages
        .filter((message) => {
          if (!pending) {
            return true;
          }
          return message.id !== pending.userId && message.id !== pending.assistantId;
        })
        .concat([
          { ...user_message, conversation_id: conversationId },
          { ...assistant_message, conversation_id: conversationId, turn_id, isStreaming: true },
        ]);
      return {
        ...current,
        messages,
        nodeStatuses: node_statuses,
        streamingTurnId: turn_id,
        pendingOptimisticIds: null,
      };
    });
    return;
  }
  if (event.event === "node") {
    streamingStore.patch(conversationId, { nodeStatuses: event.data.node_statuses });
    return;
  }
  if (event.event === "resume") {
    streamingStore.update(conversationId, (current) => ({
      ...current,
      nodeStatuses: event.data.node_statuses,
      isStreaming: true,
      streamingTurnId: event.data.turn_id,
      messages: current.messages.map((message) =>
        message.turn_id === event.data.turn_id || message.isStreaming
          ? { ...message, isStreaming: true, status: "streaming", pending_interrupt: null }
          : message,
      ),
    }));
    return;
  }
  if (event.event === "answer_delta") {
    const stepIndex = typeof event.data.step_index === "number" ? event.data.step_index : 0;
    streamingStore.update(conversationId, (current) => {
      const messages = current.messages.map((message) =>
        message.isStreaming
          ? {
              ...message,
              content: message.content + event.data.content,
              segments: upsertSegmentText(message.segments, stepIndex, event.data.content),
            }
          : message,
      );
      return { ...current, messages };
    });
    return;
  }
  if (event.event === "tool_invocation") {
    const invocation: ToolInvocation = {
      step_index: event.data.step_index,
      tool_call_id: event.data.tool_call_id,
      tool_name: event.data.tool_name,
      arguments: event.data.arguments,
      ok: event.data.ok,
      blocked: event.data.blocked,
      error_code: event.data.error_code,
      message: event.data.message,
      result_summary: event.data.result_summary,
      running: Boolean(event.data.running),
    };
    streamingStore.update(conversationId, (current) => {
      const messages = current.messages.map((message) =>
        message.isStreaming
          ? {
              ...message,
              segments: upsertSegmentTool(message.segments, invocation),
            }
          : message,
      );
      return { ...current, messages };
    });
    return;
  }
  if (event.event === "thought_snapshot") {
    streamingStore.update(conversationId, (current) => {
      const thoughts = event.data.thoughts;
      const messages = current.messages.map((message) =>
        message.isStreaming ? { ...message, thoughts } : message,
      );
      return { ...current, thoughts, messages };
    });
    return;
  }
  if (event.event === "interrupt") {
    streamingStore.update(conversationId, (current) => {
      const messages = current.messages.map((message) =>
        (message.isStreaming && message.role === "assistant") ||
        (message.role === "assistant" && message.turn_id === event.data.turn_id)
          ? {
              ...message,
              isStreaming: false,
              status: "interrupted",
              pending_interrupt: event.data.request,
            }
          : message,
      );
      return {
        ...current,
        messages,
        nodeStatuses: event.data.node_statuses,
        isStreaming: false,
        streamingTurnId: event.data.turn_id,
      };
    });
    return;
  }
  if (event.event === "done") {
    const { user_message, assistant_message } = event.data.response;
    streamingStore.update(conversationId, (current) => {
      const thoughts = current.thoughts;
      // 保留 streaming 期间累积的 segments，让 done 之后气泡仍能按步序展示工具卡片。
      const streamingAssistant = current.messages.find(
        (message) => message.isStreaming && message.role === "assistant",
      );
      const segments = streamingAssistant?.segments;
      const messages = current.messages
        .filter(
          (message) =>
            message.id !== user_message.id &&
            message.id !== assistant_message.id &&
            !(message.isStreaming && message.role === "assistant"),
        )
        .concat([
          { ...user_message, conversation_id: conversationId },
          {
            ...assistant_message,
            conversation_id: conversationId,
            thoughts,
            segments,
            pending_interrupt: null,
          },
        ]);
      return {
        ...current,
        messages,
        thoughts: [],
        nodeStatuses: {},
        isStreaming: false,
        streamingTurnId: null,
      };
    });
    return;
  }
  if (event.event === "error") {
    streamingStore.update(conversationId, (current) => {
      const messages = current.messages.map((message) =>
        message.isStreaming && message.role === "assistant"
          ? {
              ...message,
              isStreaming: false,
              status: "failed",
            }
          : message,
      );
      return {
        ...current,
        messages,
        error: event.data.message,
        isStreaming: false,
        streamingTurnId: null,
        thoughts: [],
        pendingOptimisticIds: null,
        abortController: null,
      };
    });
    return;
  }
  if (event.event === "turn_unavailable") {
    // buffer 已经过期：把 streaming 标志关掉，主消息由 listMessages 拿到。
    streamingStore.update(conversationId, (current) => ({
      ...current,
      messages: current.messages.map((message) =>
        message.isStreaming && message.role === "assistant"
          ? { ...message, isStreaming: false }
          : message,
      ),
      isStreaming: false,
      streamingTurnId: null,
      thoughts: [],
      pendingOptimisticIds: null,
      abortController: null,
    }));
    return;
  }
}

function emptySegment(stepIndex: number): MessageSegment {
  return { step_index: stepIndex, text: "", tools: [] };
}

/**
 * 按 step_index 把一段 answer_delta 文本累加到对应 segment 上。
 * 若该 step 还不存在，则按升序插入；保证 MessageList 渲染时 segment 顺序与时间一致。
 */
function upsertSegmentText(
  segments: MessageSegment[] | undefined,
  stepIndex: number,
  content: string,
): MessageSegment[] {
  const list = segments ? [...segments] : [];
  const index = list.findIndex((segment) => segment.step_index === stepIndex);
  if (index >= 0) {
    list[index] = { ...list[index], text: list[index].text + content };
    return list;
  }
  list.push({ ...emptySegment(stepIndex), text: content });
  list.sort((a, b) => a.step_index - b.step_index);
  return list;
}

/**
 * 把一条工具调用追加到对应 step 的 segment 上；同一个 tool_call_id 再次出现时覆盖，
 * 让 running → completed 的状态切换也能反映在卡片上。
 */
function upsertSegmentTool(
  segments: MessageSegment[] | undefined,
  invocation: ToolInvocation,
): MessageSegment[] {
  const list = segments ? [...segments] : [];
  const stepIndex = invocation.step_index;
  let index = list.findIndex((segment) => segment.step_index === stepIndex);
  if (index < 0) {
    list.push(emptySegment(stepIndex));
    list.sort((a, b) => a.step_index - b.step_index);
    index = list.findIndex((segment) => segment.step_index === stepIndex);
  }
  const target = list[index];
  const existingIndex = target.tools.findIndex(
    (tool) => tool.tool_call_id === invocation.tool_call_id,
  );
  const tools =
    existingIndex >= 0
      ? target.tools.map((tool, i) => (i === existingIndex ? invocation : tool))
      : [...target.tools, invocation];
  list[index] = { ...target, tools };
  return list;
}

// 给 MessageList 用：按 step_index 把 thoughts 分组，使得 thinking → tool → text 串行展示。
export function groupThoughtsByStep(thoughts: ChatThought[] | undefined): Map<number, ChatThought[]> {
  const groups = new Map<number, ChatThought[]>();
  if (!thoughts) {
    return groups;
  }
  for (const thought of thoughts) {
    const step = typeof thought.step_index === "number" ? thought.step_index : 0;
    const list = groups.get(step) ?? [];
    const existingIndex = list.findIndex((item) => item.id === thought.id);
    if (existingIndex >= 0) {
      list[existingIndex] = thought;
    } else {
      list.push(thought);
    }
    groups.set(step, list);
  }
  return groups;
}
