import type { ChatMessage, ChatStreamEvent, ChatTurnGraph, Conversation } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

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

export async function streamChat(
  conversationId: number,
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}/chat/stream`, {
    body: JSON.stringify({ message }),
    headers: {
      "Content-Type": "application/json",
    },
    method: "POST",
  });

  if (!response.ok || !response.body) {
    const errorText = await response.text();
    throw new Error(errorText || `Request failed with status ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

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
