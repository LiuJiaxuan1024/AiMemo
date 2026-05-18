import { elfEvents } from "../elf/elfEventBus";

const CHAT_NODE_MESSAGES: Record<string, string> = {
  plan_retrieval: "我在判断要不要翻记忆。",
  retrieve_notes: "我在翻你的笔记。",
  build_l3_retrieved_memory: "我在筛选相关记忆。",
  generate_answer: "我在组织回答。",
};

/**
 * 把 Memory Chat Graph 的节点事件翻译成精灵能理解的轻提示。
 * 这里只处理结构化节点，不处理 token delta，避免精灵气泡频繁跳动。
 */
export function emitChatNodeElfEvent(nodeName: string) {
  const message = CHAT_NODE_MESSAGES[nodeName] ?? "我在处理这轮对话。";
  const isRetrievalNode = nodeName === "retrieve_notes" || nodeName === "build_l3_retrieved_memory";
  const isAnswerNode = nodeName === "generate_answer";

  elfEvents.emit({
    source: "chat",
    mood: isAnswerNode ? "talking" : "thinking",
    motion: isRetrievalNode ? "look" : "thinking",
    message,
    priority: 70,
    ttlMs: 2600,
    dedupeKey: `chat-node:${nodeName}`,
    metadata: {
      nodeName,
    },
  });
}

export function emitChatAnswerStartedElfEvent() {
  elfEvents.emit({
    source: "chat",
    mood: "talking",
    motion: "working",
    message: "我开始回答了。",
    priority: 70,
    ttlMs: 1800,
    dedupeKey: "chat-answer-started",
  });
}

export function emitChatDoneElfEvent(turnId?: number) {
  elfEvents.emit({
    source: "chat",
    mood: "success",
    motion: "success",
    message: "回答好了。",
    priority: 50,
    ttlMs: 3000,
    dedupeKey: turnId ? `chat-done:${turnId}` : undefined,
    metadata: {
      turnId,
    },
  });
}

export function emitChatErrorElfEvent(message: string, turnId?: number) {
  elfEvents.emit({
    source: "chat",
    mood: "error",
    motion: "error",
    message: "这轮对话遇到问题了，点开看看错误。",
    priority: 100,
    ttlMs: 6000,
    dedupeKey: turnId ? `chat-error:${turnId}` : `chat-error:${message}`,
    metadata: {
      turnId,
    },
  });
}

export function emitChatGraphOpenElfEvent(turnId?: number) {
  elfEvents.emit({
    source: "graph",
    mood: "talking",
    motion: "look",
    message: "这是我刚才的思考流程。",
    priority: 80,
    ttlMs: 2200,
    dedupeKey: turnId ? `chat-graph-open:${turnId}` : undefined,
    metadata: {
      turnId,
    },
  });
}
