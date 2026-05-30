import type {
  ChatActiveTurnList,
  ChatMessage,
  ChatStreamEvent,
  ChatTurnGraph,
  ChatTurnStateHistory,
  Conversation,
  SegmentFollowupRequest,
  UserInputAnswer,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function createConversation(title = "新对话"): Promise<Conversation> {
  return request<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function listConversations(): Promise<Conversation[]> {
  return request<Conversation[]>("/api/conversations");
}

export async function deleteConversation(conversationId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
    method: "DELETE",
  });
  if (!response.ok && response.status !== 204) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}

export async function deleteMessageBranch(conversationId: number, messageId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages/${messageId}`, {
    method: "DELETE",
  });
  if (!response.ok && response.status !== 204) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}

export function listMessages(conversationId: number): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/api/conversations/${conversationId}/messages`);
}

export function getMessageGraph(
  conversationId: number,
  messageId: number,
): Promise<ChatTurnGraph> {
  return request<ChatTurnGraph>(`/api/conversations/${conversationId}/messages/${messageId}/graph`);
}

export function getTurnGraph(
  conversationId: number,
  turnId: number,
): Promise<ChatTurnGraph> {
  return request<ChatTurnGraph>(`/api/conversations/${conversationId}/turns/${turnId}/graph`);
}

export function getTurnStateHistory(
  conversationId: number,
  turnId: number,
): Promise<ChatTurnStateHistory> {
  return request<ChatTurnStateHistory>(`/api/conversations/${conversationId}/turns/${turnId}/state-history`);
}

export async function streamChat(
  conversationId: number,
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
  options: { parentMessageId?: number | null; signal?: AbortSignal } = {},
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}/chat/stream`, {
    body: JSON.stringify({ message, parent_message_id: options.parentMessageId ?? null }),
    headers: {
      "Content-Type": "application/json",
    },
    method: "POST",
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    const errorText = await response.text();
    throw new Error(errorText || `Request failed with status ${response.status}`);
  }

  await consumeSseStream(response, onEvent);
}

export function serializeSegmentFollowupMessage(request: SegmentFollowupRequest): string {
  return JSON.stringify({
    type: "segment_followup",
    segment_id: request.segment_id,
    original_text: request.original_text,
    user_question: request.user_question,
    source_message_id: request.source_message_id,
    position: request.position ?? null,
  });
}

export function listActiveTurns(conversationId: number): Promise<ChatActiveTurnList> {
  return request<ChatActiveTurnList>(`/api/conversations/${conversationId}/active-turns`);
}

export function cancelTurn(conversationId: number, turnId: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/conversations/${conversationId}/turns/${turnId}/cancel`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function streamTurnResume(
  conversationId: number,
  turnId: number,
  onEvent: (event: ChatStreamEvent) => void,
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  // 重连入口：拿到 buffer 从 index 0 起的全部事件 + 后续 live 增量，直到 mark_done。
  // 后端的 SSE 在 buffer 终态后会自然 close，前端的 await 也随之 resolve。
  const response = await fetch(
    `${API_BASE_URL}/api/conversations/${conversationId}/turns/${turnId}/events/stream`,
    {
      method: "GET",
      signal: options.signal,
    },
  );
  if (!response.ok || !response.body) {
    const errorText = await response.text();
    throw new Error(errorText || `Request failed with status ${response.status}`);
  }
  await consumeSseStream(response, onEvent);
}

export async function resumeInterruptedTurn(
  conversationId: number,
  turnId: number,
  answer: UserInputAnswer,
  onEvent: (event: ChatStreamEvent) => void,
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/conversations/${conversationId}/turns/${turnId}/resume/stream`,
    {
      body: JSON.stringify(answer),
      headers: {
        "Content-Type": "application/json",
      },
      method: "POST",
      signal: options.signal,
    },
  );
  if (!response.ok || !response.body) {
    const errorText = await response.text();
    throw new Error(errorText || `Request failed with status ${response.status}`);
  }
  await consumeSseStream(response, onEvent);
}

async function consumeSseStream(
  response: Response,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const rawEvent of events) {
        const parsed = parseSseEvent(rawEvent);
        if (parsed) {
          onEvent(parsed);
        }
      }
    }

    if (buffer.trim()) {
      const parsed = parseSseEvent(buffer);
      if (parsed) {
        onEvent(parsed);
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 忽略 reader 已经被释放/关闭的情况，避免 finally 吞掉外层错误。
    }
  }
}

function parseSseEvent(rawEvent: string): ChatStreamEvent | null {
  const lines = rawEvent.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLine = lines.find((line) => line.startsWith("data:"));
  if (!eventLine || !dataLine) {
    return null;
  }

  const event = eventLine.slice("event:".length).trim();
  const data = JSON.parse(dataLine.slice("data:".length).trim());
  return { event, data } as ChatStreamEvent;
}
