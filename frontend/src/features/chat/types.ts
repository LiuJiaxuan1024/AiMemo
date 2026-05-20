export interface NoteSearchResult {
  note_id: number;
  note_title: string;
  chunk_id: number;
  chunk_index: number;
  content: string;
  content_hash: string;
  token_count: number;
  distance: number;
  score: number;
}

export interface Conversation {
  id: number;
  title: string;
  status: string;
  summary: string;
  summary_message_id: number | null;
  langgraph_thread_id: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number;
  conversation_id: number;
  role: "user" | "assistant" | "system";
  content: string;
  parent_id: number | null;
  checkpoint_id: string | null;
  status: string;
  token_count: number;
  turn_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageWithTurn extends ChatMessage {
  isStreaming?: boolean;
}

export type DraftAssistantMessage = ChatMessageWithTurn;

export interface ChatResponse {
  conversation_id: number;
  thread_id: string;
  checkpoint_id: string | null;
  needs_retrieval: boolean;
  needs_query_rewrite: boolean;
  retrieval_query: string;
  retrieval_grade: string;
  retrieval_grade_reason: string;
  retrieval_reason: string;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  retrieved_chunks: NoteSearchResult[];
}

export interface ChatTurnGraph {
  turn_id: number;
  conversation_id: number;
  user_message_id: number | null;
  assistant_message_id: number | null;
  thread_id: string;
  checkpoint_id: string | null;
  status: string;
  node_statuses: Record<string, string>;
  mermaid: string;
  context_layers: Array<{
    level: number;
    name: string;
    content: string;
    budget_tokens: number | null;
    used_tokens: number;
    note: string;
  }>;
  retrieved_chunks: NoteSearchResult[];
  debug_payload: {
    version?: number;
    events?: Record<string, number>;
    nodes?: Record<
      string,
      {
        status?: string;
        started_ms?: number;
        completed_ms?: number;
        duration_ms?: number;
        retrieval_debug?: Record<string, string | number | boolean>;
      }
    >;
    summary?: {
      first_answer_token_ms?: number | null;
      last_answer_token_ms?: number | null;
      answer_token_events?: number;
      answer_chars?: number;
      retrieved_count?: number;
    };
  };
  error: string;
}

export type ChatStreamEvent =
  | {
      event: "turn";
      data: {
        turn_id: number;
        user_message: ChatMessage;
        assistant_message: ChatMessage;
        node_statuses: Record<string, string>;
      };
    }
  | { event: "node"; data: { node: string; node_statuses: Record<string, string> } }
  | { event: "answer_delta"; data: { content: string } }
  | {
      event: "done";
      data: {
        turn_id: number;
        response: ChatResponse;
        bubbles?: Array<{ text: string; emoji: string }>;
      };
    }
  | { event: "error"; data: { turn_id?: number; message: string; node_statuses?: Record<string, string> } };
