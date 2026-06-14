import type { ChatTurnGraph, ChatTurnStateHistory } from "../chat/types";

export interface ConversationExportSnapshot {
  schema_version: 1;
  conversation: {
    id: number;
    title: string;
    summary: string;
    langgraph_thread_id: string;
    exported_at: string;
  };
  messages: ExportMessage[];
  graphs: Record<string, ExportGraphSnapshot>;
}

export interface ConversationMultiExportSnapshot {
  schema_version: 1;
  exported_at: string;
  conversations: ConversationExportSnapshot[];
}

export interface ExportMessage {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  content_html: string;
  created_at: string;
  status: string;
  token_count: number;
  attachments: ExportAttachment[];
  turn_id: number | null;
  graph_id: string | null;
  followup_threads: ExportSegmentFollowupThread[];
}

export interface ExportAttachment {
  id: number;
  kind: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  status: string;
  url: string;
  data_uri: string | null;
}

export interface ExportSegmentFollowupThread {
  segment_id: string;
  original_text: string;
  position: {
    start: number;
    end: number;
  } | null;
  status: "pending" | "answered" | "failed";
  turns: ExportSegmentFollowupTurn[];
}

export interface ExportSegmentFollowupTurn {
  question: string;
  answer: string;
  answer_html: string;
  assistant_message_id: number | null;
  timestamp: string;
  status: "pending" | "answered" | "failed";
  graph_id: string | null;
}

export interface ExportGraphSnapshot
  extends Omit<ChatTurnGraph, "retrieved_chunks" | "context_layers" | "debug_payload"> {
  context_layers: unknown[];
  retrieved_chunks: unknown[];
  debug_payload: Record<string, unknown>;
  state_history?: ChatTurnStateHistory | null;
}

export interface ChatViewAdapter {
  mode: "live" | "export";
  canMutate: boolean;
  loadGraph(messageId: number): Promise<ChatTurnGraph | ExportGraphSnapshot | null>;
  loadStateHistory?(turnId: number): Promise<unknown>;
  submitSegmentFollowup?(request: unknown): Promise<void>;
  deleteMessage?(messageId: number): Promise<void>;
}
