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

export interface ConversationKnowledgeMount {
  id: number;
  conversation_id: number;
  space_id: number;
  space_name: string;
  space_icon: string | null;
  ready_document_count: number;
  document_count: number;
  created_by: string;
  scope_note: string | null;
  created_at: string;
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
  attachments?: ChatAttachment[];
  turn_id?: number | null;
  pending_interrupt?: UserInputRequest | null;
  created_at: string;
  updated_at: string;
}

export interface ChatAttachment {
  id: number;
  conversation_id: number;
  message_id: number | null;
  kind: "image" | "file" | string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  sha256: string;
  status: string;
  retention_policy: string;
  url: string;
  created_at: string;
  updated_at: string;
}

export interface PendingChatAttachment {
  localId: string;
  file: File;
  kind: "image" | "file";
  name: string;
  mimeType: string;
  sizeBytes: number;
  previewUrl?: string;
}

export interface ChatMessageWithTurn extends ChatMessage {
  isStreaming?: boolean;
  thoughts?: ChatThought[];
  segments?: MessageSegment[];
  followupThreads?: SegmentFollowupThread[];
  ui_hidden?: boolean;
}

export type DraftAssistantMessage = ChatMessageWithTurn;

export interface ChatThought {
  id: string;
  title: string;
  summary: string;
  status: string;
  related_node: string;
  related_tool_call_id?: string | null;
  step_index?: number;
}

export interface ToolInvocation {
  step_index: number;
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  blocked: boolean;
  error_code: string;
  message: string;
  result_summary: string;
  running: boolean;
}

export interface UserInputOption {
  id: string;
  label: string;
  value: string;
  description?: string;
  recommended?: boolean;
}

export interface UserInputRequest {
  kind: "user_input";
  request_id: string;
  interrupt_id?: string;
  question: string;
  options: UserInputOption[];
  selection_mode: "single" | "multiple";
  allow_other: boolean;
  other_option: UserInputOption & { placeholder?: string };
  questions?: UserInputQuestion[];
  step_index?: number;
}

export interface UserInputQuestion {
  id: string;
  question: string;
  options: UserInputOption[];
  selection_mode: "single" | "multiple";
  allow_other: boolean;
  other_placeholder: string;
}

export interface UserInputAnswer {
  request_id: string;
  selected_option_id: string;
  selected_option_ids: string[];
  answer: string;
  other_text?: string;
  request_ids?: string[];
  answers?: string[];
  question_answers?: Array<{
    question_id: string;
    question: string;
    answer: string;
    selected_option_id: string;
    selected_option_ids: string[];
    selected_option_labels: string[];
    other_text?: string;
    is_other?: boolean;
  }>;
}

/**
 * 一个 ReAct 步内的可见片段。MessageList 按 step_index 顺序渲染：
 *   thought（可选） → text（pre-tool 叙述或最终答复） → tools（本步触发的工具调用卡片）。
 *
 * step_index = 0 表示后端还没有标注 step（兼容旧 SSE 或重连时丢失 step 的事件）。
 */
export interface MessageSegment {
  step_index: number;
  text: string;
  tools: ToolInvocation[];
}

export interface SegmentFollowup {
  followup_id: string;
  user_question: string;
  assistant_answer?: string;
  status: "pending" | "answered" | "failed";
  timestamp: string;
}

export interface SegmentFollowupThread {
  segment_id: string;
  original_text: string;
  position: {
    start: number;
    end: number;
  } | null;
  followups: SegmentFollowup[];
}

export interface SegmentFollowupRequest {
  source_message_id: number;
  segment_id: string | null;
  original_text: string;
  user_question: string;
  position?: {
    start: number;
    end: number;
  } | null;
}

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
  subgraphs?: Record<string, string>;
  context_layers: Array<{
    level: number;
    name: string;
    content: string;
    budget_tokens: number | null;
    used_tokens: number;
    note: string;
    kind?: "layer" | "fused";
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
        state?: unknown;
        invocation_count?: number;
        invocations?: Array<{
          index: number;
          status?: string;
          started_ms?: number;
          completed_ms?: number;
          duration_ms?: number;
          state?: unknown;
        }>;
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

export interface ChatCheckpointState {
  checkpoint_id: string | null;
  parent_checkpoint_id: string | null;
  created_at: string | null;
  next: string[];
  tasks: Array<Record<string, unknown>>;
  interrupts: Array<Record<string, unknown>>;
  metadata: Record<string, unknown> | null;
  values: Record<string, unknown>;
}

export interface ChatTurnStateHistory {
  turn_id: number;
  conversation_id: number;
  thread_id: string;
  checkpoint_id: string | null;
  states: ChatCheckpointState[];
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
  | { event: "resume"; data: { turn_id: number; node_statuses: Record<string, string> } }
  | { event: "node"; data: { node: string; node_statuses: Record<string, string> } }
  | {
      event: "thought_snapshot";
      data: {
        node: string;
        thoughts: Array<{
          id: string;
          title: string;
          summary: string;
          status: string;
          related_node: string;
          related_tool_call_id?: string | null;
          step_index?: number;
        }>;
      };
    }
  | { event: "answer_delta"; data: { content: string; step_index?: number } }
  | {
      event: "tool_invocation";
      data: {
        step_index: number;
        tool_call_id: string;
        tool_name: string;
        arguments: Record<string, unknown>;
        ok: boolean;
        blocked: boolean;
        error_code: string;
        message: string;
        result_summary: string;
        running?: boolean;
      };
    }
  | {
      event: "interrupt";
      data: {
        turn_id: number;
        request: UserInputRequest;
        node_statuses: Record<string, string>;
      };
    }
  | {
      event: "done";
      data: {
        turn_id: number;
        response: ChatResponse;
        bubbles?: Array<{ text: string; emoji: string }>;
      };
    }
  | { event: "error"; data: { turn_id?: number; message: string; node_statuses?: Record<string, string> } }
  | { event: "turn_unavailable"; data: { turn_id: number; reason: string } };

export interface ChatActiveTurn {
  turn_id: number;
  conversation_id: number;
  status: string;
  node_statuses: Record<string, string>;
  pending_interrupt?: UserInputRequest | null;
  user_message: ChatMessage | null;
  assistant_message: ChatMessage | null;
  started_at: string;
  updated_at: string;
}

export interface ChatActiveTurnList {
  items: ChatActiveTurn[];
}

export type CommandScope = "turn" | "conversation" | "user" | "system";
export type CommandRisk = "low" | "medium" | "high";
export type CommandVisibilityState = "enabled" | "disabled" | "hidden";
export type CommandResultStatus = "success" | "failed" | "noop" | "pending_confirmation" | "needs_input";

export interface CommandOption {
  id: string;
  label: string;
  value: unknown;
  description: string;
}

export interface CommandArg {
  name: string;
  type: string;
  required: boolean;
  placeholder: string;
  options: CommandOption[];
}

export interface CommandVisibility {
  state: CommandVisibilityState;
  reason: string;
  requires_feature: string | null;
  developer_only: boolean;
}

export interface CommandSchema {
  id: string;
  command: string;
  title: string;
  description: string;
  aliases: string[];
  category: string;
  args: CommandArg[];
  scope: CommandScope;
  risk: CommandRisk;
  visibility: CommandVisibility;
  executor: string;
  reload: string[];
  result_view: string;
}

export interface CommandListResponse {
  items: CommandSchema[];
}

export interface CommandResult {
  source: "command_router";
  type: "command_result";
  command: string;
  command_id: string;
  status: CommandResultStatus;
  scope: CommandScope;
  changed: boolean;
  target: string;
  old_value: unknown | null;
  new_value: unknown | null;
  message: string;
  details: Array<{ label: string; value: string; [key: string]: unknown }>;
  suggestions: string[];
  audit_id: string | null;
  rollback_command: string | null;
}

export interface CommandExecuteResponse {
  result: CommandResult;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}
