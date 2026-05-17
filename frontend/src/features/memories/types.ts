export type MemoryStatus = "active" | "archived";

export interface Memory {
  id: number;
  level: number;
  category: string;
  content: string;
  summary: string;
  importance: number;
  confidence: number;
  source_type: string;
  source_id: number | null;
  status: MemoryStatus;
  content_hash: string;
  created_at: string;
  updated_at: string;
}

export interface MemorySourceMessage {
  id: number;
  conversation_id: number;
  conversation_title: string;
  role: string;
  content: string;
  created_at: string;
}

export interface MemoryDetail extends Memory {
  source_message: MemorySourceMessage | null;
}

export interface MemoryUpdateInput {
  category?: string;
  content?: string;
  summary?: string;
  importance?: number;
  confidence?: number;
  status?: MemoryStatus;
}
